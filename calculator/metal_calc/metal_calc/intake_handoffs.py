"""Durable single admission of an order to its existing front profile.

Only receipt metadata and published reader JSON are accessed. Dispatch owns its
own durable state: receiving context is never composition approval.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3

from .document_jobs import DocumentJobs
from .errors import Conflict, InvalidState, NotFound, OrderScopeDenied
from .util import digest_json, validate_id


ERROR_CODES = frozenset({"session_create_failed", "dispatch_failed", "dispatch_unknown",
                        "run_failed", "run_cancelled", "run_interrupted", "run_unavailable",
                        "dispatch_interrupted", "acknowledgment_missing",
                        "runtime_unavailable", "policy_unavailable", "session_missing"})


class IntakeHandoffs:
    def __init__(self, orders_root: Path, *, session_db: Path | None = None):
        self.jobs = DocumentJobs(orders_root)
        self.session_db = Path(session_db) if session_db else None
        with self.jobs._db() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS intake_handoffs (
                handoff_id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL REFERENCES document_snapshots(snapshot_id),
                session_id TEXT NOT NULL UNIQUE, order_name TEXT NOT NULL,
                dispatch_status TEXT NOT NULL DEFAULT 'prepared',
                session_created INTEGER NOT NULL DEFAULT 0, run_id TEXT,
                received_at REAL, error_code TEXT, created_at REAL NOT NULL,
                updated_at REAL NOT NULL, UNIQUE(order_id,snapshot_id)) STRICT""")

    @staticmethod
    def _row(con, handoff_id):
        validate_id(handoff_id, field="handoff_id")
        row = con.execute("SELECT h.*,s.document_set_revision,s.manifest_digest "
                          "FROM intake_handoffs h JOIN document_snapshots s USING(snapshot_id) "
                          "WHERE handoff_id=?", (handoff_id,)).fetchone()
        if row is None:
            raise NotFound("Передача приёмщику не найдена")
        return dict(row)

    def _current(self, con, row):
        latest = con.execute("SELECT snapshot_id FROM document_snapshots WHERE order_id=? "
                             "ORDER BY document_set_revision DESC LIMIT 1", (row["order_id"],)).fetchone()
        # A -> B -> A is a new immutable snapshot, even when the source hashes
        # match a historical handoff. Old conversations must remain stale.
        return bool(latest and latest[0] == row["snapshot_id"] and self.jobs._current(con, row))

    def _public(self, con, row):
        current = self._current(con, row)
        dispatch = row["dispatch_status"]
        status = ("stale" if not current else "needs_attention" if dispatch == "error"
                  else "received" if row["received_at"] is not None
                  else {"finished": "needs_attention", "prepared": "prepared",
                        "connecting": "connecting", "running": "running"}[dispatch])
        return {key: row[key] for key in ("handoff_id", "order_id", "order_name", "session_id",
                "snapshot_id", "document_set_revision", "manifest_digest", "dispatch_status",
                "received_at", "run_id", "error_code", "created_at", "updated_at")} | {
                    "profile": "default", "status": status,
                    "session_created": bool(row["session_created"]),
                    "initial_run_active": dispatch in {"connecting", "running"}}

    def prepare(self, order_id):
        with self.jobs._db() as con:
            snapshot = self.jobs._snapshot(con, order_id)
            handoff_id = "intake_" + digest_json([order_id, snapshot["snapshot_id"]])[:40]
            state = json.loads(con.execute("SELECT state_json FROM orders WHERE order_id=?",
                                           (order_id,)).fetchone()[0])
            name = str(state.get("folder_intake", {}).get("folder_name") or order_id)[:200]
            now = self.jobs.clock()
            con.execute("INSERT OR IGNORE INTO intake_handoffs(handoff_id,order_id,snapshot_id,"
                        "session_id,order_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                        (handoff_id, order_id, snapshot["snapshot_id"], handoff_id, name, now, now))
            return self._public(con, self._row(con, handoff_id))

    def get(self, handoff_id):
        with self.jobs._db() as con:
            return self._public(con, self._row(con, handoff_id))

    def find(self, order_id):
        validate_id(order_id, field="order_id")
        with self.jobs.registry._connect() as con:
            if con.execute("SELECT 1 FROM orders WHERE order_id=?", (order_id,)).fetchone() is None:
                raise NotFound("Заказ не найден")
            row = con.execute("SELECT h.*,s.document_set_revision,s.manifest_digest "
                              "FROM intake_handoffs h JOIN document_snapshots s USING(snapshot_id) "
                              "WHERE h.order_id=? AND s.order_id=h.order_id "
                              "ORDER BY s.document_set_revision DESC LIMIT 1",
                              (order_id,)).fetchone()
            return self._public(con, dict(row)) if row else None

    def claim(self, handoff_id):
        with self.jobs._db() as con:
            row = self._row(con, handoff_id)
            if not self._current(con, row):
                raise Conflict("Документы заказа изменились; передайте актуальный комплект")
            # Only these explicit pre-submission failures can be retried. A
            # missing run_id alone never proves that no run was admitted.
            claimed = con.execute("UPDATE intake_handoffs SET dispatch_status='connecting',error_code=NULL,updated_at=? "
                                  "WHERE handoff_id=? AND (dispatch_status='prepared' OR "
                                  "(dispatch_status='error' AND run_id IS NULL AND "
                                  "error_code IN ('session_create_failed','runtime_unavailable')))",
                                  (self.jobs.clock(), handoff_id)).rowcount == 1
            return self._public(con, self._row(con, handoff_id)) | {"claimed": claimed}

    def _change(self, handoff_id, action, value=None):
        with self.jobs._db() as con:
            row = self._row(con, handoff_id)
            if action == "session":
                if row["dispatch_status"] != "connecting":
                    raise Conflict("Передача уже обработана")
                con.execute("UPDATE intake_handoffs SET session_created=1 WHERE handoff_id=?", (handoff_id,))
            elif action == "dispatched":
                validate_id(value, field="run_id")
                if row["run_id"] and row["run_id"] != value:
                    raise Conflict("Передача уже связана с другим запуском")
                if row["dispatch_status"] not in {"connecting", "running"} or not row["session_created"]:
                    raise Conflict("Передача не готова к запуску")
                con.execute("UPDATE intake_handoffs SET dispatch_status='running',run_id=? WHERE handoff_id=?",
                            (value, handoff_id))
            elif action == "received":
                if not self._current(con, row):
                    raise Conflict("Комплект документов изменился")
                if not row["session_created"] or row["dispatch_status"] == "prepared":
                    raise OrderScopeDenied("Передача ещё не подключена к чату")
                con.execute("UPDATE intake_handoffs SET received_at=COALESCE(received_at,?) WHERE handoff_id=?",
                            (self.jobs.clock(), handoff_id))
            elif action == "error":
                if value not in ERROR_CODES:
                    raise InvalidState("Неизвестный код ошибки передачи")
                # A late failed poll must never overwrite confirmed completion.
                # Definitive failed/cancelled outcomes similarly outrank a later
                # transient status-read failure.
                if (row["dispatch_status"] == "finished"
                        or row["error_code"] in {"run_failed", "run_cancelled", "run_interrupted"}):
                    return self._public(con, row)
                con.execute("UPDATE intake_handoffs SET dispatch_status='error',error_code=? WHERE handoff_id=?",
                            (value, handoff_id))
            elif action == "finished":
                if row["dispatch_status"] not in {"running", "finished", "error"} or not row["run_id"]:
                    raise Conflict("Запуск передачи ещё не зарегистрирован")
                if row["error_code"] in {"run_failed", "run_cancelled", "run_interrupted"}:
                    return self._public(con, row)
                con.execute("UPDATE intake_handoffs SET dispatch_status='finished',error_code=? WHERE handoff_id=?",
                            (None if row["received_at"] is not None else "acknowledgment_missing", handoff_id))
            con.execute("UPDATE intake_handoffs SET updated_at=? WHERE handoff_id=?", (self.jobs.clock(), handoff_id))
            return self._public(con, self._row(con, handoff_id))

    def mark_session_created(self, handoff_id):
        return self._change(handoff_id, "session")

    def mark_dispatched(self, handoff_id, run_id):
        return self._change(handoff_id, "dispatched", run_id)

    def mark_error(self, handoff_id, error_code):
        return self._change(handoff_id, "error", error_code)

    def mark_received(self, handoff_id):
        return self._change(handoff_id, "received")

    def mark_finished(self, handoff_id):
        return self._change(handoff_id, "finished")

    def for_session(self, session_id):
        """Resolve trusted native identity; arbitrary forks never inherit scope."""
        if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
            raise OrderScopeDenied("Выберите заказ в Заказах и передайте его приёмщику")
        current = session_id
        visited = set()
        for _ in range(64):
            if current in visited:
                break
            visited.add(current)
            with self.jobs._db() as con:
                row = con.execute("SELECT handoff_id FROM intake_handoffs WHERE session_id=?", (current,)).fetchone()
            if row:
                record = self.get(row[0])
                if record["status"] == "stale" or not record["session_created"]:
                    raise OrderScopeDenied("Передайте актуальный комплект из карточки заказа")
                return record
            if self.session_db is None:
                break
            try:
                with closing(sqlite3.connect(self.session_db.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                    db.row_factory = sqlite3.Row
                    child = db.execute("SELECT * FROM sessions WHERE id=?", (current,)).fetchone()
                    if not child or not child["parent_session_id"] or child["source"] == "tool":
                        break
                    parent = db.execute("SELECT * FROM sessions WHERE id=?", (child["parent_session_id"],)).fetchone()
                    # Same exclusions as SessionDB._NON_CONTINUATION_CHILD_FILTER_SQL.
                    # Native publication inserts the child before ending its parent,
                    # so timestamp ordering is not a valid ancestry test.
                    candidates = db.execute("SELECT id FROM sessions WHERE parent_session_id=? "
                                            "AND COALESCE(json_extract(COALESCE(model_config,'{}'),'$._branched_from'),'') != ? "
                                            "AND COALESCE(json_extract(COALESCE(model_config,'{}'),'$._delegate_from'),'') != ? "
                                            "AND COALESCE(source,'') != 'tool'",
                                            (child["parent_session_id"],) * 3).fetchall()
                    if (not parent or parent["end_reason"] != "compression"
                            or parent["ended_at"] is None or len(candidates) != 1
                            or candidates[0][0] != current):
                        break
                    current = parent["id"]
            except (sqlite3.Error, OSError, KeyError, IndexError):
                break
        raise OrderScopeDenied("Выберите заказ в Заказах и передайте его приёмщику")

    def _cached(self, record):
        with self.jobs._db() as con:
            if not self._current(con, self._row(con, record["handoff_id"])):
                raise OrderScopeDenied("Комплект документов изменился; передайте актуальный заказ")
            snapshot = con.execute("SELECT sources_json FROM document_snapshots WHERE snapshot_id=?",
                                   (record["snapshot_id"],)).fetchone()
            rows = con.execute("SELECT r.source_id,r.job_id,json_extract(r.summary_json,'$.status') "
                               "FROM document_results r JOIN document_jobs j USING(job_id) "
                               "WHERE j.snapshot_id=? AND json_extract(j.recipe_json,'$.command')='inspect' "
                               "ORDER BY r.published_at,r.job_id", (record["snapshot_id"],)).fetchall()
            cached = {row[0]: {"job_id": row[1], "status": row[2]} for row in rows}
            if any(item["status"] not in {"complete", "partial", "failed", "unsupported"}
                   for item in cached.values()):
                raise Conflict("Сохранённое наблюдение содержит неизвестный статус")
            return json.loads(snapshot[0]), cached

    def context(self, session_id):
        record = self.for_session(session_id)
        sources, cached = self._cached(record)
        record = self.mark_received(record["handoff_id"])
        counts = {status: sum(item["status"] == status for item in cached.values())
                  for status in ("complete", "partial", "failed", "unsupported")}
        from .intake_preparation import IntakePreparation
        preparation = IntakePreparation(self).view(record["handoff_id"])
        from .intake_analysis import AnalysisStore
        analysis = AnalysisStore(self).get(record["handoff_id"])
        job = analysis["job"]
        analysis_summary = ({"status": job["status"], "stage": job["stage"], "summary": job["summary"],
                             "issues": job["issues"][:20]} if job else None)
        next_action = ("Уточните только неизвестные начальные ответы. Сотрудник сохраняет их в панели, "
                       "затем подготавливает план и отдельно нажимает «Начать разбор».")
        if job:
            next_action = ("Статус разбора показан в analysis. Сохраняйте различие между серверным заданием, "
                           "предварительными предложениями и принятым сотрудником составом. "
                           "Предложения по исходнику доступны вместе с intake_observation.")
        return {"order_id": record["order_id"], "order_name": record["order_name"],
                "snapshot_id": record["snapshot_id"], "document_set_revision": record["document_set_revision"],
                "files_total": len(sources), "cached_observations": len(cached),
                "files_without_observation": len(sources) - len(cached),
                "cached_status_counts": counts,
                "document_summary": preparation["summary"],
                "initial_answers": preparation["initial_answers"],
                "analysis": analysis_summary, "next_action": next_action,
                "receipt": "context_delivered", "composition_status": "proposed" if job and job["summary"]["sources_complete"] else "not_proposed",
                "human_approved": False, "use_for_calculation": False}

    @staticmethod
    def _page(offset, limit, maximum):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= maximum:
            raise InvalidState("Некорректные границы страницы")

    def sources(self, session_id, *, offset=0, limit=50):
        self._page(offset, limit, 100)
        record = self.for_session(session_id)
        sources, cached = self._cached(record)
        from .document_classification import DocumentClassification
        projection = DocumentClassification(self.jobs.orders_root).get_snapshot(record["order_id"], record["snapshot_id"])
        classifications = {s["source_id"]: s for s in projection["sources"]}
        return {"order_id": record["order_id"], "snapshot_id": record["snapshot_id"],
                "sources": [{**source, "observation_cached": source["source_id"] in cached,
                             "classification": classifications[source["source_id"]]["classification"],
                             "classification_reason": classifications[source["source_id"]]["reason"],
                             "observation_status": cached.get(source["source_id"], {}).get("status")}
                            for source in sources[offset:offset + limit]],
                "offset": offset, "limit": limit, "total": len(sources),
                "has_more": offset + limit < len(sources), "use_for_calculation": False}

    def observation(self, session_id, source_id, *, offset=0, limit=8000):
        self._page(offset, limit, 16000)
        record = self.for_session(session_id)
        sources, cached = self._cached(record)
        if not any(source["source_id"] == source_id for source in sources):
            raise OrderScopeDenied("Источник не принадлежит переданному заказу")
        if source_id not in cached:
            return {"source_id": source_id, "status": "not_cached", "use_for_calculation": False}
        result = self.jobs.result(cached[source_id]["job_id"], source_id)
        from .intake_analysis import AnalysisStore
        analysis = AnalysisStore(self).get(record["handoff_id"])["job"]
        if analysis:
            proposal = next((row.get("proposal") for row in analysis["rows"] if row["source_id"] == source_id), None)
            if proposal:
                result = {"reader_observation": result, "analysis_status": analysis["status"],
                          "unverified_composition_proposal": proposal, "human_approved": False}
        # The result read uses another transaction; reject a source revision
        # that changed between binding resolution and cached artifact lookup.
        if self.get(record["handoff_id"])["status"] == "stale":
            raise OrderScopeDenied("Комплект документов изменился; передайте актуальный заказ")
        # JSON is quoted source content, never executable instructions. Bounding
        # the serialized observation also bounds model context for large XLSX.
        text = json.dumps(result, ensure_ascii=False, sort_keys=True)
        return {"source_id": source_id, "status": "cached", "format": "json_excerpt",
                "excerpt": text[offset:offset + limit], "offset": offset, "limit": limit,
                "total_characters": len(text), "has_more": offset + limit < len(text),
                "observation_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "numeric_facts": "unverified", "use_for_calculation": False}
