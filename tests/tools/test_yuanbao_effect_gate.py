from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gateway.config import Platform
from tools import yuanbao_tools


@pytest.mark.asyncio
async def test_yuanbao_dm_is_queued_as_exact_external_send(monkeypatch):
    adapter = SimpleNamespace(send_dm=AsyncMock())
    pconfig = SimpleNamespace(enabled=True)
    monkeypatch.setattr(yuanbao_tools, "_get_active_adapter", lambda: adapter)
    monkeypatch.setattr(
        "gateway.config.load_gateway_config",
        lambda: SimpleNamespace(platforms={Platform.YUANBAO: pconfig}),
    )
    queue = Mock(return_value={"id": "effect_yuanbao_dm"})
    monkeypatch.setattr("tools.send_message_tool._queue_outbound_decision", queue)

    result = await yuanbao_tools.send_dm(
        group_code="group-1",
        name="Client",
        message="Exact draft",
        user_id="u-7",
    )

    assert result["status"] == "pending_decision"
    assert result["decision_id"] == "effect_yuanbao_dm"
    adapter.send_dm.assert_not_awaited()
    assert queue.call_args.kwargs["chat_id"] == "direct:u-7"
    assert queue.call_args.kwargs["cleaned_message"] == "Exact draft"


@pytest.mark.asyncio
async def test_cross_chat_sticker_is_disabled_without_durable_executor(monkeypatch):
    from gateway.session_context import reset_session_vars, set_session_vars

    adapter = SimpleNamespace(send_sticker=AsyncMock())
    monkeypatch.setattr(yuanbao_tools, "_get_active_adapter", lambda: adapter)
    set_session_vars(platform="yuanbao", chat_id="group:current")
    try:
        result = await yuanbao_tools.send_sticker(
            sticker="278", chat_id="direct:third-party"
        )
    finally:
        reset_session_vars()

    assert result["success"] is False
    assert "Cross-chat sticker" in result["error"]
    adapter.send_sticker.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("session_values", [{}, {"platform": "telegram", "chat_id": "group:target"}])
async def test_sticker_requires_the_exact_current_yuanbao_chat(monkeypatch, session_values):
    from gateway.session_context import reset_session_vars, set_session_vars

    adapter = SimpleNamespace(send_sticker=AsyncMock())
    monkeypatch.setattr(yuanbao_tools, "_get_active_adapter", lambda: adapter)
    if session_values:
        set_session_vars(**session_values)
    try:
        result = await yuanbao_tools.send_sticker(
            sticker="278", chat_id="group:target"
        )
    finally:
        reset_session_vars()

    assert result["success"] is False
    assert "Cross-chat sticker" in result["error"]
    adapter.send_sticker.assert_not_awaited()
