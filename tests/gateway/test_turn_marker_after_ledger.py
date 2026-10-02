"""The active-turn marker is cleared after the reply is in the delivery ledger (K21-274).

Cleared at the end of the turn, a crash between the turn and the send lost both
the marker and the reply. The adapter now clears it once the reply is recorded.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult
from gateway.session import SessionSource


class _Adapter(BasePlatformAdapter):
    def __init__(self, order):
        super().__init__(PlatformConfig(enabled=True, token="t"), Platform.TELEGRAM)
        self.order = order

    async def connect(self, *, is_reconnect: bool = False):
        return True

    async def disconnect(self):
        pass

    async def get_chat_info(self, chat_id):
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.order.append("send")
        return SendResult(success=True, message_id="out")


def _event() -> MessageEvent:
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm", user_id="u")
    return MessageEvent(text="hi", message_type=MessageType.TEXT, source=source, message_id="m1")


def _runner(order):
    async def clear(event):
        if not hasattr(event, "_gateway_defer_turn_clear"):
            return
        order.append("clear")
        delattr(event, "_gateway_defer_turn_clear")

    return SimpleNamespace(_clear_durable_active_turn=clear)


@pytest.mark.asyncio
async def test_marker_is_cleared_after_ledger_record_and_before_send():
    order: list[str] = []
    adapter = _Adapter(order)
    adapter.gateway_runner = _runner(order)

    async def handler(event):
        order.append("turn")
        assert event._gateway_defer_turn_clear is True
        return "answer"

    adapter.set_message_handler(handler)

    with patch("gateway.delivery_ledger.ledger_enabled", return_value=True), \
         patch("gateway.delivery_ledger.record_obligation", side_effect=lambda **kw: order.append("ledger")), \
         patch("gateway.delivery_ledger.mark_attempting"), \
         patch("gateway.delivery_ledger.mark_delivered"):
        await adapter._process_message_background(_event(), "sk")

    assert order[:4] == ["turn", "ledger", "clear", "send"]
    assert order.count("clear") == 1


@pytest.mark.asyncio
async def test_marker_is_cleared_when_the_turn_has_no_reply():
    order: list[str] = []
    adapter = _Adapter(order)
    adapter.gateway_runner = _runner(order)

    async def handler(event):
        return None

    adapter.set_message_handler(handler)
    await adapter._process_message_background(_event(), "sk")

    assert order == ["clear"]


@pytest.mark.asyncio
async def test_marker_is_cleared_when_the_turn_raises():
    order: list[str] = []
    adapter = _Adapter(order)
    adapter.gateway_runner = _runner(order)

    async def handler(event):
        raise RuntimeError("boom")

    adapter.set_message_handler(handler)
    await adapter._process_message_background(_event(), "sk")

    assert "clear" in order
