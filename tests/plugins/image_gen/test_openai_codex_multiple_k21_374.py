"""K21-374: keep the last finished image without accepting unfinished streams.

The SSE fixture reconstructs the recorded six-item response shape; original
pixels were not retained by the failing provider. All pixels and IDs here are
synthetic. Exercise the real HTTP/SSE, validation, file and receipt path.
"""

import base64
import importlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest


plugin = importlib.import_module("plugins.image_gen.openai-codex")


@pytest.fixture
def wire(tmp_path, monkeypatch, no_real_network):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(plugin, "_CODEX_HOST_CACHE", {})
    monkeypatch.setattr(plugin, "_resolve_codex_credentials", lambda: {
        "api_key": "fake-oauth-token", "base_url": "https://codex.invalid",
    })
    monkeypatch.setattr("korra_cli.codex_models.get_codex_model_ids", lambda **kw: [
        "fixture-host", "unused-fallback-host",
    ])
    fixture = Path(__file__).with_name("fixtures") / "codex_multiple_completed.sse"
    events = [json.loads(line[6:]) for line in fixture.read_text().splitlines()
              if line.startswith("data: ")]
    state = SimpleNamespace(events=events, requests=[], root=tmp_path)
    state.first, state.last = [event["item"] for event in events
                               if event["type"] == "response.output_item.done"
                               and event["item"]["type"] == "image_generation_call"]

    def respond(request):
        state.requests.append(json.loads(request.content))
        text = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in state.events)
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(
        transport=httpx.MockTransport(respond), **kw,
    ))
    return state


def generate(wire):
    return plugin.OpenAICodexImageGenProvider().generate(
        "Synthetic slide correction", action="edit", receipt=True,
        image_url=f"data:image/png;base64,{wire.first['result']}",
    )


@pytest.mark.parametrize("terminal", ["full", "empty", "first", "last", "only"])
def test_two_completed_calls_save_last_without_retry(wire, terminal):
    output = wire.events[-1]["response"]["output"]
    if terminal == "empty":
        wire.events[-1]["response"]["output"] = []
    elif terminal in {"first", "last"}:
        wire.events[-1]["response"]["output"] = [output[1 if terminal == "first" else 3]]
    elif terminal == "only":
        wire.events = wire.events[-1:]

    result = generate(wire)

    assert result["success"] is True, result
    assert result["image_source"] == "final"
    assert Path(result["image"]).read_bytes() == base64.b64decode(wire.last["result"])
    assert len(list(wire.root.glob("cache/images/*.png"))) == 1
    assert len(wire.requests) == 1
    assert wire.requests[0]["tools"][0]["action"] == "edit"


def test_diagnostics_and_receipt_count_distinct_calls_once(wire, caplog):
    with caplog.at_level(logging.WARNING, logger=plugin.__name__):
        result = generate(wire)
    assert result["success"] is True, result
    diagnostics = result["stream_diagnostics"]
    assert diagnostics["image_call_count"] == 2
    assert diagnostics["terminal_status"] == "completed"
    assert json.loads(Path(result["receipt"]).read_text())["stream_diagnostics"] == diagnostics
    assert "image_generation calls: 2" in caplog.text
    assert "last completed" in caplog.text
    for call in (wire.first, wire.last):
        assert call["result"] not in caplog.text


def test_distinct_ids_with_identical_pixels_are_two_calls(wire):
    for node in (wire.last, wire.events[-1]["response"]["output"][3]):
        node["result"] = wire.first["result"]
    result = generate(wire)
    assert result["success"] is True, result
    assert result["stream_diagnostics"]["image_call_count"] == 2


def test_repeated_copy_is_one_call(wire):
    wire.events[-1]["response"]["output"] = [wire.first]
    wire.events = [e for e in wire.events if e.get("item", {}).get("id") != wire.last["id"]]
    result = generate(wire)
    assert result["success"] is True, result
    assert result["stream_diagnostics"]["image_call_count"] == 1


def test_output_order_survives_out_of_order_done_events(wire):
    done = [e for e in wire.events if e["type"] == "response.output_item.done"
            and e["item"]["type"] == "image_generation_call"]
    wire.events = [e for e in wire.events if e not in done]
    wire.events[-1:-1] = reversed(done)
    wire.events[-1]["response"]["output"] = []
    result = generate(wire)
    assert result["success"] is True, result
    assert Path(result["image"]).read_bytes() == base64.b64decode(wire.last["result"])


def test_missing_id_copy_preserves_last_streamed_position(wire):
    wire.events[-1]["response"]["output"] = [{k: v for k, v in wire.last.items() if k != "id"}]
    result = generate(wire)
    assert result["success"] is True, result
    assert Path(result["image"]).read_bytes() == base64.b64decode(wire.last["result"])
    assert result["stream_diagnostics"]["image_call_count"] == 2


def test_payload_requests_exactly_one_image_call(wire):
    generate(wire)
    assert "exactly one call to image_generation" in wire.requests[0]["instructions"]
    assert "tool_choice" not in wire.requests[0]


@pytest.mark.parametrize("source", ["terminal", "stream"])
@pytest.mark.parametrize("status", ["in_progress", "incomplete", "failed"])
def test_unfinished_second_call_never_falls_back_to_first(wire, source, status):
    if source == "terminal":
        wire.events[-1]["response"]["output"][3]["status"] = status
    else:
        wire.last["status"] = status
        wire.events[-1]["response"]["output"] = []
    result = generate(wire)
    assert result["success"] is False
    assert result["error_type"] == "api_error"
    assert len(wire.requests) == 1
    assert not list(wire.root.glob("cache/images/*"))


@pytest.mark.parametrize("terminal_output", ["empty", "first"])
def test_second_call_with_only_preview_cannot_return_first_as_final(wire, terminal_output):
    wire.events = [e for e in wire.events if not (
        e["type"] == "response.output_item.done" and e.get("item", {}).get("id") == wire.last["id"]
    )]
    wire.events[-1]["response"]["output"] = [] if terminal_output == "empty" else [wire.first]
    wire.events.insert(-1, {
        "type": "response.image_generation_call.partial_image", "item_id": wire.last["id"],
        "output_index": 3, "partial_image_b64": wire.last["result"],
    })
    result = generate(wire)
    assert result["success"] is False
    assert result["error_type"] == "api_error"
    assert len(wire.requests) == 1
    assert not list(wire.root.glob("cache/images/*"))


@pytest.mark.parametrize("value", [None, "", "not base64", "YWJj"])
def test_bad_last_result_never_falls_back_to_first(wire, value):
    wire.events[-1]["response"]["output"][3]["result"] = value
    result = generate(wire)
    assert result["success"] is False
    assert result["error_type"] in {"empty_response", "invalid_image_output"}
    assert len(wire.requests) == 1
    assert not list(wire.root.glob("cache/images/*"))


def test_empty_first_call_is_not_hidden_by_valid_second(wire):
    wire.events[-1]["response"]["output"][1]["result"] = ""
    result = generate(wire)
    assert result["success"] is False
    assert result["error_type"] == "empty_response"
    assert result["stream_diagnostics"]["image_call_count"] == 2
    assert len(wire.requests) == 1
    assert not list(wire.root.glob("cache/images/*"))


def test_preview_is_never_selected_over_completed_second_call(wire):
    wire.events.insert(-1, {
        "type": "response.image_generation_call.partial_image", "item_id": wire.last["id"],
        "output_index": 3, "partial_image_b64": wire.first["result"],
    })
    result = generate(wire)
    assert result["success"] is True, result
    assert Path(result["image"]).read_bytes() == base64.b64decode(wire.last["result"])
    assert result["stream_diagnostics"]["partial_frames"] == 1
    assert len(wire.requests) == 1


@pytest.mark.parametrize("ending", ["truncated", "failed", "incomplete", "wrong_status"])
def test_completed_items_do_not_override_unsuccessful_response(wire, ending):
    if ending == "truncated":
        wire.events.pop()
    elif ending == "wrong_status":
        wire.events[-1]["response"]["status"] = "in_progress"
    else:
        wire.events[-1]["type"] = f"response.{ending}"
        wire.events[-1]["response"]["status"] = ending
    result = generate(wire)
    assert result["success"] is False
    assert result["error_type"] == "api_error"
    assert len(wire.requests) == 1
    assert not list(wire.root.glob("cache/images/*"))
