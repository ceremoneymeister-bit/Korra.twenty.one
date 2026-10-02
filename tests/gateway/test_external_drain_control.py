"""Tests for the external drain-control marker contract + gateway state machine.

Task 2.2/2.3. Two layers:
  * drain_control.py — the presence-based marker contract (write/clear/read,
    HERMES_HOME-scoped, never-raises).
  * GatewayRunner enter/exit/watcher + the new-turn accept gate — the
    reversible state machine driven by the marker.

Mocked tests are necessary-not-sufficient here (the HARD live-validation gate,
Q-B, exercises a real `hermes gateway run`); these lock the unit contract.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import gateway.drain_control as dc
from gateway.run import GatewayRunner
from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from tests.gateway.restart_test_helpers import make_restart_runner, make_restart_source


# ---------------------------------------------------------------------------
# Marker contract (drain_control.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


class TestMarkerContract:
    def test_absent_by_default(self, home):
        assert dc.drain_requested() is False
        assert dc.read_drain_request() is None

    def test_write_then_present(self, home):
        payload = dc.write_drain_request(principal="nas")
        assert dc.drain_requested() is True
        assert payload["action"] == "drain"
        assert payload["principal"] == "nas"
        body = dc.read_drain_request()
        assert body is not None and body["principal"] == "nas"


class TestSuppressNotification:
    """The generic suppress_notification flag on the drain marker.

    Gates ONLY the gateway's home-channel shutdown broadcast (NAS auto-update
    sets it true). Default-false so legacy/operator drains behave as before.
    The reader reuses the NS-570 epoch-staleness check so an orphaned marker
    can never silence a fresh gateway.
    """

    def test_default_false(self, home):
        payload = dc.write_drain_request(principal="nas")
        assert payload["suppress_notification"] is False
        assert dc.drain_notification_suppressed() is False

    def test_flag_round_trips_true(self, home):
        payload = dc.write_drain_request(principal="nas", suppress_notification=True)
        assert payload["suppress_notification"] is True
        body = dc.read_drain_request()
        assert body is not None and body["suppress_notification"] is True
        assert dc.drain_notification_suppressed() is True


# ---------------------------------------------------------------------------
# Instantiation-epoch staleness (NS-570: orphaned marker on durable volume)
# ---------------------------------------------------------------------------


class TestInstantiationEpoch:
    def test_write_stamps_current_epoch(self, home):
        payload = dc.write_drain_request(principal="nas")
        assert payload["epoch"] == dc.current_instantiation_epoch()
        body = dc.read_drain_request()
        assert body is not None and body["epoch"] == dc.current_instantiation_epoch()


    def test_marker_from_prior_instantiation_reads_as_absent(self, home, monkeypatch):
        # THE NS-570 REGRESSION. A begin-drain marker written by a PREVIOUS
        # container/VM instantiation survives on the durable HERMES_HOME volume
        # across a machine restart. The freshly-restarted gateway (new epoch)
        # must treat it as absent, NOT re-engage drain.
        monkeypatch.setattr(dc, "current_instantiation_epoch", lambda: "epoch-OLD")
        dc.write_drain_request(principal="nas")  # stamps "epoch-OLD"
        assert dc.drain_requested() is True  # same epoch → active

        # Simulate the restart: a brand-new instantiation epoch.
        monkeypatch.setattr(dc, "current_instantiation_epoch", lambda: "epoch-NEW")
        # The marker file is still physically present on the volume…
        assert dc.drain_request_path().exists() is True
        # …but it is ignored because its epoch belongs to a prior instantiation.
        assert dc.drain_requested() is False


    def test_current_epoch_empty_when_proc_unreadable(self, monkeypatch):
        # When neither /proc identity source is readable, the epoch is "" so
        # the staleness check is disabled rather than crashing.
        from pathlib import Path as _P

        orig_read_text = _P.read_text

        def _boom(self, *a, **k):
            if str(self).startswith("/proc/"):
                raise OSError("no /proc")
            return orig_read_text(self, *a, **k)

        dc.current_instantiation_epoch.cache_clear()
        monkeypatch.setattr(_P, "read_text", _boom)
        try:
            assert dc.current_instantiation_epoch() == ""
        finally:
            dc.current_instantiation_epoch.cache_clear()


# ---------------------------------------------------------------------------
# requested_at max-age (#85433: same-epoch orphaned marker, no restart)
# ---------------------------------------------------------------------------


class TestMarkerMaxAge:
    def test_fresh_marker_honoured(self, home):
        dc.write_drain_request(principal="nas")
        assert dc.drain_requested() is True

    def test_expired_marker_reads_as_absent(self, home):
        # THE #85433 REGRESSION. A drain-gated action completes WITHOUT a
        # machine restart, so the epoch still matches — but the writer never
        # cancelled the drain. The orphan must not wedge the gateway forever.
        from datetime import datetime, timedelta, timezone

        dc.write_drain_request(principal="nas", suppress_notification=True)
        body = dc.read_drain_request()
        assert body is not None
        body["requested_at"] = (
            datetime.now(timezone.utc)
            - timedelta(seconds=dc.DRAIN_REQUEST_MAX_AGE_SECONDS + 60)
        ).isoformat()
        dc.drain_request_path().write_text(json.dumps(body), encoding="utf-8")

        # The marker file is still physically present, with the CURRENT epoch…
        assert dc.drain_request_path().exists() is True
        # …but it is ignored because it outlived any legitimate drain.
        assert dc.drain_requested() is False
        # The suppression flag of an expired orphan is likewise ignored.
        assert dc.drain_notification_suppressed() is False

    def test_marker_without_timestamp_still_honoured(self, home):
        # Leniency contract: no requested_at (legacy/corrupt body) must fail
        # toward quiescing, exactly like the epoch check.
        payload = {"action": "drain", "epoch": dc.current_instantiation_epoch()}
        dc.drain_request_path().write_text(json.dumps(payload), encoding="utf-8")
        assert dc.drain_requested() is True

    def test_unparseable_timestamp_still_honoured(self, home):
        payload = {
            "action": "drain",
            "epoch": dc.current_instantiation_epoch(),
            "requested_at": "not-a-timestamp",
        }
        dc.drain_request_path().write_text(json.dumps(payload), encoding="utf-8")
        assert dc.drain_requested() is True

    def test_naive_timestamp_treated_as_utc(self, home):
        # A writer that stamped a tz-naive ISO string must still expire.
        from datetime import datetime, timedelta, timezone

        stale_naive = (
            datetime.now(timezone.utc)
            - timedelta(seconds=dc.DRAIN_REQUEST_MAX_AGE_SECONDS + 60)
        ).replace(tzinfo=None)
        payload = {
            "action": "drain",
            "epoch": dc.current_instantiation_epoch(),
            "requested_at": stale_naive.isoformat(),
        }
        dc.drain_request_path().write_text(json.dumps(payload), encoding="utf-8")
        assert dc.drain_requested() is False

    def test_expiry_warning_logged_once_per_marker(self, home, caplog):
        # The watcher polls every 1s; an expired orphan must warn ONCE, not
        # once per tick (~86k/day). A refreshed marker that expires again
        # warns again (new requested_at).
        import logging
        from datetime import datetime, timedelta, timezone

        def _write_expired(offset_seconds):
            dc.write_drain_request(principal="nas")
            body = dc.read_drain_request()
            assert body is not None
            body["requested_at"] = (
                datetime.now(timezone.utc)
                - timedelta(seconds=dc.DRAIN_REQUEST_MAX_AGE_SECONDS + offset_seconds)
            ).isoformat()
            dc.drain_request_path().write_text(json.dumps(body), encoding="utf-8")

        _write_expired(60)
        with caplog.at_level(logging.WARNING, logger="gateway.drain_control"):
            assert dc.drain_requested() is False
            assert dc.drain_requested() is False  # second poll tick
            assert dc.drain_requested() is False  # third poll tick
        expired_logs = [r for r in caplog.records if "expired drain marker" in r.message]
        assert len(expired_logs) == 1

        caplog.clear()
        _write_expired(120)  # a DIFFERENT requested_at that is also expired
        with caplog.at_level(logging.WARNING, logger="gateway.drain_control"):
            assert dc.drain_requested() is False
        expired_logs = [r for r in caplog.records if "expired drain marker" in r.message]
        assert len(expired_logs) == 1

    def test_rewrite_refreshes_the_clock(self, home):
        # The sanctioned keep-alive: re-writing the marker bumps requested_at,
        # so a deliberately long drain stays honoured.
        from datetime import datetime, timedelta, timezone

        dc.write_drain_request(principal="nas")
        body = dc.read_drain_request()
        assert body is not None
        body["requested_at"] = (
            datetime.now(timezone.utc)
            - timedelta(seconds=dc.DRAIN_REQUEST_MAX_AGE_SECONDS + 60)
        ).isoformat()
        dc.drain_request_path().write_text(json.dumps(body), encoding="utf-8")
        assert dc.drain_requested() is False  # expired…
        dc.write_drain_request(principal="nas")  # …keep-alive re-write
        assert dc.drain_requested() is True


# ---------------------------------------------------------------------------
# Gateway state machine (enter / exit / idempotency)
# ---------------------------------------------------------------------------


def _drain_runner():
    runner, adapter = make_restart_runner()
    runner._external_drain_active = False
    # Bind the real methods under test.
    runner._enter_external_drain = GatewayRunner._enter_external_drain.__get__(
        runner, GatewayRunner
    )
    runner._exit_external_drain = GatewayRunner._exit_external_drain.__get__(
        runner, GatewayRunner
    )
    return runner, adapter


class TestDrainStateMachine:


    def test_enter_idempotent(self):
        runner, _ = _drain_runner()
        runner._enter_external_drain()
        runner._update_runtime_status.reset_mock()
        runner._enter_external_drain()  # second call — no-op
        runner._update_runtime_status.assert_not_called()


    def test_exit_during_shutdown_does_not_revert_to_running(self):
        runner, _ = _drain_runner()
        runner._enter_external_drain()
        runner._update_runtime_status.reset_mock()
        # A shutdown drain is now in progress — exit must NOT resurrect running.
        runner._draining = True
        runner._exit_external_drain()
        assert runner._external_drain_active is False
        runner._update_runtime_status.assert_not_called()


# ---------------------------------------------------------------------------
# Watcher reconciliation
# ---------------------------------------------------------------------------


class TestDrainWatcher:

    @pytest.mark.asyncio
    async def test_watcher_enters_then_exits_with_marker(self, home):
        runner, _ = _drain_runner()
        runner._drain_control_watcher = GatewayRunner._drain_control_watcher.__get__(
            runner, GatewayRunner
        )
        # Drive a few ticks manually rather than spinning the loop.
        dc.write_drain_request()
        task = asyncio.create_task(runner._drain_control_watcher(interval=0.02))
        await asyncio.sleep(0.06)
        assert runner._external_drain_active is True
        dc.clear_drain_request()
        await asyncio.sleep(0.06)
        assert runner._external_drain_active is False
        runner._running = False
        await asyncio.sleep(0.04)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# New-turn accept gate
# ---------------------------------------------------------------------------


class TestNewTurnGate:
    @pytest.mark.asyncio
    async def test_new_turn_refused_during_external_drain(self):
        runner, _ = _drain_runner()
        runner._external_drain_active = True
        event = MessageEvent(
            text="hello",
            message_type=MessageType.TEXT,
            source=make_restart_source(),
            message_id="m1",
        )
        event.update_ids = {7}
        result = await runner._handle_message(event)
        assert result is not None
        assert 'завершает работу перед обслуживанием' in result.lower()
        assert event.update_ids == set()  # a refusal is never receipted



# ---------------------------------------------------------------------------
# Telegram intake follows the marker (reversible pause)
# ---------------------------------------------------------------------------


class _FakeTelegram:
    """The Bot API queue: updates wait here while no updater is polling."""

    def __init__(self):
        self.queue: list[tuple[int, str]] = []
        self.listeners: list = []

    def send(self, update_id, text):
        self.queue.append((update_id, text))

    async def poll(self):
        while self.queue:
            update_id, text = self.queue.pop(0)
            for listener in self.listeners:
                await listener(update_id, text)


def _telegram_bot(telegram, replies, token="123:abc", profile=None):
    from unittest.mock import AsyncMock, MagicMock
    from types import SimpleNamespace
    from gateway.config import PlatformConfig
    from plugins.platforms.telegram.adapter import TelegramAdapter

    adapter = TelegramAdapter(PlatformConfig(enabled=True, token=token))
    if profile:
        adapter.set_owner_profile(profile)
    updater = MagicMock()
    updater.running = True
    polls: list = []

    async def _stop():
        updater.running = False

    updater.stop = AsyncMock(side_effect=_stop)
    adapter._app = SimpleNamespace(updater=updater)
    adapter._polling_heartbeat_loop = AsyncMock()

    async def deliver(update_id, text):
        if not updater.running:
            telegram.queue.append((update_id, text))
            return
        update = SimpleNamespace(
            update_id=update_id, message=SimpleNamespace(chat=SimpleNamespace(id=42))
        )
        try:
            await adapter._admit_update(update, None)
        except Exception:
            return  # a receipted update is dropped by ApplicationHandlerStop
        event = MessageEvent(
            text=text, message_type=MessageType.TEXT,
            source=make_restart_source(), message_id=str(update_id),
            update_ids={update_id},
        )
        await adapter._process_message_background(event, "sk")

    async def _start_polling(**kwargs):
        updater.running = True
        polls.append(asyncio.ensure_future(telegram.poll()))

    adapter._start_polling_resilient = AsyncMock(side_effect=_start_polling)
    telegram.listeners.append(deliver)

    async def handler(event):
        replies.append(event.text)
        return "ok"

    adapter.set_message_handler(handler)
    adapter._send_with_retry = AsyncMock(return_value=MagicMock(success=True, message_id="o"))
    return adapter, updater


async def _until(condition, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)


async def _tick(runner, seconds=0.12):
    task = asyncio.create_task(runner._drain_control_watcher(interval=0.02))
    await asyncio.sleep(seconds)
    return task


async def _stop_watcher(runner, task):
    runner._running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class TestIntakeFollowsTheMarker:
    @pytest.mark.asyncio
    async def test_begin_messages_cancel_answers_each_message_once(self, home, monkeypatch):
        monkeypatch.setattr(
            "korra_cli.profiles.resolve_profile_env", lambda name: str(home / "profiles" / name)
        )
        runner, _ = _drain_runner()
        telegram, replies = _FakeTelegram(), []
        primary, primary_updater = _telegram_bot(telegram, replies)
        secondary_telegram, secondary_replies = _FakeTelegram(), []
        secondary, secondary_updater = _telegram_bot(
            secondary_telegram, secondary_replies, token="555:abc", profile="work"
        )
        runner.adapters = {Platform.TELEGRAM: primary}
        runner._profile_adapters = {"work": {Platform.TELEGRAM: secondary}}

        dc.write_drain_request(principal="nas")
        task = await _tick(runner)
        assert primary_updater.running is False and secondary_updater.running is False

        for bot in (telegram, secondary_telegram):
            for listener in bot.listeners:
                await listener(1, "first")
                await listener(2, "second")
        assert replies == [] and secondary_replies == []  # waiting in Telegram's queue

        dc.clear_drain_request()
        await _until(lambda: len(replies) == 2 and len(secondary_replies) == 2)
        await _stop_watcher(runner, task)

        assert primary_updater.running is True and secondary_updater.running is True
        assert replies == ["first", "second"]
        assert secondary_replies == ["first", "second"]
        # a redelivery of an answered update is not answered again
        await telegram.listeners[0](1, "first")
        assert replies == ["first", "second"]

    @pytest.mark.asyncio
    async def test_begin_messages_restart_answers_after_start_once(self, home):
        runner, _ = _drain_runner()
        telegram, replies = _FakeTelegram(), []
        old, old_updater = _telegram_bot(telegram, replies)
        runner.adapters = {Platform.TELEGRAM: old}

        dc.write_drain_request(principal="nas")
        task = await _tick(runner)
        await telegram.listeners[0](1, "first")
        await telegram.listeners[0](2, "second")
        await _stop_watcher(runner, task)
        assert replies == [] and len(telegram.queue) == 2

        # the old process goes away; the new one polls the same bot
        old._app = None
        telegram.listeners.clear()
        new, new_updater = _telegram_bot(telegram, replies)
        await new._start_polling_resilient()
        await _until(lambda: len(replies) == 2)
        assert replies == ["first", "second"]

    @pytest.mark.asyncio
    async def test_cancel_during_shutdown_does_not_reopen_intake(self, home):
        runner, _ = _drain_runner()
        telegram, replies = _FakeTelegram(), []
        adapter, updater = _telegram_bot(telegram, replies)
        runner.adapters = {Platform.TELEGRAM: adapter}

        dc.write_drain_request(principal="nas")
        task = await _tick(runner)
        assert updater.running is False
        runner._draining = True
        dc.clear_drain_request()
        await asyncio.sleep(0.1)
        await _stop_watcher(runner, task)

        assert updater.running is False
