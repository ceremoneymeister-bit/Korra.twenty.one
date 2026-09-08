"""Operator document jobs: durable acceptance and authorized snapshot artifacts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import StreamingResponse

from .calc_files import _require_front, _start_admin
from .calc_rates import _run_admin

_log = logging.getLogger("korra_cli.web_server")
router = APIRouter()
ERROR_STATUSES = {"Conflict": 409, "NotFound": 404, "OrderScopeDenied": 403,
                  "PathEscape": 403, "StorageUnavailable": 503, "InvalidState": 422,
                  "InvalidIdentifier": 422, "FileTooLarge": 413}

# Framed download protocol shared with ``metal_calc.document_admin``: the CLI
# writes one bounded JSON line, then exactly ``bytes`` octets from the same
# verified read. HTTP status is decided only after that line is validated, so
# a typed error can never be served as document bytes with a 200.
STREAM_PROTOCOL = "metal-calc-document-stream"
STREAM_VERSION = 1
STREAM_LIMITS = {"source": 100 * 1024**2, "image": 64 * 1024**2}
STREAM_MEDIA = {"source": "application/octet-stream", "image": "image/png"}
HEADER_LIMIT_BYTES = 16 * 1024
HEADER_TIMEOUT_SECONDS = 60
STREAM_TIMEOUT_SECONDS = 300
RETRY_BODY_LIMIT = 4096
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UNEXPECTED = "Хранилище документов ответило неожиданно"


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


async def _retry_source(request: Request):
    """Empty body or ``{}`` keeps the whole-job retry; ``{"source_id": str}`` targets one source."""
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > RETRY_BODY_LIMIT:
            raise HTTPException(413, "Параметры повтора слишком большие")
    if not body.strip():
        return None
    try:
        payload = json.loads(bytes(body))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(422, "Параметры повтора должны быть JSON-объектом") from exc
    if not isinstance(payload, dict) or set(payload) - {"source_id"}:
        raise HTTPException(422, "Неизвестные параметры повтора")
    source_id = payload.get("source_id")
    if source_id is None:
        return None
    if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 256:
        raise HTTPException(422, "Некорректный идентификатор источника")
    return source_id


@router.post("/api/calc/document-jobs/{job_id}/retry")
async def retry_job(job_id: str, request: Request):
    _require_front()
    source_id = await _retry_source(request)
    args = ["document-job-retry", "--job-id", job_id]
    if source_id is not None:
        args += ["--source-id", source_id]
    return await _admin(args)


@router.get("/api/calc/document-jobs/{job_id}/sources/{source_id}")
async def source_result(job_id: str, source_id: str):
    result = await _admin(["document-source-result", "--job-id", job_id, "--source-id", source_id])
    if result.get("image"):
        result["image"]["download_url"] = (
            f"/api/calc/document-jobs/{quote(job_id, safe='')}/sources/{quote(source_id, safe='')}/image")
    return result


async def _drain_stderr(stream, keep=4096) -> bytes:
    """Consume child stderr without buffering it whole; keep a diagnostic prefix."""
    head = bytearray()
    while chunk := await stream.read(65536):
        if len(head) < keep:
            head.extend(chunk[:keep - len(head)])
    return bytes(head)


async def _discard_stdout(stream) -> None:
    """Release subprocess pipe backpressure without retaining response bytes."""
    while await stream.read(65536):
        pass


async def _reap(process, stderr_task) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    _, _, stderr = await asyncio.gather(
        process.wait(), _discard_stdout(process.stdout), stderr_task,
        return_exceptions=True,
    )
    if process.returncode and isinstance(stderr, bytes) and stderr:
        _log.warning("calc documents: stream child rc=%s: %.200s",
                     process.returncode, stderr.decode("utf-8", "replace"))


class _ProcessStreamCleanup:
    """Give the response and its body iterator one shielded cleanup task."""

    def __init__(self, process, stderr_task):
        self.process = process
        self.stderr_task = stderr_task
        self.task = None

    async def __call__(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(_reap(self.process, self.stderr_task))
        await asyncio.shield(self.task)


class _OwnedStreamingResponse(StreamingResponse):
    def __init__(self, *args, cleanup, **kwargs):
        super().__init__(*args, **kwargs)
        self._cleanup = cleanup

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # ``http.response.start`` can fail before Starlette first enters
            # the body iterator, so the response owns the child lifecycle.
            await self._cleanup()


def _validate_header(payload, kind) -> dict:
    """Return the checked frame header or raise the HTTP error it implies."""
    if not isinstance(payload, dict):
        raise HTTPException(503, _UNEXPECTED)
    error = payload.get("error")
    if isinstance(error, dict) and "protocol" not in payload:
        code = error.get("code")
        message = error.get("message")
        raise HTTPException(ERROR_STATUSES.get(code, 422) if isinstance(code, str) else 503,
                            message if isinstance(message, str) and message else _UNEXPECTED)
    if payload.get("protocol") != STREAM_PROTOCOL or payload.get("version") != STREAM_VERSION:
        raise HTTPException(503, _UNEXPECTED)
    if payload.get("kind") != kind:
        raise HTTPException(503, _UNEXPECTED)
    size, digest, name = payload.get("bytes"), payload.get("sha256"), payload.get("name")
    if type(size) is not int or not 0 <= size <= STREAM_LIMITS[kind]:
        raise HTTPException(503, _UNEXPECTED)
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise HTTPException(503, _UNEXPECTED)
    if (not isinstance(name, str) or not name or len(name) > 1024 or "/" in name or "\\" in name
            or name in {".", ".."} or any(ord(ch) < 32 or ch == "\x7f" for ch in name)):
        raise HTTPException(503, _UNEXPECTED)
    return {"kind": kind, "bytes": size, "sha256": digest, "name": name}


async def _read_header(process, kind) -> dict:
    """Read and validate the frame header; the child is still alive on success."""
    try:
        async with asyncio.timeout(HEADER_TIMEOUT_SECONDS):
            try:
                line = await process.stdout.readuntil(b"\n")
            except asyncio.IncompleteReadError as exc:
                line = exc.partial
                if not line.strip():
                    # Died without any frame (crash before the typed error line).
                    raise HTTPException(503, _UNEXPECTED) from exc
            except asyncio.LimitOverrunError as exc:
                raise HTTPException(503, _UNEXPECTED) from exc
    except TimeoutError as exc:
        raise HTTPException(504, "Хранилище документов не ответило вовремя") from exc
    if len(line) > HEADER_LIMIT_BYTES:
        raise HTTPException(503, _UNEXPECTED)
    try:
        payload = json.loads(line.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(503, _UNEXPECTED) from exc
    header = _validate_header(payload, kind)
    return header


async def _download(job_id, source_id, kind):
    process = await _start_admin([f"document-{kind}-stream", "--job-id", job_id, "--source-id", source_id])
    process.stdin.close()
    stderr_task = asyncio.create_task(_drain_stderr(process.stderr))
    cleanup = _ProcessStreamCleanup(process, stderr_task)
    try:
        header = await _read_header(process, kind)
    except BaseException:
        # The read is side-effect free, so a child that produced a typed
        # error, a malformed frame or nothing at all is simply reaped.
        await cleanup()
        raise

    async def content():
        remaining = header["bytes"]
        digest = hashlib.sha256()
        try:
            async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
                while remaining:
                    chunk = await process.stdout.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise RuntimeError("Document stream truncated")
                    remaining -= len(chunk)
                    digest.update(chunk)
                    yield chunk
                trailing = await process.stdout.read(1)
                if trailing:
                    raise RuntimeError("Document stream did not complete")
                await process.wait()
                if process.returncode or digest.hexdigest() != header["sha256"]:
                    raise RuntimeError("Document stream did not complete")
        finally:
            await cleanup()

    try:
        return _OwnedStreamingResponse(
            content(), cleanup=cleanup, media_type=STREAM_MEDIA[kind],
            headers={"Content-Length": str(header["bytes"]), "Cache-Control": "private, no-store",
                     "X-Content-Type-Options": "nosniff",
                     "Content-Disposition": "attachment; filename*=UTF-8''" + quote(header["name"], safe="")},
        )
    except BaseException:
        await cleanup()
        raise


@router.get("/api/calc/document-jobs/{job_id}/sources/{source_id}/download")
async def source_download(job_id: str, source_id: str):
    return await _download(job_id, source_id, "source")


@router.get("/api/calc/document-jobs/{job_id}/sources/{source_id}/image")
async def image_download(job_id: str, source_id: str):
    return await _download(job_id, source_id, "image")
