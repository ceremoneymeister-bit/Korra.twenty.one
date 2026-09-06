"""Выбранный в мастере провайдер доступен и при другом провайдере главного."""

from pathlib import Path

import pytest
import yaml

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "selected-provider-test")
    from korra_cli.web_server import app

    with TestClient(app, raise_server_exceptions=False) as http:
        http.headers["Authorization"] = "Bearer selected-provider-test"
        yield http, tmp_path


@pytest.mark.parametrize("schema", ["custom_providers", "providers"])
@pytest.mark.parametrize("operation", ["create", "update"])
def test_selected_connection_and_key_reach_profile_without_copying_other_connections(client, schema, operation):
    from agent.secret_scope import load_env_file
    from korra_cli.config import get_compatible_custom_providers
    from korra_cli.providers import resolve_provider_full

    http, root = client
    row = {"name": "selected", "base_url": "https://selected.example.invalid/v1", "key_env": "SELECTED_API_KEY"}
    other = {"name": "unrelated", "base_url": "https://unrelated.example.invalid/v1", "key_env": "UNRELATED_API_KEY"}
    if schema == "providers":
        row = {"name": "selected", "api": row["base_url"], "key_env": "SELECTED_API_KEY"}
        other = {"name": "unrelated", "api": other["base_url"], "key_env": "UNRELATED_API_KEY"}
    cfg = {"model": {"provider": "openai-codex", "default": "gpt-test"},
           schema: [row, other] if schema == "custom_providers" else {"selected": row, "unrelated": other}}
    config_path = root / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg))
    env_path = root / ".env"
    env_path.write_text("SELECTED_API_KEY=selected-secret\nUNRELATED_API_KEY=other-secret\nTELEGRAM_BOT_TOKEN=never-copy\n")
    original = config_path.read_bytes(), env_path.read_bytes()
    model = {"provider": "custom:selected", "model": "selected-model"}
    if operation == "create":
        response = http.post("/api/profiles", json={"name": "learner", "no_skills": True, **model})
    else:
        assert http.post("/api/profiles", json={"name": "learner", "no_skills": True}).status_code == 200
        response = http.put("/api/profiles/learner/model", json=model)
    assert response.status_code == 200, response.text
    assert response.json()["seeded_credentials"] == ["SELECTED_API_KEY"]
    target = root / "profiles" / "learner"
    saved = yaml.safe_load((target / "config.yaml").read_text())
    definition = resolve_provider_full("custom:selected", user_providers=saved.get("providers"), custom_providers=get_compatible_custom_providers(saved))
    assert definition is not None and definition.base_url == "https://selected.example.invalid/v1"
    assert saved[schema] == ([row] if schema == "custom_providers" else {"selected": row})
    assert load_env_file(target / ".env")["SELECTED_API_KEY"] == "selected-secret"
    assert not {"UNRELATED_API_KEY", "TELEGRAM_BOT_TOKEN"}.intersection(load_env_file(target / ".env"))
    assert (config_path.read_bytes(), env_path.read_bytes()) == original


def test_model_switch_preserves_profile_connection_and_secrets(client):
    from agent.secret_scope import load_env_file

    http, root = client
    root_cfg = {"model": {"provider": "openai-codex", "default": "gpt-test"},
                "custom_providers": [{"name": "selected", "base_url": "https://root.example.invalid/v1", "key_env": "SELECTED_API_KEY"}]}
    (root / "config.yaml").write_text(yaml.safe_dump(root_cfg))
    (root / ".env").write_text("SELECTED_API_KEY=root-secret\n")
    assert http.post("/api/profiles", json={"name": "independent", "no_skills": True}).status_code == 200
    target = root / "profiles" / "independent"
    local = yaml.safe_load((target / "config.yaml").read_text())
    local["custom_providers"] = [
        {"name": "selected", "base_url": "https://profile.example.invalid/v1", "key_env": "SELECTED_API_KEY"},
        {"name": "retained", "base_url": "https://retained.example.invalid/v1"},
    ]
    (target / "config.yaml").write_text(yaml.safe_dump(local))
    (target / ".env").write_text("SELECTED_API_KEY=profile-secret\n")
    response = http.put("/api/profiles/independent/model", json={"provider": "custom:selected", "model": "new-model"})
    assert response.status_code == 200, response.text
    assert response.json()["seeded_credentials"] == []
    assert yaml.safe_load((target / "config.yaml").read_text())["custom_providers"] == local["custom_providers"]
    assert load_env_file(target / ".env")["SELECTED_API_KEY"] == "profile-secret"
