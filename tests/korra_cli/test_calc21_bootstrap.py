"""Fresh pilots have one gateway owner and three routed calculator roles."""

import json
import sys

import yaml

from calculator.deploy import bootstrap
from korra_cli.container_boot import reconcile_profile_gateways


def test_bootstrap_role_config_and_native_multiplex_reconcile(tmp_path, monkeypatch):
    root = tmp_path / "pilot"
    prompts = tmp_path / "prompts"
    (root / "rates").mkdir(parents=True)
    (root / "rates" / "_active.json").write_text("{}")
    for name in bootstrap.ROLES:
        home = prompts if name == "default" else prompts / "profiles" / name
        home.mkdir(parents=True, exist_ok=True)
        (home / "SOUL.md").write_text("Calculator role")
    monkeypatch.setattr(sys, "argv", ["bootstrap", "--root", str(root), "--prompts", str(prompts)])
    monkeypatch.setattr(bootstrap.os, "chown", lambda *args: None)
    bootstrap.main()
    data = root / "data"
    root_config = yaml.safe_load((data / "config.yaml").read_text())
    names = set(bootstrap.ROLES) - {"default"}
    assert root_config["gateway"]["multiplex_profiles"] is True
    assert set(root_config["gateway"]["multiplex_profile_allowlist"]) == names
    receiver = root_config["mcp_servers"]["metal_calc"]
    assert receiver["args"] == ["--intake-only", "--session-db", "/opt/data/state.db"]
    assert receiver["context_arguments"] == {
        tool: {"session_id": "session_id"}
        for tool in ("intake_context", "intake_sources", "intake_observation")
    }
    policy = yaml.safe_load((root / "policy/config.yaml").read_text())
    assert "args" not in policy["mcp_servers"]["metal_calc"]
    assert policy["platform_toolsets"]["api_server"] == ["metal_calc"]
    for name in names:
        profile = data / "profiles" / name
        assert json.loads((profile / "gateway_state.json").read_text())["gateway_state"] == "stopped"
        config = yaml.safe_load((profile / "config.yaml").read_text())
        assert config["mcp_servers"]["metal_calc"]["context_arguments"]["order_get"] == {
            "hermes_session_context": "request_scope",
        }
        # Simulate an older pilot's persisted named-profile run intent.
        (profile / "gateway_state.json").write_text('{"gateway_state":"running"}')

    # This native pre-config switch is essential even if YAML enables mux.
    monkeypatch.setenv("GATEWAY_MULTIPLEX_PROFILES", "1")
    actions = reconcile_profile_gateways(
        hermes_home=data, scandir=tmp_path / "service", dry_run=True,
        container_argv=["gateway", "run"],
    )
    assert {action.profile: action.action for action in actions} == {
        "default": "started", **{name: "registered" for name in names},
    }
