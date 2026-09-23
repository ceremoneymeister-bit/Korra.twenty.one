"""Owner acceptance and human steps (K21-141, K21-142).

An agent never accepts on the owner's behalf: a task the owner accepts is
*submitted* by its worker, waits in ``review`` with its result, and is
finished only by the owner — or returned with a remark. A human step is the
owner's own work: no agent is ever spawned or auto-assigned for it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from korra_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _claim(conn, tid):
    claimed = kb.claim_task(conn, tid, claimer="worker")
    assert claimed is not None
    return kb.get_task(conn, tid).current_run_id


def test_worker_completion_is_submitted_for_the_owner(kanban_home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="3 названия", assignee="pm", acceptance="owner")
        child = kb.create_task(conn, title="next", assignee="pm", parents=[tid])
        run_id = _claim(conn, tid)
        assert kb.complete_task(
            conn, tid, result="1. Поручения — …", summary="3 варианта",
            expected_run_id=run_id, as_worker=True,
        )
        task = kb.get_task(conn, tid)
        assert task.status == "review" and task.completed_at is None
        assert task.result == "1. Поручения — …"
        assert kb.get_task(conn, child).status == "todo"
        kinds = [e.kind for e in kb.list_events(conn, tid)]
        assert "submitted" in kinds and "completed" not in kinds
        assert kb.submitted_version(conn, tid) is not None


def test_owner_review_is_never_given_to_an_agent_reviewer(kanban_home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="t", assignee="pm", acceptance="owner")
        run_id = _claim(conn, tid)
        kb.complete_task(conn, tid, result="r", expected_run_id=run_id, as_worker=True)
        assert kb.claim_review_task(conn, tid, claimer="reviewer") is None
        assert not kb.has_spawnable_review(conn)


def test_owner_accepts_the_version_they_saw(kanban_home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="t", assignee="pm", acceptance="owner")
        child = kb.create_task(conn, title="next", assignee="pm", parents=[tid])
        run_id = _claim(conn, tid)
        kb.complete_task(conn, tid, result="r", summary="s", expected_run_id=run_id, as_worker=True)
        version = kb.submitted_version(conn, tid)
        out = kb.accept_result(conn, tid, author="Владелец", version=version, request_id="a1")
        assert out == {"ok": True, "duplicate": False, "reason": None, "status": "done"}
        task = kb.get_task(conn, tid)
        assert task.status == "done" and task.completed_at is not None
        assert kb.get_task(conn, child).status == "ready"
        completed = [e for e in kb.list_events(conn, tid) if e.kind == "completed"]
        assert len(completed) == 1 and completed[0].payload["accepted_by"] == "Владелец"
        again = kb.accept_result(conn, tid, author="Владелец", version=version, request_id="a1")
        assert again["ok"] and again["duplicate"]


def test_stale_acceptance_is_rejected_after_rework(kanban_home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="t", assignee="pm", acceptance="owner")
        run_id = _claim(conn, tid)
        kb.complete_task(conn, tid, result="v1", expected_run_id=run_id, as_worker=True)
        v1 = kb.submitted_version(conn, tid)
        out = kb.return_for_rework(
            conn, tid, comment="Добавь вариант со словом «помощник»",
            author="Владелец", version=v1, request_id="w1",
        )
        assert out["ok"] and out["status"] == "ready"
        assert [c.body for c in kb.list_comments(conn, tid)] == ["Добавь вариант со словом «помощник»"]
        run_id = _claim(conn, tid)
        kb.complete_task(conn, tid, result="v2", expected_run_id=run_id, as_worker=True)
        stale = kb.accept_result(conn, tid, author="Владелец", version=v1, request_id="a2")
        assert stale["ok"] is False and stale["reason"] == "stale"
        v2 = kb.submitted_version(conn, tid)
        assert kb.accept_result(conn, tid, author="Владелец", version=v2, request_id="a3")["ok"]
        assert kb.get_task(conn, tid).result == "v2"


def test_rework_requires_a_remark(kanban_home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="t", assignee="pm", acceptance="owner")
        run_id = _claim(conn, tid)
        kb.complete_task(conn, tid, result="r", expected_run_id=run_id, as_worker=True)
        with pytest.raises(ValueError):
            kb.return_for_rework(conn, tid, comment="  ", author="Владелец")


def test_default_completion_stays_final(kanban_home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="internal", assignee="pm")
        run_id = _claim(conn, tid)
        assert kb.complete_task(conn, tid, result="r", expected_run_id=run_id, as_worker=True)
        assert kb.get_task(conn, tid).status == "done"


def test_human_step_is_never_dispatched_or_auto_assigned(kanban_home, monkeypatch):
    spawned = []
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="Обучить команду", assignee="rop", actor_kind="human")
        task = kb.get_task(conn, tid)
        assert task.actor_kind == "human" and task.assignee is None and task.status == "ready"
        assert kb.claim_task(conn, tid, claimer="x") is None
        assert not kb.has_spawnable_ready(conn)
        kb.dispatch_once(
            conn,
            spawn_fn=lambda *a, **k: spawned.append(a) or 1,
            default_assignee="default",
        )
        task = kb.get_task(conn, tid)
        assert spawned == [] and task.assignee is None and task.status == "ready"
        assert kb.complete_task(conn, tid, result="Провела обучение")
        assert kb.get_task(conn, tid).status == "done"


def test_steps_inherit_the_plan_of_their_parent(kanban_home):
    with kb.connect_closing() as conn:
        first = kb.create_task(
            conn, title="Аудит", assignee="rop",
            plan_id="plan-bots", plan_title="Новая система ботов",
        )
        second = kb.create_task(conn, title="Замены", assignee="rop", parents=[first])
        task = kb.get_task(conn, second)
        assert (task.plan_id, task.plan_title) == ("plan-bots", "Новая система ботов")


def test_invalid_acceptance_is_rejected(kanban_home):
    with kb.connect_closing() as conn:
        with pytest.raises(ValueError):
            kb.create_task(conn, title="t", assignee="pm", acceptance="boss")
