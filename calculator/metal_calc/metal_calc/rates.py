from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import InvalidRatePack, NotFound
from .securefs import SecureRoot
from .util import sha256_bytes, validate_revision

QUANTITY_SOURCES = {
    "mass_kg": "kg",
    "cut_length_m": "m",
    "pierces": "pcs",
    "net_area_m2": "m2",
    "outer_area_m2": "m2",
    "parts_pcs": "pcs",
}
ROUNDING = {"none", "up_10", "up_100", "bankers", "half_up"}
OPERATION_CATEGORIES = {
    "cut",
    "pierce",
    "bend",
    "weld",
    "paint",
    "galvanize",
    "machining",
    "assembly",
    "logistics",
    "other",
}


def dec(value: Any, *, field: str, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise InvalidRatePack(f"Invalid {field}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidRatePack(f"Invalid {field}") from exc
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        raise InvalidRatePack(f"Invalid {field}")
    return result


@dataclass(frozen=True)
class RatePack:
    revision: str
    sha256: str
    pricing_policy_revision: str
    materials: dict[str, dict[str, Any]]
    operations: tuple[dict[str, Any], ...]
    offcut: dict[str, Any]
    pricing: dict[str, Any]

    def material(self, code: str) -> dict[str, Any]:
        try:
            return self.materials[code]
        except KeyError as exc:
            raise InvalidRatePack("Unknown material code") from exc


class RatePackStore:
    def __init__(self, root: SecureRoot) -> None:
        self.root = root

    def load(self, revision: str) -> RatePack:
        revision = validate_revision(revision)
        try:
            raw = self.root.read_bytes(f"{revision}.json", limit=1024 * 1024)
        except (NotFound, ValueError) as exc:
            raise InvalidRatePack("Rate pack unavailable") from exc
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidRatePack("Rate pack is not valid JSON") from exc
        return _validate_pack(data, sha256_bytes(raw), revision)


def _validate_pack(data: Any, digest: str, requested_revision: str) -> RatePack:
    if not isinstance(data, dict):
        raise InvalidRatePack("Rate pack must be an object")
    allowed = {
        "schema_version",
        "revision",
        "pricing_policy_revision",
        "materials",
        "operations",
        "offcut",
        "pricing",
    }
    if set(data) != allowed or data.get("schema_version") != 1 or data.get("revision") != requested_revision:
        raise InvalidRatePack("Rate pack schema or revision mismatch")
    if not isinstance(data.get("pricing_policy_revision"), str) or not data["pricing_policy_revision"]:
        raise InvalidRatePack("Missing pricing policy revision")
    materials = data.get("materials")
    if not isinstance(materials, dict) or not materials:
        raise InvalidRatePack("No materials configured")
    normalized_materials: dict[str, dict[str, Any]] = {}
    for code, material in materials.items():
        if not isinstance(code, str) or not isinstance(material, dict):
            raise InvalidRatePack("Invalid material")
        if set(material) != {"grade", "thickness_mm", "density_kg_m3", "rate_rub_per_kg", "rate_source"}:
            raise InvalidRatePack("Invalid material fields")
        normalized_materials[code] = {
            "grade": str(material["grade"]),
            "thickness_mm": dec(material["thickness_mm"], field="thickness_mm", positive=True),
            "density_kg_m3": dec(material["density_kg_m3"], field="density_kg_m3", positive=True),
            "rate_rub_per_kg": dec(
                material["rate_rub_per_kg"], field="rate_rub_per_kg", positive=True
            ),
            "rate_source": _source(material["rate_source"]),
        }
    operations = data.get("operations")
    if not isinstance(operations, list):
        raise InvalidRatePack("Operations must be a list")
    normalized_operations = []
    seen = set()
    for item in operations:
        expected = {"code", "category", "quantity_from", "rate_rub", "rate_source"}
        if not isinstance(item, dict) or set(item) != expected:
            raise InvalidRatePack("Invalid operation fields")
        quantity_from = item["quantity_from"]
        if quantity_from not in QUANTITY_SOURCES:
            raise InvalidRatePack("Unsupported operation quantity")
        code = str(item["code"])
        if item["category"] not in OPERATION_CATEGORIES:
            raise InvalidRatePack("Unsupported operation category")
        if not code or code in seen:
            raise InvalidRatePack("Duplicate operation code")
        seen.add(code)
        normalized_operations.append(
            {
                "code": code,
                "category": str(item["category"]),
                "quantity_from": quantity_from,
                "unit": QUANTITY_SOURCES[quantity_from],
                "rate_rub": dec(item["rate_rub"], field="operation rate", positive=True),
                "rate_source": _source(item["rate_source"]),
            }
        )
    offcut = data.get("offcut")
    if not isinstance(offcut, dict) or set(offcut) != {"kind", "percent", "source"}:
        raise InvalidRatePack("Invalid offcut model")
    if offcut["kind"] != "percent_of_area":
        raise InvalidRatePack("Unsupported offcut model")
    offcut_percent = dec(offcut["percent"], field="offcut percent")
    if offcut_percent > 100:
        raise InvalidRatePack("Invalid offcut percent")
    normalized_offcut = {
        "kind": "percent_of_area",
        "percent": offcut_percent,
        "source": _source(offcut["source"]),
    }
    pricing = data.get("pricing")
    expected_pricing = {"margin_basis", "margin_percent", "vat_included", "vat_rate_pct", "valid_days", "rounding"}
    if not isinstance(pricing, dict) or set(pricing) != expected_pricing:
        raise InvalidRatePack("Invalid pricing policy")
    if pricing["margin_basis"] not in {"on_cost", "on_price"}:
        raise InvalidRatePack("Invalid margin basis")
    if pricing["rounding"] not in ROUNDING:
        raise InvalidRatePack("Invalid rounding policy")
    valid_days = pricing["valid_days"]
    if not isinstance(valid_days, int) or isinstance(valid_days, bool) or valid_days < 1 or valid_days > 365:
        raise InvalidRatePack("Invalid quote validity")
    normalized_pricing = {
        "margin_basis": pricing["margin_basis"],
        "margin_percent": dec(pricing["margin_percent"], field="margin percent"),
        "vat_included": pricing["vat_included"],
        "vat_rate_pct": dec(pricing["vat_rate_pct"], field="VAT rate"),
        "valid_days": valid_days,
        "rounding": pricing["rounding"],
    }
    if type(pricing["vat_included"]) is not bool:
        raise InvalidRatePack("vat_included must be a boolean")
    if normalized_pricing["vat_rate_pct"] > 100:
        raise InvalidRatePack("VAT rate must be between 0 and 100")
    if not normalized_pricing["vat_included"] and normalized_pricing["vat_rate_pct"] != 0:
        raise InvalidRatePack("VAT rate must be zero when VAT is not included")
    if normalized_pricing["margin_basis"] == "on_price" and normalized_pricing["margin_percent"] >= 100:
        raise InvalidRatePack("on_price margin must be below 100 percent")
    return RatePack(
        revision=requested_revision,
        sha256=digest,
        pricing_policy_revision=data["pricing_policy_revision"],
        materials=normalized_materials,
        operations=tuple(normalized_operations),
        offcut=normalized_offcut,
        pricing=normalized_pricing,
    )


def _source(value: Any) -> dict[str, str]:
    # Сообщения по-русски и с составом полей: панель показывает их методологу
    # дословно, и «Invalid rate source» не говорит, какое поле забыто.
    if not isinstance(value, dict) or set(value) != {"kind", "ref", "as_of"}:
        raise InvalidRatePack(
            "У каждой цифры обязателен источник из трёх полей: kind (вид), "
            "ref (документ: кто и когда утвердил) и as_of (дата ГГГГ-ММ-ДД)"
        )
    kind = value.get("kind")
    if kind not in {"client_canon", "supplier_quote"}:
        raise InvalidRatePack(
            "Вид источника ставки — client_canon (утверждённый прайс/канон "
            "предприятия) или supplier_quote (счёт поставщика). Выдача модели "
            "и «примерно так» источником не являются."
        )
    if (
        not isinstance(value.get("ref"), str)
        or not value["ref"].strip()
        or len(value["ref"]) > 256
    ):
        raise InvalidRatePack(
            "В источнике пусто поле ref: назовите документ — «прайс, утв. "
            "директором 01.08.2026» или «счёт №4412 от 25.08.2026»"
        )
    if not isinstance(value.get("as_of"), str) or not value["as_of"].strip():
        raise InvalidRatePack("В источнике пусто поле as_of — дата в формате ГГГГ-ММ-ДД")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value["as_of"]) is None:
        raise InvalidRatePack("Дата источника as_of должна быть в формате ГГГГ-ММ-ДД")
    try:
        date.fromisoformat(value["as_of"])
    except ValueError as exc:
        raise InvalidRatePack("Дата источника as_of должна быть в формате ГГГГ-ММ-ДД") from exc
    out = {"kind": kind, "ref": value["ref"].strip(), "as_of": value["as_of"].strip()}
    return out
