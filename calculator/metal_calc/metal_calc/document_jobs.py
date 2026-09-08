"""Durable, order-bound document jobs. No model or costing execution here."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import struct
import time
from typing import Any

from .document_contract import (RETRYABLE_FAILURES, empty_composition, observation_summary, snapshot_sources,
                                source_manifest, validate_observation)
from .errors import Conflict, InvalidState, NotFound, OrderScopeDenied
from .registry import Registry
from .securefs import SecureRoot
from .util import canonical_json, digest_json, validate_id


class DocumentJobs:
    def __init__(self, orders_root: Path, *, clock=time.time):
        self.orders_root = Path(orders_root)
        self.registry = Registry(self.orders_root / "registry.db")
        self.clock = clock
        self._manifest_cache = {}
        self._snapshot_uploads = {}
        self._migrate()

    @contextmanager
    def _db(self):
        con = self.registry._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def _migrate(self):
        with self._db() as con:
            for sql in (
                """CREATE TABLE IF NOT EXISTS document_snapshots (
                    snapshot_id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(order_id),
                    document_set_revision INTEGER NOT NULL, manifest_digest TEXT NOT NULL,
                    manifest_json BLOB NOT NULL, sources_json BLOB NOT NULL, created_at REAL NOT NULL,
                    UNIQUE(order_id,document_set_revision)) STRICT""",
                """CREATE TABLE IF NOT EXISTS document_jobs (
                    job_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES document_snapshots(snapshot_id),
                    pipeline_fingerprint TEXT NOT NULL, recipe_json BLOB NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('queued','running','completed','partial','cancelled','stale','blocked')),
                    fence INTEGER NOT NULL DEFAULT 0, attempt_id TEXT, lease_until REAL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    UNIQUE(snapshot_id,pipeline_fingerprint)) STRICT""",
                """CREATE TABLE IF NOT EXISTS document_attempts (
                    attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES document_jobs(job_id),
                    fence INTEGER NOT NULL, owner TEXT NOT NULL, capability_sha256 TEXT NOT NULL,
                    started_at REAL NOT NULL, ended_at REAL, outcome TEXT NOT NULL,
                    UNIQUE(job_id,fence)) STRICT""",
                """CREATE TABLE IF NOT EXISTS document_chunks (
                    job_id TEXT NOT NULL REFERENCES document_jobs(job_id), source_id TEXT NOT NULL,
                    source_json BLOB NOT NULL, ordinal INTEGER NOT NULL,
                    PRIMARY KEY(job_id,source_id)) STRICT""",
                """CREATE TABLE IF NOT EXISTS document_results (
                    job_id TEXT NOT NULL, source_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL REFERENCES document_attempts(attempt_id),
                    result_sha256 TEXT NOT NULL, result_json BLOB NOT NULL,
                    assets_json BLOB NOT NULL, published_at REAL NOT NULL,
                    result_status TEXT NOT NULL, summary_json BLOB NOT NULL,
                    PRIMARY KEY(job_id,source_id),
                    FOREIGN KEY(job_id,source_id) REFERENCES document_chunks(job_id,source_id)) STRICT""",
                """CREATE TABLE IF NOT EXISTS document_control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), drained INTEGER NOT NULL) STRICT""",
                # Additive per-source failure history. One row per recorded
                # attempt (generation); rows are never updated except for the
                # operator retry authorization mark. Old code ignores the table
                # and simply sees such sources as pending.
                """CREATE TABLE IF NOT EXISTS document_failures (
                    job_id TEXT NOT NULL, source_id TEXT NOT NULL, generation INTEGER NOT NULL,
                    attempt_id TEXT NOT NULL REFERENCES document_attempts(attempt_id),
                    fence INTEGER NOT NULL, code TEXT NOT NULL, failure_sha256 TEXT NOT NULL,
                    failure_json BLOB NOT NULL, recorded_at REAL NOT NULL, retry_authorized_at REAL,
                    PRIMARY KEY(job_id,source_id,generation),
                    FOREIGN KEY(job_id,source_id) REFERENCES document_chunks(job_id,source_id)) STRICT""",
            ):
                con.execute(sql)
            con.execute("INSERT OR IGNORE INTO document_control VALUES(1,0)")

    @staticmethod
    def _job(con, job_id):
        validate_id(job_id, field="job_id")
        row = con.execute("SELECT j.*,s.order_id,s.document_set_revision,s.manifest_digest "
                          "FROM document_jobs j JOIN document_snapshots s "
                          "ON s.snapshot_id=j.snapshot_id WHERE j.job_id=?", (job_id,)).fetchone()
        if row is None:
            raise NotFound("Документное задание не найдено")
        return dict(row)

    def _current(self, con, job):
        order_id = job["order_id"]
        row = con.execute("SELECT revision FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if row is None:
            return False
        cached = self._manifest_cache.get(order_id)
        if cached is None or cached[0] != row[0]:
            state = con.execute("SELECT state_json FROM orders WHERE order_id=?", (order_id,)).fetchone()
            cached = (row[0], digest_json(source_manifest(json.loads(state[0]))))
            if len(self._manifest_cache) > 128:
                self._manifest_cache.clear()
            self._manifest_cache[order_id] = cached
        return cached[1] == job["manifest_digest"]

    def start(self, order_id: str, *, reader_version: str, command="inspect", options=None,
              source_id=None) -> dict:
        validate_id(order_id, field="order_id")
        if command not in {"inspect", "render"} or not isinstance(reader_version, str) or not reader_version:
            raise InvalidState("Неверный рецепт чтения")
        if command == "render" and not source_id:
            raise InvalidState("Для изображения нужен source_id")
        recipe = {"command": command, "options": options or {}, "reader_version": reader_version,
                  "source_id": source_id, "schema_version": 1}
        fingerprint = digest_json(recipe)
        with self._db() as con:
            state = con.execute("SELECT state_json FROM orders WHERE order_id=?", (order_id,)).fetchone()
            if state is None:
                raise NotFound("Заказ не найден")
            manifest = source_manifest(json.loads(state[0]))
            digest = digest_json(manifest)
            prior = con.execute("SELECT * FROM document_snapshots WHERE order_id=? "
                                "ORDER BY document_set_revision DESC LIMIT 1", (order_id,)).fetchone()
            if prior and prior["manifest_digest"] == digest:
                snapshot_id = prior["snapshot_id"]
            else:
                revision = prior["document_set_revision"] + 1 if prior else 1
                snapshot_id = "snap_" + digest_json([order_id, revision, digest])[:40]
                con.execute("INSERT INTO document_snapshots VALUES(?,?,?,?,?,?,?)",
                            (snapshot_id, order_id, revision, digest, canonical_json(manifest),
                             canonical_json(snapshot_sources(order_id, manifest)), self.clock()))
                con.execute("UPDATE document_jobs SET status='stale',lease_until=NULL "
                            "WHERE snapshot_id IN (SELECT snapshot_id FROM document_snapshots "
                            "WHERE order_id=? AND snapshot_id<>?)", (order_id, snapshot_id))
            sources = snapshot_sources(order_id, manifest)
            if source_id:
                sources = [source for source in sources if source["source_id"] == source_id]
                if not sources:
                    raise OrderScopeDenied("Источник не принадлежит снимку этого заказа")
            job_id = "doc_" + digest_json([snapshot_id, fingerprint])[:40]
            now = self.clock()
            con.execute("INSERT OR IGNORE INTO document_jobs(job_id,snapshot_id,pipeline_fingerprint,"
                        "recipe_json,status,created_at,updated_at) VALUES(?,?,?,?,'queued',?,?)",
                        (job_id, snapshot_id, fingerprint, canonical_json(recipe), now, now))
            for ordinal, source in enumerate(sources):
                con.execute("INSERT OR IGNORE INTO document_chunks VALUES(?,?,?,?)",
                            (job_id, source["source_id"], canonical_json(source), ordinal))
        return self.get(job_id)

    def get(self, job_id: str, *, source_limit=200, source_offset=0) -> dict:
        if type(source_limit) is not int or not 0 <= source_limit <= 200 or type(source_offset) is not int or source_offset < 0:
            raise InvalidState("Неверная страница списка источников")
        with self._db() as con:
            job = self._job(con, job_id)
            if not self._current(con, job):
                job["status"] = "stale"
                con.execute("UPDATE document_jobs SET status='stale',lease_until=NULL WHERE job_id=?", (job_id,))
            totals = con.execute("SELECT count(*),count(r.source_id),"
                                 "coalesce(sum(json_extract(r.summary_json,'$.coverage.pages_total')),0),"
                                 "coalesce(sum(json_extract(r.summary_json,'$.coverage.pages_inventoried')),0),"
                                 "count(CASE WHEN r.source_id IS NULL AND f.generation IS NOT NULL "
                                 "AND f.retry_authorized_at IS NULL THEN 1 END) "
                                 "FROM document_chunks c LEFT JOIN document_results r USING(job_id,source_id) "
                                 + self._LATEST_FAILURE + " WHERE c.job_id=?", (job_id,)).fetchone()
            rows = con.execute("SELECT c.source_json,r.summary_json,r.result_sha256,f.code,f.generation,"
                               "f.recorded_at,f.retry_authorized_at,f.attempt_id AS failure_attempt "
                               "FROM document_chunks c LEFT JOIN document_results r USING(job_id,source_id) "
                               + self._LATEST_FAILURE + " WHERE c.job_id=? ORDER BY c.ordinal LIMIT ? OFFSET ?",
                               (job_id, source_limit, source_offset)).fetchall()
            sources = []
            for row in rows:
                source = json.loads(row["source_json"])
                result = json.loads(row["summary_json"]) if row["summary_json"] else None
                coverage = result.get("coverage", {}) if result else {}
                failure = None
                if row["generation"] is not None:
                    failure = {"code": row["code"], "generation": row["generation"],
                               "attempt_id": row["failure_attempt"], "recorded_at": row["recorded_at"],
                               "retryable": True, "retry_authorized": row["retry_authorized_at"] is not None}
                if result:
                    status = result["status"]
                elif failure and not failure["retry_authorized"]:
                    status = "failed"
                else:
                    status = "pending"
                sources.append({**source, "status": status, "accepted": result is not None,
                                "coverage": coverage, "errors": result.get("errors", []) if result else [],
                                "result_sha256": row["result_sha256"], "failure": failure})
            attempt = con.execute("SELECT outcome FROM document_attempts WHERE attempt_id=?", (job["attempt_id"],)).fetchone()
            lease_until = job["lease_until"] if job["status"] == "running" else None
            return {key: job[key] for key in ("job_id", "order_id", "snapshot_id", "document_set_revision",
                    "manifest_digest", "pipeline_fingerprint", "status", "created_at", "updated_at")} | {
                "recipe": json.loads(job["recipe_json"]), "sources": sources,
                "source_offset": source_offset, "source_limit": source_limit,
                "sources_has_more": source_offset + len(rows) < totals[0],
                "execution_outcome": attempt[0] if attempt else None,
                "lease_until": lease_until,
                "lease_expired": bool(lease_until is not None and lease_until <= self.clock()),
                "coverage": {"files_total": totals[0], "files_accounted": totals[1],
                             "files_failed": totals[4],
                             "files_pending": totals[0] - totals[1] - totals[4], "pages_total_known": totals[2],
                             "pages_inventoried": totals[3],
                             "file_accounting_complete": totals[0] == totals[1],
                             "customer_request_complete": None},
                "composition": empty_composition(), "use_for_calculation": False}

    # Latest failure generation per chunk; NULL columns when none was recorded.
    _LATEST_FAILURE = (" LEFT JOIN document_failures f ON f.job_id=c.job_id AND f.source_id=c.source_id "
                       "AND f.generation=(SELECT max(generation) FROM document_failures "
                       "WHERE job_id=c.job_id AND source_id=c.source_id)")

    def list(self, order_id: str) -> dict:
        self.registry.get(order_id)
        with self._db() as con:
            ids = [r[0] for r in con.execute("SELECT j.job_id FROM document_jobs j JOIN document_snapshots s "
                                           "USING(snapshot_id) WHERE s.order_id=? ORDER BY j.created_at DESC",
                                           (order_id,))]
        return {"jobs": [{k: v for k, v in self.get(key, source_limit=0).items() if k not in {"sources", "composition"}}
                         for key in ids]}

    def set_drain(self, drained: bool) -> dict:
        with self._db() as con:
            con.execute("UPDATE document_control SET drained=? WHERE singleton=1", (int(drained),))
            active = con.execute("SELECT count(*) FROM document_jobs WHERE status='running' AND lease_until>?",
                                 (self.clock(),)).fetchone()[0]
        return {"drained": drained, "active_leases": active}

    def drained(self) -> bool:
        with self._db() as con:
            return bool(con.execute("SELECT drained FROM document_control WHERE singleton=1").fetchone()[0])

    def release(self, claim, *, error: str | None = None):
        with self._db() as con:
            job = self._authorize(con, claim)
            con.execute("UPDATE document_jobs SET status=?,lease_until=NULL,updated_at=? WHERE job_id=?",
                        ("blocked" if error else "queued", self.clock(), job["job_id"]))
            con.execute("UPDATE document_attempts SET outcome=?,ended_at=? WHERE attempt_id=?",
                        (("error:" + error[:120]) if error else "checkpoint", self.clock(), claim["attempt_id"]))

    def claim(self, job_id=None, *, owner="document-worker", lease_seconds=240) -> dict | None:
        if not 1 <= lease_seconds <= 600 or not owner or len(owner) > 128:
            raise InvalidState("Неверные параметры исполнителя")
        with self._db() as con:
            if con.execute("SELECT drained FROM document_control WHERE singleton=1").fetchone()[0]:
                return None
            now = self.clock()
            # One active deterministic job per contour. Independent dashboard
            # calls never own this lease; worker exit is recovered by expiry.
            if con.execute("SELECT 1 FROM document_jobs WHERE status='running' AND lease_until>?",
                           (now,)).fetchone():
                return None
            query = "SELECT job_id FROM document_jobs WHERE (status='queued' OR (status='running' AND lease_until<=?))"
            args = [now]
            if job_id:
                self._job(con, job_id)
                query += " AND job_id=?"
                args.append(job_id)
            for row in con.execute(query + " ORDER BY created_at", args).fetchall():
                job = self._job(con, row[0])
                if not self._current(con, job):
                    con.execute("UPDATE document_jobs SET status='stale',lease_until=NULL WHERE job_id=?", (row[0],))
                    continue
                if job["attempt_id"]:
                    con.execute("UPDATE document_attempts SET outcome='interrupted',ended_at=? "
                                "WHERE attempt_id=? AND outcome='running'", (now, job["attempt_id"]))
                losses = con.execute("SELECT count(*) FROM document_attempts WHERE job_id=? AND outcome='interrupted'",
                                     (row[0],)).fetchone()[0]
                if losses >= 3:
                    con.execute("UPDATE document_jobs SET status='blocked',lease_until=NULL WHERE job_id=?", (row[0],))
                    continue
                capability = secrets.token_urlsafe(32)
                attempt = "att_" + secrets.token_hex(20)
                fence = job["fence"] + 1
                con.execute("INSERT INTO document_attempts VALUES(?,?,?,?,?,?,NULL,'running')",
                            (attempt, row[0], fence, owner, hashlib.sha256(capability.encode()).hexdigest(), now))
                con.execute("UPDATE document_jobs SET status='running',fence=?,attempt_id=?,lease_until=?,"
                            "updated_at=? WHERE job_id=?", (fence, attempt, now + lease_seconds, now, row[0]))
                return {"job_id": row[0], "attempt_id": attempt, "capability": capability, "fence": fence}
        return None

    def _authorize(self, con, claim):
        if not isinstance(claim, dict) or not isinstance(claim.get("capability"), str):
            raise OrderScopeDenied("Требуется capability исполнителя")
        job = self._job(con, claim.get("job_id", ""))
        attempt = con.execute("SELECT * FROM document_attempts WHERE attempt_id=? AND job_id=?",
                              (claim.get("attempt_id"), job["job_id"])).fetchone()
        if (attempt is None or job["attempt_id"] != claim.get("attempt_id")
                or job["fence"] != claim.get("fence") or job["status"] != "running"
                or not job["lease_until"] or job["lease_until"] <= self.clock()
                or not hmac.compare_digest(attempt["capability_sha256"],
                                           hashlib.sha256(claim["capability"].encode()).hexdigest())
                or not self._current(con, job)):
            raise OrderScopeDenied("Capability истекла, отозвана или относится к другому заданию")
        return job

    def renew(self, claim, *, lease_seconds=240):
        if not 1 <= lease_seconds <= 600:
            raise InvalidState("Неверное время аренды")
        with self._db() as con:
            job = self._authorize(con, claim)
            con.execute("UPDATE document_jobs SET lease_until=?,updated_at=? WHERE job_id=?",
                        (self.clock() + lease_seconds, self.clock(), job["job_id"]))

    def pending(self, claim) -> list[dict]:
        """Sources this pass may read: no accepted result and no failure awaiting operator retry."""
        with self._db() as con:
            job = self._authorize(con, claim)
            rows = con.execute("SELECT c.source_json FROM document_chunks c LEFT JOIN document_results r "
                               "USING(job_id,source_id)" + self._LATEST_FAILURE +
                               " WHERE c.job_id=? AND r.source_id IS NULL "
                               "AND (f.generation IS NULL OR f.retry_authorized_at IS NOT NULL) ORDER BY c.ordinal",
                               (job["job_id"],)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def record_failure(self, claim, source_id, failure: dict) -> dict:
        """Durably record a retryable per-source failure under the same fencing as publish.

        The source keeps no accepted result; it is excluded from further passes
        until an explicit operator retry. History is immutable per generation.
        """
        if (not isinstance(failure, dict) or failure.get("code") not in RETRYABLE_FAILURES
                or not isinstance(failure.get("message", ""), str) or len(failure.get("message", "")) > 400):
            raise InvalidState("Неверная запись сбоя чтения документа")
        errors = failure.get("errors", [])
        if (not isinstance(errors, list) or len(errors) > 20
                or any(not isinstance(e, dict) or not isinstance(e.get("code"), str) or len(e["code"]) > 100
                       or not isinstance(e.get("message"), str) or len(e["message"]) > 400 for e in errors)):
            raise InvalidState("Неверный список ошибок документа")
        reader = failure.get("reader")
        reader = {k: v for k, v in reader.items() if k in {"fingerprint", "version"} and isinstance(v, str)
                  and len(v) <= 200} if isinstance(reader, dict) else {}
        checkpoint = failure.get("checkpoint")
        if checkpoint is not None and not isinstance(checkpoint, dict):
            raise InvalidState("Контрольная точка чтения должна быть объектом")
        with self._db() as con:
            job = self._authorize(con, claim)
            source = self._source(con, job["job_id"], source_id)
            if con.execute("SELECT 1 FROM document_results WHERE job_id=? AND source_id=?",
                           (job["job_id"], source_id)).fetchone():
                raise Conflict("Для источника уже принят результат")
            latest = con.execute("SELECT generation,retry_authorized_at FROM document_failures WHERE job_id=? "
                                 "AND source_id=? ORDER BY generation DESC LIMIT 1", (job["job_id"], source_id)).fetchone()
            if latest and latest["retry_authorized_at"] is None:
                raise Conflict("Сбой источника уже записан; нужен явный повтор")
            generation = (latest["generation"] + 1) if latest else 1
            now = self.clock()
            binding = {"job_id": job["job_id"], "snapshot_id": job["snapshot_id"],
                       "document_set_revision": job["document_set_revision"],
                       "manifest_digest": job["manifest_digest"],
                       "pipeline_fingerprint": job["pipeline_fingerprint"]}
            record = {"schema_version": 1, "kind": "source_failure", "code": failure["code"],
                      "message": failure.get("message", ""), "errors": errors, "reader": reader,
                      "retryable": True, "generation": generation, "attempt_id": claim["attempt_id"],
                      "fence": job["fence"], "recorded_at": now,
                      "source": {k: source[k] for k in ("source_id", "sha256", "bytes", "relative_path")},
                      "binding": binding, "checkpoint": None, "checkpoint_truncated": False,
                      "checkpoint_dropped": [], "checkpoint_image_discarded": False,
                      "checkpoint_rejected": failure.get("checkpoint_rejected") is True}
            if checkpoint is not None:
                record.update(self._bounded_checkpoint(checkpoint, source, json.loads(job["recipe_json"]), binding))
            encoded = canonical_json(record)
            if len(encoded) > self.CHECKPOINT_LIMIT + 256 * 1024:
                raise InvalidState("Запись сбоя превышает допустимый размер")
            digest = hashlib.sha256(encoded).hexdigest()
            con.execute("INSERT INTO document_failures VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                        (job["job_id"], source_id, generation, claim["attempt_id"], job["fence"],
                         failure["code"], digest, encoded, now))
            con.execute("UPDATE document_jobs SET updated_at=? WHERE job_id=?", (now, job["job_id"]))
        return {"failure_sha256": digest, "generation": generation}

    # Partial reader output kept with a failure. Larger checkpoints are cut
    # explicitly rather than rejected so a verbose parser cannot starve the pass.
    CHECKPOINT_LIMIT = 512 * 1024
    _CHECKPOINT_CORE = frozenset({"schema_version", "command", "status", "complete", "document_type",
                                  "use_for_calculation", "source", "reader", "verification", "coverage",
                                  "errors", "binding"})

    @classmethod
    def _bounded_checkpoint(cls, checkpoint: dict, source: dict, recipe: dict, binding: dict) -> dict:
        """Validate an unverified partial reader result exactly like a publication, then bound it."""
        validate_observation(checkpoint, source, recipe["command"])
        if checkpoint["status"] == "complete" or checkpoint["complete"]:
            raise InvalidState("Контрольная точка не может объявлять чтение полным")
        if checkpoint["reader"].get("fingerprint") != recipe["reader_version"]:
            raise InvalidState("Версия обработчика отличается от рецепта задания")
        if checkpoint["reader"].get("options", {}) != recipe["options"]:
            raise InvalidState("Параметры обработчика отличаются от рецепта задания")
        if "binding" in checkpoint and checkpoint["binding"] != binding:
            raise Conflict("Контрольная точка относится к другому снимку")
        kept = {k: v for k, v in checkpoint.items() if k != "image"} | {"binding": binding}
        # A partial render never carries a saved, SHA-checked asset; the
        # reader's temporary image is discarded rather than referenced.
        image_discarded = checkpoint.get("image") is not None
        dropped = []
        encoded = canonical_json(kept)
        while len(encoded) > cls.CHECKPOINT_LIMIT:
            extras = [k for k in kept if k not in cls._CHECKPOINT_CORE]
            if not extras:
                return {"checkpoint": None, "checkpoint_truncated": True, "checkpoint_dropped": dropped + ["*"],
                        "checkpoint_image_discarded": image_discarded}
            biggest = max(extras, key=lambda k: len(canonical_json(kept[k])))
            dropped.append(biggest)
            del kept[biggest]
            encoded = canonical_json(kept)
        return {"checkpoint": kept, "checkpoint_truncated": bool(dropped), "checkpoint_dropped": dropped,
                "checkpoint_image_discarded": image_discarded}

    def failures(self, job_id, source_id) -> list[dict]:
        """Immutable failure history of one source, oldest first, integrity-checked.

        Includes the bounded unverified checkpoint of each generation; this is
        the only reader of the raw failure JSON — `get()` stays compact.
        """
        with self._db() as con:
            self._job(con, job_id)
            self._source(con, job_id, source_id)
            rows = con.execute("SELECT failure_json,failure_sha256,retry_authorized_at FROM document_failures "
                               "WHERE job_id=? AND source_id=? ORDER BY generation", (job_id, source_id)).fetchall()
        history = []
        for row in rows:
            if hashlib.sha256(row[0]).hexdigest() != row[1]:
                raise Conflict("SHA сохранённой записи сбоя не совпадает")
            history.append(json.loads(row[0]) | {"failure_sha256": row[1], "retry_authorized_at": row[2]})
        return history

    @staticmethod
    def _source(con, job_id, source_id):
        row = con.execute("SELECT source_json FROM document_chunks WHERE job_id=? AND source_id=?",
                          (job_id, source_id)).fetchone()
        if row is None:
            raise OrderScopeDenied("Источник не принадлежит заданию")
        return json.loads(row[0])

    def read_source(self, job_id, source_id, claim=None) -> tuple[bytes, dict]:
        with self._db() as con:
            job = self._job(con, job_id)
            if claim is not None:
                if claim.get("job_id") != job_id:
                    raise OrderScopeDenied("Capability другого задания")
                self._authorize(con, claim)
            source = self._source(con, job_id, source_id)
            snapshot_id = job["snapshot_id"]
            if snapshot_id not in self._snapshot_uploads:
                manifest = con.execute("SELECT manifest_json FROM document_snapshots WHERE snapshot_id=?",
                                       (snapshot_id,)).fetchone()
                if len(self._snapshot_uploads) > 128:
                    self._snapshot_uploads.clear()
                self._snapshot_uploads[snapshot_id] = json.loads(manifest[0])["upload_id"]
            upload_id = self._snapshot_uploads[snapshot_id]
        # The stored upload id and ordinal come from folder receipts. Never
        # interpret source_id or user filenames as a filesystem path.
        with SecureRoot(self.orders_root, writable=False) as root:
            data = root.read_bytes(f"folders/{upload_id}/files/{source['index']}",
                                   limit=100 * 1024**2)
        if len(data) != source["bytes"] or hashlib.sha256(data).hexdigest() != source["sha256"]:
            raise Conflict("SHA/размер исходного документа изменился")
        if claim is not None:
            with self._db() as con:
                self._authorize(con, claim)
        return data, source

    def publish(self, claim, source_id, result: dict, assets: dict[str, bytes] | None = None) -> dict:
        with self._db() as con:
            job = self._authorize(con, claim)
            source = self._source(con, job["job_id"], source_id)
            recipe = json.loads(job["recipe_json"])
            validate_observation(result, source, recipe["command"])
            if result["reader"].get("fingerprint") != recipe["reader_version"]:
                raise InvalidState("Версия обработчика отличается от рецепта задания")
            if result["reader"].get("options", {}) != recipe["options"]:
                raise InvalidState("Параметры обработчика отличаются от рецепта задания")
            if recipe["command"] == "render" and result.get("image"):
                opts = recipe["options"]
                expected_crop = opts.get("crop")
                if expected_crop is None:
                    info = result.get("page_info")
                    if not isinstance(info, dict) or not all(type(info.get(k)) in {int, float} and info[k] > 0
                                                           for k in ("width", "height")):
                        raise InvalidState("Для полного изображения нужны размеры страницы")
                    expected_crop = [0, 0, info["width"], info["height"]]
                if (result.get("page") != opts.get("page") or result.get("nominal_dpi") != opts.get("dpi", 180)
                        or result.get("requested_crop") != expected_crop
                        or result["coverage"].get("rendered_pages") != [opts.get("page")]):
                    raise InvalidState("Страница или область изображения не соответствует заданию")
            # The snapshot/fence binding is server-owned; callers cannot supply
            # another revision and get it silently replaced.
            binding = {"job_id": job["job_id"], "snapshot_id": job["snapshot_id"],
                       "document_set_revision": job["document_set_revision"],
                       "manifest_digest": job["manifest_digest"],
                       "pipeline_fingerprint": job["pipeline_fingerprint"]}
            if "binding" in result and result["binding"] != binding:
                raise Conflict("Результат относится к другому снимку")
            result = {**result, "binding": binding}
            encoded = canonical_json(result)
            if len(encoded) > 64 * 1024**2:
                raise InvalidState("Артефакт превышает допустимый размер")
            digest = hashlib.sha256(encoded).hexdigest()
            old = con.execute("SELECT result_sha256 FROM document_results WHERE job_id=? AND source_id=?",
                              (job["job_id"], source_id)).fetchone()
            if old:
                if old[0] != digest:
                    raise Conflict("Другой результат уже опубликован")
                return {"result_sha256": digest, "replayed": True}
            assets = assets or {}
            image = result.get("image")
            if (set(assets) != ({"image"} if image else set())
                    or image and (not isinstance(image, dict) or not isinstance(assets.get("image"), bytes)
                                  or hashlib.sha256(assets["image"]).hexdigest() != image.get("sha256"))):
                raise InvalidState("Изображение отсутствует или не совпадает с SHA артефакта")
            if image:
                data = assets["image"]
                if (len(data) < 33 or data[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                        or type(image.get("bytes")) is not int or image["bytes"] != len(data)
                        or any(type(image.get(k)) is not int or image[k] < 1 for k in ("width", "height"))
                        or tuple(image[k] for k in ("width", "height")) != struct.unpack(">II", data[16:24])):
                    raise InvalidState("Метаданные изображения не совпадают с PNG")
            saved_assets = {}
            with SecureRoot(self.orders_root, writable=True) as root:
                root.ensure_dir("document-artifacts")
                parent_fd = root._open_dir("")
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
                for name, data in assets.items():
                    if len(data) > 64 * 1024**2:
                        raise InvalidState("Изображение превышает допустимый размер")
                    asset_hash = hashlib.sha256(data).hexdigest()
                    path = f"document-artifacts/{asset_hash}"
                    try:
                        root.atomic_write(path, data)
                    except Conflict:
                        if root.read_bytes(path, limit=64 * 1024**2) != data:
                            raise Conflict("Повреждён сохранённый артефакт")
                    saved_assets[name] = {"sha256": asset_hash, "bytes": len(data), "path": path}
            # Files are fsynced before the database pointer becomes visible.
            self._authorize(con, claim)
            con.execute("INSERT INTO document_results VALUES(?,?,?,?,?,?,?,?,?)",
                        (job["job_id"], source_id, claim["attempt_id"], digest, encoded,
                         canonical_json(saved_assets), self.clock(), result["status"],
                         canonical_json(observation_summary(result))))
            con.execute("UPDATE document_jobs SET updated_at=? WHERE job_id=?", (self.clock(), job["job_id"]))
        return {"result_sha256": digest, "replayed": False}

    def finish(self, claim) -> dict:
        with self._db() as con:
            job = self._authorize(con, claim)
            rows = con.execute("SELECT r.result_status,f.generation,f.retry_authorized_at FROM document_chunks c "
                               "LEFT JOIN document_results r USING(job_id,source_id)" + self._LATEST_FAILURE +
                               " WHERE c.job_id=?", (job["job_id"],)).fetchall()
            if any(row[0] is None and (row[1] is None or row[2] is not None) for row in rows):
                raise Conflict("Отсутствуют типизированные результаты документов")
            # Retryable failures never count as accounted: the job stays
            # partial until an operator retry produces an accepted result.
            status = "completed" if all(r[0] == "complete" for r in rows) else "partial"
            con.execute("UPDATE document_jobs SET status=?,lease_until=NULL,updated_at=? WHERE job_id=?",
                        (status, self.clock(), job["job_id"]))
            con.execute("UPDATE document_attempts SET outcome=?,ended_at=? WHERE attempt_id=?",
                        (status, self.clock(), claim["attempt_id"]))
        return self.get(job["job_id"])

    def cancel(self, job_id) -> dict:
        with self._db() as con:
            job = self._job(con, job_id)
            if job["status"] in {"queued", "running", "blocked"}:
                con.execute("UPDATE document_jobs SET status='cancelled',lease_until=NULL,updated_at=? WHERE job_id=?",
                            (self.clock(), job_id))
                con.execute("UPDATE document_attempts SET outcome='cancelled',ended_at=? "
                            "WHERE attempt_id=? AND outcome='running'", (self.clock(), job["attempt_id"]))
                # Cancel withdraws every retry permission that no pass has
                # consumed yet; a later addressed retry re-grants exactly one.
                # Failure JSON/history rows themselves are untouched.
                con.execute("UPDATE document_failures SET retry_authorized_at=NULL WHERE job_id=? "
                            "AND retry_authorized_at IS NOT NULL AND generation=(SELECT max(generation) "
                            "FROM document_failures g WHERE g.job_id=document_failures.job_id "
                            "AND g.source_id=document_failures.source_id) AND NOT EXISTS (SELECT 1 FROM "
                            "document_results r WHERE r.job_id=document_failures.job_id "
                            "AND r.source_id=document_failures.source_id)", (job_id,))
        return self.get(job_id)

    def retry(self, job_id, *, source_id: str | None = None) -> dict:
        """Explicit operator retry. Accepted results are never touched.

        Without source_id: re-queue everything still unaccepted (pending and
        every failed source). With source_id: authorize exactly that failed
        source. A stale snapshot, a running job, an accepted or never-failed
        source and a foreign source are rejected explicitly.
        """
        with self._db() as con:
            job = self._job(con, job_id)
            if job["status"] == "stale" or not self._current(con, job):
                raise Conflict("Комплект изменился; создайте задание нового снимка")
            if job["status"] == "running" and job["lease_until"] and job["lease_until"] > self.clock():
                raise Conflict("Задание выполняется; повтор возможен после завершения прохода")
            now = self.clock()
            if source_id is not None:
                self._source(con, job_id, source_id)
                if con.execute("SELECT 1 FROM document_results WHERE job_id=? AND source_id=?",
                               (job_id, source_id)).fetchone():
                    raise InvalidState("Для источника уже принят результат; повтор не требуется")
                latest = con.execute("SELECT generation,retry_authorized_at FROM document_failures WHERE job_id=? "
                                     "AND source_id=? ORDER BY generation DESC LIMIT 1", (job_id, source_id)).fetchone()
                if latest is None:
                    raise InvalidState("Источник не имеет записанного сбоя")
                # An addressed retry must read exactly one source. A job that
                # still holds never-read sources (cancelled/blocked mid-pass)
                # would silently read them too, so it needs a job-level retry.
                if con.execute("SELECT 1 FROM document_chunks c LEFT JOIN document_results r USING(job_id,source_id)"
                               + self._LATEST_FAILURE + " WHERE c.job_id=? AND r.source_id IS NULL "
                               "AND f.generation IS NULL", (job_id,)).fetchone():
                    raise Conflict("В задании есть непрочитанные источники; используйте повтор всего задания")
                con.execute("UPDATE document_failures SET retry_authorized_at=coalesce(retry_authorized_at,?) "
                            "WHERE job_id=? AND source_id=? AND generation=?",
                            (now, job_id, source_id, latest["generation"]))
            else:
                con.execute("UPDATE document_failures SET retry_authorized_at=? WHERE job_id=? "
                            "AND retry_authorized_at IS NULL AND generation=(SELECT max(generation) "
                            "FROM document_failures g WHERE g.job_id=document_failures.job_id "
                            "AND g.source_id=document_failures.source_id)", (now, job_id))
            unaccepted = con.execute("SELECT 1 FROM document_chunks c LEFT JOIN document_results r "
                                     "USING(job_id,source_id) WHERE c.job_id=? AND r.source_id IS NULL",
                                     (job_id,)).fetchone()
            if job["status"] in {"cancelled", "blocked"} or (job["status"] == "partial" and unaccepted):
                con.execute("UPDATE document_jobs SET status='queued',lease_until=NULL,updated_at=? WHERE job_id=?",
                            (now, job_id))
                # An explicit operator retry grants another bounded recovery
                # window while retaining the entire previous attempt history.
                con.execute("UPDATE document_attempts SET outcome='retry_authorized' "
                            "WHERE job_id=? AND outcome='interrupted'", (job_id,))
        return self.get(job_id)

    def result(self, job_id, source_id) -> dict:
        with self._db() as con:
            self._job(con, job_id)
            self._source(con, job_id, source_id)
            row = con.execute("SELECT result_json,result_sha256 FROM document_results WHERE job_id=? AND source_id=?",
                              (job_id, source_id)).fetchone()
            if row is None:
                raise NotFound("Результат чтения ещё не опубликован")
            if hashlib.sha256(row[0]).hexdigest() != row[1]:
                raise Conflict("SHA сохранённого результата не совпадает")
        return json.loads(row[0])

    def image(self, job_id, source_id) -> tuple[bytes, dict]:
        self.result(job_id, source_id)
        with self._db() as con:
            row = con.execute("SELECT assets_json FROM document_results WHERE job_id=? AND source_id=?",
                              (job_id, source_id)).fetchone()
            info = json.loads(row[0]).get("image")
            if not info:
                raise NotFound("Изображение не создано")
        with SecureRoot(self.orders_root, writable=False) as root:
            data = root.read_bytes(info["path"], limit=64 * 1024**2)
        if len(data) != info["bytes"] or hashlib.sha256(data).hexdigest() != info["sha256"]:
            raise Conflict("SHA сохранённого изображения не совпадает")
        return data, {k: v for k, v in info.items() if k != "path"}
