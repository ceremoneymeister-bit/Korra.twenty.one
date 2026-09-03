import asyncio
import json
from pathlib import Path
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


async def _post_chat_twice(
    monkeypatch,
    fake_upstream_client,
    headers,
    body,
    path="/api/chat/completions",
):
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
        first = await client.post(path, headers=headers, json=body)
        second = await client.post(path, headers=headers, json=body)
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


def test_delivery_fingerprint_separates_target_profiles():
    body = {"messages": [{"role": "user", "content": "Один текст"}]}
    assert request_fingerprint(body, "session-1", "default") != request_fingerprint(
        body, "session-1", "researcher"
    )


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
    web_server._CHAT_DELIVERY_STREAMS.clear()
    calls = []

    chunk_payload = json.dumps(
        {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "created": 123,
            "model": "korra-agent",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "Ответ"},
                    "finish_reason": "stop",
                }
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    response_payload = b"data: " + chunk_payload + b"\n\ndata: [DONE]\n\n"

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def aiter_raw(self):
            yield response_payload[:19]
            yield response_payload[19:]

    class FakeStreamContext:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *_args):
            return None

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, json, headers):
            assert method == "POST"
            calls.append((url, json, headers))
            return FakeStreamContext()

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
            monkeypatch,
            FakeAsyncClient,
            message_headers,
            request_body,
            "/api/chat/completions?profile=researcher",
        )
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert "Ответ" in first.text
    assert second.content == first.content
    assert second.headers["x-korra-delivery-state"] == "replayed"
    assert len(calls) == 1
    assert calls[0][0].endswith("/p/researcher/v1/chat/completions")
    assert calls[0][1]["stream"] is True
    assert calls[0][2]["Idempotency-Key"] == "12345678-1234-4234-8234-123456789abc"


def test_durable_stream_is_live_and_finishes_after_subscriber_disconnect(
    monkeypatch, tmp_path
):
    import httpx

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    first_chunk = 'data: {"choices":[{"delta":{"content":"сразу"}}]}\n\n'.encode()
    final_chunk = b"data: [DONE]\n\n"

    async def _exercise():
        release = asyncio.Event()

        class FakeResponse:
            status_code = 200
            headers = {"content-type": "text/event-stream"}

            async def aiter_raw(self):
                yield first_chunk
                await release.wait()
                yield final_chunk

        class FakeStreamContext:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *_args):
                return None

        class FakeAsyncClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def stream(self, _method, _url, json, headers):
                assert json["stream"] is True
                return FakeStreamContext()

        monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
        message_id = "12345678-1234-4234-8234-123456789abc"
        session_id = "session-live-stream"
        body = {
            "messages": [{"role": "user", "content": "Долго работай"}],
            "stream": True,
        }
        ledger = web_server._chat_delivery_ledger()
        ledger.claim(message_id, request_fingerprint(body, session_id), session_id)

        run = web_server._DurableBrowserChatStream()
        task = asyncio.create_task(
            web_server._run_durable_browser_chat_stream(
                run=run,
                message_id=message_id,
                upstream_url="http://agent/v1/chat/completions",
                body=body,
                headers={},
            )
        )
        run.task = task
        assert await run.started == (200, "text/event-stream")

        subscriber = run.subscribe()
        assert await asyncio.wait_for(anext(subscriber), timeout=1) == first_chunk
        await subscriber.aclose()
        assert not task.done()

        release.set()
        assert await asyncio.wait_for(task, timeout=1) == (
            200,
            first_chunk + final_chunk,
            "text/event-stream",
        )
        state, record = ledger.claim(
            message_id, request_fingerprint(body, session_id), session_id
        )
        assert state == "completed"
        assert record.response_body == first_chunk + final_chunk

    asyncio.run(_exercise())


def test_durable_stream_without_done_stays_pending(monkeypatch, tmp_path):
    import httpx

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    incomplete = 'data: {"choices":[{"delta":{"content":"часть"}}]}\n\n'.encode()

    async def _exercise():
        class FakeResponse:
            status_code = 200
            headers = {"content-type": "text/event-stream"}

            async def aiter_raw(self):
                yield incomplete

        class FakeStreamContext:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *_args):
                return None

        class FakeAsyncClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def stream(self, *_args, **_kwargs):
                return FakeStreamContext()

        monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
        message_id = "12345678-1234-4234-8234-123456789abc"
        session_id = "session-incomplete-stream"
        body = {
            "messages": [{"role": "user", "content": "Не обрывайся"}],
            "stream": True,
        }
        ledger = web_server._chat_delivery_ledger()
        fingerprint = request_fingerprint(body, session_id)
        ledger.claim(message_id, fingerprint, session_id)

        run = web_server._DurableBrowserChatStream()
        result = await web_server._run_durable_browser_chat_stream(
            run=run,
            message_id=message_id,
            upstream_url="http://agent/v1/chat/completions",
            body=body,
            headers={},
        )

        assert result[0] == 502
        state, record = ledger.claim(message_id, fingerprint, session_id)
        assert state == "pending"
        assert record.response_body is None
        assert run.chunks[0] == incomplete
        assert "пока не подтверждена".encode() in run.chunks[-1]

    asyncio.run(_exercise())


def test_chat_proxy_routes_profile_and_verified_attachment(monkeypatch, tmp_path):
    import httpx

    files_root = tmp_path / "files"
    attachment = files_root / "client" / "inbox" / "report.pdf"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"pdf data")
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(files_root))
    monkeypatch.setenv("API_SERVER_KEY", "test-key")
    calls = []

    class FakeResponse:
        status_code = 200
        content = b'{"ok":true}'
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

    async def _exercise():
        routes = [
            route
            for route in web_server.app.router.routes
            if getattr(route, "path", None) == "/api/chat/completions"
            and "POST" in (getattr(route, "methods", set()) or set())
        ]
        route_app = FastAPI()
        route_app.router.routes.append(routes[0])
        real_async_client = httpx.AsyncClient
        async with real_async_client(
            transport=httpx.ASGITransport(app=route_app),
            base_url="http://testserver",
        ) as client:
            monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
            return await client.post(
                "/api/chat/completions?profile=researcher",
                json={
                    "messages": [{"role": "user", "content": "Разбери"}],
                    "stream": False,
                    "attachments": [
                        {
                            "path": str(attachment),
                            "name": "Отчёт.pdf",
                            "kind": "pdf",
                            "size": len(b"pdf data"),
                            "reader": "forged-reader",
                        }
                    ],
                },
            )

    response = asyncio.run(_exercise())
    assert response.status_code == 200
    assert calls[0][0].endswith("/p/researcher/v1/chat/completions")
    forwarded = calls[0][1]
    assert "attachments" not in forwarded
    content = forwarded["messages"][-1]["content"]
    assert "[вложения]" in content
    assert "читать: pdf" in content
    assert str(attachment) in content
    assert "forged-reader" not in content


def test_chat_upload_lands_under_managed_client_root(monkeypatch, tmp_path):
    import httpx

    files_root = tmp_path / "files"
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(files_root))

    async def _exercise():
        routes = [
            route
            for route in web_server.app.router.routes
            if getattr(route, "path", None) == "/api/chat/upload"
            and "POST" in (getattr(route, "methods", set()) or set())
        ]
        assert len(routes) == 1
        route_app = FastAPI()
        route_app.router.routes.append(routes[0])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=route_app),
            base_url="http://testserver",
        ) as client:
            return await client.post(
                "/api/chat/upload",
                files={"file": ("Сводка.txt", "данные".encode(), "text/plain")},
            )

    response = asyncio.run(_exercise())
    assert response.status_code == 200
    payload = response.json()
    target = Path(payload["path"])
    assert target.is_relative_to(files_root / "client" / "inbox")
    assert target.read_bytes() == "данные".encode()
    assert payload["reader"] == "read_file"


def test_chat_upload_uses_requested_profile_home(monkeypatch, tmp_path):
    import httpx

    profile_home = tmp_path / "profiles" / "researcher"
    profile_home.mkdir(parents=True)
    monkeypatch.delenv("HERMES_DASHBOARD_FILES_ROOT", raising=False)
    monkeypatch.setattr(
        web_server,
        "_resolve_profile_dir",
        lambda profile: profile_home if profile == "researcher" else None,
    )

    async def _exercise():
        routes = [
            route
            for route in web_server.app.router.routes
            if getattr(route, "path", None) == "/api/chat/upload"
            and "POST" in (getattr(route, "methods", set()) or set())
        ]
        route_app = FastAPI()
        route_app.router.routes.append(routes[0])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=route_app),
            base_url="http://testserver",
        ) as client:
            return await client.post(
                "/api/chat/upload?profile=researcher",
                files={"file": ("Данные.txt", "профиль".encode(), "text/plain")},
            )

    response = asyncio.run(_exercise())
    assert response.status_code == 200
    target = Path(response.json()["path"])
    assert target.is_relative_to(profile_home / "client" / "inbox")
    assert target.read_bytes() == "профиль".encode()


def test_concurrent_chat_requests_with_same_dashboard_token_are_authorized(
    monkeypatch, tmp_path
):
    """Two browser tabs may start independent durable turns simultaneously."""
    import httpx

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("API_SERVER_KEY", "test-key")
    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    web_server._CHAT_DELIVERY_TASKS.clear()
    web_server._CHAT_DELIVERY_STREAMS.clear()
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
    web_server._CHAT_DELIVERY_STREAMS.clear()
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
        "stream": False,
    }
    first, second = asyncio.run(
        _post_chat_twice(monkeypatch, FailingAsyncClient, headers, body)
    )

    assert first.status_code == 502
    assert first.headers["x-korra-delivery-state"] == "pending"
    assert "Проверьте историю" in first.text
    assert second.status_code == 409
    assert calls == 1


def test_upstream_500_stays_pending_and_never_recomputes(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("API_SERVER_KEY", "test-key")
    web_server._CHAT_DELIVERY_TASKS.clear()
    web_server._CHAT_DELIVERY_STREAMS.clear()
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
        "stream": False,
    }
    first, second = asyncio.run(
        _post_chat_twice(
            monkeypatch, FailingAfterSideEffectClient, headers, body
        )
    )

    assert first.status_code == 500
    assert first.headers["x-korra-delivery-state"] == "pending"
    assert second.status_code == 409
    assert calls == 1


def test_claim_records_boot_id_and_reclaim_moves_it(tmp_path):
    ledger = DeliveryLedger(tmp_path / "ledger.sqlite3")
    fp = request_fingerprint({"messages": [{"role": "user", "content": "привет"}]}, "s-1")
    state, record = ledger.claim("msg-boot-0000000001", fp, "s-1", "boot-A")
    assert (state, record.boot_id) == ("new", "boot-A")
    state, record = ledger.claim("msg-boot-0000000001", fp, "s-1", "boot-B")
    assert (state, record.boot_id) == ("pending", "boot-A")
    ledger.reclaim_after_restart("msg-boot-0000000001", "boot-B")
    state, record = ledger.claim("msg-boot-0000000001", fp, "s-1", "boot-B")
    assert (state, record.boot_id) == ("pending", "boot-B")


def test_fingerprint_depends_on_last_user_turn_only():
    a = request_fingerprint(
        {"messages": [{"role": "user", "content": "раньше"}, {"role": "assistant", "content": "ответ"}, {"role": "user", "content": "вопрос"}]},
        "s-1",
    )
    b = request_fingerprint({"messages": [{"role": "user", "content": "вопрос"}]}, "s-1")
    c = request_fingerprint({"messages": [{"role": "user", "content": "другой"}]}, "s-1")
    assert a == b and a != c

