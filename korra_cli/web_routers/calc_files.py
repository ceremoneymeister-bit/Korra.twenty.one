"""Operator folder intake: stream bytes to the canonical calculator CLI."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import StreamingResponse

from .calc_rates import _admin_binary, _metal_calc_env, _run_admin

router = APIRouter()
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 100 * 1024 * 1024
STREAM_TIMEOUT_SECONDS = 300
ERROR_STATUSES = {"Conflict": 409, "NotFound": 404, "FileTooLarge": 413,
                  "StorageUnavailable": 503, "InvalidIdentifier": 422,
                  "InvalidState": 422, "PathEscape": 403}


def _require_front() -> None:
    # The authenticated operator cabinet owns intake. Named model profiles
    # retain their existing stage scopes and cannot create or browse folders.
    if _metal_calc_env().get("METAL_CALC_ROLE") != "front":
        raise HTTPException(403, "Папки заказов доступны в кабинете приёма заказов")


async def _admin(args: list[str], *, stdin: bytes | None = None) -> dict[str, Any]:
    _require_front()
    return await _run_admin(args, stdin=stdin, error_statuses=ERROR_STATUSES,
                            default_error_status=422)


async def _start_admin(args: list[str]):
    _require_front()
    binary = _admin_binary()
    if binary is None:
        raise HTTPException(503, "Расчётчик не установлен на этом контуре")
    try:
        return await asyncio.create_subprocess_exec(
            str(binary), *args, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **_metal_calc_env()},
        )
    except OSError as exc:
        raise HTTPException(503, "Не удалось запустить загрузку файлов") from exc


def _result(stdout: bytes, returncode: int) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(503, "Хранилище файлов ответило неожиданно") from exc
    if not isinstance(payload, dict):
        raise HTTPException(503, "Хранилище файлов ответило неожиданно")
    if returncode:
        error = payload.get("error", {})
        raise HTTPException(ERROR_STATUSES.get(error.get("code"), 422),
                            error.get("message") or "Не удалось сохранить файл")
    return payload


async def _stop_process(process) -> None:
    if process.stdin:
        process.stdin.close()
    if process.returncode is None:
        try:
            # EOF lets the canonical bounded writer clean its incomplete temp.
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()


async def _read_process_output(process) -> tuple[bytes, bytes]:
    # communicate(None) may close stdin (including on the deployed Python 3.13).
    # The request handler owns stdin until the last uploaded byte arrives.
    stdout, stderr = await asyncio.gather(process.stdout.read(), process.stderr.read())
    await process.wait()
    return stdout, stderr


def _file_urls(order_id: str, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**item, "download_url": f"/api/calc/folders/{quote(order_id, safe='')}/files/{item['index']}"}
            for item in files]


@router.get("/api/calc/folders")
async def folder_list():
    return await _admin(["folder-list"])


@router.post("/api/calc/folder-uploads")
async def folder_upload_create(request: Request):
    _require_front()
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_MANIFEST_BYTES:
            raise HTTPException(413, "Список файлов слишком большой")
        data.extend(chunk)
    return await _admin(["folder-upload-create"], stdin=bytes(data))


@router.get("/api/calc/folder-uploads/{upload_id}")
async def folder_upload_status(upload_id: str):
    return await _admin(["folder-upload-status", "--upload-id", upload_id])


@router.put("/api/calc/folder-uploads/{upload_id}/files/{index}")
async def folder_upload_file(upload_id: str, index: int, request: Request):
    process = await _start_admin(["folder-upload-file", "--upload-id", upload_id,
                                  "--index", str(index)])
    response = asyncio.create_task(_read_process_output(process))
    total = 0
    try:
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
            try:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > MAX_FILE_BYTES:
                        raise HTTPException(413, "Размер одного файла превышает 100 МиБ")
                    process.stdin.write(chunk)
                    await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass  # CLI validation rejected the file; read its typed error.
            finally:
                process.stdin.close()
            stdout, _stderr = await response
            return _result(stdout, process.returncode)
    except asyncio.TimeoutError as exc:
        raise HTTPException(504, "Загрузка заняла слишком много времени; повторите её") from exc
    finally:
        await _stop_process(process)
        if not response.done():
            response.cancel()
        await asyncio.gather(response, return_exceptions=True)


@router.post("/api/calc/folder-uploads/{upload_id}/complete")
async def folder_upload_complete(upload_id: str):
    return await _admin(["folder-upload-complete", "--upload-id", upload_id])


@router.get("/api/calc/folders/{order_id}")
async def folder_detail(order_id: str):
    detail = await _admin(["folder-detail", "--order-id", order_id])
    detail["source_files"] = _file_urls(order_id, detail["source_files"])
    return detail


@router.get("/api/calc/folders/{order_id}/files/{index}")
async def folder_download(order_id: str, index: int):
    info = await _admin(["folder-file-info", "--order-id", order_id, "--index", str(index)])
    process = await _start_admin(["folder-file-read", "--order-id", order_id, "--index", str(index)])
    process.stdin.close()

    async def content():
        stderr = asyncio.create_task(process.stderr.read())
        try:
            async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
                while chunk := await process.stdout.read(1024 * 1024):
                    yield chunk
                await process.wait()
                if process.returncode:
                    raise RuntimeError("File download did not complete")
        finally:
            # A disconnected download has no pending write to finish.
            if process.returncode is None:
                process.kill()
            await process.wait()
            await stderr

    return StreamingResponse(content(), media_type="application/octet-stream", headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(info["name"], safe=""),
        "Content-Length": str(info["bytes"]), "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    })
