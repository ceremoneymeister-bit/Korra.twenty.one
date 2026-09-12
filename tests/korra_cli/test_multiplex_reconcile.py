"""K21-058: agent tabs must work on any contour right after install/update.

A contour that arrived from 0.20.x had named profiles with their own
gateways, no ``gateway.multiplex_profiles`` flag, no root ``API_SERVER_KEY``
in profile ``.env`` files and no ``platforms.api_server.enabled: false`` pin.
Every tab except the main one answered 404. The boot reconcile repairs all
three, ``korra doctor`` names whatever is still missing, and an explicit
``multiplex_profiles: false`` is left alone.
"""

from __future__ import annotations

import json
import os
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from korra_cli import multiplex_reconcile as mr


ROOT_KEY = "root-key-0123456789abcdef0123456789abcdef"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _profile(home: Path, name: str, *, key: str | None = None, pin: bool = False,
             state: str | None = None, telegram: bool = True) -> Path:
    p = home / "profiles" / name
    _write(p / "SOUL.md", f"# {name}\n")
    env_lines = []
    if telegram:
        env_lines.append("TELEGRAM_BOT_TOKEN=123:abc")
    if key:
        env_lines.append(f"API_SERVER_KEY={key}")
    _write(p / ".env", "\n".join(env_lines) + "\n")
    cfg = {"model": {"provider": "custom:dario", "model": "claude-sonnet-5"}}
    if pin:
        cfg["platforms"] = {"api_server": {"enabled": False}}
    _write(p / "config.yaml", yaml.safe_dump(cfg, sort_keys=False))
    if state:
        _write(p / "gateway_state.json", json.dumps({
            "gateway_state": state, "desired_state": state, "timestamp": 1,
        }))
    return p


def _legacy_0_20_home(tmp_path: Path) -> Path:
    """Pavlova-shaped contour: four profiles, own gateways, no multiplex."""
    home = tmp_path / "data"
    _write(home / "config.yaml", (
        "# Korra 0.20 контур\n"
        "model:\n  provider: custom:dario\n  model: claude-sonnet-5  # основной\n"
        "gateway:\n  streaming: true\n"
    ))
    _write(home / ".env", f"API_SERVER_KEY={ROOT_KEY}\nTELEGRAM_BOT_TOKEN=1:root\n")
    for name in ("agent-architect", "figma-storybook", "fitness-assistant", "study-assistant"):
        _profile(home, name, state="running")
    return home


# ── inspect ──────────────────────────────────────────────────────────────────


def test_inspect_names_all_three_missing_items_per_profile(tmp_path):
    home = _legacy_0_20_home(tmp_path)

    report = mr.inspect_multiplex(home)

    assert report.flag is None
    assert report.root_key_present is True
    assert report.named_profiles == 4
    assert report.tabs_work is False
    for finding in report.profiles:
        assert finding.missing == [mr.MISSING_KEY, mr.MISSING_PIN, mr.GATEWAY_RUNNING]
        text = finding.describe()
        assert "API_SERVER_KEY" in text
        assert "platforms.api_server.enabled: false" in text
        assert "running" in text


def test_inspect_healthy_contour_reports_tabs_work(tmp_path):
    home = tmp_path / "data"
    _write(home / "config.yaml", "gateway:\n  multiplex_profiles: true\n")
    _write(home / ".env", f"API_SERVER_KEY={ROOT_KEY}\n")
    _profile(home, "designer", key=ROOT_KEY, pin=True, state="stopped")
    _profile(home, "lawyer", key=ROOT_KEY, pin=True)

    report = mr.inspect_multiplex(home)

    assert report.flag is True
    assert report.tabs_work is True
    assert report.broken_profiles == []


def test_inspect_detects_foreign_key_and_ignores_deleted_profiles(tmp_path):
    home = tmp_path / "data"
    _write(home / "config.yaml", "gateway:\n  multiplex_profiles: true\n")
    _write(home / ".env", f"API_SERVER_KEY={ROOT_KEY}\n")
    _profile(home, "designer", key="some-other-key", pin=True)
    gone = _profile(home, "gone", key=ROOT_KEY, pin=True)
    from korra_constants import mark_named_profile_deleted

    mark_named_profile_deleted(gone)
    (home / "profiles" / "backup-copy").mkdir()  # no SOUL.md → not a profile

    report = mr.inspect_multiplex(home)

    assert [p.name for p in report.profiles] == ["designer"]
    assert report.profiles[0].missing == [mr.MISSING_KEY]


# ── reconcile ────────────────────────────────────────────────────────────────


def test_reconcile_repairs_legacy_contour_and_is_idempotent(tmp_path):
    home = _legacy_0_20_home(tmp_path)
    env_inode = os.stat(home / "profiles" / "agent-architect" / ".env").st_ino

    report = mr.reconcile_multiplex(home)

    assert report.errors == []
    assert report.flag is True
    assert report.tabs_work is True, report
    assert any("gateway.multiplex_profiles: true" in a for a in report.actions)
    assert len([a for a in report.actions if "API_SERVER_KEY" in a]) == 4
    assert len([a for a in report.actions if "api_server.enabled: false" in a]) == 4
    assert len([a for a in report.actions if "desired_state: stopped" in a]) == 4

    root_text = (home / "config.yaml").read_text(encoding="utf-8")
    assert "# Korra 0.20 контур" in root_text, "comments must survive the flag write"
    assert "# основной" in root_text
    assert yaml.safe_load(root_text)["gateway"] == {"streaming": True, "multiplex_profiles": True}

    profile = home / "profiles" / "agent-architect"
    env_text = (profile / ".env").read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=123:abc" in env_text
    assert f"API_SERVER_KEY={ROOT_KEY}" in env_text
    assert os.stat(profile / ".env").st_ino == env_inode, "must edit the same inode"
    cfg = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["platforms"]["api_server"]["enabled"] is False
    assert cfg["model"]["model"] == "claude-sonnet-5"
    state = json.loads((profile / "gateway_state.json").read_text(encoding="utf-8"))
    assert state["desired_state"] == "stopped"
    assert state["gateway_state"] == "stopped"

    again = mr.reconcile_multiplex(home)
    assert again.actions == []
    assert again.tabs_work is True


def test_reconcile_replaces_stale_profile_key_in_place(tmp_path):
    home = tmp_path / "data"
    _write(home / "config.yaml", "gateway:\n  multiplex_profiles: true\n")
    _write(home / ".env", f"API_SERVER_KEY={ROOT_KEY}\n")
    _profile(home, "designer", key="old-key", pin=True)

    report = mr.reconcile_multiplex(home)

    env_text = (home / "profiles" / "designer" / ".env").read_text(encoding="utf-8")
    assert env_text.count("API_SERVER_KEY=") == 1
    assert f"API_SERVER_KEY={ROOT_KEY}" in env_text
    assert report.tabs_work is True


def test_reconcile_respects_explicit_false(tmp_path):
    home = tmp_path / "data"
    _write(home / "config.yaml", "gateway:\n  multiplex_profiles: false\n")
    _write(home / ".env", f"API_SERVER_KEY={ROOT_KEY}\n")
    _profile(home, "designer", state="running")
    before = (home / "profiles" / "designer" / ".env").read_text(encoding="utf-8")

    report = mr.reconcile_multiplex(home)

    assert report.flag is False
    assert report.actions == []
    assert (home / "profiles" / "designer" / ".env").read_text(encoding="utf-8") == before
    assert json.loads((home / "profiles" / "designer" / "gateway_state.json").read_text())["desired_state"] == "running"


def test_reconcile_dry_run_changes_nothing(tmp_path):
    home = _legacy_0_20_home(tmp_path)
    snapshot = {p: p.read_text(encoding="utf-8") for p in home.rglob("*") if p.is_file()}

    report = mr.reconcile_multiplex(home, dry_run=True)

    assert report.actions and all(a.startswith("[dry-run]") for a in report.actions)
    assert {p: p.read_text(encoding="utf-8") for p in home.rglob("*") if p.is_file()} == snapshot


def test_reconcile_without_root_key_reports_instead_of_inventing_one(tmp_path):
    home = tmp_path / "data"
    _write(home / "config.yaml", "gateway:\n  multiplex_profiles: true\n")
    _write(home / ".env", "TELEGRAM_BOT_TOKEN=1:root\n")
    _profile(home, "designer")

    report = mr.reconcile_multiplex(home)

    assert any("API_SERVER_KEY" in e for e in report.errors)
    assert "API_SERVER_KEY" not in (home / "profiles" / "designer" / ".env").read_text()
    assert report.tabs_work is False


def test_reconcile_creates_missing_profile_config_and_env(tmp_path):
    home = tmp_path / "data"
    _write(home / "config.yaml", "gateway:\n  multiplex_profiles: true\n")
    _write(home / ".env", f"API_SERVER_KEY={ROOT_KEY}\n")
    bare = home / "profiles" / "bare"
    _write(bare / "SOUL.md", "# bare\n")

    report = mr.reconcile_multiplex(home)

    assert report.tabs_work is True
    assert yaml.safe_load((bare / "config.yaml").read_text()) == {
        "platforms": {"api_server": {"enabled": False}}
    }
    assert f"API_SERVER_KEY={ROOT_KEY}" in (bare / ".env").read_text()


# ── container boot integration ───────────────────────────────────────────────


def test_container_boot_parks_named_gateways_under_config_flag(tmp_path, monkeypatch):
    from korra_cli.container_boot import reconcile_profile_gateways

    monkeypatch.setattr("korra_cli.container_boot._read_container_argv", lambda: ())
    monkeypatch.delenv("GATEWAY_MULTIPLEX_PROFILES", raising=False)
    home = _legacy_0_20_home(tmp_path)
    _write(home / "gateway_state.json", json.dumps({"gateway_state": "running", "timestamp": 1}))
    scandir = tmp_path / "service"
    scandir.mkdir()

    actions = reconcile_profile_gateways(hermes_home=home, scandir=scandir)

    by_name = {a.profile: a for a in actions}
    assert by_name["default"].action == "started"
    for name in ("agent-architect", "figma-storybook", "fitness-assistant", "study-assistant"):
        assert by_name[name].action == "registered", name
    assert mr.inspect_multiplex(home).tabs_work is True


def test_container_boot_dry_run_does_not_repair(tmp_path, monkeypatch):
    from korra_cli.container_boot import reconcile_profile_gateways

    monkeypatch.setattr("korra_cli.container_boot._read_container_argv", lambda: ())
    monkeypatch.delenv("GATEWAY_MULTIPLEX_PROFILES", raising=False)
    home = _legacy_0_20_home(tmp_path)

    reconcile_profile_gateways(hermes_home=home, scandir=tmp_path / "svc", dry_run=True)

    assert mr.inspect_multiplex(home).flag is None


def test_container_boot_env_false_wins_over_config(tmp_path, monkeypatch):
    from korra_cli.container_boot import reconcile_profile_gateways

    monkeypatch.setattr("korra_cli.container_boot._read_container_argv", lambda: ())
    monkeypatch.setenv("GATEWAY_MULTIPLEX_PROFILES", "false")
    home = tmp_path / "data"
    _write(home / "config.yaml", "gateway:\n  multiplex_profiles: true\n")
    _write(home / ".env", f"API_SERVER_KEY={ROOT_KEY}\n")
    _profile(home, "designer", key=ROOT_KEY, pin=True, state="running")
    scandir = tmp_path / "service"
    scandir.mkdir()

    actions = reconcile_profile_gateways(hermes_home=home, scandir=scandir)

    assert {a.profile: a.action for a in actions}["designer"] == "started"


# ── doctor ───────────────────────────────────────────────────────────────────


def test_doctor_names_missing_items_and_fixes_them(tmp_path, capsys):
    from korra_cli import doctor as doctor_mod

    home = _legacy_0_20_home(tmp_path)
    issues: list[str] = []

    doctor_mod.check_multiplex_profiles(should_fix=False, issues=issues, hermes_home=home)
    out = capsys.readouterr().out
    assert "Вкладки агентов" in out
    assert "gateway.multiplex_profiles не задан" in out
    assert "agent-architect" in out
    assert "API_SERVER_KEY" in out
    assert "platforms.api_server.enabled: false" in out
    assert issues and any("korra doctor --fix" in i for i in issues)

    fixed_issues: list[str] = []
    doctor_mod.check_multiplex_profiles(should_fix=True, issues=fixed_issues, hermes_home=home)
    out = capsys.readouterr().out
    assert "Исправлено" in out
    assert "gateway.multiplex_profiles: true" in out
    assert fixed_issues == []
    assert mr.inspect_multiplex(home).tabs_work is True


def test_doctor_is_quiet_for_single_profile_contour(tmp_path, capsys):
    from korra_cli import doctor as doctor_mod

    home = tmp_path / "data"
    _write(home / "config.yaml", "gateway:\n  multiplex_profiles: true\n")
    _write(home / ".env", f"API_SERVER_KEY={ROOT_KEY}\n")

    doctor_mod.check_multiplex_profiles(should_fix=False, issues=[], hermes_home=home)

    assert "Вкладки агентов" not in capsys.readouterr().out


def test_doctor_warns_but_does_not_flip_explicit_false(tmp_path, capsys):
    from korra_cli import doctor as doctor_mod

    home = tmp_path / "data"
    _write(home / "config.yaml", "gateway:\n  multiplex_profiles: false\n")
    _write(home / ".env", f"API_SERVER_KEY={ROOT_KEY}\n")
    _profile(home, "designer")
    issues: list[str] = []

    doctor_mod.check_multiplex_profiles(should_fix=True, issues=issues, hermes_home=home)

    out = capsys.readouterr().out
    assert "решение владельца" in out
    assert issues == []
    assert mr.inspect_multiplex(home).flag is False


def test_run_doctor_reaches_multiplex_section(monkeypatch, tmp_path):
    """The section is wired into run_doctor, not only importable."""
    from korra_cli import doctor as doctor_mod

    called = {}

    def _fake(should_fix=False, issues=None, hermes_home=None):
        called["should_fix"] = should_fix

    monkeypatch.setattr(doctor_mod, "check_multiplex_profiles", _fake)
    assert callable(doctor_mod.check_multiplex_profiles)
    assert "check_multiplex_profiles(should_fix=should_fix, issues=issues)" in Path(doctor_mod.__file__).read_text(encoding="utf-8")
