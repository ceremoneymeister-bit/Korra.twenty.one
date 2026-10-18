"""QA 17-18.10: when the terminal error frame (chat error chunk /
response.failed) itself cannot be written, log a warning instead of
swallowing it silently."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter, ThreadSafeAsyncQueue


def _request():
    req = MagicMock()
    req.headers = {}
    return req


def _broken_response():
    resp = AsyncMock(spec=web.StreamResponse)
    resp.prepare = AsyncMock()
    resp.write = AsyncMock(side_effect=ValueError("transport in bad state"))
    return resp


async def _never():
    await asyncio.sleep(3600)


def _warnings(caplog, needle):
    return [r for r in caplog.records
            if r.levelno == logging.WARNING and needle in r.getMessage()]


def test_chat_completion_error_chunk_write_failure_is_logged(caplog):
    adapter = APIServerAdapter(PlatformConfig(enabled=True, token="k"))

    async def run():
        q = ThreadSafeAsyncQueue()
        q.put_nowait("partial")
        task = asyncio.ensure_future(_never())
        with patch("gateway.platforms.api_server.web.StreamResponse",
                   return_value=_broken_response()):
            await adapter._write_sse_chat_completion(
                _request(), "cmpl-x", "m", 1, q, task,
            )
        task.cancel()

    with caplog.at_level(logging.WARNING, logger="gateway.platforms.api_server"):
        asyncio.run(run())
    assert _warnings(caplog, "error chunk")


def test_responses_failed_event_write_failure_is_logged(caplog):
    adapter = APIServerAdapter(PlatformConfig(enabled=True, token="k"))

    async def run():
        q = ThreadSafeAsyncQueue()
        q.put_nowait("partial")
        task = asyncio.ensure_future(_never())
        with patch("gateway.platforms.api_server.web.StreamResponse",
                   return_value=_broken_response()):
            await adapter._write_sse_responses(
                _request(), "resp-x", "m", 1, q, task, [None], [], "hi",
                None, None, False, "sess-x",
            )
        task.cancel()

    with caplog.at_level(logging.WARNING, logger="gateway.platforms.api_server"):
        asyncio.run(run())
    assert _warnings(caplog, "response.failed")
