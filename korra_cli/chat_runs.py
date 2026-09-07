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
