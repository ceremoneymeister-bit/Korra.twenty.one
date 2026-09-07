"""Operator document jobs: durable acceptance and authorized snapshot artifacts."""
from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import StreamingResponse

from .calc_files import _require_front, _start_admin
from .calc_rates import _run_admin

router = APIRouter()
ERROR_STATUSES = {"Conflict": 409, "NotFound": 404, "OrderScopeDenied": 403,
                  "PathEscape": 403, "StorageUnavailable": 503, "InvalidState": 422,
                  "InvalidIdentifier": 422, "FileTooLarge": 413}


async def _admin(args, stdin=None):
    _require_front()
    return await _run_admin(args, stdin=stdin, error_statuses=ERROR_STATUSES, default_error_status=422)


@router.post("/api/calc/orders/{order_id}/document-jobs", status_code=202)
async def start_job(order_id: str, request: Request):
    _require_front()
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            raise HTTPException(413, "Параметры задания слишком большие")
    return await _admin(["document-job-start", "--order-id", order_id], bytes(body) or b"{}")


@router.get("/api/calc/orders/{order_id}/document-jobs")
async def list_jobs(order_id: str):
    return await _admin(["document-jobs-list", "--order-id", order_id])


@router.get("/api/calc/document-jobs/{job_id}")
@router.get("/api/calc/document-jobs/{job_id}/result")
async def job_status(job_id: str, source_limit: int = 200, source_offset: int = 0):
    result = await _admin(["document-job-status", "--job-id", job_id,
                           "--source-limit", str(source_limit), "--source-offset", str(source_offset)])
    for source in result["sources"]:
        base = f"/api/calc/document-jobs/{quote(job_id, safe='')}/sources/{quote(source['source_id'], safe='')}"
        source.update(result_url=base, download_url=base + "/download")
    return result


@router.post("/api/calc/document-jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    return await _admin(["document-job-cancel", "--job-id", job_id])


@router.post("/api/calc/document-jobs/{job_id}/retry")
async def retry_job(job_id: str):
    return await _admin(["document-job-retry", "--job-id", job_id])


@router.get("/api/calc/document-jobs/{job_id}/sources/{source_id}")
async def source_result(job_id: str, source_id: str):
    result = await _admin(["document-source-result", "--job-id", job_id, "--source-id", source_id])
    if result.get("image"):
        result["image"]["download_url"] = (
            f"/api/calc/document-jobs/{quote(job_id, safe='')}/sources/{quote(source_id, safe='')}/image")
    return result


async def _download(job_id, source_id, kind):
    args = ["--job-id", job_id, "--source-id", source_id]
    info = await _admin([f"document-{kind}-info", *args])
    process = await _start_admin([f"document-{kind}-read", *args])
    process.stdin.close()

    async def content():
        stderr = asyncio.create_task(process.stderr.read())
        try:
            async with asyncio.timeout(300):
                while chunk := await process.stdout.read(1024 * 1024):
                    yield chunk
                await process.wait()
                if process.returncode:
                    raise RuntimeError("Document download did not complete")
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
            await stderr
    return StreamingResponse(content(), media_type="image/png" if kind == "image" else "application/octet-stream",
                             headers={"Content-Length": str(info["bytes"]), "Cache-Control": "private, no-store",
                                      "X-Content-Type-Options": "nosniff",
                                      "Content-Disposition": "attachment; filename*=UTF-8''" + quote(info["name"], safe="")})


@router.get("/api/calc/document-jobs/{job_id}/sources/{source_id}/download")
async def source_download(job_id: str, source_id: str):
    return await _download(job_id, source_id, "source")


@router.get("/api/calc/document-jobs/{job_id}/sources/{source_id}/image")
async def image_download(job_id: str, source_id: str):
    return await _download(job_id, source_id, "image")
