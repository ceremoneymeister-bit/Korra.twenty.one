"""K21-350: a lost subscription uses the real cron ledger and owner delivery."""
import base64
import json
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from pathlib import Path

import pytest

from cron import executions, incidents, jobs, scheduler
from korra_cli import dashboard_state as dashboard
from korra_cli.auth import AuthError
from korra_time import now

pytestmark = pytest.mark.usefixtures("no_real_network")


@pytest.fixture
def home(tmp_path, monkeypatch):
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: user_home)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setattr(scheduler, "_hermes_home", tmp_path)
    (tmp_path / "config.yaml").write_text(
        "model:\n  provider: openai-codex\n  default: gpt-test\n"
        "platforms:\n  telegram:\n    enabled: true\n    token: fake-test-token\n"
        "cron:\n  wrap_response: false\n"
    )
    with jobs.use_cron_store(tmp_path):
        yield tmp_path


def make_job(deliver="origin", **kw):
    return jobs.create_job(
        prompt="Отчёт", schedule="every 1d", model="gpt-test", provider="openai-codex",
        deliver=deliver, origin={"platform": "telegram", "chat_id": "42", "user_id": "42", "chat_type": "dm"},
        **kw,
    )


def attention(home):
    agent = dashboard.Agent(profile=None, name="default", home=home, label="Корра")
    return dashboard.attention_section([agent], now=now().timestamp(), tz=now().tzinfo, quota={"available": False})["items"]


def tick(job):
    assert scheduler.run_one_job(jobs.get_job(job["id"]))


@pytest.mark.parametrize("deliver", ["origin", "bot-chat", "telegram:999", "local"])
def test_missing_login_real_resolution_one_notice_and_recovery(home, deliver):
    # No auth.json: exercise real provider/auth/preflight imports, not a mocked verdict.
    first = make_job(deliver, name="Первый отчёт")
    second = make_job(deliver, name="Второй отчёт")
    send = AsyncMock(return_value={"success": True, "message_id": "notice-1"})
    with patch("tools.send_message_tool._send_to_platform", send), \
         patch("run_agent.AIAgent", side_effect=AssertionError("expired login must not run a model")):
        for job in (first, second, first):
            tick(job)
    rows = incidents.list_incidents()
    assert len(rows) == 1
    assert rows[0]["failure_type"] == "subscription:openai-codex"
    assert rows[0]["state"] == ("detected" if deliver == "local" else "alerted")
    assert bool(rows[0]["alerted_at"]) == (deliver != "local")
    assert jobs.get_job(first["id"])["last_status"] == "blocked_config"
    assert send.await_count == (0 if deliver == "local" else 1)
    if deliver != "local":
        assert send.call_args.args[2] == "42"  # no client/agent delivery
        text = send.call_args.args[3]
        assert "Вход в подписку ChatGPT" in text and "korra model" in text
    signals = [item for item in attention(home) if item["kind"] == "provider_refused"]
    assert len(signals) == 1 and signals[0]["href"] == "/models"

    # A healthy fallback/another provider must not erase the failed subscription.
    incidents.close_incidents_for_recovered_job(first["id"], provider="anthropic")
    assert incidents.list_incidents()[0]["state"] != "resolved"

    # Real saved credentials now resolve; only model execution is replaced.
    payload = base64.urlsafe_b64encode(json.dumps({"exp": (now() + timedelta(days=10)).timestamp()}).encode()).decode().rstrip("=")
    (home / "auth.json").write_text(json.dumps({"providers": {"openai-codex": {"tokens": {
        "access_token": f"fake.{payload}.fake", "refresh_token": "fake-test-refresh",
    }}}}))
    with patch("run_agent.AIAgent") as cls, patch("tools.send_message_tool._send_to_platform", send):
        cls.return_value.run_conversation.return_value = {"final_response": "[SILENT]"}
        tick(second)
    assert incidents.list_incidents()[0]["state"] == "resolved"
    assert not [item for item in attention(home) if item["kind"] == "provider_refused"]
    assert executions.latest_execution(second["id"])["status"] == "completed"

    (home / "auth.json").unlink()
    with patch("tools.send_message_tool._send_to_platform", send), \
         patch("run_agent.AIAgent", side_effect=AssertionError("no model")):
        tick(first)
    assert len(incidents.list_incidents()) == 1
    assert send.await_count == (0 if deliver == "local" else 2)


def test_failed_delivery_does_not_silence_the_next_attempt(home):
    job = make_job()
    send = AsyncMock(side_effect=[{"success": False, "error": "transport offline"}, {"success": True}])
    with patch("tools.send_message_tool._send_to_platform", send):
        tick(job)
        assert incidents.list_incidents()[0]["state"] == "detected"
        assert incidents.list_incidents()[0]["alerted_at"] is None
        tick(job)
        tick(job)
    assert send.await_count == 2
    assert incidents.list_incidents()[0]["state"] == "alerted"


def test_legacy_preflight_flag_cannot_hide_subscription_notice(home):
    job = make_job()
    jobs.save_jobs([{**job, "preflight_alerted": True}])
    send = AsyncMock(return_value={"success": True})
    with patch("tools.send_message_tool._send_to_platform", send):
        tick(job)
    assert send.await_count == 1
    assert incidents.list_incidents()[0]["alerted_at"]


def test_shared_notice_repeats_only_after_existing_pause(home, monkeypatch):
    first, second = make_job(), make_job()
    send = AsyncMock(return_value={"success": True})
    with patch("tools.send_message_tool._send_to_platform", send):
        tick(first)
        tick(second)
        later = now() + timedelta(hours=7)
        monkeypatch.setattr(scheduler, "_hermes_now", lambda: later)
        monkeypatch.setattr(incidents, "_hermes_now", lambda: later)
        tick(second)
        tick(first)
    assert send.await_count == 2
    assert len(incidents.list_incidents()) == 1


@pytest.mark.parametrize("error", [
    AuthError("Codex 429", provider="openai-codex", code="codex_rate_limited"),
    AuthError("Codex HTTP 503: service unavailable", provider="openai-codex"),
    RuntimeError("Claude capacity exhausted"),
    RuntimeError("529 overloaded"),
])
def test_congestion_stays_quiet_while_automatic_retry_is_pending(home, error):
    job = make_job()
    send = AsyncMock(return_value={"success": True})
    with patch("korra_cli.runtime_provider.resolve_runtime_provider", side_effect=error), \
         patch("tools.send_message_tool._send_to_platform", send):
        tick(job)
        tick(job)
    stored = jobs.get_job(job["id"])
    assert stored["last_status"] != "blocked_config"
    assert stored["unreachable_retry"]["attempt"] == 2
    assert send.await_count == 0
    assert attention(home) == []
    assert incidents.list_incidents()[0]["state"] == "detected"


@pytest.mark.parametrize("error", ["HTTP 429 rate limit", "HTTP 503 service unavailable", "Claude capacity exhausted"])
@pytest.mark.parametrize("mode", ["exhausted", "oneshot", "disabled", "after_call"])
def test_congestion_without_retry_notifies_owner_and_dashboard(home, mode, error):
    if mode == "oneshot":
        job = jobs.create_job(
            prompt="report", schedule="in 3h", model="gpt-test", provider="openai-codex",
            deliver="origin", origin={"platform": "telegram", "chat_id": "42", "user_id": "42", "chat_type": "dm"},
        )
    else:
        job = make_job()
    if mode == "exhausted":
        job["unreachable_retry"] = {"attempt": 3}
        jobs.save_jobs([job])
    if mode == "disabled":
        with (home / "config.yaml").open("a") as config:
            config.write("  retry_unreachable: false\n")
    send = AsyncMock(return_value={"success": True})
    runtime = {"provider": "openai-codex", "api_key": "fake", "base_url": "https://example.invalid", "api_mode": "codex_responses"}
    with patch("korra_cli.runtime_provider.resolve_runtime_provider", return_value=runtime), \
         patch("run_agent.AIAgent") as cls, patch("tools.send_message_tool._send_to_platform", send):
        cls.return_value.session_api_calls = 1 if mode == "after_call" else 0
        cls.return_value.run_conversation.side_effect = RuntimeError(error)
        tick(job)
    assert not jobs.get_job(job["id"]).get("unreachable_retry")
    assert send.await_count == 1
    assert "Вход в подписку" not in send.call_args.args[3]
    assert executions.latest_execution(job["id"])["delivery_outcome"] == "delivered"
    assert any(item["kind"] == "cron_incident" for item in attention(home))
    assert not any(item["kind"] == "provider_refused" for item in attention(home))


def test_congestion_notifies_after_the_last_automatic_retry(home):
    job = make_job()
    send = AsyncMock(return_value={"success": True})
    with patch("korra_cli.runtime_provider.resolve_runtime_provider", side_effect=AuthError("Codex HTTP 503", provider="openai-codex")), \
         patch("tools.send_message_tool._send_to_platform", send):
        for attempt in range(1, 4):
            tick(job)
            assert jobs.get_job(job["id"])["unreachable_retry"]["attempt"] == attempt
            assert send.await_count == 0
            assert attention(home) == []
        tick(job)
    assert not jobs.get_job(job["id"]).get("unreachable_retry")
    assert send.await_count == 1
    assert any(item["kind"] == "cron_incident" for item in attention(home))


def test_outer_congestion_without_planned_retry_notifies_owner(home):
    job = make_job()
    send = AsyncMock(return_value={"success": True})
    with patch.object(scheduler, "run_job", side_effect=RuntimeError("HTTP 503 service unavailable")), \
         patch("tools.send_message_tool._send_to_platform", send):
        assert scheduler.run_one_job(jobs.get_job(job["id"])) is False
    assert not jobs.get_job(job["id"]).get("unreachable_retry")
    assert send.await_count == 1
    assert any(item["kind"] == "cron_incident" for item in attention(home))


def test_outer_failure_preserves_an_eligible_automatic_retry(home):
    job = make_job()
    send = AsyncMock(return_value={"success": True})
    with patch("korra_cli.runtime_provider.resolve_runtime_provider", side_effect=AuthError("Codex HTTP 503", provider="openai-codex")), \
         patch.object(scheduler, "save_job_output", side_effect=RuntimeError("HTTP 503 service unavailable")), \
         patch("tools.send_message_tool._send_to_platform", send):
        assert scheduler.run_one_job(jobs.get_job(job["id"])) is False
    assert jobs.get_job(job["id"])["unreachable_retry"]["attempt"] == 1
    assert send.await_count == 0
    assert attention(home) == []


@pytest.mark.parametrize("provider, error", [
    ("openai-codex", "Codex access token expired"),
    ("openai-codex", "Codex 401 unauthorized"),
    ("anthropic", "OAuth access token has expired"),
    ("anthropic", "401 unauthorized"),
    ("anthropic", "OAuth token revoked"),
])
def test_runtime_oauth_failure_uses_the_same_shared_signal(home, provider, error):
    first, second = make_job(), make_job()
    send = AsyncMock(return_value={"success": True})
    runtime = {"provider": provider, "api_key": "sk-ant-oat-fake" if provider == "anthropic" else "fake",
               "base_url": "https://example.invalid", "api_mode": "anthropic_messages" if provider == "anthropic" else "codex_responses"}
    with patch("korra_cli.runtime_provider.resolve_runtime_provider", return_value=runtime), \
         patch("run_agent.AIAgent") as cls, patch("tools.send_message_tool._send_to_platform", send):
        cls.return_value.run_conversation.side_effect = RuntimeError(error)
        tick(first)
        tick(second)
    assert send.await_count == 1
    assert incidents.list_incidents()[0]["failure_type"] == f"subscription:{provider}"
    assert len([item for item in attention(home) if item["kind"] == "provider_refused"]) == 1


def test_outer_failure_records_and_delivers_subscription_notice(home):
    job = make_job("bot-chat")
    send = AsyncMock(return_value={"success": True})
    with patch.object(scheduler, "run_job", side_effect=AuthError("Codex invalid_token", provider="openai-codex", relogin_required=True)), \
         patch("tools.send_message_tool._send_to_platform", send):
        assert scheduler.run_one_job(jobs.get_job(job["id"])) is False
        assert scheduler.run_one_job(jobs.get_job(job["id"])) is False
    assert send.await_count == 1
    assert incidents.list_incidents()[0]["state"] == "alerted"


def test_error_variations_do_not_mint_separate_subscription_incidents(home):
    a, _ = incidents.upsert_incident("one", "Codex access token expired")
    b, _ = incidents.upsert_incident("two", "Codex refresh_token_reused")
    assert a == b
    assert incidents.count_incidents() == 1


def test_bare_api_key_and_transient_errors_are_not_subscription_expiry():
    assert incidents.subscription_provider("401 Unauthorized") is None
    assert incidents.subscription_provider("Anthropic API key missing") is None
    assert incidents.subscription_provider("Anthropic 401 invalid API key") is None
    assert incidents.subscription_provider(AuthError("Codex 429", provider="openai-codex")) is None


def test_expired_refresh_grant_real_auth_preflight_alerts_without_model(home):
    import httpx
    from korra_cli.auth import _save_codex_tokens

    payload = base64.urlsafe_b64encode(json.dumps({"exp": (now() - timedelta(hours=1)).timestamp()}).encode()).decode().rstrip("=")
    _save_codex_tokens({"access_token": f"fake.{payload}.fake", "refresh_token": "fake-refresh"})
    job = make_job()
    send = AsyncMock(return_value={"success": True})
    refused = httpx.Response(400, json={"error": "invalid_grant", "error_description": "refresh token expired"})
    with patch("httpx.Client.post", return_value=refused), \
         patch("tools.send_message_tool._send_to_platform", send), \
         patch("run_agent.AIAgent", side_effect=AssertionError("no model can run")):
        tick(job)
        tick(job)
    assert send.await_count == 1
    assert incidents.list_incidents()[0]["state"] == "alerted"
    assert len([item for item in attention(home) if item["kind"] == "provider_refused"]) == 1


def test_no_personal_channel_keeps_detected_signal(home):
    job = make_job("bot-chat")
    job["origin"] = {"platform": "telegram", "chat_id": "-100", "user_id": "", "chat_type": "group"}
    jobs.save_jobs([job])
    send = AsyncMock(return_value={"success": True})
    with patch("tools.send_message_tool._send_to_platform", send):
        tick(job)
        tick(job)
    assert send.await_count == 0
    assert incidents.list_incidents()[0]["state"] == "detected"
    assert incidents.list_incidents()[0]["alerted_at"] is None
    assert len([item for item in attention(home) if item["kind"] == "provider_refused"]) == 1


def test_recovered_incident_stays_off_dashboard(home):
    job = make_job()
    inc, _ = incidents.upsert_incident(job["id"], "provider response failed")
    assert any(item["kind"] == "cron_incident" for item in attention(home))
    incidents.close_incidents_for_recovered_job(job["id"])
    assert not any(item["kind"] == "cron_incident" for item in attention(home))
    assert incidents.get_incident(inc)["state"] == "resolved"


def test_subscription_notice_does_not_age_out_or_depend_on_trigger_job(home, monkeypatch):
    job = make_job("local")
    old = now() - timedelta(days=10)
    monkeypatch.setattr(incidents, "_hermes_now", lambda: old)
    incidents.upsert_incident(job["id"], incidents.subscription_notice("openai-codex"))
    jobs.pause_job(job["id"])
    assert len([item for item in attention(home) if item["kind"] == "provider_refused"]) == 1


def test_pool_and_cron_show_one_signal_per_profile(home):
    incidents.upsert_incident("one", incidents.subscription_notice("openai-codex"))
    (home / "auth.json").write_text(json.dumps({"credential_pool": {"openai-codex": [
        {"id": "fake", "last_status": "dead", "last_error_code": 401, "last_error_reason": "token_revoked"},
    ]}}))
    assert len([item for item in attention(home) if item["kind"] == "provider_refused"]) == 1


def test_parallel_jobs_share_one_confirmed_notice(home):
    import contextvars
    import threading
    from concurrent.futures import ThreadPoolExecutor

    first, second = make_job(), make_job()
    ready = threading.Barrier(2)

    def failed(*_args, **_kw):
        ready.wait(timeout=10)
        return False, "No model run", "", incidents.subscription_notice("openai-codex")

    send = AsyncMock(return_value={"success": True})
    with patch.object(scheduler, "run_job", side_effect=failed), \
         patch("tools.send_message_tool._send_to_platform", send), ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contextvars.copy_context().run, tick, job) for job in (first, second)]
        for future in futures:
            future.result(timeout=20)
    assert send.await_count == 1
    assert len(incidents.list_incidents()) == 1
    assert incidents.list_incidents()[0]["state"] == "alerted"


def test_profiles_do_not_share_incidents_or_hide_a_failed_sibling(home, monkeypatch):
    incidents.upsert_incident("one", incidents.subscription_notice("openai-codex"))
    other = home / "profiles" / "other"
    other.mkdir(parents=True)
    (other / "auth.json").write_text(json.dumps({"credential_pool": {"openai-codex": [
        {"id": "fake-working", "last_status": "ok"},
    ]}}))
    with monkeypatch.context() as scoped:
        scoped.setenv("HERMES_HOME", str(other))
        scoped.setenv("KORRA_HOME", str(other))
        with jobs.use_cron_store(other):
            assert incidents.list_incidents() == []
    roster = [dashboard.Agent(profile=None, name="default", home=home, label="Корра"),
              dashboard.Agent(profile="other", name="other", home=other, label="Другой агент")]
    signals = dashboard.attention_section(roster, now=now().timestamp(), tz=now().tzinfo, quota={"available": False})["items"]
    assert len([item for item in signals if item["kind"] == "provider_refused"]) == 1


def test_dashboard_http_signal_and_recovery(home, monkeypatch):
    from fastapi.testclient import TestClient
    from korra_cli import web_server

    job = make_job("local")
    agent = dashboard.Agent(profile=None, name="default", home=home, label="Корра")
    monkeypatch.setattr(dashboard, "list_agents", lambda: [agent])
    client = TestClient(web_server.app)
    with patch("run_agent.AIAgent", side_effect=AssertionError("no model")):
        tick(job)
    dashboard.reset_cache()
    response = client.get("/api/dashboard/state", headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN})
    assert response.status_code == 200
    signals = [item for item in response.json()["attention"]["items"] if item["kind"] == "provider_refused"]
    assert len(signals) == 1 and signals[0]["href"] == "/models"
    incidents.close_incidents_for_recovered_job(job["id"], provider="openai-codex")
    dashboard.reset_cache()
    response = client.get("/api/dashboard/state", headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN})
    assert not [item for item in response.json()["attention"]["items"] if item["kind"] == "provider_refused"]


def test_recovery_clears_legacy_per_job_subscription_signals(home):
    job = make_job()
    stamp = now().isoformat()
    with incidents._transaction() as conn:
        for index, error in enumerate(("Codex access token expired", "Codex refresh_token_reused")):
            conn.execute(
                "INSERT INTO cron_incidents (id, job_id, error_sig, state, failure_type, first_seen_at, last_seen_at, error) "
                "VALUES (?, ?, 'old-sig', 'detected', 'auth', ?, ?, ?)",
                (f"legacy-{index}", f"old-job-{index}", stamp, stamp, error),
            )
    assert len([item for item in attention(home) if item["kind"] == "provider_refused"]) == 1
    incidents.close_incidents_for_recovered_job(job["id"], provider="anthropic")
    assert len([item for item in attention(home) if item["kind"] == "provider_refused"]) == 1
    assert incidents.close_incidents_for_recovered_job(job["id"], provider="openai-codex") == 2
    assert not [item for item in attention(home) if item["kind"] == "provider_refused"]
