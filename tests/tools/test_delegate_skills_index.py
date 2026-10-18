"""delegate_task children get a one-line skill pointer, not the full skills
index (cost-aware 18.10: the index rode on every child step)."""

import socket
from unittest.mock import MagicMock, patch

import pytest

from tests.tools.test_delegate import _make_mock_parent
from tools.delegate_tool import DEFAULT_MAX_ITERATIONS, _build_child_agent


def _build(cfg):
    with patch("tools.delegate_tool._load_config", return_value=cfg), \
            patch("run_agent.AIAgent") as MockAgent:
        MockAgent.return_value = MagicMock()
        return _build_child_agent(
            task_index=0, goal="find file X", context=None, toolsets=None,
            model=None, max_iterations=50, parent_agent=_make_mock_parent(),
            task_count=1,
        )


def test_child_skills_index_disabled_by_default():
    assert _build({"max_iterations": 50})._skills_index_disabled is True


@pytest.mark.parametrize("value", [True, "true"])
def test_child_skills_index_opt_back_in(value):
    assert _build({"child_skills_index": value})._skills_index_disabled is False


def test_default_max_iterations_unchanged():
    # Kept at 250 deliberately: a 50-step cap would truncate long coding
    # children (see the 18.10 cost report); only the stale comment changed.
    from korra_cli.config import DEFAULT_CONFIG

    assert DEFAULT_MAX_ITERATIONS == 250
    assert DEFAULT_CONFIG["delegation"]["max_iterations"] == 250
    assert DEFAULT_CONFIG["delegation"]["child_skills_index"] is False


@pytest.fixture
def skills_home(monkeypatch, tmp_path):
    d = tmp_path / "skills" / "tools" / "report-finder"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: report-finder\ndescription: Find reports\n---\n")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from agent.prompt_builder import clear_skills_system_prompt_cache

    clear_skills_system_prompt_cache(clear_snapshot=True)
    yield tmp_path
    clear_skills_system_prompt_cache(clear_snapshot=True)


def _agent():
    from run_agent import AIAgent

    with patch.object(socket.socket, "connect", side_effect=OSError("offline")):
        return AIAgent(
            model="claude-sonnet-5-5", base_url="http://127.0.0.1:9/v1",
            api_key="qa-dummy", provider="custom", quiet_mode=True,
            enabled_toolsets=["skills"], platform="subagent",
            skip_context_files=True, skip_memory=True,
        )


def test_child_prompt_has_pointer_not_index(skills_home):
    child = _agent()
    assert "skill_view" in child.valid_tool_names
    child._skills_index_disabled = True
    prompt = child._build_system_prompt()
    assert "<available_skills>" not in prompt
    assert "report-finder" not in prompt
    assert "load it with skill_view(name)" in prompt


def test_regular_agent_keeps_full_index(skills_home):
    prompt = _agent()._build_system_prompt()
    assert "<available_skills>" in prompt
    assert "report-finder" in prompt
