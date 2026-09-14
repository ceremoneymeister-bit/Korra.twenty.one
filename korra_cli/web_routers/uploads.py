"""Сессия загрузки: манифест, части, публикация (K21-059).

Один примитив и для чата, и для экрана «Файлы»: клиент объявляет список файлов,
дописывает каждый частями по 4 МиБ и одним ``complete`` публикует весь пакет.
Состояние сессии живёт только на диске (манифест, receipts, маркер публикации),
поэтому перезапуск панели её не теряет, а повтор любого шага безопасен —
оборванная часть докачивается с фактического смещения, а не с нуля.

Ничего из staging агент не видит: каталог ``.uploads`` служебный
(``web_server._MANAGED_INTERNAL_NAMES``), и файл появляется в рабочей папке
только целиком, в момент публикации.

Донор механики — folder intake расчётчика (``metal_calc/folder_intake.py``):
оттуда портированы валидация имён и путей, per-index блокировка и идемпотентный
``create``. Пути, лимиты и периметр здесь свои.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import secrets
import shutil
import tempfile
import threading
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from anyio import CancelScope
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from korra_cli.web_deps import late, late_attr
from utils import atomic_write_text

_log = logging.getLogger("korra_cli.web_server")

router = APIRouter()

# Late-bound web_server helpers: state stays in web_server, monkeypatching it
# remains authoritative (see web_deps).
_managed_files_policy = late("_managed_files_policy")
_resolve_managed_path = late("_resolve_managed_path")
_is_private_managed_path = late("_is_private_managed_path")
_is_sensitive_filename = late("_is_sensitive_filename")
_chat_client_root = late("_chat_client_root")
_profile_scope = late("_profile_scope")

# Часть в 4 МиБ при 50 КБ/с идёт 80 с — влезает в client_body_timeout 300s, а
# при обрыве теряется не больше одной части.
PART_BYTES = 4 * 1024 * 1024
MAX_FILES = 10_000
MAX_DIRECTORIES = 10_000
MAX_FILE_BYTES = 2 * 1024 ** 3
MAX_TOTAL_BYTES = 20 * 1024 ** 3
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_STATE_BYTES = 64 * 1024 * 1024
# Пакет чата: больше 30 файлов уходит агенту папкой, а не строками (§3).
CHAT_MAX_FILES = 500
CHAT_FOLDER_THRESHOLD = 30
# Запас сверх объёма набора: os.link не удваивает данные, 1 ГиБ нужен журналам
# и SQLite соседей по диску.
DISK_HEADROOM_BYTES = 1024 ** 3
STAGING_TTL_DAYS = 7
PUBLISHED_TTL_HOURS = 24
SWEEP_INTERVAL_SECONDS = 600

STAGING_DIR_NAME = ".uploads"
DEDUP_DIR_NAME = ".index"
DEDUP_FILE_NAME = "sha256.jsonl"
DEDUP_MAX_ENTRIES = 10_000

LIMITS = {
    "part_bytes": PART_BYTES,
    "max_files": MAX_FILES,
    "max_directories": MAX_DIRECTORIES,
    "max_file_bytes": MAX_FILE_BYTES,
    "max_total_bytes": MAX_TOTAL_BYTES,
    "chat_max_files": CHAT_MAX_FILES,
    "chat_folder_threshold": CHAT_FOLDER_THRESHOLD,
}

_ORIGINS = frozenset({"chat", "files"})
_CONFLICT_POLICIES = frozenset({"skip", "replace", "copy"})
_MANIFEST_KEYS = frozenset({
    "upload_id", "origin", "target", "profile", "name", "directories", "files",
    "on_conflict", "client",
})
_FILE_KEYS = frozenset({"path", "size", "sha256", "last_modified"})
_SHA256_LENGTH = 64


# --- валидация манифеста ---------------------------------------------------


def validate_upload_id(value: Any) -> str:
    """Только канонический uuid4: идентификатор становится именем каталога."""
    try:
        parsed = UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(400, "Некорректный идентификатор загрузки") from exc
    if str(parsed) != value or parsed.version != 4:
        raise HTTPException(400, "Некорректный идентификатор загрузки")
    return value


def _name(value: Any) -> str:
    if (not isinstance(value, str) or not value.strip() or value in {".", ".."}
            or any(c in value for c in "/\\∕⁄⧸")
            or any(unicodedata.category(c).startswith("C") for c in value)):
        raise HTTPException(400, "Недопустимое имя файла или папки")
    value = unicodedata.normalize("NFC", value)
    if len(value.encode("utf-8")) > 255:
        raise HTTPException(400, "Слишком длинное имя файла или папки")
    return value


def _relative_path(path: Any) -> str:
    if (not isinstance(path, str) or any(unicodedata.category(c).startswith("C") for c in path)
            or not 1 <= len(path.encode("utf-8")) <= 2048):
        raise HTTPException(400, "Недопустимый путь файла или папки")
    parts = path.split("/")
    if len(parts) > 16:
        raise HTTPException(400, "Слишком много вложенных папок")
    return "/".join(_name(part) for part in parts)


def _directories(paths: list[str], explicit: Any = None) -> list[str]:
    if explicit is None:
        explicit = []
    if not isinstance(explicit, list) or len(explicit) > MAX_DIRECTORIES:
        raise HTTPException(400, f"В загрузке может быть до {MAX_DIRECTORIES} вложенных папок")
    directories = {_relative_path(path) for path in explicit}
    for path in [*paths, *directories]:
        parts = path.split("/")
        directories.update("/".join(parts[:i]) for i in range(1, len(parts)))
    if len(directories) > MAX_DIRECTORIES:
        raise HTTPException(400, f"В загрузке может быть до {MAX_DIRECTORIES} вложенных папок")
    file_keys = {path.casefold() for path in paths}
    if any(path.casefold() in file_keys for path in directories):
        raise HTTPException(409, "Один путь используется и как файл, и как папка")
    return sorted(directories)


def _sha256_field(value: Any) -> str | None:
    if value is None:
        return None
    if (not isinstance(value, str) or len(value) != _SHA256_LENGTH
            or any(char not in "0123456789abcdef" for char in value.lower())):
        raise HTTPException(400, "Некорректная контрольная сумма файла")
    return value.lower()


def parse_manifest(body: Any) -> dict[str, Any]:
    """Проверить присланный список файлов, ничего не трогая на диске."""
    if not isinstance(body, dict) or set(body) - _MANIFEST_KEYS:
        raise HTTPException(400, "Некорректный список файлов загрузки")
    origin = body.get("origin")
    if not isinstance(origin, str) or origin not in _ORIGINS:
        raise HTTPException(400, "Не указано, откуда идёт загрузка")
    on_conflict = body.get("on_conflict") or "skip"
    if not isinstance(on_conflict, str) or on_conflict not in _CONFLICT_POLICIES:
        raise HTTPException(400, "Неизвестная политика совпадающих имён")

    files = body.get("files")
    limit = CHAT_MAX_FILES if origin == "chat" else MAX_FILES
    if not isinstance(files, list):
        raise HTTPException(400, "Ожидался список files")
    if len(files) > limit:
        raise HTTPException(413, f"Больше {limit} файлов за раз не получится")

    denied = late_attr("_CHAT_DENIED_EXTENSIONS")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for entry in files:
        if not isinstance(entry, dict) or set(entry) - _FILE_KEYS or "path" not in entry:
            raise HTTPException(400, "Для каждого файла нужны path и size")
        normalized = _relative_path(entry["path"])
        basename = normalized.rsplit("/", 1)[-1]
        if _is_sensitive_filename(basename):
            raise HTTPException(400, "Такое имя файла зарезервировано")
        if origin == "chat" and Path(basename).suffix.lower() in denied:
            raise HTTPException(400, f"Файлы {Path(basename).suffix.lower()} загружать нельзя")
        key = normalized.casefold()
        if key in seen:
            raise HTTPException(409, f"Путь файла указан дважды: {normalized}")
        seen.add(key)
        size = entry.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise HTTPException(400, f"Размер файла не указан: {normalized}")
        if size > MAX_FILE_BYTES:
            raise HTTPException(413, "Файл больше 2 ГБ")
        total += size
        record: dict[str, Any] = {"path": normalized, "size": size}
        digest = _sha256_field(entry.get("sha256"))
        if digest:
            record["sha256"] = digest
        modified = entry.get("last_modified")
        if isinstance(modified, int) and not isinstance(modified, bool):
            record["last_modified"] = modified
        entries.append(record)
    if total > MAX_TOTAL_BYTES:
        raise HTTPException(413, "Общий размер загрузки больше 20 ГБ")

    directories = _directories([item["path"] for item in entries], body.get("directories"))
    manifest = {
        "upload_id": validate_upload_id(body.get("upload_id")),
        "origin": origin,
        "target": body.get("target") or {"kind": "inbox"},
        "profile": (body.get("profile") or None),
        "name": _name(body["name"]) if body.get("name") else None,
        "directories": directories,
        "files": entries,
        "on_conflict": on_conflict,
        "total_bytes": total,
        "version": 1,
    }
    if not entries and not directories and not manifest["name"]:
        raise HTTPException(400, "В загрузке нет ни файлов, ни папок")
    return manifest


# --- размещение на диске ---------------------------------------------------


def _staging_root(request: Request) -> Path:
    """Staging рядом с целью, чтобы публикация была ``os.link``, а не копией.

    Корень не зависит от профиля: ``PUT`` и ``complete`` профиль не передают, а
    сессию по ним надо находить. В локальной установке без запертого корня
    берём данные самого агента — это та же файловая система, что и
    ``_chat_client_root()``, а для целей за её пределами есть fallback на
    ``os.replace`` при ``EXDEV``.
    """
    policy = _managed_files_policy(request)
    if policy.locked_root is not None:
        return Path(policy.locked_root) / STAGING_DIR_NAME
    from korra_constants import get_hermes_home

    return Path(get_hermes_home()) / STAGING_DIR_NAME


def _session_dir(request: Request, upload_id: str) -> Path:
    session = _staging_root(request) / validate_upload_id(upload_id)
    for path in (session.parent, session, session / "files", session / "receipts"):
        if path.is_symlink():
            raise HTTPException(403, "Служебный путь загрузки недоступен")
    return session


def _lock_fd(path: Path, *, shared: bool = False, blocking: bool = True) -> int:
    # Lock files outlive the session: deleting a locked inode would let a
    # second request lock a replacement inode and enter the same session.
    if path.name == ".lock":
        locks = path.parent.parent / ".locks"
        locks.mkdir(exist_ok=True)
        path = locks / (path.parent.name + ".lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            fcntl.flock(fd, mode | (0 if blocking else fcntl.LOCK_NB))
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def _flock(path: Path, *, shared: bool = False, blocking: bool = True) -> Iterator[None]:
    """Блокирующий flock; вызывать только из threadpool."""
    fd = _lock_fd(path, shared=shared, blocking=blocking)
    try:
        yield
    finally:
        os.close(fd)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_STATE_BYTES + 1)
    except FileNotFoundError:
        raise HTTPException(404, "Загрузка не найдена или уже убрана")
    if len(raw) > MAX_STATE_BYTES:
        raise HTTPException(400, "Список файлов загрузки повреждён")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Список файлов загрузки повреждён") from exc
    if not isinstance(value, dict):
        raise HTTPException(400, "Список файлов загрузки повреждён")
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True),
                      create_mode=0o600)


async def _request_json(request: Request, *, optional: bool = False) -> Any:
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_MANIFEST_BYTES:
            raise HTTPException(413, "Список файлов загрузки слишком большой")
        raw.extend(chunk)
    if optional and not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Некорректный список файлов загрузки") from exc


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- цель публикации -------------------------------------------------------


def _resolve_target(manifest: dict[str, Any], request: Request) -> tuple[Path, Path | None]:
    """Вернуть абсолютную папку публикации и корень inbox для дедупликации."""
    target = manifest["target"]
    if not isinstance(target, dict) or target.get("kind") not in ("inbox", "path"):
        raise HTTPException(400, "Не указано, куда класть файлы")

    if target["kind"] == "inbox":
        profile = manifest.get("profile")
        if profile is not None and not late_attr("_CHAT_PROFILE_RE").fullmatch(str(profile)):
            raise HTTPException(400, "Некорректное имя профиля")
        # Профильный scope нельзя держать через await — берём путь и выходим.
        with _profile_scope(profile or None) as scoped_home:
            inbox = _chat_client_root(scoped_home) / "inbox"
        now = datetime.now()
        package = f"{secrets.token_hex(3)}-{now.strftime('%H%M')}"
        raw = inbox / now.strftime("%Y-%m-%d") / package
    else:
        inbox = None
        raw = Path(str(target.get("path") or ""))

    _policy, resolved, _display = _resolve_managed_path(str(raw), request, for_write=True)
    if _is_private_managed_path(resolved):
        raise HTTPException(403, "Служебная папка недоступна для загрузки.")
    if inbox is not None:
        _policy, inbox, _display = _resolve_managed_path(str(inbox), request, for_write=True)
    return resolved, inbox


def _check_disk_space(staging: Path, needed: int) -> None:
    try:
        usage = shutil.disk_usage(staging)
    except OSError:
        return
    if usage.free < needed + DISK_HEADROOM_BYTES:
        gigabytes = (needed + DISK_HEADROOM_BYTES) / 1024 ** 3
        free = usage.free / 1024 ** 3
        raise HTTPException(
            507,
            f"На диске контура не хватает места: нужно {gigabytes:.1f} ГБ, "
            f"свободно {free:.1f} ГБ",
        )


# --- дедупликация ----------------------------------------------------------


def _dedup_path(inbox: Path | None) -> Path | None:
    return None if inbox is None else inbox / DEDUP_DIR_NAME / DEDUP_FILE_NAME


def _read_dedup_index(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    index: dict[str, dict[str, Any]] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and isinstance(record.get("sha256"), str):
                    index[record["sha256"]] = record
    except OSError:
        return {}
    return index


def _lookup_dedup(index: dict[str, dict[str, Any]], digest: str, size: int) -> Path | None:
    record = index.get(digest)
    if not record:
        return None
    candidate = Path(str(record.get("path") or ""))
    try:
        if (not candidate.is_symlink() and candidate.is_file()
                and candidate.stat().st_size == size and _file_hash(candidate) == digest):
            return candidate
    except OSError:
        return None
    return None


def _file_hash(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError:
        return None


def _needs_folder(manifest: dict[str, Any]) -> bool:
    return bool(manifest.get("name") or manifest.get("directories")
                or (manifest["origin"] == "chat" and len(manifest["files"]) > CHAT_FOLDER_THRESHOLD))


def _append_dedup(path: Path | None, index: dict[str, dict[str, Any]], record: dict[str, Any]) -> None:
    if path is None:
        return
    previous = index.get(record["sha256"])
    if previous and all(previous.get(key) == record.get(key) for key in ("path", "size")):
        return
    index[record["sha256"]] = record
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o755)
    if len(index) > DEDUP_MAX_ENTRIES or (path.exists() and path.stat().st_size > 32 * 1024 * 1024):
        # Переписываем, оставляя только строки с живыми файлами: индекс — кэш,
        # а не журнал, и расти бесконечно ему незачем.
        alive = [
            item for item in index.values()
            if Path(str(item.get("path") or "")).is_file()
        ][-DEDUP_MAX_ENTRIES:]
        body = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in alive)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(body, encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.chmod(path, 0o644)


# --- статус сессии ---------------------------------------------------------


def _received(session: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    already = {int(key) for key in (manifest.get("already_present") or {})}
    received = []
    for index, entry in enumerate(manifest["files"]):
        if index in already:
            received.append({"index": index, "bytes": entry["size"], "complete": True})
            continue
        try:
            size = (session / "files" / f"{index}.part").stat().st_size
        except OSError:
            size = 0
        received.append({
            "index": index,
            "bytes": size,
            "complete": (session / "receipts" / f"{index}.json").is_file(),
        })
    return received


def _status_payload(session: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    published = session / "published.json"
    payload: dict[str, Any] = {
        "upload_id": manifest["upload_id"],
        "origin": manifest["origin"],
        "target": manifest.get("resolved_target"),
        "published": published.is_file(),
        "received": _received(session, manifest),
        "already_present": sorted(int(key) for key in (manifest.get("already_present") or {})),
        "limits": LIMITS,
    }
    if payload["published"]:
        payload["result"] = _read_json(published)
        delivered = {entry["index"] for entry in payload["result"]["files"]}
        payload["received"] = [{"index": index, "bytes": entry["size"] if index in delivered else 0,
                                "complete": index in delivered}
                               for index, entry in enumerate(manifest["files"])]
    return payload


# --- уборка ----------------------------------------------------------------


_SWEEP_LOCK = threading.Lock()
_LAST_SWEEP = 0.0


def _session_mtime(session: Path) -> float:
    newest = 0.0
    for path in (session, session / "manifest.json", session / "files", session / "receipts"):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    for folder in ("files", "receipts"):
        try:
            with os.scandir(session / folder) as scan:
                for entry in scan:
                    try:
                        newest = max(newest, entry.stat().st_mtime)
                    except OSError:
                        continue
        except OSError:
            continue
    return newest


def sweep_staging(staging: Path, *, force: bool = False, now: float | None = None) -> bool:
    """Убрать заброшенные сессии. Возвращает False, если интервал ещё не истёк."""
    global _LAST_SWEEP
    moment = time.time() if now is None else now
    with _SWEEP_LOCK:
        if not force and moment - _LAST_SWEEP < SWEEP_INTERVAL_SECONDS:
            return False
        _LAST_SWEEP = moment
    staging_ttl = STAGING_TTL_DAYS * 24 * 3600
    published_ttl = PUBLISHED_TTL_HOURS * 3600
    try:
        entries = list(os.scandir(staging))
    except OSError:
        return True
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        try:
            validate_upload_id(entry.name)
        except HTTPException:
            continue
        session = Path(entry.path)
        ttl = published_ttl if (session / "published.json").is_file() else staging_ttl
        if moment - _session_mtime(session) <= ttl:
            continue
        try:
            with _flock(session / ".lock", blocking=False):
                # A partially published job is recoverable, not abandoned
                # scratch. Keep its sources until completion or explicit repair.
                if (session / "publishing.json").exists() and not (session / "published.json").exists():
                    continue
                if moment - _session_mtime(session) > ttl:
                    shutil.rmtree(session)
        except OSError as exc:  # pragma: no cover - гонка с параллельной уборкой
            _log.debug("не удалось убрать сессию загрузки %s: %s", session.name, exc)
    return True


# --- маршруты --------------------------------------------------------------


@router.post("/api/uploads")
async def create_upload(request: Request):
    body = await _request_json(request)
    manifest = parse_manifest(body)
    client_note = body.get("client") if isinstance(body, dict) else None
    staging = _staging_root(request)
    session = _session_dir(request, manifest["upload_id"])
    resolved_target, inbox = _resolve_target(manifest, request)

    def _run() -> tuple[int, dict[str, Any]]:
        session.mkdir(parents=True, exist_ok=True)
        os.chmod(session.parent, 0o700)
        with _flock(session / ".lock"):
            saved = session / "manifest.json"
            if saved.is_file():
                stored = _read_json(saved)
                comparable = {
                    key: value for key, value in stored.items()
                    if key not in {"created_at", "client", "resolved_target",
                                   "resolved_inbox", "already_present", "replacement_revisions"}
                }
                if comparable != manifest:
                    raise HTTPException(409, "Эта загрузка уже имеет другой список файлов")
                return 200, _status_payload(session, stored)

            _check_disk_space(staging, manifest["total_bytes"])
            (session / "files").mkdir(exist_ok=True)
            (session / "receipts").mkdir(exist_ok=True)
            stored = dict(manifest)
            stored["created_at"] = _utcnow()
            stored["resolved_target"] = str(resolved_target)
            stored["resolved_inbox"] = str(inbox) if inbox is not None else None
            if manifest["origin"] == "files" and manifest["on_conflict"] == "replace":
                base = _publication_base(stored, request)
                stored["replacement_revisions"] = {
                    str(index): _destination_revision(base / entry["path"])
                    for index, entry in enumerate(manifest["files"])
                }
            if isinstance(client_note, dict):
                stored["client"] = client_note
            # Уже лежащие в inbox файлы браузер не передаёт заново: пакет из
            # тридцати повторно отправленных фото стоит одного запроса.
            already: dict[str, str] = {}
            if manifest["origin"] == "chat" and not _needs_folder(manifest):
                index = _read_dedup_index(_dedup_path(inbox))
                for position, entry in enumerate(manifest["files"]):
                    digest = entry.get("sha256")
                    if not digest:
                        continue
                    existing = _lookup_dedup(index, digest, entry["size"])
                    if existing is not None:
                        already[str(position)] = str(existing)
            stored["already_present"] = already
            _write_json(session / "manifest.json", stored)
            return 201, _status_payload(session, stored)

    try:
        status, payload = await run_in_threadpool(_run)
    except OSError as exc:
        _raise_storage_error(exc)
    await run_in_threadpool(sweep_staging, staging)
    return JSONResponse(payload, status_code=status)


@router.get("/api/uploads/{upload_id}")
async def get_upload(upload_id: str, request: Request):
    session = _session_dir(request, upload_id)

    def _run() -> dict[str, Any]:
        return _status_payload(session, _read_json(session / "manifest.json"))

    return await run_in_threadpool(_run)


@router.delete("/api/uploads/{upload_id}", status_code=204)
async def cancel_upload(upload_id: str, request: Request):
    session = _session_dir(request, upload_id)

    def _run() -> None:
        if not (session / "manifest.json").is_file():
            raise HTTPException(404, "Загрузка не найдена или уже убрана")
        with _flock(session / ".lock"):
            if (session / "published.json").is_file() or (session / "publishing.json").is_file():
                raise HTTPException(409, "Загрузка уже завершена, отменить её нельзя")
            shutil.rmtree(session)

    await run_in_threadpool(_run)
    return Response(status_code=204)


def _write_chunk(fd: int, chunk: bytes) -> int:
    """Записать кусок целиком; вызывается только из threadpool."""
    written = 0
    while written < len(chunk):
        written += os.write(fd, chunk[written:])
    return written


def _raise_storage_error(exc: OSError) -> None:
    if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}:
        raise HTTPException(507, "На диске установки не хватает места. Принятые части сохранены; освободите место и продолжите загрузку.") from exc
    raise exc


def _open_part(session: Path, index: int, offset: int) -> int:
    fd = os.open(session / "files" / f"{index}.part", os.O_WRONLY | os.O_CREAT
                 | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        # Права ставим явно: umask контейнера иначе отдаст 0600, и хостовой
        # бэкап/rsync клиента споткнётся о файл после публикации по os.link.
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o644)
        os.lseek(fd, offset, os.SEEK_SET)
    except OSError:
        os.close(fd)
        raise
    return fd


def _acquire_part_locks(session: Path, index: int) -> list[int]:
    fds: list[int] = []
    try:
        for path, shared in ((session / ".lock", True),
                             (session / "files" / f".{index}.lock", False)):
            fds.append(_lock_fd(path, shared=shared))
            if shared:
                _load_open_session(session)
    except BaseException:
        _release_locks(fds)
        raise
    return fds


def _release_locks(fds: list[int]) -> None:
    for fd in fds:
        try:
            os.close(fd)
        except OSError:  # pragma: no cover - дескриптор уже закрыт
            pass


def _offset_conflict(current: int) -> JSONResponse:
    """409 с фактическим размером: клиент пересинхронизируется и продолжит."""
    return JSONResponse(
        {"detail": "Часть пришла не по порядку; продолжите с указанного смещения",
         "bytes": current},
        status_code=409,
    )


def _load_open_session(session: Path) -> dict[str, Any]:
    manifest = _read_json(session / "manifest.json")
    if (session / "published.json").is_file() or (session / "publishing.json").is_file():
        raise HTTPException(410, "Загрузка уже завершена")
    return manifest


@router.put("/api/uploads/{upload_id}/files/{index}")
async def upload_part(upload_id: str, index: int, request: Request, offset: int = 0):
    session = _session_dir(request, upload_id)
    manifest = await run_in_threadpool(_load_open_session, session)
    files = manifest["files"]
    if not 0 <= index < len(files):
        raise HTTPException(404, "Файл отсутствует в списке загрузки")
    if offset < 0:
        raise HTTPException(400, "Некорректное смещение части")
    declared = int(files[index]["size"])

    raw_length = request.headers.get("content-length")
    if raw_length is None or not raw_length.isdigit():
        raise HTTPException(411, "Не указан размер части")
    length = int(raw_length)
    if length > PART_BYTES:
        raise HTTPException(413, f"Часть больше {PART_BYTES // 1024 ** 2} МиБ")

    locks = await run_in_threadpool(_acquire_part_locks, session, index)
    part = session / "files" / f"{index}.part"
    try:
        await run_in_threadpool(_load_open_session, session)
        current = await run_in_threadpool(
            lambda: part.stat().st_size if part.is_file() else 0
        )
        if offset < current:
            # Ретрай после неоднозначного исхода: ответ не дошёл, байты уже
            # на месте. Ничего не пишем — так повтор не может испортить файл.
            if offset + length <= current:
                return {"index": index, "bytes": current,
                        "complete": (session / "receipts" / f"{index}.json").is_file()}
            return _offset_conflict(current)
        if offset > current:
            return _offset_conflict(current)
        if current + length > declared:
            raise HTTPException(413, "Файл больше заявленного размера")

        fd = await run_in_threadpool(_open_part, session, index, offset)
        written = 0
        try:
            async for chunk in request.stream():
                if not chunk:
                    continue
                written += len(chunk)
                if written > length or current + written > declared:
                    raise HTTPException(413, "Файл больше заявленного размера")
                await run_in_threadpool(_write_chunk, fd, chunk)
            if written != length:
                raise HTTPException(400, "Часть пришла не целиком; повторите передачу")
            await run_in_threadpool(os.fsync, fd)
        except BaseException as exc:
            # Оборванная часть (ClientDisconnect, CancelledError, 413)
            # откатывается до размера на начало запроса: клиент спросит статус
            # и продолжит ровно с него, а не с нуля.
            with CancelScope(shield=True):
                await run_in_threadpool(_truncate_part, fd, offset)
            if isinstance(exc, ClientDisconnect):
                raise HTTPException(408, "Связь прервалась. Принятые ранее части сохранены; продолжите загрузку.") from exc
            raise
        finally:
            with CancelScope(shield=True):
                await run_in_threadpool(os.close, fd)

        total = offset + written
        complete = total == declared
        if complete:
            await run_in_threadpool(_write_receipt, session, index, total)
        return {"index": index, "bytes": total, "complete": complete}
    except OSError as exc:
        _raise_storage_error(exc)
    finally:
        with CancelScope(shield=True):
            await run_in_threadpool(_release_locks, locks)


def _truncate_part(fd: int, size: int) -> None:
    try:
        os.ftruncate(fd, size)
    except OSError:  # pragma: no cover - файл уже убран
        pass


def _write_receipt(session: Path, index: int, total: int) -> None:
    part = session / "files" / f"{index}.part"
    with part.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    _write_json(session / "receipts" / f"{index}.json", {
        "bytes": total, "sha256": digest, "received_at": _utcnow(),
    })


# --- публикация ------------------------------------------------------------


class _IncompleteUpload(Exception):
    """Незавершённая сессия: список недостающих файлов клиент получает верхним
    полем ``missing``, а не внутри ``detail`` — планировщик частей читает его
    напрямую."""

    def __init__(self, missing: list[int]) -> None:
        super().__init__("Загружены не все файлы")
        self.missing = missing


def _available_name(target: Path) -> Path:
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for counter in range(2, 10_000):
        candidate = target.with_name(f"{stem} ({counter}){suffix}")
        if not candidate.exists():
            return candidate
    raise HTTPException(409, "Слишком много файлов с таким именем")


def _copy_publish(part: Path, destination: Path, *, overwrite: bool) -> None:
    """Copy beside the target, then publish atomically; keep the source receipt."""
    _check_disk_space(destination.parent, part.stat().st_size)
    fd, name = tempfile.mkstemp(prefix=".upload-", suffix=".upload", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output, part.open("rb") as source:
            shutil.copyfileobj(source, output, 1024 * 1024)
            output.flush()
            if hasattr(os, "fchmod"):
                os.fchmod(output.fileno(), 0o644)
            os.fsync(output.fileno())
        late_attr("_publish_managed_upload")(temporary, destination, overwrite=overwrite)
    finally:
        temporary.unlink(missing_ok=True)


def _link_or_move(part: Path, destination: Path) -> None:
    """Create without clobbering; sources live until the final durable receipt."""
    try:
        os.link(part, destination)
    except FileExistsError:
        raise HTTPException(409, "Файл с таким именем уже существует.")
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        _copy_publish(part, destination, overwrite=False)


def _replace_with(part: Path, destination: Path) -> None:
    _copy_publish(part, destination, overwrite=True)


def _validate_destination(path: Path, request: Request) -> Path:
    _policy, resolved, _display = _resolve_managed_path(str(path), request, for_write=True)
    if resolved != path or _is_private_managed_path(resolved):
        raise HTTPException(403, "Путь загрузки изменился или недоступен")
    return resolved


def _publication_base(manifest: dict[str, Any], request: Request) -> Path:
    base = Path(manifest["resolved_target"])
    if manifest.get("name"):
        base /= manifest["name"]
    _validate_destination(base, request)
    # Check the whole namespace before the first mutation, including empty
    # directories, private components and a target changed since create.
    for relative in [*manifest["directories"], *(entry["path"] for entry in manifest["files"])]:
        _validate_destination(base / relative, request)
    return base


def _destination_revision(path: Path) -> str | None:
    try:
        return late_attr("_managed_file_revision_from_stat")(path.stat())
    except FileNotFoundError:
        return None


def _publish(session: Path, manifest: dict[str, Any], exclude: set[int],
             request: Request) -> dict[str, Any]:
    files = manifest["files"]
    base = _publication_base(manifest, request)
    already = {int(key): Path(value) for key, value in (manifest.get("already_present") or {}).items()}
    missing: list[int] = []
    receipts: dict[int, dict[str, Any]] = {}
    for index, entry in enumerate(files):
        if index in exclude:
            continue
        if index in already:
            source = _validate_destination(already[index], request)
            if _file_hash(source) == entry.get("sha256") and source.is_file():
                continue
            already.pop(index)
        receipt = session / "receipts" / f"{index}.json"
        part = session / "files" / f"{index}.part"
        if not receipt.is_file() or not part.is_file():
            missing.append(index)
            continue
        record = _read_json(receipt)
        if record.get("bytes") != entry["size"] or part.stat().st_size != entry["size"]:
            missing.append(index)
            continue
        digest = _file_hash(part)
        if digest != record.get("sha256") or (entry.get("sha256") and digest != entry["sha256"]):
            raise HTTPException(409, f"Файл изменился во время загрузки: {entry['path']}")
        receipts[index] = record
    if missing:
        # A file offered as already-present may have been edited/deleted
        # during upload. Report it missing so the same session can supply it.
        for index in missing:
            manifest.get("already_present", {}).pop(str(index), None)
        _write_json(session / "manifest.json", manifest)
        raise _IncompleteUpload(missing)

    journal = session / "publishing.json"
    revisions = manifest.get("replacement_revisions")
    if revisions is not None and not journal.exists():
        for index, entry in enumerate(files):
            if index not in exclude and _destination_revision(base / entry["path"]) != revisions[str(index)]:
                raise HTTPException(409, "Файл назначения изменился во время загрузки. Изменения сохранены; отмените загрузку и выберите копию или замену заново.")
    decision = {"excluded": sorted(exclude)}
    if journal.is_file():
        if _read_json(journal) != decision:
            raise HTTPException(409, "Завершение уже началось с другим списком исключений")
    else:
        _write_json(journal, decision)
    records = session / "publication"
    records.mkdir(exist_ok=True)
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o755)
    for relative in manifest["directories"]:
        folder = _validate_destination(base / relative, request)
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o755)

    inbox = Path(manifest["resolved_inbox"]) if manifest.get("resolved_inbox") else None
    # A folder attachment must contain ALL its members. Returning paths into
    # unrelated historical packages would give the agent an incomplete folder.
    dedup_path = _dedup_path(inbox) if manifest["origin"] == "chat" and not _needs_folder(manifest) else None
    dedup_index = _read_dedup_index(dedup_path)
    readers = late_attr("_CHAT_ATTACHMENT_READERS")
    policy = manifest["on_conflict"] if manifest["origin"] == "files" else "copy"
    published: list[dict[str, Any]] = []
    skipped: list[int] = []

    for index, entry in enumerate(files):
        if index in exclude:
            continue
        record_path = records / f"{index}.json"
        if record_path.is_file():
            intent = _read_json(record_path)
            descriptor = intent["descriptor"]
        else:
            name = entry["path"].rsplit("/", 1)[-1]
            suffix = Path(name).suffix.lower()
            digest = entry.get("sha256") if index in already else receipts[index]["sha256"]
            descriptor = {
                "index": index, "name": name, "kind": suffix.lstrip(".") or "bin",
                "size": entry["size"], "reader": readers.get(suffix, "unknown"),
                "sha256": digest, "deduplicated": False, "skipped": False,
            }
            existing = already.get(index)
            if existing is None and dedup_path:
                existing = _lookup_dedup(dedup_index, digest, entry["size"])
            destination = _validate_destination(existing or base / entry["path"], request)
            before_hash = _file_hash(destination)
            action = "create"
            if existing is not None:
                action = "dedup"
                descriptor["deduplicated"] = True
            elif destination.is_dir():
                raise HTTPException(409, "По этому пути уже существует папка.")
            elif destination.exists():
                if policy == "skip":
                    action = "skip"
                    descriptor.update(skipped=True, size=destination.stat().st_size,
                                      sha256=before_hash)
                elif policy == "replace":
                    action = "replace"
                else:
                    destination = _available_name(destination)
                    before_hash = None
            descriptor["path"] = str(destination)
            intent = {"action": action, "before_sha256": before_hash, "descriptor": descriptor}
            # Record the exact destination BEFORE publishing. After a crash
            # the same path is verified/reused, never a newly suffixed copy.
            _write_json(record_path, intent)

        destination = _validate_destination(Path(descriptor["path"]), request)
        action = intent["action"]
        current_hash = _file_hash(destination)
        if action in {"dedup", "skip"}:
            if current_hash != descriptor["sha256"] or not destination.is_file():
                raise HTTPException(409, "Ранее выбранный файл изменился; завершение приостановлено")
        elif current_hash != descriptor["sha256"]:
            if current_hash != intent["before_sha256"] or destination.is_dir():
                raise HTTPException(409, "Файл назначения изменился; ваши изменения сохранены")
            destination.parent.mkdir(parents=True, exist_ok=True)
            part = session / "files" / f"{index}.part"
            if action == "replace":
                _replace_with(part, destination)
            else:
                _link_or_move(part, destination)
        # Current hash equals our intended hash: an interrupted publication
        # already succeeded. Do not write the file or allocate another name.
        published.append(descriptor)
        if descriptor["skipped"]:
            skipped.append(index)
        elif dedup_path is not None:
            _append_dedup(dedup_path, dedup_index, {
                "sha256": descriptor["sha256"], "path": str(destination),
                "size": descriptor["size"], "ts": _utcnow(),
            })

    delivered = [item for item in published if not item["skipped"]]
    folder = None
    if _needs_folder(manifest):
        folder = {"path": str(base), "name": base.name, "file_count": len(delivered),
                  "total_bytes": sum(item["size"] for item in delivered)}
    result = {"published": True, "files": published, "folder": folder,
              "skipped": skipped, "excluded": sorted(exclude)}
    _write_json(session / "published.json", result)
    shutil.rmtree(session / "files", ignore_errors=True)
    shutil.rmtree(session / "receipts", ignore_errors=True)
    return result


def _path_under(root: Path, target: Path) -> bool:
    return target == root or root in target.parents


@router.post("/api/uploads/{upload_id}/complete")
async def complete_upload(upload_id: str, request: Request):
    session = _session_dir(request, upload_id)
    exclude: set[int] = set()
    body = await _request_json(request, optional=True)
    if not isinstance(body, dict) or set(body) - {"exclude"}:
        raise HTTPException(400, "Некорректное тело запроса")
    if body:
        if body.get("exclude") is not None:
            values = body["exclude"]
            if not isinstance(values, list):
                raise HTTPException(400, "exclude должен содержать список номеров")
            for value in values:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise HTTPException(400, "exclude должен содержать список номеров")
                exclude.add(value)

    mutation_lock = late_attr("_MANAGED_FILE_MUTATION_LOCK")

    def _run() -> dict[str, Any]:
        if not (session / "manifest.json").is_file():
            raise HTTPException(404, "Загрузка не найдена или уже убрана")
        with _flock(session / ".lock"):
            manifest = _read_json(session / "manifest.json")
            done = session / "published.json"
            if done.is_file():
                return _read_json(done)
            if any(not 0 <= value < len(manifest["files"]) for value in exclude):
                raise HTTPException(400, "exclude ссылается на файл вне списка загрузки")
            with mutation_lock:
                return _publish(session, manifest, exclude, request)

    try:
        return await run_in_threadpool(_run)
    except OSError as exc:
        _raise_storage_error(exc)
    except _IncompleteUpload as incomplete:
        return JSONResponse(
            {"detail": "Загружены не все файлы", "missing": incomplete.missing},
            status_code=409,
        )


# --- совместимость: одиночная загрузка чата --------------------------------


async def publish_single_chat_upload(request: Request, file, profile: str | None) -> dict[str, Any]:
    """Прежний ``POST /api/chat/upload`` поверх сессии из одного файла.

    Старый SPA, закэшированный в браузере, продолжает работать: ответ тот же,
    а файл ложится в такую же папку пакета, как у новой загрузки.
    """
    original = str(getattr(file, "filename", "") or "").strip() or "file"
    name = _name(Path(original).name)
    suffix = Path(name).suffix.lower()
    if suffix in late_attr("_CHAT_DENIED_EXTENSIONS"):
        raise HTTPException(400, f"Файлы {suffix} загружать нельзя")
    if _is_sensitive_filename(name):
        raise HTTPException(400, "Такое имя файла зарезервировано")

    upload_id = str(uuid4())
    manifest: dict[str, Any] = {
        "upload_id": upload_id, "origin": "chat", "target": {"kind": "inbox"},
        "profile": (profile or None), "name": None, "directories": [],
        "files": [{"path": name, "size": 0}], "on_conflict": "copy",
        "total_bytes": 0, "version": 1,
    }
    # Периметр (корень, симлинки, служебные папки) проверяем до того, как на
    # диске появится хоть один байт.
    resolved_target, inbox = _resolve_target(manifest, request)
    session = _session_dir(request, upload_id)

    def _prepare() -> int:
        session.mkdir(parents=True, exist_ok=True)
        os.chmod(session.parent, 0o700)
        (session / "files").mkdir(exist_ok=True)
        (session / "receipts").mkdir(exist_ok=True)
        return _open_part(session, 0, 0)

    mutation_lock = late_attr("_MANAGED_FILE_MUTATION_LOCK")
    fd = await run_in_threadpool(_prepare)
    total = 0
    try:
        try:
            limit = late_attr("_CHAT_MAX_UPLOAD_BYTES")
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise HTTPException(413, f"Файл больше {limit // 1024 ** 2} МБ")
                await run_in_threadpool(_write_chunk, fd, chunk)
            await run_in_threadpool(os.fsync, fd)
        finally:
            await run_in_threadpool(os.close, fd)
            await file.close()
        if total == 0:
            raise HTTPException(400, "Пустой файл")

        manifest["files"][0]["size"] = total
        manifest["total_bytes"] = total
        manifest["created_at"] = _utcnow()
        manifest["resolved_target"] = str(resolved_target)
        manifest["resolved_inbox"] = str(inbox) if inbox is not None else None
        manifest["already_present"] = {}

        def _run() -> dict[str, Any]:
            _write_json(session / "manifest.json", manifest)
            _write_receipt(session, 0, total)
            with _flock(session / ".lock"), mutation_lock:
                return _publish(session, manifest, set(), request)

        result = await run_in_threadpool(_run)
    finally:
        # Идентификатор сессии наружу не уходит, повторить ``complete`` по ней
        # некому — держать каталог до TTL незачем ни после успеха, ни после
        # обрыва.
        await run_in_threadpool(shutil.rmtree, session, True)
    entry = result["files"][0]
    return {
        "ok": True, "path": entry["path"], "name": original,
        "kind": entry["kind"], "size": total, "sha256": entry.get("sha256"),
        "reader": entry["reader"],
    }
