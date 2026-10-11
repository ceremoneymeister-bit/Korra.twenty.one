"""Exact start of requested cards when the general queue is off (K21-290 / Boris).

The owner turns ``kanban.dispatch_in_gateway`` off (old ready cards must stay
put) and ``kanban.dispatch_exact_requests`` on. A request is a
``dispatch_requested`` event; the one dispatcher starts only requested cards,
and still looks after every running card on the board.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from korra_cli import kanban_db as kb


def _profile(home: Path, name: str) -> None:
    d = home / "profiles" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text("{}\n")


def _owner_config(home: Path, *, general=False, exact=True) -> None:
    (home / "config.yaml").write_text(
        "kanban:\n"
        f"  dispatch_in_gateway: {str(general).lower()}\n"
        f"  dispatch_exact_requests: {str(exact).lower()}\n"
    )


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".hermes"
    h.mkdir()
    for name in ("default", "pm", "analyst"):
        _profile(h, name)
    _owner_config(h)
    monkeypatch.setenv("HERMES_HOME", str(h))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("KORRA_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("KORRA_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")
    kb._INITIALIZED_PATHS.clear()
    kb._EXACT_CONFIG_CACHE.clear()
    kb.init_db()
    return h


class Spawns:
    def __init__(self):
        self.ids = []

    def __call__(self, task, workspace, **_):
        self.ids.append(task.id)
        return 4242


def _card(conn, title="t", assignee="pm", **kw):
    return kb.create_task(conn, title=title, assignee=assignee, **kw)


def _events(conn, tid, kind):
    return [e for e in kb.list_events(conn, tid) if e.kind == kind]


# --- the flag -------------------------------------------------------------


def test_exact_requests_are_off_by_default(tmp_path, monkeypatch):
    h = tmp_path / ".hermes"
    h.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(h))
    monkeypatch.delenv("KORRA_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    kb._EXACT_CONFIG_CACHE.clear()
    assert kb.exact_dispatch_requests_enabled() is False
    from korra_cli.config_defaults import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["kanban"]["dispatch_exact_requests"] is False


def test_flag_is_read_from_the_board_owner_not_the_executor_profile(home, monkeypatch):
    worker_home = home / "profiles" / "pm"
    monkeypatch.setenv("HERMES_HOME", str(worker_home))
    kb._EXACT_CONFIG_CACHE.clear()
    assert kb.exact_dispatch_requests_enabled() is True

    _owner_config(home, exact=False)
    kb._EXACT_CONFIG_CACHE.clear()
    assert kb.exact_dispatch_requests_enabled() is False


# --- requests ---------------------------------------------------------------


def test_request_is_recorded_once_and_only_for_a_startable_card(home):
    with kb.connect_closing() as conn:
        ready = _card(conn)
        human = kb.create_task(conn, title="h", actor_kind="human")
        waiting = _card(conn, parents=[ready])
        assert kb.request_exact_dispatch(conn, ready) == "requested"
        assert kb.request_exact_dispatch(conn, ready) == "already"
        assert kb.request_exact_dispatch(conn, human) == "human"
        assert kb.request_exact_dispatch(conn, waiting) == "not_ready"
        assert kb.request_exact_dispatch(conn, "t_nope") == "missing"
        assert len(_events(conn, ready, "dispatch_requested")) == 1
        assert _events(conn, human, "dispatch_requested") == []


# --- what starts ------------------------------------------------------------


def test_only_requested_cards_start_old_ready_cards_stay(home):
    spawn = Spawns()
    with kb.connect_closing() as conn:
        old = _card(conn, "old backlog")
        wanted = _card(conn, "wanted")
        kb.request_exact_dispatch(conn, wanted)
        ids = kb.pending_exact_dispatch_ids(conn)
        assert ids == [wanted]
        res = kb.dispatch_once(conn, spawn_fn=spawn, only_task_ids=ids)
        assert spawn.ids == [wanted]
        assert res.spawned
        assert kb.get_task(conn, old).status == "ready"


def test_without_a_filter_the_general_queue_is_unchanged(home):
    spawn = Spawns()
    with kb.connect_closing() as conn:
        a = _card(conn, "a")
        b = _card(conn, "b")
        kb.dispatch_once(conn, spawn_fn=spawn)
        assert sorted(spawn.ids) == sorted([a, b])


def test_empty_filter_starts_nothing(home):
    spawn = Spawns()
    with kb.connect_closing() as conn:
        _card(conn)
        kb.dispatch_once(conn, spawn_fn=spawn, only_task_ids=[])
    assert spawn.ids == []


def test_a_card_that_cannot_start_does_not_block_the_next_request(home):
    spawn = Spawns()
    with kb.connect_closing() as conn:
        stuck = _card(conn, "stuck", assignee="lane-without-profile", priority=5)
        ok = _card(conn, "ok", assignee="analyst")
        kb.request_exact_dispatch(conn, stuck)
        kb.request_exact_dispatch(conn, ok)
        ids = kb.pending_exact_dispatch_ids(conn)
        assert ids == [stuck, ok]
        kb.dispatch_once(conn, spawn_fn=spawn, only_task_ids=ids)
        assert spawn.ids == [ok]
        assert kb.get_task(conn, stuck).status == "ready"


def test_housekeeping_runs_when_nothing_is_requested(home, monkeypatch):
    monkeypatch.setattr(kb, "_pid_alive", lambda _pid: False)
    kb._recent_worker_exits.clear()
    spawn = Spawns()
    with kb.connect_closing() as conn:
        tid = _card(conn)
        host = kb._claimer_id().split(":", 1)[0]
        kb.claim_task(conn, tid, claimer=f"{host}:w1")
        conn.execute("UPDATE tasks SET worker_pid = 91234 WHERE id = ?", (tid,))
        conn.commit()
        assert kb.pending_exact_dispatch_ids(conn) == []
        res = kb.dispatch_once(conn, spawn_fn=spawn, only_task_ids=[])
        assert tid in res.crashed
        assert kb.get_task(conn, tid).status == "ready"
        assert spawn.ids == []


# --- a request is for one piece of work --------------------------------------


def test_request_survives_retries_and_a_human_unblock(home):
    with kb.connect_closing() as conn:
        tid = _card(conn)
        kb.request_exact_dispatch(conn, tid)
        kb.block_task(conn, tid, reason="need an answer", kind="needs_input")
        assert kb.pending_exact_dispatch_ids(conn) == []
        kb.unblock_task(conn, tid)
        assert kb.pending_exact_dispatch_ids(conn) == [tid]


def test_finished_work_reopened_is_not_started_again_and_says_so(home):
    spawn = Spawns()
    with kb.connect_closing() as conn:
        tid = _card(conn)
        kb.request_exact_dispatch(conn, tid)
        kb.claim_task(conn, tid)
        kb.complete_task(conn, tid, result="done")
        conn.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (tid,))
        conn.commit()

        assert kb.pending_exact_dispatch_ids(conn) == []
        kb.pending_exact_dispatch_ids(conn, record_lapsed=True)
        kb.pending_exact_dispatch_ids(conn, record_lapsed=True)
        assert len(_events(conn, tid, "dispatch_request_lapsed")) == 1
        kb.dispatch_once(conn, spawn_fn=spawn, only_task_ids=[])
        assert spawn.ids == []

        assert kb.request_exact_dispatch(conn, tid) == "requested"
        assert kb.pending_exact_dispatch_ids(conn) == [tid]


# --- continuation --------------------------------------------------------------


def test_next_step_of_a_requested_chain_is_requested_when_the_parent_finishes(home):
    with kb.connect_closing() as conn:
        first = _card(conn, "first")
        second = _card(conn, "second", parents=[first])
        kb.request_exact_dispatch(conn, first)
        kb.claim_task(conn, first)
        kb.complete_task(conn, first, result="ok")
        assert kb.get_task(conn, second).status == "ready"
        ev = _events(conn, second, "dispatch_requested")
        assert [json.loads(e.payload) if isinstance(e.payload, str) else e.payload for e in ev][0]["source"] == "continuation"
        assert kb.pending_exact_dispatch_ids(conn) == [second]


def test_next_step_after_the_owner_accepts_is_requested(home):
    with kb.connect_closing() as conn:
        first = _card(conn, "first", acceptance="owner")
        second = _card(conn, "second", parents=[first])
        kb.request_exact_dispatch(conn, first)
        kb.claim_task(conn, first, claimer="worker")
        run_id = kb.get_task(conn, first).current_run_id
        kb.complete_task(conn, first, result="ok", expected_run_id=run_id, as_worker=True)
        assert kb.get_task(conn, first).status == "review"
        assert kb.get_task(conn, second).status == "todo"
        out = kb.accept_result(
            conn, first, author="owner", version=kb.submitted_version(conn, first),
            request_id="a1",
        )
        assert out["ok"], out
        assert kb.get_task(conn, second).status == "ready"
        assert kb.pending_exact_dispatch_ids(conn) == [second]


def test_chain_without_a_request_is_not_lifted(home):
    with kb.connect_closing() as conn:
        first = _card(conn, "first")
        second = _card(conn, "second", parents=[first])
        kb.claim_task(conn, first)
        kb.complete_task(conn, first, result="ok")
        assert kb.get_task(conn, second).status == "ready"
        assert kb.pending_exact_dispatch_ids(conn) == []


def test_continuation_needs_the_owner_flag(home):
    with kb.connect_closing() as conn:
        first = _card(conn, "first")
        second = _card(conn, "second", parents=[first])
        kb.request_exact_dispatch(conn, first)
        _owner_config(home, exact=False)
        kb._EXACT_CONFIG_CACHE.clear()
        kb.claim_task(conn, first)
        kb.complete_task(conn, first, result="ok")
        assert kb.pending_exact_dispatch_ids(conn) == []


# --- the operator path -----------------------------------------------------------


def test_cli_dispatch_task_id_starts_just_that_card(home, monkeypatch, capsys):
    from korra_cli import kanban as cli

    spawn = Spawns()
    monkeypatch.setattr(kb, "_default_spawn", spawn)
    with kb.connect_closing() as conn:
        _card(conn, "other")
        target = _card(conn, "target", assignee="analyst")
    import argparse

    args = argparse.Namespace(
        dry_run=False, max=None, failure_limit=kb.DEFAULT_SPAWN_FAILURE_LIMIT,
        json=True, task_id=[target],
    )
    assert cli._cmd_dispatch(args) == 0
    assert spawn.ids == [target]


# --- the watcher ---------------------------------------------------------------------


def _run_watcher(monkeypatch, home, *, paused=False, ticks=1):
    import gateway.kanban_watchers as kw
    import korra_cli.config as cfg_mod
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._running = True
    monkeypatch.setattr(
        cfg_mod, "load_config",
        lambda: {"kanban": {"dispatch_in_gateway": False, "dispatch_interval_seconds": 1}},
    )
    monkeypatch.setattr(kw, "_kanban_dispatch_allowed", lambda: not paused)
    spawn = Spawns()
    monkeypatch.setattr(kb, "_default_spawn", spawn)
    monkeypatch.setattr(kb, "list_boards", lambda include_archived=False: [{"slug": kb.DEFAULT_BOARD}])
    seen = {"calls": 0, "dispatch": []}
    real = kb.dispatch_once

    def spy(conn, **kwargs):
        seen["dispatch"].append(kwargs.get("only_task_ids"))
        return real(conn, **kwargs)

    monkeypatch.setattr(kb, "dispatch_once", spy)

    async def to_thread(fn, *a, **k):
        seen["calls"] += 1
        out = fn(*a, **k)
        if seen["calls"] >= 3 * ticks:
            runner._running = False
        return out

    async def sleep(_d):
        return None

    monkeypatch.setattr("gateway.run.asyncio.to_thread", to_thread)
    monkeypatch.setattr("gateway.run.asyncio.sleep", sleep)
    asyncio.run(asyncio.wait_for(runner._kanban_dispatcher_watcher(), timeout=10))
    return spawn, seen


def test_watcher_starts_requested_cards_only_and_still_runs_housekeeping(home, monkeypatch):
    with kb.connect_closing() as conn:
        _card(conn, "old backlog")
        wanted = _card(conn, "wanted", assignee="analyst")
        kb.request_exact_dispatch(conn, wanted)
    spawn, seen = _run_watcher(monkeypatch, home)
    assert spawn.ids == [wanted]
    assert seen["dispatch"] == [[wanted]]


def test_watcher_with_nothing_requested_still_calls_the_dispatcher(home, monkeypatch):
    with kb.connect_closing() as conn:
        _card(conn, "old backlog")
    spawn, seen = _run_watcher(monkeypatch, home)
    assert spawn.ids == []
    assert seen["dispatch"] == [[]]


def test_pause_stops_the_start_of_a_requested_card(home, monkeypatch):
    with kb.connect_closing() as conn:
        wanted = _card(conn, "wanted")
        kb.request_exact_dispatch(conn, wanted)
    spawn, seen = _run_watcher(monkeypatch, home, paused=True)
    assert spawn.ids == []
    assert seen["dispatch"] == []


def test_watcher_stays_off_when_neither_queue_nor_requests_are_on(home, monkeypatch):
    _owner_config(home, exact=False)
    kb._EXACT_CONFIG_CACHE.clear()
    with kb.connect_closing() as conn:
        wanted = _card(conn, "wanted")
        kb.request_exact_dispatch(conn, wanted)
    spawn, seen = _run_watcher(monkeypatch, home)
    assert spawn.ids == []
    assert seen["dispatch"] == []


# --- upgrade: a board like Boris's ----------------------------------------------------


def test_board_like_borisss_keeps_pending_requests_and_old_cards(home, monkeypatch):
    """Two profiles, exact=true in root, an event from 0.21.17, old ready cards."""
    spawn = Spawns()
    with kb.connect_closing() as conn:
        backlog = [_card(conn, f"old {i}", assignee=("pm" if i % 2 else "analyst")) for i in range(3)]
        pending = _card(conn, "asked in chat", assignee="analyst")
        finished = _card(conn, "asked and done once")
        # 0.21.17 wrote the bare event without a source for both.
        with kb.write_txn(conn):
            kb._append_event(conn, pending, "dispatch_requested", {"source": "kanban_create"})
            kb._append_event(conn, finished, "dispatch_requested", {"source": "kanban_create"})
        kb.claim_task(conn, finished)
        kb.complete_task(conn, finished, result="ok")
        conn.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (finished,))
        conn.commit()

        ids = kb.pending_exact_dispatch_ids(conn, record_lapsed=True)
        assert ids == [pending]
        kb.dispatch_once(conn, spawn_fn=spawn, only_task_ids=ids)
        assert spawn.ids == [pending]
        for tid in backlog + [finished]:
            assert kb.get_task(conn, tid).status == "ready"
        assert len(_events(conn, finished, "dispatch_request_lapsed")) == 1
