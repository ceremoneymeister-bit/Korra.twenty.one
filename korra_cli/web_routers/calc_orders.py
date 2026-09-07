"""Заказы расчётчика — чтение legacy- и workflow-реестра для панели.

Реестр заказов (`metal_calc`) хранит два поколения заказов. Legacy использует
стадии ``blank → route → time → quote``. Workflow v8 хранит отдельные
``workflow``, ``bom``, ``route_variants``, ``costing``, ``book``, ``qa`` и
``workflow_events``. Смешивать эти формы нельзя: иначе готовый v8-заказ
выглядит как четыре пустые legacy-стадии.

Этот роутер отдаёт то же состояние панели. Два human-gated действия — QA и
типизированное КП подрядчика — проходят через операторский CLI после auth;
модельного инструмента записи для КП нет.

Почему на чтение. Утверждение стадии и подтверждение цены снабжением — это
бизнес-логика ``service2`` (ревизии, проверки конфликтов, событие в ленте, а
для металла ещё и запрет утверждать стадию с непроверенной ценой). Второй
путь записи, поднятый из панели мимо этих проверок, разошёлся бы с
инструментами в первый же спорный случай. Поэтому остальные кнопки экрана
«Заказы» уводят к нужному агенту, а операторские формы вызывают канонический
сервис через CLI.

Где лежит реестр, знает конфиг контура: ``mcp_servers.metal_calc.env``
``METAL_CALC_ORDERS_ROOT``. Читаем оттуда, а не из своей константы, — иначе
панель и агент разъедутся по каталогам на первом же нестандартном деплое.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException

from korra_cli.web_deps import late
from korra_cli.web_routers.calc_rates import _run_admin
from korra_cli.web_routers import calc_files
from korra_cli.web_routers import calc_documents

_log = logging.getLogger("korra_cli.web_server")

router = APIRouter()
router.include_router(calc_files.router)
router.include_router(calc_documents.router)

# Late-bound: тесты подменяют их на web_server, и прямой импорт разошёлся бы
# с подменой.
get_hermes_home = late("get_hermes_home")
load_config = late("load_config")

#: Порядок стадий конвейера. Он же задаёт порядок на экране.
STAGE_ORDER = ("blank", "route", "time", "quote")

#: Подписи стадий на языке расчётчика, а не на языке таблицы.
STAGE_TITLES = {
    "blank": "Заготовка",
    "route": "Маршрут",
    "time": "Нормы времени",
    "quote": "КП",
}

#: Кто ведёт стадию — вкладка агента, к которой уводит кнопка действия.
STAGE_AGENTS = {
    "blank": "raschet-blank",
    "route": "raschet-route",
    "time": "raschet-time",
    "quote": "raschet-time",
}

#: Дословный порядок и подписи машины workflow. Код статуса остаётся в API
#: рядом с подписью: оператор сверяет его со схемой, а не получает наш пересказ.
WORKFLOW_STATUS_ORDER = (
    "INPUT_FROZEN",
    "BOM_VALIDATED",
    "ROUTE_OPTIONS_READY",
    "PRELIMINARY_READY",
    "ROUTE_SELECTED",
    "ROUTE_FROZEN",
    "DETAILED_COSTING",
    "COSTING_COMPLETE",
    "BOOK_ASSEMBLED",
    "QA_MECHANICAL_PASS",
    "QA_TECHNOLOGICAL_PASS",
    "READY_FOR_LD",
)

WORKFLOW_STATUS_TITLES = {
    "INPUT_FROZEN": "Вход зафиксирован",
    "BOM_VALIDATED": "Состав проверен",
    "ROUTE_OPTIONS_READY": "Варианты маршрута готовы",
    "PRELIMINARY_READY": "Предварительная оценка готова",
    "ROUTE_SELECTED": "Маршрут выбран",
    "ROUTE_FROZEN": "Маршрут заморожен",
    "DETAILED_COSTING": "Подробный расчёт",
    "COSTING_COMPLETE": "Расчёт стадий завершён",
    "BOOK_ASSEMBLED": "Книга собрана",
    "QA_MECHANICAL_PASS": "Механическая проверка пройдена",
    "QA_TECHNOLOGICAL_PASS": "Технологическая проверка пройдена",
    "READY_FOR_LD": "Готово для решения человека",
    "BLOCK_FOR_TECH_REVIEW": "Остановлено: нужен технологический разбор",
}

#: Крупные этапы рабочего экрана. Их состояние выводится из фактически
#: сохранённых разделов заказа; один лишь высокий status не закрашивает
#: отсутствующую книгу или маршрут зелёным.
WORKFLOW_STAGE_ORDER = ("input", "bom", "route", "costing", "book", "qa")
WORKFLOW_STAGE_TITLES = {
    "input": "Вход",
    "bom": "Состав",
    "route": "Маршрут",
    "costing": "Расчёт",
    "book": "Книга",
    "qa": "QA",
}

QA_GATE_TITLES = {
    "mechanical": "Механическая проверка",
    "technological": "Технологическая проверка",
    "commercial": "Коммерческая проверка",
}
QA_GATE_ACTION_TITLES = {
    "technological": "технологическую проверку",
    "commercial": "коммерческую проверку",
}
QA_GATE_ORDER = ("mechanical", "technological", "commercial")
HUMAN_QA_GATES = {"technological", "commercial"}
QA_VERDICTS = {"PASS", "ADJUST", "BLOCK", "NO_EVIDENCE"}
QA_ADJUST_OWNERS = {"supply", "norm", "front"}
QA_VERDICT_BODY_FIELDS = {
    "expected_revision",
    "gate",
    "verdict",
    "reasons",
    "adjust_owner",
}
QA_ADMIN_ERROR_STATUSES = {
    "Conflict": 409,
    "RevisionConflict": 409,
    "StorageUnavailable": 503,
}
CONTRACTOR_QUOTE_BASES = {"per_piece", "order_total"}
CONTRACTOR_QUOTE_BODY_FIELDS = {
    "expected_revision",
    "route_seq",
    "amount_rub",
    "basis",
    "vat_included",
    "quoted_at",
    "valid_until",
    "source",
    "reference",
}
MANUAL_REVIEW_BODY_FIELDS = {
    "expected_revision",
    "expected_book_digest",
    "reviewed_by",
    "reviews",
}
MANUAL_REVIEW_ITEM_FIELDS = {
    "item_index",
    "item_digest",
    "evidence",
    "reference",
}
ROUTE_RETURN_BODY_FIELDS = {"expected_revision", "reason"}


def _orders_root() -> Path:
    """Каталог реестра: сначала конфиг контура, потом ``HERMES_HOME/orders``."""
    try:
        config = load_config() or {}
        env = (
            ((config.get("mcp_servers") or {}).get("metal_calc") or {}).get("env")
            or {}
        )
        configured = str(env.get("METAL_CALC_ORDERS_ROOT") or "").strip()
        if configured:
            return Path(configured)
    except Exception:  # noqa: BLE001 — конфиг битый: молча уходим на дефолт
        _log.debug("calc orders: config unreadable, falling back to HERMES_HOME")
    return Path(get_hermes_home()) / "orders"


def _registry_path() -> Path:
    return _orders_root() / "registry.db"


def _connect() -> sqlite3.Connection:
    """Открыть реестр строго на чтение.

    ``mode=ro`` — не только про наши намерения: он же не даёт панели создать
    пустой файл там, где реестра ещё нет, и превратить «продукт не настроен»
    в «заказов нет».
    """
    path = _registry_path()
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Реестр заказов не найден — расчётчик на этом контуре ещё не настроен",
        )
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        _log.warning("calc orders: cannot open registry %s: %s", path, exc)
        raise HTTPException(status_code=503, detail="Реестр заказов недоступен") from exc


def _load_state(raw: Any) -> dict[str, Any]:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _json_digest(value: Any) -> Optional[str]:
    try:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(canonical).hexdigest()


def _stage_provisional(stage: dict[str, Any]) -> bool:
    """Есть ли в стадии хоть одна цифра «со звёздочкой».

    Флаг живёт в двух местах — на самой стадии и построчно, — потому что
    строку снабжение подтверждает поштучно. Для списка достаточно «есть или
    нет», подробности показывает карточка заказа.
    """
    result = stage.get("result")
    if not isinstance(result, dict):
        return False
    if result.get("provisional") is True:
        return True
    lines = result.get("lines")
    if isinstance(lines, list):
        return any(
            isinstance(line, dict) and line.get("provisional") is True for line in lines
        )
    return False


def _stage_summary(state: dict[str, Any]) -> dict[str, Any]:
    stages = state.get("stages")
    stages = stages if isinstance(stages, dict) else {}
    summary: dict[str, Any] = {}
    for name in STAGE_ORDER:
        stage = stages.get(name)
        if not isinstance(stage, dict):
            summary[name] = {"status": "pending", "provisional": False}
            continue
        summary[name] = {
            "status": str(stage.get("status") or "pending"),
            "provisional": _stage_provisional(stage),
            "proposed_at": stage.get("proposed_at"),
            "approved_at": stage.get("approved_at"),
            "approved_by": stage.get("approved_by"),
        }
    return summary


def _current_stage(summary: dict[str, Any]) -> Optional[str]:
    """Стадия, которая сейчас ждёт человека или агента.

    Первая неутверждённая по порядку конвейера: именно она отвечает на
    вопрос «где заказ стоит», а последняя тронутая — нет (после отката
    ревизии последней тронутой окажется уже пройденная).
    """
    for name in STAGE_ORDER:
        if summary.get(name, {}).get("status") != "approved":
            return name
    return None


def _quote_price(state: dict[str, Any]) -> Optional[dict[str, Any]]:
    stages = state.get("stages")
    quote = (stages or {}).get("quote") if isinstance(stages, dict) else None
    result = quote.get("result") if isinstance(quote, dict) else None
    price = result.get("price") if isinstance(result, dict) else None
    return price if isinstance(price, dict) else None


def _workflow_status_title(status: Any) -> str:
    code = str(status or "")
    return WORKFLOW_STATUS_TITLES.get(code, code or "Статус не записан")


def _safe_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _selected_route_steps(state: dict[str, Any]) -> list[dict[str, Any]]:
    workflow = _safe_mapping(state.get("workflow"))
    variants = _safe_mapping(state.get("route_variants"))
    variant = variants.get(str(workflow.get("frozen_variant_id") or ""))
    if not isinstance(variant, dict):
        return []
    steps = variant.get("steps")
    if not isinstance(steps, list):
        return []
    allowed = {
        "seq",
        "process_code",
        "actual_process_name",
        "execution_mode",
        "cost_owner",
        "note",
    }
    return [
        {key: value for key, value in step.items() if key in allowed}
        for step in steps
        if isinstance(step, dict)
    ]


def _contractor_quote_lines(
    state: dict[str, Any],
    active_pack: Optional[dict[str, Any]],
    *,
    today: Optional[date] = None,
) -> list[dict[str, Any]]:
    """One operator-facing line for every outsource step of the frozen route."""
    workflow = _safe_mapping(state.get("workflow"))
    variants = _safe_mapping(state.get("route_variants"))
    frozen = variants.get(str(workflow.get("frozen_variant_id") or ""))
    if not isinstance(frozen, dict) or frozen.get("status") != "frozen":
        return []
    raw_quotes = _safe_mapping(_safe_mapping(state.get("costing")).get("contractor_quotes"))
    public_fields = {
        "contract_version",
        "route_seq",
        "process_code",
        "amount_rub",
        "currency",
        "basis",
        "vat_included",
        "quoted_at",
        "valid_until",
        "source",
        "reference",
        "panel_actor",
        "order_quantity",
        "order_amount_rub",
        "normalized_amount_rub",
        "normalized_basis",
        "normalized_vat_included",
        "vat_rate_pct",
        "route_digest",
        "calculation_revision",
        "pack_revision",
        "pack_fingerprint",
        "recorded_at",
        "digest",
    }
    active_fingerprint = (
        str(active_pack.get("fingerprint"))
        if isinstance(active_pack, dict) and active_pack.get("fingerprint")
        else None
    )
    utc_today = today if today is not None else datetime.now(UTC).date()
    lines: list[dict[str, Any]] = []
    for step in frozen.get("steps") or []:
        if not isinstance(step, dict) or step.get("execution_mode") != "outsource":
            continue
        seq = step.get("seq")
        quote = raw_quotes.get(str(seq))
        reasons: list[str] = []
        public_quote: Optional[dict[str, Any]] = None
        if isinstance(quote, dict):
            public_quote = {key: quote[key] for key in public_fields if key in quote}
            recorded_digest = quote.get("digest")
            if (
                not isinstance(recorded_digest, str)
                or len(recorded_digest) != 64
                or _json_digest({key: value for key, value in quote.items() if key != "digest"})
                != recorded_digest
            ):
                reasons.append("record_digest")
            expected = {
                "contract_version": 1,
                "route_seq": seq,
                "process_code": step.get("process_code"),
                "route_digest": frozen.get("digest"),
                "calculation_revision": workflow.get("calculation_revision"),
                "order_quantity": workflow.get("quantity"),
                "normalized_basis": "order_total",
                "currency": "RUB",
            }
            reasons.extend(
                field for field, value in expected.items() if quote.get(field) != value
            )
            if active_fingerprint is None:
                reasons.append("pack_unavailable")
            elif quote.get("pack_fingerprint") != active_fingerprint:
                reasons.append("pack_fingerprint")
            try:
                quote_date = date.fromisoformat(str(quote.get("quoted_at")))
                valid_until = date.fromisoformat(str(quote.get("valid_until")))
            except ValueError:
                reasons.append("valid_until")
            else:
                if quote_date > utc_today:
                    reasons.append("quoted_at")
                if valid_until < quote_date or valid_until < utc_today:
                    reasons.append("valid_until")
        line = {
            "seq": seq,
            "process_code": step.get("process_code"),
            "actual_process_name": step.get("actual_process_name"),
            "execution_mode": "outsource",
            "note": step.get("note"),
            "status": "missing" if not isinstance(quote, dict) else ("stale" if reasons else "current"),
            "stale_reasons": reasons,
        }
        if public_quote is not None:
            line["quote"] = public_quote
        lines.append(line)
    return sorted(lines, key=lambda line: int(line.get("seq") or 0))


def _workflow_stages(state: dict[str, Any]) -> list[dict[str, str]]:
    """Шесть видимых этапов, подтверждённых сохранёнными артефактами."""
    workflow = _safe_mapping(state.get("workflow"))
    variants = _safe_mapping(state.get("route_variants"))
    frozen_id = str(workflow.get("frozen_variant_id") or "")
    frozen = variants.get(frozen_id)
    costing = _safe_mapping(state.get("costing"))
    book = state.get("book")
    qa_receipts = _workflow_qa_receipts(state)
    complete = {
        "input": bool(workflow),
        "bom": bool(_safe_mapping(state.get("bom"))),
        "route": isinstance(frozen, dict) and frozen.get("status") == "frozen",
        "costing": isinstance(costing.get("blank"), dict)
        and isinstance(costing.get("time"), dict),
        "book": isinstance(book, dict),
        "qa": isinstance(book, dict)
        and all(receipt.get("status") == "complete" for receipt in qa_receipts.values()),
    }
    current = next((name for name in WORKFLOW_STAGE_ORDER if not complete[name]), None)
    if current is None and workflow.get("status") == "BLOCK_FOR_TECH_REVIEW":
        current = "qa"

    stages: list[dict[str, str]] = []
    for name in WORKFLOW_STAGE_ORDER:
        if complete[name]:
            status = "complete"
        elif name == current:
            status = (
                "blocked"
                if workflow.get("status") == "BLOCK_FOR_TECH_REVIEW"
                else "current"
            )
        else:
            status = "pending"
        stages.append({"name": name, "title": WORKFLOW_STAGE_TITLES[name], "status": status})
    return stages


def _workflow_price(state: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Только клиентская цена; внутренние суммы сюда попасть не могут."""
    price = _safe_mapping(_safe_mapping(state.get("book")).get("price"))
    if not price:
        return None
    allowed = {
        "amount_kind",
        "net_total_rub",
        "vat_amount_rub",
        "total_rub",
        "currency",
        "vat_included",
        "vat_rate_pct",
        "valid_until",
        "is_final",
        "status_note",
    }
    return {key: value for key, value in price.items() if key in allowed}


def _workflow_internal_cost(state: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Внутренняя себестоимость идёт отдельным операторским блоком."""
    cost = _safe_mapping(_safe_mapping(state.get("book")).get("cost"))
    if not cost:
        return None
    allowed = {
        "amount_kind",
        "blank_rub",
        "machining_rub",
        "contractor_rub",
        "extras_rub",
        "total_rub",
        "rates_include_vat",
        "net_equivalent_rub",
    }
    return {key: value for key, value in cost.items() if key in allowed}


def _workflow_items(value: Any, allowed: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {key: item[key] for key in allowed if key in item}
        for item in value
        if isinstance(item, dict)
    ]


def _workflow_qa_receipts(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    workflow = _safe_mapping(state.get("workflow"))
    revision = str(workflow.get("calculation_revision") or "")
    current = _safe_mapping(_safe_mapping(state.get("qa")).get(revision))
    receipts: dict[str, dict[str, Any]] = {}
    allowed = {
        "verdict",
        "computed_by",
        "by",
        "actor_role",
        "at",
        "reasons",
        "adjust_owner",
        "blocked",
    }
    for gate in QA_GATE_ORDER:
        receipt = current.get(gate)
        if not isinstance(receipt, dict):
            receipts[gate] = {
                "title": QA_GATE_TITLES[gate],
                "verdict": None,
                "status": "pending",
            }
            continue
        public = {key: value for key, value in receipt.items() if key in allowed}
        checks = receipt.get("checks")
        if isinstance(checks, list):
            public["checks_total"] = len(checks)
            public["checks_passed"] = sum(
                isinstance(item, dict) and item.get("ok") is True for item in checks
            )
        public["title"] = QA_GATE_TITLES[gate]
        complete = public.get("verdict") == "PASS"
        if gate == "mechanical":
            complete = bool(
                complete
                and public.get("computed_by") == "engine"
                and public.get("checks_total", 0) > 0
                and public.get("checks_passed") == public.get("checks_total")
            )
        elif gate in HUMAN_QA_GATES:
            complete = bool(complete and public.get("actor_role") == "qa")
        public["status"] = "complete" if complete else "attention"
        receipts[gate] = public
    return receipts


def _workflow_current_qa_gate(state: dict[str, Any]) -> Optional[str]:
    receipts = _workflow_qa_receipts(state)
    return next(
        (gate for gate in QA_GATE_ORDER if receipts[gate].get("status") != "complete"),
        None,
    )


def _workflow_book_binding(state: dict[str, Any]) -> dict[str, Any]:
    """Verify that the current book is exactly bound to current workflow inputs."""

    def invalid(detail: str) -> dict[str, Any]:
        return {"valid": False, "detail": detail}

    def is_sha256(value: Any) -> bool:
        return bool(
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    workflow = _safe_mapping(state.get("workflow"))
    book = state.get("book")
    if not isinstance(book, dict):
        return invalid("Текущая книга расчёта отсутствует или повреждена.")
    book_pack_fingerprint = book.get("pack_fingerprint")
    if not isinstance(book_pack_fingerprint, str) or not book_pack_fingerprint:
        return invalid("В книге отсутствует точная версия данных предприятия.")

    revision = workflow.get("calculation_revision")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
        or book.get("calculation_revision") != revision
    ):
        return invalid("Книга собрана не для текущей ревизии расчёта.")

    frozen_id = workflow.get("frozen_variant_id")
    variants = _safe_mapping(state.get("route_variants"))
    frozen = variants.get(str(frozen_id or ""))
    if (
        not isinstance(frozen, dict)
        or frozen.get("status") != "frozen"
        or not is_sha256(frozen.get("digest"))
        or book.get("route_digest") != frozen.get("digest")
    ):
        return invalid("Книга не относится к текущему замороженному маршруту.")

    costing = _safe_mapping(state.get("costing"))
    for part, book_key, title in (
        ("blank", "blank_digest", "заготовки"),
        ("time", "time_digest", "норм времени"),
    ):
        current = costing.get(part)
        if (
            not isinstance(current, dict)
            or not is_sha256(current.get("digest"))
            or book.get(book_key) != current.get("digest")
            or current.get("pack_fingerprint") != book_pack_fingerprint
        ):
            return invalid(f"Книга не относится к текущему расчёту {title}.")

    contractor_quotes = costing.get("contractor_quotes", {})
    if not isinstance(contractor_quotes, dict):
        return invalid("Текущие КП подрядчиков повреждены.")
    for quote in contractor_quotes.values():
        if not isinstance(quote, dict):
            return invalid("Текущее КП подрядчика повреждено.")
        recorded_quote_digest = quote.get("digest")
        if (
            not is_sha256(recorded_quote_digest)
            or _json_digest(
                {key: value for key, value in quote.items() if key != "digest"}
            )
            != recorded_quote_digest
        ):
            return invalid("Контрольная сумма КП подрядчика не совпадает.")
    if book.get("contractor_quotes_digest") != _json_digest(contractor_quotes):
        return invalid("Книга не относится к текущим КП подрядчиков.")

    recorded_digest = book.get("digest")
    if not is_sha256(recorded_digest):
        return invalid("Контрольная сумма книги отсутствует или повреждена.")
    try:
        canonical = json.dumps(
            {key: value for key, value in book.items() if key != "digest"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return invalid("Книга содержит данные, для которых нельзя проверить контрольную сумму.")
    if hashlib.sha256(canonical).hexdigest() != recorded_digest:
        return invalid("Контрольная сумма книги не совпадает с её содержимым.")
    return {"valid": True, "detail": None}


def _workflow_manual_review(
    state: dict[str, Any], active_pack: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """Expose exact items and validate the latest immutable QA receipt."""
    workflow = _safe_mapping(state.get("workflow"))
    book = _safe_mapping(state.get("book"))
    raw_items = book.get("manual_review_required")
    items = raw_items if isinstance(raw_items, list) else []
    public_items = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        public_items.append(
            {
                **{key: item[key] for key in {"seq", "process_code", "reason"} if key in item},
                "item_index": index,
                "item_digest": _json_digest(item),
            }
        )
    if not items:
        return {"required": False, "valid": True, "items": public_items, "receipt": None}

    current_qa = _safe_mapping(
        _safe_mapping(state.get("qa")).get(str(workflow.get("calculation_revision") or ""))
    )
    history = current_qa.get("manual_review_receipts")
    receipt = history[-1] if isinstance(history, list) and history else None
    valid = isinstance(receipt, dict)
    if valid:
        recorded_digest = receipt.get("digest")
        valid = bool(
            isinstance(recorded_digest, str)
            and len(recorded_digest) == 64
            and _json_digest({key: value for key, value in receipt.items() if key != "digest"})
            == recorded_digest
            and receipt.get("contract_version") == 1
            and receipt.get("book_digest") == book.get("digest")
            and receipt.get("manual_items_digest") == _json_digest(items)
            and receipt.get("calculation_revision") == workflow.get("calculation_revision")
            and isinstance(active_pack, dict)
            and receipt.get("pack_revision") == active_pack.get("active")
            and receipt.get("pack_fingerprint") == active_pack.get("fingerprint")
            and receipt.get("actor_role") == "qa"
            and isinstance(receipt.get("reviewed_by"), str)
            and bool(receipt["reviewed_by"].strip())
        )
    reviews = receipt.get("reviews") if isinstance(receipt, dict) else None
    if valid:
        valid = isinstance(reviews, list) and len(reviews) == len(items)
    if valid:
        for index, (item, review) in enumerate(zip(items, reviews, strict=True)):
            if not isinstance(item, dict) or not isinstance(review, dict):
                valid = False
                break
            if (
                set(review)
                != {"item_index", "item_digest", "item", "evidence", "reference"}
                or review.get("item_index") != index
                or review.get("item_digest") != _json_digest(item)
                or review.get("item") != item
                or not isinstance(review.get("evidence"), str)
                or not review["evidence"].strip()
                or not isinstance(review.get("reference"), str)
                or not review["reference"].strip()
            ):
                valid = False
                break
    public_receipt = None
    if isinstance(receipt, dict):
        public_receipt = {
            key: receipt[key]
            for key in (
                "digest",
                "book_digest",
                "manual_items_digest",
                "calculation_revision",
                "pack_revision",
                "reviewed_by",
                "recorded_at",
                "reviews",
            )
            if key in receipt
        }
        public_receipt["valid"] = bool(valid)
    return {
        "required": True,
        "valid": bool(valid),
        "items": public_items,
        "receipt": public_receipt,
    }


def _workflow_price_validity(
    price: Optional[dict[str, Any]], *, today: Optional[date] = None
) -> Optional[str]:
    """Return a fail-closed validity issue for the customer price."""
    if not isinstance(price, dict):
        return "missing"
    value = price.get("valid_until")
    if not isinstance(value, str):
        return "invalid"
    try:
        valid_until = date.fromisoformat(value)
    except ValueError:
        return "invalid"
    if valid_until.isoformat() != value:
        return "invalid"
    utc_today = today if today is not None else datetime.now(UTC).date()
    return "expired" if valid_until < utc_today else None


def _workflow_customer_price_is_final(
    state: dict[str, Any],
    stale_pack: Optional[bool],
    active_pack: Optional[dict[str, Any]],
) -> bool:
    """Fail closed: a book flag alone never makes a customer price final."""
    workflow = _safe_mapping(state.get("workflow"))
    price = _workflow_price(state)
    receipts = _workflow_qa_receipts(state)
    return bool(
        price
        and price.get("is_final") is True
        and workflow.get("status") == "READY_FOR_LD"
        and stale_pack is False
        and _workflow_book_binding(state).get("valid") is True
        and _workflow_price_validity(price) is None
        and all(
            line.get("status") == "current"
            for line in _contractor_quote_lines(state, active_pack)
        )
        and _workflow_manual_review(state, active_pack).get("valid") is True
        and all(receipt.get("status") == "complete" for receipt in receipts.values())
    )


def _workflow_stale_pack(
    state: dict[str, Any], active: Optional[dict[str, Any]]
) -> Optional[bool]:
    """То же сравнение, что ``WorkflowService.workflow_status``.

    ``None`` означает «сверка недоступна», а не «данные свежие».
    """
    if not isinstance(active, dict) or not active.get("fingerprint"):
        return None
    costing = _safe_mapping(state.get("costing"))
    book = _safe_mapping(state.get("book"))
    required = [
        costing.get("pack_fingerprint"),
        _safe_mapping(costing.get("blank")).get("pack_fingerprint"),
        _safe_mapping(costing.get("time")).get("pack_fingerprint"),
        book.get("pack_fingerprint"),
    ]
    quotes = _safe_mapping(costing.get("contractor_quotes"))
    quote_fingerprints = [
        quote.get("pack_fingerprint") if isinstance(quote, dict) else None
        for quote in quotes.values()
    ]
    all_recorded = required + quote_fingerprints
    if not any(all_recorded):
        return None
    # Partial provenance is stale, not proof that the current active pack was
    # used. Ignoring a missing book fingerprint could make the UI show FINAL
    # while the canonical report correctly refuses it.
    if any(not isinstance(value, str) or not value for value in all_recorded):
        return True
    recorded = {str(value) for value in all_recorded}
    return recorded != {str(active.get("fingerprint"))}


def _workflow_blockers(
    state: dict[str, Any],
    stale_pack: Optional[bool],
    active_pack: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    workflow = _safe_mapping(state.get("workflow"))
    book = _safe_mapping(state.get("book"))
    price = _workflow_price(state)
    blockers: list[dict[str, Any]] = []

    if stale_pack is True:
        blockers.append(
            {
                "kind": "stale_pack",
                "title": "Расчёт сделан по прежним данным предприятия",
                "detail": "Откройте новую ревизию расчёта и пересоберите книгу.",
            }
        )
    elif isinstance(state.get("book"), dict) and stale_pack is None:
        blockers.append(
            {
                "kind": "freshness_unknown",
                "title": "Актуальность данных расчёта не подтверждена",
                "detail": "Восстановите сверку с действующим пакетом перед решением.",
            }
        )
    if "book" in state:
        book_binding = _workflow_book_binding(state)
        if book_binding.get("valid") is not True:
            blockers.append(
                {
                    "kind": "book_binding",
                    "title": "Книга не связана с текущим расчётом",
                    "detail": book_binding.get("detail")
                    or "Пересоберите книгу из текущих стадий расчёта.",
                }
            )
    price_validity = _workflow_price_validity(price)
    if price is not None and price_validity == "expired":
        blockers.append(
            {
                "kind": "price_expired",
                "title": "Срок действия цены истёк",
                "detail": "Пересоберите книгу с актуальным сроком действия цены.",
            }
        )
    elif price is not None and price_validity is not None:
        blockers.append(
            {
                "kind": "price_validity",
                "title": "Срок действия цены не подтверждён",
                "detail": "В книге нужна корректная дата valid_until в формате ГГГГ-ММ-ДД.",
            }
        )
    if workflow.get("status") == "BLOCK_FOR_TECH_REVIEW":
        blockers.append(
            {
                "kind": "tech_review",
                "title": "Автоматический цикл остановлен",
                "detail": "Нужен технологический разбор маршрута.",
            }
        )
    if book.get("provisional") is True:
        blockers.append(
            {
                "kind": "material_price",
                "title": "Цена материала требует подтверждения",
                "detail": "Снабжение должно подтвердить актуальную котировку.",
            }
        )

    route_unpriced = _workflow_items(
        book.get("route_unpriced"),
        {
            "seq",
            "process_code",
            "actual_process_name",
            "execution_mode",
            "note",
            "reason",
        },
    )
    for item in route_unpriced:
        code = item.get("actual_process_name") or item.get("process_code") or "операция"
        blockers.append(
            {
                "kind": "route_unpriced",
                "title": f"Нет цены подрядчика: {code}",
                "detail": item.get("reason")
                or item.get("note")
                or "Получите и учтите КП подрядчика.",
                "route_seq": item.get("seq"),
            }
        )
    unpriced_sequences = {item.get("seq") for item in route_unpriced}
    for line in _contractor_quote_lines(state, active_pack):
        if line.get("status") == "current" or line.get("seq") in unpriced_sequences:
            continue
        code = line.get("actual_process_name") or line.get("process_code") or "операция"
        reasons = line.get("stale_reasons") or []
        expired = "valid_until" in reasons
        blockers.append(
            {
                "kind": "route_unpriced",
                "title": (
                    f"Истёк срок КП подрядчика: {code}"
                    if expired
                    else f"Нет актуального КП подрядчика: {code}"
                ),
                "detail": (
                    "Запишите новое действующее КП и пересоберите книгу."
                    if expired
                    else "Запишите КП для текущей ревизии и замороженного маршрута."
                ),
                "route_seq": line.get("seq"),
            }
        )

    manual_review_status = _workflow_manual_review(state, active_pack)
    manual_review = manual_review_status["items"]
    for item in manual_review if manual_review_status.get("valid") is not True else []:
        blockers.append(
            {
                "kind": "manual_review",
                "title": f"Нужна ручная сверка: {item.get('process_code') or 'операция'}",
                "detail": item.get("reason") or "Проверьте ставку и правило расчёта.",
                "route_seq": item.get("seq"),
            }
        )

    # До сборки книги отсутствие квитанций — нормальная очередь этапов, а не
    # повод посылать человека в QA раньше маршрута и расчёта.
    if isinstance(state.get("book"), dict):
        for gate, receipt in _workflow_qa_receipts(state).items():
            verdict = receipt.get("verdict")
            if verdict is None:
                blockers.append(
                    {
                        "kind": "qa_pending",
                        "title": f"Ожидается: {str(receipt['title']).lower()}",
                        "detail": "Гейт ещё не пройден и квитанция PASS не записана.",
                        "gate": gate,
                    }
                )
            elif receipt.get("status") != "complete":
                reasons = receipt.get("reasons")
                detail = (
                    "; ".join(str(item) for item in reasons)
                    if isinstance(reasons, list)
                    else ""
                )
                if gate == "mechanical" and verdict == "PASS":
                    if receipt.get("computed_by") != "engine":
                        detail = "PASS должен быть вычислен движком, а не записан оператором."
                    elif not receipt.get("checks_total"):
                        detail = "Механическая квитанция не содержит проверок."
                    elif receipt.get("checks_passed") != receipt.get("checks_total"):
                        detail = "Не все механические проверки завершились успешно."
                elif gate in HUMAN_QA_GATES and verdict == "PASS":
                    if receipt.get("actor_role") != "qa":
                        detail = "PASS должен быть подписан ролью QA."
                blockers.append(
                    {
                        "kind": "qa",
                        "title": f"{receipt['title']}: квитанция недействительна"
                        if verdict == "PASS"
                        else f"{receipt['title']}: {verdict}",
                        "detail": detail or "Закройте замечания проверки.",
                        "gate": gate,
                    }
                )

    explained_price = bool(
        blockers
        or book.get("provisional") is True
        or route_unpriced
        or (manual_review and manual_review_status.get("valid") is not True)
    )
    if (
        price
        and not _workflow_customer_price_is_final(state, stale_pack, active_pack)
        and not explained_price
    ):
        blockers.append(
            {
                "kind": "price_not_final",
                "title": "Цена пока не окончательная",
                "detail": price.get("status_note")
                or "Проверьте основания предварительного расчёта перед решением.",
            }
        )
    if workflow.get("status") == "READY_FOR_LD" and price is None:
        blockers.append(
            {
                "kind": "price_missing",
                "title": "В готовом заказе нет клиентской цены",
                "detail": "Проверьте целостность книги расчёта.",
            }
        )
    return blockers


def _workflow_next_action(
    state: dict[str, Any], blockers: list[dict[str, Any]]
) -> dict[str, Any]:
    workflow = _safe_mapping(state.get("workflow"))
    status = str(workflow.get("status") or "")
    kinds = {str(item.get("kind")) for item in blockers}

    if "freshness_unknown" in kinds:
        return {
            "kind": "recalculate",
            "label": "Подтвердить актуальность расчёта по действующим данным",
            "profile": None,
        }
    if "stale_pack" in kinds:
        return {
            "kind": "recalculate",
            "label": "Открыть пересчёт по действующим данным предприятия",
            "profile": None,
        }
    if "book_binding" in kinds:
        return {
            "kind": "recalculate",
            "label": "Пересобрать книгу для текущей ревизии расчёта",
            "profile": None,
        }
    if "price_expired" in kinds or "price_validity" in kinds:
        return {
            "kind": "recalculate",
            "label": "Пересобрать книгу с действующим сроком цены",
            "profile": None,
        }
    if status == "BLOCK_FOR_TECH_REVIEW":
        return {
            "kind": "tech_review",
            "label": "Провести технологический разбор маршрута",
            "profile": "raschet-route",
        }
    if "material_price" in kinds:
        return {
            "kind": "supply",
            "label": "Подтвердить актуальную цену материала",
            "profile": "raschet-blank",
        }
    if "route_unpriced" in kinds:
        return {
            "kind": "contractor_quote",
            "label": "Получить и учесть КП подрядчика",
            "profile": None,
        }
    if "manual_review" in kinds:
        return {
            "kind": "manual_review",
            "label": "Провести ручную коммерческую сверку",
            "profile": None,
        }
    if "qa_pending" in kinds:
        pending = next(item for item in blockers if item.get("kind") == "qa_pending")
        gate = str(pending.get("gate") or "")
        if gate == "mechanical":
            return {
                "kind": "qa_engine",
                "label": "Ожидается автоматическая механическая проверка",
                "profile": None,
                "qa_gate": gate,
            }
        return {
            "kind": "qa_human",
            "label": f"Провести {QA_GATE_ACTION_TITLES.get(gate, gate)} человеком",
            "profile": None,
            "qa_gate": gate,
        }
    if "qa" in kinds or "price_not_final" in kinds or "price_missing" in kinds:
        return {
            "kind": "resolve_blocker",
            "label": "Закрыть замечания перед следующим QA-гейтом",
            "profile": None,
        }
    if status == "READY_FOR_LD":
        return {
            "kind": "human_decision",
            "label": "Проверить итог и принять решение по расчёту",
            "profile": None,
        }

    current = next(
        (
            stage["name"]
            for stage in _workflow_stages(state)
            if stage["status"] in {"current", "blocked"}
        ),
        None,
    )
    if current in {"bom", "route"}:
        return {
            "kind": "route",
            "label": "Проверить состав и зафиксировать маршрут",
            "profile": "raschet-route",
        }
    if current == "costing":
        costing = _safe_mapping(state.get("costing"))
        if not isinstance(costing.get("blank"), dict):
            return {
                "kind": "supply",
                "label": "Посчитать материал и заготовительные операции",
                "profile": "raschet-blank",
            }
        return {
            "kind": "norm",
            "label": "Посчитать нормы времени и остальные операции",
            "profile": "raschet-time",
        }
    if current == "book":
        return {"kind": "assemble_book", "label": "Собрать книгу расчёта", "profile": None}
    if current == "qa":
        return {"kind": "qa", "label": "Провести следующий QA-гейт", "profile": None}
    return {"kind": "inspect", "label": "Проверить состояние заказа", "profile": None}


def _workflow_source_files(state: dict[str, Any]) -> list[dict[str, Any]]:
    return _workflow_items(
        state.get("source_files"),
        {"source_file_id", "name", "sha256", "format", "bytes", "received_at"},
    )


def _workflow_events(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Публичная лента без служебных полей диагностических событий."""
    return _workflow_items(state.get("workflow_events"), {"event", "at", "by"})[-50:]


def _workflow_card(
    row: tuple, state: dict[str, Any], active_pack: Optional[dict[str, Any]]
) -> dict[str, Any]:
    order_id, revision, registry_status, created_at, updated_at, _raw = row
    workflow = _safe_mapping(state.get("workflow"))
    status = str(workflow.get("status") or registry_status)
    book = _safe_mapping(state.get("book"))
    customer = _safe_mapping(state.get("customer"))
    stale_pack = _workflow_stale_pack(state, active_pack)
    contractor_quotes = _contractor_quote_lines(state, active_pack)
    manual_review = _workflow_manual_review(state, active_pack)
    blockers = _workflow_blockers(state, stale_pack, active_pack)
    price = _workflow_price(state)
    if price is not None:
        price["is_final"] = _workflow_customer_price_is_final(
            state, stale_pack, active_pack
        )
    route_unpriced = _workflow_items(
        book.get("route_unpriced"),
        {
            "seq",
            "process_code",
            "actual_process_name",
            "execution_mode",
            "note",
            "reason",
        },
    )
    return {
        "kind": "workflow",
        "order_id": order_id,
        "revision": revision,
        "status": status,
        "status_title": _workflow_status_title(status),
        "registry_status": registry_status,
        "created_at": created_at,
        "updated_at": updated_at,
        "customer": customer.get("name"),
        "stages": {},
        "current_stage": None,
        "workflow": {
            key: workflow.get(key)
            for key in (
                "status",
                "schema_version",
                "calculation_revision",
                "route_return_count",
                "quantity",
                "kd_revision",
                "frozen_variant_id",
            )
            if key in workflow
        },
        "workflow_stages": _workflow_stages(state),
        "provisional": bool(
            book.get("provisional") is True
            or (price is not None and price.get("is_final") is not True)
        ),
        "stale_stages": [],
        "stale_pack": stale_pack,
        "book_binding_valid": (
            _workflow_book_binding(state).get("valid") is True if "book" in state else None
        ),
        "book_digest": book.get("digest") if isinstance(book.get("digest"), str) else None,
        "price": price,
        "internal_cost": _workflow_internal_cost(state),
        "contractor_quotes": contractor_quotes,
        "route_unpriced": route_unpriced,
        "manual_review_required": manual_review["items"],
        "manual_review_receipt": manual_review["receipt"],
        "qa_receipts": _workflow_qa_receipts(state),
        "blockers": blockers,
        "next_action": _workflow_next_action(state, blockers),
        "warnings": state.get("warnings") if isinstance(state.get("warnings"), list) else [],
    }


async def _active_pack() -> Optional[dict[str, Any]]:
    """Действующая ревизия данных предприятия с отпечатком.

    Отпечаток считает `metal-calc-admin pack-active` — тот же код, которым
    инструменты сверяют стадии; второй расчёт отпечатка в панели разошёлся
    бы с движком молча. Недоступный CLI или незаведённые данные — не авария
    списка заказов: без отпечатка сверка просто не делается.
    """
    try:
        result = await _run_admin(["pack-active"])
    except HTTPException:
        return None
    if not isinstance(result, dict) or not result.get("active"):
        return None
    return result


def _stale_stages(state: dict[str, Any], active: Optional[dict[str, Any]]) -> list[str]:
    """Стадии, посчитанные по другим данным, чем действующие.

    То же правило, что `_stage_is_stale` движка: отпечаток ДАННЫХ, а не хеш
    файла (повторное сохранение без правок не старит заказы); стадии до
    появления отпечатка сверяются по старому полю.
    """
    if not isinstance(active, dict):
        return []
    stages = state.get("stages")
    stages = stages if isinstance(stages, dict) else {}
    stale: list[str] = []
    for name in STAGE_ORDER:
        stage = stages.get(name)
        result = stage.get("result") if isinstance(stage, dict) else None
        if not isinstance(result, dict):
            continue
        recorded, current = result.get("pack_fingerprint"), active.get("fingerprint")
        if not recorded:
            recorded, current = result.get("pack_sha256"), active.get("sha256")
        if recorded and recorded != current:
            stale.append(name)
    return stale


def _row_to_card(row: tuple, active_pack: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    order_id, revision, status, created_at, updated_at, raw = row
    state = _load_state(raw)
    intake = state.get("folder_intake")
    if isinstance(intake, dict) and status == "draft":
        return {
            "kind": "draft", "order_id": order_id, "revision": revision,
            "status": "draft", "status_title": "Черновик", "created_at": created_at,
            "updated_at": updated_at, "folder_name": intake.get("folder_name"),
            "file_count": len(intake.get("files", [])), "total_bytes": intake.get("total_bytes", 0),
            "customer": None, "stages": {}, "current_stage": None,
            "provisional": False, "stale_stages": [], "price": None, "warnings": [],
        }
    if isinstance(state.get("workflow"), dict):
        return _workflow_card(row, state, active_pack)
    summary = _stage_summary(state)
    customer = state.get("customer")
    return {
        "kind": "legacy",
        "order_id": order_id,
        "revision": revision,
        "status": status,
        "created_at": created_at,
        "updated_at": updated_at,
        "customer": customer.get("name") if isinstance(customer, dict) else None,
        "stages": summary,
        "current_stage": _current_stage(summary),
        "provisional": any(s.get("provisional") for s in summary.values()),
        "stale_stages": _stale_stages(state, active_pack),
        "price": _quote_price(state),
        "warnings": state.get("warnings") if isinstance(state.get("warnings"), list) else [],
    }


@router.get("/api/calc/orders")
async def calc_orders_list(limit: int = 100):
    """Список заказов, свежие сверху."""
    # Бессмысленный limit (0, отрицательный, не число) — это «не задан», а не
    # «показать ноль заказов»: пустой экран при живом реестре читается как
    # «заказов нет».
    try:
        requested = int(limit)
    except (TypeError, ValueError):
        requested = 0
    limit = min(requested if requested >= 1 else 100, 500)
    connection = _connect()
    try:
        rows = connection.execute(
            "SELECT order_id, revision, status, created_at, updated_at, state_json "
            "FROM orders ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.Error as exc:
        _log.warning("calc orders: list failed: %s", exc)
        raise HTTPException(status_code=503, detail="Реестр заказов недоступен") from exc
    finally:
        connection.close()
    active_pack = await _active_pack()
    return {
        "orders": [_row_to_card(row, active_pack) for row in rows],
        "stage_order": list(STAGE_ORDER),
        "stage_titles": STAGE_TITLES,
        "stage_agents": STAGE_AGENTS,
        "pack": {"active_revision": active_pack.get("active")} if active_pack else None,
    }


@router.post("/api/calc/orders/{order_id}/contractor-quote")
async def calc_order_contractor_quote(order_id: str, body: Any = Body(...)):
    """Record one typed outsource quote through the human-only calculator CLI."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Ожидался объект КП подрядчика")
    if not order_id or len(order_id) > 128:
        raise HTTPException(status_code=422, detail="Некорректный идентификатор заказа")
    unknown_fields = sorted(set(body) - CONTRACTOR_QUOTE_BODY_FIELDS)
    missing_fields = sorted(CONTRACTOR_QUOTE_BODY_FIELDS - set(body))
    if unknown_fields or missing_fields:
        parts = []
        if missing_fields:
            parts.append("не заданы: " + ", ".join(missing_fields))
        if unknown_fields:
            parts.append("неизвестны: " + ", ".join(unknown_fields))
        raise HTTPException(
            status_code=422,
            detail="Поля КП подрядчика: " + "; ".join(parts),
        )

    expected_revision = body.get("expected_revision")
    route_seq = body.get("route_seq")
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        raise HTTPException(status_code=422, detail="expected_revision должен быть целым числом")
    if isinstance(route_seq, bool) or not isinstance(route_seq, int) or route_seq < 1:
        raise HTTPException(status_code=422, detail="route_seq должен быть целым числом")
    amount_raw = body.get("amount_rub")
    if isinstance(amount_raw, bool) or amount_raw is None:
        raise HTTPException(status_code=422, detail="amount_rub должен быть больше нуля")
    try:
        amount = Decimal(str(amount_raw))
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=422, detail="amount_rub должен быть числом") from None
    if not amount.is_finite() or amount <= 0 or amount > Decimal("1000000000000"):
        raise HTTPException(status_code=422, detail="amount_rub должен быть больше нуля")
    basis = body.get("basis")
    if basis not in CONTRACTOR_QUOTE_BASES:
        raise HTTPException(status_code=422, detail="basis должен быть per_piece или order_total")
    if type(body.get("vat_included")) is not bool:
        raise HTTPException(status_code=422, detail="vat_included должен быть boolean")
    quoted_at = body.get("quoted_at")
    if not isinstance(quoted_at, str):
        raise HTTPException(status_code=422, detail="quoted_at должен быть датой ГГГГ-ММ-ДД")
    try:
        quote_date = date.fromisoformat(quoted_at)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="quoted_at должен быть датой ГГГГ-ММ-ДД"
        ) from None
    if quote_date.isoformat() != quoted_at:
        raise HTTPException(status_code=422, detail="quoted_at должен быть датой ГГГГ-ММ-ДД")
    if quote_date > datetime.now(UTC).date():
        raise HTTPException(status_code=422, detail="quoted_at не может быть датой из будущего")
    valid_until_raw = body.get("valid_until")
    if not isinstance(valid_until_raw, str):
        raise HTTPException(
            status_code=422,
            detail="valid_until должен быть датой ГГГГ-ММ-ДД не раньше quoted_at",
        )
    try:
        valid_until = date.fromisoformat(valid_until_raw)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="valid_until должен быть датой ГГГГ-ММ-ДД не раньше quoted_at",
        ) from None
    if valid_until.isoformat() != valid_until_raw or valid_until < quote_date:
        raise HTTPException(
            status_code=422,
            detail="valid_until должен быть датой ГГГГ-ММ-ДД не раньше quoted_at",
        )
    for field in ("source", "reference"):
        value = body.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            raise HTTPException(
                status_code=422,
                detail=f"{field} должен содержать от 1 до 512 символов",
            )

    connection = _connect()
    try:
        row = connection.execute(
            "SELECT order_id, revision, status, created_at, updated_at, state_json "
            "FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        _log.warning("calc orders: contractor quote read failed: %s", exc)
        raise HTTPException(status_code=503, detail="Реестр заказов недоступен") from exc
    finally:
        connection.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    if row[1] != expected_revision:
        raise HTTPException(
            status_code=409,
            detail=f"Заказ уже изменился: ожидалась ревизия {expected_revision}, сейчас {row[1]}",
        )
    state = _load_state(row[5])
    workflow = _safe_mapping(state.get("workflow"))
    variants = _safe_mapping(state.get("route_variants"))
    frozen = variants.get(str(workflow.get("frozen_variant_id") or ""))
    matches = [
        step
        for step in (frozen.get("steps") if isinstance(frozen, dict) else []) or []
        if isinstance(step, dict)
        and step.get("seq") == route_seq
        and step.get("execution_mode") == "outsource"
    ]
    if not isinstance(frozen, dict) or frozen.get("status") != "frozen" or len(matches) != 1:
        raise HTTPException(
            status_code=409,
            detail="Шаг не является outsource-операцией текущего замороженного маршрута",
        )

    stdin = json.dumps(
        {
            "amount_rub": amount_raw,
            "basis": basis,
            "vat_included": body["vat_included"],
            "quoted_at": quoted_at,
            "valid_until": valid_until_raw,
            "source": body["source"].strip(),
            "reference": body["reference"].strip(),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    return await _run_admin(
        [
            "contractor-quote-set",
            "--order-id",
            order_id,
            "--expected-revision",
            str(expected_revision),
            "--route-seq",
            str(route_seq),
            "--actor",
            "panel",
        ],
        stdin=stdin,
        error_statuses=QA_ADMIN_ERROR_STATUSES,
        default_error_status=422,
    )


@router.post("/api/calc/orders/{order_id}/qa-verdict")
async def calc_order_qa_verdict(order_id: str, body: Any = Body(...)):
    """Record the current human QA gate through the canonical calculator CLI.

    The dashboard auth middleware protects every non-public ``/api`` route.
    This endpoint additionally binds the decision to the exact registry
    revision and recomputes the ordered gate before invoking the writer.
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Ожидался объект решения QA")
    if not order_id or len(order_id) > 128:
        raise HTTPException(status_code=422, detail="Некорректный идентификатор заказа")
    unknown_fields = sorted(set(body) - QA_VERDICT_BODY_FIELDS)
    if unknown_fields:
        raise HTTPException(
            status_code=422,
            detail=f"Неизвестные поля решения QA: {', '.join(unknown_fields)}",
        )

    expected_revision = body.get("expected_revision")
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        raise HTTPException(status_code=422, detail="expected_revision должен быть целым числом")
    gate = body.get("gate")
    if gate not in HUMAN_QA_GATES:
        raise HTTPException(
            status_code=422,
            detail="Панель может записывать только технологический или коммерческий QA-гейт",
        )
    verdict = body.get("verdict")
    if verdict not in QA_VERDICTS:
        raise HTTPException(
            status_code=422,
            detail="verdict должен быть PASS, ADJUST, BLOCK или NO_EVIDENCE",
        )
    raw_reasons = body.get("reasons", [])
    if not isinstance(raw_reasons, list) or len(raw_reasons) > 10:
        raise HTTPException(status_code=422, detail="reasons должен быть списком до 10 причин")
    if any(
        not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500
        for reason in raw_reasons
    ):
        raise HTTPException(
            status_code=422,
            detail="Каждая причина должна содержать от 1 до 500 символов",
        )
    reasons = [reason.strip() for reason in raw_reasons]
    if verdict != "PASS" and not reasons:
        raise HTTPException(
            status_code=422,
            detail="Для решения, отличного от PASS, укажите причину",
        )
    adjust_owner = body.get("adjust_owner")
    if verdict == "ADJUST":
        if adjust_owner not in QA_ADJUST_OWNERS:
            raise HTTPException(
                status_code=422,
                detail="ADJUST требует adjust_owner: supply, norm или front",
            )
    elif "adjust_owner" in body:
        raise HTTPException(
            status_code=422,
            detail="adjust_owner разрешён только для решения ADJUST",
        )

    connection = _connect()
    try:
        row = connection.execute(
            "SELECT order_id, revision, status, created_at, updated_at, state_json "
            "FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        _log.warning("calc orders: QA read failed: %s", exc)
        raise HTTPException(status_code=503, detail="Реестр заказов недоступен") from exc
    finally:
        connection.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    if row[1] != expected_revision:
        raise HTTPException(
            status_code=409,
            detail=f"Заказ уже изменился: ожидалась ревизия {expected_revision}, сейчас {row[1]}",
        )

    state = _load_state(row[5])
    if not isinstance(state.get("workflow"), dict):
        raise HTTPException(status_code=422, detail="QA-гейты доступны только workflow-заказам")
    book_binding = _workflow_book_binding(state)
    if book_binding.get("valid") is not True:
        raise HTTPException(
            status_code=409,
            detail=book_binding.get("detail") or "Книга не связана с текущим расчётом",
        )
    current_gate = _workflow_current_qa_gate(state)
    if current_gate == "mechanical":
        raise HTTPException(
            status_code=409,
            detail="Сначала движок должен завершить механическую проверку",
        )
    if current_gate is None:
        raise HTTPException(status_code=409, detail="Все QA-гейты этой ревизии уже закрыты")
    if gate != current_gate:
        raise HTTPException(
            status_code=409,
            detail=f"Сейчас по порядку ожидается гейт {current_gate}, а не {gate}",
        )

    active_pack = await _active_pack()
    stale_pack = _workflow_stale_pack(state, active_pack)
    if stale_pack is not False:
        detail = (
            "Расчёт сделан по прежним данным предприятия"
            if stale_pack is True
            else "Не удалось подтвердить актуальность данных расчёта"
        )
        raise HTTPException(status_code=409, detail=detail)
    if (
        gate == "commercial"
        and verdict == "PASS"
        and _workflow_manual_review(state, active_pack).get("valid") is not True
    ):
        raise HTTPException(
            status_code=409,
            detail="Сначала закройте все пункты ручной сверки текущей книги",
        )

    stdin_body: dict[str, Any] = {"reasons": reasons}
    if verdict == "ADJUST":
        stdin_body["adjust_owner"] = adjust_owner
    stdin = json.dumps(stdin_body, ensure_ascii=False).encode("utf-8")
    return await _run_admin(
        [
            "qa-verdict",
            "--order-id",
            order_id,
            "--expected-revision",
            str(expected_revision),
            "--gate",
            gate,
            "--verdict",
            verdict,
            "--actor",
            "panel",
        ],
        stdin=stdin,
        error_statuses=QA_ADMIN_ERROR_STATUSES,
        default_error_status=422,
    )


@router.post("/api/calc/orders/{order_id}/manual-review")
async def calc_order_manual_review(order_id: str, body: Any = Body(...)):
    """Close every exact manual item through the authenticated QA-only CLI."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Ожидался объект ручной сверки")
    if not order_id or len(order_id) > 128:
        raise HTTPException(status_code=422, detail="Некорректный идентификатор заказа")
    if set(body) != MANUAL_REVIEW_BODY_FIELDS:
        raise HTTPException(
            status_code=422,
            detail="Ручная сверка требует expected_revision, expected_book_digest, reviewed_by и reviews",
        )
    expected_revision = body.get("expected_revision")
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        raise HTTPException(status_code=422, detail="expected_revision должен быть целым числом")
    expected_book_digest = body.get("expected_book_digest")
    if (
        not isinstance(expected_book_digest, str)
        or len(expected_book_digest) != 64
        or any(character not in "0123456789abcdef" for character in expected_book_digest)
    ):
        raise HTTPException(status_code=422, detail="expected_book_digest должен быть sha256 книги")
    reviewed_by = body.get("reviewed_by")
    if not isinstance(reviewed_by, str) or not reviewed_by.strip() or len(reviewed_by) > 512:
        raise HTTPException(status_code=422, detail="Укажите проверившего (до 512 символов)")
    reviews = body.get("reviews")
    if not isinstance(reviews, list) or not 1 <= len(reviews) <= 64:
        raise HTTPException(status_code=422, detail="reviews должен быть непустым списком до 64 пунктов")

    connection = _connect()
    try:
        row = connection.execute(
            "SELECT order_id, revision, status, created_at, updated_at, state_json "
            "FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        _log.warning("calc orders: manual review read failed: %s", exc)
        raise HTTPException(status_code=503, detail="Реестр заказов недоступен") from exc
    finally:
        connection.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    if row[1] != expected_revision:
        raise HTTPException(
            status_code=409,
            detail=f"Заказ уже изменился: ожидалась ревизия {expected_revision}, сейчас {row[1]}",
        )
    state = _load_state(row[5])
    workflow = _safe_mapping(state.get("workflow"))
    if workflow.get("status") not in {
        "BOOK_ASSEMBLED",
        "QA_MECHANICAL_PASS",
        "QA_TECHNOLOGICAL_PASS",
    }:
        raise HTTPException(status_code=409, detail="Текущая стадия не допускает ручную сверку книги")
    binding = _workflow_book_binding(state)
    if binding.get("valid") is not True:
        raise HTTPException(status_code=409, detail=binding.get("detail"))
    book = _safe_mapping(state.get("book"))
    if book.get("digest") != expected_book_digest:
        raise HTTPException(status_code=409, detail="Книга уже изменилась — обновите карточку")
    active_pack = await _active_pack()
    if _workflow_stale_pack(state, active_pack) is not False:
        raise HTTPException(status_code=409, detail="Актуальность данных книги не подтверждена")
    exact_items = _workflow_manual_review(state, active_pack)["items"]
    if len(reviews) != len(exact_items):
        raise HTTPException(status_code=409, detail="Нужно проверить каждый текущий пункт ровно один раз")
    normalized = []
    for expected, review in zip(exact_items, reviews, strict=True):
        if not isinstance(review, dict) or set(review) != MANUAL_REVIEW_ITEM_FIELDS:
            raise HTTPException(status_code=422, detail="Пункт сверки содержит неверные поля")
        if (
            review.get("item_index") != expected.get("item_index")
            or review.get("item_digest") != expected.get("item_digest")
        ):
            raise HTTPException(status_code=409, detail="Пункты ручной сверки изменились")
        for field in ("evidence", "reference"):
            value = review.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} должен содержать от 1 до 512 символов",
                )
        normalized.append(
            {
                "item_index": review["item_index"],
                "item_digest": review["item_digest"],
                "evidence": review["evidence"].strip(),
                "reference": review["reference"].strip(),
            }
        )

    return await _run_admin(
        [
            "manual-review-complete",
            "--order-id",
            order_id,
            "--expected-revision",
            str(expected_revision),
            "--expected-book-digest",
            expected_book_digest,
            "--actor",
            reviewed_by.strip(),
        ],
        stdin=json.dumps({"reviews": normalized}, ensure_ascii=False).encode("utf-8"),
        error_statuses=QA_ADMIN_ERROR_STATUSES,
        default_error_status=422,
    )


@router.post("/api/calc/orders/{order_id}/route-return")
async def calc_order_route_return(order_id: str, body: Any = Body(...)):
    """Return the current frozen route from the technological human gate."""
    if not order_id or len(order_id) > 128:
        raise HTTPException(status_code=422, detail="Некорректный идентификатор заказа")
    if not isinstance(body, dict) or set(body) != ROUTE_RETURN_BODY_FIELDS:
        raise HTTPException(
            status_code=422, detail="Возврат маршрута требует expected_revision и reason"
        )
    expected_revision = body.get("expected_revision")
    reason = body.get("reason")
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        raise HTTPException(status_code=422, detail="expected_revision должен быть целым числом")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 512:
        raise HTTPException(status_code=422, detail="Укажите причину возврата (до 512 символов)")

    connection = _connect()
    try:
        row = connection.execute(
            "SELECT order_id, revision, status, created_at, updated_at, state_json "
            "FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        _log.warning("calc orders: route return read failed: %s", exc)
        raise HTTPException(status_code=503, detail="Реестр заказов недоступен") from exc
    finally:
        connection.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    if row[1] != expected_revision:
        raise HTTPException(status_code=409, detail="Заказ уже изменился — обновите карточку")
    state = _load_state(row[5])
    if _workflow_book_binding(state).get("valid") is not True:
        raise HTTPException(status_code=409, detail="Книга не связана с текущим расчётом")
    if _workflow_current_qa_gate(state) != "technological":
        raise HTTPException(
            status_code=409,
            detail="Возврат маршрута доступен на текущем технологическом QA-гейте",
        )
    active_pack = await _active_pack()
    if _workflow_stale_pack(state, active_pack) is not False:
        raise HTTPException(status_code=409, detail="Актуальность данных книги не подтверждена")
    return await _run_admin(
        [
            "route-return",
            "--order-id",
            order_id,
            "--expected-revision",
            str(expected_revision),
            "--actor",
            "panel",
        ],
        stdin=json.dumps({"reason": reason.strip()}, ensure_ascii=False).encode("utf-8"),
        error_statuses=QA_ADMIN_ERROR_STATUSES,
        default_error_status=422,
    )


@router.get("/api/calc/orders/{order_id}")
async def calc_order_detail(order_id: str):
    """Полное состояние заказа, адаптированное без смешения поколений."""
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT order_id, revision, status, created_at, updated_at, state_json "
            "FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        _log.warning("calc orders: detail failed: %s", exc)
        raise HTTPException(status_code=503, detail="Реестр заказов недоступен") from exc
    finally:
        connection.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    state = _load_state(row[5])
    card = _row_to_card(row, await _active_pack())
    if card["kind"] == "draft":
        card["detail"] = {
            "stages": {}, "events": [], "provenance": state.get("provenance", {}),
            "source_files": calc_files._file_urls(order_id, state["folder_intake"]["files"]),
        }
    elif card["kind"] == "workflow":
        card["detail"] = {
            "stages": {},
            "events": _workflow_events(state),
            "source_files": _workflow_source_files(state),
            "provenance": state.get("provenance")
            if isinstance(state.get("provenance"), dict)
            else {},
            "route_steps": _selected_route_steps(state),
        }
    else:
        stages = state.get("stages")
        stages = stages if isinstance(stages, dict) else {}
        events = state.get("pipeline_events")
        card["detail"] = {
            "stages": {
                name: stages.get(name)
                for name in STAGE_ORDER
                if isinstance(stages.get(name), dict)
            },
            "events": events[-50:] if isinstance(events, list) else [],
            "source_files": state.get("source_files")
            if isinstance(state.get("source_files"), list)
            else [],
            "provenance": state.get("provenance")
            if isinstance(state.get("provenance"), dict)
            else {},
        }
    return card
