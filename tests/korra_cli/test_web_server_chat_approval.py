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

from korra_cli import web_server


def _route_app_paths(*paths: str) -> FastAPI:
    """Псевдоним для маршрутов вне пары одобрения (тот же сбор приложения)."""
    return _route_app(*paths)


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
    app = _route_app(
        "/api/chat/approval", "/api/chat/approvals", "/api/chat/decisions"
    )
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
    assert call["headers"]["X-Korra-Attended"] == "1"


def test_board_answer_reaches_the_same_version_through_panel_proxy(monkeypatch):
    response = asyncio.run(_call(monkeypatch, "POST", "/api/chat/approval", {
        "session_id": "origin", "choice": "once", "request_id": "kb_exact_version", "answer": "До пятницы",
    }))
    assert response.status_code == 200
    assert _Recorder.calls[0]["url"].endswith("/api/sessions/origin/approval")
    assert _Recorder.calls[0]["json"] == {
        "choice": "once", "request_id": "kb_exact_version", "answer": "До пятницы",
    }


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


def test_profile_decision_center_is_not_bound_to_the_open_chat(monkeypatch):
    _Recorder.response_body = b'{"object":"list","data":[]}'

    response = asyncio.run(
        _call(monkeypatch, "GET", "/api/chat/decisions?profile=sales")
    )

    assert response.status_code == 200
    assert _Recorder.calls[0]["method"] == "GET"
    assert _Recorder.calls[0]["url"].endswith(
        "/p/sales/api/effect-decisions"
    )


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


def test_panel_marks_its_own_chat_stream_as_read_by_a_human(monkeypatch):
    """Только по этой отметке движок вешает слушателя одобрений.

    Сторонний OpenAI-совместимый клиент ходит в тот же `/v1/chat/completions`
    и тоже держит поток, но карточку не читает: вопрос в него означал бы ход,
    зависший до таймаута одобрения. Отметку ставит именно прокси панели, и
    ставит на каждый чат-запрос, а не только на потоковый.
    """
    real_client = httpx.AsyncClient
    app = _route_app_paths("/api/chat/completions")

    async def _call():
        transport = httpx.ASGITransport(app=app)
        async with real_client(
            transport=transport, base_url="http://testserver"
        ) as cli:
            monkeypatch.setattr(httpx, "AsyncClient", _Recorder)
            return await cli.post(
                "/api/chat/completions",
                json={
                    "model": "korra-agent",
                    "messages": [{"role": "user", "content": "Привет"}],
                    "stream": False,
                },
            )

    asyncio.run(_call())
    assert _Recorder.calls, "запрос до движка не дошёл"
    assert _Recorder.calls[0]["headers"]["X-Korra-Attended"] == "1"


# ---------------------------------------------------------------------------
# K21-299: решения исполнителя другого профиля — в исходном чате по связи карточки
# ---------------------------------------------------------------------------


class _ByUrl(_Recorder):
    bodies: dict = {}
    failing: set = set()

    def _respond(self, method, url, headers=None, json=None):
        response = super()._respond(method, url, headers, json)
        for needle in type(self).failing:
            if needle in str(url):
                return httpx.Response(503, content=b"{}")
        for needle, body in type(self).bodies.items():
            if needle in str(url):
                return httpx.Response(
                    200, content=json_module.dumps(body).encode(),
                    headers={"content-type": "application/json"},
                )
        return response


json_module = json


async def _call_by_url(monkeypatch, method, path, body=None):
    real_client = httpx.AsyncClient
    app = _route_app("/api/chat/approval", "/api/chat/approvals", "/api/chat/decisions")
    transport = httpx.ASGITransport(app=app)
    async with real_client(transport=transport, base_url="http://testserver") as cli:
        monkeypatch.setattr(httpx, "AsyncClient", _ByUrl)
        if method == "POST":
            return await cli.post(path, json=body)
        return await cli.get(path)


@pytest.fixture
def linked_home(tmp_path, monkeypatch):
    """Чат chat-1 передал карточку исполнителю sender; чат chat-9 ничего не передавал."""
    from korra_cli import kanban_db as kb
    from korra_state import SessionDB

    home = tmp_path / ".hermes"
    (home / "profiles" / "sender").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    kb._INITIALIZED_PATHS.clear()
    db = SessionDB(home / "state.db")
    for sid in ("chat-1", "chat-9"):
        db.create_session(sid, source="api_server")
    db.close()
    kb.init_db()
    with kb.connect_closing() as conn:
        kb.create_task(conn, title="send", assignee="sender", session_id="chat-1")
    _ByUrl.bodies, _ByUrl.failing = {}, set()
    return home


def test_effect_of_the_executor_shows_in_the_origin_chat(linked_home, monkeypatch):
    _ByUrl.bodies = {
        "/p/sender/api/sessions/chat-1/approvals": {"data": [
            {"request_id": "effect_mine"}, {"request_id": "kb_not_an_effect"},
        ]},
        "/api/sessions/chat-1/approvals": {"data": [{"request_id": "req-own"}]},
    }
    response = asyncio.run(_call_by_url(monkeypatch, "GET", "/api/chat/approvals?session_id=chat-1"))
    assert [i["request_id"] for i in response.json()["data"]] == ["req-own", "effect_mine"]


def test_chat_without_handed_over_cards_asks_only_its_own_profile(linked_home, monkeypatch):
    _ByUrl.bodies = {"/api/sessions/chat-9/approvals": {"data": [{"request_id": "req-own"}]}}
    response = asyncio.run(_call_by_url(monkeypatch, "GET", "/api/chat/approvals?session_id=chat-9"))
    assert [i["request_id"] for i in response.json()["data"]] == ["req-own"]
    assert len(_ByUrl.calls) == 1


def test_unreachable_executor_does_not_hide_own_decisions(linked_home, monkeypatch):
    _ByUrl.bodies = {"/api/sessions/chat-1/approvals": {"data": [{"request_id": "req-own"}]}}
    _ByUrl.failing = {"/p/sender/"}
    response = asyncio.run(_call_by_url(monkeypatch, "GET", "/api/chat/approvals?session_id=chat-1"))
    assert response.status_code == 200
    assert [i["request_id"] for i in response.json()["data"]] == ["req-own"]


def test_answer_goes_to_the_executor_profile_with_the_stored_session(linked_home, monkeypatch):
    from korra_state import SessionDB

    db = SessionDB(linked_home / "state.db")
    db.end_session("chat-1", "session_reset")
    db.close()
    db = SessionDB(linked_home / "state.db")
    db.create_session("chat-2", source="api_server", session_key="k", parent_session_id="chat-1",
                      model_config={"_reset_from": "chat-1"})
    db.close()
    # the reset needs a shared routing key to count as the same chat
    import sqlite3
    conn = sqlite3.connect(linked_home / "state.db")
    conn.execute("UPDATE sessions SET session_key='k' WHERE id='chat-1'")
    conn.commit(); conn.close()
    _ByUrl.bodies = {"/p/sender/api/sessions/chat-1/approvals": {"data": [{"request_id": "effect_mine"}]}}
    response = asyncio.run(_call_by_url(monkeypatch, "POST", "/api/chat/approval", {
        "session_id": "chat-2", "choice": "once", "request_id": "effect_mine"}))
    assert response.status_code == 200
    posts = [c for c in _ByUrl.calls if c["method"] == "POST"]
    assert len(posts) == 1 and posts[0]["url"].endswith("/p/sender/api/sessions/chat-1/approval")


def test_executor_center_answer_is_forwarded_unchanged(linked_home, monkeypatch):
    # The executor's own centre posts the stored origin session to its own
    # profile: no link is needed and no foreign-session check may refuse it.
    _ByUrl.bodies = {}
    response = asyncio.run(_call_by_url(monkeypatch, "POST", "/api/chat/approval?profile=sender", {
        "session_id": "chat-1", "choice": "once", "request_id": "effect_mine"}))
    assert response.status_code == 200
    assert [c["url"] for c in _ByUrl.calls if c["method"] == "POST"][0].endswith(
        "/p/sender/api/sessions/chat-1/approval")


def test_background_origin_is_not_linked_and_passes_through(linked_home, monkeypatch):
    response = asyncio.run(_call_by_url(monkeypatch, "POST", "/api/chat/approval?profile=sender", {
        "session_id": "cron:job:run1", "choice": "deny", "request_id": "effect_cron"}))
    assert response.status_code == 200
    assert _ByUrl.calls[0]["url"].endswith("/p/sender/api/sessions/cron%3Ajob%3Arun1/approval")


def test_other_chat_cannot_reach_the_executor_effect(linked_home, monkeypatch):
    _ByUrl.bodies = {"/p/sender/api/sessions/chat-1/approvals": {"data": [{"request_id": "effect_mine"}]}}
    asyncio.run(_call_by_url(monkeypatch, "POST", "/api/chat/approval", {
        "session_id": "chat-9", "choice": "once", "request_id": "effect_mine"}))
    assert all("/p/sender/" not in c["url"] for c in _ByUrl.calls)


def test_executor_down_is_reported_as_unavailable_not_as_missing(linked_home, monkeypatch):
    _ByUrl.failing = {"/p/sender/"}
    response = asyncio.run(_call_by_url(monkeypatch, "POST", "/api/chat/approval", {
        "session_id": "chat-1", "choice": "once", "request_id": "effect_mine"}))
    assert response.status_code == 503
    assert not [c for c in _ByUrl.calls if c["method"] == "POST"]
