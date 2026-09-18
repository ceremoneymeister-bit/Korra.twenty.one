"""Exercise the actual delivery ledger, router and durable SSE fan-out."""
import asyncio
import json

import httpx
from fastapi import FastAPI, HTTPException
import pytest

from korra_cli import web_server
from korra_cli.chat_runs import router, chat_runs, resume_chat_run, queued_upstream_stream


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("KORRA_HOME", str(tmp_path))
    monkeypatch.setattr(web_server, "_CHAT_DELIVERY_STREAMS", {})
    return web_server._chat_delivery_ledger()


def remember(ledger, message="message-1234567890123456", profile="lawyer", session="session-a"):
    ledger.claim(message, message, session, "old-boot")
    ledger.remember_request(message, profile, {"messages": [{"role": "user", "content": "Проверка"}]})
    return message


def test_active_reconnect_replays_prefix_and_tail_once_after_detach(isolated):
    async def scenario():
        mid = remember(isolated)
        run = web_server._DurableBrowserChatStream()
        web_server._CHAT_DELIVERY_STREAMS[f"{isolated.path}:{mid}"] = run
        run.mark_started(200, "text/event-stream")
        first = b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
        tail = b'data: {"choices":[{"delta":{"content":"last"}}]}\n\ndata: [DONE]\n\n'
        await run.publish(first)
        response = await resume_chat_run(mid, "session-a", "lawyer")
        reader = response.body_iterator
        assert await anext(reader) == first
        await reader.aclose()  # Navigation, not a stop command.
        assert not run.done
        assert (await chat_runs("lawyer", "session-a"))["runs"][0]["status"] == "running"
        again = await resume_chat_run(mid, "session-a", "lawyer")
        reader = again.body_iterator
        assert await anext(reader) == first
        await run.publish(tail)
        isolated.complete(mid, response_body=first + tail, status_code=200, content_type="text/event-stream")
        await run.finish((200, first + tail, "text/event-stream"))
        assert await anext(reader) == tail
        with pytest.raises(StopAsyncIteration):
            await anext(reader)
        web_server._CHAT_DELIVERY_STREAMS.clear()
        completed = await resume_chat_run(mid, "session-a", "lawyer")
        assert completed.body == first + tail
        assert (await chat_runs("lawyer", "session-a"))["runs"][0]["status"] == "completed"
    asyncio.run(scenario())


def test_router_scope_and_abandoned_work_never_restarts_model(isolated):
    mid = remember(isolated)
    async def scenario():
        app = FastAPI()
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            listed = await client.get("/api/chat/runs", params={"profile": "lawyer"})
            assert listed.json()["runs"][0]["status"] == "interrupted"
            assert (await client.get("/api/chat/runs", params={"profile": "accountant"})).json() == {"runs": []}
            for profile, session in [("accountant", "session-a"), ("lawyer", "session-b")]:
                response = await client.get(f"/api/chat/runs/{mid}/stream", params={"profile": profile, "session_id": session})
                assert response.status_code == 404
            response = await client.get(f"/api/chat/runs/{mid}/stream", params={"profile": "lawyer", "session_id": "session-a"})
            assert response.status_code == 409
        assert isolated.response(mid, "lawyer", "session-a").boot_id == "old-boot"
    asyncio.run(scenario())


def test_queue_retries_only_pre_execution_capacity_refusal(isolated, monkeypatch):
    async def scenario():
        calls = []
        def handle(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(429, json={"error": {"message": "Too many concurrent runs (max 1)"}})
            return httpx.Response(200, content=b"data: [DONE]\n\n")
        run = web_server._DurableBrowserChatStream()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            async with queued_upstream_stream(client, run, "http://agent/v1/chat/completions", {}, {}) as response:
                assert response.status_code == 200
                assert len(calls) == 2
                assert run.status == "running"
                assert b'"queued"' in b"".join(run.chunks)
        # A provider rate limit is not admission: do not retry ambiguous work.
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(429, json={"error": "provider quota"}))) as client:
            async with queued_upstream_stream(client, run, "http://agent/v1/chat/completions", {}, {}) as response:
                assert response.status_code == 429
    asyncio.run(scenario())


def test_second_message_in_busy_session_is_rejected_but_sibling_is_allowed(isolated):
    from korra_cli.chat_runs import serialise_chat_admission
    async def scenario():
        mid = remember(isolated)
        run = web_server._DurableBrowserChatStream()
        web_server._CHAT_DELIVERY_STREAMS[f"{isolated.path}:{mid}"] = run
        called = []
        @serialise_chat_admission
        async def handler(**kwargs):
            called.append(kwargs)
            return "accepted"
        with pytest.raises(HTTPException) as exc:
            await handler(session_id="session-a", target_profile="lawyer", message_id_raw="message-other-1234567890")
        assert exc.value.status_code == 409
        assert not called
        assert await handler(session_id="session-b", target_profile="lawyer", message_id_raw="message-other-1234567890") == "accepted"
        assert await handler(session_id="session-a", target_profile="accountant", message_id_raw="message-other-1234567890") == "accepted"
    asyncio.run(scenario())


def test_failed_stream_is_not_a_ready_answer(isolated):
    mid = remember(isolated)
    isolated.complete(mid, response_body=web_server._durable_stream_error_event("Отказ провайдера") + b"data: [DONE]\n\n", status_code=200, content_type="text/event-stream")
    assert asyncio.run(chat_runs("lawyer", "session-a"))["runs"][0]["status"] == "failed"


def test_explicit_cancel_stops_queued_task_and_retains_terminal_replay(isolated):
    from korra_cli.chat_runs import cancel_chat_run
    async def scenario():
        mid = remember(isolated)
        run = web_server._DurableBrowserChatStream()
        run.status = "queued"
        exited = asyncio.Event()
        async def waiting():
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()
                await run.finish((503, b"", "application/json"))
        run.task = asyncio.create_task(waiting())
        await asyncio.sleep(0)
        web_server._CHAT_DELIVERY_STREAMS[f"{isolated.path}:{mid}"] = run
        assert await cancel_chat_run(mid, "session-a", "lawyer") == {"stopped": True}
        assert exited.is_set()
        record = isolated.response(mid, "lawyer", "session-a")
        assert b"[DONE]" in record.response_body
        assert (await chat_runs("lawyer", "session-a"))["runs"][0]["status"] == "failed"
    asyncio.run(scenario())


def test_open_session_lists_every_intent_but_global_poll_keeps_latest_only(isolated):
    first = remember(isolated, "first-message-1234567890")
    isolated.fail(first)
    second = remember(isolated, "second-message-123456789")
    isolated.fail(second)

    opened = asyncio.run(chat_runs("lawyer", "session-a"))["runs"]
    assert [item["message_id"] for item in opened] == [second, first]
    polled = asyncio.run(chat_runs("lawyer"))["runs"]
    assert [item["message_id"] for item in polled] == [second]


def test_failed_run_projects_reset_metadata_for_browser_recovery(isolated):
    mid = remember(isolated, "quota-message-123456789")
    body = (
        b'data: {"choices":[{"delta":{},"finish_reason":"error"}],'
        b'"error":{"message":"Model usage limit reached",'
        b'"reason":"rate_limit","resets_at":"2026-09-19T10:30:00Z"}}\n\n'
        b"data: [DONE]\n\n"
    )
    isolated.complete(
        mid,
        response_body=body,
        status_code=200,
        content_type="text/event-stream",
    )

    run = asyncio.run(chat_runs("lawyer", "session-a"))["runs"][0]
    assert run["status"] == "failed"
    assert run["failure"] == {
        "message": "Model usage limit reached",
        "reason": "rate_limit",
        "resets_at": "2026-09-19T10:30:00Z",
    }
