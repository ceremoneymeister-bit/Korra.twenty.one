"""A Telegram Reply to a scheduled result reaches the stored execution.

Regression for 0.21.15 review P1-A: the native adapter never set
``reply_to_is_own_message``, so ``cron.result_links.reply_context`` dropped
every real reply. These tests start from a Telegram message object and go
through ``_build_message_event``, not from a hand-built event.
"""
from types import SimpleNamespace as NS

import pytest

from cron import executions, result_links as links
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageType

BOT_ID = 4242
OWNER_ID = 555


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(executions, "EXECUTIONS_FILE", tmp_path / "executions.db")
    return executions.EXECUTIONS_FILE


@pytest.fixture
def adapter(monkeypatch):
    from plugins.platforms.telegram.adapter import TelegramAdapter

    config = PlatformConfig(enabled=True, token="test-token", extra={})
    instance = TelegramAdapter(config)
    instance._bot = NS(id=BOT_ID)
    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: NS(platforms={Platform.TELEGRAM: config}),
    )
    return instance


def _message(*, reply_author, chat_id=OWNER_ID, chat_type="private", sender=OWNER_ID,
             thread_id=None, is_forum=False):
    chat = NS(id=chat_id, type=chat_type, title=None, full_name="Марина", is_forum=is_forum)
    replied = NS(message_id=10, from_user=NS(id=reply_author, is_bot=reply_author == BOT_ID),
                 text="Отчёт за 28 сентября", caption=None)
    return NS(
        chat=chat,
        from_user=NS(id=sender, is_bot=False, full_name="Марина", first_name="Марина",
                     username="marina"),
        text="перенеси на час",
        caption=None,
        entities=[],
        caption_entities=[],
        message_thread_id=thread_id,
        is_topic_message=False,
        message_id=20,
        reply_to_message=replied,
        quote=None,
        date=None,
        forum_topic_created=None,
    )


def _record(adapter, *, chat_id=str(OWNER_ID), thread_id=None, text="Отчёт за 28 сентября",
            origin=None):
    from tools.send_message_tool import _configured_account_identity

    job = {"id": "job-a", "name": "Отчёт по продажам", "execution_id": "run-28",
           "enabled": True, "origin": origin}
    links.record(account=_configured_account_identity("telegram", adapter.config),
                 chat_id=chat_id, thread_id=thread_id, result=links.snapshot(job, text),
                 receipt={"success": True, "message_id": "10"})
    return job


def test_reply_to_own_delivery_sets_flag_and_resolves_execution(adapter, ledger, monkeypatch):
    job = _record(adapter)
    monkeypatch.setattr("cron.jobs.get_job", lambda job_id: job)

    event = adapter._build_message_event(_message(reply_author=BOT_ID), MessageType.TEXT)

    assert event.reply_to_is_own_message is True
    note = links.reply_context(event, event.source)
    assert note is not None and '"execution_id": "run-28"' in note


def test_reply_to_someone_else_is_not_bound(adapter, ledger, monkeypatch):
    job = _record(adapter)
    monkeypatch.setattr("cron.jobs.get_job", lambda job_id: job)

    event = adapter._build_message_event(_message(reply_author=777), MessageType.TEXT)

    assert event.reply_to_is_own_message is False
    assert links.reply_context(event, event.source) is None


def test_forum_general_reply_matches_threadless_delivery(adapter, ledger, monkeypatch):
    group = "-100500"
    job = _record(adapter, chat_id=group, thread_id=None,
                  origin={"platform": "telegram", "user_id": str(OWNER_ID)})
    monkeypatch.setattr("cron.jobs.get_job", lambda job_id: job)

    event = adapter._build_message_event(
        _message(reply_author=BOT_ID, chat_id=int(group), chat_type="supergroup", is_forum=True),
        MessageType.TEXT,
    )

    assert event.source.thread_id == "1"
    assert links.reply_context(event, event.source) is not None


def test_group_member_cannot_bind_someone_elses_result(adapter, ledger, monkeypatch):
    group = "-100500"
    job = _record(adapter, chat_id=group,
                  origin={"platform": "telegram", "user_id": str(OWNER_ID)})
    monkeypatch.setattr("cron.jobs.get_job", lambda job_id: job)
    monkeypatch.setattr("gateway.credential_management.owner_matches", lambda *a, **k: False)

    stranger = adapter._build_message_event(
        _message(reply_author=BOT_ID, chat_id=int(group), chat_type="group", sender=999),
        MessageType.TEXT,
    )
    creator = adapter._build_message_event(
        _message(reply_author=BOT_ID, chat_id=int(group), chat_type="group"),
        MessageType.TEXT,
    )

    assert links.reply_context(stranger, stranger.source) is None
    assert links.reply_context(creator, creator.source) is not None

    monkeypatch.setattr("gateway.credential_management.owner_matches", lambda *a, **k: True)
    assert links.reply_context(stranger, stranger.source) is not None

    monkeypatch.setattr("cron.jobs.get_job", lambda job_id: None)
    assert links.reply_context(creator, creator.source) is None


def test_reply_context_quotes_a_bounded_part_of_the_result(adapter, ledger, monkeypatch):
    job = _record(adapter, text="я" * 10000)
    monkeypatch.setattr("cron.jobs.get_job", lambda job_id: job)

    event = adapter._build_message_event(_message(reply_author=BOT_ID), MessageType.TEXT)
    note = links.reply_context(event, event.source)

    assert note is not None and "я" * 4000 in note and "я" * 4001 not in note
