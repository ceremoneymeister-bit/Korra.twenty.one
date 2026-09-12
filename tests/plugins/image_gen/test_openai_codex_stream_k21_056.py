"""K21-056: a finished Codex image must not be lost by the stream parser.

Live shape recorded 12.09.2026 on Dmitry's contour: the backend emits the
completed ``image_generation_call`` in ``response.output_item.done`` and then a
terminal ``response.completed`` whose ``output`` is ``[]``. The previous parser
read only the terminal output, found zero calls and reported ``empty_response``
for every request — while the quota was spent and a PNG had already arrived.

These fixtures drive the real SSE parser (``_iter_sse_json`` over an httpx-like
stream) rather than a mocked ``_collect_image_b64`` so the exact code path that
lost the image is the one under test.
"""

from __future__ import annotations

import base64
import importlib
import io
import json
from typing import Any, Dict, Iterable, List

import httpx
import pytest
from PIL import Image


provider_mod = importlib.import_module("plugins.image_gen.openai-codex")


def _png_b64(color=(200, 100, 50)) -> str:
    out = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode("ascii")


def _credentials():
    return {"api_key": "token", "base_url": "https://chatgpt.com/backend-api/codex"}


def _sse_lines(events: Iterable[Dict[str, Any]]) -> List[str]:
    """Render events the way the Codex backend does: ``event:`` + ``data:``."""
    lines: List[str] = []
    for event in events:
        lines.append(f"event: {event['type']}")
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")
    return lines


def _install_stream(monkeypatch, events: Iterable[Dict[str, Any]]):
    lines = _sse_lines(events)

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            return iter(lines)

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr(httpx, "Client", _Client)


def _collect():
    return provider_mod._collect_image_b64(
        "token", prompt="portrait", size="1024x1024", quality="medium"
    )


def _live_shape_events(result_b64: str, *, terminal_output=None) -> List[Dict[str, Any]]:
    """The 12.09.2026 recording: finished item early, empty terminal output."""
    return [
        {"type": "response.created", "response": {"id": "resp_1", "status": "in_progress", "output": []}},
        {"type": "response.in_progress", "response": {"id": "resp_1", "status": "in_progress", "output": []}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"id": "ig_1", "type": "image_generation_call", "status": "in_progress", "result": None},
        },
        {"type": "response.image_generation_call.in_progress", "item_id": "ig_1", "output_index": 0},
        {"type": "response.image_generation_call.generating", "item_id": "ig_1", "output_index": 0},
        {"type": "response.image_generation_call.completed", "item_id": "ig_1", "output_index": 0},
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "ig_1",
                "type": "image_generation_call",
                "status": "completed",
                "result": result_b64,
                "output_format": "png",
                "size": "1024x1024",
            },
        },
        {
            "type": "response.completed",
            "response": {
                "id": "resp_1",
                "status": "completed",
                "output": [] if terminal_output is None else terminal_output,
            },
        },
    ]


# ── Recorded live shape ───────────────────────────────────────────────────────


def test_completed_item_survives_empty_terminal_output(monkeypatch):
    image = _png_b64()
    _install_stream(monkeypatch, _live_shape_events(image))

    assert _collect() == {"b64": image, "source": "final"}


def test_generate_saves_file_from_live_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(provider_mod, "_resolve_codex_credentials", _credentials)
    _install_stream(monkeypatch, _live_shape_events(_png_b64()))

    result = provider_mod.OpenAICodexImageGenProvider().generate("simple poster")

    assert result["success"] is True, result
    assert result["image_source"] == "final"
    saved = list((tmp_path / "cache" / "images").glob("*.png"))
    assert len(saved) == 1


def test_same_call_in_stream_and_terminal_counts_once(monkeypatch):
    image = _png_b64()
    terminal_item = {
        "id": "ig_1",
        "type": "image_generation_call",
        "status": "completed",
        "result": image,
    }
    _install_stream(monkeypatch, _live_shape_events(image, terminal_output=[terminal_item]))

    assert _collect() == {"b64": image, "source": "final"}


# ── Guards that must survive the fix ─────────────────────────────────────────


def test_partial_frame_only_is_not_a_result(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(provider_mod, "_resolve_codex_credentials", _credentials)
    events = [
        {"type": "response.created", "response": {"id": "resp_2", "status": "in_progress", "output": []}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"id": "ig_2", "type": "image_generation_call", "status": "in_progress", "result": None},
        },
        {
            "type": "response.image_generation_call.partial_image",
            "item_id": "ig_2",
            "output_index": 0,
            "partial_image_index": 0,
            "partial_image_b64": _png_b64(),
        },
        {"type": "response.completed", "response": {"id": "resp_2", "status": "completed", "output": []}},
    ]
    _install_stream(monkeypatch, events)

    with pytest.raises(provider_mod.CodexImageEmptyResponse) as excinfo:
        _collect()
    assert "partial_image frames: 1" in str(excinfo.value)

    result = provider_mod.OpenAICodexImageGenProvider().generate("poster")
    assert result["success"] is False
    assert result["error_type"] == "empty_response"
    assert result["stream_diagnostics"]["partial_frames"] == 1
    assert not list((tmp_path / "cache" / "images").glob("*"))


def test_in_progress_item_without_result_is_not_a_result(monkeypatch):
    events = [
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {"id": "ig_3", "type": "image_generation_call", "status": "in_progress", "result": None},
        },
        {"type": "response.completed", "response": {"id": "resp_3", "status": "completed", "output": []}},
    ]
    _install_stream(monkeypatch, events)

    with pytest.raises(provider_mod.CodexImageEmptyResponse) as excinfo:
        _collect()
    assert "image_generation_call status: in_progress" in str(excinfo.value)


def test_two_distinct_calls_are_still_an_error(monkeypatch):
    image = _png_b64()
    second = {
        "id": "ig_other",
        "type": "image_generation_call",
        "status": "completed",
        "result": _png_b64(color=(1, 2, 3)),
    }
    _install_stream(monkeypatch, _live_shape_events(image, terminal_output=[second]))

    with pytest.raises(RuntimeError, match="multiple image_generation calls"):
        _collect()


def test_same_result_without_id_is_one_call(monkeypatch):
    """Streamed copy without id + terminal copy with id = one generation."""
    image = _png_b64()
    events = [
        {
            "type": "response.output_item.done",
            "item": {"type": "image_generation_call", "status": "completed", "result": image},
        },
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": [{
                    "id": "call-1",
                    "type": "image_generation_call",
                    "status": "completed",
                    "result": image,
                }],
            },
        },
    ]
    _install_stream(monkeypatch, events)

    assert _collect() == {"b64": image, "source": "final"}


def test_early_item_without_terminal_completed_is_an_error(monkeypatch):
    events = _live_shape_events(_png_b64())[:-1]
    _install_stream(monkeypatch, events)

    with pytest.raises(RuntimeError) as excinfo:
        _collect()
    message = str(excinfo.value)
    assert "without response.completed" in message
    assert "response.output_item.done×1" in message
    assert "no terminal response.* event" in message


# ── The error must say what actually arrived ─────────────────────────────────


def test_refusal_text_reaches_the_error(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(provider_mod, "_resolve_codex_credentials", _credentials)
    message_item = {
        "id": "msg_1",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "refusal", "refusal": "I can't create images of real people."}],
    }
    events = [
        {"type": "response.created", "response": {"id": "resp_4", "status": "in_progress", "output": []}},
        {"type": "response.output_item.added", "output_index": 0, "item": {**message_item, "status": "in_progress", "content": []}},
        {"type": "response.refusal.done", "item_id": "msg_1", "refusal": "I can't create images of real people."},
        {"type": "response.output_item.done", "output_index": 0, "item": message_item},
        {"type": "response.completed", "response": {"id": "resp_4", "status": "completed", "output": [message_item]}},
    ]
    _install_stream(monkeypatch, events)

    result = provider_mod.OpenAICodexImageGenProvider().generate("portrait of a person")

    assert result["success"] is False
    assert result["error_type"] == "empty_response"
    assert "refusal: I can't create images of real people." in result["error"]
    assert "output items: message" in result["error"]
    assert "terminal response.completed status=completed" in result["error"]
    assert result["stream_diagnostics"]["refusals"] == ["I can't create images of real people."]


def test_incomplete_details_reach_the_error(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(provider_mod, "_resolve_codex_credentials", _credentials)
    events = [
        {"type": "response.created", "response": {"id": "resp_5", "status": "in_progress", "output": []}},
        {
            "type": "response.incomplete",
            "response": {
                "id": "resp_5",
                "status": "incomplete",
                "output": [],
                "incomplete_details": {"reason": "content_filter"},
            },
        },
    ]
    _install_stream(monkeypatch, events)

    result = provider_mod.OpenAICodexImageGenProvider().generate("poster")

    assert result["success"] is False
    assert result["error_type"] == "api_error"
    assert "response.incomplete" in result["error"]
    assert "incomplete_details.reason=content_filter" in result["error"]


def test_failed_response_error_message_reaches_the_error(monkeypatch):
    events = [
        {
            "type": "response.failed",
            "response": {
                "id": "resp_6",
                "status": "failed",
                "output": [],
                "error": {"code": "server_error", "message": "image backend unavailable"},
            },
        },
    ]
    _install_stream(monkeypatch, events)

    with pytest.raises(RuntimeError) as excinfo:
        _collect()
    message = str(excinfo.value)
    assert "response.failed" in message
    assert "error: image backend unavailable" in message


def test_diagnostics_redact_secrets():
    diagnostics = provider_mod.CodexImageStreamDiagnostics()
    diagnostics.observe({
        "type": "error",
        "message": "rejected Authorization: Bearer sk-live-secret-token",
    })
    summary = diagnostics.summary()
    assert "sk-live-secret-token" not in summary
    assert "[REDACTED]" in summary


def test_empty_response_error_carries_event_inventory(monkeypatch, tmp_path):
    """A stream with no image at all must list every event type it carried."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(provider_mod, "_resolve_codex_credentials", _credentials)
    events = [
        {"type": "response.created", "response": {"id": "r", "status": "in_progress", "output": []}},
        {"type": "response.in_progress", "response": {"id": "r", "status": "in_progress", "output": []}},
        {"type": "response.completed", "response": {"id": "r", "status": "completed", "output": []}},
    ]
    _install_stream(monkeypatch, events)

    result = provider_mod.OpenAICodexImageGenProvider().generate("poster")

    assert result["error_type"] == "empty_response"
    assert "events: response.created×1, response.in_progress×1, response.completed×1" in result["error"]
    assert result["stream_diagnostics"]["event_types"] == {
        "response.created": 1,
        "response.in_progress": 1,
        "response.completed": 1,
    }
