"""Экран «Заказы» расчётчика: чтение реестра конвейера панелью.

Проверяем то, ради чего роутер существует: панель показывает то же
состояние, что видит агент, находит реестр там, где его прописал контур, и
не притворяется пустым, когда реестра нет.
"""

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from korra_cli.web_routers import calc_orders


# ---------------------------------------------------------------------------
# Фикстуры: реестр ровно той формы, какую пишет metal_calc.service2
# ---------------------------------------------------------------------------



def _digest_json(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _refresh_book_digest(state: dict) -> None:
    book = state["book"]
    book["contractor_quotes_digest"] = _digest_json(
        state.get("costing", {}).get("contractor_quotes", {})
    )
    book["digest"] = _digest_json({key: value for key, value in book.items() if key != "digest"})


def _make_registry(root: Path, orders: list[tuple[str, int, str, dict]]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "registry.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE orders (
            order_id TEXT PRIMARY KEY,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            state_json BLOB NOT NULL
        ) STRICT
        """
    )
    for order_id, revision, updated_at, state in orders:
        connection.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)",
            (
                order_id,
                revision,
                "draft",
                "2026-08-25T09:00:00Z",
                updated_at,
                # STRICT-таблица держит state_json как BLOB — так же, как
                # реальный реестр; поэтому и читаем мы байты, а не строку.
                json.dumps(state, ensure_ascii=False).encode("utf-8"),
            ),
        )
    connection.commit()
    connection.close()
    return path


def _state_blank_provisional() -> dict:
    """Заготовка утверждена, но металл — «со звёздочкой»."""
    return {
        "customer": {"name": "Аргентум"},
        "stages": {
            "blank": {
                "status": "approved",
                "proposed_at": "2026-08-25T10:29:43Z",
                "approved_at": "2026-08-25T10:31:18Z",
                "approved_by": "Лариса (оператор)",
                "result": {
                    "total_rub": 1337.5,
                    "provisional": True,
                    "lines": [
                        {"code": "material:steel-40x", "provisional": True, "subtotal_rub": 1187.5},
                        {"code": "blank:bandsaw", "provisional": False, "subtotal_rub": 150},
                    ],
                },
            }
        },
        "pipeline_events": [
            {"stage": "blank", "event": "proposed", "at": "2026-08-25T10:29:43Z"},
            {"stage": "blank", "event": "approved", "at": "2026-08-25T10:31:18Z"},
        ],
        "warnings": [],
    }


def _state_full_quote() -> dict:
    """Конвейер пройден до КП."""
    stage = lambda at: {  # noqa: E731 — короткий локальный конструктор
        "status": "approved",
        "proposed_at": at,
        "approved_at": at,
        "approved_by": "Лариса (оператор)",
        "result": {"provisional": False},
    }
    return {
        "customer": {"name": "Canary Test"},
        "stages": {
            "blank": stage("2026-08-25T10:31:18Z"),
            "route": stage("2026-08-25T10:37:24Z"),
            "time": stage("2026-08-25T10:55:00Z"),
            "quote": {
                "status": "proposed",
                "proposed_at": "2026-08-25T12:30:37Z",
                "result": {
                    "provisional": False,
                    "price": {"total_rub": 11618.4, "currency": "RUB", "vat_rate_pct": 20},
                },
            },
        },
        "pipeline_events": [{"stage": "quote", "event": "proposed", "at": "2026-08-25T12:30:37Z"}],
    }


def _state_v8_ready() -> dict:
    """Workflow v8: книга и три QA-квитанции одной ревизии."""
    state = {
        "customer": {"name": "Техстком", "external_ref": "34112-P01"},
        "source_files": [
            {
                "source_file_id": "src_1234567890abcdef",
                "name": "34112-P01.pdf",
                "sha256": "a" * 64,
                "format": "pdf",
                "bytes": 12345,
                "received_at": "2026-09-04T06:30:00Z",
                # Серверный путь не относится к операторской карточке.
                "internal_path": "/orders/34112-P01/source.pdf",
            }
        ],
        "status": "READY_FOR_LD",
        "workflow": {
            "schema_version": 3,
            "status": "READY_FOR_LD",
            "calculation_revision": 3,
            "route_return_count": 0,
            "quantity": 100,
            "kd_revision": "КД-34112",
            "frozen_variant_id": "rv-main",
        },
        "bom": {"root": "part-1", "nodes": {"part-1": {"quantity": 1}}},
        "route_variants": {
            "rv-main": {
                "status": "frozen",
                "digest": "a" * 64,
                "steps": [
                    {
                        "seq": 1,
                        "process_code": "CUT.LASER.SHEET",
                        "execution_mode": "in_house",
                        "cost_owner": "supply",
                        "note": "лазер",
                        "private_rate": 999,
                    }
                ],
            }
        },
        "costing": {
            "pack_fingerprint": "fp-current",
            "contractor_quotes": {},
            "blank": {
                "digest": "b" * 64,
                "pack_fingerprint": "fp-current",
                "total_rub": 300000,
            },
            "time": {
                "digest": "c" * 64,
                "pack_fingerprint": "fp-current",
                "total_rub": 32477.25,
            },
        },
        "book": {
            "calculation_revision": 3,
            "route_digest": "a" * 64,
            "blank_digest": "b" * 64,
            "time_digest": "c" * 64,
            "pack_fingerprint": "fp-current",
            "provisional": False,
            "route_unpriced": [],
            "manual_review_required": [],
            "cost": {
                "amount_kind": "INTERNAL_COST",
                "blank_rub": 300000,
                "machining_rub": 32477.25,
                "extras_rub": 0,
                "total_rub": 332477.25,
            },
            "price": {
                "amount_kind": "CUSTOMER_PRICE",
                "net_total_rub": 309294.38,
                "vat_amount_rub": 68044.76,
                "total_rub": 377339.14,
                "currency": "RUB",
                "vat_included": True,
                "vat_rate_pct": 22,
                "valid_until": "2099-09-18",
                "is_final": True,
                # Внутренняя разбивка не должна протечь в customer price.
                "blank_rub": 300000,
            },
        },
        "qa": {
            "3": {
                "mechanical": {
                    "verdict": "PASS",
                    "computed_by": "engine",
                    "at": "2026-09-04T07:00:00Z",
                    "checks": [{"code": "sum", "ok": True}, {"code": "vat", "ok": True}],
                },
                "technological": {
                    "verdict": "PASS",
                    "by": "Лариса",
                    "actor_role": "qa",
                    "at": "2026-09-04T07:05:00Z",
                    "reasons": [],
                },
                "commercial": {
                    "verdict": "PASS",
                    "by": "Лариса",
                    "actor_role": "qa",
                    "at": "2026-09-04T07:10:00Z",
                    "reasons": [],
                },
            }
        },
        "workflow_events": [
            {"event": "book_assembled", "at": "2026-09-04T06:55:00Z"},
            {
                "event": "qa_commercial:PASS",
                "at": "2026-09-04T07:10:00Z",
                "by": "Лариса",
                "internal_payload": {"rates": [1, 2, 3]},
            },
        ],
        "warnings": [],
    }
    _refresh_book_digest(state)
    return state


def _state_v8_with_blockers() -> dict:
    """Workflow v8: сумма есть, но подряд и ручная сверка ещё не закрыты."""
    state = _state_v8_ready()
    state["status"] = "QA_TECHNOLOGICAL_PASS"
    state["workflow"]["status"] = "QA_TECHNOLOGICAL_PASS"
    state["book"]["price"]["is_final"] = False
    state["book"]["price"]["status_note"] = "Сумма не окончательная, добавится сумма из КП"
    state["book"]["route_unpriced"] = [
        {
            "seq": 4,
            "process_code": "FINISH.POWDER_COAT",
            "actual_process_name": "Порошковая окраска",
            "execution_mode": "outsource",
            "note": "Ждём КП подрядчика",
        }
    ]
    state["route_variants"]["rv-main"]["steps"].append(
        {
            "seq": 4,
            "process_code": "FINISH.POWDER_COAT",
            "actual_process_name": "Порошковая окраска",
            "execution_mode": "outsource",
            "cost_owner": "external",
            "note": "Ждём КП подрядчика",
        }
    )
    state["book"]["manual_review_required"] = [
        {
            "seq": 2,
            "process_code": "FORM.BEND.SHEET",
            "reason": "Длина гиба больше собственной оси",
        }
    ]
    state["qa"]["3"].pop("commercial")
    _refresh_book_digest(state)
    return state


def _state_v8_waiting_for_technological_qa() -> dict:
    state = _state_v8_ready()
    state["status"] = "QA_MECHANICAL_PASS"
    state["workflow"]["status"] = "QA_MECHANICAL_PASS"
    state["book"]["price"]["is_final"] = False
    state["qa"]["3"].pop("technological")
    state["qa"]["3"].pop("commercial")
    _refresh_book_digest(state)
    return state


def _add_manual_review_receipt(state: dict, *, reviewed_by: str = "Лариса") -> dict:
    items = state["book"]["manual_review_required"]
    receipt = {
        "contract_version": 1,
        "book_digest": state["book"]["digest"],
        "manual_items_digest": _digest_json(items),
        "calculation_revision": state["workflow"]["calculation_revision"],
        "pack_revision": "r-schema4",
        "pack_fingerprint": "fp-current",
        "reviewed_by": reviewed_by,
        "actor_role": "qa",
        "recorded_at": "2026-09-04T08:00:00Z",
        "reviews": [
            {
                "item_index": index,
                "item_digest": _digest_json(item),
                "item": item,
                "evidence": "Ставка и формула сверены",
                "reference": f"ТК-{index + 1}",
            }
            for index, item in enumerate(items)
        ],
    }
    receipt["digest"] = _digest_json(receipt)
    state["qa"][str(state["workflow"]["calculation_revision"])][
        "manual_review_receipts"
    ] = [receipt]
    return receipt


@pytest.fixture
def registry_home(tmp_path, monkeypatch):
    """HERMES_HOME с реестром на месте по умолчанию и без конфига metal_calc."""
    _make_registry(
        tmp_path / "orders",
        [
            ("ord-2", 4, "2026-08-25T12:40:02Z", _state_blank_provisional()),
            ("ord-1", 9, "2026-08-25T12:30:37Z", _state_full_quote()),
        ],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    return tmp_path


@pytest.fixture
def v8_registry_home(tmp_path, monkeypatch):
    _make_registry(
        tmp_path / "orders",
        [
            ("34112-P01", 17, "2026-09-04T07:10:00Z", _state_v8_ready()),
            ("34112-P12", 12, "2026-09-04T07:05:00Z", _state_v8_with_blockers()),
        ],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    _fake_pack_active(
        monkeypatch,
        {"active": "r-schema4", "fingerprint": "fp-current", "sha256": "sha-current"},
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Где искать реестр
# ---------------------------------------------------------------------------


def test_registry_path_follows_contour_config(tmp_path, monkeypatch):
    """Каталог реестра берём из конфига контура, а не из своей константы.

    Ошибиться здесь — значит показать пустой экран при живом реестре, что
    неотличимо от «заказов ещё нет».
    """
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path / "home"))
    monkeypatch.setattr(
        calc_orders,
        "load_config",
        lambda: {
            "mcp_servers": {
                "metal_calc": {"env": {"METAL_CALC_ORDERS_ROOT": str(tmp_path / "elsewhere")}}
            }
        },
    )
    assert calc_orders._registry_path() == tmp_path / "elsewhere" / "registry.db"


def test_registry_path_falls_back_to_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    assert calc_orders._registry_path() == tmp_path / "orders" / "registry.db"


def test_broken_config_does_not_break_the_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(
        calc_orders, "load_config", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert calc_orders._registry_path() == tmp_path / "orders" / "registry.db"


# ---------------------------------------------------------------------------
# Список заказов
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_returns_orders_newest_first(registry_home):
    payload = await calc_orders.calc_orders_list()
    assert [o["order_id"] for o in payload["orders"]] == ["ord-2", "ord-1"]
    assert payload["stage_order"] == ["blank", "route", "time", "quote"]
    assert payload["stage_titles"]["blank"] == "Заготовка"


@pytest.mark.asyncio
async def test_list_surfaces_the_star_on_the_card(registry_home):
    """«Звёздочка» по металлу должна быть видна списком, а не только внутри.

    Из-за неё нельзя подписывать КП — если она всплывает только на третьем
    клике, её увидят после отправки цены заказчику.
    """
    ord2 = next(o for o in (await calc_orders.calc_orders_list())["orders"] if o["order_id"] == "ord-2")
    assert ord2["provisional"] is True
    assert ord2["stages"]["blank"]["provisional"] is True


@pytest.mark.asyncio
async def test_current_stage_is_the_first_unapproved(registry_home):
    """«Где заказ стоит» = первая неутверждённая стадия по порядку конвейера.

    Не последняя тронутая: после отката ревизии последней тронутой окажется
    уже пройденная стадия, и экран показал бы движение назад как прогресс.
    """
    orders = {o["order_id"]: o for o in (await calc_orders.calc_orders_list())["orders"]}
    assert orders["ord-2"]["current_stage"] == "route"
    assert orders["ord-1"]["current_stage"] == "quote"


@pytest.mark.asyncio
async def test_list_carries_the_price_when_the_quote_exists(registry_home):
    orders = {o["order_id"]: o for o in (await calc_orders.calc_orders_list())["orders"]}
    assert orders["ord-1"]["price"]["total_rub"] == pytest.approx(11618.4)
    assert orders["ord-2"]["price"] is None


@pytest.mark.asyncio
async def test_list_limit_is_clamped(registry_home):
    """Ограничение сверху режет, а бессмысленное снизу не опустошает экран."""
    assert len((await calc_orders.calc_orders_list(limit=1))["orders"]) == 1
    # 0 и отрицательный limit — это «не задан», а не «показать ноль заказов»:
    # пустой список читался бы как «заказов нет» при живом реестре.
    assert len((await calc_orders.calc_orders_list(limit=0))["orders"]) == 2
    assert len((await calc_orders.calc_orders_list(limit=-5))["orders"]) == 2
    assert len((await calc_orders.calc_orders_list(limit=10_000))["orders"]) == 2


# ---------------------------------------------------------------------------
# Карточка заказа
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_returns_stages_and_events(registry_home):
    card = await calc_orders.calc_order_detail("ord-2")
    assert card["customer"] == "Аргентум"
    assert card["detail"]["stages"]["blank"]["result"]["total_rub"] == pytest.approx(1337.5)
    assert [e["event"] for e in card["detail"]["events"]] == ["proposed", "approved"]


@pytest.mark.asyncio
async def test_detail_404_for_unknown_order(registry_home):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await calc_orders.calc_order_detail("ord-missing")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Реестра нет
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_registry_says_not_configured_and_creates_nothing(tmp_path, monkeypatch):
    """Отсутствие реестра — это 404 «не настроен», а не пустой список.

    И панель НЕ должна создавать файл: пустой registry.db на месте
    ненастроенного продукта потом читается как «заказов нет».
    """
    from fastapi import HTTPException

    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    with pytest.raises(HTTPException) as exc:
        await calc_orders.calc_orders_list()
    assert exc.value.status_code == 404
    assert not (tmp_path / "orders" / "registry.db").exists()


@pytest.mark.asyncio
async def test_registry_is_opened_read_only(registry_home):
    """Соединение открыто ``mode=ro`` — запись через него невозможна."""
    connection = calc_orders._connect()
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM orders")
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Устаревшие данные предприятия
# ---------------------------------------------------------------------------


def _fake_pack_active(monkeypatch, payload):
    async def fake(args, **kwargs):
        assert args == ["pack-active"]
        return payload

    monkeypatch.setattr(calc_orders, "_run_admin", fake)


def _state_with_fingerprints(fingerprint: str) -> dict:
    """Заказ, стадии которого несут отпечаток данных — как пишет service2."""
    return {
        "customer": {"name": "Аргентум"},
        "stages": {
            "blank": {
                "status": "approved",
                "approved_by": "larisa",
                "result": {"pack_fingerprint": fingerprint, "total_rub": 100.0},
            },
            "route": {
                "status": "approved",
                "approved_by": "larisa",
                "result": {"pack_fingerprint": fingerprint},
            },
        },
    }


@pytest.mark.asyncio
async def test_workflow_ready_surfaces_customer_price_and_real_status(v8_registry_home):
    """READY_FOR_LD не должен выглядеть как blank pending или терять цену."""
    orders = {order["order_id"]: order for order in (await calc_orders.calc_orders_list())["orders"]}
    ready = orders["34112-P01"]

    assert ready["kind"] == "workflow"
    assert ready["registry_status"] == "draft"
    assert ready["status"] == "READY_FOR_LD"
    assert ready["status_title"] == "Готово для решения человека"
    assert [stage["status"] for stage in ready["workflow_stages"]] == ["complete"] * 6
    assert ready["stages"] == {}
    assert ready["price"] == {
        "amount_kind": "CUSTOMER_PRICE",
        "net_total_rub": 309294.38,
        "vat_amount_rub": 68044.76,
        "total_rub": 377339.14,
        "currency": "RUB",
        "vat_included": True,
        "vat_rate_pct": 22,
        "valid_until": "2099-09-18",
        "is_final": True,
    }
    assert "blank_rub" not in ready["price"]
    assert ready["internal_cost"]["amount_kind"] == "INTERNAL_COST"
    assert ready["internal_cost"]["total_rub"] == pytest.approx(332477.25)
    assert ready["blockers"] == []
    assert ready["next_action"]["kind"] == "human_decision"


@pytest.mark.asyncio
async def test_workflow_surfaces_unresolved_commercial_blockers(v8_registry_home):
    orders = {order["order_id"]: order for order in (await calc_orders.calc_orders_list())["orders"]}
    blocked = orders["34112-P12"]

    assert blocked["status"] == "QA_TECHNOLOGICAL_PASS"
    assert blocked["provisional"] is True
    assert blocked["price"]["is_final"] is False
    assert blocked["route_unpriced"][0]["process_code"] == "FINISH.POWDER_COAT"
    assert blocked["contractor_quotes"] == [
        {
            "seq": 4,
            "process_code": "FINISH.POWDER_COAT",
            "actual_process_name": "Порошковая окраска",
            "execution_mode": "outsource",
            "note": "Ждём КП подрядчика",
            "status": "missing",
            "stale_reasons": [],
        }
    ]
    assert blocked["manual_review_required"][0]["process_code"] == "FORM.BEND.SHEET"
    assert blocked["manual_review_required"][0]["item_index"] == 0
    assert len(blocked["manual_review_required"][0]["item_digest"]) == 64
    assert blocked["manual_review_receipt"] is None
    assert {item["kind"] for item in blocked["blockers"]} == {
        "route_unpriced",
        "manual_review",
        "qa_pending",
    }
    assert blocked["next_action"]["kind"] == "contractor_quote"
    assert blocked["qa_receipts"]["mechanical"]["verdict"] == "PASS"
    assert blocked["qa_receipts"]["technological"]["verdict"] == "PASS"
    assert blocked["qa_receipts"]["commercial"] == {
        "title": "Коммерческая проверка",
        "verdict": None,
        "status": "pending",
    }


def test_customer_finality_requires_current_manual_review_receipt():
    state = _state_v8_ready()
    state["book"]["manual_review_required"] = [
        {"seq": 2, "process_code": "FORM.BEND.SHEET", "reason": "Сверить правило"}
    ]
    _refresh_book_digest(state)
    active = {"active": "r-schema4", "fingerprint": "fp-current"}

    assert calc_orders._workflow_customer_price_is_final(state, False, active) is False
    receipt = _add_manual_review_receipt(state)
    assert calc_orders._workflow_customer_price_is_final(state, False, active) is True

    receipt["book_digest"] = "f" * 64
    receipt["digest"] = _digest_json(
        {key: value for key, value in receipt.items() if key != "digest"}
    )
    assert calc_orders._workflow_customer_price_is_final(state, False, active) is False


def test_contractor_quote_line_becomes_stale_after_utc_valid_until():
    state = _state_v8_with_blockers()
    quote = {
        "contract_version": 1,
        "route_seq": 4,
        "process_code": "FINISH.POWDER_COAT",
        "amount_rub": 1250,
        "currency": "RUB",
        "basis": "order_total",
        "vat_included": True,
        "quoted_at": "2026-09-04",
        "valid_until": "2026-09-05",
        "source": "ООО Покраска",
        "reference": "КП-42",
        "order_quantity": 100,
        "normalized_basis": "order_total",
        "route_digest": "a" * 64,
        "calculation_revision": 3,
        "pack_fingerprint": "fp-current",
    }
    quote["digest"] = _digest_json(quote)
    state["costing"]["contractor_quotes"] = {"4": quote}
    active = {"active": "r-current", "fingerprint": "fp-current"}

    current = calc_orders._contractor_quote_lines(
        state, active, today=date(2026, 9, 5)
    )
    expired = calc_orders._contractor_quote_lines(
        state, active, today=date(2026, 9, 6)
    )
    future_quote = dict(quote)
    future_quote["quoted_at"] = "2026-09-07"
    future_quote["valid_until"] = "2026-09-08"
    future_quote["digest"] = _digest_json(
        {key: value for key, value in future_quote.items() if key != "digest"}
    )
    state["costing"]["contractor_quotes"] = {"4": future_quote}
    future = calc_orders._contractor_quote_lines(
        state, active, today=date(2026, 9, 6)
    )

    assert current[0]["status"] == "current"
    assert current[0]["quote"]["source"] == "ООО Покраска"
    assert expired[0]["status"] == "stale"
    assert expired[0]["stale_reasons"] == ["valid_until"]
    assert future[0]["status"] == "stale"
    assert future[0]["stale_reasons"] == ["quoted_at"]


@pytest.mark.asyncio
async def test_workflow_detail_uses_workflow_events_route_and_safe_sources(v8_registry_home):
    detail = await calc_orders.calc_order_detail("34112-P01")

    assert [event["event"] for event in detail["detail"]["events"]] == [
        "book_assembled",
        "qa_commercial:PASS",
    ]
    assert "internal_payload" not in detail["detail"]["events"][1]
    assert detail["detail"]["source_files"][0]["name"] == "34112-P01.pdf"
    assert "internal_path" not in detail["detail"]["source_files"][0]
    assert detail["detail"]["route_steps"][0]["process_code"] == "CUT.LASER.SHEET"
    assert "private_rate" not in detail["detail"]["route_steps"][0]


@pytest.mark.asyncio
async def test_workflow_stale_pack_checks_component_fingerprints(tmp_path, monkeypatch):
    state = _state_v8_ready()
    state["costing"].pop("pack_fingerprint")
    state["book"].pop("pack_fingerprint")
    state["costing"]["blank"]["pack_fingerprint"] = "fp-old"
    state["costing"]["time"]["pack_fingerprint"] = "fp-old"
    _refresh_book_digest(state)
    _make_registry(
        tmp_path / "orders",
        [("ord-workflow-stale", 5, "2026-09-04T08:00:00Z", state)],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    _fake_pack_active(
        monkeypatch,
        {"active": "r-new", "fingerprint": "fp-new", "sha256": "sha-new"},
    )

    card = (await calc_orders.calc_orders_list())["orders"][0]
    assert card["stale_pack"] is True
    assert card["price"]["is_final"] is False
    assert card["provisional"] is True
    assert card["blockers"][0]["kind"] == "stale_pack"
    assert card["next_action"]["kind"] == "recalculate"


def test_workflow_missing_book_fingerprint_can_never_look_final():
    state = _state_v8_ready()
    state["book"].pop("pack_fingerprint")
    _refresh_book_digest(state)
    active = {"active": "r-current", "fingerprint": "fp-current"}

    stale = calc_orders._workflow_stale_pack(state, active)
    binding = calc_orders._workflow_book_binding(state)

    assert stale is True
    assert binding["valid"] is False
    assert calc_orders._workflow_customer_price_is_final(state, stale, active) is False


@pytest.mark.asyncio
async def test_workflow_missing_qa_receipts_are_pending_not_pass(tmp_path, monkeypatch):
    state = _state_v8_ready()
    state["status"] = "BOOK_ASSEMBLED"
    state["workflow"]["status"] = "BOOK_ASSEMBLED"
    state["qa"] = {}
    _make_registry(
        tmp_path / "orders",
        [("ord-awaits-qa", 2, "2026-09-04T08:10:00Z", state)],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    _fake_pack_active(
        monkeypatch,
        {"active": "r-current", "fingerprint": "fp-current", "sha256": "sha"},
    )

    card = (await calc_orders.calc_orders_list())["orders"][0]
    assert card["price"]["is_final"] is False
    assert card["provisional"] is True
    assert [item["kind"] for item in card["blockers"]] == ["qa_pending"] * 3
    assert [item["title"] for item in card["blockers"]] == [
        "Ожидается: механическая проверка",
        "Ожидается: технологическая проверка",
        "Ожидается: коммерческая проверка",
    ]
    assert card["workflow_stages"][-1]["status"] == "current"
    assert card["next_action"] == {
        "kind": "qa_engine",
        "label": "Ожидается автоматическая механическая проверка",
        "profile": None,
        "qa_gate": "mechanical",
    }


@pytest.mark.asyncio
async def test_workflow_next_action_exposes_only_current_human_qa_gate(
    tmp_path, monkeypatch
):
    state = _state_v8_waiting_for_technological_qa()
    _make_registry(
        tmp_path / "orders",
        [("ord-awaits-tech-qa", 17, "2026-09-04T08:12:00Z", state)],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    _fake_pack_active(
        monkeypatch,
        {"active": "r-current", "fingerprint": "fp-current", "sha256": "sha"},
    )

    card = (await calc_orders.calc_orders_list())["orders"][0]
    assert card["next_action"] == {
        "kind": "qa_human",
        "label": "Провести технологическую проверку человеком",
        "profile": None,
        "qa_gate": "technological",
    }


@pytest.mark.asyncio
async def test_workflow_rejects_operator_forged_mechanical_pass(tmp_path, monkeypatch):
    state = _state_v8_ready()
    state["qa"]["3"]["mechanical"]["computed_by"] = "operator"
    _make_registry(
        tmp_path / "orders",
        [("ord-forged-mechanical", 3, "2026-09-04T08:15:00Z", state)],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    _fake_pack_active(
        monkeypatch,
        {"active": "r-current", "fingerprint": "fp-current", "sha256": "sha"},
    )

    card = (await calc_orders.calc_orders_list())["orders"][0]
    assert card["price"]["is_final"] is False
    assert card["provisional"] is True
    assert card["qa_receipts"]["mechanical"]["status"] == "attention"
    assert card["workflow_stages"][-1]["status"] != "complete"
    assert [item["kind"] for item in card["blockers"]] == ["qa"]
    assert "движком" in card["blockers"][0]["detail"]
    assert card["next_action"]["kind"] == "resolve_blocker"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gate", "actor_role"),
    [("technological", "front"), ("commercial", {"forged": "qa"})],
    ids=["front-role", "corrupt-role"],
)
async def test_workflow_rejects_human_pass_without_qa_provenance(
    tmp_path, monkeypatch, gate, actor_role
):
    state = _state_v8_ready()
    state["qa"]["3"][gate]["actor_role"] = actor_role
    _make_registry(
        tmp_path / "orders",
        [("ord-forged-human", 3, "2026-09-04T08:16:00Z", state)],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    _fake_pack_active(
        monkeypatch,
        {"active": "r-current", "fingerprint": "fp-current", "sha256": "sha"},
    )

    card = (await calc_orders.calc_orders_list())["orders"][0]
    assert card["price"]["is_final"] is False
    assert card["provisional"] is True
    assert card["qa_receipts"][gate]["status"] == "attention"
    blocker = next(item for item in card["blockers"] if item.get("gate") == gate)
    assert blocker["kind"] == "qa"
    assert "ролью QA" in blocker["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["calculation_revision", "blank_digest", "book_digest"],
)
async def test_workflow_rejects_book_not_bound_to_current_calculation(
    tmp_path, monkeypatch, case
):
    state = _state_v8_ready()
    if case == "calculation_revision":
        state["workflow"]["calculation_revision"] = 4
        state["qa"]["4"] = state["qa"].pop("3")
    elif case == "blank_digest":
        state["costing"]["blank"]["digest"] = "d" * 64
    else:
        state["book"]["price"]["total_rub"] += 1
    _make_registry(
        tmp_path / "orders",
        [("ord-unbound-book", 3, "2026-09-04T08:17:00Z", state)],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    _fake_pack_active(
        monkeypatch,
        {"active": "r-current", "fingerprint": "fp-current", "sha256": "sha"},
    )

    card = (await calc_orders.calc_orders_list())["orders"][0]
    assert card["book_binding_valid"] is False
    assert card["price"]["is_final"] is False
    assert card["provisional"] is True
    assert "book_binding" in {item["kind"] for item in card["blockers"]}
    assert card["next_action"]["kind"] == "recalculate"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("valid_until", "blocker_kind"),
    [("2000-01-01", "price_expired"), ("завтра", "price_validity")],
)
async def test_workflow_rejects_expired_or_invalid_customer_price(
    tmp_path, monkeypatch, valid_until, blocker_kind
):
    state = _state_v8_ready()
    state["book"]["price"]["valid_until"] = valid_until
    _refresh_book_digest(state)
    _make_registry(
        tmp_path / "orders",
        [("ord-price-invalid", 3, "2026-09-04T08:17:30Z", state)],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    _fake_pack_active(
        monkeypatch,
        {"active": "r-current", "fingerprint": "fp-current", "sha256": "sha"},
    )

    card = (await calc_orders.calc_orders_list())["orders"][0]
    assert card["book_binding_valid"] is True
    assert card["price"]["is_final"] is False
    assert card["provisional"] is True
    assert blocker_kind in {item["kind"] for item in card["blockers"]}
    assert card["next_action"]["kind"] == "recalculate"


@pytest.mark.parametrize(
    ("valid_until", "issue"),
    [("2026-09-04", None), ("2026-09-03", "expired"), ("2026-02-29", "invalid")],
)
def test_workflow_price_validity_uses_inclusive_utc_date(valid_until, issue):
    assert (
        calc_orders._workflow_price_validity(
            {"valid_until": valid_until}, today=date(2026, 9, 4)
        )
        == issue
    )


@pytest.mark.parametrize("checks", [[], [{"code": "sum", "ok": True}, {"ok": False}]])
def test_workflow_rejects_empty_or_failed_mechanical_checks(checks):
    state = _state_v8_ready()
    state["qa"]["3"]["mechanical"]["checks"] = checks

    receipts = calc_orders._workflow_qa_receipts(state)

    assert receipts["mechanical"]["verdict"] == "PASS"
    assert receipts["mechanical"]["status"] == "attention"


@pytest.mark.asyncio
async def test_contractor_quote_endpoint_uses_exact_route_revision_and_panel_actor(
    v8_registry_home, monkeypatch
):
    called = {}

    async def fake_admin(args, **kwargs):
        called["args"] = args
        called["stdin"] = kwargs["stdin"]
        called["error_statuses"] = kwargs["error_statuses"]
        return {"ok": True, "revision": 13, "calculation_revision": 4}

    monkeypatch.setattr(calc_orders, "_run_admin", fake_admin)
    result = await calc_orders.calc_order_contractor_quote(
        "34112-P12",
        {
            "expected_revision": 12,
            "route_seq": 4,
            "amount_rub": "1250.50",
            "basis": "order_total",
            "vat_included": True,
            "quoted_at": "2026-09-04",
            "valid_until": "2026-10-04",
            "source": " ООО Покраска ",
            "reference": " КП-42 ",
        },
    )

    assert result["revision"] == 13
    assert called["args"] == [
        "contractor-quote-set",
        "--order-id",
        "34112-P12",
        "--expected-revision",
        "12",
        "--route-seq",
        "4",
        "--actor",
        "panel",
    ]
    assert json.loads(called["stdin"]) == {
        "amount_rub": "1250.50",
        "basis": "order_total",
        "vat_included": True,
        "quoted_at": "2026-09-04",
        "valid_until": "2026-10-04",
        "source": "ООО Покраска",
        "reference": "КП-42",
    }
    assert called["error_statuses"]["RevisionConflict"] == 409


@pytest.mark.asyncio
async def test_contractor_quote_endpoint_rejects_stale_or_non_outsource_step(
    v8_registry_home,
):
    payload = {
        "expected_revision": 12,
        "route_seq": 4,
        "amount_rub": "1250",
        "basis": "per_piece",
        "vat_included": False,
        "quoted_at": "2026-09-04",
        "valid_until": "2026-10-04",
        "source": "ООО Покраска",
        "reference": "КП-42",
    }
    with pytest.raises(calc_orders.HTTPException) as stale:
        await calc_orders.calc_order_contractor_quote(
            "34112-P12", payload | {"expected_revision": 11}
        )
    assert stale.value.status_code == 409

    with pytest.raises(calc_orders.HTTPException) as in_house:
        await calc_orders.calc_order_contractor_quote(
            "34112-P12", payload | {"route_seq": 1}
        )
    assert in_house.value.status_code == 409

    for invalid in (
        payload | {"amount_rub": 0},
        payload | {"basis": "free_text"},
        payload | {"vat_included": "yes"},
        payload | {"quoted_at": "04.09.2026"},
        payload | {"quoted_at": "2999-09-04", "valid_until": "2999-10-04"},
        payload | {"valid_until": "2026-09-03"},
        payload | {"actor": "forged"},
    ):
        with pytest.raises(calc_orders.HTTPException) as rejected:
            await calc_orders.calc_order_contractor_quote("34112-P12", invalid)
        assert rejected.value.status_code == 422


@pytest.mark.asyncio
async def test_human_qa_endpoint_passes_exact_revision_gate_and_panel_actor(
    tmp_path, monkeypatch
):
    state = _state_v8_waiting_for_technological_qa()
    _make_registry(
        tmp_path / "orders",
        [("ord-human-qa", 17, "2026-09-04T08:18:00Z", state)],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    monkeypatch.setattr(
        calc_orders,
        "_active_pack",
        lambda: _async_value({"active": "r-current", "fingerprint": "fp-current"}),
    )
    called = {}

    async def fake_admin(args, **kwargs):
        called["args"] = args
        called["stdin"] = kwargs.get("stdin")
        called["default_error_status"] = kwargs.get("default_error_status")
        called["error_statuses"] = kwargs.get("error_statuses")
        return {
            "ok": True,
            "order_id": "ord-human-qa",
            "revision": 18,
            "status": "QA_TECHNOLOGICAL_PASS",
            "gate": "technological",
            "verdict": "PASS",
        }

    monkeypatch.setattr(calc_orders, "_run_admin", fake_admin)
    result = await calc_orders.calc_order_qa_verdict(
        "ord-human-qa",
        {
            "expected_revision": 17,
            "gate": "technological",
            "verdict": "PASS",
            "reasons": [],
        },
    )

    assert result["revision"] == 18
    assert called["args"] == [
        "qa-verdict",
        "--order-id",
        "ord-human-qa",
        "--expected-revision",
        "17",
        "--gate",
        "technological",
        "--verdict",
        "PASS",
        "--actor",
        "panel",
    ]
    assert json.loads(called["stdin"]) == {"reasons": []}
    assert called["default_error_status"] == 422
    assert called["error_statuses"]["RevisionConflict"] == 409


@pytest.mark.asyncio
async def test_human_qa_endpoint_passes_exact_adjust_owner_through_stdin(
    tmp_path, monkeypatch
):
    state = _state_v8_waiting_for_technological_qa()
    _make_registry(
        tmp_path / "orders",
        [("ord-human-adjust", 17, "2026-09-04T08:18:30Z", state)],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    monkeypatch.setattr(
        calc_orders,
        "_active_pack",
        lambda: _async_value({"active": "r-current", "fingerprint": "fp-current"}),
    )
    called = {}

    async def fake_admin(args, **kwargs):
        called["args"] = args
        called["stdin"] = kwargs["stdin"]
        return {
            "ok": True,
            "order_id": "ord-human-adjust",
            "revision": 18,
            "status": "DETAILED_COSTING",
            "gate": "technological",
            "verdict": "ADJUST",
        }

    monkeypatch.setattr(calc_orders, "_run_admin", fake_admin)
    result = await calc_orders.calc_order_qa_verdict(
        "ord-human-adjust",
        {
            "expected_revision": 17,
            "gate": "technological",
            "verdict": "ADJUST",
            "reasons": ["Уточнить норму времени"],
            "adjust_owner": "norm",
        },
    )

    assert result["verdict"] == "ADJUST"
    assert called["args"] == [
        "qa-verdict",
        "--order-id",
        "ord-human-adjust",
        "--expected-revision",
        "17",
        "--gate",
        "technological",
        "--verdict",
        "ADJUST",
        "--actor",
        "panel",
    ]
    assert json.loads(called["stdin"]) == {
        "reasons": ["Уточнить норму времени"],
        "adjust_owner": "norm",
    }


@pytest.mark.asyncio
async def test_human_qa_endpoint_rejects_stale_revision_and_out_of_order_gate(
    tmp_path, monkeypatch
):
    state = _state_v8_waiting_for_technological_qa()
    _make_registry(
        tmp_path / "orders",
        [("ord-human-qa", 17, "2026-09-04T08:18:00Z", state)],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    monkeypatch.setattr(
        calc_orders,
        "_active_pack",
        lambda: _async_value({"active": "r-current", "fingerprint": "fp-current"}),
    )

    with pytest.raises(calc_orders.HTTPException) as stale:
        await calc_orders.calc_order_qa_verdict(
            "ord-human-qa",
            {"expected_revision": 16, "gate": "technological", "verdict": "PASS"},
        )
    assert stale.value.status_code == 409

    with pytest.raises(calc_orders.HTTPException) as out_of_order:
        await calc_orders.calc_order_qa_verdict(
            "ord-human-qa",
            {"expected_revision": 17, "gate": "commercial", "verdict": "PASS"},
        )
    assert out_of_order.value.status_code == 409


@pytest.mark.asyncio
async def test_human_qa_endpoint_validates_reasons_and_never_accepts_mechanical(
    tmp_path, monkeypatch
):
    state = _state_v8_waiting_for_technological_qa()
    _make_registry(
        tmp_path / "orders",
        [("ord-human-qa", 17, "2026-09-04T08:18:00Z", state)],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})

    for payload in (
        {"expected_revision": 17, "gate": "technological", "verdict": "ADJUST"},
        {
            "expected_revision": 17,
            "gate": "technological",
            "verdict": "ADJUST",
            "reasons": ["Нужна корректировка"],
        },
        {
            "expected_revision": 17,
            "gate": "technological",
            "verdict": "ADJUST",
            "reasons": ["Нужна корректировка"],
            "adjust_owner": "sales",
        },
        {
            "expected_revision": 17,
            "gate": "technological",
            "verdict": "PASS",
            "adjust_owner": "front",
        },
        {
            "expected_revision": 17,
            "gate": "technological",
            "verdict": "PASS",
            "actor": "forged-user",
        },
        {"expected_revision": 17, "gate": "technological", "verdict": "FAIL"},
        {"expected_revision": 17, "gate": "mechanical", "verdict": "PASS"},
    ):
        with pytest.raises(calc_orders.HTTPException) as invalid:
            await calc_orders.calc_order_qa_verdict("ord-human-qa", payload)
        assert invalid.value.status_code == 422


@pytest.mark.asyncio
async def test_manual_review_endpoint_binds_exact_book_items_and_reviewer(
    tmp_path, monkeypatch
):
    state = _state_v8_with_blockers()
    _make_registry(
        tmp_path / "orders",
        [("ord-manual-review", 17, "2026-09-04T08:18:00Z", state)],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    monkeypatch.setattr(
        calc_orders,
        "_active_pack",
        lambda: _async_value({"active": "r-schema4", "fingerprint": "fp-current"}),
    )
    called = {}

    async def fake_admin(args, **kwargs):
        called["args"] = args
        called["stdin"] = kwargs["stdin"]
        return {"ok": True, "revision": 18}

    monkeypatch.setattr(calc_orders, "_run_admin", fake_admin)
    item = calc_orders._workflow_manual_review(
        state, {"active": "r-schema4", "fingerprint": "fp-current"}
    )["items"][0]
    result = await calc_orders.calc_order_manual_review(
        "ord-manual-review",
        {
            "expected_revision": 17,
            "expected_book_digest": state["book"]["digest"],
            "reviewed_by": "Лариса",
            "reviews": [
                {
                    "item_index": item["item_index"],
                    "item_digest": item["item_digest"],
                    "evidence": "Проверено по техкарте",
                    "reference": "ТК-42",
                }
            ],
        },
    )

    assert result["revision"] == 18
    assert called["args"] == [
        "manual-review-complete",
        "--order-id",
        "ord-manual-review",
        "--expected-revision",
        "17",
        "--expected-book-digest",
        state["book"]["digest"],
        "--actor",
        "Лариса",
    ]
    assert json.loads(called["stdin"])["reviews"][0]["reference"] == "ТК-42"

    with pytest.raises(calc_orders.HTTPException) as stale_item:
        await calc_orders.calc_order_manual_review(
            "ord-manual-review",
            {
                "expected_revision": 17,
                "expected_book_digest": state["book"]["digest"],
                "reviewed_by": "Лариса",
                "reviews": [
                    {
                        "item_index": 0,
                        "item_digest": "0" * 64,
                        "evidence": "Проверено",
                        "reference": "ТК-42",
                    }
                ],
            },
        )
    assert stale_item.value.status_code == 409


@pytest.mark.asyncio
async def test_route_return_endpoint_requires_current_technological_gate(
    tmp_path, monkeypatch
):
    state = _state_v8_waiting_for_technological_qa()
    _make_registry(
        tmp_path / "orders",
        [("ord-route-return", 17, "2026-09-04T08:18:00Z", state)],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    monkeypatch.setattr(
        calc_orders,
        "_active_pack",
        lambda: _async_value({"active": "r-schema4", "fingerprint": "fp-current"}),
    )
    called = {}

    async def fake_admin(args, **kwargs):
        called["args"] = args
        called["stdin"] = kwargs["stdin"]
        return {"ok": True, "revision": 18, "status": "ROUTE_OPTIONS_READY"}

    monkeypatch.setattr(calc_orders, "_run_admin", fake_admin)
    result = await calc_orders.calc_order_route_return(
        "ord-route-return",
        {"expected_revision": 17, "reason": "Маршрут не соответствует КД"},
    )
    assert result["status"] == "ROUTE_OPTIONS_READY"
    assert called["args"] == [
        "route-return",
        "--order-id",
        "ord-route-return",
        "--expected-revision",
        "17",
        "--actor",
        "panel",
    ]
    assert json.loads(called["stdin"]) == {"reason": "Маршрут не соответствует КД"}

    state["qa"]["3"]["technological"] = {
        "verdict": "PASS",
        "actor_role": "qa",
    }
    state["workflow"]["status"] = "QA_TECHNOLOGICAL_PASS"
    closed_home = tmp_path / "closed"
    _make_registry(
        closed_home / "orders",
        [("ord-route-return", 17, "2026-09-04T08:19:00Z", state)],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(closed_home))
    with pytest.raises(calc_orders.HTTPException) as closed:
        await calc_orders.calc_order_route_return(
            "ord-route-return",
            {"expected_revision": 17, "reason": "Повторный возврат"},
        )
    assert closed.value.status_code == 409


def _async_value(value):
    async def result():
        return value

    return result()


def test_human_qa_endpoint_requires_dashboard_authentication():
    from starlette.testclient import TestClient

    from korra_cli.web_server import app

    response = TestClient(app).post(
        "/api/calc/orders/ord-human-qa/qa-verdict",
        json={
            "expected_revision": 17,
            "gate": "technological",
            "verdict": "PASS",
            "reasons": [],
        },
    )

    assert response.status_code == 401

    quote_response = TestClient(app).post(
        "/api/calc/orders/ord-human-qa/contractor-quote",
        json={
            "expected_revision": 17,
            "route_seq": 4,
            "amount_rub": "1200",
            "basis": "order_total",
            "vat_included": True,
            "quoted_at": "2026-09-04",
            "valid_until": "2026-10-04",
            "source": "ООО Подряд",
            "reference": "КП-42",
        },
    )
    assert quote_response.status_code == 401

    manual_response = TestClient(app).post(
        "/api/calc/orders/ord-human-qa/manual-review",
        json={
            "expected_revision": 17,
            "expected_book_digest": "a" * 64,
            "reviewed_by": "Лариса",
            "reviews": [],
        },
    )
    assert manual_response.status_code == 401

    return_response = TestClient(app).post(
        "/api/calc/orders/ord-human-qa/route-return",
        json={"expected_revision": 17, "reason": "Маршрут неверен"},
    )
    assert return_response.status_code == 401


@pytest.mark.asyncio
async def test_workflow_does_not_offer_qa_before_book_exists(tmp_path, monkeypatch):
    state = _state_v8_ready()
    state["status"] = "BOM_VALIDATED"
    state["workflow"]["status"] = "BOM_VALIDATED"
    state["workflow"].pop("frozen_variant_id")
    state.pop("costing")
    state.pop("book")
    state["qa"] = {}
    _make_registry(
        tmp_path / "orders",
        [("ord-needs-route", 2, "2026-09-04T08:20:00Z", state)],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})
    _fake_pack_active(
        monkeypatch,
        {"active": "r-current", "fingerprint": "fp-current", "sha256": "sha"},
    )

    card = (await calc_orders.calc_orders_list())["orders"][0]
    assert card["blockers"] == []
    assert card["workflow_stages"][2]["status"] == "current"
    assert card["next_action"]["kind"] == "route"


@pytest.mark.asyncio
async def test_stale_stages_are_named_on_the_card(tmp_path, monkeypatch):
    """Правка прайса видна списком заказов, а не только отказом инструмента.

    До баннера stale-состояние знал только агент: методолог правила ставку,
    а экран продолжал говорить «всё в порядке» — до первого отказа посреди
    работы. Второй прогон — про меру сравнения: отпечаток ДАННЫХ, а не хеш
    файла; повторное сохранение без правок не должно старить живые заказы.
    """
    _make_registry(
        tmp_path / "orders",
        [("ord-fp", 3, "2026-08-30T10:00:00Z", _state_with_fingerprints("fp-old"))],
    )
    monkeypatch.setattr(calc_orders, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(calc_orders, "load_config", lambda: {})

    _fake_pack_active(
        monkeypatch,
        {"active": "r-new", "fingerprint": "fp-new", "sha256": "sha-new"},
    )
    payload = await calc_orders.calc_orders_list()
    card = payload["orders"][0]
    assert card["stale_stages"] == ["blank", "route"]
    assert payload["pack"] == {"active_revision": "r-new"}

    _fake_pack_active(
        monkeypatch,
        {"active": "r-resave", "fingerprint": "fp-old", "sha256": "sha-other"},
    )
    card = (await calc_orders.calc_orders_list())["orders"][0]
    assert card["stale_stages"] == []


@pytest.mark.asyncio
async def test_unavailable_cli_means_no_stale_check(registry_home):
    """Недоступный CLI не превращает список заказов в ошибку."""
    payload = await calc_orders.calc_orders_list()
    for order in payload["orders"]:
        assert order["stale_stages"] == []
    assert payload["pack"] is None
