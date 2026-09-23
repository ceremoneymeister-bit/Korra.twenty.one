"""Owner control of a Kanban task (K21-139).

The owner answers an agent's question, pauses work and archives steps from
the board. Each of these must do exactly what the board says:

* a different question after an answer is progress, not an unblock loop;
* a pause parks the task, stops its live worker and never feeds triage;
* archiving an unfinished step does not release the steps waiting for it;
* an answer is saved and resumes the task atomically, once per request.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from korra_cli import kanban_db as kb
from korra_cli import kanban_decompose


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _running(conn, tid):
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
    assert kb.claim_task(conn, tid, claimer="worker") is not None


def _running_task(conn, title="t"):
    tid = kb.create_task(conn, title=title, assignee="worker")
    _running(conn, tid)
    return tid


# ---------------------------------------------------------------------------
# Questions and the unblock-loop breaker
# ---------------------------------------------------------------------------

def test_second_different_question_stays_with_owner(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        kb.block_task(conn, tid, reason="Какую дату поставить?", kind="needs_input")
        kb.unblock_task(conn, tid)
        _running(conn, tid)
        kb.block_task(conn, tid, reason="Кому отправить отчёт?", kind="needs_input")
        task = kb.get_task(conn, tid)
        assert task.status == "blocked"
        assert task.block_recurrences == 1
        assert task.block_reason == "Кому отправить отчёт?"


def test_same_question_again_is_still_a_loop(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        kb.block_task(conn, tid, reason="Нужен доступ к CRM", kind="needs_input")
        kb.unblock_task(conn, tid)
        _running(conn, tid)
        kb.block_task(conn, tid, reason="  нужен доступ к  crm ", kind="needs_input")
        assert kb.get_task(conn, tid).status == "triage"


def test_loop_triage_is_not_offered_to_auto_decompose(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        kb.block_task(conn, tid, reason="same", kind="needs_input")
        kb.unblock_task(conn, tid)
        _running(conn, tid)
        kb.block_task(conn, tid, reason="same", kind="needs_input")
        idea = kb.create_task(conn, title="idea", triage=True)
    ids = kanban_decompose.list_triage_ids()
    assert idea in ids
    assert tid not in ids


def test_completion_clears_the_question(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        kb.block_task(conn, tid, reason="q", kind="needs_input")
        kb.unblock_task(conn, tid)
        assert kb.complete_task(conn, tid, result="done")
        assert kb.get_task(conn, tid).block_reason is None


# ---------------------------------------------------------------------------
# Owner pause
# ---------------------------------------------------------------------------

def test_pause_is_sticky_and_outside_the_loop_breaker(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        kb.block_task(conn, tid, reason="q", kind="needs_input")
        kb.unblock_task(conn, tid)
        _running(conn, tid)
        assert kb.block_task(conn, tid, reason="Подожди", kind=kb.OWNER_PAUSE_KIND)
        assert kb.unblock_task(conn, tid)
        _running(conn, tid)
        assert kb.block_task(conn, tid, reason="Подожди", kind=kb.OWNER_PAUSE_KIND)
        task = kb.get_task(conn, tid)
        assert task.status == "blocked"
        assert task.block_kind == kb.OWNER_PAUSE_KIND
        assert task.block_recurrences == 1
        kb.recompute_ready(conn)
        assert kb.get_task(conn, tid).status == "blocked"


def test_waiting_step_can_be_paused_and_resumes_waiting(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        parent = kb.create_task(conn, title="parent", assignee="worker")
        child = kb.create_task(conn, title="child", assignee="worker", parents=[parent])
        assert kb.get_task(conn, child).status == "todo"
        assert kb.block_task(conn, child, kind=kb.OWNER_PAUSE_KIND)
        assert kb.unblock_task(conn, child)
        assert kb.get_task(conn, child).status == "todo"


def test_pause_stops_the_live_worker(kanban_home: Path, monkeypatch) -> None:
    calls = []

    def fake_terminate(pid, claim_lock, **_):
        calls.append((pid, claim_lock))
        return {"terminated": True, "host_local": True}

    monkeypatch.setattr(kb, "_terminate_reclaimed_worker", fake_terminate)
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET worker_pid = 4242 WHERE id = ?", (tid,))
        outcome = kb.pause_task(conn, tid, reason="Стоп")
        assert outcome == {"ok": True, "stopped": True}
        assert calls and calls[0][0] == 4242
        task = kb.get_task(conn, tid)
        assert task.status == "blocked" and task.worker_pid is None
        kinds = [e.kind for e in kb.list_events(conn, tid)]
        assert "worker_stopped" in kinds


def test_paused_worker_cannot_complete_late(kanban_home: Path, monkeypatch) -> None:
    monkeypatch.setattr(kb, "_terminate_reclaimed_worker", lambda *a, **k: {"terminated": False})
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        run_id = kb.get_task(conn, tid).current_run_id
        kb.pause_task(conn, tid)
        assert not kb.complete_task(conn, tid, result="late", expected_run_id=run_id)
        assert kb.get_task(conn, tid).status == "blocked"


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

def test_archiving_an_unfinished_step_does_not_release_the_next(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        parent = _running_task(conn, "Подтвердить замены")
        child = kb.create_task(conn, title="Заменить ссылки", assignee="worker", parents=[parent])
        kb.block_task(conn, parent, reason="Подтвердите", kind="needs_input")
        assert kb.archive_task(conn, parent)
        kb.recompute_ready(conn)
        assert kb.get_task(conn, child).status == "todo"
        assert kb.claim_task(conn, child, claimer="worker") is None


def test_archiving_a_finished_step_keeps_releasing(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        parent = _running_task(conn, "parent")
        child = kb.create_task(conn, title="child", assignee="worker", parents=[parent])
        assert kb.complete_task(conn, parent, result="ok")
        assert kb.get_task(conn, child).status == "ready"
        assert kb.archive_task(conn, parent)
        assert kb.get_task(conn, child).status == "ready"


def test_archive_can_stop_the_live_worker(kanban_home: Path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        kb, "_terminate_reclaimed_worker",
        lambda pid, claim_lock, **_: calls.append(pid) or {"terminated": True},
    )
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET worker_pid = 777 WHERE id = ?", (tid,))
        assert kb.archive_task(conn, tid, stop_worker=True)
        assert calls == [777]


# ---------------------------------------------------------------------------
# Answer and resume
# ---------------------------------------------------------------------------

def _blocked_with_question(conn, question="Подтвердите 4 замены"):
    tid = _running_task(conn)
    kb.block_task(conn, tid, reason=question, kind="needs_input")
    return tid


def test_answer_saves_comment_and_resumes_once(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _blocked_with_question(conn)
        revision = kb.block_revision(conn, tid)
        first = kb.respond_to_block(
            conn, tid, answer="Подтверждаю", author="Владелец",
            request_id="r1", revision=revision,
        )
        assert first == {"ok": True, "status": "ready", "duplicate": False, "reason": None}
        again = kb.respond_to_block(
            conn, tid, answer="Подтверждаю", author="Владелец",
            request_id="r1", revision=revision,
        )
        assert again["ok"] and again["duplicate"] and again["status"] == "ready"
        comments = kb.list_comments(conn, tid)
        assert [c.body for c in comments] == ["Подтверждаю"]


def test_answer_to_a_replaced_question_is_rejected(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _blocked_with_question(conn, "Первый вопрос")
        old = kb.block_revision(conn, tid)
        kb.unblock_task(conn, tid)
        _running(conn, tid)
        kb.block_task(conn, tid, reason="Второй вопрос", kind="needs_input")
        outcome = kb.respond_to_block(
            conn, tid, answer="да", author="Владелец", request_id="r2", revision=old,
        )
        assert outcome["ok"] is False and outcome["reason"] == "stale"
        assert kb.get_task(conn, tid).status == "blocked"
        assert kb.list_comments(conn, tid) == []


def test_answer_to_a_task_that_is_not_waiting_is_rejected(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        tid = _running_task(conn)
        outcome = kb.respond_to_block(
            conn, tid, answer="да", author="Владелец", request_id="r3",
        )
        assert outcome == {"ok": False, "status": "running", "duplicate": False, "reason": "not_blocked"}


def test_answer_respects_unfinished_parents(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        parent = kb.create_task(conn, title="parent", assignee="worker")
        child = kb.create_task(conn, title="child", assignee="worker", parents=[parent])
        kb.block_task(conn, child, kind=kb.OWNER_PAUSE_KIND, reason="пауза")
        outcome = kb.respond_to_block(
            conn, child, answer=None, author="Владелец", request_id="r4",
        )
        assert outcome["ok"] and outcome["status"] == "todo"
