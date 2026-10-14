"""Промежуточный WAV и OGG — внутри временного каталога и для имени без расширения: соседний файл цел (R2, ревью 0.21.19)."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('provider', ['piper', 'neutts', 'kittentts'])
@pytest.mark.parametrize('suffix', ['.mp3', ''])
def test_failed_wav_provider_preserves_sibling(tmp_path, monkeypatch, provider, suffix):
    from tools import tts_tool
    audio = tmp_path / 'audio'
    audio.mkdir()
    target = audio / ('report' + suffix)
    sibling = audio / ('report.wav' if suffix else '.wav')
    sibling.write_bytes(b'original recording')
    cfg = {'provider': provider, 'piper': {'voices_dir': str(tmp_path / 'voices')}, 'kittentts': {'model': str(tmp_path / 'fake-model')}}
    monkeypatch.setattr(tts_tool, '_load_tts_config', lambda: cfg)

    if provider == 'piper':
        class Voice:
            def synthesize_wav(self, text, wav_file):
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b'\x00\x00')
                raise RuntimeError('synthesis failed')
        monkeypatch.setattr(tts_tool, '_import_piper', lambda: SimpleNamespace(load=lambda *a, **kw: Voice()))
        monkeypatch.setattr(tts_tool, '_resolve_piper_voice_path', lambda *a: tmp_path / 'fake.onnx')
    elif provider == 'neutts':
        monkeypatch.setattr(tts_tool, '_check_neutts_available', lambda: True)
        def synthesize(cmd, **kwargs):
            wav = Path(cmd[cmd.index('--out') + 1])
            wav.write_bytes(b'partial wav')
            return SimpleNamespace(returncode=1, stderr='synthesis failed')
        monkeypatch.setattr(tts_tool.subprocess, 'run', synthesize)
    else:
        model = SimpleNamespace(generate=lambda *a, **kw: [0.0, 0.0])
        monkeypatch.setattr(tts_tool, '_import_kittentts', lambda: lambda *a: model)
        def write_wav(path, audio, rate):
            Path(path).write_bytes(b'partial wav')
            raise RuntimeError('WAV write failed')
        monkeypatch.setitem(sys.modules, 'soundfile', SimpleNamespace(write=write_wav))

    result = json.loads(tts_tool.text_to_speech_tool('hello', output_path=str(target)))
    assert result['success'] is False
    assert sibling.read_bytes() == b'original recording'
    assert not target.exists()
