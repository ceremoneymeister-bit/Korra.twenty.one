"""QA 17-18.10: a cron fire that meets an in-progress MCP reload is moved to
the next tick: no failure_streak, no failure delivery, the job stays due."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("croniter")

import cron.jobs as cron_jobs
import cron.scheduler as sched


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with cron_jobs.use_cron_store(tmp_path):
        cron_jobs.ensure_dirs()
        yield tmp_path


@pytest.fixture
def mcp_reloading():
    from gateway.mcp_reload import try_enter_mcp_reload

    lease = try_enter_mcp_reload()
    assert lease is not None
    yield
    lease.release()


def _make_due(job_id, due_at=None):
    due_at = due_at or (cron_jobs._hermes_now() - timedelta(seconds=30)).isoformat()
    with cron_jobs._jobs_lock():
        jobs = cron_jobs.load_jobs()
        for j in jobs:
            if j["id"] == job_id:
                j["next_run_at"] = due_at
        cron_jobs.save_jobs(jobs)
    return due_at


def _fire(job, tmp_path):
    deliveries = []

    def fake_deliver(jb, content, adapters=None, loop=None, decision_session_id="", failure_notice=False):
        deliveries.append(content)
        return None

    with patch("cron.scheduler._hermes_home", tmp_path), \
         patch("cron.scheduler._resolve_origin", return_value=None), \
         patch.object(sched, "_preflight_check_delivery", lambda j: None), \
         patch.object(sched, "_deliver_result", side_effect=fake_deliver), \
         patch("run_agent.AIAgent", side_effect=AssertionError("agent created")):
        assert sched.run_one_job(job) is True
    return deliveries


def test_recurring_job_is_deferred_to_next_tick(store, mcp_reloading):
    job = cron_jobs.create_job(prompt="отчёт", schedule="* * * * *",
                               name="minutely", deliver="telegram:1")
    due_at = _make_due(
        job["id"], cron_jobs._hermes_now().replace(second=0, microsecond=0).isoformat()
    )
    fired = dict(cron_jobs.get_job(job["id"]))
    cron_jobs.advance_next_runs([job["id"]])  # the tick's at-most-once pre-advance

    deliveries = _fire(fired, store)

    got = cron_jobs.get_job(job["id"])
    assert deliveries == []
    assert got.get("failure_streak", 0) == 0
    assert got.get("last_status") != "error"
    assert got["next_run_at"] <= cron_jobs._hermes_now().isoformat()
    assert got["next_run_at"] >= due_at
    assert job["id"] in [j["id"] for j in cron_jobs.get_due_jobs()]


def test_oneshot_is_deferred_without_spending_its_dispatch(store, mcp_reloading):
    job = cron_jobs.create_job(prompt="x", schedule="in 3h", name="once",
                               deliver="telegram:1")
    _make_due(job["id"])
    due = [j for j in cron_jobs.get_due_jobs() if j["id"] == job["id"]]
    assert due

    deliveries = _fire(dict(due[0]), store)

    got = cron_jobs.get_job(job["id"])
    assert deliveries == []
    assert got.get("failure_streak", 0) == 0
    assert (got.get("repeat") or {}).get("completed", 0) == 0
    assert got.get("run_claim") is None
    assert job["id"] in [j["id"] for j in cron_jobs.get_due_jobs()]
