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


@pytest.mark.parametrize("waiting_for_parent", [False, True])
def test_exact_request_without_a_gateway_is_kept_without_promising_a_start(env, monkeypatch, waiting_for_parent):
    from tools import kanban_tools as kt

    _config(env, general=False, exact=True)
    monkeypatch.setattr(kt, "_dispatcher_presence", lambda: (False, "The gateway is not running."))
    parents = []
    if waiting_for_parent:
        with kb.connect_closing() as conn:
            parents = [kb.create_task(conn, title="parent", assignee="pm")]
    out = _create(parents=parents)
    ex = out["execution"]
    assert ex["state"] == ("waiting_for_parents" if waiting_for_parent else "no_dispatcher")
    assert ex["will_start"] is False and ex["started"] is False
    assert "gateway is not running" in ex["reason"]
    assert len(_requests(out["task_id"])) == 1


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


def test_exact_mode_keeps_the_request_of_a_waiting_card_and_starts_it_after_an_unrequested_parent(env):
    _config(env, general=False, exact=True)
    with kb.connect_closing() as conn:
        parent = kb.create_task(conn, title="old task", assignee="pm")
        kb.claim_task(conn, parent)
    out = _create(parents=[parent])
    child, ex = out["task_id"], out["execution"]
    assert ex["state"] == "waiting_for_parents" and ex["will_start"] is True
    assert len(_requests(child)) == 1
    with kb.connect_closing() as conn:
        assert kb.pending_exact_dispatch_ids(conn) == []
        kb.complete_task(conn, parent, result="done")
        assert kb.get_task(conn, child).status == "ready"
        assert kb.pending_exact_dispatch_ids(conn) == [child]


def test_exact_mode_waiting_card_does_not_lift_other_old_cards(env):
    _config(env, general=False, exact=True)
    with kb.connect_closing() as conn:
        old = kb.create_task(conn, title="old ready", assignee="pm")
        parent = kb.create_task(conn, title="old running", assignee="pm")
        kb.claim_task(conn, parent)
    child = _create(parents=[parent])["task_id"]
    with kb.connect_closing() as conn:
        kb.complete_task(conn, parent, result="done")
        assert kb.pending_exact_dispatch_ids(conn) == [child]
        assert old not in kb.pending_exact_dispatch_ids(conn)


def test_waiting_card_without_any_start_path_does_not_promise_a_start(env):
    _config(env, general=False, exact=False)
    with kb.connect_closing() as conn:
        parent = kb.create_task(conn, title="p", assignee="pm")
    out = _create(parents=[parent])
    assert out["execution"]["state"] == "waiting_for_parents"
    assert out["execution"]["will_start"] is False
    assert _requests(out["task_id"]) == []


def test_waiting_card_in_general_mode_follows_the_dispatcher(env, monkeypatch):
    from tools import kanban_tools as kt

    _config(env, general=True, exact=False)
    with kb.connect_closing() as conn:
        parent = kb.create_task(conn, title="p", assignee="pm")
    assert _create(parents=[parent])["execution"]["will_start"] is True
    monkeypatch.setattr(kt, "_dispatcher_presence", lambda: (False, "No dispatcher is running."))
    ex = _create(parents=[parent])["execution"]
    assert ex["state"] == "waiting_for_parents" and ex["will_start"] is False


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


# --- «веду сам» (initial_status=self) через инструмент -------------------------


@pytest.fixture
def can_close(monkeypatch):
    """A profile that has kanban_complete (explicit kanban toolset)."""
    from tools import kanban_tools as kt

    monkeypatch.setattr(kt, "_check_kanban_worker_mode", lambda: True)
    return kt


@pytest.mark.parametrize("value", ["self", "Self", " SELF "])
def test_self_card_needs_no_assignee_and_is_not_started(env, can_close, monkeypatch, value):
    _config(env, general=False, exact=True)
    monkeypatch.setenv("KORRA_PROFILE", "operator")
    subscribed: list[str] = []
    monkeypatch.setattr(can_close, "_maybe_auto_subscribe",
                        lambda conn, tid: subscribed.append(tid) or True)
    out = json.loads(can_close._handle_create({"title": "моя работа", "initial_status": value}))
    assert "error" not in out, out
    ex = out["execution"]
    assert ex["state"] == "self"
    assert ex["started"] is False and ex["will_start"] is False and ex["run_id"] is None
    assert f"kanban_complete(task_id='{out['task_id']}')" in ex["reason"]
    assert "not subscribed" not in ex["reason"]
    assert out["max_runtime_seconds"] is None
    # No subscription: finishing one's own work wakes nobody and pings nobody.
    assert out["subscribed"] is False and subscribed == []
    assert _requests(out["task_id"]) == []
    with kb.connect_closing() as conn:
        task = kb.get_task(conn, out["task_id"])
        assert task.status == "running" and task.assignee is None
        assert task.actor_kind == "self" and task.claim_lock == "self:operator"
        assert kb.list_notify_subs(conn, out["task_id"]) == []


def test_self_card_is_refused_in_a_chat_without_kanban_complete(env, monkeypatch):
    """The owner's main chat has no kanban_complete (cost-aware 18.10): a self
    card it made would stay «в работе» forever, so it is refused up front."""
    from tools import kanban_tools as kt

    monkeypatch.setattr(kt, "_check_kanban_worker_mode", lambda: False)
    out = json.loads(kt._handle_create({"title": "моя работа", "initial_status": "self"}))
    assert "error" in out and "kanban_complete" in out["error"]
    with kb.connect_closing() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_create_schema_does_not_promise_an_unavailable_close_path():
    from tools import kanban_tools as kt

    desc = kt.KANBAN_CREATE_SCHEMA["parameters"]["properties"]["initial_status"]["description"]
    assert "close it with kanban_complete" in desc
    assert "refused elsewhere" in desc
    assert "until it is unblocked" in desc


def test_worker_closes_the_self_card_it_recorded_but_nothing_foreign(env, can_close, monkeypatch):
    kt = can_close
    with kb.connect_closing() as conn:
        mine = kb.create_task(conn, title="задача исполнителя", assignee="pm")
        other = kb.create_task(conn, title="чужая задача", assignee="pm")
    monkeypatch.setattr(kt, "_is_dispatcher_owned_worker", lambda: True)
    monkeypatch.setenv("KORRA_PROFILE", "pm")

    monkeypatch.setenv("KORRA_KANBAN_TASK", other)
    foreign = json.loads(kt._handle_create({"title": "чужая self", "initial_status": "self"}))
    assert foreign["execution"]["state"] == "self"

    monkeypatch.setenv("KORRA_KANBAN_TASK", mine)
    own = json.loads(kt._handle_create({"title": "моя self", "initial_status": "self"}))
    done = json.loads(kt._handle_complete({"task_id": own["task_id"], "summary": "готово"}))
    assert "error" not in done, done
    # A self card recorded by another worker's task and a plain foreign task
    # stay out of reach.
    for tid in (foreign["task_id"], other):
        refused = json.loads(kt._handle_complete({"task_id": tid, "summary": "x"}))
        assert "worker is scoped to task" in refused.get("error", ""), refused
    with kb.connect_closing() as conn:
        assert kb.get_task(conn, own["task_id"]).status == "done"
        assert kb.get_task(conn, foreign["task_id"]).status == "running"
        assert kb.get_task(conn, other).status == "ready"
