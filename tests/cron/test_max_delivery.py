"""MAX through the existing cron/send_message dispatch and recipient policy."""
import json
from unittest.mock import AsyncMock

import httpx
import pytest


@pytest.fixture
def max_sender(monkeypatch):
    from korra_constants import get_hermes_home
    from korra_cli.plugins import discover_plugins
    from gateway.platform_registry import platform_registry
    import sys

    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("platforms:\n  max:\n    enabled: true\ncron:\n  mirror_to_session: false\n")
    monkeypatch.setenv("MAX_BOT_TOKEN", "fixture-secret")
    discover_plugins()
    entry = platform_registry.get("max")
    module = sys.modules[entry.adapter_factory.__module__]
    api = sys.modules[module.MaxClient.__module__]
    monkeypatch.setattr(api.MaxClient, "_pace", AsyncMock())
    monkeypatch.setattr(api, "pinned_url", lambda url: ("https://8.8.8.8/upload", "cdn.max.ru", "cdn.max.ru"))
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == "/uploads":
            return httpx.Response(200, json={"url": "https://cdn.max.ru/upload"})
        if request.url.path == "/upload":
            return httpx.Response(200, json={"token": "file-token"})
        assert request.url.path == "/messages"
        return httpx.Response(200, json={"message": {"body": {"mid": str(len(requests))}}})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(**{**kw, "transport": httpx.MockTransport(handler)}))
    return requests


def test_cron_delivers_to_max_chat(max_sender):
    from cron.scheduler import _deliver_result, _resolve_delivery_targets
    job = {"id": "max-fixture", "name": "Отчёт", "deliver": "max:-77", "origin_owner": True,
           "recipients_policy": 1, "recipients_confirmed": {"targets": ["max:-77"]}}
    targets = _resolve_delivery_targets(job)
    assert targets[0]["platform"] == "max" and targets[0]["chat_id"] == "-77"
    assert _deliver_result(job, "Результат автоматизации") is None
    messages = [req for req in max_sender if req.url.path == "/messages"]
    assert len(messages) == 1
    assert messages[0].url.params["chat_id"] == "-77"
    assert "Результат автоматизации" in json.loads(messages[0].content)["text"]


def test_send_message_confirmed_recipient_and_file(max_sender, tmp_path):
    from tools.send_message_tool import send_message_tool
    from cron.recipients import bind_running_job, reset_running_job
    file = tmp_path / "report.txt"
    file.write_text("Отчёт готов")
    job = {"id": "max-fixture", "name": "Отчёт", "deliver": "max:-77", "origin_owner": True,
           "recipients_policy": 1, "recipients_confirmed": {"targets": ["max:-77"]}}
    binding = bind_running_job(job)
    try:
        result = json.loads(send_message_tool({"target": "max:-77", "message": f"Отчёт\nMEDIA:{file}"}))
    finally:
        reset_running_job(binding)
    assert result.get("success"), result
    messages = [req for req in max_sender if req.url.path == "/messages"]
    assert len(messages) == 1
    assert json.loads(messages[0].content)["attachments"] == [{"type": "file", "payload": {"token": "file-token"}}]
