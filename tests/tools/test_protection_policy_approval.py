"""Policy edits require an exact owner decision even with approvals off."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import approval as a


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    from gateway.session_context import reset_session_vars
    reset_session_vars()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text('approvals:\n  mode: "off"\nsecurity:\n  tirith_enabled: false\n')
    monkeypatch.setenv("HERMES_INTERACTIVE", "1")
    monkeypatch.setattr(a, "_YOLO_MODE_FROZEN", True)
    a._permanent_approved.clear()
    a.clear_session("default")
    return tmp_path


@pytest.mark.parametrize("command", [
    "korra config set approvals.mode off", "hermes config unset approvals.deny",
    'korra -p other config set security.protected_instruction_files false',
    "korra --profile=other config set --force approvals '{}'",
    "korra config unset agent", "korra config set skills.inline_shell true",
    "echo hi; korra config unset security",
    'sh -c "korra config set approvals.mode off"',
    "echo hi > $HERMES_HOME/config.yaml",
    "echo hi > $HOME/.hermes/profiles/other/config.yaml",
    "python3 -m korra_cli.main config set approvals.mode off",
    "korra config unset gateway.platforms.telegram",
    "korra config set platforms.telegram.allow_from '[]'",
    'curl -X PUT http://127.0.0.1:9119/api/config -d @settings.json',
])
def test_policy_detector_and_once_only_gate(command):
    assert a.detect_dangerous_command(command)[0]
    calls = []
    def approve(*args, **kw):
        calls.append(kw)
        return "once"
    for guard in (a.check_all_command_guards, a.check_dangerous_command):
        assert guard(command, "local", approve)["approved"]
        assert guard(command, "local", approve)["approved"]
    assert len(calls) == 4
    assert all(call["allow_session"] is False and call["allow_permanent"] is False for call in calls)
    assert not a._permanent_approved


@pytest.mark.parametrize("command", ["korra config set display.color true",
    "korra config set mcp.servers.example.command my-script", "korra config get approvals.mode",
    "korra config set gateway.platforms.telegram.reply_prefix hello",
    'echo "korra config set approvals.mode off"'])
def test_ordinary_config_and_scripts_stay_autonomous(command):
    assert not a._changes_protection_policy(command)
    assert a.check_all_command_guards(command, "local")["approved"]


def test_backup_file_is_not_policy():
    assert not a._changes_protection_policy("echo x > ~/.hermes/config.yaml.bak")


@pytest.mark.parametrize("name", ["KORRA_SESSION_OWNER", "HERMES_SESSION_OWNER", "KORRA_MANAGED_DIR", "HERMES_MANAGED_DIR"])
def test_runtime_authority_cannot_be_persisted_as_a_credential(name):
    from korra_cli.config import save_env_value
    with pytest.raises(ValueError, match="denylist"):
        save_env_value(name, "synthetic")


def test_gateway_owner_decides_once_for_each_operation():
    from gateway.session_context import set_session_vars, clear_session_vars
    tokens = set_session_vars(platform="telegram", chat_type="dm", user_id="42", owner_principal="live", session_key="policy-owner")
    shown = []
    def notify(data):
        shown.append(data)
        a.resolve_gateway_approval("policy-owner", "once")
    registration = a.register_gateway_notify("policy-owner", notify, attended=True)
    try:
        for _ in range(2):
            assert a.check_all_command_guards("korra config unset approvals", "local")["approved"]
        assert len(shown) == 2
        assert all(not data["allow_session"] and not data["allow_permanent"] for data in shown)
        assert not a.is_approved("policy-owner", a._PROTECTION_CHANGE_KEY)
    finally:
        a.unregister_gateway_notify("policy-owner", registration)
        a.clear_session("policy-owner")
        clear_session_vars(tokens)


@pytest.mark.parametrize("choice", ["deny", "timeout", "always", "session"])
def test_no_broad_or_missing_consent(choice):
    assert not a.check_all_command_guards("korra config unset security", "docker", lambda *x, **kw: choice)["approved"]


def test_no_human_and_visitor_are_blocked(monkeypatch):
    from gateway.session_context import set_session_vars, clear_session_vars
    monkeypatch.delenv("HERMES_INTERACTIVE")
    assert not a.check_all_command_guards("korra config unset security", "local")["approved"]
    tokens = set_session_vars(platform="telegram", user_id="visitor", chat_type="dm", owner_principal="")
    try:
        assert not a.check_all_command_guards("korra config unset security", "local", lambda *x, **kw: "once")["approved"]
    finally:
        clear_session_vars(tokens)


def test_hardline_still_wins():
    assert not a.check_all_command_guards("korra config unset security; reboot", "local", lambda *x, **kw: "once")["approved"]


def test_sibling_file_write_is_refused_and_owner_cli_still_works(home, monkeypatch):
    from tools import file_tools as ft
    monkeypatch.setattr(ft, "_hermes_config_resolved_loaded", False)
    target = home / "profiles/other/config.yaml"
    target.parent.mkdir(parents=True)
    assert json.loads(ft.write_file_tool(str(target), "approvals: {}"))["error"]
    assert not target.exists()
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "HERMES_HOME": str(home),
           "KORRA_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-m", "korra_cli.main", "config", "set", "approvals.mode", "off"],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
