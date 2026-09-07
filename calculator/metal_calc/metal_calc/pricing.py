from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN, ROUND_HALF_UP
from typing import Any

from .rates import RatePack
from .util import public_number

CENT = Decimal("0.01")
HUNDRED = Decimal("100")


def qmoney(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def calculate(
    geometry: dict[str, Any],
    mass: dict[str, Any],
    pack: RatePack,
    material_code: str,
    *,
    quantity_source: dict[str, str] | None = None,
) -> dict[str, Any]:
    material = pack.material(material_code)
    quantities = _quantities(geometry, mass, material)
    offcut_multiplier = Decimal("1") + pack.offcut["percent"] / HUNDRED
    material_quantity = quantities["mass_kg"] * offcut_multiplier
    material_line = _line(
        code=f"material:{material_code}",
        category="material",
        quantity=material_quantity,
        unit="kg",
        rate=material["rate_rub_per_kg"],
        rate_source=material["rate_source"],
        quantity_ref=f"geometry:{geometry['revision']}:mass+offcut",
        quantity_source=quantity_source,
    )
    operations = []
    for operation in pack.operations:
        operations.append(
            _line(
                code=operation["code"],
                category=operation["category"],
                quantity=quantities[operation["quantity_from"]],
                unit=operation["unit"],
                rate=operation["rate_rub"],
                rate_source=operation["rate_source"],
                quantity_ref=f"geometry:{geometry['revision']}:{operation['quantity_from']}",
                quantity_source=quantity_source,
            )
        )
    lines = [material_line, *operations]
    material_total = Decimal(str(material_line["subtotal_rub"]))
    logistics_total = sum(
        (Decimal(str(line["subtotal_rub"])) for line in operations if line["category"] == "logistics"),
        Decimal("0"),
    )
    operations_total = sum(
        (Decimal(str(line["subtotal_rub"])) for line in operations if line["category"] != "logistics"),
        Decimal("0"),
    )
    cost_total = qmoney(sum((Decimal(str(line["subtotal_rub"])) for line in lines), Decimal("0")))
    pricing = pack.pricing
    percent = pricing["margin_percent"]
    if pricing["margin_basis"] == "on_cost":
        net_price = cost_total * (Decimal("1") + percent / HUNDRED)
    else:
        net_price = cost_total / (Decimal("1") - percent / HUNDRED)
    price = net_price
    if pricing["vat_included"]:
        price *= Decimal("1") + pricing["vat_rate_pct"] / HUNDRED
    price = _round_price(price, pricing["rounding"])
    # Margin is economic margin on the net-of-VAT selling price. VAT is a tax
    # liability and must not inflate margin. Rounding of the public gross price
    # is reflected back into the exact net amount.
    effective_net_price = (
        price / (Decimal("1") + pricing["vat_rate_pct"] / HUNDRED)
        if pricing["vat_included"]
        else price
    )
    margin_absolute = qmoney(effective_net_price - cost_total)
    effective_net_price = qmoney(effective_net_price)
    vat_amount = qmoney(price - effective_net_price)
    if pricing["margin_basis"] == "on_cost":
        actual_margin = Decimal("0") if cost_total == 0 else margin_absolute / cost_total * HUNDRED
    else:
        actual_margin = (
            Decimal("0") if effective_net_price == 0 else margin_absolute / effective_net_price * HUNDRED
        )
    cost = {
        "lines": lines,
        "material_rub": public_number(qmoney(material_total)),
        "operations_rub": public_number(qmoney(operations_total)),
        "logistics_rub": public_number(qmoney(logistics_total)),
        "total_rub": public_number(cost_total),
        "rounding": "none",
    }
    price_out = {
        "net_total_rub": public_number(effective_net_price),
        "vat_amount_rub": public_number(vat_amount),
        "total_rub": public_number(price),
        "currency": "RUB",
        "vat_included": pricing["vat_included"],
        "vat_rate_pct": public_number(pricing["vat_rate_pct"]),
        "valid_until": (date.today() + timedelta(days=pricing["valid_days"])).isoformat(),
    }
    margin = {
        "absolute_rub": public_number(margin_absolute),
        "percent": public_number(actual_margin.quantize(CENT, rounding=ROUND_HALF_EVEN)),
        "basis": pricing["margin_basis"],
    }
    return {
        "operations": operations,
        "cost": cost,
        "price": price_out,
        "margin": margin,
        "_effective_mass_kg": public_number(quantities["mass_kg"]),
    }


def _quantities(
    geometry: dict[str, Any], mass: dict[str, Any], material: dict[str, Any]
) -> dict[str, Decimal]:
    parts = geometry["parts"]
    computed_mass = (
        sum(
            (Decimal(str(p["area_mm2"])) * Decimal(p["quantity"]) for p in parts),
            Decimal("0"),
        )
        * material["thickness_mm"]
        * material["density_kg_m3"]
        / Decimal("1000000000")
    )
    return {
        "mass_kg": computed_mass,
        "cut_length_m": sum(
            (Decimal(str(p["cut_length_mm"])) * Decimal(p["quantity"]) for p in parts), Decimal("0")
        )
        / Decimal("1000"),
        "pierces": sum((Decimal(p["pierces"] * p["quantity"]) for p in parts), Decimal("0")),
        "net_area_m2": sum(
            (Decimal(str(p["area_mm2"])) * Decimal(p["quantity"]) for p in parts), Decimal("0")
        )
        / Decimal("1000000"),
        "outer_area_m2": sum(
            (Decimal(str(p["outer_area_mm2"])) * Decimal(p["quantity"]) for p in parts), Decimal("0")
        )
        / Decimal("1000000"),
        "parts_pcs": sum((Decimal(p["quantity"]) for p in parts), Decimal("0")),
    }


def _line(
    *,
    code: str,
    category: str,
    quantity: Decimal,
    unit: str,
    rate: Decimal,
    rate_source: dict[str, str],
    quantity_ref: str,
    quantity_source: dict[str, str] | None,
) -> dict[str, Any]:
    subtotal = qmoney(quantity * rate)
    return {
        "code": code,
        "category": category,
        "quantity": public_number(quantity),
        "unit": unit,
        "quantity_source": (
            {
                **quantity_source,
                "ref": f"{quantity_source['ref']};metric={quantity_ref}",
            }
            if quantity_source
            else {"kind": "client_canon", "ref": quantity_ref}
        ),
        "rate_rub": public_number(rate),
        "rate_source": rate_source,
        "subtotal_rub": public_number(subtotal),
    }


def _round_price(value: Decimal, mode: str) -> Decimal:
    if mode == "half_up":
        # Обычное коммерческое округление до рубля: ровно 50 копеек всегда
        # увеличивает модуль суммы. Политика отдельная от bankers, чтобы выбор
        # методолога был воспроизводим на граничных значениях.
        return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if mode == "bankers":
        # Банковское округление до рубля: половина уходит к чётному. Раньше
        # эта ветка совпадала с «none» — две опции политики давали один
        # результат, и методолог, выбравшая «банковское», получала обычное.
        return value.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    if mode == "none":
        return qmoney(value)
    step = Decimal("10") if mode == "up_10" else Decimal("100")
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step
