"""Read-only discovery and reattachment for the dashboard's durable chat turns."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

router = APIRouter()


def _server():
    from korra_cli import web_server
    return web_server


def _status(item, ledger):
    server = _server()
    run = server._CHAT_DELIVERY_STREAMS.get(f"{ledger.path}:{item['message_id']}")
    if run and not run.done:
        return getattr(run, "status", "running")
    return "interrupted" if item["status"] == "pending" else item["status"]


@router.get("/api/chat/runs")
async def chat_runs(profile: str | None = None, session_id: str | None = None):
    server = _server()
    ledger = server._chat_delivery_ledger()
    items = await server.run_in_threadpool(ledger.runs, profile, session_id)
    return {"runs": [{**item, "status": _status(item, ledger)} for item in items]}


@router.get("/api/chat/runs/{message_id}/stream")
async def resume_chat_run(message_id: str, session_id: str, profile: str = ""):
    server = _server()
    ledger = server._chat_delivery_ledger()
    record = await server.run_in_threadpool(ledger.response, message_id, profile, session_id)
    if record is None:
        raise HTTPException(404, "Ход не найден в этом чате")
    headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    run = server._CHAT_DELIVERY_STREAMS.get(f"{ledger.path}:{message_id}")
    if run is not None:
        return StreamingResponse(run.subscribe(), media_type="text/event-stream", headers=headers)
    if record.status == "completed" and record.response_body is not None:
        return Response(record.response_body, media_type=record.content_type, headers=headers)
    raise HTTPException(409, "Связь с ходом потеряна. Проверьте историю перед повторной отправкой.")

# Only admission (not the lifetime of the SSE reader) is serialised. Different
# profiles/sessions remain independent; two browser tabs cannot fork one turn.
from contextlib import asynccontextmanager
import asyncio
import functools

_admissions: dict[tuple[str, str], asyncio.Lock] = {}


def serialise_chat_admission(handler):
    @functools.wraps(handler)
    async def wrapped(*, session_id, target_profile="", **kwargs):
        key = (target_profile, session_id)
        lock = _admissions.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                server = _server()
                ledger = server._chat_delivery_ledger()
                for item in await server.run_in_threadpool(ledger.runs, target_profile, session_id):
                    if item["message_id"] != kwargs["message_id_raw"] and _status(item, ledger) in {"running", "queued"}:
                        raise HTTPException(409, "В этом чате агент ещё отвечает. Откройте его ход или начните другой чат.")
                return await handler(session_id=session_id, target_profile=target_profile, **kwargs)
        finally:
            # A waiting admission may still reference this lock; retain it
            # until the waiters have drained to avoid creating a second lock.
            if not lock.locked() and not getattr(lock, "_waiters", None):
                _admissions.pop(key, None)
    return wrapped


@asynccontextmanager
async def queued_upstream_stream(client, run, url, body, headers):
    """Queue only a confirmed gateway concurrency refusal, before any work."""
    deadline = asyncio.get_running_loop().time() + 600
    while True:
        async with client.stream("POST", url, json={**body, "stream": True}, headers=headers) as response:
            capacity_refusal = False
            if response.status_code == 429:
                raw = await response.aread()
                capacity_refusal = b"Too many concurrent runs" in raw
            if not capacity_refusal or asyncio.get_running_loop().time() >= deadline:
                run.status = "running"
                yield response
                return
            run.status = "queued"
            run.mark_started(200, "text/event-stream")
            await run.publish(b'event: korra.run.status\ndata: {"status":"queued"}\n\n')
        await asyncio.sleep(1)


@router.post("/api/chat/runs/{message_id}/cancel")
async def cancel_chat_run(message_id: str, session_id: str, profile: str = ""):
    from contextlib import suppress
    server = _server()
    ledger = server._chat_delivery_ledger()
    record = await server.run_in_threadpool(ledger.response, message_id, profile, session_id)
    if record is None:
        raise HTTPException(404, "Ход пока не найден. Попробуйте остановить ещё раз.")
    run = server._CHAT_DELIVERY_STREAMS.get(f"{ledger.path}:{message_id}")
    if run is None or run.done:
        return {"stopped": False}
    terminal = server._durable_stream_error_event("Ход остановлен пользователем.") + b"data: [DONE]\n\n"
    await run.publish(terminal)
    if run.task:
        # Explicit Stop closes the upstream SSE: its existing disconnect
        # handler interrupts the executor-backed agent and drains it.
        run.task.cancel()
        with suppress(asyncio.CancelledError):
            await run.task
    await server.run_in_threadpool(ledger.complete, message_id,
                                  response_body=b"".join(run.chunks), status_code=200,
                                  content_type="text/event-stream")
    return {"stopped": True}
