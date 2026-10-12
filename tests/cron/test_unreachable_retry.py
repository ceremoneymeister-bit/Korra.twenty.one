"""K21-287 а: сбой сети до первого вызова модели не съедает регулярное задание."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("croniter")

import cron.jobs as cron_jobs
import cron.scheduler as sched
from cron import unreachable_retry as ur


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(ur, "retry_enabled", lambda: True)
    with cron_jobs.use_cron_store(tmp_path):
        cron_jobs.ensure_dirs()
        yield tmp_path


def _daily(**kw):
    # Следующий запуск через ~12 часов, чтобы лесенка 5/15/30 минут всегда была раньше.
    hour = (cron_jobs._hermes_now().hour + 12) % 24
    return cron_jobs.create_job(prompt="отчёт", schedule=f"0 {hour} * * *", name="daily", **kw)


def _in_minutes(iso, base=None):
    base = base or cron_jobs._hermes_now()
    return (datetime.fromisoformat(iso) - base).total_seconds() / 60


def test_ladder_5_15_30_then_natural_schedule(store):
    job = _daily()
    natural = cron_jobs.get_job(job["id"])["next_run_at"]
    steps = []
    for _ in range(3):
        assert cron_jobs.mark_job_run(job["id"], False, "net", model_unreachable=True)
        got = cron_jobs.get_job(job["id"])
        steps.append(round(_in_minutes(got["next_run_at"])))
        assert got["unreachable_retry"]["attempt"] == len(steps)
    assert steps == [5, 15, 30]

    assert cron_jobs.mark_job_run(job["id"], False, "net", model_unreachable=True)
    final = cron_jobs.get_job(job["id"])
    assert "unreachable_retry" not in final
    assert final["next_run_at"] == natural or _in_minutes(final["next_run_at"]) > 60


def test_retry_attempt_costs_no_repeat_slot_and_no_streak(store):
    job = _daily(repeat=3)
    assert cron_jobs.mark_job_run(job["id"], False, "net", model_unreachable=True)
    got = cron_jobs.get_job(job["id"])
    assert got["repeat"]["completed"] == 0
    assert got.get("failure_streak", 0) == 0

    assert cron_jobs.mark_job_run(job["id"], True)
    got = cron_jobs.get_job(job["id"])
    assert got["repeat"]["completed"] == 1
    assert "unreachable_retry" not in got


def test_model_reached_failure_does_not_retry(store):
    job = _daily()
    cron_jobs.mark_job_run(job["id"], False, "boom")
    got = cron_jobs.get_job(job["id"])
    assert "unreachable_retry" not in got
    assert got["failure_streak"] == 1


def test_oneshot_manual_and_disabled_do_not_retry(store, monkeypatch):
    once = cron_jobs.create_job(prompt="x", schedule="in 3h", name="once")
    cron_jobs.mark_job_run(once["id"], False, "net", model_unreachable=True)
    assert "unreachable_retry" not in cron_jobs.get_job(once["id"])

    manual = _daily()
    cron_jobs.update_job(manual["id"], {"manual_run_at": cron_jobs._hermes_now().isoformat()})
    cron_jobs.mark_job_run(manual["id"], False, "net", model_unreachable=True)
    assert "unreachable_retry" not in cron_jobs.get_job(manual["id"])

    monkeypatch.setattr(ur, "retry_enabled", lambda: False)
    plain = _daily()
    cron_jobs.mark_job_run(plain["id"], False, "net", model_unreachable=True)
    assert "unreachable_retry" not in cron_jobs.get_job(plain["id"])


def test_frequent_interval_job_is_its_own_retry(store):
    job = cron_jobs.create_job(prompt="x", schedule="every 5m", name="fast")
    cron_jobs.mark_job_run(job["id"], False, "net", model_unreachable=True)
    assert "unreachable_retry" not in cron_jobs.get_job(job["id"])


def test_off_lattice_retry_instant_fires_instead_of_being_reanchored(store):
    job = _daily()
    cron_jobs.mark_job_run(job["id"], False, "net", model_unreachable=True)
    parked = cron_jobs.get_job(job["id"])["next_run_at"]

    future = datetime.fromisoformat(parked) + timedelta(seconds=30)
    with patch.object(cron_jobs, "_hermes_now", return_value=future):
        due = cron_jobs.get_due_jobs()
    assert [j["id"] for j in due] == [job["id"]]


def test_edited_expression_does_not_inherit_the_exemption(store):
    job = _daily()
    cron_jobs.mark_job_run(job["id"], False, "net", model_unreachable=True)
    parked = cron_jobs.get_job(job["id"])["next_run_at"]
    cron_jobs.update_job(job["id"], {"schedule": "30 7 * * *"})
    raw = cron_jobs.get_job(job["id"])
    raw["next_run_at"] = parked
    assert ur.is_retry_fire(raw, parked) is False


# ── через run_one_job ──────────────────────────────────────────────────────


def _fire(job, tmp_path, *, resolve_error=None, agent_error=None):
    deliveries = []

    def fake_deliver(jb, content, adapters=None, loop=None, decision_session_id="", failure_notice=False):
        deliveries.append(content)
        return None

    runtime = {
        "api_key": "k", "base_url": "https://example.invalid/v1",
        "provider": "openrouter", "api_mode": "chat_completions",
    }
    with patch("cron.scheduler._hermes_home", tmp_path), \
         patch("cron.scheduler._resolve_origin", return_value=None), \
         patch("korra_cli.env_loader.load_hermes_dotenv"), \
         patch("korra_cli.env_loader.reset_secret_source_cache"), \
         patch("korra_state.SessionDB", return_value=MagicMock()), \
         patch("tools.mcp_tool.discover_mcp_tools", return_value=[]), \
         patch("korra_cli.runtime_provider.resolve_runtime_provider",
               side_effect=resolve_error, return_value=runtime), \
         patch.object(sched, "_preflight_check_delivery", lambda j: None), \
         patch.object(sched, "_deliver_result", side_effect=fake_deliver), \
         patch("run_agent.AIAgent") as agent_cls:
        agent = MagicMock()
        agent.session_api_calls = 0
        agent.run_conversation.side_effect = agent_error
        agent_cls.return_value = agent
        sched.run_one_job(dict(cron_jobs.get_job(job["id"])))
    return deliveries


def test_unreachable_resolve_is_retried_silently_then_reported_once(store):
    import httpx

    job = _daily(deliver="telegram:1")
    for expected in (1, 2, 3):
        notices = _fire(job, store, resolve_error=httpx.ConnectError("[Errno 8] nodename nor servname"))
        assert notices == [], "interim failure must not be reported"
        assert cron_jobs.get_job(job["id"])["unreachable_retry"]["attempt"] == expected

    notices = _fire(job, store, resolve_error=httpx.ConnectError("[Errno 8] nodename nor servname"))
    assert len(notices) == 1, "the ladder is exhausted: now the owner is told"
    assert "unreachable_retry" not in cron_jobs.get_job(job["id"])


def test_agent_connection_error_without_api_calls_is_retried(store):
    job = _daily(deliver="telegram:1")
    notices = _fire(job, store, agent_error=RuntimeError("Connection error."))
    assert notices == []
    assert cron_jobs.get_job(job["id"])["unreachable_retry"]["attempt"] == 1


def test_failure_after_model_replied_is_not_retried(store):
    job = _daily(deliver="telegram:1")
    notices = _fire(job, store, agent_error=RuntimeError("tool exploded"))
    assert len(notices) == 1
    assert "unreachable_retry" not in cron_jobs.get_job(job["id"])


def test_agent_with_completed_calls_is_not_classified_unreachable():
    agent = MagicMock()
    agent.session_api_calls = 2
    assert ur.is_model_unreachable_failure(RuntimeError("Connection error."), agent) is False


def test_job_with_script_is_not_retried_and_owner_is_told(store):
    import httpx

    marker = store / "side_effect.txt"
    (store / "scripts").mkdir()
    script = store / "scripts" / "action.sh"
    script.write_text(f"#!/bin/sh\nprintf 'action\\n' >> '{marker}'\nprintf 'report this action\\n'\n")
    job = _daily(script=str(script), deliver="telegram:1")

    notices = _fire(job, store, resolve_error=httpx.ConnectError("Connection error."))

    assert len(notices) == 1, "the script already ran: the failure is reported, not held for a re-run"
    got = cron_jobs.get_job(job["id"])
    assert "unreachable_retry" not in got
    assert got["next_run_at"] == job["next_run_at"]
    assert marker.read_text().splitlines() == ["action"]


@pytest.mark.parametrize("key,value", [("monitor_script", "m.sh"), ("monitor_url", "https://example.invalid/x")])
def test_job_with_monitor_source_is_not_retried(store, key, value):
    job = _daily()
    cron_jobs.update_job(job["id"], {key: value})
    cron_jobs.mark_job_run(job["id"], False, "net", model_unreachable=True)
    assert "unreachable_retry" not in cron_jobs.get_job(job["id"])
