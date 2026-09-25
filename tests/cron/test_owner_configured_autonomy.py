"""Reports and autonomous runs the owner set up go out without a per-message decision.

Dmitry, 25.09.2026: «любые отчёты, которые пользователь просит отправлять —
всегда без подтверждения, и любые автономные действия, которые пользователь
настраивает — всегда без подтверждения». A job created by anybody else still
waits for an exact decision (K21-114).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cron.scheduler import _deliver_result, _delivery_decision_session


@pytest.mark.parametrize(
    "origin,needs_decision",
    [
        (None, False),  # created in the cabinet or on the owner's computer
        ({"platform": "api_server", "chat_id": "web"}, False),
        ({"platform": "telegram", "chat_id": "42", "owner": True}, False),  # asked in the owner's DM
        ({"platform": "telegram", "chat_id": "777", "owner": False}, True),  # a visitor's job
        ({"platform": "webhook", "chat_id": "hook"}, True),  # content-triggered
    ],
)
def test_only_jobs_created_by_somebody_else_wait_for_a_decision(origin, needs_decision):
    job = {"id": "daily", **({"origin": origin} if origin else {})}
    session = _delivery_decision_session(job, "run-1")
    assert (session == "cron:daily:run-1") is needs_decision
    assert (session == "") is (not needs_decision)


def _telegram_config():
    from gateway.config import Platform

    pconfig = MagicMock(enabled=True)
    cfg = MagicMock()
    cfg.platforms = {Platform.TELEGRAM: pconfig}
    return cfg


@pytest.mark.parametrize("owner_job", [True, False])
def test_scheduled_report_goes_out_for_the_owner_and_waits_for_others(owner_job):
    job = {
        "id": "daily-1",
        "name": "Отчёт владельцу",
        "deliver": "origin",
        "origin": {"platform": "telegram", "chat_id": "42", "owner": owner_job},
    }
    queue = MagicMock(return_value={"id": "effect_1"})
    send = AsyncMock(return_value={"success": True})
    with (
        patch("gateway.config.load_gateway_config", return_value=_telegram_config()),
        patch("tools.send_message_tool._queue_outbound_decision", queue),
        patch("tools.send_message_tool._send_to_platform", new=send),
        patch("korra_cli.profiles.get_active_profile_name", return_value="nyura-bitrix"),
    ):
        result = _deliver_result(
            job, "Утренний отчёт", decision_session_id=_delivery_decision_session(job, "run-7"),
        )

    if owner_job:
        assert result is None
        send.assert_awaited_once()
        queue.assert_not_called()
    else:
        assert result == "waiting_decision:effect_1"
        send.assert_not_awaited()


def _cron_turn(owner: bool):
    from gateway.session_context import set_background_owner, set_session_vars

    return set_background_owner(owner), set_session_vars(cron_session="1", platform="telegram", chat_id="42")


@pytest.mark.parametrize("owner_job", [True, False])
def test_agent_send_inside_a_scheduled_run_follows_who_set_the_job_up(owner_job):
    from gateway.config import Platform
    from gateway.session_context import clear_session_vars, reset_background_owner
    from tools.send_message_tool import send_message_tool

    tg = SimpleNamespace(enabled=True, token="t", extra={})
    config = SimpleNamespace(platforms={Platform.TELEGRAM: tg}, get_home_channel=lambda _p: None)
    queue = MagicMock(return_value={"id": "effect_2", "status": "pending"})
    send = AsyncMock(return_value={"success": True})
    owner_token, tokens = _cron_turn(owner_job)
    try:
        with (
            patch("gateway.config.load_gateway_config", return_value=config),
            patch("tools.interrupt.is_interrupted", return_value=False),
            patch("model_tools._run_async", side_effect=lambda coro: __import__("asyncio").run(coro)),
            patch("tools.send_message_tool._queue_outbound_decision", queue),
            patch("tools.send_message_tool._send_to_platform", new=send),
            patch("gateway.mirror.mirror_to_session", return_value=True),
        ):
            json.loads(send_message_tool({"action": "send", "target": "telegram:2002", "message": "Отчёт менеджерам"}))
    finally:
        clear_session_vars(tokens)
        reset_background_owner(owner_token)

    if owner_job:
        queue.assert_not_called()
        send.assert_awaited_once()
    else:
        queue.assert_called_once()
        send.assert_not_awaited()


def test_a_live_conversation_still_asks_before_sending_to_somebody_else():
    """A one-off send the agent proposes in chat is not owner-configured autonomy."""
    from tools.send_message_tool import _owner_configured_run
    from gateway.session_context import clear_session_vars, set_session_vars

    tokens = set_session_vars(platform="api_server", chat_id="web")
    try:
        assert _owner_configured_run() is False
    finally:
        clear_session_vars(tokens)
