"""The streamed compression summary must not outlive its waiting host."""

from __future__ import annotations

import ast
import asyncio
import inspect
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent import auxiliary_client as aux
from agent.conversation_compression import (
    DEFAULT_CONTEXT_TOTAL_CEILING_SECONDS,
    CompressionCommitFence,
    run_compress_context_with_progress_timeout,
)


def _chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        id="resp-1",
        model="test-model",
        usage=None,
        choices=[
            SimpleNamespace(
                index=0,
                finish_reason=None,
                delta=SimpleNamespace(content=text, tool_calls=None),
            )
        ],
    )


class _Stream:
    def __init__(self, count: int = 50) -> None:
        self._count = count
        self.yielded = 0
        self.closed = False

    def __iter__(self):
        for _ in range(self._count):
            self.yielded += 1
            yield _chunk("x")

    def close(self) -> None:
        self.closed = True


class _AsyncStream(_Stream):
    async def __aiter__(self):
        for _ in range(self._count):
            self.yielded += 1
            yield _chunk("x")


def test_stream_ceiling_structurally_outlives_the_default_host_ceiling():
    for aux_timeout in (None, 0, 30.0, 120.0, 300.0):
        assert (
            aux._aux_stream_total_ceiling(aux_timeout)
            >= DEFAULT_CONTEXT_TOTAL_CEILING_SECONDS
        )
    assert aux._aux_stream_total_ceiling(600.0) == 2400.0


def test_commit_fence_publishes_its_shared_deadline():
    fence = CompressionCommitFence()
    assert fence.deadline_monotonic is None

    fence.set_total_ceiling_seconds(600.0)
    published = fence.deadline_monotonic
    assert published is not None
    assert 590.0 < published - time.monotonic() <= 600.0

    fence.set_total_ceiling_seconds(0.001)
    time.sleep(0.01)
    assert fence.deadline_exceeded
    assert fence.deadline_monotonic <= time.monotonic()


def test_host_deadline_starts_after_cold_thread_context_setup():
    """One-time local setup must not expire the shared provider deadline."""
    fence = CompressionCommitFence()
    worker_started = threading.Event()

    def _slow_context_capture(target):
        time.sleep(0.15)
        return target

    def _worker(_fence):
        worker_started.set()
        assert not _fence.deadline_exceeded
        return ([{"role": "assistant", "content": "summary"}], "prompt")

    with patch(
        "tools.thread_context.propagate_context_to_thread",
        side_effect=_slow_context_capture,
    ):
        messages, prompt = run_compress_context_with_progress_timeout(
            worker=_worker,
            messages=[{"role": "user", "content": "source"}],
            system_prompt_fallback="fallback",
            idle_timeout_seconds=0.1,
            total_ceiling_seconds=0.1,
            fence=fence,
            stall_fallback=False,
        )

    assert worker_started.is_set()
    assert messages == [{"role": "assistant", "content": "summary"}]
    assert prompt == "prompt"


def test_streamed_summary_stops_at_an_elapsed_host_deadline():
    stream = _Stream(count=50)
    with aux.aux_stream_deadline(time.monotonic() - 1.0):
        with pytest.raises(TimeoutError, match="host compression deadline"):
            aux._aggregate_chat_stream(stream, model="m", total_ceiling=2400.0)

    assert stream.yielded == 1
    assert stream.closed is True


def test_live_stream_and_fenceless_call_keep_historical_behaviour():
    live = _Stream(count=5)
    with aux.aux_stream_deadline(time.monotonic() + 600.0):
        response = aux._aggregate_chat_stream(live, model="m", total_ceiling=2400.0)
    assert response.choices[0].message.content == "xxxxx"

    fenceless = _Stream(count=3)
    response = aux._aggregate_chat_stream(fenceless, model="m", total_ceiling=2400.0)
    assert response.choices[0].message.content == "xxx"


def test_deadline_scope_is_nested_and_does_not_leak():
    outer = time.monotonic() + 900.0
    with aux.aux_stream_deadline(outer):
        assert aux._current_aux_stream_deadline() == outer
        with aux.aux_stream_deadline(None):
            assert aux._current_aux_stream_deadline() == outer
        with aux.aux_stream_deadline(outer + 10.0):
            assert aux._current_aux_stream_deadline() == outer
        earlier = time.monotonic() + 10.0
        with aux.aux_stream_deadline(earlier):
            assert aux._current_aux_stream_deadline() == earlier
        assert aux._current_aux_stream_deadline() == outer
    assert aux._current_aux_stream_deadline() is None


def test_async_stream_mirror_honours_the_host_deadline():
    stream = _AsyncStream(count=50)

    async def _run():
        with aux.aux_stream_deadline(time.monotonic() - 1.0):
            return await aux._aggregate_chat_stream_async(
                stream, model="m", total_ceiling=2400.0
            )

    with pytest.raises(TimeoutError, match="host compression deadline"):
        asyncio.run(_run())
    assert stream.yielded == 1


def test_protected_provider_daemon_inherits_the_host_deadline():
    seen: dict[str, object] = {}

    def _callback(_kwargs):
        seen["deadline"] = aux._current_aux_stream_deadline()
        seen["thread"] = threading.current_thread().name
        return "ok"

    deadline = time.monotonic() + 42.0
    cancel_event = threading.Event()
    with (
        aux.aux_progress_hook(lambda: None),
        aux.aux_interrupt_protection(cancel_event=cancel_event),
        aux.aux_stream_deadline(deadline),
    ):
        assert aux._run_protected_sync_provider_call(_callback, {}) == "ok"

    assert seen["thread"] == "hermes-protected-aux-provider"
    assert seen["deadline"] == deadline


def test_compression_summary_dispatch_installs_the_fence_deadline():
    from agent import conversation_compression

    path = Path(inspect.getsourcefile(conversation_compression))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    wired = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        names = {
            item.context_expr.func.id
            for item in node.items
            if isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
        }
        if "aux_progress_hook" in names:
            assert "aux_stream_deadline" in names
            wired = True
    assert wired, "compression summary dispatch scope not found"
