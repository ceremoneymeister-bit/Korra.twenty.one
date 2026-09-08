from concurrent.futures import ThreadPoolExecutor
import sqlite3

from tests.korra_cli.test_web_server_calc_intake import cabinet, install_native, baseline


def setup(client, root, order, monkeypatch):
    calls = install_native(monkeypatch, run_status="completed")
    original = __import__("korra_cli.web_routers.calc_intake", fromlist=["_native"])
    fake = original._native
    async def native(method, path, **kwargs):
        if path == "/p/intake-analysis/v1/capabilities":
            return 200, {"features": {"runs_idempotency": {"durable": True}}}
        return await fake(method, path, **kwargs)
    monkeypatch.setattr(original, "_native", native)
    handoff = client.post(f"/api/calc/orders/{order}/intake-handoff").json()
    hid = handoff["handoff_id"]
    with sqlite3.connect(root / "registry.db") as db:
        db.execute("UPDATE intake_handoffs SET received_at=1 WHERE handoff_id=?", (hid,))
    client.get(f"/api/calc/intake-handoffs/{hid}")
    initial = {"snapshot_id": handoff["snapshot_id"], "expected_revision": 0, "request_id": "answers",
               "answers": {"scope": "whole", "scope_note": "", "more_documents": "no",
                           "quantity_source": "unknown", "notes": ""}}
    assert client.post(f"/api/calc/intake-handoffs/{hid}/preparation/answers", json=initial).status_code == 200
    return hid, calls, initial


def test_plan_does_not_start_work_and_explicit_start_replays(cabinet, monkeypatch):
    client, config, root, order = cabinet
    hid, calls, initial = setup(client, root, order, monkeypatch)
    path = f"/api/calc/intake-handoffs/{hid}/analysis"
    before = baseline(root)
    assert client.get(path).json() == {"plan": None, "job": None}
    plan_response = client.post(path + "/plan")
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert plan["can_start"] is True
    assert client.get(path).json()["job"] is None
    assert baseline(root) == before
    body = {"plan_id": plan["plan_id"], "request_id": "explicit-start"}
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(pool.map(lambda _: client.post(path + "/start", json=body), range(3)))
    assert all(r.status_code == 200 for r in replies), [r.text for r in replies]
    assert len({r.json()["job_id"] for r in replies}) == 1
    job = replies[0].json()
    assert job["status"] == "queued"
    assert job["human_approved"] is False
    assert len([c for c in calls if c[1] == "/v1/runs"]) == 1  # Only initial receipt.
    assert client.post(path + "/start", json={**body, "actor": "model"}).status_code == 422
    assert client.post(path + "/cancel", json={"job_id": "analysis_other"}).status_code in {403,404}
    assert client.post(path + "/cancel", json={"job_id": job["job_id"]}).json()["status"] == "cancelled"
    assert client.get(path).json()["job"]["status"] == "cancelled"
    config["mcp_servers"]["metal_calc"]["env"]["METAL_CALC_ROLE"] = "tech"
    assert client.get(path).status_code == 403
    assert client.post(path + "/plan").status_code == 403


def test_changed_answers_refuse_old_plan(cabinet, monkeypatch):
    client, config, root, order = cabinet
    hid, calls, initial = setup(client, root, order, monkeypatch)
    path = f"/api/calc/intake-handoffs/{hid}/analysis"
    plan = client.post(path + "/plan").json()
    changed = {**initial, "expected_revision": 1, "request_id": "new-answers",
               "answers": {**initial["answers"], "more_documents": "yes"}}
    assert client.post(f"/api/calc/intake-handoffs/{hid}/preparation/answers", json=changed).status_code == 200
    response = client.post(path + "/start", json={"plan_id": plan["plan_id"], "request_id": "start"})
    assert response.status_code == 409, response.text
    assert client.get(path).json()["job"] is None
