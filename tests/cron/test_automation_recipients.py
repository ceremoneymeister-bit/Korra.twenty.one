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


#: ``korra send`` in a child process, as an agent's terminal runs it. Nothing
#: may leave from here: the local transport and the decision queue only count.
_CHILD_SEND = r"""
import argparse, json, sys
from unittest.mock import MagicMock, patch
from gateway.config import Platform
from korra_cli.send_cmd import cmd_send

cfg = MagicMock(platforms={Platform.TELEGRAM: MagicMock(enabled=True, token="fake")})
local, decisions = [], []
def dispatch(**kw):
    local.append(kw)
    return {"success": True}
def decide(**kw):
    decisions.append(kw)
    return {"id": "decision-in-terminal", "status": "pending"}
code = None
with patch("gateway.config.load_gateway_config", return_value=cfg), \
     patch("tools.send_message_tool._dispatch_resolved_send", side_effect=dispatch), \
     patch("tools.send_message_tool._queue_outbound_decision", side_effect=decide):
    try:
        cmd_send(argparse.Namespace(to="telegram:555", message="С днём рождения", file=None,
                                    subject=None, json=True, quiet=False, list=False))
    except SystemExit as exc:
        code = exc.code
print("RESULT " + json.dumps({"exit": code, "local": len(local), "decisions": len(decisions)}))
"""


def _run_child(store, extra_env: dict, script: str = _CHILD_SEND) -> dict:
    import os
    import subprocess
    import sys

    env = {**os.environ, "KORRA_HOME": str(store), "HERMES_HOME": str(store),
           "KORRA_CRON_SESSION": "1", "PYTHONDONTWRITEBYTECODE": "1", "API_SERVER_KEY": "k",
           **extra_env}
    for name in ("KORRA_CRON_RUN_TOKEN", "HERMES_CRON_RUN_TOKEN"):
        if name not in extra_env:
            env.pop(name, None)
    out = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                         text=True, timeout=120)
    lines = [line for line in out.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, out.stdout + out.stderr
    return json.loads(lines[-1][len("RESULT "):])


def test_korra_send_in_a_running_jobs_terminal_is_sent_by_the_gateway(store):
    """Re-check A (round 3): the terminal hands the send to the gateway that
    issued the run's secret; the gateway checks it and the confirmed audience
    and sends itself — exactly once. A made-up secret, a public id or a
    finished run send nothing and the terminal asks the owner instead."""
    import http.server
    import threading

    from cron import executions
    from gateway.config import Platform

    created = _tool(OWNER_DM, action="create", prompt="Поздравления", schedule="every day at 10am",
                    audience="клиенты с ДР")
    _resolve(created["recipients"]["decision_id"], "once")
    job = _job(created["job_id"])
    sent: list[dict] = []

    class Gateway(http.server.BaseHTTPRequestHandler):
        """The gateway's /api/cron/run-send, answered from this process."""

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            ok = self.path == recipients.SEND_ROUTE and self.headers.get("Authorization") == "Bearer k"
            grant = recipients.lookup_live_run(body.get("token", ""), profile="default") if ok else None
            result = recipients.send_for_live_run(grant, body["target"], body["message"]) if grant else None
            status = 404 if result is None else 409 if result.get("status") == "not_confirmed" else 200
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(result or {}).encode())

        def log_message(self, *args):
            pass

    cfg = MagicMock(platforms={Platform.TELEGRAM: MagicMock(enabled=True, token="fake")})

    def gateway_dispatch(**kw):
        sent.append(kw)
        return {"success": True, "message_id": "7"}

    server = http.server.HTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    at = {"API_SERVER_PROXY_TARGET": f"http://127.0.0.1:{server.server_port}"}
    attempt = executions.create_execution(job["id"], source="scheduled")
    executions.mark_execution_running(attempt["id"])
    token = recipients.issue_run_token({**job, "execution_id": attempt["id"]})
    try:
        with patch("gateway.config.load_gateway_config", return_value=cfg), \
             patch("tools.send_message_tool._dispatch_resolved_send", side_effect=gateway_dispatch):
            assert _run_child(store, {**at, "KORRA_CRON_RUN_TOKEN": token}) == \
                {"exit": 0, "local": 0, "decisions": 0}
            assert [(item["chat_id"], item["cleaned_message"]) for item in sent] == \
                [("555", "С днём рождения")]
            for extra in ({"KORRA_CRON_RUN_TOKEN": "made-up"}, {"KORRA_CRON_JOB_ID": job["id"]}):
                assert _run_child(store, {**at, **extra}) == {"exit": 1, "local": 0, "decisions": 1}
            executions.finish_execution(attempt["id"], success=True)
            assert _run_child(store, {**at, "KORRA_CRON_RUN_TOKEN": token}) == \
                {"exit": 1, "local": 0, "decisions": 1}
        assert len(sent) == 1
    finally:
        recipients.retire_run_token(token)
        server.shutdown()
    assert recipients.lookup_live_run(token) is None


def test_korra_send_run_again_after_a_lost_answer_does_not_send_twice(store):
    """Astra round 5 R1: the gateway sent and the answer never came back
    (``outcome_unknown``); running ``korra send`` again in the same run gets
    the stored result — the recipient is not greeted twice."""
    import http.server
    import threading

    from cron import executions
    from gateway.config import Platform

    created = _tool(OWNER_DM, action="create", prompt="Поздравления", schedule="every day at 10am",
                    audience="клиенты с ДР")
    _resolve(created["recipients"]["decision_id"], "once")
    job = _job(created["job_id"])
    sent: list[dict] = []
    calls = {"n": 0}

    class Gateway(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            grant = recipients.lookup_live_run(body.get("token", ""), profile="default")
            result = recipients.send_once_for_live_run(body["token"], grant, body["target"], body["message"])
            calls["n"] += 1
            if calls["n"] == 1:
                self.close_connection = True  # sent, then the answer is lost
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        def log_message(self, *args):
            pass

    cfg = MagicMock(platforms={Platform.TELEGRAM: MagicMock(enabled=True, token="fake")})

    def gateway_dispatch(**kw):
        sent.append(kw)
        return {"success": True, "message_id": "7"}

    server = http.server.HTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    at = {"API_SERVER_PROXY_TARGET": f"http://127.0.0.1:{server.server_port}"}
    attempt = executions.create_execution(job["id"], source="scheduled")
    executions.mark_execution_running(attempt["id"])
    token = recipients.issue_run_token({**job, "execution_id": attempt["id"]})
    try:
        with patch("gateway.config.load_gateway_config", return_value=cfg), \
             patch("tools.send_message_tool._dispatch_resolved_send", side_effect=gateway_dispatch):
            lost = _run_child(store, {**at, "KORRA_CRON_RUN_TOKEN": token})
            assert lost == {"exit": 1, "local": 0, "decisions": 0}  # outcome unknown, no card
            again = _run_child(store, {**at, "KORRA_CRON_RUN_TOKEN": token})
            assert again == {"exit": 0, "local": 0, "decisions": 0}
        assert calls["n"] == 2
        assert [(item["chat_id"], item["cleaned_message"]) for item in sent] == [("555", "С днём рождения")]
    finally:
        recipients.retire_run_token(token)
        server.shutdown()


@pytest.mark.parametrize("answer", ["live_run", "send_result"])
def test_a_server_the_terminal_started_cannot_send_for_a_run(store, answer):
    """Astra round 3 §2.1: a process without the secret starts its own server
    and points ``korra send`` at it — here answering with the ids of a real
    live run whose audience the owner confirmed. Whatever it answers, the
    terminal sends nothing itself; the stand-in only receives the request."""
    from cron import executions

    created = _tool(OWNER_DM, action="create", prompt="Поздравления", schedule="every day at 10am",
                    audience="клиенты с ДР")
    _resolve(created["recipients"]["decision_id"], "once")
    attempt = executions.create_execution(created["job_id"], source="scheduled")
    executions.mark_execution_running(attempt["id"])
    fake = ({"job_id": created["job_id"], "execution_id": attempt["id"]} if answer == "live_run"
            else {"success": True})
    script = r"""
import http.server, json, os, threading
hits = []
class Fake(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        hits.append(self.path)
        self.send_response(200); self.end_headers()
        self.wfile.write(os.environ["FAKE_ANSWER"].encode())
    def log_message(self, *a): pass
server = http.server.HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["API_SERVER_PROXY_TARGET"] = f"http://127.0.0.1:{server.server_port}"
os.environ["API_SERVER_KEY"] = "invented-key-not-the-gateways"
""" + _CHILD_SEND + r"""
print("HITS " + str(len(hits)))
"""
    try:
        result = _run_child(store, {"KORRA_CRON_RUN_TOKEN": "review-forged",
                                    "FAKE_ANSWER": json.dumps(fake)}, script)
    finally:
        executions.finish_execution(attempt["id"], success=True)
    assert result["local"] == 0
    assert result["decisions"] == 0  # the request left: no second copy through a card
    # An answer without a send result is not taken for one.
    assert result["exit"] == (0 if answer == "send_result" else 1)


def test_an_unreachable_gateway_leaves_the_send_to_the_owner(store, monkeypatch, capsys):
    """Re-check A: after a gateway restart the secret is gone and nobody
    listens — ``korra send`` saves a real decision and exits 1, nothing is lost."""
    import argparse

    from gateway.config import Platform
    from gateway.session_context import clear_session_vars, set_session_vars
    from korra_cli.send_cmd import cmd_send
    from tools.effect_decisions import get_decision

    monkeypatch.setenv("API_SERVER_KEY", "local-test-key")
    monkeypatch.setenv("API_SERVER_PROXY_TARGET", "http://127.0.0.1:1")
    monkeypatch.setenv("KORRA_CRON_RUN_TOKEN", "unrecoverable-after-restart")
    monkeypatch.setenv("KORRA_CRON_SESSION", "1")
    cfg = MagicMock(platforms={Platform.TELEGRAM: MagicMock(enabled=True, token="fake")})
    tokens = set_session_vars(cron_session="1", **OWNER_DM)
    try:
        with patch("gateway.config.load_gateway_config", return_value=cfg), \
             patch("tools.send_message_tool._dispatch_resolved_send",
                   side_effect=AssertionError("nothing is sent from the terminal")):
            with pytest.raises(SystemExit) as exit_info:
                cmd_send(argparse.Namespace(to="telegram:555", message="local diagnostic",
                                            file=None, subject=None, json=True, quiet=False))
        assert exit_info.value.code == 1
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "pending_decision", output
        assert get_decision(output["decision_id"])["status"] == "pending"
    finally:
        clear_session_vars(tokens)


@pytest.mark.parametrize("status,expected", [(409, None), (404, None), (401, None),
                                             (500, "outcome_unknown")])
def test_what_the_terminal_does_with_each_gateway_answer(monkeypatch, status, expected):
    """4xx: refused, nothing was sent — ask the owner. 5xx: it may have been
    sent — report it, never queue a second copy."""
    import io
    import urllib.error

    from gateway.session_context import clear_session_vars, set_session_vars

    monkeypatch.setenv("API_SERVER_KEY", "k")

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, status, "x", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    from gateway.session_context import _VAR_MAP

    tokens = set_session_vars(cron_session="1", **OWNER_DM)
    secret = _VAR_MAP[recipients.RUNNING_JOB_ENV].set("secret")
    try:
        result = recipients.send_through_scheduler("telegram:555", "Привет")
    finally:
        _VAR_MAP[recipients.RUNNING_JOB_ENV].reset(secret)
        clear_session_vars(tokens)
    assert (result or {}).get("status") == expected


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


def test_a_delivery_edit_racing_an_audience_removal_keeps_it_removed(store, monkeypatch):
    """Re-check C (update path): a removal that lands after the tool read the
    job but before its write wins — the plan is recomputed from the job as it
    is under the store lock, never from the tool's earlier snapshot."""
    import cron.jobs as jobs_module

    created = _tool(OWNER_DM, action="create", prompt="Поздравления", schedule="every day at 10am",
                    audience="source A")
    _resolve(created["recipients"]["decision_id"], "once")
    real_mutate = jobs_module.mutate_job

    def removal_lands_first(job_id, compute):
        jobs_module.update_job(job_id, {"audience": None,
                                        "recipients_confirmed": {"targets": [], "audience": ""}})
        return real_mutate(job_id, compute)

    monkeypatch.setattr(jobs_module, "mutate_job", removal_lands_first)
    _tool(OWNER_DM, action="update", job_id=created["job_id"], deliver="origin")
    job = _job(created["job_id"])
    assert recipients.confirmed_audience(job) == ""
    token = recipients.bind_running_job(job)
    try:
        assert recipients.send_allowed_in_run("telegram", "555") is False
    finally:
        recipients.reset_running_job(token)


# --- Чистое ревью Astra: P1-2 (неопределённый исход, адрес) и P1-3 (маскирование)

SECRET_TEXT = "OPENAI_API_KEY=sk-" + "a" * 48


@pytest.fixture
def live_send(store, monkeypatch):
    from cron import executions, jobs
    from gateway.config import Platform

    job = jobs.create_job(prompt="review", schedule="every 1h", deliver="local",
                          created_by_owner=True, recipients_policy=1,
                          recipients_confirmed={"targets": ["telegram:555"]})
    execution = executions.create_execution(job["id"], source="scheduled")
    executions.mark_execution_running(execution["id"])
    token = recipients.issue_run_token({**job, "execution_id": execution["id"]})
    grant = recipients.lookup_live_run(token)
    assert grant
    cfg = MagicMock(platforms={Platform.TELEGRAM: MagicMock(enabled=True, token="fake")})
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: cfg)
    try:
        yield token, grant, cfg, job
    finally:
        recipients.retire_run_token(token)
        executions.finish_execution(execution["id"], success=True)


def _transport(monkeypatch, *answers):
    sent = []

    def dispatch(**kw):
        sent.append((kw["chat_id"], kw["cleaned_message"]))
        answer = answers[min(len(sent), len(answers)) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr("tools.send_message_tool._dispatch_resolved_send", dispatch)
    return sent


def test_a_send_the_service_may_have_taken_is_not_sent_again(live_send, monkeypatch):
    token, grant, _cfg, _job_ = live_send
    sent = _transport(monkeypatch, TimeoutError("accepted; acknowledgement lost"), {"success": True})
    first = recipients.send_once_for_live_run(token, grant, "telegram:555", "Birthday")
    again = recipients.send_once_for_live_run(token, grant, "telegram:555", "Birthday")
    assert first["status"] == "outcome_unknown" and first["success"] is False
    assert again["status"] == "outcome_unknown" and again["repeat"] is True
    assert "повторно не отправлялось" in again["error"]
    assert sent == [("555", "Birthday")]


def test_a_failure_the_transport_reports_is_an_unknown_outcome_too(live_send, monkeypatch):
    # Длинный текст уходит частями: ошибка второй части не значит, что
    # первая не дошла.
    token, grant, _cfg, _job_ = live_send
    sent = _transport(monkeypatch, {"error": "Telegram send failed: chunk 2/2 timed out"}, {"success": True})
    first = recipients.send_once_for_live_run(token, grant, "telegram:555", "Отчёт")
    again = recipients.send_once_for_live_run(token, grant, "telegram:555", "Отчёт")
    assert first["status"] == again["status"] == "outcome_unknown"
    assert len(sent) == 1


def test_a_refusal_before_sending_can_be_tried_again(live_send, monkeypatch):
    from gateway.config import Platform

    token, grant, cfg, _job_ = live_send
    sent = _transport(monkeypatch, {"success": True})
    cfg.platforms[Platform.TELEGRAM].enabled = False
    refused = recipients.send_once_for_live_run(token, grant, "telegram:555", "Birthday")
    assert refused.get("error") and refused.get("status") != "outcome_unknown" and sent == []
    cfg.platforms[Platform.TELEGRAM].enabled = True
    assert recipients.send_once_for_live_run(token, grant, "telegram:555", "Birthday")["success"] is True
    assert sent == [("555", "Birthday")]


def test_one_recipient_spelled_two_ways_is_one_send(live_send, monkeypatch):
    token, grant, _cfg, _job_ = live_send
    sent = _transport(monkeypatch, {"success": True, "message_id": "7"})
    assert recipients.send_once_for_live_run(token, grant, "TELEGRAM:555", "Birthday")["success"]
    again = recipients.send_once_for_live_run(token, grant, "telegram:555", "Birthday")
    assert again["repeat"] is True and len(sent) == 1


def test_a_send_for_a_run_goes_out_with_secrets_masked(live_send, monkeypatch):
    from agent.redact import redact_sensitive_text

    token, grant, _cfg, _job_ = live_send
    sent = _transport(monkeypatch, {"success": True})
    assert recipients.send_once_for_live_run(token, grant, "telegram:555", SECRET_TEXT)["success"]
    assert sent == [("555", redact_sensitive_text(SECRET_TEXT, force=True))]
    assert SECRET_TEXT not in sent[0][1]


def test_the_agents_own_send_inside_a_run_is_masked_as_well(live_send, monkeypatch):
    from tools.send_message_tool import send_message_tool

    _token, grant, _cfg, job = live_send
    sent = _transport(monkeypatch, {"success": True})
    bound = recipients.bind_running_job({**job, "execution_id": grant["execution_id"]})
    try:
        result = json.loads(send_message_tool({"action": "send", "target": "telegram:555",
                                               "message": SECRET_TEXT}))
    finally:
        recipients.reset_running_job(bound)
    assert result.get("success") is True
    assert len(sent) == 1 and SECRET_TEXT not in sent[0][1]


def test_the_result_reaches_other_people_masked_and_the_owner_as_is(store):
    from cron import jobs
    from cron.scheduler import _deliver_result
    from gateway.config import Platform

    job = jobs.create_job(prompt="Отчёт", schedule="every 1h", deliver="telegram:555,telegram:42",
                          created_by_owner=True, recipients_policy=1,
                          recipients_confirmed={"targets": ["telegram:555"]})
    cfg = MagicMock()
    cfg.platforms = {Platform.TELEGRAM: MagicMock(enabled=True)}
    send = AsyncMock(return_value={"success": True})
    with (patch("gateway.config.load_gateway_config", return_value=cfg),
          patch("tools.send_message_tool._send_to_platform", new=send)):
        assert _deliver_result(job, f"Ключ: {SECRET_TEXT}") is None
    texts = {str(call.args[2]): call.args[3] for call in send.await_args_list}
    assert SECRET_TEXT not in texts["555"]
    assert SECRET_TEXT in texts["42"]


def test_home_channel_shorthand_and_its_id_are_one_send(live_send, monkeypatch):
    """Второе чистое ревью Astra, P2-1: ``telegram`` уходит в домашний канал
    555 — тот же получатель, что ``telegram:555``, и повтор не уходит дважды."""
    token, grant, cfg, _job = live_send
    cfg.get_home_channel.return_value = MagicMock(chat_id="555")
    sent = _transport(monkeypatch, {"success": True, "message_id": "7"})
    first = recipients.send_once_for_live_run(token, grant, "telegram", "Birthday")
    again = recipients.send_once_for_live_run(token, grant, "telegram:555", "Birthday")
    assert first["success"] is True and again["success"] is True
    assert again.get("repeat") is True
    assert len(sent) == 1, sent


def test_the_owners_own_chat_needs_no_card_inside_a_run(store):
    """Канал владельца — не посторонний: отправка туда из запуска проходит
    без решения, как автодоставка результата (решение Дмитрия 28.09)."""
    from cron import jobs

    job = jobs.create_job(prompt="watchdog", schedule="every 1h", deliver="local",
                          created_by_owner=True, recipients_policy=1,
                          recipients_confirmed={"targets": ["telegram:555"]})
    bound = recipients.bind_running_job(job)
    try:
        assert recipients.send_allowed_in_run("telegram", "42") is True
        assert recipients.send_allowed_in_run("telegram", "555") is True
        assert recipients.send_allowed_in_run("telegram", "777") is False
    finally:
        recipients.reset_running_job(bound)


#: Скрипт задания без модели: печатает свой контекст и дважды шлёт постороннему.
_SCRIPT_SEND = r"""
import argparse, json, sys
sys.path.insert(0, REPO)
from unittest.mock import MagicMock, patch
from gateway.config import Platform
from gateway.session_context import get_session_env
from korra_cli.send_cmd import cmd_send, _run_by_agent

cfg = MagicMock(platforms={Platform.TELEGRAM: MagicMock(enabled=True, token="fake")})
local, decisions = [], []
def dispatch(**kw):
    local.append((kw["chat_id"], kw["cleaned_message"]))
    return {"success": True}
def decide(**kw):
    decisions.append(kw)
    return {"id": "decision-from-script", "status": "pending"}
context = {"run_by_agent": _run_by_agent(), "cron_session": get_session_env("KORRA_CRON_SESSION", ""),
           "has_token": bool(get_session_env("KORRA_CRON_RUN_TOKEN", ""))}
with patch("gateway.config.load_gateway_config", return_value=cfg), \
     patch("tools.send_message_tool._dispatch_resolved_send", side_effect=dispatch), \
     patch("tools.send_message_tool._queue_outbound_decision", side_effect=decide):
    for _ in range(2):
        try:
            cmd_send(argparse.Namespace(to="telegram:777", message="REVIEW_API_KEY=synthetic-only",
                                        file=None, subject=None, json=True, quiet=True, list=False))
        except SystemExit:
            pass
print("RESULT " + json.dumps({**context, "local": local, "decisions": len(decisions)}))
"""


def test_a_script_job_sends_through_the_same_boundary_as_an_agent(store, monkeypatch):
    """Второе чистое ревью Astra, P1-1: скрипт задания без модели — такая же
    автоматическая работа, как ход агента. Его ``korra send`` постороннему не
    уходит напрямую, а ждёт решения; секрет запуска живёт только пока идёт
    скрипт."""
    from pathlib import Path

    from cron import jobs, scheduler

    repo = str(Path(__file__).resolve().parents[2])
    monkeypatch.setenv("API_SERVER_PROXY_TARGET", "http://127.0.0.1:1")
    (store / "scripts").mkdir(exist_ok=True)
    (store / "scripts" / "probe.py").write_text(_SCRIPT_SEND.replace("REPO", repr(repo)))
    job = jobs.create_job(prompt="", schedule="every 1h", script="probe.py", no_agent=True,
                          deliver="local", created_by_owner=True, recipients_policy=1,
                          recipients_confirmed={"targets": ["telegram:555"]})

    ok, _doc, output, _error = scheduler.run_job(job, execution_id="exec-script-probe")

    assert ok, output
    line = [row for row in str(output).splitlines() if row.startswith("RESULT ")][-1]
    result = json.loads(line[len("RESULT "):])
    assert result["run_by_agent"] is True
    assert result["cron_session"] == "1" and result["has_token"] is True
    assert result["local"] == []  # ничего не ушло мимо шлюза
    assert result["decisions"] == 2
    assert recipients._LIVE_RUNS == {}  # секрет отозван вместе с концом скрипта
