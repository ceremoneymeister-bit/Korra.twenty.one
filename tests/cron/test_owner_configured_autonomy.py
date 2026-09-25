"""Reports and autonomous runs the owner set up go out without a per-message decision.

Dmitry, 25.09.2026: «любые отчёты, которые пользователь просит отправлять —
всегда без подтверждения, и любые автономные действия, которые пользователь
настраивает — всегда без подтверждения». Everything else — jobs created by
somebody else, one-off sends an agent proposes, failure notices — keeps its
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
