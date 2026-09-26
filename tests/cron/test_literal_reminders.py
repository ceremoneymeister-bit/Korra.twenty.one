"""Simple reminders use the real job store and skip model/session creation."""

import json
from unittest.mock import patch
from unittest.mock import AsyncMock, MagicMock

import pytest

from cron.jobs import create_job, get_job, job_payload_is_empty, update_job
from cron.scheduler import _preflight_check_provider_key, run_job
from korra_cli.web_models import CronJobCreate
from korra_cli.web_server import _validate_dashboard_cron_effective_job


def test_reminder_survives_reload_and_runs_without_a_model(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    job = create_job(prompt="", reminder="Позвонить поставщику", schedule="in 1h", deliver="local")
    saved = get_job(job["id"])
    assert saved["reminder"] == "Позвонить поставщику"
    assert not job_payload_is_empty(saved)
    assert saved["provider_snapshot"] is None
    with patch("korra_cli.runtime_provider.resolve_runtime_provider", side_effect=AssertionError("model must not run")):
        assert _preflight_check_provider_key(saved, {}) is None
        ok, output, result, error = run_job(saved)
    assert ok and error is None
    assert result == "Позвонить поставщику"
    assert result in output
    assert not (tmp_path / "state.db").exists()
    assert update_job(job["id"], {"reminder": "Позвонить завтра"})["reminder"] == "Позвонить завтра"


@pytest.mark.parametrize("extra", [{"prompt": "compute"}, {"skills": ["web"]}, {"script": "test.py"}, {"no_agent": True}])
def test_reminder_rejects_hidden_execution_modes(extra):
    with pytest.raises(ValueError, match="Напоминание"):
        create_job(**{"prompt": "", "reminder": "Reminder", "schedule": "in 1h", **extra})


def test_clearing_the_only_reminder_does_not_leave_an_empty_job():
    job = create_job(prompt="", reminder="Reminder", schedule="in 1h")
    with pytest.raises(ValueError):
        update_job(job["id"], {"reminder": ""})
    assert get_job(job["id"])["reminder"] == "Reminder"


def test_dashboard_and_agent_tool_accept_literal_reminders(monkeypatch):
    body = CronJobCreate(reminder="Remember", schedule="in 1h")
    _validate_dashboard_cron_effective_job(body.model_dump())
    from cron import scheduler
    from tools.cronjob_tools import cronjob

    monkeypatch.setattr(scheduler, "create_job_with_scheduler_registration", create_job)
    created = json.loads(cronjob(action="create", reminder="Remember", schedule="in 1h", deliver="local"))
    assert created["success"]
    assert get_job(created["job_id"])["reminder"] == "Remember"


@pytest.mark.parametrize("outcome", ["delivered", "failed", "waiting_decision"])
@pytest.mark.parametrize("text", ["SILENT", "Запомнить строку MEDIA:/tmp/private.txt"])
def test_reminder_store_execution_delivery_and_receipt(tmp_path, monkeypatch, outcome, text):
    from cron import jobs, scheduler, executions
    from gateway.config import Platform

    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(scheduler, "_hermes_home", None)
    cfg = MagicMock(platforms={Platform.TELEGRAM: MagicMock(enabled=True)})
    send = AsyncMock(return_value={"success": outcome != "failed", "error": "offline"} if outcome == "failed" else {"success": True})
    queue = MagicMock(return_value={"id": "test-decision"})
    with jobs.use_cron_store(tmp_path):
        job = create_job(prompt="", reminder=text, name="Позвонить", schedule="every 1h",
                         deliver="telegram:42", created_by_owner=outcome != "waiting_decision")
        with (
            patch("korra_cli.runtime_provider.resolve_runtime_provider", side_effect=AssertionError("no model")),
            patch("gateway.config.load_gateway_config", return_value=cfg),
            patch("tools.send_message_tool._send_to_platform", send),
            patch("tools.send_message_tool._queue_outbound_decision", queue),
        ):
            assert scheduler.run_one_job(job)
        receipt = executions.latest_execution(job["id"])
        assert receipt["status"] == "completed"
        assert receipt["delivery_outcome"] == outcome
        assert jobs.get_job(job["id"])["last_status"] == "ok"
    assert not (tmp_path / "state.db").exists()
    if outcome == "waiting_decision":
        send.assert_not_called()
        queue.assert_called_once()
    else:
        queue.assert_not_called()
        send.assert_awaited_once()
        assert text in send.call_args.args[3]
        assert send.call_args.kwargs["media_files"] == []
