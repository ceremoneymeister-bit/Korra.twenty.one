"""Independent recovery review: typed boundaries and real dashboard auth gates."""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_document_jobs_adversarial import (
    admitted, harness, observation, render_job, rendered_observation,
)
from metal_calc.errors import InvalidState, NotFound


@pytest.mark.parametrize("field", ["source", "verification"])
@pytest.mark.parametrize("value", ["unexpected", ["unexpected"], 42])
def test_typed_objects_rejected_as_domain_errors_before_publication(harness, field, value):
    _, jobs, _ = harness
    _, job, claim = admitted(harness)
    source = job["sources"][0]
    result = observation(source)
    result[field] = value
    with pytest.raises(InvalidState):
        jobs.publish(claim, source["source_id"], result)
    with pytest.raises(NotFound):
        jobs.result(job["job_id"], source["source_id"])
    assert jobs.get(job["job_id"])["coverage"]["files_accounted"] == 0


@pytest.mark.parametrize("field", ["pages_inventoried", "pages_accounted"])
def test_complete_pdf_inventory_requires_all_known_pages(harness, field):
    _, jobs, _ = harness
    _, job, claim = admitted(harness)
    source = job["sources"][0]
    result = observation(source)
    result["coverage"][field] = 0
    with pytest.raises(InvalidState):
        jobs.publish(claim, source["source_id"], result)
    assert jobs.get(job["job_id"])["coverage"]["files_accounted"] == 0


@pytest.mark.parametrize("field,value", [("bytes", 1), ("width", 2), ("height", 0)])
def test_image_metadata_must_describe_saved_png(harness, field, value):
    _, jobs, _ = harness
    job, source, claim = render_job(harness)
    result, assets = rendered_observation(source)
    result["image"][field] = value
    with pytest.raises(InvalidState):
        jobs.publish(claim, source["source_id"], result, assets)
    with pytest.raises(NotFound):
        jobs.image(job["job_id"], source["source_id"])


def test_uncropped_recipe_rejects_result_claiming_a_crop(harness):
    _, jobs, _ = harness
    job, source, claim = render_job(harness)
    result, assets = rendered_observation(source)
    result["requested_crop"] = [0, 0, 1, 1]
    with pytest.raises(InvalidState):
        jobs.publish(claim, source["source_id"], result, assets)
    assert jobs.get(job["job_id"])["coverage"]["files_accounted"] == 0


DOCUMENT_REQUESTS = [
    ("POST", "/api/calc/orders/order1/document-jobs"),
    ("GET", "/api/calc/orders/order1/document-jobs"),
    ("GET", "/api/calc/document-jobs/doc1"),
    ("GET", "/api/calc/document-jobs/doc1/result"),
    ("POST", "/api/calc/document-jobs/doc1/cancel"),
    ("POST", "/api/calc/document-jobs/doc1/retry"),
    ("GET", "/api/calc/document-jobs/doc1/sources/src1"),
    ("GET", "/api/calc/document-jobs/doc1/sources/src1/download"),
    ("GET", "/api/calc/document-jobs/doc1/sources/src1/image"),
]


@pytest.mark.parametrize("gated", [False, True])
def test_all_document_routes_require_dashboard_auth_before_cli(monkeypatch, gated):
    from fastapi.testclient import TestClient
    from korra_cli import web_server
    from korra_cli.web_routers import calc_documents

    monkeypatch.setattr(web_server.app.state, "bound_host", "127.0.0.1", raising=False)
    monkeypatch.setattr(web_server.app.state, "bound_port", 8080, raising=False)
    monkeypatch.setattr(web_server.app.state, "auth_required", gated, raising=False)
    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "recovery-review-session-token")
    invoked = []

    async def unexpected_admin(*args, **kwargs):
        invoked.append(args)
        raise AssertionError("Unauthenticated document request reached the CLI")

    monkeypatch.setattr(calc_documents, "_run_admin", unexpected_admin)
    client = TestClient(web_server.app, base_url="http://127.0.0.1:8080")
    try:
        for method, path in DOCUMENT_REQUESTS:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code == 401, (method, path, response.status_code, response.text)
        assert invoked == []
    finally:
        client.close()


@pytest.mark.parametrize("role", ["tech", "supply", "norm", ""])
def test_authenticated_nonfront_cannot_reach_any_document_cli(monkeypatch, role):
    from fastapi.testclient import TestClient
    from korra_cli import web_server
    from korra_cli.web_routers import calc_documents, calc_rates

    monkeypatch.setattr(web_server.app.state, "bound_host", "127.0.0.1", raising=False)
    monkeypatch.setattr(web_server.app.state, "bound_port", 8080, raising=False)
    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "recovery-review-session-token")
    config = {"mcp_servers": {"metal_calc": {"env": {"METAL_CALC_ROLE": role}}}}
    monkeypatch.setattr(calc_rates, "load_config", lambda: config)
    invoked = []

    async def admin(args, **kwargs):
        invoked.append(args)
        return {"jobs": []}

    monkeypatch.setattr(calc_documents, "_run_admin", admin)
    client = TestClient(web_server.app, base_url="http://127.0.0.1:8080",
                        headers={"Authorization": "Bearer recovery-review-session-token"})
    try:
        for method, path in DOCUMENT_REQUESTS:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code == 403, (method, path, response.status_code, response.text)
        assert invoked == []
        config["mcp_servers"]["metal_calc"]["env"]["METAL_CALC_ROLE"] = "front"
        allowed = client.get("/api/calc/orders/order1/document-jobs")
        assert allowed.status_code == 200 and allowed.json() == {"jobs": []}
        assert invoked == [["document-jobs-list", "--order-id", "order1"]]
    finally:
        client.close()


@pytest.mark.parametrize("role", ["tech", "supply", "norm", ""])
def test_cli_itself_denies_nonfront_before_storage_access(tmp_path, monkeypatch, role):
    from metal_calc import document_admin

    orders = tmp_path / "must-not-be-created"
    monkeypatch.setenv("METAL_CALC_ROLE", role)
    monkeypatch.setenv("METAL_CALC_ORDERS_ROOT", str(orders))
    commands = ["document-job-start", "document-jobs-list", "document-job-status",
                "document-job-cancel", "document-job-retry", "document-source-result",
                "document-source-info", "document-source-read", "document-image-info",
                "document-image-read"]
    for command in commands:
        replies = []
        args = SimpleNamespace(command=command, order_id="order1", job_id="doc1", source_id="src1")
        with pytest.raises(SystemExit) as exc:
            document_admin.run(args, lambda: {}, replies.append)
        assert exc.value.code == 2
        assert len(replies) == 1 and replies[0]["error"]["code"] == "OrderScopeDenied"
    assert not orders.exists()
