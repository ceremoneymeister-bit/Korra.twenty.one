"""A follow-up that reaches the busy handler as the turn ends still gets a turn (K21-274).

The busy handler awaits before deciding. If the running turn finishes meanwhile
it releases the session guard, and whatever the handler queued has no task to
run it until some later message arrives.
"""

from __future__ import annotations

import asyncio

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult
from gateway.session import SessionSource, build_session_key


class _Adapter(BasePlatformAdapter):
    async def connect(self, *, is_reconnect: bool = False):
        return True

    async def disconnect(self):
        pass

    async def get_chat_info(self, chat_id):
        return None

    async def send(self, *args, **kwargs):
        return SendResult(success=True, message_id="x")


def _event(text: str) -> MessageEvent:
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm", user_id="u")
    return MessageEvent(text=text, message_type=MessageType.TEXT, source=source, message_id=f"m-{text}")


async def _setup(busy_handler_factory):
    adapter = _Adapter(PlatformConfig(enabled=True, token="t"), Platform.TELEGRAM)
    ran: list[str] = []
    first_started = asyncio.Event()
    finish_first = asyncio.Event()

    async def handler(event):
        ran.append(event.text)
        if event.text == "M1":
            first_started.set()
            await finish_first.wait()
        return "r"

    adapter.set_message_handler(handler)
    adapter.set_busy_session_handler(busy_handler_factory(adapter, finish_first))
    return adapter, ran, first_started, finish_first


async def _settle(adapter):
    for _ in range(200):
        await asyncio.sleep(0.01)
        if not adapter._active_sessions and not adapter._pending_messages:
            return


@pytest.mark.asyncio
async def test_follow_up_queued_by_handler_runs_when_turn_ended_meanwhile():
    def factory(adapter, finish_first):
        async def busy(event, session_key):
            finish_first.set()
            while session_key in adapter._active_sessions:
                await asyncio.sleep(0)
            adapter._pending_messages[session_key] = event
            return True

        return busy

    adapter, ran, started, _ = await _setup(factory)
    key = build_session_key(_event("M1").source)
    await adapter.handle_message(_event("M1"))
    await started.wait()
    await adapter.handle_message(_event("M2"))
    await _settle(adapter)

    assert ran == ["M1", "M2"]
    assert key not in adapter._pending_messages


@pytest.mark.asyncio
async def test_follow_up_left_to_base_path_runs_when_turn_ended_meanwhile():
    def factory(adapter, finish_first):
        async def busy(event, session_key):
            finish_first.set()
            while session_key in adapter._active_sessions:
                await asyncio.sleep(0)
            return False

        return busy

    adapter, ran, started, _ = await _setup(factory)
    await adapter.handle_message(_event("M1"))
    await started.wait()
    await adapter.handle_message(_event("M2"))
    await _settle(adapter)

    assert ran == ["M1", "M2"]


@pytest.mark.asyncio
async def test_follow_up_still_queued_when_turn_is_still_running():
    def factory(adapter, finish_first):
        async def busy(event, session_key):
            return False

        return busy

    adapter, ran, started, finish_first = await _setup(factory)
    key = build_session_key(_event("M1").source)
    await adapter.handle_message(_event("M1"))
    await started.wait()
    await adapter.handle_message(_event("M2"))
    assert adapter._pending_messages[key].text == "M2"
    finish_first.set()
    await _settle(adapter)

    assert ran == ["M1", "M2"]
