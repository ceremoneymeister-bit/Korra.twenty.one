"""Regression tests for safe stdio MCP recovery without side-effect replay.

A dead child detected before dispatch is safe to respawn and retry once. A
child or transport that disappears after dispatch is different: the external
effect may already have happened, so the session is restored for future calls
but the in-flight operation is reported as uncertain and is never replayed.
"""

import asyncio
import json
import threading
from unittest.mock import MagicMock

import pytest


def _success_result():
    result = MagicMock()
    result.is_error = False
    block = MagicMock()
    block.text = "ok"
    result.content = [block]
    result.structured_content = None
    result.meta = None
    return result


def _install_stub_server(
    mcp_tool_module,
    name: str,
    call_tool_impl,
    *,
    children_dead,
    on_reconnect=None,
):
    server = MagicMock()
    server.name = name
    session = MagicMock()
    session.call_tool = call_tool_impl
    server.session = session

    ready_flag = threading.Event()
    ready_flag.set()

    class _ReconnectAdapter:
        def __init__(self):
            self.set_calls = 0

        def set(self):
            self.set_calls += 1
            if on_reconnect is not None:
                on_reconnect(server)

    server._reconnect_event = _ReconnectAdapter()
    server._ready = ready_flag
    server._is_recycled_stdio.return_value = False
    server._stdio_children_dead = children_dead

    mcp_tool_module._servers[name] = server
    mcp_tool_module._server_error_counts.pop(name, None)
    mcp_tool_module._server_breaker_opened_at.pop(name, None)
    return server


def _cleanup(mcp_tool_module, name: str) -> None:
    mcp_tool_module._servers.pop(name, None)
    mcp_tool_module._server_error_counts.pop(name, None)
    mcp_tool_module._server_breaker_opened_at.pop(name, None)


def test_precall_dead_children_respawn_and_retry(monkeypatch, tmp_path):
    """A call that never reached the child is safe to retry exactly once."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from tools import mcp_tool
    from tools.mcp_tool import _make_tool_handler

    called = {"n": 0}
    alive = {"v": False}

    async def _call_tool(*args, **kwargs):
        called["n"] += 1
        return _success_result()

    def _respawn(server):
        alive["v"] = True
        new_session = MagicMock()
        new_session.call_tool = _call_tool
        server.session = new_session
        server._ready.set()

    server = _install_stub_server(
        mcp_tool,
        "srv-dead",
        _call_tool,
        children_dead=lambda: not alive["v"],
        on_reconnect=_respawn,
    )
    mcp_tool._ensure_mcp_loop()
    try:
        parsed = json.loads(_make_tool_handler("srv-dead", "tool1", 10.0)({}))
        assert parsed == {"result": "ok"}
        assert server._reconnect_event.set_calls == 1
        assert called["n"] == 1
        assert mcp_tool._server_error_counts.get("srv-dead", 0) == 0
    finally:
        _cleanup(mcp_tool, "srv-dead")


def test_midcall_child_exit_reconnects_without_replay(monkeypatch, tmp_path):
    """Watcher-first death after dispatch is ambiguous and is not replayed."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from tools import mcp_tool
    from tools.mcp_tool import _make_tool_handler

    alive = {"v": True}
    effects = {"n": 0}

    async def _effect_then_hang(*args, **kwargs):
        effects["n"] += 1
        alive["v"] = False
        await asyncio.sleep(30)

    async def _unexpected_replay(*args, **kwargs):
        effects["n"] += 1
        return _success_result()

    async def _watch_children():
        while alive["v"]:
            await asyncio.sleep(0.05)

    def _respawn(server):
        alive["v"] = True
        new_session = MagicMock()
        new_session.call_tool = _unexpected_replay
        server.session = new_session
        server._ready.set()

    server = _install_stub_server(
        mcp_tool,
        "srv-midcall",
        _effect_then_hang,
        children_dead=lambda: not alive["v"],
        on_reconnect=_respawn,
    )
    server._watch_stdio_children = _watch_children
    mcp_tool._ensure_mcp_loop()
    try:
        parsed = json.loads(_make_tool_handler("srv-midcall", "tool1", 10.0)({}))
        assert parsed["outcome_uncertain"] is True
        assert "may have completed" in parsed["error"]
        assert "did not replay" in parsed["error"]
        assert server._reconnect_event.set_calls == 1
        assert effects["n"] == 1
    finally:
        _cleanup(mcp_tool, "srv-midcall")


def test_sdk_first_transport_close_midcall_is_uncertain_without_replay(
    monkeypatch, tmp_path
):
    """The SDK commonly observes the closed pipe before the child watcher."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    anyio = pytest.importorskip("anyio")
    from tools import mcp_tool
    from tools.mcp_tool import _make_tool_handler

    effects = {"n": 0}

    async def _effect_then_pipe_closes(*args, **kwargs):
        effects["n"] += 1
        raise anyio.ClosedResourceError

    async def _unexpected_replay(*args, **kwargs):
        effects["n"] += 1
        return _success_result()

    async def _watch_children():
        await asyncio.sleep(30)

    def _respawn(server):
        new_session = MagicMock()
        new_session.call_tool = _unexpected_replay
        server.session = new_session
        server._ready.set()

    server = _install_stub_server(
        mcp_tool,
        "srv-sdk-first",
        _effect_then_pipe_closes,
        children_dead=lambda: False,
        on_reconnect=_respawn,
    )
    server._watch_stdio_children = _watch_children
    server._is_http = lambda: False
    mcp_tool._ensure_mcp_loop()
    try:
        parsed = json.loads(_make_tool_handler("srv-sdk-first", "tool1", 10.0)({}))
        assert parsed["outcome_uncertain"] is True
        assert "did not replay" in parsed["error"]
        assert server._reconnect_event.set_calls == 1
        assert effects["n"] == 1
    finally:
        _cleanup(mcp_tool, "srv-sdk-first")


def test_dead_child_never_returning_is_not_reported_as_a_timeout(
    monkeypatch, tmp_path
):
    """A failed pre-dispatch respawn reports a dead child, not a timeout."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from tools import mcp_tool
    from tools.mcp_tool import _make_tool_handler

    monkeypatch.setattr(mcp_tool, "_STDIO_RESPAWN_WAIT_SEC", 1.0)
    called = {"n": 0}

    async def _call_tool(*args, **kwargs):
        called["n"] += 1
        return _success_result()

    server = _install_stub_server(
        mcp_tool, "srv-gone", _call_tool, children_dead=lambda: True
    )
    mcp_tool._ensure_mcp_loop()
    try:
        parsed = json.loads(_make_tool_handler("srv-gone", "tool1", 300.0)({}))
        assert "exited" in parsed["error"]
        for forbidden in ("TimeoutError", "300s", "timed out"):
            assert forbidden not in parsed["error"]
        assert server._reconnect_event.set_calls == 1
        assert called["n"] == 0
        assert mcp_tool._server_error_counts.get("srv-gone", 0) == 1
    finally:
        _cleanup(mcp_tool, "srv-gone")


def test_child_dying_again_after_respawn_does_not_hot_cycle(monkeypatch, tmp_path):
    """A broken child gets one respawn request, never an internal retry loop."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from tools import mcp_tool
    from tools.mcp_tool import _make_tool_handler

    monkeypatch.setattr(mcp_tool, "_STDIO_RESPAWN_WAIT_SEC", 1.0)
    called = {"n": 0}

    async def _call_tool(*args, **kwargs):
        called["n"] += 1
        return _success_result()

    def _respawn_then_die(server):
        new_session = MagicMock()
        new_session.call_tool = _call_tool
        server.session = new_session
        server._ready.set()

    server = _install_stub_server(
        mcp_tool,
        "srv-flap",
        _call_tool,
        children_dead=lambda: True,
        on_reconnect=_respawn_then_die,
    )
    mcp_tool._ensure_mcp_loop()
    try:
        parsed = json.loads(_make_tool_handler("srv-flap", "tool1", 10.0)({}))
        assert "exited again" in parsed["error"]
        assert "do NOT retry" in parsed["error"]
        assert server._reconnect_event.set_calls == 1
        assert called["n"] == 0
        assert mcp_tool._server_error_counts.get("srv-flap", 0) == 1
    finally:
        _cleanup(mcp_tool, "srv-flap")
