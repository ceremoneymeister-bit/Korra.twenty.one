import asyncio
import json
import stat
import threading

import anyio
from fastapi import FastAPI
from fastapi.responses import Response
import pytest
from starlette.requests import Request

from hermes_cli import web_server
from hermes_cli.chat_delivery import (
    DeliveryConflict,
    DeliveryLedger,
    openai_json_to_sse,
    request_fingerprint,
    validate_client_message_id,
)


async def _post_chat_twice(monkeypatch, fake_upstream_client, headers, body):
    """Exercise the registered route without Starlette's broken httpx2 TestClient."""
    import httpx

    async def _inline_run_in_threadpool(call, *args, **kwargs):
        return call(*args, **kwargs)

    routes = [
        route
        for route in web_server.app.router.routes
        if getattr(route, "path", None) == "/api/chat/completions"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    assert len(routes) == 1

    route_app = FastAPI()
    route_app.router.routes.append(routes[0])
    real_async_client = httpx.AsyncClient
    transport = httpx.ASGITransport(app=route_app)
    async with real_async_client(
        transport=transport, base_url="http://testserver"
    ) as client:
        monkeypatch.setattr(httpx, "AsyncClient", fake_upstream_client)
        monkeypatch.setattr(
            web_server, "run_in_threadpool", _inline_run_in_threadpool
        )
        first = await client.post("/api/chat/completions", headers=headers, json=body)
        second = await client.post("/api/chat/completions", headers=headers, json=body)
    return first, second


def test_delivery_ledger_replays_completed_result_and_rejects_id_reuse(tmp_path):
    ledger = DeliveryLedger(tmp_path / "delivery.sqlite3")
    body = {"messages": [{"role": "user", "content": "Привет"}], "stream": True}
    fingerprint = request_fingerprint(body, "session-1")
    state, _record = ledger.claim("msg-1234567890abcdef", fingerprint, "session-1")
    assert state == "new"

    ledger.complete(
        "msg-1234567890abcdef",
        response_body=b"data: [DONE]\n\n",
        status_code=200,
        content_type="text/event-stream",
    )
    state, record = ledger.claim("msg-1234567890abcdef", fingerprint, "session-1")
    assert state == "completed"
    assert record.response_body == b"data: [DONE]\n\n"

    with pytest.raises(DeliveryConflict):
        ledger.claim(
            "msg-1234567890abcdef",
            request_fingerprint({"messages": [{"role": "user", "content": "Другое"}]}, "session-1"),
            "session-1",
        )


def test_failed_delivery_can_retry_with_same_fingerprint(tmp_path):
    ledger = DeliveryLedger(tmp_path / "delivery.sqlite3")
    fingerprint = request_fingerprint({"messages": ["same"]}, "session-1")
    assert ledger.claim("msg-1234567890abcdef", fingerprint, "session-1")[0] == "new"
    ledger.fail("msg-1234567890abcdef")
    state, record = ledger.claim("msg-1234567890abcdef", fingerprint, "session-1")
    assert state == "retry"
    assert record.status == "pending"


def test_delivery_ledger_is_private_on_disk(tmp_path):
    ledger = DeliveryLedger(tmp_path / "private" / "delivery.sqlite3")
    ledger.claim("msg-1234567890abcdef", "fingerprint", "session-1")
    assert stat.S_IMODE(ledger.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(ledger.path.stat().st_mode) == 0o600


def test_delivery_ledger_initializes_concurrent_first_claims_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "delivery.sqlite3"
    simultaneous_claims = threading.Barrier(2)

    def _claim(index):
        simultaneous_claims.wait(timeout=5)
        return DeliveryLedger(path).claim(
            f"msg-concurrent-{index:04d}",
            f"fingerprint-{index}",
            f"session-{index}",
        )[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(_claim, (1, 2)))

    assert states == ["new", "new"]


def test_openai_response_converts_to_frontend_sse_contract():
    payload = json.dumps({
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 123,
        "model": "korra-agent",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "Готово **без потери**"},
            "finish_reason": "stop",
        }],
    }, ensure_ascii=False).encode("utf-8")
    sse = openai_json_to_sse(payload).decode("utf-8")
    assert "Готово **без потери**" in sse
    assert sse.endswith("data: [DONE]\n\n")


@pytest.mark.parametrize("value", ["", "short", "bad id with spaces", "x" * 81])
def test_client_message_id_validation_rejects_unsafe_values(value):
    with pytest.raises(ValueError):
        validate_client_message_id(value)


def test_client_message_id_validation_accepts_uuid():
    assert validate_client_message_id("12345678-1234-4234-8234-123456789abc")


def test_chat_proxy_persists_and_replays_same_client_message(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("API_SERVER_KEY", "test-key")
    web_server._CHAT_DELIVERY_TASKS.clear()
    calls = []

    response_payload = json.dumps({
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 123,
        "model": "korra-agent",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "Ответ"},
            "finish_reason": "stop",
        }],
    }, ensure_ascii=False).encode("utf-8")

    class FakeResponse:
        status_code = 200
        content = response_payload
        headers = {"content-type": "application/json"}

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json, headers):
            calls.append((url, json, headers))
            return FakeResponse()

    message_headers = {
        "X-Hermes-Session-Id": "session-durable-1",
        "X-Korra-Client-Message-Id": "12345678-1234-4234-8234-123456789abc",
    }
    request_body = {
        "model": "korra-agent",
        "messages": [{"role": "user", "content": "Проверь доставку"}],
        "stream": True,
    }
    first, second = asyncio.run(
        _post_chat_twice(
            monkeypatch, FakeAsyncClient, message_headers, request_body
        )
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert "Ответ" in first.text
    assert second.content == first.content
    assert second.headers["x-korra-delivery-state"] == "replayed"
    assert len(calls) == 1
    assert calls[0][1]["stream"] is False
    assert calls[0][2]["Idempotency-Key"] == "12345678-1234-4234-8234-123456789abc"


def test_concurrent_chat_requests_with_same_dashboard_token_are_authorized(
    monkeypatch, tmp_path
):
    """Two browser tabs may start independent durable turns simultaneously."""
    import httpx

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("API_SERVER_KEY", "test-key")
    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    web_server._CHAT_DELIVERY_TASKS.clear()
    claim_states = []

    async def _claim_without_calling_the_agent(
        *, message_id_raw, session_id, body, **_kwargs
    ):
        message_id = validate_client_message_id(message_id_raw)
        fingerprint = request_fingerprint(body, session_id)
        ledger = web_server._chat_delivery_ledger()

        state, _record = ledger.claim(message_id, fingerprint, session_id)
        claim_states.append(state)
        return Response(content=b'{"ok":true}', media_type="application/json")

    monkeypatch.setattr(
        web_server,
        "_durable_browser_chat_response",
        _claim_without_calling_the_agent,
    )

    async def _exercise():
        route_app = FastAPI()
        routes = [
            route
            for route in web_server.app.router.routes
            if getattr(route, "path", None) == "/api/chat/completions"
            and "POST" in (getattr(route, "methods", set()) or set())
        ]
        assert len(routes) == 1
        route_app.router.routes.append(routes[0])
        route_app.state.auth_required = False

        # Keep this a pure ASGI auth shim. Registering the function middleware
        # here would reintroduce the suite's unrelated BaseHTTPMiddleware hang;
        # the predicate below is the production legacy-token decision itself.
        async def _legacy_auth_app(scope, receive, send):
            request = Request(scope, receive=receive)
            if not web_server._has_valid_session_token(request):
                response = Response(
                    content=b'{"detail":"Unauthorized"}',
                    status_code=401,
                    media_type="application/json",
                )
                await response(scope, receive, send)
                return
            await route_app(scope, receive, send)

        responses = []

        async def _post(index):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_legacy_auth_app),
                base_url="http://127.0.0.1",
            ) as client:
                response = await client.post(
                    "/api/chat/completions",
                    headers={
                        "Authorization": f"Bearer {web_server._SESSION_TOKEN}",
                        "X-Hermes-Session-Id": f"concurrent-session-{index}",
                        "X-Korra-Client-Message-Id": (
                            f"12345678-1234-4234-8234-123456789ab{index}"
                        ),
                    },
                    json={
                        "model": "korra-agent",
                        "messages": [{"role": "user", "content": f"Запрос {index}"}],
                        "stream": True,
                    },
                )
            responses.append(response)

        with anyio.fail_after(10):
            async with anyio.create_task_group() as task_group:
                task_group.start_soon(_post, 1)
                task_group.start_soon(_post, 2)
        return responses

    responses = anyio.run(_exercise)

    assert sorted(response.status_code for response in responses) == [200, 200]
    assert claim_states == ["new", "new"]


def test_ambiguous_upstream_failure_stays_pending_and_blocks_duplicate(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("API_SERVER_KEY", "test-key")
    web_server._CHAT_DELIVERY_TASKS.clear()
    calls = 0

    class FailingAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, json, headers):
            nonlocal calls
            calls += 1
            raise RuntimeError("connection dropped after write")

    headers = {
        "X-Hermes-Session-Id": "session-ambiguous-1",
        "X-Korra-Client-Message-Id": "12345678-1234-4234-8234-123456789abc",
    }
    body = {
        "model": "korra-agent",
        "messages": [{"role": "user", "content": "Не дублировать"}],
        "stream": True,
    }
    first, second = asyncio.run(
        _post_chat_twice(monkeypatch, FailingAsyncClient, headers, body)
    )

    assert first.status_code == 502
    assert "Проверьте историю" in first.text
    assert second.status_code == 409
    assert calls == 1


def test_upstream_500_stays_pending_and_never_recomputes(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("API_SERVER_KEY", "test-key")
    web_server._CHAT_DELIVERY_TASKS.clear()
    calls = 0

    class ServerErrorResponse:
        status_code = 500
        content = b'{"detail":"failed after side effect"}'
        headers = {"content-type": "application/json"}

    class FailingAfterSideEffectClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, json, headers):
            nonlocal calls
            calls += 1
            return ServerErrorResponse()

    headers = {
        "X-Hermes-Session-Id": "session-http-500",
        "X-Korra-Client-Message-Id": "12345678-1234-4234-8234-123456789abc",
    }
    body = {
        "model": "korra-agent",
        "messages": [{"role": "user", "content": "Одно действие"}],
        "stream": True,
    }
    first, second = asyncio.run(
        _post_chat_twice(
            monkeypatch, FailingAfterSideEffectClient, headers, body
        )
    )

    assert first.status_code == 500
    assert second.status_code == 409
    assert calls == 1
