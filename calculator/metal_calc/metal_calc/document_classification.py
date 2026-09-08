"""Versioned business classification; original receipts and reader results stay intact.

Only a verified thumbnail cache is service material. All other files remain
engineering documents, including unknown/unsupported inputs needing review.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import struct

from .errors import Conflict, MetalCalcError, OrderScopeDenied
from .folder_intake import validate_upload_id
from .securefs import SecureRoot
from .util import canonical_json, digest_json, validate_id

RULE_VERSION = "thumbs-cfb-v1"
MAX_CACHE_BYTES = 4 * 1024**2
MAX_SNAPSHOT_INSPECTION_BYTES = 16 * 1024**2
_END = 0xFFFFFFFE
_FREE = 0xFFFFFFFF


def is_thumbnail_cache(data: bytes) -> bool:
    """Recognize the bounded CFB v3 thumbnail variant present in this pilot.

    No general OLE parsing or image decoding. Older Catalog/mini-stream variants
    remain unresolved until a separate tested rule supports them.
    """
    if not 1536 <= len(data) <= MAX_CACHE_BYTES or len(data) % 512:
        return False
    if (data[:8] != bytes.fromhex("d0cf11e0a1b11ae1") or any(data[8:24])
            or struct.unpack_from("<HHHH", data, 26) != (3, 0xFFFE, 9, 6)
            or any(data[34:40]) or struct.unpack_from("<I", data, 40)[0]
            or struct.unpack_from("<I", data, 56)[0] != 4096
            or struct.unpack_from("<II", data, 68) != (_END, 0)):
        return False
    sectors = len(data) // 512 - 1
    fat_count, directory_start = struct.unpack_from("<II", data, 44)
    difat = struct.unpack_from("<109I", data, 76)
    if (not 1 <= fat_count <= min(109, sectors)
            or len(set(difat[:fat_count])) != fat_count
            or any(n >= sectors for n in difat[:fat_count])
            or any(n != _FREE for n in difat[fat_count:])):
        return False
    fat = []
    for sector in difat[:fat_count]:
        fat.extend(struct.unpack_from("<128I", data, (sector + 1) * 512))
    if len(fat) < sectors or any(fat[n] != 0xFFFFFFFD for n in difat[:fat_count]):
        return False
    used = set(difat[:fat_count])

    def chain(start: int) -> bytes:
        pieces = []
        while start != _END:
            if start >= sectors or start in used:
                raise ValueError("invalid or overlapping sector chain")
            used.add(start)
            pieces.append(data[(start + 1) * 512:(start + 2) * 512])
            start = fat[start]
        return b"".join(pieces)

    try:
        directory = chain(directory_start)
        streams = []
        names = set()
        for offset in range(0, len(directory), 128):
            entry = directory[offset:offset + 128]
            kind = entry[66]
            if kind == 0:
                if (any(entry[:68]) or any(entry[80:])
                        or any(n not in {0, _FREE} for n in struct.unpack_from("<III", entry, 68))):
                    return False
                continue
            name_size = struct.unpack_from("<H", entry, 64)[0]
            if (not 2 <= name_size <= 64 or name_size % 2
                    or entry[name_size - 2:name_size] != b"\0\0"):
                return False
            name = entry[:name_size - 2].decode("utf-16le")
            start, size = struct.unpack_from("<IQ", entry, 116)
            if offset == 0:
                if kind != 5 or name != "Root Entry" or size or start != _END:
                    return False
            elif (kind != 2 or not re.fullmatch(r"(?:32|48|96|256|768|1024)_[0-9a-f]{16}", name)
                  or name in names or not 4096 <= size <= MAX_CACHE_BYTES):
                return False
            else:
                streams.append((start, size))
            names.add(name)
        if "Root Entry" not in names or not streams:
            return False
        mini_start, mini_count = struct.unpack_from("<II", data, 60)
        if mini_count > sectors or (not mini_count and mini_start != _END):
            return False
        if mini_count:
            mini = chain(mini_start)
            if len(mini) != mini_count * 512 or any(b != 0xFF for b in mini):
                return False
        for start, size in streams:
            stream = chain(start)
            if (len(stream) != (size + 511) // 512 * 512
                    or struct.unpack_from("<II", stream) != (24, 3)
                    or struct.unpack_from("<Q", stream, 8)[0] != size - 24
                    or stream[24:27] != b"\xff\xd8\xff"
                    or stream[size - 2:size] != b"\xff\xd9"):
                return False
        return all(i in used or fat[i] == _FREE for i in range(sectors))
    except (ValueError, UnicodeDecodeError, struct.error):
        return False


class DocumentClassification:
    """Explicit preparation writes a projection; cached-only reads never open sources.

    ``engineering_documents`` counts every non-service input, even unsupported
    or unresolved ones. Only ``classification == 'service'`` permits exclusion.
    Constructing the service performs no migration or other database write.
    """

    def __init__(self, orders_root: Path):
        self.orders_root = Path(orders_root)
        self.path = self.orders_root / "registry.db"

    def _connect(self, *, writable=False):
        if self.path.is_symlink():
            raise Conflict("registry.db must not be a symlink")
        con = sqlite3.connect(self.path.absolute().as_uri() + ("?mode=rw" if writable else "?mode=ro"),
                              uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA trusted_schema=OFF")
        return con

    @staticmethod
    def _snapshot(con, order_id, snapshot_id):
        validate_id(order_id, field="order_id")
        validate_id(snapshot_id, field="snapshot_id")
        row = con.execute("SELECT * FROM document_snapshots WHERE snapshot_id=? AND order_id=?",
                          (snapshot_id, order_id)).fetchone()
        if row is None:
            raise OrderScopeDenied("Снимок не принадлежит заказу")
        return dict(row)

    @staticmethod
    def _projection(snapshot, sources, *, persisted):
        service_count = sum(s["classification"] == "service" for s in sources)
        result = {"order_id": snapshot["order_id"], "snapshot_id": snapshot["snapshot_id"],
                  "manifest_digest": snapshot["manifest_digest"], "rule_version": RULE_VERSION,
                  "summary": {"files_total": len(sources), "service_files": service_count,
                              "engineering_documents": len(sources) - service_count},
                  "sources": sources}
        return {**result, "persisted": persisted,
                "projection_sha256": digest_json(result) if persisted else None}

    @staticmethod
    def _entry(source, reason="service_classification_pending", classification="unknown", **evidence):
        return {**source, "classification": classification, "reason": reason,
                "rule_version": RULE_VERSION, "evidence": evidence}

    def get_snapshot(self, order_id: str, snapshot_id: str) -> dict:
        """Return saved projection, or a conservative unresolved metadata-only view."""
        with closing(self._connect()) as con:
            snapshot = self._snapshot(con, order_id, snapshot_id)
            exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                 "AND name='document_classifications'").fetchone()
            row = con.execute("SELECT projection_json,projection_sha256 FROM document_classifications "
                              "WHERE snapshot_id=? AND rule_version=?", (snapshot_id, RULE_VERSION)).fetchone() if exists else None
        if row:
            if hashlib.sha256(row["projection_json"]).hexdigest() != row["projection_sha256"]:
                raise Conflict("Контрольная сумма классификации не совпала")
            projection = json.loads(row["projection_json"])
            if (projection["order_id"] != order_id or projection["snapshot_id"] != snapshot_id
                    or projection["manifest_digest"] != snapshot["manifest_digest"]):
                raise Conflict("Классификация относится к другому комплекту")
            return {**projection, "persisted": True, "projection_sha256": row["projection_sha256"]}
        return self._projection(snapshot, [self._entry(s) for s in json.loads(snapshot["sources_json"])],
                                persisted=False)

    def ensure_snapshot(self, order_id: str, snapshot_id: str) -> dict:
        """Classify only bounded Thumbs.db candidates, then save atomically.

        Source I/O happens before the short SQLite write transaction. Historical
        snapshots, job results, and order/chat state are never rewritten.
        A transient unavailable source leaves the projection unsaved so the
        next explicit preparation can retry after the file is restored.
        """
        cached = self.get_snapshot(order_id, snapshot_id)
        if cached["persisted"]:
            return cached
        with closing(self._connect()) as con:
            snapshot = self._snapshot(con, order_id, snapshot_id)
        upload_id = validate_upload_id(json.loads(snapshot["manifest_json"])["upload_id"])
        entries = []
        inspected_bytes = 0
        retry_needed = False
        for source in json.loads(snapshot["sources_json"]):
            name = PurePosixPath(source["relative_path"]).name.casefold()
            if name != "thumbs.db":
                known = PurePosixPath(name).suffix in {".pdf", ".xlsx"}
                entries.append(self._entry(source, "document_metadata_only" if known else "unknown_format",
                                           "engineering" if known else "unknown"))
                continue
            if not 1536 <= source["bytes"] <= MAX_CACHE_BYTES:
                entries.append(self._entry(source, "service_candidate_size_out_of_bounds"))
                continue
            if inspected_bytes + source["bytes"] > MAX_SNAPSHOT_INSPECTION_BYTES:
                entries.append(self._entry(source, "service_candidate_budget_exceeded"))
                continue
            inspected_bytes += source["bytes"]
            try:
                with SecureRoot(self.orders_root, writable=False) as root:
                    data = root.read_bytes(f"folders/{upload_id}/files/{source['index']}", limit=source["bytes"])
            except (OSError, ValueError, MetalCalcError):
                entries.append(self._entry(source, "service_candidate_unreadable"))
                retry_needed = True
                continue
            if len(data) != source["bytes"] or hashlib.sha256(data).hexdigest() != source["sha256"]:
                entries.append(self._entry(source, "source_integrity_conflict"))
            elif is_thumbnail_cache(data):
                entries.append(self._entry(source, "verified_windows_thumbnail_cache", "service",
                                           sha256_verified=True, content_format="cfb_v3_thumbnail_jpeg",
                                           bytes_inspected=len(data)))
            else:
                entries.append(self._entry(source, "service_name_content_conflict",
                                           sha256_verified=True, bytes_inspected=len(data)))
        projection = self._projection(snapshot, entries, persisted=not retry_needed)
        if retry_needed:
            return projection
        payload = {k: v for k, v in projection.items() if k not in {"persisted", "projection_sha256"}}
        with closing(self._connect(writable=True)) as con, con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("""CREATE TABLE IF NOT EXISTS document_classifications (
                snapshot_id TEXT NOT NULL REFERENCES document_snapshots(snapshot_id),
                rule_version TEXT NOT NULL, projection_sha256 TEXT NOT NULL,
                projection_json BLOB NOT NULL, PRIMARY KEY(snapshot_id,rule_version)) STRICT""")
            con.execute("INSERT OR IGNORE INTO document_classifications VALUES(?,?,?,?)",
                        (snapshot_id, RULE_VERSION, projection["projection_sha256"], canonical_json(payload)))
        return self.get_snapshot(order_id, snapshot_id)
