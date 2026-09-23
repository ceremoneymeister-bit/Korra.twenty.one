"""Browser dictation uploads: what arrived is measured, logged and returned.

22.09.2026 a five-minute browser monologue of an owner reached the STT
provider as a few hundred characters, and nothing on the server recorded how
large or how long the received file was — a truncated upload looked exactly
like a short answer. Since 0.21.13 the endpoint logs size, probed duration
and the browser-clock length of every dictation, warns when the file holds
clearly less audio than was recorded, and returns ``audio_seconds`` so the
browser can tell the owner.
"""
import base64
import logging
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not installed",
)


@pytest.fixture
def client(monkeypatch, _isolate_hermes_home):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    import korra_state
    from korra_constants import get_hermes_home
    from korra_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(korra_state, "DEFAULT_DB_PATH", home / "state.db")
    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


@pytest.fixture
def heard(monkeypatch):
    """Replace the STT provider; record which file it was handed."""
    import tools.voice_mode as voice_mode

    seen = {}

    def _fake_transcribe(path):
        seen["path"] = path
        return {"success": True, "transcript": "весь монолог", "provider": "fake"}

    monkeypatch.setattr(voice_mode, "transcribe_recording", _fake_transcribe)
    return seen


def _live_webm(tmp_path, seconds: float) -> bytes:
    """Opus WebM written to a pipe: like MediaRecorder output, no Duration."""
    out = tmp_path / "live.webm"
    with out.open("wb") as fh:
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-f", "lavfi",
                "-i", f"sine=frequency=440:duration={seconds}",
                "-c:a", "libopus", "-b:a", "24k", "-f", "webm", "-",
            ],
            check=True,
            stdout=fh,
        )
    return out.read_bytes()


def _post(client, audio: bytes, **extra):
    payload = base64.b64encode(audio).decode("ascii")
    return client.post(
        "/api/audio/transcribe",
        json={
            "data_url": f"data:audio/webm;codecs=opus;base64,{payload}",
            "mime_type": "audio/webm;codecs=opus",
            **extra,
        },
    )


def test_probe_measures_live_webm_without_declared_duration(tmp_path):
    from tools.transcription_tools import _probe_audio_duration, probe_audio_seconds

    path = tmp_path / "rec.webm"
    path.write_bytes(_live_webm(tmp_path, 4))

    # The header has no answer for browser recordings...
    assert _probe_audio_duration(str(path)) is None
    # ...but the stream itself does.
    assert probe_audio_seconds(str(path)) == pytest.approx(4.0, abs=0.1)


def test_probe_returns_none_for_unreadable_file(tmp_path):
    from tools.transcription_tools import probe_audio_seconds

    path = tmp_path / "junk.webm"
    path.write_bytes(b"\x00not audio at all")
    assert probe_audio_seconds(str(path)) is None


def test_upload_logs_what_arrived_and_returns_audio_seconds(
    client, heard, tmp_path, caplog
):
    caplog.set_level(logging.INFO, logger="korra_cli.web_server")

    resp = _post(client, _live_webm(tmp_path, 4), duration_ms=4200)

    assert resp.status_code == 200
    body = resp.json()
    assert body["transcript"] == "весь монолог"
    assert body["audio_seconds"] == pytest.approx(4.0, abs=0.1)
    received = [r for r in caplog.records if "Browser dictation" in r.getMessage()]
    assert len(received) == 1
    message = received[0].getMessage()
    assert "hermes-desktop-voice-" in message
    assert "audio/webm;codecs=opus" in message
    assert "recorded 4.2s" in message
    assert received[0].levelno == logging.INFO


def test_upload_shorter_than_recorded_is_warned(client, heard, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="korra_cli.web_server")

    resp = _post(client, _live_webm(tmp_path, 4), duration_ms=312_000)

    assert resp.status_code == 200
    assert resp.json()["audio_seconds"] == pytest.approx(4.0, abs=0.1)
    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "shorter than recorded" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "for 312.0s recorded" in warnings[0]


def test_upload_from_client_without_duration_keeps_working(
    client, heard, tmp_path, caplog
):
    # The desktop app and pre-0.21.13 web clients send no duration_ms.
    caplog.set_level(logging.INFO, logger="korra_cli.web_server")

    resp = _post(client, _live_webm(tmp_path, 2))

    assert resp.status_code == 200
    assert resp.json()["audio_seconds"] == pytest.approx(2.0, abs=0.1)
    assert "recorded unknown" in caplog.text
    assert "shorter than recorded" not in caplog.text


def test_unmeasurable_upload_is_still_transcribed(client, heard, caplog):
    caplog.set_level(logging.INFO, logger="korra_cli.web_server")

    resp = _post(client, b"\x00fakeaudio", duration_ms=30_000)

    assert resp.status_code == 200
    assert resp.json()["audio_seconds"] is None
    assert heard["path"].endswith(".webm")
    assert "audio unknown" in caplog.text
