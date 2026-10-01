"""Owner chat, Telegram and board share a version and one SQLite transition."""
from concurrent.futures import ThreadPoolExecutor

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
    db.create_session("other", source="api_server")
    db.create_session("worker", source="kanban")
    db.close()
    kb.init_db()
    return tmp_path


def question(kind="needs_input", board="default"):
    with kb.connect_closing(board=board) as conn:
        tid = kb.create_task(conn, title="Проверить договор", assignee="lawyer", session_id="origin")
        kb.claim_task(conn, tid, claimer="worker")
        kb.block_task(conn, tid, kind=kind, reason="Разрешить отправку клиенту?" if kind == "approval" else "Какой срок?")
        return tid, decisions.project_task(conn, kb.get_task(conn, tid), board)


@pytest.mark.parametrize("kind", ["needs_input", "approval"])
def test_pending_origin_and_answer_survive_restart(home, kind):
    tid, item = question(kind)
    pending = decisions.list_pending(decisions.session_ids("origin"))
    assert [p["request_id"] for p in pending] == [item["request_id"]]
    assert "Проверить договор" in item["command"] and f"task={tid}" in item["task_url"]
    assert decisions.list_pending(decisions.session_ids("other")) == []
    assert decisions.list_pending(decisions.session_ids("worker")) == []
    out = decisions.resolve(item["request_id"], "once", source_session_id="origin", answer="Завтра")
    assert out["status"] == "ready"
    assert decisions.list_pending(decisions.session_ids("origin")) == []
    # A retry after a lost HTTP reply returns the durable first transition.
    assert decisions.resolve(item["request_id"], "once", source_session_id="origin", answer="Завтра")["duplicate"]
    with kb.connect_closing() as conn:
        comments = kb.list_comments(conn, tid)
        assert len(comments) == 1 and "Завтра" in comments[0].body
        assert len([e for e in kb.list_events(conn, tid) if e.kind == "owner_responded"]) == 1


def test_two_channels_race_with_opposite_verdicts(home):
    tid, item = question("approval")
    def respond(choice):
        try:
            return decisions.resolve(item["request_id"], choice, source_session_id="origin")
        except decisions.KanbanDecisionConflict:
            return None
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(respond, ["once", "deny"]))
    assert sum(o is not None for o in outcomes) == 1
    with kb.connect_closing() as conn:
        assert len(kb.list_comments(conn, tid)) == 1
        assert len([e for e in kb.list_events(conn, tid) if e.kind in {"owner_granted", "owner_denied"}]) == 1
        assert kb.claim_task(conn, tid, claimer="first") is not None
        assert kb.claim_task(conn, tid, claimer="second") is None


def test_stale_question_cannot_unblock_new_version(home):
    tid, old = question()
    decisions.resolve(old["request_id"], "once", source_session_id="origin", answer="Завтра")
    with kb.connect_closing() as conn:
        kb.claim_task(conn, tid, claimer="worker")
        kb.block_task(conn, tid, kind="needs_input", reason="Какой адрес?")
    # A retry succeeds idempotently for the old version, without touching new work.
    assert decisions.resolve(old["request_id"], "once", source_session_id="origin")["duplicate"]
    with kb.connect_closing() as conn:
        assert kb.get_task(conn, tid).status in {"blocked", "triage"}
        assert len(kb.list_comments(conn, tid)) == 1
    with pytest.raises(decisions.KanbanDecisionConflict):
        decisions.resolve(old["request_id"], "deny", source_session_id="origin")


def test_unrelated_or_worker_chat_cannot_decide(home):
    _, item = question("approval")
    for sid in ("other", "worker", "missing"):
        with pytest.raises(decisions.KanbanDecisionConflict):
            decisions.resolve(item["request_id"], "once", source_session_id=sid)


def test_result_acceptance_and_rework_share_board_version(home):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="Договор", assignee="lawyer", session_id="origin", acceptance="owner")
        kb.claim_task(conn, tid, claimer="worker")
        kb.complete_task(conn, tid, result="Версия договора", as_worker=True)
        item = decisions.project_task(conn, kb.get_task(conn, tid), "default")
    with pytest.raises(decisions.KanbanDecisionConflict):
        decisions.resolve(item["request_id"], "deny", source_session_id="origin")
    decisions.resolve(item["request_id"], "deny", source_session_id="origin", answer="Добавить срок")
    with kb.connect_closing() as conn:
        assert kb.get_task(conn, tid).status == "ready"
        assert kb.list_comments(conn, tid)[-1].body == "Добавить срок"
    with pytest.raises(decisions.KanbanDecisionConflict):
        decisions.resolve(item["request_id"], "once", source_session_id="origin")


def test_named_board_and_waiting_tab_projection(home, monkeypatch):
    from korra_cli import chat_activity
    kb.create_board("contracts")
    tid, item = question(board="contracts")
    monkeypatch.setattr(chat_activity, "_profile_targets", lambda _: [("", home)])
    runs = chat_activity.project_chat_activity([], profile=None, session_id=None)
    assert len(runs) == 1
    assert (runs[0]["session_id"], runs[0]["status"]) == ("origin", "waiting_decision")
    assert runs[0]["updated_at"] == item["timestamp"]
    assert decisions.resolve(item["request_id"], "once", source_session_id="origin")["ok"]
    with kb.connect_closing(board="contracts") as conn:
        assert kb.get_task(conn, tid).status == "ready"


def test_question_older_than_history_page_is_still_waiting(home, monkeypatch):
    from korra_cli import chat_activity
    _, item = question()
    db = SessionDB(home / "state.db")
    for i in range(110):
        db.create_session(f"new-{i}", source="api_server")
    db.close()
    monkeypatch.setattr(chat_activity, "_profile_targets", lambda _: [("", home)])
    runs = chat_activity.project_chat_activity([], profile=None, session_id=None)
    assert [(r["session_id"], r["status"]) for r in runs] == [("origin", "waiting_decision")]


def test_polling_does_not_create_missing_board(home):
    (home / "kanban.db").unlink()
    assert decisions.list_pending() == []
    assert not (home / "kanban.db").exists()


def test_new_telegram_owner_catches_up_only_current_question(home, monkeypatch):
    monkeypatch.setattr("gateway.credential_management.installation_owners", lambda _: frozenset())
    tid, item = question()
    config = {"gateway": {"credential_management": {"owners": {"telegram": ["111", "*"]}}}}
    with kb.connect_closing() as conn:
        decisions.subscribe_telegram_owners(conn, tid, "default", config=config, catch_up_current=True)
        sub = kb.list_notify_subs(conn)[0]
        assert (sub["chat_id"], sub["delivery_mode"]) == ("111", "notify")
        assert sub["last_event_id"] == item["board_version"] - 1
        kb.advance_notify_cursor(conn, task_id=tid, platform="telegram", chat_id="111", new_cursor=item["board_version"])
        decisions.subscribe_telegram_owners(conn, tid, "default", config=config, catch_up_current=True)
        assert kb.list_notify_subs(conn)[0]["last_event_id"] == item["board_version"]


def test_compressed_owner_chat_can_answer_but_an_explicit_fork_cannot(home):
    _, item = question()
    db = SessionDB(home / "state.db")
    db.end_session("origin", "compression")
    db.create_session("compressed", source="api_server", parent_session_id="origin")
    db.create_session("fork", source="api_server", parent_session_id="origin", model_config={"_branched_from": "origin"})
    db.close()
    assert decisions.list_pending(decisions.session_ids("compressed"))[0]["request_id"] == item["request_id"]
    assert decisions.list_pending(decisions.session_ids("fork")) == []
    with pytest.raises(decisions.KanbanDecisionConflict):
        decisions.resolve(item["request_id"], "once", source_session_id="fork")
    assert decisions.resolve(item["request_id"], "once", source_session_id="compressed")["ok"]
