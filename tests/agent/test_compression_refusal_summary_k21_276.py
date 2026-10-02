"""K21-276: a model's refusal is not a compaction summary."""

from unittest.mock import patch

import pytest

from agent.context_compressor import ContextCompressor, _is_summary_refusal
from tests.agent.test_compressor_truncated_summary_guard import _mock_response, _msgs

REFUSAL = "I can't produce this summary as requested. The instructions conflict with my operating rules."
REAL = "## Goal\nFinish the task.\n\n## Completed Actions\n1. Did X."


@pytest.mark.parametrize(
    "content",
    [
        REFUSAL,
        "Sorry, I am unable to create a context checkpoint for this conversation.",
        "I'm unable to summarize this conversation.",
        "We couldn't write the summary you asked for.",
    ],
)
def test_refusal_bodies_are_detected(content):
    assert _is_summary_refusal(content) is True


@pytest.mark.parametrize(
    "content",
    [
        REAL + "\n2. Recorded that a provider refused an earlier request.",
        "I cannot see the earlier turns, but here is the summary:\n" + REAL,
        "I can't help with that request.",
        "The summary of the work so far: done.",
    ],
)
def test_real_summaries_and_unrelated_text_are_not_refusals(content):
    assert _is_summary_refusal(content) is False


def _compressor():
    with patch("agent.context_compressor.get_model_context_length", return_value=100000):
        return ContextCompressor(
            model="test", quiet_mode=True, protect_first_n=2, protect_last_n=2,
            abort_on_summary_failure=False,
        )


def test_refusal_text_is_not_stored_as_summary_and_compression_aborts():
    c = _compressor()
    msgs = _msgs()
    with patch("agent.context_compressor.call_llm", return_value=_mock_response(REFUSAL, "stop")):
        result = c.compress(msgs, current_tokens=999999, force=True)

    assert result == msgs
    assert c._last_summary_empty_content_failure is True
    assert c._last_compress_aborted is True
    assert "refusal content" in (c._last_summary_error or "")
    assert c._previous_summary is None


def test_explicit_provider_refusal_field_wins_over_summary_shaped_content():
    c = _compressor()
    msgs = _msgs()
    response = _mock_response(REAL, "stop")
    response.choices[0].message.refusal = "policy refusal"
    with patch("agent.context_compressor.call_llm", return_value=response):
        result = c.compress(msgs, current_tokens=999999, force=True)

    assert result == msgs
    assert c._last_compress_aborted is True
    assert c._previous_summary is None


def test_real_summary_is_still_accepted():
    c = _compressor()
    with patch("agent.context_compressor.call_llm", return_value=_mock_response(REAL, "stop")):
        result = c.compress(_msgs(), current_tokens=999999, force=True)

    assert result != _msgs()
    assert "Finish the task." in (c._previous_summary or "")
