#!/usr/bin/env python3
"""Авто-handoff конвейера V2: событие approved -> сессия следующего агента.

Требование записи 40: «расчётчик номер 2 должен получить самостоятельно
задачу от 1-го расчётчика; автоматически инициируется новая сессия, которая
появляется в списке сессий 2-го агента; человек может зайти, наблюдать,
давать поправки».

Механика (все части штатные, проверены спайком A и слайсом 3):
  1. Реестр заказов пишет ``pipeline_events`` (service2) — источник правды.
  2. На ``approved`` стадии N раннер создаёт у профиля N+1 сессию через
     ``POST /p/<profile>/api/sessions`` с обычным детерминированным opaque id.
     Подписанная ``mcs1.<claims>.<hmac>`` capability передаётся отдельно,
     только request-local header первого агентского хода; повтор не плодит дублей
     (структурированный ``session_exists`` считается безопасным повтором).
  3. В сессию отправляется карточка задачи одним non-stream
     ``/v1/chat/completions`` c ``X-Hermes-Session-Id`` — агент отрабатывает
     первый ход сам; сессия видна во вкладке панели.
  4. ``time_costed`` не вызывает модель: книга и mechanical QA выполняются
     типизированным сервисом для order_id события. Курсор двигается только
     после подтверждённого реестром результата.
  5. Курсор прочих событий — ``cursor.json`` рядом с раннером; delivery
     повторяется с backoff в одной детерминированной сессии.

Карточка ссылается на заказ в реестре, а не пересказывает данные прозой:
данные агент читает typed-инструментами (order_get / pipeline_status).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from metal_calc.order_scope import (
    AUTONOMOUS_ACTIVE_STATUSES,
    AUTONOMOUS_STAGE_SCOPE,
    CAPABILITY_TTL_SECONDS,
    event_identity_from_cursor_key,
    issue_order_scope,
    load_scope_secret,
)

# ── конфигурация ─────────────────────────────────────────────────────────

REGISTRY_DB = Path("/opt/data/orders/registry.db")
PROFILES_ROOT = Path("/opt/data/profiles")
RATES_ROOT = Path("/etc/metal-calc/rates")
STATE_DIR = Path("/opt/data/handoff")
TICK_SECONDS = 15
CHAT_TIMEOUT = 420

# Legacy v6: стадия, чьё approved запускает handoff -> (профиль, карточка).
ROUTES: dict[str, tuple[str, str]] = {
    "blank": (
        "raschet-route",
        "Заказ {order_id} (ревизия {revision}): стадия «Заготовка» утверждена. "
        "Прочитай заказ инструментами pipeline_status и order_get, определи "
        "маршрут изготовления и сохрани его инструментом route_propose "
        "Спорные операции помечай (?) в note. Это автоматическая "
        "передача от расчётчика заготовки.",
    ),
    "route": (
        "raschet-time",
        "Заказ {order_id} (ревизия {revision}): маршрут утверждён. Прочитай "
        "его инструментами pipeline_status и order_get и посчитай нормы "
        "времени инструментом time_calc. Если для какой-то "
        "позиции не хватает геометрических параметров — перечисли, каких "
        "именно, и остановись, не выдумывая значений. Это автоматическая "
        "передача от расчётчика маршрута.",
    ),
    "time": (
        "raschet-time",
        "Заказ {order_id} (ревизия {revision}): нормы времени утверждены. "
        "Собери итоговое КП инструментом quote_build; extras "
        "добавляй только если они явно есть в данных заказа. Это "
        "автоматическая передача завершающего шага конвейера.",
    ),
}

# Схема V2 (V3-конвейер): событие workflow_events -> (профиль, карточка).
# Имя события сверяется по части до двоеточия (route_frozen:variant-2).
V3_ROUTES: dict[str, tuple[str, str]] = {
    "input_frozen": (
        "raschet-route",
        "Заказ {order_id} (ревизия {revision}): вход зафиксирован фронтом. "
        "Ты — технолог-маршрутчик. Прочитай заказ (workflow_status, "
        "order_get), объяви состав изделия инструментом bom_upsert, затем "
        "предложи маршрут route_variants_propose (сначала посмотри "
        "process_catalog: in_house только для кодов парка) и заморозь его "
        "route_freeze. Спорные шаги помечай (?) в note. Это автоматическая "
        "передача от фронта.",
    ),
    "route_return": (
        "raschet-route",
        "Заказ {order_id} (ревизия {revision}): маршрут формально возвращён "
        "технологу. Прочитай текущий заказ (workflow_status, order_get), "
        "предложи исправленный вариант route_variants_propose и заморозь его "
        "route_freeze. Причину возврата возьми из workflow; спорные шаги "
        "пометь (?) в note. Это отдельная передача после возврата маршрута.",
    ),
    "route_frozen": (
        "raschet-blank",
        "Заказ {order_id} (ревизия {revision}): маршрут заморожен. Ты — "
        "агент материалов и заготовки. Прочитай заказ (workflow_status, "
        "order_get), определи материал и массу и посчитай свою зону "
        "инструментом blank_drivers_set: каждый in_house-шаг заготовки "
        "ровно один раз. Чего не хватает — перечисли и остановись, не "
        "выдумывая. Это автоматическая передача от технолога.",
    ),
    "blank_costed": (
        "raschet-time",
        "Заказ {order_id} (ревизия {revision}): заготовка посчитана. Ты — "
        "нормировщик. Прочитай заказ (workflow_status, order_get) и посчитай "
        "нормы станочных шагов и строки прочих работ инструментом "
        "time_norms_set. Не хватает геометрических параметров — перечисли, "
        "каких, и остановись. Это автоматическая передача от агента "
        "заготовки.",
    ),
}

# Книга и механический QA не требуют интерпретации. Этот этап запускается
# непосредственно типизированным сервисом и потому не выдаёт LLM capability
# чтения/изменения произвольного order_id.
DIRECT_V3_STAGES = frozenset({"time_costed"})


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}", flush=True)


# ── профили: адрес и ключ API-сервера ────────────────────────────────────


def profile_api(profile: str) -> tuple[str, str]:
    # Korra 21 serves all profiles through one authenticated gateway.
    # Keep the old per-profile layout usable by the existing runner tests.
    shared_port = os.environ.get("METAL_CALC_GATEWAY_PORT")
    if shared_port is not None and profile not in {
        target for target, _ in (*ROUTES.values(), *V3_ROUTES.values())
    }:
        raise TargetConfigurationError("unknown calculator handoff profile")
    env_path = (
        PROFILES_ROOT.parent / ".env"
        if shared_port is not None
        else PROFILES_ROOT / profile / ".env"
    )
    try:
        values: dict[str, str] = {}
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key] = value.strip().strip('"').strip("'")
        port = int(shared_port if shared_port is not None else values["API_SERVER_PORT"])
        api_key = values["API_SERVER_KEY"]
    except (OSError, KeyError, ValueError) as error:
        raise TargetConfigurationError(
            f"profile API is not configured: {profile} ({type(error).__name__})"
        ) from error
    if not 1 <= port <= 65535 or not api_key:
        raise TargetConfigurationError(f"profile API is not configured: {profile}")
    base = f"http://127.0.0.1:{port}"
    return (f"{base}/p/{profile}" if shared_port is not None else base), api_key


class TargetConfigurationError(RuntimeError):
    """Цель handoff не настроена достаточно безопасно для вызова."""


class CursorStateError(RuntimeError):
    """Курсор существует, но его нельзя безопасно использовать."""


def scoped_order_capability(job: dict, expires_at: int) -> str:
    """Issue the signed request-only capability for one handoff event."""
    stage = str(job.get("stage") or "")
    scope = AUTONOMOUS_STAGE_SCOPE.get(stage)
    if scope is None or job.get("mode") != "agent":
        raise TargetConfigurationError("job is not an autonomous V3 handoff")
    role, expected_profile = scope
    if job.get("profile") != expected_profile:
        raise TargetConfigurationError("handoff profile does not match workflow stage")
    return issue_order_scope(
        load_scope_secret(),
        order_id=str(job["order_id"]),
        role=role,
        profile=expected_profile,
        stage=stage,
        event_identity=event_identity_from_cursor_key(str(job["cursor_key"])),
        expires_at=expires_at,
    )


def handoff_session_id(job: dict, expires_at: int) -> str:
    """Derive a harmless opaque session id; it grants no order access."""
    identity = hashlib.sha256(
        f"{job['cursor_key']}\0{expires_at}".encode("utf-8")
    ).hexdigest()[:32]
    return f"handoff-{identity}"


def target_api(job: dict) -> tuple[str, str]:
    """Разрешить API-цель в обычном изолированном profile layout."""
    return profile_api(job["profile"])


def _request(url: str, key: str, payload: dict, headers: dict | None = None, timeout: int = 30):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            **(headers or {}),
        },
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=timeout)


def _session_has_card(base: str, key: str, session_id: str, card: str) -> bool:
    """Reconcile an ambiguous POST without sending the same turn twice."""
    request = urllib.request.Request(
        f"{base}/api/sessions/{session_id}/messages?limit=100",
        headers={"Authorization": f"Bearer {key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except Exception:  # noqa: BLE001 — отсутствие доказательства означает retry
        return False
    if (
        not isinstance(payload, dict)
        or payload.get("object") != "list"
        or payload.get("session_id") != session_id
    ):
        return False
    messages = payload.get("data")
    if not isinstance(messages, list):
        return False
    return any(
        isinstance(message, dict)
        and message.get("role") == "user"
        and message.get("content") == card
        for message in messages
    )


# ── события и курсор ─────────────────────────────────────────────────────


def pending_handoffs(states: list[dict], cursor: dict) -> list[dict]:
    """Невыполненные handoff-задания из событий всех заказов.

    Чистая функция (тестируется без БД). Два источника: legacy
    ``pipeline_events`` (approved стадий из ROUTES) и V3 ``workflow_events``
    (имена из V3_ROUTES, суффикс после «:» отбрасывается). Каждое задание
    несёт профиль и шаблон карточки — исполнителю не нужно знать, из какого
    конвейера оно пришло.
    """
    jobs = []
    scheduled: set[str] = set()
    for state in states:
        order_id = state.get("order_id")
        for event_index, event in enumerate(state.get("pipeline_events", [])):
            stage = event.get("stage")
            if event.get("event") != "approved" or stage not in ROUTES:
                continue
            key = f"{order_id}:{stage}:{event.get('at')}:{event_index}"
            if cursor.get(key) or key in scheduled:
                continue
            profile, card = ROUTES[stage]
            jobs.append(
                {
                    "order_id": order_id,
                    "stage": stage,
                    "cursor_key": key,
                    "profile": profile,
                    "card": card,
                }
            )
            scheduled.add(key)
        for event_index, event in enumerate(state.get("workflow_events", [])):
            name = str(event.get("event") or "").split(":", 1)[0]
            if name not in V3_ROUTES and name not in DIRECT_V3_STAGES:
                continue
            key = f"{order_id}:wf:{name}:{event.get('at')}:{event_index}"
            if cursor.get(key) or key in scheduled:
                continue
            job = {
                "order_id": order_id,
                "stage": name,
                "cursor_key": key,
                "event_at": event.get("at"),
                "event_index": event_index,
            }
            if name in V3_ROUTES:
                target, card = V3_ROUTES[name]
                job.update({"profile": target, "card": card, "mode": "agent"})
            else:
                job["mode"] = "deterministic_book"
            jobs.append(job)
            scheduled.add(key)
    return jobs


def agent_trigger_is_current(job: dict, state: dict) -> bool:
    """Reject superseded historical workflow events after cursor loss.

    A durable registry may contain several events with the same stage after an
    ADJUST/recalculation loop.  Only the latest exact event is eligible and the
    workflow must still be waiting at that stage; otherwise replaying a cold
    cursor would start an obsolete agent turn against the current revision.
    """
    stage = job.get("stage")
    active_statuses = AUTONOMOUS_ACTIVE_STATUSES.get(stage)
    if active_statuses is None:
        return True  # legacy jobs retain their established behavior
    workflow = state.get("workflow")
    events = state.get("workflow_events")
    event_index = job.get("event_index")
    if (
        not isinstance(workflow, dict)
        or workflow.get("status") not in active_statuses
        or not isinstance(events, list)
        or not isinstance(event_index, int)
        or not 0 <= event_index < len(events)
    ):
        return False
    event = events[event_index]
    if (
        not isinstance(event, dict)
        or str(event.get("event") or "").split(":", 1)[0] != stage
        or event.get("at") != job.get("event_at")
    ):
        return False
    matching_indexes = [
        index
        for index, candidate in enumerate(events)
        if isinstance(candidate, dict)
        and str(candidate.get("event") or "").split(":", 1)[0] == stage
    ]
    if not matching_indexes or event_index != matching_indexes[-1]:
        return False
    if stage == "input_frozen" and any(
        isinstance(candidate, dict)
        and str(candidate.get("event") or "").split(":", 1)[0] == "route_return"
        for candidate in events[event_index + 1:]
    ):
        return False
    return True


def _time_costed_events_after_trigger(job: dict, state: dict) -> list[dict]:
    """Вернуть только события после именно этого ``time_costed``."""
    events = state.get("workflow_events") or []
    if not isinstance(events, list):
        return []
    expected_at = job.get("event_at")
    expected_index = job.get("event_index")
    if isinstance(expected_index, int) and 0 <= expected_index < len(events):
        candidate = events[expected_index]
        if (
            isinstance(candidate, dict)
            and str(candidate.get("event") or "").split(":", 1)[0]
            == "time_costed"
            and candidate.get("at") == expected_at
        ):
            return events[expected_index + 1:]
    for index, event in enumerate(events):
        if (
            isinstance(event, dict)
            and str(event.get("event") or "").split(":", 1)[0]
            == "time_costed"
            and event.get("at") == expected_at
        ):
            return events[index + 1:]
    return []


def time_costed_postcondition(job: dict, state: dict) -> bool:
    """Книга и mechanical QA подтверждены реестром, а не ответом модели."""
    if job.get("stage") != "time_costed":
        return False
    workflow = state.get("workflow")
    if not isinstance(workflow, dict):
        return False
    try:
        current_calculation_revision = int(workflow["calculation_revision"])
    except (KeyError, TypeError, ValueError):
        return False

    later_names = {
        str(event.get("event") or "")
        for event in _time_costed_events_after_trigger(job, state)
        if isinstance(event, dict)
    }
    book = state.get("book")
    qa = state.get("qa") or {}
    if not isinstance(qa, dict):
        qa = {}
    revision_receipts = qa.get(str(current_calculation_revision)) or {}
    current_receipt = (
        revision_receipts.get("mechanical")
        if isinstance(revision_receipts, dict)
        else None
    )
    current_checks = current_receipt.get("checks") if isinstance(current_receipt, dict) else None
    if (
        "qa_mechanical:PASS" in later_names
        and isinstance(book, dict)
        and book.get("calculation_revision") == current_calculation_revision
        and isinstance(current_receipt, dict)
        and current_receipt.get("verdict") == "PASS"
        and current_receipt.get("computed_by") == "engine"
        and isinstance(current_checks, list)
        and bool(current_checks)
        and all(isinstance(check, dict) and check.get("ok") is True for check in current_checks)
    ):
        return True

    if "qa_mechanical:ADJUST" not in later_names:
        return False
    history = state.get("workflow_history") or []
    if not isinstance(history, list):
        return False
    for archived in history:
        if not isinstance(archived, dict) or archived.get("reason") != "qa_mechanical_adjust":
            continue
        try:
            archived_revision = int(archived["calculation_revision"])
        except (KeyError, TypeError, ValueError):
            continue
        archived_book = archived.get("book")
        archived_qa = archived.get("qa") or {}
        receipt = (
            archived_qa.get("mechanical")
            if isinstance(archived_qa, dict)
            else None
        )
        archived_checks = receipt.get("checks") if isinstance(receipt, dict) else None
        if (
            archived_revision < current_calculation_revision
            and isinstance(archived_book, dict)
            and archived_book.get("calculation_revision") == archived_revision
            and isinstance(receipt, dict)
            and receipt.get("verdict") == "ADJUST"
            and receipt.get("computed_by") == "engine"
            and isinstance(archived_checks, list)
            and bool(archived_checks)
            and any(
                isinstance(check, dict) and check.get("ok") is False
                for check in archived_checks
            )
        ):
            return True
    return False


def load_cursor() -> dict:
    try:
        raw = (STATE_DIR / "cursor.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        cursor = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CursorStateError(f"invalid cursor JSON: {error}") from error
    if not isinstance(cursor, dict):
        raise CursorStateError("invalid cursor: top-level value must be an object")
    return cursor


def save_cursor(cursor: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_DIR / "cursor.json.tmp"
    with tmp.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(cursor, ensure_ascii=False, indent=1))
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(STATE_DIR / "cursor.json")
    directory_fd = os.open(STATE_DIR, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@contextmanager
def tick_lock():
    """Не позволять двум процессам одновременно доставлять один cursor batch."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / "runner.lock").open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


# ── исполнение одного handoff ────────────────────────────────────────────


def execute(job: dict, revision: int, attempt_no: int = 0) -> bool:
    profile, card_template = job["profile"], job["card"]
    target_label = profile
    try:
        base, key = target_api(job)
    except TargetConfigurationError as error:
        log(f"target unavailable: {target_label} {error}")
        return False
    # Один event/revision — одна сессия. Сетевой timeout неоднозначен: сервер
    # мог принять ход. Новый -tN плодил параллельных исполнителей одного заказа.
    if job.get("mode") == "agent":
        session_id = job.get("delivery_session_id")
        tool_scope = job.get("delivery_tool_scope")
        if (
            not isinstance(session_id, str)
            or not session_id.startswith("handoff-")
            or not isinstance(tool_scope, str)
            or not tool_scope.startswith("mcs1.")
        ):
            log(f"scope missing: {target_label} stage={job.get('stage')}")
            return False
    else:
        # Legacy jobs retain their old deterministic session identity. V3
        # autonomous jobs can only enter through the signed branch above.
        event_identity = hashlib.sha256(
            job["cursor_key"].encode("utf-8")
        ).hexdigest()[:16]
        session_id = f"handoff-{job['order_id']}-{job['stage']}-{event_identity}"
        tool_scope = ""
    session_ref = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    title = f"Авто: {job['order_id']} / {job['stage']} approved"  # без ←: title-валидатор его режет

    try:
        with _request(
            f"{base}/api/sessions", key,
            {"id": session_id, "title": title, "source": "handoff-runner"},
        ) as response:
            response.read()
        log(f"session created: {target_label} session_ref={session_ref}")
    except urllib.error.HTTPError as error:
        detail = ""
        error_code = None
        try:
            detail = error.read().decode("utf-8", "replace")
            error_code = (json.loads(detail).get("error") or {}).get("code")
        except Exception:  # noqa: BLE001
            pass
        if error.code == 409 and error_code == "session_exists":
            pass  # создана прошлой попыткой — это норма, шлём карточку туда
        else:
            log(
                f"session create failed {error.code}: "
                f"{target_label} session_ref={session_ref} code={error_code or 'unknown'}"
            )
            return False
    except Exception as error:  # noqa: BLE001 — сбой одной цели не роняет весь tick
        log(
            f"session create failed: {target_label} session_ref={session_ref} "
            f"{type(error).__name__}"
        )
        return False

    card = job.get("delivery_card") or card_template.format(
        order_id=job["order_id"], revision=revision
    )
    # The API server coalesces concurrent/retried non-stream requests carrying
    # the same Idempotency-Key.  The key is derived from the exact immutable
    # handoff turn, so an ambiguous timeout cannot start a second agent turn
    # during the server's idempotency window.
    idempotency_key = "handoff-" + hashlib.sha256(
        f"{session_id}\0{card}\0{tool_scope}".encode("utf-8")
    ).hexdigest()
    try:
        with _request(
            f"{base}/v1/chat/completions",
            key,
            {"model": "default", "stream": False,
             "messages": [{"role": "user", "content": card}]},
            headers={
                "X-Hermes-Session-Id": session_id,
                **(
                    {"X-Hermes-Tool-Scope": tool_scope}
                    if tool_scope
                    else {}
                ),
                "Idempotency-Key": idempotency_key,
            },
            timeout=CHAT_TIMEOUT,
        ) as response:
            data = json.loads(response.read())
        preview = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        if not isinstance(preview, str) or not preview.strip():
            raise RuntimeError("empty model response")
        if preview.strip().startswith("API call failed"):
            raise RuntimeError(f"model refused: {preview[:60]}")
        log(
            f"card delivered: {target_label} session_ref={session_ref} "
            f"answer={preview[:80]!r}"
        )
        return True
    except Exception as error:  # noqa: BLE001 — ambiguous failure is reconciled next tick
        if _session_has_card(base, key, session_id, card):
            log(
                f"card delivery reconciled from session: "
                f"{target_label} session_ref={session_ref}"
            )
            return True
        log(
            f"card delivery uncertain: {target_label} session_ref={session_ref} "
            f"{type(error).__name__}"
        )
        return False


def execute_book_machine(job: dict, revision: int, registry) -> bool:
    """Собрать книгу и запустить mechanical QA без модели и prompt boundary."""
    from metal_calc.packs2 import PipelinePackStore
    from metal_calc.securefs import SecureRoot
    from metal_calc.service3 import WorkflowService

    with SecureRoot(RATES_ROOT, writable=False) as rates_root:
        workflow_service = WorkflowService(
            registry,
            PipelinePackStore(rates_root),
        )
        _, state = registry.get(job["order_id"])
        status = (state.get("workflow") or {}).get("status")
        if status == "COSTING_COMPLETE":
            result = workflow_service.book_assemble(
                job["order_id"], revision, {}, actor_role="book_machine"
            )
            revision = int(result["revision"])
            status = "BOOK_ASSEMBLED"
        if status != "BOOK_ASSEMBLED":
            return False
        workflow_service.qa_run_mechanical(
            job["order_id"], revision, actor_role="book_machine"
        )
    return True


# ── главный цикл ─────────────────────────────────────────────────────────


def _tick_locked(registry) -> None:
    cursor = load_cursor()
    states = registry.all_states()
    for job in pending_handoffs(states, cursor):
        attempts_key = f"attempts:{job['cursor_key']}"
        retry_key = f"retry_not_before:{job['cursor_key']}"
        delivery_revision_key = f"delivery_revision:{job['cursor_key']}"
        delivery_card_key = f"delivery_card:{job['cursor_key']}"
        delivery_scope_expiry_key = f"delivery_scope_expiry:{job['cursor_key']}"
        delivery_session_id_key = f"delivery_session_id:{job['cursor_key']}"
        if time.time() < float(cursor.get(retry_key, 0)):
            continue
        attempt_no = int(cursor.get(attempts_key, 0))
        try:
            revision, state = registry.get(job["order_id"])
        except Exception as error:  # noqa: BLE001 — один заказ не блокирует очередь
            log(
                f"state read failed: {job['order_id']} "
                f"{type(error).__name__} {error}"
            )
            cursor[attempts_key] = attempt_no + 1
            save_cursor(cursor)
            continue

        requires_postcondition = job.get("stage") == "time_costed"
        if job.get("mode") == "agent" and not agent_trigger_is_current(job, state):
            log(
                f"handoff obsolete: {job['order_id']} stage={job['stage']} "
                f"event={job['cursor_key']}"
            )
            cursor[job["cursor_key"]] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            cursor.pop(attempts_key, None)
            cursor.pop(retry_key, None)
            cursor.pop(delivery_revision_key, None)
            cursor.pop(delivery_card_key, None)
            cursor.pop(delivery_scope_expiry_key, None)
            cursor.pop(delivery_session_id_key, None)
            save_cursor(cursor)
            continue
        if requires_postcondition and time_costed_postcondition(job, state):
            log(f"handoff already complete: {job['order_id']} stage=time_costed")
            cursor[job["cursor_key"]] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            cursor.pop(attempts_key, None)
            cursor.pop(delivery_revision_key, None)
            cursor.pop(delivery_card_key, None)
            cursor.pop(delivery_scope_expiry_key, None)
            cursor.pop(delivery_session_id_key, None)
            save_cursor(cursor)
            continue

        delivery_revision = revision
        delivery_job = job
        if job.get("mode") == "agent":
            # An expired bearer cannot make progress. Rotate the whole frozen
            # delivery identity only after expiry and only while the exact
            # workflow trigger above is still current. Unexpired retries keep
            # the byte-identical session/card/revision tuple.
            now = int(time.time())
            existing_expiry = cursor.get(delivery_scope_expiry_key)
            if existing_expiry is not None and int(existing_expiry) <= now:
                cursor.pop(delivery_revision_key, None)
                cursor.pop(delivery_card_key, None)
                cursor.pop(delivery_scope_expiry_key, None)
                cursor.pop(delivery_session_id_key, None)
            # Freeze the complete delivery payload identity before the first
            # potentially ambiguous network POST. save_cursor() fsyncs both
            # file and directory, so retries cannot drift across a registry
            # revision or deployed card-template change.
            if delivery_revision_key not in cursor:
                cursor[delivery_revision_key] = revision
            delivery_revision = int(cursor[delivery_revision_key])
            if delivery_card_key not in cursor:
                cursor[delivery_card_key] = job["card"].format(
                    order_id=job["order_id"], revision=delivery_revision
                )
            if delivery_scope_expiry_key not in cursor:
                cursor[delivery_scope_expiry_key] = (
                    now + CAPABILITY_TTL_SECONDS
                )
            if delivery_session_id_key not in cursor:
                try:
                    cursor[delivery_session_id_key] = handoff_session_id(
                        job, int(cursor[delivery_scope_expiry_key])
                    )
                except Exception as error:  # noqa: BLE001 — fail closed per job
                    log(
                        f"scope issuance failed: {job['order_id']} "
                        f"stage={job['stage']} {type(error).__name__}"
                    )
                    cursor[attempts_key] = attempt_no + 1
                    cursor[retry_key] = int(time.time()) + min(
                        300, 15 * (2 ** min(attempt_no, 4))
                    )
                    save_cursor(cursor)
                    continue
            save_cursor(cursor)
            try:
                delivery_tool_scope = scoped_order_capability(
                    job, int(cursor[delivery_scope_expiry_key])
                )
            except Exception as error:  # noqa: BLE001 — fail closed per job
                log(
                    f"scope issuance failed: {job['order_id']} "
                    f"stage={job['stage']} {type(error).__name__}"
                )
                cursor[attempts_key] = attempt_no + 1
                cursor[retry_key] = int(time.time()) + min(
                    300, 15 * (2 ** min(attempt_no, 4))
                )
                save_cursor(cursor)
                continue
            delivery_job = {
                **job,
                "delivery_card": str(cursor[delivery_card_key]),
                "delivery_session_id": str(cursor[delivery_session_id_key]),
                "delivery_tool_scope": delivery_tool_scope,
            }

        log(f"handoff: {job['order_id']} stage={job['stage']} attempt={attempt_no}")
        try:
            if job.get("mode") == "deterministic_book":
                delivered = execute_book_machine(job, revision, registry)
            else:
                delivered = execute(delivery_job, delivery_revision, attempt_no)
        except Exception as error:  # noqa: BLE001 — изоляция одного задания
            delivered = False
            log(
                f"handoff failed: {job['order_id']} stage={job['stage']} "
                f"{type(error).__name__}"
            )

        completed = delivered
        if requires_postcondition:
            completed = False
            try:
                _, state_after = registry.get(job["order_id"])
                completed = time_costed_postcondition(job, state_after)
            except Exception as error:  # noqa: BLE001 — без state нет доказательства успеха
                log(
                    f"postcondition read failed: {job['order_id']} "
                    f"{type(error).__name__} {error}"
                )

        if completed:
            cursor[job["cursor_key"]] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            cursor.pop(attempts_key, None)
            cursor.pop(retry_key, None)
            cursor.pop(delivery_revision_key, None)
            cursor.pop(delivery_card_key, None)
            cursor.pop(delivery_scope_expiry_key, None)
            cursor.pop(delivery_session_id_key, None)
        else:
            cursor[attempts_key] = attempt_no + 1
            cursor[retry_key] = int(time.time()) + min(300, 15 * (2 ** min(attempt_no, 4)))
        save_cursor(cursor)


def tick(registry) -> None:
    with tick_lock() as acquired:
        if not acquired:
            log("tick skipped: another handoff runner holds the lock")
            return
        _tick_locked(registry)


def main() -> None:
    from metal_calc.registry import Registry

    registry = Registry(REGISTRY_DB)
    log(f"handoff runner started, tick={TICK_SECONDS}s")
    once = "--once" in sys.argv
    while True:
        try:
            tick(registry)
        except Exception as error:  # noqa: BLE001 — раннер не умирает от одного сбоя
            log(f"tick failed: {type(error).__name__} {error}")
        if once:
            return
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
