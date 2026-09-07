from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from . import __version__
from .config import Settings
from .errors import (
    Conflict,
    FileTooLarge,
    InvalidIdentifier,
    InvalidRatePack,
    InvalidState,
    NotFound,
    UnsupportedFormat,
)
from .geometry import CadkitAdapter
from .pricing import calculate
from .rates import RatePackStore
from .registry import Registry
from .securefs import SecureRoot
from .util import SHA256_RE, digest_json, sha256_bytes, utcnow, validate_id
from .xlsx import render_quote

_CACHE_PREFIX_RE = re.compile(r"^doc_[0-9a-f]{12}_(.+)$", re.DOTALL)
_IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_STATUS = {"received", "analyzing", "needs_human", "calculated", "quoted", "won", "lost", "cancelled"}
_MODEL_STATUS_TRANSITIONS = {
    "quoted": {"won", "lost", "cancelled"},
    "received": {"cancelled"},
    "analyzing": {"cancelled"},
    "needs_human": {"cancelled"},
    "calculated": {"cancelled"},
}


class MetalCalcService:
    def __init__(self, settings: Settings, *, geometry_adapter: Any | None = None) -> None:
        if not _IMAGE_DIGEST_RE.fullmatch(settings.image_digest):
            raise RuntimeError("METAL_CALC_IMAGE_DIGEST must be an immutable sha256 digest")
        self.settings = settings
        self.cache = SecureRoot(settings.cache_root, writable=False)
        self.orders = SecureRoot(settings.orders_root, writable=True)
        self.delivery = SecureRoot(settings.delivery_root, writable=True)
        self.rates_root = SecureRoot(settings.rates_root, writable=False)
        self.rate_packs = RatePackStore(self.rates_root)
        self.registry = Registry(settings.orders_root / "registry.db")
        self.geometry_adapter = geometry_adapter or CadkitAdapter(
            settings.cadkit_path, settings.geometry_timeout_seconds
        )
        self.schema = _load_trusted_json(settings.schema_path)
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())

    def close(self) -> None:
        self.cache.close()
        self.orders.close()
        self.delivery.close()
        self.rates_root.close()

    def order_upsert(
        self, order_id: str, expected_revision: int, patch: dict[str, Any]
    ) -> dict[str, Any]:
        validate_id(order_id, field="order_id")
        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise InvalidState("expected_revision must be a non-negative integer")
        if not isinstance(patch, dict) or not set(patch).issubset(
            {"customer", "status", "manual_facts_propose"}
        ):
            raise InvalidState("Patch contains server-managed or unknown fields")
        if expected_revision == 0:
            if set(patch) != {"customer"}:
                raise InvalidState("New order requires only customer")
            customer = _customer(patch["customer"])
            state = {
                "order_id": order_id,
                "customer": customer,
                "source_files": [],
                "status": "received",
                "warnings": [],
                "timestamps": {},
                "provenance": {
                    "image_digest": self.settings.image_digest,
                    "metal_calc_version": __version__,
                    "computed_by": "metal_calc_mcp",
                },
            }
            revision, state = self.registry.create(order_id, state)
            return self._order_response(revision, state)
        if "manual_facts_propose" in patch:
            if set(patch) != {"manual_facts_propose"}:
                raise InvalidState("Fact proposals cannot be combined with other updates")
            current_revision, current = self.registry.get(order_id)
            if current_revision != expected_revision:
                raise Conflict("Order revision conflict")
            if current["status"] in {"calculated", "quoted", "won", "lost", "cancelled"}:
                raise InvalidState("Facts cannot be proposed after calculation or closure")
            additions = patch["manual_facts_propose"]
            if not isinstance(additions, list) or not additions or len(additions) > 32:
                raise InvalidState("manual_facts_propose must contain 1..32 proposals")
            known_sources = {item["source_file_id"] for item in current["source_files"]}
            proposals = [
                _part_quantity_proposal(item, known_sources, order_id=order_id)
                for item in additions
            ]
            revision, state, saved = self.registry.propose_facts(
                order_id, expected_revision, proposals
            )
            response = self._order_response(revision, state)
            response["fact_proposals"] = saved
            return response

        def mutate(state: dict[str, Any]) -> None:
            if "customer" in patch:
                requested_customer = _customer(patch["customer"])
                if (
                    (state.get("source_files") or state["status"] != "received")
                    and requested_customer != state["customer"]
                ):
                    raise InvalidState("Customer is immutable after order intake starts")
                state["customer"] = requested_customer
            if "status" in patch:
                requested = patch["status"]
                if requested not in _STATUS or requested not in _MODEL_STATUS_TRANSITIONS.get(state["status"], set()):
                    raise InvalidState("Status transition is not allowed")
                state["status"] = requested
                if requested in {"won", "lost", "cancelled"}:
                    state["timestamps"]["closed_at"] = utcnow()

        revision, state = self.registry.mutate(
            order_id,
            mutate,
            expected_revision=expected_revision,
            validator=self._validate_if_complete,
        )
        self._validate_if_complete(state)
        return self._order_response(revision, state)

    def ingest_attachment(self, order_id: str, cache_name: str) -> dict[str, Any]:
        validate_id(order_id, field="order_id")
        original_name = _validate_cache_name(cache_name)
        revision, order = self.registry.get(order_id)
        if len(order["source_files"]) >= self.settings.max_files_per_order:
            raise InvalidState("Order file limit exceeded")
        fd = self.cache.open_read_fd(cache_name)
        try:
            info = os.fstat(fd)
            if info.st_size < 1:
                raise UnsupportedFormat("Empty file")
            if info.st_size > self.settings.max_input_bytes:
                raise FileTooLarge("Input exceeds 20 MiB")
            head = os.read(fd, min(4096, info.st_size))
            file_format = _detect_format(original_name, head)
            os.lseek(fd, 0, os.SEEK_SET)
            digest = _hash_fd(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            for source in order["source_files"]:
                if source["sha256"] == digest:
                    return {**source, "original_name": source["name"], "idempotent": True}
            if isinstance(order.get("workflow"), dict):
                raise InvalidState(
                    "Вход V9 уже зафиксирован: новый файл нельзя тихо добавить "
                    "в текущую ревизию расчёта"
                )
            source_id = f"src_{digest[:24]}"
            extension = file_format
            relative = f"{order_id}/in/{source_id}.{extension}"
            self.orders.ensure_dir(f"{order_id}/in")
            with os.fdopen(fd, "rb", closefd=False) as source_stream:
                try:
                    copied_bytes, copied_digest = self.orders.atomic_copy(relative, source_stream)
                except Conflict:
                    _verify_published_file(
                        self.orders, relative, expected_bytes=info.st_size, expected_sha256=digest
                    )
                    copied_bytes, copied_digest = info.st_size, digest
            if copied_bytes != info.st_size or copied_digest != digest:
                raise InvalidState("Ingest digest mismatch")
        finally:
            os.close(fd)
        source_record = {
            "source_file_id": source_id,
            "name": original_name,
            "sha256": digest,
            "format": file_format,
            "bytes": info.st_size,
            "received_at": utcnow(),
        }

        def add_source(state: dict[str, Any]) -> None:
            if any(item["sha256"] == digest for item in state["source_files"]):
                return
            if isinstance(state.get("workflow"), dict):
                raise InvalidState(
                    "Вход V9 уже зафиксирован: новый файл нельзя тихо добавить "
                    "в текущую ревизию расчёта"
                )
            if len(state["source_files"]) >= self.settings.max_files_per_order:
                raise InvalidState("Order file limit exceeded")
            state["source_files"].append(source_record)

        _, updated = self.registry.mutate(
            order_id,
            add_source,
            expected_revision=revision,
            validator=self._validate_if_complete,
        )
        self._validate_if_complete(updated)
        return {**source_record, "original_name": original_name, "idempotent": False}

    def analyze_drawing(
        self,
        order_id: str,
        source_file_ids: list[str],
        units_hint: str | None,
        rates_revision: str,
        material_code: str | None,
    ) -> dict[str, Any]:
        validate_id(order_id, field="order_id")
        if units_hint is not None:
            raise InvalidState("units_hint is not trusted in v1; operator-approved units are required")
        if not source_file_ids or len(source_file_ids) > self.settings.max_files_per_order:
            raise InvalidState("source_file_ids must contain 1..32 items")
        if len(set(source_file_ids)) != len(source_file_ids):
            raise InvalidState("Duplicate source_file_id")
        revision, order = self.registry.get(order_id)
        pack = self.rate_packs.load(rates_revision)
        if material_code is None:
            warning = _warning("missing_material", "stop", "Material is required for deterministic mass")
            return {"geometry": None, "mass": None, "warnings": [warning], "abstain": True}
        material = pack.material(material_code)
        sources_by_id = {source["source_file_id"]: source for source in order["source_files"]}
        try:
            sources = [sources_by_id[source_id] for source_id in source_file_ids]
        except KeyError as exc:
            raise NotFound("Source file not found in order") from exc
        input_sha = digest_json([source["sha256"] for source in sources])
        warnings: list[dict[str, Any]] = []
        raw_results: list[dict[str, Any]] = []
        versions: dict[str, str] = {}
        drawing_sources = [source for source in sources if source["format"] in {"dxf", "dwg"}]
        if len(drawing_sources) != len(sources):
            warnings.append(
                _warning("text_only_geometry", "stop", "PDF geometry is not supported in v1")
            )
        if drawing_sources:
            with tempfile.TemporaryDirectory(prefix="metal-calc-") as temp_name:
                temp = Path(temp_name)
                inputs: list[Path] = []
                for source in drawing_sources:
                    target = temp / f"{source['source_file_id']}.{source['format']}"
                    source_fd = self.orders.open_read_fd(
                        f"{order_id}/in/{source['source_file_id']}.{source['format']}"
                    )
                    try:
                        with os.fdopen(source_fd, "rb", closefd=False) as inp, target.open("xb") as out:
                            while chunk := inp.read(1024 * 1024):
                                out.write(chunk)
                    finally:
                        os.close(source_fd)
                    inputs.append(target)
                workdir = temp / "work"
                workdir.mkdir(mode=0o700)
                raw_results, versions = self.geometry_adapter.analyze(
                    inputs,
                    thickness_mm=float(material["thickness_mm"]),
                    density_kg_m3=float(material["density_kg_m3"]),
                    workdir=workdir,
                )
        parts: list[dict[str, Any]] = []
        total_mass = 0.0
        for source, raw in zip(drawing_sources, raw_results, strict=True):
            part, part_warnings, part_mass = _normalize_cadkit(source, raw)
            warnings.extend(part_warnings)
            if part is not None:
                parts.append(part)
                total_mass += part_mass
        if not parts:
            warnings.append(_warning("geometry_unverified", "stop", "No verified geometry produced"))
        abstain = any(warning["severity"] == "stop" for warning in warnings)
        geometry_seed = {
            "input_sha256": input_sha,
            "parts": parts,
            "confidence": "deterministic" if parts and not abstain else ("text_only" if sources else "unknown"),
            "material_code": material_code,
            "rates_revision": rates_revision,
            "rates_sha256": pack.sha256,
        }
        geometry_revision = f"geo_{digest_json(geometry_seed)[:24]}"
        geometry = {
            "revision": geometry_revision,
            "input_sha256": input_sha,
            "parts": parts,
            "confidence": geometry_seed["confidence"],
            "analyzed_at": utcnow(),
        }
        mass = {"computed_kg": round(total_mass, 4)}

        def store(state: dict[str, Any]) -> None:
            state["geometry"] = geometry
            state["mass"] = mass
            state["material"] = {
                "code": material_code,
                "grade": material["grade"],
                "thickness_mm": float(material["thickness_mm"]),
                "density_kg_m3": float(material["density_kg_m3"]),
            }
            for field in ("operations", "calculation", "cost", "price", "margin", "artifacts"):
                state.pop(field, None)
            state["warnings"] = warnings
            state["status"] = "needs_human" if abstain else "analyzing"
            state["provenance"].update(
                {
                    "cadkit_version": versions.get("cadkit_version", "unknown"),
                    "ezdxf_version": versions.get("ezdxf_version", "unknown"),
                    "shapely_version": versions.get("shapely_version", "unknown"),
                    "rates_revision": rates_revision,
                    "analysis_rates_sha256": pack.sha256,
                }
            )

        _, stored = self.registry.mutate(
            order_id,
            store,
            expected_revision=revision,
            validator=self._validate_if_complete,
        )
        self._validate_if_complete(stored)
        return {
            "geometry": geometry,
            "mass": mass,
            "warnings": warnings,
            "abstain": abstain,
            "engine_versions": versions,
            "input_sha256": input_sha,
        }

    def calculate_quote(
        self,
        order_id: str,
        geometry_revision: str,
        rates_revision: str,
        material_code: str,
        approved_manual_fact_ids: list[str],
    ) -> dict[str, Any]:
        validate_id(order_id, field="order_id")
        if len(set(approved_manual_fact_ids)) != len(approved_manual_fact_ids):
            raise InvalidState("Duplicate approved_manual_fact_id")
        revision, order = self.registry.get(order_id)
        geometry = order.get("geometry")
        if geometry is None or geometry.get("revision") != geometry_revision:
            raise Conflict("Geometry revision mismatch")
        if (order.get("material") or {}).get("code") != material_code:
            raise Conflict("Material selection mismatch")
        if order.get("provenance", {}).get("rates_revision") != rates_revision:
            raise Conflict("Rate pack revision differs from geometry analysis")
        stop_warnings = [warning for warning in order["warnings"] if warning["severity"] == "stop"]
        if geometry.get("confidence") != "deterministic" or stop_warnings:
            return {
                "calculation": None,
                "operations": [],
                "cost": None,
                "price": None,
                "margin": None,
                "warnings": stop_warnings,
                "abstain": True,
                "calculation_sha256": None,
            }
        if order.get("calculation"):
            saved_ids = sorted(order["calculation"].get("applied_manual_fact_ids", []))
            if saved_ids != sorted(approved_manual_fact_ids):
                raise Conflict("Order was already calculated with a different quantity snapshot")
            bundle = {
                key: order[key]
                for key in ("calculation", "operations", "cost", "price", "margin")
            }
            return {
                **bundle,
                "warnings": [],
                "abstain": False,
                "calculation_sha256": _calculation_digest(bundle),
                "idempotent": True,
            }
        try:
            pack = self.rate_packs.load(rates_revision)
            if order.get("provenance", {}).get("analysis_rates_sha256") != pack.sha256:
                raise Conflict("Rate pack content changed after geometry analysis")
            material = pack.material(material_code)
            stored_material = order["material"]
            if (
                str(material["grade"]) != str(stored_material["grade"])
                or float(material["thickness_mm"]) != float(stored_material["thickness_mm"])
                or float(material["density_kg_m3"]) != float(stored_material["density_kg_m3"])
            ):
                raise Conflict("Material definition changed after geometry analysis")
            pricing_geometry = deepcopy(geometry)
            facts_by_id = {fact["fact_id"]: fact for fact in order.get("manual_facts", [])}
            approved_facts = []
            for fact_id in approved_manual_fact_ids:
                if fact_id in facts_by_id:
                    approved_facts.append(facts_by_id[fact_id])
                    continue
                try:
                    status, _ = self.registry.fact_status(order_id, fact_id)
                except NotFound as exc:
                    raise InvalidState("Approved manual fact not found") from exc
                if status == "pending":
                    warning = _warning(
                        "missing_quantity", "stop", "Quantity fact is pending operator approval"
                    )
                    return {
                        "calculation": None,
                        "operations": [],
                        "cost": None,
                        "price": None,
                        "margin": None,
                        "warnings": [warning],
                        "abstain": True,
                        "calculation_sha256": None,
                    }
            quantities: dict[str, int] = {}
            applied_fact_ids: list[str] = []
            part_refs = {part["part_ref"] for part in pricing_geometry["parts"]}
            for fact in approved_facts:
                prefix = "part_quantity:"
                if not fact["code"].startswith(prefix):
                    raise InvalidState("Unsupported manual fact")
                source_id = fact["code"][len(prefix) :]
                if source_id not in part_refs:
                    warning = _warning(
                        "missing_quantity", "stop", "Quantity fact does not target calculated geometry"
                    )
                    return _abstain_calculation(warning)
                if source_id in quantities:
                    raise InvalidState("More than one quantity fact selected for a part")
                quantities[source_id] = int(fact["value"])
                applied_fact_ids.append(fact["fact_id"])
            missing_part_refs = sorted(part_refs - quantities.keys())
            if missing_part_refs or len(quantities) != len(part_refs):
                warning = _warning(
                    "missing_quantity",
                    "stop",
                    "Each priced part requires exactly one operator-approved RFQ quantity",
                )
                return _abstain_calculation(warning)
            for part in pricing_geometry["parts"]:
                if part["part_ref"] in quantities:
                    part["quantity"] = quantities[part["part_ref"]]
            manual_source = (
                {
                    "kind": "manual_override",
                    "ref": (
                        f"geometry:{geometry_revision};"
                        f"facts_sha256:{digest_json(sorted(applied_fact_ids))}"
                    ),
                }
                if applied_fact_ids
                else None
            )
            priced = calculate(
                pricing_geometry,
                order["mass"],
                pack,
                material_code,
                quantity_source=manual_source,
            )
        except InvalidRatePack:
            warning = _warning("missing_rate", "stop", "Required rate pack or rate is unavailable")
            return {
                "calculation": None,
                "operations": [],
                "cost": None,
                "price": None,
                "margin": None,
                "warnings": [warning],
                "abstain": True,
                "calculation_sha256": None,
            }
        effective_mass = priced.pop("_effective_mass_kg")
        stable_output = {
            "geometry_revision": geometry_revision,
            "rates_revision": pack.revision,
            **priced,
        }
        output_sha = digest_json(stable_output)
        calculation = {
            "engine": "metal_calc_mcp",
            "geometry_revision": geometry_revision,
            "rates_revision": pack.revision,
            "rates_sha256": pack.sha256,
            "pricing_policy_revision": pack.pricing_policy_revision,
            "input_sha256": digest_json(
                {
                    "geometry_input_sha256": geometry["input_sha256"],
                    "approved_manual_fact_ids": sorted(applied_fact_ids),
                }
            ),
            "output_sha256": output_sha,
            "decimal_places": 2,
            "offcut_model": {
                "kind": pack.offcut["kind"],
                "percent": float(pack.offcut["percent"]),
                "source": pack.offcut["source"],
            },
            "effective_quantities": [
                {
                    "part_ref": part["part_ref"],
                    "quantity": part["quantity"],
                    "fact_id": next(
                        fact["fact_id"]
                        for fact in approved_facts
                        if fact["code"] == f"part_quantity:{part['part_ref']}"
                    ),
                }
                for part in pricing_geometry["parts"]
            ],
            "effective_mass_kg": effective_mass,
            "applied_manual_fact_ids": sorted(applied_fact_ids),
            "calculated_at": utcnow(),
            "abstain": False,
        }
        bundle = {"calculation": calculation, **priced}
        calculation_sha = _calculation_digest(bundle)

        def store(state: dict[str, Any]) -> None:
            state.update(bundle)
            state["status"] = "calculated"
            state["provenance"]["rates_revision"] = pack.revision
            state.pop("artifacts", None)

        _, stored = self.registry.mutate(
            order_id,
            store,
            expected_revision=revision,
            validator=self._validate_order,
        )
        return {**bundle, "warnings": [], "abstain": False, "calculation_sha256": calculation_sha}

    def render_quote_xlsx(
        self, order_id: str, calculation_sha256: str, template: str = "default"
    ) -> dict[str, Any]:
        validate_id(order_id, field="order_id")
        if template != "default" or not SHA256_RE.fullmatch(calculation_sha256):
            raise InvalidState("Invalid render request")
        revision, order = self.registry.get(order_id)
        required = ("calculation", "operations", "cost", "price", "margin")
        if order.get("status") not in {"calculated", "quoted"} or not all(order.get(key) is not None for key in required):
            raise InvalidState("Order has no renderable calculation")
        bundle = {key: order[key] for key in required}
        if _calculation_digest(bundle) != calculation_sha256:
            raise Conflict("Calculation digest mismatch")
        for artifact in order.get("artifacts", []):
            if calculation_sha256[:16] in artifact["order_path"]:
                if self._delivery_is_live(order_id, artifact):
                    delivery = artifact["delivery"]
                    return _artifact_response(artifact, rows=None, idempotent=True)
                return self._restage_artifact(
                    order_id, revision, artifact, calculation_sha256
                )
        payload, rows = render_quote(order)
        digest = sha256_bytes(payload)
        artifact_id = f"art_{digest[:24]}"
        file_name = f"quote_{calculation_sha256[:16]}.xlsx"
        order_relative = f"{order_id}/out/{file_name}"
        self.orders.ensure_dir(f"{order_id}/out")
        _publish_or_adopt(self.orders, order_relative, payload)
        delivery_id = f"delivery_{os.urandom(12).hex()}"
        delivery_name = f"quote_{os.urandom(12).hex()}.xlsx"
        self.delivery.ensure_dir(order_id)
        _publish_or_adopt(self.delivery, f"{order_id}/{delivery_name}", payload)
        staged_at = utcnow()
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=self.settings.delivery_ttl_seconds)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        media_path = str(self.settings.delivery_public_root / order_id / delivery_name)
        artifact = {
            "artifact_id": artifact_id,
            "kind": "quote_xlsx",
            "order_path": f"out/{file_name}",
            "sha256": digest,
            "bytes": len(payload),
            "created_at": staged_at,
            "delivery": {
                "delivery_id": delivery_id,
                "media_path": media_path,
                "sha256": digest,
                "staged_at": staged_at,
                "expires_at": expires_at,
            },
        }

        def store(state: dict[str, Any]) -> None:
            state.setdefault("artifacts", []).append(artifact)
            state["status"] = "quoted"
            state["timestamps"]["quoted_at"] = staged_at

        _, stored = self.registry.mutate(
            order_id,
            store,
            expected_revision=revision,
            validator=self._validate_order,
        )
        return {
            "artifact_id": artifact_id,
            "order_path": artifact["order_path"],
            "media_path": media_path,
            "sha256": digest,
            "bytes": len(payload),
            "rows": rows,
            "delivery_id": delivery_id,
            "expires_at": expires_at,
            "warnings": [],
            "idempotent": False,
        }

    def _delivery_is_live(self, order_id: str, artifact: dict[str, Any]) -> bool:
        delivery = artifact["delivery"]
        try:
            if _parse_datetime(delivery["expires_at"]) <= datetime.now(UTC):
                return False
            file_name = _delivery_basename(delivery["media_path"], order_id)
            payload = self.delivery.read_bytes(
                f"{order_id}/{file_name}", limit=max(artifact["bytes"], 1)
            )
            return len(payload) == artifact["bytes"] and sha256_bytes(payload) == artifact["sha256"]
        except (NotFound, ValueError, InvalidState):
            return False

    def _restage_artifact(
        self,
        order_id: str,
        revision: int,
        artifact: dict[str, Any],
        calculation_sha256: str,
    ) -> dict[str, Any]:
        order_file = artifact["order_path"]
        if not order_file.startswith("out/"):
            raise InvalidState("Invalid stored artifact path")
        payload = self.orders.read_bytes(
            f"{order_id}/{order_file}", limit=max(artifact["bytes"], 1)
        )
        if len(payload) != artifact["bytes"] or sha256_bytes(payload) != artifact["sha256"]:
            raise InvalidState("Stored quote artifact is missing or corrupted")
        delivery_id = f"delivery_{os.urandom(12).hex()}"
        delivery_name = f"quote_{os.urandom(12).hex()}.xlsx"
        self.delivery.ensure_dir(order_id)
        _publish_or_adopt(self.delivery, f"{order_id}/{delivery_name}", payload)
        staged_at = utcnow()
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=self.settings.delivery_ttl_seconds)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        replacement = {
            "delivery_id": delivery_id,
            "media_path": str(self.settings.delivery_public_root / order_id / delivery_name),
            "sha256": artifact["sha256"],
            "staged_at": staged_at,
            "expires_at": expires_at,
        }

        def store(state: dict[str, Any]) -> None:
            matches = [
                item
                for item in state.get("artifacts", [])
                if item["artifact_id"] == artifact["artifact_id"]
            ]
            if len(matches) != 1:
                raise Conflict("Artifact changed during restage")
            matches[0]["delivery"] = replacement

        _, stored = self.registry.mutate(
            order_id,
            store,
            expected_revision=revision,
            validator=self._validate_order,
        )
        refreshed = next(
            item for item in stored["artifacts"] if item["artifact_id"] == artifact["artifact_id"]
        )
        return _artifact_response(refreshed, rows=None, idempotent=False, restaged=True)

    def order_get(self, order_id: str) -> dict[str, Any]:
        revision, state = self.registry.get(order_id)
        response = self._order_response(revision, state)
        response["fact_proposals"] = self.registry.list_fact_proposals(order_id)
        return response

    def approve_fact(self, order_id: str, fact_id: str, approved_by: str) -> dict[str, Any]:
        validate_id(order_id, field="order_id")
        if not isinstance(fact_id, str) or not re.fullmatch(r"fact_[0-9a-f]{16,64}", fact_id):
            raise InvalidIdentifier("Invalid fact_id")
        if not isinstance(approved_by, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9@._:-]{1,127}", approved_by):
            raise InvalidIdentifier("Invalid approver identity")
        revision, state, fact = self.registry.approve_fact(
            order_id, fact_id, approved_by, utcnow(), validator=self._validate_if_complete
        )
        return {"revision": revision, "fact": fact, "status": "approved"}

    def order_list(
        self,
        statuses: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
        sort: str = "updated_desc",
    ) -> dict[str, Any]:
        if statuses is not None and (not isinstance(statuses, list) or any(status not in _STATUS for status in statuses)):
            raise InvalidState("Invalid statuses")
        try:
            items, total = self.registry.list(statuses=statuses, limit=limit, offset=offset, sort=sort)
        except ValueError as exc:
            raise InvalidState(str(exc)) from exc
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def order_stats(
        self,
        period_from: str,
        period_to: str,
        group_by: str,
        statuses: list[str] | None = None,
    ) -> dict[str, Any]:
        start = _parse_date(period_from)
        end = _parse_date(period_to)
        if end < start or (end - start).days > 3660:
            raise InvalidState("Invalid statistics period")
        if group_by not in {"day", "week", "customer", "status"}:
            raise InvalidState("Invalid group_by")
        if statuses is not None and any(status not in _STATUS for status in statuses):
            raise InvalidState("Invalid statuses")
        selected = []
        for order in self.registry.all_states():
            created = _parse_datetime(order["timestamps"]["created_at"]).date()
            if start <= created <= end and (statuses is None or order["status"] in statuses):
                selected.append(order)
        buckets: dict[str, dict[str, Any]] = {}
        for order in selected:
            created = _parse_datetime(order["timestamps"]["created_at"])
            if group_by == "day":
                key = created.date().isoformat()
            elif group_by == "week":
                iso = created.isocalendar()
                key = f"{iso.year}-W{iso.week:02d}"
            elif group_by == "customer":
                key = order["customer"]["name"]
            else:
                key = order["status"]
            bucket = buckets.setdefault(
                key,
                {"key": key, "count": 0, "quoted_rub": 0.0, "won_rub": 0.0, "cost_rub": 0.0, "margin_rub": 0.0},
            )
            bucket["count"] += 1
            if order["status"] in {"quoted", "won", "lost"} and order.get("price"):
                bucket["quoted_rub"] += float(order["price"]["total_rub"])
            if order["status"] == "won" and order.get("price"):
                bucket["won_rub"] += float(order["price"]["total_rub"])
            if order.get("cost"):
                bucket["cost_rub"] += float(order["cost"]["total_rub"])
            if order.get("margin"):
                bucket["margin_rub"] += float(order["margin"]["absolute_rub"])
        quoted_count = sum(1 for order in selected if order["status"] in {"quoted", "won", "lost"})
        won_count = sum(1 for order in selected if order["status"] == "won")
        return {
            "counts": sorted(buckets.values(), key=lambda item: item["key"]),
            "quoted_rub": round(sum(item["quoted_rub"] for item in buckets.values()), 2),
            "won_rub": round(sum(item["won_rub"] for item in buckets.values()), 2),
            "cost_rub": round(sum(item["cost_rub"] for item in buckets.values()), 2),
            "margin_rub": round(sum(item["margin_rub"] for item in buckets.values()), 2),
            "conversion": round(won_count / quoted_count, 4) if quoted_count else None,
            "warnings": [],
        }

    def _order_response(self, revision: int, state: dict[str, Any]) -> dict[str, Any]:
        complete = bool(state.get("source_files"))
        if complete:
            self._validate_order(state)
        return {"revision": revision, "schema_complete": complete, "order": deepcopy(state)}

    def _validate_if_complete(self, state: dict[str, Any]) -> None:
        if state.get("source_files"):
            self._validate_order(state)

    def _validate_order(self, state: dict[str, Any]) -> None:
        errors = sorted(
            self.validator.iter_errors(_decimalize(state)),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            first = errors[0]
            path = ".".join(str(item) for item in first.absolute_path) or "$"
            raise InvalidState(f"Stored order violates schema at {path}: {first.message}")


def _load_trusted_json(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
            raise RuntimeError("Invalid order schema file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return json.load(stream, parse_float=Decimal)
    finally:
        os.close(fd)


def _customer(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or not set(value).issubset({"name", "contact", "external_ref"}):
        raise InvalidState("Invalid customer")
    name = value.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 256:
        raise InvalidState("Customer name is required")
    out = {"name": name.strip()}
    for key, limit in (("contact", 256), ("external_ref", 128)):
        if key in value:
            field = value[key]
            if not isinstance(field, str) or len(field) > limit:
                raise InvalidState(f"Invalid customer {key}")
            out[key] = field
    return out


def _validate_cache_name(value: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 255 or unicodedata.normalize("NFKC", value) != value:
        raise InvalidIdentifier("Invalid cache_name")
    if any(character in value for character in ("/", "\\", "\x00", "∕", "⁄", "⧸")):
        raise InvalidIdentifier("Invalid cache_name")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise InvalidIdentifier("Invalid cache_name")
    match = _CACHE_PREFIX_RE.fullmatch(value)
    if match is None or match.group(1) in {".", ".."}:
        raise InvalidIdentifier("Invalid cache_name")
    return match.group(1)


def _detect_format(name: str, head: bytes) -> str:
    suffix = Path(name).suffix.lower().removeprefix(".")
    if suffix == "pdf" and head.startswith(b"%PDF-"):
        return "pdf"
    if suffix == "dwg" and head.startswith(b"AC10"):
        return "dwg"
    if suffix == "dxf":
        if head.startswith(b"AutoCAD Binary DXF\r\n\x1a\x00") or (
            b"SECTION" in head.upper() and b"\x00" not in head[:256]
        ):
            return "dxf"
    raise UnsupportedFormat("Extension and file signature do not match")


def _hash_fd(fd: int) -> str:
    digest = hashlib.sha256()
    while chunk := os.read(fd, 1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _verify_published_file(
    root: SecureRoot,
    relative: str,
    *,
    expected_bytes: int,
    expected_sha256: str,
) -> None:
    try:
        payload = root.read_bytes(relative, limit=expected_bytes)
    except (NotFound, ValueError) as exc:
        raise Conflict("Published file cannot be adopted") from exc
    if len(payload) != expected_bytes or sha256_bytes(payload) != expected_sha256:
        raise Conflict("Published file conflicts with deterministic output")


def _publish_or_adopt(root: SecureRoot, relative: str, payload: bytes) -> None:
    digest = sha256_bytes(payload)
    try:
        root.atomic_write(relative, payload)
    except Conflict:
        _verify_published_file(
            root, relative, expected_bytes=len(payload), expected_sha256=digest
        )


def _warning(code: str, severity: str, message: str, part_ref: str | None = None) -> dict[str, Any]:
    warning: dict[str, Any] = {"code": code, "severity": severity, "message": message[:1024]}
    if part_ref:
        warning["part_ref"] = part_ref
    return warning


def _abstain_calculation(warning: dict[str, Any]) -> dict[str, Any]:
    return {
        "calculation": None,
        "operations": [],
        "cost": None,
        "price": None,
        "margin": None,
        "warnings": [warning],
        "abstain": True,
        "calculation_sha256": None,
    }


def _normalize_cadkit(
    source: dict[str, Any], raw: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], float]:
    part_ref = source["source_file_id"]
    warnings: list[dict[str, Any]] = []
    if not raw.get("ok"):
        return None, [_warning("geometry_unverified", "stop", "cadkit rejected the drawing", part_ref)], 0.0
    selected = raw.get("largest") if raw.get("contours", 1) > 1 else raw
    if raw.get("contours", 1) > 1:
        warnings.append(
            _warning("geometry_unverified", "stop", "Multiple top-level contours require human selection", part_ref)
        )
    for text in raw.get("warnings", []):
        warning_text = str(text)
        upper = warning_text.upper()
        if "КОНТУР НЕ ЗАМКНУТ" in upper:
            warnings.append(_warning("open_contour", "stop", "Open contour detected", part_ref))
        elif "$INSUNITS" in upper:
            warnings.append(_warning("unit_ambiguous", "stop", "Drawing units are not declared", part_ref))
        elif warning_text.startswith("чертёж в единицах «"):
            warnings.append(_warning("other", "info", "Declared drawing units were converted to mm", part_ref))
        elif warning_text.startswith("отброшена рамка чертежа ("):
            warnings.append(_warning("other", "info", "Recognized drawing sheet frame was excluded", part_ref))
        else:
            warnings.append(_warning("geometry_unverified", "stop", "Geometry requires human review", part_ref))
    if selected is None:
        return None, [*warnings, _warning("geometry_unverified", "stop", "No part contour selected", part_ref)], 0.0
    area = selected.get("area_mm2")
    outer = selected.get("outer_area_mm2", area)
    cut = selected.get("cut_length_mm")
    bbox = selected.get("bbox")
    if not all(isinstance(value, (int, float)) and value > 0 for value in (area, outer, cut)) or not bbox:
        return None, [*warnings, _warning("geometry_unverified", "stop", "Invalid cadkit metrics", part_ref)], 0.0
    part = {
        "part_ref": part_ref,
        "area_mm2": area,
        "outer_area_mm2": outer,
        "cut_length_mm": cut,
        "holes": int(selected.get("holes", 0)),
        "pierces": int(selected.get("pierces", 0)),
        "bbox_mm": [bbox[0], bbox[1]],
        "quantity": 1,
    }
    return part, warnings, float(selected.get("mass_kg") or 0)


def _calculation_digest(bundle: dict[str, Any]) -> str:
    stable = deepcopy(bundle)
    stable.get("calculation", {}).pop("calculated_at", None)
    return digest_json(stable)


def _parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise InvalidState("Date must use YYYY-MM-DD") from exc


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _decimalize(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [_decimalize(item) for item in value]
    if isinstance(value, dict):
        return {key: _decimalize(item) for key, item in value.items()}
    return value


def _delivery_basename(media_path: str, order_id: str) -> str:
    expected_prefix = f"/opt/data/delivery/{order_id}/"
    if not isinstance(media_path, str) or not media_path.startswith(expected_prefix):
        raise InvalidState("Invalid delivery path")
    name = media_path[len(expected_prefix) :]
    validate_id(name, field="delivery file")
    if not name.endswith(".xlsx"):
        raise InvalidState("Invalid delivery file")
    return name


def _artifact_response(
    artifact: dict[str, Any],
    *,
    rows: int | None,
    idempotent: bool,
    restaged: bool = False,
) -> dict[str, Any]:
    delivery = artifact["delivery"]
    return {
        "artifact_id": artifact["artifact_id"],
        "order_path": artifact["order_path"],
        "media_path": delivery["media_path"],
        "sha256": artifact["sha256"],
        "bytes": artifact["bytes"],
        "rows": rows,
        "delivery_id": delivery["delivery_id"],
        "expires_at": delivery["expires_at"],
        "warnings": [],
        "idempotent": idempotent,
        "restaged": restaged,
    }


def _part_quantity_proposal(
    value: Any, known_sources: set[str], *, order_id: str
) -> dict[str, Any]:
    expected = {
        "code",
        "target_source_file_id",
        "value",
        "unit",
        "evidence_ref",
    }
    if not isinstance(value, dict) or set(value) != expected or value.get("code") != "part_quantity":
        raise InvalidState("Only part_quantity manual facts are accepted")
    target = value["target_source_file_id"]
    if target not in known_sources:
        raise InvalidState("Manual fact target is not an order source")
    quantity = value["value"]
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        raise InvalidState("Part quantity must be a positive integer")
    if value["unit"] != "pcs":
        raise InvalidState("Invalid quantity unit")
    evidence = value["evidence_ref"]
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 256:
        raise InvalidState("Evidence reference is required")
    proposed_at = utcnow()
    seed = {
        "order_id": order_id,
        "code": "part_quantity",
        "target_source_file_id": target,
        "value": quantity,
        "unit": "pcs",
        "evidence_ref": evidence.strip(),
    }
    digest = digest_json(seed)
    return {
        "fact_id": f"fact_{digest[:24]}",
        "code": "part_quantity",
        "target_source_file_id": target,
        "value": quantity,
        "unit": "pcs",
        "evidence_ref": evidence.strip(),
        "proposal_sha256": digest,
        "status": "pending",
        "proposed_at": proposed_at,
    }
