"""K21-276: an unanswered request survives the next turn's clean-text persist override."""

from agent.agent_runtime_helpers import repair_message_sequence
from korra_state import SessionDB

OLD = "please deploy build 42 to staging"
NEW = "can you also run the smoke tests"
MERGED = f"{OLD}\n\n{NEW}"


def _agent(db=None, sid=None):
    from run_agent import AIAgent

    agent = AIAgent(
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
        session_db=db,
        session_id=sid,
    )
    agent._session_db_created = True
    agent._persist_user_message_idx = 1
    agent._persist_user_message_override = NEW
    agent._persist_user_message_timestamp = None
    return agent


def _repaired_turn():
    messages = [
        {"role": "assistant", "content": "previous response"},
        {"role": "user", "content": OLD},
        {"role": "user", "content": f"[03:00] {NEW}"},
    ]
    assert repair_message_sequence(None, messages) == 1
    assert len(messages) == 2
    return messages


def test_in_memory_override_keeps_unanswered_request():
    messages = _repaired_turn()
    _agent()._apply_persist_user_message_override(messages)
    assert messages[1]["content"] == MERGED


def test_db_flush_keeps_unanswered_request_and_wire_sidecar(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="s", source="cli")
    try:
        messages = _repaired_turn()
        wire = messages[1]["content"]
        _agent(db, "s")._flush_messages_to_session_db(messages, None)

        row = db.get_messages_as_conversation("s")[-1]
        assert row["content"] == MERGED
        assert row["api_content"] == wire
        assert "_merged_turn_prefix" not in row
    finally:
        db.close()


def test_flush_after_in_memory_override_is_stable(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="s", source="cli")
    try:
        messages = _repaired_turn()
        agent = _agent(db, "s")
        agent._apply_persist_user_message_override(messages)
        agent._flush_messages_to_session_db(messages, None)
        assert db.get_messages_as_conversation("s")[-1]["content"] == MERGED
    finally:
        db.close()


def test_turn_without_unanswered_request_is_replaced_whole():
    messages = [{"role": "assistant", "content": "ok"}, {"role": "user", "content": f"[03:00] {NEW}"}]
    _agent()._apply_persist_user_message_override(messages)
    assert messages[1]["content"] == NEW


def test_marker_is_not_sent_to_the_provider():
    from agent.transports.chat_completions import ChatCompletionsTransport

    messages = _repaired_turn()
    sent = ChatCompletionsTransport().convert_messages(messages)
    assert "_merged_turn_prefix" not in sent[1]
    assert sent[1]["content"] == messages[1]["content"]
