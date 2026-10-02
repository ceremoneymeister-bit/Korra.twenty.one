"""K21-274: during a drain Telegram intake stops, so new messages wait in Telegram's queue."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter
from tests.gateway.restart_test_helpers import make_restart_runner


def _adapter_with_running_updater():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="123:abc"))
    updater = MagicMock()
    updater.running = True

    async def _stop():
        updater.running = False

    updater.stop = AsyncMock(side_effect=_stop)
    app = MagicMock()
    app.updater = updater
    app.stop = AsyncMock()
    app.shutdown = AsyncMock()
    adapter._app = app
    return adapter, app, updater


@pytest.mark.asyncio
async def test_pause_intake_stops_updater_but_keeps_app_and_bot():
    adapter, app, updater = _adapter_with_running_updater()
    bot = adapter._bot = MagicMock()

    await adapter.pause_intake()

    updater.stop.assert_awaited_once()
    app.stop.assert_not_awaited()
    app.shutdown.assert_not_awaited()
    assert adapter._app is app and adapter._bot is bot


@pytest.mark.asyncio
async def test_pause_intake_is_idempotent():
    adapter, _app, updater = _adapter_with_running_updater()

    await adapter.pause_intake()
    await adapter.pause_intake()

    updater.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_paused_adapter_does_not_restart_polling():
    adapter, _app, updater = _adapter_with_running_updater()
    await adapter.pause_intake()

    adapter._schedule_polling_recovery(RuntimeError("boom"), reason="heartbeat probe")
    await adapter._probe_pending_updates(MagicMock(), 1)
    await adapter._handle_polling_network_error(RuntimeError("boom"))

    assert adapter._polling_error_task is None
    updater.start_polling.assert_not_called()


@pytest.mark.asyncio
async def test_pause_intake_cancels_running_recovery_task():
    adapter, _app, _updater = _adapter_with_running_updater()
    task = asyncio.ensure_future(asyncio.sleep(60))
    adapter._polling_error_task = task

    await adapter.pause_intake()
    await asyncio.sleep(0)

    assert task.cancelled()


@pytest.mark.asyncio
async def test_pause_intake_survives_a_failing_updater_stop():
    adapter, _app, updater = _adapter_with_running_updater()
    updater.stop = AsyncMock(side_effect=RuntimeError("socket gone"))

    await adapter.pause_intake()

    assert adapter._polling_teardown_started is True


@pytest.mark.asyncio
async def test_disconnect_after_pause_still_stops_the_app():
    adapter, app, updater = _adapter_with_running_updater()
    app.running = True
    await adapter.pause_intake()

    await adapter.disconnect()

    updater.stop.assert_awaited_once()
    app.stop.assert_awaited_once()
    app.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_runner_pauses_primary_and_secondary_adapters():
    runner, adapter = make_restart_runner()
    adapter.pause_intake = AsyncMock()
    secondary = MagicMock()
    secondary.pause_intake = AsyncMock()
    runner._profile_adapters = {"helper": {"telegram": secondary}}

    await runner._pause_platform_intake()

    adapter.pause_intake.assert_awaited_once()
    secondary.pause_intake.assert_awaited_once()


@pytest.mark.asyncio
async def test_runner_pause_tolerates_adapters_without_it_and_failures():
    runner, adapter = make_restart_runner()
    assert not hasattr(adapter, "pause_intake")
    other = MagicMock()
    other.pause_intake = AsyncMock(side_effect=RuntimeError("boom"))
    runner._profile_adapters = {"helper": {"telegram": other}}

    await runner._pause_platform_intake()

    other.pause_intake.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_pauses_intake_before_notifying_and_draining():
    runner, adapter = make_restart_runner()
    runner._restart_drain_timeout = 0.0
    order = []
    adapter.pause_intake = AsyncMock(side_effect=lambda: order.append("pause"))
    adapter.disconnect = AsyncMock(side_effect=lambda: order.append("disconnect"))
    runner._notify_active_sessions_of_shutdown = AsyncMock(
        side_effect=lambda: order.append("notify")
    )

    with (
        patch("gateway.status.remove_pid_file"),
        patch("gateway.status.write_runtime_status"),
        patch("agent.auxiliary_client.shutdown_cached_clients"),
    ):
        await runner.stop()

    assert order[:2] == ["pause", "notify"]
    assert order.index("disconnect") > order.index("pause")


@pytest.mark.asyncio
async def test_restart_request_pauses_intake_during_the_after_turn_wait():
    runner, adapter = make_restart_runner()
    adapter.pause_intake = AsyncMock()
    paused_before_wait = []

    async def _wait():
        paused_before_wait.append(adapter.pause_intake.await_count)
        return False

    runner._await_active_work_before_restart = _wait
    runner.stop = AsyncMock()

    assert runner.request_restart() is True
    await runner._restart_task

    assert paused_before_wait == [1]


# --- the pause is reversible: a cancelled external drain reopens intake ---


def _paused_adapter():
    adapter, app, updater = _adapter_with_running_updater()
    adapter._polling_error_callback_ref = MagicMock()

    async def _start(**kwargs):
        updater.running = True
        return True

    adapter._start_polling_resilient = AsyncMock(side_effect=_start)
    adapter._polling_heartbeat_loop = AsyncMock()
    return adapter, app, updater


@pytest.mark.asyncio
async def test_resume_intake_restarts_polling_and_lifts_the_fence():
    adapter, _app, updater = _paused_adapter()
    await adapter.pause_intake()
    assert updater.running is False and adapter._polling_teardown_started is True

    await adapter.resume_intake()

    adapter._start_polling_resilient.assert_awaited_once_with(
        drop_pending_updates=False,
        error_callback=adapter._polling_error_callback_ref,
        require_progress=False,
    )
    assert updater.running is True
    assert adapter._polling_teardown_started is False
    await asyncio.sleep(0)
    adapter._polling_heartbeat_loop.assert_awaited_once()
    # and the pause can be applied again
    await adapter.pause_intake()
    assert updater.running is False


@pytest.mark.asyncio
async def test_resume_intake_is_a_noop_without_a_pause():
    adapter, _app, _updater = _paused_adapter()

    await adapter.resume_intake()

    adapter._start_polling_resilient.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_intake_does_not_revive_a_disconnected_adapter():
    adapter, app, _updater = _paused_adapter()
    app.running = True
    await adapter.pause_intake()
    await adapter.disconnect()

    await adapter.resume_intake()

    adapter._start_polling_resilient.assert_not_awaited()
    assert adapter._polling_teardown_started is True


@pytest.mark.asyncio
async def test_resume_intake_restarts_the_webhook_server():
    adapter, _app, updater = _paused_adapter()
    adapter._webhook_mode = True
    adapter._webhook_start_kwargs = {"port": 8443, "url_path": "/telegram"}
    updater.start_webhook = AsyncMock()
    await adapter.pause_intake()

    await adapter.resume_intake()

    updater.start_webhook.assert_awaited_once_with(port=8443, url_path="/telegram")
    adapter._start_polling_resilient.assert_not_awaited()
    adapter._bot_identity_refresh_task.cancel()


@pytest.mark.asyncio
async def test_resume_intake_hands_a_failed_start_to_polling_recovery():
    adapter, _app, _updater = _paused_adapter()
    adapter._start_polling_resilient = AsyncMock(side_effect=RuntimeError("boom"))
    adapter._schedule_polling_recovery = MagicMock()
    await adapter.pause_intake()

    await adapter.resume_intake()

    adapter._schedule_polling_recovery.assert_called_once()
    assert adapter._polling_teardown_started is False


@pytest.mark.asyncio
async def test_runner_resumes_primary_and_secondary_adapters():
    runner, adapter = make_restart_runner()
    adapter.resume_intake = AsyncMock()
    secondary = MagicMock()
    secondary.resume_intake = AsyncMock()
    broken = MagicMock()
    broken.resume_intake = AsyncMock(side_effect=RuntimeError("boom"))
    runner._profile_adapters = {"helper": {"telegram": secondary, "other": broken}}

    await runner._resume_platform_intake()

    adapter.resume_intake.assert_awaited_once()
    secondary.resume_intake.assert_awaited_once()
    broken.resume_intake.assert_awaited_once()
