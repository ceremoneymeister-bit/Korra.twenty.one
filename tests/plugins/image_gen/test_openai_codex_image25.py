"""K21-032 contract tests for the native GPT Image 2.5 Codex provider."""

from __future__ import annotations

import base64
import importlib
import io
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
