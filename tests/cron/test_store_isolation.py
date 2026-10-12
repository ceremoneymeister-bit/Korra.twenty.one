"""K21-287 (г): одна битая запись или нечитаемая база не останавливают остальные задания и профили."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from unittest.mock import patch

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    from cron import jobs as cron_jobs

    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with cron_jobs.use_cron_store(tmp_path):
        cron_jobs.ensure_dirs()
        yield tmp_path


def _good_job(job_id="good-1"):
    return {
        "id": job_id,
        "name": "Отчёт",
        "prompt": "p",
        "schedule": {"kind": "interval", "minutes": 60, "display": "every 60m"},
        "enabled": True,
        "state": "scheduled",
        "next_run_at": "2020-01-01T00:00:00+00:00",
        "created_at": "2020-01-01T00:00:00+00:00",
    }


def test_junk_entries_in_jobs_list_do_not_stop_healthy_jobs(store):
    from cron import jobs as cron_jobs

    jobs_file = cron_jobs._current_cron_store().jobs_file
    jobs_file.write_text(json.dumps({"jobs": [None, "oops", 7, _good_job()]}), encoding="utf-8")

    due = cron_jobs.get_due_jobs()

    assert [j["id"] for j in due] == ["good-1"]
    assert [j["id"] for j in json.loads(jobs_file.read_text(encoding="utf-8"))["jobs"]] == ["good-1"]


def test_scalar_jobs_field_is_replaced_by_empty_list(store):
    from cron import jobs as cron_jobs

    jobs_file = cron_jobs._current_cron_store().jobs_file
    jobs_file.write_text(json.dumps({"jobs": None}), encoding="utf-8")

    assert cron_jobs.load_jobs() == []
    assert json.loads(jobs_file.read_text(encoding="utf-8"))["jobs"] == []


def test_bare_list_with_junk_keeps_only_jobs(store):
    from cron import jobs as cron_jobs

    jobs_file = cron_jobs._current_cron_store().jobs_file
    jobs_file.write_text(json.dumps([None, _good_job()]), encoding="utf-8")

    assert [j["id"] for j in cron_jobs.load_jobs()] == ["good-1"]


def test_multiplex_profile_with_unreadable_ledger_does_not_block_siblings(tmp_path):
    bad = tmp_path / "bad"
    good = tmp_path / "good"
    for d in (bad, good):
        (d / "cron").mkdir(parents=True)
    ticked: list[str] = []

    def fake_tick(*a, **k):
        from cron.jobs import _current_cron_store

        ticked.append(_current_cron_store().jobs_file.parent.parent.name)
        return 0

    from cron.scheduler_provider import InProcessCronScheduler

    def recover(self=None):
        from cron.jobs import _current_cron_store

        if _current_cron_store().jobs_file.parent.parent.name == "bad":
            raise sqlite3.DatabaseError("file is not a database")
        return 0

    prov = InProcessCronScheduler()
    prov.recover_interrupted = recover
    stop = threading.Event()
    with patch("cron.scheduler.tick", side_effect=fake_tick), \
         patch("cron.jobs.record_ticker_heartbeat", lambda **kw: None):
        t = threading.Thread(
            target=prov.start,
            args=(stop,),
            kwargs={"interval": 0, "profile_homes": [("bad", bad), ("good", good)]},
            daemon=True,
        )
        t.start()
        deadline = time.monotonic() + 10
        while "good" not in ticked and time.monotonic() < deadline:
            time.sleep(0.005)
        stop.set()
        t.join(timeout=5)

    assert not t.is_alive()
    assert "good" in ticked


def test_multiplex_tick_failure_in_one_profile_does_not_skip_next(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for d in (first, second):
        (d / "cron").mkdir(parents=True)
    ticked: list[str] = []

    def fake_tick(*a, **k):
        from cron.jobs import _current_cron_store

        name = _current_cron_store().jobs_file.parent.parent.name
        ticked.append(name)
        if name == "first":
            raise sqlite3.OperationalError("database is locked")
        return 0

    from cron.scheduler_provider import InProcessCronScheduler

    prov = InProcessCronScheduler()
    stop = threading.Event()
    with patch("cron.scheduler.tick", side_effect=fake_tick), \
         patch("cron.jobs.record_ticker_heartbeat", lambda **kw: None):
        t = threading.Thread(
            target=prov.start,
            args=(stop,),
            kwargs={"interval": 0, "profile_homes": [("first", first), ("second", second)]},
            daemon=True,
        )
        t.start()
        deadline = time.monotonic() + 10
        while "second" not in ticked and time.monotonic() < deadline:
            time.sleep(0.005)
        stop.set()
        t.join(timeout=5)

    assert not t.is_alive()
    assert "second" in ticked


def test_single_profile_ticker_survives_unreadable_ledger_at_startup(tmp_path, monkeypatch):
    from cron.scheduler_provider import InProcessCronScheduler

    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ticks: list[int] = []
    prov = InProcessCronScheduler()

    def broken(self=None):
        raise sqlite3.DatabaseError("file is not a database")

    prov.recover_interrupted = broken
    stop = threading.Event()
    with patch("cron.scheduler.tick", side_effect=lambda *a, **k: ticks.append(1) or 0):
        t = threading.Thread(target=prov.start, args=(stop,), kwargs={"interval": 0}, daemon=True)
        t.start()
        deadline = time.monotonic() + 10
        while not ticks and time.monotonic() < deadline:
            time.sleep(0.005)
        stop.set()
        t.join(timeout=5)

    assert ticks and not t.is_alive()
