from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from io import BytesIO
import json
from decimal import Decimal
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from metal_calc.errors import Conflict, InvalidState
from metal_calc.errors import InvalidRatePack
from metal_calc.rates import RatePackStore
from metal_calc.securefs import SecureRoot
from metal_calc.xlsx import _safe_cell
from metal_calc.xlsx import render_quote
from metal_calc.service import _normalize_cadkit


def _analyze_and_calculate(service, order_id: str, source_id: str):
    analyzed = service.analyze_drawing(
        order_id, [source_id], None, "test-v1", "steel-test-4"
    )
    fact_id = service.order_get(order_id)["order"]["manual_facts"][0]["fact_id"]
    calculated = service.calculate_quote(
        order_id,
        analyzed["geometry"]["revision"],
        "test-v1",
        "steel-test-4",
        [fact_id],
    )
    return analyzed, calculated


def test_golden_path_is_schema_valid_and_stages_only_delivery(prepared_order) -> None:
    service, order_id, source_id = prepared_order
    analyzed, calculated = _analyze_and_calculate(service, order_id, source_id)
    assert analyzed["abstain"] is False
    assert calculated["abstain"] is False
    assert calculated["cost"]["material_rub"] == 103.62
    assert calculated["cost"]["operations_rub"] == 24
    assert calculated["cost"]["total_rub"] == 127.62
    rendered = service.render_quote_xlsx(order_id, calculated["calculation_sha256"])
    assert rendered["media_path"].startswith("/opt/data/delivery/order-test-1/")
    assert rendered["bytes"] > 1000
    order = service.order_get(order_id)["order"]
    assert order["status"] == "quoted"
    assert order["artifacts"][0]["delivery"]["sha256"] == rendered["sha256"]
    service._validate_order(order)
    actual_delivery = service.settings.delivery_root / order_id / rendered["media_path"].rsplit("/", 1)[1]
    workbook = load_workbook(actual_delivery, data_only=False)
    assert workbook["КП"]["A1"].value == "Коммерческое предложение"


def test_vat_does_not_inflate_margin(prepared_order) -> None:
    service, order_id, source_id = prepared_order
    _, calculated = _analyze_and_calculate(service, order_id, source_id)
    gross = calculated["price"]["total_rub"]
    cost = calculated["cost"]["total_rub"]
    margin = calculated["margin"]["absolute_rub"]
    assert (
        Decimal(str(calculated["price"]["net_total_rub"]))
        + Decimal(str(calculated["price"]["vat_amount_rub"]))
        == Decimal(str(gross))
    )
    assert gross > cost
    assert margin == pytest.approx(calculated["price"]["net_total_rub"] - cost, abs=0.001)
    assert margin < gross - cost


def test_quantity_fact_is_append_only_and_scales_all_quantities(prepared_order) -> None:
    service, order_id, source_id = prepared_order
    state = service.order_get(order_id)
    fact = state["order"]["manual_facts"][0]
    assert fact["code"] == f"part_quantity:{source_id}"
    assert fact["value"] == 3
    _, calculated = _analyze_and_calculate(service, order_id, source_id)
    lines = {line["code"]: line for line in calculated["cost"]["lines"]}
    assert lines["cut"]["quantity"] == pytest.approx(1.2)
    assert lines["pierce"]["quantity"] == 12
    assert lines["cut"]["quantity_source"]["kind"] == "manual_override"
    assert f"metric=geometry:" in lines["cut"]["quantity_source"]["ref"]
    persisted = service.order_get(order_id)["order"]
    assert persisted["geometry"]["parts"][0]["quantity"] == 1
    assert persisted["mass"]["computed_kg"] == pytest.approx(0.314)
    snapshot = persisted["calculation"]["effective_quantities"][0]
    assert snapshot == {
        "part_ref": source_id,
        "quantity": 3,
        "fact_id": fact["fact_id"],
    }
    assert persisted["calculation"]["effective_mass_kg"] == pytest.approx(0.942)
    assert persisted["calculation"]["applied_manual_fact_ids"] == [fact["fact_id"]]
    assert lines[f"material:steel-test-4"]["quantity"] == pytest.approx(1.0362)
    with pytest.raises(InvalidState):
        service.order_upsert(
            order_id,
            service.order_get(order_id)["revision"],
            {"manual_facts": []},
        )


@pytest.mark.parametrize("field,value", [("value", 1.5), ("unit", "kg")])
def test_quantity_fact_requires_integer_pieces(service, field, value) -> None:
    order_id = "order-bad-fact"
    service.order_upsert(order_id, 0, {"customer": {"name": "Test"}})
    cache_name = "doc_aaaaaaaaaaaa_part.dxf"
    (service.settings.cache_root / cache_name).write_bytes(b"0\nSECTION\n0\nEOF\n")
    source = service.ingest_attachment(order_id, cache_name)
    fact = {
        "code": "part_quantity",
        "target_source_file_id": source["source_file_id"],
        "value": 2,
        "unit": "pcs",
        "evidence_ref": "telegram:test",
    }
    fact[field] = value
    with pytest.raises(InvalidState):
        service.order_upsert(
            order_id,
            service.order_get(order_id)["revision"],
            {"manual_facts_propose": [fact]},
        )


def test_model_cannot_self_approve_and_pending_blocks_calculation(service) -> None:
    order_id = "order-pending"
    service.order_upsert(order_id, 0, {"customer": {"name": "Test"}})
    cache_name = "doc_bbbbbbbbbbbb_part.dxf"
    (service.settings.cache_root / cache_name).write_bytes(b"0\nSECTION\n0\nEOF\n")
    source = service.ingest_attachment(order_id, cache_name)
    revision = service.order_get(order_id)["revision"]
    with pytest.raises(InvalidState):
        service.order_upsert(
            order_id,
            revision,
            {
                "manual_facts_propose": [
                    {
                        "code": "part_quantity",
                        "target_source_file_id": source["source_file_id"],
                        "value": 2,
                        "unit": "pcs",
                        "evidence_ref": "telegram:test",
                        "approved_by": "model",
                    }
                ]
            },
        )
    proposed = service.order_upsert(
        order_id,
        revision,
        {
            "manual_facts_propose": [
                {
                    "code": "part_quantity",
                    "target_source_file_id": source["source_file_id"],
                    "value": 2,
                    "unit": "pcs",
                    "evidence_ref": "telegram:test",
                }
            ]
        },
    )
    fact_id = proposed["fact_proposals"][0]["fact_id"]
    assert service.order_get(order_id)["fact_proposals"][0]["status"] == "pending"
    analyzed = service.analyze_drawing(
        order_id, [source["source_file_id"]], None, "test-v1", "steel-test-4"
    )
    pending = service.calculate_quote(
        order_id,
        analyzed["geometry"]["revision"],
        "test-v1",
        "steel-test-4",
        [fact_id],
    )
    assert pending["abstain"] is True
    assert pending["warnings"][0]["code"] == "missing_quantity"
    approved = service.approve_fact(order_id, fact_id, "operator:test")
    assert approved["fact"]["approved_by"] == "operator:test"
    calculated = service.calculate_quote(
        order_id,
        analyzed["geometry"]["revision"],
        "test-v1",
        "steel-test-4",
        [fact_id],
    )
    assert calculated["abstain"] is False
    pierce = next(line for line in calculated["cost"]["lines"] if line["code"] == "pierce")
    assert pierce["quantity"] == 8


def test_same_quantity_evidence_in_two_orders_has_distinct_fact_ids(service) -> None:
    cache_name = "doc_cccccccccccc_part.dxf"
    (service.settings.cache_root / cache_name).write_bytes(b"0\nSECTION\n0\nEOF\n")
    fact_ids = []
    for order_id in ("order-collision-a", "order-collision-b"):
        service.order_upsert(order_id, 0, {"customer": {"name": "Test"}})
        source = service.ingest_attachment(order_id, cache_name)
        proposed = service.order_upsert(
            order_id,
            service.order_get(order_id)["revision"],
            {
                "manual_facts_propose": [
                    {
                        "code": "part_quantity",
                        "target_source_file_id": source["source_file_id"],
                        "value": 2,
                        "unit": "pcs",
                        "evidence_ref": "telegram:same-evidence",
                    }
                ]
            },
        )
        fact_ids.append(proposed["fact_proposals"][0]["fact_id"])
    assert fact_ids[0] != fact_ids[1]


def test_unselected_manual_fact_abstains_and_cannot_relabel_quantity(prepared_order) -> None:
    service, order_id, source_id = prepared_order
    analyzed = service.analyze_drawing(order_id, [source_id], None, "test-v1", "steel-test-4")
    calculated = service.calculate_quote(
        order_id, analyzed["geometry"]["revision"], "test-v1", "steel-test-4", []
    )
    assert calculated["abstain"] is True
    assert calculated["warnings"][0]["code"] == "missing_quantity"
    persisted = service.order_get(order_id)["order"]
    assert "calculation" not in persisted
    assert persisted["geometry"]["parts"][0]["quantity"] == 1


def test_corrected_quantity_supersedes_old_fact(prepared_order) -> None:
    service, order_id, source_id = prepared_order
    old_fact_id = service.order_get(order_id)["order"]["manual_facts"][0]["fact_id"]
    proposed = service.order_upsert(
        order_id,
        service.order_get(order_id)["revision"],
        {
            "manual_facts_propose": [
                {
                    "code": "part_quantity",
                    "target_source_file_id": source_id,
                    "value": 5,
                    "unit": "pcs",
                    "evidence_ref": "telegram:corrected-quantity",
                }
            ]
        },
    )
    new_fact_id = proposed["fact_proposals"][0]["fact_id"]
    service.approve_fact(order_id, new_fact_id, "operator:test")
    current = service.order_get(order_id)
    statuses = {item["fact_id"]: item["status"] for item in current["fact_proposals"]}
    assert statuses[old_fact_id] == "superseded"
    assert statuses[new_fact_id] == "approved"
    assert [item["fact_id"] for item in current["order"]["manual_facts"]] == [new_fact_id]
    analyzed = service.analyze_drawing(
        order_id, [source_id], None, "test-v1", "steel-test-4"
    )
    old_attempt = service.calculate_quote(
        order_id,
        analyzed["geometry"]["revision"],
        "test-v1",
        "steel-test-4",
        [old_fact_id],
    )
    assert old_attempt["abstain"] is True
    corrected = service.calculate_quote(
        order_id,
        analyzed["geometry"]["revision"],
        "test-v1",
        "steel-test-4",
        [new_fact_id],
    )
    assert corrected["calculation"]["effective_quantities"][0]["quantity"] == 5


def test_rate_revision_must_match_geometry(prepared_order) -> None:
    service, order_id, source_id = prepared_order
    analyzed = service.analyze_drawing(order_id, [source_id], None, "test-v1", "steel-test-4")
    with pytest.raises(Conflict):
        service.calculate_quote(
            order_id,
            analyzed["geometry"]["revision"],
            "test-v2",
            "steel-test-4",
            [],
        )


def test_rate_pack_digest_must_match_geometry(prepared_order) -> None:
    from conftest import rate_pack

    service, order_id, source_id = prepared_order
    analyzed = service.analyze_drawing(order_id, [source_id], None, "test-v1", "steel-test-4")
    changed = rate_pack()
    changed["operations"][0]["rate_rub"] = "11"
    (service.settings.rates_root / "test-v1.json").write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(Conflict):
        service.calculate_quote(
            order_id,
            analyzed["geometry"]["revision"],
            "test-v1",
            "steel-test-4",
            [],
        )


def test_reanalysis_with_new_rate_pack_has_new_geometry_revision(prepared_order) -> None:
    service, order_id, source_id = prepared_order
    first = service.analyze_drawing(order_id, [source_id], None, "test-v1", "steel-test-4")
    second = service.analyze_drawing(order_id, [source_id], None, "test-v2", "steel-test-4")
    assert first["geometry"]["revision"] != second["geometry"]["revision"]
    state = service.order_get(order_id)["order"]
    assert state["geometry"]["revision"] == second["geometry"]["revision"]
    assert state["provenance"]["rates_revision"] == "test-v2"


def test_validation_failure_rolls_back_revision_and_state(prepared_order, monkeypatch) -> None:
    service, order_id, _ = prepared_order
    before = service.order_get(order_id)

    def reject(_state):
        raise InvalidState("forced validation failure")

    monkeypatch.setattr(service, "_validate_if_complete", reject)
    with pytest.raises(InvalidState, match="forced validation"):
        service.order_upsert(
            order_id,
            before["revision"],
            {"customer": before["order"]["customer"]},
        )
    after = service.order_get(order_id)
    assert after["revision"] == before["revision"]
    assert after["order"] == before["order"]


def test_recalculate_is_idempotent_only_for_exact_fact_snapshot(prepared_order) -> None:
    service, order_id, source_id = prepared_order
    analyzed, first = _analyze_and_calculate(service, order_id, source_id)
    fact_id = service.order_get(order_id)["order"]["manual_facts"][0]["fact_id"]
    second = service.calculate_quote(
        order_id,
        analyzed["geometry"]["revision"],
        "test-v1",
        "steel-test-4",
        [fact_id],
    )
    assert second["idempotent"] is True
    assert second["calculation_sha256"] == first["calculation_sha256"]
    with pytest.raises(Conflict):
        service.calculate_quote(
            order_id,
            analyzed["geometry"]["revision"],
            "test-v1",
            "steel-test-4",
            [],
        )


def test_server_managed_fields_and_stale_revision_are_rejected(service) -> None:
    created = service.order_upsert("order-lock", 0, {"customer": {"name": "Test"}})
    with pytest.raises(InvalidState):
        service.order_upsert("order-lock", created["revision"], {"price": {"total_rub": 1}})
    service.order_upsert("order-lock", created["revision"], {"customer": {"name": "New"}})
    with pytest.raises(Conflict):
        service.order_upsert("order-lock", created["revision"], {"customer": {"name": "Stale"}})


def test_customer_is_frozen_after_first_attachment(service) -> None:
    order_id = "order-customer-freeze"
    service.order_upsert(order_id, 0, {"customer": {"name": "Original"}})
    cache_name = "doc_dddddddddddd_part.dxf"
    (service.settings.cache_root / cache_name).write_bytes(b"0\nSECTION\n0\nEOF\n")
    service.ingest_attachment(order_id, cache_name)
    before = service.order_get(order_id)
    with pytest.raises(InvalidState, match="immutable"):
        service.order_upsert(
            order_id,
            before["revision"],
            {"customer": {"name": "Replacement"}},
        )
    after = service.order_get(order_id)
    assert after["revision"] == before["revision"]
    assert after["order"]["customer"] == {"name": "Original"}


def test_ingest_adopts_file_published_before_registry_commit(service, monkeypatch) -> None:
    order_id = "order-ingest-recovery"
    service.order_upsert(order_id, 0, {"customer": {"name": "Test"}})
    cache_name = "doc_eeeeeeeeeeee_part.dxf"
    payload = b"0\nSECTION\n0\nEOF\n"
    (service.settings.cache_root / cache_name).write_bytes(payload)
    original_mutate = service.registry.mutate

    def crash_once(*args, **kwargs):
        raise RuntimeError("simulated crash before registry commit")

    monkeypatch.setattr(service.registry, "mutate", crash_once)
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.ingest_attachment(order_id, cache_name)
    assert service.order_get(order_id)["order"]["source_files"] == []
    monkeypatch.setattr(service.registry, "mutate", original_mutate)
    recovered = service.ingest_attachment(order_id, cache_name)
    assert recovered["sha256"]
    assert service.order_get(order_id)["order"]["source_files"][0]["sha256"] == recovered["sha256"]


def test_stats_use_typed_registry(prepared_order) -> None:
    service, order_id, source_id = prepared_order
    _, calculated = _analyze_and_calculate(service, order_id, source_id)
    service.render_quote_xlsx(order_id, calculated["calculation_sha256"])
    today = datetime.now(UTC).date().isoformat()
    result = service.order_stats(today, today, "status")
    assert result["counts"][0]["key"] == "quoted"
    assert result["quoted_rub"] == calculated["price"]["total_rub"]


def test_live_delivery_is_idempotent_and_corruption_is_restaged(prepared_order) -> None:
    service, order_id, source_id = prepared_order
    _, calculated = _analyze_and_calculate(service, order_id, source_id)
    first = service.render_quote_xlsx(order_id, calculated["calculation_sha256"])
    second = service.render_quote_xlsx(order_id, calculated["calculation_sha256"])
    assert second["idempotent"] is True
    delivery_name = first["media_path"].rsplit("/", 1)[1]
    (service.settings.delivery_root / order_id / delivery_name).write_bytes(b"corrupt")
    third = service.render_quote_xlsx(order_id, calculated["calculation_sha256"])
    assert third["restaged"] is True
    assert third["media_path"] != first["media_path"]
    assert third["sha256"] == first["sha256"]


def test_render_adopts_artifact_published_before_registry_commit(prepared_order, monkeypatch) -> None:
    service, order_id, source_id = prepared_order
    _, calculated = _analyze_and_calculate(service, order_id, source_id)
    order_before = service.order_get(order_id)

    class AdvancingDateTime:
        calls = 0

        @classmethod
        def now(cls, tz=None):
            cls.calls += 1
            value = datetime(2030, 1, 1, tzinfo=timezone.utc) + timedelta(days=cls.calls)
            return value if tz is not None else value.replace(tzinfo=None)

    import openpyxl.writer.excel as openpyxl_excel

    monkeypatch.setattr(
        openpyxl_excel,
        "datetime",
        SimpleNamespace(datetime=AdvancingDateTime, timezone=timezone),
    )
    first_payload, _ = render_quote(order_before["order"])
    second_payload, _ = render_quote(order_before["order"])
    assert first_payload == second_payload
    calculated_at = datetime.fromisoformat(
        order_before["order"]["calculation"]["calculated_at"].replace("Z", "+00:00")
    ).astimezone(UTC).replace(tzinfo=None, microsecond=0)
    expected_timestamp = calculated_at.isoformat().encode("ascii") + b"Z"
    with ZipFile(BytesIO(first_payload), "r") as archive:
        core_properties = archive.read("docProps/core.xml")
    assert core_properties.count(expected_timestamp) == 2
    original_mutate = service.registry.mutate

    def crash_once(*args, **kwargs):
        raise RuntimeError("simulated crash before artifact commit")

    monkeypatch.setattr(service.registry, "mutate", crash_once)
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.render_quote_xlsx(order_id, calculated["calculation_sha256"])
    assert "artifacts" not in service.order_get(order_id)["order"]
    monkeypatch.setattr(service.registry, "mutate", original_mutate)
    recovered = service.render_quote_xlsx(order_id, calculated["calculation_sha256"])
    assert recovered["sha256"]
    assert service.order_get(order_id)["order"]["artifacts"][0]["sha256"] == recovered["sha256"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda pack: pack["pricing"].update(vat_included="false"),
        lambda pack: pack["pricing"].update(vat_rate_pct="101"),
        lambda pack: pack["pricing"].update(vat_included=False, vat_rate_pct="20"),
        lambda pack: pack["pricing"].update(margin_basis="on_price", margin_percent="100"),
        lambda pack: pack["materials"]["steel-test-4"].update(rate_rub_per_kg="0"),
        lambda pack: pack["operations"][0].update(rate_rub="0"),
        lambda pack: pack["operations"][0].update(category="arbitrary"),
        lambda pack: pack["operations"][0]["rate_source"].update(as_of="13.08.2026"),
        lambda pack: pack["operations"][0]["rate_source"].update(as_of="2026-W33-4"),
        lambda pack: pack["operations"][0]["rate_source"].update(ref=""),
        lambda pack: pack["operations"][0]["rate_source"].update(extra="x"),
    ],
)
def test_rate_pack_rejects_ambiguous_or_unsafe_policy(tmp_path, mutation) -> None:
    from conftest import rate_pack

    rates = tmp_path / "rates"
    rates.mkdir()
    pack = rate_pack()
    mutation(pack)
    (rates / "test-v1.json").write_text(json.dumps(pack), encoding="utf-8")
    root = SecureRoot(rates, writable=False)
    try:
        with pytest.raises(InvalidRatePack):
            RatePackStore(root).load("test-v1")
    finally:
        root.close()


def test_formula_injection_is_escaped(service) -> None:
    assert _safe_cell("=HYPERLINK()") == "'=HYPERLINK()"


@pytest.mark.parametrize(
    "warning",
    [
        "вложенность блоков > 4 — часть геометрии пропущена",
        "3 сущностей блока не перенеслись — проверить деталь глазами",
        "отброшено 5 тонких линий по типу линии",
        "unsupported entity skipped",
    ],
)
def test_cadkit_data_loss_warning_is_stop(warning) -> None:
    source = {"source_file_id": "src_" + "a" * 24}
    raw = {
        "ok": True,
        "area_mm2": 100,
        "outer_area_mm2": 100,
        "cut_length_mm": 40,
        "bbox": [10, 10],
        "holes": 0,
        "pierces": 1,
        "contours": 1,
        "mass_kg": 1,
        "warnings": [warning],
    }
    _, warnings, _ = _normalize_cadkit(source, raw)
    assert warnings == [
        {
            "code": "geometry_unverified",
            "severity": "stop",
            "message": "Geometry requires human review",
            "part_ref": source["source_file_id"],
        }
    ]


def test_missing_units_is_always_stop_and_units_hint_is_rejected(service) -> None:
    source = {"source_file_id": "src_" + "b" * 24}
    raw = {
        "ok": True,
        "area_mm2": 100,
        "outer_area_mm2": 100,
        "cut_length_mm": 40,
        "bbox": [10, 10],
        "holes": 0,
        "pierces": 1,
        "contours": 1,
        "mass_kg": 1,
        "warnings": ["$INSUNITS не задан — единицы приняты как мм, СВЕРИТЬ со штампом"],
    }
    _, warnings, _ = _normalize_cadkit(source, raw)
    assert warnings[0]["code"] == "unit_ambiguous"
    assert warnings[0]["severity"] == "stop"
    with pytest.raises(InvalidState, match="not trusted"):
        service.analyze_drawing("unknown", [], "mm", "test-v1", "steel-test-4")
