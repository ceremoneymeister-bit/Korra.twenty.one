import asyncio
import json
import socket
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageType
from plugins.platforms.max import adapter as module
from plugins.platforms.max import client as api


def update(*, mid="incoming", chat_type="dialog", sender=12, attachments=None, text="Привет"):
    return {"update_type": "message_created", "timestamp": 1700000000000,
            "message": {"sender": {"user_id": sender, "first_name": "Марина", "is_bot": False},
                        "recipient": {"chat_id": -77, "chat_type": chat_type},
                        "body": {"mid": mid, "text": text, "attachments": attachments or []},
                        "link": {"type": "reply", "message": {"mid": "previous"}}}}


@pytest.fixture
def adapter():
    return module.MaxAdapter(PlatformConfig(enabled=True))


@pytest.fixture
def fake_http(monkeypatch):
    original = httpx.AsyncClient
    requests = []
    replies = []

    def handler(request):
        requests.append(request)
        answer = replies.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(**{**kw, "transport": httpx.MockTransport(handler)}))
    monkeypatch.setattr(api.MaxClient, "_pace", AsyncMock())
    return requests, replies


def sent(mid="sent"):
    return httpx.Response(200, json={"message": {"body": {"mid": mid}}})


@pytest.mark.parametrize("kind,expected", [("dialog", "dm"), ("chat", "group"), ("channel", "channel"), ("unknown", "group")])
def test_parse_identity(adapter, kind, expected):
    event = adapter._parse(update(chat_type=kind))
    assert event.source.chat_type == expected
    assert event.source.platform == Platform("max")
    assert event.source.chat_id == "-77"
    assert event.source.user_id == "12"
    assert event.reply_to_message_id == "previous"
    assert event.text == "Привет"
    assert event.timestamp.tzinfo is not None


@pytest.mark.asyncio
async def test_dedup_and_self_messages(adapter):
    adapter.handle_message = AsyncMock()
    await adapter._receive(update())
    await adapter._receive(update())
    adapter._bot_id = "12"
    await adapter._receive(update(mid="own"))
    bot = update(mid="bot", sender=45)
    bot["message"]["sender"]["is_bot"] = True
    await adapter._receive(bot)
    adapter.handle_message.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,mime,expected", [("image", "image/png", MessageType.PHOTO), ("audio", "audio/ogg", MessageType.VOICE),
                                               ("video", "video/mp4", MessageType.VIDEO), ("file", "text/plain", MessageType.DOCUMENT)])
async def test_inbound_media_shared_cache(adapter, kind, mime, expected):
    import base64
    data = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jL1sAAAAASUVORK5CYII=") if kind == "image" else b"fixture"
    adapter._client = SimpleNamespace(download=AsyncMock(return_value=(data, mime, "attachment")))
    adapter.set_authorization_check(lambda *args: True)
    adapter.handle_message = AsyncMock()
    await adapter._receive(update(text="", attachments=[{"type": kind, "payload": {"url": "https://cdn.max.ru/a"}}]))
    event = adapter.handle_message.call_args.args[0]
    assert event.message_type == expected
    assert len(event.media_urls) == 1
    assert event.media_types[0].startswith(mime.split("/")[0])
    assert "saved at" in event.text


@pytest.mark.asyncio
async def test_denied_sender_never_downloads(adapter):
    adapter._client = SimpleNamespace(download=AsyncMock())
    adapter.set_authorization_check(lambda *args: False)
    adapter.handle_message = AsyncMock()
    await adapter._receive(update(attachments=[{"type": "file", "payload": {"url": "https://127.0.0.1/secret"}}]))
    adapter._client.download.assert_not_awaited()
    adapter.handle_message.assert_awaited_once()  # usual gateway denial/pairing


@pytest.mark.asyncio
async def test_send_splits_without_losing_text(adapter, fake_http):
    requests, replies = fake_http
    replies.extend([sent("1"), sent("2"), sent("3")])
    adapter._client = api.MaxClient("fixture-token")
    text = "🙂 код\n" * 1800
    try:
        result = await adapter.send("-77", text, reply_to="incoming")
    finally:
        await adapter._client.close()
    assert result.success
    bodies = [json.loads(req.content) for req in requests]
    assert "".join(body["text"] for body in bodies) == text
    assert all(len(body["text"]) <= 4000 for body in bodies)
    assert bodies[0]["link"] == {"type": "reply", "mid": "incoming"}
    assert "link" not in bodies[1]
    assert all(req.url.params["chat_id"] == "-77" and req.headers["Authorization"] == "fixture-token" for req in requests)
    assert all("fixture-token" not in str(req.url) for req in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(429, "too.many.requests"), (400, "attachment.not.ready")])
async def test_rejected_send_retries_with_backoff(fake_http, monkeypatch, status, code):
    requests, replies = fake_http
    replies.extend([httpx.Response(status, json={"code": code}, headers={"Retry-After": "3"}), sent()])
    sleep = AsyncMock()
    monkeypatch.setattr(api.asyncio, "sleep", sleep)
    client = api.MaxClient("fixture")
    try:
        assert await client.send("3", "ответ") == "sent"
    finally:
        await client.close()
    assert len(requests) == 2
    sleep.assert_awaited_once_with(3)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.Response(503, json={"code": "unavailable"}), httpx.ReadTimeout("secret URL"), httpx.Response(200, json={"bad": "response"})])
async def test_ambiguous_post_and_partial_reply_are_never_retried(adapter, fake_http, failure):
    requests, replies = fake_http
    replies.extend([sent("first"), failure])
    adapter._client = api.MaxClient("fixture")
    try:
        result = await adapter._send_with_retry("3", "x" * 5000)
    finally:
        await adapter._client.close()
    assert not result.success and not result.retryable
    assert result.message_id == "first"
    assert len(requests) == 2
    assert "secret" not in result.error


@pytest.mark.asyncio
async def test_get_retries_temporary_failure(fake_http, monkeypatch):
    requests, replies = fake_http
    replies.extend([httpx.Response(503), httpx.ReadTimeout("error"), httpx.Response(200, json={"user_id": 1, "is_bot": True})])
    monkeypatch.setattr(api.asyncio, "sleep", AsyncMock())
    client = api.MaxClient("fixture")
    try:
        assert (await client.me())["user_id"] == 1
    finally:
        await client.close()
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_polling_webhook_conflict_stops_with_actionable_status(adapter, fake_http):
    requests, replies = fake_http
    replies.append(httpx.Response(405))
    adapter._client = api.MaxClient("fixture")
    try:
        await adapter._poll()
    finally:
        await adapter._client.close()
    assert adapter.has_fatal_error and not adapter.fatal_error_retryable
    assert "webhook" in adapter.fatal_error_message
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,meta,response", [("file", {}, {"token": "file-token"}),
                                              ("image", {}, {"photos": {"123": {"token": "image-token"}}}),
                                              ("audio", {"token": "audio-token"}, {}),
                                              ("video", {"token": "video-token"}, {})])
async def test_upload_then_send(adapter, fake_http, monkeypatch, tmp_path, kind, meta, response):
    requests, replies = fake_http
    replies.extend([httpx.Response(200, json={"url": "https://cdn.max.ru/upload?sig=private", **meta}),
                    httpx.Response(200, json=response), sent()])
    monkeypatch.setattr(api, "pinned_url", lambda url: ("https://8.8.8.8/upload?sig=private", "cdn.max.ru", "cdn.max.ru"))
    file = tmp_path / "отчёт.txt"
    file.write_text("отчёт")
    adapter._client = api.MaxClient("fixture-secret")
    try:
        result = await adapter._send_file("42", str(file), kind, "Готово")
    finally:
        await adapter._client.close()
    assert result.success
    assert requests[0].url.params["type"] == kind
    assert b'name="data"' in requests[1].content
    assert "Authorization" not in requests[1].headers
    assert requests[1].extensions["sni_hostname"] == "cdn.max.ru"
    body = json.loads(requests[2].content)
    assert body["attachments"] == [{"type": kind, "payload": {"token": kind + "-token"}}]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,ok,message", [(200, {"user_id": 1, "is_bot": True, "first_name": "Пример"}, True, "Бот Пример подключён"),
                                                  (401, {}, False, "Токен не принят")])
async def test_token_probe(fake_http, status, body, ok, message):
    requests, replies = fake_http
    replies.append(httpx.Response(status, json=body))
    result = await api.test_connection({"MAX_BOT_TOKEN": "fixture"})
    assert result["ok"] is ok and result["message"] == message
    assert requests[0].method == "GET" and requests[0].url.path == "/me"


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.1", "::1", "224.0.0.1"])
def test_attachment_rejects_nonpublic_dns(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(None, None, None, None, (ip, 443))])
    with pytest.raises(api.MaxError):
        api.pinned_url("https://cdn.max.ru/file")


def test_attachment_pins_ip_and_keeps_hostname(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(None, None, None, None, ("8.8.8.8", 443))])
    assert api.pinned_url("https://cdn.max.ru/file?sig=x") == ("https://8.8.8.8/file?sig=x", "cdn.max.ru", "cdn.max.ru")
    with pytest.raises(api.MaxError):
        api.pinned_url("http://cdn.max.ru/file")


def test_ca_is_host_scoped():
    import hashlib
    fingerprint = bytes.fromhex("D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31")
    for host, included in [("platform-api2.max.ru", True), ("cdn.max.ru", True), ("example.com", False), ("max.ru.evil.org", False)]:
        ctx = api.ssl_context(host)
        assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
        roots = {hashlib.sha256(cert).digest() for cert in ctx.get_ca_certs(binary_form=True)}
        assert (fingerprint in roots) is included


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [httpx.Response(302, headers={"Location": "https://127.0.0.1/secret"}),
                                      httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"bad"),
                                      httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"x" * 9)])
async def test_download_rejects_redirect_mime_and_size(fake_http, monkeypatch, response):
    requests, replies = fake_http
    replies.append(response)
    monkeypatch.setattr(api, "MAX_ATTACHMENT_BYTES", 8)
    monkeypatch.setattr(api, "pinned_url", lambda url: ("https://8.8.8.8/image", "cdn.max.ru", "cdn.max.ru"))
    client = api.MaxClient("secret")
    try:
        with pytest.raises(api.MaxError):
            await client.download({"type": "image", "payload": {"url": "https://cdn.max.ru/image"}})
    finally:
        await client.close()
    assert len(requests) == 1
    assert "Authorization" not in requests[0].headers


@pytest.mark.asyncio
async def test_video_fetches_direct_media_url(fake_http, monkeypatch):
    requests, replies = fake_http
    replies.extend([httpx.Response(200, json={"urls": {"mp4_720": "https://cdn.max.ru/movie"}}),
                    httpx.Response(200, headers={"Content-Type": "video/mp4"}, content=b"movie")])
    monkeypatch.setattr(api, "pinned_url", lambda url: (url, "cdn.max.ru", "cdn.max.ru"))
    client = api.MaxClient("fixture")
    try:
        content, mime, name = await client.download({"type": "video", "payload": {"token": "movie-token", "url": "https://max.ru/player"}})
    finally:
        await client.close()
    assert requests[0].url.path == "/videos/movie-token"
    assert requests[1].url.path == "/movie"
    assert content == b"movie" and mime == "video/mp4"


@pytest.mark.asyncio
async def test_request_rate_limit(monkeypatch):
    # Deterministic virtual clock tests pacing without a wall-clock race.
    now = [100.0]
    async def sleep(delay):
        now[0] += delay
    monkeypatch.setattr(api.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(api.asyncio, "sleep", sleep)
    api._limiter.cache_clear()
    client = api.MaxClient("fixture")
    second = api.MaxClient("fixture")
    times = []
    try:
        for index, message in enumerate((True, False, True, False, False, True)):
            await (client if index % 2 else second)._pace(message)
            times.append(now[0])
    finally:
        await client.close()
        await second.close()
        api._limiter.cache_clear()
    assert all(b - a >= 0.2 for a, b in zip(times, times[1:]))
    assert times[2] - times[0] >= 0.5
    assert times[5] - times[2] >= 0.5


def test_admission_and_owner_are_independent(monkeypatch):
    from korra_cli.plugins import discover_plugins
    from gateway.authz_mixin import GatewayAuthorizationMixin
    from gateway.credential_management import owner_principal
    discover_plugins()
    runner = GatewayAuthorizationMixin()
    runner.config = GatewayConfig()
    runner.adapters = {}
    runner.pairing_store = SimpleNamespace(is_approved=lambda *args: False)
    monkeypatch.setenv("MAX_ALLOWED_USERS", "12,34")
    assert runner._get_unauthorized_dm_behavior(Platform("max")) == "ignore"
    for uid, allowed in [("12", True), ("34", True), ("56", False)]:
        source = module.MaxAdapter(PlatformConfig()).build_source(chat_id="77", chat_type="dm", user_id=uid)
        assert runner._is_user_authorized(source) is allowed
    owners = {"gateway": {"credential_management": {"owners": {"max": ["12"]}}}}
    assert owner_principal(owners, platform="max", user_id="12", chat_type="dm", internal=False, installation={}) == "live"
    assert owner_principal(owners, platform="max", user_id="34", chat_type="dm", internal=False, installation={}) == ""
    assert owner_principal(owners, platform="max", user_id="12", chat_type="group", internal=False, installation={}) == ""
    from gateway.principal import current_principal, may_change_owner_services
    from gateway.session_context import set_session_vars, clear_session_vars
    for uid, chat_type, is_owner in [("12", "dm", True), ("34", "dm", False), ("12", "group", False)]:
        verdict = owner_principal(owners, platform="max", user_id=uid, chat_type=chat_type, internal=False, installation={})
        tokens = set_session_vars(platform="max", chat_type=chat_type, user_id=uid, owner_principal=verdict)
        try:
            assert current_principal().owner is is_owner
            assert may_change_owner_services() is is_owner
        finally:
            clear_session_vars(tokens)
    monkeypatch.delenv("MAX_ALLOWED_USERS")
    assert runner._is_user_authorized(source) is False
    assert runner._get_unauthorized_dm_behavior(Platform("max")) == "pair"


def test_scoped_token_never_borrows_other_profile(monkeypatch):
    from agent.secret_scope import set_secret_scope, reset_secret_scope
    monkeypatch.setenv("MAX_BOT_TOKEN", "root-secret")
    monkeypatch.setattr("agent.secret_scope._MULTIPLEX_ACTIVE", True)
    scope = set_secret_scope({})
    try:
        assert module._token() == ""
    finally:
        reset_secret_scope(scope)
