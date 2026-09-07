"""Конвейер V3: сквозной путь схемы V2, роли, один возврат, QA-гейты."""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from metal_calc.errors import Conflict, InvalidRatePack, InvalidState
from metal_calc.calc_qa import mechanical_checks
from metal_calc.packs2 import ACTIVE_POINTER, PipelinePackStore
from metal_calc.registry import Registry
from metal_calc.securefs import SecureRoot
from metal_calc.service3 import WorkflowService
from metal_calc.util import digest_json, sha256_bytes

from test_pack_v3 import MONEY, pack_v3, pack_v4


def workflow_pack(revision: str = "v3-test") -> dict[str, Any]:
    data = pack_v3(revision)
    data["process_park"]["FINISH.FITTER"] = {"rate_id": "blank:bench"}
    return data


@pytest.fixture
def packs(tmp_path: Path) -> PipelinePackStore:
    rates = tmp_path / "rates3"
    rates.mkdir()
    payload = json.dumps(workflow_pack(), ensure_ascii=False, sort_keys=True).encode("utf-8")
    (rates / "v3-test.json").write_bytes(payload)
    (rates / ACTIVE_POINTER).write_text(
        json.dumps({"revision": "v3-test", "sha256": sha256_bytes(payload)}),
        encoding="utf-8",
    )
    return PipelinePackStore(SecureRoot(rates, writable=False))


@pytest.fixture
def svc(tmp_path: Path, packs: PipelinePackStore) -> WorkflowService:
    registry = Registry(tmp_path / "orders3" / "registry.db")
    return WorkflowService(registry, packs)


DEFAULT_SOURCE = {
    "source_file_id": f"src_{'a' * 24}",
    "name": "чертёж вал-042.pdf",
    "sha256": "a" * 64,
    "format": "pdf",
    "bytes": 100,
    "received_at": "2026-09-04T00:00:00Z",
}
SECOND_SOURCE = {
    "source_file_id": f"src_{'b' * 24}",
    "name": "техническое задание.pdf",
    "sha256": "b" * 64,
    "format": "pdf",
    "bytes": 200,
    "received_at": "2026-09-04T00:01:00Z",
}


def source_manifest(*sources: dict[str, Any]) -> list[dict[str, str]]:
    selected = sources or (DEFAULT_SOURCE,)
    return [
        {
            "source_file_id": source["source_file_id"],
            "name": source["name"],
            "sha256": source["sha256"],
        }
        for source in selected
    ]


def new_order(
    svc: WorkflowService,
    order_id: str = "ord-v3-1",
    *,
    sources: list[dict[str, Any]] | None = None,
    customer: dict[str, str] | None = None,
) -> int:
    revision, _ = svc.registry.create(
        order_id,
        {
            "order_id": order_id,
            "customer": customer or {"name": "Synthetic"},
            "source_files": copy.deepcopy([DEFAULT_SOURCE] if sources is None else sources),
            "status": "draft",
            "warnings": [],
            "timestamps": {},
            "provenance": {"created_by": "test"},
        },
    )
    return revision


ROUTE_STEPS = [
    {"seq": 1, "process_code": "BLANK.CUTOFF", "execution_mode": "in_house", "note": "распил прутка"},
    {"seq": 2, "process_code": "MACHINING.TURN.CNC", "execution_mode": "in_house", "note": "точение"},
    {"seq": 3, "process_code": "FINISH.FITTER", "execution_mode": "in_house", "note": "слесарка"},
    {
        "seq": 4,
        "process_code": "FINISH.POWDER_COAT",
        "execution_mode": "outsource",
        "note": "порошок у подрядчика",
    },
]


def drive_to_frozen(
    svc: WorkflowService,
    order_id: str = "ord-v3-1",
    *,
    route_steps: list[dict[str, Any]] | None = None,
    customer: dict[str, str] | None = None,
    kd_revision: str = "КД-42 rev.B",
) -> int:
    revision = new_order(svc, order_id, customer=customer)
    revision = svc.input_freeze(
        order_id,
        revision,
        10,
        kd_revision,
        source_manifest(),
        "вход зафиксирован",
        actor_role="front",
    )["revision"]
    revision = svc.bom_upsert(
        order_id,
        revision,
        [
            {
                "bom_node_id": "part-1",
                "parent_bom_id": None,
                "node_type": "MANUFACTURED_PART",
                "make_or_buy": "MAKE",
                "quantity": 1,
                "note": "вал",
            },
        ],
        actor_role="tech",
    )["revision"]
    proposed = svc.route_variants_propose(
        order_id, revision, route_steps or ROUTE_STEPS, actor_role="tech"
    )
    revision = proposed["revision"]
    frozen = svc.route_freeze(order_id, revision, proposed["variant_id"], actor_role="tech")
    return frozen["revision"]


def drive_to_book(
    svc: WorkflowService,
    order_id: str = "ord-v3-1",
    *,
    route_steps: list[dict[str, Any]] | None = None,
    customer: dict[str, str] | None = None,
    kd_revision: str = "КД-42 rev.B",
    book_actor_role: str = "front",
    include_contractor_quotes: bool = True,
) -> int:
    revision = drive_to_frozen(
        svc,
        order_id,
        route_steps=route_steps,
        customer=customer,
        kd_revision=kd_revision,
    )
    revision = svc.blank_drivers_set(
        order_id,
        revision,
        "steel-09g2s",
        "2.5",
        "per_piece",
        "по чертежу, плотность 7850",
        [{"route_seq": 1, "cuts": 10, "note": "один рез на деталь"}],
        actor_role="supply",
    )["revision"]
    revision = svc.time_norms_set(
        order_id,
        revision,
        [
            {
                "route_seq": 2,
                "batch": 5,
                "params": {"diameter_mm": "60", "length_mm": "120", "stock_mm": "3"},
            }
        ],
        [{"route_seq": 3, "hours": "0.5", "quantity_basis": "order_total", "note": "снятие заусенцев"}],
        actor_role="norm",
    )["revision"]
    if include_contractor_quotes:
        for step in route_steps or ROUTE_STEPS:
            if step["execution_mode"] != "outsource":
                continue
            revision = svc.contractor_quote_set(
                order_id,
                revision,
                step["seq"],
                "120",
                "per_piece",
                True,
                "2026-09-04",
                "2099-09-04",
                "ООО Подряд",
                f"КП-{step['seq']}",
                "panel-test",
                actor_role="panel",
            )["revision"]
    return svc.book_assemble(order_id, revision, actor_role=book_actor_role)["revision"]


def complete_manual_review(
    svc: WorkflowService,
    revision: int,
    order_id: str = "ord-v3-1",
    *,
    reviewed_by: str = "Методолог",
) -> int:
    _, state = svc.registry.get(order_id)
    book = state["book"]
    items = book["manual_review_required"]
    if not items:
        return revision
    reviews = [
        {
            "item_index": index,
            "item_digest": digest_json(item),
            "evidence": f"Сверено по техкарте, пункт {index + 1}",
            "reference": f"ТК-{index + 1}",
        }
        for index, item in enumerate(items)
    ]
    return svc.manual_review_complete(
        order_id,
        revision,
        book["digest"],
        reviews,
        reviewed_by,
        actor_role="qa",
    )["revision"]


# ── v9: типизированная фиксация входа ──────────────────────────────────


def test_input_freeze_binds_exact_attachments_and_records_digest(
    svc: WorkflowService,
) -> None:
    customer = {"name": "Synthetic", "external_ref": "test-ref"}
    revision = new_order(
        svc,
        sources=[DEFAULT_SOURCE, SECOND_SOURCE],
        customer=customer,
    )
    result = svc.input_freeze(
        "ord-v3-1",
        revision,
        10,
        "КД-42 rev.B",
        source_manifest(SECOND_SOURCE, DEFAULT_SOURCE),
        "вход зафиксирован",
        actor_role="front",
    )
    workflow = result["workflow"]
    canonical_manifest = source_manifest(DEFAULT_SOURCE, SECOND_SOURCE)
    assert workflow["source_manifest"] == canonical_manifest
    assert workflow["quantity"] == 10
    assert workflow["kd_revision"] == "КД-42 rev.B"
    assert workflow["calculator_version"] == "v9"
    assert workflow["input_contract_version"] == 1
    assert workflow["input_order_revision"] == revision
    assert workflow["input_origin"] == "order_registry.source_files"
    assert workflow["input_digest"] == digest_json(
        {
            "contract_version": 1,
            "order_id": "ord-v3-1",
            "customer": customer,
            "quantity": 10,
            "kd_revision": "КД-42 rev.B",
            "source_manifest": canonical_manifest,
        }
    )


def test_input_freeze_rejects_empty_actual_attachments(svc: WorkflowService) -> None:
    revision = new_order(svc, sources=[])
    with pytest.raises(InvalidState, match="нет загруженных вложений"):
        svc.input_freeze(
            "ord-v3-1", revision, 10, "КД", source_manifest(), "вход", actor_role="front"
        )
    current, state = svc.registry.get("ord-v3-1")
    assert current == revision
    assert "workflow" not in state


def test_input_freeze_rejects_phantom_name(svc: WorkflowService) -> None:
    revision = new_order(svc)
    manifest = source_manifest()
    manifest[0]["name"] = "подменённое имя.pdf"
    with pytest.raises(InvalidState, match="имя.*не совпадает"):
        svc.input_freeze(
            "ord-v3-1", revision, 10, "КД", manifest, "вход", actor_role="front"
        )
    assert svc.registry.get("ord-v3-1")[0] == revision


def test_input_freeze_rejects_hash_id_cross_match(svc: WorkflowService) -> None:
    revision = new_order(svc, sources=[DEFAULT_SOURCE, SECOND_SOURCE])
    manifest = source_manifest(DEFAULT_SOURCE, SECOND_SOURCE)
    manifest[0]["sha256"], manifest[1]["sha256"] = (
        manifest[1]["sha256"],
        manifest[0]["sha256"],
    )
    with pytest.raises(InvalidState, match="source_file_id.*sha256"):
        svc.input_freeze(
            "ord-v3-1", revision, 10, "КД", manifest, "вход", actor_role="front"
        )
    assert svc.registry.get("ord-v3-1")[0] == revision


def test_input_freeze_rejects_duplicate_manifest_entry(svc: WorkflowService) -> None:
    revision = new_order(svc)
    duplicate = source_manifest()[0]
    with pytest.raises(InvalidState, match="source_file_id .* задан дважды"):
        svc.input_freeze(
            "ord-v3-1",
            revision,
            10,
            "КД",
            [duplicate, copy.deepcopy(duplicate)],
            "вход",
            actor_role="front",
        )
    assert svc.registry.get("ord-v3-1")[0] == revision


def test_input_freeze_replay_is_explicit_conflict(svc: WorkflowService) -> None:
    revision = new_order(svc)
    first = svc.input_freeze(
        "ord-v3-1", revision, 10, "КД", source_manifest(), "вход", actor_role="front"
    )
    with pytest.raises(Conflict, match="повторная фиксация запрещена"):
        svc.input_freeze(
            "ord-v3-1",
            first["revision"],
            10,
            "КД",
            source_manifest(),
            "повтор",
            actor_role="front",
        )
    assert svc.registry.get("ord-v3-1")[0] == first["revision"]


def test_input_freeze_rejects_stale_order_revision(svc: WorkflowService) -> None:
    revision = new_order(svc)

    def touch(state: dict[str, Any]) -> None:
        state["warnings"].append({"code": "test-touch"})

    current, _ = svc.registry.mutate("ord-v3-1", touch, expected_revision=revision)
    with pytest.raises(Conflict, match="revision conflict"):
        svc.input_freeze(
            "ord-v3-1", revision, 10, "КД", source_manifest(), "вход", actor_role="front"
        )
    saved_revision, state = svc.registry.get("ord-v3-1")
    assert saved_revision == current
    assert "workflow" not in state


def test_input_freeze_rejects_legacy_string_manifest(svc: WorkflowService) -> None:
    revision = new_order(svc)
    with pytest.raises(InvalidState, match="source_file_id, name и sha256"):
        svc.input_freeze(
            "ord-v3-1", revision, 10, "КД", ["чертёж.pdf"], "вход", actor_role="front"
        )


# ── happy path ───────────────────────────────────────────────────────────


def test_full_path_to_ready_for_ld(svc: WorkflowService) -> None:
    revision = drive_to_book(svc)
    result = svc.qa_run_mechanical("ord-v3-1", revision, actor_role="front")
    assert result["verdict"]["verdict"] == "PASS"
    assert result["status"] == "QA_MECHANICAL_PASS"
    revision = result["revision"]
    revision = svc.qa_verdict(
        "ord-v3-1", revision, "technological", "PASS", [], "методолог", actor_role="qa"
    )["revision"]
    revision = complete_manual_review(svc, revision)
    final = svc.qa_verdict(
        "ord-v3-1", revision, "commercial", "PASS", [], "методолог", actor_role="qa"
    )
    assert final["status"] == "READY_FOR_LD"
    status = svc.workflow_status("ord-v3-1")
    assert status["workflow"]["status"] == "READY_FOR_LD"
    assert status["workflow"]["calculation_revision"] == 1
    assert status["book"]["route_unpriced"] == []
    assert status["contractor_quotes"][0]["process_code"] == "FINISH.POWDER_COAT"
    # Число детерминировано до человеческого решения; окончательным для
    # клиента оно становится только через workflow + QA + manual receipt.
    assert status["book"]["price"]["is_final"] is True
    assert "status_note" not in status["book"]["price"]


def test_book_math_matches_stage_totals(svc: WorkflowService) -> None:
    drive_to_book(svc)
    _, state = svc.registry.get("ord-v3-1")
    blank_total = Decimal(str(state["costing"]["blank"]["total_rub"]))
    time_total = Decimal(str(state["costing"]["time"]["total_rub"]))
    contractor_total = Decimal(str(state["book"]["cost"]["contractor_rub"]))
    cost_total = Decimal(str(state["book"]["cost"]["total_rub"]))
    assert contractor_total == Decimal("1000.00")
    assert cost_total == blank_total + time_total + contractor_total
    price = state["book"]["price"]
    assert Decimal(str(price["net_total_rub"])) + Decimal(str(price["vat_amount_rub"])) == Decimal(
        str(price["total_rub"])
    )
    # Материал stocked → без звёздочки; слесарка оплачена по blank:bench 900 ₽/ч.
    assert state["book"]["provisional"] is False
    service_line = state["costing"]["time"]["service_lines"][0]
    assert service_line["rate_id"] == "blank:bench"
    assert Decimal(str(service_line["subtotal_rub"])) == Decimal("450.00")


def test_contractor_quote_is_panel_only_and_normalizes_to_pack_basis(
    svc: WorkflowService,
) -> None:
    revision = drive_to_frozen(svc)
    with pytest.raises(InvalidState, match="роли panel"):
        svc.contractor_quote_set(
            "ord-v3-1",
            revision,
            4,
            "120",
            "per_piece",
            True,
            "2026-09-04",
            "2099-09-04",
            "ООО Подряд",
            "КП-42",
            "agent",
            actor_role="front",
        )
    with pytest.raises(InvalidState, match="outsource-шагом"):
        svc.contractor_quote_set(
            "ord-v3-1",
            revision,
            1,
            "120",
            "per_piece",
            True,
            "2026-09-04",
            "2099-09-04",
            "ООО Подряд",
            "КП-42",
            "panel-user",
            actor_role="panel",
        )
    with pytest.raises(InvalidState, match="valid_until"):
        svc.contractor_quote_set(
            "ord-v3-1",
            revision,
            4,
            "120",
            "per_piece",
            True,
            "2026-09-04",
            "2026-09-03",
            "ООО Подряд",
            "КП-42",
            "panel-user",
            actor_role="panel",
        )
    with pytest.raises(InvalidState, match="будущего"):
        svc.contractor_quote_set(
            "ord-v3-1",
            revision,
            4,
            "120",
            "per_piece",
            True,
            "2999-09-04",
            "2999-10-04",
            "ООО Подряд",
            "КП-42",
            "panel-user",
            actor_role="panel",
        )

    result = svc.contractor_quote_set(
        "ord-v3-1",
        revision,
        4,
        "120",
        "per_piece",
        True,
        "2026-09-04",
        "2099-09-04",
        "ООО Подряд",
        "КП-42",
        "panel-user",
        actor_role="panel",
    )
    quote = result["quote"]
    assert quote["amount_rub"] == 120
    assert quote["order_amount_rub"] == 1200
    assert quote["normalized_amount_rub"] == 1000
    assert quote["normalized_basis"] == "order_total"
    assert quote["normalized_vat_included"] is False
    assert quote["valid_until"] == "2099-09-04"
    assert quote["calculation_revision"] == 1
    assert quote["panel_actor"] == "panel-user"
    assert quote["digest"] == digest_json(
        {key: value for key, value in quote.items() if key != "digest"}
    )
    with pytest.raises(Conflict, match="revision conflict"):
        svc.contractor_quote_set(
            "ord-v3-1",
            revision,
            4,
            "130",
            "order_total",
            False,
            "2026-09-04",
            "2099-09-04",
            "ООО Подряд",
            "КП-43",
            "panel-user",
            actor_role="panel",
        )


def test_missing_contractor_quote_stays_unpriced_and_blocks_final(
    svc: WorkflowService,
) -> None:
    revision = drive_to_book(svc, include_contractor_quotes=False)
    status = svc.workflow_status("ord-v3-1")
    assert status["contractor_quotes"] == []
    assert status["book"]["route_unpriced"] == [
        {
            "seq": 4,
            "process_code": "FINISH.POWDER_COAT",
            "execution_mode": "outsource",
            "note": "порошок у подрядчика",
            "reason": "КП подрядчика не записано",
        }
    ]
    assert status["book"]["price"]["is_final"] is False
    revision = svc.qa_run_mechanical(
        "ord-v3-1", revision, actor_role="front"
    )["revision"]
    revision = svc.qa_verdict(
        "ord-v3-1",
        revision,
        "technological",
        "PASS",
        [],
        "методолог",
        actor_role="qa",
    )["revision"]
    with pytest.raises(InvalidState, match="КП подрядчика отсутствует"):
        svc.qa_verdict(
            "ord-v3-1",
            revision,
            "commercial",
            "PASS",
            [],
            "методолог",
            actor_role="qa",
        )


def test_contractor_quote_update_bumps_calc_revision_and_archives_old_record(
    svc: WorkflowService,
) -> None:
    revision = drive_to_book(svc)
    revision = svc.qa_run_mechanical(
        "ord-v3-1", revision, actor_role="front"
    )["revision"]
    _, before = svc.registry.get("ord-v3-1")
    old_quote = copy.deepcopy(before["costing"]["contractor_quotes"]["4"])

    result = svc.contractor_quote_set(
        "ord-v3-1",
        revision,
        4,
        "1500",
        "order_total",
        False,
        "2026-09-04",
        "2099-09-05",
        "ООО Подряд",
        "КП-43",
        "panel-user",
        actor_role="panel",
    )
    assert result["calculation_revision"] == 2
    assert result["status"] == "COSTING_COMPLETE"
    _, state = svc.registry.get("ord-v3-1")
    assert "book" not in state
    assert str(1) not in (state.get("qa") or {})
    assert state["costing"]["contractor_quote_history"] == [old_quote]
    assert state["workflow_history"][-1]["book"] is not None
    assert state["workflow_history"][-1]["qa"]["mechanical"]["verdict"] == "PASS"
    current = state["costing"]["contractor_quotes"]["4"]
    assert current["calculation_revision"] == 2
    assert current["normalized_amount_rub"] == 1500
    assert current["digest"] != old_quote["digest"]


def test_stale_contractor_quote_is_shown_but_not_added_to_book(
    svc: WorkflowService,
) -> None:
    revision = drive_to_book(svc)
    result = svc.contractor_quote_set(
        "ord-v3-1",
        revision,
        4,
        "1500",
        "order_total",
        False,
        "2026-09-04",
        "2099-09-05",
        "ООО Подряд",
        "КП-43",
        "panel-user",
        actor_role="panel",
    )

    def make_stale(state: dict[str, Any]) -> None:
        quote = state["costing"]["contractor_quotes"]["4"]
        quote["calculation_revision"] = 1
        quote["digest"] = digest_json(
            {key: value for key, value in quote.items() if key != "digest"}
        )

    revision, _ = svc.registry.mutate(
        "ord-v3-1", make_stale, expected_revision=result["revision"]
    )
    svc.book_assemble("ord-v3-1", revision, actor_role="front")
    _, state = svc.registry.get("ord-v3-1")
    book = state["book"]
    assert book["cost"]["contractor_rub"] == 0
    assert book["cost"]["contractor_quotes"][0]["current"] is False
    assert book["cost"]["contractor_quotes"][0]["stale_reasons"] == [
        "calculation_revision"
    ]
    assert "calculation_revision" in book["route_unpriced"][0]["reason"]
    assert book["price"]["is_final"] is False


def test_future_persisted_contractor_quote_is_stale_and_unpriced(
    svc: WorkflowService,
) -> None:
    revision = drive_to_book(svc)
    result = svc.contractor_quote_set(
        "ord-v3-1",
        revision,
        4,
        "1500",
        "order_total",
        False,
        "2026-09-04",
        "2099-09-05",
        "ООО Подряд",
        "КП-43",
        "panel-user",
        actor_role="panel",
    )

    def corrupt_date(state: dict[str, Any]) -> None:
        quote = state["costing"]["contractor_quotes"]["4"]
        quote["quoted_at"] = "2999-09-04"
        quote["valid_until"] = "2999-09-05"
        quote["digest"] = digest_json(
            {key: value for key, value in quote.items() if key != "digest"}
        )

    revision, _ = svc.registry.mutate(
        "ord-v3-1", corrupt_date, expected_revision=result["revision"]
    )
    svc.book_assemble("ord-v3-1", revision, actor_role="front")
    _, state = svc.registry.get("ord-v3-1")
    book = state["book"]
    assert book["cost"]["contractor_rub"] == 0
    assert book["cost"]["contractor_quotes"][0]["current"] is False
    assert book["cost"]["contractor_quotes"][0]["stale_reasons"] == ["quoted_at"]
    assert "quoted_at" in book["route_unpriced"][0]["reason"]
    assert book["price"]["is_final"] is False


def test_contractor_quote_expiry_caps_price_validity_and_blocks_commercial_pass(
    svc: WorkflowService, monkeypatch
) -> None:
    import metal_calc.service3 as service3_module

    monkeypatch.setattr(service3_module, "utcnow", lambda: "2026-09-04T12:00:00Z")
    revision = drive_to_book(svc)
    updated = svc.contractor_quote_set(
        "ord-v3-1",
        revision,
        4,
        "1500",
        "order_total",
        False,
        "2026-09-04",
        "2026-09-05",
        "ООО Подряд",
        "КП-44",
        "panel-user",
        actor_role="panel",
    )
    revision = svc.book_assemble(
        "ord-v3-1", updated["revision"], actor_role="front"
    )["revision"]
    _, state = svc.registry.get("ord-v3-1")
    assert state["book"]["route_unpriced"] == []
    assert state["book"]["price"]["valid_until"] == "2026-09-05"
    revision = svc.qa_run_mechanical(
        "ord-v3-1", revision, actor_role="front"
    )["revision"]
    revision = svc.qa_verdict(
        "ord-v3-1",
        revision,
        "technological",
        "PASS",
        [],
        "методолог",
        actor_role="qa",
    )["revision"]
    monkeypatch.setattr(service3_module, "utcnow", lambda: "2026-09-06T00:00:00Z")
    with pytest.raises(InvalidState, match="истекло"):
        svc.qa_verdict(
            "ord-v3-1",
            revision,
            "commercial",
            "PASS",
            [],
            "методолог",
            actor_role="qa",
        )
    expired = svc.contractor_quote_set(
        "ord-v3-1",
        revision,
        4,
        "1600",
        "order_total",
        False,
        "2026-09-04",
        "2026-09-05",
        "ООО Подряд",
        "КП-45",
        "panel-user",
        actor_role="panel",
    )
    book = svc.book_assemble(
        "ord-v3-1", expired["revision"], actor_role="front"
    )["book"]
    assert book["cost"]["contractor_rub"] == 0
    assert book["cost"]["contractor_quotes"][0]["current"] is False
    assert "valid_until" in book["route_unpriced"][0]["reason"]


def test_v4_material_markup_applies_only_to_material(tmp_path: Path) -> None:
    rates = tmp_path / "rates4"
    rates.mkdir()
    data = pack_v4()
    data["process_park"]["FINISH.FITTER"] = {
        "rate_id": "blank:bench",
        "requires_manual_review": True,
        "note": "Ручная сверка тестовой операции",
    }
    data["pricing"]["rates_include_vat"] = True
    # Этот маршрут использует BLANK.CUTOFF, поэтому дополнительная лазерная
    # ставка в данном тесте не участвует: проверяется только разложение цены.
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    (rates / "v4-test.json").write_bytes(payload)
    (rates / ACTIVE_POINTER).write_text(
        json.dumps({"revision": "v4-test", "sha256": sha256_bytes(payload)}),
        encoding="utf-8",
    )
    service = WorkflowService(
        Registry(tmp_path / "orders4" / "registry.db"),
        PipelinePackStore(SecureRoot(rates, writable=False)),
    )
    revision = drive_to_book(service, "ord-v4-markup")
    _, state = service.registry.get("ord-v4-markup")
    material = Decimal(str(state["costing"]["blank"]["lines"][0]["subtotal_rub"]))
    cost = Decimal(str(state["book"]["cost"]["total_rub"]))
    price = Decimal(str(state["book"]["price"]["total_rub"]))
    assert price == (
        (cost - material) * Decimal("1.20") + material * Decimal("1.15")
    ).quantize(Decimal("0.01"))
    assert state["book"]["cost"]["rates_include_vat"] is True
    assert Decimal(str(state["book"]["cost"]["net_equivalent_rub"])) == (
        cost / Decimal("1.20")
    ).quantize(Decimal("0.01"))
    assert state["book"]["margin"]["material_markup_percent"] == 15
    assert state["book"]["component_markups"]["material"] == {
        "base_rub": 2050,
        "percent": 15,
        "amount_rub": 307.5,
        "sell_rub": 2357.5,
    }
    assert {
        (item["seq"], item["process_code"], item["reason"])
        for item in state["book"]["manual_review_required"]
    } == {
        (3, "FINISH.FITTER", "Ручная сверка тестовой операции"),
        (2, "MACHINING.TURN.CNC", "Норма времени или накладные параметры помечены TEMPLATE"),
    }
    assert state["book"]["price"]["is_final"] is True
    tampered = copy.deepcopy(state)
    tampered["book"]["manual_review_required"] = []
    tampered["book"]["digest"] = digest_json(
        {k: v for k, v in tampered["book"].items() if k != "digest"}
    )
    manual_check = {
        check["code"]: check
        for check in mechanical_checks(tampered, service.packs.load_active())
    }
    assert manual_check["commercial_completeness"]["ok"] is False
    checks = service.qa_run_mechanical(
        "ord-v4-markup", revision, actor_role="front"
    )
    assert checks["verdict"]["verdict"] == "PASS"
    revision = checks["revision"]
    revision = service.qa_verdict(
        "ord-v4-markup", revision, "technological", "PASS", [], "методолог", actor_role="qa"
    )["revision"]
    with pytest.raises(InvalidState, match="QA-квитанцией"):
        service.qa_verdict(
            "ord-v4-markup", revision, "commercial", "PASS", [], "методолог", actor_role="qa"
        )
    with pytest.raises(InvalidState, match="роли qa"):
        service.manual_review_complete(
            "ord-v4-markup",
            revision,
            state["book"]["digest"],
            [],
            "Методолог",
            actor_role="front",
        )

    revision = complete_manual_review(service, revision, "ord-v4-markup")
    _, reviewed = service.registry.get("ord-v4-markup")
    receipt = reviewed["qa"]["1"]["manual_review_receipts"][-1]
    assert receipt["book_digest"] == reviewed["book"]["digest"]
    assert receipt["manual_items_digest"] == digest_json(
        reviewed["book"]["manual_review_required"]
    )
    assert receipt["calculation_revision"] == 1
    assert receipt["pack_revision"] == "v4-test"
    assert receipt["actor_role"] == "qa"
    assert receipt["reviewed_by"] == "Методолог"
    assert receipt["digest"] == digest_json(
        {key: value for key, value in receipt.items() if key != "digest"}
    )
    event = reviewed["workflow_events"][-1]
    assert event["event"] == "manual_review_completed"
    assert event["receipt_digest"] == receipt["digest"]
    final = service.qa_verdict(
        "ord-v4-markup",
        revision,
        "commercial",
        "PASS",
        [],
        "методолог",
        actor_role="qa",
    )
    assert final["status"] == "READY_FOR_LD"


def test_manual_review_receipt_fails_closed_after_book_digest_changes(
    tmp_path: Path,
) -> None:
    rates = tmp_path / "rates4-stale-review"
    rates.mkdir()
    data = pack_v4()
    data["process_park"]["FINISH.FITTER"] = {
        "rate_id": "blank:bench",
        "requires_manual_review": True,
        "note": "Ручная сверка тестовой операции",
    }
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    (rates / "v4-test.json").write_bytes(payload)
    (rates / ACTIVE_POINTER).write_text(
        json.dumps({"revision": "v4-test", "sha256": sha256_bytes(payload)}),
        encoding="utf-8",
    )
    service = WorkflowService(
        Registry(tmp_path / "orders4-stale-review" / "registry.db"),
        PipelinePackStore(SecureRoot(rates, writable=False)),
    )
    revision = drive_to_book(service, "ord-v4-review")
    revision = service.qa_run_mechanical(
        "ord-v4-review", revision, actor_role="front"
    )["revision"]
    revision = service.qa_verdict(
        "ord-v4-review",
        revision,
        "technological",
        "PASS",
        [],
        "методолог",
        actor_role="qa",
    )["revision"]
    revision = complete_manual_review(service, revision, "ord-v4-review")

    def rebuild_book(state: dict[str, Any]) -> None:
        state["book"]["price"]["status_note"] = "пересобрано"
        state["book"]["digest"] = digest_json(
            {key: value for key, value in state["book"].items() if key != "digest"}
        )

    revision, _ = service.registry.mutate(
        "ord-v4-review", rebuild_book, expected_revision=revision
    )
    with pytest.raises(InvalidState, match="QA-квитанцией"):
        service.qa_verdict(
            "ord-v4-review",
            revision,
            "commercial",
            "PASS",
            [],
            "методолог",
            actor_role="qa",
        )


def test_v4_supply_requires_and_prices_each_laser_driver(tmp_path: Path) -> None:
    rates = tmp_path / "rates4-drivers"
    rates.mkdir()
    data = pack_v4()
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    (rates / "v4-test.json").write_bytes(payload)
    (rates / ACTIVE_POINTER).write_text(
        json.dumps({"revision": "v4-test", "sha256": sha256_bytes(payload)}),
        encoding="utf-8",
    )
    service = WorkflowService(
        Registry(tmp_path / "orders4-drivers" / "registry.db"),
        PipelinePackStore(SecureRoot(rates, writable=False)),
    )
    laser_rates = service.process_catalog_tool()["park"]["CUT.LASER.SHEET"]["rates"]
    assert [(rate["rate_id"], rate["tariff_unit"]) for rate in laser_rates] == [
        ("blank:laser", "per_cut_m"),
        ("svc:laser-pierce", "per_pierce"),
    ]
    assert laser_rates[1]["quantity_field"] == "pierces_per_piece"
    revision = new_order(service, "ord-v4-drivers")
    revision = service.input_freeze(
        "ord-v4-drivers", revision, 10, "КД", source_manifest(), "вход", actor_role="front"
    )["revision"]
    revision = service.bom_upsert(
        "ord-v4-drivers",
        revision,
        [{"bom_node_id": "part", "parent_bom_id": None, "node_type": "MANUFACTURED_PART", "make_or_buy": "MAKE", "quantity": 1, "note": "деталь"}],
        actor_role="tech",
    )["revision"]
    proposed = service.route_variants_propose(
        "ord-v4-drivers",
        revision,
        [
            {"seq": 1, "process_code": "CUT.LASER.SHEET", "execution_mode": "in_house", "note": "лазер"},
            {"seq": 2, "process_code": "FORM.PRESS_BEND", "execution_mode": "in_house", "note": "гибка"},
        ],
        actor_role="tech",
    )
    revision = service.route_freeze(
        "ord-v4-drivers", proposed["revision"], proposed["variant_id"], actor_role="tech"
    )["revision"]
    with pytest.raises(InvalidState, match="svc:laser-pierce"):
        service.blank_drivers_set(
            "ord-v4-drivers", revision, "steel-09g2s", "1", "total", "масса",
            [{"route_seq": 1, "length_m": "12", "quantity_basis": "order_total", "note": "метры"}], actor_role="supply"
        )
    with pytest.raises(InvalidState, match="целое количество pierces_per_piece"):
        service.blank_drivers_set(
            "ord-v4-drivers", revision, "steel-09g2s", "1", "total", "масса",
            [
                {"route_seq": 1, "rate_id": "blank:laser", "length_m": "12", "quantity_basis": "order_total", "note": "метры"},
                {"route_seq": 1, "rate_id": "svc:laser-pierce", "pierces_per_piece": "2.5", "note": "врезки"},
            ], actor_role="supply"
        )
    with pytest.raises(InvalidState, match="quantity_basis"):
        service.blank_drivers_set(
            "ord-v4-drivers", revision, "steel-09g2s", "1", "total", "масса",
            [
                {"route_seq": 1, "rate_id": "blank:laser", "length_m": "12", "note": "неясный базис"},
                {"route_seq": 1, "rate_id": "svc:laser-pierce", "pierces_per_piece": 2, "note": "врезки"},
            ], actor_role="supply"
        )
    result = service.blank_drivers_set(
        "ord-v4-drivers", revision, "steel-09g2s", "1", "total", "масса",
        [
            {"route_seq": 1, "rate_id": "blank:laser", "length_m": "12", "quantity_basis": "order_total", "note": "метры"},
            {"route_seq": 1, "rate_id": "svc:laser-pierce", "pierces_per_piece": 2, "note": "врезки"},
        ], actor_role="supply"
    )["result"]
    route_lines = [line for line in result["lines"] if "route_seq" in line]
    assert [line["rate_id"] for line in route_lines] == ["blank:laser", "svc:laser-pierce"]
    assert [Decimal(str(line["subtotal_rub"])) for line in route_lines] == [
        Decimal("1440.00"), Decimal("140.00")
    ]
    per_piece_line = service._line_from_rate(
        service.packs.load_active(),
        cost_line_id="probe",
        rate_id="blank:laser",
        item={"length_m": "12", "quantity_basis": "per_piece"},
        note="probe",
        order_quantity=10,
    )
    assert Decimal(str(per_piece_line["quantity"])) == Decimal("120")
    assert Decimal(str(per_piece_line["subtotal_rub"])) == Decimal("14400.00")
    result_revision = service.registry.get("ord-v4-drivers")[0]
    with pytest.raises(InvalidState, match="целое количество bends_per_piece"):
        service.time_norms_set(
            "ord-v4-drivers",
            result_revision,
            [],
            [{"route_seq": 2, "bends_per_piece": "3.7", "note": "ошибка"}],
            actor_role="norm",
        )
    timed = service.time_norms_set(
        "ord-v4-drivers",
        result_revision,
        [],
        [{"route_seq": 2, "bends_per_piece": 3, "note": "три гиба на деталь"}],
        actor_role="norm",
    )["result"]
    assert timed["service_lines"][0]["rate_id"] == "svc:press-bend"
    assert Decimal(str(timed["service_lines"][0]["subtotal_rub"])) == Decimal("1500.00")
    assert timed["service_lines"][0]["driver_basis"] == "per_piece"
    assert timed["service_lines"][0]["driver_value"] == 3
    book_revision = service.book_assemble(
        "ord-v4-drivers",
        service.registry.get("ord-v4-drivers")[0],
        actor_role="front",
    )["revision"]
    _, state = service.registry.get("ord-v4-drivers")
    tampered = copy.deepcopy(state)
    tampered["costing"]["time"]["service_lines"][0]["driver_value"] = 1
    tampered["costing"]["time"]["digest"] = digest_json(
        {
            k: v
            for k, v in tampered["costing"]["time"].items()
            if k != "digest"
        }
    )
    tampered["book"]["time_digest"] = tampered["costing"]["time"]["digest"]
    tampered["book"]["digest"] = digest_json(
        {k: v for k, v in tampered["book"].items() if k != "digest"}
    )
    driver_check = {
        check["code"]: check
        for check in mechanical_checks(tampered, service.packs.load_active())
    }
    assert driver_check["per_piece_drivers"]["ok"] is False
    assert book_revision == service.registry.get("ord-v4-drivers")[0]


def test_v4_supply_confirm_requires_matching_vat_basis(tmp_path: Path) -> None:
    rates = tmp_path / "rates4-vat"
    rates.mkdir()
    data = pack_v4()
    data["process_park"]["FINISH.FITTER"] = {"rate_id": "blank:bench"}
    data["materials"]["steel-09g2s"]["stock"] = "purchase"
    data["pricing"]["rates_include_vat"] = True
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    (rates / "v4-test.json").write_bytes(payload)
    (rates / ACTIVE_POINTER).write_text(
        json.dumps({"revision": "v4-test", "sha256": sha256_bytes(payload)}),
        encoding="utf-8",
    )
    service = WorkflowService(
        Registry(tmp_path / "orders4-vat" / "registry.db"),
        PipelinePackStore(SecureRoot(rates, writable=False)),
    )
    revision = drive_to_book(service, "ord-v4-vat")
    with pytest.raises(InvalidState, match="явно укажите rate_includes_vat"):
        service.supply_confirm(
            "ord-v4-vat", revision, "100", "снабжение", "счёт", actor_role="supply"
        )
    with pytest.raises(InvalidState, match="ожидает закупочную ставку с НДС"):
        service.supply_confirm(
            "ord-v4-vat",
            revision,
            "100",
            "снабжение",
            "счёт",
            False,
            actor_role="supply",
        )
    confirmed = service.supply_confirm(
        "ord-v4-vat",
        revision,
        "100",
        "снабжение",
        "счёт",
        True,
        actor_role="supply",
    )
    material_line = confirmed["result"]["lines"][0]
    assert material_line["rate_includes_vat"] is True


def test_v4_in_house_axis_limit_rejects_long_press_bend(tmp_path: Path) -> None:
    rates = tmp_path / "rates4-limit"
    rates.mkdir()
    data = pack_v4()
    data["rate_registry"]["svc:press-bend"] = {
        "kind": "matrix",
        "tariff_unit": "per_bend",
        "vat_included": False,
        "source": MONEY,
        "axes": [
            {"name": "length_mm", "unit": "mm", "edges": ["0", "3028", "6000"]}
        ],
        "grid": [["50"], ["140"]],
    }
    data["process_park"]["FORM.PRESS_BEND"] = {
        "rate_id": "svc:press-bend",
        "in_house_axis_max": {"length_mm": "3028"},
    }
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    (rates / "v4-test.json").write_bytes(payload)
    (rates / ACTIVE_POINTER).write_text(
        json.dumps({"revision": "v4-test", "sha256": sha256_bytes(payload)}),
        encoding="utf-8",
    )
    service = WorkflowService(
        Registry(tmp_path / "orders4-limit" / "registry.db"),
        PipelinePackStore(SecureRoot(rates, writable=False)),
    )
    revision = new_order(service, "ord-v4-limit")
    revision = service.input_freeze(
        "ord-v4-limit", revision, 100, "КД", source_manifest(), "вход", actor_role="front"
    )["revision"]
    revision = service.bom_upsert(
        "ord-v4-limit",
        revision,
        [{"bom_node_id": "part", "parent_bom_id": None, "node_type": "MANUFACTURED_PART", "make_or_buy": "MAKE", "quantity": 1, "note": "деталь"}],
        actor_role="tech",
    )["revision"]
    proposed = service.route_variants_propose(
        "ord-v4-limit",
        revision,
        [{"seq": 1, "process_code": "FORM.PRESS_BEND", "execution_mode": "in_house", "note": "гибка"}],
        actor_role="tech",
    )
    revision = service.route_freeze(
        "ord-v4-limit", proposed["revision"], proposed["variant_id"], actor_role="tech"
    )["revision"]
    revision = service.blank_drivers_set(
        "ord-v4-limit", revision, "steel-09g2s", "10", "total", "лист", [], actor_role="supply"
    )["revision"]
    assert service.process_catalog_tool()["park"]["FORM.PRESS_BEND"][
        "in_house_axis_max"
    ] == {"length_mm": "3028"}
    with pytest.raises(InvalidState, match="length_mm.*3028.*подряд"):
        service.time_norms_set(
            "ord-v4-limit",
            revision,
            [],
            [{"route_seq": 1, "bends_per_piece": 7, "axes": {"length_mm": "5693"}, "note": "длинная гибка"}],
            actor_role="norm",
        )


# ── роли ─────────────────────────────────────────────────────────────────

def test_roles_are_enforced_on_writes(svc: WorkflowService) -> None:
    revision = new_order(svc)
    with pytest.raises(InvalidState, match="роли front"):
        svc.input_freeze(
            "ord-v3-1", revision, 10, "КД", source_manifest(), "н", actor_role="tech"
        )
    revision = svc.input_freeze(
        "ord-v3-1", revision, 10, "КД-42", source_manifest(), "вход", actor_role="front"
    )["revision"]
    with pytest.raises(InvalidState, match="роли tech"):
        svc.bom_upsert("ord-v3-1", revision, [], actor_role="supply")
    with pytest.raises(InvalidState, match="роли"):
        svc.qa_run_mechanical("ord-v3-1", revision, actor_role="norm")
    with pytest.raises(InvalidState, match="не задана"):
        svc.book_assemble("ord-v3-1", revision, actor_role=None)


def test_book_machine_can_only_assemble_and_run_engine_qa(svc: WorkflowService) -> None:
    revision = drive_to_book(svc, book_actor_role="book_machine")
    mechanical = svc.qa_run_mechanical(
        "ord-v3-1", revision, actor_role="book_machine"
    )
    assert mechanical["status"] == "QA_MECHANICAL_PASS"
    assert mechanical["verdict"]["verdict"] == "PASS"
    assert mechanical["verdict"]["computed_by"] == "engine"
    saved_revision, state = svc.registry.get("ord-v3-1")
    receipts = state["qa"][str(state["workflow"]["calculation_revision"])]
    assert set(receipts) == {"mechanical"}
    with pytest.raises(InvalidState, match="роли qa"):
        svc.qa_verdict(
            "ord-v3-1",
            saved_revision,
            "technological",
            "PASS",
            [],
            "machine",
            actor_role="book_machine",
        )
    assert svc.registry.get("ord-v3-1")[0] == saved_revision


def test_front_service_cannot_write_a_human_qa_pass(svc: WorkflowService) -> None:
    revision = drive_to_book(svc)
    revision = svc.qa_run_mechanical(
        "ord-v3-1", revision, actor_role="front"
    )["revision"]
    with pytest.raises(InvalidState, match="роли qa"):
        svc.qa_verdict(
            "ord-v3-1",
            revision,
            "technological",
            "PASS",
            [],
            "front-agent",
            actor_role="front",
        )
    assert svc.registry.get("ord-v3-1")[0] == revision


def test_chain_is_forced(svc: WorkflowService) -> None:
    revision = new_order(svc)
    revision = svc.input_freeze(
        "ord-v3-1", revision, 10, "КД-42", source_manifest(), "вход", actor_role="front"
    )["revision"]
    # Маршрут без BOM — отказ со статусом из схемы.
    with pytest.raises(InvalidState, match="BOM_VALIDATED"):
        svc.route_variants_propose("ord-v3-1", revision, ROUTE_STEPS, actor_role="tech")
    # Заготовка без замороженного маршрута — отказ.
    with pytest.raises(InvalidState, match="Замороженного маршрута нет"):
        svc.blank_drivers_set(
            "ord-v3-1", revision, "steel-09g2s", "1", "per_piece", "н",
            [], actor_role="supply",
        )


def test_route_requires_park_membership(svc: WorkflowService) -> None:
    revision = new_order(svc)
    revision = svc.input_freeze(
        "ord-v3-1", revision, 10, "КД-42", source_manifest(), "вход", actor_role="front"
    )["revision"]
    revision = svc.bom_upsert(
        "ord-v3-1",
        revision,
        [
            {
                "bom_node_id": "p1",
                "parent_bom_id": None,
                "node_type": "MANUFACTURED_PART",
                "make_or_buy": "MAKE",
                "quantity": 1,
                "note": "деталь",
            }
        ],
        actor_role="tech",
    )["revision"]
    steps = [
        {"seq": 1, "process_code": "FORM.TUBE_BEND", "execution_mode": "in_house", "note": "гиб"}
    ]
    with pytest.raises(InvalidRatePack, match="не заведена в парке"):
        svc.route_variants_propose("ord-v3-1", revision, steps, actor_role="tech")
    # Тот же код в подряд — можно: чужая операция это подряд, не отказ.
    steps[0]["execution_mode"] = "outsource"
    svc.route_variants_propose("ord-v3-1", revision, steps, actor_role="tech")


def test_other_specified_requires_outsource_and_name(svc: WorkflowService) -> None:
    revision = new_order(svc)
    revision = svc.input_freeze(
        "ord-v3-1", revision, 10, "КД-42", source_manifest(), "вход", actor_role="front"
    )["revision"]
    revision = svc.bom_upsert(
        "ord-v3-1",
        revision,
        [
            {
                "bom_node_id": "p1",
                "parent_bom_id": None,
                "node_type": "MANUFACTURED_PART",
                "make_or_buy": "MAKE",
                "quantity": 1,
                "note": "деталь",
            }
        ],
        actor_role="tech",
    )["revision"]
    bad = [
        {"seq": 1, "process_code": "OTHER.SPECIFIED", "execution_mode": "in_house", "note": "х"}
    ]
    with pytest.raises(InvalidState, match="слот подряда"):
        svc.route_variants_propose("ord-v3-1", revision, bad, actor_role="tech")
    good = [
        {
            "seq": 1,
            "process_code": "OTHER.SPECIFIED",
            "execution_mode": "outsource",
            "note": "цинкование",
            "actual_process_name": "гальваническое цинкование",
        }
    ]
    svc.route_variants_propose("ord-v3-1", revision, good, actor_role="tech")


# ── биекция и NEEDS_RATE ─────────────────────────────────────────────────

def test_supply_bijection_missing_and_duplicate(svc: WorkflowService) -> None:
    revision = drive_to_frozen(svc)
    with pytest.raises(InvalidState, match="Не посчитаны шаги заготовки"):
        svc.blank_drivers_set(
            "ord-v3-1", revision, "steel-09g2s", "1", "per_piece", "нота",
            [], actor_role="supply",
        )
    with pytest.raises(InvalidState, match="дважды"):
        svc.blank_drivers_set(
            "ord-v3-1", revision, "steel-09g2s", "1", "per_piece", "нота",
            [
                {"route_seq": 1, "cuts": 10, "note": "рез"},
                {"route_seq": 1, "cuts": 10, "note": "рез повторно"},
            ],
            actor_role="supply",
        )


def test_needs_rate_refuses_route_with_unrated_park_code(svc: WorkflowService) -> None:
    revision = new_order(svc)
    revision = svc.input_freeze(
        "ord-v3-1", revision, 4, "КД-42", source_manifest(), "вход", actor_role="front"
    )["revision"]
    revision = svc.bom_upsert(
        "ord-v3-1",
        revision,
        [
            {
                "bom_node_id": "p1",
                "parent_bom_id": None,
                "node_type": "MANUFACTURED_PART",
                "make_or_buy": "MAKE",
                "quantity": 1,
                "note": "деталь",
            }
        ],
        actor_role="tech",
    )["revision"]
    steps = [
        {"seq": 1, "process_code": "BLANK.CUTOFF", "execution_mode": "in_house", "note": "рез"},
        {"seq": 2, "process_code": "JOIN.WELD", "execution_mode": "in_house", "note": "сварка"},
    ]
    proposed = svc.route_variants_propose("ord-v3-1", revision, steps, actor_role="tech")
    revision = svc.route_freeze(
        "ord-v3-1", proposed["revision"], proposed["variant_id"], actor_role="tech"
    )["revision"]
    revision = svc.blank_drivers_set(
        "ord-v3-1", revision, "steel-09g2s", "1", "per_piece", "нота",
        [{"route_seq": 1, "cuts": 4, "note": "рез"}], actor_role="supply",
    )["revision"]
    # Сварка в парке без ставки: NEEDS_RATE, а не ноль в цене.
    with pytest.raises(InvalidRatePack, match="NEEDS_RATE"):
        svc.time_norms_set(
            "ord-v3-1", revision, [],
            [{"route_seq": 2, "hours": "1", "quantity_basis": "order_total", "note": "шов"}],
            actor_role="norm",
        )


def test_matrix_rate_prices_service_line(svc: WorkflowService) -> None:
    revision = new_order(svc)
    revision = svc.input_freeze(
        "ord-v3-1", revision, 2, "КД-42", source_manifest(), "вход", actor_role="front"
    )["revision"]
    revision = svc.bom_upsert(
        "ord-v3-1",
        revision,
        [
            {
                "bom_node_id": "p1",
                "parent_bom_id": None,
                "node_type": "MANUFACTURED_PART",
                "make_or_buy": "MAKE",
                "quantity": 1,
                "note": "обечайка",
            }
        ],
        actor_role="tech",
    )["revision"]
    steps = [
        {"seq": 1, "process_code": "BLANK.CUTOFF", "execution_mode": "in_house", "note": "рез"},
        {"seq": 2, "process_code": "FORM.ROLL", "execution_mode": "in_house", "note": "вальцовка"},
    ]
    proposed = svc.route_variants_propose("ord-v3-1", revision, steps, actor_role="tech")
    revision = svc.route_freeze(
        "ord-v3-1", proposed["revision"], proposed["variant_id"], actor_role="tech"
    )["revision"]
    revision = svc.blank_drivers_set(
        "ord-v3-1", revision, "steel-09g2s", "5", "per_piece", "лист 2 мм",
        [{"route_seq": 1, "cuts": 2, "note": "рез"}], actor_role="supply",
    )["revision"]
    result = svc.time_norms_set(
        "ord-v3-1", revision, [],
        [
            {
                "route_seq": 2,
                "pieces": 2,
                "axes": {"thickness_mm": "2.5", "width_mm": "600"},
                "note": "вальцовка обечайки",
            }
        ],
        actor_role="norm",
    )
    line = result["result"]["service_lines"][0]
    # Толщина 2,5 (интервал 1,9–2,9), ширина 600 (500–999) → 1650 ₽/шт × 2.
    assert Decimal(str(line["subtotal_rub"])) == Decimal("3300.00")


# ── один возврат маршрута ────────────────────────────────────────────────

def test_route_return_once_then_block_persists(svc: WorkflowService) -> None:
    revision = drive_to_book(svc)
    first = svc.route_return("ord-v3-1", revision, "маршрут спорит с КД", actor_role="qa")
    assert first["blocked"] is False
    assert first["status"] == "ROUTE_OPTIONS_READY"
    assert first["route_return_count"] == 1
    _, state = svc.registry.get("ord-v3-1")
    assert state["workflow"]["calculation_revision"] == 2
    # Расчёт и книга ушли в историю, не потерялись.
    assert state["workflow_history"][0]["book"] is not None
    assert "book" not in state
    assert "contractor_quotes" not in (state.get("costing") or {})

    # Новый вариант, снова замороженный.
    proposed = svc.route_variants_propose(
        "ord-v3-1", first["revision"], ROUTE_STEPS, actor_role="tech"
    )
    frozen = svc.route_freeze(
        "ord-v3-1", proposed["revision"], proposed["variant_id"], actor_role="tech"
    )
    second = svc.route_return("ord-v3-1", frozen["revision"], "снова спорит", actor_role="qa")
    assert second["blocked"] is True
    assert second["status"] == "BLOCK_FOR_TECH_REVIEW"
    # Стоп ПЕРЕЖИВАЕТ транзакцию: он в реестре, а не в тексте отказа.
    _, state = svc.registry.get("ord-v3-1")
    assert state["workflow"]["status"] == "BLOCK_FOR_TECH_REVIEW"
    assert state["status"] == "BLOCK_FOR_TECH_REVIEW"


# ── QA ───────────────────────────────────────────────────────────────────

def test_mechanical_verdict_cannot_be_set_by_hand(svc: WorkflowService) -> None:
    revision = drive_to_book(svc)
    with pytest.raises(InvalidState, match="вычисляется движком"):
        svc.qa_verdict(
            "ord-v3-1", revision, "mechanical", "PASS", [], "кто-то", actor_role="qa"
        )


def test_gate_order_is_enforced(svc: WorkflowService) -> None:
    revision = drive_to_book(svc)
    with pytest.raises(InvalidState, match="QA_MECHANICAL_PASS"):
        svc.qa_verdict(
            "ord-v3-1", revision, "technological", "PASS", [], "методолог", actor_role="qa"
        )
    revision = svc.qa_run_mechanical("ord-v3-1", revision, actor_role="front")["revision"]
    with pytest.raises(InvalidState, match="QA_TECHNOLOGICAL_PASS"):
        svc.qa_verdict(
            "ord-v3-1", revision, "commercial", "PASS", [], "методолог", actor_role="qa"
        )


def test_no_evidence_is_not_pass(svc: WorkflowService) -> None:
    revision = drive_to_book(svc)
    revision = svc.qa_run_mechanical("ord-v3-1", revision, actor_role="front")["revision"]
    result = svc.qa_verdict(
        "ord-v3-1",
        revision,
        "technological",
        "NO_EVIDENCE",
        ["нет фото страницы справочника"],
        "методолог",
        actor_role="qa",
    )
    # Зафиксировано, но статус не продвинулся: NO_EVIDENCE ≠ PASS.
    assert result["status"] == "QA_MECHANICAL_PASS"
    with pytest.raises(InvalidState, match="QA_TECHNOLOGICAL_PASS"):
        svc.qa_verdict(
            "ord-v3-1", result["revision"], "commercial", "PASS", [], "методолог", actor_role="qa"
        )


def test_adjust_bumps_revision_and_resets_gates(svc: WorkflowService) -> None:
    revision = drive_to_book(svc)
    revision = svc.qa_run_mechanical("ord-v3-1", revision, actor_role="front")["revision"]
    result = svc.qa_verdict(
        "ord-v3-1",
        revision,
        "technological",
        "ADJUST",
        ["нет источника нормы у точения"],
        "методолог",
        adjust_owner="norm",
        actor_role="qa",
    )
    assert result["status"] == "DETAILED_COSTING"
    _, state = svc.registry.get("ord-v3-1")
    assert state["workflow"]["calculation_revision"] == 2
    # Квитанции прежней ревизии ушли в историю; текущая ревизия чиста.
    assert str(1) not in (state.get("qa") or {})
    # Новая книга требует пересчёта времени: пересчитываем и проходим заново.
    revision = svc.time_norms_set(
        "ord-v3-1",
        result["revision"],
        [
            {
                "route_seq": 2,
                "batch": 5,
                "params": {"diameter_mm": "60", "length_mm": "120", "stock_mm": "3"},
            }
        ],
        [{"route_seq": 3, "hours": "0.5", "quantity_basis": "order_total", "note": "заусенцы"}],
        actor_role="norm",
    )["revision"]
    revision = svc.book_assemble("ord-v3-1", revision, actor_role="front")["revision"]
    verdict = svc.qa_run_mechanical("ord-v3-1", revision, actor_role="front")
    assert verdict["verdict"]["verdict"] == "PASS"


def test_adjust_to_tech_is_refused_use_route_return(svc: WorkflowService) -> None:
    revision = drive_to_book(svc)
    revision = svc.qa_run_mechanical("ord-v3-1", revision, actor_role="front")["revision"]
    with pytest.raises(InvalidState, match="route_return"):
        svc.qa_verdict(
            "ord-v3-1",
            revision,
            "technological",
            "ADJUST",
            ["маршрут неверен"],
            "методолог",
            adjust_owner="tech",
            actor_role="qa",
        )


def test_provisional_blocks_commercial_pass(svc: WorkflowService) -> None:
    # Материал под закупку (steel-40x) → звёздочка → коммерческий PASS запрещён.
    revision = drive_to_frozen(svc, "ord-v3-2")
    revision = svc.blank_drivers_set(
        "ord-v3-2", revision, "steel-40x", "2.5", "per_piece", "закупка",
        [{"route_seq": 1, "cuts": 10, "note": "рез"}], actor_role="supply",
    )["revision"]
    revision = svc.time_norms_set(
        "ord-v3-2",
        revision,
        [
            {
                "route_seq": 2,
                "batch": 5,
                "params": {"diameter_mm": "60", "length_mm": "120", "stock_mm": "3"},
            }
        ],
        [{"route_seq": 3, "hours": "0.5", "quantity_basis": "order_total", "note": "заусенцы"}],
        actor_role="norm",
    )["revision"]
    revision = svc.contractor_quote_set(
        "ord-v3-2",
        revision,
        4,
        "120",
        "per_piece",
        True,
        "2026-09-04",
        "2099-09-04",
        "ООО Подряд",
        "КП-4",
        "panel-test",
        actor_role="panel",
    )["revision"]
    revision = svc.book_assemble("ord-v3-2", revision, actor_role="front")["revision"]
    revision = svc.qa_run_mechanical("ord-v3-2", revision, actor_role="front")["revision"]
    revision = svc.qa_verdict(
        "ord-v3-2", revision, "technological", "PASS", [], "методолог", actor_role="qa"
    )["revision"]
    with pytest.raises(InvalidState, match="предварительной цене"):
        svc.qa_verdict(
            "ord-v3-2", revision, "commercial", "PASS", [], "методолог", actor_role="qa"
        )
    # supply_confirm снимает звёздочку и сносит книгу — пересборка обязательна.
    revision = svc.supply_confirm(
        "ord-v3-2", revision, "97", "снабжение-иванов", "счёт 214 от 01.09",
        actor_role="supply",
    )["revision"]
    _, state = svc.registry.get("ord-v3-2")
    assert state["workflow"]["status"] == "COSTING_COMPLETE"
    assert "book" not in state
    revision = svc.contractor_quote_set(
        "ord-v3-2",
        revision,
        4,
        "120",
        "per_piece",
        True,
        "2026-09-04",
        "2099-09-04",
        "ООО Подряд",
        "КП-4 повторно после смены ревизии",
        "panel-test",
        actor_role="panel",
    )["revision"]
    revision = svc.book_assemble("ord-v3-2", revision, actor_role="front")["revision"]
    revision = svc.qa_run_mechanical("ord-v3-2", revision, actor_role="front")["revision"]
    revision = svc.qa_verdict(
        "ord-v3-2", revision, "technological", "PASS", [], "методолог", actor_role="qa"
    )["revision"]
    revision = complete_manual_review(svc, revision, "ord-v3-2")
    final = svc.qa_verdict(
        "ord-v3-2", revision, "commercial", "PASS", [], "методолог", actor_role="qa"
    )
    assert final["status"] == "READY_FOR_LD"


def test_calc_revision_open_keeps_route_frozen(svc: WorkflowService) -> None:
    revision = drive_to_book(svc)
    result = svc.calc_revision_open(
        "ord-v3-1", revision, "методолог сменила ставки", actor_role="front"
    )
    assert result["status"] == "ROUTE_FROZEN"
    assert result["calculation_revision"] == 2
    _, state = svc.registry.get("ord-v3-1")
    assert "book" not in state
    assert set(state["costing"]) == {"contractor_quotes", "pack_fingerprint"}
    assert state["costing"]["contractor_quotes"]["4"]["calculation_revision"] == 1
    assert state["workflow_history"][0]["costing"] is not None


def test_legacy_tools_refuse_v3_order(svc: WorkflowService, packs: PipelinePackStore) -> None:
    from metal_calc.service2 import PipelineService

    revision = new_order(svc)
    revision = svc.input_freeze(
        "ord-v3-1", revision, 10, "КД-42", source_manifest(), "вход", actor_role="front"
    )["revision"]
    legacy = PipelineService(svc.registry, packs)
    with pytest.raises(Conflict, match="схеме разделения V2"):
        legacy.blank_cost(
            "ord-v3-1", revision, "steel-09g2s", 10, "1", "per_piece", "нота",
            [{"op_code": "bandsaw", "cuts": 1, "note": "рез"}],
        )
    with pytest.raises(Conflict, match="схеме разделения V2"):
        legacy.stage_approve("ord-v3-1", revision, "blank", "оператор")


def test_input_freeze_refuses_legacy_pipeline_order(svc: WorkflowService) -> None:
    revision, _ = svc.registry.create(
        "ord-legacy",
        {
            "order_id": "ord-legacy",
            "customer": {"name": "Synthetic"},
            "source_files": [],
            "status": "draft",
            "warnings": [],
            "timestamps": {},
            "provenance": {"created_by": "test"},
            "stages": {"blank": {"status": "proposed"}},
        },
    )
    with pytest.raises(Conflict, match="конвейер v6"):
        svc.input_freeze(
            "ord-legacy", revision, 5, "КД", source_manifest(), "вход", actor_role="front"
        )


# ── регресс находок Codex-ревью 01.09 ────────────────────────────────────


def test_bom_keeps_one_manufactured_root_rule(svc: WorkflowService) -> None:
    revision = new_order(svc)
    revision = svc.input_freeze(
        "ord-v3-1", revision, 10, "КД-42", source_manifest(), "вход", actor_role="front"
    )["revision"]
    nodes = [
        {
            "bom_node_id": node_id,
            "parent_bom_id": None,
            "node_type": "MANUFACTURED_PART",
            "make_or_buy": "MAKE",
            "quantity": 1,
            "note": "деталь",
        }
        for node_id in ("root-1", "root-2")
    ]
    with pytest.raises(InvalidState, match="Ровно одна изготавливаемая деталь-корень"):
        svc.bom_upsert("ord-v3-1", revision, nodes, actor_role="tech")


def test_purchased_items_are_refused_fail_closed(svc: WorkflowService) -> None:
    """B2: узел без тарификации = нулевая закупка при зелёных гейтах."""
    revision = new_order(svc)
    revision = svc.input_freeze(
        "ord-v3-1", revision, 10, "КД-42", source_manifest(), "вход", actor_role="front"
    )["revision"]
    nodes = [
        {
            "bom_node_id": "p1",
            "parent_bom_id": None,
            "node_type": "MANUFACTURED_PART",
            "make_or_buy": "MAKE",
            "quantity": 1,
            "note": "вал",
        },
        {
            "bom_node_id": "bolt-1",
            "parent_bom_id": "p1",
            "node_type": "PURCHASED_ITEM",
            "make_or_buy": "BUY",
            "quantity": 4,
            "note": "болт",
        },
    ]
    with pytest.raises(InvalidState, match="не тарифицируются"):
        svc.bom_upsert("ord-v3-1", revision, nodes, actor_role="tech")


def test_per_piece_rate_must_cover_whole_order(svc: WorkflowService) -> None:
    """B1: штучный тариф на 1 изделие вместо заказа."""
    revision = new_order(svc)
    revision = svc.input_freeze(
        "ord-v3-1", revision, 100, "КД-42", source_manifest(), "вход", actor_role="front"
    )["revision"]
    revision = svc.bom_upsert(
        "ord-v3-1",
        revision,
        [
            {
                "bom_node_id": "p1",
                "parent_bom_id": None,
                "node_type": "MANUFACTURED_PART",
                "make_or_buy": "MAKE",
                "quantity": 1,
                "note": "обечайка",
            }
        ],
        actor_role="tech",
    )["revision"]
    steps = [
        {"seq": 1, "process_code": "BLANK.CUTOFF", "execution_mode": "in_house", "note": "рез"},
        {"seq": 2, "process_code": "FORM.ROLL", "execution_mode": "in_house", "note": "вальцовка"},
    ]
    proposed = svc.route_variants_propose("ord-v3-1", revision, steps, actor_role="tech")
    revision = svc.route_freeze(
        "ord-v3-1", proposed["revision"], proposed["variant_id"], actor_role="tech"
    )["revision"]
    revision = svc.blank_drivers_set(
        "ord-v3-1", revision, "steel-09g2s", "5", "per_piece", "лист",
        [{"route_seq": 1, "cuts": 100, "note": "резы"}], actor_role="supply",
    )["revision"]
    with pytest.raises(InvalidState, match="pieces обязан равняться"):
        svc.time_norms_set(
            "ord-v3-1", revision, [],
            [
                {
                    "route_seq": 2,
                    "pieces": 1,
                    "axes": {"thickness_mm": "2.5", "width_mm": "600"},
                    "note": "вальцовка одной штуки",
                }
            ],
            actor_role="norm",
        )


def test_pack_change_between_gates_blocks_pass(
    svc: WorkflowService, tmp_path: Path
) -> None:
    """B3: смена активного пака после механического PASS не даёт ручные PASS."""
    from metal_calc.errors import PackChanged

    revision = drive_to_book(svc)
    revision = svc.qa_run_mechanical("ord-v3-1", revision, actor_role="front")["revision"]
    # Публикуем «новые ставки»: другой пак меняет отпечаток данных.
    rates = tmp_path / "rates3"
    changed = workflow_pack("v3-test2")
    changed["machines"]["turning"]["rate_rub_per_hour"] = "5000"
    payload = json.dumps(changed, ensure_ascii=False, sort_keys=True).encode("utf-8")
    (rates / "v3-test2.json").write_bytes(payload)
    (rates / ACTIVE_POINTER).write_text(
        json.dumps({"revision": "v3-test2", "sha256": sha256_bytes(payload)}),
        encoding="utf-8",
    )
    with pytest.raises(PackChanged, match="другой ревизии данных"):
        svc.qa_verdict(
            "ord-v3-1", revision, "technological", "PASS", [], "методолог", actor_role="qa"
        )
    # Штатный выход БЕЗ расходования возврата маршрута (B6): открыть пересчёт,
    # пересчитать обе зоны по новым данным, собрать книгу заново.
    result = svc.calc_revision_open(
        "ord-v3-1", revision, "новые ставки", actor_role="front"
    )
    assert result["status"] == "ROUTE_FROZEN"
    revision = svc.blank_drivers_set(
        "ord-v3-1", result["revision"], "steel-09g2s", "2.5", "per_piece", "чертёж",
        [{"route_seq": 1, "cuts": 10, "note": "рез"}], actor_role="supply",
    )["revision"]
    revision = svc.time_norms_set(
        "ord-v3-1", revision,
        [
            {
                "route_seq": 2,
                "batch": 5,
                "params": {"diameter_mm": "60", "length_mm": "120", "stock_mm": "3"},
            }
        ],
        [{"route_seq": 3, "hours": "0.5", "quantity_basis": "order_total", "note": "заусенцы"}],
        actor_role="norm",
    )["revision"]
    revision = svc.book_assemble("ord-v3-1", revision, actor_role="front")["revision"]
    verdict = svc.qa_run_mechanical("ord-v3-1", revision, actor_role="front")
    assert verdict["verdict"]["verdict"] == "PASS"
    _, state = svc.registry.get("ord-v3-1")
    assert state["workflow"]["route_return_count"] == 0


def test_workflow_status_marks_any_saved_costing_fingerprint_stale(
    svc: WorkflowService,
) -> None:
    revision = drive_to_book(svc)
    assert svc.workflow_status("ord-v3-1")["pack"]["stale"] is False

    def split_fingerprints(state: dict[str, Any]) -> None:
        state["costing"]["time"]["pack_fingerprint"] = "0" * 64

    svc.registry.mutate(
        "ord-v3-1", split_fingerprints, expected_revision=revision
    )
    assert svc.workflow_status("ord-v3-1")["pack"]["stale"] is True


def test_input_freeze_refuses_priced_v1_order(svc: WorkflowService) -> None:
    """B5: заказ с v1-ценой не переводится в схему — две живые цены."""
    revision, _ = svc.registry.create(
        "ord-v1-priced",
        {
            "order_id": "ord-v1-priced",
            "customer": {"name": "Synthetic"},
            "source_files": [],
            "status": "calculated",
            "warnings": [],
            "timestamps": {},
            "provenance": {"created_by": "test"},
            "price": {"total_rub": 12345},
        },
    )
    with pytest.raises(Conflict, match="две живые цены|статусе"):
        svc.input_freeze(
            "ord-v1-priced", revision, 5, "КД", source_manifest(), "вход", actor_role="front"
        )


def test_history_snapshot_survives_mutation(svc: WorkflowService) -> None:
    """M7: история хранит копию, а не ссылку на живые dict."""
    revision = drive_to_book(svc)
    svc.route_return("ord-v3-1", revision, "проверка истории", actor_role="qa")
    _, state = svc.registry.get("ord-v3-1")
    archived = state["workflow_history"][0]
    assert archived["costing"]["blank"]["lines"], "история потеряла заготовку"
    assert archived["costing"]["time"]["items"], "история потеряла нормы"
    assert archived["book"]["digest"], "история потеряла книгу"


def test_supply_confirm_archives_pre_confirm_book(svc: WorkflowService) -> None:
    """M7: в архиве старая книга рядом со СТАРОЙ заготовкой, digest сходятся."""
    revision = drive_to_frozen(svc, "ord-v3-2")
    revision = svc.blank_drivers_set(
        "ord-v3-2", revision, "steel-40x", "2.5", "per_piece", "закупка",
        [{"route_seq": 1, "cuts": 10, "note": "рез"}], actor_role="supply",
    )["revision"]
    revision = svc.time_norms_set(
        "ord-v3-2", revision,
        [
            {
                "route_seq": 2,
                "batch": 5,
                "params": {"diameter_mm": "60", "length_mm": "120", "stock_mm": "3"},
            }
        ],
        [{"route_seq": 3, "hours": "0.5", "quantity_basis": "order_total", "note": "заусенцы"}],
        actor_role="norm",
    )["revision"]
    revision = svc.book_assemble("ord-v3-2", revision, actor_role="front")["revision"]
    revision = svc.supply_confirm(
        "ord-v3-2", revision, "97", "снабжение", "счёт 1", actor_role="supply"
    )["revision"]
    _, state = svc.registry.get("ord-v3-2")
    archived = state["workflow_history"][0]
    assert archived["book"]["blank_digest"] == archived["costing"]["blank"]["digest"]
    # Живая заготовка уже подтверждена и отличается от архивной.
    assert state["costing"]["blank"]["digest"] != archived["costing"]["blank"]["digest"]
    assert state["costing"]["blank"]["provisional"] is False


def test_material_change_drops_time_not_refuses(svc: WorkflowService) -> None:
    """M8: адресный ADJUST снабжению со сменой материала завершается."""
    revision = drive_to_book(svc)
    revision = svc.qa_run_mechanical("ord-v3-1", revision, actor_role="front")["revision"]
    revision = svc.qa_verdict(
        "ord-v3-1", revision, "technological", "ADJUST",
        ["материал в КД другой"], "методолог", adjust_owner="supply", actor_role="qa",
    )["revision"]
    # Снабжение записывает ДРУГОЙ материал — нормы старого материала уходят.
    revision = svc.blank_drivers_set(
        "ord-v3-1", revision, "steel-40x", "2.5", "per_piece", "по уточнённой КД",
        [{"route_seq": 1, "cuts": 10, "note": "рез"}], actor_role="supply",
    )["revision"]
    _, state = svc.registry.get("ord-v3-1")
    assert "time" not in state["costing"]
    assert state["workflow"]["status"] == "DETAILED_COSTING"
    events = [e["event"] for e in state["workflow_events"]]
    assert "time_dropped_material_changed" in events


def test_upgrade_validates_already_v3_input() -> None:
    """M11: pack-upgrade не отвечает ok на битый v3."""
    from metal_calc.packadmin import upgrade_pack_v2_to_v3

    broken = workflow_pack()
    broken["process_park"] = {}
    raw = json.dumps(broken, ensure_ascii=False).encode("utf-8")
    with pytest.raises(InvalidRatePack, match="process_park пуст"):
        upgrade_pack_v2_to_v3(raw)


def test_matrix_open_end_requires_strict_bool() -> None:
    """M10: строка "false" не превращается в открытую границу."""
    from test_pack_v3 import _validate

    data = workflow_pack()
    data["rate_registry"]["svc:roll"]["axes"][1]["open_end"] = "false"
    with pytest.raises(InvalidRatePack, match="строго true или false"):
        _validate(data)
