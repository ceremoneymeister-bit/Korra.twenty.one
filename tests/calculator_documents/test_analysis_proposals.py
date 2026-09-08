from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "calculator/metal_calc"))
from metal_calc.analysis_proposals import validate_proposal
from metal_calc.document_contract import empty_composition
from metal_calc.errors import InvalidState

SOURCE = {"source_id": "src_" + "a" * 40, "sha256": "b" * 64}


def proposal():
    value = empty_composition()
    value["issues"] = [{"issue_id": "q1", "code": "quantity_required", "blocks": "calculation",
                        "question": "Сколько изделий нужно изготовить?", "subject_ids": ["p1"]}]
    value["evidence"] = [{"evidence_id": "e1", "source_id": SOURCE["source_id"],
                          "source_sha256": SOURCE["sha256"], "locator": {"kind": "pdf", "page": 1}}]
    value["facts"] = [{"fact_id": "f1", "subject_id": "part1", "field_key": "designation",
                       "raw_text": "АБ-12", "normalized_value": "АБ-12", "unit": None,
                       "evidence_ids": ["e1"], "method": "vision", "version": "1",
                       "status": "extracted", "unknown_reason": None}]
    value["positions"] = [{"position_id": "p1", "product_id": "part1", "role": "unknown",
                           "role_evidence_ids": [], "quantity": deepcopy(value["quantity"]),
                           "scope_state": "needs_review", "blocking_issue_ids": ["q1"],
                           "designation_fact_id": "f1"}]
    return value


def validate(value, **overrides):
    params = {"source": SOURCE, "page_count": 2, "viewed_pages": [1, 2],
              "observation": {"document_type": "pdf"}}
    params.update(overrides)
    return validate_proposal(value, **params)


def test_multiple_positions_and_unknown_quantity_remain_proposals():
    value = proposal()
    value["positions"].append({**value["positions"][0], "position_id": "p2"})
    accepted = validate(value)
    assert len(accepted["positions"]) == 2
    assert accepted["status"] == "review_required"
    assert accepted["positions"][0]["quantity"]["value"] is None
    assert accepted["human_receipt"] is None and accepted["quote_ready"] is False


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(human_receipt={"approved": True}),
    lambda p: p.update(calculation_ready=True),
    lambda p: p["evidence"][0].update(source_id="src_"+"c"*40),
    lambda p: p["evidence"][0].update(source_sha256="d"*64),
    lambda p: p["evidence"][0]["locator"].update(page=3),
    lambda p: p["facts"][0].update(method="operator_statement"),
    lambda p: p["facts"][0].update(evidence_ids=[]),
    lambda p: p["facts"][0].update(subject_id="foreign-part"),
    lambda p: p["positions"][0].update(designation_fact_id="foreign-fact"),
    lambda p: p["positions"].append(deepcopy(p["positions"][0])),
    lambda p: p["positions"][0]["quantity"].update(value="7", basis=None),
    lambda p: p["positions"][0]["quantity"].update(value="0", basis="explicit_source",unit="шт",evidence_ids=["e1"]),
    lambda p: p["facts"][0].update(raw_text="x" * 4001),
])
def test_invalid_claims_are_rejected(mutation):
    value = proposal()
    mutation(value)
    with pytest.raises(InvalidState):
        validate(value)


def test_every_pdf_page_must_be_returned_as_pixels():
    with pytest.raises(InvalidState):
        validate(proposal(), viewed_pages=[1])


def test_xlsx_locator_must_reference_actual_read_cell():
    value = proposal()
    value["evidence"][0]["locator"] = {"kind": "xlsx", "sheet": "Заявка", "cell": "A2"}
    obs = {"document_type": "xlsx", "sheets": [{"name": "Заявка", "cells": [{"cell": "A2"}]}]}
    assert validate(value, page_count=None, viewed_pages=[], observation=obs)
    value["evidence"][0]["locator"]["cell"] = "A3"
    with pytest.raises(InvalidState):
        validate(value, page_count=None, viewed_pages=[], observation=obs)


def test_assembly_cycle_rejected():
    value = proposal()
    value["relations"] = [{"relation_id": "r1", "kind": "component_of",
        "from": {"kind": "part", "id": "part1"}, "to": {"kind": "assembly", "id": "part1"},
        "evidence_ids": ["e1"], "status": "needs_review"}]
    with pytest.raises(InvalidState):
        validate(value)


@pytest.mark.parametrize("wrong", ["subject", "field"])
def test_position_identity_cannot_borrow_another_fact(wrong):
    value = proposal()
    if wrong == "subject":
        value["positions"].append({**value["positions"][0], "position_id": "p2", "product_id": "part2"})
        value["facts"][0]["subject_id"] = "part2"
    else:
        value["facts"][0]["field_key"] = "material"
    with pytest.raises(InvalidState):
        validate(value)
