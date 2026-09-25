import json
import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "voice-test")
    from korra_cli.web_server import app
    with TestClient(app, raise_server_exceptions=False) as http:
        http.headers["Authorization"] = "Bearer voice-test"
        yield http, tmp_path


def draft(**patch):
    return {"enabled": True, "provider": "compatible", "voice": "calm",
            "model": "local-tts", "base_url": "http://localhost:7777/v1",
            "speed": 1, "web_mode": "manual", "telegram_mode": "off", **patch}


def test_default_is_off_and_speech_refused(client):
    http, _ = client
    assert http.get("/api/profiles/default/voice").json()["enabled"] is False
    assert http.post("/api/profiles/default/voice/speak", json={"text": "hello"}).status_code == 409


def test_profile_settings_and_keys_are_isolated_and_survive_read(client, monkeypatch):
    http, root = client
    assert http.post("/api/profiles", json={"name": "listener", "no_skills": True}).status_code == 200
    (root / "config.yaml").write_text("model:\n  provider: fixture\n")
    before = (root / "config.yaml").read_bytes()
    monkeypatch.setenv("KORRA_VOICE_HTTP_KEY", "foreign-process-secret")
    reply = http.put("/api/profiles/listener/voice", json=draft(api_key="own-secret", telegram_mode="all"))
    assert reply.status_code == 200, reply.text
    assert reply.json()["has_key"] is True
    assert "own-secret" not in reply.text and "foreign-process-secret" not in reply.text
    assert (root / "config.yaml").read_bytes() == before
    assert http.get("/api/profiles/default/voice").json()["has_key"] is False
    cfg = yaml.safe_load((root / "profiles/listener/config.yaml").read_text())
    assert cfg["tts"]["provider"] == "compatible"
    assert cfg["voice"]["agent_voice"]["telegram_mode"] == "all"
    assert "own-secret" not in (root / "profiles/listener/config.yaml").read_text()
    assert http.put("/api/profiles/listener/voice", json=draft()).json()["has_key"] is True
    assert http.put("/api/profiles/listener/voice", json=draft(clear_key=True)).json()["has_key"] is False
    assert os.environ["KORRA_VOICE_HTTP_KEY"] == "foreign-process-secret"


@pytest.mark.parametrize("url", ["file:///tmp/audio", "https://user:secret@example.com/v1", "https://example.com/v1?key=secret", "", "http://host:bad/v1"])
def test_bad_endpoint_refused_without_write(client, url):
    http, root = client
    before = (root / "config.yaml").read_bytes() if (root / "config.yaml").exists() else None
    reply = http.put("/api/profiles/default/voice", json=draft(base_url=url))
    assert reply.status_code == 400
    assert ((root / "config.yaml").read_bytes() if (root / "config.yaml").exists() else None) == before


def test_elevenlabs_requires_own_key_not_process_key(client, monkeypatch):
    http, _ = client
    monkeypatch.setenv("ELEVENLABS_API_KEY", "unrelated-account")
    assert http.put("/api/profiles/default/voice", json=draft(provider="elevenlabs")).status_code == 400
    assert http.put("/api/profiles/default/voice", json=draft(provider="elevenlabs", enabled=False, api_key="test-key")).status_code == 200


def test_synthesis_uses_selected_profile_and_cleans_audio(client, monkeypatch):
    http, root = client
    assert http.post("/api/profiles", json={"name": "listener", "no_skills": True}).status_code == 200
    assert http.put("/api/profiles/listener/voice", json=draft(api_key="own-secret")).status_code == 200
    paths = []
    def fake_tts(text, output_path, provider):
        from korra_constants import get_hermes_home
        from korra_cli.agent_voice import profile_key
        assert get_hermes_home() == root / "profiles/listener"
        assert profile_key("compatible") == "own-secret"
        assert provider == "compatible" and text == "hello"
        path = Path(output_path)
        path.write_bytes(b"sample")
        paths.append(path)
        return json.dumps({"success": True, "file_path": str(path)})
    monkeypatch.setattr("tools.tts_tool.text_to_speech_tool", fake_tts)
    result = http.post("/api/profiles/listener/voice/speak", json={"text": "hello"})
    assert result.status_code == 200, result.text
    assert result.json()["clips"] == ["data:audio/mpeg;base64,c2FtcGxl"]
    assert not paths[0].exists()
    assert http.post("/api/profiles/listener/voice/speak", json={"text": "x" * 5001}).status_code == 422


def test_synthesis_error_does_not_expose_provider_secret(client, monkeypatch):
    http, _ = client
    assert http.put("/api/profiles/default/voice", json=draft()).status_code == 200
    def fail(*args, **kwargs):
        raise RuntimeError("Bearer secret-should-not-leak")
    monkeypatch.setattr("tools.tts_tool.text_to_speech_tool", fail)
    reply = http.post("/api/profiles/default/voice/speak", json={"text": "hello"})
    assert reply.status_code == 502
    assert "secret-should-not-leak" not in reply.text


def test_real_tts_api_respects_installed_image_write_root(client, monkeypatch):
    import httpx
    http, root = client
    monkeypatch.setenv("KORRA_WRITE_SAFE_ROOT", str(root))
    assert http.post("/api/profiles", json={"name": "listener", "no_skills": True}).status_code == 200
    assert http.put("/api/profiles/listener/voice", json=draft()).status_code == 200
    requests = []
    def audio_service(request):
        requests.append(request)
        return httpx.Response(200, content=b"ID3fixture", headers={"content-type": "audio/mpeg"})
    client_class = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: client_class(transport=httpx.MockTransport(audio_service), **kw))
    result = http.post("/api/profiles/listener/voice/speak", json={"text": "Привет"})
    assert result.status_code == 200, result.text
    assert result.json()["clips"] == ["data:audio/mpeg;base64,SUQzZml4dHVyZQ=="]
    assert len(requests) == 1
    assert not list((root / "profiles/listener").rglob("korra-voice-*"))


@pytest.mark.parametrize("mode,kind,expected", [("all", "text", True), ("voice_only", "text", False), ("voice_only", "voice", True), ("off", "voice", False)])
def test_telegram_mode_handles_voice_in_scoped_runner_once(tmp_path, mode, kind, expected):
    from gateway.run import GatewayRunner
    from gateway.config import Platform
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.session import SessionSource
    from korra_cli.config import save_config
    save_config({"voice": {"agent_voice": {"enabled": True, "telegram_mode": mode}}})
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._voice_mode = {}
    runner.adapters = {}
    event = MessageEvent(text="hello", message_type=MessageType.VOICE if kind == "voice" else MessageType.TEXT,
                         source=SessionSource(platform=Platform.TELEGRAM, chat_id="1"))
    assert runner._should_send_voice_reply(event, "answer", [], already_sent=False) is expected
    assert event._korra_voice_handled is True


def test_telegram_explicit_chat_optout_and_tool_dedup():
    from gateway.run import GatewayRunner
    from gateway.config import Platform
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.session import SessionSource
    from korra_cli.config import save_config
    save_config({"voice": {"agent_voice": {"enabled": True, "telegram_mode": "all"}}})
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._voice_mode = {"telegram:1": "off"}
    runner.adapters = {}
    event = MessageEvent(text="hello", message_type=MessageType.TEXT, source=SessionSource(platform=Platform.TELEGRAM, chat_id="1"))
    assert not runner._should_send_voice_reply(event, "answer", [])
    runner._voice_mode = {}
    messages = [{"role": "assistant", "tool_calls": [{"function": {"name": "text_to_speech"}}]}]
    assert not runner._should_send_voice_reply(event, "answer", messages)
