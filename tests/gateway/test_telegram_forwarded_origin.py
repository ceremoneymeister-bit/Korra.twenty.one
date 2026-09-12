"""A forwarded message must not read as the owner's own words.

``_build_message_event`` copied ``message.text`` and never looked at
``forward_origin``: a post the owner forwarded for an opinion arrived as if
they had typed it. The agent then answered its instructions, attributed its
claims to the owner, and — worst for a system that resolves people — filed
someone else's words under the owner's name.

Pinned here: who the text came from, and when, travels with it.
"""

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageType
from plugins.platforms.telegram.adapter import TelegramAdapter


FORWARD_DATE = datetime.datetime(2026, 9, 12, 14, 30, tzinfo=datetime.timezone.utc)


def _make_message(text="", caption=None, document=None, **forward_fields):
    msg = MagicMock()
    msg.message_id = 42
    msg.text = text
    msg.caption = caption
    msg.date = None
    msg.photo = None
    msg.video = None
    msg.video_note = None
    msg.audio = None
    msg.voice = None
    msg.sticker = None
    msg.document = document
    msg.media_group_id = None
    msg.chat = MagicMock()
    msg.chat.id = 100
    msg.chat.type = "private"
    msg.chat.title = None
    msg.chat.full_name = "Test User"
    msg.from_user = MagicMock()
    msg.from_user.id = 1
    msg.from_user.full_name = "Test User"
    msg.message_thread_id = None
    msg.reply_to_message = None
    msg.quote = None
    msg.reply_text = AsyncMock()
    # Absent by default — only the case under test sets a forward field.
    msg.forward_origin = None
    msg.forward_from = None
    msg.forward_from_chat = None
    msg.forward_sender_name = None
    msg.forward_date = None
    for name, value in forward_fields.items():
        setattr(msg, name, value)
    return msg


def _expected_local_time() -> str:
    return FORWARD_DATE.astimezone().strftime("%d.%m.%Y %H:%M")


@pytest.fixture()
def adapter():
    a = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    a.handle_message = AsyncMock()
    a._is_callback_user_authorized = lambda user_id, **_kw: True
    return a


@pytest.fixture(autouse=True)
def _redirect_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gateway.platforms.base.DOCUMENT_CACHE_DIR", tmp_path / "doc_cache"
    )


# ---------------------------------------------------------------------------
# Who wrote it
# ---------------------------------------------------------------------------

def test_forward_from_a_user_is_attributed(adapter):
    msg = _make_message(
        text="приходи в субботу",
        forward_origin=SimpleNamespace(
            type="user",
            date=FORWARD_DATE,
            sender_user=SimpleNamespace(full_name="Иван Петров"),
        ),
    )

    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.text.startswith("[Переслано от Иван Петров, ")
    assert _expected_local_time() in event.text
    assert "приходи в субботу" in event.text


def test_forward_from_a_channel_names_the_channel(adapter):
    msg = _make_message(
        text="важный пост",
        forward_origin=SimpleNamespace(
            type="channel",
            date=FORWARD_DATE,
            chat=SimpleNamespace(title="Наука и жизнь"),
            message_id=9,
        ),
    )

    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert "Наука и жизнь" in event.text
    assert "канала" in event.text
    assert "важный пост" in event.text


def test_hidden_sender_is_named_as_hidden(adapter):
    msg = _make_message(
        text="аноним",
        forward_origin=SimpleNamespace(
            type="hidden_user", date=FORWARD_DATE, sender_user_name="Некто",
        ),
    )

    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert "Некто" in event.text


def test_legacy_forward_fields_still_work(adapter):
    """Telegram kept forward_from for clients older than Bot API 7.0."""
    msg = _make_message(
        text="старый формат",
        forward_from=SimpleNamespace(full_name="Пётр Сидоров"),
        forward_date=FORWARD_DATE,
    )

    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert "Пётр Сидоров" in event.text
    assert "старый формат" in event.text


def test_own_message_is_untouched(adapter):
    event = adapter._build_message_event(_make_message(text="мои слова"), MessageType.TEXT)

    assert event.text == "мои слова"
    assert "Переслано" not in event.text


# ---------------------------------------------------------------------------
# The caption path must not drop the attribution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forwarded_document_keeps_attribution_next_to_its_caption(adapter):
    file_obj = AsyncMock()
    file_obj.download_as_bytearray = AsyncMock(return_value=bytearray(b"Hello"))
    file_obj.file_path = "documents/notes.txt"
    doc = MagicMock()
    doc.file_name = "notes.txt"
    doc.mime_type = "text/plain"
    doc.file_size = 5
    doc.get_file = AsyncMock(return_value=file_obj)

    msg = _make_message(
        caption="перескажи",
        document=doc,
        forward_origin=SimpleNamespace(
            type="channel", date=FORWARD_DATE,
            chat=SimpleNamespace(title="Наука и жизнь"), message_id=9,
        ),
    )
    update = MagicMock()
    update.message = msg
    update.update_id = 7

    await adapter._handle_media_message(update, MagicMock())

    event = adapter.handle_message.call_args[0][0]
    assert "Наука и жизнь" in event.text
    assert "перескажи" in event.text
