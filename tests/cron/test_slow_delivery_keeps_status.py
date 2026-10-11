"""K21-287 (е): доставленный отчёт не записывается «прерванным», если отправка шла долго."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from cron import jobs, scheduler


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with jobs.use_cron_store(tmp_path):
        yield tmp_path


def test_slow_delivery_holding_the_fence_does_not_turn_into_interrupted(home, monkeypatch):
    job = jobs.create_job(prompt="отчёт", schedule="every 1h", name="Отчёт", deliver="telegram:42")
    assert jobs.claim_job_for_fire(job["id"]) is True
    claimed = jobs.get_job(job["id"])

    monkeypatch.setattr(jobs, "_JOBS_LOCK_TIMEOUT_SECONDS", 0.15)
    monkeypatch.setattr(scheduler, "_RUN_CLAIM_HEARTBEAT_SECONDS", 0.02)
    monkeypatch.setattr(scheduler, "_preflight_check_delivery", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(scheduler, "run_job", lambda *a, **k: (True, "вывод", "Готовый отчёт", None))
    sent = []

    def slow_delivery(*args, **kwargs):
        time.sleep(0.7)
        sent.append(args[1])
        return None

    monkeypatch.setattr(scheduler, "_deliver_result", slow_delivery)

    with patch("agent.secret_scope.set_secret_scope", return_value=None), \
         patch("agent.secret_scope.build_profile_secret_scope", return_value=None), \
         patch("agent.secret_scope.reset_secret_scope"):
        scheduler.run_one_job(claimed)

    assert sent == ["Готовый отчёт"]
    saved = jobs.get_job(job["id"])
    assert saved["last_status"] == "ok", saved.get("last_error")
    assert not saved.get("last_error")
