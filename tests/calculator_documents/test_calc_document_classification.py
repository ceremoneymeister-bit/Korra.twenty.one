"""Synthetic caches and real SQLite exercise classification without pilot writes."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import struct
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "calculator/metal_calc"))
from metal_calc import document_classification
from metal_calc.document_classification import DocumentClassification, MAX_CACHE_BYTES, is_thumbnail_cache
from metal_calc.document_jobs import DocumentJobs
from metal_calc.errors import Conflict, OrderScopeDenied
from metal_calc.folder_intake import FolderIntake
from metal_calc.securefs import SecureRoot
from test_document_jobs_adversarial import observation, upload


def thumbnail_cache(name="256_0123456789abcdef"):
    """Minimal regular-sector CFB cache; JPEG remains opaque to the classifier."""
    free, end = 0xFFFFFFFF, 0xFFFFFFFE
    header = bytearray(512)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", header, 40, 0, 1, 1, 0, 4096, end, 0, end, 0)
    struct.pack_into("<109I", header, 76, 0, *([free] * 108))
    fat = [0xFFFFFFFD, end] + list(range(3, 10)) + [end] + [free] * 118
    directory = bytearray(512)
    for offset in range(0, 512, 128):
        struct.pack_into("<III", directory, offset + 68, free, free, free)
    for offset, text, kind, start, size in ((0, "Root Entry", 5, end, 0),
                                          (128, name, 2, 2, 4096)):
        encoded = (text + "\0").encode("utf-16le")
        directory[offset:offset + len(encoded)] = encoded
        struct.pack_into("<H", directory, offset + 64, len(encoded))
        directory[offset + 66] = kind
        struct.pack_into("<IQ", directory, offset + 116, start, size)
    stream = bytearray(4096)
    struct.pack_into("<IIQ", stream, 0, 24, 3, len(stream) - 24)
    stream[24:27] = b"\xff\xd8\xff"
    stream[-2:] = b"\xff\xd9"
    return bytes(header + struct.pack("<128I", *fat) + directory + stream)


def test_verified_cache_and_content_conflicts():
    assert is_thumbnail_cache(thumbnail_cache())
    assert not is_thumbnail_cache(thumbnail_cache("Workbook"))
    assert not is_thumbnail_cache(b"%PDF-1.7\nengineering drawing")
    assert not is_thumbnail_cache(bytes.fromhex("d0cf11e0a1b11ae1") + b"\0" * 2040)


@pytest.mark.parametrize("offset,value", [(512 + 2 * 4, 2), (48, 900), (512 + 2 * 4, 1)])
def test_cyclic_out_of_bounds_and_overlapping_chains_are_unresolved(offset, value):
    data = bytearray(thumbnail_cache())
    struct.pack_into("<I", data, offset, value)
    assert not is_thumbnail_cache(bytes(data))


def prepare_case(root, files):
    root.mkdir(exist_ok=True)
    intake = FolderIntake(root)
    try:
        order_id = upload(intake, name="synthetic classification " + str(uuid4()), files=files)
    finally:
        intake.close()
    jobs = DocumentJobs(root)
    job = jobs.start(order_id, reader_version="test")
    return order_id, jobs, job


def test_projection_persists_and_leaves_sources_snapshots_and_results_intact(tmp_path, monkeypatch):
    order, jobs, job = prepare_case(tmp_path, [("drawing.pdf", b"%PDF synthetic"),
                                              ("a/Thumbs.db", thumbnail_cache()),
                                              ("requirements.db", b"material requirements")])
    claim = jobs.claim(job["job_id"], owner="classification-test")
    for source in job["sources"]:
        jobs.publish(claim, source["source_id"], observation(source, status="unsupported"))

    def historical_rows():
        with sqlite3.connect(tmp_path / "registry.db") as con:
            return {table: con.execute("SELECT * FROM " + table).fetchall()
                    for table in ("orders", "document_snapshots", "document_jobs", "document_results")}

    baseline = historical_rows()
    classifier = DocumentClassification(tmp_path)
    before = classifier.get_snapshot(order, job["snapshot_id"])
    assert before["persisted"] is False
    assert before["summary"]["service_files"] == 0
    reads = []
    original = SecureRoot.read_bytes

    def limited_read(root, relative, *, limit):
        reads.append(relative)
        assert relative.endswith("/files/1"), "Only the service candidate may be opened"
        assert limit == len(thumbnail_cache()) <= MAX_CACHE_BYTES
        return original(root, relative, limit=limit)

    monkeypatch.setattr(SecureRoot, "read_bytes", limited_read)
    saved = classifier.ensure_snapshot(order, job["snapshot_id"])
    assert saved["summary"] == {"files_total": 3, "service_files": 1, "engineering_documents": 2}
    assert saved["sources"][1]["evidence"]["sha256_verified"] is True
    assert saved["sources"][2]["reason"] == "unknown_format"
    assert historical_rows() == baseline
    restarted = DocumentClassification(tmp_path)
    assert restarted.get_snapshot(order, job["snapshot_id"]) == saved
    assert restarted.ensure_snapshot(order, job["snapshot_id"]) == saved
    assert len(reads) == 1, "Persisted projections must not trigger repeated content reads"
    with sqlite3.connect(tmp_path / "registry.db") as con:
        assert con.execute("SELECT count(*) FROM document_classifications").fetchone()[0] == 1


@pytest.mark.parametrize("name,content,reason", [
    ("Thumbs.db", thumbnail_cache("Workbook"), "service_name_content_conflict"),
    ("Thumbs.db", b"%PDF-1.7" + b"x" * 4096, "service_name_content_conflict"),
    ("order.db", thumbnail_cache(), "unknown_format"),
    ("Thumbs.db", b"x" * (MAX_CACHE_BYTES + 1), "service_candidate_size_out_of_bounds"),
])
def test_spoofed_names_and_unknown_content_remain_engineering(tmp_path, name, content, reason):
    order, _, job = prepare_case(tmp_path, [(name, content)])
    view = DocumentClassification(tmp_path).ensure_snapshot(order, job["snapshot_id"])
    assert view["summary"]["engineering_documents"] == 1
    assert view["summary"]["service_files"] == 0
    assert view["sources"][0]["reason"] == reason


def test_cache_only_is_read_only_even_before_migration_and_rejects_foreign_snapshot(tmp_path, monkeypatch):
    order, _, job = prepare_case(tmp_path, [("Thumbs.db", thumbnail_cache())])
    other, _, _ = prepare_case(tmp_path, [("other.pdf", b"x")])
    classifier = DocumentClassification(tmp_path)
    monkeypatch.setattr(SecureRoot, "read_bytes", lambda *a, **kw: pytest.fail("cached-only source read"))
    assert classifier.get_snapshot(order, job["snapshot_id"])["summary"]["service_files"] == 0
    with sqlite3.connect(tmp_path / "registry.db") as con:
        assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='document_classifications'").fetchone()
    with pytest.raises(OrderScopeDenied):
        classifier.ensure_snapshot(other, job["snapshot_id"])


def test_source_hash_conflict_does_not_hide_file(tmp_path):
    order, _, job = prepare_case(tmp_path, [("Thumbs.db", thumbnail_cache())])
    with sqlite3.connect(tmp_path / "registry.db") as con:
        manifest = json.loads(con.execute("SELECT manifest_json FROM document_snapshots").fetchone()[0])
    # Test-only source corruption, preserving length and filename.
    path = tmp_path / "folders" / manifest["upload_id"] / "files/0"
    content = bytearray(path.read_bytes())
    content[-1] ^= 1
    path.write_bytes(content)
    result = DocumentClassification(tmp_path).ensure_snapshot(order, job["snapshot_id"])
    assert result["sources"][0]["reason"] == "source_integrity_conflict"
    assert result["summary"]["service_files"] == 0


def test_concurrent_projection_has_one_row_and_corruption_is_rejected(tmp_path):
    order, _, job = prepare_case(tmp_path, [("Thumbs.db", thumbnail_cache())])
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: DocumentClassification(tmp_path).ensure_snapshot(order, job["snapshot_id"]),
                                range(3)))
    assert results[0] == results[1] == results[2]
    with sqlite3.connect(tmp_path / "registry.db") as con:
        assert con.execute("SELECT count(*) FROM document_classifications").fetchone()[0] == 1
        con.execute("UPDATE document_classifications SET projection_json=?", (b"{}",))
    with pytest.raises(Conflict):
        DocumentClassification(tmp_path).get_snapshot(order, job["snapshot_id"])


def test_unknown_allocated_sector_or_mini_stream_content_prevents_exclusion():
    data = bytearray(thumbnail_cache())
    data.extend(b"\0" * 512)
    struct.pack_into("<I", data, 512 + 10 * 4, 0xFFFFFFFE)
    assert not is_thumbnail_cache(bytes(data)), "Unclaimed allocated stream is meaningful"
    struct.pack_into("<II", data, 60, 10, 1)
    data[-512:] = b"\xff" * 512
    assert is_thumbnail_cache(bytes(data)), "An empty, accounted miniFAT is valid"
    data[-512] = 0
    assert not is_thumbnail_cache(bytes(data)), "Mini-stream data is unsupported by this rule"


def test_transient_unreadable_source_can_be_classified_after_restore(tmp_path, monkeypatch):
    order, _, job = prepare_case(tmp_path, [("Thumbs.db", thumbnail_cache())])
    classifier = DocumentClassification(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(SecureRoot, "read_bytes", lambda *a, **kw: (_ for _ in ()).throw(PermissionError()))
        failed = classifier.ensure_snapshot(order, job["snapshot_id"])
    assert failed["persisted"] is False
    assert failed["sources"][0]["reason"] == "service_candidate_unreadable"
    assert classifier.get_snapshot(order, job["snapshot_id"])["persisted"] is False
    restored = classifier.ensure_snapshot(order, job["snapshot_id"])
    assert restored["persisted"] is True
    assert restored["summary"]["service_files"] == 1


def test_snapshot_inspection_budget_keeps_uninspected_candidates_visible(tmp_path, monkeypatch):
    content = thumbnail_cache()
    order, _, job = prepare_case(tmp_path, [("one/Thumbs.db", content), ("two/Thumbs.db", content)])
    monkeypatch.setattr(document_classification, "MAX_SNAPSHOT_INSPECTION_BYTES", len(content))
    result = DocumentClassification(tmp_path).ensure_snapshot(order, job["snapshot_id"])
    assert result["summary"] == {"files_total": 2, "service_files": 1, "engineering_documents": 1}
    assert result["sources"][1]["reason"] == "service_candidate_budget_exceeded"


def test_symlink_substitution_is_not_read_as_service_evidence(tmp_path):
    order, _, job = prepare_case(tmp_path, [("Thumbs.db", thumbnail_cache())])
    with sqlite3.connect(tmp_path / "registry.db") as con:
        manifest = json.loads(con.execute("SELECT manifest_json FROM document_snapshots").fetchone()[0])
    path = tmp_path / "folders" / manifest["upload_id"] / "files/0"
    original = path.with_name("original")
    path.rename(original)
    path.symlink_to(original)
    result = DocumentClassification(tmp_path).ensure_snapshot(order, job["snapshot_id"])
    assert result["summary"]["service_files"] == 0
    assert result["sources"][0]["reason"] == "service_candidate_unreadable"
