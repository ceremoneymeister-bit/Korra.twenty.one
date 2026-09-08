"""Bounded semantic preparation, separate from readers and human acceptance."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import PurePosixPath
import secrets
import time

from .document_classification import DocumentClassification
from .analysis_recipe import LIMITS, RENDER_OPTIONS, recipe_fingerprint, source_contents
from .errors import Conflict, InvalidState, NotFound, OrderScopeDenied
from .intake_preparation import IntakePreparation
from .util import canonical_json, digest_json, validate_id

SOURCE_STATES = {"pending", "reading", "rendering", "ready", "running", "complete", "failed", "unsupported", "blocked"}
ATTEMPT_STATES = {"prepared", "dispatching", "running", "completed", "failed", "cancelled", "unknown"}
TERMINAL_SOURCES = {"complete", "failed", "unsupported", "blocked"}


class AnalysisStore:
    def __init__(self, handoffs, *, clock=time.time):
        self.handoffs, self.jobs, self.clock = handoffs, handoffs.jobs, clock
        self.preparation = IntakePreparation(handoffs)
        with self.jobs._db() as con:
            for sql in (
                """CREATE TABLE IF NOT EXISTS analysis_plans (
                    plan_id TEXT PRIMARY KEY, handoff_id TEXT NOT NULL REFERENCES intake_handoffs(handoff_id),
                    plan_json BLOB NOT NULL, created_at REAL NOT NULL) STRICT""",
                """CREATE TABLE IF NOT EXISTS analysis_jobs (
                    job_id TEXT PRIMARY KEY, handoff_id TEXT NOT NULL REFERENCES intake_handoffs(handoff_id),
                    plan_id TEXT NOT NULL UNIQUE REFERENCES analysis_plans(plan_id),
                    status TEXT NOT NULL, stage TEXT NOT NULL, fence INTEGER NOT NULL DEFAULT 0,
                    lease_token TEXT, lease_until REAL, owner TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL) STRICT""",
                """CREATE TABLE IF NOT EXISTS analysis_sources (
                    job_id TEXT NOT NULL REFERENCES analysis_jobs(job_id), source_id TEXT NOT NULL,
                    state_json BLOB NOT NULL, PRIMARY KEY(job_id,source_id)) STRICT""",
                """CREATE TABLE IF NOT EXISTS analysis_attempts (
                    attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES analysis_jobs(job_id),
                    source_id TEXT NOT NULL, session_id TEXT NOT NULL UNIQUE, idempotency_key TEXT NOT NULL UNIQUE,
                    state_json BLOB NOT NULL, created_at REAL NOT NULL,
                    FOREIGN KEY(job_id,source_id) REFERENCES analysis_sources(job_id,source_id)) STRICT""",
                """CREATE TABLE IF NOT EXISTS analysis_proposals (
                    job_id TEXT NOT NULL, source_id TEXT NOT NULL, attempt_id TEXT NOT NULL REFERENCES analysis_attempts(attempt_id),
                    proposal_sha256 TEXT NOT NULL, proposal_json BLOB NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(job_id,source_id), FOREIGN KEY(job_id,source_id) REFERENCES analysis_sources(job_id,source_id)) STRICT""",
                """CREATE TABLE IF NOT EXISTS analysis_requests (
                    handoff_id TEXT NOT NULL, request_id TEXT NOT NULL, action TEXT NOT NULL,
                    payload_digest TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES analysis_jobs(job_id),
                    actor TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(handoff_id,request_id)) STRICT""",
                """CREATE TABLE IF NOT EXISTS analysis_control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), drained INTEGER NOT NULL) STRICT""",
            ):
                con.execute(sql)
            con.execute("INSERT OR IGNORE INTO analysis_control VALUES(1,0)")

    @staticmethod
    def _load_plan(con, plan_id):
        row = con.execute("SELECT plan_json FROM analysis_plans WHERE plan_id=?", (plan_id,)).fetchone()
        if row is None:
            raise NotFound("План разбора не найден")
        return json.loads(row[0])

    def _job(self, con, job_id):
        validate_id(job_id, field="job_id")
        row = con.execute("SELECT * FROM analysis_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise NotFound("Разбор не найден")
        return dict(row)

    def _current(self, con, job):
        plan = self._load_plan(con, job["plan_id"])
        handoff = self.handoffs._row(con, job["handoff_id"])
        return (self.handoffs._current(con, handoff)
                and self.preparation._answers(con, job["handoff_id"])["revision"] == plan["answers_revision"]
                and plan["recipe_fingerprint"] == recipe_fingerprint())

    @staticmethod
    def _observation(con, snapshot_id, source, job_id):
        row = con.execute("SELECT r.result_json,r.result_sha256,j.recipe_json FROM document_results r "
                          "JOIN document_jobs j USING(job_id) WHERE r.job_id=? AND r.source_id=? AND j.snapshot_id=?",
                          (job_id, source["source_id"], snapshot_id)).fetchone()
        if row is None:
            raise NotFound("Результат чтения источника ещё не опубликован")
        if hashlib.sha256(row[0]).hexdigest() != row[1]:
            raise Conflict("Контрольная сумма результата чтения не совпала")
        result = json.loads(row[0])
        if (result.get("source", {}).get("sha256") != source["sha256"]
                or result["source"].get("source_id") != source["source_id"]
                or result["source"].get("bytes") != source["bytes"]):
            raise OrderScopeDenied("Наблюдение относится к другому источнику")
        return result, json.loads(row[2])

    def plan(self, handoff_id):
        record = self.handoffs.get(handoff_id)
        projection = DocumentClassification(self.jobs.orders_root).get_snapshot(record["order_id"], record["snapshot_id"])
        if not projection["persisted"]:
            raise Conflict("Сначала обновите сведения о комплекте")
        with self.jobs._db() as con:
            record = self.handoffs._row(con, handoff_id)
            answers = self.preparation._answers(con, handoff_id)
            if (not self.handoffs._current(con, record) or record["dispatch_status"] != "finished"
                    or not record["session_created"] or answers["revision"] < 1):
                raise Conflict("Завершите передачу и сохраните начальные ответы для актуального комплекта")
            fingerprint = recipe_fingerprint()
            plan_id = "aplan_" + digest_json([handoff_id, record["snapshot_id"], answers["revision"],
                                             fingerprint, projection["projection_sha256"]])[:40]
            prior = con.execute("SELECT plan_json FROM analysis_plans WHERE plan_id=?", (plan_id,)).fetchone()
            if prior:
                return json.loads(prior[0])
            included = []
            for item in projection["sources"]:
                if item["classification"] == "service":
                    continue
                source = {k: item[k] for k in ("source_id", "index", "relative_path", "sha256", "bytes")}
                source.update(inspect_job_id=None, page_count=None, document_type=PurePosixPath(item["relative_path"]).suffix.lower().lstrip("."))
                cached = con.execute("SELECT j.job_id FROM document_results r JOIN document_jobs j USING(job_id) "
                                     "WHERE j.snapshot_id=? AND r.source_id=? AND r.result_status='complete' "
                                     "AND json_extract(j.recipe_json,'$.command')='inspect' ORDER BY r.published_at DESC LIMIT 1",
                                     (record["snapshot_id"], source["source_id"])).fetchone()
                if cached:
                    observation, _ = self._observation(con, record["snapshot_id"], source, cached[0])
                    source.update(inspect_job_id=cached[0], document_type=observation.get("document_type"),
                                  page_count=observation.get("coverage", {}).get("pages_total"))
                included.append(source)
            known_pages = sum(s["page_count"] or 0 for s in included if s["document_type"] == "pdf")
            blockers = []
            if len(included) > LIMITS["max_files"]:
                blockers.append("meaningful_files_limit")
            if not included:
                blockers.append("no_engineering_documents")
            if any((s["page_count"] or 0) > LIMITS["max_pages_per_pdf"] for s in included if s["document_type"] == "pdf"):
                blockers.append("pdf_page_limit")
            if known_pages > LIMITS["max_total_pdf_pages"]:
                blockers.append("order_page_limit")
            cached_count = sum(s["inspect_job_id"] is not None for s in included)
            plan = {"plan_id": plan_id, "handoff_id": handoff_id, "order_id": record["order_id"],
                    "snapshot_id": record["snapshot_id"], "answers_revision": answers["revision"],
                    "initial_answers": answers["answers"], "recipe_fingerprint": fingerprint,
                    "classification_sha256": projection["projection_sha256"], "limits": dict(LIMITS),
                    "files_total": projection["summary"]["files_total"], "included_sources": included,
                    "source_ids": [s["source_id"] for s in included],
                    "service_files": projection["summary"]["service_files"], "cached_documents": cached_count,
                    "documents_to_read": len(included) - cached_count, "known_pages": known_pages,
                    "pages_unknown": sum(s["document_type"] == "pdf" and s["page_count"] is None for s in included),
                    "can_start": not blockers, "blockers": blockers,
                    "intended_result": "Предварительный состав, источники и вопросы для проверки сотрудником",
                    "created_at": self.clock(), "human_approved": False, "use_for_calculation": False}
            con.execute("INSERT INTO analysis_plans VALUES(?,?,?,?)", (plan_id, handoff_id, canonical_json(plan), self.clock()))
            return plan

    def _request(self, con, handoff_id, request_id, action, payload):
        validate_id(request_id, field="request_id")
        row = con.execute("SELECT * FROM analysis_requests WHERE handoff_id=? AND request_id=?",
                          (handoff_id, request_id)).fetchone()
        if row and (row["action"] != action or row["payload_digest"] != digest_json(payload)):
            raise Conflict("Запрос уже использован для другого действия")
        return row

    def start(self, handoff_id, *, plan_id, request_id):
        with self.jobs._db() as con:
            plan = self._load_plan(con, plan_id)
            if plan["handoff_id"] != handoff_id:
                raise OrderScopeDenied("План относится к другой передаче")
            prior = self._request(con, handoff_id, request_id, "start", [plan_id])
            if prior:
                return self._public(con, self._job(con, prior["job_id"]))
            shell = {"plan_id": plan_id, "handoff_id": handoff_id}
            if not self._current(con, shell) or not plan["can_start"]:
                raise Conflict("План устарел или превышает показанные ограничения")
            job_id = "analysis_" + digest_json([handoff_id, plan_id])[:40]
            now = self.clock()
            inserted = con.execute("INSERT OR IGNORE INTO analysis_jobs "
                                   "(job_id,handoff_id,plan_id,status,stage,created_at,updated_at) VALUES(?,?,?,'queued','inventory',?,?)",
                                   (job_id, handoff_id, plan_id, now, now)).rowcount
            if inserted:
                for source in plan["included_sources"]:
                    state = {**source, "render_jobs": {}, "status": "pending", "error_code": None,
                             "attempt_id": None, "viewed_pages": []}
                    con.execute("INSERT INTO analysis_sources VALUES(?,?,?)", (job_id, source["source_id"], canonical_json(state)))
            con.execute("INSERT INTO analysis_requests VALUES(?,?,?,?,?,'panel',?)",
                        (handoff_id, request_id, "start", digest_json([plan_id]), job_id, now))
            return self._public(con, self._job(con, job_id))

    def _source_rows(self, con, job_id):
        rows = []
        for row in con.execute("SELECT state_json FROM analysis_sources WHERE job_id=? ORDER BY rowid", (job_id,)):
            source = json.loads(row[0])
            source["attempt"] = self._attempt(con, source["attempt_id"]) if source["attempt_id"] else None
            proposal = con.execute("SELECT proposal_json FROM analysis_proposals WHERE job_id=? AND source_id=?",
                                   (job_id, source["source_id"])).fetchone()
            source["proposal"] = json.loads(proposal[0]) if proposal else None
            rows.append(source)
        return rows

    def _public(self, con, job):
        rows = self._source_rows(con, job["job_id"])
        status = job["status"] if self._current(con, job) else "stale"
        summary = {"sources_total": len(rows), "sources_complete": sum(s["status"] == "complete" for s in rows),
                   "sources_failed": sum(s["status"] in {"failed", "unsupported", "blocked"} for s in rows)}
        issues = [{"source_id": s["source_id"], "relative_path": s["relative_path"], "code": s["error_code"],
                   "next_action": "Проверьте проблему источника; повтор выполняется отдельным действием сотрудника"}
                  for s in rows if s["error_code"]]
        public_rows = [{k: v for k, v in s.items() if k != "attempt"} for s in rows]
        active_attempt = any(s["attempt"] and s["attempt"]["status"] in {"prepared", "dispatching", "running", "unknown"} for s in rows)
        return {k: job[k] for k in ("job_id", "handoff_id", "plan_id", "stage", "created_at", "updated_at")} | {
            "status": status, "summary": summary, "rows": public_rows, "issues": issues,
            "cancellable": status in {"queued", "running", "blocked"},
            "retryable": status in {"partial", "blocked", "cancelled"} and not active_attempt
                         and any(s["status"] in {"failed", "unsupported", "blocked"} for s in rows),
            "human_approved": False, "use_for_calculation": False}

    def get(self, handoff_id):
        with self.jobs._db() as con:
            self.handoffs._row(con, handoff_id)
            plan = con.execute("SELECT plan_json FROM analysis_plans WHERE handoff_id=? ORDER BY rowid DESC LIMIT 1", (handoff_id,)).fetchone()
            job = con.execute("SELECT * FROM analysis_jobs WHERE handoff_id=? ORDER BY rowid DESC LIMIT 1", (handoff_id,)).fetchone()
            return {"plan": json.loads(plan[0]) if plan else None, "job": self._public(con, dict(job)) if job else None}

    @staticmethod
    def _attempt(con, attempt_id):
        row = con.execute("SELECT * FROM analysis_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        if row is None:
            raise NotFound("Попытка разбора не найдена")
        return {k: row[k] for k in row.keys() if k != "state_json"} | json.loads(row["state_json"])

    def drained(self):
        with self.jobs._db() as con:
            return bool(con.execute("SELECT drained FROM analysis_control").fetchone()[0])

    def set_drain(self, drained):
        if type(drained) is not bool:
            raise InvalidState("Нужен явный режим остановки приёма заданий")
        with self.jobs._db() as con:
            con.execute("UPDATE analysis_control SET drained=?", (int(drained),))

    def claim(self, owner, lease_seconds=120):
        validate_id(owner, field="owner")
        if type(lease_seconds) not in {int, float} or not 1 <= lease_seconds <= 600:
            raise InvalidState("Неверный срок аренды исполнителя")
        with self.jobs._db() as con:
            if con.execute("SELECT drained FROM analysis_control").fetchone()[0]:
                return None
            now = self.clock()
            if con.execute("SELECT 1 FROM analysis_jobs WHERE lease_until>?", (now,)).fetchone():
                return None
            for row in con.execute("SELECT * FROM analysis_jobs WHERE status IN ('queued','running') ORDER BY updated_at,rowid").fetchall():
                job = dict(row)
                if not self._current(con, job):
                    con.execute("UPDATE analysis_jobs SET status='stale',lease_token=NULL,lease_until=NULL WHERE job_id=?", (job["job_id"],))
                    continue
                token = secrets.token_urlsafe(32)
                fence = job["fence"] + 1
                con.execute("UPDATE analysis_jobs SET status='running',fence=?,lease_token=?,lease_until=?,owner=?,updated_at=? WHERE job_id=?",
                            (fence, hashlib.sha256(token.encode()).hexdigest(), now + lease_seconds, owner, now, job["job_id"]))
                plan = self._load_plan(con, job["plan_id"])
                return {k: plan[k] for k in ("order_id", "snapshot_id", "plan_id", "handoff_id", "answers_revision")} | {
                    "job_id": job["job_id"], "owner": owner, "fence": fence, "lease_token": token}
        return None

    def _authorize(self, con, claim):
        if not isinstance(claim, dict) or not isinstance(claim.get("lease_token"), str):
            raise OrderScopeDenied("Требуется аренда исполнителя разбора")
        job = self._job(con, claim.get("job_id", ""))
        if (job["status"] not in {"queued", "running"} or job["fence"] != claim.get("fence")
                or not job["lease_token"] or not job["lease_until"] or job["lease_until"] <= self.clock()
                or not hmac.compare_digest(job["lease_token"], hashlib.sha256(claim["lease_token"].encode()).hexdigest())
                or not self._current(con, job)):
            raise OrderScopeDenied("Аренда или версия разбора больше не действуют")
        return job

    def renew(self, claim, lease_seconds=120):
        if type(lease_seconds) not in {int, float} or not 1 <= lease_seconds <= 600:
            raise InvalidState("Неверный срок аренды исполнителя")
        with self.jobs._db() as con:
            job = self._authorize(con, claim)
            con.execute("UPDATE analysis_jobs SET lease_until=? WHERE job_id=?", (self.clock() + lease_seconds, job["job_id"]))
        return claim

    def release(self, claim):
        # Release may follow recompute/cancel; only the matching lease can be dropped.
        with self.jobs._db() as con:
            con.execute("UPDATE analysis_jobs SET lease_token=NULL,lease_until=NULL WHERE job_id=? AND fence=? AND lease_token=?",
                        (claim["job_id"], claim["fence"], hashlib.sha256(claim["lease_token"].encode()).hexdigest()))

    @staticmethod
    def _source(con, job_id, source_id):
        row = con.execute("SELECT state_json FROM analysis_sources WHERE job_id=? AND source_id=?", (job_id, source_id)).fetchone()
        if row is None:
            raise OrderScopeDenied("Источник не входит в этот разбор")
        return json.loads(row[0])

    @staticmethod
    def _save_source(con, job_id, source):
        con.execute("UPDATE analysis_sources SET state_json=? WHERE job_id=? AND source_id=?",
                    (canonical_json(source), job_id, source["source_id"]))

    def sources(self, claim):
        with self.jobs._db() as con:
            return self._source_rows(con, self._authorize(con, claim)["job_id"])

    def _dependency(self, con, job, source, dependency_id, *, command, page=None):
        row = con.execute("SELECT j.snapshot_id,j.recipe_json FROM document_jobs j JOIN document_chunks c USING(job_id) "
                          "WHERE j.job_id=? AND c.source_id=?", (dependency_id, source["source_id"])).fetchone()
        plan = self._load_plan(con, job["plan_id"])
        recipe = json.loads(row[1]) if row else {}
        if (row is None or row[0] != plan["snapshot_id"] or recipe.get("command") != command
                or command == "render" and (recipe.get("options", {}).get("page") != page
                    or any(recipe.get("options", {}).get(k) != v for k, v in RENDER_OPTIONS.items()))):
            raise OrderScopeDenied("Зависимость не соответствует источнику, снимку или странице")

    def update_source(self, claim, source_id, **changes):
        allowed = {"inspect_job_id", "render_jobs", "page_count", "document_type", "status", "error_code", "retry_requested"}
        if set(changes) - allowed:
            raise InvalidState("Недопустимое изменение источника")
        if "retry_requested" in changes and changes["retry_requested"] is not False:
            raise InvalidState("Повтор может разрешить только сотрудник")
        if "status" in changes and changes["status"] not in SOURCE_STATES:
            raise InvalidState("Неизвестное состояние источника")
        if "error_code" in changes and changes["error_code"] is not None and (not isinstance(changes["error_code"], str) or len(changes["error_code"]) > 100):
            raise InvalidState("Неверный код проблемы источника")
        if "page_count" in changes and changes["page_count"] is not None and (type(changes["page_count"]) is not int or not 1 <= changes["page_count"] <= 1000000):
            raise InvalidState("Неверное число страниц")
        if "document_type" in changes and changes["document_type"] not in {"pdf", "xlsx", "unknown", "unsupported"}:
            raise InvalidState("Неизвестный тип документа")
        with self.jobs._db() as con:
            job = self._authorize(con, claim)
            source = self._source(con, job["job_id"], source_id)
            if source["attempt_id"] and any(k in changes and changes[k] != source[k]
                                             for k in ("inspect_job_id", "render_jobs", "page_count", "document_type")):
                raise Conflict("Источники модельной попытки уже зафиксированы")
            if source["status"] in TERMINAL_SOURCES and changes != {k: source.get(k) for k in changes}:
                raise Conflict("Завершённый источник меняется только явным повтором")
            if "inspect_job_id" in changes:
                self._dependency(con, job, source, changes["inspect_job_id"], command="inspect")
            if "render_jobs" in changes:
                mapping = changes["render_jobs"]
                if not isinstance(mapping, dict) or len(mapping) > LIMITS["max_pages_per_pdf"]:
                    raise InvalidState("Слишком много страниц изображения")
                for page, dependency_id in mapping.items():
                    if not isinstance(page, str) or not page.isdecimal() or str(int(page)) != page or not 1 <= int(page) <= LIMITS["max_pages_per_pdf"]:
                        raise InvalidState("Неверная страница изображения")
                    self._dependency(con, job, source, dependency_id, command="render", page=int(page))
            if changes.get("status") == "complete":
                if not con.execute("SELECT 1 FROM analysis_proposals WHERE job_id=? AND source_id=?", (job["job_id"], source_id)).fetchone():
                    raise Conflict("Нужна сохранённая публикация состава")
                attempt = self._attempt(con, source["attempt_id"])
                if attempt["status"] not in {"completed", "failed", "cancelled"}:
                    raise Conflict("Модельный запуск ещё не завершён")
            source.update(changes)
            self._save_source(con, job["job_id"], source)
            con.execute("UPDATE analysis_jobs SET updated_at=? WHERE job_id=?", (self.clock(), job["job_id"]))
            return source

    def _gate(self, con, job):
        plan = self._load_plan(con, job["plan_id"])
        blockers, pages = [], 0
        rows = self._source_rows(con, job["job_id"])
        if len(rows) > LIMITS["max_files"]:
            blockers.append("meaningful_files_limit")
        for source in rows:
            if source["document_type"] not in {"pdf", "xlsx"}:
                continue
            failed_source = source["status"] in {"failed", "unsupported", "blocked"}
            if failed_source and source["document_type"] == "xlsx":
                continue
            if not source["inspect_job_id"]:
                blockers.append("inventory_incomplete" if failed_source else "inventory_pending")
                continue
            try:
                result, recipe = self._observation(con, plan["snapshot_id"], source, source["inspect_job_id"])
            except NotFound:
                dependency = con.execute("SELECT status FROM document_jobs WHERE job_id=?",
                                         (source["inspect_job_id"],)).fetchone()
                waiting = not failed_source and dependency and dependency[0] in {"queued", "running"}
                blockers.append("inventory_pending" if waiting else "inventory_incomplete")
                continue
            coverage = result.get("coverage", {})
            if (recipe["command"] != "inspect" or coverage.get("inventory_complete") is not True
                    or result["status"] != "complete" and not failed_source):
                blockers.append("inventory_incomplete")
                continue
            if source["document_type"] == "pdf":
                count = coverage.get("pages_total")
                if type(count) is not int or count < 1:
                    blockers.append("inventory_incomplete")
                else:
                    pages += count
                    if count > LIMITS["max_pages_per_pdf"]:
                        blockers.append("pdf_page_limit")
            elif coverage.get("cells_read", 0) > LIMITS["max_xlsx_cells"] or coverage.get("cells_complete") is not True:
                blockers.append("xlsx_cell_limit")
            if result["status"] == "complete":
                try:
                    source_contents({**source, "page_count": coverage.get("pages_total")}, result)
                except InvalidState:
                    blockers.append("model_context_limit")
        if pages > LIMITS["max_total_pdf_pages"]:
            blockers.append("order_page_limit")
        return {"ready": not blockers, "blockers": sorted(set(blockers)), "known_pages": pages}

    def gate(self, claim):
        with self.jobs._db() as con:
            return self._gate(con, self._authorize(con, claim))

    def create_attempt(self, claim, source_id):
        with self.jobs._db() as con:
            job = self._authorize(con, claim)
            source = self._source(con, job["job_id"], source_id)
            if source["attempt_id"]:
                return self._attempt(con, source["attempt_id"])
            if source["status"] in TERMINAL_SOURCES or not self._gate(con, job)["ready"]:
                raise Conflict("Источник или общий план не готов к смысловому разбору")
            if con.execute("SELECT 1 FROM analysis_attempts WHERE json_extract(state_json,'$.status') "
                           "IN ('prepared','dispatching','running','unknown')").fetchone():
                raise Conflict("Другая модельная попытка ещё не завершена")
            plan = self._load_plan(con, job["plan_id"])
            observation, _ = self._observation(con, plan["snapshot_id"], source, source["inspect_job_id"])
            if observation["status"] != "complete" or observation.get("document_type") not in {"pdf", "xlsx"}:
                raise Conflict("Нужно полное наблюдение поддержанного источника")
            source["document_type"] = observation["document_type"]
            source["page_count"] = observation.get("coverage", {}).get("pages_total")
            if source["document_type"] == "pdf":
                for page in range(1, source["page_count"] + 1):
                    render_id = source["render_jobs"].get(str(page))
                    self._dependency(con, job, source, render_id, command="render", page=page)
                    rendered, _ = self._observation(con, plan["snapshot_id"], source, render_id)
                    if rendered["status"] != "complete" or not rendered.get("image"):
                        raise Conflict("Все страницы должны быть подготовлены до запуска модели")
                    if not 0 < rendered["image"]["bytes"] <= LIMITS["max_model_image_bytes"]:
                        raise Conflict("Изображение страницы превышает предел модельного входа")
            attempt_id = "aattempt_" + secrets.token_hex(20)
            session_id = "analysis_" + secrets.token_hex(20)
            state = {"status": "prepared", "run_id": None, "request_body": None, "error_code": None}
            con.execute("INSERT INTO analysis_attempts VALUES(?,?,?,?,?,?,?)", (attempt_id, job["job_id"], source_id,
                        session_id, "analysis-" + secrets.token_hex(20), canonical_json(state), self.clock()))
            source.update(attempt_id=attempt_id, status="running", viewed_pages=[])
            self._save_source(con, job["job_id"], source)
            return self._attempt(con, attempt_id)

    def record_cancelled_dispatch(self, attempt_id, run_id):
        """Keep late native admission identifiable for cancellation, never revive it."""
        validate_id(run_id, field="run_id")
        with self.jobs._db() as con:
            attempt = self._attempt(con, attempt_id)
            job = self._job(con, attempt["job_id"])
            if (job["status"] not in {"cancelled", "stale"} and self._current(con, job)
                    or attempt["status"] not in {"dispatching", "unknown"}
                    or not attempt["request_body"] or attempt["run_id"] not in {None, run_id}):
                raise Conflict("Поздний запуск не соответствует отменённой попытке")
            state = {k: attempt[k] for k in ("status", "run_id", "request_body", "error_code")}
            state["run_id"] = run_id
            con.execute("UPDATE analysis_attempts SET state_json=? WHERE attempt_id=?", (canonical_json(state), attempt_id))
            return self._attempt(con, attempt_id)

    def update_attempt(self, claim, attempt_id, **changes):
        if set(changes) - {"status", "run_id", "request_body", "error_code"}:
            raise InvalidState("Недопустимое изменение попытки")
        if "status" in changes and changes["status"] not in ATTEMPT_STATES:
            raise InvalidState("Неизвестное состояние попытки")
        if "error_code" in changes and changes["error_code"] is not None and (not isinstance(changes["error_code"], str) or len(changes["error_code"]) > 100):
            raise InvalidState("Неверный код проблемы попытки")
        transitions = {"prepared": {"prepared", "dispatching", "failed", "cancelled"},
                       "dispatching": {"dispatching", "running", "failed", "unknown"},
                       "running": {"running", "completed", "failed", "cancelled", "unknown"},
                       "unknown": {"unknown"}, "completed": {"completed"},
                       "failed": {"failed"}, "cancelled": {"cancelled"}}
        with self.jobs._db() as con:
            job = self._authorize(con, claim)
            attempt = self._attempt(con, attempt_id)
            if attempt["job_id"] != job["job_id"] or self._source(con, job["job_id"], attempt["source_id"])["attempt_id"] != attempt_id:
                raise OrderScopeDenied("Попытка относится к другому источнику или запуску")
            if changes.get("status", attempt["status"]) not in transitions[attempt["status"]]:
                raise Conflict("Нельзя повторно запустить или изменить завершённую попытку")
            for key in ("run_id", "request_body"):
                if key in changes and attempt[key] is not None and changes[key] != attempt[key]:
                    raise Conflict("Параметры модельного запуска уже зафиксированы")
            if "run_id" in changes:
                validate_id(changes["run_id"], field="run_id")
            if "request_body" in changes:
                body = changes["request_body"]
                if not isinstance(body, dict) or body.get("session_id") != attempt["session_id"] or len(canonical_json(body)) > 32000:
                    raise InvalidState("Неверное тело модельного запуска")
                if attempt["status"] != "prepared" and body != attempt["request_body"]:
                    raise Conflict("Тело запроса должно быть сохранено до отправки")
            updated = attempt | changes
            if updated["status"] == "dispatching" and updated["request_body"] is None:
                raise Conflict("Перед отправкой нужно зафиксировать тело запроса")
            if updated["status"] == "running" and not updated["run_id"]:
                raise Conflict("Не указан принятый модельный запуск")
            state = {k: updated[k] for k in ("status", "run_id", "request_body", "error_code")}
            con.execute("UPDATE analysis_attempts SET state_json=? WHERE attempt_id=?", (canonical_json(state), attempt_id))
            return self._attempt(con, attempt_id)

    def _binding(self, con, session_id, *, published_replay=False):
        validate_id(session_id, field="session_id")
        row = con.execute("SELECT attempt_id FROM analysis_attempts WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise OrderScopeDenied("Сессия не связана с попыткой разбора")
        attempt = self._attempt(con, row[0])
        job = self._job(con, attempt["job_id"])
        source = self._source(con, job["job_id"], attempt["source_id"])
        saved = con.execute("SELECT 1 FROM analysis_proposals WHERE job_id=? AND source_id=? AND attempt_id=?",
                            (job["job_id"], source["source_id"], attempt["attempt_id"])).fetchone()
        if (not self._current(con, job) or job["status"] in {"cancelled", "stale"}
                or source["attempt_id"] != attempt["attempt_id"]
                or not (published_replay and saved) and (job["status"] != "running" or attempt["status"] not in {"dispatching", "running"})):
            raise OrderScopeDenied("Сессия или версия разбора больше не действуют")
        plan = self._load_plan(con, job["plan_id"])
        observation, _ = self._observation(con, plan["snapshot_id"], source, source["inspect_job_id"])
        return {"job": self._public(con, job), "plan": plan, "source": source, "attempt": attempt,
                "observation": observation}

    def for_session(self, session_id):
        """Exact identity only: copied/forked/compressed sessions inherit no scope."""
        with self.jobs._db() as con:
            return self._binding(con, session_id)

    def record_view(self, session_id, page):
        if type(page) is not int:
            raise InvalidState("Нужен номер страницы")
        with self.jobs._db() as con:
            binding = self._binding(con, session_id)
            source, plan = binding["source"], binding["plan"]
            if source["document_type"] != "pdf" or not 1 <= page <= (source["page_count"] or 0):
                raise InvalidState("Страница не принадлежит назначенному PDF")
            rendered, recipe = self._observation(con, plan["snapshot_id"], source, source["render_jobs"].get(str(page)))
            if (recipe["command"] != "render" or recipe.get("options", {}).get("page") != page
                    or rendered["status"] != "complete" or not rendered.get("image")):
                raise Conflict("Изображение страницы ещё не подготовлено")
            source["viewed_pages"] = sorted(set(source["viewed_pages"]) | {page})
            self._save_source(con, binding["job"]["job_id"], source)
            return {"viewed_pages": source["viewed_pages"]}

    def submit(self, session_id, proposal):
        from .analysis_proposals import validate_proposal

        with self.jobs._db() as con:
            binding = self._binding(con, session_id, published_replay=True)
            source, attempt = binding["source"], binding["attempt"]
            normalized = validate_proposal(proposal, source=source, page_count=source["page_count"],
                                           viewed_pages=source["viewed_pages"], observation=binding["observation"])
            digest = digest_json(normalized)
            prior = con.execute("SELECT proposal_sha256,attempt_id FROM analysis_proposals WHERE job_id=? AND source_id=?",
                                (attempt["job_id"], source["source_id"])).fetchone()
            if prior and (prior[0] != digest or prior[1] != attempt["attempt_id"]):
                raise Conflict("Для источника уже сохранено другое предложение")
            if not prior:
                con.execute("INSERT INTO analysis_proposals VALUES(?,?,?,?,?,?)", (attempt["job_id"], source["source_id"],
                            attempt["attempt_id"], digest, canonical_json(normalized), self.clock()))
            return {"job_id": attempt["job_id"], "source_id": source["source_id"], "proposal_sha256": digest,
                    "status": "proposal_saved", "human_approved": False, "use_for_calculation": False}

    def recompute(self, claim):
        with self.jobs._db() as con:
            job = self._authorize(con, claim)
            rows = self._source_rows(con, job["job_id"])
            if any(s["attempt"] and s["attempt"]["status"] == "unknown" for s in rows):
                status, stage = "blocked", "review"
            elif all(s["status"] in TERMINAL_SOURCES for s in rows):
                status = ("completed" if all(s["status"] == "complete" for s in rows) else
                          "partial" if any(s["status"] == "complete" for s in rows) else "blocked")
                stage = "review"
            else:
                status = "running"
                stage = ("analysis" if any(s["attempt_id"] for s in rows) else
                         "rendering" if any(s["status"] in {"rendering", "ready"} for s in rows) else "inventory")
            con.execute("UPDATE analysis_jobs SET status=?,stage=?,updated_at=? WHERE job_id=?",
                        (status, stage, self.clock(), job["job_id"]))
            return self._public(con, self._job(con, job["job_id"]))

    finish = recompute

    def cancel(self, handoff_id, job_id):
        with self.jobs._db() as con:
            job = self._job(con, job_id)
            if job["handoff_id"] != handoff_id:
                raise OrderScopeDenied("Разбор относится к другой передаче")
            if job["status"] == "cancelled":
                return self._public(con, job)
            con.execute("UPDATE analysis_jobs SET status='cancelled',fence=fence+1,lease_token=NULL,lease_until=NULL,updated_at=? WHERE job_id=?",
                        (self.clock(), job_id))
            for row in self._source_rows(con, job_id):
                source = self._source(con, job_id, row["source_id"])
                if source["status"] != "complete":
                    source.update(status="blocked", error_code="analysis_cancelled")
                    self._save_source(con, job_id, source)
            return self._public(con, self._job(con, job_id))

    def retry(self, handoff_id, job_id, request_id):
        with self.jobs._db() as con:
            job = self._job(con, job_id)
            if job["handoff_id"] != handoff_id:
                raise OrderScopeDenied("Разбор относится к другой передаче")
            prior = self._request(con, handoff_id, request_id, "retry", [job_id])
            if prior:
                return self._public(con, job)
            if not self._current(con, job) or not self._public(con, job)["retryable"]:
                raise Conflict("Повтор возможен после подтверждённого завершения неудачной попытки")
            for row in self._source_rows(con, job_id):
                source = self._source(con, job_id, row["source_id"])
                if source["status"] == "complete":
                    continue
                if row["proposal"]:
                    source.update(status="complete", error_code=None)
                else:
                    source.update(status="pending", attempt_id=None, viewed_pages=[], error_code=None, retry_requested=True)
                self._save_source(con, job_id, source)
            con.execute("UPDATE analysis_jobs SET status='queued',stage='inventory',fence=fence+1,lease_token=NULL,lease_until=NULL,updated_at=? WHERE job_id=?",
                        (self.clock(), job_id))
            con.execute("INSERT INTO analysis_requests VALUES(?,?,?,?,?,'panel',?)", (handoff_id, request_id, "retry", digest_json([job_id]), job_id, self.clock()))
            return self._public(con, self._job(con, job_id))

    def cancelled_attempts(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidState("Неверный размер списка попыток")
        with self.jobs._db() as con:
            result = []
            rows = con.execute("SELECT attempt_id FROM analysis_attempts WHERE json_extract(state_json,'$.status') "
                               "IN ('prepared','dispatching','running','unknown') ORDER BY created_at LIMIT 100").fetchall()
            for row in rows:
                attempt = self._attempt(con, row[0])
                job = self._job(con, attempt["job_id"])
                if job["status"] in {"cancelled", "stale"} or not self._current(con, job):
                    result.append(attempt)
                    if len(result) == limit:
                        break
            return result

    def mark_cancelled(self, attempt_id):
        """Cleanup bookkeeping only, after native cancellation was confirmed."""
        with self.jobs._db() as con:
            attempt = self._attempt(con, attempt_id)
            job = self._job(con, attempt["job_id"])
            if job["status"] not in {"cancelled", "stale"} and self._current(con, job):
                raise Conflict("Действующая попытка не может быть отменена через очистку")
            state = {k: attempt[k] for k in ("status", "run_id", "request_body", "error_code")}
            state["status"] = "cancelled"
            con.execute("UPDATE analysis_attempts SET state_json=? WHERE attempt_id=?", (canonical_json(state), attempt_id))
            return self._attempt(con, attempt_id)
