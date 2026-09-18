"""Automatic auxiliary routing never guesses a different paid provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent import auxiliary_client as aux


@pytest.fixture
def foreign_api_route():
    """A foreign API account is available, but was never selected for this session."""
    aux._aux_unhealthy_until.clear()
    api_client = MagicMock(name="foreign_api_client")
    discover = MagicMock(return_value=(api_client, "paid-api-model"))
    with (
        patch.object(aux, "resolve_provider_client", return_value=(None, None)),
        patch.object(aux, "_try_configured_fallback_chain", return_value=(None, None, "")),
        patch.object(aux, "_try_main_fallback_chain", return_value=(None, None, "")),
        patch.object(aux, "_get_provider_chain", return_value=[("foreign-api", discover)]),
    ):
        yield api_client, discover


def test_selected_subscription_down_refuses_foreign_api_at_resolve_time(foreign_api_route):
    _api_client, discover = foreign_api_route
    runtime = {
        "provider": "openai-codex",
        "model": "gpt-5.4",
        "base_url": "https://chatgpt.com/backend-api/codex",
        "api_key": "expired-subscription-token",
    }

    assert aux._resolve_auto_route(main_runtime=runtime, task="compression") == (None, None, "")
    discover.assert_not_called()


def test_selected_subscription_down_refuses_foreign_api_mid_request(foreign_api_route):
    _api_client, discover = foreign_api_route
    with patch.object(aux, "_read_main_provider", return_value="openai-codex"):
        assert aux._try_payment_fallback("openai-codex", task="compression") == (None, None, "")
    discover.assert_not_called()


def test_explicit_task_fallback_still_wins_before_discovery(foreign_api_route):
    _api_client, discover = foreign_api_route
    subscription_client = MagicMock(name="configured_subscription_client")
    runtime = {
        "provider": "openai-codex",
        "model": "gpt-5.4",
        "base_url": "https://chatgpt.com/backend-api/codex",
        "api_key": "expired-subscription-token",
    }
    with patch.object(
        aux,
        "_try_configured_fallback_chain",
        return_value=(subscription_client, "claude-opus-4-1", "fallback_chain[0](anthropic)"),
    ):
        client, model, provider = aux._resolve_auto_route(
            main_runtime=runtime,
            task="compression",
        )

    assert (client, model, provider) == (
        subscription_client,
        "claude-opus-4-1",
        "anthropic",
    )
    discover.assert_not_called()


def test_unconfigured_legacy_auto_mode_can_still_discover(foreign_api_route):
    api_client, discover = foreign_api_route
    with patch.object(aux, "_read_main_provider", return_value="auto"):
        client, model, provider = aux._resolve_auto_route(
            main_runtime={"provider": "auto"},
            task="compression",
        )

    assert (client, model, provider) == (api_client, "paid-api-model", "foreign-api")
    discover.assert_called_once_with()
