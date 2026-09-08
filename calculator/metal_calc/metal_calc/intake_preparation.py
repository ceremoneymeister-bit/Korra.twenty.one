"""Operator-owned initial answers, separate from model facts and run admission."""
from __future__ import annotations

import json

from .errors import Conflict, InvalidState
from .intake_handoffs import IntakeHandoffs
from .util import digest_json, validate_id


DEFAULT_ANSWERS = {"scope": "unknown", "scope_note": "", "more_documents": "unknown",
                   "quantity_source": "unknown", "notes": ""}


def validate_answers(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_ANSWERS):
        raise InvalidState("Укажите охват, дополнительные документы и источник количеств")
    choices = {"scope": {"whole", "selected", "unknown"},
               "more_documents": {"yes", "no", "unknown"},
               "quantity_source": {"in_documents", "separate", "unknown"}}
    for key, allowed in choices.items():
        if not isinstance(value[key], str) or value[key] not in allowed:
            raise InvalidState("Неизвестный вариант начального ответа")
    for key in ("scope_note", "notes"):
        if not isinstance(value[key], str) or len(value[key]) > 2000:
            raise InvalidState("Примечание должно содержать не более 2000 символов")
    result = {**value, "scope_note": value["scope_note"].strip(), "notes": value["notes"].strip()}
    if result["scope"] == "selected" and not result["scope_note"]:
        raise InvalidState("Укажите выбранные изделия или работы")
    return result


class IntakePreparation:
    def __init__(self, handoffs: IntakeHandoffs):
        self.handoffs = handoffs
        with handoffs.jobs._db() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS intake_answer_decisions (
                decision_id TEXT PRIMARY KEY,
                handoff_id TEXT NOT NULL REFERENCES intake_handoffs(handoff_id),
                revision INTEGER NOT NULL, request_id TEXT NOT NULL,
                payload_digest TEXT NOT NULL, answers_json TEXT NOT NULL,
                actor TEXT NOT NULL, source TEXT NOT NULL,
                reference_json TEXT, recorded_at REAL NOT NULL,
                UNIQUE(handoff_id,revision), UNIQUE(handoff_id,request_id)) STRICT""")

    @staticmethod
    def _answers(con, handoff_id):
        row = con.execute("SELECT * FROM intake_answer_decisions WHERE handoff_id=? "
                          "ORDER BY revision DESC LIMIT 1", (handoff_id,)).fetchone()
        if row is None:
            return {"revision": 0, "answers": dict(DEFAULT_ANSWERS), "receipt": None}
        return {"revision": row["revision"], "answers": json.loads(row["answers_json"]),
                "receipt": {key: row[key] for key in
                            ("actor", "source", "recorded_at", "decision_id")} | {
                    "reference": json.loads(row["reference_json"]) if row["reference_json"] else None}}

    def answers(self, handoff_id):
        with self.handoffs.jobs._db() as con:
            self.handoffs._row(con, handoff_id)
            return self._answers(con, handoff_id)

    def view(self, handoff_id, *, classify=False):
        from .document_classification import DocumentClassification

        record = self.handoffs.get(handoff_id)
        classifier = DocumentClassification(self.handoffs.jobs.orders_root)
        project = classifier.ensure_snapshot if classify else classifier.get_snapshot
        projection = project(record["order_id"], record["snapshot_id"])
        _, cached = self.handoffs._cached(record)
        sources = projection["sources"]
        documents = [s for s in sources if s["classification"] != "service"]
        service = [s for s in sources if s["classification"] == "service"]
        reasons = {
            "service_candidate_unreadable": "Не удалось проверить служебный файл. Повторите обновление сведений.",
            "source_integrity_conflict": "Содержимое не совпадает с квитанцией загрузки. Проверьте исходник и передайте исправленный комплект.",
            "service_name_content_conflict": "Содержимое не подтверждает служебный кэш. Файл оставлен среди документов для проверки.",
            "service_candidate_size_out_of_bounds": "Размер выходит за пределы проверки служебного кэша. Файл требует отдельной проверки.",
            "service_candidate_budget_exceeded": "Достигнут предел проверки служебных файлов в комплекте. Файл оставлен для отдельной проверки.",
            "unknown_format": "Назначение и поддержка формата требуют отдельной проверки; исходник сохранён.",
        }
        summary = {**projection["summary"],
                   "cached_engineering_documents": sum(s["source_id"] in cached for s in documents),
                   "documents_without_observation": sum(s["source_id"] not in cached for s in documents),
                   "unsupported_documents": sum(cached.get(s["source_id"], {}).get("status") == "unsupported"
                                                for s in documents),
                   "failed_documents": sum(cached.get(s["source_id"], {}).get("status") == "failed" for s in documents),
                   "partial_documents": sum(cached.get(s["source_id"], {}).get("status") == "partial" for s in documents),
                   "service_file_names": [s["relative_path"] for s in service[:50]],
                   "classification_issues": [{"source_id": s["source_id"], "relative_path": s["relative_path"],
                       "reason": s["reason"], "message": reasons[s["reason"]]}
                       for s in sources if s["reason"] in reasons][:50],
                   "classification_pending": not projection["persisted"]}
        editable = bool(record["session_created"] and not record["initial_run_active"]
                        and record["status"] != "stale" and record["error_code"] not in
                        {"dispatch_unknown", "dispatch_interrupted", "run_unavailable"})
        return {key: record[key] for key in ("handoff_id", "order_id", "snapshot_id", "document_set_revision")} | {
            "editable": editable, "summary": summary, "initial_answers": self.answers(handoff_id)}

    def save(self, handoff_id, *, snapshot_id, expected_revision, request_id, answers,
             actor="panel", source="operator_cabinet", reference=None):
        """Only the operator admin surface calls this; never a model tool."""
        validate_id(request_id, field="request_id")
        if type(expected_revision) is not int or expected_revision < 0:
            raise InvalidState("Некорректная версия начальных ответов")
        if source not in {"operator_cabinet", "operator_chat"} or not isinstance(actor, str) or not actor:
            raise InvalidState("Не указан источник решения оператора")
        normalized = validate_answers(answers)
        payload_digest = digest_json([snapshot_id, expected_revision, normalized, actor, source, reference])
        with self.handoffs.jobs._db() as con:
            record = self.handoffs._row(con, handoff_id)
            if snapshot_id != record["snapshot_id"] or not self.handoffs._current(con, record):
                raise Conflict("Комплект изменился; откройте актуальную передачу заказа")
            prior = con.execute("SELECT payload_digest FROM intake_answer_decisions "
                                "WHERE handoff_id=? AND request_id=?", (handoff_id, request_id)).fetchone()
            if prior:
                if prior[0] != payload_digest:
                    raise Conflict("Этот запрос уже использован для другого ответа")
                # A late replay must not replace a newer operator decision.
                return self._answers(con, handoff_id)
            current = self._answers(con, handoff_id)
            if current["revision"] != expected_revision:
                raise Conflict("Ответы уже изменились; обновите их перед сохранением")
            if (not record["session_created"] or record["dispatch_status"] in {"prepared", "connecting", "running"}
                    or record["error_code"] in {"dispatch_unknown", "dispatch_interrupted", "run_unavailable"}):
                raise Conflict("Дождитесь завершения передачи заказа приёмщику")
            revision = expected_revision + 1
            decision_id = "answer_" + digest_json([handoff_id, request_id])[:40]
            con.execute("INSERT INTO intake_answer_decisions "
                        "(decision_id,handoff_id,revision,request_id,payload_digest,answers_json,"
                        "actor,source,reference_json,recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (decision_id, handoff_id, revision, request_id, payload_digest,
                         json.dumps(normalized, ensure_ascii=False), actor, source,
                         json.dumps(reference, ensure_ascii=False) if reference else None,
                         self.handoffs.jobs.clock()))
            return self._answers(con, handoff_id)
