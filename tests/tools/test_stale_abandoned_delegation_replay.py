"""K21-388: an old abandoned delegation must not wake the model after a restart.

Real SQLite (tmp state.db). Recovery of a dead owner's ``running`` record stamps
a technical ``completed_at=now``; the replay age cap has to be measured from
``dispatched_at`` for such records, otherwise a 17-day-old task looks fresh.
"""

import queue
import time

import pytest

from tools import async_delegation as ad

DAY = 24 * 3600.0


@pytest.fixture(autouse=True)
def _ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(ad, "_db_path", lambda: tmp_path / "state.db")


def _dispatch(delegation_id, age_s, status="running"):
    ad._persist_dispatch({
        "delegation_id": delegation_id, "goal": "g", "context": None,
        "toolsets": None, "role": "leaf", "model": "m",
        "session_key": "S", "origin_ui_session_id": "",
        "parent_session_id": "S", "status": status,
        "dispatched_at": time.time() - age_s, "completed_at": None,
        "interrupt_fn": None,
    })


def _owner_gone(delegation_id):
    with ad._transaction() as conn:
        conn.execute(
            "UPDATE async_delegations SET owner_pid=NULL WHERE delegation_id=?",
            (delegation_id,),
        )


def _complete(delegation_id, dispatched_age_s, completed_age_s):
    now = time.time()
    ad._persist_completion(
        {"delegation_id": delegation_id, "status": "completed",
         "dispatched_at": now - dispatched_age_s,
         "completed_at": now - completed_age_s},
        {"summary": "ok"},
    )


def _row(delegation_id):
    with ad._connect() as conn:
        return conn.execute(
            "SELECT state, delivery_state, event_json FROM async_delegations "
            "WHERE delegation_id=?", (delegation_id,),
        ).fetchone()


def _restore():
    q = queue.Queue()
    n = ad.restore_undelivered_completions(q)
    return n, [q.get_nowait() for _ in range(q.qsize())]


def test_koza_17_day_old_running_task_is_dropped_not_replayed():
    _dispatch("koza", 17 * DAY)
    _owner_gone("koza")

    n, events = _restore()

    assert (n, events) == (0, [])
    state, delivery, event_json = _row("koza")
    assert (state, delivery) == ("unknown", "dropped")
    assert event_json is not None


def test_second_restart_does_not_revive_dropped_abandoned_task():
    _dispatch("koza", 17 * DAY)
    _owner_gone("koza")
    _restore()

    assert _restore() == (0, [])
    assert _row("koza")[1] == "dropped"


def test_fresh_abandoned_task_still_notifies_once():
    _dispatch("fresh", 3600.0)
    _owner_gone("fresh")

    n, events = _restore()

    assert n == 1
    assert events[0]["delegation_id"] == "fresh"
    assert events[0]["status"] == "unknown"
    assert _row("fresh")[1] == "pending"


def test_abandoned_task_just_under_cap_notifies_just_over_is_dropped():
    _dispatch("under", ad._MAX_COMPLETION_REPLAY_AGE_S - 600)
    _dispatch("over", ad._MAX_COMPLETION_REPLAY_AGE_S + 600)
    _owner_gone("under")
    _owner_gone("over")

    n, events = _restore()

    assert [e["delegation_id"] for e in events] == ["under"]
    assert _row("over")[1] == "dropped"


def test_fresh_completed_task_is_delivered_exactly_once():
    _dispatch("done", 600.0)
    _complete("done", 600.0, 60.0)

    n, events = _restore()
    assert n == 1 and events[0]["delegation_id"] == "done"

    claim = "c1"
    assert ad.claim_completion_delivery("done", claim)
    assert ad.complete_completion_delivery("done", claim)

    assert _restore() == (0, [])
    assert _row("done")[1] == "delivered"


def test_old_completed_task_is_dropped():
    _dispatch("old", 17 * DAY)
    _complete("old", 17 * DAY, 17 * DAY - 60)

    assert _restore() == (0, [])
    assert _row("old")[1] == "dropped"


def test_long_running_task_finished_recently_is_still_delivered():
    _dispatch("long", 3 * DAY)
    _complete("long", 3 * DAY, 60.0)

    n, events = _restore()

    assert n == 1 and events[0]["delegation_id"] == "long"
