"""Legacy Telegram cron replies keep their original reach and durable queue."""
from copy import deepcopy
import sqlite3
from unittest.mock import AsyncMock

import pytest

from cron import executions, jobs, scheduler
from gateway.config import GatewayConfig, Platform, PlatformConfig
from tools import effect_decisions


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(scheduler, "_hermes_home", None)
    (tmp_path / "config.yaml").write_text("cron:\n  wrap_response: false\n")
    config = GatewayConfig(platforms={
        Platform.TELEGRAM: PlatformConfig(enabled=True, token="fixture-token"),
    })
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: config)
    send = AsyncMock(return_value={"success": True, "message_id": "fixture-message"})
    monkeypatch.setattr("tools.send_message_tool._send_to_platform", send)
    with jobs.use_cron_store(tmp_path):
        yield tmp_path, send


def legacy_job(**changes):
    return {
        "id": "daily", "name": "Daily report", "deliver": "origin",
        "origin": {"platform": "telegram", "chat_id": "1220", "thread_id": None},
        **changes,
    }


def deliver(job, content="Saved report", *, run_id="run-new", **kwargs):
    session = scheduler._delivery_decision_session(job, run_id)
    # Compatibility for a reply must never promote the job to an owner's job.
    assert session == f"cron:{job['id']}:{run_id}"
    return scheduler._deliver_result(job, content, decision_session_id=session, **kwargs)


@pytest.mark.parametrize("chat_id", ["1220", 1220])
@pytest.mark.parametrize("target", ["origin", "telegram:1220"])
def test_legacy_reply_returns_only_to_its_original_private_chat(delivery, chat_id, target):
    _, send = delivery
    job = legacy_job(deliver=target)
    job["origin"]["chat_id"] = chat_id
    before = deepcopy(job)
    assert deliver(job) is None
    send.assert_awaited_once()
    assert str(send.await_args.args[2]) == str(chat_id)
    assert send.await_args.args[3] == "Saved report"
    assert effect_decisions.list_profile_decisions() == []
    assert job == before


@pytest.mark.parametrize("origin_change,job_change,target", [
    ({}, {}, "telegram:999"),
    ({"chat_id": "-100500"}, {}, "origin"),
    ({"chat_id": "-100500"}, {}, "telegram:1220"),
    ({"chat_id": None}, {}, "telegram:1220"),
    ({"chat_id": "@someone"}, {}, "origin"),
    ({"chat_id": "0"}, {}, "origin"),
    ({"platform": "discord"}, {}, "telegram:1220"),
    ({"chat_type": "group"}, {}, "origin"),
    ({"chat_type": "channel"}, {}, "origin"),
    ({"user_id": "999"}, {}, "origin"),
    ({"user_id": ""}, {}, "origin"),
    ({"user_id": None}, {}, "origin"),
    ({"owner": False}, {}, "origin"),
    ({"owner": False}, {"created_by_owner": True}, "origin"),
    ({"owner": True}, {"created_by_owner": False}, "origin"),
    ({"owner": None}, {}, "origin"),
    ({}, {"created_by_owner": False}, "origin"),
    ({}, {"created_by_owner": None}, "origin"),
])
def test_legacy_fallback_never_overrides_identity_or_expands_reach(
    delivery, origin_change, job_change, target,
):
    _, send = delivery
    job = legacy_job(deliver=target, **job_change)
    job["origin"].update(origin_change)
    before = deepcopy(job)
    result = deliver(job)
    assert result.startswith("waiting_decision:")
    send.assert_not_awaited()
    pending, = effect_decisions.list_profile_decisions()
    assert result == f"waiting_decision:{pending['id']}"
    assert pending["status"] == "pending"
    assert pending["payload"]["message"] == "Saved report"
    assert job == before


@pytest.mark.parametrize("target,waits", [("origin", False), ("telegram:999", True)])
def test_new_non_owner_job_keeps_creator_identity_after_reload(delivery, target, waits):
    _, send = delivery
    job = jobs.create_job(
        prompt="", reminder="Saved report", schedule="every 1h", deliver=target,
        origin={"platform": "telegram", "chat_id": "1220", "user_id": "1220", "owner": False},
        created_by_owner=False,
    )
    reloaded = jobs.get_job(job["id"])
    result = deliver(reloaded)
    assert bool(result and result.startswith("waiting_decision:")) is waits
    assert send.await_count == (0 if waits else 1)
    assert reloaded["created_by_owner"] is False
    assert jobs.get_job(job["id"])["origin"]["owner"] is False


def test_legacy_reply_does_not_supersede_or_execute_old_pending_results(delivery):
    home, send = delivery
    job = legacy_job(pending_result_policy="latest")
    stamped = deepcopy(job)
    stamped["origin"]["owner"] = False
    assert deliver(stamped, "Historical report", run_id="run-old").startswith("waiting_decision:")
    db_path = home / "effect_decisions.sqlite3"
    with sqlite3.connect(db_path) as db:
        before = db.execute("SELECT * FROM effect_decisions ORDER BY id").fetchall()
    assert deliver(job) is None
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT * FROM effect_decisions ORDER BY id").fetchall() == before
    pending, = effect_decisions.list_profile_decisions()
    assert pending["status"] == "pending"
    assert pending["payload"]["message"] == "Historical report"
    send.assert_awaited_once()
    assert send.await_args.args[3] == "Saved report"


def test_legacy_reply_in_fanout_does_not_authorize_another_recipient(delivery):
    assert deliver(legacy_job(deliver="origin,telegram:999")).startswith("waiting_decision:")
    pending, = effect_decisions.list_profile_decisions()
    assert pending["payload"]["chat_id"] == "999"
    delivery[1].assert_awaited_once()
    assert delivery[1].await_args.args[2] == "1220"


def test_legacy_origin_is_not_authority_for_bot_chat(delivery, monkeypatch):
    bot_send = AsyncMock()
    monkeypatch.setattr(scheduler, "_deliver_to_bot_chat", bot_send)
    assert "только задания владельца" in deliver(legacy_job(deliver="bot-chat"))
    bot_send.assert_not_called()
    delivery[1].assert_not_awaited()


@pytest.mark.parametrize("target,allowed", [("origin", True), ("telegram:999", False)])
def test_legacy_failure_notice_stays_in_original_private_chat(delivery, target, allowed):
    result = deliver(legacy_job(deliver=target), failure_notice=True)
    assert (result is None) is allowed
    assert delivery[1].await_count == int(allowed)
    assert effect_decisions.list_profile_decisions() == []


def test_legacy_script_run_saves_result_and_delivers_without_changing_origin(delivery):
    home, send = delivery
    (home / "scripts").mkdir()
    script = home / "scripts" / "report.py"
    script.write_text("print('Saved report')\n")
    job = jobs.create_job(
        prompt="", schedule="every 1h", script=str(script), no_agent=True,
        deliver="origin", origin=legacy_job()["origin"],
    )
    # Reproduce the on-disk form before creator verdicts existed.
    job.pop("created_by_owner")
    jobs.save_jobs([job])
    assert scheduler.run_one_job(jobs.get_job(job["id"]))
    receipt = executions.latest_execution(job["id"])
    assert receipt["status"] == "completed", receipt
    assert receipt["delivery_outcome"] == "delivered"
    output, = (home / "cron" / "output" / job["id"]).glob("*.md")
    assert "Saved report" in output.read_text()
    assert "Saved report" in send.await_args.args[3]
    send.assert_awaited_once()
    saved = jobs.get_job(job["id"])
    assert saved["origin"] == job["origin"]
    assert "created_by_owner" not in saved
    assert effect_decisions.list_profile_decisions() == []
    from cron.result_links import lookup
    from tools.send_message_tool import _configured_account_identity

    linked = lookup(
        account=_configured_account_identity("telegram", PlatformConfig(token="fixture-token")),
        chat_id="1220", thread_id=None, message_id="fixture-message",
    )
    assert linked["execution_id"] == receipt["id"]
    assert linked["text"] == "Saved report"


@pytest.mark.asyncio
async def test_old_cron_queue_is_visible_in_profile_center_without_live_prompt(delivery):
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from gateway.platforms.api_server import APIServerAdapter
    from tools import approval

    job = legacy_job()
    job["origin"]["owner"] = False
    result = deliver(job)
    pending, = effect_decisions.list_profile_decisions()
    assert result == f"waiting_decision:{pending['id']}"
    # Telegram's live /approve queue cannot resolve a durable cron session.
    assert approval.list_gateway_approvals("telegram:1220") == []
    assert approval.list_gateway_approvals(pending["source_session_id"]) == []
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    app = web.Application()
    app.router.add_get("/api/effect-decisions", adapter._handle_effect_decisions)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/effect-decisions")
        assert response.status == 200
        body = await response.json()
    item, = body["data"]
    assert item["request_id"] == pending["id"]
    assert item["source_session_id"] == pending["source_session_id"]
    assert item["effect_status"] == "pending"
    assert "Saved report" in item["command"]
    assert effect_decisions.get_decision(pending["id"]) == pending
