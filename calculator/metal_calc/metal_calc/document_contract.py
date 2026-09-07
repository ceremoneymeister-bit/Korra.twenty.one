"""Document preparation is separate from the single-part costing workflow."""
from __future__ import annotations

import re
from typing import Any

from .errors import InvalidState
from .util import digest_json

SCHEMA_VERSION = 1
JOB_STATES = {"queued", "running", "completed", "partial", "cancelled", "stale", "blocked"}
RESULT_STATES = {"complete", "partial", "failed", "unsupported"}


def source_manifest(state: dict) -> dict:
    intake = state.get("folder_intake")
    if not isinstance(intake, dict):
        raise InvalidState("Заказ не содержит загруженной папки")
    from .folder_intake import validate_upload_id, _relative_path
    upload_id = validate_upload_id(intake.get("upload_id"))
    files = intake.get("files")
    if not isinstance(files, list) or len(files) > 10000:
        raise InvalidState("Неверный список документов")
    entries = []
    for index, item in enumerate(files):
        if (not isinstance(item, dict) or item.get("index") != index
                or type(item.get("bytes")) is not int or not 0 <= item["bytes"] <= 100 * 1024**2
                or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))):
            raise InvalidState("Неверная квитанция исходного документа")
        entries.append({"index": index, "relative_path": _relative_path(item["relative_path"]),
                        "bytes": item["bytes"], "sha256": item["sha256"]})
    return {"upload_id": upload_id, "files": entries,
            "directories": sorted(intake.get("directories", []))}


def snapshot_sources(order_id: str, manifest: dict) -> list[dict]:
    digest = digest_json(manifest)
    return [{**item, "source_id": "src_" + digest_json([order_id, digest, item["index"]])[:40]}
            for item in manifest["files"]]


def empty_composition() -> dict:
    return {"schema_version": SCHEMA_VERSION, "status": "not_proposed", "positions": [],
            "facts": [], "evidence": [], "relations": [], "issues": [
                {"code": "scope_review_required", "blocks": "composition_acceptance",
                 "question": "Какие изделия и работы входят в заказ?"},
                {"code": "quantity_required", "blocks": "calculation",
                 "question": "Укажите тираж изготовления и его источник"}],
            "quantity": {"value": None, "unit": None, "basis": None, "evidence_ids": []},
            "human_receipt": None, "calculation_ready": False, "quote_ready": False}


def validate_observation(result: Any, source: dict, command: str) -> None:
    if not isinstance(result, dict):
        raise InvalidState("Нужен типизированный результат чтения документа")
    evidence = result.get("source")
    if not isinstance(evidence, dict):
        raise InvalidState("Источник должен быть объектом")
    if (result.get("schema_version") != 2 or result.get("command") != command
            or result.get("status") not in RESULT_STATES
            or type(result.get("complete")) is not bool
            or result["complete"] != (result["status"] == "complete")
            or result.get("use_for_calculation") is not False
            or evidence.get("source_id") != source["source_id"]
            or evidence.get("sha256") != source["sha256"]
            or evidence.get("bytes") != source["bytes"]
            or not isinstance(result.get("coverage"), dict)
            or not isinstance(result.get("reader"), dict)
            or not isinstance(result.get("errors"), list)):
        raise InvalidState("Результат не соответствует источнику или схеме чтения")
    if result["status"] == "complete" and evidence.get("sha256_verified") is not True:
        raise InvalidState("Результат без сверки SHA не может быть полным")
    verification = result.get("verification")
    if (not isinstance(verification, dict) or verification.get("use_for_calculation") is not False
            or verification.get("numeric_facts") != "unverified"):
        raise InvalidState("Чтение документа не подтверждает числовые факты")
    # S1 has no proposal/approval writer. Text and native completed cannot
    # smuggle a human receipt or legacy calculation state into publication.
    if any(key in result for key in ("human_receipt", "approved_by", "bom", "calculation_ready")):
        raise InvalidState("Результат чтения содержит недопустимое подтверждение")
    coverage = result["coverage"]
    for key in ("pages_total", "pages_inventoried", "pages_accounted", "sheets_total", "sheets_inventoried", "cells_read"):
        value = coverage.get(key)
        if value is not None and (type(value) is not int or not 0 <= value <= 1000000):
            raise InvalidState("Неверный счётчик coverage")
    for key in ("inventory_complete", "text_truncated", "cells_complete"):
        if key in coverage and type(coverage[key]) is not bool:
            raise InvalidState("Неверное состояние coverage")
    for key in ("selected_text_pages", "text_pages_read", "rendered_pages"):
        values = coverage.get(key, [])
        if (not isinstance(values, list) or len(values) > 2000
                or any(type(n) is not int or not 1 <= n <= 2000 for n in values)
                or len(values) != len(set(values))
                or coverage.get("pages_total") is not None and any(n > coverage["pages_total"] for n in values)):
            raise InvalidState("Неверные страницы coverage")
    for read, total in (("pages_inventoried", "pages_total"), ("sheets_inventoried", "sheets_total")):
        if coverage.get(total) is not None and (coverage.get(read) or 0) > coverage[total]:
            raise InvalidState("Coverage превышает число источников")
    if any(not isinstance(e, dict) or not isinstance(e.get("code"), str)
           or len(e["code"]) > 100 or not isinstance(e.get("message"), str)
           or len(e["message"]) > 400 for e in result["errors"]):
        raise InvalidState("Неверный список ошибок документа")
    if result["complete"] and (result["errors"] or coverage.get("text_truncated") is True):
        raise InvalidState("Неполный результат не может быть complete")
    if result["complete"] and command == "inspect" and coverage.get("inventory_complete") is not True:
        raise InvalidState("Полный inspect требует завершённого inventory")
    if result["complete"] and command == "inspect":
        fields = {"pdf": ("pages_total", "pages_inventoried"), "xlsx": ("sheets_total", "sheets_inventoried")}
        if result.get("document_type") not in fields:
            raise InvalidState("Неподдержанный документ не может быть complete")
        total, read = fields[result["document_type"]]
        if type(coverage.get(total)) is not int or coverage.get(read) != coverage[total]:
            raise InvalidState("Полный inspect требует учёта всех страниц/листов")
        if result["document_type"] == "pdf" and coverage.get("pages_accounted") != coverage[total]:
            raise InvalidState("Полный inspect требует учёта всех страниц")
    if result["complete"] and command == "render" and not isinstance(result.get("image"), dict):
        raise InvalidState("Полный render требует сохранённого изображения")


def observation_summary(result: dict) -> dict:
    return {"status": result["status"], "coverage": result["coverage"],
            "errors": result["errors"][:20], "error_count": len(result["errors"]),
            "has_image": bool(result.get("image"))}
