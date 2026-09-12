"""Admission of Telegram updates, judged by the installed PTB runtime.

Two things can only be checked against the real ``telegram.ext.filters``: that
a video note (кружок) now matches the media handler at all, and that an update
no handler claims leaves a line in the log instead of vanishing. Both were
silent before 12.09.2026 — the кружок gave the owner nothing back, and nobody
could have noticed without a complaint.

The gateway test suite replaces the ``telegram`` package with a mock, so this
file lives at the root, next to ``test_telegram_polling_progress_ptb.py``,
where the real library is the one under test.
"""

import datetime
import sys

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")
if not hasattr(sys.modules["telegram"], "__file__"):
    # tests/gateway/conftest.py replaces the package with a mock for the whole
    # process when it is collected first. scripts/run_tests.sh spawns a pytest
    # per file, so this file still runs for real there; inside one shared
    # process a mocked PTB would judge nothing.
    pytest.skip(
        "`telegram` is mocked in this process — run this file on its own",
        allow_module_level=True,
    )
from telegram import Chat, Dice, Message, Update, User, VideoNote  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

from gateway.config import PlatformConfig  # noqa: E402
from plugins.platforms.telegram.adapter import TelegramAdapter  # noqa: E402


def _message(**payload) -> Message:
    return Message(
        message_id=payload.pop("message_id", 77),
        date=datetime.datetime.now(datetime.timezone.utc),
        chat=Chat(id=100, type="private"),
        from_user=User(id=1, first_name="Test", is_bot=False),
        **payload,
    )


def _video_note_update() -> Update:
    return Update(
        update_id=1,
        message=_message(
            video_note=VideoNote(
                file_id="fid", file_unique_id="uid", length=240, duration=6,
            ),
        ),
    )


def _dice_update() -> Update:
    return Update(update_id=2, message=_message(message_id=78, dice=Dice(value=4, emoji="🎲")))


def _text_update() -> Update:
    return Update(update_id=3, message=_message(message_id=79, text="привет"))


@pytest.fixture()
def adapter() -> TelegramAdapter:
    a = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    a._register_handlers(MagicMock())
    return a


def test_video_note_reaches_the_media_handler(adapter):
    matched = [h for h in adapter._core_handlers if h.check_update(_video_note_update())]
    assert any(
        getattr(h, "callback", None) == adapter._handle_media_message for h in matched
    ), "видео-кружок не доходит ни до одного хендлера медиа"


def test_voice_and_photo_still_reach_the_same_handler(adapter):
    from telegram import PhotoSize, Voice

    for payload in (
        {"voice": Voice(file_id="v", file_unique_id="vu", duration=3)},
        {"photo": (PhotoSize(file_id="p", file_unique_id="pu", width=1, height=1),)},
    ):
        update = Update(update_id=4, message=_message(**payload))
        matched = [h for h in adapter._core_handlers if h.check_update(update)]
        assert any(
            getattr(h, "callback", None) == adapter._handle_media_message for h in matched
        ), payload


@pytest.mark.asyncio
async def test_unhandled_update_kind_is_logged_once(adapter, caplog):
    with caplog.at_level("INFO"):
        await adapter._on_platform_update(_dice_update(), MagicMock())
        await adapter._on_platform_update(_dice_update(), MagicMock())

    lines = [r.getMessage() for r in caplog.records if "Unhandled update" in r.getMessage()]
    assert len(lines) == 1, lines
    assert "kind=dice" in lines[0]


@pytest.mark.asyncio
async def test_handled_update_kinds_stay_out_of_the_log(adapter, caplog):
    with caplog.at_level("INFO"):
        await adapter._on_platform_update(_text_update(), MagicMock())
        await adapter._on_platform_update(_video_note_update(), MagicMock())

    assert not [r for r in caplog.records if "Unhandled update" in r.getMessage()]
