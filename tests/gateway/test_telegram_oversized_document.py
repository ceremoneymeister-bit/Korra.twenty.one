"""An oversized Telegram document must not swallow the owner's caption.

Over the size cap the handler used to *replace* ``event.text`` with the English
sentence "The document is too large or its size could not be verified." — the
caption the owner had typed disappeared and the agent answered the system
sentence as if the owner had written it. The note belongs next to the caption,
in the language the contour speaks.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


def _make_document(file_size, file_name="смета.pdf", mime_type="application/pdf"):
    doc = MagicMock()
    doc.file_name = file_name
    doc.mime_type = mime_type
    doc.file_size = file_size
    doc.get_file = AsyncMock(side_effect=AssertionError("файл сверх лимита не качаем"))
    return doc


def _make_message(document=None, caption=None):
    msg = MagicMock()
    msg.message_id = 42
    msg.text = caption or ""
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
    msg.reply_text = AsyncMock()
    return msg


def _make_update(msg):
    update = MagicMock()
    update.message = msg
    update.update_id = 5
    return update


@pytest.fixture()
def adapter():
    a = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    a.handle_message = AsyncMock()
    a._is_callback_user_authorized = lambda user_id, **_kw: True
    return a


@pytest.mark.asyncio
async def test_oversized_document_keeps_the_caption_and_speaks_russian(adapter):
    doc = _make_document(file_size=adapter._max_doc_bytes + 1)
    msg = _make_message(document=doc, caption="посчитай по этой смете")

    await adapter._handle_media_message(_make_update(msg), MagicMock())

    event = adapter.handle_message.call_args[0][0]
    assert "посчитай по этой смете" in event.text
    assert "The document is too large" not in event.text
    assert "слишком большой" in event.text
    assert "20 МБ" in event.text
    assert event.media_urls == []


@pytest.mark.asyncio
async def test_document_of_unverifiable_size_is_reported_the_same_way(adapter):
    msg = _make_message(document=_make_document(file_size=None), caption="вот файл")

    await adapter._handle_media_message(_make_update(msg), MagicMock())

    event = adapter.handle_message.call_args[0][0]
    assert "вот файл" in event.text
    assert "слишком большой" in event.text
