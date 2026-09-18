"""Same-name MCP servers keep profile-owned identity, policy, and overlays."""
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
                 "_server_profile_bindings", "_server_tool_scopes", "_server_adoptions",
                 "_orphaned_adopters", "_server_trust_levels", "_tool_read_only_hints",
                 "_native_profile_discovery_locks"):
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
    cfg_a = config("shared")
    cfg_a["metal_calc"]["headers"] = {"Authorization": "Bearer A"}
    cfg_b = config("shared")
    cfg_b["metal_calc"]["headers"] = {"Authorization": "Bearer B"}
    with home(root):
        mcp_tool.register_mcp_servers(cfg_a)
        root_entry = registry.get_entry("mcp__metal_calc__shared")
    with home(analysis):
        mcp_tool.register_mcp_servers(cfg_b)
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
        cfg["metal_calc"]["headers"] = {"Authorization": "Bearer A"}
        cfg["metal_calc"]["supports_parallel_tool_calls"] = True
        mcp_tool.register_mcp_servers(cfg)
        assert mcp_tool.is_mcp_tool_parallel_safe("mcp__metal_calc__shared")
        for _ in range(mcp_tool._CIRCUIT_BREAKER_THRESHOLD):
            mcp_tool._bump_server_error(connected[0].name)
        mcp_tool._record_connect_failure(connected[0].name)
    with home(analysis):
        cfg = config("shared")
        cfg["metal_calc"]["headers"] = {"Authorization": "Bearer B"}
        mcp_tool.register_mcp_servers(cfg)
        assert not mcp_tool._connect_cooldown_active(connected[1].name)
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


def test_toolset_resolution_memo_is_profile_scoped(setup_profiles):
    registry, _, root, analysis = setup_profiles
    import toolsets

    with home(root):
        mcp_tool.register_mcp_servers(config("shared"))
        assert toolsets.resolve_toolset("mcp-metal_calc") == [
            "mcp__metal_calc__shared"
        ]
    with home(analysis):
        assert registry.get_tool_names_for_toolset("mcp-metal_calc") == []
        assert toolsets.resolve_toolset("mcp-metal_calc") == []


def test_same_static_identity_shares_one_connection(setup_profiles, monkeypatch):
    registry, connected, root, analysis = setup_profiles
    cfg = config("shared")
    cfg["metal_calc"]["env"] = {"ACCOUNT": "same", "TOKEN": "same-token"}
    monkeypatch.setattr(mcp_tool, "_load_mcp_config", lambda: cfg)

    with home(root):
        mcp_tool.register_mcp_servers(cfg)
        root_entry = registry.get_entry("mcp__metal_calc__shared")
    with home(analysis):
        mcp_tool.register_mcp_servers(cfg)
        adopter_entry = registry.get_entry("mcp__metal_calc__shared")
        assert adopter_entry is not root_entry
        assert json.loads(adopter_entry.handler({}))["result"] == "shared"
        assert mcp_tool.get_mcp_status()[0]["status"] == "connected"

    assert len(connected) == 1
    assert connected[0].session.call_tool.call_count == 1


def test_different_static_credentials_never_share_connection(setup_profiles):
    registry, connected, root, analysis = setup_profiles
    cfg_a = config("shared")
    cfg_a["metal_calc"]["headers"] = {"Authorization": "Bearer A"}
    cfg_b = config("shared")
    cfg_b["metal_calc"]["headers"] = {"Authorization": "Bearer B"}

    with home(root):
        mcp_tool.register_mcp_servers(cfg_a)
        root_entry = registry.get_entry("mcp__metal_calc__shared")
    with home(analysis):
        mcp_tool.register_mcp_servers(cfg_b)
        profile_entry = registry.get_entry("mcp__metal_calc__shared")
        assert profile_entry is not root_entry
        assert json.loads(profile_entry.handler({}))["result"] == "shared"

    assert len(connected) == 2
    assert connected[0].session.call_tool.call_count == 0
    assert connected[1].session.call_tool.call_count == 1


@pytest.mark.parametrize(
    "identity",
    [
        {"url": "https://mcp.example/x", "auth": "oauth"},
        {
            "url": "https://mcp.example/x",
            "client_cert": "/certs/shared.pem",
            "client_key": "/certs/shared.key",
        },
    ],
    ids=["oauth", "mtls"],
)
def test_profile_bound_auth_never_shares_connection(setup_profiles, identity):
    _, connected, root, analysis = setup_profiles
    cfg = config("shared")
    cfg["metal_calc"].update(identity)

    with home(root):
        mcp_tool.register_mcp_servers(cfg)
    with home(analysis):
        mcp_tool.register_mcp_servers(cfg)

    assert len(connected) == 2


def test_adopter_keeps_its_own_trust_policy(
    setup_profiles, monkeypatch
):
    registry, connected, root, analysis = setup_profiles
    route = config("shared")
    route["metal_calc"]["headers"] = {"Authorization": "Bearer shared"}
    cfg_a = {"metal_calc": dict(route["metal_calc"], trust="full")}
    cfg_b = {"metal_calc": dict(route["metal_calc"], trust="untrusted")}
    asked = []

    monkeypatch.setattr(
        "tools.approval.request_elicitation_consent",
        lambda *args, **_kwargs: asked.append(args) or "deny",
    )

    with home(root):
        mcp_tool.register_mcp_servers(cfg_a)
        root_entry = registry.get_entry("mcp__metal_calc__shared")
    with home(analysis):
        mcp_tool.register_mcp_servers(cfg_b)
        adopter_entry = registry.get_entry("mcp__metal_calc__shared")
        assert "error" in json.loads(adopter_entry.handler({}))
        assert asked
        assert connected[0].session.call_tool.call_count == 0
    with home(root):
        assert "error" not in json.loads(root_entry.handler({}))

    assert len(connected) == 1
    assert len(asked) == 1


def test_owner_reload_restores_adopter_tools(
    setup_profiles, monkeypatch
):
    registry, connected, root, analysis = setup_profiles
    cfg = config("shared")
    cfg["metal_calc"]["headers"] = {"Authorization": "Bearer shared"}
    monkeypatch.setattr(mcp_tool, "_load_mcp_config", lambda: cfg)

    with home(root):
        mcp_tool.register_mcp_servers(cfg)
    with home(analysis):
        mcp_tool.register_mcp_servers(cfg)
        assert registry.get_tool_names_for_toolset("mcp-metal_calc") == [
            "mcp__metal_calc__shared"
        ]
    assert len(connected) == 1

    with home(root):
        mcp_tool.shutdown_mcp_servers()
    with home(analysis):
        assert registry.get_tool_names_for_toolset("mcp-metal_calc") == []

    with home(root):
        mcp_tool.register_mcp_servers(cfg)
    with home(analysis):
        assert registry.get_tool_names_for_toolset("mcp-metal_calc") == [
            "mcp__metal_calc__shared"
        ]
        assert mcp_tool.get_mcp_status()[0]["status"] == "connected"

    assert len(connected) == 2


def test_adopter_reload_keeps_owner_connection_alive(setup_profiles):
    registry, connected, root, analysis = setup_profiles
    cfg = config("shared")
    cfg["metal_calc"]["headers"] = {"Authorization": "Bearer shared"}

    with home(root):
        mcp_tool.register_mcp_servers(cfg)
        owner_entry = registry.get_entry("mcp__metal_calc__shared")
    with home(analysis):
        mcp_tool.register_mcp_servers(cfg)
        mcp_tool.shutdown_mcp_servers()
        assert registry.get_entry("mcp__metal_calc__shared") is None

    connected[0].shutdown.assert_not_called()
    with home(root):
        assert registry.get_entry("mcp__metal_calc__shared") is owner_entry
    with home(analysis):
        mcp_tool.register_mcp_servers(cfg)
        assert registry.get_entry("mcp__metal_calc__shared") is not None

    assert len(connected) == 1
