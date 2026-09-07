"""Cryptographic and MCP behavior contracts for autonomous order scope."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json

import pytest

from metal_calc.errors import OrderScopeDenied
from metal_calc.mcp_server import build_mcp
from metal_calc.order_scope import (
    event_identity_from_cursor_key,
    issue_order_scope,
    latest_workflow_event_identity,
    verify_order_scope,
    verify_workflow_scope_state,
)
from metal_calc.packs2 import PipelinePackStore
from metal_calc.securefs import SecureRoot
from metal_calc.service2 import PipelineService
from metal_calc.service3 import WorkflowService

SECRET = bytes.fromhex("11" * 32)
FUTURE = 4_102_444_800


def _token(
    order_id: str = "order-a",
    *,
    role: str = "tech",
    profile: str = "raschet-route",
    stage: str = "input_frozen",
    cursor_key: str = "order-a:wf:input_frozen:t1:0",
    expires_at: int = FUTURE,
) -> str:
    return issue_order_scope(
        SECRET,
        order_id=order_id,
        role=role,
        profile=profile,
        stage=stage,
        event_identity=event_identity_from_cursor_key(cursor_key),
        expires_at=expires_at,
    )


def _resign_claims(token: str, **overrides) -> str:
    prefix, encoded, _signature = token.split(".")
    payload = json.loads(
        base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    )
    payload.update(overrides)
    changed = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(
        hmac.new(
            SECRET,
            f"{prefix}.{changed}".encode("ascii"),
            hashlib.sha256,
        ).digest()
    ).decode().rstrip("=")
    return f"{prefix}.{changed}.{signature}"


def test_scope_allows_exact_order_runtime_and_event_only() -> None:
    token = _token()
    event = event_identity_from_cursor_key("order-a:wf:input_frozen:t1:0")
    claims = verify_order_scope(
        token,
        SECRET,
        order_id="order-a",
        expected_role="tech",
        expected_profile="raschet-route",
        expected_event_identity=event,
        now=FUTURE - 1,
    )
    assert len(claims.order_hash) == 43
    assert len(claims.event_identity) == 43
    assert claims.stage == "input_frozen"
    assert len(token) <= 256

    rejected = [
        {"order_id": "order-b"},
        {"expected_role": "supply"},
        {"expected_profile": "raschet-time"},
        {"expected_event_identity": event_identity_from_cursor_key(
            "order-a:wf:input_frozen:t2:1"
        )},
        {"now": FUTURE},
    ]
    base = {
        "order_id": "order-a",
        "expected_role": "tech",
        "expected_profile": "raschet-route",
        "expected_event_identity": event,
        "now": FUTURE - 1,
    }
    for override in rejected:
        with pytest.raises(OrderScopeDenied):
            verify_order_scope(token, SECRET, **(base | override))
    with pytest.raises(OrderScopeDenied):
        verify_order_scope(
            token[:-1] + ("A" if token[-1] != "A" else "B"),
            SECRET,
            **base,
        )

    with pytest.raises(OrderScopeDenied):
        verify_order_scope(
            _resign_claims(token, a="other-service"),
            SECRET,
            **base,
        )
    with pytest.raises(ValueError, match="do not form an autonomous scope"):
        _token(stage="route_frozen")


def test_route_return_does_not_revive_original_tech_scope() -> None:
    token = _token()
    claims = verify_order_scope(
        token,
        SECRET,
        order_id="order-a",
        expected_role="tech",
        expected_profile="raschet-route",
        now=FUTURE - 1,
    )
    in_progress = {
        "workflow": {"status": "BOM_VALIDATED"},
        "workflow_events": [{"event": "input_frozen", "at": "t1"}],
    }
    verify_workflow_scope_state(claims, in_progress, "order-a")

    returned = {
        "workflow": {"status": "ROUTE_OPTIONS_READY"},
        "workflow_events": [
            {"event": "input_frozen", "at": "t1"},
            {"event": "route_return:ROUTE_OPTIONS_READY", "at": "t2"},
        ],
    }
    with pytest.raises(OrderScopeDenied):
        verify_workflow_scope_state(claims, returned, "order-a")

    returned_token = _token(
        stage="route_return",
        cursor_key="order-a:wf:route_return:t2:1",
    )
    returned_claims = verify_order_scope(
        returned_token,
        SECRET,
        order_id="order-a",
        expected_role="tech",
        expected_profile="raschet-route",
        now=FUTURE - 1,
    )
    verify_workflow_scope_state(returned_claims, returned, "order-a")


def _freeze(service, workflow: WorkflowService, order_id: str, payload: bytes) -> int:
    service.order_upsert(order_id, 0, {"customer": {"name": f"Customer {order_id}"}})
    cache_name = f"doc_{payload.hex().ljust(12, '0')[:12]}_part.dxf"
    (service.settings.cache_root / cache_name).write_bytes(
        b"0\nSECTION\n2\nHEADER\n999\n" + payload + b"\n0\nENDSEC\n0\nEOF\n"
    )
    source = service.ingest_attachment(order_id, cache_name)
    current = service.order_get(order_id)
    result = workflow.input_freeze(
        order_id,
        current["revision"],
        2,
        "R1",
        [
            {
                "source_file_id": source["source_file_id"],
                "name": source["name"],
                "sha256": source["sha256"],
            }
        ],
        "scope test",
        actor_role="front",
    )
    return int(result["revision"])


def _call(mcp, name: str, arguments: dict) -> dict:
    result = asyncio.run(mcp.call_tool(name, arguments))
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


def _workflow_services(service):
    from metal_calc.packs2 import ACTIVE_POINTER
    from test_workflow_v3 import ROUTE_STEPS, workflow_pack

    pack = workflow_pack()
    payload = json.dumps(pack, ensure_ascii=False, sort_keys=True).encode("utf-8")
    (service.settings.rates_root / "v3-test.json").write_bytes(payload)
    (service.settings.rates_root / ACTIVE_POINTER).write_text(
        json.dumps(
            {
                "revision": "v3-test",
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    packs = PipelinePackStore(SecureRoot(service.settings.rates_root, writable=False))
    return (
        PipelineService(service.registry, packs),
        WorkflowService(service.registry, packs),
        ROUTE_STEPS,
    )


def test_autonomous_mcp_allows_a_and_denies_b_read_and_write(
    service, monkeypatch
) -> None:
    packs = PipelinePackStore(SecureRoot(service.settings.rates_root, writable=False))
    pipeline = PipelineService(service.registry, packs)
    workflow = WorkflowService(service.registry, packs)
    revision_a = _freeze(service, workflow, "order-a", b"a")
    revision_b = _freeze(service, workflow, "order-b", b"b")
    _, state_a = service.registry.get("order-a")
    event_a = latest_workflow_event_identity(state_a, "order-a", "input_frozen")
    token_a = issue_order_scope(
        SECRET,
        order_id="order-a",
        role="tech",
        profile="raschet-route",
        stage="input_frozen",
        event_identity=event_a,
        expires_at=FUTURE,
    )
    monkeypatch.setenv("METAL_CALC_SCOPE_SECRET", SECRET.hex())
    mcp = build_mcp(
        service,
        pipeline,
        workflow,
        role="tech",
        profile="raschet-route",
    )
    real_get = service.registry.get
    get_calls: list[str] = []

    def tracked_get(order_id: str):
        get_calls.append(order_id)
        return real_get(order_id)

    monkeypatch.setattr(service.registry, "get", tracked_get)

    allowed_read = _call(
        mcp,
        "order_get",
        {"order_id": "order-a", "hermes_session_context": token_a},
    )
    assert "error" not in allowed_read, allowed_read
    assert allowed_read["order"]["order_id"] == "order-a"
    assert get_calls and set(get_calls) == {"order-a"}

    get_calls.clear()
    missing_context = _call(mcp, "order_get", {"order_id": "order-a"})
    assert missing_context["error"]["code"] == "OrderScopeDenied"
    assert get_calls == []

    invalid_before_registry = {
        "tampered": token_a[:-1] + ("A" if token_a[-1] != "A" else "B"),
        "expired": issue_order_scope(
            SECRET,
            order_id="order-a",
            role="tech",
            profile="raschet-route",
            stage="input_frozen",
            event_identity=event_a,
            expires_at=1,
        ),
        "wrong-role": _resign_claims(token_a, r="s"),
        "wrong-profile": _resign_claims(token_a, p="b"),
        "wrong-audience": _resign_claims(token_a, a="other-service"),
    }
    for label, invalid_token in invalid_before_registry.items():
        get_calls.clear()
        denied = _call(
            mcp,
            "order_get",
            {
                "order_id": "order-a",
                "hermes_session_context": invalid_token,
            },
        )
        assert denied["error"]["code"] == "OrderScopeDenied", label
        assert get_calls == [], label

    get_calls.clear()
    denied_read = _call(
        mcp,
        "order_get",
        {"order_id": "order-b", "hermes_session_context": token_a},
    )
    assert denied_read["error"]["code"] == "OrderScopeDenied"
    assert get_calls == []

    node = [{
        "bom_node_id": "part-1",
        "parent_bom_id": None,
        "node_type": "MANUFACTURED_PART",
        "make_or_buy": "MAKE",
        "quantity": 2,
        "note": "part",
    }]
    denied_write = _call(
        mcp,
        "bom_upsert",
        {
            "order_id": "order-b",
            "expected_revision": revision_b,
            "nodes": node,
            "hermes_session_context": token_a,
        },
    )
    assert denied_write["error"]["code"] == "OrderScopeDenied"
    assert get_calls == []
    assert service.order_get("order-b")["revision"] == revision_b

    allowed_write = _call(
        mcp,
        "bom_upsert",
        {
            "order_id": "order-a",
            "expected_revision": revision_a,
            "nodes": node,
            "hermes_session_context": token_a,
        },
    )
    assert allowed_write["order_id"] == "order-a"
    assert allowed_write["revision"] == revision_a + 1

    def finish_tech_phase(state: dict) -> None:
        state["workflow"]["status"] = "ROUTE_FROZEN"
        state["status"] = "ROUTE_FROZEN"

    service.registry.mutate(
        "order-a",
        finish_tech_phase,
        expected_revision=allowed_write["revision"],
    )
    get_calls.clear()
    replay = _call(
        mcp,
        "order_get",
        {"order_id": "order-a", "hermes_session_context": token_a},
    )
    assert replay["error"]["code"] == "OrderScopeDenied"
    # One lookup is needed to prove the phase ended; the protected service
    # read itself is never reached.
    assert get_calls == ["order-a"]


@pytest.mark.parametrize(
    ("role", "profile", "tool_calls"),
    [
        (
            "tech",
            "raschet-route",
            [
                ("order_get", {}),
                ("workflow_status", {}),
                ("bom_upsert", {"expected_revision": 1, "nodes": []}),
                (
                    "route_variants_propose",
                    {"expected_revision": 1, "steps": []},
                ),
                (
                    "route_freeze",
                    {"expected_revision": 1, "variant_id": "variant-1"},
                ),
            ],
        ),
        (
            "supply",
            "raschet-blank",
            [
                ("order_get", {}),
                ("workflow_status", {}),
                (
                    "blank_drivers_set",
                    {
                        "expected_revision": 1,
                        "material_code": "steel-40x",
                        "mass_kg": "1",
                        "mass_basis": "per_piece",
                        "mass_note": "test",
                        "items": [],
                    },
                ),
                (
                    "supply_confirm",
                    {
                        "expected_revision": 1,
                        "rate_rub_per_kg": "1",
                        "confirmed_by": "test",
                        "source_ref": "test",
                    },
                ),
            ],
        ),
        (
            "norm",
            "raschet-time",
            [
                ("order_get", {}),
                ("workflow_status", {}),
                ("time_norms_set", {"expected_revision": 1, "entries": []}),
            ],
        ),
    ],
)
def test_every_autonomous_order_tool_fails_closed_without_trusted_scope(
    service, monkeypatch, role, profile, tool_calls
) -> None:
    packs = PipelinePackStore(SecureRoot(service.settings.rates_root, writable=False))
    mcp = build_mcp(
        service,
        PipelineService(service.registry, packs),
        WorkflowService(service.registry, packs),
        role=role,
        profile=profile,
    )
    get_calls: list[str] = []
    real_get = service.registry.get

    def tracked_get(order_id: str):
        get_calls.append(order_id)
        return real_get(order_id)

    monkeypatch.setattr(service.registry, "get", tracked_get)
    for name, arguments in tool_calls:
        denied = _call(mcp, name, {"order_id": "order-b", **arguments})
        assert denied["error"]["code"] == "OrderScopeDenied", name
    assert get_calls == []


def test_supply_scope_survives_blank_costing_for_confirm_write(
    service, monkeypatch
) -> None:
    pipeline, workflow, route_steps = _workflow_services(service)
    revision = _freeze(service, workflow, "order-supply", b"s")
    revision = workflow.bom_upsert(
        "order-supply",
        revision,
        [
            {
                "bom_node_id": "part-1",
                "parent_bom_id": None,
                "node_type": "MANUFACTURED_PART",
                "make_or_buy": "MAKE",
                "quantity": 2,
                "note": "part",
            }
        ],
        actor_role="tech",
    )["revision"]
    proposed = workflow.route_variants_propose(
        "order-supply", revision, route_steps, actor_role="tech"
    )
    frozen = workflow.route_freeze(
        "order-supply",
        proposed["revision"],
        proposed["variant_id"],
        actor_role="tech",
    )
    _, state = service.registry.get("order-supply")
    token = issue_order_scope(
        SECRET,
        order_id="order-supply",
        role="supply",
        profile="raschet-blank",
        stage="route_frozen",
        event_identity=latest_workflow_event_identity(
            state, "order-supply", "route_frozen"
        ),
        expires_at=FUTURE,
    )
    monkeypatch.setenv("METAL_CALC_SCOPE_SECRET", SECRET.hex())
    mcp = build_mcp(
        service,
        pipeline,
        workflow,
        role="supply",
        profile="raschet-blank",
    )

    blank = _call(
        mcp,
        "blank_drivers_set",
        {
            "order_id": "order-supply",
            "expected_revision": frozen["revision"],
            "material_code": "steel-40x",
            "mass_kg": "2.5",
            "mass_basis": "per_piece",
            "mass_note": "purchase",
            "items": [{"route_seq": 1, "cuts": 10, "note": "cut"}],
            "hermes_session_context": token,
        },
    )
    assert blank.get("error") is None, blank
    _, state_after_blank = service.registry.get("order-supply")
    assert state_after_blank["workflow"]["status"] == "DETAILED_COSTING"

    confirmed = _call(
        mcp,
        "supply_confirm",
        {
            "order_id": "order-supply",
            "expected_revision": blank["revision"],
            "rate_rub_per_kg": "97",
            "confirmed_by": "scope-test",
            "source_ref": "invoice-1",
            "hermes_session_context": token,
        },
    )
    assert confirmed.get("error") is None, confirmed
    assert confirmed["revision"] == blank["revision"] + 1


def test_route_return_issues_new_scope_that_can_revise_and_refreeze(
    service, monkeypatch
) -> None:
    pipeline, workflow, route_steps = _workflow_services(service)
    revision = _freeze(service, workflow, "order-return", b"r")
    _, input_state = service.registry.get("order-return")
    old_token = issue_order_scope(
        SECRET,
        order_id="order-return",
        role="tech",
        profile="raschet-route",
        stage="input_frozen",
        event_identity=latest_workflow_event_identity(
            input_state, "order-return", "input_frozen"
        ),
        expires_at=FUTURE,
    )
    revision = workflow.bom_upsert(
        "order-return",
        revision,
        [
            {
                "bom_node_id": "part-1",
                "parent_bom_id": None,
                "node_type": "MANUFACTURED_PART",
                "make_or_buy": "MAKE",
                "quantity": 2,
                "note": "part",
            }
        ],
        actor_role="tech",
    )["revision"]
    proposed = workflow.route_variants_propose(
        "order-return", revision, route_steps, actor_role="tech"
    )
    frozen = workflow.route_freeze(
        "order-return",
        proposed["revision"],
        proposed["variant_id"],
        actor_role="tech",
    )
    returned = workflow.route_return(
        "order-return",
        frozen["revision"],
        "revise route",
        actor_role="qa",
    )
    _, returned_state = service.registry.get("order-return")
    new_token = issue_order_scope(
        SECRET,
        order_id="order-return",
        role="tech",
        profile="raschet-route",
        stage="route_return",
        event_identity=latest_workflow_event_identity(
            returned_state, "order-return", "route_return"
        ),
        expires_at=FUTURE,
    )
    monkeypatch.setenv("METAL_CALC_SCOPE_SECRET", SECRET.hex())
    mcp = build_mcp(
        service,
        pipeline,
        workflow,
        role="tech",
        profile="raschet-route",
    )

    denied = _call(
        mcp,
        "order_get",
        {"order_id": "order-return", "hermes_session_context": old_token},
    )
    assert denied["error"]["code"] == "OrderScopeDenied"
    revised = _call(
        mcp,
        "route_variants_propose",
        {
            "order_id": "order-return",
            "expected_revision": returned["revision"],
            "steps": route_steps,
            "hermes_session_context": new_token,
        },
    )
    assert revised.get("error") is None, revised
    refrozen = _call(
        mcp,
        "route_freeze",
        {
            "order_id": "order-return",
            "expected_revision": revised["revision"],
            "variant_id": revised["variant_id"],
            "hermes_session_context": new_token,
        },
    )
    assert refrozen.get("error") is None, refrozen
    assert refrozen["status"] == "ROUTE_FROZEN"
