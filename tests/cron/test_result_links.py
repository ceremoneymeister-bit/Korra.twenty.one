from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from cron import executions, result_links as links


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(executions, "EXECUTIONS_FILE", tmp_path / "executions.db")
    return executions.EXECUTIONS_FILE


def result(job="a", run="run-a"):
    return links.snapshot({"id": job, "name": "Напоминание", "execution_id": run}, "Одинаковый текст")


def test_confirmed_chunks_survive_reopen_and_cannot_be_reassigned(ledger):
    receipt = NS(success=True, message_id="10", raw_response={"message_ids": ["10", "11"]})
    assert links.record(account="bot", chat_id="100", thread_id=None, result=result(), receipt=receipt)
    links.record(account="bot", chat_id="100", thread_id=None, result=result("b"), receipt=receipt)
    for mid in ("10", "11"):
        saved = links.lookup(account="bot", chat_id="100", thread_id=None, message_id=mid)
        assert saved["job_id"] == "a"
        assert saved["execution_id"] == "run-a"
    assert not links.record(account="bot", chat_id="100", thread_id=None, result=result(),
                            receipt={"success": False, "message_id": "12"})
    assert links.lookup(account="bot", chat_id="100", thread_id=None, message_id="12") is None


@pytest.mark.parametrize("scope", [{"account": "other"}, {"chat_id": "200"}, {"thread_id": "8"}, {"thread_id": "1"}, {"message_id": "99"}])
def test_no_cross_account_chat_topic_or_result_lookup(ledger, scope):
    links.record(account="bot", chat_id="100", thread_id="7", result=result(),
                 receipt={"success": True, "message_id": "10"})
    query = dict(account="bot", chat_id="100", thread_id="7", message_id="10")
    query.update(scope)
    assert links.lookup(**query) is None


def test_profile_isolation(monkeypatch, tmp_path):
    monkeypatch.setattr(executions, "EXECUTIONS_FILE", None)
    home = [tmp_path / "first"]
    monkeypatch.setattr(executions, "get_hermes_home", lambda: home[0])
    query = dict(account="bot", chat_id="100", thread_id=None, message_id="10")
    links.record(**{k: v for k, v in query.items() if k != "message_id"}, result=result(),
                 receipt={"success": True, "message_id": "10"})
    home[0] = tmp_path / "other"
    assert links.lookup(**query) is None
    assert not home[0].exists()
    home[0] = tmp_path / "first"
    assert links.lookup(**query)["job_id"] == "a"


def test_actual_receipt_route_wins_over_requested_topic_and_username(ledger):
    links.record(account="bot", chat_id="@channel", thread_id="7", result=result(), receipt={
        "success": True, "message_ids": ["10", "11"], "message_receipts": [
            {"message_id": "10", "chat_id": "-100", "thread_id": 7},
            {"message_id": "11", "chat_id": "-100", "thread_id": None},
        ]})
    assert links.lookup(account="bot", chat_id="-100", thread_id="7", message_id="10")
    assert links.lookup(account="bot", chat_id="-100", thread_id=None, message_id="11")
    assert links.lookup(account="bot", chat_id="-100", thread_id="7", message_id="11") is None


def test_reply_context_is_exact_and_checks_current_job(ledger, monkeypatch):
    from gateway.config import Platform, PlatformConfig
    from tools.send_message_tool import _configured_account_identity
    config = PlatformConfig(enabled=True, token="test-token")
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: NS(platforms={Platform.TELEGRAM: config}))
    account = _configured_account_identity("telegram", config)
    source = NS(platform=Platform.TELEGRAM, chat_id="100", thread_id=None)
    event = NS(internal=False, reply_to_is_own_message=True, reply_to_message_id="10")
    job = {"id": "a", "name": "Напоминание", "execution_id": "run-a"}
    links.record(account=account, chat_id="100", thread_id=None, result=links.snapshot(job, "Первый"),
                 receipt={"success": True, "message_id": "10"})
    links.record(account=account, chat_id="100", thread_id=None, result=result("b", "run-b"),
                 receipt={"success": True, "message_id": "11"})
    monkeypatch.setattr("cron.jobs.get_job", lambda job_id: job)
    assert '"current_job_state": "unchanged"' in links.reply_context(event, source)
    job["prompt"] = "new"
    note = links.reply_context(event, source)
    assert '"current_job_state": "changed"' in note and '"job_id": "a"' in note
    assert "run-b" not in note
    monkeypatch.setattr("cron.jobs.get_job", lambda job_id: None)
    assert '"current_job_state": "removed"' in links.reply_context(event, source)
    event.reply_to_is_own_message = False
    assert links.reply_context(event, source) is None
    event.reply_to_is_own_message = True
    event.internal = True
    assert links.reply_context(event, source) is None


@pytest.mark.asyncio
async def test_gateway_preprocessing_keeps_visible_text_clean_and_resolves_in_profile(monkeypatch, tmp_path):
    from contextlib import contextmanager
    from gateway.run import GatewayRunner
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = NS(multiplex_profiles=True)
    runner._resolve_profile_home_for_source = lambda source: tmp_path
    active = []
    @contextmanager
    def scope(home):
        active.append(home)
        try:
            yield
        finally:
            active.pop()
    monkeypatch.setattr("gateway.run._profile_runtime_scope", scope)
    def context(event, source):
        assert active == [tmp_path]
        return "EXACT RESULT"
    monkeypatch.setattr(links, "reply_context", context)
    runner._prepare_inbound_message_text = AsyncMock(return_value="Ответ пользователя")
    event = NS()
    text = await runner._prepare_profile_scoped_inbound_message_text(event=event, source=NS(), history=[])
    assert text == "Ответ пользователя"
    assert event._cron_reply_context == "EXACT RESULT"
    monkeypatch.setattr(links, "reply_context", lambda *args: None)
    await runner._prepare_profile_scoped_inbound_message_text(event=event, source=NS(), history=[])
    assert event._cron_reply_context is None


def test_scheduler_delivery_persists_link_for_the_confirmed_run(ledger, monkeypatch):
    from cron.scheduler import _deliver_result
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from tools.send_message_tool import _configured_account_identity
    config = PlatformConfig(enabled=True, token="test-token")
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: GatewayConfig(platforms={Platform.TELEGRAM: config}))
    send = AsyncMock(return_value={"success": True, "message_id": "71"})
    monkeypatch.setattr("tools.send_message_tool._send_to_platform", send)
    job = {"id": "report", "execution_id": "run-71", "name": "Отчёт", "deliver": "origin",
           "origin": {"platform": "telegram", "chat_id": "100"}}
    _deliver_result(job, "Результат")
    send.assert_awaited_once()
    assert links.lookup(account=_configured_account_identity("telegram", config), chat_id="100",
                        thread_id=None, message_id="71")["execution_id"] == "run-71"
    # Execution-retention pruning must not sever an old Telegram reply.
    with executions._transaction() as conn:
        conn.execute("DELETE FROM executions")
    assert links.lookup(account=_configured_account_identity("telegram", config), chat_id="100",
                        thread_id=None, message_id="71")["text"] == "Результат"
