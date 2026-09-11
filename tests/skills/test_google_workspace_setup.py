"""Security-floor tests for the Google Workspace runtime installer."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest


SETUP_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills/productivity/google-workspace/scripts/setup.py"
)


@pytest.fixture()
def setup_module(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    spec = importlib.util.spec_from_file_location(
        "test_google_workspace_setup_module",
        SETUP_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stale_google_transitives_are_reported_missing(setup_module, monkeypatch):
    installed = {
        "google-api-python-client": "2.194.0",
        "google-auth": "2.55.0",
        "google-auth-oauthlib": "1.3.1",
        "google-auth-httplib2": "0.3.1",
        "httplib2": "0.31.2",
        "pyasn1": "0.6.3",
    }

    def fake_version(name):
        try:
            return installed[name]
        except KeyError:
            raise PackageNotFoundError(name) from None

    monkeypatch.setattr(setup_module, "_distribution_version", fake_version)

    assert setup_module._missing_required_packages() == [
        "google-auth==2.55.1",
        "httplib2==0.32.0",
        "pyasn1==0.6.4",
    ]


def test_installer_repairs_stale_transitives(setup_module, monkeypatch):
    states = iter(
        [
            [
                "google-auth==2.55.1",
                "httplib2==0.32.0",
                "pyasn1==0.6.4",
            ],
            [],
        ]
    )
    monkeypatch.setattr(
        setup_module,
        "_missing_required_packages",
        lambda: next(states),
    )
    calls = []
    monkeypatch.setattr(
        setup_module.subprocess,
        "check_call",
        lambda argv, **kwargs: calls.append(argv),
    )

    assert setup_module.install_deps() is True
    assert calls == [
        [
            setup_module.sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "google-auth==2.55.1",
            "httplib2==0.32.0",
            "pyasn1==0.6.4",
        ]
    ]


def test_services_resolve_to_exact_minimum_scopes(setup_module):
    services = setup_module._parse_services("drive,sheets,calendar")

    assert services == ("calendar", "drive", "sheets")
    assert setup_module._scopes_for_services(services) == [
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets",
    ]


def test_all_services_requires_explicit_standalone_alias(setup_module):
    assert setup_module._parse_services("all") == ("all",)
    assert setup_module._scopes_for_services(("all",)) == setup_module.SCOPES

    with pytest.raises(ValueError, match="cannot be combined"):
        setup_module._parse_services("all,calendar")


@pytest.mark.parametrize("raw", [None, "", "  ", "drive,", "drive,,calendar", "unknown"])
def test_services_fail_closed_for_empty_or_unknown_values(setup_module, raw):
    with pytest.raises(ValueError):
        setup_module._parse_services(raw)


def test_auth_url_uses_and_persists_exact_selected_scope_contract(
    setup_module, monkeypatch,
):
    setup_module.CLIENT_SECRET_PATH.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(setup_module, "_ensure_deps", lambda: None)
    captured = {}

    class FakeFlow:
        code_verifier = "verifier"

        @classmethod
        def from_client_secrets_file(cls, path, *, scopes, redirect_uri, **kwargs):
            captured.update(path=path, scopes=scopes, redirect_uri=redirect_uri, kwargs=kwargs)
            return cls()

        def authorization_url(self, **kwargs):
            captured["authorization_kwargs"] = kwargs
            return "https://accounts.example/auth", "state-123"

    flow_module = types.ModuleType("google_auth_oauthlib.flow")
    flow_module.Flow = FakeFlow
    package = types.ModuleType("google_auth_oauthlib")
    package.flow = flow_module
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", package)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_module)

    setup_module.get_auth_url(("calendar", "drive", "sheets"))

    assert captured["scopes"] == [
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    pending = json.loads(setup_module.PENDING_AUTH_PATH.read_text(encoding="utf-8"))
    assert pending["services"] == ["calendar", "drive", "sheets"]
    assert pending["scopes"] == captured["scopes"]


def test_auth_url_refuses_scope_change_while_token_exists(setup_module, monkeypatch):
    setup_module.CLIENT_SECRET_PATH.write_text("{}", encoding="utf-8")
    setup_module.TOKEN_PATH.write_text(
        json.dumps(
            {
                "scopes": setup_module._scopes_for_services(("calendar",)),
                "korra_services": ["calendar"],
                "korra_requested_scopes": setup_module._scopes_for_services(("calendar",)),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        setup_module,
        "_ensure_deps",
        lambda: pytest.fail("OAuth dependencies must not load after scope mismatch"),
    )

    with pytest.raises(SystemExit):
        setup_module.get_auth_url(("calendar", "drive"))

    assert not setup_module.PENDING_AUTH_PATH.exists()


def test_exchange_persists_selected_services_with_exact_scopes(setup_module, monkeypatch):
    setup_module.CLIENT_SECRET_PATH.write_text("{}", encoding="utf-8")
    setup_module._save_pending_auth(
        state="state-123",
        code_verifier="verifier",
        services=("calendar", "drive", "sheets"),
    )
    monkeypatch.setattr(setup_module, "_ensure_deps", lambda: None)

    requested_scopes = setup_module._scopes_for_services(("calendar", "drive", "sheets"))

    class FakeCredentials:
        granted_scopes = requested_scopes

        def to_json(self):
            return json.dumps(
                {
                    "token": "ya29.test",
                    "refresh_token": "1//refresh",
                    "client_id": "client",
                    "client_secret": "secret",
                    "scopes": requested_scopes,
                }
            )

    class FakeFlow:
        credentials = FakeCredentials()

        @classmethod
        def from_client_secrets_file(cls, path, **kwargs):
            assert kwargs["scopes"] == requested_scopes
            return cls()

        def fetch_token(self, *, code):
            assert code == "code-123"

    flow_module = types.ModuleType("google_auth_oauthlib.flow")
    flow_module.Flow = FakeFlow
    package = types.ModuleType("google_auth_oauthlib")
    package.flow = flow_module
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", package)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_module)

    setup_module.exchange_auth_code("code-123")

    payload = json.loads(setup_module.TOKEN_PATH.read_text(encoding="utf-8"))
    assert payload["scopes"] == requested_scopes
    assert payload["korra_services"] == ["calendar", "drive", "sheets"]
    assert payload["korra_requested_scopes"] == requested_scopes
    assert not setup_module.PENDING_AUTH_PATH.exists()


def test_exchange_rejects_tampered_pending_scope_contract_before_oauth(
    setup_module, monkeypatch,
):
    setup_module.CLIENT_SECRET_PATH.write_text("{}", encoding="utf-8")
    setup_module.PENDING_AUTH_PATH.write_text(
        json.dumps(
            {
                "state": "state-123",
                "code_verifier": "verifier",
                "redirect_uri": setup_module.REDIRECT_URI,
                "services": ["calendar"],
                "scopes": ["https://www.googleapis.com/auth/gmail.send"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        setup_module,
        "_ensure_deps",
        lambda: pytest.fail("OAuth dependencies must not load for tampered state"),
    )

    with pytest.raises(SystemExit):
        setup_module.exchange_auth_code("code-123")

    assert not setup_module.TOKEN_PATH.exists()


def test_exchange_rejects_full_callback_without_matching_state(setup_module, monkeypatch):
    setup_module.CLIENT_SECRET_PATH.write_text("{}", encoding="utf-8")
    setup_module._save_pending_auth(
        state="state-123",
        code_verifier="verifier",
        services=("calendar",),
    )
    monkeypatch.setattr(
        setup_module,
        "_ensure_deps",
        lambda: pytest.fail("OAuth must not start for a callback without state"),
    )

    with pytest.raises(SystemExit):
        setup_module.exchange_auth_code("http://localhost:1/?code=code-123")

    assert not setup_module.TOKEN_PATH.exists()


def test_exchange_rejects_token_when_google_reports_no_granted_scopes(
    setup_module, monkeypatch,
):
    setup_module.CLIENT_SECRET_PATH.write_text("{}", encoding="utf-8")
    setup_module._save_pending_auth(
        state="state-123",
        code_verifier="verifier",
        services=("calendar",),
    )
    monkeypatch.setattr(setup_module, "_ensure_deps", lambda: None)

    class FakeCredentials:
        granted_scopes = None

        def to_json(self):
            return json.dumps(
                {
                    "token": "ya29.test",
                    "refresh_token": "1//refresh",
                }
            )

    class FakeFlow:
        credentials = FakeCredentials()

        @classmethod
        def from_client_secrets_file(cls, path, **kwargs):
            return cls()

        def fetch_token(self, *, code):
            return None

    flow_module = types.ModuleType("google_auth_oauthlib.flow")
    flow_module.Flow = FakeFlow
    package = types.ModuleType("google_auth_oauthlib")
    package.flow = flow_module
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", package)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_module)

    with pytest.raises(SystemExit):
        setup_module.exchange_auth_code("code-123")

    assert not setup_module.TOKEN_PATH.exists()


def test_check_fails_when_tracked_token_has_missing_or_extra_scopes(
    setup_module, monkeypatch,
):
    expected = setup_module._scopes_for_services(("calendar", "drive"))
    payload = {
        "scopes": expected[:1],
        "korra_services": ["calendar", "drive"],
        "korra_requested_scopes": expected,
    }
    setup_module.TOKEN_PATH.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(setup_module, "_ensure_deps", lambda: None)

    class FakeCredentials:
        valid = True

    class FakeCredentialsModule:
        @staticmethod
        def from_authorized_user_file(path):
            return FakeCredentials()

    credentials_module = types.ModuleType("google.oauth2.credentials")
    credentials_module.Credentials = FakeCredentialsModule
    oauth2_module = types.ModuleType("google.oauth2")
    oauth2_module.credentials = credentials_module
    google_module = types.ModuleType("google")
    google_module.oauth2 = oauth2_module
    request_module = types.ModuleType("google.auth.transport.requests")
    request_module.Request = lambda: object()
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.oauth2", oauth2_module)
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", credentials_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", request_module)

    assert setup_module.check_auth() is False
    payload["scopes"] = expected + ["https://www.googleapis.com/auth/gmail.send"]
    setup_module.TOKEN_PATH.write_text(json.dumps(payload), encoding="utf-8")
    assert setup_module.check_auth() is False


def test_check_live_uses_non_mutating_event_probe_for_calendar_scope(
    setup_module, monkeypatch,
):
    selected = ("calendar",)
    scopes = setup_module._scopes_for_services(selected)
    setup_module.TOKEN_PATH.write_text(
        json.dumps(
            {
                "scopes": scopes,
                "korra_services": list(selected),
                "korra_requested_scopes": scopes,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_module, "check_auth", lambda quiet=False: True)
    captured = {}

    class FakeRequest:
        def execute(self):
            captured["executed"] = True

    class FakeEvents:
        def list(self, **kwargs):
            captured["list_kwargs"] = kwargs
            return FakeRequest()

    class FakeService:
        def events(self):
            return FakeEvents()

    discovery_module = types.ModuleType("googleapiclient.discovery")
    discovery_module.build = lambda api, version, credentials: FakeService()
    errors_module = types.ModuleType("googleapiclient.errors")
    errors_module.HttpError = RuntimeError
    api_package = types.ModuleType("googleapiclient")
    api_package.discovery = discovery_module
    api_package.errors = errors_module

    class FakeCredentialsModule:
        @staticmethod
        def from_authorized_user_file(path):
            return object()

    credentials_module = types.ModuleType("google.oauth2.credentials")
    credentials_module.Credentials = FakeCredentialsModule
    oauth2_module = types.ModuleType("google.oauth2")
    oauth2_module.credentials = credentials_module
    google_module = types.ModuleType("google")
    google_module.oauth2 = oauth2_module
    monkeypatch.setitem(sys.modules, "googleapiclient", api_package)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", discovery_module)
    monkeypatch.setitem(sys.modules, "googleapiclient.errors", errors_module)
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.oauth2", oauth2_module)
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", credentials_module)

    assert setup_module.check_auth_live() is True
    assert captured["list_kwargs"] == {"calendarId": "primary", "maxResults": 1}
    assert captured["executed"] is True


def test_check_refresh_preserves_selected_scope_contract(setup_module, monkeypatch):
    selected = ("calendar", "drive", "sheets")
    scopes = setup_module._scopes_for_services(selected)
    setup_module.TOKEN_PATH.write_text(
        json.dumps(
            {
                "token": "ya29.old",
                "refresh_token": "1//refresh",
                "scopes": scopes,
                "korra_services": list(selected),
                "korra_requested_scopes": scopes,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_module, "_ensure_deps", lambda: None)

    class FakeCredentials:
        valid = False
        expired = True
        refresh_token = "1//refresh"

        def refresh(self, request):
            self.valid = True
            self.expired = False

        def to_json(self):
            return json.dumps(
                {
                    "token": "ya29.refreshed",
                    "refresh_token": "1//refresh",
                }
            )

    class FakeCredentialsModule:
        @staticmethod
        def from_authorized_user_file(path):
            return FakeCredentials()

    credentials_module = types.ModuleType("google.oauth2.credentials")
    credentials_module.Credentials = FakeCredentialsModule
    oauth2_module = types.ModuleType("google.oauth2")
    oauth2_module.credentials = credentials_module
    google_module = types.ModuleType("google")
    google_module.oauth2 = oauth2_module
    request_module = types.ModuleType("google.auth.transport.requests")
    request_module.Request = lambda: object()
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.oauth2", oauth2_module)
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", credentials_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", request_module)

    assert setup_module.check_auth() is True
    refreshed = json.loads(setup_module.TOKEN_PATH.read_text(encoding="utf-8"))
    assert refreshed["scopes"] == scopes
    assert refreshed["korra_services"] == list(selected)
    assert refreshed["korra_requested_scopes"] == scopes


def test_revoke_uses_refresh_token_and_clears_token_and_pending(
    setup_module, monkeypatch,
):
    selected = ("calendar", "drive")
    scopes = setup_module._scopes_for_services(selected)
    setup_module.TOKEN_PATH.write_text(
        json.dumps(
            {
                "token": "ya29.test",
                "refresh_token": "1//refresh",
                "scopes": scopes,
                "korra_services": list(selected),
                "korra_requested_scopes": scopes,
            }
        ),
        encoding="utf-8",
    )
    setup_module.PENDING_AUTH_PATH.write_text("{}", encoding="utf-8")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, **kwargs):
        captured["url"] = request.full_url
        captured["data"] = request.data
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    setup_module.revoke()

    assert captured["url"] == "https://oauth2.googleapis.com/revoke"
    assert captured["data"] == b"token=1%2F%2Frefresh"
    assert not setup_module.TOKEN_PATH.exists()
    assert not setup_module.PENDING_AUTH_PATH.exists()


def test_revoke_without_token_still_removes_pending_state(setup_module):
    setup_module.PENDING_AUTH_PATH.write_text("{}", encoding="utf-8")

    setup_module.revoke()

    assert not setup_module.PENDING_AUTH_PATH.exists()


def test_revoke_does_not_let_malformed_scope_metadata_block_remote_revoke(
    setup_module, monkeypatch,
):
    setup_module.TOKEN_PATH.write_text(
        json.dumps(
            {
                "refresh_token": "1//refresh",
                "scopes": ["https://www.googleapis.com/auth/cloud-platform"],
            }
        ),
        encoding="utf-8",
    )
    setup_module.PENDING_AUTH_PATH.write_text("{}", encoding="utf-8")
    captured = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, **kwargs: captured.append(request) or FakeResponse(),
    )

    setup_module.revoke()

    assert len(captured) == 1
    assert not setup_module.TOKEN_PATH.exists()
    assert not setup_module.PENDING_AUTH_PATH.exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["setup.py", "--auth-url"],
        ["setup.py", "--auth-url", "--services", "unknown"],
        ["setup.py", "--check", "--services", "drive"],
    ],
)
def test_cli_rejects_missing_unknown_or_misplaced_services_before_action(
    setup_module, monkeypatch, argv,
):
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(
        setup_module,
        "get_auth_url",
        lambda services: pytest.fail("OAuth action must not start for invalid CLI input"),
    )

    with pytest.raises(SystemExit) as exc:
        setup_module.main()

    assert exc.value.code == 2


def test_cli_passes_canonical_services_to_auth_url(setup_module, monkeypatch):
    captured = []
    monkeypatch.setattr(
        sys,
        "argv",
        ["setup.py", "--auth-url", "--services", "drive,sheets,calendar"],
    )
    monkeypatch.setattr(setup_module, "get_auth_url", captured.append)

    setup_module.main()

    assert captured == [("calendar", "drive", "sheets")]
