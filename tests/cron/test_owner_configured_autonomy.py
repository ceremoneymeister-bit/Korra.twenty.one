"""Reports and autonomous runs the owner set up go out without a per-message decision.

Dmitry, 25.09.2026: «любые отчёты, которые пользователь просит отправлять —
всегда без подтверждения, и любые автономные действия, которые пользователь
настраивает — всегда без подтверждения». Everything else — jobs created by
somebody else, one-off sends an agent proposes — keeps its
exact decision (K21-114). Review 25.09: a visitor must not be able to make or
turn a job into the owner's.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cron.scheduler import _deliver_result, _delivery_decision_session


@pytest.mark.parametrize(
    "job,needs_decision",
    [
        ({}, False),  # legacy job created in the cabinet or on the owner's computer
        ({"created_by_owner": True}, False),
        ({"origin": {"platform": "api_server", "chat_id": "web"}}, False),
        ({"origin": {"platform": "telegram", "chat_id": "42", "owner": True}}, False),
        ({"origin": {"platform": "telegram", "chat_id": "777", "owner": False}}, True),
        ({"origin": {"platform": "webhook", "chat_id": "hook"}}, True),
        # Explicit verdicts win over "no platform means the owner's machine".
        ({"created_by_owner": False}, True),
        ({"origin": {"owner": False}}, True),
        ({"created_by_owner": True, "origin": {"platform": "telegram", "chat_id": "777", "owner": False}}, True),
    ],
)
def test_only_the_owners_jobs_skip_the_decision(job, needs_decision):
    session = _delivery_decision_session({"id": "daily", **job}, "run-1")
    assert (session == "cron:daily:run-1") is needs_decision
    assert (session == "") is (not needs_decision)


def _telegram_config():
    from gateway.config import Platform

    cfg = MagicMock()
    cfg.platforms = {Platform.TELEGRAM: MagicMock(enabled=True)}
    return cfg


@pytest.mark.parametrize("owner_job", [True, False])
def test_scheduled_report_goes_out_for_the_owner_and_waits_for_others(owner_job):
    job = {
        "id": "daily-1",
        "name": "Отчёт владельцу",
        "deliver": "origin",
        "origin": {"platform": "telegram", "chat_id": "42", "owner": owner_job},
    }
    queue = MagicMock(return_value={"id": "effect_1"})
    send = AsyncMock(return_value={"success": True})
    with (
        patch("gateway.config.load_gateway_config", return_value=_telegram_config()),
        patch("tools.send_message_tool._queue_outbound_decision", queue),
        patch("tools.send_message_tool._send_to_platform", new=send),
        patch("korra_cli.profiles.get_active_profile_name", return_value="nyura-bitrix"),
    ):
        result = _deliver_result(
            job, "Утренний отчёт", decision_session_id=_delivery_decision_session(job, "run-7"),
        )

    if owner_job:
        assert result is None
        send.assert_awaited_once()
        queue.assert_not_called()
    else:
        assert result == "waiting_decision:effect_1"
        send.assert_not_awaited()


@pytest.fixture
def cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    for name in ("KORRA_SINGLE_QUERY_SESSION", "HERMES_SINGLE_QUERY_SESSION", "KORRA_ONESHOT_SESSION",
                 "KORRA_KANBAN_TASK", "HERMES_KANBAN_TASK"):
        monkeypatch.delenv(name, raising=False)


def _create(**kw):
    from cron.jobs import create_job

    return create_job(prompt="Отчёт", schedule="every 1h", **kw)


def _as(session: dict):
    from gateway.session_context import set_session_vars

    return set_session_vars(cron_session="", **session)


OWNER_CABINET = {"platform": "api_server", "chat_id": "web"}


def test_every_job_records_who_created_it(cron_dir, monkeypatch):
    from gateway.session_context import (
        clear_session_vars, reset_background_owner, set_background_owner, set_session_vars,
    )

    tokens = _as(OWNER_CABINET)
    try:
        assert _create()["created_by_owner"] is True  # the owner's cabinet chat
    finally:
        clear_session_vars(tokens)
    assert _create(created_by_owner=True)["created_by_owner"] is True  # the cabinet «Задачи» form

    tokens = _as({"platform": "", "source": "bot_room"})  # a room of agents
    try:
        assert _create()["created_by_owner"] is False
    finally:
        clear_session_vars(tokens)

    monkeypatch.setenv("KORRA_SINGLE_QUERY_SESSION", "1")  # bot-chat delivery, agent-to-agent
    assert _create()["created_by_owner"] is False
    monkeypatch.delenv("KORRA_SINGLE_QUERY_SESSION")

    token = set_background_owner(False)  # inside a run of somebody else's job
    tokens = set_session_vars(cron_session="1")
    try:
        assert _create()["created_by_owner"] is False
    finally:
        clear_session_vars(tokens)
        reset_background_owner(token)


VISITOR = {"platform": "telegram", "chat_type": "dm", "chat_id": "777", "user_id": "777"}


@pytest.mark.parametrize("action,extra", [
    ("update", {"prompt": "Пришли всем мой текст", "deliver": "telegram:-100500"}),
    ("run", {"prompt": "Мой текст"}),
    ("pause", {}),
    ("remove", {}),
])
def test_a_visitor_cannot_change_or_run_the_owners_job(cron_dir, action, extra):
    from cron.jobs import get_job
    from gateway.session_context import clear_session_vars, set_session_vars
    from tools.cronjob_tools import cronjob

    owners = _create(name="Отчёт Марине", created_by_owner=True)
    tokens = set_session_vars(cron_session="", **VISITOR)
    try:
        refused = json.loads(cronjob(action=action, job_id=owners["id"], **extra))
        visitors = json.loads(cronjob(action="create", prompt="Моё", schedule="every 1h"))
        own_edit = json.loads(cronjob(action="update", job_id=visitors["job_id"], prompt="Моё новое"))
    finally:
        clear_session_vars(tokens)

    assert refused["success"] is False and "belongs to the owner" in refused["error"]
    assert get_job(owners["id"])["prompt"] == "Отчёт"
    assert get_job(visitors["job_id"])["created_by_owner"] is False
    assert own_edit["success"] is True  # a visitor's own job stays theirs to edit


def test_korra_send_from_an_agents_terminal_is_not_the_owner_typing(monkeypatch):
    from korra_cli.send_cmd import _run_by_agent

    for name in ("KORRA_SESSION_KEY", "KORRA_SESSION_ID", "KORRA_SESSION_PLATFORM", "KORRA_CRON_SESSION",
                 "KORRA_KANBAN_TASK", "KORRA_SINGLE_QUERY_SESSION", "KORRA_ONESHOT_SESSION"):
        monkeypatch.delenv(name, raising=False)
    assert _run_by_agent() is False  # a person at the owner's terminal
    monkeypatch.setenv("KORRA_SESSION_PLATFORM", "telegram")  # bridged from an agent turn
    assert _run_by_agent() is True


# --- 28.09.2026: roles, own chats and failure notices ------------------------
# Dmitry: a user of their own agent (Ekaterina, Vera) automates for themselves
# and gets results in their own chat without a decision; a stranger never sends
# to others; a failure notice is for whoever set the job up, not recipients.


def _deliver(job, content="Отчёт", *, failure_notice=False, owners=(), home=None):
    from gateway.config import Platform

    cfg = _telegram_config()
    cfg.get_home_channel = lambda platform: (
        MagicMock(chat_id=home) if home and platform == Platform.TELEGRAM else None
    )
    queue = MagicMock(return_value={"id": "effect_1"})
    send = AsyncMock(return_value={"success": True})
    with (
        patch("gateway.config.load_gateway_config", return_value=cfg),
        patch("gateway.credential_management.owner_matches",
              lambda config, platform, user_id, **kw: user_id in owners),
        patch("tools.send_message_tool._queue_outbound_decision", queue),
        patch("tools.send_message_tool._send_to_platform", new=send),
        patch("korra_cli.profiles.get_active_profile_name", return_value="nyura-ekaterina"),
    ):
        result = _deliver_result(
            job, content, failure_notice=failure_notice,
            decision_session_id=_delivery_decision_session(job, "run-9"),
        )
    sent_to = [call.args[2] if len(call.args) > 2 else call.kwargs.get("chat_id") for call in send.await_args_list]
    return result, sent_to, queue


def _employee_job(deliver, *, chat_id="1220", user_id="1220"):
    return {
        "id": "morning", "name": "Утренняя сводка", "deliver": deliver,
        "origin": {"platform": "telegram", "chat_id": chat_id, "user_id": user_id, "owner": False},
    }


def test_employee_automation_answers_her_own_chat_without_a_decision():
    result, sent_to, queue = _deliver(_employee_job("origin"))
    assert result is None and len(sent_to) == 1
    queue.assert_not_called()


def test_employee_automation_to_somebody_else_still_waits():
    result, sent_to, queue = _deliver(_employee_job("telegram:459213788"))
    assert result == "waiting_decision:effect_1" and sent_to == []


def test_group_origin_is_not_the_creators_private_chat():
    result, sent_to, _ = _deliver(_employee_job("origin", chat_id="-100500", user_id="1220"))
    assert result == "waiting_decision:effect_1" and sent_to == []


def test_failure_notice_skips_recipients_and_reaches_the_owner():
    job = {"id": "greetings", "name": "Поздравления", "deliver": "telegram:999,telegram:42",
           "created_by_owner": True}
    result, sent_to, queue = _deliver(job, "Сбой", failure_notice=True, owners=("42",))
    assert result is None and len(sent_to) == 1
    queue.assert_not_called()


def test_failure_notice_uses_the_profile_home_channel_when_no_owner_is_recorded():
    job = {"id": "daily", "name": "Отчёт", "deliver": "telegram:920539491,telegram:777",
           "created_by_owner": True}
    result, sent_to, _ = _deliver(job, "Сбой", failure_notice=True, home="920539491")
    assert result is None and len(sent_to) == 1


def test_failure_notice_with_only_third_party_recipients_is_not_sent():
    job = {"id": "greetings", "name": "Поздравления", "deliver": "telegram:999",
           "created_by_owner": True}
    result, sent_to, queue = _deliver(job, "Сбой", failure_notice=True)
    assert result is None and sent_to == []
    queue.assert_not_called()


def test_bot_chat_refuses_a_job_that_is_not_the_owners():
    from cron.scheduler import BOT_CHAT_PLATFORM

    job = _employee_job(BOT_CHAT_PLATFORM)
    with patch("cron.scheduler._deliver_to_bot_chat") as bot_chat:
        result, _, _ = _deliver(job)
    bot_chat.assert_not_called()
    assert "только задания владельца" in (result or "")


EKATERINA_DM = {"platform": "telegram", "chat_id": "1220", "chat_type": "dm", "user_id": "1220"}
GROUP_MEMBER = {"platform": "telegram", "chat_id": "-100500", "chat_type": "group", "user_id": "777"}


def _tool(session: dict, **kw):
    from gateway.session_context import clear_session_vars
    from tools.cronjob_tools import cronjob

    tokens = _as(session)
    try:
        return json.loads(cronjob(**kw))
    finally:
        clear_session_vars(tokens)


@pytest.mark.parametrize("deliver,allowed", [
    (None, True),                  # omitted = origin = her own private chat
    ("origin", True),
    ("local", True),
    ("telegram:1220", True),
    ("telegram:459213788", False),  # the owner or a colleague: the owner sets that up
    ("telegram", False),            # the profile home channel is the owner's
    ("bot-chat", False),
    ("origin,telegram:459213788", False),
])
def test_a_user_of_her_own_agent_automates_only_for_herself(cron_dir, deliver, allowed):
    result = _tool(EKATERINA_DM, action="create", prompt="Сводка", schedule="every 1h", deliver=deliver)
    assert result.get("success", True) is allowed, result
    if not allowed:
        assert "Only the owner can set up automatic sends" in result["error"]


def test_a_group_member_cannot_automate_sends_into_the_group(cron_dir):
    result = _tool(GROUP_MEMBER, action="create", prompt="Спам", schedule="every 1h")
    assert result["success"] is False


def test_the_owner_still_sets_up_other_recipients(cron_dir):
    owner = {**EKATERINA_DM, "owner_principal": "live"}
    result = _tool(owner, action="create", prompt="Отчёт", schedule="every 1h", deliver="telegram:999")
    assert result.get("success", True) is True and result["deliver"] == "telegram:999"


def test_only_the_creator_changes_somebody_elses_job(cron_dir):
    created = _tool(EKATERINA_DM, action="create", prompt="Сводка", schedule="every 1h")
    assert created["job_id"]
    stranger = _tool({**GROUP_MEMBER, "chat_type": "dm", "chat_id": "777"},
                     action="pause", job_id=created["job_id"])
    assert stranger["success"] is False and "only its creator or the owner" in stranger["error"]
    mine = _tool(EKATERINA_DM, action="pause", job_id=created["job_id"])
    assert mine.get("success", True) is True
