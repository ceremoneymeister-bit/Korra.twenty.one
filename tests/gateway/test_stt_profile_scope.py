"""F10: pending/clarify voice uses the recipient's profile, including worker threads."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent import secret_scope, transcription_registry
from agent.transcription_provider import TranscriptionProvider
from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner, _profile_runtime_scope
from gateway.session import SessionSource
from korra_constants import get_hermes_home


class ScopedSTT(TranscriptionProvider):
    name = "scope-test-stt"

    def __init__(self):
        self.calls = []

    def is_available(self):
        return bool(secret_scope.get_secret("DEEPGRAM_API_KEY"))

    def transcribe(self, file_path, **kwargs):
        key = secret_scope.get_secret("DEEPGRAM_API_KEY")
        self.calls.append((get_hermes_home(), key))
        return {"success": True, "transcript": get_hermes_home().name}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    root.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("KORRA_HOME", str(root))
    monkeypatch.setenv("DEEPGRAM_API_KEY", "fake-process-key-must-not-leak")
    for name in ("alice", "bob"):
        home = root / "profiles" / name
        home.mkdir(parents=True)
        (home / ".env").write_text(f"DEEPGRAM_API_KEY=fake-{name}-key\n")
        (home / "config.yaml").write_text("stt:\n  enabled: true\n  provider: scope-test-stt\n")
    provider = ScopedSTT()
    transcription_registry.register_provider(provider)
    monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True, stt_enabled=True, stt_echo_transcripts=False)
    adapter = SimpleNamespace(send=AsyncMock())
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._profile_adapters = {n: {Platform.TELEGRAM: adapter} for n in ("alice", "bob")}
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"fake audio")
    def event(name):
        return MessageEvent(text="", source=SessionSource(platform=Platform.TELEGRAM, chat_id=name, profile=name),
                            message_type=MessageType.VOICE, media_urls=[str(audio)], media_types=["audio/ogg"])
    yield runner, provider, event, adapter, root
    transcription_registry._reset_for_tests()


@pytest.mark.asyncio
async def test_two_pending_profiles_override_even_another_active_scope(setup):
    runner, provider, event, adapter, root = setup
    with _profile_runtime_scope(root / "profiles" / "alice"):
        results = await asyncio.gather(*[
            runner._transcribe_pending_audio_event_once(event(name)) for name in ("alice", "bob")
        ])
        assert secret_scope.get_secret("DEEPGRAM_API_KEY") == "fake-alice-key"
    assert [r[1] for r in results] == [["alice"], ["bob"]]
    assert set(provider.calls) == {(root / "profiles" / n, f"fake-{n}-key") for n in ("alice", "bob")}
    assert secret_scope.current_secret_scope() is None


@pytest.mark.asyncio
async def test_clarify_voice_has_scope_and_reuses_transcription(setup):
    runner, provider, event, adapter, root = setup
    voice = event("bob")
    assert await runner._prepare_clarify_reply_text(voice) == "bob"
    assert await runner._prepare_clarify_reply_text(voice) == "bob"
    assert provider.calls == [(root / "profiles" / "bob", "fake-bob-key")]


@pytest.mark.asyncio
async def test_failure_notifies_once_and_never_borrows_other_profile_key(setup, monkeypatch):
    from tools import transcription_tools
    runner, provider, event, adapter, root = setup
    (root / "profiles" / "bob" / ".env").write_text("")
    monkeypatch.setattr(transcription_tools, "transcribe_audio_local_fallback",
                        lambda *_: {"success": False, "error": "No installed local STT"})
    voice = event("bob")
    with _profile_runtime_scope(root / "profiles" / "alice"):
        assert await runner._prepare_clarify_reply_text(voice) == ""
        assert await runner._prepare_clarify_reply_text(voice) == ""
    assert provider.calls == []
    adapter.send.assert_awaited_once()
    assert adapter.send.call_args.args[0] == "bob"
    assert "Не удалось распознать" in adapter.send.call_args.args[1]
