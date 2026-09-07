from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .errors import InvalidIdentifier

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_id(value: str, *, field: str = "identifier") -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise InvalidIdentifier(f"Invalid {field}")
    return value


def validate_revision(value: str) -> str:
    if not isinstance(value, str) or not REVISION_RE.fullmatch(value):
        raise InvalidIdentifier("Invalid revision")
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    ).encode("utf-8")


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"Unsupported type: {type(value).__name__}")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def public_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)

