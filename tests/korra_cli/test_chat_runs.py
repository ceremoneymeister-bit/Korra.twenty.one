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


def test_corrupt_effect_store_surfaces_as_degraded_503(isolated, monkeypatch):
    from korra_cli import chat_activity
    from tools.effect_decisions import EffectDecisionStoreUnavailable

    def _raise_store_unavailable(*_args, **_kwargs):
        raise EffectDecisionStoreUnavailable()

    monkeypatch.setattr(
        chat_activity,
        "project_chat_activity",
        _raise_store_unavailable,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(chat_runs("lawyer"))

    assert exc.value.status_code == 503
    assert "требуется восстановление" in str(exc.value.detail)


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


def test_opened_chat_learns_where_the_turn_lies_in_history(isolated, monkeypatch, tmp_path):
    """0.21.15, ревью Astra R2: для открытого чата журнал говорит, в какой
    строке истории лежит реплика хода, — клиент не угадывает по тексту."""
    from korra_state import SessionDB

    written = remember(isolated, message="written-1234567890123456")
    waiting = remember(isolated, message="waiting-1234567890123456")
    db_path = tmp_path / "state-runs.db"
    db = SessionDB(db_path)
    db.create_session("session-a", source="dashboard")
    db.append_message("session-a", role="user", content="Проверка",
                      display_metadata={"client_message_id": written})
    row_id = db.get_messages("session-a")[-1]["id"]
    db.close()
    monkeypatch.setattr(web_server, "_open_session_db_for_profile",
                        lambda profile, read_only=True: SessionDB(db_path))

    runs = {run["message_id"]: run for run in asyncio.run(chat_runs("lawyer", "session-a"))["runs"]}
    assert runs[written]["history_row_id"] == row_id
    assert runs[waiting]["history_row_id"] is None
    assert runs[written]["turn_tracked"] is True
    # Опрос оболочки без открытого чата базу не читает.
    assert all("history_row_id" not in run for run in asyncio.run(chat_runs("lawyer"))["runs"])


def test_a_turn_admitted_before_compression_is_seen_from_the_continuation(isolated, monkeypatch, tmp_path):
    """Ревью Astra, раунд 6, P2-7: ход принят под id родителя, во время него
    сжатие открыло продолжение; чат, открытый по новому id, видит этот ход, а
    поток и отмена идут по сессии записи."""
    from korra_state import SessionDB

    mid = remember(isolated, message="before-compress-12345678", session="parent-s")
    db_path = tmp_path / "state-chain.db"
    db = SessionDB(db_path)
    db.create_session("parent-s", source="dashboard")
    db.append_message("parent-s", role="user", content="Проверка",
                      display_metadata={"client_message_id": mid})
    db.end_session("parent-s", "compression")
    db.create_session("tip-s", source="compression", parent_session_id="parent-s")
    db.append_message("tip-s", role="assistant", content="продолжение")
    db.close()
    monkeypatch.setattr(web_server, "_open_session_db_for_profile",
                        lambda profile, read_only=True: SessionDB(db_path))

    runs = asyncio.run(chat_runs("lawyer", "tip-s"))["runs"]
    assert [run["message_id"] for run in runs] == [mid]
    assert runs[0]["session_id"] == "parent-s"
    assert runs[0]["history_row_id"] is not None


def test_an_opened_long_chat_does_not_pull_every_old_prompt(isolated):
    """Третье чистое ревью Astra, P2-3: открытие чата не тянет сотни старых
    ходов с полными запросами — только последние, и полный текст лишь у тех,
    что могут понадобиться для восстановления."""
    long_text = "длинное письмо " * 700
    for index in range(120):
        mid = f"old-run-{index:04d}-1234567890ab"
        isolated.claim(mid, mid, "session-a", "old-boot")
        isolated.remember_request(mid, "lawyer", {"messages": [{"role": "user", "content": long_text}]})
        isolated.complete(mid, response_body=b"data: [DONE]\n\n", status_code=200, content_type="text/event-stream")
    runs = asyncio.run(chat_runs("lawyer", "session-a"))["runs"]
    assert len(runs) == 50
    assert all(len(run["user_message"]["content"]) == len(long_text) for run in runs[:10])
    assert all(len(run["user_message"]["content"]) <= 160 for run in runs[10:])
    assert len(json.dumps(runs, ensure_ascii=False)) < 200_000


def test_conditional_poll_tracks_final_snapshot_and_scope(isolated):
    async def scenario():
        mid = remember(isolated)
        app = FastAPI()
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            query = {"profile": "lawyer", "session_id": "session-a"}
            first = await client.get("/api/chat/runs", params=query)
            etag = first.headers["etag"]
            unchanged = await client.get("/api/chat/runs", params=query, headers={"If-None-Match": etag})
            assert unchanged.status_code == 304 and unchanged.content == b""
            assert unchanged.headers["cache-control"] == "private, no-cache"
            other = await client.get("/api/chat/runs", params={"profile": "accountant"}, headers={"If-None-Match": etag})
            assert other.status_code == 200 and other.headers["etag"] != etag
            isolated.complete(mid, response_body=b'{"choices":[]}', status_code=200, content_type="application/json")
            changed = await client.get("/api/chat/runs", params=query, headers={"If-None-Match": etag})
            assert changed.status_code == 200 and changed.headers["etag"] != etag
            assert changed.json()["runs"][0]["status"] == "completed"
            # The old SPA does not send a conditional header and keeps receiving JSON.
            assert (await client.get("/api/chat/runs", params=query)).status_code == 200
    asyncio.run(scenario())
