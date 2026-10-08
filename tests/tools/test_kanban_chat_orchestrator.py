"""The main agent plans on the board from chat (K21-142)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def chat_env(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    (home / "profiles" / "pm").mkdir(parents=True)
    (home / "profiles" / "pm" / "config.yaml").write_text("{}\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("KORRA_KANBAN_TASK", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from korra_cli import kanban_db as kb
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return home


def _mode(monkeypatch, value, profile="default"):
    from tools import kanban_tools as kt
    monkeypatch.setattr(kt, "load_config", lambda: {"kanban": {"chat_tools": value}}, raising=False)
    monkeypatch.setattr("korra_cli.config.load_config", lambda: {"kanban": {"chat_tools": value}})
    monkeypatch.setattr("korra_cli.profiles.get_active_profile_name", lambda: profile)
    return kt


def test_main_agent_gets_the_board_by_default(chat_env, monkeypatch):
    kt = _mode(monkeypatch, "main")
    assert kt._profile_has_kanban_toolset() is True
    assert kt._check_kanban_orchestrator_mode() is True


def test_specialist_profile_does_not_by_default(chat_env, monkeypatch):
    kt = _mode(monkeypatch, "main", profile="designer")
    assert kt._profile_has_kanban_toolset() is False


@pytest.mark.parametrize("value,profile,expected", [
    ("off", "default", False), ("all", "designer", True),
])
def test_chat_tools_setting(chat_env, monkeypatch, value, profile, expected):
    kt = _mode(monkeypatch, value, profile=profile)
    assert kt._profile_has_kanban_toolset() is expected


def test_unknown_agent_returns_the_list_of_agents(chat_env, monkeypatch):
    from tools import kanban_tools as kt
    monkeypatch.setattr(kt, "_known_profiles", lambda: [("default", "Корра"), ("pm", "Продакт-менеджер")])
    out = json.loads(kt._handle_create({"title": "t", "assignee": "manager"}))
    assert "unknown assignee" in out["error"]
    assert "pm (Продакт-менеджер)" in out["error"]
    ok = json.loads(kt._handle_create({"title": "t", "assignee": "pm", "plan_title": "План"}))
    assert ok["ok"] is True


def test_agent_cannot_mark_the_owners_step_done(chat_env):
    from tools import kanban_tools as kt
    from korra_cli import kanban_db as kb
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="Обучить команду", actor_kind="human")
    finally:
        conn.close()
    out = json.loads(kt._handle_complete({"task_id": tid, "summary": "обучил"}))
    assert "owner's own step" in out["error"]


def test_chat_agent_gets_chat_guidance_not_worker_protocol(monkeypatch):
    from agent.prompt_builder import KANBAN_CHAT_GUIDANCE, KANBAN_GUIDANCE
    assert "Plan in one pass" in KANBAN_CHAT_GUIDANCE
    assert "ONE task" not in KANBAN_CHAT_GUIDANCE
    assert len(KANBAN_CHAT_GUIDANCE) < 3000
    assert "full deliverable text in `result`" in KANBAN_GUIDANCE


def test_show_tells_the_chat_what_the_card_waits_for(chat_env):
    """The chat answers a board question with ``block_revision`` from
    kanban_show and must recognise a permission as the owner's decision.
    Live run 24.09: kanban_show carried neither, so the main agent offered to
    pass the owner's consent and guessed the revision from an error text."""
    from tools import kanban_tools as kt
    from korra_cli import kanban_db as kb
    conn = kb.connect()
    try:
        ask = kb.create_task(conn, title="Письмо", assignee="pm", acceptance="owner",
                             plan_id="p1", plan_title="План")
        crm = kb.create_task(conn, title="CRM", assignee="pm")
        own = kb.create_task(conn, title="Выбрать тему", actor_kind="human")
        for tid, kind in ((ask, "needs_input"), (crm, kb.APPROVAL_BLOCK_KIND)):
            kb.claim_task(conn, tid, claimer="w")
            assert kb.block_task(conn, tid, reason=f"вопрос {tid}", kind=kind)
        ask_rev = kb.block_revision(conn, ask)
    finally:
        conn.close()
    shown = json.loads(kt._handle_show({"task_id": ask}))["task"]
    assert shown["block_revision"] == ask_rev
    assert shown["block_kind"] == "needs_input"
    assert shown["block_reason"] == f"вопрос {ask}"
    assert shown["needs_approval"] is False
    assert (shown["acceptance"], shown["plan_id"], shown["plan_title"]) == ("owner", "p1", "План")
    assert json.loads(kt._handle_show({"task_id": crm}))["task"]["needs_approval"] is True
    assert json.loads(kt._handle_show({"task_id": own}))["task"]["actor_kind"] == "human"


def test_worker_is_told_what_to_do_after_a_refusal():
    """Live run 24.09: after «Не разрешать» the worker asked the owner again
    instead of handing in what it had prepared."""
    from agent.prompt_builder import KANBAN_GUIDANCE
    from korra_cli import kanban_db as kb
    assert "does not grant" in KANBAN_GUIDANCE
    assert "do not ask for the same permission again" in KANBAN_GUIDANCE
    import inspect
    assert "сдайте результат" in inspect.getsource(kb.respond_to_block)


def test_saved_scope_repeats_do_not_need_a_second_approval():
    from agent.prompt_builder import KANBAN_CHAT_GUIDANCE, KANBAN_GUIDANCE
    for instructions in (KANBAN_CHAT_GUIDANCE, KANBAN_GUIDANCE):
        assert "recipients" in instructions
        assert "first external action" in instructions
    assert "without another approval" in KANBAN_CHAT_GUIDANCE
    assert "need no new approval" in KANBAN_GUIDANCE
    assert "Never approve or accept on the owner's behalf" in KANBAN_CHAT_GUIDANCE
    assert "acceptance='auto'" in KANBAN_CHAT_GUIDANCE


def test_chat_comment_is_signed_by_the_chat_not_worker(chat_env, monkeypatch):
    """Live run 24.09: the main agent's comment appeared on the owner's card
    as «worker». A chat comment names the profile; the chat relays an answer
    through kanban_unblock alone."""
    from tools import kanban_tools as kt
    from korra_cli import kanban_db as kb
    from agent.prompt_builder import KANBAN_CHAT_GUIDANCE
    monkeypatch.delenv("KORRA_PROFILE", raising=False)
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    monkeypatch.setattr("korra_cli.profiles.get_active_profile_name", lambda: "default")
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="CRM", assignee="pm")
    finally:
        conn.close()
    assert json.loads(kt._handle_comment({"task_id": tid, "body": "заметка"}))["ok"] is True
    conn = kb.connect()
    try:
        assert kb.list_comments(conn, tid)[-1].author == "default (чат)"
    finally:
        conn.close()
    assert "add no separate comment" in KANBAN_CHAT_GUIDANCE


def test_block_from_chat_stops_the_running_executor(chat_env, monkeypatch):
    """The owner's word to stop is a pause: the executor's process ends too."""
    from tools import kanban_tools as kt
    from korra_cli import kanban_db as kb
    stopped = []
    monkeypatch.setattr(
        kb, "_terminate_reclaimed_worker",
        lambda pid, claim_lock, **_: stopped.append(pid) or {"terminated": True},
    )
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="CRM", assignee="pm")
        kb.claim_task(conn, tid, claimer="w")
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET worker_pid = 4242 WHERE id = ?", (tid,))
    out = json.loads(kt._handle_block({"task_id": tid, "reason": "Владелец просил остановить"}))
    assert out["ok"] is True and out["block_kind"] == kb.OWNER_PAUSE_KIND
    assert stopped == [4242]
    with kb.connect_closing() as conn:
        task = kb.get_task(conn, tid)
        assert (task.status, task.block_kind) == ("blocked", kb.OWNER_PAUSE_KIND)
        assert "worker_stopped" in [e.kind for e in kb.list_events(conn, tid)]


def test_owner_edit_from_chat_changes_what_the_judge_checks(chat_env, monkeypatch):
    """K21-298: the owner changes the criteria in the main chat; the judge
    accepts the handoff against the current description, not the old one."""
    from tools import kanban_tools as kt
    from korra_cli import kanban_db as kb

    seen = []

    def judge(goal, last_response, **_):
        seen.append(goal)
        ok = "без интеграции" in goal
        return ("done" if ok else "continue"), "нужна интеграция", False, None, False

    monkeypatch.setattr(kt, "judge_goal", judge)
    monkeypatch.setattr(kt, "_goal_judge_available", lambda: True)
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="CRM", assignee="pm", goal_mode=True,
                             body="Подключить CRM и проверить интеграцию.")
        kb.claim_task(conn, tid, claimer="w")

    out = json.loads(kt._handle_edit({"task_id": tid, "body": "Достаточно выгрузки, без интеграции."}))
    assert out["ok"] is True
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("KORRA_KANBAN_TASK", tid)
    done = json.loads(kt._handle_complete({"summary": "Выгрузка готова"}))
    assert done.get("ok") is True, done
    assert "без интеграции" in seen[-1] and "Подключить CRM" not in seen[-1]
    with kb.connect_closing() as conn:
        assert kb.get_task(conn, tid).status == "done"
        edited = [e for e in kb.list_events(conn, tid) if e.kind == "edited"]
        assert edited and edited[-1].payload["previous_body"] == "Подключить CRM и проверить интеграцию."


def test_edit_from_chat_refuses_finished_and_worker_calls(chat_env, monkeypatch):
    from tools import kanban_tools as kt
    from korra_cli import kanban_db as kb
    with kb.connect_closing() as conn:
        tid = kb.create_task(conn, title="CRM", assignee="pm", body="старое")
        kb.claim_task(conn, tid, claimer="w")
        kb.complete_task(conn, tid, result="готово")
    out = json.loads(kt._handle_edit({"task_id": tid, "body": "новое"}))
    assert "error" in out
    with kb.connect_closing() as conn:
        assert kb.get_task(conn, tid).body == "старое"
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("KORRA_KANBAN_TASK", tid)
    assert "error" in json.loads(kt._handle_edit({"task_id": tid, "body": "новое"}))
