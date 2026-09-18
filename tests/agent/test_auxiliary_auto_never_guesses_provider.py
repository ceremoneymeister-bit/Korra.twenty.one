"""Automatic auxiliary routing never guesses a different paid provider."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent import auxiliary_client as aux


def _subscription_runtime() -> dict:
    return {
        "provider": "openai-codex",
        "requested_provider": "auto",
        "model": "gpt-5.4",
        "base_url": "https://chatgpt.com/backend-api/codex",
        "api_key": "expired-subscription-token",
        "auth_mode": "chatgpt",
    }


class _SyntheticRouteError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


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
    runtime = _subscription_runtime()

    assert aux._resolve_auto_route(main_runtime=runtime, task="compression") == (None, None, "")
    discover.assert_not_called()


def test_selected_subscription_down_refuses_foreign_api_mid_request(foreign_api_route):
    _api_client, discover = foreign_api_route
    with patch.object(aux, "_read_main_provider", return_value="auto"):
        assert aux._try_payment_fallback(
            "auto",
            task="compression",
            main_runtime=_subscription_runtime(),
        ) == (None, None, "")
    discover.assert_not_called()


def test_sync_runtime_subscription_failure_never_discovers_foreign_api(
    foreign_api_route,
):
    _api_client, discover = foreign_api_route
    primary = MagicMock(name="codex_subscription_client")
    primary.base_url = "https://chatgpt.com/backend-api/codex"
    primary.api_key = "expired-subscription-token"
    primary.chat.completions.create.side_effect = _SyntheticRouteError(
        401,
        "expired subscription credential",
    )
    runtime = _subscription_runtime()

    with (
        patch.object(
            aux,
            "_resolve_task_provider_model",
            return_value=("auto", "gpt-5.4", None, None, None),
        ),
        patch.object(aux, "_get_cached_client", return_value=(primary, "gpt-5.4")),
        patch.object(aux, "_refresh_provider_credentials", return_value=False),
        patch.object(aux, "_recoverable_pool_provider", return_value=None),
        patch.object(
            aux,
            "_try_payment_fallback",
            wraps=aux._try_payment_fallback,
        ) as fallback,
        pytest.raises(_SyntheticRouteError, match="expired subscription"),
    ):
        aux.call_llm(
            task="compression",
            main_runtime=runtime,
            messages=[{"role": "user", "content": "summarize"}],
        )

    assert fallback.call_args.kwargs["main_runtime"]["provider"] == "openai-codex"
    discover.assert_not_called()


@pytest.mark.asyncio
async def test_async_runtime_subscription_capacity_failure_never_discovers_foreign_api(
    foreign_api_route,
):
    _api_client, discover = foreign_api_route
    primary = MagicMock(name="async_codex_subscription_client")
    primary.base_url = "https://chatgpt.com/backend-api/codex"
    primary.api_key = "expired-subscription-token"
    primary.chat.completions.create = AsyncMock(
        side_effect=_SyntheticRouteError(402, "subscription capacity exhausted")
    )
    runtime = _subscription_runtime()

    with (
        patch.object(
            aux,
            "_resolve_task_provider_model",
            return_value=("auto", "gpt-5.4", None, None, None),
        ),
        patch.object(aux, "_get_cached_client", return_value=(primary, "gpt-5.4")),
        patch.object(aux, "_recoverable_pool_provider", return_value=None),
        patch.object(
            aux,
            "_try_payment_fallback",
            wraps=aux._try_payment_fallback,
        ) as fallback,
        pytest.raises(_SyntheticRouteError, match="capacity exhausted"),
    ):
        await aux.async_call_llm(
            task="compression",
            main_runtime=runtime,
            messages=[{"role": "user", "content": "summarize"}],
        )

    assert fallback.call_args.kwargs["main_runtime"]["provider"] == "openai-codex"
    discover.assert_not_called()


def test_legacy_task_api_fallback_is_not_billing_consent():
    entry = {"provider": "openrouter", "model": "paid/model"}
    with (
        patch.object(
            aux,
            "_get_auxiliary_task_config",
            return_value={"fallback_chain": [entry]},
        ),
        patch.object(aux, "_resolve_fallback_entry") as resolve,
    ):
        assert aux._try_configured_fallback_chain(
            "compression",
            "auto",
            main_runtime=_subscription_runtime(),
        ) == (None, None, "")
    resolve.assert_not_called()


def test_legacy_top_level_api_fallback_is_not_billing_consent():
    entry = {"provider": "openrouter", "model": "paid/model"}
    with (
        patch("korra_cli.config.load_config_readonly", return_value={}),
        patch("korra_cli.fallback_config.get_fallback_chain", return_value=[entry]),
        patch.object(aux, "_resolve_fallback_entry") as resolve,
    ):
        assert aux._try_main_fallback_chain(
            "compression",
            "auto",
            main_runtime=_subscription_runtime(),
        ) == (None, None, "")
    resolve.assert_not_called()


def test_explicit_subscription_fallback_remains_available():
    entry = {"provider": "openai-codex", "model": "gpt-5.4-mini"}
    real_client = MagicMock()
    real_client.api_key = "subscription-token"
    real_client.base_url = "https://chatgpt.com/backend-api/codex"
    subscription_client = aux.CodexAuxiliaryClient(real_client, "gpt-5.4-mini")
    with (
        patch.object(
            aux,
            "_get_auxiliary_task_config",
            return_value={"fallback_chain": [entry]},
        ),
        patch.object(
            aux,
            "_resolve_fallback_entry",
            return_value=(subscription_client, "gpt-5.4-mini"),
        ),
    ):
        client, model, label = aux._try_configured_fallback_chain(
            "compression",
            "anthropic",
            main_runtime=_subscription_runtime(),
        )

    assert client is subscription_client
    assert model == "gpt-5.4-mini"
    assert label == "fallback_chain[0](openai-codex)"


def test_explicit_task_fallback_still_wins_before_discovery(foreign_api_route):
    _api_client, discover = foreign_api_route
    subscription_client = MagicMock(name="configured_subscription_client")
    runtime = _subscription_runtime()
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
