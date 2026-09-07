"""Полнота данных предприятия: чем «завела не всё» отличается от «ошиблась».

Оба случая раньше проходили публикацию одинаково молча, а разница между
ними для человека огромная. Мёртвая запись (норма для станка, которого нет)
— ошибка данных, её надо отвергать. Непокрытая пара «станок × группа» —
законное решение предприятия («нержавейку на эрозии не режем»), её надо
показывать, а не запрещать.

Общее у них одно: без этих проверок и то и другое всплывало на третьей
стадии — после того, как человек утвердил заготовку и маршрут.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from metal_calc.errors import InvalidRatePack
from metal_calc.packs2 import _validate_pack2, norm_coverage

from test_pipeline_v2 import pipeline_pack


def build(data: dict[str, Any]):
    raw = json.dumps(data).encode()
    return _validate_pack2(data, hashlib.sha256(raw).hexdigest(), data["revision"])


def test_norm_for_absent_machine_is_rejected():
    """Мёртвая норма выглядит покрытием, которого нет."""
    data = pipeline_pack()
    del data["machines"]["edm"]
    with pytest.raises(InvalidRatePack) as excinfo:
        build(data)
    assert "Станки" in str(excinfo.value.public_message)


def test_complete_pack_reports_no_gaps():
    assert norm_coverage(build(pipeline_pack())) == {}


def test_new_material_group_without_norms_is_reported_not_blocked():
    """Публикация проходит — но пробел назван поимённо."""
    data = pipeline_pack()
    data["materials"]["st-12x18"] = {
        "grade": "12Х18Н10Т",
        "group": "stainless",
        "stock": "stocked",
        "density_kg_m3": "7900",
        "rate_rub_per_kg": "420",
        "rate_source": data["materials"]["steel-40x"]["rate_source"],
    }
    gaps = norm_coverage(build(data))
    assert set(gaps) == {"turning", "milling", "drilling", "edm"}
    assert all(groups == ["stainless"] for groups in gaps.values())


def test_new_machine_without_norms_is_reported():
    """Станок завели, нормы к нему — нет. Считать по нему нечем."""
    data = pipeline_pack()
    data["machines"]["grinding"] = {
        "rate_rub_per_hour": "2800",
        "rate_source": data["machines"]["turning"]["rate_source"],
    }
    gaps = norm_coverage(build(data))
    assert gaps == {"grinding": ["steel"]}
