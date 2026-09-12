"""A Telegram video note (кружок) reaches the agent the way a voice message does.

A video note is a voice message with a face: the owner speaks into it, they do
not send a video file. Until 12.09.2026 the media filter admitted
``PHOTO|VIDEO|AUDIO|VOICE|Document.ALL|Sticker.ALL`` only, so the update reached
no handler and the кружок vanished — no reply, no error, not even a note to the
agent (that half is pinned in ``tests/test_telegram_video_note_ptb.py``, where
the real PTB filters can judge a real update).

Here: once the handler does see it, the file is cached and handed over on the
voice path, so the gateway transcribes its soundtrack instead of dropping it.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageType
from plugins.platforms.telegram.adapter import TelegramAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file_obj(data: bytes = b"circle-bytes"):
    f = AsyncMock()
    f.download_as_bytearray = AsyncMock(return_value=bytearray(data))
    f.file_path = "video_notes/file_7.mp4"
    return f


def _make_video_note(file_size: int = 2048, file_obj=None):
    note = MagicMock()
    note.file_size = file_size
    note.duration = 6
    note.get_file = AsyncMock(return_value=file_obj or _make_file_obj())
    return note


def _make_message(video_note=None, caption=None):
    msg = MagicMock()
    msg.message_id = 77
    msg.text = ""
    msg.caption = caption
    msg.date = None
    msg.photo = None
    msg.video = None
    msg.video_note = video_note
    msg.audio = None
    msg.voice = None
    msg.sticker = None
    msg.document = None
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
    update.update_id = 11
    return update


@pytest.fixture()
def adapter():
    a = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    a.handle_message = AsyncMock()
    a._is_callback_user_authorized = lambda user_id, **_kw: True
    return a


@pytest.fixture(autouse=True)
def _redirect_cache(tmp_path, monkeypatch):
    """Keep cached circles out of ~/.hermes."""
    monkeypatch.setattr(
        "gateway.platforms.base.VIDEO_CACHE_DIR", tmp_path / "video_cache"
    )
    monkeypatch.setattr(
        "gateway.platforms.base.AUDIO_CACHE_DIR", tmp_path / "audio_cache"
    )


# ---------------------------------------------------------------------------
# The circle travels the voice path: cached file + STT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_video_note_is_cached_and_goes_the_voice_way(adapter):
    from gateway.run import _event_media_is_stt_input

    msg = _make_message(video_note=_make_video_note(), caption="послушай")

    await adapter._handle_media_message(_make_update(msg), MagicMock())

    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.call_args[0][0]
    assert event.message_type is MessageType.VOICE
    assert len(event.media_urls) == 1
    assert event.media_urls[0].endswith(".mp4")
    # The gateway decides STT from the event alone, so the event itself has to
    # qualify — otherwise the soundtrack is never transcribed.
    assert _event_media_is_stt_input(event, 0)
    # The owner's caption survives, and the agent is told this was a circle.
    assert "послушай" in event.text
    assert "кружок" in event.text.lower()
    assert event.media_urls[0] in event.text


@pytest.mark.asyncio
async def test_oversized_video_note_is_not_downloaded(adapter):
    adapter._max_doc_bytes = 1024
    note = _make_video_note(file_size=4096)
    note.get_file = AsyncMock(side_effect=AssertionError("кружок сверх лимита не качаем"))

    await adapter._handle_media_message(_make_update(_make_message(video_note=note)), MagicMock())

    event = adapter.handle_message.call_args[0][0]
    assert event.media_urls == []
    assert "exceeds" in event.text


@pytest.mark.asyncio
async def test_failed_video_note_download_is_surfaced(adapter):
    note = _make_video_note()
    note.get_file = AsyncMock(side_effect=RuntimeError("Telegram CDN down"))
    msg = _make_message(video_note=note)

    await adapter._handle_media_message(_make_update(msg), MagicMock())

    msg.reply_text.assert_awaited_once()
    assert "кружок" in msg.reply_text.await_args.args[0].lower()
    event = adapter.handle_message.call_args[0][0]
    assert event.media_urls == []
    assert "could not be downloaded" in (event.text or "")
