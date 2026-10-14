"""F19: auto uses the installation subscription; ambient API keys are not consent."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent import auxiliary_client as aux


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-unused-api-key")
    monkeypatch.setattr(aux, "_RUNTIME_MAIN_CONTEXT", __import__('contextvars').ContextVar('test_runtime', default={}))
    monkeypatch.setattr(aux, "_paid_lane_warned", set())
    monkeypatch.setattr(aux, "_aux_unhealthy_until", {})
    (tmp_path / "config.yaml").write_text("model:\n  provider: auto\n  default: configured-subscription-model\n")
    return tmp_path


def test_paid_openrouter_default_is_blocked_before_client_or_pool(config, monkeypatch):
    build = Mock()
    pool = Mock()
    monkeypatch.setattr(aux, "_create_openai_client", build)
    monkeypatch.setattr(aux, "_select_pool_entry", pool)
    assert aux._try_openrouter() == (None, None)
    build.assert_not_called()
    pool.assert_not_called()


@pytest.mark.parametrize("task", ["compression", "title_generation", "session_search", "web_extract"])
def test_no_subscription_auto_never_discovers_api(config, monkeypatch, task):
    discovery = Mock(return_value=[])
    monkeypatch.setattr(aux, "_get_provider_chain", discovery)
    assert aux._resolve_auto_route(task=task) == (None, None, "")
    assert aux._try_payment_fallback("auto", task=task) == (None, None, "")
    discovery.assert_not_called()


@pytest.mark.parametrize("provider", ["openai-codex", "anthropic"])
def test_unbound_auto_uses_active_subscription_for_text_and_vision(config, monkeypatch, provider):
    from korra_cli import auth, runtime_provider
    (config / "auth.json").write_text(json.dumps({"active_provider": provider, "providers": {}}))
    token = "fake-codex-token" if provider == "openai-codex" else "sk-ant-oat01-fake-test-token"
    runtime = {"provider": provider, "api_key": token, "auth_mode": "oauth", "api_mode": "codex_responses" if provider == "openai-codex" else "anthropic_messages"}
    # Only the OAuth refresh/transport boundary is substituted. Config and
    # active-provider lookup, auto routing and credential policy run for real.
    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", lambda **kwargs: runtime)
    raw = SimpleNamespace(api_key=token, base_url="https://subscription.invalid")
    client = aux.CodexAuxiliaryClient(raw, "configured-subscription-model") if provider == "openai-codex" else raw
    resolved = Mock(return_value=(client, "configured-subscription-model"))
    monkeypatch.setattr(aux, "resolve_provider_client", resolved)
    monkeypatch.setattr(aux, "_resolve_provider_vision_default", lambda *_: None)
    monkeypatch.setattr(aux, "_main_model_supports_vision", lambda *_: True)
    discovery = Mock()
    monkeypatch.setattr(aux, "_get_provider_chain", discovery)
    assert auth.get_active_provider() == provider
    assert aux._resolve_auto_route(task="compression") == (client, "configured-subscription-model", provider)
    assert resolved.call_args.args[:2] == (provider, "configured-subscription-model")
    assert aux.resolve_vision_provider_client() == (provider, client, "configured-subscription-model")
    discovery.assert_not_called()


def test_subscription_without_vision_degrades_without_api(config, monkeypatch):
    monkeypatch.setattr(aux, "_main_model_supports_vision", lambda *_: False)
    discovery = Mock()
    monkeypatch.setattr(aux, "_resolve_strict_vision_backend", discovery)
    assert aux.resolve_vision_provider_client(main_runtime={"provider": "openai-codex", "model": "text-model"}) == (None, None, None)
    discovery.assert_not_called()


def test_config_read_failure_fails_closed(config, monkeypatch):
    monkeypatch.setattr(__import__('korra_cli.config', fromlist=['load_config_readonly']),
                        "load_config_readonly", Mock(side_effect=OSError("unreadable config")))
    assert aux._aux_openrouter_settings()[0] is True


def test_explicit_owner_route_remains_available(config, monkeypatch):
    (config / "config.yaml").write_text("auxiliary:\n  free_only: false\n  compression:\n    provider: openrouter\n    model: owner-chosen-model\n")
    monkeypatch.setattr(aux, "_select_pool_entry", lambda *_: (False, None))
    client = object()
    build = Mock(return_value=client)
    monkeypatch.setattr(aux, "_create_openai_client", build)
    assert aux.get_text_auxiliary_client("compression") == (client, "owner-chosen-model")
    assert str(build.call_args.kwargs["base_url"]).startswith("https://openrouter.ai")


def test_missing_key_never_announces_a_paid_request(config, monkeypatch, caplog):
    (config / "config.yaml").write_text("auxiliary:\n  free_only: false\n")
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setattr(aux, "_select_pool_entry", lambda *_: (False, None))
    assert aux._try_openrouter() == (None, None)
    assert "PAID lane engaged" not in caplog.text


@pytest.mark.parametrize("value", [None, 0, "false"])
def test_malformed_opt_in_does_not_enable_api_discovery(config, value):
    import yaml
    (config / "config.yaml").write_text(yaml.safe_dump({"auxiliary": {"free_only": value}}))
    assert aux._aux_openrouter_settings()[0] is True


def test_unbound_configured_api_fallback_is_not_default_consent(config):
    # A fallback row (possibly inherited from an older configuration) must
    # not make an auto subscription task contact another API account.
    assert not aux._subscription_fallback_entry_may_resolve({"provider": "openai"}, {"provider": "auto"})
    assert not aux._subscription_fallback_client_allowed("anthropic", SimpleNamespace(api_key="fake-api-key"), {})
