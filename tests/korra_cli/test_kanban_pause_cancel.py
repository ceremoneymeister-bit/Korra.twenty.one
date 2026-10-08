"""Pausing a waiting card and cancelling one (K21-298)."""
import pytest

from korra_cli import kanban_db as kb
from korra_cli import kanban_decisions as decisions
from korra_state import SessionDB


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("KORRA_KANBAN_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("KORRA_KANBAN_DB", raising=False)
    db = SessionDB(tmp_path / "state.db")
    db.create_session("origin", source="api_server")
    db.close()
    kb.init_db()
    return tmp_path


def _asked(conn, kind="needs_input"):
    tid = kb.create_task(conn, title="Договор", assignee="lawyer", session_id="origin")
    kb.claim_task(conn, tid, claimer="worker")
    kb.block_task(conn, tid, kind=kind, reason="Какой срок?")
    return tid


def test_pause_of_a_waiting_card_keeps_its_history(home):
    with kb.connect_closing() as conn:
        tid = _asked(conn)
        kb.add_comment(conn, tid, "worker", "заметка")
        before = [e.kind for e in kb.list_events(conn, tid)]
        assert kb.pause_task(conn, tid, reason="Подождите")["ok"]
        task = kb.get_task(conn, tid)
        assert (task.status, task.block_kind) == ("blocked", kb.OWNER_PAUSE_KIND)
        assert [e.kind for e in kb.list_events(conn, tid)][:len(before)] == before
        assert [c.body for c in kb.list_comments(conn, tid)] == ["заметка"]
        assert decisions.project_task(conn, task, "default") is None
        assert kb.unblock_task(conn, tid)


def test_pause_of_a_parked_repeated_question(home):
    with kb.connect_closing() as conn:
        tid = _asked(conn)
        kb.unblock_task(conn, tid)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
        kb.claim_task(conn, tid, claimer="worker")
        kb.block_task(conn, tid, kind="needs_input", reason="Какой срок?")
        assert kb.get_task(conn, tid).status == "triage"
        assert kb.pause_task(conn, tid)["ok"]
        assert kb.get_task(conn, tid).status == "blocked"


def test_permission_question_is_not_dissolved_by_a_pause(home):
    with kb.connect_closing() as conn:
        tid = _asked(conn, kind="approval")
        assert not kb.pause_task(conn, tid)["ok"]
        assert kb.get_task(conn, tid).block_kind == "approval"


def test_cancel_removes_pending_decision_and_subscriptions(home):
    with kb.connect_closing() as conn:
        tid = _asked(conn)
        item = decisions.project_task(conn, kb.get_task(conn, tid), "default")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="111")
        kb.add_notify_sub(conn, task_id=tid, platform="api_server", chat_id="origin")
    assert [p["request_id"] for p in decisions.list_pending({"origin"})] == [item["request_id"]]
    with kb.connect_closing() as conn:
        assert kb.archive_task(conn, tid, stop_worker=True)
        assert kb.list_notify_subs(conn, tid) == []
    assert decisions.list_pending({"origin"}) == []
    with pytest.raises(decisions.KanbanDecisionConflict, match="отменено"):
        decisions.resolve(item["request_id"], "once", source_session_id="origin", answer="Завтра")
    with kb.connect_closing() as conn:
        assert kb.get_task(conn, tid).status == "archived"
        assert kb.claim_task(conn, tid, claimer="worker") is None


def test_finished_card_keeps_subscriptions_when_archived(home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="Готово", assignee="lawyer", session_id="origin")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="111")
        kb.claim_task(conn, tid, claimer="worker")
        kb.complete_task(conn, tid, result="ok", as_worker=True)
        assert kb.archive_task(conn, tid)
        assert len(kb.list_notify_subs(conn, tid)) == 1
