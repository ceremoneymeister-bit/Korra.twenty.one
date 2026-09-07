"""Signed, order-scoped capabilities for autonomous calculator profiles."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import OrderScopeDenied
from .util import validate_id

CAPABILITY_PREFIX = "mcs1"
CAPABILITY_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_CAPABILITY_LENGTH = 256
SECRET_ENV = "METAL_CALC_SCOPE_SECRET"

# Event -> exact autonomous runtime identity. Compact signed codes keep the
# full capability below the API server's 256-byte trusted-header limit.
AUTONOMOUS_STAGE_SCOPE: dict[str, tuple[str, str]] = {
    "input_frozen": ("tech", "raschet-route"),
    "route_return": ("tech", "raschet-route"),
    "route_frozen": ("supply", "raschet-blank"),
    "blank_costed": ("norm", "raschet-time"),
}
AUTONOMOUS_ACTIVE_STATUSES: dict[str, frozenset[str]] = {
    # Tech needs three writes in one turn; each advances the workflow.
    "input_frozen": frozenset(
        {"INPUT_FROZEN", "BOM_VALIDATED", "ROUTE_OPTIONS_READY"}
    ),
    "route_return": frozenset({"ROUTE_OPTIONS_READY"}),
    # Supply first records deterministic blank drivers. The durable event is
    # blank_costed, while the resulting workflow status is DETAILED_COSTING;
    # it may then replace the provisional metal rate in the same turn.
    "route_frozen": frozenset({"ROUTE_FROZEN", "DETAILED_COSTING"}),
    "blank_costed": frozenset({"DETAILED_COSTING"}),
}
_ROLE_CODES = {"tech": "t", "supply": "s", "norm": "n"}
_PROFILE_CODES = {
    "raschet-route": "r",
    "raschet-blank": "b",
    "raschet-time": "n",
}
_STAGE_CODES = {
    "input_frozen": "i",
    "route_return": "u",
    "route_frozen": "r",
    "blank_costed": "b",
}
_CODE_TO_ROLE = {value: key for key, value in _ROLE_CODES.items()}
_CODE_TO_PROFILE = {value: key for key, value in _PROFILE_CODES.items()}
_CODE_TO_STAGE = {value: key for key, value in _STAGE_CODES.items()}
_HEX_SECRET = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class OrderScopeClaims:
    role: str
    profile: str
    stage: str
    order_hash: str
    event_identity: str
    expires_at: int


def _b64_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _b64_decode(payload: str) -> bytes:
    if not payload or re.fullmatch(r"[A-Za-z0-9_-]+", payload) is None:
        raise ValueError("invalid base64url")
    decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    if _b64_encode(decoded) != payload:
        raise ValueError("non-canonical base64url")
    return decoded


def load_scope_secret(environ: Mapping[str, str] | None = None) -> bytes:
    """Read the credential without ever including its value in an error."""
    source = os.environ if environ is None else environ
    raw = (source.get(SECRET_ENV) or "").strip()
    if _HEX_SECRET.fullmatch(raw) is None:
        raise RuntimeError(f"{SECRET_ENV} must be 64 lowercase hex characters")
    return bytes.fromhex(raw)


def order_identity(order_id: str) -> str:
    validate_id(order_id, field="order_id")
    return _b64_encode(hashlib.sha256(order_id.encode("utf-8")).digest())


def event_identity_from_cursor_key(cursor_key: str) -> str:
    if not isinstance(cursor_key, str) or not cursor_key:
        raise ValueError("cursor_key must be a non-empty string")
    return _b64_encode(hashlib.sha256(cursor_key.encode("utf-8")).digest())


def workflow_event_cursor_key(
    order_id: str, stage: str, event_at: Any, event_index: int
) -> str:
    validate_id(order_id, field="order_id")
    if stage not in AUTONOMOUS_STAGE_SCOPE:
        raise ValueError("stage is not autonomous")
    if not isinstance(event_index, int) or isinstance(event_index, bool) or event_index < 0:
        raise ValueError("event_index must be a non-negative integer")
    return f"{order_id}:wf:{stage}:{event_at}:{event_index}"


def latest_workflow_event_identity(
    state: Mapping[str, Any], order_id: str, stage: str
) -> str:
    """Bind a capability to the latest exact trigger event in durable state."""
    events = state.get("workflow_events")
    if not isinstance(events, list):
        raise OrderScopeDenied()
    matches = [
        (index, event)
        for index, event in enumerate(events)
        if isinstance(event, dict)
        and str(event.get("event") or "").split(":", 1)[0] == stage
    ]
    if not matches:
        raise OrderScopeDenied()
    index, event = matches[-1]
    cursor_key = workflow_event_cursor_key(order_id, stage, event.get("at"), index)
    return event_identity_from_cursor_key(cursor_key)


def verify_workflow_scope_state(
    claims: OrderScopeClaims, state: Mapping[str, Any], order_id: str
) -> None:
    """Reject a valid token once its workflow phase has ended or returned."""
    workflow = state.get("workflow")
    status = workflow.get("status") if isinstance(workflow, dict) else None
    if status not in AUTONOMOUS_ACTIVE_STATUSES.get(claims.stage, frozenset()):
        raise OrderScopeDenied(
            "Сессия не имеет действующего доступа к этому заказу"
        )
    if claims.event_identity != latest_workflow_event_identity(
        state, order_id, claims.stage
    ):
        raise OrderScopeDenied(
            "Сессия не имеет действующего доступа к этому заказу"
        )
    # ROUTE_OPTIONS_READY is both a normal tech intermediate status and the
    # target of a later formal route return. A returned route must not revive
    # the original session merely because the old input_frozen event remains.
    if claims.stage == "input_frozen":
        events = state.get("workflow_events") or []
        trigger_index = None
        for index, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            name = str(event.get("event") or "").split(":", 1)[0]
            if name != claims.stage:
                continue
            identity = event_identity_from_cursor_key(
                workflow_event_cursor_key(
                    order_id, claims.stage, event.get("at"), index
                )
            )
            if identity == claims.event_identity:
                trigger_index = index
        if trigger_index is None or any(
            isinstance(event, dict)
            and str(event.get("event") or "").split(":", 1)[0] == "route_return"
            for event in events[trigger_index + 1:]
        ):
            raise OrderScopeDenied(
                "Сессия не имеет действующего доступа к этому заказу"
            )


def issue_order_scope(
    secret: bytes,
    *,
    order_id: str,
    role: str,
    profile: str,
    stage: str,
    event_identity: str,
    expires_at: int,
) -> str:
    """Issue one deterministic capability for one workflow trigger."""
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("scope secret must contain at least 32 bytes")
    if AUTONOMOUS_STAGE_SCOPE.get(stage) != (role, profile):
        raise ValueError("stage, role and profile do not form an autonomous scope")
    if not isinstance(event_identity, str) or re.fullmatch(
        r"[A-Za-z0-9_-]{43}", event_identity
    ) is None:
        raise ValueError("invalid event identity")
    if not isinstance(expires_at, int) or isinstance(expires_at, bool) or expires_at < 1:
        raise ValueError("expires_at must be a positive unix timestamp")
    payload = {
        "a": "m",
        "e": event_identity,
        "o": order_identity(order_id),
        "p": _PROFILE_CODES[profile],
        "r": _ROLE_CODES[role],
        "s": _STAGE_CODES[stage],
        "v": 1,
        "x": expires_at,
    }
    encoded = _b64_encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = _b64_encode(
        hmac.new(secret, f"{CAPABILITY_PREFIX}.{encoded}".encode("ascii"), hashlib.sha256).digest()
    )
    token = f"{CAPABILITY_PREFIX}.{encoded}.{signature}"
    if len(token) > MAX_CAPABILITY_LENGTH:
        raise ValueError("scope capability exceeds trusted header limit")
    return token


def verify_order_scope(
    token: str,
    secret: bytes,
    *,
    order_id: str,
    expected_role: str,
    expected_profile: str,
    expected_event_identity: str | None = None,
    now: int | None = None,
) -> OrderScopeClaims:
    """Verify signature, order, runtime identity, event and expiry."""
    try:
        if not isinstance(token, str) or len(token) > MAX_CAPABILITY_LENGTH:
            raise ValueError("invalid token")
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("invalid secret")
        prefix, encoded, supplied_signature = token.split(".")
        if prefix != CAPABILITY_PREFIX:
            raise ValueError("invalid prefix")
        expected_signature = _b64_encode(
            hmac.new(
                secret,
                f"{CAPABILITY_PREFIX}.{encoded}".encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("invalid signature")
        payload = json.loads(_b64_decode(encoded))
        if not isinstance(payload, dict) or set(payload) != {
            "a", "e", "o", "p", "r", "s", "v", "x"
        }:
            raise ValueError("invalid claims")
        if (
            payload["a"] != "m"
            or payload["v"] != 1
            or not isinstance(payload["x"], int)
            or isinstance(payload["x"], bool)
            or not isinstance(payload["o"], str)
            or re.fullmatch(r"[A-Za-z0-9_-]{43}", payload["o"]) is None
            or not isinstance(payload["e"], str)
            or re.fullmatch(r"[A-Za-z0-9_-]{43}", payload["e"]) is None
        ):
            raise ValueError("invalid claims")
        claims = OrderScopeClaims(
            role=_CODE_TO_ROLE[payload["r"]],
            profile=_CODE_TO_PROFILE[payload["p"]],
            stage=_CODE_TO_STAGE[payload["s"]],
            order_hash=str(payload["o"]),
            event_identity=str(payload["e"]),
            expires_at=payload["x"],
        )
        if AUTONOMOUS_STAGE_SCOPE.get(claims.stage) != (claims.role, claims.profile):
            raise ValueError("incoherent claims")
        if claims.role != expected_role or claims.profile != expected_profile:
            raise ValueError("wrong runtime")
        if claims.order_hash != order_identity(order_id):
            raise ValueError("wrong order")
        if expected_event_identity is not None and not hmac.compare_digest(
            claims.event_identity, expected_event_identity
        ):
            raise ValueError("wrong event")
        current_time = int(time.time()) if now is None else now
        if current_time >= claims.expires_at:
            raise ValueError("expired")
        return claims
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OrderScopeDenied(
            "Сессия не имеет действующего доступа к этому заказу"
        ) from exc


__all__ = [
    "AUTONOMOUS_STAGE_SCOPE",
    "AUTONOMOUS_ACTIVE_STATUSES",
    "CAPABILITY_TTL_SECONDS",
    "OrderScopeClaims",
    "event_identity_from_cursor_key",
    "issue_order_scope",
    "latest_workflow_event_identity",
    "load_scope_secret",
    "verify_order_scope",
    "verify_workflow_scope_state",
    "workflow_event_cursor_key",
]
