import sqlite3
import json

import pytest
from korra_state import SessionDB
from korra_cli.session_listing import hide_service_sources


def seed(db, sid="cron_job_run"):
    db.create_session(sid, source="cron")
    db.append_message(sid, "user", "Scheduled instruction")
    db.append_message(sid, "assistant", "Result")


def test_human_turn_saved_atomically_and_visible_without_losing_run(tmp_path):
    with SessionDB(tmp_path / "state.db") as db:
        seed(db)
        seed(db, "cron_job_other")
        original = db.get_messages("cron_job_run")
        db.append_messages_batch("cron_job_run", [{"role": "user", "content": "Обсудим результат"}], human_turn=True)
        assert db.get_session("cron_job_run")["source"] == "cron_discussion"
        assert db.get_messages("cron_job_run")[:len(original)] == original
        from tools.session_search_tool import session_search
        found = json.loads(session_search(query="Обсудим", db=db))
        assert [row["session_id"] for row in found["results"]] == ["cron_job_run"]
        assert db.session_count(exclude_sources=hide_service_sources(None)) == 1
        assert [r["id"] for r in db.list_sessions_rich(exclude_sources=hide_service_sources(None))] == ["cron_job_run"]
        assert {r["id"] for r in db.list_cron_job_runs("job")} == {"cron_job_run", "cron_job_other"}


@pytest.mark.parametrize("message,human", [
    ({"role": "user", "content": "internal", "display_kind": "internal_notification"}, True),
    ({"role": "user", "content": "summary", "_compressed_summary": True}, True),
    ({"role": "user", "content": "Scheduled follow-up"}, False),
    ({"role": "assistant", "content": "Result"}, True),
])
def test_internal_runs_remain_hidden(tmp_path, message, human):
    with SessionDB(tmp_path / "state.db") as db:
        seed(db)
        db.append_messages_batch("cron_job_run", [message], human_turn=human)
        assert db.get_session("cron_job_run")["source"] == "cron"


@pytest.mark.parametrize("tip_source", ["cron", "dashboard"])
def test_compression_lineage_promoted_but_delegates_untouched(tmp_path, tip_source):
    with SessionDB(tmp_path / "state.db") as db:
        seed(db)
        db.end_session("cron_job_run", end_reason="compression")
        db.create_session("tip", source=tip_source, parent_session_id="cron_job_run")
        db.create_session("delegate", source="cron", parent_session_id="cron_job_run",
                          model_config={"_delegate_from": "cron_job_run"})
        db.append_messages_batch("tip", [{"role": "user", "content": "Продолжим"}], human_turn=True)
        assert db.get_session("tip")["source"] == ("cron_discussion" if tip_source == "cron" else tip_source)
        assert db.get_session("cron_job_run")["source"] == "cron_discussion"
        assert db.get_session("delegate")["source"] == "cron"


def test_old_discussion_recovered_once_without_rewriting_text(tmp_path):
    path = tmp_path / "state.db"
    with SessionDB(path) as db:
        seed(db)
        seed(db, "cron_job_pure")
        db.append_message("cron_job_run", "user", "Уточнение человека")
        before = db.get_messages("cron_job_run")
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM state_meta WHERE key='cron_discussions_v1'")
    with SessionDB(path) as db:
        assert db.get_session("cron_job_run")["source"] == "cron_discussion"
        assert db.get_session("cron_job_pure")["source"] == "cron"
        assert db.get_messages("cron_job_run") == before
        db.append_message("cron_job_pure", "user", "Later internal follow-up")
    with SessionDB(path) as db:
        assert db.get_session("cron_job_pure")["source"] == "cron"


def test_actual_agent_flush_promotes_only_interactive_turn(tmp_path, monkeypatch):
    from run_agent import AIAgent
    monkeypatch.setattr("run_agent._session_source_for_agent", lambda platform: platform)
    with SessionDB(tmp_path / "state.db") as db:
        for platform in ("dashboard", "cron", "tool"):
            sid = "cron_job_" + platform
            seed(db, sid)
            agent = AIAgent.__new__(AIAgent)
            agent.platform = platform
            agent.session_id = sid
            agent._session_db = db
            agent._session_db_created = True
            agent._last_flushed_db_idx = 0
            agent._flush_messages_to_session_db([{"role": "user", "content": "Продолжим"}])
            assert db.get_session(sid)["source"] == ("cron_discussion" if platform == "dashboard" else "cron")


def test_promotion_failure_rolls_back_the_human_turn(tmp_path, monkeypatch):
    with SessionDB(tmp_path / "state.db") as db:
        seed(db)
        before = db.get_messages("cron_job_run")
        def fail(*args):
            raise ValueError("test write failure")
        monkeypatch.setattr("cron.session_discussions.promote", fail)
        with pytest.raises(ValueError, match="test write failure"):
            db.append_messages_batch("cron_job_run", [{"role": "user", "content": "Reply"}], human_turn=True)
        assert db.get_messages("cron_job_run") == before
        assert db.get_session("cron_job_run")["source"] == "cron"
