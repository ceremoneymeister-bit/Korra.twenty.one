"""Telegram board answers use existing approval buttons, with exact owner/version."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter
from korra_cli import kanban_db as kb
from korra_cli.kanban_decisions import project_task
from korra_state import SessionDB


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("KORRA_KANBAN_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text('gateway:\n  credential_management:\n    owners:\n      telegram: ["111"]\n')
    db = SessionDB(tmp_path / "state.db")
    db.create_session("origin", source="api_server")
    db.close()
    kb.init_db()
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake"))
    adapter._bot = AsyncMock()
    adapter._bot.send_message.return_value = SimpleNamespace(message_id=42)
    monkeypatch.setattr(adapter, "_is_callback_user_authorized", lambda *a, **kw: True)
    return adapter


async def send_question(adapter, kind="approval"):
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="Договор", assignee="lawyer", session_id="origin")
        kb.claim_task(conn, tid, claimer="worker")
        kb.block_task(conn, tid, kind=kind, reason="Какой срок?")
        item = project_task(conn, kb.get_task(conn, tid), "default")
    result = await adapter.send_exec_approval(
        "111", item["command"], "origin", description="Ждёт вас. Ответьте на это сообщение или используйте кнопки.",
        metadata={**item, "owner_id": "111"}, allow_session=False, allow_permanent=False,
    )
    assert result.success
    return tid, item, next(iter(adapter._approval_state))


def callback(approval_id, user="111", choice="once", chat="111"):
    query = SimpleNamespace(
        data=f"ea:{choice}:{approval_id}", from_user=SimpleNamespace(id=user, first_name="Owner"),
        message=SimpleNamespace(chat_id=chat, chat=SimpleNamespace(type="private"), message_thread_id=None),
        answer=AsyncMock(), edit_message_reply_markup=AsyncMock(), edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(callback_query=query), query


@pytest.mark.asyncio
async def test_authorized_visitor_is_not_owner_and_cannot_consume_button(setup):
    adapter = setup
    tid, _, approval_id = await send_question(adapter)
    update, query = callback(approval_id, user="222")
    await adapter._handle_callback_query(update, None)
    assert "Только владелец" in query.answer.call_args.kwargs["text"]
    assert approval_id in adapter._approval_state
    with kb.connect_closing() as conn:
        assert kb.get_task(conn, tid).status == "blocked"
        assert kb.list_comments(conn, tid) == []
    update, query = callback(approval_id)
    await adapter._handle_callback_query(update, None)
    with kb.connect_closing() as conn:
        assert kb.get_task(conn, tid).status == "ready"
        assert len(kb.list_comments(conn, tid)) == 1
    await adapter._handle_callback_query(update, None)
    with kb.connect_closing() as conn:
        assert len(kb.list_comments(conn, tid)) == 1


@pytest.mark.asyncio
async def test_reply_closes_same_question_once_and_never_calls_model(setup):
    adapter = setup
    tid, _, _ = await send_question(adapter, "needs_input")
    msg = SimpleNamespace(
        chat_id="111", chat=SimpleNamespace(type="private"), from_user=SimpleNamespace(id="111"),
        reply_to_message=SimpleNamespace(message_id=42), text="До пятницы", reply_text=AsyncMock(),
    )
    assert await adapter._answer_kanban_reply(msg)
    assert await adapter._answer_kanban_reply(msg)
    with kb.connect_closing() as conn:
        assert [c.body for c in kb.list_comments(conn, tid)] == ["До пятницы"]
        assert kb.get_task(conn, tid).status == "ready"


@pytest.mark.asyncio
async def test_reply_to_pre_restart_prompt_is_not_relayed_as_new_user_text(setup):
    msg = SimpleNamespace(
        chat_id="111", reply_to_message=SimpleNamespace(message_id=42,
            from_user=SimpleNamespace(is_bot=True), text="Ждёт вас. Ответьте на это сообщение или используйте кнопки."),
        text="Да", reply_text=AsyncMock(),
    )
    assert await setup._answer_kanban_reply(msg)
    assert "После перезапуска" in msg.reply_text.call_args.args[0]


@pytest.mark.asyncio
async def test_keyboard_counter_does_not_reuse_ids_after_restart(setup):
    await send_question(setup)
    other = TelegramAdapter(PlatformConfig(enabled=True, token="fake"))
    other._bot = AsyncMock()
    other._bot.send_message.return_value = SimpleNamespace(message_id=44)
    await other.send_exec_approval("111", "test", "other")
    assert set(setup._approval_state).isdisjoint(other._approval_state)


def test_secondary_profile_verifies_its_owner_config(setup, tmp_path, monkeypatch):
    from korra_constants import get_hermes_home
    profile_home = tmp_path / "secondary"
    profile_home.mkdir()
    (profile_home / "config.yaml").write_text('gateway:\n  credential_management:\n    owners:\n      telegram: ["222"]\n')
    monkeypatch.setattr("korra_cli.profiles.resolve_profile_env", lambda _: profile_home)
    monkeypatch.setattr("gateway.credential_management.installation_owner_config", lambda: {})
    setup.set_owner_profile("secondary")
    before = get_hermes_home()
    assert setup._kanban_owner_matches({"owner_id": "222", "chat_id": "222"}, "222", "222", "private")
    assert not setup._kanban_owner_matches({"owner_id": "111", "chat_id": "111"}, "111", "111", "private")
    assert get_hermes_home() == before


@pytest.mark.asyncio
async def test_existing_effect_buttons_resolve_exact_request(setup, monkeypatch):
    from unittest.mock import Mock
    resolver = Mock(return_value=1)
    monkeypatch.setattr("tools.approval.resolve_gateway_approval", resolver)
    await setup.send_exec_approval("111", "Сообщение клиенту", "origin", metadata={"request_id": "effect_exact"})
    approval_id = next(iter(setup._approval_state))
    update, _ = callback(approval_id)
    await setup._handle_callback_query(update, None)
    resolver.assert_called_once_with("origin", "once", request_id="effect_exact")


@pytest.mark.asyncio
async def test_capability_question_has_no_continue_as_proposed_button(setup, monkeypatch):
    import plugins.platforms.telegram.adapter as telegram
    monkeypatch.setattr(telegram, "InlineKeyboardButton", lambda text, **kw: text)
    monkeypatch.setattr(telegram, "InlineKeyboardMarkup", lambda rows: rows)
    adapter = setup
    await send_question(adapter, kind="capability")
    assert adapter._bot.send_message.call_args.kwargs["reply_markup"] is None
    adapter._bot.send_message.reset_mock()
    await send_question(adapter, kind="needs_input")
    assert adapter._bot.send_message.call_args.kwargs["reply_markup"] == [["Продолжить как предложено"]]
