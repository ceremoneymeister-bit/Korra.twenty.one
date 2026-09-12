"""A failed media download is explained to the owner, not spelled at them.

When the contour could not fetch an attachment, the reply carried the name of
the Python exception: «⚠️ Не удалось скачать голосовое сообщение
(InvalidToken). Попробуйте отправить его ещё раз.» For three installations
that string was the only thing the owner saw for nine days — it names nothing
the owner can act on, and it reads like a token problem when the file simply
was not readable on the server. The class name belongs in the log, where it is
diagnostic.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource
from gateway.config import Platform
from plugins.platforms.telegram.adapter import TelegramAdapter


class InvalidToken(Exception):
    """Stands in for telegram.error.InvalidToken, which PTB raises on a 404."""


@pytest.fixture()
def adapter():
    return TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))


def _event() -> MessageEvent:
    return MessageEvent(
        text="расшифруй",
        message_type=MessageType.VOICE,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="100", chat_type="dm"),
    )


@pytest.mark.asyncio
async def test_owner_is_told_the_file_is_unavailable_not_the_exception(adapter, caplog):
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    event = _event()

    with caplog.at_level(logging.INFO):
        await adapter._surface_media_cache_failure(
            msg, event, "voice message", InvalidToken("Not Found"),
        )

    reply = msg.reply_text.await_args.args[0]
    assert "голосовое сообщение" in reply
    assert "файл недоступен на сервере" in reply
    assert "InvalidToken" not in reply
    # The agent must not repeat the class name to the owner either.
    assert "InvalidToken" not in event.text
    assert "could not be downloaded" in event.text
    assert "расшифруй" in event.text
    # …and the name stays available where it is diagnostic.
    assert any("InvalidToken" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_named_attachment_is_still_named(adapter):
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    event = _event()

    await adapter._surface_media_cache_failure(
        msg, event, "document", InvalidToken("Not Found"), display_name="смета.pdf",
    )

    assert "смета.pdf" in msg.reply_text.await_args.args[0]
    assert "смета.pdf" in event.text
