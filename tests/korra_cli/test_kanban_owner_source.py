"""An answer from a button or reply in Telegram or the web chat names its source (K21-300)."""
import pytest

from korra_cli import kanban_db as kb
from korra_cli import kanban_decisions as decisions
from korra_state import SessionDB


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("KORRA_KANBAN_HOME", str(tmp_path))
    monkeypatch.setenv("KORRA_TIMEZONE", "UTC")
    import korra_time
    korra_time.reset_cache()
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("KORRA_KANBAN_DB", raising=False)
    db = SessionDB(tmp_path / "state.db")
    db.create_session("origin", source="api_server")
    db.close()
    kb.init_db()
    return tmp_path


def _asked(conn):
    tid = kb.create_task(conn, title="Договор", assignee="lawyer", session_id="origin")
    kb.claim_task(conn, tid, claimer="worker")
    kb.block_task(conn, tid, kind="needs_input", reason="Какой срок?")
    return tid, kb.block_revision(conn, tid)


def test_telegram_reply_signs_the_comment_with_name_platform_and_time(home):
    with kb.connect_closing() as conn:
        tid, revision = _asked(conn)
    request = decisions.decision_id("default", tid, revision, "question")
    decisions.resolve(request, "once", source_session_id="origin", answer="30 дней",
                      allowed_sessions={"origin"},
                      source={"platform": "telegram", "user_name": "Дмитрий", "message_id": "55"})
    with kb.connect_closing() as conn:
        comment = kb.list_comments(conn, tid)[-1]
        event = [e for e in kb.list_events(conn, tid) if e.kind == "owner_responded"][-1]
    assert comment.body == "30 дней"
    assert comment.author.startswith("Дмитрий · Telegram · ")
    assert event.payload["source"]["message_id"] == "55"
    assert event.payload["comment_id"] == comment.id


def test_answer_without_source_keeps_the_plain_author(home):
    with kb.connect_closing() as conn:
        tid, revision = _asked(conn)
    request = decisions.decision_id("default", tid, revision, "question")
    decisions.resolve(request, "once", source_session_id="origin", answer="30 дней",
                      allowed_sessions={"origin"})
    with kb.connect_closing() as conn:
        assert kb.list_comments(conn, tid)[-1].author == "Владелец"
