"""A card from chat is executable: skills, time limit (K21-301, K21-304)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _skill(home: Path, name: str) -> None:
    d = home / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\nDo the thing.\n"
    )


@pytest.fixture
def env(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    for prof in ("default", "pm", "analyst"):
        (home / "profiles" / prof).mkdir(parents=True)
        (home / "profiles" / prof / "config.yaml").write_text("{}\n")
    _skill(home / "profiles" / "pm", "research-notes")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("KORRA_KANBAN_TASK", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from korra_cli import kanban_db as kb
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return home


def _create(**args):
    from tools import kanban_tools as kt
    return json.loads(kt._handle_create({"title": "t", **args}))


def test_skill_of_the_chosen_agent_is_checked_at_create(env):
    ok = _create(assignee="pm", skills=["research-notes"])
    assert ok["ok"] is True

    bad = _create(assignee="analyst", skills=["research-notes"])
    assert "cannot load skill" in bad["error"]
    assert "research-notes" in bad["error"]
    assert "Nothing was created" in bad["error"]

    from korra_cli import kanban_db as kb
    with kb.connect_closing() as conn:
        assert len(kb.list_tasks(conn)) == 1


def test_one_missing_skill_among_present_ones_is_reported(env):
    bad = _create(assignee="pm", skills=["research-notes", "ghost"])
    assert "ghost" in bad["error"]
    assert "research-notes" not in bad["error"].split("skill(s):")[1].split(".")[0]


def test_dispatcher_blocks_card_whose_skill_vanished_without_attempts(env):
    from korra_cli import kanban_db as kb

    with kb.connect_closing() as conn:
        tid = kb.create_task(
            conn, title="t", assignee="analyst", skills=["research-notes"],
        )
    spawned = []

    def spawn(task, workspace, **_):
        spawned.append(task.id)
        return 4242

    for _ in range(3):
        with kb.connect_closing() as conn:
            res = kb.dispatch_once(conn, spawn_fn=spawn)
    assert spawned == []
    assert res.spawned == []
    with kb.connect_closing() as conn:
        task = kb.get_task(conn, tid)
        assert task.status == "blocked"
        assert task.block_kind == "capability"
        assert "research-notes" in (task.block_reason or "")
        assert kb.list_runs(conn, tid) == [] or all(
            r.outcome == "blocked" for r in kb.list_runs(conn, tid)
        )
        assert task.consecutive_failures == 0


def test_dispatcher_still_starts_card_with_available_skill(env):
    from korra_cli import kanban_db as kb

    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="t", assignee="pm", skills=["research-notes"])
    spawned = []

    def spawn(task, workspace, **_):
        spawned.append(task.id)
        return 4242

    with kb.connect_closing() as conn:
        kb.dispatch_once(conn, spawn_fn=spawn)
    assert spawned == [tid]


def test_chat_card_gets_default_time_limit_and_it_is_shown(env):
    out = _create(assignee="pm")
    assert out["max_runtime_seconds"] == 1200
    from korra_cli import kanban_db as kb
    with kb.connect_closing() as conn:
        assert kb.get_task(conn, out["task_id"]).max_runtime_seconds == 1200


def test_smaller_explicit_limit_is_kept(env):
    out = _create(assignee="pm", max_runtime_seconds=300)
    assert out["max_runtime_seconds"] == 300


def test_worker_created_card_keeps_old_default(env, monkeypatch):
    monkeypatch.setenv("KORRA_KANBAN_TASK", "t_parent")
    out = _create(assignee="pm")
    assert out["max_runtime_seconds"] is None
