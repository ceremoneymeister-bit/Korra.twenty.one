"""K21-276 (4): mid-turn compaction keeps the tool round the model has not read."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from agent.context_compressor import ContextCompressor
from agent.model_metadata import estimate_messages_tokens_rough
from agent.prompt_builder import steer_user_row


def _terminal_call(call_id: str, command: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "terminal", "arguments": json.dumps({"command": command})},
    }


def _terminal_result(call_id: str, tag: str, lines: int) -> dict:
    output = "\n".join(
        f"node-{tag}-{i:03d} cpu={i % 97} lease={tag.upper()}{i:06X} zone=eu-west-2a status=HEALTHY "
        f"mem=41% disk=73% uptime=31d kernel=6.8.0-45 owner=okafor rack=R-{i % 40:02d} note=nominal"
        for i in range(lines)
    )
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps({"output": output, "exit_code": 0})}


def _session(newest_lines: int):
    prose = ("the quarterly fleet review keeps drifting between regions and owners " * 360)[:24_000]
    msgs: list[dict] = [{"role": "system", "content": "You are Korra."}]
    for t in range(13):
        msgs += [
            {"role": "user", "content": f"{prose}\nRun probe {t} and name the hottest node."},
            {"role": "assistant", "content": None, "tool_calls": [_terminal_call(f"old_{t}", f"python3 probe.py c{t} 3")]},
            _terminal_result(f"old_{t}", f"o{t}", 70),
            {"role": "assistant", "content": f"node-o{t}-000 is the hottest."},
        ]
    msgs += [
        {"role": "user", "content": "Run both probes and compare them."},
        {"role": "assistant", "content": None, "tool_calls": [
            _terminal_call("new_a", "python3 probe.py indus 3"), _terminal_call("new_b", "python3 probe.py lyra 1"),
        ]},
        _terminal_result("new_a", "na", 70),
        _terminal_result("new_b", "nb", newest_lines),
    ]
    return msgs


def _compressor(ctx=272_000):
    with patch("agent.context_compressor.get_model_context_length", return_value=ctx):
        c = ContextCompressor(
            model="test/model", threshold_percent=0.50, protect_first_n=3, protect_last_n=8,
            quiet_mode=True, config_context_length=ctx,
        )
    c._generate_summary = lambda *a, **k: "compact summary of earlier turns"
    return c


@pytest.mark.parametrize("newest_lines", [420, 1000], ids=["bigger_than_soft_ceiling", "huge_but_fits"])
@pytest.mark.parametrize("steered", [False, True], ids=["round_last", "steer_after_round"])
def test_pending_tool_round_survives_compaction_verbatim(steered, newest_lines):
    c = _compressor()
    msgs = _session(newest_lines=newest_lines)
    pending = {m["tool_call_id"]: m["content"] for m in msgs[-2:]}
    previous = msgs[-6]
    assert previous["tool_call_id"] == "old_12"
    if steered:
        msgs.append(steer_user_row("Also flag any node above 90% cpu."))

    out = c.compress(list(msgs), current_tokens=estimate_messages_tokens_rough(msgs))

    by_id = {m.get("tool_call_id"): m.get("content") for m in out if m.get("role") == "tool"}
    for call_id, content in pending.items():
        assert by_id.get(call_id) == content, f"{call_id} was stubbed: {by_id.get(call_id)!r:.120}"
    assert by_id.get("old_12") != previous["content"], "older rounds must still give way"


def test_round_larger_than_a_fifth_of_the_window_still_gives_way():
    c = _compressor()
    msgs = _session(newest_lines=2500)
    huge = msgs[-1]["content"]
    assert estimate_messages_tokens_rough([msgs[-1]]) > 0.2 * 272_000

    out = c.compress(list(msgs), current_tokens=estimate_messages_tokens_rough(msgs))

    by_id = {m.get("tool_call_id"): m.get("content") for m in out if m.get("role") == "tool"}
    assert by_id.get("new_b") != huge
    assert estimate_messages_tokens_rough(out) < estimate_messages_tokens_rough(msgs)
