"""Русские формулировки терминальных ошибок провайдера."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent import conversation_loop
from run_agent import AIAgent


def _make_failing_agent(
    *,
    provider: str = "openrouter",
    base_url: str = "https://openrouter.ai/api/v1",
    model: str = "anthropic/claude-sonnet",
) -> AIAgent:
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url=base_url,
            provider=provider,
            api_mode="chat_completions",
            model=model,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    agent.client = MagicMock()
    agent._cached_system_prompt = "You are helpful."
    agent._use_prompt_caching = False
    agent._api_max_retries = 1
    agent.compression_enabled = False
    agent.save_trajectories = False
    return agent


def test_rate_limit_terminal_message_explains_wait_or_model_switch(monkeypatch):
    monkeypatch.setenv("HERMES_LANGUAGE", "ru")
    formatter = getattr(conversation_loop, "_rate_limit_terminal_message", None)
    assert callable(formatter), "нет отдельной понятной формулировки для rate limit"

    message = formatter(
        summary="HTTP 429: Too Many Requests",
        retries=3,
        provider="openrouter",
        model="anthropic/claude-sonnet",
    )

    assert "временный лимит запросов" in message.lower()
    assert "подожд" in message.lower()
    assert "/model" in message
    assert "HTTP 429: Too Many Requests" in message


def test_rate_limit_terminal_message_keeps_english_baseline(monkeypatch):
    monkeypatch.setenv("HERMES_LANGUAGE", "en")
    formatter = getattr(conversation_loop, "_rate_limit_terminal_message", None)
    assert callable(formatter)

    message = formatter(
        summary="HTTP 429",
        retries=3,
        provider="openrouter",
        model="test-model",
    )

    assert message == "API call failed after 3 retries: HTTP 429"


def test_run_conversation_returns_russian_rate_limit_explanation(monkeypatch):
    monkeypatch.setenv("HERMES_LANGUAGE", "ru")
    agent = _make_failing_agent()
    error = Exception("Rate limit exceeded: Too Many Requests")
    error.status_code = 429
    agent.client.chat.completions.create.side_effect = error

    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("Продолжай работу")

    assert agent.client.chat.completions.create.called
    assert result["failed"] is True
    assert result["failure_reason"] == "rate_limit"
    assert "временный лимит запросов" in result["final_response"].lower()
    assert "подожд" in result["final_response"].lower()
    assert "Rate limit exceeded" in result["error"]


def test_run_conversation_carries_known_subscription_reset(monkeypatch):
    monkeypatch.setenv("HERMES_LANGUAGE", "ru")
    agent = _make_failing_agent(provider="openai-codex", base_url="http://codex.local")
    error = Exception("The usage limit has been reached")
    error.status_code = 429
    error.body = {
        "error": {
            "type": "usage_limit_reached",
            "message": str(error),
            "resets_at": "2026-09-19T10:30:00Z",
        }
    }
    error.response = MagicMock(headers={})
    agent.client.chat.completions.create.side_effect = error

    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
        patch("agent.conversation_loop.time.sleep"),
    ):
        result = agent.run_conversation("Продолжай работу")

    assert result["failed"] is True
    assert result["failure_reason"] == "rate_limit"
    assert result["failure_reset_at"] == "2026-09-19T10:30:00Z"


def test_run_conversation_returns_russian_confirmed_billing_explanation(monkeypatch):
    monkeypatch.setenv("HERMES_LANGUAGE", "ru")
    agent = _make_failing_agent(
        provider="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-opus-5",
    )
    error = Exception("Your credit balance is too low to access the Anthropic API.")
    error.status_code = 400
    error.body = {
        "error": {
            "type": "invalid_request_error",
            "message": str(error),
        }
    }
    agent.client.chat.completions.create.side_effect = error

    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("Продолжай работу")

    assert result["failed"] is True
    assert result["failure_reason"] == "billing"
    assert result["billing_unverified"] is False
    assert "закончилась квота или средства" in result["final_response"].lower()
    assert "api-ключ" in result["final_response"].lower()
    assert "credit balance is too low" in result["error"].lower()


def test_run_conversation_keeps_russian_unverified_billing_hedged(monkeypatch):
    monkeypatch.setenv("HERMES_LANGUAGE", "ru")
    agent = _make_failing_agent(
        provider="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-opus-5",
    )
    detail = (
        "You're out of extra usage. Add more at "
        "claude.ai/settings/usage and keep going."
    )
    error = Exception(detail)
    error.status_code = 400
    error.body = {
        "error": {
            "type": "invalid_request_error",
            "message": detail,
        }
    }
    agent.client.chat.completions.create.side_effect = error

    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("Продолжай работу")

    assert result["failed"] is True
    assert result["failure_reason"] == "billing"
    assert result["billing_unverified"] is True
    assert "не подтверждено" in result["final_response"].lower()
    assert "фильтр" in result["final_response"].lower()
    assert "out of extra usage" in result["error"].lower()


def test_provider_recovery_statuses_are_clear_in_russian(monkeypatch):
    monkeypatch.setenv("HERMES_LANGUAGE", "ru")
    formatter = getattr(conversation_loop, "_provider_recovery_status", None)
    assert callable(formatter), "нет русских статусов восстановления провайдера"

    assert "закончилась квота" in formatter("billing").lower()
    assert "может быть неточным" in formatter("billing_unverified").lower()
    assert "недоступен" in formatter("unreachable").lower()
    assert "лимит запросов" in formatter("rate_limit").lower()
    assert "не удалось подтвердить доступ" in formatter("auth").lower()


def test_provider_recovery_status_keeps_technical_upstream_name(monkeypatch):
    monkeypatch.setenv("HERMES_LANGUAGE", "ru")
    formatter = getattr(conversation_loop, "_provider_recovery_status", None)
    assert callable(formatter)

    message = formatter("upstream_rate_limit", upstream="DeepSeek")

    assert "DeepSeek" in message
    assert "временного лимита" in message.lower()
