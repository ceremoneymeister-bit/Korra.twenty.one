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


@pytest.mark.parametrize('action,method,path_suffix,body', [
    ('tasklists', 'GET', '/users/@me/lists', None),
    ('list', 'GET', '/lists/list-1/tasks', None),
    ('get', 'GET', '/lists/list-1/tasks/task-1', None),
    ('create', 'POST', '/lists/list-1/tasks', {'title': 'Тест', 'notes': 'Заметка'}),
    ('complete', 'PATCH', '/lists/list-1/tasks/task-1', {'status': 'completed'}),
])
def test_tasks_python_uses_real_discovery_and_fake_transport(api_module, monkeypatch, capsys, action, method, path_suffix, body):
    import httplib2
    from urllib.parse import urlsplit, parse_qs
    from googleapiclient.discovery import build
    calls = []
    class FakeHTTP:
        def request(self, uri, method='GET', body=None, headers=None, **kwargs):
            calls.append((uri, method, json.loads(body) if body else None))
            return httplib2.Response({'status': '200', 'content-type': 'application/json'}), b'{"id":"task-1","items":[],"nextPageToken":"page-2"}'
    service = build('tasks', 'v1', http=FakeHTTP(), static_discovery=True)
    monkeypatch.setattr(api_module, '_gws_binary', lambda: None)
    monkeypatch.setattr(api_module, 'build_service', lambda name, version: service)
    args = SimpleNamespace(action=action, tasklist='list-1', task_id='task-1', max=7,
                           page_token='page-1', show_completed=True, title='Тест', notes='Заметка', due='')
    api_module._require_selected_service('tasks')
    api_module.tasks_command(args)
    uri, actual_method, actual_body = calls[0]
    assert actual_method == method and urlsplit(uri).path.endswith(path_suffix)
    assert actual_body == body
    if action in {'list', 'tasklists'}:
        params = parse_qs(urlsplit(uri).query)
        assert params['pageToken'] == ['page-1'] and params['maxResults'] == ['7']
        assert json.loads(capsys.readouterr().out)['nextPageToken'] == 'page-2'


@pytest.mark.parametrize('action,resource,method', [
    ('tasklists', 'tasklists', 'list'), ('list', 'tasks', 'list'),
    ('get', 'tasks', 'get'), ('create', 'tasks', 'insert'), ('complete', 'tasks', 'patch'),
])
def test_tasks_gws_transport_contract(api_module, monkeypatch, capsys, action, resource, method):
    calls = []
    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout='{"id":"task-1"}', stderr='')
    monkeypatch.setattr(api_module.subprocess, 'run', fake_run)
    args = SimpleNamespace(action=action, tasklist='list-1', task_id='task-1', max=5,
                           page_token='', show_completed=False, title='Task', notes='', due='')
    api_module.tasks_command(args)
    assert calls[0][1:4] == ['tasks', resource, method]
    if action == 'complete':
        assert json.loads(calls[0][calls[0].index('--json') + 1]) == {'status': 'completed'}
    assert json.loads(capsys.readouterr().out)['id'] == 'task-1'


def test_tasks_cli_rejects_missing_scope_before_transport(api_module, monkeypatch):
    scopes = ['https://www.googleapis.com/auth/drive']
    _write_token(api_module.TOKEN_PATH, scopes=scopes, korra_services=['drive'], korra_requested_scopes=scopes)
    monkeypatch.setattr(api_module.sys, 'argv', ['google_api.py', 'tasks', 'complete', 'task-1'])
    monkeypatch.setattr(api_module.subprocess, 'run', lambda *_args, **_kwargs: pytest.fail('No request without Tasks consent'))
    with pytest.raises(SystemExit):
        api_module.main()


def _tabbed_doc():
    def tab(tab_id, title, text, children=()):
        return {
            "tabProperties": {"tabId": tab_id, "title": title},
            "documentTab": {"body": {"content": [
                {"endIndex": len(text) + 2,
                 "paragraph": {"elements": [{"textRun": {"content": text + "\n"}}]}},
            ]}},
            "childTabs": list(children),
        }

    return {
        "title": "План",
        "documentId": "doc-1",
        "tabs": [
            tab("t.a", "Первая", "alpha", [tab("t.a1", "Вложенная", "alpha-child")]),
            tab("t.b", "Вторая", "beta"),
            tab("t.c", "Третья", "gamma"),
        ],
    }


def _fake_docs_gws(api_module, monkeypatch, doc):
    calls = []

    def fake_run(cmd, params=None, body=None, **kwargs):
        calls.append((cmd, params, body))
        return doc if cmd[-1] == "get" else {}

    monkeypatch.setattr(api_module, "_run_gws", fake_run)
    return calls


def test_docs_get_reads_every_tab(api_module, monkeypatch, capsys):
    calls = _fake_docs_gws(api_module, monkeypatch, _tabbed_doc())
    api_module.docs_get(SimpleNamespace(doc_id="doc-1", tab=None))

    assert calls[0][1]["includeTabsContent"] is True
    out = json.loads(capsys.readouterr().out)
    assert [(t["tabId"], t["body"], t["level"]) for t in out["tabs"]] == [
        ("t.a", "alpha\n", 0),
        ("t.a1", "alpha-child\n", 1),
        ("t.b", "beta\n", 0),
        ("t.c", "gamma\n", 0),
    ]


def test_docs_get_single_tab_and_legacy_shape(api_module, monkeypatch, capsys):
    _fake_docs_gws(api_module, monkeypatch, _tabbed_doc())
    api_module.docs_get(SimpleNamespace(doc_id="doc-1", tab="t.c"))
    assert json.loads(capsys.readouterr().out)["body"] == "gamma\n"

    legacy = {"title": "Старый", "documentId": "d", "body": {"content": [
        {"paragraph": {"elements": [{"textRun": {"content": "text\n"}}]}}]}}
    _fake_docs_gws(api_module, monkeypatch, legacy)
    api_module.docs_get(SimpleNamespace(doc_id="d", tab=None))
    assert json.loads(capsys.readouterr().out)["body"] == "text\n"


def test_docs_append_writes_to_chosen_tab(api_module, monkeypatch, capsys):
    calls = _fake_docs_gws(api_module, monkeypatch, _tabbed_doc())
    api_module.docs_append(SimpleNamespace(doc_id="doc-1", text="новое", tab="t.b"))

    request = calls[1][2]["requests"][0]["insertText"]
    assert request["location"] == {"index": 5, "tabId": "t.b"}
    assert request["text"] == "новое\n"
    out = json.loads(capsys.readouterr().out)
    assert out["tab"] == "t.b" and out["tabTitle"] == "Вторая"
    assert "note" not in out


def test_docs_append_without_tab_reports_where_it_wrote(api_module, monkeypatch, capsys):
    calls = _fake_docs_gws(api_module, monkeypatch, _tabbed_doc())
    api_module.docs_append(SimpleNamespace(doc_id="doc-1", text="x", tab=None))

    assert calls[1][2]["requests"][0]["insertText"]["location"]["tabId"] == "t.a"
    out = json.loads(capsys.readouterr().out)
    assert out["tab"] == "t.a" and out["tabTitle"] == "Первая"
    assert "--tab" in out["note"]
    assert [t["tabId"] for t in out["tabs"]] == ["t.a", "t.a1", "t.b", "t.c"]


def test_docs_append_unknown_tab_fails_without_writing(api_module, monkeypatch, capsys):
    calls = _fake_docs_gws(api_module, monkeypatch, _tabbed_doc())
    with pytest.raises(SystemExit):
        api_module.docs_append(SimpleNamespace(doc_id="doc-1", text="x", tab="t.zzz"))

    assert len(calls) == 1
    assert "t.zzz" in capsys.readouterr().err


def test_skill_doc_matches_connection_behaviour():
    text = (API_PATH.parents[1] / "SKILL.md").read_text(encoding="utf-8")
    assert "remain\nisolated" not in text and "remain isolated" not in text
    assert "shared-all" in text
    assert "off by default" in text and "Settings → Keys → Google Workspace" in text
    assert "First call `google_workspace_auth`" not in text


def _gmail_send_args(**over):
    base = dict(to="client@example.com", subject="Счёт", body="Здравствуйте", cc="", html=False,
                from_header="", thread_id="")
    base.update(over)
    return SimpleNamespace(**base)


def _agent_turn_env(monkeypatch):
    for name in ("KORRA_CRON_SESSION", "KORRA_CRON_RUN_TOKEN", "KORRA_EFFECT_DECISION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KORRA_SESSION_KEY", "agent:main:telegram:dm:1")
    monkeypatch.setenv("KORRA_SESSION_ID", "sess-1")
    monkeypatch.setenv("KORRA_SESSION_PLATFORM", "telegram")
    monkeypatch.setattr(sys, "argv", ["google_api.py", "gmail", "send", "--to", "client@example.com",
                                      "--subject", "Счёт", "--body", "Здравствуйте"])


def _sent_calls(api_module, monkeypatch):
    sent = []

    def fake_run(cmd, params=None, body=None, **kwargs):
        sent.append(cmd)
        return {"id": "m1", "threadId": "t1"}

    monkeypatch.setattr(api_module, "_run_gws", fake_run)
    return sent


def test_gmail_send_by_agent_waits_for_owner_decision(api_module, monkeypatch, capsys):
    from tools import effect_decisions as decisions

    _agent_turn_env(monkeypatch)
    sent = _sent_calls(api_module, monkeypatch)
    with pytest.raises(SystemExit):
        api_module.gmail_send(_gmail_send_args())

    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "pending_decision" and not sent
    decision = decisions.get_decision(out["decision_id"])
    assert decision["kind"] == "outbound_message" and decision["status"] == decisions.PENDING
    assert decision["payload"]["chat_id"] == "client@example.com"
    assert "Здравствуйте" in decision["payload"]["message"]
    assert decisions.approval_payload(decision)["choices"] == ["once", "deny"]


def test_gmail_reply_by_agent_waits_for_owner_decision(api_module, monkeypatch, capsys):
    _agent_turn_env(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["google_api.py", "gmail", "reply", "m0", "--body", "Спасибо"])
    sent = []

    def fake_run(cmd, params=None, body=None, **kwargs):
        sent.append(cmd)
        return {"threadId": "t0", "payload": {"headers": [
            {"name": "From", "value": "ivan@example.com"}, {"name": "Subject", "value": "Вопрос"}]}}

    monkeypatch.setattr(api_module, "_run_gws", fake_run)
    with pytest.raises(SystemExit):
        api_module.gmail_reply(SimpleNamespace(message_id="m0", body="Спасибо", from_header=""))

    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "pending_decision"
    assert all(cmd[-1] == "get" for cmd in sent)


def test_gmail_send_from_owner_automation_needs_no_decision(api_module, monkeypatch, capsys):
    _agent_turn_env(monkeypatch)
    monkeypatch.setenv("KORRA_CRON_SESSION", "1")
    monkeypatch.setenv("KORRA_CRON_RUN_TOKEN", "run-token")
    sent = _sent_calls(api_module, monkeypatch)
    api_module.gmail_send(_gmail_send_args())

    assert json.loads(capsys.readouterr().out)["status"] == "sent" and len(sent) == 1


def test_gmail_send_by_a_person_at_the_shell_needs_no_decision(api_module, monkeypatch, capsys):
    _agent_turn_env(monkeypatch)
    for name in ("KORRA_SESSION_KEY", "KORRA_SESSION_ID", "KORRA_SESSION_PLATFORM"):
        monkeypatch.delenv(name)
    monkeypatch.delenv("KORRA_KANBAN_TASK", raising=False)
    sent = _sent_calls(api_module, monkeypatch)
    api_module.gmail_send(_gmail_send_args())

    assert json.loads(capsys.readouterr().out)["status"] == "sent" and len(sent) == 1


def test_approved_gmail_decision_runs_the_script_once_and_binds_its_arguments(api_module, monkeypatch, capsys):
    from tools import effect_decisions as decisions
    from tools import send_message_tool

    _agent_turn_env(monkeypatch)
    with pytest.raises(SystemExit):
        api_module.gmail_send(_gmail_send_args())
    decision_id = json.loads(capsys.readouterr().out)["decision_id"]

    runs = []

    def fake_subprocess(cmd, env=None, **kwargs):
        runs.append((cmd, env["KORRA_EFFECT_DECISION_ID"]))
        return SimpleNamespace(returncode=0, stdout='{"status": "sent"}', stderr="")

    monkeypatch.setattr("subprocess.run", fake_subprocess)
    monkeypatch.setattr("korra_cli.google_workspace.workspace_skill_status",
                        lambda *a, **k: {"execution_path": str(API_PATH)})
    result = send_message_tool.resolve_outbound_message_decision(
        decision_id, "once", source_session_key="agent:main:telegram:dm:1")
    assert result["status"] == decisions.SUCCEEDED
    assert runs[0][0][2:] == ["gmail", "send", "--to", "client@example.com", "--subject", "Счёт",
                              "--body", "Здравствуйте"]
    assert runs[0][1] == decision_id

    # The script itself sends only for the exact arguments of an executing decision.
    sent = _sent_calls(api_module, monkeypatch)
    monkeypatch.setenv("KORRA_EFFECT_DECISION_ID", decision_id)
    with pytest.raises(SystemExit):
        api_module.gmail_send(_gmail_send_args())
    assert not sent


def test_denied_gmail_decision_never_runs_the_script(api_module, monkeypatch, capsys):
    from tools import effect_decisions as decisions
    from tools import send_message_tool

    _agent_turn_env(monkeypatch)
    with pytest.raises(SystemExit):
        api_module.gmail_send(_gmail_send_args())
    decision_id = json.loads(capsys.readouterr().out)["decision_id"]
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("denied send must not run"))
    result = send_message_tool.resolve_outbound_message_decision(
        decision_id, "deny", source_session_key="agent:main:telegram:dm:1")
    assert result["status"] == decisions.DENIED
