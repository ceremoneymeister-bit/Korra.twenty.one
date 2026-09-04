"""Свежий контур без ключа провайдера: панель обязана сказать это словами.

На чистой раскатке (пустой каталог данных, ключ ещё не введён) первое
сообщение в чат уходило в пустоту: движок ловил отказ провайдера, складывал
готовый текст подсказки в ``final_response`` — но потоковый путь
``/v1/chat/completions`` отдавал его только через дельты, которых у такого
хода не было ни одной. Клиент получал пустой ответ с ``finish_reason: "stop"``,
то есть провал, выданный за успех.

Здесь закреплено:
  • ход без единой текстовой дельты всё равно доносит ``final_response``
    (симметрия с ``/v1/responses``, где такая подстраховка уже была);
  • отказ провайдера виден как отказ: ``finish_reason: "error"`` и причина
    в поле ``error`` финального чанка, а не молчаливый успех — причём ровно
    одним экземпляром, без дубля текстом ответа;
  • обычный ход не получает свой ответ дважды.
"""

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from unittest.mock import patch

from gateway.config import PlatformConfig
from gateway.platforms.api_server import (
    APIServerAdapter,
    cors_middleware,
    security_headers_middleware,
)

_KEY = "sk-secret-key-for-tests-32-chars"
_AUTH = {"Authorization": f"Bearer {_KEY}"}


def _create_app() -> tuple[web.Application, APIServerAdapter]:
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": _KEY}))
    mws = [mw for mw in (cors_middleware, security_headers_middleware) if mw is not None]
    app = web.Application(middlewares=mws)
    app["api_server_adapter"] = adapter
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    return app, adapter


def _chunks(body: str) -> list[dict]:
    """Все data-блоки потока, кроме завершающего [DONE]."""
    out: list[dict] = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):].strip()
        if payload == "[DONE]":
            continue
        out.append(json.loads(payload))
    return out


def _texts(body: str) -> list[str]:
    return [
        chunk["choices"][0]["delta"]["content"]
        for chunk in _chunks(body)
        if chunk.get("choices") and chunk["choices"][0].get("delta", {}).get("content")
    ]


def _finish(body: str) -> dict:
    finals = [
        chunk
        for chunk in _chunks(body)
        if chunk.get("choices") and chunk["choices"][0].get("finish_reason")
    ]
    assert finals, body
    return finals[-1]


async def _post(cli: TestClient) -> str:
    resp = await cli.post(
        "/v1/chat/completions",
        headers=_AUTH,
        json={
            "model": "test",
            "messages": [{"role": "user", "content": "Привет! Кто ты?"}],
            "stream": True,
        },
    )
    assert resp.status == 200
    return await resp.text()


#: Ровно та форма, которую отдаёт ветка отказа провайдера в ``_run_agent``.
_NO_PROVIDER_TEXT = (
    "⚠️ Не удалось обратиться к провайдеру ответа. Провайдер ответа не "
    "настроен: добавьте ключ в разделе «Ключи» или выберите провайдера и "
    "модель командой «korra model»."
)
_NO_PROVIDER_RESULT = {
    "final_response": _NO_PROVIDER_TEXT,
    "messages": [],
    "api_calls": 0,
    "tools": [],
    "completed": False,
    "failed": True,
    "error": "Провайдер ответа не настроен: добавьте ключ в разделе «Ключи».",
}


class TestNoProviderReachesTheUser:
    @pytest.mark.asyncio
    async def test_final_response_reaches_client_without_text_deltas(self):
        """Ход без дельт всё равно доносит свой текст."""
        app, adapter = _create_app()

        async def _mock_run_agent(**kwargs):
            # Ни одного вызова stream_delta_callback — так выглядит ход,
            # оборвавшийся до первого токена модели.
            return (
                {"final_response": "весь ответ пришёл разом", "messages": [], "api_calls": 0},
                {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            )

        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_run_agent", side_effect=_mock_run_agent):
                body = await _post(cli)

        assert _texts(body) == ["весь ответ пришёл разом"], body

    @pytest.mark.asyncio
    async def test_provider_failure_is_not_reported_as_success(self):
        """Отказ провайдера назван причиной, а не выдан за пустой успех."""
        app, adapter = _create_app()

        async def _mock_run_agent(**kwargs):
            return (
                dict(_NO_PROVIDER_RESULT),
                {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            )

        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_run_agent", side_effect=_mock_run_agent):
                body = await _post(cli)

        # Причина едет одним экземпляром — полем error финального чанка.
        # Панель показывает её плашкой «Корра не смогла ответить: …», и текст
        # не должен приезжать вторым экземпляром как ответ агента.
        assert _texts(body) == [], body

        finish = _finish(body)
        assert finish["choices"][0]["finish_reason"] == "error"
        assert "Ключи" in finish["error"]["message"]
        assert finish["hermes"]["failed"] is True

    @pytest.mark.asyncio
    async def test_streamed_answer_is_not_duplicated_by_final_response(self):
        """Обычный ход не получает свой ответ вторым экземпляром."""
        app, adapter = _create_app()

        async def _mock_run_agent(**kwargs):
            cb = kwargs.get("stream_delta_callback")
            if cb:
                cb("Привет! ")
                cb("Я Корра.")
            return (
                {"final_response": "Привет! Я Корра.", "messages": [], "api_calls": 1},
                {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )

        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_run_agent", side_effect=_mock_run_agent):
                body = await _post(cli)

        assert _texts(body) == ["Привет! ", "Я Корра."], body
        assert _finish(body)["choices"][0]["finish_reason"] == "stop"
