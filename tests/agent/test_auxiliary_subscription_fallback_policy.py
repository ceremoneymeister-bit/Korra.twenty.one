"""T4-08: old fallback rows cannot authorize auxiliary API billing.

Real config, routing, call_llm/async_call_llm and OpenAI SDK requests against
MockTransport; only subscription construction and HTTP are substituted.
"""

import asyncio
from contextvars import ContextVar

import httpx
import pytest
import yaml
from openai import APIStatusError, OpenAI

from agent import auxiliary_client as aux


@pytest.fixture
def routes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "fake-unused-api-key")
    monkeypatch.setattr(aux, "_RUNTIME_MAIN_CONTEXT", ContextVar("fallback_runtime", default={}))
    monkeypatch.setattr(aux, "_client_cache", {})
    monkeypatch.setattr(aux, "_aux_unhealthy_until", {})
    monkeypatch.setattr(aux, "_select_pool_entry", lambda *_: (False, None))
    monkeypatch.setattr(aux, "_refresh_provider_credentials", lambda *_: False)
    requests = []
    clients = []

    def respond(request):
        requests.append(request.url.host)
        if request.url.host == "subscription.invalid":
            return httpx.Response(402, json={"error": {"message": "subscription capacity exhausted"}})
        return httpx.Response(200, json={
            "id": "synthetic", "object": "chat.completion", "created": 1,
            "model": "fallback-model", "choices": [{
                "index": 0, "message": {"role": "assistant", "content": "result"},
                "finish_reason": "stop",
            }],
        })

    def build(**kwargs):
        client = OpenAI(api_key="fake-unused-api-key", base_url=kwargs["base_url"],
                        http_client=httpx.Client(transport=httpx.MockTransport(respond)), max_retries=0)
        clients.append(client)
        return client

    monkeypatch.setattr(aux, "_create_openai_client", build)
    async_transports = []

    def http_kwargs(_base_url, *, async_mode=False):
        transport = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        async_transports.append(transport)
        return {"http_client": transport}

    monkeypatch.setattr(aux, "_openai_http_client_kwargs", http_kwargs)

    def configure(provider, task, chain, policy, available):
        entry = {"provider": "custom", "model": "fallback-model",
                 "base_url": "https://paid.invalid/v1", "key_env": "OPENAI_API_KEY"}
        task_cfg = {"provider": "auto"}
        cfg = {"model": {"provider": provider, "default": "subscription-model"},
               "auxiliary": {"transient_retries": 0, task: task_cfg}}
        if policy != "absent":
            cfg["auxiliary"]["free_only"] = policy
        if chain == "task":
            task_cfg["fallback_chain"] = [entry]
        else:
            cfg["fallback_providers"] = [entry]
        (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
        primary = build(base_url="https://subscription.invalid/v1") if available else None
        if primary is not None:
            primary.api_key = "sk-ant-oat01-fake-subscription-token"
        monkeypatch.setattr(aux, "_build_codex_client", lambda *_: (primary, "subscription-model"))
        monkeypatch.setattr(aux, "_try_anthropic", lambda **_: (primary, "subscription-model"))
        return primary

    yield configure, requests
    for client in clients:
        client.close()
    for transport in async_transports:
        asyncio.run(transport.aclose())


@pytest.mark.parametrize("provider", ["openai-codex", "anthropic"])
@pytest.mark.parametrize("chain", ["task", "main"])
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("policy,bound", [
    (True, False), (False, False), (True, True), (False, True),
    ("absent", False), ("false", False), (None, False), (0, False),
])
def test_unavailable_subscription_respects_billing_consent(routes, provider, chain, async_mode, policy, bound):
    configure, requests = routes
    configure(provider, "title_generation", chain, policy, available=False)
    call = aux.async_call_llm if async_mode else aux.call_llm
    runtime = {"provider": provider, "model": "subscription-model", "auth_mode": "oauth"} if bound else {}

    def invoke():
        result = call("title_generation", main_runtime=runtime, messages=[{"role": "user", "content": "title"}])
        return asyncio.run(result) if async_mode else result

    if policy is False:
        assert invoke().choices[0].message.content == "result"
        assert requests == ["paid.invalid"]
    else:
        with pytest.raises(RuntimeError):
            invoke()
        assert requests == []


@pytest.mark.parametrize("provider", ["openai-codex", "anthropic"])
@pytest.mark.parametrize("task", ["title_generation", "vision"])
@pytest.mark.parametrize("chain", ["task", "main"])
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("policy", [True, False])
def test_request_error_cannot_bypass_cost_policy(routes, monkeypatch, provider, task, chain, async_mode, policy):
    configure, requests = routes
    configure(provider, task, chain, policy, available=True)
    monkeypatch.setattr(aux, "_main_model_supports_vision", lambda *_: True)
    monkeypatch.setattr(aux, "_resolve_provider_vision_default", lambda *_: None)
    call = aux.async_call_llm if async_mode else aux.call_llm

    def invoke():
        result = call(task, main_runtime={}, messages=[{"role": "user", "content": "synthetic"}])
        return asyncio.run(result) if async_mode else result

    # Vision resolves auto to a concrete provider before request-time
    # fallback, so it uses the task chain and main-model safety net only.
    if policy is False and not (task == "vision" and chain == "main"):
        assert invoke().choices[0].message.content == "result"
        assert requests == ["subscription.invalid", "paid.invalid"]
    else:
        with pytest.raises(APIStatusError, match="subscription capacity exhausted"):
            invoke()
        assert requests == ["subscription.invalid"]


@pytest.mark.parametrize("provider", ["openai-codex", "anthropic"])
@pytest.mark.parametrize("async_mode", [False, True])
def test_unavailable_vision_does_not_discover_paid_fallback(routes, provider, async_mode):
    configure, requests = routes
    configure(provider, "vision", "task", True, available=False)
    call = aux.async_call_llm if async_mode else aux.call_llm
    with pytest.raises(RuntimeError):
        result = call("vision", main_runtime={}, messages=[{"role": "user", "content": "image"}])
        if async_mode:
            asyncio.run(result)
    assert requests == []


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("policy", [True, False])
def test_resolved_anthropic_api_credential_requires_opt_in(routes, monkeypatch, bound, policy):
    configure, _requests = routes
    configure("openai-codex", "title_generation", "task", policy, available=False)
    api_client = type("APIClient", (), {"api_key": "fake-api-key"})()
    monkeypatch.setattr(aux, "_try_anthropic", lambda **_: (api_client, "api-model"))
    runtime = {"provider": "openai-codex", "model": "subscription-model"} if bound else {}
    assert aux._subscription_fallback_client_allowed("anthropic", api_client, runtime) is (policy is False)
    assert aux._try_main_agent_model_fallback("other", main_runtime={"provider": "anthropic", "model": "api-model"}) == (
        (None, None, "") if policy else (api_client, "api-model", "main-agent(anthropic)")
    )
