"""Recipients other than the owner are confirmed once, at setup.

Dmitry, 28.09.2026: an automation the owner set up runs without
confirmations. Sending to somebody else needs that recipient confirmed once —
the owner picks them in the cabinet form or approves the card the agent shows.
A changing audience (clients with a birthday today) is confirmed as a source;
the job then messages them itself and reports whom.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cron import recipients

OWNER_DM = {"platform": "telegram", "chat_id": "42", "chat_type": "dm", "user_id": "42",
            "owner_principal": "live", "session_key": "agent:main:telegram:dm:42"}
EMPLOYEE_DM = {"platform": "telegram", "chat_id": "1220", "chat_type": "dm", "user_id": "1220"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    from cron import jobs as cron_jobs

    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setattr("tools.approval.notify_gateway_request", lambda *a, **k: True)
    monkeypatch.setattr("cron.scheduler._is_owner_side_chat",
                        lambda job, platform, chat_id: str(chat_id) == "42")
    for name in ("KORRA_SINGLE_QUERY_SESSION", "HERMES_SINGLE_QUERY_SESSION", "KORRA_ONESHOT_SESSION",
                 "KORRA_KANBAN_TASK", "HERMES_KANBAN_TASK"):
        monkeypatch.delenv(name, raising=False)
    with cron_jobs.use_cron_store(tmp_path):
        cron_jobs.ensure_dirs()
        yield tmp_path


def _tool(session: dict, **kw):
    from gateway.session_context import clear_session_vars, set_session_vars
    from tools.cronjob_tools import cronjob

    tokens = set_session_vars(cron_session="", **session)
    try:
        return json.loads(cronjob(**kw))
    finally:
        clear_session_vars(tokens)


def _resolve(decision_id: str, choice: str):
    from tools.effect_decisions import get_decision, resolve_effect_decision

    decision = get_decision(decision_id)
    return resolve_effect_decision(decision_id, choice,
                                   source_session_key=decision["source_session_key"])


def _job(job_id):
    from cron.jobs import get_job

    return get_job(job_id)


def test_agent_created_recipient_waits_for_one_confirmation(store):
    created = _tool(OWNER_DM, action="create", prompt="Отчёт для бухгалтера",
                    schedule="every day at 9am", deliver="telegram:999,origin")
    assert created["recipients"]["targets"] == ["telegram:999"]
    job = _job(created["job_id"])
    assert job["recipients_policy"] == 1
    assert recipients.delivery_allowed(job, "telegram", "42") is True        # the owner's own chat
    assert recipients.delivery_allowed(job, "telegram", "999") is False

    decision = _resolve(created["recipients"]["decision_id"], "once")
    assert decision["status"] == "succeeded"
    job = _job(created["job_id"])
    assert job["recipients_confirmed"]["targets"] == ["telegram:999"]
    assert not job.get("recipients_pending")
    assert recipients.delivery_allowed(job, "telegram", "999") is True


def test_a_denied_recipient_stays_unsent(store):
    created = _tool(OWNER_DM, action="create", prompt="Отчёт", schedule="every 1h",
                    deliver="telegram:999")
    _resolve(created["recipients"]["decision_id"], "deny")
    job = _job(created["job_id"])
    assert not job.get("recipients_pending")
    assert recipients.delivery_allowed(job, "telegram", "999") is False


def test_scheduled_delivery_holds_an_unconfirmed_recipient(store):
    from cron.scheduler import _deliver_result
    from gateway.config import Platform

    created = _tool(OWNER_DM, action="create", prompt="Отчёт", schedule="every 1h",
                    deliver="telegram:999,telegram:42")
    cfg = MagicMock()
    cfg.platforms = {Platform.TELEGRAM: MagicMock(enabled=True)}
    send = AsyncMock(return_value={"success": True})
    with (patch("gateway.config.load_gateway_config", return_value=cfg),
          patch("tools.send_message_tool._send_to_platform", new=send)):
        error = _deliver_result(_job(created["job_id"]), "Отчёт")
    assert "Получатель ещё не подтверждён: telegram:999" in (error or "")
    assert send.await_count == 1  # only the owner's own chat


def test_jobs_before_the_policy_keep_their_recipients_and_confirm_only_additions(store):
    from cron.jobs import create_job

    legacy = create_job(prompt="Отчёт", schedule="every 1h", deliver="telegram:999",
                        created_by_owner=True)
    assert recipients.delivery_allowed(legacy, "telegram", "999") is True

    changed = _tool(OWNER_DM, action="update", job_id=legacy["id"],
                    deliver="telegram:999,telegram:888")
    assert changed["recipients"]["targets"] == ["telegram:888"]
    job = _job(legacy["id"])
    assert recipients.delivery_allowed(job, "telegram", "999") is True
    assert recipients.delivery_allowed(job, "telegram", "888") is False


def test_cabinet_form_selection_is_the_confirmation(store):
    from cron.jobs import create_job

    job = create_job(prompt="Отчёт", schedule="every 1h", deliver="telegram:999",
                     created_by_owner=True)
    assert "recipients_policy" not in job
    assert recipients.delivery_allowed(job, "telegram", "999") is True


def test_a_confirmed_audience_lets_the_job_message_people_it_finds(store):
    created = _tool(OWNER_DM, action="create", prompt="Поздравь клиентов с днём рождения",
                    schedule="every day at 10am", deliver="origin",
                    audience="клиенты с днём рождения сегодня, из Bitrix")
    assert created["recipients"]["audience"] == "клиенты с днём рождения сегодня, из Bitrix"
    job = _job(created["job_id"])

    token = recipients.bind_running_job(job)
    try:
        assert recipients.send_allowed_in_run("telegram", "555") is False
    finally:
        recipients.reset_running_job(token)
    assert recipients.audience_run_note(job) == ""

    _resolve(created["recipients"]["decision_id"], "once")
    job = _job(created["job_id"])
    token = recipients.bind_running_job(job)
    try:
        assert recipients.send_allowed_in_run("telegram", "555") is True
    finally:
        recipients.reset_running_job(token)
    note = recipients.audience_run_note(job)
    assert "клиенты с днём рождения сегодня" in note and "list of people you messaged" in note


def test_somebody_else_cannot_give_an_automation_an_audience(store):
    result = _tool(EMPLOYEE_DM, action="create", prompt="Напиши всем", schedule="every 1h",
                   audience="все клиенты")
    assert result["success"] is False and "Only the owner" in result["error"]


def test_the_card_names_the_automation_and_its_recipients(store):
    from tools.effect_decisions import approval_payload, get_decision

    created = _tool(OWNER_DM, action="create", prompt="Отчёт", schedule="every day at 9am",
                    name="Отчёт бухгалтеру", deliver="telegram:999")
    card = approval_payload(get_decision(created["recipients"]["decision_id"]))
    assert card["decision_kind"] == "automation_recipients"
    assert "Отчёт бухгалтеру" in card["command"] and "telegram:999" in card["command"]
    assert card["choices"] == ["once", "deny"]
