"""Exercise the real HTTP → CLI → secure filesystem → canonical registry path."""
import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from starlette.requests import ClientDisconnect
from starlette.testclient import TestClient

from korra_cli.calc_policy import CalculatorBoundaryMiddleware
from korra_cli.web_routers import calc_files, calc_orders, calc_rates


@pytest.fixture
def cabinet(tmp_path, monkeypatch):
    root = tmp_path / "orders"
    root.mkdir()
    binary = tmp_path / "metal-calc-admin"
    package = Path(__file__).resolve().parents[2] / "calculator" / "metal_calc"
    binary.write_text(f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(package)!r})\n"
                      "from metal_calc.admin import main\nmain()\n")
    binary.chmod(0o700)
    config = {"mcp_servers": {"metal_calc": {"command": str(tmp_path / "metal-calc-mcp"),
              "env": {"METAL_CALC_ORDERS_ROOT": str(root), "METAL_CALC_ROLE": "front"}}}}
    monkeypatch.setattr(calc_rates, "load_config", lambda: config)
    monkeypatch.setattr(calc_orders, "load_config", lambda: config)
    async def no_pack():
        return None
    monkeypatch.setattr(calc_orders, "_active_pack", no_pack)
    monkeypatch.setenv("KORRA_UI_MODE", "calc")
    app = FastAPI()
    app.include_router(calc_orders.router)
    app.add_middleware(CalculatorBoundaryMiddleware)
    with TestClient(app) as client:
        yield client, config, root


def request(count=2):
    return {"upload_id": str(uuid4()), "folder_name": "34012 заказ",
            "files": [{"path": f"чертежи/{i}.pdf", "size": 5} for i in range(count)]}


def test_folder_upload_and_orders_share_durable_draft_via_real_cli(cabinet):
    client, config, root = cabinet
    manifest = request(31)
    created = client.post("/api/calc/folder-uploads", json=manifest)
    assert created.status_code == 200, created.text
    base = f"/api/calc/folder-uploads/{manifest['upload_id']}"
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda index: client.put(f"{base}/files/{index}", content=b"12345"), range(31)))
    assert all(response.status_code == 200 for response in responses)
    assert client.get("/api/calc/folders").json()["orders"] == []
    completed = client.post(f"{base}/complete")
    assert completed.status_code == 200, completed.text
    order_id = completed.json()["order_id"]
    assert client.post(f"{base}/complete").json()["order_id"] == order_id
    folder = client.get(f"/api/calc/folders/{order_id}").json()
    assert folder["file_count"] == 31
    assert folder["source_files"][30]["relative_path"] == "чертежи/30.pdf"
    orders = client.get("/api/calc/orders").json()["orders"]
    assert len(orders) == 1 and orders[0]["kind"] == "draft"
    assert orders[0]["order_id"] == order_id
    detail = client.get(f"/api/calc/orders/{order_id}").json()["detail"]
    assert detail["source_files"] == folder["source_files"]
    download = client.get(folder["source_files"][0]["download_url"])
    assert download.status_code == 200
    assert download.content == b"12345"
    assert download.headers["content-disposition"].startswith("attachment;")
    assert str(root) not in json.dumps(folder)
    assert client.get(f"/api/calc/folders/{order_id}/files/-1").status_code == 404


@pytest.mark.parametrize("role", ["tech", "supply", "norm", "qa", "", "unknown"])
def test_non_front_roles_cannot_read_or_mutate_folders(cabinet, role):
    client, config, root = cabinet
    config["mcp_servers"]["metal_calc"]["env"]["METAL_CALC_ROLE"] = role
    assert client.get("/api/calc/folders").status_code == 403
    assert client.post("/api/calc/folder-uploads", json=request()).status_code == 403
    assert client.put(f"/api/calc/folder-uploads/{uuid4()}/files/0", content=b"12345").status_code == 403
    assert not (root / "registry.db").exists()


def test_http_retries_limits_and_conflicts(cabinet, monkeypatch):
    client, config, root = cabinet
    manifest = request(1)
    assert client.post("/api/calc/folder-uploads", json=manifest).status_code == 200
    base = f"/api/calc/folder-uploads/{manifest['upload_id']}"
    assert client.post(f"{base}/complete").status_code == 409
    assert client.put(f"{base}/files/1", content=b"12345").status_code == 422
    assert client.put(f"{base}/files/0", content=b"123").status_code == 422
    assert client.put(f"{base}/files/0", content=b"12345").status_code == 200
    assert client.put(f"{base}/files/0", content=b"54321").status_code == 409
    assert client.post(f"{base}/complete").status_code == 200
    duplicate = client.post("/api/calc/folder-uploads", json=request(1))
    assert duplicate.status_code == 409
    assert isinstance(duplicate.json()["detail"], str)
    monkeypatch.setattr(calc_files, "MAX_FILE_BYTES", 4)
    assert client.put(f"{base}/files/0", content=b"12345").status_code == 413
    malicious = request(1)
    malicious["files"][0]["path"] = "../../config.yaml"
    assert client.post("/api/calc/folder-uploads", json=malicious).status_code == 422


def test_disconnected_upload_cleans_temp_and_can_resume(cabinet):
    client, config, root = cabinet
    manifest = request(1)
    client.post("/api/calc/folder-uploads", json=manifest)
    class InterruptedRequest:
        async def stream(self):
            yield b"123"
            raise ClientDisconnect()
    with pytest.raises(ClientDisconnect):
        asyncio.run(calc_files.folder_upload_file(manifest["upload_id"], 0, InterruptedRequest()))
    assert not list(root.rglob(".tmp-*"))
    base = f"/api/calc/folder-uploads/{manifest['upload_id']}"
    assert client.get(base).json()["received"] == []
    assert client.put(f"{base}/files/0", content=b"12345").status_code == 200
    assert client.post(f"{base}/complete").status_code == 200


def test_cancelled_upload_task_finishes_child_and_can_resume(cabinet):
    client, config, root = cabinet
    manifest = request(1)
    client.post("/api/calc/folder-uploads", json=manifest)
    async def cancel_upload():
        streaming = asyncio.Event()
        class PausedRequest:
            async def stream(self):
                yield b"123"
                streaming.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(calc_files.folder_upload_file(manifest["upload_id"], 0, PausedRequest()))
        await streaming.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(cancel_upload())
    assert not list(root.rglob(".tmp-*"))
    base = f"/api/calc/folder-uploads/{manifest['upload_id']}"
    assert client.get(base).json()["received"] == []
    assert client.put(f"{base}/files/0", content=b"12345").status_code == 200


def test_stream_timeout_keeps_draft_unpublished(cabinet, monkeypatch):
    client, config, root = cabinet
    manifest = request(1)
    client.post("/api/calc/folder-uploads", json=manifest)
    monkeypatch.setattr(calc_files, "STREAM_TIMEOUT_SECONDS", 0.05)
    class PausedRequest:
        async def stream(self):
            yield b"123"
            await asyncio.Event().wait()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as caught:
        asyncio.run(calc_files.folder_upload_file(manifest["upload_id"], 0, PausedRequest()))
    assert caught.value.status_code == 504
    assert not list(root.rglob(".tmp-*"))
    assert client.get("/api/calc/folders").json()["orders"] == []
