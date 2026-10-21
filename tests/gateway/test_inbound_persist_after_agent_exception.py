"""QA 17-18.10: when the agent raises and saving the inbound user message
fails, the native store retains it for retry; unexpected failures log at ERROR."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.gateway.test_42039_duplicate_user_message import _bootstrap, _event, _source

_KEY = "agent:main:telegram:group:-1001:12345"


@pytest.mark.asyncio
async def test_real_store_queues_failed_insert_and_flushes_once(monkeypatch, tmp_path):
    import sqlite3

    from gateway.session import SessionStore
    from korra_state import SessionDB

    runner = _bootstrap(monkeypatch, tmp_path)
    runner._run_agent = AsyncMock(side_effect=RuntimeError("client init failed"))
    db = SessionDB(db_path=tmp_path / "transcript.db")
    db.create_session("sess-dedup", "telegram")
    store = SessionStore(tmp_path / "sessions", runner.config)
    store._db = db
    runner.session_store.append_to_transcript = store.append_to_transcript
    runner.session_store.load_transcript = store.load_transcript
    append = db.append_message
    failed = False

    def fail_first_user_insert(*args, **kwargs):
        nonlocal failed
        if kwargs.get("role") == "user" and not failed:
            failed = True
            raise sqlite3.OperationalError("database is locked")
        return append(*args, **kwargs)

    monkeypatch.setattr(db, "append_message", fail_first_user_insert)
    try:
        await runner._handle_message_with_agent(_event(), _source(), _KEY, 1)
        assert failed
        assert len(store._dirty_transcripts["sess-dedup"]) == 1
        assert db.get_messages("sess-dedup") == []
        # The existing store drains its backlog on the next write.
        store.append_to_transcript("sess-dedup", {"role": "assistant", "content": "recovered"})
        rows = db.get_messages("sess-dedup")
        assert [(row["role"], row["content"]) for row in rows] == [
            ("user", "hello world"), ("assistant", "recovered"),
        ]
        assert "sess-dedup" not in store._dirty_transcripts
    finally:
        db.close()


@pytest.mark.asyncio
async def test_persistent_persist_failure_logs_error(monkeypatch, tmp_path, caplog):
    runner = _bootstrap(monkeypatch, tmp_path)
    runner._run_agent = AsyncMock(side_effect=RuntimeError("client init failed"))
    runner.session_store.append_to_transcript = MagicMock(side_effect=OSError("disk full"))

    with caplog.at_level(logging.DEBUG, logger="gateway.run"):
        await runner._handle_message_with_agent(_event(), _source(), _KEY, 1)

    records = [r for r in caplog.records
               if "persist inbound user message" in r.getMessage()]
    assert records and records[-1].levelno == logging.ERROR
