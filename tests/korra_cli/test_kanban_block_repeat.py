"""A card blocked again for the same reason, with nothing new from the owner,
stays parked under the question already asked (K21-298)."""
import pytest

from korra_cli import kanban_db as kb
from korra_cli import kanban_decisions as decisions
from korra_state import SessionDB

ASKED = "Укажите, на какой адрес отправлять договор клиенту"
REWORDED = "На какой адрес нужно отправить договор клиента? Укажите адрес"
NOTIFYING = {"blocked", "block_loop_detected"}


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


def _rerun(conn, tid):
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
    assert kb.claim_task(conn, tid, claimer="worker") is not None


def _kinds(conn, tid):
    return [e.kind for e in kb.list_events(conn, tid)]


def test_repeated_and_reworded_blocks_notify_once_and_start_no_run(home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="Договор", assignee="lawyer", session_id="origin")
        kb.claim_task(conn, tid, claimer="worker")
        kb.block_task(conn, tid, kind="needs_input", reason=ASKED)
        asked = kb.block_revision(conn, tid)
        assert kb.unblock_task(conn, tid)  # a cron lets it go with no news
        _rerun(conn, tid)
        assert kb.block_task(conn, tid, kind="needs_input", reason=ASKED)
        _rerun(conn, tid)
        assert kb.block_task(conn, tid, kind="needs_input", reason=ASKED)
        _rerun(conn, tid)
        assert kb.block_task(conn, tid, kind="needs_input", reason=REWORDED)
        task = kb.get_task(conn, tid)
        assert task.status == "triage"
        assert len([k for k in _kinds(conn, tid) if k in NOTIFYING]) == 1
        assert kb.block_revision(conn, tid) == asked
        assert not kb.unblock_task(conn, tid)
        assert kb.claim_task(conn, tid, claimer="worker") is None


def test_real_answer_is_news_and_resumes_a_repeated_question(home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="Договор", assignee="lawyer", session_id="origin")
        kb.claim_task(conn, tid, claimer="worker")
        kb.block_task(conn, tid, kind="needs_input", reason=ASKED)
        asked = kb.block_revision(conn, tid)
        kb.unblock_task(conn, tid)
        _rerun(conn, tid)
        kb.block_task(conn, tid, kind="needs_input", reason=ASKED)
        out = kb.respond_to_block(conn, tid, answer="ул. Ленина, 1", author="Владелец",
                                  request_id="r-1", revision=asked)
        assert out["ok"] and out["status"] == "ready"


def test_capability_repeat_needs_words_not_a_bare_continue(home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="Группа", assignee="lawyer", session_id="origin")
        kb.claim_task(conn, tid, claimer="worker")
        kb.block_task(conn, tid, kind="capability", reason="Нет доступа к личному Telegram")
        item = decisions.project_task(conn, kb.get_task(conn, tid), "default")
    assert item["choices"] == []
    with pytest.raises(decisions.KanbanDecisionConflict, match="Напишите"):
        decisions.resolve(item["request_id"], "once", source_session_id="origin")
    with kb.connect_closing() as conn:
        task = kb.get_task(conn, tid)
        assert task.status == "blocked" and kb.list_comments(conn, tid) == []
        assert kb.claim_task(conn, tid, claimer="worker") is None
    out = decisions.resolve(item["request_id"], "once", source_session_id="origin", answer="Подключил, попробуйте")
    assert out["status"] == "ready"


def test_capability_asked_differently_without_news_is_parked(home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="Группа", assignee="lawyer", session_id="origin")
        kb.claim_task(conn, tid, claimer="worker")
        kb.block_task(conn, tid, kind="capability", reason="Нет доступа к личному Telegram")
        kb.unblock_task(conn, tid)
        _rerun(conn, tid)
        kb.block_task(conn, tid, kind="capability", reason="Нужно подключение аккаунта")
        assert kb.get_task(conn, tid).status == "triage"
        assert [k for k in _kinds(conn, tid) if k in NOTIFYING] == ["blocked"]
        assert decisions.project_task(conn, kb.get_task(conn, tid), "default")["choices"] == []
