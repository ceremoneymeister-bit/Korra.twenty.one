"""OpenAI image generation backend — ChatGPT/Codex OAuth variant.

Uses the GPT Image 2.5 Sunburst/Flare catalog through the Codex Responses API
``image_generation`` tool. This lets users who are already authenticated with
Codex/ChatGPT generate images without configuring a separate API key.

Selection precedence for the image model (first hit wins):

1. Exact model requested by ``image_generate``
2. ``image_gen.openai-codex.model`` in the active profile's ``config.yaml``
3. ``image_gen.model`` in the active profile's ``config.yaml``
4. :data:`DEFAULT_MODEL` — ``gpt-image-2.5-sunburst``

Output is decoded and saved atomically under the active profile's
``cache/images/``. Source images for generation/editing are sent as Responses
``input_image`` content parts.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import io
import json
import logging
import os
import re
import tempfile
import uuid
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    normalize_reference_images,
    resolve_aspect_ratio,
    success_response,
)

logger = logging.getLogger(__name__)


# NOTE: do NOT reintroduce an "account capability" classifier keyed on
# ``Tool choice 'image_generation' not found in 'tools' parameter``. That HTTP
# 400 is a *request-shape* rejection (the Codex backend resolves tool_choice as
# a function-tool name and never recognizes hosted-tool entries) — it is
# emitted for every account, including accounts where image generation works.
# A previous version of this file translated that 400 into "Image generation is
# not enabled for the current Codex account. Switch the image provider to
# OpenAI API key, FAL, or xAI.", which reported a universal bug in our own
# payload as the user's entitlement problem and sent people away from a
# provider that was never actually tried. The request-shape bug is fixed by
# omitting tool_choice (see ``_build_responses_payload``); any remaining HTTP
# error must surface verbatim so it stays diagnosable. See issues #19505,
# #49008 and #31335.

_MAX_ERROR_BODY_CHARS = 500


def _summarize_error_body(body: str) -> str:
    """Return a bounded, information-preserving summary of an error body.

    Prefers the parsed ``error.message`` field, because a blind head-truncation
    of the raw body can cut the actual message off entirely — Codex error
    payloads sometimes carry hundreds of bytes of leading metadata, so
    ``body[:500]`` yielded a wall of padding and no diagnosis. Falls back to a
    truncated raw body for non-JSON responses.
    """
    text = body or ""
    try:
        payload = json.loads(text)
        error = payload.get("error") if isinstance(payload, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        if isinstance(message, str) and message.strip():
            return _sanitize_error_text(message.strip())
    except (TypeError, ValueError):
        pass
    return _sanitize_error_text(text)



# ---------------------------------------------------------------------------
# GPT Image 2.5 contract selectively ported from gpt-image-2-5-agent-kit
# v0.3.1 at cc52564687907194c8db02e95996757fb5d19b86.  The kit's CLI,
# alternate token sources, and required tool_choice shape are intentionally
# not embedded in Korra; this provider remains a native image_generate backend.
# ---------------------------------------------------------------------------

_MODELS: Dict[str, Dict[str, Any]] = {
    "gpt-image-2.5-sunburst": {
        "display": "GPT Image 2.5 Sunburst",
        "speed": "precise",
    },
    "gpt-image-2.5-flare": {
        "display": "GPT Image 2.5 Flare",
        "speed": "fast",
    },
}

DEFAULT_MODEL = "gpt-image-2.5-sunburst"
_QUALITIES = ("low", "medium", "high", "xhigh", "max", "auto")
_BACKGROUNDS = ("opaque", "transparent", "auto")
_OUTPUT_FORMATS = ("png", "jpeg", "webp")
_ACTIONS = ("auto", "generate", "edit")
_REFERENCE_ROLES = ("identity", "style", "logo", "layout", "general")
_PRESETS = {
    "portrait": "Compose an intentional portrait with clear facial structure and controlled lighting.",
    "likeness": "Preserve the referenced person's distinguishing features and proportions; do not copy unrelated pose or background details.",
    "thumbnail": "Use one clear focal point, strong contrast, and readable hierarchy at export size.",
    "product": "Keep the product recognizable with clear subject separation and useful layout space.",
    "no-text": "Do not include lettering, captions, labels, logos, UI text, or watermarks.",
    "russian-text": "Render supplied Cyrillic copy verbatim, preserving spelling, case, punctuation, and line breaks.",
    "edit": "Apply only the requested changes to the first image and preserve all other content unless explicitly changed.",
    "brand-style": "Apply the requested brand palette, materials, lighting, typography direction, and visual density consistently.",
}
_ROLE_GUIDANCE = {
    "identity": "identity anchor only; preserve distinguishing features without copying unrelated pose or background",
    "style": "style anchor only; borrow palette, materials, lighting, and visual density",
    "logo": "logo anchor; preserve mark geometry, colors, proportions, and exact lettering",
    "layout": "layout anchor only; borrow spatial arrangement and scale",
    "general": "use only for the purpose stated in the request",
}

_SIZES = {
    "landscape": "1536x1024",
    "square": "1024x1024",
    "portrait": "1024x1536",
}

# Codex Responses surface used for the request. The chat model itself is only
# the host that calls the ``image_generation`` tool; the actual image work is
# done by ``API_MODEL``.
_CODEX_CHAT_MODEL = "gpt-5.5"
_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_CODEX_INSTRUCTIONS = (
    "You are an assistant that must fulfill image generation and image editing "
    "requests by using the image_generation tool when provided."
)

_MAX_REFERENCE_IMAGES = 5
_MAX_INPUT_IMAGE_BYTES = 25 * 1024 * 1024
# GPT Image 2.5's Responses ``input_image`` accepts raster formats only. The
# shared magic-byte sniffer also recognizes SVG/TIFF/ICO, which the API
# rejects server-side — gate to this allowlist so unsupported inputs fail
# locally with a clear error instead of an opaque HTTP 400.
_ACCEPTED_INPUT_MIME = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)

_FORMAT_EXTENSIONS = {"png": "png", "jpeg": "jpg", "webp": "webp"}
_PIL_FORMATS = {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}
_SENSITIVE_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"(authorization\s*[:=]\s*)[^,;\n]+", re.IGNORECASE),
    re.compile(r"(cookie\s*[:=]\s*)[^\n]+", re.IGNORECASE),
    re.compile(r"([\"']?(?:access|refresh|id)_token[\"']?\s*[:=]\s*)[\"']?[^,}\]\s\"']+", re.IGNORECASE),
)


# ---------------------------------------------------------------------------
# Config + auth helpers
# ---------------------------------------------------------------------------


def _load_image_gen_config() -> Dict[str, Any]:
    """Read ``image_gen`` from config.yaml (returns {} on any failure)."""
    try:
        from korra_cli.config import load_config

        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception as exc:
        logger.debug("Could not load image_gen config: %s", exc)
        return {}


def _resolve_model(requested: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """Resolve one exact GPT Image 2.5 model without legacy fallback."""
    if requested is not None:
        candidate = requested.strip() if isinstance(requested, str) else ""
        if candidate not in _MODELS:
            raise ValueError(
                f"Unsupported Codex image model: {requested!r}. Choose from "
                f"{', '.join(_MODELS)}."
            )
        return candidate, _MODELS[candidate]

    cfg = _load_image_gen_config()
    sub = cfg.get("openai-codex") if isinstance(cfg.get("openai-codex"), dict) else {}
    candidate: Optional[str] = None
    if isinstance(sub, dict):
        value = sub.get("model")
        if isinstance(value, str) and value.strip():
            candidate = value.strip()
    if candidate is None:
        top = cfg.get("model")
        if isinstance(top, str) and top.strip():
            candidate = top.strip()

    if candidate is not None:
        if candidate not in _MODELS:
            raise ValueError(
                f"Configured image_gen model {candidate!r} is not supported by "
                "the openai-codex provider. Select Sunburst or Flare."
            )
        return candidate, _MODELS[candidate]

    return DEFAULT_MODEL, _MODELS[DEFAULT_MODEL]


def _resolve_codex_credentials() -> Dict[str, str]:
    """Return refreshed credentials from Korra's active-profile auth broker."""
    from korra_cli.auth import resolve_codex_runtime_credentials

    credentials = resolve_codex_runtime_credentials(refresh_if_expiring=True)
    token = str(credentials.get("api_key") or "").strip()
    base_url = str(credentials.get("base_url") or "").strip().rstrip("/")
    if not token:
        raise RuntimeError("Codex OAuth broker returned no access token")
    if not base_url:
        raise RuntimeError("Codex OAuth broker returned no base URL")
    return {"api_key": token, "base_url": base_url}


def _read_codex_access_token() -> Optional[str]:
    """Compatibility probe backed only by the refresh-capable auth broker."""
    try:
        return _resolve_codex_credentials()["api_key"]
    except Exception as exc:
        logger.debug("Could not resolve Codex access token: %s", exc)
        return None


def _sniff_image_mime(raw: bytes) -> Optional[str]:
    """Return a safe raster image MIME from magic bytes (not filename labels).

    Delegates magic-byte detection to the shared sniffer in
    ``agent.image_routing`` (single source of truth), then gates the result
    to :data:`_ACCEPTED_INPUT_MIME` — the raster formats GPT Image 2.5's
    ``input_image`` actually accepts. SVG/TIFF/ICO (which the shared sniffer
    also recognizes) are rejected here so they fail locally with a clear
    error instead of an opaque server-side HTTP 400.
    """
    from agent.image_routing import _sniff_mime_from_bytes

    mime = _sniff_mime_from_bytes(raw)
    if mime in _ACCEPTED_INPUT_MIME:
        return mime
    return None


def _sanitize_error_text(value: Any, *, limit: int = _MAX_ERROR_BODY_CHARS) -> str:
    text = str(value or "")[:limit]
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub(
            lambda match: f"{match.group(1)}[REDACTED]" if match.groups() else "Bearer [REDACTED]",
            text,
        )
    return text


def _decoded_image(raw: bytes, *, label: str, expected_format: Optional[str] = None):
    """Fully decode raster bytes with Pillow and return a detached image."""
    from PIL import Image

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as probe:
                actual = (probe.format or "").upper()
                if expected_format and actual != _PIL_FORMATS[expected_format]:
                    raise ValueError(
                        f"{label} decoded as {actual or 'unknown'}, expected "
                        f"{_PIL_FORMATS[expected_format]}."
                    )
                probe.verify()
            with Image.open(io.BytesIO(raw)) as decoded:
                decoded.load()
                return decoded.copy()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"{label} is not a valid, fully decodable image.") from exc


def _assert_local_input_root(path: Path) -> Path:
    """Confine local reads to the active profile or current workspace root."""
    from korra_constants import get_hermes_home

    resolved = path.expanduser().resolve()
    roots = (get_hermes_home().resolve(), Path.cwd().resolve())
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise ValueError(
            "Local image input must be inside the active Korra profile or "
            "current workspace."
        )
    return resolved


def _assert_publish_path(path: Path, allowed_root: Path) -> None:
    """Reject output parents that escape or traverse symlinks below a profile."""
    root = allowed_root.resolve()
    candidate = path.absolute()
    try:
        relative_parent = candidate.parent.relative_to(root)
    except ValueError as exc:
        raise ValueError("Output path must remain under the active Korra profile.") from exc

    current = root
    for part in relative_parent.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Output directory must not traverse a symlink: {current}")
    resolved_parent = candidate.parent.resolve()
    if resolved_parent != root and root not in resolved_parent.parents:
        raise ValueError("Resolved output directory escapes the active Korra profile.")


def _profile_image_output_dir() -> Tuple[Path, Path]:
    from korra_constants import get_hermes_home

    root = get_hermes_home().resolve()
    output_dir = root / "cache" / "images"
    _assert_publish_path(output_dir / ".confinement-check", root)
    output_dir.mkdir(parents=True, exist_ok=True)
    _assert_publish_path(output_dir / ".confinement-check", root)
    return root, output_dir


def _atomic_publish(path: Path, data: bytes, *, allowed_root: Optional[Path] = None) -> None:
    """Publish bytes atomically and refuse to replace an existing target."""
    if allowed_root is not None:
        _assert_publish_path(path, allowed_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if allowed_root is not None:
        _assert_publish_path(path, allowed_root)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ValueError(f"Refusing to overwrite existing output: {path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_validated_image(
    path: Path,
    raw: bytes,
    output_format: str,
    *,
    allowed_root: Optional[Path] = None,
) -> Dict[str, Any]:
    image = _decoded_image(raw, label="Generated payload", expected_format=output_format)
    try:
        metadata = {
            "width": image.width,
            "height": image.height,
            "format": (image.format or _PIL_FORMATS[output_format]).lower(),
            "mode": image.mode,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    finally:
        image.close()
    _atomic_publish(path, raw, allowed_root=allowed_root)
    return metadata


def _validate_size(size: str) -> str:
    if size == "auto":
        return size
    if not re.fullmatch(r"[0-9]+x[0-9]+", size):
        raise ValueError("Size must be auto or WIDTHxHEIGHT.")
    width, height = (int(edge) for edge in size.split("x"))
    if width <= 0 or height <= 0 or width % 16 or height % 16:
        raise ValueError("Size edges must be positive multiples of 16.")
    if max(width, height) > 3840 or max(width, height) > 3 * min(width, height):
        raise ValueError("Size exceeds the 3840px edge or 3:1 aspect-ratio bound.")
    if not 655_360 <= width * height <= 8_294_400:
        raise ValueError("Size must contain between 655360 and 8294400 pixels.")
    return size


def _validate_options(
    *, quality: str, size: str, background: str, output_format: str,
    output_compression: Optional[int], action: str,
) -> None:
    if quality not in _QUALITIES:
        raise ValueError(f"Unsupported quality: {quality!r}.")
    _validate_size(size)
    if background not in _BACKGROUNDS:
        raise ValueError(f"Unsupported background: {background!r}.")
    if output_format not in _OUTPUT_FORMATS:
        raise ValueError(f"Unsupported output format: {output_format!r}.")
    if background == "transparent" and output_format == "jpeg":
        raise ValueError("Transparent backgrounds require PNG or WebP output.")
    if output_compression is not None:
        if type(output_compression) is not int or not 0 <= output_compression <= 100:
            raise ValueError("Output compression must be an integer from 0 to 100.")
        if output_format not in {"jpeg", "webp"}:
            raise ValueError("Output compression is supported only for JPEG or WebP.")
    if action not in _ACTIONS:
        raise ValueError(f"Unsupported action: {action!r}.")


def _accepted_raster_bytes(raw: bytes, *, label: str) -> Tuple[bytes, str]:
    """Return ``(bytes, mime)`` GPT Image 2.5 accepts, transcoding when needed.

    PNG/JPEG/GIF/WebP pass through untouched. HEIC/HEIF (iPhone photos),
    AVIF, BMP and TIFF are re-encoded to PNG through the shared transcoder
    so they work as edit bases and identity references (K21-057). Anything
    else — or a format whose decoder is missing — fails with the file name,
    the detected format and the concrete reason instead of a blind
    "not a supported image".
    """
    from agent.image_routing import (
        _sniff_mime_from_bytes,
        image_format_label,
        transcode_image_to_png,
    )

    sniffed = _sniff_mime_from_bytes(raw)
    if sniffed in _ACCEPTED_INPUT_MIME:
        return raw, sniffed
    if sniffed in {"image/heic", "image/avif", "image/bmp", "image/tiff"}:
        png, reason = transcode_image_to_png(raw, mime=sniffed)
        if png is not None:
            if len(png) > _MAX_INPUT_IMAGE_BYTES:
                raise ValueError(
                    f"{label}: {image_format_label(sniffed)} после перекодирования в PNG "
                    "превышает 25MB."
                )
            logger.info(
                "Codex image input %s transcoded %s -> image/png", label, sniffed
            )
            return png, "image/png"
        raise ValueError(
            f"{label}: формат {image_format_label(sniffed)} не перекодирован в PNG — {reason}"
        )
    if sniffed is None:
        raise ValueError(
            f"{label}: not a supported image — содержимое не распознано как "
            "изображение (нужны PNG, JPEG, GIF, WebP или фото HEIC/AVIF/BMP/TIFF)."
        )
    raise ValueError(
        f"{label}: формат {image_format_label(sniffed)} не поддерживается как вход "
        "GPT Image 2.5 (нужны PNG, JPEG, GIF, WebP или фото HEIC/AVIF/BMP/TIFF, "
        "которые перекодируются автоматически)."
    )


def _data_url_to_input_image_url(value: str) -> str:
    """Validate and canonicalize a data:image URL for Responses input_image."""
    if "," not in value:
        raise ValueError("Image data URL is missing a comma separator")
    header, data = value.split(",", 1)
    header_lc = header.lower()
    if not header_lc.startswith("data:image/") or ";base64" not in header_lc:
        raise ValueError("Only base64 data:image URLs are supported as Codex image inputs")
    raw = base64.b64decode(data, validate=True)
    if len(raw) > _MAX_INPUT_IMAGE_BYTES:
        raise ValueError("Image data URL exceeds 25MB cap")
    raw, mime = _accepted_raster_bytes(raw, label="Image data URL")
    decoded = _decoded_image(raw, label="Image data URL")
    decoded.close()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _local_image_to_data_url(value: str) -> str:
    """Read a local image path and return a validated data:image URL."""
    try:
        from agent.file_safety import get_read_block_error

        blocked = get_read_block_error(value)
        if blocked:
            raise ValueError(blocked)
    except ValueError:
        raise
    except Exception as exc:
        logger.debug("Codex image input read guard unavailable: %s", exc)

    path = _assert_local_input_root(Path(value))
    if not path.is_file():
        raise ValueError(f"Image input path does not exist or is not a file: {value}")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError(f"Image input path is empty: {value}")
    if size > _MAX_INPUT_IMAGE_BYTES:
        raise ValueError(f"Image input path exceeds 25MB cap: {value}")
    raw = path.read_bytes()
    raw, mime = _accepted_raster_bytes(raw, label=f"Файл {path.name}")
    decoded = _decoded_image(raw, label="Image input")
    decoded.close()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _to_input_image_part(value: str) -> Dict[str, str]:
    """Convert a URL/data URL/local path into a Responses input_image part."""
    candidate = (value or "").strip()
    if not candidate:
        raise ValueError("Blank image input")
    lowered = candidate.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        image_url = candidate
    elif lowered.startswith("data:"):
        image_url = _data_url_to_input_image_url(candidate)
    else:
        image_url = _local_image_to_data_url(candidate)
    return {"type": "input_image", "image_url": image_url}


def _normalize_input_images(
    image_url: Optional[str],
    reference_image_urls: Optional[List[str]],
) -> List[Dict[str, str]]:
    """Collect primary + reference images as ordered Responses content parts."""
    values: List[str] = []
    if isinstance(image_url, str) and image_url.strip():
        values.append(image_url.strip())
    for ref in (normalize_reference_images(reference_image_urls) or []):
        values.append(ref)
    if len(values) > _MAX_REFERENCE_IMAGES:
        raise ValueError(f"At most {_MAX_REFERENCE_IMAGES} combined image inputs are supported.")
    return [_to_input_image_part(value) for value in values]


def _data_url_bytes(value: str, *, label: str) -> bytes:
    if "," not in value:
        raise ValueError(f"{label} data URL is missing a comma separator.")
    header, data = value.split(",", 1)
    if ";base64" not in header.lower():
        raise ValueError(f"{label} must be a base64 data URL.")
    try:
        return base64.b64decode(data, validate=True)
    except Exception as exc:
        raise ValueError(f"{label} contains invalid base64 data.") from exc


def _source_bytes(value: str, *, label: str) -> bytes:
    candidate = (value or "").strip()
    if candidate.lower().startswith("data:"):
        return _data_url_bytes(candidate, label=label)
    if candidate.lower().startswith(("http://", "https://")):
        raise ValueError(f"{label} must be a profile/workspace file or data URL.")
    path = _assert_local_input_root(Path(candidate))
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {candidate}")
    if path.stat().st_size > _MAX_INPUT_IMAGE_BYTES:
        raise ValueError(f"{label} exceeds 25MB cap.")
    return path.read_bytes()


def _mask_tool_part(mask: str, edit_base: str) -> Dict[str, str]:
    mask_raw = _source_bytes(mask, label="Mask")
    base_raw = _source_bytes(edit_base, label="Edit base")
    mask_image = _decoded_image(mask_raw, label="Mask", expected_format="png")
    base_image = _decoded_image(base_raw, label="Edit base")
    try:
        if mask_image.size != base_image.size:
            raise ValueError("Mask and edit base must have matching dimensions.")
        if "A" not in mask_image.getbands() and "transparency" not in mask_image.info:
            raise ValueError("Mask PNG must contain an alpha channel.")
        if mask_image.convert("RGBA").getchannel("A").getextrema()[0] == 255:
            raise ValueError("Mask PNG must contain transparent pixels.")
    finally:
        mask_image.close()
        base_image.close()
    encoded = base64.b64encode(mask_raw).decode("ascii")
    return {"image_url": f"data:image/png;base64,{encoded}"}


def _build_guided_prompt(
    prompt: str, *, input_count: int, has_edit_base: bool,
    reference_roles: Optional[List[str]], presets: Optional[List[str]],
    preserve: Optional[List[str]],
) -> str:
    selected_presets = list(dict.fromkeys(presets or []))
    unknown_presets = [item for item in selected_presets if item not in _PRESETS]
    if unknown_presets:
        raise ValueError(f"Unsupported preset: {unknown_presets[0]!r}.")
    if "no-text" in selected_presets and "russian-text" in selected_presets:
        raise ValueError("no-text and russian-text presets cannot be combined.")

    roles = reference_roles or ["general"] * (input_count - int(has_edit_base))
    if len(roles) != input_count - int(has_edit_base):
        raise ValueError("reference_roles must match reference_image_urls.")
    if any(role not in _REFERENCE_ROLES for role in roles):
        raise ValueError("reference_roles contains an unsupported role.")
    preserved = preserve or []
    if preserved and not has_edit_base:
        raise ValueError("preserve instructions require an edit base image.")
    if any(not isinstance(item, str) or not item.strip() for item in preserved):
        raise ValueError("preserve instructions must be non-empty strings.")

    parts = [prompt.strip()]
    if has_edit_base and "edit" not in selected_presets:
        parts.append(_PRESETS["edit"])
    parts.extend(_PRESETS[item] for item in selected_presets)
    if input_count:
        role_lines = []
        if has_edit_base:
            role_lines.append("Image 1 [edit_base]: base image to modify.")
        offset = 2 if has_edit_base else 1
        role_lines.extend(
            f"Image {index + offset} [{role}]: {_ROLE_GUIDANCE[role]}."
            for index, role in enumerate(roles)
        )
        parts.append("Reference roles in attachment order:\n" + "\n".join(role_lines))
    if preserved:
        parts.append(
            "Preserve unchanged:\n"
            + "\n".join(f"- {item.strip()}" for item in preserved)
        )
    parts.append(
        "Honor the requested medium, composition, literal text, and crop. "
        "Use each reference only for its assigned role."
    )
    return "\n\n".join(parts)


# Progressive preview frames (partial_image_b64) are intermediate renders.
# Request none; a result is only a completed ``image_generation_call`` item
# (streamed via response.output_item.done or listed in the terminal
# response.completed.output) confirmed by a completed terminal event.
# generate() additionally refuses every source except ``final``.
_PARTIAL_IMAGES_REQUESTED = 0
def _build_responses_payload(
    *,
    prompt: str,
    size: str,
    quality: str,
    input_images: Optional[List[Dict[str, str]]] = None,
    image_model: str = DEFAULT_MODEL,
    background: str = "opaque",
    output_format: str = "png",
    output_compression: Optional[int] = None,
    action: str = "auto",
    mask_part: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build the Codex Responses request body for an image_generation call."""
    content: List[Dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    if input_images:
        content.extend(input_images)
    tool: Dict[str, Any] = {
        "type": "image_generation",
        "model": image_model,
        "size": size,
        "quality": quality,
        "output_format": output_format,
        "background": background,
        "action": action,
        "partial_images": _PARTIAL_IMAGES_REQUESTED,
    }
    if output_compression is not None:
        tool["output_compression"] = output_compression
    if mask_part is not None:
        tool["input_image_mask"] = mask_part

    return {
        "model": _CODEX_CHAT_MODEL,
        "store": False,
        "instructions": _CODEX_INSTRUCTIONS,
        "input": [{
            "type": "message",
            "role": "user",
            "content": content,
        }],
        "tools": [tool],
        # No ``tool_choice`` is sent: the chatgpt.com/backend-api/codex backend
        # rejects every shape we have for forcing the hosted ``image_generation``
        # tool. ``{"type": "allowed_tools", "mode": "required", "tools": [{"type":
        # "image_generation"}]}`` (and the simpler ``{"type": "image_generation"}``
        # form) both 400 with ``Tool choice 'image_generation' not found in 'tools'
        # parameter`` — the backend looks up tool_choice as a *function* name and
        # never recognizes hosted-tool entries. Letting the host model decide is
        # the only shape Codex currently accepts; the ``instructions`` above are
        # what nudge it toward the tool. See issue #19505.
        "stream": True,
    }


def _extract_image_candidates(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(final_result_b64, latest_partial_b64)`` from a payload tree.

    Final ``image_generation_call.result`` and progressive ``partial_image_b64``
    are tracked separately so a partial can never overwrite a genuine final,
    including when both coexist in the same event payload.
    """
    result_b64: Optional[str] = None
    partial_b64: Optional[str] = None

    def walk(node: Any) -> None:
        nonlocal result_b64, partial_b64
        if isinstance(node, dict):
            if node.get("type") == "image_generation_call":
                result = node.get("result")
                if isinstance(result, str) and result:
                    result_b64 = result
            partial = node.get("partial_image_b64")
            if isinstance(partial, str) and partial:
                partial_b64 = partial
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return result_b64, partial_b64


def _extract_image_b64(value: Any) -> Optional[str]:
    """Return image b64 from a payload, preferring final result over partial.

    Progressive ``partial_image_b64`` is only used when no final
    ``image_generation_call.result`` is present in the same payload tree.
    """
    result_b64, partial_b64 = _extract_image_candidates(value)
    return result_b64 or partial_b64


def _png_pixel_size(raw: bytes) -> Optional[str]:
    """Return ``\"{w}x{h}\"`` for a PNG payload, or None if not a PNG IHDR."""
    import struct

    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    # IHDR: length(4) + type(4) + width(4) + height(4)
    if raw[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", raw[16:24])
    return f"{width}x{height}"


def _iter_sse_json(response: Any):
    """Yield JSON payloads from an SSE response without OpenAI SDK parsing.

    The ChatGPT/Codex backend can emit image-generation events newer than the
    pinned Python SDK understands. Parsing raw SSE keeps this provider tolerant
    of those event-shape changes.
    """
    event_name: Optional[str] = None
    data_lines: List[str] = []

    def flush():
        nonlocal event_name, data_lines
        if not data_lines:
            event_name = None
            return None
        raw = "\n".join(data_lines).strip()
        event = event_name
        event_name = None
        data_lines = []
        if not raw or raw == "[DONE]":
            return None
        payload = json.loads(raw)
        if isinstance(payload, dict) and event and "type" not in payload:
            payload["type"] = event
        return payload

    for line in response.iter_lines():
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        line = str(line)
        if line == "":
            payload = flush()
            if payload is not None:
                yield payload
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())

    payload = flush()
    if payload is not None:
        yield payload


class CodexImageStreamDiagnostics:
    """What the Codex SSE stream actually carried, for honest error text.

    The previous ``empty_response`` message was blind: it said only that no
    completed ``image_generation_call`` was found, which on 12.09.2026 sent a
    day of diagnosis toward "content policy on faces" while the server had in
    fact delivered a finished PNG in ``response.output_item.done`` and an
    empty ``response.completed.output``. Every event type, the terminal
    status, refusal text and ``incomplete_details`` are recorded here so the
    error names what arrived instead of what was expected.
    """

    _MAX_TEXT = 240

    def __init__(self) -> None:
        self.event_types: Dict[str, int] = {}
        self.terminal_status: Optional[str] = None
        self.terminal_event: Optional[str] = None
        self.output_item_types: List[str] = []
        self.image_call_statuses: List[str] = []
        self.partial_frames = 0
        self.refusals: List[str] = []
        self.incomplete_reason: Optional[str] = None
        self.error_message: Optional[str] = None
        self.output_text: List[str] = []

    def _note_text(self, bucket: List[str], value: Any) -> None:
        if isinstance(value, str) and value.strip():
            text = _sanitize_error_text(value.strip(), limit=self._MAX_TEXT)
            if text not in bucket:
                bucket.append(text)

    def observe(self, event: Dict[str, Any]) -> None:
        event_type = str(event.get("type") or "?")
        self.event_types[event_type] = self.event_types.get(event_type, 0) + 1

        if "partial_image_b64" in event:
            self.partial_frames += 1

        item = event.get("item")
        if event_type == "response.output_item.done" and isinstance(item, dict):
            item_type = str(item.get("type") or "?")
            self.output_item_types.append(item_type)
            if item_type == "image_generation_call":
                self.image_call_statuses.append(str(item.get("status") or "?"))
            if item_type == "refusal":
                self._note_text(self.refusals, item.get("refusal"))
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "refusal":
                    self._note_text(self.refusals, part.get("refusal"))
                elif part.get("type") == "output_text":
                    self._note_text(self.output_text, part.get("text"))

        if event_type == "response.refusal.done":
            self._note_text(self.refusals, event.get("refusal"))
        if event_type == "error":
            self._note_text_error(event.get("message") or event.get("error"))

        response_obj = event.get("response")
        if event_type in {
            "response.completed",
            "response.failed",
            "response.incomplete",
        } and isinstance(response_obj, dict):
            self.terminal_event = event_type
            status = response_obj.get("status")
            self.terminal_status = str(status) if status else None
            details = response_obj.get("incomplete_details")
            if isinstance(details, dict) and details.get("reason"):
                self.incomplete_reason = str(details.get("reason"))
            error = response_obj.get("error")
            if isinstance(error, dict):
                self._note_text_error(error.get("message") or error.get("code"))
            elif error:
                self._note_text_error(error)

    def _note_text_error(self, value: Any) -> None:
        if isinstance(value, dict):
            value = value.get("message") or value.get("code") or json.dumps(value)[: self._MAX_TEXT]
        if isinstance(value, str) and value.strip():
            self.error_message = _sanitize_error_text(value.strip(), limit=self._MAX_TEXT)

    def summary(self) -> str:
        parts: List[str] = []
        if self.event_types:
            parts.append(
                "events: "
                + ", ".join(f"{name}×{count}" for name, count in self.event_types.items())
            )
        else:
            parts.append("events: none")
        if self.terminal_event:
            parts.append(
                f"terminal {self.terminal_event} status={self.terminal_status or '?'}"
            )
        else:
            parts.append("no terminal response.* event")
        if self.output_item_types:
            parts.append("output items: " + ", ".join(self.output_item_types))
        if self.image_call_statuses:
            parts.append(
                "image_generation_call status: " + ", ".join(self.image_call_statuses)
            )
        if self.partial_frames:
            parts.append(f"partial_image frames: {self.partial_frames}")
        if self.incomplete_reason:
            parts.append(f"incomplete_details.reason={self.incomplete_reason}")
        if self.error_message:
            parts.append(f"error: {self.error_message}")
        if self.refusals:
            parts.append("refusal: " + " | ".join(self.refusals))
        if self.output_text:
            parts.append("assistant text: " + " | ".join(self.output_text))
        return "; ".join(parts)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "event_types": dict(self.event_types),
            "terminal_event": self.terminal_event,
            "terminal_status": self.terminal_status,
            "output_item_types": list(self.output_item_types),
            "image_call_statuses": list(self.image_call_statuses),
            "partial_frames": self.partial_frames,
            "incomplete_reason": self.incomplete_reason,
            "error_message": self.error_message,
            "refusals": list(self.refusals),
            "output_text": list(self.output_text),
        }


class CodexImageEmptyResponse(RuntimeError):
    """The stream finished cleanly but carried no completed image result."""

    def __init__(self, diagnostics: CodexImageStreamDiagnostics) -> None:
        self.diagnostics = diagnostics
        super().__init__(
            "Codex response contained no completed image_generation_call result "
            f"({diagnostics.summary()})"
        )


def _completed_image_calls(nodes: Any) -> List[Dict[str, Any]]:
    """Return every ``image_generation_call`` object found in a payload tree."""
    return [
        node
        for node in _walk_objects(nodes)
        if node.get("type") == "image_generation_call"
    ]


def _merge_image_calls(
    terminal_calls: List[Dict[str, Any]],
    streamed_calls: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge terminal-output calls with streamed ``output_item.done`` calls.

    The terminal ``response.completed.output`` is authoritative when it
    carries the call; the streamed copy is used when the terminal output is
    empty (observed 12.09.2026: ``output: []`` after a completed item). Calls
    are deduplicated by ``id`` so the same call seen twice is not counted as
    two generations, while two distinct ids still trip the multi-call guard.
    """
    merged: List[Dict[str, Any]] = []
    seen_ids: set = set()
    seen_results: set = set()
    for call in terminal_calls + streamed_calls:
        call_id = call.get("id")
        result = call.get("result")
        has_id = isinstance(call_id, str) and bool(call_id)
        has_result = isinstance(result, str) and bool(result)
        if has_id and call_id in seen_ids:
            continue
        # The same finished image seen twice (streamed item without an id,
        # terminal copy with one) is one generation, not two.
        if has_result and result in seen_results:
            continue
        if has_id:
            seen_ids.add(call_id)
        if has_result:
            seen_results.add(result)
        merged.append(call)
    return merged


def _collect_image_b64(
    token: str,
    *,
    prompt: str,
    size: str,
    quality: str,
    input_images: Optional[List[Dict[str, str]]] = None,
    image_model: str = DEFAULT_MODEL,
    background: str = "opaque",
    output_format: str = "png",
    output_compression: Optional[int] = None,
    action: str = "auto",
    mask_part: Optional[Dict[str, str]] = None,
    base_url: str = _CODEX_BASE_URL,
) -> Optional[Dict[str, str]]:
    """Stream a Codex Responses image_generation call.

    Return one final result only after ``response.completed`` confirms the
    stream finished. The completed ``image_generation_call`` may arrive either
    inside the terminal ``response.completed.output`` or earlier as a
    ``response.output_item.done`` item with ``status == "completed"`` — the
    Codex backend has been observed sending the finished item early and an
    empty terminal ``output`` (K21-056). Preview frames
    (``partial_image_b64``) are never a result, truncated streams are an
    error, and more than one distinct image call is an error.

    Raises :class:`CodexImageEmptyResponse` (carrying stream diagnostics)
    when the stream completed but no finished image was delivered.
    """
    import httpx
    from agent.codex_headers import codex_cloudflare_headers

    headers = codex_cloudflare_headers(token)
    headers.update({
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    payload = _build_responses_payload(
        prompt=prompt,
        size=size,
        quality=quality,
        input_images=input_images,
        image_model=image_model,
        background=background,
        output_format=output_format,
        output_compression=output_compression,
        action=action,
        mask_part=mask_part,
    )
    timeout = httpx.Timeout(300.0, connect=30.0, read=300.0, write=30.0, pool=30.0)

    diagnostics = CodexImageStreamDiagnostics()
    completed_response: Optional[Dict[str, Any]] = None
    streamed_calls: List[Dict[str, Any]] = []
    with httpx.Client(timeout=timeout, headers=headers) as http:
        with http.stream("POST", f"{base_url.rstrip('/')}/responses", json=payload) as response:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                exc.response.read()
                raise RuntimeError(
                    f"Codex Responses API returned HTTP {exc.response.status_code}: "
                    f"{_summarize_error_body(exc.response.text)}"
                ) from exc
            for event in _iter_sse_json(response):
                if not isinstance(event, dict):
                    continue
                diagnostics.observe(event)
                event_type = event.get("type")
                if event_type == "response.output_item.done":
                    item = event.get("item")
                    if (
                        isinstance(item, dict)
                        and item.get("type") == "image_generation_call"
                        and item.get("status") == "completed"
                    ):
                        streamed_calls.append(item)
                elif event_type == "response.completed":
                    terminal = event.get("response")
                    if not isinstance(terminal, dict) or terminal.get("status") != "completed":
                        raise RuntimeError(
                            "Codex image response did not complete successfully "
                            f"({diagnostics.summary()})"
                        )
                    completed_response = terminal
                elif event_type in {"response.failed", "response.incomplete"}:
                    raise RuntimeError(
                        f"Codex image response ended with {event_type} "
                        f"({diagnostics.summary()})"
                    )

    if completed_response is None:
        raise RuntimeError(
            "Codex image stream ended without response.completed "
            f"({diagnostics.summary()})"
        )

    terminal_calls = _completed_image_calls(completed_response.get("output", []))
    calls = _merge_image_calls(terminal_calls, streamed_calls)
    if len(calls) > 1:
        raise RuntimeError(
            "Codex returned multiple image_generation calls "
            f"({diagnostics.summary()})"
        )
    if not calls:
        raise CodexImageEmptyResponse(diagnostics)
    call = calls[0]
    if call.get("status") != "completed":
        raise RuntimeError(
            "Codex returned a non-completed image_generation call "
            f"({diagnostics.summary()})"
        )
    result_b64 = call.get("result")
    if not isinstance(result_b64, str) or not result_b64:
        raise CodexImageEmptyResponse(diagnostics)
    return {"b64": result_b64, "source": "final"}


def _walk_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_objects(child)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class OpenAICodexImageGenProvider(ImageGenProvider):
    """GPT Image 2.5 routed through the active profile's Codex OAuth."""

    @property
    def name(self) -> str:
        return "openai-codex"

    @property
    def display_name(self) -> str:
        return "OpenAI (Codex auth)"

    def is_available(self) -> bool:
        if not _read_codex_access_token():
            return False
        try:
            import httpx  # noqa: F401
        except ImportError:
            return False
        return True

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": model_id,
                "display": meta["display"],
                "speed": meta["speed"],
                "price": "varies",
            }
            for model_id, meta in _MODELS.items()
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "OpenAI (Codex auth)",
            "badge": "ChatGPT subscription",
            "tag": "Sunburst/Flare via profile-local ChatGPT/Codex OAuth",
            "env_vars": [],
            "post_setup_hint": (
                "Sign in with `hermes auth codex` (or `hermes setup` → Codex) "
                "if you haven't already. No API key needed."
            ),
        }

    def capabilities(self) -> Dict[str, Any]:
        # The Codex Responses image_generation tool accepts source/reference
        # images as `input_image` message content parts. Keep this capability
        # honest so the dynamic `image_generate` schema encourages identity-
        # preserving edits instead of unrelated text-to-image redraws.
        return {
            "modalities": ["text", "image"],
            "max_reference_images": _MAX_REFERENCE_IMAGES,
            "reference_limit_includes_base": True,
            "image_models": list(_MODELS),
            "qualities": list(_QUALITIES),
            "sizes": True,
            "backgrounds": list(_BACKGROUNDS),
            "output_formats": list(_OUTPUT_FORMATS),
            "output_compression": True,
            "actions": list(_ACTIONS),
            "reference_roles": list(_REFERENCE_ROLES),
            "presets": list(_PRESETS),
            "mask": True,
            "receipts": True,
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        *,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)

        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider="openai-codex",
                aspect_ratio=aspect,
            )

        try:
            import httpx  # noqa: F401
        except ImportError:
            return error_response(
                error="httpx Python package not installed (pip install httpx)",
                error_type="missing_dependency",
                provider="openai-codex",
                aspect_ratio=aspect,
            )

        try:
            model_id, _meta = _resolve_model(kwargs.get("model"))
            quality = str(kwargs.get("quality") or "medium").strip().lower()
            size = str(kwargs.get("size") or _SIZES.get(aspect, _SIZES["square"])).strip().lower()
            background = str(kwargs.get("background") or "opaque").strip().lower()
            output_format = str(kwargs.get("output_format") or "png").strip().lower()
            output_compression = kwargs.get("output_compression")
            action = str(
                kwargs.get("action") or ("edit" if image_url else "auto")
            ).strip().lower()
            _validate_options(
                quality=quality,
                size=size,
                background=background,
                output_format=output_format,
                output_compression=output_compression,
                action=action,
            )
            if action == "edit" and not image_url:
                raise ValueError("The edit action requires image_url as its base image.")
            if action == "generate" and image_url:
                raise ValueError("The generate action cannot include an edit base image.")
            if kwargs.get("mask") and (not image_url or action != "edit"):
                raise ValueError("A mask requires image_url and action='edit'.")
        except Exception as exc:
            return error_response(
                error=str(exc),
                error_type="invalid_argument",
                provider="openai-codex",
                prompt=prompt,
                aspect_ratio=aspect,
            )

        try:
            credentials = _resolve_codex_credentials()
            token = credentials["api_key"]
            base_url = credentials["base_url"]
        except Exception as exc:
            return error_response(
                error=(
                    "Codex/ChatGPT OAuth credentials are unavailable: "
                    f"{_sanitize_error_text(exc)}. Run "
                    "`hermes auth codex` (or `hermes setup` → Codex) to sign in."
                ),
                error_type="auth_required",
                provider="openai-codex",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        try:
            input_images = _normalize_input_images(image_url, reference_image_urls)
            explicit_refs = normalize_reference_images(reference_image_urls) or []
            guided_prompt = _build_guided_prompt(
                prompt,
                input_count=len(input_images),
                has_edit_base=bool(image_url),
                reference_roles=kwargs.get("reference_roles"),
                presets=kwargs.get("presets"),
                preserve=kwargs.get("preserve"),
            )
            mask_part = None
            if kwargs.get("mask"):
                mask_part = _mask_tool_part(str(kwargs["mask"]), str(image_url))
        except Exception as exc:
            return error_response(
                error=f"Invalid image input for Codex image editing: {exc}",
                error_type="invalid_image_input",
                provider="openai-codex",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        try:
            output_root, output_dir = _profile_image_output_dir()
        except Exception as exc:
            return error_response(
                error=f"Unsafe profile image output path: {_sanitize_error_text(exc)}",
                error_type="invalid_image_output",
                provider="openai-codex",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        try:
            collected = _collect_image_b64(
                token,
                prompt=guided_prompt,
                size=size,
                quality=quality,
                input_images=input_images or None,
                image_model=model_id,
                background=background,
                output_format=output_format,
                output_compression=output_compression,
                action=action,
                mask_part=mask_part,
                base_url=base_url,
            )
        except CodexImageEmptyResponse as exc:
            # The stream finished but carried no finished image. Say exactly
            # what arrived (event types, terminal status, refusal text) so the
            # next person does not guess at policy or quota.
            logger.warning("Codex image stream carried no result: %s", exc)
            err = error_response(
                error=_sanitize_error_text(exc, limit=1200),
                error_type="empty_response",
                provider="openai-codex",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )
            err["stream_diagnostics"] = exc.diagnostics.as_dict()
            return err
        except Exception as exc:
            logger.debug("Codex image generation failed", exc_info=True)
            return error_response(
                error=(
                    "OpenAI image generation via Codex auth failed: "
                    f"{_sanitize_error_text(exc, limit=1200)}"
                ),
                error_type="api_error",
                provider="openai-codex",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        if not collected or not collected.get("b64"):
            return error_response(
                error=(
                    "Codex response contained no completed image_generation_call result"
                ),
                error_type="empty_response",
                provider="openai-codex",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        image_source = collected.get("source") or "unknown"
        b64 = collected["b64"]

        # Defense in depth: never deliver a progressive-only frame as success.
        # Partials are intermediate previews and have presented as smeared /
        # unfinished images when saved as finals.
        if image_source != "final":
            pixel_hint = None
            try:
                import base64 as _b64mod

                pixel_hint = _png_pixel_size(_b64mod.b64decode(b64, validate=False))
            except Exception:
                pixel_hint = None
            detail = (
                "Codex returned only a progressive partial image frame; "
                "refusing to save it as a final deliverable."
            )
            if pixel_hint:
                detail = f"{detail} partial_pixel_size={pixel_hint}."
            err = error_response(
                error=detail,
                error_type="incomplete_image",
                provider="openai-codex",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )
            err["image_source"] = image_source
            err["requested_size"] = size
            err["partial_pixel_size"] = pixel_hint
            return err

        try:
            raw_bytes = base64.b64decode(b64, validate=True)
            extension = _FORMAT_EXTENSIONS[output_format]
            filename = f"openai_codex_{model_id}_{uuid.uuid4().hex}.{extension}"
            saved_path = output_dir / filename
            output_meta = _atomic_write_validated_image(
                saved_path, raw_bytes, output_format, allowed_root=output_root
            )
        except Exception as exc:
            return error_response(
                error=f"Could not validate/save image to profile cache: {_sanitize_error_text(exc)}",
                error_type="invalid_image_output",
                provider="openai-codex",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        receipt_path = None
        receipt_error = None
        if kwargs.get("receipt") is True:
            candidate_receipt_path = saved_path.with_suffix(saved_path.suffix + ".receipt.json")
            receipt = {
                    "schema": "korra.image-generation.receipt.v1",
                    "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "provider": "openai-codex",
                    "source": {
                        "repository": "AlekseiUL/gpt-image-2-5-agent-kit",
                        "revision": "cc52564687907194c8db02e95996757fb5d19b86",
                    },
                    "image_model": model_id,
                    "host_model": _CODEX_CHAT_MODEL,
                    "quality": quality,
                    "size": size,
                    "background": background,
                    "output_format": output_format,
                    "output_compression": output_compression,
                    "action": action,
                    "prompt_sha256": hashlib.sha256(guided_prompt.encode("utf-8")).hexdigest(),
                    "input_image_count": len(input_images),
                    "reference_roles": list(kwargs.get("reference_roles") or ["general"] * len(explicit_refs)),
                    "output": output_meta,
            }
            try:
                _atomic_publish(
                    candidate_receipt_path,
                    (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                    allowed_root=output_root,
                )
                receipt_path = candidate_receipt_path
            except Exception as exc:
                # The generated image is already a valid, published deliverable.
                # Keep that success terminal so an optional metadata failure
                # cannot prompt a second paid generation request.
                receipt_error = _sanitize_error_text(exc)

        return success_response(
            image=str(saved_path),
            model=model_id,
            prompt=prompt,
            aspect_ratio=aspect,
            provider="openai-codex",
            modality="image" if input_images else "text",
            extra={
                "size": size,
                "quality": quality,
                "background": background,
                "output_format": output_format,
                "output_compression": output_compression,
                "action": action,
                "input_image_count": len(input_images),
                "image_source": image_source,
                "requested_size": size,
                "pixel_size": f"{output_meta['width']}x{output_meta['height']}",
                "receipt": str(receipt_path) if receipt_path else None,
                "receipt_error": receipt_error,
            },
        )


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Plugin entry point — register the Codex-backed image-gen provider."""
    ctx.register_image_gen_provider(OpenAICodexImageGenProvider())
