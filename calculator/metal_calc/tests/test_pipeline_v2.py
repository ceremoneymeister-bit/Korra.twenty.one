"""Тесты конвейера V2: пак, формулы времени, стадии, supervised-петля."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from metal_calc.errors import Conflict, InvalidRatePack, InvalidState
from metal_calc.packs2 import ACTIVE_POINTER, PipelinePackStore
from metal_calc.registry import Registry
from metal_calc.securefs import SecureRoot
from metal_calc.service2 import PipelineService
from metal_calc.timenorms import machine_minutes, piece_minutes

MONEY = {"kind": "client_canon", "ref": "synthetic-price-list", "as_of": "2026-08-20"}
HANDBOOK = {
    "kind": "handbook",
    "ref": "Справочник технолога-машиностроителя, т.2, изд.5, с.610 (TEMPLATE)",
    "as_of": "2026-08-20",
}


def pipeline_pack(revision: str = "v2-test") -> dict[str, Any]:
    return {
        "schema_version": 2,
        "revision": revision,
        # Раньше здесь стоял "template", и на нём считал ВЕСЬ набор тестов —
        # то есть проверялось поведение на образце, а не на данных клиента.
        # Панель публикует "active"; отказ на шаблоне проверяется отдельно.
        "status": "active",
        "blank_ops": {
            "laser": {"rate_kind": "per_cut_m", "rate_rub": "120", "rate_source": MONEY},
            "bandsaw": {"rate_kind": "per_cut", "rate_rub": "150", "rate_source": MONEY},
            "bench": {"rate_kind": "per_hour", "rate_rub": "900", "rate_source": MONEY},
        },
        "materials": {
            "steel-40x": {
                "grade": "40Х",
                "group": "steel",
                "stock": "purchase",
                "density_kg_m3": "7850",
                "rate_rub_per_kg": "95",
                "rate_source": MONEY,
            },
            "steel-09g2s": {
                "grade": "09Г2С",
                "group": "steel",
                "stock": "stocked",
                "density_kg_m3": "7850",
                "rate_rub_per_kg": "82",
                "rate_source": MONEY,
            },
        },
        "machines": {
            "turning": {"rate_rub_per_hour": "3000", "rate_source": MONEY},
            "milling": {"rate_rub_per_hour": "3200", "rate_source": MONEY},
            "drilling": {"rate_rub_per_hour": "2000", "rate_source": MONEY},
            "edm": {"rate_rub_per_hour": "4200", "rate_source": MONEY},
        },
        "norm_params": {
            "turning:steel": {
                "cutting_speed_m_min": "180",
                "feed_mm_rev": "0.3",
                "depth_mm": "2",
                "source": HANDBOOK,
            },
            "drilling:steel": {
                "cutting_speed_m_min": "25",
                "feed_mm_rev": "0.2",
                "source": HANDBOOK,
            },
            "milling:steel": {
                "feed_table_mm_min": "400",
                "depth_mm": "3",
                "source": HANDBOOK,
            },
            "edm:steel": {"removal_min_per_cm2": "0.8", "source": HANDBOOK},
        },
        "overheads": {
            "t_aux_min": "2",
            "k_service_rest_pct": "8",
            "t_setup_min": "20",
            "source": HANDBOOK,
        },
        "extras": {
            "packaging": {"rate_rub": "500", "rate_source": MONEY},
            "logistics": {"rate_rub": "35", "rate_source": MONEY},
        },
        "pricing": {
            "margin_basis": "on_cost",
            "margin_percent": "20",
            "vat_included": True,
            "vat_rate_pct": "20",
            "valid_days": 14,
            "rounding": "none",
        },
    }


@pytest.fixture
def packs(tmp_path: Path) -> PipelinePackStore:
    rates = tmp_path / "rates2"
    rates.mkdir()
    (rates / "v2-test.json").write_text(
        json.dumps(pipeline_pack()), encoding="utf-8"
    )
    # Указатель действующей ревизии: инструменты конвейера больше не получают
    # её аргументом, а читают отсюда.
    (rates / ACTIVE_POINTER).write_text(
        json.dumps({"revision": "v2-test"}), encoding="utf-8"
    )
    return PipelinePackStore(SecureRoot(rates, writable=False))


@pytest.fixture
def pipeline(tmp_path: Path, packs: PipelinePackStore) -> PipelineService:
    registry = Registry(tmp_path / "orders2" / "registry.db")
    return PipelineService(registry, packs)


def new_order(pipeline: PipelineService, order_id: str = "ord-1") -> int:
    revision, _ = pipeline.registry.create(
        order_id,
        {
            "order_id": order_id,
            "customer": {"name": "Synthetic"},
            "source_files": [],
            "status": "draft",
            "warnings": [],
            "timestamps": {},
            "provenance": {"created_by": "test"},
        },
    )
    return revision


# ── пак ──────────────────────────────────────────────────────────────────


def test_pack2_loads(packs: PipelinePackStore) -> None:
    pack = packs.load("v2-test")
    assert pack.status == "active"
    assert set(pack.blank_ops) == {"laser", "bandsaw", "bench"}
    assert pack.material("steel-40x")["stock"] == "purchase"
    assert pack.norms_for("turning", "steel")["source"]["kind"] == "handbook"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(revision="other"),
        lambda d: d.update(status="draft"),
        lambda d: d["blank_ops"].update(unknown={"rate_kind": "per_cut", "rate_rub": "1", "rate_source": MONEY}),
        lambda d: d["materials"]["steel-40x"].update(stock="maybe"),
        lambda d: d["norm_params"].update({"welding:steel": {"cutting_speed_m_min": "1", "feed_mm_rev": "1", "depth_mm": "1", "source": HANDBOOK}}),
        lambda d: d["norm_params"]["turning:steel"].update(source={"kind": "llm", "ref": "модель сказала", "as_of": "2026-08-20"}),
        lambda d: d["overheads"].update(k_service_rest_pct="150"),
    ],
)
def test_pack2_fail_closed(tmp_path: Path, mutate) -> None:
    data = pipeline_pack()
    mutate(data)
    rates = tmp_path / "broken"
    rates.mkdir()
    (rates / "v2-test.json").write_text(json.dumps(data), encoding="utf-8")
    store = PipelinePackStore(SecureRoot(rates, writable=False))
    with pytest.raises(InvalidRatePack):
        store.load("v2-test")


# ── формулы времени (контрольные значения посчитаны руками) ─────────────


def test_turning_control(packs: PipelinePackStore) -> None:
    pack = packs.load("v2-test")
    # n = 1000*180/(pi*100) = 572.9578 об/мин; проходы = ceil(4/2) = 2
    # T_o = 50*2 / (572.9578*0.3) = 0.5818 мин -> 0.58
    main = machine_minutes(
        pack, "turning", "steel", {"diameter_mm": 100, "length_mm": 50, "stock_mm": 4}
    )
    assert str(main["t_main_min"]) == "0.5818"
    assert main["detail"]["passes"] == 2
    # t_shift = (0.581776+2)*1.08 = 2.788318 (машинное время не округляется
    # до денег); total = 27.88318*... = 47.88; t_piece = 47.88/10
    piece = piece_minutes(pack, main, quantity=10, batch=10)
    assert piece["t_piece_min"] == 4.79
    assert piece["t_total_min"] == 47.88


def test_drilling_and_milling_and_edm(packs: PipelinePackStore) -> None:
    pack = packs.load("v2-test")
    # n = 1000*25/(pi*10) = 795.7747; T = 30/(795.7747*0.2) = 0.1885 -> 0.19
    drill = machine_minutes(pack, "drilling", "steel", {"diameter_mm": 10, "length_mm": 30})
    assert str(drill["t_main_min"]) == "0.1885"
    # проходы = ceil(6/3) = 2; T = 200*2/400 = 1.00
    mill = machine_minutes(pack, "milling", "steel", {"length_mm": 200, "stock_mm": 6})
    assert str(mill["t_main_min"]) == "1.0000"
    # 12.5 см2 * 0.8 мин/см2 = 10.00
    edm = machine_minutes(pack, "edm", "steel", {"surface_cm2": "12.5"})
    assert str(edm["t_main_min"]) == "10.0000"


def test_norms_missing_material_group(packs: PipelinePackStore) -> None:
    pack = packs.load("v2-test")
    with pytest.raises(InvalidRatePack):
        machine_minutes(pack, "turning", "titanium", {"diameter_mm": 10, "length_mm": 10, "stock_mm": 1})


def test_bad_params_rejected(packs: PipelinePackStore) -> None:
    pack = packs.load("v2-test")
    with pytest.raises(InvalidState):
        machine_minutes(pack, "turning", "steel", {"diameter_mm": -5, "length_mm": 10, "stock_mm": 1})
    with pytest.raises(InvalidState):
        piece_minutes(
            pack,
            machine_minutes(pack, "edm", "steel", {"surface_cm2": 1}),
            quantity=0,
            batch=1,
        )


# ── конвейер ────────────────────────────────────────────────────────────


def test_pipeline_end_to_end(pipeline: PipelineService) -> None:
    revision = new_order(pipeline)

    # Агент 1: заготовка. Металл под закупку -> звёздочка.
    blank = pipeline.blank_cost(
        "ord-1",
        revision,
        "steel-40x",
        10,
        "12.5",
        "total",
        "масса из чертежа поз.1, фланец",
        [
            {"op_code": "laser", "length_m": "12.4", "note": "рез контура"},
            {"op_code": "bandsaw", "cuts": 4, "note": "торцовка прутка"},
        ],
    )
    # 12.5*95 = 1187.50; 12.4*120 = 1488.00; 4*150 = 600 -> 3275.5
    assert blank["result"]["total_rub"] == 3275.5
    assert blank["result"]["provisional"] is True

    approved = pipeline.stage_approve("ord-1", blank["revision"], "blank", "методолог")
    assert approved["status"] == "approved"

    # Агент 2: маршрут; время до утверждения маршрута запрещено.
    route = pipeline.route_propose(
        "ord-1",
        approved["revision"],
        [
            {"seq": 1, "op_code": "bandsaw", "note": "отрезка заготовки"},
            {"seq": 2, "op_code": "turning", "note": "черновое и чистовое точение"},
            {"seq": 3, "op_code": "drilling", "note": "8 отверстий"},
        ],
    )
    with pytest.raises(InvalidState):
        pipeline.time_calc(
            "ord-1",
            route["revision"],
            "steel-40x",
            [{"route_seq": 2, "quantity": 10, "batch": 10, "params": {"diameter_mm": 100, "length_mm": 50, "stock_mm": 4}}],
        )
    route_ok = pipeline.stage_approve("ord-1", route["revision"], "route", "методолог")

    # Агент 3: нормы времени только по позициям утверждённого маршрута.
    time_result = pipeline.time_calc(
        "ord-1",
        route_ok["revision"],
        "steel-40x",
        [
            {
                "route_seq": 2,
                "quantity": 10,
                "batch": 10,
                "params": {"diameter_mm": 100, "length_mm": 50, "stock_mm": 4},
            },
            # Сверловка из утверждённого маршрута. Раньше её здесь не было, и
            # тест проходил: пропущенная операция уходила в цену нулём. Теперь
            # каждый станочный шаг маршрута обязан быть посчитан ровно раз.
            {
                "route_seq": 3,
                "quantity": 10,
                "batch": 10,
                "params": {"diameter_mm": 18, "length_mm": 20},
            },
        ],
    )
    with pytest.raises(InvalidState):
        pipeline.time_calc(
            "ord-1",
            time_result["revision"],
            "steel-40x",
            [{"route_seq": 9, "quantity": 1, "batch": 1, "params": {}}],
        )
    time_ok = pipeline.stage_approve("ord-1", time_result["revision"], "time", "методолог")

    # Снабжение подтвердило цену металла: 95 -> 101 руб/кг, звёздочка снята.
    confirmed = pipeline.supply_confirm(
        "ord-1", time_ok["revision"], "101", "снабжение: Иванов", "счёт МК-778"
    )
    assert confirmed["result"]["provisional"] is False
    # 12.5*101 = 1262.50; итог 1262.5+1488+600 = 3350.5
    assert confirmed["result"]["total_rub"] == 3350.5

    # Сборка КП: blank 3350.5 + time 3862.00 (неокруглённое машинное время) + упаковка 500
    quote = pipeline.quote_build(
        "ord-1", confirmed["revision"], {"packaging": 1}
    )
    cost = quote["result"]["cost"]
    assert cost["total_rub"] == 7712.5
    # net = 7712.50*1.2 = 9255.00; gross = net*1.2 = 11106.00
    assert quote["result"]["price"]["total_rub"] == 11106.0
    assert quote["result"]["provisional"] is False

    status = pipeline.pipeline_status("ord-1")
    assert [e["event"] for e in status["events"]] == [
        "proposed",
        "approved",
        "proposed",
        "approved",
        "proposed",
        "approved",
        "supply_confirmed",
        "proposed",
    ]


def test_route_locked_after_time(pipeline: PipelineService) -> None:
    revision = new_order(pipeline, "ord-2")
    blank = pipeline.blank_cost(
        "ord-2", revision, "steel-09g2s", 1, "3", "total", "лист",
        [{"op_code": "laser", "length_m": 1, "note": "рез"}],
    )
    revision = pipeline.stage_approve("ord-2", blank["revision"], "blank", "методолог")["revision"]
    route = pipeline.route_propose(
        "ord-2", revision, [{"seq": 1, "op_code": "turning", "note": "точение"}]
    )
    ok = pipeline.stage_approve("ord-2", route["revision"], "route", "методолог")
    time_result = pipeline.time_calc(
        "ord-2",
        ok["revision"],
        "steel-09g2s",
        [
            {
                "route_seq": 1,
                "quantity": 1,
                "batch": 1,
                "params": {"diameter_mm": 50, "length_mm": 40, "stock_mm": 2},
            }
        ],
    )
    with pytest.raises(Conflict):
        pipeline.route_propose(
            "ord-2",
            time_result["revision"],
            [{"seq": 1, "op_code": "milling", "note": "передумали"}],
        )


def test_optimistic_locking(pipeline: PipelineService) -> None:
    new_order(pipeline, "ord-3")
    with pytest.raises(Conflict):
        pipeline.blank_cost(
            "ord-3", 99, "steel-40x", 1, "1", "total", "масса",
            [{"op_code": "bandsaw", "cuts": 1, "note": "рез"}],
        )


def test_stocked_material_is_not_provisional(pipeline: PipelineService) -> None:
    revision = new_order(pipeline, "ord-4")
    blank = pipeline.blank_cost(
        "ord-4",
        revision,
        "steel-09g2s",
        1,
        "3",
        "total",
        "оприходованный лист",
        [{"op_code": "laser", "length_m": 1, "note": "рез"}],
    )
    assert blank["result"]["provisional"] is False
    with pytest.raises(Conflict):
        pipeline.supply_confirm("ord-4", blank["revision"], "90", "снабжение", "счёт")


# ── совместимость с путём v1 ────────────────────────────────────────────


def test_v1_validator_accepts_pipeline_fields(prepared_order, tmp_path: Path) -> None:
    """Заказ, прошедший через конвейер V2, не ломает строгую v1-валидацию."""
    service, order_id, source_id = prepared_order
    (service.settings.rates_root / "v2-test.json").write_text(
        json.dumps(pipeline_pack()), encoding="utf-8"
    )
    (service.settings.rates_root / ACTIVE_POINTER).write_text(
        json.dumps({"revision": "v2-test"}), encoding="utf-8"
    )
    pipe = PipelineService(
        service.registry,
        PipelinePackStore(SecureRoot(service.settings.rates_root, writable=False)),
    )
    current = service.order_get(order_id)["revision"]
    blank = pipe.blank_cost(
        order_id, current, "steel-40x", 1, "2", "total", "масса",
        [{"op_code": "bandsaw", "cuts": 1, "note": "рез"}],
    )
    current = pipe.stage_approve(order_id, blank["revision"], "blank", "методолог")["revision"]
    pipe.route_propose(
        order_id, current, [{"seq": 1, "op_code": "turning", "note": "точение"}]
    )
    analyzed = service.analyze_drawing(order_id, [source_id], None, "test-v1", "steel-test-4")
    fact_id = service.order_get(order_id)["order"]["manual_facts"][0]["fact_id"]
    service.calculate_quote(
        order_id, analyzed["geometry"]["revision"], "test-v1", "steel-test-4", [fact_id]
    )
    order = service.order_get(order_id)["order"]
    assert order["stages"]["route"]["status"] == "proposed"
    assert order["status"] == "calculated"


def test_route_error_lists_allowed_ops(pipeline: PipelineService) -> None:
    revision = new_order(pipeline, "ord-5")
    blank = pipeline.blank_cost(
        "ord-5", revision, "steel-40x", 1, "1", "total", "масса",
        [{"op_code": "bandsaw", "cuts": 1, "note": "рез"}],
    )
    revision = pipeline.stage_approve("ord-5", blank["revision"], "blank", "методолог")["revision"]
    with pytest.raises(InvalidState) as err:
        pipeline.route_propose(
            "ord-5", revision, [{"seq": 1, "op_code": "grind_od", "note": "x"}]
        )
    assert "allowed:" in str(err.value) and "turning" in str(err.value)
