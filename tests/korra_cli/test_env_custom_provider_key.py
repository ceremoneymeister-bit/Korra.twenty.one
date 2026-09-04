"""GET /api/env знает ключ провайдера, объявленного в конфигурации.

Свой endpoint подключают через ``custom_providers`` / ``providers``, а ключ к
нему называют в ``key_env``. Каталог движка (``CANONICAL_PROVIDERS``) о таком
провайдере не знает, поэтому его ключ приезжал на страницу «Ключи и доступы»
безымянным ``custom``-рядом: в клиентском режиме такие ряды не показываются
вовсе, а счётчик «Настроено N из M провайдеров» их не считал. Контур,
работающий именно на этом ключе, при этом честно отвечал — экран противоречил
факту («настроено 0 из 15» при живом агенте).
"""

import korra_cli.config as config
from fastapi.testclient import TestClient

import korra_cli.web_server as web_server
from korra_cli.web_server import _SESSION_TOKEN, app

client = TestClient(app)
HEADERS = {"X-Hermes-Session-Token": _SESSION_TOKEN}

CUSTOM_PROVIDER = {
    "name": "dario",
    "base_url": "http://127.0.0.1:3456",
    "key_env": "DARIO_API_KEY",
    "api_mode": "anthropic_messages",
    "model": "claude-opus-4-8",
}


def _env_rows(monkeypatch, env_on_disk, providers=(CUSTOM_PROVIDER,)):
    """Живой GET /api/env поверх заданных .env и списка своих провайдеров."""
    monkeypatch.setattr(web_server, "load_env", lambda: dict(env_on_disk))
    # Канальные учётные данные читают настоящую конфигурацию — гасим, чтобы
    # проверять именно провайдерскую ветку.
    monkeypatch.setattr(web_server, "_channel_managed_env_keys", lambda: set())
    monkeypatch.setattr(config, "load_config", lambda *a, **k: {})
    monkeypatch.setattr(
        config, "get_compatible_custom_providers", lambda cfg=None: list(providers)
    )
    resp = client.get("/api/env", headers=HEADERS)
    assert resp.status_code == 200
    return resp.json()


def test_custom_provider_key_is_a_provider_row(monkeypatch):
    rows = _env_rows(monkeypatch, {"DARIO_API_KEY": "sk-secret-value"})
    assert "DARIO_API_KEY" in rows
    row = rows["DARIO_API_KEY"]
    assert row["category"] == "provider", "ключ провайдера остался безымянным custom"
    assert row["is_set"] is True
    assert row["custom"] is False


def test_custom_provider_key_carries_provider_identity(monkeypatch):
    """По этим полям панель собирает карточку и считает «настроено N из M»."""
    rows = _env_rows(monkeypatch, {"DARIO_API_KEY": "sk-secret-value"})
    row = rows["DARIO_API_KEY"]
    assert row["provider"] == "custom:dario"
    assert row["provider_label"] == "dario"


def test_custom_provider_key_stays_secret(monkeypatch):
    rows = _env_rows(monkeypatch, {"DARIO_API_KEY": "sk-secret-value"})
    row = rows["DARIO_API_KEY"]
    assert row["is_password"] is True
    assert "sk-secret-value" not in str(row)


def test_no_custom_providers_changes_nothing(monkeypatch):
    """Пустая конфигурация не выдумывает провайдерских рядов."""
    rows = _env_rows(monkeypatch, {"SOME_OTHER_KEY": "x"}, providers=())
    assert rows["SOME_OTHER_KEY"]["category"] == "custom"


def test_catalogued_key_is_not_hijacked(monkeypatch):
    """Совпадение ``key_env`` с каталогом не переписывает карточку каталога."""
    rows = _env_rows(
        monkeypatch,
        {"ANTHROPIC_API_KEY": "sk-x"},
        providers=({"name": "свой", "key_env": "ANTHROPIC_API_KEY"},),
    )
    assert rows["ANTHROPIC_API_KEY"]["provider"] == "anthropic"
