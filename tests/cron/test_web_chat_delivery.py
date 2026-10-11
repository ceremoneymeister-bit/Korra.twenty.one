"""K21-323: напоминание из веб-чата кабинета не теряется молча.

У веб-чата (`api_server`) нет канала отправки: результат записывается в историю
этого чата и виден после обновления страницы. Если чата нет, агент при создании
получает прямое указание сказать, что напоминание не придёт.
"""
from __future__ import annotations

import json

import pytest

WEB_CHAT = {"platform": "api_server", "chat_id": "web-chat-1", "session_key": "web-chat-1"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    from cron import jobs as cron_jobs

    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for name in ("KORRA_SINGLE_QUERY_SESSION", "HERMES_SINGLE_QUERY_SESSION", "KORRA_ONESHOT_SESSION"):
        monkeypatch.delenv(name, raising=False)
    with cron_jobs.use_cron_store(tmp_path):
        cron_jobs.ensure_dirs()
        yield tmp_path


def _make_chat(session_id: str) -> None:
    from korra_state import SessionDB

    db = SessionDB()
    try:
        db.create_session(session_id=session_id, source="api_server")
    finally:
        db.close()


def _messages(session_id: str) -> list:
    from korra_state import SessionDB

    db = SessionDB()
    try:
        return db.get_messages(session_id)
    finally:
        db.close()


def _create(session: dict, **kw):
    from gateway.session_context import clear_session_vars, set_session_vars
    from tools.cronjob_tools import _cronjob_handler

    tokens = set_session_vars(cron_session="", async_delivery=False, **session)
    try:
        return json.loads(_cronjob_handler({"action": "create", **kw}))
    finally:
        clear_session_vars(tokens)


def test_reminder_from_web_chat_lands_in_that_chat(store):
    from cron.jobs import get_job
    from cron.scheduler import _deliver_result

    _make_chat("web-chat-1")
    created = _create(WEB_CHAT, prompt="Позвонить поставщику", schedule="in 2m", deliver="origin")
    assert created["success"] is True
    assert "НЕ придёт" not in created["message"]

    job = get_job(created["job_id"])
    assert _deliver_result(job, "Позвоните поставщику") is None

    rows = [m for m in _messages("web-chat-1") if m["role"] == "assistant"]
    assert rows and "Позвоните поставщику" in rows[-1]["content"]


def test_missing_web_chat_is_told_at_creation_and_reported_on_delivery(store):
    from cron.jobs import get_job
    from cron.scheduler import _deliver_result

    created = _create(WEB_CHAT, prompt="Позвонить поставщику", schedule="in 2m", deliver="origin")
    assert "Напоминание НЕ придёт в этот чат" in created["message"]
    assert "telegram" in created["message"]

    error = _deliver_result(get_job(created["job_id"]), "Позвоните поставщику")
    assert error and "чат кабинета" in error


def test_web_chat_delivery_needs_no_recipient_confirmation(store):
    from cron.scheduler import _is_creator_private_chat

    job = {"origin": dict(WEB_CHAT)}
    assert _is_creator_private_chat(job, "api_server", "web-chat-1") is True
    assert _is_creator_private_chat(job, "api_server", "another-chat") is False
    assert _is_creator_private_chat({"origin": None}, "api_server", "web-chat-1") is False
