"""K21-371: subscription image requests survive a retired Responses host.

Exercise real config loading, model discovery, payload, SSE parsing and output
publication. Only OAuth credentials and the HTTP transport are synthetic.
"""

import base64
import importlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from agent import models_dev
from korra_cli import codex_models

plugin = importlib.import_module("plugins.image_gen.openai-codex")


@pytest.fixture
def wire(tmp_path, monkeypatch, no_real_network):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(plugin, "_CODEX_HOST_CACHE", {}, raising=False)
    monkeypatch.setattr(models_dev, "get_model_capabilities", lambda *a, **kw: None)
    state = SimpleNamespace(
        models=["gpt-6.1-sol", "gpt-5.6-sol"],
        requests=[], probes=[], replies=[], token="fake-oauth-token", root=tmp_path,
    )
    monkeypatch.setattr(plugin, "_resolve_codex_credentials", lambda: {
        "api_key": state.token, "base_url": "https://chatgpt.com/backend-api/codex",
    })

    def catalog(url, **kwargs):
        state.probes.append(kwargs)
        if state.models is None:
            raise httpx.ConnectError("catalog offline")
        return httpx.Response(200, json={"models": [
            {"slug": model, "priority": index} for index, model in enumerate(state.models)
        ]})

    monkeypatch.setattr(httpx, "get", catalog)

    buf = io.BytesIO()
    Image.new("RGB", (2, 3), (10, 20, 30)).save(buf, format="PNG")
    state.image = base64.b64encode(buf.getvalue()).decode()
    state.events = [{"type": "response.completed", "response": {
        "status": "completed", "output": [{
            "type": "image_generation_call", "status": "completed", "result": state.image,
        }],
    }}]

    def respond(request):
        assert request.url.path == "/backend-api/codex/responses"
        assert request.headers["Authorization"] == f"Bearer {state.token}"
        state.requests.append(json.loads(request.content))
        if state.replies:
            status, body = state.replies.pop(0)
            return httpx.Response(status, json=body)
        text = "".join(f"data: {json.dumps(event)}\n\n" for event in state.events)
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    return state


def generate(**kwargs):
    # A fresh provider must still reuse the process's connection-specific choice.
    return plugin.OpenAICodexImageGenProvider().generate("Нарисуй дом", **kwargs)


def unsupported(model):
    return (400, {"detail": (
        f"The '{model}' model is not supported when using Codex with a ChatGPT account."
    )})


def test_payload_uses_preferred_live_host_and_preserves_image_model(wire):
    result = generate(model="gpt-image-2.5-flare", receipt=True)
    assert result["success"] is True
    payload = wire.requests[0]
    assert payload["model"] == "gpt-5.6-sol"
    assert payload["model"] != "gpt-5.5"
    assert payload["tools"][0]["model"] == "gpt-image-2.5-flare"
    assert "tool_choice" not in payload
    receipt = json.loads(Path(result["receipt"]).read_text())
    assert receipt["host_model"] == payload["model"]
    assert wire.probes[0]["headers"]["Authorization"] == f"Bearer {wire.token}"


def test_uses_catalog_when_preferred_hosts_are_absent(wire):
    wire.models = ["future-codex-host"]
    assert generate()["success"] is True
    assert wire.requests[0]["model"] == "future-codex-host"


def test_skips_catalog_models_known_to_lack_tools(wire, monkeypatch):
    wire.models = ["text-only-host", "future-tool-host"]
    monkeypatch.setattr(models_dev, "get_model_capabilities", lambda provider, model, **kw:
                        SimpleNamespace(supports_tools=model != "text-only-host"))
    assert generate()["success"] is True
    assert wire.requests[0]["model"] == "future-tool-host"


def test_context_picker_alias_is_not_sent_or_retried(wire):
    wire.models = ["gpt-5.6-sol-900k", "gpt-6.1-sol"]
    wire.replies = [unsupported("gpt-5.6-sol")]
    assert generate()["success"] is True
    assert [p["model"] for p in wire.requests] == ["gpt-5.6-sol", "gpt-6.1-sol"]


@pytest.mark.parametrize("offline", [True, False])
def test_unavailable_catalog_uses_verified_offline_order(wire, monkeypatch, offline):
    if offline:
        wire.models = None
    else:
        monkeypatch.setattr(codex_models, "get_codex_model_ids", lambda *a, **kw: [])
    wire.replies = [unsupported("gpt-5.6-sol")]
    assert generate()["success"] is True
    assert [p["model"] for p in wire.requests] == ["gpt-5.6-sol", "gpt-6.1-sol"]


def test_model_rejection_retries_once_and_caches_success(wire):
    wire.replies = [unsupported("gpt-5.6-sol")]
    result = generate(receipt=True)
    assert result["success"] is True
    assert [p["model"] for p in wire.requests] == ["gpt-5.6-sol", "gpt-6.1-sol"]
    assert json.loads(Path(result["receipt"]).read_text())["host_model"] == "gpt-6.1-sol"
    assert generate()["success"] is True
    assert [p["model"] for p in wire.requests] == [
        "gpt-5.6-sol", "gpt-6.1-sol", "gpt-6.1-sol",
    ]
    assert len(wire.probes) == 1


def test_retry_preserves_image_edit_and_every_other_payload_field(wire):
    wire.replies = [unsupported("gpt-5.6-sol")]
    assert generate(
        image_url=f"data:image/png;base64,{wire.image}",
        reference_image_urls=[f"data:image/png;base64,{wire.image}"],
        reference_roles=["style"], model="gpt-image-2.5-flare",
    )["success"] is True
    first, second = wire.requests
    assert first["model"] != second["model"]
    assert {k: v for k, v in first.items() if k != "model"} == {
        k: v for k, v in second.items() if k != "model"
    }
    assert len(second["input"][0]["content"]) == 3


@pytest.mark.parametrize("body", [
    {"error": {"message": "The 'gpt-5.6-sol' model is not supported."}},
    {"detail": 'Model "gpt-5.6-sol" is not supported for this account.'},
])
def test_supported_rejection_body_shapes(wire, body):
    wire.replies = [(400, body)]
    assert generate()["success"] is True
    assert len(wire.requests) == 2


def test_cached_host_can_be_replaced_if_it_is_later_retired(wire):
    assert generate()["success"] is True
    wire.replies = [unsupported("gpt-5.6-sol")]
    assert generate()["success"] is True
    assert generate()["success"] is True
    assert [p["model"] for p in wire.requests] == [
        "gpt-5.6-sol", "gpt-5.6-sol", "gpt-6.1-sol", "gpt-6.1-sol",
    ]
    assert len(wire.probes) == 1


@pytest.mark.parametrize("status,body", [
    (400, {"error": {"message": "Tool choice 'image_generation' not found in 'tools' parameter."}}),
    (400, {"detail": "The model's image_generation tool is not supported."}),
    (400, {"detail": "The 'gpt-image-2.5-sunburst' model is not supported."}),
    (400, {"detail": "The 'gpt-5.6-sol' model does not support this image size."}),
    (401, {"detail": "The 'gpt-5.6-sol' model is not supported."}),
    (429, {"detail": "Too many requests"}),
    (500, {"detail": "The 'gpt-5.6-sol' model is not supported."}),
])
def test_other_errors_do_not_trigger_generation_retry(wire, status, body):
    wire.replies = [(status, body)]
    result = generate()
    assert result["success"] is False
    assert result["error_type"] == "api_error"
    assert len(wire.requests) == 1


def test_two_rejections_stop_with_plain_russian_error(wire):
    wire.models += ["third-host-must-not-be-tried"]
    wire.replies = [unsupported("gpt-5.6-sol"), unsupported("gpt-6.1-sol")]
    result = generate()
    assert result["success"] is False
    assert result["error_type"] == "host_model_unavailable"
    assert "подписк" in result["error"].lower()
    assert "позже" in result["error"].lower()
    assert all(word not in result["error"] for word in ["HTTP", "gpt-", "Codex", "api_key"])
    assert len(wire.requests) == 2


def test_single_candidate_rejection_does_not_repeat_same_request(wire):
    wire.models = ["single-host"]
    wire.replies = [unsupported("single-host")]
    result = generate()
    assert result["error_type"] == "host_model_unavailable"
    assert len(wire.requests) == 1


def test_catalog_with_no_tool_hosts_has_plain_error_without_generation(wire, monkeypatch):
    wire.models = ["text-only-host"]
    monkeypatch.setattr(models_dev, "get_model_capabilities", lambda *a, **kw:
                        SimpleNamespace(supports_tools=False))
    result = generate()
    assert result["error_type"] == "host_model_unavailable"
    assert not wire.requests


@pytest.mark.parametrize("events", [
    [{"type": "response.image_generation_call.partial_image", "partial_image_b64": "abc"}],
    [{"type": "response.completed", "response": {"status": "completed", "output": []}}],
    [{"type": "response.failed", "response": {"error": {
        "message": "The 'gpt-5.6-sol' model is not supported.",
    }}}],
    [
        {"type": "response.image_generation_call.partial_image", "partial_image_b64": "abc"},
        {"type": "response.failed", "response": {"error": {
            "message": "The 'gpt-5.6-sol' model is not supported.",
        }}},
    ],
])
def test_stream_failures_never_retry_even_with_model_rejection_text(wire, events):
    wire.events = events
    assert generate()["success"] is False
    assert len(wire.requests) == 1
    assert not list(wire.root.glob("cache/images/*.png"))


def test_explicit_profile_host_takes_priority_over_cached_choice(wire):
    assert generate()["success"] is True
    (wire.root / "config.yaml").write_text(json.dumps({
        "image_gen": {"openai-codex": {"host_model": "my-explicit-host"}},
    }))
    assert generate()["success"] is True
    assert wire.requests[-1]["model"] == "my-explicit-host"


def test_connection_cache_does_not_cross_oauth_accounts(wire):
    wire.replies = [unsupported("gpt-5.6-sol")]
    assert generate()["success"] is True
    wire.token = "another-fake-account"
    assert generate()["success"] is True
    assert wire.requests[-1]["model"] == "gpt-5.6-sol"
    assert len(wire.probes) == 2


def test_token_refresh_for_same_account_keeps_cached_host(wire):
    def jwt(nonce):
        payload = json.dumps({
            "https://api.openai.com/auth": {"chatgpt_account_id": "test-account"},
            "nonce": nonce,
        }).encode()
        return "header." + base64.urlsafe_b64encode(payload).decode().rstrip("=") + ".signature"

    wire.token = jwt("first")
    wire.replies = [unsupported("gpt-5.6-sol")]
    assert generate()["success"] is True
    wire.token = jwt("refreshed")
    assert generate()["success"] is True
    assert wire.requests[-1]["model"] == "gpt-6.1-sol"
    assert len(wire.probes) == 1
