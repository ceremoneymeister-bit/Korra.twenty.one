import json

import httpx
import pytest
import yaml


@pytest.fixture
def client(monkeypatch):
    from starlette.testclient import TestClient
    from korra_cli import web_server
    from korra_constants import get_hermes_home
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("{}\n")
    monkeypatch.delenv("MAX_BOT_TOKEN", raising=False)
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    return client


def test_channels_catalog_and_save(client):
    from korra_constants import get_hermes_home
    response = client.get("/api/messaging/platforms")
    assert response.status_code == 200
    platforms = response.json()["platforms"]
    ids = [item["id"] for item in platforms]
    entry = platforms[ids.index("max")]
    assert ids.index("max") == ids.index("telegram") + 1
    assert "личных сообщениях" in entry["description"] and "MAX" in entry["name"]
    token = next(item for item in entry["env_vars"] if item["key"] == "MAX_BOT_TOKEN")
    assert token["required"] and token["is_password"]
    assert "Чат-боты" in token["description"] and token["url"].startswith("https://dev.max.ru/")
    result = client.put("/api/messaging/platforms/max", json={"enabled": True, "env_vars": {"MAX_BOT_TOKEN": "fixture-secret", "MAX_ALLOWED_USERS": "12,34"}})
    assert result.status_code == 200, result.text
    config = yaml.safe_load((get_hermes_home() / "config.yaml").read_text())
    assert config["platforms"]["max"]["enabled"] is True
    assert "fixture-secret" not in result.text


@pytest.mark.parametrize("profile_token,expected", [("worker-token", "Бот Рабочий подключён"), ("bad-token", "Токен не принят"), ("", "Укажите токен бота MAX")])
def test_check_uses_selected_profile_without_starting_gateway(client, monkeypatch, profile_token, expected):
    from korra_cli import profiles
    from korra_constants import get_hermes_home
    home = get_hermes_home()
    worker = home / "profiles" / "worker"
    worker.mkdir(parents=True)
    (worker / "config.yaml").write_text("{}\n")
    (worker / ".env").write_text("MAX_BOT_TOKEN=" + profile_token + "\n")
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: home / "profiles")
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: home)
    monkeypatch.setenv("MAX_BOT_TOKEN", "root-secret-must-not-leak")
    requests = []
    def handle(request):
        requests.append(request)
        assert request.url.path == "/me" and request.method == "GET"
        assert request.headers["Authorization"] == profile_token
        return httpx.Response(401, json={}) if profile_token == "bad-token" else httpx.Response(200, json={"is_bot": True, "user_id": 15, "first_name": "Рабочий"})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(**{**kw, "transport": httpx.MockTransport(handle)}))
    result = client.post("/api/messaging/platforms/max/test?profile=worker")
    assert result.status_code == 200, result.text
    assert result.json()["message"] == expected
    assert len(requests) == bool(profile_token)
    assert "root-secret" not in result.text


@pytest.mark.parametrize("name,enabled", [("max", True), ("max-platform", True), ("max-platform", False)])
def test_user_plugin_collision_and_legacy_extra(tmp_path, monkeypatch, name, enabled):
    from korra_constants import get_hermes_home
    from korra_cli.plugins import PluginManager
    from gateway.platform_registry import platform_registry
    from gateway.config import GatewayConfig, Platform
    home = get_hermes_home()
    plugin = home / "plugins" / name
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(f"name: {name}\nkind: platform\nversion: 1.0.0\n")
    (plugin / "__init__.py").write_text(
        "def register(ctx):\n"
        "    ctx.register_platform(name='max', label='User MAX', adapter_factory=lambda cfg: cfg, check_fn=lambda: True)\n"
    )
    (home / "config.yaml").write_text(yaml.safe_dump({"plugins": {"enabled": [name] if enabled else [], "disabled": [] if enabled else [name]}}))
    manager = PluginManager()
    manager.discover_and_load()
    entry = platform_registry.get("max")
    assert entry is not None
    assert (entry.label == "User MAX") is enabled
    parsed = GatewayConfig.from_dict({"platforms": {"max": {"enabled": True, "extra": {"listen_host": "ignored", "auto_reply": False, "expected_bot_id": 123}}}})
    adapter = entry.adapter_factory(parsed.platforms[Platform("max")])
    assert adapter is not None  # no validation of obsolete installation fields
