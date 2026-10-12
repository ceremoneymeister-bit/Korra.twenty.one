"""K21-336: клиент Camofox находит встроенный сервер сам и ходит к нему по Unix-сокету."""

import json
from unittest.mock import patch

import pytest
import requests

import tools.browser_camofox as bc
from korra_cli import camofox_embedded as emb
from tests.fakes.camofox_embedded import (  # noqa: F401  (фикстуры)
    TcpSpy,
    UnixServer,
    choose_browser,
    data,
    in_profile,
    install,
)
from tools.browser_camofox_state import get_camofox_identity

REPLY = json.dumps({"ok": True, "tabId": "t1", "url": "https://example.com/", "snapshot": "page"}).encode()


@pytest.fixture(autouse=True)
def _clean():
    yield
    with bc._sessions_lock:
        bc._sessions.clear()
    bc._unix_session = None


@pytest.fixture
def embedded(install, data):
    """Camofox выбран в настройках (настоящая запись выбора), сервер включён."""
    choose_browser("Camofox")
    assert emb.is_enabled()
    return data


def test_url_and_key_come_from_the_bundled_server_without_any_env(embedded):
    assert bc.get_camofox_url() == emb.BASE_URL
    assert bc._auth_headers() == {"Authorization": f"Bearer {emb.access_key()}"}
    assert bc.is_camofox_mode() is True
    assert bc._using_embedded_camofox() is True


def test_explicit_camofox_url_and_api_key_win_over_the_bundled_server(embedded, monkeypatch):
    monkeypatch.setenv("CAMOFOX_URL", "http://other-host:9377/")
    monkeypatch.setenv("CAMOFOX_API_KEY", "external-key")
    assert bc.get_camofox_url() == "http://other-host:9377"
    assert bc._auth_headers() == {"Authorization": "Bearer external-key"}
    assert bc._using_embedded_camofox() is False


def test_bundled_server_is_not_used_when_another_browser_is_selected(embedded):
    choose_browser("Local Browser")
    assert bc.get_camofox_url() == ""
    assert bc._auth_headers() == {}
    assert bc.is_camofox_mode() is False


def test_bundled_server_never_becomes_the_default_browser(install, data):
    assert bc.get_camofox_url() == ""
    assert bc.is_camofox_mode() is False


def test_selection_and_identity_do_not_depend_on_the_server_running(embedded):
    emb.disable()
    assert bc.get_camofox_url() == emb.BASE_URL
    assert bc.is_camofox_mode() is True
    assert bc._auth_headers() == {"Authorization": f"Bearer {emb.access_key()}"}
    assert bc._managed_persistence_enabled() is True


def test_persistence_is_on_for_the_bundled_server_even_if_config_says_off(embedded):
    with UnixServer(emb.socket_path(), reply=REPLY):
        assert bc._managed_persistence_enabled() is True
        session = bc._get_session("task-1")
        assert session["managed"] is True
        bc._drop_session("task-1")
        assert bc._get_session("task-1")["user_id"] == session["user_id"]
        assert session["user_id"] == get_camofox_identity("task-1")["user_id"]


def test_persistence_stays_config_driven_for_external_server(embedded, monkeypatch):
    monkeypatch.setenv("CAMOFOX_URL", "http://other-host:9377")
    assert bc._managed_persistence_enabled() is False
    assert bc._get_session("task-2")["managed"] is False
    with patch(
        "tools.browser_camofox.load_config",
        return_value={"browser": {"camofox": {"managed_persistence": True}}},
    ):
        assert bc._managed_persistence_enabled() is True


def test_soft_cleanup_keeps_the_profile_for_the_bundled_server(embedded):
    with UnixServer(emb.socket_path(), reply=REPLY):
        bc._get_session("task-3")
        assert bc.camofox_soft_cleanup("task-3") is True


# ── Транспорт: Unix-сокет, а не TCP ────────────────────────────────────────


def test_navigation_goes_over_the_socket_with_the_key(embedded):
    with UnixServer(emb.socket_path(), reply=REPLY) as server:
        result = json.loads(bc.camofox_navigate("https://example.com/", "nav"))
    assert result["success"] is True
    assert server.seen
    # /health открыт без ключа; всё остальное идёт с ключом установки.
    keyed = [entry for entry in server.seen if entry["path"] != "/health"]
    assert keyed
    assert {entry["auth"] for entry in keyed} == {f"Bearer {emb.access_key()}"}
    assert {entry["host"] for entry in server.seen} == {emb.UNIX_HOST}
    assert "/tabs" in [entry["path"] for entry in keyed]


def test_a_tcp_listener_on_loopback_never_receives_the_installation_key(embedded):
    # Подмена из ревью: порт установки больше не существует, а чужой слушатель
    # не получает ни запроса, ни ключа, даже когда настоящий сервер молчит.
    with TcpSpy() as spy:
        result = json.loads(bc.camofox_navigate("https://example.com/", "spoof"))
        assert result["success"] is False
        with UnixServer(emb.socket_path(), reply=REPLY):
            assert json.loads(bc.camofox_navigate("https://example.com/", "spoof2"))["success"] is True
        bc.check_camofox_available()
    assert spy.seen == []


def test_check_camofox_available_uses_the_socket(embedded):
    assert bc.check_camofox_available() is False
    with UnixServer(emb.socket_path(), reply=REPLY):
        assert bc.check_camofox_available() is True


def test_external_server_still_uses_plain_tcp(embedded, monkeypatch):
    with TcpSpy() as spy:
        monkeypatch.setenv("CAMOFOX_URL", f"http://127.0.0.1:{spy.port}")
        monkeypatch.setenv("CAMOFOX_API_KEY", "external-key")
        assert bc._using_embedded_camofox() is False
        bc._get("/tabs", params={"userId": "u"})
    assert spy.seen == ["Bearer external-key"]


# ── Сервер выключен или молчит: понятная ошибка до создания сессии ─────────


def test_silent_server_gives_a_russian_error_before_any_session_exists(embedded):
    result = json.loads(bc.camofox_navigate("https://example.com", "silent"))
    assert result["success"] is False
    assert "не отвечает" in result["error"]
    assert "korra doctor" in result["error"]
    assert emb.access_key() not in result["error"]
    assert "Traceback" not in result["error"] and "Errno" not in result["error"]
    assert bc._sessions == {}


def test_disabled_server_with_embedded_selected_says_how_to_turn_it_on(embedded):
    emb.disable()
    result = json.loads(bc.camofox_navigate("https://example.com", "off"))
    assert result["success"] is False
    assert "выключен" in result["error"]
    assert "korra doctor --fix" in result["error"]
    assert bc._sessions == {}
    snapshot = json.loads(bc.camofox_snapshot(task_id="off"))
    assert snapshot["success"] is False and "выключен" in snapshot["error"]
    assert bc._sessions == {}


def test_no_anonymous_session_is_ever_created_or_cached(embedded):
    emb.disable()
    for tool in (
        lambda: bc.camofox_navigate("https://example.com", "anon"),
        lambda: bc.camofox_click("e1", "anon"),
        lambda: bc.camofox_scroll("down", "anon"),
    ):
        tool()
        assert bc._sessions == {}


def test_after_reenabling_the_same_profile_keeps_its_identity(embedded):
    expected = get_camofox_identity("keep")
    with UnixServer(emb.socket_path(), reply=REPLY):
        before = bc._get_session("keep")
    bc._drop_session("keep")

    emb.disable()
    json.loads(bc.camofox_navigate("https://example.com", "keep"))
    assert bc._sessions == {}

    assert emb.sync() is True  # владелец включил заново
    with UnixServer(emb.socket_path(), reply=REPLY):
        result = json.loads(bc.camofox_navigate("https://example.com", "keep"))
        after = bc._get_session("keep")
    assert result["success"] is True
    assert after["managed"] is True
    assert after["user_id"] == before["user_id"] == expected["user_id"]
    assert after["session_key"] == before["session_key"] == expected["session_key"]


def test_server_dying_mid_session_is_explained_in_words(embedded):
    with UnixServer(emb.socket_path(), reply=REPLY):
        bc._get_session("mid")
    with pytest.raises(bc.CamofoxUnreachable) as exc:
        bc._get("/tabs/x/snapshot")
    assert "не отвечает" in str(exc.value)
    assert "Errno" not in str(exc.value) and "Max retries" not in str(exc.value)


def test_external_server_keeps_the_original_connection_error(embedded, monkeypatch):
    monkeypatch.setenv("CAMOFOX_URL", "http://127.0.0.1:1")
    with patch("tools.browser_camofox.requests.post", side_effect=requests.ConnectionError("boom")):
        result = json.loads(bc.camofox_navigate("https://example.com", "t"))
    assert "Cannot connect to Camofox at http://127.0.0.1:1" in result["error"]


# ── Два профиля одной установки ────────────────────────────────────────────


def test_two_profiles_with_different_browsers_do_not_disturb_each_other(install, data):
    with in_profile(data, "a"):
        choose_browser("Camofox")
        id_a = get_camofox_identity("a")
    assert emb.is_enabled()
    with in_profile(data, "b"):
        choose_browser("Local Browser")
        id_b = get_camofox_identity("b")
        assert bc.get_camofox_url() == ""
        assert bc.is_camofox_mode() is False
    assert emb.is_enabled()  # профиль A всё ещё пользуется сервером
    with in_profile(data, "a"):
        assert bc.is_camofox_mode() is True
        assert bc.get_camofox_url() == emb.BASE_URL
        assert bc._managed_persistence_enabled() is True
        with UnixServer(emb.socket_path(), reply=REPLY):
            assert json.loads(bc.camofox_navigate("https://example.com", "a"))["success"] is True
            assert bc._get_session("a")["user_id"] == id_a["user_id"]
    assert id_a["user_id"] != id_b["user_id"]
    with in_profile(data, "a"):
        choose_browser("Local Browser")
    assert not emb.is_enabled()  # последний потребитель ушёл


# ── SSRF и приватные адреса: Camofox не слабее Chromium ───────────────────


def _navigate(url, monkeypatch, terminal_env="local"):
    import tools.browser_tool as bt

    monkeypatch.setenv("TERMINAL_ENV", terminal_env)
    with patch("tools.browser_camofox.camofox_navigate", return_value='{"success": true}') as nav:
        result = json.loads(bt.browser_navigate(url, task_id="ssrf"))
    return result, nav


def test_metadata_endpoint_is_blocked_for_the_bundled_server(embedded, monkeypatch):
    result, nav = _navigate("http://169.254.169.254/latest/meta-data/", monkeypatch)
    assert result["success"] is False
    assert "metadata" in result["error"]
    nav.assert_not_called()


def test_private_address_is_blocked_when_the_terminal_is_not_local(embedded, monkeypatch):
    result, nav = _navigate("http://10.0.0.5/admin", monkeypatch, terminal_env="docker")
    assert result["success"] is False
    assert "private" in result["error"].lower()
    nav.assert_not_called()


def test_public_page_reaches_the_bundled_server(embedded, monkeypatch):
    with patch("tools.browser_tool._is_safe_url", return_value=True):
        result, nav = _navigate("https://example.com/", monkeypatch)
    assert result.get("success") is True
    nav.assert_called_once()
