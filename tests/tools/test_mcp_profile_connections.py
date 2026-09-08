"""Same-name MCP servers have separate connections and tools per profile."""
import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from korra_constants import set_hermes_home_override, reset_hermes_home_override
from tools import mcp_tool
from tools.registry import ToolRegistry


@contextmanager
def home(path):
    token = set_hermes_home_override(str(path))
    try:
        yield
    finally:
        reset_hermes_home_override(token)


@pytest.fixture
def setup_profiles(tmp_path, monkeypatch):
    registry = ToolRegistry()
    monkeypatch.setattr("tools.registry.registry", registry)
    monkeypatch.setattr("agent.secret_scope.is_multiplex_active", lambda: True)
    for attr in ("_servers", "_lazy_server_configs", "_lazy_server_tool_names", "_lazy_server_fingerprints",
                 "_server_connect_errors", "_server_connect_retry_after", "_server_connect_failures",
                 "_mcp_tool_server_names", "_server_error_counts", "_server_breaker_opened_at",
                 "_server_profile_bindings", "_native_profile_discovery_locks"):
        monkeypatch.setattr(mcp_tool, attr, {})
    for attr in ("_server_connecting", "_parallel_safe_servers", "_native_profiles_discovered"):
        monkeypatch.setattr(mcp_tool, attr, set())
    monkeypatch.setattr(mcp_tool, "_ensure_mcp_loop", lambda: None)
    monkeypatch.setattr(mcp_tool, "_stop_mcp_loop", lambda **kwargs: None)

    def run(factory, timeout):
        return asyncio.run(factory())
    monkeypatch.setattr(mcp_tool, "_run_on_mcp_loop", run)
    connected = []

    async def connect(name, config):
        names = config["args"]
        session = SimpleNamespace(call_tool=AsyncMock(return_value=CallToolResult(
            content=[TextContent(type="text", text=names[0])])),
            list_tools=AsyncMock())
        server = SimpleNamespace(name=name, session=session, _rpc_lock=asyncio.Lock(),
            tool_timeout=10, _tools=[Tool(name=n, inputSchema={"type": "object"}) for n in names],
            initialize_result=SimpleNamespace(capabilities=SimpleNamespace(tools=True)),
            _config=config, _registered_tool_names=[], _sampling=None)
        server.shutdown = AsyncMock(side_effect=lambda: mcp_tool.MCPServerTask._deregister_tools(server))
        connected.append(server)
        return server
    monkeypatch.setattr(mcp_tool, "_connect_server", connect)
    return registry, connected, tmp_path / "root", tmp_path / "analysis"


def config(*names):
    return {"metal_calc": {"command": "synthetic-mcp", "args": list(names),
        "tools": {"resources": False, "prompts": False}}}


def test_same_name_profiles_keep_exact_tools_and_dispatch(setup_profiles):
    registry, connected, root, analysis = setup_profiles
    with home(root):
        mcp_tool.register_mcp_servers(config("intake_context", "intake_sources", "intake_observation"))
        root_names = registry.get_tool_names_for_toolset("mcp-metal_calc")
        root_definitions = registry.get_definitions(set(root_names), quiet=True)
        assert len(root_definitions) == 3
        root_entry = registry.get_entry("mcp__metal_calc__intake_context")
        errors_before = dict(mcp_tool._server_error_counts)
    with home(analysis):
        mcp_tool.register_mcp_servers(config("analysis_context", "analysis_page", "analysis_submit"))
        assert registry.get_tool_names_for_toolset("mcp-metal_calc") == [
            "mcp__metal_calc__analysis_context", "mcp__metal_calc__analysis_page", "mcp__metal_calc__analysis_submit"]
        assert json.loads(registry.get_entry("mcp__metal_calc__analysis_context").handler({}))["result"] == "analysis_context"
        assert "error" in json.loads(root_entry.handler({})), "Captured foreign handler must fail closed"
        root_key = connected[0].name
        assert mcp_tool._server_error_counts.get(root_key) == errors_before.get(root_key)
    with home(root):
        assert registry.get_tool_names_for_toolset("mcp-metal_calc") == root_names
        assert registry.get_definitions(set(root_names), quiet=True) == root_definitions
        assert json.loads(root_entry.handler({}))["result"] == "intake_context"
        mcp_tool.register_mcp_servers(config("intake_context", "intake_sources", "intake_observation"))
    assert len(connected) == 2


def test_identical_tool_names_dispatch_and_teardown_stay_profile_owned(setup_profiles):
    registry, connected, root, analysis = setup_profiles
    with home(root):
        mcp_tool.register_mcp_servers(config("shared"))
        root_entry = registry.get_entry("mcp__metal_calc__shared")
    with home(analysis):
        mcp_tool.register_mcp_servers(config("shared"))
        child_entry = registry.get_entry("mcp__metal_calc__shared")
        assert child_entry is not root_entry
        child_entry.handler({})
        assert connected[0].session.call_tool.call_count == 0
        assert connected[1].session.call_tool.call_count == 1
        mcp_tool.shutdown_mcp_servers()
        assert registry.get_entry("mcp__metal_calc__shared") is None
    with home(root):
        assert registry.get_entry("mcp__metal_calc__shared") is root_entry
        assert registry.get_toolset_alias_target("metal_calc") == "mcp-metal_calc"
        root_entry.handler({})
        assert connected[0].session.call_tool.call_count == 1
        assert mcp_tool.get_registered_mcp_server_names() == {"metal_calc"}
    connected[0].shutdown.assert_not_called()
    connected[1].shutdown.assert_called_once()


def test_native_discovery_is_once_per_profile_and_resets_on_scoped_teardown(setup_profiles, monkeypatch):
    _, _, root, analysis = setup_profiles
    from tools.registry import registry
    seen = []
    monkeypatch.setattr(mcp_tool, "discover_mcp_tools", lambda: seen.append(registry.current_scope_key()))
    with home(root):
        mcp_tool.ensure_native_profile_mcp_tools()
        mcp_tool.ensure_native_profile_mcp_tools()
    with home(analysis):
        mcp_tool.ensure_native_profile_mcp_tools()
        mcp_tool.shutdown_mcp_servers()
        mcp_tool.ensure_native_profile_mcp_tools()
    assert seen == [str(root), str(analysis), str(analysis)]


def test_circuit_and_parallel_policy_do_not_cross_same_name_profiles(setup_profiles):
    registry, connected, root, analysis = setup_profiles
    with home(root):
        cfg = config("shared")
        cfg["metal_calc"]["supports_parallel_tool_calls"] = True
        mcp_tool.register_mcp_servers(cfg)
        assert mcp_tool.is_mcp_tool_parallel_safe("mcp__metal_calc__shared")
        for _ in range(mcp_tool._CIRCUIT_BREAKER_THRESHOLD):
            mcp_tool._bump_server_error(connected[0].name)
    with home(analysis):
        mcp_tool.register_mcp_servers(config("shared"))
        assert not mcp_tool.is_mcp_tool_parallel_safe("mcp__metal_calc__shared")
        assert "error" not in json.loads(registry.get_entry("mcp__metal_calc__shared").handler({}))
    with home(root):
        assert "unreachable" in registry.get_entry("mcp__metal_calc__shared").handler({})


def test_transient_initial_discovery_does_not_poison_native_marker(setup_profiles, monkeypatch):
    _, _, _, analysis = setup_profiles
    responses = iter([[], ["mcp__metal_calc__analysis_context"]])
    calls = []
    def discover():
        calls.append(1)
        return next(responses)
    monkeypatch.setattr(mcp_tool, "discover_mcp_tools", discover)
    monkeypatch.setattr(mcp_tool, "_load_mcp_config", lambda: config("analysis_context"))
    with home(analysis):
        mcp_tool.ensure_native_profile_mcp_tools()
        mcp_tool.ensure_native_profile_mcp_tools()
        mcp_tool.ensure_native_profile_mcp_tools()
    assert len(calls) == 2
