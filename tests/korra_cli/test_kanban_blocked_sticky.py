"""Regression tests for #28712 — kanban dispatcher must not auto-promote
worker-initiated ``kanban_block`` (sticky blocks), but must keep
auto-recovering circuit-breaker blocks.

The bug: when a worker called ``kanban_block(reason="review-required:
...")`` to hand off to a human, the dispatcher's ``recompute_ready``
would promote the task back to ``ready`` on the next tick.  The fresh
worker found nothing to do (work already applied), exited cleanly, and
got recorded as a ``protocol_violation`` → ``gave_up`` → promote → loop
until manual intervention.

These tests pin down:

* Worker / operator-initiated blocks are sticky and survive
  ``recompute_ready``.
* Circuit-breaker blocks (``gave_up`` event, status flipped via
  ``_record_task_failure``) no longer auto-recover: the owner was told
  «сдался», so only an explicit unblock resumes the task.
* A card created ``blocked`` (R3) is sticky too, with or without parents.
* An explicit ``kanban_unblock`` clears the sticky state.
* The full block → promote → crash → ``gave_up`` loop is broken after
  this fix: subsequent ticks leave the task blocked.

The tangentially related schema-init ordering bug originally reported
in #28712 (``init_db`` crashing on legacy DBs that pre-dated the
``session_id`` migration) is covered separately by
``test_kanban_db.py::test_connect_migrates_legacy_db_before_optional_column_indexes``,
landed via #28754 / #28781 ahead of this fix.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from korra_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated HERMES_HOME with an empty kanban DB."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


# ---------------------------------------------------------------------------
# Worker-initiated kanban_block must be sticky
# ---------------------------------------------------------------------------


def test_worker_block_is_not_auto_promoted_by_recompute_ready(kanban_home: Path) -> None:
    """A standalone task that a worker explicitly blocks for review
    must stay blocked across an arbitrary number of dispatcher ticks.
    Before #28712's fix, ``recompute_ready`` would silently flip it
    back to ``ready`` on the very next tick."""
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="needs human review")
        kb.claim_task(conn, tid)
        assert kb.block_task(
            conn, tid,
            reason="review-required: please verify ACL change",
            expected_run_id=kb.get_task(conn, tid).current_run_id,
        )
        assert kb.get_task(conn, tid).status == "blocked"

        # Hammer the promotion code — exactly the dispatcher loop's
        # behaviour, just compressed in time.
        for _ in range(5):
            promoted = kb.recompute_ready(conn)
            assert promoted == 0, "worker-blocked task must not auto-promote"
            assert kb.get_task(conn, tid).status == "blocked"




# ---------------------------------------------------------------------------
# Circuit-breaker blocks still auto-recover (preserve #40c1decb3 intent)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# unblock_task clears the sticky state
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Full bug-shaped loop: block → promote → crash → gave_up → next tick
# ---------------------------------------------------------------------------


def test_protocol_violation_loop_is_broken(kanban_home: Path) -> None:
    """Reproduces the exact #28712 loop and asserts the dispatcher
    leaves the task blocked instead of cycling.

    Loop shape from the issue:

    1. Worker calls ``kanban_block`` → status='blocked',
       ``task_runs.outcome='blocked'``, ``blocked`` event.
    2. (Bug) Dispatcher promotes back to ``ready``.
    3. Fresh worker exits cleanly without terminal tool call →
       ``protocol_violation`` event.
    4. ``_record_task_failure(failure_limit=1)`` → ``gave_up`` event,
       status='blocked' again.
    5. (Bug) Dispatcher promotes again → infinite loop.

    With the fix in place, step 2 never happens — the test simulates
    one would-be loop cycle by faking the crash-then-gave_up entries
    that *would* have been written and asserts the *next* tick still
    leaves the task blocked.
    """
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="loop reproducer")
        kb.claim_task(conn, tid)
        kb.block_task(
            conn, tid,
            reason="review-required: human eyes please",
            expected_run_id=kb.get_task(conn, tid).current_run_id,
        )
        assert kb.get_task(conn, tid).status == "blocked"

        # First dispatcher tick — must NOT promote.
        assert kb.recompute_ready(conn) == 0
        assert kb.get_task(conn, tid).status == "blocked"

        # Simulate the (hypothetical) protocol_violation + gave_up
        # entries that the dispatcher would have written if the bug
        # were still present.  Even with those event rows in place,
        # the worker-initiated ``blocked`` event is the most recent
        # of the ``{blocked, unblocked}`` pair, so the sticky guard
        # still fires.
        now = int(time.time())
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) "
            "VALUES (?, 'protocol_violation', NULL, ?)",
            (tid, now),
        )
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) "
            "VALUES (?, 'gave_up', NULL, ?)",
            (tid, now + 1),
        )
        conn.commit()

        # Subsequent ticks must still leave it blocked.
        for _ in range(3):
            promoted = kb.recompute_ready(conn)
            assert promoted == 0
            assert kb.get_task(conn, tid).status == "blocked"


# ---------------------------------------------------------------------------
# Schema-init recovery on legacy DBs is covered by
# tests/korra_cli/test_kanban_db.py::test_connect_migrates_legacy_db_before_optional_column_indexes
# (landed via #28754 / #28781).  The original PR shipped a duplicate test
# here; dropped during salvage to avoid two assertions of the same contract.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# A card created ``blocked`` waits for a human (R3)
# ---------------------------------------------------------------------------


def _spawner(spawns: list) :
    def fake_spawn(task, workspace, board=None):
        spawns.append(task.id)
        return 4242
    return fake_spawn


@pytest.mark.parametrize("with_done_parent", [False, True])
def test_card_created_blocked_is_not_started_by_the_dispatcher(
    kanban_home: Path, all_assignees_spawnable, with_done_parent: bool,
) -> None:
    """Without parents ``recompute_ready`` saw no sticky event, promoted the
    card (``all([])`` is True) and the same pass spawned a worker."""
    spawns: list[str] = []
    with kb.connect() as conn:
        parents: tuple[str, ...] = ()
        if with_done_parent:
            parent = kb.create_task(conn, title="parent", assignee="bob")
            kb.complete_task(conn, parent, result="done")
            parents = (parent,)
        tid = kb.create_task(
            conn, title="нужен человек", assignee="alice",
            initial_status="blocked", parents=parents,
        )
        for _ in range(3):
            kb.dispatch_once(conn, spawn_fn=_spawner(spawns))
        assert kb.get_task(conn, tid).status == "blocked"
        kinds = [e.kind for e in kb.list_events(conn, tid)]
        assert "promoted" not in kinds and "claimed" not in kinds
        assert spawns == []

        assert kb.unblock_task(conn, tid)
        assert kb.get_task(conn, tid).status == "ready"
        kb.dispatch_once(conn, spawn_fn=_spawner(spawns))
    assert spawns == [tid]


# ---------------------------------------------------------------------------
# «Сдался» (gave_up) is final until an explicit unblock
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("forced", [False, True])
def test_gave_up_task_is_not_respawned_by_the_same_pass(
    kanban_home: Path, all_assignees_spawnable, forced: bool,
) -> None:
    """Crash/timeout paths trip the breaker at DEFAULT_FAILURE_LIMIT (or a
    forced trip below any limit), while the dispatcher passes the configured
    ``kanban.failure_limit`` to ``recompute_ready``. The counter check alone
    let the very pass that wrote ``gave_up`` promote and respawn the task,
    and the owner later heard «сдался» a second time."""
    spawns: list[str] = []
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="падает", assignee="alice")
        if forced:
            assert kb._record_task_failure(
                conn, tid, "protocol violation", outcome="crashed", force_trip=True,
            )
        else:
            assert not kb._record_task_failure(conn, tid, "boom", outcome="crashed")
            assert kb._record_task_failure(conn, tid, "boom", outcome="crashed")
        assert kb.get_task(conn, tid).status == "blocked"
        for _ in range(3):
            kb.dispatch_once(conn, spawn_fn=_spawner(spawns), failure_limit=5)
        assert kb.get_task(conn, tid).status == "blocked"
        kinds = [e.kind for e in kb.list_events(conn, tid)]
        assert kinds.count("gave_up") == 1
        assert "promoted" not in kinds
        assert spawns == []

        # The owner's unblock is the way back; it resets the counter.
        assert kb.unblock_task(conn, tid)
        assert kb.get_task(conn, tid).consecutive_failures == 0
        kb.dispatch_once(conn, spawn_fn=_spawner(spawns), failure_limit=5)
    assert spawns == [tid]
