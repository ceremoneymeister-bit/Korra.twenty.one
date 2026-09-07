from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from metal_calc.config import Settings
from metal_calc.service import MetalCalcService


class FakeGeometry:
    def analyze(
        self,
        inputs: list[Path],
        *,
        thickness_mm: float,
        density_kg_m3: float,
        workdir: Path,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        return (
            [
                {
                    "file": path.name,
                    "ok": True,
                    "units": "мм",
                    "scale": 1.0,
                    "bbox": [100.0, 100.0],
                    "area_mm2": 10000.0,
                    "outer_area_mm2": 10000.0,
                    "cut_length_mm": 400.0,
                    "pierces": 4,
                    "holes": 0,
                    "contours": 1,
                    "mass_kg": 0.314,
                    "warnings": [],
                    "error": "",
                }
                for path in inputs
            ],
            {"cadkit_version": "test", "ezdxf_version": "1.4.4", "shapely_version": "2.1.2"},
        )


def rate_pack(revision: str = "test-v1", *, vat: bool = True) -> dict[str, Any]:
    source = {
        "kind": "client_canon",
        "ref": "synthetic-test-policy",
        "as_of": "2026-08-13",
    }
    return {
        "schema_version": 1,
        "revision": revision,
        "pricing_policy_revision": "pricing-test-v1",
        "materials": {
            "steel-test-4": {
                "grade": "TEST-S235",
                "thickness_mm": "4",
                "density_kg_m3": "7850",
                "rate_rub_per_kg": "100",
                "rate_source": source,
            }
        },
        "operations": [
            {
                "code": "cut",
                "category": "cut",
                "quantity_from": "cut_length_m",
                "rate_rub": "10",
                "rate_source": source,
            },
            {
                "code": "pierce",
                "category": "pierce",
                "quantity_from": "pierces",
                "rate_rub": "1",
                "rate_source": source,
            },
        ],
        "offcut": {"kind": "percent_of_area", "percent": "10", "source": source},
        "pricing": {
            "margin_basis": "on_cost",
            "margin_percent": "20",
            "vat_included": vat,
            "vat_rate_pct": "20" if vat else "0",
            "valid_days": 14,
            "rounding": "none",
        },
    }


@pytest.fixture
def service(tmp_path: Path) -> MetalCalcService:
    cache = tmp_path / "cache"
    orders = tmp_path / "orders"
    delivery = tmp_path / "delivery"
    rates = tmp_path / "rates"
    for path in (cache, orders, delivery, rates):
        path.mkdir()
    (rates / "test-v1.json").write_text(json.dumps(rate_pack()), encoding="utf-8")
    (rates / "test-v2.json").write_text(json.dumps(rate_pack("test-v2")), encoding="utf-8")
    calculator_root = Path(__file__).resolve().parents[2]
    settings = Settings(
        cache_root=cache,
        orders_root=orders,
        delivery_root=delivery,
        rates_root=rates,
        schema_path=calculator_root / "review" / "order_schema.json",
        cadkit_path=calculator_root / "lib" / "cadkit.py",
        image_digest="sha256:" + "0" * 64,
        delivery_public_root=Path("/opt/data/delivery"),
    )
    instance = MetalCalcService(settings, geometry_adapter=FakeGeometry())
    yield instance
    instance.close()


@pytest.fixture
def prepared_order(service: MetalCalcService) -> tuple[MetalCalcService, str, str]:
    order_id = "order-test-1"
    service.order_upsert(order_id, 0, {"customer": {"name": "Synthetic Customer"}})
    cache_name = "doc_0123456789ab_part.dxf"
    (service.settings.cache_root / cache_name).write_bytes(b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n")
    source = service.ingest_attachment(order_id, cache_name)
    current = service.order_get(order_id)
    proposed = service.order_upsert(
        order_id,
        current["revision"],
        {
            "manual_facts_propose": [
                {
                    "code": "part_quantity",
                    "target_source_file_id": source["source_file_id"],
                    "value": 3,
                    "unit": "pcs",
                    "evidence_ref": "telegram:synthetic-message-1",
                }
            ]
        },
    )
    fact_id = proposed["fact_proposals"][0]["fact_id"]
    service.approve_fact(order_id, fact_id, "operator:test")
    return service, order_id, source["source_file_id"]
