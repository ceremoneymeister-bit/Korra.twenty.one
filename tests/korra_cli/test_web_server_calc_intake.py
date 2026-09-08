"""Actual HTTP→admin CLI→SQLite handoff; native transport is the only fake."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from korra_cli.web_routers import calc_intake, calc_orders, calc_rates


@pytest.fixture
def cabinet(tmp_path, monkeypatch):
    root = tmp_path / "orders"
    root.mkdir()
    package = Path(__file__).resolve().parents[2] / "calculator" / "metal_calc"
    binary = tmp_path / "metal-calc-admin"
    binary.write_text(f"#!{sys.executable}\nimport sys\nsys.path.insert(0,{str(package)!r})\n"
                      "from metal_calc.admin import main\nmain()\n")
    binary.chmod(0o700)
    config = {
        "mcp_servers": {"metal_calc": {
            "command": str(tmp_path / "metal-calc-mcp"),
            "args": ["--intake-only", "--session-db", str(tmp_path / "state.db")],
            "context_arguments": {tool: {"session_id": "session_id"} for tool in calc_intake.INTAKE_TOOLS},
            "tools": {"resources": False, "prompts": False},
            "env": {"METAL_CALC_ROLE": "front", "METAL_CALC_ORDERS_ROOT": str(root)},
        }},
        "platform_toolsets": {"api_server": ["metal_calc"]},
        "agent": {"disabled_toolsets": ["terminal", "file", "code_execution", "delegation", "context_engine"]},
    }
    monkeypatch.setattr(calc_rates, "load_config", lambda: config)
    app = FastAPI()
    app.include_router(calc_orders.router)
    with TestClient(app) as client:
        uid = str(uuid4())
        manifest = {"upload_id": uid, "folder_name": "Синтетический приём",
                    "files": [{"path": "fixture.txt", "size": 4}]}
        assert client.post("/api/calc/folder-uploads", json=manifest).status_code == 200
        assert client.put(f"/api/calc/folder-uploads/{uid}/files/0", content=b"test").status_code == 200
        order = client.post(f"/api/calc/folder-uploads/{uid}/complete").json()["order_id"]
        yield client, config, root, order


def install_native(monkeypatch, *, run_status="running", create_status=201, submit_status=202,
                   timeout=False, durable=True):
    calls = []

    async def native(method, path, *, body=None, extra_headers=None):
        calls.append((method, path, body, extra_headers))
        if path == "/v1/capabilities":
            return 200, {"features": {"runs_idempotency": {"durable": durable}}}
        if path == "/api/sessions":
            return create_status, {"error": {"code": "session_exists"}} if create_status == 409 else {}
        if path == "/v1/runs":
            if timeout:
                raise calc_intake._NativeFailure("gateway_unavailable")
            return submit_status, {"run_id": "run_fixture"}
        return 200, {"status": run_status}

    monkeypatch.setattr(calc_intake, "_native", native)
    return calls


def baseline(root):
    with sqlite3.connect(root / "registry.db") as db:
        orders = db.execute("SELECT order_id,state_json FROM orders ORDER BY order_id").fetchall()
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        jobs = db.execute("SELECT * FROM document_jobs").fetchall() if "document_jobs" in tables else []
        results = db.execute("SELECT * FROM document_results").fetchall() if "document_results" in tables else []
    return orders, jobs, results


def test_native_title_keeps_identity_for_long_duplicate_folder_names():
    left = {"order_name": "я" * 200, "handoff_id": "intake_" + "a" * 40}
    right = {**left, "handoff_id": "intake_" + "b" * 40}
    assert len(calc_intake._session_title(left)) <= 100
    assert calc_intake._session_title(left) != calc_intake._session_title(right)


def test_preparation_answers_http_cli_reload_replay_and_scope(cabinet, monkeypatch):
    client, config, root, order = cabinet
    calls = install_native(monkeypatch, run_status="completed")
    record = client.post(f"/api/calc/orders/{order}/intake-handoff").json()
    hid = record["handoff_id"]
    with sqlite3.connect(root / "registry.db") as db:
        db.execute("UPDATE intake_handoffs SET received_at=1 WHERE handoff_id=?", (hid,))
    assert client.get(f"/api/calc/intake-handoffs/{hid}").json()["status"] == "received"
    path = f"/api/calc/intake-handoffs/{hid}/preparation"
    before = baseline(root)
    view = client.get(path).json()
    assert view["editable"] is True
    assert view["summary"]["engineering_documents"] == 1
    assert view["summary"]["service_files"] == 0
    body = {"snapshot_id": view["snapshot_id"], "expected_revision": 0,
            "request_id": "save-whole", "answers": {"scope": "whole", "scope_note": "",
            "more_documents": "unknown", "quantity_source": "unknown", "notes": ""}}
    saved = client.post(path + "/answers", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["initial_answers"]["answers"]["scope"] == "whole"
    assert saved.json()["initial_answers"]["receipt"]["actor"] == "panel"
    assert client.get(path).json()["initial_answers"] == saved.json()["initial_answers"]
    assert client.post(path + "/answers", json=body).json()["initial_answers"] == saved.json()["initial_answers"]
    assert client.post(path + "/answers", json={**body, "request_id": "new"}).status_code == 409
    assert client.post(path + "/answers", json={**body, "snapshot_id": "snap_other"}).status_code == 409
    assert client.post(path + "/answers", json={**body, "actor": "model"}).status_code == 422
    assert client.post(path + "/answers", content=b"x" * 25000).status_code == 413
    assert baseline(root) == before
    assert len([c for c in calls if c[1] == "/v1/runs"]) == 1
    config["mcp_servers"]["metal_calc"]["env"]["METAL_CALC_ROLE"] = "tech"
    assert client.get(path).status_code == 403
    assert client.post(path + "/answers", json=body).status_code == 403


def test_concurrent_clicks_bind_once_and_do_not_process_sources(cabinet, monkeypatch):
    client, config, root, order = cabinet
    calls = install_native(monkeypatch)
    before = baseline(root)
    path = f"/api/calc/orders/{order}/intake-handoff"
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(pool.map(lambda _: client.post(path), range(3)))
    assert all(r.status_code == 200 for r in replies), [r.text for r in replies]
    records = [r.json() for r in replies]
    assert len({r["handoff_id"] for r in records}) == 1
    assert len({r["session_id"] for r in records}) == 1
    assert len([c for c in calls if c[1] == "/api/sessions"]) == 1
    submits = [c for c in calls if c[1] == "/v1/runs"]
    assert len(submits) == 1
    assert submits[0][3]["Idempotency-Key"] == submits[0][3]["X-Hermes-Tool-Scope"]
    assert "fixture.txt" not in json.dumps(submits[0][2])
    assert baseline(root) == before
    assert client.post(path).json()["run_id"] == "run_fixture"
    assert len([c for c in calls if c[1] == "/v1/runs"]) == 1


@pytest.mark.parametrize("status", [201, 409])
def test_existing_session_reused_and_receipt_waits_for_completed_run(cabinet, monkeypatch, status):
    client, config, root, order = cabinet
    install_native(monkeypatch, create_status=status, run_status="completed")
    record = client.post(f"/api/calc/orders/{order}/intake-handoff").json()
    assert record["initial_run_active"] and record["chat_blocked"]
    with sqlite3.connect(root / "registry.db") as db:
        db.execute("UPDATE intake_handoffs SET received_at=1 WHERE handoff_id=?", (record["handoff_id"],))
    final = client.get(f"/api/calc/intake-handoffs/{record['handoff_id']}").json()
    assert final["status"] == "received"
    assert not final["initial_run_active"] and not final["chat_blocked"]
    assert baseline(root)[1:] == ([], [])


def test_completed_without_tool_receipt_is_not_received(cabinet, monkeypatch):
    client, config, root, order = cabinet
    install_native(monkeypatch, run_status="completed")
    record = client.post(f"/api/calc/orders/{order}/intake-handoff").json()
    final = client.get(f"/api/calc/intake-handoffs/{record['handoff_id']}").json()
    assert final["status"] == "needs_attention"
    assert final["error_code"] == "acknowledgment_missing"
    assert final["received_at"] is None


def test_explicit_retry_before_run_reuses_binding(cabinet, monkeypatch):
    client, config, root, order = cabinet
    calls = install_native(monkeypatch, create_status=503)
    path = f"/api/calc/orders/{order}/intake-handoff"
    first = client.post(path).json()
    assert first["chat_blocked"] and not first["session_created"]
    assert first["error_code"] == "session_create_failed"
    assert not [c for c in calls if c[1] == "/v1/runs"]
    calls = install_native(monkeypatch, create_status=409)
    second = client.post(path).json()
    assert second["session_id"] == first["session_id"]
    assert second["run_id"] == "run_fixture"
    assert len([c for c in calls if c[1] == "/v1/runs"]) == 1


@pytest.mark.parametrize("timeout,status,code,blocked", [
    (True, 202, "dispatch_unknown", True),
    (False, 500, "dispatch_unknown", True),
    (False, 429, "dispatch_failed", False),
])
def test_failed_dispatch_never_blindly_resubmits(cabinet, monkeypatch, timeout, status, code, blocked):
    client, config, root, order = cabinet
    calls = install_native(monkeypatch, timeout=timeout, submit_status=status)
    path = f"/api/calc/orders/{order}/intake-handoff"
    record = client.post(path).json()
    assert record["status"] == "needs_attention"
    assert record["error_code"] == code
    assert record["chat_blocked"] is blocked
    assert client.post(path).json()["session_id"] == record["session_id"]
    assert len([c for c in calls if c[1] == "/v1/runs"]) == 1


@pytest.mark.parametrize("change", [
    "role", "args", "context", "toolset", "durable", "disabled", "include", "exclude", "resources",
    "disabled_toolset",
])
def test_unsafe_runtime_or_missing_durable_store_does_not_create_binding(cabinet, monkeypatch, change):
    client, config, root, order = cabinet
    calls = install_native(monkeypatch, durable=change != "durable")
    mcp = config["mcp_servers"]["metal_calc"]
    if change == "role":
        mcp["env"]["METAL_CALC_ROLE"] = "tech"
    elif change == "args":
        mcp["args"] = []
    elif change == "context":
        mcp["context_arguments"] = {}
    elif change == "toolset":
        config["platform_toolsets"]["api_server"].append("terminal")
    elif change == "disabled":
        mcp["enabled"] = False
    elif change in {"include", "exclude"}:
        mcp["tools"][change] = []
    elif change == "resources":
        mcp["tools"]["resources"] = True
    elif change == "disabled_toolset":
        config["agent"]["disabled_toolsets"].append("mcp-metal_calc")
    response = client.post(f"/api/calc/orders/{order}/intake-handoff")
    assert response.status_code == (403 if change == "role" else 503)
    assert not [c for c in calls if c[0] == "POST"]
    with sqlite3.connect(root / "registry.db") as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='intake_handoffs'").fetchall()


def test_poll_failure_keeps_active_binding_and_stale_claim_is_visible(cabinet, monkeypatch):
    client, config, root, order = cabinet
    install_native(monkeypatch)
    record = client.post(f"/api/calc/orders/{order}/intake-handoff").json()

    async def offline(*args, **kwargs):
        raise calc_intake._NativeFailure("gateway_unavailable")

    monkeypatch.setattr(calc_intake, "_native", offline)
    path = f"/api/calc/intake-handoffs/{record['handoff_id']}"
    assert client.get(path).json()["chat_blocked"]
    with sqlite3.connect(root / "registry.db") as db:
        db.execute("UPDATE intake_handoffs SET dispatch_status='connecting',run_id=NULL,updated_at=1")
    final = client.get(path).json()
    assert final["error_code"] == "dispatch_interrupted"
    assert final["chat_blocked"]


@pytest.mark.asyncio
async def test_native_transport_bounds_and_hides_error_body(monkeypatch):
    import httpx
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * (128 * 1024 + 1)))
    monkeypatch.setenv("API_SERVER_KEY", "synthetic-secret")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))
    with pytest.raises(calc_intake._NativeFailure) as failure:
        await calc_intake._native("GET", "/v1/capabilities")
    assert failure.value.code == "gateway_response_invalid"
    assert "synthetic-secret" not in str(failure.value)
