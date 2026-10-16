"""F12: an unscoped cleanup thread closes only the owning profile's browser."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from agent import secret_scope
from gateway.run import _profile_runtime_scope
from korra_constants import get_hermes_home
from tools import browser_tool as bt, browser_camofox as cf


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)
    monkeypatch.setattr(bt, "_active_sessions", {})
    monkeypatch.setattr(bt, "_session_last_activity", {})
    monkeypatch.setattr(bt, "_recording_sessions", set())
    monkeypatch.setattr(cf, "_sessions", {})
    monkeypatch.setattr(bt, "_start_browser_cleanup_thread", lambda: None)
    # Keep this regression synchronous except the explicitly unscoped reaper.
    monkeypatch.setattr(bt, "_stop_cdp_supervisor", lambda *_: None)
    if hasattr(bt, "_session_cleanup_contexts"):
        monkeypatch.setattr(bt, "_session_cleanup_contexts", {})
    for name in ("alice", "bob"):
        home = tmp_path / name
        home.mkdir()
        (home / ".env").write_text(f"CAMOFOX_URL=http://{name}.invalid:9377\n")
        (home / "config.yaml").write_text("browser:\n  camofox:\n    managed_persistence: false\n")
    sent = []
    def request(method, url, **kwargs):
        sent.append((method, url, get_hermes_home().name))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {})
    monkeypatch.setattr(cf, "_request", request)
    return tmp_path, sent


def reap():
    # A real Thread has no inherited contextvars (unlike asyncio.to_thread).
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(bt._cleanup_inactive_browser_sessions).result()


def test_generic_idle_cleanup_uses_each_profile_scope(setup, monkeypatch, caplog):
    root, sent = setup
    commands = []
    monkeypatch.setattr(bt, "_create_local_session", lambda task: {"session_name": "fake-" + task, "bb_session_id": None})
    monkeypatch.setattr(bt, "_get_cdp_override", lambda: None)
    monkeypatch.setattr(bt, "_is_local_mode", lambda: True)
    monkeypatch.setattr(bt, "_run_browser_command", lambda task, *args, **kwargs: commands.append((task, get_hermes_home().name)))
    for name in ("alice", "bob"):
        with _profile_runtime_scope(root / name):
            bt._get_session_info(name)
    bt._session_last_activity["alice"] = 0
    reap()
    assert commands == [("alice", "alice")]
    assert "alice" not in bt._active_sessions
    assert "bob" in bt._active_sessions
    assert "no profile secret scope" not in caplog.text
    bt._session_last_activity["bob"] = 0
    reap()
    assert commands[-1] == ("bob", "bob")
    assert not bt._session_last_activity


def test_camofox_sessions_are_reaped_in_their_scope(setup, caplog):
    root, sent = setup
    users = {}
    for name in ("alice", "bob"):
        with _profile_runtime_scope(root / name):
            users[name] = cf._get_session(name)["user_id"]
    assert set(bt._session_last_activity) == {"alice", "bob"}
    bt._session_last_activity["alice"] = 0
    reap()
    assert sent == [("delete", f"http://alice.invalid:9377/sessions/{users['alice']}", "alice")]
    assert (str(root / "bob"), "bob") in cf._sessions
    bt._session_last_activity["bob"] = 0
    reap()
    assert sent[-1] == ("delete", f"http://bob.invalid:9377/sessions/{users['bob']}", "bob")
    assert not cf._sessions
    assert not bt._session_last_activity
    assert "no profile secret scope" not in caplog.text


def test_managed_camofox_cleanup_preserves_server_login(setup):
    root, sent = setup
    (root / "alice" / "config.yaml").write_text("browser:\n  camofox:\n    managed_persistence: true\n")
    with _profile_runtime_scope(root / "alice"):
        cf._get_session("alice")
    bt._session_last_activity["alice"] = 0
    reap()
    assert not cf._sessions
    assert sent == []
