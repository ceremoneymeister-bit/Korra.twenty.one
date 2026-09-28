import json
import os
import base64
import hashlib
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


def test_bundled_voices_can_be_previewed_without_a_key(client):
    http, root = client
    catalog = http.get("/api/voices")
    assert catalog.status_code == 200, catalog.text
    voices = {voice["id"]: voice for voice in catalog.json()["voices"]}
    assert {"navigator", "boss"} <= set(voices)
    for voice_id, expected_hash in {
        "navigator": "ecde8fc560f7f46cf434c59ea0e13a2770e79bafde8ea6a395ad56fe9a5155aa",
        "boss": "d974072da8562d5384013c359edc16eec65895fdbf5425ba43ede1f09aca97a5",
    }.items():
        sample = http.get(voices[voice_id]["sample_url"])
        assert sample.status_code == 200 and sample.headers["content-type"].startswith("audio/mpeg")
        assert hashlib.sha256(sample.content).hexdigest() == expected_hash
    navigator = voices["navigator"]
    assert navigator["model"] == "fish-audio/s2.1-pro"
    assert navigator["default_speed"] == 1.05
    assert voices["boss"]["default_speed"] == 1.0
    assert http.get("/api/voices/unknown/sample").status_code == 404
    selected = draft(provider="openrouter_fish", voice="navigator", model=navigator["model"],
                     base_url="", speed=1.05)
    assert http.put("/api/profiles/default/voice", json=selected).status_code == 400
    assert http.put("/api/profiles/default/voice", json={**selected, "api_key": "my-openrouter-key"}).status_code == 200
    assert http.get("/api/profiles/default/voice").json() == {
        **selected, "has_key": True,
    }
    assert "my-openrouter-key" not in (root / "config.yaml").read_text()
    assert http.post("/api/profiles", json={"name": "listener", "no_skills": True}).status_code == 200
    assert http.get("/api/profiles/listener/voice").json()["has_key"] is False
    assert http.put("/api/profiles/listener/voice", json={**selected, "voice": "unknown"}).status_code == 400


def test_bundled_navigator_synthesis_sends_reference_and_selected_speed(client, monkeypatch):
    import httpx
    http, root = client
    selected = draft(provider="openrouter_fish", voice="navigator", model="fish-audio/s2.1-pro",
                     base_url="", speed=1.05, api_key="profile-openrouter-key")
    assert http.put("/api/profiles/default/voice", json=selected).status_code == 200
    captured = []
    def service(request):
        captured.append(request)
        return httpx.Response(200, content=b"ID3fixture", headers={"content-type": "audio/mpeg"})
    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original_client(transport=httpx.MockTransport(service), **kw))
    result = http.post("/api/profiles/default/voice/speak", json={"text": "Привет, Дима"})
    assert result.status_code == 200, result.text
    assert result.json()["clips"] == ["data:audio/mpeg;base64,SUQzZml4dHVyZQ=="]
    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/audio/speech"
    assert request.headers["authorization"] == "Bearer profile-openrouter-key"
    payload = json.loads(request.content)
    assert payload["model"] == "fish-audio/s2.1-pro" and payload["input"] == "Привет, Дима"
    assert payload["provider"] == {"only": ["fish-audio"], "allow_fallbacks": False,
                                    "max_price": {"prompt": 0.000015, "completion": 0},
                                    "options": {"fish-audio": {"prosody": {"speed": 1.05}}}}
    assert hashlib.sha256(base64.b64decode(payload["input_references"][0]["input_audio"]["data"].split(",", 1)[1])).hexdigest() == "0cae14a9421df33626741420fca6d8eae0ef1a57c138b5bf2e3aa3a1a4b5ea0f"
    assert payload["input_references"][1]["text"].startswith("Внешне это пока")
    assert not list(root.rglob("korra-voice-*"))


def test_bundled_boss_synthesis_uses_approved_voice_and_rap_style(client, monkeypatch):
    import httpx
    http, _ = client
    selected = draft(provider="openrouter_fish", voice="boss", model="fish-audio/s2.1-pro",
                     base_url="", speed=1.0, api_key="profile-openrouter-key")
    assert http.put("/api/profiles/default/voice", json=selected).status_code == 200
    captured = []
    original_client = httpx.Client
    def service(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, content=b"ID3fixture", headers={"content-type": "audio/mpeg"})
    monkeypatch.setattr(httpx, "Client", lambda **kw: original_client(transport=httpx.MockTransport(service), **kw))
    result = http.post("/api/profiles/default/voice/speak", json={"text": "Здравствуйте, чем помочь?"})
    assert result.status_code == 200, result.text
    assert len(captured) == 1
    payload = captured[0]
    assert payload["input"] == "[playful, confident spoken rap] Здравствуйте, чем помочь?"
    assert payload["provider"]["options"]["fish-audio"]["prosody"]["speed"] == 1.0
    assert hashlib.sha256(base64.b64decode(payload["input_references"][0]["input_audio"]["data"].split(",", 1)[1])).hexdigest() == "b5897e6762a796ccbb915e80fedfffbf2e5257100e8fe37412a59a375d590520"


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
