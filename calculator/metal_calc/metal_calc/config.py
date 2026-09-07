from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    cache_root: Path
    orders_root: Path
    delivery_root: Path
    rates_root: Path
    schema_path: Path
    cadkit_path: Path
    image_digest: str
    delivery_public_root: Path = Path("/opt/data/delivery")
    delivery_ttl_seconds: int = 3600
    max_input_bytes: int = 20 * 1024 * 1024
    max_files_per_order: int = 32
    geometry_timeout_seconds: int = 120

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            cache_root=Path(os.environ.get("METAL_CALC_CACHE_ROOT", "/opt/data/cache/documents")),
            orders_root=Path(os.environ.get("METAL_CALC_ORDERS_ROOT", "/opt/data/orders")),
            delivery_root=Path(os.environ.get("METAL_CALC_DELIVERY_ROOT", "/opt/data/delivery")),
            rates_root=Path(os.environ.get("METAL_CALC_RATES_ROOT", "/etc/metal-calc/rates")),
            schema_path=Path(os.environ.get("METAL_CALC_ORDER_SCHEMA", "/etc/metal-calc/order_schema.json")),
            cadkit_path=Path(os.environ.get("METAL_CALC_CADKIT_PATH", "/opt/metal-calc/lib/cadkit.py")),
            image_digest=os.environ.get("METAL_CALC_IMAGE_DIGEST", ""),
            delivery_public_root=Path(
                os.environ.get("METAL_CALC_DELIVERY_PUBLIC_ROOT", "/opt/data/delivery")
            ),
            delivery_ttl_seconds=int(os.environ.get("METAL_CALC_DELIVERY_TTL_SECONDS", "3600")),
        )
