"""Review of 0.21.13 (Astra, R3–R7, R11): the owner's controls hold server-side."""

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
    assert kb.claim_task(conn, tid, claimer="worker") is not None
    return kb.get_task(conn, tid).current_run_id


# R3 ---------------------------------------------------------------------

def test_pause_during_spawn_cancels_the_worker(kanban_home, monkeypatch, all_assignees_spawnable):
    stopped = []
    monkeypatch.setattr(
        kb, "_terminate_reclaimed_worker",
        lambda pid, claim_lock, **_: stopped.append(pid) or {"terminated": True},
    )
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="t", assignee="worker")

        def spawn(task, workspace, board=None):
            # The owner pauses while the worker is starting.
            kb.pause_task(conn, task.id, reason="стоп")
            return 4321

        kb.dispatch_once(conn, spawn_fn=spawn)
        task = kb.get_task(conn, tid)
        assert task.status == "blocked" and task.worker_pid is None
        assert stopped == [4321]
        kinds = [e.kind for e in kb.list_events(conn, tid)]
        assert "spawn_cancelled" in kinds and "spawned" not in kinds


def test_pause_before_spawn_skips_it(kanban_home, monkeypatch, all_assignees_spawnable):
    spawned = []
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="t", assignee="worker")
        real_resolve = kb.resolve_workspace

        def resolve_and_pause(task, board=None):
            kb.pause_task(conn, task.id)
            return real_resolve(task, board=board)

        monkeypatch.setattr(kb, "resolve_workspace", resolve_and_pause)
        kb.dispatch_once(conn, spawn_fn=lambda *a, **k: spawned.append(a) or 99)
        assert spawned == []
        assert kb.get_task(conn, tid).status == "blocked"


# R4 ---------------------------------------------------------------------

def _approval(conn):
    tid = kb.create_task(conn, title="Заменить ссылки в CRM", assignee="rop")
    _claim(conn, tid)
    kb.block_task(conn, tid, reason="Разрешите 4 замены в CRM: …", kind="approval")
    return tid


def test_approval_question_needs_an_explicit_owner_decision(kanban_home):
    with kb.connect_closing() as conn:
        tid = _approval(conn)
        rev = kb.block_revision(conn, tid)
        plain = kb.respond_to_block(conn, tid, answer="да", author="Владелец",
                                    request_id="a1", revision=rev)
        assert plain["ok"] is False and plain["reason"] == "decision_required"
        assert not kb.unblock_task(conn, tid)
        assert kb.get_task(conn, tid).status == "blocked"
        granted = kb.respond_to_block(conn, tid, answer=None, author="Владелец",
                                      request_id="a2", revision=rev, decision="grant")
        assert granted["ok"] and granted["status"] == "ready"
        body = kb.list_comments(conn, tid)[-1].body
        assert "РАЗРЕШИЛ" in body and "Разрешите 4 замены" in body
        assert "owner_granted" in [e.kind for e in kb.list_events(conn, tid)]


def test_denied_approval_resumes_preparation_only(kanban_home):
    with kb.connect_closing() as conn:
        tid = _approval(conn)
        out = kb.respond_to_block(conn, tid, answer="каталог не трогать", author="Владелец",
                                  request_id="d1", revision=kb.block_revision(conn, tid),
                                  decision="deny")
        assert out["ok"]
        assert "НЕ РАЗРЕШИЛ" in kb.list_comments(conn, tid)[-1].body


# R5 ---------------------------------------------------------------------

def test_agent_cannot_accept_a_result_waiting_for_the_owner(kanban_home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="t", assignee="pm", acceptance="owner")
        child = kb.create_task(conn, title="next", assignee="pm", parents=[tid])
        run = _claim(conn, tid)
        assert kb.complete_task(conn, tid, result="v1", expected_run_id=run, as_worker=True)
        assert kb.get_task(conn, tid).status == "review"
        assert not kb.complete_task(conn, tid, result="v1", as_worker=True)
        assert kb.get_task(conn, tid).status == "review"
        assert kb.get_task(conn, child).status == "todo"


# R6 ---------------------------------------------------------------------

def test_answer_without_revision_is_refused_when_a_question_waits(kanban_home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="t", assignee="w")
        _claim(conn, tid)
        kb.block_task(conn, tid, reason="Какой срок?", kind="needs_input")
        out = kb.respond_to_block(conn, tid, answer="завтра", author="Владелец", request_id="x")
        assert out["ok"] is False and out["reason"] == "stale"


def test_repeated_question_in_triage_has_a_revision(kanban_home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="t", assignee="w")
        _claim(conn, tid)
        kb.block_task(conn, tid, reason="Нужен доступ", kind="needs_input")
        kb.unblock_task(conn, tid)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
        _claim(conn, tid)
        kb.block_task(conn, tid, reason="Нужен доступ", kind="needs_input")
        assert kb.get_task(conn, tid).status == "triage"
        assert isinstance(kb.block_revision(conn, tid), int)


def test_acceptance_requires_the_version(kanban_home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="t", assignee="pm", acceptance="owner")
        run = _claim(conn, tid)
        kb.complete_task(conn, tid, result="v1", expected_run_id=run, as_worker=True)
        out = kb.accept_result(conn, tid, author="Владелец", request_id="a")
        assert out["ok"] is False and out["reason"] == "stale"


# R7 ---------------------------------------------------------------------

def test_every_submitted_version_keeps_its_full_text(kanban_home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="t", assignee="pm", acceptance="owner")
        run = _claim(conn, tid)
        kb.complete_task(conn, tid, result="Полная версия 1 с обоснованиями",
                         summary="Short overview 1", expected_run_id=run, as_worker=True)
        kb.return_for_rework(conn, tid, comment="добавь вариант", author="Владелец",
                             version=kb.submitted_version(conn, tid), request_id="w")
        run = _claim(conn, tid)
        kb.complete_task(conn, tid, result="Полная версия 2",
                         summary="Short overview 2", expected_run_id=run, as_worker=True)
        results = [r.result for r in kb.list_runs(conn, tid)]
        assert "Полная версия 1 с обоснованиями" in results
        assert "Полная версия 2" in results


# R11 --------------------------------------------------------------------

def test_human_step_is_never_reviewed_or_completed_by_an_agent(kanban_home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="Обучить команду", actor_kind="human")
        assert not kb.request_review(conn, tid, summary="x", reviewer="default", force=True)
        assert kb.get_task(conn, tid).status == "ready"
        assert not kb.complete_task(conn, tid, result="обучил", as_worker=True)
        assert kb.get_task(conn, tid).status == "ready"
        # The owner (not an agent) marks their own step done.
        assert kb.complete_task(conn, tid, result="Провела обучение")
        assert kb.get_task(conn, tid).status == "done"


# R6, chat path -----------------------------------------------------------

def test_chat_unblock_carries_the_owner_answer_atomically(kanban_home, monkeypatch):
    import json

    from tools import kanban_tools as kt

    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("KORRA_KANBAN_TASK", raising=False)
    with kb.connect_closing() as conn:
        question = kb.create_task(conn, title="q", assignee="worker")
        _claim(conn, question)
        kb.block_task(conn, question, reason="Какой формат отчёта?", kind="needs_input")
        revision = kb.block_revision(conn, question)
        wait = kb.create_task(conn, title="w", assignee="worker")
        _claim(conn, wait)
        kb.block_task(conn, wait, reason="сервис недоступен", kind="transient")
        crm = kb.create_task(conn, title="c", assignee="worker")
        _claim(conn, crm)
        kb.block_task(conn, crm, reason="Разрешите 4 замены в CRM", kind="approval")
        crm_revision = kb.block_revision(conn, crm)

    def unblock(**args):
        return json.loads(kt._handle_unblock(args))

    # A question is not resumed without the owner's words or on a stale screen.
    assert "error" in unblock(task_id=question)
    assert "error" in unblock(task_id=question, answer="PDF", revision=revision - 1)
    out = unblock(task_id=question, answer="PDF", revision=revision)
    assert out["ok"] and out["status"] == "ready"
    with kb.connect_closing() as conn:
        assert any("PDF" in c.body for c in kb.list_comments(conn, question))
    # A technical wait is released as before.
    assert unblock(task_id=wait)["status"] == "ready"
    # Permission for external changes is the owner's decision on the card only.
    assert "error" in unblock(task_id=crm, answer="да", revision=crm_revision)
    with kb.connect_closing() as conn:
        assert kb.get_task(conn, crm).status == "blocked"
