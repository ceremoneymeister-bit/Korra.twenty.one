"""K21-276: a value shortened by the compressor must not be written back by a tool call."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.context_compressor import _truncate_tool_call_args_json
from agent.tool_dispatch_helpers import _context_pruned_argument_paths
from tests.run_agent.test_tool_call_guardrail_runtime import _make_agent, _mock_tool_call

LONG = "line of the original file\n" * 40


def test_compressor_output_is_detected_for_effect_capable_tools():
    shortened = json.loads(_truncate_tool_call_args_json(json.dumps({"path": "a.md", "content": LONG})))
    assert _context_pruned_argument_paths("write_file", shortened) == ["$.content"]
    assert _context_pruned_argument_paths("mcp_files_write", {"edits": [shortened]}) == ["$.edits[0].content"]


@pytest.mark.parametrize("tail", ["...[truncated]", "…[truncated]"])
def test_both_legacy_tails_are_detected(tail):
    assert _context_pruned_argument_paths("patch", {"new_string": "x" * 300 + tail}) == ["$.new_string"]


def test_read_only_tools_short_strings_and_untouched_values_pass():
    shortened = {"query": "x" * 300 + "...[truncated]"}
    assert _context_pruned_argument_paths("read_file", shortened) == []
    assert _context_pruned_argument_paths("write_file", {"content": "see the ...[truncated] marker"}) == []
    assert _context_pruned_argument_paths("write_file", {"content": LONG}) == []


def test_sequential_dispatch_blocks_write_with_pruned_content():
    agent = _make_agent("write_file")
    args = {"path": "a.md", "content": "x" * 300 + "...[truncated]"}
    tc = _mock_tool_call("write_file", json.dumps(args), "c-pruned")
    msg = SimpleNamespace(content="", tool_calls=[tc])
    messages = []

    with patch("run_agent.handle_function_call", return_value="SHOULD_NOT_RUN") as mock_hfc:
        agent._execute_tool_calls_sequential(msg, messages, "task-1")

    mock_hfc.assert_not_called()
    assert "context-compression artifact" in messages[0]["content"]
    assert "$.content" in messages[0]["content"]


def test_plugin_modified_arguments_are_checked_too():
    agent = _make_agent("write_file")
    tc = _mock_tool_call("write_file", json.dumps({"path": "a.md", "content": "ok"}), "c-mod")
    msg = SimpleNamespace(content="", tool_calls=[tc])
    messages = []
    modified = {"path": "a.md", "content": "y" * 300 + "…[truncated]"}

    with (
        patch("korra_cli.plugins._dispatch_pre_tool_call_hooks", return_value=(None, modified)),
        patch("run_agent.handle_function_call", return_value="SHOULD_NOT_RUN") as mock_hfc,
    ):
        agent._execute_tool_calls_sequential(msg, messages, "task-1")

    mock_hfc.assert_not_called()
    assert "context-compression artifact" in messages[0]["content"]
