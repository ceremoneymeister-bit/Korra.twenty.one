"""Typed, fail-closed contract for freezing calculator input."""
from __future__ import annotations

from typing import Any, TypedDict

from .errors import InvalidState
from .util import SHA256_RE, digest_json, validate_id

INPUT_CONTRACT_VERSION = 1
INPUT_ORIGIN = "order_registry.source_files"
MAX_SOURCE_FILES = 32


class SourceManifestEntry(TypedDict):
    """Identity of one attachment already ingested into the order registry."""

    source_file_id: str
    name: str
    sha256: str


def bind_source_manifest(
    source_files: Any, source_manifest: Any
) -> list[SourceManifestEntry]:
    """Return a canonical manifest after exact binding to stored attachments."""
    if not isinstance(source_files, list) or not 1 <= len(source_files) <= MAX_SOURCE_FILES:
        raise InvalidState(
            "В заказе нет загруженных вложений: сначала вызовите "
            "ingest_attachment, затем фиксируйте вход"
        )
    if not isinstance(source_manifest, list) or not 1 <= len(source_manifest) <= MAX_SOURCE_FILES:
        raise InvalidState(
            "source_manifest — непустой список объектов "
            "{source_file_id, name, sha256}, до 32 позиций"
        )

    actual = _index_entries(source_files, label="state.source_files", exact_keys=False)
    claimed = _index_entries(source_manifest, label="source_manifest", exact_keys=True)

    actual_ids = set(actual)
    claimed_ids = set(claimed)
    if actual_ids != claimed_ids:
        missing = sorted(actual_ids - claimed_ids)
        unknown = sorted(claimed_ids - actual_ids)
        detail = []
        if missing:
            detail.append(f"не указаны: {', '.join(missing)}")
        if unknown:
            detail.append(f"не принадлежат заказу: {', '.join(unknown)}")
        raise InvalidState(
            "source_manifest не совпадает с фактическими вложениями заказа"
            + (f" ({'; '.join(detail)})" if detail else "")
        )

    for source_id, entry in claimed.items():
        stored = actual[source_id]
        if entry["name"] != stored["name"]:
            raise InvalidState(
                f"source_manifest: имя для {source_id} не совпадает с вложением заказа"
            )
        if entry["sha256"] != stored["sha256"]:
            raise InvalidState(
                f"source_manifest: sha256 для {source_id} не совпадает с вложением заказа"
            )

    return [actual[source_id] for source_id in sorted(actual)]


def input_metadata(
    *,
    order_id: str,
    order_revision: int,
    customer: Any,
    quantity: int,
    kd_revision: str,
    source_manifest: list[SourceManifestEntry],
) -> dict[str, Any]:
    """Build auditable origin/revision metadata and digest of frozen intake."""
    if not isinstance(customer, dict):
        raise InvalidState("У заказа нет типизированных данных заказчика")
    name = customer.get("name")
    if not isinstance(name, str) or not name.strip():
        raise InvalidState("У заказа не задан заказчик")
    payload = {
        "contract_version": INPUT_CONTRACT_VERSION,
        "order_id": order_id,
        "customer": customer,
        "quantity": quantity,
        "kd_revision": kd_revision,
        "source_manifest": source_manifest,
    }
    return {
        "input_contract_version": INPUT_CONTRACT_VERSION,
        "input_digest": digest_json(payload),
        "input_order_revision": order_revision,
        "input_origin": INPUT_ORIGIN,
    }


def _index_entries(
    entries: list[Any], *, label: str, exact_keys: bool
) -> dict[str, SourceManifestEntry]:
    indexed: dict[str, SourceManifestEntry] = {}
    seen_hashes: set[str] = set()
    required = {"source_file_id", "name", "sha256"}
    for position, raw in enumerate(entries):
        if not isinstance(raw, dict) or (
            set(raw) != required if exact_keys else not required.issubset(raw)
        ):
            raise InvalidState(
                f"{label}[{position}] должен содержать "
                "source_file_id, name и sha256"
            )
        source_id = raw["source_file_id"]
        validate_id(source_id, field=f"{label}[{position}].source_file_id")
        name = raw["name"]
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name.encode("utf-8")) > 255
        ):
            raise InvalidState(f"{label}[{position}].name недопустимо")
        sha256 = raw["sha256"]
        if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
            raise InvalidState(f"{label}[{position}].sha256 недопустимо")
        if source_id != f"src_{sha256[:24]}":
            raise InvalidState(
                f"{label}[{position}]: source_file_id не соответствует sha256"
            )
        if not exact_keys:
            byte_count = raw.get("bytes")
            if (
                not isinstance(byte_count, int)
                or isinstance(byte_count, bool)
                or byte_count < 1
            ):
                raise InvalidState(f"{label}[{position}].bytes должно быть больше нуля")
        if source_id in indexed:
            raise InvalidState(f"{label}: source_file_id {source_id} задан дважды")
        if sha256 in seen_hashes:
            raise InvalidState(f"{label}: sha256 {sha256} задан дважды")
        indexed[source_id] = {
            "source_file_id": source_id,
            "name": name,
            "sha256": sha256,
        }
        seen_hashes.add(sha256)
    return indexed


__all__ = [
    "INPUT_CONTRACT_VERSION",
    "INPUT_ORIGIN",
    "SourceManifestEntry",
    "bind_source_manifest",
    "input_metadata",
]
