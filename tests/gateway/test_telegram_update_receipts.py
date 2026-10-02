"""K21-274: the Telegram queue survives a cold start and a redelivery is answered once."""
import asyncio
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter
from plugins.platforms.telegram.update_receipts import (
    RECEIPT_TTL_SECONDS,
    UpdateReceipts,
    receipts_path,
)


def test_receipt_survives_restart(tmp_path):
    path = tmp_path / "r.txt"
    first = UpdateReceipts(path)
    assert first.seen(101) is False
    first.record(101)
    again = UpdateReceipts(path)
    assert again.seen(101) is True
    assert again.seen(102) is False


def test_expired_and_garbage_lines_are_dropped(tmp_path):
    path = tmp_path / "r.txt"
    old = time.time() - RECEIPT_TTL_SECONDS - 60
    path.write_text(f"1 {old:.0f}\nnot a receipt\n2 {time.time():.0f}\n", encoding="utf-8")
    receipts = UpdateReceipts(path)
    assert receipts.seen(1) is False  # expired: allowed again
    assert receipts.seen(2) is True
    assert "not a receipt" not in path.read_text(encoding="utf-8")


def test_unwritable_location_still_dedups_in_memory(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    receipts = UpdateReceipts(blocker / "sub" / "r.txt")
    receipts.record(5)
    assert receipts.seen(5) is True


def test_path_is_per_bot_and_needs_a_numeric_bot_id(tmp_path):
    assert receipts_path(tmp_path, "123:abc").name == "telegram_update_receipts_123.txt"
    assert receipts_path(tmp_path, "456:abc").name == "telegram_update_receipts_456.txt"
    assert receipts_path(tmp_path, "garbage") is None


def _adapter(token="123:abc", profile=None):
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token=token))
    if profile:
        adapter.set_owner_profile(profile)
    return adapter


def _message_update(update_id, chat_id=42):
    return SimpleNamespace(update_id=update_id, message=SimpleNamespace(chat=SimpleNamespace(id=chat_id)))


def _stop_exception(monkeypatch):
    class ApplicationHandlerStop(Exception):
        pass

    monkeypatch.setattr(
        sys.modules["telegram.ext"], "ApplicationHandlerStop", ApplicationHandlerStop, raising=False
    )
    return ApplicationHandlerStop


def _event(*update_ids, chat_id="42", text="hi"):
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.session import SessionSource
    from gateway.config import Platform

    source = SessionSource(platform=Platform.TELEGRAM, chat_id=chat_id, chat_type="dm", user_id="u")
    return MessageEvent(
        text=text, message_type=MessageType.TEXT, source=source,
        message_id=f"m{update_ids[0]}", update_ids=set(update_ids),
    )


@pytest.mark.asyncio
async def test_message_is_receipted_only_when_its_processing_ends(tmp_path, monkeypatch):
    stop = _stop_exception(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _adapter()
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(event):
        started.set()
        await release.wait()

    adapter.set_message_handler(handler)
    await adapter._admit_update(_message_update(77), None)
    # admitted but not processed: a redelivery must get through
    await adapter._admit_update(_message_update(77), None)
    task = asyncio.create_task(adapter._process_message_background(_event(77), "sk"))
    await started.wait()
    await adapter._admit_update(_message_update(77), None)  # mid-turn crash: still not receipted
    release.set()
    await task
    with pytest.raises(stop):
        await adapter._admit_update(_message_update(77), None)
    # a new process (new adapter) still remembers it
    with pytest.raises(stop):
        await _adapter()._admit_update(_message_update(77), None)
    # the same id on another bot is a different message
    await _adapter(token="999:zzz")._admit_update(_message_update(77), None)


@pytest.mark.asyncio
async def test_cancelled_turn_before_its_reply_is_not_receipted(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _adapter()
    started = asyncio.Event()

    async def handler(event):
        started.set()
        await asyncio.sleep(60)

    adapter.set_message_handler(handler)
    receipts = adapter._receipts_for_bot()
    event = _event(88)
    task = asyncio.create_task(adapter._process_message_background(event, "sk"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert receipts.seen(88) is False
    assert event.update_ids == {88}


@pytest.mark.asyncio
async def test_second_batch_of_a_chat_is_recovered_after_a_crash(tmp_path, monkeypatch):
    stop = _stop_exception(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _adapter()
    adapter.set_message_handler(AsyncMock(return_value=None))
    await adapter._admit_update(_message_update(101), None)
    await adapter._admit_update(_message_update(102), None)  # same chat, other batch
    await adapter._process_message_background(_event(101), "sk")
    # the process dies before the second batch is processed
    restarted = _adapter()
    with pytest.raises(stop):
        await restarted._admit_update(_message_update(101), None)  # not run twice
    await restarted._admit_update(_message_update(102), None)  # recovered


@pytest.mark.asyncio
async def test_receipt_covers_every_part_merged_into_the_event(tmp_path, monkeypatch):
    stop = _stop_exception(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _adapter()
    adapter._text_batch_delay_seconds = 0.01
    adapter._text_batch_split_delay_seconds = 0.01
    handed = []
    adapter.handle_message = AsyncMock(side_effect=handed.append)
    adapter._enqueue_text_event(_event(101, text="part one"))
    adapter._enqueue_text_event(_event(102, text="part two"))
    await asyncio.sleep(0.2)
    assert len(handed) == 1 and handed[0].update_ids == {101, 102}

    queued = {}
    from gateway.platforms.base import merge_pending_message_event
    merge_pending_message_event(queued, "sk", _event(103), merge_text=True)
    merge_pending_message_event(queued, "sk", _event(104), merge_text=True)
    assert queued["sk"].update_ids == {103, 104}

    adapter.set_message_handler(AsyncMock(return_value=None))
    await adapter._process_message_background(queued["sk"], "sk")
    for uid in (103, 104):
        with pytest.raises(stop):
            await adapter._admit_update(_message_update(uid), None)
    await adapter._admit_update(_message_update(105), None)


@pytest.mark.asyncio
async def test_receipt_is_written_once_the_reply_is_in_the_ledger(tmp_path, monkeypatch):
    from gateway.platforms.base import SendResult

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _adapter()
    adapter.set_message_handler(AsyncMock(return_value="answer"))
    order = []
    receipts = adapter._receipts_for_bot()

    async def send(**kwargs):
        order.append("send:" + str(receipts.seen(7)))
        return SendResult(success=True, message_id="out")

    adapter._send_with_retry = send
    with patch("gateway.delivery_ledger.ledger_enabled", return_value=True), \
         patch("gateway.delivery_ledger.record_obligation", side_effect=lambda **kw: order.append("ledger")), \
         patch("gateway.delivery_ledger.mark_attempting"), \
         patch("gateway.delivery_ledger.mark_delivered"):
        order.append("before:" + str(receipts.seen(7)))
        await adapter._process_message_background(_event(7), "sk")
    assert order == ["before:False", "ledger", "send:True"]


@pytest.mark.asyncio
async def test_non_message_update_is_receipted_at_once(tmp_path, monkeypatch):
    stop = _stop_exception(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _adapter()
    callback = SimpleNamespace(update_id=5, message=None, channel_post=None)
    await adapter._admit_update(callback, None)
    with pytest.raises(stop):
        await adapter._admit_update(callback, None)


@pytest.mark.asyncio
async def test_secondary_profile_keeps_receipts_in_its_own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        "korra_cli.profiles.resolve_profile_env", lambda name: str(tmp_path / "profiles" / name)
    )
    adapter = _adapter(token="555:abc", profile="work")
    await adapter._admit_update(SimpleNamespace(update_id=1, message=None, channel_post=None), None)
    assert (tmp_path / "profiles" / "work" / "telegram_update_receipts_555.txt").exists()
    assert not (tmp_path / "telegram_update_receipts_555.txt").exists()


def test_admission_handler_is_registered_ahead_of_core_handlers(monkeypatch):
    monkeypatch.setattr(
        "plugins.platforms.telegram.adapter.TypeHandler",
        lambda kind, callback: SimpleNamespace(callback=callback),
    )
    adapter = _adapter()
    app = MagicMock()
    adapter._register_handlers(app)
    groups = {c.kwargs.get("group", 0) for c in app.add_handler.call_args_list}
    first = app.add_handler.call_args_list[0]
    assert first.kwargs.get("group") == -1
    assert first.args[0].callback == adapter._admit_update
    assert {-1, 0, 99} <= groups


@pytest.mark.asyncio
async def test_cold_start_keeps_the_pending_queue_and_logs_it(monkeypatch, caplog):
    async def _noop():
        return []

    monkeypatch.setattr("plugins.platforms.telegram.adapter.discover_fallback_ips", _noop)
    monkeypatch.setattr("plugins.platforms.telegram.adapter.HTTPXRequest", lambda **kw: MagicMock())
    monkeypatch.setattr("gateway.status.acquire_scoped_lock", lambda *a, **k: (True, None))
    monkeypatch.setattr("gateway.status.release_scoped_lock", lambda *a, **k: None)

    adapter = _adapter()
    seen = {}

    async def fake_start_polling(**kwargs):
        seen.update(kwargs)
        adapter._record_polling_progress(adapter._polling_generation)

    updater = SimpleNamespace(start_polling=AsyncMock(side_effect=fake_start_polling), stop=AsyncMock(), running=True)
    bot = SimpleNamespace(set_my_commands=AsyncMock(), delete_webhook=AsyncMock())
    app = SimpleNamespace(bot=bot, updater=updater, add_handler=MagicMock(),
                          initialize=AsyncMock(), start=AsyncMock())
    builder = MagicMock()
    for name in ("token", "request", "get_updates_request"):
        getattr(builder, name).return_value = builder
    builder.build.return_value = app
    monkeypatch.setattr(
        "plugins.platforms.telegram.adapter.Application",
        SimpleNamespace(builder=MagicMock(return_value=builder)),
    )
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    with caplog.at_level("INFO"):
        assert await adapter.connect(is_reconnect=False) is True
    task = getattr(adapter, "_polling_heartbeat_task", None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    assert seen["drop_pending_updates"] is False
    assert "keeps the pending update queue" in caplog.text
