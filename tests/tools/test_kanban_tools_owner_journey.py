"""Agent tools respect the owner journey (K21-141, K21-142)."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def worker_env(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "test-worker")
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    from pathlib import Path as _Path
    monkeypatch.setattr(_Path, "home", lambda: tmp_path)
    from korra_cli import kanban_db as kb
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="worker-test", assignee="test-worker", acceptance="owner")
        kb.claim_task(conn, tid)
    finally:
        conn.close()
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    return tid


def test_agent_completion_is_submitted_not_accepted(worker_env):
    from tools import kanban_tools as kt
    from korra_cli import kanban_db as kb
    out = json.loads(kt._handle_complete({"summary": "готово", "result": "полный текст"}))
    assert out["ok"] is True
    conn = kb.connect()
    try:
        task = kb.get_task(conn, worker_env)
        assert task.status == "review" and task.result == "полный текст"
    finally:
        conn.close()


def test_create_human_step_needs_no_assignee(worker_env):
    from tools import kanban_tools as kt
    from korra_cli import kanban_db as kb
    out = json.loads(kt._handle_create({
        "title": "Обучить команду",
        "actor_kind": "human",
        "plan_title": "Новая система ботов",
        "plan_id": "plan-bots",
    }))
    assert out["ok"] is True, out
    conn = kb.connect()
    try:
        task = kb.get_task(conn, out["task_id"])
        assert task.actor_kind == "human" and task.assignee is None
        assert task.plan_title == "Новая система ботов"
    finally:
        conn.close()


def test_create_agent_step_still_requires_assignee(worker_env):
    from tools import kanban_tools as kt
    out = json.loads(kt._handle_create({"title": "no owner"}))
    assert "assignee is required" in (out.get("error") or "")


def test_create_final_step_for_owner_acceptance(worker_env):
    from tools import kanban_tools as kt
    from korra_cli import kanban_db as kb
    out = json.loads(kt._handle_create({
        "title": "Итог", "assignee": "reviewer", "acceptance": "owner",
        "parents": [worker_env],
    }))
    conn = kb.connect()
    try:
        task = kb.get_task(conn, out["task_id"])
        assert task.acceptance == "owner" and task.status == "todo"
    finally:
        conn.close()
