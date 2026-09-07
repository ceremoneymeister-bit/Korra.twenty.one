"""Synthetic end-to-end HTTP → CLI → queue → real bounded PDF reader."""
import hashlib
import io
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from fastapi import FastAPI
from pypdf import PdfWriter
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "calculator/metal_calc"))
from metal_calc.document_jobs import DocumentJobs
from metal_calc.document_worker import run_once
from metal_calc.registry import Registry
from korra_cli.web_routers import calc_documents, calc_files, calc_rates


@pytest.fixture
def cabinet(tmp_path, monkeypatch):
    root = tmp_path / "orders"
    root.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "agent-home"))
    binary = tmp_path / "metal-calc-admin"
    binary.write_text(f"#!{sys.executable}\nimport sys\nsys.path.insert(0,{str(ROOT / 'calculator/metal_calc')!r})\n"
                      "from metal_calc.admin import main\nmain()\n")
    binary.chmod(0o700)
    config = {"mcp_servers": {"metal_calc": {"command": str(tmp_path / "metal-calc-mcp"),
              "env": {"METAL_CALC_ORDERS_ROOT": str(root), "METAL_CALC_ROLE": "front"}}}}
    monkeypatch.setattr(calc_rates, "load_config", lambda: config)
    app = FastAPI()
    app.include_router(calc_files.router)
    app.include_router(calc_documents.router)
    with TestClient(app) as client:
        yield client, root, config


def upload(client, name):
    writer = PdfWriter()
    writer.add_blank_page(width=240, height=120)
    writer.add_blank_page(width=300, height=140)
    buf = io.BytesIO()
    writer.write(buf)
    data = buf.getvalue()
    uid = str(uuid4())
    files = [("nested/drawing.pdf", data), ("Thumbs.db", b"unsupported"), ("broken.pdf", b"%PDF-broken")]
    manifest = {"upload_id": uid, "folder_name": name,
                "files": [{"path": p, "size": len(b)} for p, b in files], "directories": ["empty"]}
    assert client.post("/api/calc/folder-uploads", json=manifest).status_code == 200
    base = f"/api/calc/folder-uploads/{uid}"
    for index, (_, value) in enumerate(files):
        assert client.put(f"{base}/files/{index}", content=value).status_code == 200
    return client.post(f"{base}/complete").json()["order_id"], data


def test_job_survives_client_lifetime_and_publishes_exact_source_render(cabinet):
    client, root, _ = cabinet
    order, data = upload(client, "synthetic-34219")
    baseline = Registry(root / "registry.db").get(order)
    route = f"/api/calc/orders/{order}/document-jobs"
    response = client.post(route, json={})
    assert response.status_code == 202, response.text
    job = response.json()
    jid = job["job_id"]
    assert client.post(route, json={}).json()["job_id"] == jid
    assert job["coverage"]["files_total"] == 3
    jobs = DocumentJobs(root)
    # The HTTP start has already returned. Processing and checkpoints are owned
    # by a separate worker, reconstructed from disk between every increment.
    first = run_once(jobs, document_python=sys.executable, max_sources=1)
    assert first["processed"] == 1, first
    after_one = client.get(f"/api/calc/document-jobs/{jid}").json()
    assert after_one["coverage"]["files_accounted"] == 1
    saved_hash = after_one["sources"][0]["result_sha256"]
    assert client.post(f"/api/calc/document-jobs/{jid}/cancel").status_code == 200
    assert client.post(f"/api/calc/document-jobs/{jid}/retry").status_code == 200
    rest = run_once(DocumentJobs(root), document_python=sys.executable)
    assert rest["processed"] == 2 and rest["status"] == "partial", rest
    status = client.get(f"/api/calc/document-jobs/{jid}/result").json()
    assert status["coverage"]["files_pending"] == 0
    assert status["coverage"]["pages_total_known"] == 2
    assert [s["status"] for s in status["sources"]] == ["complete", "unsupported", "failed"]
    assert status["sources"][0]["result_sha256"] == saved_hash
    source = status["sources"][0]
    assert client.get(source["download_url"]).content == data
    evidence = client.get(source["result_url"]).json()
    assert evidence["source"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert evidence["coverage"]["pages_inventoried"] == 2
    assert evidence["use_for_calculation"] is False
    page = client.get(f"/api/calc/document-jobs/{jid}?source_limit=1&source_offset=1").json()
    assert len(page["sources"]) == 1 and page["sources_has_more"]
    assert page["coverage"]["files_total"] == 3
    render = client.post(route, json={"command": "render", "source_id": source["source_id"],
                                     "options": {"page": 2, "dpi": 72, "crop": [10, 20, 110, 80]}})
    assert render.status_code == 202, render.text
    rendered = run_once(DocumentJobs(root), document_python=sys.executable)
    assert rendered["status"] == "completed", rendered
    rid = render.json()["job_id"]
    image_result = client.get(f"/api/calc/document-jobs/{rid}/sources/{source['source_id']}").json()
    assert image_result["page"] == 2
    assert image_result["requested_crop"] == [10, 20, 110, 80]
    png = client.get(image_result["image"]["download_url"])
    assert png.status_code == 200
    assert png.content.startswith(b"\x89PNG")
    assert hashlib.sha256(png.content).hexdigest() == image_result["image"]["sha256"]
    assert str(root) not in json.dumps([status, evidence, image_result])
    assert Registry(root / "registry.db").get(order) == baseline


def test_foreign_sources_and_profiles_and_invalid_recipes_rejected(cabinet):
    client, root, config = cabinet
    a, _ = upload(client, "a")
    b, _ = upload(client, "b")
    ja = client.post(f"/api/calc/orders/{a}/document-jobs", json={}).json()
    jb = client.post(f"/api/calc/orders/{b}/document-jobs", json={}).json()
    foreign = jb["sources"][0]["source_id"]
    assert client.get(f"/api/calc/document-jobs/{ja['job_id']}/sources/{foreign}/download").status_code == 403
    assert client.post(f"/api/calc/orders/{a}/document-jobs", json={"command": "render", "source_id": foreign,
                                                                 "options": {"page": 1}}).status_code == 403
    assert client.post(f"/api/calc/orders/{a}/document-jobs", json={"path": "/etc/passwd"}).status_code == 422
    assert client.post(f"/api/calc/orders/{a}/document-jobs", json={"options": {"timeout": 999}}).status_code == 422
    config["mcp_servers"]["metal_calc"]["env"]["METAL_CALC_ROLE"] = "tech"
    assert client.get(f"/api/calc/document-jobs/{ja['job_id']}").status_code == 403
    assert client.post(f"/api/calc/orders/{a}/document-jobs", json={}).status_code == 403
