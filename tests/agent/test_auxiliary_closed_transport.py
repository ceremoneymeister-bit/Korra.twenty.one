"""A watchdog timeout must not be replaced by a retry on its closed client."""

import asyncio
from contextlib import ExitStack
from unittest.mock import patch

import httpx
import openai
import pytest

from agent import auxiliary_client as ac


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("close_on_attempt", [1, 2])
def test_timeout_keeps_original_error_when_transport_closed(async_mode, close_on_attempt):
    leaf = openai.OpenAI(api_key="test-only", max_retries=0)
    wrapper = ac.CodexAuxiliaryClient(leaf, "test-model")
    client = ac.AsyncCodexAuxiliaryClient(wrapper) if async_mode else wrapper
    failure = openai.APITimeoutError(
        request=httpx.Request("POST", "https://example.test")
    )
    calls = []

    def timed_out(**kwargs):
        calls.append(kwargs)
        if len(calls) >= close_on_attempt:
            leaf.close()
        if len(calls) > close_on_attempt:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        raise failure

    with ExitStack() as stack:
        for name, value in {
            "_resolve_task_provider_model": (
                "openai-codex", "test-model", None, None, None,
            ),
            "_get_cached_client": (client, "test-model"),
            "_try_configured_fallback_chain": (None, None, ""),
            "_try_main_agent_model_fallback": (None, None, ""),
            "_transient_retry_count": 2,
        }.items():
            stack.enter_context(patch.object(ac, name, return_value=value))
        stack.enter_context(
            patch.object(wrapper.chat.completions, "create", side_effect=timed_out)
        )
        stack.enter_context(patch.object(ac.time, "sleep"))
        try:
            with pytest.raises(openai.APITimeoutError) as caught:
                kwargs = {
                    "task": "vision",
                    "messages": [{"role": "user", "content": "inspect"}],
                }
                if async_mode:
                    asyncio.run(ac.async_call_llm(**kwargs))
                else:
                    ac.call_llm(**kwargs)
            assert caught.value is failure
            assert len(calls) == close_on_attempt
            assert leaf.is_closed()
        finally:
            leaf.close()
