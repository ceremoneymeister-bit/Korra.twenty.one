"""Одобрение опасных команд в потоке /v1/chat/completions.

До этих маршрутов панельный чат был единственной поверхностью агента, где
опасная команда не спрашивала, а просто вешала ход: адаптер api_server не
регистрировал слушателя одобрений, ядро возвращало инструменту
``status: pending_approval`` — и отвечать на этот запрос было нечем и некому.

Здесь закреплено ровно три вещи:
  • слушатель регистрируется под тем же ключом, который `_run_agent` биндит
    в session_context (разойдутся — карточка уедет в чужую очередь);
  • запрос доезжает до клиента отдельным SSE-событием, а не текстом ответа;
  • решение исполняет то же ядро одобрений, что и мессенджеры, а на
    опоздавшее решение маршрут отвечает честным 409, а не молчаливым 200.
"""

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from unittest.mock import patch

from gateway.config import PlatformConfig
from gateway.platforms.api_server import (
    APIServerAdapter,
    CHAT_APPROVAL_SSE_EVENT,
    _chat_approval_event,
    cors_middleware,
    security_headers_middleware,
)
from tools import approval as approval_mod


def _make_adapter(api_key: str = "") -> APIServerAdapter:
    extra = {"key": api_key} if api_key else {}
    return APIServerAdapter(PlatformConfig(enabled=True, extra=extra))


def _create_app(adapter: APIServerAdapter) -> web.Application:
    mws = [mw for mw in (cors_middleware, security_headers_middleware) if mw is not None]
    app = web.Application(middlewares=mws)
    app["api_server_adapter"] = adapter
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    app.router.add_get(
        "/api/sessions/{session_id}/approvals", adapter._handle_session_approvals
    )
    app.router.add_post(
        "/api/sessions/{session_id}/approval", adapter._handle_session_approval
    )
    return app


@pytest.fixture
def adapter():
    return _make_adapter()


@pytest.fixture
def auth_adapter():
    return _make_adapter(api_key="sk-secret-key-for-tests-32-chars")


@pytest.fixture(autouse=True)
def _clean_approval_queues():
    """Очереди одобрений — процессный синглтон; между тестами их не делим."""
    yield
    with approval_mod._lock:
        approval_mod._gateway_queues.clear()
        approval_mod._gateway_notify_cbs.clear()


def _sse_payloads(body: str, event_name: str) -> list[dict]:
    """Вынуть данные всех блоков с заданным именем события."""
    payloads: list[dict] = []
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != f"event: {event_name}":
            continue
        for follow in lines[index + 1: index + 4]:
            if follow.startswith("data: "):
                payloads.append(json.loads(follow[len("data: "):]))
                break
    return payloads


class TestApprovalEventPayload:
    def test_choices_follow_backend_capabilities(self):
        event = _chat_approval_event(
            {
                "request_id": "req-1",
                "command": "rm -rf /opt/data",
                "description": "Рекурсивное удаление",
                "allow_session": True,
                "allow_permanent": False,
            },
            session_id="s-1",
        )
        # «Навсегда» не предлагается, когда ядро не даёт этот scope: иначе
        # карточка показала бы кнопку, ответ которой сервер отвергнет.
        assert event["choices"] == ["once", "session", "deny"]
        assert event["session_id"] == "s-1"
        assert event["event"] == "approval.request"

    def test_smart_denied_offers_one_shot_only(self):
        event = _chat_approval_event(
            {"request_id": "req-2", "command": "ls", "smart_denied": True},
            session_id="s-1",
        )
        assert event["choices"] == ["once", "deny"]

    def test_command_is_redacted_before_it_reaches_the_browser(self):
        event = _chat_approval_event(
            {
                "request_id": "req-3",
                "command": "curl -H 'Authorization: Bearer sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH' https://x",
                "description": "Запрос с токеном",
            },
            session_id="s-1",
        )
        # Карточку в браузере скриншотят так же, как сообщение в мессенджере.
        assert "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH" not in event["command"]


#: Продолжение сессии по X-Hermes-Session-Id движок пускает только с ключом —
#: без него он не может отличить владельца от любого, кто угадал идентификатор.
_AUTH = {"Authorization": "Bearer sk-secret-key-for-tests-32-chars"}


class TestChatStreamApproval:
    @pytest.mark.asyncio
    async def test_stream_emits_approval_request_and_registers_listener(
        self, auth_adapter
    ):
        """Запрос одобрения доезжает до клиента отдельным событием."""
        adapter = auth_adapter
        app = _create_app(adapter)
        seen: dict = {}

        async def _mock_run_agent(**kwargs):
            session_key = kwargs.get("session_id") or ""
            # Слушатель обязан быть зарегистрирован до старта хода и ровно под
            # тем ключом, который агент увидит как approval session key.
            notify = approval_mod._gateway_notify_cbs.get(session_key)
            seen["registered"] = notify is not None
            seen["listener_session"] = adapter._chat_approval_sessions.get(session_key)
            if notify is not None:
                notify({
                    "request_id": "req-live",
                    "command": "rm -rf /opt/data/tmp",
                    "description": "Рекурсивное удаление каталога",
                    "pattern_keys": ["rm_rf"],
                    "allow_session": True,
                    "allow_permanent": True,
                })
            return (
                {"final_response": "готово", "messages": [], "api_calls": 1},
                {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )

        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_run_agent", side_effect=_mock_run_agent):
                resp = await cli.post(
                    "/v1/chat/completions",
                    headers={**_AUTH, "X-Hermes-Session-Id": "s-live"},
                    json={
                        "model": "test",
                        "messages": [{"role": "user", "content": "почисти"}],
                        "stream": True,
                    },
                )
                assert resp.status == 200
                body = await resp.text()

        assert seen["registered"] is True
        assert seen["listener_session"] == "s-live"

        events = _sse_payloads(body, CHAT_APPROVAL_SSE_EVENT)
        assert len(events) == 1, body
        assert events[0]["request_id"] == "req-live"
        assert events[0]["command"] == "rm -rf /opt/data/tmp"
        assert events[0]["choices"] == ["once", "session", "always", "deny"]

        # Ход кончился — слушателя и записи о сессии быть не должно, иначе
        # решение по мёртвому ходу приняли бы за живое.
        assert "s-live" not in adapter._chat_approval_sessions
        assert "s-live" not in approval_mod._gateway_notify_cbs

    @pytest.mark.asyncio
    async def test_stream_without_approval_emits_no_such_event(self, auth_adapter):
        """Обычный ход не сорит новым событием в поток чужих клиентов."""
        adapter = auth_adapter
        app = _create_app(adapter)

        async def _mock_run_agent(**kwargs):
            cb = kwargs.get("stream_delta_callback")
            if cb:
                cb("готово")
            return (
                {"final_response": "готово", "messages": [], "api_calls": 1},
                {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )

        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_run_agent", side_effect=_mock_run_agent):
                resp = await cli.post(
                    "/v1/chat/completions",
                    headers={**_AUTH, "X-Hermes-Session-Id": "s-quiet"},
                    json={
                        "model": "test",
                        "messages": [{"role": "user", "content": "привет"}],
                        "stream": True,
                    },
                )
                body = await resp.text()

        assert _sse_payloads(body, CHAT_APPROVAL_SSE_EVENT) == []


class TestSessionApprovalEndpoints:
    @pytest.mark.asyncio
    async def test_decision_resolves_the_queued_request(self, adapter):
        app = _create_app(adapter)
        entry = approval_mod._ApprovalEntry({
            "request_id": "req-1",
            "command": "rm -rf /opt/data/tmp",
            "description": "Рекурсивное удаление",
        })
        with approval_mod._lock:
            approval_mod._gateway_queues["s-1"] = [entry]
        adapter._chat_approval_sessions["s-1"] = "s-1"

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/api/sessions/s-1/approval",
                json={"choice": "always", "request_id": "req-1"},
            )
            assert resp.status == 200
            data = await resp.json()

        assert data["resolved"] == 1
        assert data["choice"] == "always"
        # Ждавший поток агента разбужен именно этим решением.
        assert entry.result == "always"
        assert entry.event.is_set()

    @pytest.mark.asyncio
    async def test_approve_alias_maps_to_single_use(self, adapter):
        app = _create_app(adapter)
        entry = approval_mod._ApprovalEntry({"request_id": "req-1", "command": "ls"})
        with approval_mod._lock:
            approval_mod._gateway_queues["s-1"] = [entry]
        adapter._chat_approval_sessions["s-1"] = "s-1"

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/api/sessions/s-1/approval", json={"choice": "approve"})
            assert resp.status == 200
        assert entry.result == "once"

    @pytest.mark.asyncio
    async def test_unknown_choice_is_rejected(self, adapter):
        app = _create_app(adapter)
        adapter._chat_approval_sessions["s-1"] = "s-1"
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/api/sessions/s-1/approval", json={"choice": "maybe"})
            assert resp.status == 400
            assert (await resp.json())["error"]["code"] == "invalid_approval_choice"

    @pytest.mark.asyncio
    async def test_decision_for_a_finished_turn_is_409(self, adapter):
        """Ход кончился — решение уже никуда не пойдёт, и об этом надо сказать."""
        app = _create_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/api/sessions/s-gone/approval", json={"choice": "once"})
            assert resp.status == 409
            assert (await resp.json())["error"]["code"] == "approval_not_active"

    @pytest.mark.asyncio
    async def test_decision_without_pending_request_is_409(self, adapter):
        app = _create_app(adapter)
        adapter._chat_approval_sessions["s-1"] = "s-1"
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/api/sessions/s-1/approval", json={"choice": "once"})
            assert resp.status == 409
            assert (await resp.json())["error"]["code"] == "approval_not_pending"

    @pytest.mark.asyncio
    async def test_decision_is_scoped_to_its_own_session(self, adapter):
        """Решение одного чата не разблокирует команду другого."""
        app = _create_app(adapter)
        mine = approval_mod._ApprovalEntry({"request_id": "req-mine", "command": "ls"})
        theirs = approval_mod._ApprovalEntry({"request_id": "req-theirs", "command": "rm -rf /"})
        with approval_mod._lock:
            approval_mod._gateway_queues["s-mine"] = [mine]
            approval_mod._gateway_queues["s-theirs"] = [theirs]
        adapter._chat_approval_sessions["s-mine"] = "s-mine"
        adapter._chat_approval_sessions["s-theirs"] = "s-theirs"

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/api/sessions/s-mine/approval", json={"choice": "once"})
            assert resp.status == 200

        assert mine.result == "once"
        assert theirs.result is None
        assert not theirs.event.is_set()

    @pytest.mark.asyncio
    async def test_pending_list_restores_the_card_after_reload(self, adapter):
        app = _create_app(adapter)
        entry = approval_mod._ApprovalEntry({
            "request_id": "req-1",
            "command": "shutdown -h now",
            "description": "Выключение машины",
            "allow_session": True,
            "allow_permanent": False,
        })
        with approval_mod._lock:
            approval_mod._gateway_queues["s-1"] = [entry]
        adapter._chat_approval_sessions["s-1"] = "s-1"

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/api/sessions/s-1/approvals")
            assert resp.status == 200
            body = await resp.json()

        assert len(body["data"]) == 1
        assert body["data"][0]["request_id"] == "req-1"
        assert body["data"][0]["command"] == "shutdown -h now"
        assert body["data"][0]["choices"] == ["once", "session", "deny"]

    @pytest.mark.asyncio
    async def test_pending_list_for_an_idle_session_is_empty_not_404(self, adapter):
        """Чат панели создаёт запись сессии лениво: 404 читался бы как поломка."""
        app = _create_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/api/sessions/s-never-existed/approvals")
            assert resp.status == 200
            assert (await resp.json())["data"] == []

    @pytest.mark.asyncio
    async def test_endpoints_require_the_api_key(self, auth_adapter):
        app = _create_app(auth_adapter)
        auth_adapter._chat_approval_sessions["s-1"] = "s-1"
        async with TestClient(TestServer(app)) as cli:
            unauth = await cli.post("/api/sessions/s-1/approval", json={"choice": "once"})
            assert unauth.status == 401
            listing = await cli.get("/api/sessions/s-1/approvals")
            assert listing.status == 401

            ok = await cli.get(
                "/api/sessions/s-1/approvals",
                headers={"Authorization": "Bearer sk-secret-key-for-tests-32-chars"},
            )
            assert ok.status == 200
