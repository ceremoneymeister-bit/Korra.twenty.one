"""K21-057: HEIC photos from an iPhone must be visible and usable.

Live case 12.09.2026: a folder of five photos — two JPG, three HEIC — gave the
agent "two references". The HEIC files vanished without a log line, because
``agent.image_routing`` swallowed the missing ``pillow-heif`` ImportError and
"plugin missing" looked exactly like "format unsupported".

These tests cover the shared transcoder, the native-attach path, the
vision_analyze normaliser, the Codex image-input path, Telegram document
intake and the panel readers. The HEIC fixture is produced by pillow-heif at
test time (the image ships it as a core dependency), so no binary blob is
committed.
"""

from __future__ import annotations

import base64
import importlib
import io
from pathlib import Path

import pytest
from PIL import Image

import agent.image_routing as image_routing


pillow_heif = pytest.importorskip("pillow_heif", reason="pillow-heif is a core dependency (K21-057)")


@pytest.fixture(autouse=True)
def _fresh_plugin_state():
    image_routing._reset_optional_image_plugins_for_tests()
    yield
    image_routing._reset_optional_image_plugins_for_tests()


def _heic_bytes(color=(90, 120, 200)) -> bytes:
    pillow_heif.register_heif_opener()
    out = io.BytesIO()
    Image.new("RGB", (16, 12), color).save(out, format="HEIF")
    data = out.getvalue()
    assert data[4:8] == b"ftyp", "fixture must be an ISO-BMFF HEIF container"
    return data


def _jpeg_bytes() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 0, 0)).save(out, format="JPEG")
    return out.getvalue()


def _png_size(data: bytes):
    with Image.open(io.BytesIO(data)) as im:
        return im.format, im.size


# ── Shared transcoder ─────────────────────────────────────────────────────────


def test_heic_is_sniffed_and_transcoded_to_png():
    raw = _heic_bytes()
    assert image_routing._sniff_mime_from_bytes(raw) == "image/heic"

    png, reason = image_routing.transcode_image_to_png(raw)

    assert reason == ""
    assert png is not None
    assert _png_size(png) == ("PNG", (16, 12))


def test_missing_heif_plugin_is_named_not_silent(monkeypatch):
    raw = _heic_bytes()
    monkeypatch.setattr(
        image_routing, "register_optional_image_plugins", lambda: {"heif": False, "avif": True}
    )

    png, reason = image_routing.transcode_image_to_png(raw, mime="image/heic")

    assert png is None
    assert "pillow-heif" in reason
    assert "HEIC" in reason


def test_svg_reason_is_explicit():
    png, reason = image_routing.transcode_image_to_png(b"<svg xmlns='x'/>")
    assert png is None
    assert "SVG" in reason


def test_image_extensions_cover_iphone_and_chromium_formats(tmp_path: Path):
    for ext in (".HEIC", ".heif", ".avif"):
        target = tmp_path / f"photo{ext}"
        target.write_bytes(_heic_bytes())
        local, _urls = image_routing.extract_image_refs(f"посмотри {target}")
        assert local == [str(target)], ext


# ── Native attach path ────────────────────────────────────────────────────────


def test_heic_file_attaches_as_png_data_url(tmp_path: Path):
    target = tmp_path / "IMG_2249.HEIC"
    target.write_bytes(_heic_bytes())

    url = image_routing._file_to_data_url(target)

    assert url is not None and url.startswith("data:image/png;base64,")
    decoded = base64.b64decode(url.split(",", 1)[1])
    assert _png_size(decoded) == ("PNG", (16, 12))


def test_mixed_folder_attaches_every_photo(tmp_path: Path):
    jpg = tmp_path / "IMG_2581.jpg"
    jpg.write_bytes(_jpeg_bytes())
    heic_upper = tmp_path / "IMG_2249.HEIC"
    heic_upper.write_bytes(_heic_bytes())
    heic_lower = tmp_path / "IMG_2397.heic"
    heic_lower.write_bytes(_heic_bytes((1, 2, 3)))

    parts, skipped = image_routing.build_native_content_parts(
        "вот референсы", [str(jpg), str(heic_upper), str(heic_lower)]
    )

    assert skipped == []
    images = [p for p in parts if p.get("type") == "image_url"]
    assert len(images) == 3
    assert parts[0]["type"] == "text"
    assert "IMG_2249.HEIC" in parts[0]["text"]
    assert "[Изображение не прочитано" not in parts[0]["text"]


def test_dropped_photo_is_named_with_reason(tmp_path: Path, monkeypatch):
    jpg = tmp_path / "IMG_2581.jpg"
    jpg.write_bytes(_jpeg_bytes())
    heic = tmp_path / "IMG_2249.HEIC"
    heic.write_bytes(_heic_bytes())
    monkeypatch.setattr(
        image_routing, "register_optional_image_plugins", lambda: {"heif": False, "avif": True}
    )

    parts, skipped = image_routing.build_native_content_parts("референсы", [str(jpg), str(heic)])

    assert skipped == [str(heic)]
    assert len([p for p in parts if p.get("type") == "image_url"]) == 1
    text = parts[0]["text"]
    assert "[Изображение не прочитано: IMG_2249.HEIC" in text
    assert "pillow-heif" in text


def test_all_photos_dropped_still_names_them(tmp_path: Path, monkeypatch):
    heic = tmp_path / "IMG_2755 2.HEIC"
    heic.write_bytes(_heic_bytes())
    monkeypatch.setattr(
        image_routing, "register_optional_image_plugins", lambda: {"heif": False, "avif": True}
    )

    parts, skipped = image_routing.build_native_content_parts("что на фото?", [str(heic)])

    assert skipped == [str(heic)]
    assert parts == [{"type": "text", "text": parts[0]["text"]}]
    assert parts[0]["text"].startswith("что на фото?")
    assert "IMG_2755 2.HEIC" in parts[0]["text"]


# ── vision_analyze ────────────────────────────────────────────────────────────


def test_vision_mime_sniff_recognises_heic():
    from tools.vision_tools import _detect_image_mime_type_from_bytes

    assert _detect_image_mime_type_from_bytes(_heic_bytes()) == "image/heic"
    assert _detect_image_mime_type_from_bytes(b"TOP-SECRET=1\n") is None


def test_vision_normaliser_transcodes_heic(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from tools.vision_tools import _normalize_to_supported_image

    source = tmp_path / "temp_image_x.img"
    source.write_bytes(_heic_bytes())

    path, mime, error = _normalize_to_supported_image(source, "image/heic", "/opt/data/IMG_2249.HEIC")

    assert error is None
    assert mime == "image/png"
    assert path is not None and _png_size(path.read_bytes())[0] == "PNG"


def test_vision_normaliser_error_names_source_and_cause(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        image_routing, "register_optional_image_plugins", lambda: {"heif": False, "avif": True}
    )
    from tools.vision_tools import _normalize_to_supported_image

    source = tmp_path / "temp_image_y.img"
    source.write_bytes(_heic_bytes())

    path, mime, error = _normalize_to_supported_image(source, "image/heic", "/opt/data/IMG_2249.HEIC")

    assert path is None and mime is None
    assert "IMG_2249.HEIC" in error
    assert "HEIC" in error
    assert "pillow-heif" in error


# ── image_generate (Codex) references ────────────────────────────────────────


def test_codex_local_heic_reference_becomes_png(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    provider_mod = importlib.import_module("plugins.image_gen.openai-codex")
    target = tmp_path / "IMG_2249.HEIC"
    target.write_bytes(_heic_bytes())

    url = provider_mod._local_image_to_data_url(str(target))

    assert url.startswith("data:image/png;base64,")
    assert _png_size(base64.b64decode(url.split(",", 1)[1]))[0] == "PNG"


def test_codex_unsupported_reference_names_file_and_format(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    provider_mod = importlib.import_module("plugins.image_gen.openai-codex")
    target = tmp_path / "logo.ico"
    target.write_bytes(b"\x00\x00\x01\x00" + b"\x00" * 60)

    with pytest.raises(ValueError) as excinfo:
        provider_mod._local_image_to_data_url(str(target))
    message = str(excinfo.value)
    assert "logo.ico" in message
    assert "ICO" in message


def test_codex_heic_without_plugin_names_the_plugin(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        image_routing, "register_optional_image_plugins", lambda: {"heif": False, "avif": True}
    )
    provider_mod = importlib.import_module("plugins.image_gen.openai-codex")
    target = tmp_path / "IMG_2397.heic"
    target.write_bytes(_heic_bytes())

    with pytest.raises(ValueError) as excinfo:
        provider_mod._local_image_to_data_url(str(target))
    message = str(excinfo.value)
    assert "IMG_2397.heic" in message
    assert "pillow-heif" in message


def test_codex_generate_with_heic_reference_sends_png_part(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    provider_mod = importlib.import_module("plugins.image_gen.openai-codex")
    monkeypatch.setattr(
        provider_mod,
        "_resolve_codex_credentials",
        lambda: {"api_key": "t", "base_url": "https://chatgpt.com/backend-api/codex"},
    )
    seen = {}

    def _collect(token, **kwargs):
        seen.update(kwargs)
        out = io.BytesIO()
        Image.new("RGB", (4, 4), (1, 2, 3)).save(out, format="PNG")
        return {"b64": base64.b64encode(out.getvalue()).decode("ascii"), "source": "final"}

    monkeypatch.setattr(provider_mod, "_collect_image_b64", _collect)
    ref = tmp_path / "IMG_2249.HEIC"
    ref.write_bytes(_heic_bytes())

    result = provider_mod.OpenAICodexImageGenProvider().generate(
        "portrait", reference_image_urls=[str(ref)], reference_roles=["identity"]
    )

    assert result["success"] is True, result
    parts = seen["input_images"]
    assert len(parts) == 1
    assert parts[0]["image_url"].startswith("data:image/png;base64,")


# ── Platform intake and panel ────────────────────────────────────────────────


def test_gateway_accepts_heic_document_bytes_as_image():
    from gateway.platforms.base import SUPPORTED_IMAGE_DOCUMENT_TYPES, _looks_like_image

    assert _looks_like_image(_heic_bytes()) is True
    assert _looks_like_image(b"<html>nope</html>") is False
    assert SUPPORTED_IMAGE_DOCUMENT_TYPES[".heic"] == "image/heic"
    assert SUPPORTED_IMAGE_DOCUMENT_TYPES[".avif"] == "image/avif"


def test_telegram_document_maps_cover_heic():
    adapter = importlib.import_module("plugins.platforms.telegram.adapter")

    assert ".heic" in adapter._TELEGRAM_IMAGE_EXTENSIONS
    assert adapter._TELEGRAM_IMAGE_MIME_TO_EXT["image/heic"] == ".heic"
    assert adapter._TELEGRAM_IMAGE_EXT_TO_MIME[".heif"] == "image/heif"


def test_panel_readers_treat_iphone_formats_as_images():
    from korra_cli.web_server import _CHAT_ATTACHMENT_READERS

    assert _CHAT_ATTACHMENT_READERS[".heic"] == "image"
    assert _CHAT_ATTACHMENT_READERS[".heif"] == "image"
    assert _CHAT_ATTACHMENT_READERS[".avif"] == "image"


def test_file_search_glob_is_case_insensitive(tmp_path: Path):
    from tools.file_operations import ShellFileOperations

    class _Env:
        def __init__(self):
            self.cwd = str(tmp_path)
            self.commands = []

        def execute(self, command, cwd=None, **kwargs):
            self.commands.append(command)
            if command.startswith("test -e"):
                return {"output": "exists\n", "returncode": 0}
            if command.startswith("command -v"):
                return {"output": "yes\n", "returncode": 0}
            return {"output": "", "returncode": 1}

    env = _Env()
    ShellFileOperations(env).search("*.heic", path=str(tmp_path), target="files")

    rg_commands = [c for c in env.commands if c.startswith("rg --files")]
    assert rg_commands, env.commands
    assert all("--glob-case-insensitive" in c for c in rg_commands)
