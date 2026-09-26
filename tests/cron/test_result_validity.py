import json
import sqlite3
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cron import jobs, scheduler, executions, result_validity
from tools import effect_decisions as decisions
from korra_time import now


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with jobs.use_cron_store(tmp_path):
        yield tmp_path


def test_policy_roundtrip_and_invalid_update_keeps_old_job(home):
    job = jobs.create_job(prompt="", reminder="Звонок", schedule="every 1h",
                          delivery_ttl_seconds=3600, pending_result_policy="latest")
    assert jobs.get_job(job["id"])["delivery_ttl_seconds"] == 3600
    for change in ({"delivery_ttl_seconds": -1}, {"delivery_ttl_seconds": True}, {"pending_result_policy": "guess"}):
        with pytest.raises(ValueError):
            jobs.update_job(job["id"], change)
    assert jobs.get_job(job["id"])["pending_result_policy"] == "latest"
    assert jobs.update_job(job["id"], {"delivery_ttl_seconds": None})["delivery_ttl_seconds"] is None


def test_expired_result_never_resolves_or_calls_transport(home):
    job = {"id": "old", "_result_started_at": 1, "delivery_ttl_seconds": 1}
    with patch.object(scheduler, "_resolve_delivery_targets", side_effect=AssertionError("No transport")):
        assert scheduler._deliver_result(job, "Отчёт") == result_validity.EXPIRED_DELIVERY


def test_queued_cron_receipt_tracks_decision_without_rewriting_audit(home, monkeypatch):
    from gateway.config import Platform
    monkeypatch.setattr(scheduler, "_hermes_home", None)
    cfg = MagicMock(platforms={Platform.TELEGRAM: MagicMock(enabled=True, token="fixture")})
    job = jobs.create_job(prompt="", reminder="Звонок", schedule="every 1h", deliver="telegram:42",
                          created_by_owner=False, delivery_ttl_seconds=3600, pending_result_policy="latest")
    send = AsyncMock(side_effect=AssertionError("Must wait"))
    with patch("gateway.config.load_gateway_config", return_value=cfg), patch("tools.send_message_tool._send_to_platform", send), patch("tools.approval.notify_gateway_request"):
        assert scheduler.run_one_job(job)
    receipt = executions.latest_execution(job["id"])
    assert receipt["delivery_outcome"] == "waiting_decision"
    assert receipt["result_text"] == "Звонок"
    items = decisions.list_profile_decisions()
    assert len(items) == 1
    item = items[0]
    assert item["payload"]["validity"]["execution_id"] == receipt["id"]
    assert item["payload"]["validity"]["expires_at"] > item["created_at"]
    decisions.decide(item["id"], choice="deny", source_session_id=item["source_session_id"])
    assert executions.latest_execution(job["id"])["delivery_outcome"] == "denied"
    assert executions.latest_executions([job["id"]])[job["id"]]["delivery_outcome"] == "denied"
    with sqlite3.connect(home / "cron" / "executions.db") as conn:
        assert conn.execute("SELECT delivery_outcome FROM executions WHERE id=?", (receipt["id"],)).fetchone()[0] == "waiting_decision"
    send.assert_not_called()


def test_missed_reminder_remains_visible_and_not_resent_after_restart(home, monkeypatch):
    job = jobs.create_job(prompt="", reminder="Позвонить", schedule="in 1h", deliver="local")
    future = now() + timedelta(hours=2)
    monkeypatch.setattr(jobs, "_hermes_now", lambda: future)
    assert jobs.get_due_jobs() == []
    saved = jobs.get_job(job["id"])
    assert saved and saved["last_status"] == "missed" and saved["enabled"] is False
    receipt = executions.latest_execution(job["id"])
    assert receipt["source"] == "missed"
    assert receipt["delivery_outcome"] == "expired"
    assert jobs.get_due_jobs() == []
    assert len(executions.list_executions(job_id=job["id"])) == 1


def test_reminder_cannot_turn_into_a_model_instruction(home):
    with pytest.raises(ValueError, match="адресовано человеку"):
        jobs.create_job(prompt="", reminder="Текст", schedule="in 1h", deliver="bot-chat")
    job = jobs.create_job(prompt="", reminder="Текст", schedule="in 1h", deliver="local")
    with pytest.raises(ValueError, match="адресовано человеку"):
        jobs.update_job(job["id"], {"deliver": "bot-chat:other"})
    with patch.object(scheduler, "_deliver_to_bot_chat", side_effect=AssertionError("No model")):
        error = scheduler._deliver_result({**job, "deliver": "bot-chat"}, "Текст")
    assert "адресовано человеку" in error


def test_bare_bot_chat_inherits_context_profile_not_process_root(home, monkeypatch):
    from korra_constants import set_hermes_home_override, reset_hermes_home_override
    captured = {}
    def run(*args, **kwargs):
        captured.update(kwargs["env"])
        return MagicMock(returncode=0, stdout="", stderr="")
    token = set_hermes_home_override(str(home / "profiles" / "rop"))
    try:
        with patch("shutil.which", return_value="/usr/bin/hermes"), patch("subprocess.run", side_effect=run):
            assert scheduler._deliver_to_bot_chat({"id": "job"}, "Report", "") is None
    finally:
        reset_hermes_home_override(token)
    assert captured["KORRA_HOME"] == str(home / "profiles" / "rop")
