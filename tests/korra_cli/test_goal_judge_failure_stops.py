"""Narrow witness from upstream c15af4055e / #91264."""
import pytest
from korra_cli import goals


def test_judge_transport_failure_never_starts_another_worker_turn(monkeypatch):
    monkeypatch.setattr(goals, "judge_goal", lambda *a, **kw:
                        ("wait", "judge unreachable", False, None, True))
    turns = []
    result = goals.run_kanban_goal_loop(
        task_id="t1", goal_text="finish", run_turn=lambda p: turns.append(p) or "x",
        task_status_fn=lambda: "running", block_fn=lambda r: pytest.fail(r),
        first_response="first attempt", max_turns=3,
    )
    assert result["outcome"] == "stopped"
    assert "judge transport failure" in result["reason"]
    assert turns == []
