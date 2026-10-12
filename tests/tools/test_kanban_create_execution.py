"""kanban_create tells created from started, honestly (K21-290)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from korra_cli import kanban_db as kb


def _config(home: Path, *, general: bool, exact: bool) -> None:
    (home / "config.yaml").write_text(
        "kanban:\n"
        f"  dispatch_in_gateway: {str(general).lower()}\n"
        f"  dispatch_exact_requests: {str(exact).lower()}\n"
    )
    kb._EXACT_CONFIG_CACHE.clear()


@pytest.fixture
def env(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    for prof in ("default", "pm"):
        (home / "profiles" / prof).mkdir(parents=True)
        (home / "profiles" / prof / "config.yaml").write_text("{}\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("KORRA_KANBAN_TASK", raising=False)
    monkeypatch.delenv("KORRA_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    from tools import kanban_tools as kt

    monkeypatch.setattr(kt, "_dispatcher_presence", lambda: (True, ""))
    monkeypatch.setattr(kt, "_maybe_auto_subscribe", lambda conn, tid: True)

    def no_start(*a, **k):
        raise AssertionError("kanban_create must not start a worker itself")

    monkeypatch.setattr(kb, "dispatch_once", no_start)
    monkeypatch.setattr("agent.estop.is_engaged", lambda: False)
    return home


def _create(**args):
    from tools import kanban_tools as kt

    return json.loads(kt._handle_create({"title": "t", "assignee": "pm", **args}))


def _requests(tid):
    with kb.connect_closing() as conn:
        return [e for e in kb.list_events(conn, tid) if e.kind == "dispatch_requested"]


def test_created_card_is_not_called_started(env):
    out = _create()
    ex = out["execution"]
    assert ex["started"] is False
    assert ex["run_id"] is None
    assert ex["state"] == "queued" and ex["will_start"] is True


def test_general_queue_without_a_running_dispatcher_says_nothing_will_start(env, monkeypatch):
    from tools import kanban_tools as kt

    monkeypatch.setattr(kt, "_dispatcher_presence", lambda: (False, "The gateway is not running."))
    ex = _create()["execution"]
    assert ex["state"] == "no_dispatcher" and ex["will_start"] is False
    assert "gateway is not running" in ex["reason"]


def test_both_queues_off_names_the_operator_command(env):
    _config(env, general=False, exact=False)
    out = _create()
    ex = out["execution"]
    assert ex["state"] == "no_dispatcher" and ex["will_start"] is False
    assert f"korra kanban dispatch --task-id {out['task_id']}" in ex["reason"]
    assert _requests(out["task_id"]) == []


def test_exact_mode_records_a_request_and_does_not_start_the_worker(env):
    _config(env, general=False, exact=True)
    out = _create()
    ex = out["execution"]
    assert ex["state"] == "queued" and ex["will_start"] is True and ex["started"] is False
    assert len(_requests(out["task_id"])) == 1


def test_pause_is_reported_and_the_request_waits(env, monkeypatch):
    _config(env, general=False, exact=True)
    monkeypatch.setattr("agent.estop.is_engaged", lambda: True)
    out = _create()
    ex = out["execution"]
    assert ex["state"] == "paused" and ex["will_start"] is False and ex["started"] is False
    assert len(_requests(out["task_id"])) == 1


def test_human_step_is_not_started_or_requested(env):
    _config(env, general=False, exact=True)
    out = _create(assignee=None, actor_kind="human")
    ex = out["execution"]
    assert ex["state"] == "human_step" and ex["will_start"] is False
    assert _requests(out["task_id"]) == []


def test_step_waiting_for_a_parent_says_so(env):
    _config(env, general=False, exact=True)
    first = _create()["task_id"]
    ex = _create(parents=[first])["execution"]
    assert ex["state"] == "waiting_for_parents" and ex["started"] is False


def test_missing_subscription_is_reported(env, monkeypatch):
    from tools import kanban_tools as kt

    monkeypatch.setattr(kt, "_maybe_auto_subscribe", lambda conn, tid: False)
    ex = _create()["execution"]
    assert ex["notify"] == "not_subscribed"
    assert "not subscribed" in ex["reason"]


def test_worker_follows_the_owner_flag_not_its_own_profile(env, monkeypatch):
    _config(env, general=False, exact=True)
    monkeypatch.setenv("HERMES_HOME", str(env / "profiles" / "pm"))
    monkeypatch.setenv("KORRA_KANBAN_TASK", "t_parent")
    out = _create()
    assert out["execution"]["state"] == "queued"
    assert len(_requests(out["task_id"])) == 1


def test_running_is_claimed_only_with_a_linked_live_run(env, monkeypatch):
    from tools import kanban_tools as kt

    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="t", assignee="pm")
        kb.claim_task(conn, tid, claimer="w")
        conn.execute("UPDATE tasks SET worker_pid = 4242 WHERE id = ?", (tid,))
        conn.commit()
        task = kb.get_task(conn, tid)

        monkeypatch.setattr(kb, "_pid_alive", lambda _p: True)
        live = kt._created_card_execution(kb, conn, task, subscribed=True)
        assert live["state"] == "running" and live["started"] is True
        assert live["run_id"] == task.current_run_id

        monkeypatch.setattr(kb, "_pid_alive", lambda _p: False)
        dead = kt._created_card_execution(kb, conn, task, subscribed=True)
        assert dead["started"] is False and dead["state"] == "claimed"


def test_guidance_ties_a_started_claim_to_the_execution_block():
    from agent.prompt_builder import KANBAN_CHAT_GUIDANCE as g

    assert "execution.started" in g
    assert "within about a minute" not in g
    assert len(g) <= 3500
