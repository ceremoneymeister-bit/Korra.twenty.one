"""Tests for Google Workspace gws bridge and CLI wrapper."""

import importlib.util
import json
import subprocess
import sys
import threading
import time
import types
from contextlib import contextmanager
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


BRIDGE_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills/productivity/google-workspace/scripts/gws_bridge.py"
)
API_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills/productivity/google-workspace/scripts/google_api.py"
)


@pytest.fixture
def bridge_module(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    spec = importlib.util.spec_from_file_location("gws_bridge_test", BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module._native_google._credentials = lambda *_args, **_kwargs: SimpleNamespace(
        token=json.loads(module.get_token_path().read_text(encoding="utf-8"))["token"]
    )
    return module


@pytest.fixture
def api_module(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    spec = importlib.util.spec_from_file_location("gws_api_test", API_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    # Ensure the gws CLI code path is taken even when the binary isn't
    # installed (CI).  Without this, calendar_list() falls through to the
    # Python SDK path which imports ``googleapiclient`` — not in deps.
    module._gws_binary = lambda: "/usr/bin/gws"
    _write_token(module.TOKEN_PATH, scopes=module.SCOPES)
    module._native_google._credentials = lambda *_args, **_kwargs: SimpleNamespace(
        token=json.loads(module.TOKEN_PATH.read_text(encoding="utf-8"))["token"]
    )
    return module


def _write_token(path: Path, *, token="ya29.test", expiry=None, **extra):
    if expiry is None:
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    data = {
        "token": token,
        "refresh_token": "1//refresh",
        "client_id": "123.apps.googleusercontent.com",
        "client_secret": "secret",
        "token_uri": "https://oauth2.googleapis.com/token",
        **extra,
    }
    data["expiry"] = expiry
    path.write_text(json.dumps(data))


def test_skill_wrappers_use_shared_source_token_and_lock(monkeypatch, tmp_path):
    root = tmp_path / "install"
    source = root / "profiles" / "assistant"
    consumer = root / "profiles" / "rop"
    source_google = source / "google-workspace"
    consumer.mkdir(parents=True)
    source_google.mkdir(parents=True)
    scopes = ["https://www.googleapis.com/auth/drive"]
    _write_token(
        source_google / "token.json",
        scopes=scopes,
        korra_services=["drive"],
        korra_requested_scopes=scopes,
    )
    root_google = root / "google-workspace"
    root_google.mkdir()
    (root_google / "shared-access.json").write_text(
        json.dumps({"version": 1, "profile_sources": {"rop": "assistant"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(consumer))

    spec = importlib.util.spec_from_file_location("gws_api_shared_test", API_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    locked_homes = []

    @contextmanager
    def capture_lock(home):
        locked_homes.append(home)
        yield

    monkeypatch.setattr(module._native_google, "_state_lock", capture_lock)
    monkeypatch.setattr(
        module._native_google,
        "_credentials",
        lambda *_args, **_kwargs: SimpleNamespace(token="ya29.shared"),
    )
    monkeypatch.setattr(module, "_gws_binary", lambda: "/usr/bin/gws")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: MagicMock(returncode=0, stdout="{}", stderr=""),
    )

    assert module.GRANT_HOME == source
    assert module.TOKEN_PATH == source_google / "token.json"
    assert module._run_gws(["drive", "files", "list"]) == {}
    assert locked_homes == [source]

    bridge_spec = importlib.util.spec_from_file_location("gws_bridge_shared_test", BRIDGE_PATH)
    bridge = importlib.util.module_from_spec(bridge_spec)
    assert bridge_spec.loader is not None
    bridge_spec.loader.exec_module(bridge)
    assert bridge.get_token_path() == source_google / "token.json"


def test_bridge_returns_valid_token(bridge_module, tmp_path):
    """Non-expired token is returned without refresh."""
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    token_path = bridge_module.get_token_path()
    _write_token(
        token_path,
        token="ya29.valid",
        expiry=future,
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )

    result = bridge_module.get_valid_token()
    assert result == "ya29.valid"


def test_bridge_rejects_unknown_legacy_scope(bridge_module):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    _write_token(
        bridge_module.get_token_path(),
        expiry=future,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )

    with pytest.raises(SystemExit):
        bridge_module.get_valid_token()


def test_bridge_blocks_unselected_service(bridge_module):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    scopes = ["https://www.googleapis.com/auth/drive"]
    _write_token(
        bridge_module.get_token_path(),
        expiry=future,
        scopes=scopes,
        korra_services=["drive"],
        korra_requested_scopes=scopes,
    )

    with pytest.raises(SystemExit):
        bridge_module.get_valid_token("docs")










def test_bridge_main_injects_token_env(bridge_module, tmp_path):
    """main() sets GOOGLE_WORKSPACE_CLI_TOKEN in subprocess env."""
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    token_path = bridge_module.get_token_path()
    _write_token(
        token_path,
        token="ya29.injected",
        expiry=future,
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )

    captured = {}

    def capture_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})
        return MagicMock(returncode=0)

    with patch.object(sys, "argv", ["gws_bridge.py", "gmail", "+triage"]):
        with patch.object(subprocess, "run", side_effect=capture_run):
            with pytest.raises(SystemExit):
                bridge_module.main()

    assert captured["env"]["GOOGLE_WORKSPACE_CLI_TOKEN"] == "ya29.injected"
    assert captured["cmd"] == ["gws", "gmail", "+triage"]


def test_api_calendar_list_uses_events_list(api_module):
    """calendar_list calls _run_gws with events list + params."""
    captured = {}

    def capture_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stdout="{}", stderr="")

    args = api_module.argparse.Namespace(
        start="", end="", max=25, calendar="primary", func=api_module.calendar_list,
    )

    with patch.object(api_module.subprocess, "run", side_effect=capture_run):
        api_module.calendar_list(args)

    cmd = captured["cmd"]
    # _gws_binary() returns "/usr/bin/gws", so cmd[0] is that binary
    assert cmd[0] == "/usr/bin/gws"
    assert "calendar" in cmd
    assert "events" in cmd
    assert "list" in cmd
    assert "--params" in cmd
    params = json.loads(cmd[cmd.index("--params") + 1])
    assert "timeMin" in params
    assert "timeMax" in params
    assert params["calendarId"] == "primary"


def test_gws_path_rejects_invalid_scope_contract_before_subprocess(api_module, monkeypatch):
    scopes = ["https://www.googleapis.com/auth/calendar.events"]
    _write_token(
        api_module.TOKEN_PATH,
        scopes=scopes,
        korra_services=["calendar"],
        korra_requested_scopes=["https://www.googleapis.com/auth/drive"],
    )
    monkeypatch.setattr(
        api_module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("gws must not run for an invalid scope contract"),
    )

    with pytest.raises(SystemExit):
        api_module._run_gws(["calendar", "events", "list"])


def test_tracked_drive_sheets_calendar_token_blocks_docs_dispatch(
    api_module, monkeypatch,
):
    selected_scopes = [
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    _write_token(
        api_module.TOKEN_PATH,
        scopes=selected_scopes,
        korra_services=["calendar", "drive", "sheets"],
        korra_requested_scopes=selected_scopes,
    )
    monkeypatch.setattr(
        api_module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("docs command must be blocked before gws"),
    )

    with pytest.raises(SystemExit):
        api_module._run_gws(["docs", "documents", "get"])


def test_gws_token_mutation_is_detected_without_rewriting_it(api_module, monkeypatch):
    selected_scopes = ["https://www.googleapis.com/auth/calendar.events"]
    _write_token(
        api_module.TOKEN_PATH,
        scopes=selected_scopes,
        korra_services=["calendar"],
        korra_requested_scopes=selected_scopes,
    )

    def rewrite_token(*args, **kwargs):
        _write_token(api_module.TOKEN_PATH, token="ya29.refreshed", scopes=selected_scopes)
        return MagicMock(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(api_module.subprocess, "run", rewrite_token)

    with pytest.raises(SystemExit):
        api_module._run_gws(["calendar", "events", "list"])
    payload = json.loads(api_module.TOKEN_PATH.read_text(encoding="utf-8"))
    assert "korra_services" not in payload
    assert "korra_requested_scopes" not in payload


def test_native_refresh_precedes_gws_snapshot_and_private_runtime(api_module, monkeypatch):
    selected_scopes = ["https://www.googleapis.com/auth/calendar.events"]
    _write_token(
        api_module.TOKEN_PATH,
        token="ya29.expired",
        scopes=selected_scopes,
        korra_services=["calendar"],
        korra_requested_scopes=selected_scopes,
    )
    captured = {}

    def refresh_before_snapshot(*_args, **_kwargs):
        payload = json.loads(api_module.TOKEN_PATH.read_text(encoding="utf-8"))
        payload["token"] = "ya29.refreshed"
        api_module.TOKEN_PATH.write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(token="ya29.refreshed")

    def successful_gws(_cmd, **kwargs):
        env = kwargs["env"]
        home = Path(env["HOME"])
        captured.update(env=env, cwd=kwargs["cwd"], home=home)
        assert kwargs["cwd"] == home
        assert home.is_dir()
        assert env["GOOGLE_WORKSPACE_CLI_TOKEN"] == "ya29.refreshed"
        for variable in (
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_RUNTIME_DIR",
            "XDG_STATE_HOME",
        ):
            path = Path(env[variable])
            assert path.parent == home
            assert path.is_dir()
        assert "HERMES_HOME" not in env
        assert "KORRA_HOME" not in env
        assert "KORRA_GOOGLE_OAUTH_CLIENT_PATH" not in env
        return MagicMock(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(api_module._native_google, "_credentials", refresh_before_snapshot)
    monkeypatch.setattr(api_module.subprocess, "run", successful_gws)

    assert api_module._run_gws(["calendar", "events", "list"]) == {}
    assert json.loads(api_module.TOKEN_PATH.read_text(encoding="utf-8"))["token"] == "ya29.refreshed"
    assert not captured["home"].exists()


def test_revoke_waits_for_inflight_gws_execution(api_module, monkeypatch):
    selected_scopes = ["https://www.googleapis.com/auth/calendar.events"]
    _write_token(
        api_module.TOKEN_PATH,
        token="ya29.active",
        scopes=selected_scopes,
        korra_services=["calendar"],
        korra_requested_scopes=selected_scopes,
    )
    gws_entered = threading.Event()
    release_gws = threading.Event()
    errors = []
    gws_result = {}
    revoke_result = {}
    remote_values = []

    monkeypatch.setattr(
        api_module._native_google,
        "_credentials",
        lambda *_args, **_kwargs: SimpleNamespace(token="ya29.active"),
    )

    def blocking_gws(*_args, **_kwargs):
        gws_entered.set()
        assert release_gws.wait(2)
        return MagicMock(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(api_module.subprocess, "run", blocking_gws)

    def run_gws():
        try:
            gws_result.update(api_module._run_gws(["calendar", "events", "list"]))
        except BaseException as exc:
            errors.append(exc)

    def run_revoke():
        try:
            revoke_result.update(
                api_module._native_google.revoke(
                    profile_home=api_module.HERMES_HOME,
                    remote_revoke=lambda value: remote_values.append(value),
                )
            )
        except BaseException as exc:
            errors.append(exc)

    gws_thread = threading.Thread(target=run_gws)
    revoke_thread = threading.Thread(target=run_revoke)
    gws_thread.start()
    assert gws_entered.wait(2)
    revoke_thread.start()
    time.sleep(0.05)
    assert revoke_thread.is_alive()
    release_gws.set()
    gws_thread.join(2)
    revoke_thread.join(2)

    assert not gws_thread.is_alive()
    assert not revoke_thread.is_alive()
    assert errors == []
    assert gws_result == {}
    assert revoke_result == {"status": "revoked", "remote_revoked": True}
    assert remote_values == ["1//refresh"]
    assert not api_module.TOKEN_PATH.exists()












def test_api_get_credentials_delegates_to_native_writer(api_module, monkeypatch):
    token_path = api_module.TOKEN_PATH
    selected_scopes = [
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    _write_token(
        token_path,
        token="ya29.old",
        scopes=selected_scopes,
        korra_services=["calendar", "drive", "sheets"],
        korra_requested_scopes=selected_scopes,
    )

    marker = SimpleNamespace(token="ya29.native")
    calls = []
    monkeypatch.setattr(
        api_module._native_google,
        "_credentials",
        lambda *args, **kwargs: calls.append((args, kwargs)) or marker,
    )

    creds = api_module.get_credentials()

    saved = json.loads(token_path.read_text())
    assert creds is marker
    assert calls == [((api_module.HERMES_HOME,), {})]
    assert saved["token"] == "ya29.old"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"scopes": []},
        {"scopes": ["https://www.googleapis.com/auth/cloud-platform"]},
        {
            "scopes": ["https://www.googleapis.com/auth/calendar.events"],
            "korra_services": ["calendar"],
            "korra_requested_scopes": ["https://www.googleapis.com/auth/drive"],
        },
    ],
)
def test_api_scope_loading_fails_closed_for_missing_or_mismatched_contract(
    api_module, payload,
):
    api_module.TOKEN_PATH.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit):
        api_module._stored_token_scopes()


def test_api_refresh_rejects_scope_expansion(api_module, monkeypatch):
    token_path = api_module.TOKEN_PATH
    selected_scopes = ["https://www.googleapis.com/auth/calendar.events"]
    _write_token(
        token_path,
        token="ya29.old",
        scopes=selected_scopes,
        korra_services=["calendar"],
        korra_requested_scopes=selected_scopes,
    )

    error_type = api_module._native_google.GoogleWorkspaceError
    monkeypatch.setattr(
        api_module._native_google,
        "_credentials",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            error_type("scope_mismatch", "expanded scope", status_code=409)
        ),
    )

    with pytest.raises(SystemExit):
        api_module.get_credentials()

    assert json.loads(token_path.read_text(encoding="utf-8"))["token"] == "ya29.old"
