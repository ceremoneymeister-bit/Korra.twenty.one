"""Данные предприятия — экран ввода ставок, режимов и политики цены.

До этого роутера прайсы и нормы попадали в контур только через нас: файл
готовили руками и клали на сервер. Из-за этого расчётчик у клиента простаивал
с пустым каталогом ставок. Здесь методолог заводит и правит их сама, а агенты
считают по ним со следующего же вызова инструмента — паки не кэшируются,
хранилище перечитывает файл каждый раз.

Ключевое решение: **панель не валидирует пак сама и не пишет его сама**. И то
и другое делает `metal-calc-admin`, запускаемый подпроцессом. Причина
техническая, а не эстетическая: движковый venv не импортирует `metal_calc`
(наследование идёт в обратную сторону), поэтому «просто позвать валидатор»
из панели нельзя. А второй свод правил, переписанный на TypeScript или на
Python панели, разошёлся бы с движком на первом же спорном случае — и
разошёлся бы молча, в деньгах.

Отсюда же формат ошибок: CLI отдаёт `{"error": {"code", "message"}}`, и
`message` мы показываем человеку дословно. Он написан валидатором и называет
секцию и строку («materials.09g2s: …»), поэтому подменять его своим текстом
значило бы отобрать у человека единственную подсказку, где чинить.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
import weakref
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException

from korra_cli.web_deps import late

_log = logging.getLogger("korra_cli.web_server")

router = APIRouter()

get_hermes_home = late("get_hermes_home")
load_config = late("load_config")

#: Автор ревизий с этого экрана. Идентификатор, а не имя: имена людей не
#: попадают ни в артефакты, ни в историю ревизий (privacy-гейт сборки образа
#: проверяет это машинно).
DEFAULT_AUTHOR = "panel"

#: Черновик и лимиты. Пак — три-четыре килобайта; мегабайт с запасом ловит
#: заклинивший клиент, а не живого человека.
DRAFT_NAME = "_draft.json"
DRAFT_ARCHIVE_DIR = "_draft_archive"
MAX_BODY_BYTES = 1024 * 1024
CLI_TIMEOUT_SECONDS = 30
_DRAFT_MUTATION_LOCKS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Lock
] = weakref.WeakKeyDictionary()


def _config() -> dict[str, Any]:
    try:
        return load_config() or {}
    except Exception:  # noqa: BLE001 — битый конфиг не должен ронять экран
        _log.debug("calc rates: config unreadable")
        return {}


def _metal_calc_env() -> dict[str, str]:
    section = ((_config().get("mcp_servers") or {}).get("metal_calc") or {})
    env = section.get("env")
    return {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}


#: Умолчание каталога ставок — дословно то же, что у ``metal_calc.config``.
#:
#: Раньше здесь стоял ``HERMES_HOME/rates``, и это стоило часа на выкатке: на
#: контуре, где конфиг не объявляет каталог явно, публикация уходила в
#: ``/etc/metal-calc/rates`` (умолчание CLI), а панель читала пустой
#: ``/opt/data/rates`` и показывала «данные ещё не заведены» поверх заведённых.
#: Расхождение умолчаний двух сторон выглядит как отсутствие данных — самая
#: дорогая форма ошибки, потому что она не похожа на ошибку.
DEFAULT_RATES_ROOT = Path("/etc/metal-calc/rates")


def _rates_root() -> Path:
    """Каталог ставок: из конфига контура, иначе общее с движком умолчание.

    Берём оттуда же, откуда его берёт MCP-сервер. Своя константа означала бы,
    что панель и агент однажды разъедутся по каталогам, и человек будет
    править файл, которого никто не читает.
    """
    configured = (_metal_calc_env().get("METAL_CALC_RATES_ROOT") or "").strip()
    if configured:
        return Path(configured)
    return DEFAULT_RATES_ROOT


def _admin_binary() -> Optional[Path]:
    """Где лежит `metal-calc-admin`.

    Ищем его рядом с MCP-сервером, путь к которому уже записан в конфиге
    контура: оба ставятся одним пакетом в один venv. Так панель не знает ни
    про `/opt/metal-calc`, ни про venv на томе данных — раскладка может
    отличаться между образом и стендом, и это не её забота.
    """
    override = (os.environ.get("METAL_CALC_ADMIN_BIN") or "").strip()
    if override:
        return Path(override)
    command = ((_config().get("mcp_servers") or {}).get("metal_calc") or {}).get("command")
    if isinstance(command, str) and command.strip():
        candidate = Path(command.strip()).with_name("metal-calc-admin")
        if candidate.exists():
            return candidate
    found = shutil.which("metal-calc-admin")
    return Path(found) if found else None


async def _run_admin(
    args: list[str],
    *,
    stdin: bytes | None = None,
    error_statuses: Optional[dict[str, int]] = None,
    default_error_status: int = 400,
) -> dict[str, Any]:
    """Позвать CLI и вернуть его JSON.

    Отказ инструмента (exit 2) — это не сбой панели, а сообщение человеку.
    Для валидации это 400; stateful-команды могут передать таблицу кодов для
    409/422. Всё остальное — 503: чинить это человеку нечем.
    """
    binary = _admin_binary()
    if binary is None:
        raise HTTPException(
            status_code=503,
            detail="Расчётчик не установлен на этом контуре — данные вводить некуда",
        )
    env = {**os.environ, **_metal_calc_env()}
    try:
        process = await asyncio.create_subprocess_exec(
            str(binary),
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError as exc:
        _log.warning("calc rates: cannot start %s: %s", binary, exc)
        raise HTTPException(status_code=503, detail="Не удалось запустить расчётчик") from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=stdin), timeout=CLI_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise HTTPException(status_code=504, detail="Расчётчик не ответил вовремя") from exc

    text = stdout.decode("utf-8", "replace").strip()
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        _log.warning(
            "calc rates: %s produced non-JSON output rc=%s: %.200s",
            binary.name,
            process.returncode,
            stderr.decode("utf-8", "replace"),
        )
        raise HTTPException(status_code=503, detail="Расчётчик ответил неожиданно")

    if process.returncode == 0:
        return payload if isinstance(payload, dict) else {"result": payload}

    error = payload.get("error") if isinstance(payload, dict) else None
    message = (error or {}).get("message") if isinstance(error, dict) else None
    code = str((error or {}).get("code") or "") if isinstance(error, dict) else ""
    # Текст валидатора — единственное, что подсказывает человеку, какую строку
    # чинить. Заменять его на «ошибка сохранения» нельзя.
    status = (error_statuses or {}).get(code, default_error_status)
    raise HTTPException(status_code=status, detail=message or "Данные не прошли проверку")


def _read_json_file(name: str, *, limit: int = MAX_BODY_BYTES) -> Optional[Any]:
    path = _rates_root() / name
    try:
        if path.stat().st_size > limit:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _draft_snapshot() -> tuple[Optional[Any], Optional[str]]:
    """Read one draft and return a content version for compare-and-swap."""
    path = _rates_root() / DRAFT_NAME
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Черновик недоступен") from exc
    if path.is_symlink() or len(raw) > MAX_BODY_BYTES:
        raise HTTPException(
            status_code=503,
            detail="Черновик повреждён или превышает допустимый размер",
        )
    try:
        draft = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Черновик повреждён") from exc
    if not isinstance(draft, dict):
        raise HTTPException(status_code=503, detail="Черновик повреждён")
    return draft, hashlib.sha256(raw).hexdigest()


def _body_bytes(body: Any) -> bytes:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Ожидался объект с данными предприятия")
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Данные слишком велики")
    return raw


def _draft_mutation_lock() -> asyncio.Lock:
    """One lock per running loop; pytest and reloads may use multiple loops."""
    loop = asyncio.get_running_loop()
    lock = _DRAFT_MUTATION_LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _DRAFT_MUTATION_LOCKS[loop] = lock
    return lock


def _expected_draft_version(body: dict[str, Any]) -> Optional[str]:
    """Validate the mandatory compare-and-swap token, including explicit null."""
    if "expected_draft_version" not in body:
        raise HTTPException(
            status_code=400,
            detail="Укажите expected_draft_version текущего черновика",
        )
    version = body.get("expected_draft_version")
    if version is not None and (
        not isinstance(version, str)
        or len(version) != 64
        or any(char not in "0123456789abcdef" for char in version)
    ):
        raise HTTPException(status_code=400, detail="Некорректная версия черновика")
    return version


def _require_draft_version(expected: Optional[str]) -> None:
    """Fail before a stateful CLI call when another tab changed the draft."""
    _current, current = _draft_snapshot()
    if current != expected:
        raise HTTPException(
            status_code=409,
            detail="Черновик уже изменился в другой вкладке; обновите экран",
        )


# ── чтение ───────────────────────────────────────────────────────────────


@router.get("/api/calc/rates/active")
async def calc_rates_active():
    """Действующие данные предприятия целиком — то, по чему считают агенты."""
    pointer = _read_json_file("_active.json")
    if not isinstance(pointer, dict) or not isinstance(pointer.get("revision"), str):
        # Пусто — это «ещё не заполнено», а не ошибка: экран должен открыться
        # и дать заполнить, а не показать красное.
        return {"revision": None, "pack": None, "configured": False}
    revision = pointer["revision"]
    pack = _read_json_file(f"{revision}.json")
    if pack is None:
        raise HTTPException(
            status_code=503, detail=f"Файл действующей ревизии {revision} недоступен"
        )
    return {
        "revision": revision,
        "sha256": pointer.get("sha256"),
        "activated_at": pointer.get("activated_at"),
        "activated_by": pointer.get("activated_by"),
        "pack": pack,
        "configured": True,
    }


@router.get("/api/calc/rates/revisions")
async def calc_rates_revisions():
    """История ревизий: кто, когда и зачем менял цифры."""
    return await _run_admin(["pack-list"])


@router.get("/api/calc/rates/revision/{revision}")
async def calc_rates_revision(revision: str):
    """Содержимое конкретной ревизии — предпросмотр перед откатом."""
    if revision.startswith("_") or "/" in revision or ".." in revision:
        raise HTTPException(status_code=404, detail="Ревизия не найдена")
    pack = _read_json_file(f"{revision}.json")
    if pack is None:
        raise HTTPException(status_code=404, detail="Ревизия не найдена")
    return {"revision": revision, "pack": pack}


@router.get("/api/calc/rates/draft")
async def calc_rates_draft_get():
    """Незавершённый ввод.

    Заполнение прайса — это часовая работа, а вкладки закрывают. Черновик
    паком не является и агентам не виден: он лежит под служебным именем,
    которое нельзя запросить как ревизию.
    """
    draft, version = _draft_snapshot()
    return {"draft": draft, "exists": draft is not None, "version": version}


# ── запись ───────────────────────────────────────────────────────────────


@router.put("/api/calc/rates/draft")
async def calc_rates_draft_put(body: Any = Body(...)):
    if not isinstance(body, dict) or set(body) != {"draft", "expected_version"}:
        raise HTTPException(
            status_code=400,
            detail="Черновик требует поля draft и expected_version",
        )
    expected_version = body.get("expected_version")
    if expected_version is not None and (
        not isinstance(expected_version, str)
        or len(expected_version) != 64
        or any(char not in "0123456789abcdef" for char in expected_version)
    ):
        raise HTTPException(status_code=400, detail="Некорректная версия черновика")
    raw = _body_bytes(body.get("draft"))
    path = _rates_root() / DRAFT_NAME
    async with _draft_mutation_lock():
        try:
            _current, current_version = _draft_snapshot()
            if current_version != expected_version:
                raise HTTPException(
                    status_code=409,
                    detail="Черновик уже изменился в другой вкладке; обновите экран перед сохранением",
                )
            # Пишем через временный файл в том же каталоге: оборванная запись не
            # должна оставить человека с половиной его работы.
            temporary = path.with_name(f".{DRAFT_NAME}.tmp")
            temporary.write_bytes(raw)
            os.replace(temporary, path)
        except HTTPException:
            raise
        except OSError as exc:
            _log.warning("calc rates: draft write failed: %s", exc)
            raise HTTPException(status_code=503, detail="Не удалось сохранить черновик") from exc
    return {
        "ok": True,
        "bytes": len(raw),
        "version": hashlib.sha256(raw).hexdigest(),
    }


@router.post("/api/calc/rates/validate")
async def calc_rates_validate(body: Any = Body(...)):
    """Проверить данные, ничего не записывая — дословными правилами движка."""
    return await _run_admin(["pack-validate"], stdin=_body_bytes(body))


@router.post("/api/calc/rates/publish")
async def calc_rates_publish(body: Any = Body(...)):
    """Опубликовать новую ревизию и сделать её действующей."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Ожидался объект с данными предприятия")
    pack = body.get("pack")
    note = str(body.get("note") or "").strip()
    author = str(body.get("author") or DEFAULT_AUTHOR).strip() or DEFAULT_AUTHOR
    expected_version = _expected_draft_version(body)
    async with _draft_mutation_lock():
        _require_draft_version(expected_version)
        result = await _run_admin(
            ["pack-publish", "--author", author, "--note", note],
            stdin=_body_bytes(pack),
        )
        # Черновик больше не нужен: он уже стал ревизией. Lock + CAS не дают
        # публикации удалить более свежую работу другой вкладки во время CLI.
        try:
            (_rates_root() / DRAFT_NAME).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            _log.warning("calc rates: published draft cleanup failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="Ревизия опубликована, но черновик не удалось убрать; обновите экран",
            ) from exc
    return result


@router.post("/api/calc/rates/activate")
async def calc_rates_activate(body: Any = Body(...)):
    """Вернуть прошлую ревизию, сохранив незаконченный draft в архиве."""
    if not isinstance(body, dict) or not isinstance(body.get("revision"), str):
        raise HTTPException(status_code=400, detail="Не указана ревизия")
    author = str(body.get("author") or DEFAULT_AUTHOR).strip() or DEFAULT_AUTHOR
    expected_version = _expected_draft_version(body)
    draft_path = _rates_root() / DRAFT_NAME
    archived_path: Path | None = None
    async with _draft_mutation_lock():
        _require_draft_version(expected_version)
        try:
            if draft_path.exists():
                archive_root = _rates_root() / DRAFT_ARCHIVE_DIR
                archive_root.mkdir(mode=0o700, exist_ok=True)
                archived_path = archive_root / f"draft-before-activate-{time.time_ns()}.json"
                os.replace(draft_path, archived_path)
            result = await _run_admin(
                ["pack-activate", "--revision", body["revision"], "--author", author]
            )
        except Exception:
            if archived_path is not None and archived_path.exists() and not draft_path.exists():
                try:
                    os.replace(archived_path, draft_path)
                except OSError:
                    _log.exception("calc rates: failed to restore draft after activate error")
            raise
    return {**result, "draft_archived": archived_path is not None}
