"""Behavior contracts for config-driven trusted MCP request context."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools import mcp_tool


def _tool():
    return SimpleNamespace(
        name="order_get",
        description="Read one order",
        inputSchema={
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "hermes_session_context": {"type": "string"},
            },
            "required": ["order_id", "hermes_session_context"],
        },
    )


def _result(text: str):
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        isError=False,
        structuredContent=None,
    )


def test_trusted_context_argument_is_absent_from_model_schema() -> None:
    config = {
        "context_arguments": {
            "order_get": {"hermes_session_context": "session_id"}
        }
    }
    context = mcp_tool._context_arguments_for_tool(config, "order_get")
    schema = mcp_tool._hide_mcp_context_arguments(
        mcp_tool._convert_mcp_schema("calculator", _tool()), context
    )

    assert schema["parameters"]["properties"] == {
        "order_id": {"type": "string"}
    }
    assert schema["parameters"]["required"] == ["order_id"]


def test_front_without_context_mapping_can_read_while_stage_role_requires_scope() -> None:
    """The same typed tool is interactive for front and scoped for stage roles."""
    from gateway.session_context import reset_session_vars

    reset_session_vars()
    session = MagicMock()
    session.call_tool = AsyncMock(return_value=_result("front-read"))
    server = SimpleNamespace(
        session=session, _rpc_lock=asyncio.Lock(),
        _pending_call_context=None, mark_tool_call=lambda: None,
    )

    def run(coro_or_factory, timeout=30):
        coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
        return asyncio.run(coro)

    with patch("tools.mcp_tool._get_connected_server_for_call", return_value=server), patch(
        "tools.mcp_tool._run_on_mcp_loop", side_effect=run
    ):
        front = mcp_tool._make_tool_handler("front", "order_get", 30)
        assert json.loads(front({"order_id": "A"}))["result"] == "front-read"
        session.call_tool.assert_awaited_once_with("order_get", arguments={"order_id": "A"})
        session.call_tool.reset_mock()
        tech = mcp_tool._make_tool_handler(
            "tech", "order_get", 30, {"hermes_session_context": "request_scope"}
        )
        assert "trusted request context" in str(json.loads(tech({"order_id": "A"}))["error"])
        session.call_tool.assert_not_awaited()


def test_invalid_or_missing_schema_context_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unsupported trusted"):
        mcp_tool._context_arguments_for_tool(
            {"context_arguments": {"x": {"hidden": "user_task"}}}, "x"
        )
    with pytest.raises(ValueError, match="absent from server schema"):
        mcp_tool._hide_mcp_context_arguments(
            {"parameters": {"type": "object", "properties": {}}},
            {"hidden": "session_id"},
        )


def test_dispatch_overwrites_forged_context_and_missing_context_never_calls() -> None:
    session = MagicMock()
    session.call_tool = AsyncMock(return_value=_result("accepted"))
    server = SimpleNamespace(
        session=session,
        _rpc_lock=asyncio.Lock(),
        _pending_call_context=None,
        mark_tool_call=lambda: None,
    )
    mcp_tool._servers["scope-test"] = server

    def run(coro_or_factory, timeout=30):
        coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
        return asyncio.run(coro)

    try:
        handler = mcp_tool._make_tool_handler(
            "scope-test",
            "order_get",
            30,
            {"hermes_session_context": "session_id"},
        )
        with patch("tools.mcp_tool._run_on_mcp_loop", side_effect=run):
            accepted = json.loads(
                handler(
                    {
                        "order_id": "B",
                        "hermes_session_context": "forged-by-model",
                    },
                    session_id="trusted-session",
                )
            )
        assert accepted["result"] == "accepted"
        session.call_tool.assert_awaited_once_with(
            "order_get",
            arguments={
                "order_id": "B",
                "hermes_session_context": "trusted-session",
            },
        )

        session.call_tool.reset_mock()
        denied = json.loads(handler({"order_id": "A"}))
        assert "trusted request context" in str(denied["error"])
        session.call_tool.assert_not_awaited()
    finally:
        mcp_tool._servers.pop("scope-test", None)


def test_cached_registration_hides_and_injects_trusted_context() -> None:
    from tools.registry import registry

    server_name = "scope-cache-contract"
    config = {
        "context_arguments": {
            "order_get": {"hermes_session_context": "session_id"}
        }
    }
    entry = {
        "tools": [
            {
                "name": "order_get",
                "description": "Read one order",
                "inputSchema": _tool().inputSchema,
            }
        ],
        "utility_tools": [],
    }
    session = MagicMock()
    session.call_tool = AsyncMock(return_value=_result("accepted"))
    server = SimpleNamespace(
        session=session,
        _rpc_lock=asyncio.Lock(),
        _pending_call_context=None,
        mark_tool_call=lambda: None,
    )
    mcp_tool._servers[server_name] = server

    def run(coro_or_factory, timeout=30):
        coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
        return asyncio.run(coro)

    registered = []
    try:
        registered = mcp_tool._register_from_cache_sync(
            server_name, config, entry
        )
        assert len(registered) == 1
        schema = registry.get_schema(registered[0])
        assert schema["parameters"]["properties"] == {
            "order_id": {"type": "string"}
        }
        assert schema["parameters"]["required"] == ["order_id"]

        with patch("tools.mcp_tool._run_on_mcp_loop", side_effect=run):
            response = registry.dispatch(
                registered[0],
                {
                    "order_id": "A",
                    "hermes_session_context": "forged-by-model",
                },
                session_id="trusted-session",
            )
        assert json.loads(response)["result"] == "accepted"
        session.call_tool.assert_awaited_once_with(
            "order_get",
            arguments={
                "order_id": "A",
                "hermes_session_context": "trusted-session",
            },
        )
    finally:
        for name in registered:
            registry.deregister(name)
        mcp_tool._servers.pop(server_name, None)


def test_request_scope_comes_only_from_task_local_gateway_context(
    monkeypatch,
) -> None:
    from gateway.session_context import clear_session_vars, set_session_vars

    monkeypatch.setenv("HERMES_TRUSTED_TOOL_SCOPE", "env-must-not-win")
    session = MagicMock()
    session.call_tool = AsyncMock(return_value=_result("accepted"))
    server = SimpleNamespace(
        session=session,
        _rpc_lock=asyncio.Lock(),
        _pending_call_context=None,
        mark_tool_call=lambda: None,
    )
    mcp_tool._servers["request-scope-test"] = server

    def run(coro_or_factory, timeout=30):
        coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
        return asyncio.run(coro)

    handler = mcp_tool._make_tool_handler(
        "request-scope-test",
        "order_get",
        30,
        {"hermes_session_context": "request_scope"},
    )
    tokens = set_session_vars(trusted_tool_scope="trusted-request-scope")
    try:
        with patch("tools.mcp_tool._run_on_mcp_loop", side_effect=run):
            accepted = json.loads(
                handler(
                    {
                        "order_id": "A",
                        "hermes_session_context": "forged-by-model",
                    }
                )
            )
        assert accepted["result"] == "accepted"
        session.call_tool.assert_awaited_once_with(
            "order_get",
            arguments={
                "order_id": "A",
                "hermes_session_context": "trusted-request-scope",
            },
        )
    finally:
        clear_session_vars(tokens)
        mcp_tool._servers.pop("request-scope-test", None)

    session.call_tool.reset_mock()
    denied = json.loads(handler({"order_id": "A"}))
    assert "trusted request context" in str(denied["error"])
    session.call_tool.assert_not_awaited()
