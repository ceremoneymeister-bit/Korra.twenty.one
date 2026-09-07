"""Real calculator dashboard requests keep role setup and engine files closed."""

import hashlib
import json
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from korra_cli import web_server
from korra_cli.calc_policy import _allows_mutation


@pytest.fixture
def cabinet(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_UI_MODE", "calc")
    monkeypatch.setenv("KORRA_DASHBOARD_FILES_ROOT", str(tmp_path))
    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    monkeypatch.setattr(web_server.app.state, "bound_host", None, raising=False)
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    yield client, tmp_path
    client.close()


def test_files_only_expose_client_and_only_mutate_inbox(cabinet):
    client, root = cabinet
    secret = root / "config.yaml"
    secret.write_text("secret runtime settings")
    delivery = root / "client" / "delivery" / "report.txt"
    delivery.parent.mkdir(parents=True)
    delivery.write_text("immutable report")

    assert client.get("/api/files", params={"path": str(root)}).status_code == 403
    assert client.get("/api/files/text", params={"path": str(secret)}).status_code == 403
    assert client.get("/api/files/text", params={"path": str(delivery)}).status_code == 200
    assert client.put("/api/files/text", json={
        "path": str(delivery), "content": "tampered",
        "expected_sha256": hashlib.sha256(delivery.read_bytes()).hexdigest(),
    }).status_code == 403
    assert delivery.read_text() == "immutable report"

    listing = client.get("/api/files", params={"path": str(root / "client")}).json()
    for entry in listing["entries"]:
        assert entry["capabilities"] == {"rename": False, "trash": False}
        for endpoint in ("rename", "trash"):
            assert client.post(f"/api/files/{endpoint}", json={
                "path": entry["path"], "expected_revision": entry["revision"],
                "new_name": "changed",
            }).status_code == 403

    incoming = root / "client" / "inbox" / "request.txt"
    response = client.post("/api/files/upload", json={
        "path": str(incoming), "data_url": "data:text/plain;base64,dGVzdA==",
    })
    assert response.status_code == 200, response.text
    assert incoming.read_text() == "test"
    escape = root / "client" / "inbox" / "escape"
    escape.symlink_to(root, target_is_directory=True)
    assert client.post("/api/files/upload", json={
        "path": str(escape / "config.yaml"), "data_url": "data:text/plain;base64,dGVzdA==",
    }).status_code == 403
    assert secret.read_text() == "secret runtime settings"


@pytest.mark.parametrize("path", [
    "/api/profiles", "/api/profiles/raschet-route/soul", "/api/config/raw",
    "/api/tools", "/api/cron", "/api/profiles/raschet-route/open-terminal",
])
def test_admin_mutations_rejected_before_handler(cabinet, path):
    client, _root = cabinet
    assert client.post(path, json={}).status_code == 403


@pytest.mark.parametrize("key", ["METAL_CALC_SCOPE_SECRET", "PATH", "KORRA_MANAGED_DIR"])
def test_arbitrary_env_mutation_and_reveal_are_rejected(cabinet, key):
    client, _root = cabinet
    assert client.put("/api/env", json={"key": key, "value": "forged"}).status_code == 403
    assert client.post("/api/env/reveal", json={"key": key}).status_code == 403


def test_provider_setup_and_model_selection_remain_reachable(cabinet, monkeypatch):
    client, _root = cabinet
    import korra_cli.credential_lifecycle as credentials

    saved = []
    monkeypatch.setattr(credentials, "save_provider_env_credential", lambda key, value: saved.append((key, value)) or {"ok": True})
    assert client.put("/api/env", json={"key": "OPENAI_API_KEY", "value": "test-provider-key"}).status_code == 200
    assert saved == [("OPENAI_API_KEY", "test-provider-key")]
    # Validation is the handler's own 400/422, proving middleware permits it.
    assert client.post("/api/model/set", json={"scope": "invalid"}).status_code in {400, 422}
    assert _allows_mutation("/api/providers/oauth/openai-codex/start")
    assert _allows_mutation("/api/providers/oauth/openai-codex/submit")


def test_shell_websockets_are_closed(cabinet):
    client, _root = cabinet
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/ws"):
            pass
    assert rejected.value.code == 1008


def test_calc_routers_are_registered_and_use_dashboard_auth(cabinet, monkeypatch):
    client, root = cabinet
    from korra_cli.web_routers import calc_orders, calc_rates

    monkeypatch.setattr(calc_orders, "load_config", lambda: {"mcp_servers": {"metal_calc": {"env": {"METAL_CALC_ORDERS_ROOT": str(root / "orders")}}}})
    monkeypatch.setattr(calc_rates, "load_config", lambda: {"mcp_servers": {"metal_calc": {"env": {"METAL_CALC_RATES_ROOT": str(root / "rates")}}}})
    assert client.get("/api/calc/orders").status_code == 404  # unseeded registry
    assert client.get("/api/calc/rates/draft").status_code == 200
    client.headers.pop(web_server._SESSION_HEADER_NAME)
    assert client.get("/api/calc/rates/draft").status_code == 401


def test_config_and_profile_reads_do_not_disclose_runtime_secrets(cabinet, monkeypatch):
    client, root = cabinet
    import korra_cli.mcp_config as mcp_config

    secret = "calculator-scope-sentinel-91e847ff-secret"
    config = {
        "model": {"default": "test-model", "provider": "openai", "context_length": 64000},
        "auxiliary": {"vision": {"model": "vision-model", "api_key": secret, "max_tokens": 4000}},
        "custom_providers": [{"name": "custom", "api_key": secret}],
        "mcp_servers": {"metal_calc": {
            "command": "metal-calc-mcp", "env": {"METAL_CALC_SCOPE_SECRET": secret, "CUSTOM_CREDENTIAL": secret},
            "headers": {"Authorization": secret},
        }},
    }
    baseline = deepcopy(config)
    monkeypatch.setattr(web_server, "load_config", lambda: config)
    monkeypatch.setattr(web_server, "_profile_scope", lambda profile: nullcontext(root))
    monkeypatch.setattr(mcp_config, "_get_mcp_servers", lambda: config["mcp_servers"])

    for profile in (None, "raschet-route"):
        params = {"profile": profile} if profile else {}
        response = client.get("/api/config", params=params)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert secret not in json.dumps(payload)
        assert payload["model"] == "test-model"
        assert payload["model_context_length"] == 64000
        assert payload["auxiliary"]["vision"]["model"] == "vision-model"
        assert payload["auxiliary"]["vision"]["max_tokens"] == 4000
        assert client.get("/api/config/raw", params=params).status_code == 403
        response = client.get("/api/mcp/servers", params=params)
        assert response.status_code == 200, response.text
        assert secret not in json.dumps(response.json())
        assert response.json()["servers"][0]["env"]["METAL_CALC_SCOPE_SECRET"] == "***"
    assert config == baseline  # response filtering cannot erase runtime credentials


def test_dashboard_preferences_can_change_without_runtime_config_mutation(cabinet, monkeypatch):
    client, _root = cabinet
    state = {"mcp_servers": {"metal_calc": {"command": "immutable-mcp"}}, "dashboard": {}}
    baseline = deepcopy(state["mcp_servers"])
    monkeypatch.setattr(web_server, "load_config", lambda: deepcopy(state))
    monkeypatch.setattr(web_server, "save_config", lambda updated: state.update(updated))
    assert client.put("/api/dashboard/theme", json={"name": "korra"}).status_code == 200
    assert client.put("/api/dashboard/font", json={"font": "onest"}).status_code == 200
    assert state["dashboard"] == {"theme": "korra", "font": "onest"}
    assert state["mcp_servers"] == baseline
