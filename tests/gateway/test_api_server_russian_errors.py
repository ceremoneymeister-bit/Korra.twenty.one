"""Real HTTP serialization of failed turns keeps Russian copy and API failure codes."""

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("truncated", [False, True])
async def test_russian_failed_turn_keeps_wire_failure_semantics(monkeypatch, stream, truncated):
    monkeypatch.setenv("KORRA_LANGUAGE", "ru")
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    diagnostic = (
        "Response truncated due to output length limit"
        if truncated else "HTTP 401: Incorrect API key provided: smoke-key\r\nProvider diagnostic"
    )
    final_text = "Уже полученная часть ответа" if truncated else diagnostic
    result = {
        "final_response": final_text,
        "error": diagnostic,
        "completed": False,
        "partial": truncated,
        "failed": not truncated,
        "messages": [],
    }

    async def run_agent(**kwargs):
        callback = kwargs.get("stream_delta_callback")
        if callback and truncated:
            callback(final_text)
        return result, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    monkeypatch.setattr(adapter, "_run_agent", run_agent)
    app = web.Application()
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/v1/chat/completions", json={
            "model": "korra-agent", "stream": stream,
            "messages": [{"role": "user", "content": "Проверка"}],
        })
        assert response.status == 200
        assert "\n" not in response.headers.get("X-Hermes-Error", "")
        assert "\r" not in response.headers.get("X-Hermes-Error", "")
        if stream:
            body = await response.text()
            chunks = [json.loads(line[6:]) for line in body.splitlines()
                      if line.startswith("data: ") and line != "data: [DONE]"]
            data = chunks[-1]
            assert data["error"]["message"] == data["hermes"]["error"]
            if truncated:
                assert any(c["choices"][0]["delta"].get("content") == final_text for c in chunks)
        else:
            data = await response.json()
            if truncated:
                assert data["choices"][0]["message"]["content"] == final_text
            else:
                assert "Подключение к модели больше не даёт доступа" in data["choices"][0]["message"]["content"]
        assert data["choices"][0]["finish_reason"] == ("length" if truncated else "error")
        assert data["hermes"]["error_code"] == ("output_truncated" if truncated else "agent_error")
        assert "Техническая причина:" not in data["hermes"]["error"]
        assert diagnostic not in data["hermes"]["error"]
        assert "Ответ не поместился" in data["hermes"]["error"] if truncated else "Подключение к модели больше не даёт доступа" in data["hermes"]["error"]
        if not stream:
            assert response.headers["X-Hermes-Error"] == " ".join(diagnostic.split())
        assert data["hermes"]["partial"] is truncated
        assert data["hermes"]["failed"] is (not truncated)
    assert result["error"] == diagnostic


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("reason,diagnostic,expected", [
    ("billing", "HTTP 429: insufficient_quota", "объём работы"),
    ("rate_limit", "HTTP 429: slow down", "временно не принимает"),
    ("overloaded", "HTTP 503", "перегружен"),
])
async def test_model_limits_cross_http_with_distinct_guidance(monkeypatch, stream, reason, diagnostic, expected):
    monkeypatch.setenv("KORRA_LANGUAGE", "ru")
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    async def run_agent(**kwargs):
        return {"final_response": diagnostic, "error": diagnostic, "failed": True,
                "completed": False, "messages": [], "failure_reason": reason}, {}
    monkeypatch.setattr(adapter, "_run_agent", run_agent)
    app = web.Application()
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/v1/chat/completions", json={"model":"korra-agent", "stream":stream,
            "messages":[{"role":"user", "content":"Проверка"}]})
        assert response.status == 200
        if stream:
            chunks = [json.loads(line[6:]) for line in (await response.text()).splitlines()
                      if line.startswith("data: ") and line != "data: [DONE]"]
            body = chunks[-1]
        else:
            body = await response.json()
        assert body["choices"][0]["finish_reason"] == "error"
        assert expected in body["error"]["message"] if stream else expected in body["hermes"]["error"]
        assert "пополн" not in json.dumps(body, ensure_ascii=False).lower()
