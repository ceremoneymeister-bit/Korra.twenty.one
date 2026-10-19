"""QA 17-18.10: when the agent raises and saving the inbound user message
fails, the gateway retries the write and logs the failure at ERROR."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.gateway.test_42039_duplicate_user_message import _bootstrap, _event, _source

_KEY = "agent:main:telegram:group:-1001:12345"


def _user_calls(mock):
    return [c for c in mock.call_args_list
            if len(c.args) >= 2 and isinstance(c.args[1], dict)
            and c.args[1].get("role") == "user"]


@pytest.mark.asyncio
async def test_transient_persist_failure_is_retried(monkeypatch, tmp_path):
    runner = _bootstrap(monkeypatch, tmp_path)
    runner._run_agent = AsyncMock(side_effect=RuntimeError("client init failed"))
    attempts = []

    def flaky(session_id, entry, *a, **kw):
        if entry.get("role") == "user":
            attempts.append(entry)
            if len(attempts) == 1:
                raise OSError("database is locked")

    runner.session_store.append_to_transcript = MagicMock(side_effect=flaky)

    await runner._handle_message_with_agent(_event(), _source(), _KEY, 1)

    assert len(attempts) == 2
    assert attempts[1]["content"] == "hello world"


@pytest.mark.asyncio
async def test_persistent_persist_failure_logs_error(monkeypatch, tmp_path, caplog):
    runner = _bootstrap(monkeypatch, tmp_path)
    runner._run_agent = AsyncMock(side_effect=RuntimeError("client init failed"))
    runner.session_store.append_to_transcript = MagicMock(side_effect=OSError("disk full"))

    with caplog.at_level(logging.DEBUG, logger="gateway.run"):
        await runner._handle_message_with_agent(_event(), _source(), _KEY, 1)

    records = [r for r in caplog.records
               if "persist inbound user message" in r.getMessage()]
    assert records and records[-1].levelno == logging.ERROR
    assert len(_user_calls(runner.session_store.append_to_transcript)) == 2
