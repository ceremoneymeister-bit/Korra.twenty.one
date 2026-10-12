"""K21-336: клиент Camofox находит встроенный сервер сам и не ломает прежние режимы."""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

import tools.browser_camofox as bc
from korra_cli import camofox_embedded as emb


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("CAMOFOX_URL", raising=False)
    monkeypatch.delenv("CAMOFOX_API_KEY", raising=False)
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    yield
    with bc._sessions_lock:
        bc._sessions.clear()


@pytest.fixture
def embedded(tmp_path, monkeypatch):
    """Встроенный сервер включён и выбран в настройках."""
    home = tmp_path / "opt-camofox"
    server = home.joinpath(*emb._SERVER_RELPATH)
    server.parent.mkdir(parents=True)
    server.write_text("")
    (home / "browser").mkdir()
    (home / "browser" / "camoufox-bin").write_text("")
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(home))
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(data))
    emb.enable()
    selection = {"value": "camofox"}
    monkeypatch.setattr(
        "tools.tool_backend_helpers.read_selection", lambda section: selection["value"]
    )
    return selection


def test_url_and_key_come_from_the_bundled_server_without_any_env(embedded):
    assert bc.get_camofox_url() == emb.endpoint()
    assert bc.get_camofox_url().startswith("http://127.0.0.1:")
    assert bc._auth_headers() == {"Authorization": f"Bearer {emb.access_key()}"}
    assert bc.is_camofox_mode() is True


def test_explicit_camofox_url_and_api_key_win_over_the_bundled_server(embedded, monkeypatch):
    monkeypatch.setenv("CAMOFOX_URL", "http://other-host:9377/")
    monkeypatch.setenv("CAMOFOX_API_KEY", "external-key")
    assert bc.get_camofox_url() == "http://other-host:9377"
    assert bc._auth_headers() == {"Authorization": "Bearer external-key"}
    assert bc._using_embedded_camofox() is False


def test_bundled_server_is_not_used_when_another_browser_is_selected(embedded):
    embedded["value"] = "local"
    assert bc.get_camofox_url() == ""
    assert bc._auth_headers() == {}
    assert bc.is_camofox_mode() is False


def test_bundled_server_never_becomes_the_default_browser(embedded):
    embedded["value"] = None
    assert bc.get_camofox_url() == ""
    assert bc.is_camofox_mode() is False


def test_disabled_bundled_server_gives_no_url(embedded):
    emb.disable()
    assert bc.get_camofox_url() == ""
    assert bc._auth_headers() == {}


def test_persistence_is_on_for_the_bundled_server_even_if_config_says_off(embedded):
    with patch("tools.browser_camofox.load_config", return_value={"browser": {"camofox": {}}}):
        assert bc._managed_persistence_enabled() is True
        session = bc._get_session("task-1")
    assert session["managed"] is True
    bc._drop_session("task-1")
    with patch("tools.browser_camofox.load_config", return_value={"browser": {"camofox": {}}}):
        assert bc._get_session("task-1")["user_id"] == session["user_id"]


def test_persistence_stays_config_driven_for_external_server(embedded, monkeypatch):
    monkeypatch.setenv("CAMOFOX_URL", "http://other-host:9377")
    with patch("tools.browser_camofox.load_config", return_value={"browser": {"camofox": {}}}):
        assert bc._managed_persistence_enabled() is False
        assert bc._get_session("task-2")["managed"] is False
    with patch(
        "tools.browser_camofox.load_config",
        return_value={"browser": {"camofox": {"managed_persistence": True}}},
    ):
        assert bc._managed_persistence_enabled() is True


def test_soft_cleanup_keeps_the_profile_for_the_bundled_server(embedded):
    with patch("tools.browser_camofox.load_config", return_value={"browser": {"camofox": {}}}):
        bc._get_session("task-3")
        assert bc.camofox_soft_cleanup("task-3") is True


def test_unreachable_bundled_server_is_explained_in_words_without_secrets(embedded):
    with patch(
        "tools.browser_camofox.requests.post",
        side_effect=requests.ConnectionError("HTTPConnectionPool(host='127.0.0.1')"),
    ), patch("tools.browser_camofox.load_config", return_value={"browser": {"camofox": {}}}):
        result = json.loads(bc.camofox_navigate("https://example.com", "t"))
    assert result["success"] is False
    assert "не отвечает" in result["error"]
    assert "korra doctor" in result["error"]
    assert emb.access_key() not in result["error"]
    assert "Traceback" not in result["error"] and "HTTPConnectionPool" not in result["error"]


def test_other_tools_also_get_words_when_the_bundled_server_is_down(embedded):
    with patch(
        "tools.browser_camofox.requests.get",
        side_effect=requests.ConnectionError("Max retries exceeded"),
    ):
        with pytest.raises(bc.CamofoxUnreachable) as exc:
            bc._get("/tabs/x/snapshot")
    assert "не отвечает" in str(exc.value)
    assert "Max retries" not in str(exc.value)


def test_external_server_keeps_the_original_connection_error(monkeypatch):
    monkeypatch.setenv("CAMOFOX_URL", "http://127.0.0.1:1")
    with patch(
        "tools.browser_camofox.requests.post", side_effect=requests.ConnectionError("boom")
    ), patch("tools.browser_camofox.load_config", return_value={"browser": {"camofox": {}}}):
        result = json.loads(bc.camofox_navigate("https://example.com", "t"))
    assert "Cannot connect to Camofox at http://127.0.0.1:1" in result["error"]


def test_requests_carry_the_bundled_key_to_the_bundled_url(embedded):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"ok": True}
    with patch("tools.browser_camofox.requests.post", return_value=resp) as post:
        bc._post("/tabs/x/click", {"ref": "e1"})
    args, kwargs = post.call_args
    assert args[0] == f"{emb.endpoint()}/tabs/x/click"
    assert kwargs["headers"] == {"Authorization": f"Bearer {emb.access_key()}"}


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
