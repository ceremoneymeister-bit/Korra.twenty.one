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
    # The unconfirmed recipient is not in deliver and nothing runs before the answer.
    assert job["recipients_policy"] == 1
    assert job["deliver"] == "origin"
    assert job["enabled"] is False and job["paused_reason"] == recipients.PAUSE_REASON
    assert recipients.delivery_allowed(job, "telegram", "999") is False

    decision = _resolve(created["recipients"]["decision_id"], "once")
    assert decision["status"] == "succeeded"
    job = _job(created["job_id"])
    assert job["deliver"] == "telegram:999,origin"
    assert job["recipients_confirmed"]["targets"] == ["telegram:999"]
    assert not job.get("recipients_pending")
    assert job["enabled"] is True and job["state"] == "scheduled"
    assert recipients.delivery_allowed(job, "telegram", "999") is True


def test_a_denied_recipient_stays_unsent(store):
    created = _tool(OWNER_DM, action="create", prompt="Отчёт", schedule="every 1h",
                    deliver="telegram:999")
    _resolve(created["recipients"]["decision_id"], "deny")
    job = _job(created["job_id"])
    assert not job.get("recipients_pending")
    assert "999" not in job["deliver"]
    assert job["enabled"] is False and job["paused_reason"] == recipients.DENIED_REASON


def test_scheduled_delivery_never_reaches_an_unconfirmed_recipient(store):
    from cron.scheduler import _deliver_result
    from gateway.config import Platform

    created = _tool(OWNER_DM, action="create", prompt="Отчёт", schedule="every 1h",
                    deliver="telegram:999,telegram:42")
    job = _job(created["job_id"])
    cfg = MagicMock()
    cfg.platforms = {Platform.TELEGRAM: MagicMock(enabled=True)}
    send = AsyncMock(return_value={"success": True})
    with (patch("gateway.config.load_gateway_config", return_value=cfg),
          patch("tools.send_message_tool._send_to_platform", new=send)):
        error = _deliver_result(job, "Отчёт")
        # Even a hand-edited deliver is held back by the delivery guard.
        guarded = _deliver_result({**job, "deliver": "telegram:999"}, "Отчёт")
    assert error is None and send.await_count == 1  # only the owner's own chat
    assert "Получатель ещё не подтверждён: telegram:999" in (guarded or "")


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


# --- 0.21.15 Astra review of candidate ce8046b8e1 ----------------------------


def test_a_failed_card_leaves_a_changed_job_safe(store, monkeypatch):
    """P1-2: the new recipient and its guard are written together."""
    from cron.jobs import create_job

    legacy = create_job(prompt="Отчёт", schedule="every 1h", deliver="telegram:111",
                        created_by_owner=True)
    monkeypatch.setattr(recipients, "request_confirmation",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("store unavailable")))
    changed = _tool(OWNER_DM, action="update", job_id=legacy["id"], deliver="telegram:222")
    assert changed["recipients"]["status"] == "card_failed"
    job = _job(legacy["id"])
    assert "222" not in job["deliver"]
    assert job["enabled"] is False and job["recipients_pending"]["targets"] == ["telegram:222"]
    assert recipients.delivery_allowed(job, "telegram", "222") is False


def test_stored_jobs_are_safe_for_the_0_21_14_scheduler(store):
    """P1-3: an engine that ignores recipients_policy still finds nothing to send."""
    created = _tool(OWNER_DM, action="create", prompt="Отчёт", schedule="every 1h",
                    deliver="telegram:999")
    job = _job(created["job_id"])
    assert "telegram:999" not in str(job["deliver"])
    assert job["enabled"] is False and job["state"] == "paused"


def test_a_changed_audience_revokes_the_old_one_and_old_cards_do_not_apply(store):
    """P2-4: confirmation belongs to the version of the setting it was shown for."""
    created = _tool(OWNER_DM, action="create", prompt="Поздравления", schedule="every day at 10am",
                    audience="клиенты A")
    first = created["recipients"]["decision_id"]
    _resolve(first, "once")
    assert recipients.confirmed_audience(_job(created["job_id"])) == "клиенты A"

    changed = _tool(OWNER_DM, action="update", job_id=created["job_id"], audience="клиенты B")
    job = _job(created["job_id"])
    assert recipients.confirmed_audience(job) == ""       # A no longer authorises anything
    token = recipients.bind_running_job(job)
    try:
        assert recipients.send_allowed_in_run("telegram", "555") is False
    finally:
        recipients.reset_running_job(token)

    removed = _tool(OWNER_DM, action="update", job_id=created["job_id"], audience="")
    assert "recipients" not in removed
    from tools.effect_decisions import get_decision
    assert get_decision(changed["recipients"]["decision_id"])["status"] == "denied"
    job = _job(created["job_id"])
    assert not job.get("recipients_pending") and recipients.confirmed_audience(job) == ""


def test_an_old_card_approved_after_a_change_does_not_confirm_anything(store, monkeypatch):
    created = _tool(OWNER_DM, action="create", prompt="Отчёт", schedule="every 1h",
                    deliver="telegram:999")
    stale = created["recipients"]["decision_id"]
    # Keep the stale card pending to prove the version check, not the retirement.
    monkeypatch.setattr(recipients, "retire_card", lambda decision_id: None)
    _tool(OWNER_DM, action="update", job_id=created["job_id"], deliver="telegram:777")
    decision = _resolve(stale, "once")
    assert decision["status"] == "failed"
    job = _job(created["job_id"])
    assert recipients.delivery_allowed(job, "telegram", "999") is False
    assert "999" not in str(job["deliver"])


def test_choosing_recipients_in_the_cabinet_form_confirms_them(store):
    """P2-5: the owner's explicit choice in the form is the confirmation."""
    from cron.jobs import update_job

    created = _tool(OWNER_DM, action="create", prompt="Отчёт", schedule="every 1h",
                    deliver="telegram:999")
    before = _job(created["job_id"])["deliver"]
    update_job(created["job_id"], {"deliver": "telegram:555"})
    job = recipients.accept_owner_form_edit(created["job_id"], before)
    assert recipients.delivery_allowed(job, "telegram", "555") is True
    assert not job.get("recipients_pending")
    assert job["enabled"] is True


def test_a_one_shot_reminder_is_not_spent_before_the_answer(store):
    """P2-6: confirmed after its time, it goes out now instead of being lost."""
    from datetime import timedelta

    from cron.jobs import _hermes_now, update_job

    created = _tool(OWNER_DM, action="create", reminder="Позвонить поставщику", schedule="in 5m",
                    deliver="telegram:999")
    job = _job(created["job_id"])
    assert job["enabled"] is False
    past = (_hermes_now() - timedelta(hours=1)).isoformat()
    update_job(job["id"], {"schedule": {**job["schedule"], "run_at": past}, "next_run_at": None})
    _resolve(created["recipients"]["decision_id"], "once")
    job = _job(created["job_id"])
    assert job["enabled"] is True and job["deliver"] == "telegram:999"
    assert job["next_run_at"] is not None


def test_korra_send_in_a_running_jobs_terminal_uses_its_confirmation(store):
    """P1-1 / re-check A: a child process (``korra send`` in the job's
    terminal) holds this run's secret; a public job id grants nothing, and the
    secret works only while its exact execution runs."""
    import os
    import subprocess
    import sys

    from cron import executions

    created = _tool(OWNER_DM, action="create", prompt="Поздравления", schedule="every day at 10am",
                    audience="клиенты с ДР")
    _resolve(created["recipients"]["decision_id"], "once")
    job = _job(created["job_id"])
    child = (
        "from cron import recipients;"
        "job = recipients.running_job();"
        "print(job['id'] if job else '-', recipients.send_allowed_in_run('telegram', '555'))"
    )
    base_env = {**os.environ, "KORRA_HOME": str(store), "HERMES_HOME": str(store),
                "KORRA_CRON_SESSION": "1", "PYTHONDONTWRITEBYTECODE": "1"}

    def ask(**extra) -> list:
        return subprocess.run([sys.executable, "-c", child], env={**base_env, **extra},
                              capture_output=True, text=True, check=True, timeout=120).stdout.split()[-2:]

    attempt = executions.create_execution(job["id"], source="scheduled")
    executions.mark_execution_running(attempt["id"])
    token = recipients.issue_run_token({**job, "execution_id": attempt["id"]})
    try:
        assert ask(KORRA_CRON_JOB_ID=job["id"]) == ["-", "False"]      # an id is public
        assert ask(KORRA_CRON_RUN_TOKEN="forged") == ["-", "False"]
        assert ask(KORRA_CRON_RUN_TOKEN=token) == [job["id"], "True"]
        executions.finish_execution(attempt["id"], success=True)
        assert ask(KORRA_CRON_RUN_TOKEN=token) == ["-", "False"]       # that run is over
    finally:
        recipients.retire_run_token(token)
    assert not list((store / "cron" / "run-tokens").glob("*.json"))


def test_resume_run_now_and_forced_fire_wait_for_the_answer(store):
    """Re-check B: nothing spends the run before the owner answers."""
    from cron.jobs import claim_job_for_fire, resume_job, trigger_job

    created = _tool(OWNER_DM, action="create", reminder="Позвонить поставщику", schedule="in 30m",
                    deliver="telegram:999")
    job_id = created["job_id"]
    for attempt in (lambda: resume_job(job_id), lambda: trigger_job(job_id)):
        with pytest.raises(ValueError, match="ждёт подтверждения получателей"):
            attempt()
    assert claim_job_for_fire(job_id, force=True) is False
    job = _job(job_id)
    assert job["enabled"] is False and job["recipients_pending"]["targets"] == ["telegram:999"]


def test_an_answer_racing_a_change_does_not_restore_the_old_setting(store, monkeypatch):
    """Re-check C: the version check and the write happen under one lock."""
    from tools import effect_decisions

    created = _tool(OWNER_DM, action="create", prompt="Поздравления", schedule="every day at 10am",
                    audience="source A")
    real_claim = effect_decisions.claim_execution

    def claim_then_change(decision_id, **kw):
        result = real_claim(decision_id, **kw)
        _tool(OWNER_DM, action="update", job_id=created["job_id"], audience="")
        return result

    monkeypatch.setattr(effect_decisions, "claim_execution", claim_then_change)
    decision = _resolve(created["recipients"]["decision_id"], "once")
    assert decision["status"] == "failed"
    assert recipients.confirmed_audience(_job(created["job_id"])) == ""


def test_a_job_created_by_an_automation_for_its_own_chat_keeps_delivering(store):
    """Re-check D: the inherited confirmation is stored with the new job."""
    from gateway.session_context import _VAR_MAP, set_background_owner, reset_background_owner

    owner_token = set_background_owner(True)
    auto = [(_VAR_MAP[name], _VAR_MAP[name].set(value)) for name, value in (
        ("KORRA_CRON_AUTO_DELIVER_PLATFORM", "telegram"), ("KORRA_CRON_AUTO_DELIVER_CHAT_ID", "999"))]
    try:
        from gateway.session_context import clear_session_vars, set_session_vars
        from tools.cronjob_tools import cronjob

        tokens = set_session_vars(platform="", chat_id="", cron_session="1")
        try:
            created = json.loads(cronjob(action="create", prompt="Дочерняя проверка", schedule="every 1h"))
        finally:
            clear_session_vars(tokens)
    finally:
        for var, token in reversed(auto):
            var.reset(token)
        reset_background_owner(owner_token)
    job = _job(created["job_id"])
    assert job["deliver"] == "telegram:999" and job["enabled"] is True
    assert recipients.delivery_allowed(job, "telegram", "999") is True


def test_saving_the_form_without_changing_recipients_keeps_the_wait(store):
    """Re-check E: the form re-sends deliver; only a real change counts, and it
    never cancels a pending audience."""
    from cron.jobs import update_job

    created = _tool(OWNER_DM, action="create", prompt="Поздравления", schedule="every day at 10am",
                    deliver="telegram:999", audience="клиенты с ДР")
    job = _job(created["job_id"])
    update_job(job["id"], {"name": "Поздравления клиентов", "deliver": job["deliver"]})
    after = recipients.accept_owner_form_edit(job["id"], job["deliver"])
    assert after is None
    assert _job(job["id"])["recipients_pending"]["targets"] == ["telegram:999"]

    update_job(job["id"], {"deliver": "telegram:555"})
    changed = recipients.accept_owner_form_edit(job["id"], job["deliver"])
    assert recipients.delivery_allowed(changed, "telegram", "555") is True
    assert changed["recipients_pending"]["audience"] == "клиенты с ДР"
    assert changed["recipients_pending"]["targets"] == []
    assert changed["enabled"] is False
