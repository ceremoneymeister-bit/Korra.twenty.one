"""K21-274: the Telegram queue survives a cold start and a redelivery is answered once."""
import asyncio
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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


def _event(chat_id="42"):
    return SimpleNamespace(source=SimpleNamespace(chat_id=chat_id))


@pytest.mark.asyncio
async def test_message_is_receipted_when_handed_to_the_gateway(tmp_path, monkeypatch):
    stop = _stop_exception(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    handed = []

    async def fake_handle(self, event):
        handed.append(event)

    monkeypatch.setattr("gateway.platforms.base.BasePlatformAdapter.handle_message", fake_handle)
    adapter = _adapter()
    await adapter._admit_update(_message_update(77), None)
    # admitted but still in the batching window: a redelivery must get through
    await adapter._admit_update(_message_update(77), None)
    await adapter.handle_message(_event())
    assert handed
    with pytest.raises(stop):
        await adapter._admit_update(_message_update(77), None)
    # a new process (new adapter) still remembers it
    with pytest.raises(stop):
        await _adapter()._admit_update(_message_update(77), None)
    # the same id on another bot is a different message
    await _adapter(token="999:zzz")._admit_update(_message_update(77), None)


@pytest.mark.asyncio
async def test_handoff_in_another_chat_does_not_receipt_the_message(tmp_path, monkeypatch):
    _stop_exception(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        "gateway.platforms.base.BasePlatformAdapter.handle_message", AsyncMock()
    )
    adapter = _adapter()
    await adapter._admit_update(_message_update(10, chat_id=1), None)
    await adapter.handle_message(_event(chat_id="2"))
    await adapter._admit_update(_message_update(10, chat_id=1), None)  # still not stopped


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
