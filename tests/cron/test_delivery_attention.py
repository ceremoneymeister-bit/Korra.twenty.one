"""K21-323/350: delivery failures remain separate from computation failures."""
import argparse
import json
import time
from pathlib import Path

import pytest

from cron import executions, incidents, jobs, scheduler
from gateway.session_context import (
    clear_session_vars, reset_background_owner, set_background_owner, set_session_vars,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "scripts").mkdir()
    (home / "config.yaml").write_text("platforms:\n  telegram:\n    enabled: false\n    home_channel:\n      platform: telegram\n      chat_id: '12345'\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("KORRA_HOME", str(home))
    monkeypatch.setattr(executions, "EXECUTIONS_FILE", None)
    monkeypatch.setattr(incidents, "EXECUTIONS_FILE", None)
    tokens = set_session_vars(platform="", chat_id="", cron_session="")
    owner_token = set_background_owner(True)
    try:
        with jobs.use_cron_store(home):
            yield home
    finally:
        reset_background_owner(owner_token)
        clear_session_vars(tokens)


def _tool(**args):
    from tools.cronjob_tools import _cronjob_handler
    tokens = set_session_vars(platform="", chat_id="", cron_session="")
    try:
        return json.loads(_cronjob_handler(args))
    finally:
        clear_session_vars(tokens)


def _attention(home):
    from korra_cli.dashboard_state import Agent, _cron_items
    return _cron_items([Agent("default", "test", home, "Test")], now=time.time(), tz=None)


def test_tool_create_and_update_warn_before_disconnected_delivery(store):
    created = _tool(action="create", reminder="Проверить отчёт", schedule="every 1h", deliver="telegram")
    assert created["success"]
    assert "telegram" in created.get("warning", "")
    assert "не будет доставлен" in created["warning"]
    assert "не подключён" in created["warning"]
    assert "Каналы" in created["warning"]
    assert "credentials" not in created["warning"]
    updated = _tool(action="update", job_id=created["job_id"], name="Проверка")
    assert updated["success"]
    assert "telegram" in updated.get("warning", "")
    assert "не подключён" in updated["warning"]
    assert "credentials" not in updated["warning"]
    assert "warning" not in jobs.get_job(created["job_id"])


def test_cli_create_and_edit_show_warning(store, capsys):
    from korra_cli.cron import cron_create, cron_edit
    assert cron_create(argparse.Namespace(schedule="every 1h", prompt=None,
        deliver="telegram", script=None, no_agent=False)) == 1  # no execution content
    script = store / "scripts" / "report.sh"
    script.write_text("#!/bin/sh\nprintf report")
    assert cron_create(argparse.Namespace(schedule="every 1h", prompt=None,
        deliver="telegram", script="report.sh", no_agent=True)) == 0
    output = capsys.readouterr().out
    assert "не будет доставлен" in output and "не подключён" in output
    assert "credentials" not in output and "hermes setup" not in output
    job = jobs.list_jobs()[0]
    assert cron_edit(argparse.Namespace(job_id=job["id"], name="Новое имя")) == 0
    output = capsys.readouterr().out
    assert "не будет доставлен" in output and "не подключён" in output
    assert "credentials" not in output and "hermes setup" not in output


def test_api_mutations_check_selected_profile_without_default_credentials(store, monkeypatch):
    from fastapi.testclient import TestClient
    from korra_cli import web_server as web
    profile = store / "profiles" / "reports"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("platforms: {}\n")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-default-token")
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "12345")
    monkeypatch.setattr(web, "_cron_profile_home", lambda p: ("reports", profile))
    monkeypatch.setattr(web, "_notify_cron_provider_for_profile", lambda p: None)
    # Real request/route/store chain; auth is irrelevant to delivery config.
    client = TestClient(web.app)
    monkeypatch.setattr(web, "_SESSION_TOKEN", "test-session-token")
    headers = {"Authorization": "Bearer test-session-token"}
    created = client.post("/api/cron/jobs?profile=reports", headers=headers,
        json={"reminder": "Проверить", "schedule": "every 1h", "deliver": "telegram"})
    assert created.status_code == 200, created.text
    job = created.json()
    assert "telegram" in job.get("warning", "")
    assert "не подключён" in job["warning"] and "credentials" not in job["warning"]
    updated = client.put(f"/api/cron/jobs/{job['id']}?profile=reports", headers=headers,
        json={"updates": {"name": "Проверка"}})
    assert updated.status_code == 200, updated.text
    assert "telegram" in updated.json().get("warning", "")
    assert "не подключён" in updated.json()["warning"]
    assert "credentials" not in updated.json()["warning"]
    # Credential belongs to this profile now; process token is still irrelevant.
    (profile / ".env").write_text("TELEGRAM_BOT_TOKEN=fake-profile-token\n")
    updated = client.put(f"/api/cron/jobs/{job['id']}?profile=reports", headers=headers,
        json={"updates": {"name": "Подключено"}})
    assert updated.status_code == 200
    assert not updated.json().get("warning")
    assert "warning" not in json.loads((profile / "cron" / "jobs.json").read_text())["jobs"][0]


@pytest.mark.parametrize("deliver", ["local", "origin", "all", "api_server:web-chat", "bot-chat", "bot-chat:default"])
def test_local_surfaces_do_not_get_channel_warning(store, deliver):
    assert scheduler.cron_delivery_warning({"deliver": deliver}) is None


def test_connected_and_relay_fronted_channels_do_not_warn(store, monkeypatch):
    (store / "config.yaml").write_text("platforms: {}\n")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-test-token")
    assert scheduler.cron_delivery_warning({"deliver": "telegram:12345"}) is None
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    monkeypatch.setenv("GATEWAY_RELAY_URL", "https://relay.invalid")
    monkeypatch.setenv("GATEWAY_RELAY_PLATFORMS", "telegram,slack")
    assert scheduler.cron_delivery_warning({"deliver": "telegram:12345,slack:678"}) is None


def test_origin_checks_its_platform_not_another_profile(store):
    assert "telegram" in scheduler.cron_delivery_warning({"deliver": "origin", "origin": {"platform": "telegram", "chat_id": "12345"}})
    assert scheduler.cron_delivery_warning({"deliver": "origin", "origin": {"platform": "api_server", "chat_id": "web-chat"}}) is None


def _script_job(home, content="report", **kwargs):
    script = home / "scripts" / "report.sh"
    script.write_text("#!/bin/sh\nprintf '%s' " + "'" + content.replace("'", "'\\''") + "'\n")
    return jobs.create_job(prompt=None, schedule="every 1h", no_agent=True,
        script="report.sh", deliver="telegram", created_by_owner=True, **kwargs)


def test_real_script_delivery_failure_dedup_ack_recovery(store):
    job = _script_job(store)
    compute_id, _ = incidents.upsert_incident(job["id"], "script previous failure")
    for _ in range(2):
        assert scheduler.run_one_job(jobs.get_job(job["id"])) is True
    saved = jobs.get_job(job["id"])
    assert saved["last_status"] == "delivery_failed"  # already supplied by K21-323
    assert saved["failure_streak"] == 0
    assert not saved["last_error"]
    attempts = executions.list_executions(job_id=job["id"])
    assert len(attempts) == 2
    assert all(r["status"] == "completed" and r["delivery_outcome"] == "failed" for r in attempts)
    delivery = [r for r in incidents.list_incidents() if r["failure_type"] == "delivery"]
    assert len(delivery) == 1
    row = delivery[0]
    assert row["state"] == "detected" and row["alerted_at"] is None
    assert row["output_file"] and Path(row["output_file"]).is_file()
    assert incidents.get_incident(compute_id)["state"] == "resolved"
    attention = _attention(store)
    assert len(attention) == 1
    assert "Проверьте доставку" in attention[0]["title"]
    assert "Результат вычислен, но не доставлен" in attention[0]["detail"]
    assert "не выполнилась" not in attention[0]["title"]
    # An empty script says nothing about delivery recovery.
    (store / "scripts" / "report.sh").write_text("#!/bin/sh\nexit 0\n")
    scheduler.run_one_job(jobs.get_job(job["id"]))
    assert incidents.get_incident(row["id"])["state"] == "detected"
    (store / "scripts" / "report.sh").write_text("#!/bin/sh\nprintf report\n")
    # Successful local history delivery, no network or messenger sends.
    from korra_state import SessionDB
    with SessionDB() as db:
        db.create_session(session_id="web-chat", source="api_server")
    jobs.update_job(job["id"], {"deliver": "api_server:web-chat"})
    scheduler.run_one_job(jobs.get_job(job["id"]))
    assert incidents.get_incident(row["id"])["state"] == "resolved"
    jobs.update_job(job["id"], {"deliver": "telegram"})
    scheduler.run_one_job(jobs.get_job(job["id"]))
    assert incidents.get_incident(row["id"])["state"] == "detected"
    incidents.ack_incident(row["id"])
    scheduler.run_one_job(jobs.get_job(job["id"]))
    assert incidents.get_incident(row["id"])["state"] == "closed"
    assert not _attention(store)
    assert len(executions.list_executions(job_id=job["id"])) == 6


def test_completed_one_shot_delivery_failure_is_in_attention(store):
    job = _script_job(store, repeat=1)
    scheduler.run_one_job(job)
    assert jobs.get_job(job["id"])["state"] == "completed"
    assert _attention(store), "Consumed one-shots still need their result delivered"


def test_missing_media_is_delivery_failure_with_existing_attention(store):
    from korra_state import SessionDB
    with SessionDB() as db:
        db.create_session(session_id="web-chat", source="api_server")
    missing = store / "missing-report.pdf"
    job = _script_job(store, content=f"Готов отчёт\nMEDIA:{missing}")
    jobs.update_job(job["id"], {"deliver": "api_server:web-chat"})
    scheduler.run_one_job(jobs.get_job(job["id"]))
    saved = jobs.get_job(job["id"])
    assert saved["last_status"] == "delivery_failed"
    assert saved["failure_streak"] == 0 and not saved["last_error"]
    run = executions.list_executions(job_id=job["id"])[0]
    assert run["status"] == "completed" and run["delivery_outcome"] == "failed"
    row = incidents.list_incidents()[0]
    assert row["failure_type"] == "delivery"
    assert "attachment" in row["error"] and "missing file" in row["error"]
    assert "Проверьте доставку" in _attention(store)[0]["title"]


def test_delivery_error_does_not_turn_into_subscription_incident(store):
    incidents.upsert_incident("job", "delivery to Telegram: authentication 401 codex-report", failure_type="delivery")
    row = incidents.list_incidents()[0]
    assert row["failure_type"] == "delivery"


@pytest.mark.parametrize("deliver, reason", [
    ("telegram:12345", "не подключ"),
    ("unknown-channel:12345", "неизвестный канал"),
])
def test_save_warning_names_the_russian_reason_and_profile_action(store, deliver, reason):
    warning = scheduler.cron_delivery_warning({"deliver": deliver})
    assert warning
    assert reason in warning.lower()
    assert "Каналы" in warning and "профил" in warning
    assert deliver.split(":", 1)[0] in warning
    assert all(word not in warning for word in ("credentials", "hermes setup", "deliver", "gateway"))


@pytest.mark.parametrize("execution_kind", ["computed", "script_error", "runner_exception"])
@pytest.mark.parametrize("outcome", ["unknown", "failed"])
def test_delivery_attention_tells_only_what_the_receipt_proves(store, monkeypatch, execution_kind, outcome):
    job = _script_job(store)
    if execution_kind == "script_error":
        (store / "scripts" / "report.sh").write_text("#!/bin/sh\nexit 1\n")
    elif execution_kind == "runner_exception":
        def raise_before_delivery(*args, **kwargs):
            raise RuntimeError("Тестовый сбой вычисления")
        monkeypatch.setattr(scheduler, "run_job", raise_before_delivery)
    sent = []
    error = "Статус отправки неизвестен; сначала проверьте получателя." if outcome == "unknown" else "Канал не подключён."

    def receipt(job, content, **kwargs):
        sent.append((content, kwargs))
        return (scheduler._DELIVERY_UNKNOWN_PREFIX if outcome == "unknown" else "") + error

    monkeypatch.setattr(scheduler, "_deliver_result", receipt)
    scheduler.run_one_job(job)
    run = executions.latest_execution(job["id"])
    assert run["status"] == ("completed" if execution_kind == "computed" else "failed")
    assert run["delivery_outcome"] == outcome
    assert run["delivery_error"] == error
    assert len(sent) == 1, "An uncertain receipt must not cause a blind second send"
    assert sent[0][1]["failure_notice"] is (execution_kind != "computed")
    assert len(executions.list_executions(job_id=job["id"])) == 1
    row = next(r for r in incidents.list_incidents() if r["failure_type"] == "delivery")
    assert row["state"] == "detected" and row["alerted_at"] is None
    item = next(i for i in _attention(store) if i["id"] == f"cron:default:{row['id']}")
    assert "доставк" in item["title"].lower()
    text = item["title"] + " " + item["detail"] + " " + row["error"]
    if outcome == "unknown":
        assert "не доставлен" not in text
        assert "не подтверждена" in text and "проверьте получателя" in text.lower()
    if execution_kind != "computed":
        assert "Результат" not in text
        assert "уведомлени" in text.lower()
    elif outcome == "failed":
        assert "Результат вычислен, но не доставлен" in text
