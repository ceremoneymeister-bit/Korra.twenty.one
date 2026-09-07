"""Пак v3: парк процессов, реестр ставок, матричные тарифы, автоперенос v2→v3."""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from metal_calc.errors import InvalidRatePack, InvalidState
from metal_calc.packadmin import upgrade_pack_v2_to_v3, validate_pack
from metal_calc.packs2 import _validate_pack2, implausible_values
from metal_calc.process_catalog import CATALOG, CATALOG_VERSION, catalog_listing
from metal_calc.util import sha256_bytes

from test_pipeline_v2 import MONEY, pipeline_pack


def _validate(data: dict[str, Any]):
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return _validate_pack2(data, sha256_bytes(raw), data["revision"])


def pack_v3(revision: str = "v3-test") -> dict[str, Any]:
    data = pipeline_pack(revision)
    data["schema_version"] = 3
    data["process_park"] = {
        "BLANK.CUTOFF": {"method_code": "BAND_SAW", "rate_id": "blank:bandsaw"},
        "CUT.LASER.SHEET": {"rate_id": "blank:laser"},
        "MACHINING.TURN.CNC": {"rate_id": "machine:turning"},
        "MACHINING.MILL.CNC": {"rate_id": "machine:milling"},
        "MACHINING.EDM.GENERAL": {"rate_id": "machine:edm"},
        "FORM.ROLL": {"rate_id": "svc:roll"},
        "JOIN.WELD": {"rate_id": None, "note": "ставка не заведена — NEEDS_RATE"},
    }
    data["rate_registry"] = {
        "svc:roll": {
            "kind": "matrix",
            "tariff_unit": "per_piece",
            "vat_included": False,
            "source": MONEY,
            "axes": [
                {"name": "thickness_mm", "unit": "mm", "edges": ["0", "1.9", "2.9"]},
                {
                    "name": "width_mm",
                    "unit": "mm",
                    "edges": ["0", "499", "999"],
                    "open_end": True,
                },
            ],
            "grid": [
                ["370", "740", "1110"],
                ["550", "1650", None],
            ],
        }
    }
    return data


def pack_v4(revision: str = "v4-test") -> dict[str, Any]:
    data = pack_v3(revision)
    data["schema_version"] = 4
    data["pricing"]["material_markup_percent"] = "15"
    data["pricing"]["rates_include_vat"] = False
    data["rate_registry"]["svc:laser-pierce"] = {
        "kind": "scalar",
        "tariff_unit": "per_pierce",
        "vat_included": False,
        "value": "7",
        "source": MONEY,
    }
    data["rate_registry"]["svc:press-bend"] = {
        "kind": "scalar",
        "tariff_unit": "per_bend",
        "vat_included": False,
        "value": "50",
        "source": MONEY,
    }
    data["process_park"]["CUT.LASER.SHEET"]["supplementary_rate_ids"] = [
        "svc:laser-pierce"
    ]
    data["process_park"]["FORM.PRESS_BEND"] = {"rate_id": "svc:press-bend"}
    return data


# ── каталог ──────────────────────────────────────────────────────────────


def test_catalog_carries_thirty_codes_and_families() -> None:
    assert len(CATALOG) == 30
    assert CATALOG_VERSION == "metal_processes@1.0.0"
    assert CATALOG["MACHINING.TURN.MANUAL"].formula_family == "turning"
    assert CATALOG["MACHINING.EDM.HOLE_DRILL"].formula_family == "edm"
    assert CATALOG["FORM.ROLL"].formula_family is None
    assert CATALOG["BLANK.CUTOFF"].method_required
    assert not CATALOG["OTHER.SPECIFIED"].is_process
    listing = catalog_listing()
    assert listing["BLANK.CUTOFF"]["method_codes"] == [
        "BAND_SAW",
        "ANGLE_GRINDER",
        "MECHANICAL_PIPE_CUTTER",
    ]


def test_catalog_unknown_code_names_neighbours() -> None:
    from metal_calc import process_catalog

    with pytest.raises(InvalidState, match="MACHINING.TURN"):
        process_catalog.get("MACHINING.TURN.LATHE")


# ── валидация v3 ─────────────────────────────────────────────────────────


def test_v2_pack_still_validates_with_canonical_registry() -> None:
    pack = _validate(pipeline_pack())
    assert pack.schema_version == 2
    assert pack.process_park == {}
    assert pack.rate_registry["machine:turning"]["value"] == Decimal("3000")
    assert pack.rate_registry["material:steel-40x"]["tariff_unit"] == "per_kg"


def test_v3_pack_validates_park_and_matrix() -> None:
    pack = _validate(pack_v3())
    assert pack.schema_version == 3
    assert pack.park_entry("BLANK.CUTOFF")["method_code"] == "BAND_SAW"
    assert pack.park_entry("JOIN.WELD")["rate_id"] is None
    assert pack.rate("svc:roll")["kind"] == "matrix"


def test_v4_adds_component_markup_supplementary_rates_and_explicit_units() -> None:
    data = pack_v4()
    data["process_park"]["CUT.LASER.SHEET"]["requires_manual_review"] = True
    pack = _validate(data)
    assert pack.schema_version == 4
    assert pack.pricing["material_markup_percent"] == Decimal("15")
    assert pack.park_entry("CUT.LASER.SHEET")["supplementary_rate_ids"] == [
        "svc:laser-pierce"
    ]
    assert pack.rate("svc:laser-pierce")["tariff_unit"] == "per_pierce"
    assert pack.rate("svc:press-bend")["tariff_unit"] == "per_bend"
    assert pack.park_entry("CUT.LASER.SHEET")["requires_manual_review"] is True
    assert "наценка 0% — работа по себестоимости" not in implausible_values(pack)


def test_v4_accepts_explicit_commercial_half_up_rounding() -> None:
    data = pack_v4()
    data["pricing"]["rounding"] = "half_up"

    pack = _validate(data)

    assert pack.pricing["rounding"] == "half_up"


def test_v4_rejects_non_boolean_manual_review_flag() -> None:
    data = pack_v4()
    data["process_park"]["CUT.LASER.SHEET"]["requires_manual_review"] = "yes"
    with pytest.raises(InvalidRatePack, match="requires_manual_review.*boolean"):
        _validate(data)


def test_v4_validates_in_house_axis_max_against_primary_rate() -> None:
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
    data["process_park"]["FORM.PRESS_BEND"]["in_house_axis_max"] = {
        "length_mm": "3028"
    }
    pack = _validate(data)
    assert pack.park_entry("FORM.PRESS_BEND")["in_house_axis_max"] == {
        "length_mm": "3028"
    }

    data["process_park"]["FORM.PRESS_BEND"]["in_house_axis_max"] = {
        "unknown_axis": "10"
    }
    with pytest.raises(InvalidRatePack, match="in_house_axis_max"):
        _validate(data)


def test_v4_rejects_duplicate_supplementary_rate() -> None:
    data = pack_v4()
    data["process_park"]["CUT.LASER.SHEET"]["supplementary_rate_ids"] = [
        "blank:laser",
        "svc:laser-pierce",
    ]
    with pytest.raises(InvalidRatePack, match="не должны повторяться"):
        _validate(data)


def test_v4_rejects_supplementary_rate_outside_supply_zone() -> None:
    data = pack_v4()
    data["process_park"]["MACHINING.TURN.CNC"]["supplementary_rate_ids"] = [
        "svc:laser-pierce"
    ]
    with pytest.raises(InvalidRatePack, match="только.*supply"):
        _validate(data)


def test_v3_rejects_v4_fields() -> None:
    data = pack_v3()
    data["pricing"]["material_markup_percent"] = "15"
    with pytest.raises(InvalidRatePack, match="лишние: material_markup_percent"):
        _validate(data)


def test_v3_requires_exactly_one_cutoff_method() -> None:
    data = pack_v3()
    del data["process_park"]["BLANK.CUTOFF"]["method_code"]
    with pytest.raises(InvalidRatePack, match="ровно один method_code"):
        _validate(data)
    data["process_park"]["BLANK.CUTOFF"]["method_code"] = "LASER"
    with pytest.raises(InvalidRatePack, match="ровно один method_code"):
        _validate(data)


def test_v3_rejects_method_for_methodless_code() -> None:
    data = pack_v3()
    data["process_park"]["CUT.LASER.SHEET"]["method_code"] = "BAND_SAW"
    with pytest.raises(InvalidRatePack, match="лишние: method_code"):
        _validate(data)


def test_v3_rejects_other_specified_in_park() -> None:
    data = pack_v3()
    data["process_park"]["OTHER.SPECIFIED"] = {"rate_id": None}
    with pytest.raises(InvalidRatePack, match="слот вывода"):
        _validate(data)


def test_v3_rejects_unknown_park_code() -> None:
    data = pack_v3()
    data["process_park"]["MACHINING.TURN.LATHE"] = {"rate_id": None}
    with pytest.raises(InvalidState, match="отсутствует в каталоге"):
        _validate(data)


def test_v3_park_rate_must_resolve() -> None:
    data = pack_v3()
    data["process_park"]["FORM.ROLL"]["rate_id"] = "svc:missing"
    with pytest.raises(InvalidRatePack, match="не найдена в реестре"):
        _validate(data)


def test_v3_reserved_prefix_refused() -> None:
    data = pack_v3()
    data["rate_registry"]["machine:turning"] = {
        "kind": "scalar",
        "tariff_unit": "per_hour",
        "vat_included": False,
        "value": "999",
        "source": MONEY,
    }
    with pytest.raises(InvalidRatePack, match="занимает"):
        _validate(data)


def test_v3_gross_rate_refused() -> None:
    data = pack_v3()
    data["rate_registry"]["svc:roll"]["vat_included"] = True
    with pytest.raises(InvalidRatePack, match="vat_included"):
        _validate(data)


def test_v3_empty_park_refused() -> None:
    data = pack_v3()
    data["process_park"] = {}
    with pytest.raises(InvalidRatePack, match="process_park пуст"):
        _validate(data)


def test_v3_changes_fingerprint_but_v2_core_untouched() -> None:
    base = _validate(pack_v3())
    changed_data = pack_v3()
    changed_data["process_park"]["FINISH.FITTER"] = {"rate_id": "blank:bench"}
    changed = _validate(changed_data)
    # Парк — данные предприятия: его правка обязана менять отпечаток, иначе
    # PackChanged не увидит смену состава операций.
    assert base.fingerprint != changed.fingerprint


# ── матричная ставка ─────────────────────────────────────────────────────


def test_matrix_boundary_belongs_to_lower_interval() -> None:
    pack = _validate(pack_v3())
    value = pack.rate_value("svc:roll", {"thickness_mm": "1.9", "width_mm": "300"})
    assert value == Decimal("370")


def test_matrix_open_end_and_null_cell() -> None:
    pack = _validate(pack_v3())
    assert pack.rate_value(
        "svc:roll", {"thickness_mm": "1", "width_mm": "5000"}
    ) == Decimal("1110")
    with pytest.raises(InvalidRatePack, match="не оказывается"):
        pack.rate_value("svc:roll", {"thickness_mm": "2.5", "width_mm": "5000"})


def test_matrix_below_minimum_refused() -> None:
    data = pack_v3()
    data["rate_registry"]["svc:roll"]["axes"][0]["edges"] = ["1", "1.9", "2.9"]
    pack = _validate(data)
    with pytest.raises(InvalidRatePack, match="ниже нижней границы"):
        pack.rate_value("svc:roll", {"thickness_mm": "0.5", "width_mm": "300"})


def test_matrix_above_maximum_refused_without_open_end() -> None:
    pack = _validate(pack_v3())
    with pytest.raises(InvalidRatePack, match="выше верхней границы"):
        pack.rate_value("svc:roll", {"thickness_mm": "10", "width_mm": "300"})


def test_matrix_requires_all_axes_and_rejects_extra() -> None:
    pack = _validate(pack_v3())
    with pytest.raises(InvalidRatePack, match="нужны значения осей"):
        pack.rate_value("svc:roll", {"thickness_mm": "1"})
    with pytest.raises(InvalidRatePack, match="Лишние оси"):
        pack.rate_value(
            "svc:roll", {"thickness_mm": "1", "width_mm": "300", "depth_mm": "1"}
        )


def test_matrix_categorical_code_rejects_fractional_value() -> None:
    data = pack_v3()
    data["rate_registry"]["svc:roll"]["axes"][1]["name"] = "width_code"
    pack = _validate(data)

    with pytest.raises(InvalidRatePack, match="целым кодом"):
        pack.rate_value("svc:roll", {"thickness_mm": "1", "width_code": "1.5"})


def test_matrix_edges_must_increase() -> None:
    data = pack_v3()
    data["rate_registry"]["svc:roll"]["axes"][0]["edges"] = ["0", "2", "2"]
    with pytest.raises(InvalidRatePack, match="строго возрастать"):
        _validate(data)


def test_scalar_rate_value() -> None:
    pack = _validate(pack_v3())
    assert pack.rate_value("machine:turning") == Decimal("3000")


# ── validate_pack / автоперенос ─────────────────────────────────────────


def test_validate_pack_accepts_v3_bytes() -> None:
    raw = json.dumps(pack_v3(), ensure_ascii=False).encode("utf-8")
    result = validate_pack(raw)
    assert result["ok"] is True


def test_upgrade_v2_to_v3_produces_valid_draft() -> None:
    raw = json.dumps(pipeline_pack(), ensure_ascii=False).encode("utf-8")
    result = upgrade_pack_v2_to_v3(raw)
    assert result["ok"] is True and result["already_v3"] is False
    pack = result["pack"]
    assert pack["schema_version"] == 3
    assert pack["process_park"]["BLANK.CUTOFF"]["method_code"] == "BAND_SAW"
    assert pack["process_park"]["MACHINING.TURN.CNC"]["rate_id"] == "machine:turning"
    # Черновик обязан проходить строгий валидатор v3 — иначе это не черновик,
    # а ловушка для публикации.
    _validate(pack)
    assert any("drilling" in warning for warning in result["warnings"]) or (
        "drilling" not in pipeline_pack()["machines"]
    )


def test_upgrade_v3_passthrough() -> None:
    raw = json.dumps(pack_v3(), ensure_ascii=False).encode("utf-8")
    result = upgrade_pack_v2_to_v3(raw)
    assert result["already_v3"] is True
