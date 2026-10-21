"""Служебный ход веб-панели не идёт параллельно с ходом человека.

Случай со стенда: человек поручил работу дочернему агенту, его ход ещё
думал, а уведомление «[ASYNC DELEGATION BATCH COMPLETE …]» пришло self-post'ом
в ту же сессию и запустило второй ход рядом. Оба хода дописали финал — владелец
получил два почти одинаковых ответа. Здесь настоящий HTTP-путь
``deliver_wake`` → ``/v1/chat/completions``; подменён только агент.
"""

import asyncio
import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from gateway.wake import deliver_wake

WAKE_TEXT = "[ASYNC DELEGATION BATCH COMPLETE — deleg_test] готово"
HUMAN_TEXT = "Поручи помощнику проверку"
AUTH = {"Authorization": "Bearer test-api-key", "X-Hermes-Session-Id": "parent"}


class _Recorder:
    """Агент-заглушка: считает одновременные ходы и пишет финал в историю."""

    def __init__(self, db, durations):
        self.db = db
        self.durations = durations
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls = []

    def run_conversation(self, *args, **kwargs):
        user_message = kwargs.get("user_message")
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append((user_message, list(kwargs.get("conversation_history") or [])))
        try:
            time.sleep(self.durations.get(user_message, 0.0))
            final = f"ответ на: {user_message[:20]}"
            self.db.append_message("parent", "user", user_message)
            self.db.append_message("parent", "assistant", final)
            return {"final_response": final}
        finally:
            with self.lock:
                self.active -= 1

    def agent(self):
        agent = MagicMock()
        agent.run_conversation.side_effect = self.run_conversation
        agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
        return agent


def _answered(body, text="ответ на: Поручи"):
    return text in body or json.dumps(text)[1:-1] in body


def _server(tmp_path, monkeypatch):
    from aiohttp import web
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "test-api-key"}))
    app = web.Application()
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    return adapter, app


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
async def test_wake_waits_for_running_human_turn_and_sees_its_answer(tmp_path, monkeypatch, stream):
    from aiohttp.test_utils import TestClient, TestServer
    from korra_state import SessionDB

    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("parent", source="dashboard")
        rec = _Recorder(db, {HUMAN_TEXT: 0.6, WAKE_TEXT: 0.05})
        adapter, app = _server(tmp_path, monkeypatch)
        adapter._session_db = db
        with patch.object(adapter, "_create_agent", side_effect=lambda *a, **k: rec.agent()):
            async with TestClient(TestServer(app)) as client:
                adapter._host, adapter._port = "127.0.0.1", client.server.port

                async def human():
                    resp = await client.post("/v1/chat/completions", headers=AUTH,
                        json={"messages": [{"role": "user", "content": HUMAN_TEXT}], "stream": stream})
                    body = await resp.text()
                    return resp.status, body

                human_task = asyncio.create_task(human())
                for _ in range(100):
                    if rec.active:
                        break
                    await asyncio.sleep(0.01)
                assert rec.active == 1, "ход человека не начался"
                await deliver_wake(adapter, text=WAKE_TEXT, session_id="parent", silence_allowed=False)
                status, body = await human_task

        assert status == 200 and _answered(body)
        assert rec.max_active == 1, "служебный ход шёл параллельно с ходом человека"
        assert [c[0] for c in rec.calls] == [HUMAN_TEXT, WAKE_TEXT]
        wake_history = [(r["role"], r["content"]) for r in rec.calls[1][1]]
        assert ("assistant", f"ответ на: {HUMAN_TEXT[:20]}") in wake_history


@pytest.mark.asyncio
async def test_human_turn_waits_for_running_wake_and_gets_answer(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from korra_state import SessionDB

    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("parent", source="dashboard")
        rec = _Recorder(db, {HUMAN_TEXT: 0.05, WAKE_TEXT: 0.5})
        adapter, app = _server(tmp_path, monkeypatch)
        adapter._session_db = db
        with patch.object(adapter, "_create_agent", side_effect=lambda *a, **k: rec.agent()):
            async with TestClient(TestServer(app)) as client:
                adapter._host, adapter._port = "127.0.0.1", client.server.port
                wake_task = asyncio.create_task(deliver_wake(adapter, text=WAKE_TEXT, session_id="parent"))
                for _ in range(100):
                    if rec.active:
                        break
                    await asyncio.sleep(0.01)
                resp = await client.post("/v1/chat/completions", headers=AUTH,
                    json={"messages": [{"role": "user", "content": HUMAN_TEXT}], "stream": True})
                body = await resp.text()
                await wake_task

        assert resp.status == 200 and _answered(body)
        assert rec.max_active == 1
        assert [c[0] for c in rec.calls] == [WAKE_TEXT, HUMAN_TEXT]


@pytest.mark.asyncio
async def test_wake_retry_after_client_timeout_does_not_start_second_turn(tmp_path, monkeypatch):
    """Таймаут self-post'а не означает, что ход не идёт: повтор — тот же ход."""
    from aiohttp.test_utils import TestClient, TestServer
    from korra_state import SessionDB
    import gateway.wake as wake

    monkeypatch.setattr(wake, "WAKE_TURN_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(wake, "_RETRY_DELAYS_SECONDS", (0.05, 0.05, 0.05))
    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("parent", source="dashboard")
        rec = _Recorder(db, {WAKE_TEXT: 0.7})
        adapter, app = _server(tmp_path, monkeypatch)
        adapter._session_db = db
        with patch.object(adapter, "_create_agent", side_effect=lambda *a, **k: rec.agent()):
            async with TestClient(TestServer(app)) as client:
                adapter._host, adapter._port = "127.0.0.1", client.server.port
                await deliver_wake(adapter, text=WAKE_TEXT, session_id="parent")
                await asyncio.sleep(0.2)

        assert [c[0] for c in rec.calls] == [WAKE_TEXT], "повтор доставки запустил второй ход"


@pytest.mark.asyncio
async def test_other_sessions_are_not_serialised(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from korra_state import SessionDB

    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("parent", source="dashboard")
        db.create_session("other", source="dashboard")
        rec = _Recorder(db, {HUMAN_TEXT: 0.5, WAKE_TEXT: 0.5})
        adapter, app = _server(tmp_path, monkeypatch)
        adapter._session_db = db
        with patch.object(adapter, "_create_agent", side_effect=lambda *a, **k: rec.agent()):
            async with TestClient(TestServer(app)) as client:
                adapter._host, adapter._port = "127.0.0.1", client.server.port
                human = asyncio.create_task(client.post("/v1/chat/completions",
                    headers={**AUTH, "X-Hermes-Session-Id": "other"},
                    json={"messages": [{"role": "user", "content": HUMAN_TEXT}]}))
                await asyncio.sleep(0.1)
                await deliver_wake(adapter, text=WAKE_TEXT, session_id="parent")
                resp = await human
                assert resp.status == 200
        assert rec.max_active == 2


def test_gateway_wake_key_is_stable_per_producer_event():
    from gateway.run import _wake_idempotency_key

    evt = {"type": "async_delegation", "delegation_id": "deleg_test"}
    assert _wake_idempotency_key(evt) == _wake_idempotency_key(dict(evt)) == "async_delegation:deleg_test"
    assert _wake_idempotency_key({"type": "completion", "session_id": "proc_1", "started_at": 5}) == "completion:proc_1:5"
    assert _wake_idempotency_key({"type": "watch_match", "session_id": "proc_1"}) == ""
