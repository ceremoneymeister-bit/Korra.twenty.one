"""Панельные маршруты решения по опасной команде.

Панель и движок — разные процессы: очередь одобрений живёт в шлюзе, а браузер
разговаривает только с панелью. Эти два маршрута — единственный мост между
ними, поэтому здесь закреплено, куда именно уходит решение (адрес, профиль,
ключ) и что панель не пропускает наверх заведомо неисполнимое.
"""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from hermes_cli import web_server


def _route_app(*paths: str) -> FastAPI:
    """Собрать приложение ровно из проверяемых маршрутов панели.

    TestClient самой Starlette здесь не годится: тест подменяет
    ``httpx.AsyncClient``, чтобы перехватить исходящий запрос к движку, а
    TestClient ходит тем же классом — и подменил бы сам себя.
    """
    app = FastAPI()
    found = [
        route
        for route in web_server.app.router.routes
        if getattr(route, "path", None) in paths
    ]
    assert {getattr(route, "path", None) for route in found} == set(paths), (
        "панель не отдаёт маршруты одобрения — решение человека некуда отправить"
    )
    app.router.routes.extend(found)
    return app


class _Recorder:
    """Подменённый httpx-клиент: запоминает запрос и отвечает заготовкой."""

    calls: list[dict] = []
    response_status = 200
    response_body = b'{"resolved": 1}'

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _respond(self, method, url, headers=None, json=None):
        type(self).calls.append({
            "method": method,
            "url": str(url),
            "headers": dict(headers or {}),
            "json": json,
        })
        return httpx.Response(
            type(self).response_status,
            content=type(self).response_body,
            headers={"content-type": "application/json"},
        )

    async def post(self, url, json=None, headers=None):
        return self._respond("POST", url, headers, json)

    async def get(self, url, headers=None):
        return self._respond("GET", url, headers, None)


async def _call(monkeypatch, method: str, path: str, body=None):
    real_client = httpx.AsyncClient
    app = _route_app("/api/chat/approval", "/api/chat/approvals")
    transport = httpx.ASGITransport(app=app)
    async with real_client(transport=transport, base_url="http://testserver") as cli:
        monkeypatch.setattr(httpx, "AsyncClient", _Recorder)
        if method == "POST":
            return await cli.post(path, json=body)
        return await cli.get(path)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("API_SERVER_KEY", "sk-panel-key-for-tests-0123456789")
    _Recorder.calls = []
    _Recorder.response_status = 200
    _Recorder.response_body = b'{"resolved": 1}'
    yield


def test_decision_goes_to_the_engine_with_the_server_key(monkeypatch):
    response = asyncio.run(
        _call(
            monkeypatch,
            "POST",
            "/api/chat/approval",
            {"session_id": "s-1", "choice": "always", "request_id": "req-1"},
        )
    )

    assert response.status_code == 200
    assert len(_Recorder.calls) == 1
    call = _Recorder.calls[0]
    assert call["url"].endswith("/api/sessions/s-1/approval")
    assert call["json"] == {"choice": "always", "request_id": "req-1"}
    # Движок пускает к решению только по ключу сервера; браузерный токен
    # панели наверх не годится.
    assert call["headers"]["Authorization"] == "Bearer sk-panel-key-for-tests-0123456789"


def test_decision_for_a_profile_keeps_the_profile_prefix(monkeypatch):
    asyncio.run(
        _call(
            monkeypatch,
            "POST",
            "/api/chat/approval?profile=buhgalter",
            {"session_id": "s-1", "choice": "once", "request_id": "req-1"},
        )
    )
    # Без префикса решение ушло бы агенту другого профиля.
    assert _Recorder.calls[0]["url"].endswith(
        "/p/buhgalter/api/sessions/s-1/approval"
    )


def test_unknown_choice_never_reaches_the_engine(monkeypatch):
    response = asyncio.run(
        _call(
            monkeypatch,
            "POST",
            "/api/chat/approval",
            {"session_id": "s-1", "choice": "может быть", "request_id": "req-1"},
        )
    )
    assert response.status_code == 400
    assert _Recorder.calls == []


def test_broken_session_id_never_reaches_the_engine(monkeypatch):
    response = asyncio.run(
        _call(
            monkeypatch,
            "POST",
            "/api/chat/approval",
            {"session_id": "../../etc", "choice": "once", "request_id": "req-1"},
        )
    )
    assert response.status_code == 400
    assert _Recorder.calls == []


def test_engine_conflict_reaches_the_browser_verbatim(monkeypatch):
    """409 «отвечать уже некому» обязан доехать как 409, а не как успех."""
    _Recorder.response_status = 409
    _Recorder.response_body = json.dumps(
        {"error": {"message": "no pending approval", "code": "approval_not_pending"}}
    ).encode()

    response = asyncio.run(
        _call(
            monkeypatch,
            "POST",
            "/api/chat/approval",
            {"session_id": "s-1", "choice": "once", "request_id": "req-1"},
        )
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "approval_not_pending"


def test_pending_list_is_proxied_for_the_reload_case(monkeypatch):
    _Recorder.response_body = json.dumps({
        "object": "list",
        "session_id": "s-1",
        "data": [{"request_id": "req-1", "command": "shutdown -h now"}],
    }).encode()

    response = asyncio.run(
        _call(monkeypatch, "GET", "/api/chat/approvals?session_id=s-1")
    )

    assert response.status_code == 200
    assert response.json()["data"][0]["request_id"] == "req-1"
    assert _Recorder.calls[0]["method"] == "GET"
    assert _Recorder.calls[0]["url"].endswith("/api/sessions/s-1/approvals")


def test_missing_server_key_is_a_server_error_not_a_silent_success(monkeypatch):
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    response = asyncio.run(
        _call(
            monkeypatch,
            "POST",
            "/api/chat/approval",
            {"session_id": "s-1", "choice": "once", "request_id": "req-1"},
        )
    )
    assert response.status_code == 500
    assert _Recorder.calls == []
