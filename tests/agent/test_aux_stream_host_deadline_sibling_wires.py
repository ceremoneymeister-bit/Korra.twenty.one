"""Host compression deadlines cover Codex and Anthropic stream consumers."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent import auxiliary_client as aux
from agent.anthropic_adapter import create_anthropic_message


def _codex_content_event(text="tok"):
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def _consume_codex(stream, *, model, on_event):
    del model
    for event in stream:
        on_event(event)
    return SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="summary")],
            )
        ],
        usage=None,
    )


def _make_codex_adapter(event_iter):
    real_client = SimpleNamespace(
        base_url="https://chatgpt.com/backend-api/codex",
        responses=SimpleNamespace(create=lambda **_kwargs: event_iter),
        close=lambda: None,
    )
    return aux._CodexCompletionsAdapter(real_client, "gpt-5.6-sol")


def test_codex_live_stream_stops_at_the_host_deadline():
    yielded = [0]

    def _live_forever():
        while True:
            time.sleep(0.02)
            yielded[0] += 1
            yield _codex_content_event()

    adapter = _make_codex_adapter(_live_forever())
    started = time.monotonic()
    with (
        patch("agent.codex_runtime._consume_codex_event_stream", _consume_codex),
        aux.aux_stream_deadline(time.monotonic() + 0.4),
        pytest.raises(TimeoutError, match="hard ceiling"),
    ):
        adapter.create(
            messages=[{"role": "user", "content": "summarize"}],
            timeout=300,
        )
    assert time.monotonic() - started < 5.0
    assert yielded[0] < 100


def test_codex_keepalives_cannot_extend_the_host_deadline():
    yielded = [0]

    def _keepalives_forever():
        while True:
            time.sleep(0.02)
            yielded[0] += 1
            yield SimpleNamespace(type="response.in_progress")

    adapter = _make_codex_adapter(_keepalives_forever())
    with (
        patch("agent.codex_runtime._consume_codex_event_stream", _consume_codex),
        aux.aux_stream_deadline(time.monotonic() + 0.3),
        pytest.raises(TimeoutError, match="hard ceiling"),
    ):
        adapter.create(messages=[{"role": "user", "content": "summarize"}], timeout=300)
    assert yielded[0] < 100


def test_codex_full_silence_uses_request_timeout_without_closing_shared_client():
    create_finished = threading.Event()
    closed = threading.Event()
    observed_timeout = []

    def _blocked_create(**kwargs):
        observed_timeout.append(kwargs["timeout"])
        # Model an SDK request-local timeout while waiting for response
        # headers. It ends the attempt without touching the shared client.
        time.sleep(float(kwargs["timeout"]) + 0.05)
        create_finished.set()
        raise TimeoutError("request-local header timeout")

    real_client = SimpleNamespace(
        base_url="https://chatgpt.com/backend-api/codex",
        responses=SimpleNamespace(create=_blocked_create),
        close=closed.set,
    )
    adapter = aux._CodexCompletionsAdapter(real_client, "gpt-5.6-sol")
    deadline = time.monotonic() + 0.3
    outcome = {}

    def _run():
        try:
            with aux.aux_stream_deadline(deadline):
                adapter.create(
                    messages=[{"role": "user", "content": "summarize"}],
                    timeout=300,
                )
        except BaseException as exc:  # the result is asserted in the owner
            outcome["exception"] = exc

    worker = threading.Thread(target=_run, daemon=True)
    retired = threading.Event()
    with patch(
        "agent.auxiliary_client._evict_cached_client_instance",
        side_effect=lambda _client: retired.set(),
    ):
        worker.start()
        assert retired.wait(timeout=5.0), "host deadline never retired the client"
        assert create_finished.wait(timeout=5.0)
        worker.join(timeout=5.0)
        assert isinstance(outcome.get("exception"), TimeoutError)
        assert "hard ceiling" in str(outcome["exception"])
    assert observed_timeout and observed_timeout[0] <= 0.3
    assert not closed.is_set()


def test_codex_without_host_deadline_completes_normally():
    adapter = _make_codex_adapter(
        iter([_codex_content_event(), _codex_content_event()])
    )
    with patch("agent.codex_runtime._consume_codex_event_stream", _consume_codex):
        response = adapter.create(
            messages=[{"role": "user", "content": "summarize"}], timeout=300
        )
    assert response.choices[0].message.content == "summary"


class _AnthropicStream:
    def __init__(self, count=10_000, delay=0.01, event_factory=None):
        self._count = count
        self._delay = delay
        self._event_factory = event_factory or (
            lambda: SimpleNamespace(
                type="content_block_delta", delta=SimpleNamespace(text="tok")
            )
        )
        self.yielded = 0
        self.exited = False
        self.response = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.exited = True
        return False

    def __iter__(self):
        for _ in range(self._count):
            time.sleep(self._delay)
            self.yielded += 1
            yield self._event_factory()

    def get_final_message(self):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="summary")])


def _anthropic_client(stream):
    return SimpleNamespace(
        messages=SimpleNamespace(
            stream=lambda **_kwargs: stream,
            create=lambda **_kwargs: pytest.fail("must not fall back to create()"),
        )
    )


def test_anthropic_live_stream_stops_and_closes_at_the_host_deadline():
    stream = _AnthropicStream()
    ticks = []
    with (
        aux.aux_progress_hook(lambda: ticks.append(1)),
        aux.aux_stream_deadline(time.monotonic() + 0.3),
    ):
        hook = aux._anthropic_aux_stream_event_hook()
        with pytest.raises(
            TimeoutError, match="timed out at the host compression deadline"
        ):
            create_anthropic_message(
                _anthropic_client(stream),
                {"model": "m", "messages": []},
                on_stream_event=hook,
            )
    assert stream.exited
    assert ticks
    assert stream.yielded < 1000


def test_anthropic_keepalives_do_not_count_as_progress_or_escape_deadline():
    stream = _AnthropicStream(
        event_factory=lambda: SimpleNamespace(type="message_start")
    )
    ticks = []
    with (
        aux.aux_progress_hook(lambda: ticks.append(1)),
        aux.aux_stream_deadline(time.monotonic() + 0.3),
    ):
        with pytest.raises(TimeoutError, match="host compression deadline"):
            create_anthropic_message(
                _anthropic_client(stream),
                {"model": "m", "messages": []},
                on_stream_event=aux._anthropic_aux_stream_event_hook(),
            )
    assert ticks == []
    assert stream.exited


def test_anthropic_full_silence_wakes_and_closes_at_the_host_deadline():
    release = threading.Event()

    class _SilentAnthropicStream(_AnthropicStream):
        def __init__(self):
            super().__init__(count=0)
            self.closed = False

        def __iter__(self):
            release.wait(timeout=10.0)
            return iter(())

        def close(self):
            self.closed = True
            release.set()

    stream = _SilentAnthropicStream()
    adapter = aux._AnthropicCompletionsAdapter(
        _anthropic_client(stream), "claude-test"
    )
    started = time.monotonic()
    try:
        with (
            aux.aux_progress_hook(lambda: None),
            aux.aux_stream_deadline(time.monotonic() + 0.3),
            pytest.raises(TimeoutError, match="host compression deadline"),
        ):
            adapter.create(
                messages=[{"role": "user", "content": "summarize"}],
                timeout=900,
            )
    finally:
        release.set()
    assert time.monotonic() - started < 5.0
    assert stream.closed
    assert stream.exited


def test_anthropic_stream_honours_explicit_hard_cancel():
    stream = _AnthropicStream()
    cancelled = {"value": False}
    with (
        aux.aux_progress_hook(lambda: None),
        aux.aux_interrupt_protection(cancel_check=lambda: cancelled["value"]),
    ):
        hook = aux._anthropic_aux_stream_event_hook()

        def _cancel_after_first(event):
            cancelled["value"] = True
            hook(event)

        with pytest.raises(aux.AuxiliaryExplicitCancellation):
            create_anthropic_message(
                _anthropic_client(stream),
                {"model": "m", "messages": []},
                on_stream_event=_cancel_after_first,
            )
    assert stream.yielded == 1
    assert stream.exited


def test_anthropic_without_host_deadline_completes_normally():
    stream = _AnthropicStream(count=5, delay=0)
    with aux.aux_progress_hook(lambda: None):
        message = create_anthropic_message(
            _anthropic_client(stream),
            {"model": "m", "messages": []},
            on_stream_event=aux._anthropic_aux_stream_event_hook(),
        )
    assert message.content[0].text == "summary"
    assert stream.yielded == 5
