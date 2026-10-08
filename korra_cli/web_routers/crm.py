"""CRM connection of the installation, used by the «Продажи» dashboard card.

The body is read as plain JSON on purpose: a validation error from a typed
model would echo the submitted key back. Nothing here returns or logs a secret;
see :func:`korra_cli.crm_connection.public` for what is exposed. A CRM-side
failure answers 200 with ``{"ok": false, "error": {...}}`` so the dialog can
show it next to the field; a malformed request or a missing connection answers
with its own HTTP status.
"""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


async def _body(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


async def _run(func, *args, **kwargs):
    from korra_cli import crm_sales
    from korra_cli.crm_connection import CrmConnectionError

    try:
        result = await asyncio.to_thread(func, *args, **kwargs)
    except CrmConnectionError as exc:
        return JSONResponse(
            {"ok": False, "error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status_code
        )
    crm_sales.reset()
    return result


@router.get("/api/dashboard/crm")
async def get_crm_connection():
    from korra_cli import crm_connection

    return await asyncio.to_thread(crm_connection.status)


@router.post("/api/dashboard/crm/check")
async def check_crm_connection(request: Request):
    """Check typed credentials without saving; an empty body rechecks the saved connection."""
    from korra_cli import crm_connection

    return await _run(crm_connection.check, await _body(request))


@router.put("/api/dashboard/crm")
async def save_crm_connection(request: Request):
    from korra_cli import crm_connection

    return await _run(crm_connection.save, await _body(request))


@router.patch("/api/dashboard/crm")
async def update_crm_settings(request: Request):
    from korra_cli import crm_connection

    return await _run(crm_connection.update_settings, await _body(request))


@router.post("/api/dashboard/crm/adopt")
async def adopt_agent_crm_key(request: Request):
    from korra_cli import crm_connection

    body = await _body(request)
    return await _run(crm_connection.adopt, body.get("profile"), body.get("type"))


@router.delete("/api/dashboard/crm")
async def disconnect_crm():
    from korra_cli import crm_connection

    return await _run(crm_connection.disconnect)
