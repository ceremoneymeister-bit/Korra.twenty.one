"""Render one PDF page using Poppler, already shipped in the managed image."""
import base64
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading

from fastapi import HTTPException

from korra_cli._subprocess_compat import windows_hide_flags

_SLOTS = threading.BoundedSemaphore(2)
_MAX_BYTES = 50 * 1024 * 1024


def read_pdf_page(target: Path, page: int) -> dict:
    if page < 1:
        raise HTTPException(400, "Номер страницы должен быть больше нуля.")
    if target.suffix.lower() != ".pdf":
        raise HTTPException(415, "Этот файл не является PDF. Скачайте оригинал.")
    if target.stat().st_size > _MAX_BYTES:
        raise HTTPException(413, "PDF больше 50 МБ. Скачайте оригинал для просмотра.")
    info = shutil.which("pdfinfo")
    renderer = shutil.which("pdftoppm")
    if not info or not renderer:
        raise HTTPException(503, "Предпросмотр PDF здесь недоступен. Скачайте оригинал.")
    if not _SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Сейчас открываются другие PDF. Повторите через несколько секунд.")
    try:
        options = dict(stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
                       check=True, creationflags=windows_hide_flags())
        metadata = subprocess.run(
            [info, str(target)], env={**os.environ, "LC_ALL": "C"}, **options,
        ).stdout
        match = re.search(rb"(?m)^Pages:\s+(\d+)", metadata)
        if not match:
            raise HTTPException(422, "Не удалось прочитать страницы PDF. Скачайте оригинал.")
        pages = int(match[1])
        if page > pages:
            raise HTTPException(400, "В PDF нет такой страницы.")
        # Bound the output to one page; no document cache or temporary files.
        png = subprocess.run([
            renderer, "-f", str(page), "-l", str(page), "-singlefile",
            "-scale-to", "1800", "-png", str(target),
        ], **options).stdout
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(422, "Не удалось показать страницу PDF. Скачайте оригинал.")
        return {"page": page, "pages": pages, "data_url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(422, "PDF слишком сложный для быстрого просмотра. Скачайте оригинал.") from exc
    except (subprocess.CalledProcessError, OSError) as exc:
        raise HTTPException(422, "Не удалось открыть PDF: он повреждён или защищён паролем. Скачайте оригинал.") from exc
    finally:
        _SLOTS.release()
