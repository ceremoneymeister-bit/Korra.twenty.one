"""K21-287 (д): пропущенное при рестарте разовое задание не исчезает молча."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest

from cron import executions, jobs, scheduler
from korra_time import now


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    jobs.pop_missed_oneshot_notices()
    with jobs.use_cron_store(tmp_path):
        yield tmp_path
    jobs.pop_missed_oneshot_notices()


def _miss(monkeypatch, hours=3):
    future = now() + timedelta(hours=hours)
    monkeypatch.setattr(jobs, "_hermes_now", lambda: future)


def test_missed_agent_oneshot_leaves_skipped_record(home, monkeypatch):
    job = jobs.create_job(prompt="Собери отчёт", schedule="in 5m", name="Отчёт", deliver="local")
    _miss(monkeypatch)

    assert jobs.get_due_jobs() == []

    assert jobs.get_job(job["id"]) is None
    receipt = executions.latest_execution(job["id"])
    assert receipt["source"] == "missed"
    assert receipt["delivery_outcome"] == "skipped"
    assert "не работала" in receipt["error"]


def test_missed_oneshot_queues_exactly_one_owner_notice(home, monkeypatch):
    job = jobs.create_job(prompt="Собери отчёт", schedule="in 5m", name="Отчёт", deliver="local")
    jobs.create_job(prompt="", reminder="Позвонить", schedule="in 5m", name="Звонок", deliver="local")
    _miss(monkeypatch)

    assert jobs.get_due_jobs() == []
    assert jobs.get_due_jobs() == []

    notices = jobs.pop_missed_oneshot_notices()
    assert len(notices) == 2
    texts = sorted(n["text"] for n in notices)
    assert "Напоминание «Звонок» не сработало вовремя" in texts[0]
    assert "Разовое задание «Отчёт» не сработало вовремя" in texts[1]
    assert jobs.pop_missed_oneshot_notices() == []
    assert job["id"] in {n["job"]["id"] for n in notices}


def test_tick_tells_owner_once_without_confirmation(home, monkeypatch):
    jobs.create_job(prompt="Собери отчёт", schedule="in 5m", name="Отчёт", deliver="local")
    _miss(monkeypatch)
    jobs.get_due_jobs()

    sent = []
    with patch.object(scheduler, "_deliver_result", side_effect=lambda job, text, **kw: sent.append((job, text, kw)) or None):
        scheduler._notify_missed_oneshots(None, None)
        scheduler._notify_missed_oneshots(None, None)

    assert len(sent) == 1
    job, text, kw = sent[0]
    assert "не сработало вовремя" in text
    assert kw["failure_notice"] is True
    assert "delivery_ttl_seconds" not in job


def test_owner_web_chat_receives_the_notice(home, monkeypatch):
    from korra_state import SessionDB

    db = SessionDB()
    try:
        db.create_session(session_id="web-chat-1", source="api_server")
    finally:
        db.close()
    job = jobs.create_job(
        prompt="Собери отчёт", schedule="in 5m", name="Отчёт", deliver="origin",
        origin={"platform": "api_server", "chat_id": "web-chat-1", "session_key": "web-chat-1"},
    )
    assert job
    _miss(monkeypatch)
    jobs.get_due_jobs()

    scheduler._notify_missed_oneshots(None, None)

    db = SessionDB()
    try:
        rows = [m for m in db.get_messages("web-chat-1") if m["role"] == "assistant"]
    finally:
        db.close()
    assert len(rows) == 1 and "не сработало вовремя" in rows[0]["content"]
