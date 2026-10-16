"""A failed text_to_speech call must never destroy a file that already lived at output_path.

The model picks ``output_path``; before this fix a provider failure swept that path as a
"partial artifact" even when it was the user's pre-existing file.
"""

import json
from pathlib import Path

import pytest

from gateway.session_context import _UNSET, _VAR_MAP
from tools import tts_tool


@pytest.fixture(autouse=True)
def _edge_without_session_platform(monkeypatch):
    for var in _VAR_MAP.values():
        var.set(_UNSET)
    monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {"provider": "edge"})
    monkeypatch.setattr(tts_tool, "_import_edge_tts", lambda: object())
    yield
    for var in _VAR_MAP.values():
        var.set(_UNSET)


@pytest.fixture
def audio_dir(tmp_path):
    path = tmp_path / "audio"  # tmp_path also holds the conftest's isolated HERMES_HOME
    path.mkdir()
    return path


async def _partial_write_then_fail(_text: str, output_path: str, _cfg: dict) -> str:
    Path(output_path).write_bytes(b"partial")
    raise RuntimeError("provider unreachable")


async def _write_audio(_text: str, output_path: str, _cfg: dict) -> str:
    Path(output_path).write_bytes(b"new-audio")
    return output_path


def test_failed_synthesis_keeps_existing_file_and_cleans_partial(audio_dir, monkeypatch):
    existing = audio_dir / "notes.mp3"
    existing.write_bytes(b"user data")
    monkeypatch.setattr(tts_tool, "_generate_edge_tts", _partial_write_then_fail)

    result = json.loads(tts_tool.text_to_speech_tool("hello", output_path=str(existing)))

    assert result["success"] is False
    assert existing.read_bytes() == b"user data"
    assert sorted(p.name for p in audio_dir.iterdir()) == ["notes.mp3"]


def test_failed_synthesis_to_new_path_leaves_nothing_behind(audio_dir, monkeypatch):
    monkeypatch.setattr(tts_tool, "_generate_edge_tts", _partial_write_then_fail)

    result = json.loads(tts_tool.text_to_speech_tool("hello", output_path=str(audio_dir / "new.mp3")))

    assert result["success"] is False
    assert list(audio_dir.iterdir()) == []


def test_successful_synthesis_still_overwrites_existing_file(audio_dir, monkeypatch):
    existing = audio_dir / "notes.mp3"
    existing.write_bytes(b"user data")
    monkeypatch.setattr(tts_tool, "_generate_edge_tts", _write_audio)

    result = json.loads(tts_tool.text_to_speech_tool("hello", output_path=str(existing)))

    assert result["success"] is True
    assert result["file_path"] == str(existing)
    assert existing.read_bytes() == b"new-audio"
    assert sorted(p.name for p in audio_dir.iterdir()) == ["notes.mp3"]


@pytest.mark.parametrize("target_exists", [False, True])
def test_failed_piper_preserves_existing_sibling_wav(tmp_path, monkeypatch, target_exists):
    from types import SimpleNamespace
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    target = audio_dir / "report.mp3"
    existing = target.with_suffix(".wav")
    existing.write_bytes(b"original recording")
    if target_exists:
        target.write_bytes(b"original mp3")

    class Voice:
        def synthesize_wav(self, text, wav_file):
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(b"\x00\x00")
            raise RuntimeError("synthesis failed")

    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {
        "provider": "piper", "piper": {"voices_dir": str(tmp_path / "voices")},
    })
    monkeypatch.setattr(tts_tool, "_import_piper", lambda: SimpleNamespace(load=lambda *a, **kw: Voice()))
    monkeypatch.setattr(tts_tool, "_resolve_piper_voice_path", lambda *a: tmp_path / "fake.onnx")
    result = json.loads(tts_tool.text_to_speech_tool("hello", output_path=str(target)))
    assert result["success"] is False
    assert existing.read_bytes() == b"original recording"
    if target_exists:
        assert target.read_bytes() == b"original mp3"
    else:
        assert not target.exists()
    assert sorted(p.name for p in audio_dir.iterdir()) == (
        ["report.mp3", "report.wav"] if target_exists else ["report.wav"]
    )


def _multipart_setup(monkeypatch):
    monkeypatch.setattr(tts_tool, "_resolve_max_text_length", lambda *_a, **_k: 35)

    def single(text, output_path, **_kwargs):
        Path(output_path).write_bytes(b"chunk-" + text[:3].encode())
        return json.dumps({"success": True, "file_path": output_path, "provider": "edge"})

    def deliver(encoded, output_path, _profile, **_kwargs):
        base = Path(output_path)
        staged = []
        for index, source in enumerate(encoded, start=1):
            target = base.with_name(f"{base.stem}.part{index:02d}{base.suffix}")
            Path(source).replace(target)
            staged.append(str(target))
        return staged, False

    monkeypatch.setattr(tts_tool, "_text_to_speech_single", single)
    monkeypatch.setattr(tts_tool, "_build_audio_delivery_files", deliver)
    return "Первое предложение для озвучки. Второе предложение для озвучки."


def test_multipart_failure_keeps_existing_first_part(audio_dir, monkeypatch):
    text = _multipart_setup(monkeypatch)
    first = audio_dir / "voice.part01.mp3"
    first.write_bytes(b"old-first")
    (audio_dir / "voice.part02.mp3").mkdir()  # destination of the second part is unusable

    result = json.loads(tts_tool.text_to_speech_tool(text, output_path=str(audio_dir / "voice.mp3")))

    assert result["success"] is False
    assert "voice.part02.mp3" in result["error"]
    assert first.read_bytes() == b"old-first"
    assert sorted(p.name for p in audio_dir.iterdir()) == ["voice.part01.mp3", "voice.part02.mp3"]


def test_multipart_success_replaces_all_existing_parts(audio_dir, monkeypatch):
    text = _multipart_setup(monkeypatch)
    (audio_dir / "voice.part01.mp3").write_bytes(b"old-first")
    (audio_dir / "voice.part02.mp3").write_bytes(b"old-second")

    result = json.loads(tts_tool.text_to_speech_tool(text, output_path=str(audio_dir / "voice.mp3")))

    assert result["success"] is True
    assert len(result["file_paths"]) == 2
    for path in result["file_paths"]:
        assert Path(path).read_bytes().startswith(b"chunk-")
    assert sorted(p.name for p in audio_dir.iterdir()) == ["voice.part01.mp3", "voice.part02.mp3"]
