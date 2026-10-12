"""Длинный ответ из нескольких сообщений при лимите Telegram (flood) и сбое сервера:
начало не дублируется, хвост не теряется."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


class _Flood(Exception):
    def __init__(self, retry_after):
        super().__init__(f"Flood control exceeded. Retry in {retry_after} seconds")
        self.retry_after = retry_after


def _adapter(monkeypatch, script):
    """``script`` — список исходов для вызовов send_message: исключение или None (успех)."""
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    adapter.MAX_MESSAGE_LENGTH = 120
    adapter._should_attempt_rich = lambda *a, **k: False
    sent = []
    outcomes = list(script)

    async def send_message(**kwargs):
        outcome = outcomes.pop(0) if outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        sent.append(kwargs["text"])
        return SimpleNamespace(message_id=100 + len(sent), chat_id=kwargs["chat_id"], message_thread_id=None)

    adapter._bot.send_message = AsyncMock(side_effect=send_message)
    real_sleep = asyncio.sleep

    async def fast_sleep(delay, *a, **k):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    return adapter, sent


_TEXT = " ".join(f"слово{i:03d}" for i in range(60))


@pytest.mark.asyncio
async def test_flood_on_second_chunk_resumes_without_repeating_the_start(monkeypatch):
    # чанк 1 прошёл, чанк 2 отклонён на 7 секунд (дольше внутреннего ожидания), затем всё проходит
    adapter, sent = _adapter(monkeypatch, [None, _Flood(7)])

    result = await adapter._send_with_retry("123", _TEXT)

    assert result.success
    joined = " ".join(sent)
    for i in range(60):
        assert joined.count(f"слово{i:03d}") == 1, f"слово{i:03d}"
    assert len(sent) >= 3


@pytest.mark.asyncio
async def test_single_send_reports_partial_delivery_with_the_rest(monkeypatch):
    adapter, sent = _adapter(monkeypatch, [None, _Flood(7)])

    result = await adapter.send("123", _TEXT)

    assert not result.success
    raw = result.raw_response
    assert raw["partial_overflow"] and raw["delivered_chunks"] == 1
    assert raw["total_chunks"] > 1 and raw["resume_chunks"]
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_ambiguous_timeout_midway_is_not_resumed_or_resent(monkeypatch):
    from telegram.error import TimedOut

    adapter, sent = _adapter(monkeypatch, [None, TimedOut("Timed out")])

    result = await adapter._send_with_retry("123", _TEXT)

    assert not result.success
    # ни повтора начала, ни досылки (сообщение могло дойти); человек получает короткое уведомление
    assert sum("слово000" in text for text in sent) == 1
    assert not any("слово011" in text for text in sent)
    assert any("не полностью" in text for text in sent)
    assert "resume_chunks" not in result.raw_response


@pytest.mark.asyncio
async def test_server_error_on_later_chunk_does_not_duplicate_the_start(monkeypatch):
    from telegram.error import NetworkError

    adapter, sent = _adapter(
        monkeypatch, [None] + [NetworkError("Bad Gateway")] * 3
    )

    result = await adapter._send_with_retry("123", _TEXT)

    assert result.success
    joined = " ".join(sent)
    for i in range(60):
        assert joined.count(f"слово{i:03d}") == 1


@pytest.mark.asyncio
async def test_flood_refusal_never_falls_back_to_truncated_plain_text(monkeypatch):
    adapter, sent = _adapter(monkeypatch, [_Flood(7), _Flood(7), _Flood(7)])

    result = await adapter._send_with_retry("123", _TEXT)

    assert not result.success
    assert not any("обычным текстом" in text for text in sent)
    assert not any("Не удалось доставить" in text for text in sent)
