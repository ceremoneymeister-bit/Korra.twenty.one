"""Tests for Google Workspace gws bridge and CLI wrapper."""

import importlib.util
import json
import subprocess
import sys
import types
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
    return module


def _write_token(path: Path, *, token="ya29.test", expiry=None, **extra):
    data = {
        "token": token,
        "refresh_token": "1//refresh",
        "client_id": "123.apps.googleusercontent.com",
        "client_secret": "secret",
        "token_uri": "https://oauth2.googleapis.com/token",
        **extra,
    }
    if expiry is not None:
        data["expiry"] = expiry
    path.write_text(json.dumps(data))


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
        scopes=["https://www.googleapis.com/auth/calendar.events"],
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


def test_gws_refresh_cannot_drop_tracked_service_metadata(api_module, monkeypatch):
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

    assert api_module._run_gws(["calendar", "events", "list"]) == {}
    payload = json.loads(api_module.TOKEN_PATH.read_text(encoding="utf-8"))
    assert payload["korra_services"] == ["calendar"]
    assert payload["korra_requested_scopes"] == selected_scopes












def test_api_get_credentials_refresh_persists_authorized_user_type(api_module, monkeypatch):
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

    class FakeCredentials:
        def __init__(self):
            self.expired = True
            self.refresh_token = "1//refresh"
            self.valid = True

        def refresh(self, request):
            self.expired = False

        def to_json(self):
            return json.dumps({
                "token": "ya29.refreshed",
                "refresh_token": "1//refresh",
                "client_id": "123.apps.googleusercontent.com",
                "client_secret": "secret",
                "token_uri": "https://oauth2.googleapis.com/token",
            })

    class FakeCredentialsModule:
        @staticmethod
        def from_authorized_user_file(filename, scopes):
            assert filename == str(token_path)
            assert scopes == selected_scopes
            return FakeCredentials()

    google_module = types.ModuleType("google")
    oauth2_module = types.ModuleType("google.oauth2")
    credentials_module = types.ModuleType("google.oauth2.credentials")
    credentials_module.Credentials = FakeCredentialsModule
    transport_module = types.ModuleType("google.auth.transport")
    requests_module = types.ModuleType("google.auth.transport.requests")
    requests_module.Request = lambda: object()

    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.oauth2", oauth2_module)
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", credentials_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport", transport_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", requests_module)

    creds = api_module.get_credentials()

    saved = json.loads(token_path.read_text())
    assert isinstance(creds, FakeCredentials)
    assert saved["token"] == "ya29.refreshed"
    assert saved["type"] == "authorized_user"
    assert saved["scopes"] == selected_scopes
    assert saved["korra_services"] == ["calendar", "drive", "sheets"]
    assert saved["korra_requested_scopes"] == selected_scopes


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

    class FakeCredentials:
        expired = True
        refresh_token = "1//refresh"
        valid = True

        def refresh(self, request):
            return None

        def to_json(self):
            return json.dumps(
                {
                    "token": "ya29.expanded",
                    "refresh_token": "1//refresh",
                    "scopes": selected_scopes
                    + ["https://www.googleapis.com/auth/gmail.send"],
                }
            )

    class FakeCredentialsModule:
        @staticmethod
        def from_authorized_user_file(filename, scopes):
            return FakeCredentials()

    google_module = types.ModuleType("google")
    oauth2_module = types.ModuleType("google.oauth2")
    credentials_module = types.ModuleType("google.oauth2.credentials")
    credentials_module.Credentials = FakeCredentialsModule
    transport_module = types.ModuleType("google.auth.transport")
    requests_module = types.ModuleType("google.auth.transport.requests")
    requests_module.Request = lambda: object()
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.oauth2", oauth2_module)
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", credentials_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport", transport_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", requests_module)

    with pytest.raises(SystemExit):
        api_module.get_credentials()

    assert json.loads(token_path.read_text(encoding="utf-8"))["token"] == "ya29.old"
