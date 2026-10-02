"""K21-276 (1): a hung summary is a timeout, not a terminal network failure."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent.context_compressor import ContextCompressor
from agent.conversation_compression import (
    compress_context,
    compression_blocked_transiently,
)
from korra_state import SessionDB

STALL = TimeoutError(
    "Codex auxiliary Responses stream stalled: no new output for 60.0s (60.1s elapsed)"
)


def _compressor(**kwargs):
    with patch("agent.context_compressor.get_model_context_length", return_value=100000):
        return ContextCompressor(model="main-model", quiet_mode=True, **kwargs)


def _turns():
    return [
        {"role": "user", "content": "do something"},
        {"role": "assistant", "content": "ok"},
    ]


def _history(n=12):
    out = [{"role": "system", "content": "sys"}]
    for i in range(n):
        out.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " * 20})
    return out


def test_codex_stall_is_timeout_with_ladder_cooldown():
    c = _compressor()
    with patch("agent.context_compressor.call_llm", side_effect=STALL), patch(
        "agent.context_compressor.time.monotonic", return_value=1000.0
    ):
        assert c._generate_summary(_turns()) is None
    assert c._consecutive_timeout_failures == 1
    assert c._summary_failure_cooldown_until == 1060.0


def test_codex_stall_retries_on_main_model_before_giving_up():
    ok = MagicMock()
    ok.choices = [MagicMock()]
    ok.choices[0].message.content = "summary via main model"
    c = _compressor(summary_model_override="aux-model")
    with patch("agent.context_compressor.call_llm", side_effect=[STALL, ok]) as call:
        result = c._generate_summary(_turns())
    assert call.call_count == 2
    assert "summary via main model" in result


def test_first_stall_preserves_session_second_stall_uses_fallback_summary():
    c = _compressor()
    msgs = _history()
    with patch("agent.context_compressor.call_llm", side_effect=STALL):
        first = c.compress(copy.deepcopy(msgs), current_tokens=999999)
        assert first == msgs
        assert c._last_compress_aborted is True
        c._summary_failure_cooldown_until = 0.0  # the 60s cooldown lapsed
        second = c.compress(copy.deepcopy(msgs), current_tokens=999999)
    assert c._last_compress_aborted is False
    assert c._last_summary_fallback_used is True
    assert second != msgs


def test_plain_connection_error_still_aborts_every_time():
    c = _compressor()
    msgs = _history()
    with patch("agent.context_compressor.call_llm", side_effect=ConnectionError("Connection error.")):
        for _ in range(3):
            assert c.compress(copy.deepcopy(msgs), current_tokens=999999, force=True) == msgs
            assert c._last_compress_aborted is True


def test_aborted_summary_is_reported_as_transient_block(tmp_path: Path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("STALL_ABORT", source="cli")
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            model="test/model",
            quiet_mode=True,
            session_db=db,
            session_id="STALL_ABORT",
            skip_context_files=True,
            skip_memory=True,
        )
    agent._compression_feasibility_checked = True
    agent.compression_in_place = True
    agent._cached_system_prompt = "sys"
    comp = agent.context_compressor

    def aborting_compress(messages, **_kw):
        comp._record_compression_failure_cooldown(60, "stalled")
        comp._last_summary_error = "stalled"
        comp._last_compress_aborted = True
        return list(messages)

    comp.compress = aborting_compress
    live = [{"role": "user", "content": f"m{i}"} for i in range(20)]
    before = copy.deepcopy(live)
    out, _ = compress_context(agent, live, "sys", approx_tokens=500_000)
    assert out == before
    assert compression_blocked_transiently(agent) is True


# ---------------------------------------------------------------------------
# K21-276 (3): provider overload keeps the transcript until it is sustained
# ---------------------------------------------------------------------------


class _Overloaded(Exception):
    status_code = 529

    def __init__(self):
        super().__init__("Error code: 529 - {'type': 'overloaded_error', 'message': 'Overloaded'}")


def test_overload_preserves_transcript_then_degrades_after_three_in_a_row():
    c = _compressor()
    msgs = _history()
    with patch("agent.context_compressor.call_llm", side_effect=_Overloaded()):
        for attempt in (1, 2):
            out = c.compress(copy.deepcopy(msgs), current_tokens=999999)
            assert out == msgs, f"attempt {attempt} must keep the transcript"
            assert c._last_compress_aborted is True
            c._summary_failure_cooldown_until = 0.0
        third = c.compress(copy.deepcopy(msgs), current_tokens=999999)
    assert c._last_compress_aborted is False
    assert c._last_summary_fallback_used is True
    assert third != msgs


def test_successful_summary_resets_overload_budget():
    ok = MagicMock()
    ok.choices = [MagicMock()]
    ok.choices[0].message.content = "a real summary"
    c = _compressor()
    msgs = _history()
    with patch("agent.context_compressor.call_llm", side_effect=_Overloaded()):
        c.compress(copy.deepcopy(msgs), current_tokens=999999)
    c._summary_failure_cooldown_until = 0.0
    assert getattr(c, "_consecutive_overload_failures", 0) == 1
    with patch("agent.context_compressor.call_llm", return_value=ok):
        c.compress(copy.deepcopy(msgs), current_tokens=999999)
    assert getattr(c, "_consecutive_overload_failures", 0) == 0


def test_overload_on_aux_model_retries_on_main_first():
    ok = MagicMock()
    ok.choices = [MagicMock()]
    ok.choices[0].message.content = "summary via main model"
    c = _compressor(summary_model_override="aux-model")
    with patch("agent.context_compressor.call_llm", side_effect=[_Overloaded(), ok]) as call:
        result = c._generate_summary(_turns())
    assert call.call_count == 2
    assert "summary via main model" in result
    assert getattr(c, "_consecutive_overload_failures", 0) == 0


def test_abort_on_summary_failure_still_hard_aborts_overload():
    c = _compressor(abort_on_summary_failure=True)
    msgs = _history()
    with patch("agent.context_compressor.call_llm", side_effect=_Overloaded()):
        for _ in range(4):
            c._summary_failure_cooldown_until = 0.0
            assert c.compress(copy.deepcopy(msgs), current_tokens=999999) == msgs
