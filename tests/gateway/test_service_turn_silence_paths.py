"""Служебный ход о фоновой работе одинаков на всех путях: модели сообщается про NO_REPLY,
шлюз его глушит; новое сообщение человека всегда получает ответ.

Замер на стенде 20.10: завершение дочернего агента давало второе сообщение (2 из 2), а
веб-панель NO_REPLY не глушила вовсе.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.response_filters import SERVICE_TURN_SILENCE_HINT, with_service_turn_silence_hint
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from tools.process_registry import format_process_notification


def _delegation_evt(**extra):
    evt = {
        "type": "async_delegation",
        "goal": "проверить отчёт",
        "status": "completed",
        "summary": "Отчёт проверен, ошибок нет",
        "session_key": "agent:main:telegram:dm:123",
    }
    evt.update(extra)
    return evt


def _runner(monkeypatch, tmp_path):
    import gateway.run as gateway_run

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    runner = GatewayRunner(GatewayConfig())
    runner.adapters[Platform.TELEGRAM] = SimpleNamespace(send=AsyncMock(), handle_message=AsyncMock())
    runner.session_store._entries["agent:main:telegram:dm:123"] = SimpleNamespace(
        origin=SessionSource(platform=Platform.TELEGRAM, chat_id="123", chat_type="dm", user_id="123")
    )
    return runner


def test_hint_is_idempotent_and_skips_commands():
    once = with_service_turn_silence_hint("[IMPORTANT: done]")
    assert once.endswith(SERVICE_TURN_SILENCE_HINT)
    assert with_service_turn_silence_hint(once) == once
    assert with_service_turn_silence_hint("/loop tick") == "/loop tick"
    assert with_service_turn_silence_hint("") == ""


@pytest.mark.asyncio
async def test_messenger_child_agent_completion_carries_hint(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path)
    evt = _delegation_evt()
    text = format_process_notification(evt)

    assert await runner._deliver_completion_notification(text, evt) is True

    event = runner.adapters[Platform.TELEGRAM].handle_message.await_args.args[0]
    assert event.internal is True
    assert event.text == with_service_turn_silence_hint(text)
    assert event.text.count(SERVICE_TURN_SILENCE_HINT) == 1


@pytest.mark.asyncio
async def test_web_child_agent_completion_self_post_carries_hint(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path)
    runner.adapters[Platform.API_SERVER] = SimpleNamespace(
        supports_async_delivery=False, handle_message=AsyncMock(),
        _host="127.0.0.1", _port=8642, _api_key="k", _model_name="m",
    )
    posts = []

    async def fake_self_post(adapter, *, text, session_id, idempotency_key=""):
        posts.append((text, session_id))

    import gateway.wake as wake_mod

    monkeypatch.setattr(wake_mod, "_self_post_chat_completion", fake_self_post)
    evt = _delegation_evt(session_key="", origin_session_id="web-sid")
    text = format_process_notification(evt)

    assert await runner._deliver_completion_notification(text, evt) is True
    assert posts == [(with_service_turn_silence_hint(text), "web-sid")]


@pytest.mark.parametrize("evt, allowed", [
    ({"type": "completion", "exit_code": 0}, True),
    ({"type": "completion", "exit_code": 0, "completion_reason": "exited"}, True),
    ({"type": "completion", "exit_code": 1}, False),
    ({"type": "completion", "exit_code": -15, "completion_reason": "killed"}, False),
    ({"type": "completion", "exit_code": "?", "completion_reason": "failed_start"}, False),
    ({"type": "completion", "exit_code": 0, "completion_reason": "lost"}, False),
    ({"type": "watch_match", "pattern": "READY"}, False),
    ({"type": "async_delegation", "status": "completed", "summary": "ok"}, True),
    ({"type": "async_delegation", "status": "failed", "error": "boom"}, False),
    ({"type": "async_delegation", "status": "completed", "exit_reason": "max_iterations"}, False),
    ({"type": "async_delegation", "is_batch": True, "results": [{"status": "completed"}, {"status": "error"}]}, False),
    ({"type": "async_delegation", "is_batch": True, "results": [{"status": "completed"}, {"status": "success"}]}, True),
])
def test_hint_only_for_successful_work(evt, allowed):
    from gateway.response_filters import is_successful_background_completion

    assert is_successful_background_completion(evt) is allowed


@pytest.mark.asyncio
async def test_messenger_failed_child_agent_and_process_get_no_hint(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path)
    adapter = runner.adapters[Platform.TELEGRAM]
    failed = _delegation_evt(status="failed", error="model not found", summary=None)
    await runner._deliver_completion_notification(format_process_notification(failed), failed)
    crashed = {"type": "completion", "session_id": "p1", "command": "make", "exit_code": 2,
               "output": "Error", "session_key": "agent:main:telegram:dm:123"}
    await runner._inject_watch_notification(format_process_notification(crashed), crashed)
    texts = [call.args[0].text for call in adapter.handle_message.await_args_list]
    assert len(texts) == 2
    assert all(SERVICE_TURN_SILENCE_HINT not in t for t in texts)


@pytest.mark.asyncio
async def test_kanban_style_wake_hint_only_when_allowed():
    from gateway.wake import deliver_wake

    adapter = SimpleNamespace(handle_message=AsyncMock())
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm")
    await deliver_wake(adapter, text="Задача заблокирована", source=source)
    await deliver_wake(adapter, text="Задача выполнена", source=source, silence_allowed=True)
    first, second = (c.args[0].text for c in adapter.handle_message.await_args_list)
    assert first == "Задача заблокирована"
    assert second == with_service_turn_silence_hint("Задача выполнена")


def test_hide_latest_service_turn_rows_stops_at_human_message(tmp_path):
    from agent.conversation_loop import _CODEX_INCOMPLETE_NUDGE_SERVICE
    from korra_state import SessionDB

    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("s", source="dashboard")
        db.append_message("s", "user", "Запусти проверку")
        db.append_message("s", "assistant", "NO_REPLY")  # ответ человеку: не трогаем
        db.append_message("s", "user", "[IMPORTANT: done]", display_kind="internal_notification")
        db.append_message("s", "assistant", "")
        db.append_message("s", "user", _CODEX_INCOMPLETE_NUDGE_SERVICE)
        db.append_message("s", "assistant", "NO_REPLY")
        db.append_message("s", "user", "А что с отчётом?")
        db.append_message("s", "assistant", "NO_REPLY")  # после человека: не трогаем

        assert db.hide_latest_service_turn_rows(
            "s", assistant_contents=("NO_REPLY",), user_contents=(_CODEX_INCOMPLETE_NUDGE_SERVICE,),
        ) == 2
        kinds = [(m["role"], m["content"][:12], m.get("display_kind")) for m in db.get_messages("s")]
    assert kinds == [
        ("user", "Запусти пров", None),
        ("assistant", "NO_REPLY", None),
        ("user", "[IMPORTANT: ", "internal_notification"),
        ("assistant", "", None),
        ("user", _CODEX_INCOMPLETE_NUDGE_SERVICE[:12], "hidden"),
        ("assistant", "NO_REPLY", "hidden"),
        ("user", "А что с отчё", None),
        ("assistant", "NO_REPLY", None),
    ]


async def _web_turn(tmp_path, monkeypatch, *, internal, reply):
    """Настоящий HTTP -> APIServerAdapter -> AIAgent -> SQLite; подменён только вызов модели."""
    from unittest.mock import patch

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from openai.types.chat import ChatCompletion

    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    from gateway.wake import deliver_wake
    from korra_state import SessionDB
    from run_agent import AIAgent

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = SessionDB(tmp_path / "state.db")
    db.create_session("parent", source="dashboard")
    db.append_message("parent", "user", "Поручи проверку")
    db.append_message("parent", "assistant", "Проверка завершена, ошибок нет")
    agent = AIAgent(api_key="test-key", base_url="http://127.0.0.1:1/v1", model="gpt-4o-mini",
                    provider="openai", quiet_mode=True, skip_context_files=True, skip_memory=True,
                    skip_background_review=True, session_db=db, session_id="parent", enabled_toolsets=[],
                    platform="api_server")
    agent._session_db_created = True
    agent._cached_system_prompt = "SYSTEM"
    agent._skip_mcp_refresh = True
    agent._disable_streaming = True
    seen = []

    def model_call(kwargs):
        seen.append(kwargs["messages"][-1]["content"])
        return ChatCompletion(id="t", object="chat.completion", created=1, model="gpt-4o-mini",
            choices=[{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
            usage={"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13})

    monkeypatch.setattr(agent, "_interruptible_api_call", model_call)
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "test-api-key"}))
    adapter._session_db = db
    app = web.Application()
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    body = None
    with patch.object(adapter, "_create_agent", return_value=agent):
        async with TestClient(TestServer(app)) as client:
            adapter._host, adapter._port = "127.0.0.1", client.server.port
            if internal:
                await deliver_wake(adapter, text="[ASYNC DELEGATION COMPLETE — d1] готово",
                                   session_id="parent", silence_allowed=True)
            else:
                response = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": "Bearer test-api-key", "X-Hermes-Session-Id": "parent"},
                    json={"messages": [{"role": "user", "content": "Что с проверкой?"}], "stream": False},
                )
                assert response.status == 200, await response.text()
                body = await response.json()
    rows = db.get_messages("parent")
    db.close()
    return seen, rows, body


@pytest.mark.asyncio
async def test_web_service_turn_no_reply_is_hidden(tmp_path, monkeypatch):
    seen, rows, _ = await _web_turn(tmp_path, monkeypatch, internal=True, reply="NO_REPLY")
    assert SERVICE_TURN_SILENCE_HINT in seen[0]
    assert rows[-2]["display_kind"] == "internal_notification"
    assert rows[-1]["role"] == "assistant" and rows[-1]["content"] == "NO_REPLY"
    assert rows[-1]["display_kind"] == "hidden"


@pytest.mark.asyncio
async def test_web_service_turn_with_news_is_shown(tmp_path, monkeypatch):
    _, rows, _ = await _web_turn(tmp_path, monkeypatch, internal=True, reply="Помощник нашёл ошибку в разделе 3")
    assert rows[-1]["content"] == "Помощник нашёл ошибку в разделе 3"
    assert rows[-1].get("display_kind") is None


@pytest.mark.asyncio
async def test_web_human_turn_is_never_silenced(tmp_path, monkeypatch):
    seen, rows, body = await _web_turn(tmp_path, monkeypatch, internal=False, reply="NO_REPLY")
    assert SERVICE_TURN_SILENCE_HINT not in seen[0]
    assert rows[-2]["role"] == "user" and rows[-2].get("display_kind") is None
    assert rows[-1].get("display_kind") is None
    # Ответ человеку остаётся в ответе запроса, как и раньше.
    assert body["choices"][0]["message"]["content"] == "NO_REPLY"
