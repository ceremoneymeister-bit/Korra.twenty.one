"""K21-269: /learn и /refine в обычном веб-чате вызывают штатные обработчики.

Веб-чат панели ходит в ``/v1/chat/completions``: раньше текст ``/learn …`` и
``/refine …`` уходил модели как обычное сообщение. Теперь ``/learn`` строит тот
же промпт, что и мессенджеры, а ``/refine`` запускает ручной разбор без вызова
модели в основном ходе.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from agent.learn_prompt import build_learn_prompt
from gateway.config import PlatformConfig
from gateway.platforms.api_server import (
    APIServerAdapter,
    _parse_learning_slash,
    cors_middleware,
    security_headers_middleware,
)

_KEY = "sk-secret-key-for-tests-32-chars"
_AUTH = {"Authorization": f"Bearer {_KEY}"}


def _agent(response="ответ модели"):
    agent = MagicMock()
    agent.run_conversation.return_value = {"final_response": response}
    agent.session_prompt_tokens = 0
    agent.session_completion_tokens = 0
    agent.session_total_tokens = 0
    agent.valid_tool_names = {"skill_manage"}
    agent._spawn_background_review.return_value = True
    return agent


def _adapter():
    return APIServerAdapter(PlatformConfig(enabled=True, extra={"key": _KEY}))


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/learn", ("learn", "")),
        ("/learn как мы сдаём отчёт", ("learn", "как мы сдаём отчёт")),
        ("  /refine  тема\nвторая строка ", ("refine", "тема\nвторая строка")),
        ("/refine", ("refine", "")),
    ],
)
def test_parse_recognises_learning_commands(text, expected):
    assert _parse_learning_slash(text) == expected


@pytest.mark.parametrize(
    "text",
    ["привет", "/learner", "/learning", "расскажи про /learn", "/goal x", "", None,
     [{"type": "text", "text": "/learn"}]],
)
def test_parse_ignores_everything_else(text):
    assert _parse_learning_slash(text) is None


@pytest.mark.asyncio
async def test_learn_sends_the_standard_prompt_and_keeps_the_typed_text():
    adapter = _adapter()
    agent = _agent()
    history = [{"role": "user", "content": "раньше"}]
    with patch.object(adapter, "_create_agent", return_value=agent):
        result, _ = await adapter._run_agent(
            user_message="/learn наш процесс сдачи отчёта",
            conversation_history=history,
            session_id="s1",
        )
    agent.run_conversation.assert_called_once()
    kwargs = agent.run_conversation.call_args.kwargs
    assert kwargs["user_message"] == build_learn_prompt("наш процесс сдачи отчёта")
    assert kwargs["persist_user_message"] == "/learn наш процесс сдачи отчёта"
    assert kwargs["conversation_history"] == history
    assert result["final_response"] == "ответ модели"


@pytest.mark.asyncio
async def test_ordinary_text_is_untouched():
    adapter = _adapter()
    agent = _agent()
    with patch.object(adapter, "_create_agent", return_value=agent):
        await adapter._run_agent(
            user_message="расскажи про /learn", conversation_history=[], session_id="s1"
        )
    kwargs = agent.run_conversation.call_args.kwargs
    assert kwargs["user_message"] == "расскажи про /learn"
    assert "persist_user_message" not in kwargs
    agent._spawn_background_review.assert_not_called()


@pytest.mark.asyncio
async def test_refine_starts_manual_review_once_without_a_model_turn():
    adapter = _adapter()
    agent = _agent()
    history = [
        {"role": "user", "content": "вопрос"},
        {"role": "assistant", "content": "ответ"},
    ]
    with patch.object(adapter, "_create_agent", return_value=agent):
        result, _ = await adapter._run_agent(
            user_message="/refine сохрани правило про отчёты",
            conversation_history=history,
            session_id="s1",
        )
    agent.run_conversation.assert_not_called()
    agent._spawn_background_review.assert_called_once_with(
        messages_snapshot=history,
        review_memory=True,
        review_skills=True,
        focus="сохрани правило про отчёты",
        manual=True,
    )
    assert "Изучаю диалог" in result["final_response"]
    assert "сохрани правило про отчёты" in result["final_response"]
    assert not result.get("failed")


@pytest.mark.asyncio
async def test_refine_without_topic_and_without_skill_tool():
    adapter = _adapter()
    agent = _agent()
    agent.valid_tool_names = {"memory"}
    with patch.object(adapter, "_create_agent", return_value=agent):
        await adapter._run_agent(
            user_message="/refine",
            conversation_history=[{"role": "user", "content": "x"}],
            session_id="s1",
        )
    kwargs = agent._spawn_background_review.call_args.kwargs
    assert kwargs["focus"] is None
    assert kwargs["review_skills"] is False
    assert kwargs["manual"] is True


@pytest.mark.asyncio
async def test_refine_on_empty_dialog_says_there_is_nothing_to_review():
    adapter = _adapter()
    agent = _agent()
    with patch.object(adapter, "_create_agent", return_value=agent):
        result, _ = await adapter._run_agent(
            user_message="/refine", conversation_history=[], session_id="s1"
        )
    agent._spawn_background_review.assert_not_called()
    agent.run_conversation.assert_not_called()
    assert "нечего разбирать" in result["final_response"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome,expected",
    [(False, "уже выполняется"), (RuntimeError("boom"), "Не удалось запустить /refine")],
)
async def test_refine_reports_busy_and_failed_start(outcome, expected):
    adapter = _adapter()
    agent = _agent()
    if isinstance(outcome, Exception):
        agent._spawn_background_review.side_effect = outcome
    else:
        agent._spawn_background_review.return_value = outcome
    with patch.object(adapter, "_create_agent", return_value=agent):
        result, _ = await adapter._run_agent(
            user_message="/refine",
            conversation_history=[{"role": "user", "content": "x"}],
            session_id="s1",
        )
    assert expected in result["final_response"]


def _app(adapter):
    mws = [mw for mw in (cors_middleware, security_headers_middleware) if mw is not None]
    app = web.Application(middlewares=mws)
    app["api_server_adapter"] = adapter
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_refine_through_chat_completions_reaches_the_browser(stream):
    adapter = _adapter()
    agent = _agent()
    with patch.object(adapter, "_create_agent", return_value=agent):
        async with TestClient(TestServer(_app(adapter))) as cli:
            resp = await cli.post(
                "/v1/chat/completions",
                headers=_AUTH,
                json={
                    "model": "korra-agent",
                    "stream": stream,
                    "messages": [
                        {"role": "user", "content": "привет"},
                        {"role": "assistant", "content": "здравствуйте"},
                        {"role": "user", "content": "/refine"},
                    ],
                },
            )
            assert resp.status == 200
            raw = await resp.text()
    if stream:
        text = "".join(
            json.loads(line[6:])["choices"][0]["delta"].get("content", "")
            for line in raw.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
            if json.loads(line[6:]).get("choices")
        )
    else:
        text = json.loads(raw)["choices"][0]["message"]["content"]
    assert "Изучаю диалог" in text
    agent.run_conversation.assert_not_called()
    agent._spawn_background_review.assert_called_once()
    snapshot = agent._spawn_background_review.call_args.kwargs["messages_snapshot"]
    assert [m["content"] for m in snapshot] == ["привет", "здравствуйте"]
