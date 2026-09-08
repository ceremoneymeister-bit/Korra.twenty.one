"""Раздел «Обновления» в панели контура.

Почему панель не обновляет себя сама
------------------------------------
Korra 21 у клиента работает из неизменяемого образа: файловая система
контейнера — часть выпуска, и переписывать её изнутри нельзя (движок и сам
отказывается — см. :mod:`korra_cli.update_contract`). Перезапустить свой
контейнер панель тоже не может: у процесса внутри нет ни docker-сокета, ни
прав на хосте, и это сделано намеренно.

Поэтому обновление исполняет хост (`docs/client-deploy/update.sh`), а команду
ему отдаёт кабинет по отдельному каналу обслуживания. Панели в этой схеме
остаётся продуктовая часть, которой раньше не было совсем:

* показать владельцу, **что у него стоит** — по заметкам к выпуску, которые
  едут внутри образа (:mod:`korra_cli.release_notes`);
* показать, **что доступно** — кабинет заранее кладёт сюда карточку выпуска;
* принять от владельца **просьбу обновиться** и сохранить её на диск;
* показать **ход и результат** — кабинет докладывает сюда же.

Ничего из этого не запускает обновление: это доска объявлений между владельцем
и кабинетом. Решение и исполнение остаются у хоста, где на них есть права.

Состояние лежит в `$HERMES_HOME/update-center/` тремя маленькими файлами
(`available.json`, `request.json`, `progress.json`). Файлы, а не БД, потому что
переживать они должны ровно одно событие — перезапуск контейнера во время
обновления, — а данных в них на десяток строк.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from korra_cli import release_notes as _notes
from korra_cli.config import get_hermes_home

_log = logging.getLogger("korra_cli.web_server")

router = APIRouter()

STATE_DIRNAME = "update-center"
AVAILABLE_FILE = "available.json"
REQUEST_FILE = "request.json"
PROGRESS_FILE = "progress.json"

#: Присланная кабинетом карточка больше этого — не карточка. Ограничение
#: держит на замке и файл на диске, и ответ, который читает браузер.
_MAX_ANNOUNCE_BYTES = 64 * 1024

#: Просьба клиента живёт сутки. Кабинет мог быть недоступен, человек мог уйти
#: домой — но «я нажал вчера» не должно превращаться в обновление на третий
#: день, когда владелец про него забыл.
REQUEST_TTL_SECONDS = 24 * 3600

#: Шаги обновления так, как их видит владелец. Ключ — фаза хостового updater
#: (`docs/client-deploy/updater.py`), значение — шаг на экране. Несколько фаз
#: намеренно сходятся в один шаг: человеку не нужно знать разницу между
#: `protect_previous_image` и `fetch`, ему нужно знать, что идёт загрузка.
STEPS: list[dict[str, str]] = [
    {"key": "fetch", "title": "Получаем новую сборку",
     "detail": "Скачиваем и проверяем образ. Агенты продолжают работать."},
    {"key": "drain", "title": "Ждём завершения текущих дел",
     "detail": "Новые задачи не берём, начатые доводим до конца."},
    {"key": "backup", "title": "Делаем резервную копию",
     "detail": "Память, документы, ключи и расписания сохраняются целиком."},
    {"key": "switch", "title": "Переключаем на новую версию",
     "detail": "Здесь панель недоступна примерно минуту."},
    {"key": "check", "title": "Проверяем, что всё поднялось",
     "detail": "Профили, доступы и ответ модели."},
]

#: Фазы updater → шаг из ``STEPS``. Незнакомая фаза не ломает экран: шаг
#: остаётся прежним, а состояние берётся из статуса операции.
PHASE_STEP = {
    "pending": "fetch",
    "queued": "fetch",
    "dispatching": "fetch",
    "preflight": "fetch",
    "protect_previous_image": "fetch",
    "fetch": "fetch",
    "candidate_check": "fetch",
    "draining": "drain",
    "stopping": "drain",
    "backup": "backup",
    "schema_rehearsal": "backup",
    "cleanup": "backup",
    "recreate": "switch",
    "smoke": "check",
    "complete": "check",
    "already_current": "check",
    "rollback": "switch",
    "rollback_draining": "switch",
    "rollback_restore": "switch",
    "rollback_recreate": "switch",
    "rollback_complete": "check",
}

#: Итог операции словами владельца, а не кодом состояния.
STATUS_TEXT = {
    "pending": "Обновление принято, сейчас начнём.",
    "running": "Обновляем. Это займёт несколько минут.",
    "succeeded": "Готово. Установлена новая версия.",
    "failed": "Обновиться не удалось. Прежняя версия работает как раньше.",
    "rolled_back": "Вернули прежнюю версию. Данные на месте.",
    "rollback_failed": "Возврат прежней версии не завершился. Мы уже разбираемся.",
}

_STATUSES = set(STATUS_TEXT)
_FINAL_STATUSES = {"succeeded", "failed", "rolled_back", "rollback_failed"}


def state_dir() -> Path:
    return Path(get_hermes_home()) / STATE_DIRNAME


def _read(name: str) -> Optional[dict]:
    """Прочитать файл состояния. Битый или чужой файл — как будто его нет.

    Экран обновления обязан открыться при любом содержимом каталога: пустая
    карточка честнее пятисотки на странице, куда человек пришёл разбираться.
    """
    path = state_dir() / name
    try:
        if path.stat().st_size > _MAX_ANNOUNCE_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write(name: str, payload: Optional[dict]) -> None:
    from utils import atomic_write_text

    directory = state_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / name
    if payload is None:
        path.unlink(missing_ok=True)
        return
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False), create_mode=0o600)


def _installed() -> dict[str, Any]:
    """Что стоит: выпуск из заметок образа плюс проверяемая провенансом версия."""
    from korra_cli import __version__

    note = _notes.current_release()
    found: dict[str, Any] = {
        "version": __version__,
        "release_id": note.release_id if note else "",
        "title": note.title if note else "",
        "published_at": note.published_at if note else "",
        "summary": note.summary if note else "",
        "sections": [section.to_dict() for section in note.sections] if note else [],
        "revision": note.revision if note else "",
        "pause": note.pause if note else "",
        "image": "",
    }
    try:
        from korra_cli.image_provenance import read_image_provenance

        provenance = read_image_provenance()
    except Exception:  # pragma: no cover — провенанс не обязателен
        provenance = None
    if provenance is not None and provenance.valid:
        # Метка образа точнее заметок: заметки пишет человек, метку — сборка.
        found["revision"] = provenance.revision or found["revision"]
        found["image"] = provenance.image or ""
        found["version"] = provenance.version or found["version"]
    return found


def _managed_externally() -> bool:
    """Обновлением управляет хост, а не панель.

    Тот же контракт, что закрывает `hermes update` внутри образа: если движок
    отказывается обновлять себя сам, то и кнопка в панели не должна обещать
    обновление своими силами.
    """
    try:
        from korra_cli.update_contract import evaluate_update_admission

        return evaluate_update_admission(_notes.PROJECT_ROOT) is not None
    except Exception:  # pragma: no cover — на источнике контракта может не быть
        return False


def _clean_text(value: Any, limit: int = 400) -> str:
    """Только строка и только по длине: карточка выпуска — это текст."""
    return value.strip()[:limit] if isinstance(value, str) else ""


def _clean_items(raw: Any, limit: int = 24) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return found
    for item in raw[:limit]:
        if isinstance(item, str):
            title, detail = _clean_text(item, 200), ""
        elif isinstance(item, dict):
            title, detail = _clean_text(item.get("title"), 200), _clean_text(item.get("detail"), 600)
        else:
            continue
        if title:
            found.append({"title": title, "detail": detail})
    return found


def _clean_sections(raw: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return found
    for section in raw[:8]:
        if not isinstance(section, dict):
            continue
        items = _clean_items(section.get("items"))
        if items:
            found.append({"heading": _clean_text(section.get("heading"), 80), "items": items})
    return found


def _available() -> Optional[dict[str, Any]]:
    """Карточка доступного выпуска — то, что положил кабинет.

    Данные пришли снаружи, поэтому в ответ уходит не сам файл, а разобранная
    и обрезанная копия: экран показывает текст, а не то, что кто-то записал.
    """
    raw = _read(AVAILABLE_FILE)
    if not raw or not _clean_text(raw.get("release_id"), 80):
        return None
    return {
        "release_id": _clean_text(raw.get("release_id"), 80),
        "title": _clean_text(raw.get("title"), 200),
        "published_at": _clean_text(raw.get("published_at"), 40),
        "summary": _clean_text(raw.get("summary"), 800),
        "sections": _clean_sections(raw.get("sections")),
        "pause": _clean_text(raw.get("pause"), 80) or "около минуты",
        "announced_at": raw.get("announced_at") if isinstance(raw.get("announced_at"), (int, float)) else None,
        "self_service": bool(raw.get("self_service")),
    }


def _request(available: Optional[dict]) -> Optional[dict[str, Any]]:
    """Просьба владельца обновиться, если она ещё жива и относится к делу."""
    raw = _read(REQUEST_FILE)
    if not raw:
        return None
    requested_at = raw.get("requested_at")
    if not isinstance(requested_at, (int, float)):
        return None
    if time.time() - requested_at > REQUEST_TTL_SECONDS:
        return None
    release_id = _clean_text(raw.get("release_id"), 80)
    return {
        "release_id": release_id,
        "requested_at": requested_at,
        # Выпуск сменился, пока просьба ждала: кабинет такую просьбу не
        # исполняет, и владельцу мы её тоже не показываем как актуальную.
        "stale": bool(available and release_id and release_id != available["release_id"]),
    }


def _progress(installed: dict) -> Optional[dict[str, Any]]:
    """Ход операции: шаг, состояние и человеческий текст.

    Отдельно помечаем случай «операция завершилась, а мы всё ещё на прежней
    версии»: панель в этот момент уже перезапустилась и обязана сказать
    владельцу правду, а не показывать вечное «обновляем».
    """
    raw = _read(PROGRESS_FILE)
    if not raw:
        return None
    status = raw.get("status")
    if status not in _STATUSES:
        return None
    phase = _clean_text(raw.get("phase"), 60)
    step = PHASE_STEP.get(phase.removeprefix("remote_"), "fetch")
    release_id = _clean_text(raw.get("release_id"), 80)
    return {
        "status": status,
        "step": step,
        "phase": phase,
        "release_id": release_id,
        "message": _clean_text(raw.get("message"), 400) or STATUS_TEXT[status],
        "error": _clean_text(raw.get("error"), 400),
        "started_at": raw.get("started_at") if isinstance(raw.get("started_at"), (int, float)) else None,
        "updated_at": raw.get("updated_at") if isinstance(raw.get("updated_at"), (int, float)) else None,
        "final": status in _FINAL_STATUSES,
        "installed_target": bool(release_id and release_id == installed.get("release_id")),
    }


@router.get("/api/updates/state")
async def updates_state() -> dict[str, Any]:
    """Всё, что нужно разделу «Обновления», одним запросом."""
    installed = _installed()
    available = _available()
    progress = _progress(installed)
    same = bool(available and available["release_id"] == installed.get("release_id"))
    return {
        "installed": installed,
        "available": None if same else available,
        "up_to_date": same or available is None,
        "managed_externally": _managed_externally(),
        "request": _request(available),
        "progress": progress,
        "steps": STEPS,
        "checked_at": time.time(),
    }


class AnnounceBody(BaseModel):
    """Карточка выпуска и/или ход операции — так, как их присылает кабинет."""

    model_config = ConfigDict(extra="forbid")

    release: Optional[dict] = None
    progress: Optional[dict] = None
    clear_request: bool = False


@router.post("/api/updates/announce")
async def updates_announce(body: AnnounceBody) -> dict[str, Any]:
    """Кабинет сообщает контуру о доступном выпуске и о ходе обновления.

    Маршрут ничего не запускает и не может: он только кладёт на диск текст,
    который панель потом покажет. Поэтому он безопасен даже в том смысле, в
    каком безопасна доска объявлений — испортить им можно ровно надпись.
    """
    if body.release is not None:
        payload = dict(body.release)
        payload["announced_at"] = time.time()
        if len(json.dumps(payload, ensure_ascii=False).encode()) > _MAX_ANNOUNCE_BYTES:
            raise HTTPException(413, "Карточка выпуска слишком большая")
        _write(AVAILABLE_FILE, payload)
    if body.progress is not None:
        payload = dict(body.progress)
        payload["updated_at"] = time.time()
        if len(json.dumps(payload, ensure_ascii=False).encode()) > _MAX_ANNOUNCE_BYTES:
            raise HTTPException(413, "Сообщение о ходе слишком большое")
        _write(PROGRESS_FILE, payload)
    if body.clear_request:
        # Просьба принята к исполнению: снимаем её, чтобы после обновления
        # владелец не видел «мы попросили» рядом с уже установленной версией.
        _write(REQUEST_FILE, None)
    return {"ok": True}


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_id: str = Field(default="", max_length=80)


@router.post("/api/updates/request")
async def updates_request(body: RequestBody, request: Request) -> dict[str, Any]:
    """Владелец нажал «Обновить». Контейнер при этом не трогается.

    Единственное действие — запись просьбы на диск. Забирает её кабинет: у него
    есть и проверенный выпуск, и права на хосте, и он же вернёт сюда ход.
    """
    available = _available()
    if not available:
        raise HTTPException(409, "Обновление сейчас недоступно. Попробуйте позже.")
    release_id = body.release_id or available["release_id"]
    if release_id != available["release_id"]:
        raise HTTPException(409, "Выпуск изменился. Обновите страницу и попробуйте снова.")
    progress = _progress(_installed())
    if progress and not progress["final"]:
        raise HTTPException(409, "Обновление уже идёт.")
    _write(REQUEST_FILE, {
        "release_id": release_id,
        "requested_at": time.time(),
        # Кто попросил — для журнала кабинета; секретов здесь нет.
        "source": "panel",
        "client_host": (request.headers.get("x-forwarded-prefix") or "").strip()[:120],
    })
    _log.info("update-center: владелец запросил обновление до %s", release_id)
    return {"ok": True, "release_id": release_id}


@router.delete("/api/updates/request")
async def updates_request_cancel() -> dict[str, Any]:
    """Передумал. Пока кабинет не забрал просьбу, её можно снять."""
    _write(REQUEST_FILE, None)
    return {"ok": True}
