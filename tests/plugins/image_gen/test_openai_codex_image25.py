"""K21-032 contract tests for the native GPT Image 2.5 Codex provider."""

from __future__ import annotations

import base64
import importlib
import io
import json
from pathlib import Path

from PIL import Image


provider_mod = importlib.import_module("plugins.image_gen.openai-codex")


def _credentials(token="token"):
    return {
        "api_key": token,
        "base_url": "https://chatgpt.com/backend-api/codex",
    }


def _image_b64(fmt: str = "PNG") -> str:
    out = io.BytesIO()
    Image.new("RGBA", (2, 3), (12, 34, 56, 255)).save(out, format=fmt)
    return base64.b64encode(out.getvalue()).decode("ascii")


def _write_image(path: Path, *, size=(8, 8), color=(10, 20, 30, 255)):
    Image.new("RGBA", size, color).save(path, format="PNG")


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


def test_reference_roles_and_presets_are_bounded_and_ordered():
    prompt = provider_mod._build_guided_prompt(
        "Make a campaign portrait",
        input_count=3,
        has_edit_base=True,
        reference_roles=["identity", "style"],
        presets=["portrait", "russian-text"],
        preserve=["eye color", "logo spelling"],
    )

    assert "Image 1 [edit_base]" in prompt
    assert "Image 2 [identity]" in prompt
    assert "Image 3 [style]" in prompt
    assert "eye color" in prompt
    assert "Cyrillic" in prompt

    try:
        provider_mod._build_guided_prompt(
            "bad combination",
            input_count=0,
            has_edit_base=False,
            reference_roles=None,
            presets=["no-text", "russian-text"],
            preserve=None,
        )
    except ValueError as exc:
        assert "cannot be combined" in str(exc)
    else:
        raise AssertionError("conflicting presets were accepted")


def test_edit_base_and_references_share_five_image_limit():
    image = f"data:image/png;base64,{_image_b64()}"

    assert len(provider_mod._normalize_input_images(image, [image] * 4)) == 5
    try:
        provider_mod._normalize_input_images(image, [image] * 5)
    except ValueError as exc:
        assert "At most 5 combined" in str(exc)
    else:
        raise AssertionError("edit base plus five references exceeded the combined cap")


def test_mask_requires_alpha_png_matching_edit_base(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    base = tmp_path / "base.png"
    mask = tmp_path / "mask.png"
    wrong_size = tmp_path / "wrong-size.png"
    _write_image(base)
    _write_image(mask, color=(0, 0, 0, 0))
    _write_image(wrong_size, size=(16, 8), color=(0, 0, 0, 0))

    part = provider_mod._mask_tool_part(str(mask), str(base))
    assert part["image_url"].startswith("data:image/png;base64,")

    try:
        provider_mod._mask_tool_part(str(wrong_size), str(base))
    except ValueError as exc:
        assert "matching dimensions" in str(exc)
    else:
        raise AssertionError("mismatched mask dimensions were accepted")


def test_local_reference_read_is_confined_to_profile_or_workspace(tmp_path):
    from korra_constants import reset_hermes_home_override, set_hermes_home_override

    profile_home = tmp_path / "profile"
    outside = tmp_path / "outside.png"
    profile_home.mkdir()
    _write_image(outside)

    token = set_hermes_home_override(profile_home)
    try:
        try:
            provider_mod._local_image_to_data_url(str(outside))
        except ValueError as exc:
            assert "active Korra profile or current workspace" in str(exc)
        else:
            raise AssertionError("reference outside the allowed roots was accepted")
    finally:
        reset_hermes_home_override(token)


def test_invalid_model_fails_instead_of_falling_back(monkeypatch):
    monkeypatch.setattr(provider_mod, "_resolve_codex_credentials", _credentials)
    result = provider_mod.OpenAICodexImageGenProvider().generate(
        "poster", model="gpt-image-2-medium"
    )
    assert result["success"] is False
    assert result["error_type"] == "invalid_argument"


def test_nonfinal_response_is_not_retried(monkeypatch):
    monkeypatch.setattr(provider_mod, "_resolve_codex_credentials", _credentials)
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


def test_generate_rejects_invalid_base64_before_publishing(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(provider_mod, "_resolve_codex_credentials", _credentials)
    monkeypatch.setattr(
        provider_mod,
        "_collect_image_b64",
        lambda *args, **kwargs: {"b64": "not@@base64", "source": "final"},
    )

    result = provider_mod.OpenAICodexImageGenProvider().generate("poster")

    assert result["success"] is False
    assert result["error_type"] == "invalid_image_output"
    assert not list((tmp_path / "cache" / "images").glob("*"))


def test_generate_saves_decoded_image_and_sanitized_receipt(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(provider_mod, "_resolve_codex_credentials", _credentials)
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


def test_optional_receipt_failure_keeps_generated_image_success_terminal(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(provider_mod, "_resolve_codex_credentials", _credentials)
    monkeypatch.setattr(
        provider_mod,
        "_collect_image_b64",
        lambda *args, **kwargs: {"b64": _image_b64(), "source": "final"},
    )
    real_publish = provider_mod._atomic_publish

    def publish(path, payload, **kwargs):
        if str(path).endswith(".receipt.json"):
            raise OSError("receipt storage unavailable")
        return real_publish(path, payload, **kwargs)

    monkeypatch.setattr(provider_mod, "_atomic_publish", publish)

    result = provider_mod.OpenAICodexImageGenProvider().generate(
        "one terminal image",
        receipt=True,
    )

    assert result["success"] is True
    assert Path(result["image"]).is_file()
    assert result["receipt"] is None
    assert result["receipt_error"] == "receipt storage unavailable"
    assert len(list((tmp_path / "cache" / "images").glob("*.png"))) == 1


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
    assert props["reference_image_urls"]["maxItems"] == 4
    assert props["reference_roles"]["maxItems"] == 4
    assert "upscale" not in props

    image_gen_registry._reset_for_tests()


def test_credentials_delegate_only_to_canonical_profile_broker(monkeypatch, tmp_path):
    from korra_cli import auth

    calls = []
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-be-ignored")
    monkeypatch.setenv("CHATGPT_CODEX_ACCESS_TOKEN", "env-must-be-ignored")
    (tmp_path / "token.txt").write_text("file-must-be-ignored")

    def _resolve(**kwargs):
        calls.append(kwargs)
        return {
            "api_key": " profile-broker-token ",
            "base_url": "https://chatgpt.com/backend-api/codex/",
        }

    monkeypatch.setattr(auth, "resolve_codex_runtime_credentials", _resolve)
    assert provider_mod._resolve_codex_credentials() == {
        "api_key": "profile-broker-token",
        "base_url": "https://chatgpt.com/backend-api/codex",
    }
    assert calls == [{"refresh_if_expiring": True}]


def test_expiring_profile_token_is_refreshed_before_image_request(monkeypatch, tmp_path):
    import time
    from korra_cli import auth

    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time()) - 60}).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    expired_token = f"header.{payload}.signature"
    profile_home = tmp_path / "profile"
    profile_home.mkdir()
    (profile_home / "auth.json").write_text(
        json.dumps({
            "version": 1,
            "providers": {
                "openai-codex": {
                    "tokens": {
                        "access_token": expired_token,
                        "refresh_token": "refresh-secret",
                    },
                },
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "missing-codex-home"))
    refreshed = []

    def _refresh(tokens, timeout_seconds):
        refreshed.append((tokens["refresh_token"], timeout_seconds))
        return {"access_token": "fresh-access", "refresh_token": "fresh-refresh"}

    monkeypatch.setattr(auth, "_refresh_codex_auth_tokens", _refresh)

    credentials = provider_mod._resolve_codex_credentials()

    assert credentials["api_key"] == "fresh-access"
    assert refreshed and refreshed[0][0] == "refresh-secret"


def test_refresh_failure_is_redacted_without_image_request(monkeypatch):
    calls = []

    def _auth_failure():
        raise RuntimeError(
            "refresh rejected Authorization: Bearer SUPER-SECRET access_token=SECOND-SECRET"
        )

    monkeypatch.setattr(provider_mod, "_resolve_codex_credentials", _auth_failure)
    monkeypatch.setattr(
        provider_mod,
        "_collect_image_b64",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = provider_mod.OpenAICodexImageGenProvider().generate("poster")

    assert result["success"] is False
    assert result["error_type"] == "auth_required"
    assert "SUPER-SECRET" not in result["error"]
    assert "SECOND-SECRET" not in result["error"]
    assert "[REDACTED]" in result["error"]
    assert calls == []


def test_generated_output_follows_active_profile_home(monkeypatch, tmp_path):
    from korra_constants import reset_hermes_home_override, set_hermes_home_override

    monkeypatch.setattr(provider_mod, "_resolve_codex_credentials", _credentials)
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


def test_symlinked_profile_cache_is_rejected_before_backend_call(monkeypatch, tmp_path):
    from korra_constants import reset_hermes_home_override, set_hermes_home_override

    profile_home = tmp_path / "profile"
    outside = tmp_path / "outside"
    profile_home.mkdir()
    outside.mkdir()
    (profile_home / "cache").symlink_to(outside, target_is_directory=True)
    backend_calls = []
    monkeypatch.setattr(provider_mod, "_resolve_codex_credentials", _credentials)
    monkeypatch.setattr(
        provider_mod,
        "_collect_image_b64",
        lambda *args, **kwargs: backend_calls.append((args, kwargs)),
    )

    token = set_hermes_home_override(profile_home)
    try:
        result = provider_mod.OpenAICodexImageGenProvider().generate("poster")
    finally:
        reset_hermes_home_override(token)

    assert result["success"] is False
    assert result["error_type"] == "invalid_image_output"
    assert "symlink" in result["error"]
    assert backend_calls == []
    assert not list(outside.iterdir())


def test_pillow_decompression_bomb_warning_fails_closed(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 4)

    try:
        provider_mod._decoded_image(
            base64.b64decode(_image_b64(), validate=True), label="Reference"
        )
    except ValueError as exc:
        assert "fully decodable" in str(exc)
    else:
        raise AssertionError("Pillow decompression-bomb warning was ignored")
