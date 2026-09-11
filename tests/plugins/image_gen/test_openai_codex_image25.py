"""K21-032 contract tests for the native GPT Image 2.5 Codex provider."""

from __future__ import annotations

import base64
import importlib
import io
import json
from pathlib import Path

from PIL import Image


provider_mod = importlib.import_module("plugins.image_gen.openai-codex")


def _image_b64(fmt: str = "PNG") -> str:
    out = io.BytesIO()
    Image.new("RGBA", (2, 3), (12, 34, 56, 255)).save(out, format=fmt)
    return base64.b64encode(out.getvalue()).decode("ascii")


def test_payload_supports_image25_controls_without_tool_choice():
    payload = provider_mod._build_responses_payload(
        prompt="poster",
        size="1536x864",
        quality="xhigh",
        image_model="gpt-image-2.5-flare",
        background="transparent",
        output_format="webp",
        output_compression=82,
        action="generate",
    )

    assert "tool_choice" not in payload
    assert payload["tools"] == [{
        "type": "image_generation",
        "model": "gpt-image-2.5-flare",
        "size": "1536x864",
        "quality": "xhigh",
        "output_format": "webp",
        "background": "transparent",
        "action": "generate",
        "partial_images": 0,
        "output_compression": 82,
    }]


def test_invalid_model_fails_instead_of_falling_back(monkeypatch):
    monkeypatch.setattr(provider_mod, "_read_codex_access_token", lambda: "token")
    result = provider_mod.OpenAICodexImageGenProvider().generate(
        "poster", model="gpt-image-2-medium"
    )
    assert result["success"] is False
    assert result["error_type"] == "invalid_argument"


def test_nonfinal_response_is_not_retried(monkeypatch):
    monkeypatch.setattr(provider_mod, "_read_codex_access_token", lambda: "token")
    calls = []

    def _partial(*args, **kwargs):
        calls.append((args, kwargs))
        return {"b64": _image_b64(), "source": "partial"}

    monkeypatch.setattr(provider_mod, "_collect_image_b64", _partial)
    result = provider_mod.OpenAICodexImageGenProvider().generate("poster")

    assert result["success"] is False
    assert result["error_type"] == "incomplete_image"
    assert len(calls) == 1


def test_atomic_writer_validates_decoded_format_and_preserves_existing(tmp_path):
    target = tmp_path / "image.webp"
    target.write_bytes(b"existing")

    try:
        provider_mod._atomic_write_validated_image(
            target, base64.b64decode(_image_b64("PNG"), validate=True), "webp"
        )
    except ValueError as exc:
        assert "WEBP" in str(exc)
    else:
        raise AssertionError("PNG payload was accepted as WEBP")

    assert target.read_bytes() == b"existing"


def test_generate_saves_decoded_image_and_sanitized_receipt(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(provider_mod, "_read_codex_access_token", lambda: "token")
    monkeypatch.setattr(
        provider_mod,
        "_collect_image_b64",
        lambda *args, **kwargs: {"b64": _image_b64(), "source": "final"},
    )

    result = provider_mod.OpenAICodexImageGenProvider().generate(
        "private client portrait",
        model="gpt-image-2.5-sunburst",
        quality="high",
        receipt=True,
    )

    assert result["success"] is True
    assert Path(result["image"]).is_relative_to(tmp_path / "cache" / "images")
    receipt = Path(result["receipt"])
    assert receipt.is_relative_to(tmp_path / "cache" / "images")
    text = receipt.read_text(encoding="utf-8")
    assert "private client portrait" not in text
    assert "token" not in text
    assert '"prompt_sha256"' in text


def test_stream_accepts_only_terminal_completed_result(monkeypatch):
    import httpx

    image = _image_b64()

    class _Response:
        def __init__(self, include_terminal=True):
            self.include_terminal = include_terminal

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            early = {
                "type": "response.output_item.done",
                "item": {"type": "image_generation_call", "status": "completed", "result": image},
            }
            yield f"data: {json.dumps(early)}"
            yield ""
            if self.include_terminal:
                terminal = {
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
                }
                yield f"data: {json.dumps(terminal)}"
                yield ""

    class _Client:
        include_terminal = True

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return _Response(self.include_terminal)

    monkeypatch.setattr(httpx, "Client", _Client)
    result = provider_mod._collect_image_b64(
        "token", prompt="poster", size="1024x1024", quality="medium"
    )
    assert result == {"b64": image, "source": "final"}

    _Client.include_terminal = False
    try:
        provider_mod._collect_image_b64(
            "token", prompt="poster", size="1024x1024", quality="medium"
        )
    except RuntimeError as exc:
        assert "response.completed" in str(exc)
    else:
        raise AssertionError("early output item was accepted without terminal completion")


def test_dynamic_schema_advertises_only_native_image25_options(monkeypatch):
    from agent import image_gen_registry
    from korra_cli import plugins as plugin_runtime
    from tools import image_generation_tool

    image_gen_registry._reset_for_tests()
    image_gen_registry.register_provider(provider_mod.OpenAICodexImageGenProvider())
    monkeypatch.setattr(
        image_generation_tool, "_read_configured_image_provider", lambda: "openai-codex"
    )
    monkeypatch.setattr(plugin_runtime, "_ensure_plugins_discovered", lambda *args, **kwargs: None)

    schema = image_generation_tool._build_dynamic_image_schema()
    props = schema["parameters"]["properties"]

    assert props["image_model"]["enum"] == [
        "gpt-image-2.5-sunburst", "gpt-image-2.5-flare"
    ]
    assert props["quality"]["enum"] == ["low", "medium", "high", "xhigh", "max", "auto"]
    assert {"size", "background", "output_format", "output_compression"} <= set(props)
    assert {"action", "reference_roles", "preserve", "mask", "presets", "receipt"} <= set(props)
    assert "upscale" not in props

    image_gen_registry._reset_for_tests()


def test_token_reader_delegates_only_to_canonical_profile_broker(monkeypatch, tmp_path):
    from agent import auxiliary_client

    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-be-ignored")
    monkeypatch.setenv("CHATGPT_CODEX_ACCESS_TOKEN", "env-must-be-ignored")
    (tmp_path / "token.txt").write_text("file-must-be-ignored")

    monkeypatch.setattr(auxiliary_client, "_read_codex_access_token", lambda: None)
    assert provider_mod._read_codex_access_token() is None

    monkeypatch.setattr(
        auxiliary_client, "_read_codex_access_token", lambda: " profile-broker-token "
    )
    assert provider_mod._read_codex_access_token() == "profile-broker-token"


def test_generated_output_follows_active_profile_home(monkeypatch, tmp_path):
    from korra_constants import reset_hermes_home_override, set_hermes_home_override

    monkeypatch.setattr(provider_mod, "_read_codex_access_token", lambda: "token")
    monkeypatch.setattr(
        provider_mod,
        "_collect_image_b64",
        lambda *args, **kwargs: {"b64": _image_b64(), "source": "final"},
    )
    provider = provider_mod.OpenAICodexImageGenProvider()
    outputs = []

    for profile_name in ("client-a", "client-b"):
        profile_home = tmp_path / profile_name
        token = set_hermes_home_override(profile_home)
        try:
            result = provider.generate("profile-scoped icon", receipt=True)
        finally:
            reset_hermes_home_override(token)
        assert result["success"] is True
        output = Path(result["image"])
        receipt = Path(result["receipt"])
        assert output.is_relative_to(profile_home.resolve())
        assert receipt.is_relative_to(profile_home.resolve())
        outputs.append(output)

    assert outputs[0].parent != outputs[1].parent
