import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


def test_compatible_http_through_real_tts_and_telegram_opus(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required for Telegram conversion")
    from korra_cli.config import save_config, save_env_value
    from tools.tts_tool import text_to_speech_tool
    mp3 = tmp_path / "source.mp3"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.1", "-y", str(mp3)], check=True)
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            calls.append((self.path, self.headers.get("Authorization"), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.end_headers()
            self.wfile.write(mp3.read_bytes())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-send")
    monkeypatch.setenv("KORRA_VOICE_HTTP_KEY", "other-profile-key")
    monkeypatch.setattr("gateway.session_context.get_session_env", lambda key, default="": "telegram" if key == "KORRA_SESSION_PLATFORM" else default)
    save_config({"voice": {"agent_voice": {"enabled": True}}, "tts": {"provider": "compatible", "compatible": {
        "base_url": f"http://127.0.0.1:{server.server_port}/v1", "model": "local-model", "voice": "warm", "speed": 1.1}}})
    save_env_value("KORRA_VOICE_HTTP_KEY", "profile-key")
    try:
        result = json.loads(text_to_speech_tool("Привет", output_path=str(tmp_path / "reply.ogg")))
        assert result["success"], result
        assert Path(result["file_path"]).read_bytes().startswith(b"OggS")
        assert result["voice_compatible"] is True
        assert calls == [("/v1/audio/speech", "Bearer profile-key", {"input": "Привет", "model": "local-model", "voice": "warm", "response_format": "mp3", "speed": 1.1})]
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_disabled_voice_never_calls_provider(monkeypatch):
    from korra_cli.config import save_config
    from tools.tts_tool import check_tts_requirements, text_to_speech_tool
    save_config({"voice": {"agent_voice": {"enabled": False}}, "tts": {"provider": "compatible"}})
    def forbidden(*_args, **_kwargs):
        pytest.fail("disabled voice called a service")
    monkeypatch.setattr("korra_cli.agent_voice.generate_compatible", forbidden)
    assert not check_tts_requirements()
    assert not json.loads(text_to_speech_tool("hello"))["success"]


@pytest.mark.parametrize("status", [200, 401, 503, 302])
def test_elevenlabs_http_contract_no_sdk_or_fallback(tmp_path, monkeypatch, status):
    import httpx
    from korra_cli.config import save_config, save_env_value
    from tools.tts_tool import text_to_speech_tool, check_tts_requirements
    save_config({"voice": {"agent_voice": {"enabled": True, "provider": "elevenlabs"}},
                 "tts": {"provider": "elevenlabs", "elevenlabs": {"voice_id": "voice-one", "model_id": "eleven_flash_v2_5", "speed": 1.1}}})
    save_env_value("KORRA_VOICE_ELEVENLABS_KEY", "own-key")
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/text-to-speech/voice-one/stream"
        assert request.url.params["output_format"] == "mp3_44100_128"
        assert request.headers["xi-api-key"] == "own-key"
        assert json.loads(request.content) == {"text": "hello", "model_id": "eleven_flash_v2_5", "voice_settings": {"speed": 1.1}}
        return httpx.Response(status, content=b"ID3fixture", headers={"content-type": "audio/mpeg", "location": "https://another.example/speech"})
    client_class = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs))
    def no_sdk():
        pytest.fail("managed voice requires no optional SDK")
    monkeypatch.setattr("tools.tts_tool._import_elevenlabs", no_sdk)
    assert check_tts_requirements()
    result = json.loads(text_to_speech_tool("hello", output_path=str(tmp_path / "speech.mp3")))
    assert result["success"] is (status == 200)
    assert len(calls) == 1
    if status != 200:
        assert not (tmp_path / "speech.mp3").exists()


def test_managed_voice_cannot_switch_to_unselected_paid_provider():
    from korra_cli.config import save_config
    from tools.tts_tool import text_to_speech_tool
    save_config({"voice": {"agent_voice": {"enabled": True, "provider": "compatible"}}})
    result = json.loads(text_to_speech_tool("hello", provider="openai"))
    assert not result["success"]
