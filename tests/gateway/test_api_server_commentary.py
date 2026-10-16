"""K21-376: raw provider phases reach the actual API handler, final stays clean."""
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from agent.codex_runtime import _consume_codex_event_stream
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from run_agent import AIAgent


def provider_events(on_tool=None):
    for index, (phase, text) in enumerate([
        ("analysis", "PRIVATE_ANALYSIS"),
        ("commentary", "Проверяю файл."),
        ("commentary", "Проверяю результат."),
        ("final_answer", "Готово."),
    ]):
        item = {"id": f"m{index}", "type": "message", "role": "assistant", "phase": phase,
                "content": [{"type": "output_text", "text": text}]}
        yield {"type": "response.output_item.added", "item": item}
        yield {"type": "response.output_text.delta", "delta": text}
        yield {"type": "response.output_item.done", "item": item}
        if index == 1 and on_tool:
            on_tool()
    yield {"type": "response.completed", "response": {"status": "completed", "usage": {}}}


@pytest.mark.asyncio
async def test_raw_provider_commentary_through_chat_handler(monkeypatch):
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    class FixtureAgent(AIAgent):
        # Replace only expensive initialization/model transport; API factory,
        # worker, Codex phase parser and public callback are all real.
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def run_conversation(self, **kwargs):
            def tool():
                self.tool_start_callback("call1", "read_file", {"path": "fixture.txt"})
                self.tool_complete_callback("call1", "read_file", {}, "fixture data")
            _consume_codex_event_stream(provider_events(tool), model="fixture",
                on_text_delta=self.stream_delta_callback,
                on_commentary_message=self._fire_streamed_codex_commentary)
            return {"final_response": "Готово.", "completed": True}

    _runtime(monkeypatch, adapter, FixtureAgent)
    app = web.Application()
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/v1/chat/completions", json={"stream": True,
            "messages": [{"role": "user", "content": "Проверь"}]})
        wire = await response.text()
    frames = [json.loads(line[6:]) for line in wire.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    commentary = [p["text"] for p in frames if "text" in p]
    assert commentary == ["Проверяю файл.", "Проверяю результат."]
    visible = [p.get("text") or p.get("choices", [{}])[0].get("delta", {}).get("content") for p in frames]
    assert [text for text in visible if text] == ["Проверяю файл.", "Проверяю результат.", "Готово."]
    assert [p.get("text") or p.get("status") for p in frames if "text" in p or "status" in p] == [
        "Проверяю файл.", "running", "completed", "Проверяю результат."]
    assert "PRIVATE_ANALYSIS" not in wire
    assert "".join(p["choices"][0]["delta"].get("content", "") for p in frames if "choices" in p) == "Готово."


def test_factory_forwards_commentary(monkeypatch):
    captured = {}
    class Agent:
        def __init__(self, **kwargs):
            captured.update(kwargs)
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    _runtime(monkeypatch, adapter, Agent)
    callback = lambda text, **kw: None
    adapter._create_agent(interim_assistant_callback=callback)
    assert captured["interim_assistant_callback"] is callback
    assert not captured.get("reasoning_callback")


def _runtime(monkeypatch, adapter, agent_class):
    monkeypatch.setattr("run_agent.AIAgent", agent_class)
    monkeypatch.setattr("gateway.run._resolve_runtime_agent_kwargs", lambda: {
        "provider": "openai-codex", "base_url": "https://example.test", "api_mode": "codex_responses"})
    monkeypatch.setattr("gateway.run._resolve_gateway_model", lambda: "fixture")
    monkeypatch.setattr("gateway.run._load_gateway_config", lambda: {})
    monkeypatch.setattr("gateway.run.GatewayRunner._load_reasoning_config", staticmethod(lambda model="": {}))
    monkeypatch.setattr("gateway.run.GatewayRunner._load_fallback_model", staticmethod(lambda: None))
    monkeypatch.setattr("korra_cli.tools_config._get_platform_tools", lambda *_: set())
    monkeypatch.setattr(adapter, "_ensure_session_db", lambda: None)
