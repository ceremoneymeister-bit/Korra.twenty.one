"""K21-336: клиент Camofox находит встроенный сервер сам и ходит к нему по Unix-сокету."""

import json
import time
from unittest.mock import patch

import pytest
import requests

import tools.browser_camofox as bc
from korra_cli import camofox_embedded as emb
from tests.fakes.camofox_embedded import (  # noqa: F401  (фикстуры)
    TcpSpy,
    UnixServer,
    choose_browser,
    chromium_root,
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


@pytest.fixture(autouse=True)
def _server_state(install, data):
    """Сервер при старте сам создаёт ключ и каталог; клиент их не создаёт."""
    emb.ensure_state()


@pytest.fixture
def embedded(install, data):
    """Camofox выбран в настройках (настоящая запись выбора), сервер включён."""
    choose_browser("Camofox")
    assert emb.is_enabled()
    return data


def test_url_and_key_come_from_the_bundled_server_without_any_env(embedded):
    with UnixServer(emb.socket_path(), reply=REPLY):
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


def test_profile_without_a_browser_choice_uses_the_default(install, data):
    with UnixServer(emb.socket_path(), reply=REPLY) as server:
        assert bc.get_camofox_url() == emb.BASE_URL
        assert bc.is_camofox_mode() is True
        assert bc._using_embedded_camofox() is True
        assert bc._auth_headers()
    assert server.seen


@pytest.mark.parametrize(
    "browser",
    [
        {"cloud_provider": "local"},
        {"cloud_provider": "browserbase"},
        {"cdp_url": "http://127.0.0.1:9222"},
        {"backend": "browser-use"},
        {"backend": False},
        {"engine": "chromium"},
        {"use_real_profile": True},
    ],
)
def test_explicit_choices_keep_the_default_out(install, data, browser):
    from korra_cli.config import save_config

    save_config({"browser": browser})
    with UnixServer(emb.socket_path(), reply=REPLY):
        assert bc.get_camofox_url() == ""
        assert bc.is_camofox_mode() is False


def test_external_server_beats_the_default(install, data, monkeypatch):
    monkeypatch.setenv("CAMOFOX_URL", "http://other-host:9377")
    assert bc.get_camofox_url() == "http://other-host:9377"
    assert bc.is_camofox_mode() is True
    assert bc._using_embedded_camofox() is False
    assert bc.embedded_fallback_note() == ""


def test_nothing_changes_outside_the_image(tmp_path, monkeypatch, data):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    assert bc.get_camofox_url() == ""
    assert bc.is_camofox_mode() is False
    assert bc.embedded_fallback_note() == ""


def test_identity_does_not_depend_on_the_server_running(embedded):
    emb.disable(signal=False)
    assert bc.get_camofox_url() == emb.BASE_URL
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


def test_a_tcp_listener_on_loopback_never_receives_the_installation_key(embedded, chromium_navigate):
    # Подмена из ревью: порт установки больше не существует, а чужой слушатель
    # не получает ни запроса, ни ключа, даже когда настоящий сервер молчит.
    with TcpSpy() as spy:
        json.loads(bc.camofox_navigate("https://example.com/", "spoof"))
        chromium_navigate.assert_called_once()  # молчащий сервер: страница в Chromium, не на чужой порт
        with UnixServer(emb.socket_path(), reply=REPLY):
            assert json.loads(bc.camofox_navigate("https://example.com/", "spoof2"))["success"] is True
        bc.check_camofox_available()
    assert spy.seen == []


def test_check_camofox_available_uses_the_socket(embedded):
    assert bc.check_camofox_available() is False
    with UnixServer(emb.socket_path(), reply=REPLY):
        assert bc.check_camofox_available() is True


def test_availability_check_rejects_http_200_with_failed_readiness(embedded):
    with UnixServer(emb.socket_path(), reply=b'{"ok":false}'):
        assert bc.check_camofox_available() is False


def test_external_server_still_uses_plain_tcp(embedded, monkeypatch):
    with TcpSpy() as spy:
        monkeypatch.setenv("CAMOFOX_URL", f"http://127.0.0.1:{spy.port}")
        monkeypatch.setenv("CAMOFOX_API_KEY", "external-key")
        assert bc._using_embedded_camofox() is False
        bc._get("/tabs", params={"userId": "u"})
    assert spy.seen == ["Bearer external-key"]


# ── Сервер молчит или выключен владельцем ──────────────────────────────────


@pytest.fixture
def chromium_navigate():
    """Обычный Chromium вместо настоящего: возвращает признак, что страницу открыл он."""
    with patch("tools.browser_tool._browser_navigate", return_value='{"success": true, "via": "chromium"}') as nav:
        yield nav


def test_silent_server_gives_a_russian_error_to_tools_that_need_a_page(embedded):
    snapshot = json.loads(bc.camofox_snapshot(task_id="silent"))
    assert snapshot["success"] is False
    assert "не отвечает" in snapshot["error"] and "korra doctor" in snapshot["error"]
    assert emb.access_key() not in snapshot["error"]
    assert "Traceback" not in snapshot["error"] and "Errno" not in snapshot["error"]
    assert bc._sessions == {}


def test_silent_server_turns_the_same_navigate_call_into_chromium(embedded, chromium_navigate):
    result = json.loads(bc.camofox_navigate("https://example.com", "silent"))
    assert result == {"success": True, "via": "chromium"}
    chromium_navigate.assert_called_once_with("https://example.com", "silent")
    assert bc._sessions == {}


def test_server_switched_off_with_camofox_selected_explains_the_next_step(embedded, chromium_navigate):
    emb.disable(signal=False)
    result = json.loads(bc.camofox_navigate("https://example.com", "off"))
    chromium_navigate.assert_not_called()
    assert result["success"] is False
    assert "выключен" in result["error"]
    assert "korra tools camofox on" in result["error"]
    assert bc._sessions == {}
    snapshot = json.loads(bc.camofox_snapshot(task_id="off"))
    assert snapshot["success"] is False and "korra tools camofox on" in snapshot["error"]
    assert bc._sessions == {}


def test_server_switched_off_profile_without_a_choice_goes_to_chromium_without_an_error(install, data):
    emb.disable(signal=False)
    assert bc.is_camofox_mode() is False
    assert bc.get_camofox_url() == ""
    assert bc.embedded_fallback_note() == ""
    result, run, camofox = _navigate_through_dispatch()
    camofox.assert_not_called()
    assert result["success"] is True and "note" not in result
    emb.enable()
    with UnixServer(emb.socket_path(), reply=REPLY):
        bc._health_cache = (0.0, "", True)
        assert bc.is_camofox_mode() is True


def test_no_anonymous_session_is_ever_created_or_cached(embedded):
    emb.disable(signal=False)
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

    emb.disable(signal=False)
    json.loads(bc.camofox_navigate("https://example.com", "keep"))
    assert bc._sessions == {}

    emb.enable()  # владелец включил заново
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


def test_two_profiles_with_different_browsers_do_not_disturb_each_other(chromium_root, install, data):
    with in_profile(data, "a"):
        choose_browser("Camofox")
        id_a = get_camofox_identity("a")
    with in_profile(data, "b"):
        choose_browser("Local Browser")
        id_b = get_camofox_identity("b")
        assert bc.get_camofox_url() == ""
        assert bc.is_camofox_mode() is False
    assert emb.is_enabled()
    with in_profile(data, "a"), UnixServer(emb.socket_path(), reply=REPLY):
        assert bc.is_camofox_mode() is True
        assert bc.get_camofox_url() == emb.BASE_URL
        assert bc._managed_persistence_enabled() is True
        assert json.loads(bc.camofox_navigate("https://example.com", "a"))["success"] is True
        assert bc._get_session("a")["user_id"] == id_a["user_id"]
    assert id_a["user_id"] != id_b["user_id"]
    with in_profile(data, "a"):
        choose_browser("Local Browser")
    assert not emb.is_enabled()  # последний потребитель выбрал другой браузер


# ── SSRF и приватные адреса: Camofox не слабее Chromium ───────────────────


def _navigate(url, monkeypatch, terminal_env="local"):
    import tools.browser_tool as bt

    monkeypatch.setenv("TERMINAL_ENV", terminal_env)
    with UnixServer(emb.socket_path(), reply=REPLY), patch(
        "tools.browser_camofox.camofox_navigate", return_value='{"success": true}'
    ) as nav:
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


# ── Сервер не отвечает: вызов уходит в Chromium с пометкой ─────────────────


@pytest.fixture(autouse=True)
def _fresh_health():
    bc._health_cache = (0.0, "", True)
    yield
    bc._health_cache = (0.0, "", True)


def _navigate_through_dispatch(url="https://example.com/"):
    import tools.browser_tool as bt

    chromium = {"success": True, "data": {"title": "t", "url": url}}
    with patch("tools.browser_tool._run_browser_command", return_value=chromium) as run, patch(
        "tools.browser_camofox.camofox_navigate", return_value='{"success": true, "via": "camofox"}'
    ) as camofox:
        result = json.loads(bt.browser_navigate(url, task_id="fallback"))
    return result, run, camofox


def test_silent_selected_server_sends_the_call_to_chromium_with_a_short_note(embedded):
    assert bc.is_camofox_mode() is False
    result, run, camofox = _navigate_through_dispatch()
    camofox.assert_not_called()
    run.assert_called()
    assert result["success"] is True
    assert "Chromium" in result["note"] and "не отвечает" in result["note"]
    assert bc._sessions == {}


def test_running_server_takes_the_call_without_a_note(install, data):
    choose_browser("Camofox")
    with UnixServer(emb.socket_path(), reply=REPLY):
        result, run, camofox = _navigate_through_dispatch()
    camofox.assert_called_once()
    run.assert_not_called()
    assert "note" not in result


def test_explicit_chromium_choice_is_not_a_failure(chromium_root, install, data):
    result, run, camofox = _navigate_through_dispatch()
    camofox.assert_not_called()
    assert result["success"] is True and "note" not in result


# ── Сервер сломался посреди работы: тот же вызов уходит в Chromium ──────────


def _navigate_for_real(url="https://example.com/", task_id="live"):
    """Диспетчер как у агента: настоящий Camofox-клиент, Chromium подменён."""
    import tools.browser_tool as bt

    chromium = {"success": True, "data": {"title": "t", "url": url}}
    with patch("tools.browser_tool._run_browser_command", return_value=chromium) as run:
        result = json.loads(bt.browser_navigate(url, task_id=task_id))
    return result, run


def test_connection_lost_after_a_good_probe_resets_the_cache_and_the_same_call_uses_chromium(install, data):
    choose_browser("Camofox")
    # Сервер ответил на /health, кэш «жив», но к вкладке соединение рвётся.
    server = UnixServer(emb.socket_path(), reply=REPLY)
    with server:
        first, run_first = _navigate_for_real(task_id="first")
        assert "note" not in first and first.get("success") is True
        run_first.assert_not_called()
        assert bc._health_cache[2] is True
    # Сервер пропал, кэш ещё «жив» (TTL не вышел).
    assert bc._health_cache[2] is True and bc._health_cache[0] > 0
    result, run = _navigate_for_real(task_id="second")
    run.assert_called()
    assert result["success"] is True
    assert "Chromium" in result["note"]
    assert bc._health_cache[2] is False  # кэш сброшен: следующие вызовы сразу в Chromium
    assert bc.is_camofox_mode() is False
    assert not any(key[1] == "second" for key in bc._sessions)


def test_server_answering_5xx_on_tab_creation_sends_the_call_to_chromium(install, data):
    choose_browser("Camofox")
    def respond(method, path, body):
        if method == "POST" and path == "/tabs":
            return 500, b'{"error": "browser failed to launch"}'

    with UnixServer(emb.socket_path(), reply=REPLY, respond=respond) as server:
        result, run = _navigate_for_real(task_id="five")
    run.assert_called()
    assert result["success"] is True
    assert "Chromium" in result["note"]
    posts = [e for e in server.seen if e["method"] == "POST" and e["path"] == "/tabs"]
    assert len(posts) == 2  # сам вызов и проба пустой вкладки
    assert "about:blank" in posts[1]["body"]
    assert bc._health_cache[2] is False
    assert bc.is_camofox_mode() is False


def test_tab_creation_timeout_sends_the_call_to_chromium(install, data, monkeypatch):
    choose_browser("Camofox")
    def respond(method, path, body):
        if method == "POST" and path == "/tabs":
            time.sleep(1.5)
            return 500, b"{}"

    monkeypatch.setattr(bc, "_get_command_timeout", lambda: 1)
    with UnixServer(emb.socket_path(), reply=REPLY, respond=respond):
        result, run = _navigate_for_real(task_id="slow")
    run.assert_called()
    assert "Chromium" in result["note"]
    assert bc.is_camofox_mode() is False


@pytest.mark.parametrize("recovers", [True, False])
@pytest.mark.parametrize("failure_stage", ["tab", "health"])
def test_hung_tab_creation_restarts_service_and_retries_once_before_fallback(
    install, data, monkeypatch, recovers, failure_stage, caplog,
):
    restarted = False
    signals = []
    servers = []
    # Сохранённые входы двух профилей должны пережить восстановление побайтно.
    for name in ("a", "b"):
        saved = data / "camofox" / "profiles" / name / "storage-state.json"
        saved.parent.mkdir(parents=True)
        saved.write_text(name)

    def respond(method, path, body):
        if path == "/health" and failure_stage == "health" and not (restarted and recovers):
            return 503, b'{"ok":false,"contextReady":false}'
        if method == "POST" and path == "/tabs" and not (restarted and recovers):
            # Зависшая фабрика: HTTP-клиент должен сам прервать ожидание.
            time.sleep(2)
            return 500, b'{}'

    def signal(cmd, **kwargs):
        nonlocal restarted
        import subprocess

        signals.append(cmd)
        servers[-1].__exit__()
        restarted = True
        server = UnixServer(emb.socket_path(), reply=REPLY, respond=respond)
        server.__enter__()
        servers.append(server)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(emb, "in_container", lambda: True)
    monkeypatch.setattr(emb.subprocess, "run", signal)
    monkeypatch.setattr(emb, "_RESTART_WAIT", 2.0)
    monkeypatch.setattr(bc, "_get_command_timeout", lambda: 0.5)
    # Ускоряем только транспортный deadline, оставляя настоящий Unix HTTP-путь.
    request = bc._request

    def fast_request(method, *args, **kwargs):
        if method.lower() == "post":
            kwargs["timeout"] = 0.5
        return request(method, *args, **kwargs)

    monkeypatch.setattr(bc, "_request", fast_request)
    first = UnixServer(emb.socket_path(), reply=REPLY, respond=respond)
    first.__enter__()
    servers.append(first)
    try:
        with in_profile(data, "a"):
            choose_browser("Camofox")
            expected = get_camofox_identity("recover")
            result, chromium = _navigate_for_real(task_id="recover")
        assert len(signals) == 1
        assert signals[0][1:] == ["-t", emb.S6_SERVICE_DIR]
        posts = [e for s in servers for e in s.seen if e["method"] == "POST"]
        if failure_stage == "tab":
            assert len(posts) == 3  # запрос, диагностическая пустая вкладка, один повтор
            assert json.loads(posts[0]["body"]) == json.loads(posts[2]["body"])
        else:
            assert len(posts) == (1 if recovers else 0)  # сбой health лечится до создания вкладки
        assert all(json.loads(e["body"])["userId"] == expected["user_id"] for e in posts)
        if recovers:
            chromium.assert_not_called()
            assert result["success"] is True and "note" not in result
        else:
            chromium.assert_called()
            assert "Chromium" in result["note"]
            assert "Chromium" in caplog.text
        for name in ("a", "b"):
            assert (data / "camofox" / "profiles" / name / "storage-state.json").read_text() == name
    finally:
        servers[-1].__exit__()


def test_a_dead_site_does_not_mark_a_healthy_server_as_down(install, data):
    choose_browser("Camofox")
    # POST /tabs навигирует на адрес, поэтому недоступный сайт тоже даёт 500;
    # пустая вкладка при этом открывается, сервер исправен, ошибка остаётся ошибкой.
    def respond(method, path, body):
        if method == "POST" and path == "/tabs" and "dead.example" in body:
            return 500, b'{"error": "net::ERR_NAME_NOT_RESOLVED"}'

    with UnixServer(emb.socket_path(), reply=REPLY, respond=respond) as server:
        result, run = _navigate_for_real("https://dead.example/", task_id="dead")
        assert bc.is_camofox_mode() is True
    run.assert_not_called()
    assert result["success"] is False
    assert "note" not in result
    assert bc._health_cache[2] is True
    assert any(e["method"] == "DELETE" for e in server.seen)  # проба убрала за собой вкладку


def test_a_client_error_from_the_server_is_not_a_broken_server(install, data):
    choose_browser("Camofox")
    def respond(method, path, body):
        if method == "POST" and path == "/tabs":
            return 400, b'{"error": "bad url"}'

    with UnixServer(emb.socket_path(), reply=REPLY, respond=respond) as server:
        result, run = _navigate_for_real(task_id="four")
        assert bc.is_camofox_mode() is True
    run.assert_not_called()
    assert result["success"] is False
    assert len([e for e in server.seen if e["method"] == "POST"]) == 1  # пробы не было


def test_other_tools_after_a_lost_connection_get_a_russian_error_and_later_calls_use_chromium(install, data):
    choose_browser("Camofox")
    with UnixServer(emb.socket_path(), reply=REPLY):
        assert bc.is_camofox_mode() is True
        bc._get_session("mid")["tab_id"] = "t1"
    snapshot = json.loads(bc.camofox_snapshot(task_id="mid"))
    assert snapshot["success"] is False and "не отвечает" in snapshot["error"]
    assert bc.is_camofox_mode() is False  # дальше навигация уходит в Chromium


def test_an_external_server_failure_still_fails_as_before(install, data, monkeypatch):
    monkeypatch.setenv("CAMOFOX_URL", "http://127.0.0.1:1")
    with patch("tools.browser_camofox.requests.post", side_effect=requests.ConnectionError("boom")), patch(
        "tools.browser_tool._browser_navigate"
    ) as chromium:
        result = json.loads(bc.camofox_navigate("https://example.com", "ext"))
    chromium.assert_not_called()
    assert result["success"] is False and "Cannot connect to Camofox" in result["error"]
    assert bc.is_camofox_mode() is True


def test_external_server_failure_is_not_replaced_by_chromium(install, data, monkeypatch):
    monkeypatch.setenv("CAMOFOX_URL", "http://127.0.0.1:1")
    assert bc.is_camofox_mode() is True
    assert bc.embedded_fallback_note() == ""


def test_cdp_override_is_not_annotated(install, data, monkeypatch):
    monkeypatch.setenv("BROWSER_CDP_URL", "ws://127.0.0.1:9222/devtools/browser/x")
    assert bc.embedded_fallback_note() == ""


def test_the_server_coming_back_returns_calls_to_camofox(install, data, monkeypatch):
    choose_browser("Camofox")
    assert bc.is_camofox_mode() is False
    with UnixServer(emb.socket_path(), reply=REPLY):
        bc._health_cache = (0.0, "", True)  # срок пробы вышел
        assert bc.is_camofox_mode() is True


def test_health_probe_is_reused_for_a_few_seconds(install, data):
    choose_browser("Camofox")
    with patch.object(emb, "health", return_value=False) as probe:
        for _ in range(5):
            assert bc.is_camofox_mode() is False
    assert probe.call_count == 1
