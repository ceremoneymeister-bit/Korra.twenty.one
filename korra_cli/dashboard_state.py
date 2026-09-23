"""Live data for the personal dashboard cards (``GET /api/dashboard/state``).

Every card of the 0.21.12 board rendered an honest "summary unavailable"
because nothing on the server fed it. This module is that feed: one read-only
pass over stores the installation already owns, shaped for the five cards
plus the Codex quota card.

Three rules shape every section:

* **Zero and invention are equally wrong.** A source that could not be read
  is reported as an error for that source, never as "nothing to show". An
  empty source says so explicitly, so the card can offer the first step.
* **Read-only, always.** Databases are opened with ``mode=ro``; nothing here
  creates tables, repairs files, refreshes tokens or calls a model. The
  dashboard is the first screen and may run next to a busy gateway on a
  2 vCPU / 3 GiB host.
* **Bounded work.** Directory walks have an entry budget, queries are
  aggregate or ``LIMIT``-ed, and every section is cached for a few seconds so
  several open tabs do not multiply the load.
"""

from __future__ import annotations

import errno
import heapq
import json
import logging
import os
import re
import sqlite3
import stat
import sys
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

STATE_VERSION = 1

PERIOD_DAYS = {"week": 7, "month": 30}
DEFAULT_PERIOD = "week"

#: Seconds each section may be served from memory.
SECTION_TTL = {
    "attention": 10.0,
    "agents": 30.0,
    "metrics": 60.0,
    "upcoming": 20.0,
    "artifacts": 30.0,
    "quota": 10.0,
}

#: Sessions that are not a conversation with the owner.
_BACKGROUND_SOURCES = frozenset({"cron", "kanban"})
_SERVICE_SOURCES = frozenset({"maintenance"})
_NOT_DIALOG_SOURCES = _BACKGROUND_SOURCES | _SERVICE_SOURCES | frozenset({"subagent"})

MAIN_AGENT_LABEL = "Корра"

_PLATFORM_LABELS = {
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "discord": "Discord",
    "slack": "Slack",
    "email": "почту",
    "max": "MAX",
    "vk": "ВКонтакте",
}

_PROVIDER_LABELS = {
    "openai-codex": "ChatGPT (Codex)",
    "anthropic": "Claude",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "nous": "Nous Portal",
    "google": "Google",
    "gemini": "Gemini",
}

_TERMINAL_AUTH_REASONS = frozenset({
    "token_invalidated",
    "token_revoked",
    "invalid_token",
    "invalid_grant",
    "unauthorized_client",
    "refresh_token_reused",
    "credential_persist_failed",
})


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Agent:
    """One profile of the installation, as the dashboard names it."""

    #: Wire name: ``""`` for the main agent, the profile name otherwise —
    #: the same value ``/api/chat/runs`` and the agents screen use.
    profile: str
    name: str
    home: Path
    label: str


def _wire(name: str) -> str:
    return "" if not name or name == "default" else name


def list_agents() -> list[Agent]:
    """Profiles this installation serves, main agent first."""
    from korra_cli import profiles as profiles_mod
    from korra_constants import get_default_hermes_root

    try:
        pairs = list(profiles_mod.profiles_to_serve(multiplex=True))
    except Exception:
        logger.debug("dashboard: profile enumeration failed", exc_info=True)
        pairs = []
    if not pairs:
        pairs = [("default", get_default_hermes_root())]

    agents: list[Agent] = []
    seen: set[str] = set()
    for name, home in pairs:
        name = str(name or "default")
        if name in seen:
            continue
        seen.add(name)
        try:
            display = profiles_mod.read_profile_meta(Path(home)).get("display_name") or ""
        except Exception:
            display = ""
        label = display or (MAIN_AGENT_LABEL if name == "default" else name)
        agents.append(Agent(profile=_wire(name), name=name, home=Path(home), label=label))
    agents.sort(key=lambda agent: (agent.profile != "", agent.name))
    return agents


def _label_for(agents: Iterable[Agent], profile: Optional[str]) -> Optional[str]:
    if profile is None:
        return None
    wire = _wire(str(profile))
    for agent in agents:
        if agent.profile == wire:
            return agent.label
    return str(profile) or None


def agent_href(profile: str, session_id: Optional[str] = None) -> str:
    """Address of an agent's chat, the same one the agents screen accepts."""
    from urllib.parse import urlencode

    params = {"agent": profile or "default"}
    if session_id:
        params["resume"] = session_id
    return f"/agents?{urlencode(params)}"


def _ro(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite file strictly read-only."""
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Column names of ``table``; empty when the table does not exist.

    A damaged file raises instead: "no such table" and "cannot read the
    store" must not look alike to the owner.
    """
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _parse_iso(value: Any, tz: Optional[tzinfo] = None) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz) if tz is not None else parsed.astimezone()
    return parsed


def _timezone() -> Optional[tzinfo]:
    try:
        from korra_time import get_timezone

        return get_timezone()
    except Exception:
        return None


def _timezone_name(tz: Optional[tzinfo]) -> Optional[str]:
    key = getattr(tz, "key", None)
    return key if isinstance(key, str) and key else None


def _local(ts: float, tz: Optional[tzinfo]) -> datetime:
    return datetime.fromtimestamp(ts, tz) if tz is not None else datetime.fromtimestamp(ts).astimezone()


def _midnight(ts: float, tz: Optional[tzinfo]) -> datetime:
    return _local(ts, tz).replace(hour=0, minute=0, second=0, microsecond=0)


_MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _human_moment(ts: float, now: float, tz: Optional[tzinfo]) -> str:
    """«сегодня в 14:00», «завтра в 09:30», «25 сентября в 14:00»."""
    moment = _local(ts, tz)
    today = _midnight(now, tz)
    clock = moment.strftime("%H:%M")
    day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    delta_days = round((day - today).total_seconds() / 86400)
    if delta_days == 0:
        return f"сегодня в {clock}"
    if delta_days == 1:
        return f"завтра в {clock}"
    return f"{moment.day} {_MONTHS_GENITIVE[moment.month - 1]} в {clock}"


def _trim(text: Any, limit: int = 160) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Section cache
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: dict[tuple, tuple[float, Any]] = {}
_key_locks: dict[tuple, threading.Lock] = {}


def _cached(key: tuple, ttl: float, compute: Callable[[], Any]) -> Any:
    """Serve ``compute()`` from memory for ``ttl`` seconds of wall time.

    One computation per key at a time: two tabs polling together share the
    work instead of doubling it.
    """
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] > time.time():
            return hit[1]
        lock = _key_locks.setdefault(key, threading.Lock())
    with lock:
        with _cache_lock:
            hit = _cache.get(key)
            if hit is not None and hit[0] > time.time():
                return hit[1]
        value = compute()
        with _cache_lock:
            _cache[key] = (time.time() + ttl, value)
        return value


def reset_cache() -> None:
    """Forget every cached section (tests and config changes)."""
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Metrics: «Мои показатели»
# ---------------------------------------------------------------------------

METRIC_KEYS = ("dialogs", "messages", "background", "tokens")


def metrics_section(
    agents: list[Agent], *, period: str, now: float, tz: Optional[tzinfo]
) -> dict[str, Any]:
    """Daily counts for the chosen period and the one before it."""
    days = PERIOD_DAYS.get(period, PERIOD_DAYS[DEFAULT_PERIOD])
    today = _midnight(now, tz)
    start = today - timedelta(days=days - 1)
    previous_start = start - timedelta(days=days)
    end = today + timedelta(days=1)
    base = previous_start.timestamp()

    series = {key: [0] * days for key in METRIC_KEYS}
    previous = dict.fromkeys(METRIC_KEYS, 0)
    unreadable: list[str] = []
    read = 0

    for agent in agents:
        db = agent.home / "state.db"
        if not db.is_file():
            continue
        try:
            with closing(_ro(db)) as conn:
                cols = _columns(conn, "sessions")
                if not cols:
                    continue
                root = "parent_session_id IS NULL" if "parent_session_id" in cols else "1"
                rows = conn.execute(
                    "SELECT CAST((started_at - ?) / 86400 AS INTEGER) AS d, "
                    "COALESCE(source, '') AS source, "
                    f"({root}) AS root, COUNT(*) AS n, "
                    "COALESCE(SUM(message_count), 0) AS messages, "
                    "COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0) AS tokens "
                    "FROM sessions WHERE started_at >= ? AND started_at < ? "
                    "GROUP BY d, source, root",
                    (base, base, end.timestamp()),
                ).fetchall()
        except sqlite3.Error:
            logger.warning("dashboard metrics: %s unreadable", db, exc_info=True)
            unreadable.append(agent.label)
            continue
        read += 1
        for row in rows:
            source = str(row["source"] or "")
            if source in _SERVICE_SOURCES:
                continue
            index = int(row["d"])
            if index < 0 or index >= 2 * days:
                continue
            values = {
                "dialogs": int(row["n"]) if row["root"] and source not in _NOT_DIALOG_SOURCES else 0,
                "messages": int(row["messages"]) if row["root"] and source not in _NOT_DIALOG_SOURCES else 0,
                "background": int(row["n"]) if row["root"] and source in _BACKGROUND_SOURCES else 0,
                "tokens": int(row["tokens"] or 0),
            }
            for key, value in values.items():
                if index < days:
                    previous[key] += value
                else:
                    series[key][index - days] += value

    totals = {key: sum(series[key]) for key in METRIC_KEYS}
    if read == 0:
        status = "error" if unreadable else "empty"
    elif not any(totals.values()) and not any(previous.values()):
        status = "empty"
    else:
        status = "ok"
    return {
        "status": status,
        "period": period if period in PERIOD_DAYS else DEFAULT_PERIOD,
        "days": days,
        "start": start.timestamp(),
        "end": end.timestamp(),
        "labels": [(start + timedelta(days=i)).date().isoformat() for i in range(days)],
        "series": series,
        "totals": totals,
        "previous": previous,
        "unreadable": unreadable,
    }


# ---------------------------------------------------------------------------
# Agents: when each one last worked
# ---------------------------------------------------------------------------


def agents_section(agents: list[Agent]) -> dict[str, Any]:
    """Last conversation activity per agent; live state comes from chat runs."""
    result: list[dict[str, Any]] = []
    unreadable: list[str] = []
    for agent in agents:
        last: Optional[float] = None
        db = agent.home / "state.db"
        if db.is_file():
            try:
                with closing(_ro(db)) as conn:
                    cols = _columns(conn, "sessions")
                    if cols:
                        active = (
                            "COALESCE(last_activity_at, started_at)"
                            if "last_activity_at" in cols
                            else "started_at"
                        )
                        rows = conn.execute(
                            f"SELECT {active} FROM sessions "
                            "WHERE COALESCE(source, '') != 'maintenance' "
                            "ORDER BY started_at DESC LIMIT 20"
                        ).fetchall()
                        stamps = [float(row[0]) for row in rows if row[0] is not None]
                        last = max(stamps) if stamps else None
            except sqlite3.Error:
                unreadable.append(agent.label)
        result.append({"profile": agent.profile, "label": agent.label, "last_active_at": last})
    return {"status": "error" if unreadable and len(unreadable) == len(agents) else "ok",
            "agents": result, "unreadable": unreadable}


# ---------------------------------------------------------------------------
# Upcoming: «Ближайшие задачи» — the day by hours
# ---------------------------------------------------------------------------

#: A planned run this far in the past means the scheduler has not fired it.
_LATE_AFTER_SECONDS = 300
#: Occurrences computed per job inside the seven-day horizon.
_OCCURRENCE_CAP = 400
#: A job firing more often than this per day shows as one series row.
_SERIES_THRESHOLD = 4
_EVENTS_LIMIT = 40


def _read_jobs(path: Path) -> list[dict[str, Any]]:
    """Parse ``jobs.json`` without the scheduler's auto-repair writes."""
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8-sig")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(raw, strict=False)
    if isinstance(data, dict):
        jobs = data.get("jobs", [])
        if isinstance(jobs, dict):
            jobs = [dict(value, id=value.get("id") or key) for key, value in jobs.items()
                    if isinstance(value, dict)]
    elif isinstance(data, list):
        jobs = data
    else:
        raise ValueError("jobs.json has an unknown shape")
    from cron.jobs import _normalize_job_record

    return [_normalize_job_record(job) for job in jobs if isinstance(job, dict)]


def _job_runnable(job: dict[str, Any]) -> bool:
    from cron.jobs import is_job_runnable

    return is_job_runnable(job) and job.get("state") not in {"completed", "error"}


def _occurrences(
    job: dict[str, Any], *, now: datetime, until: datetime, tz: Optional[tzinfo]
) -> list[datetime]:
    """Planned starts of ``job`` from its next run up to ``until``.

    The scheduler's own ``next_run_at`` is the first one; later ones follow
    the schedule. An overdue ``next_run_at`` is kept once (it is a fact the
    owner should see), and the series resumes from now instead of replaying
    every missed slot.
    """
    schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
    kind = schedule.get("kind")
    first = _parse_iso(job.get("next_run_at"), tz)
    if kind == "once":
        at = first or _parse_iso(schedule.get("run_at"), tz)
        return [at] if at is not None and at < until else []
    if first is None or first >= until:
        return []
    if tz is not None:
        first = first.astimezone(tz)
    out = [first]
    cursor = max(first, now)
    if kind == "interval":
        try:
            minutes = float(schedule.get("minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0.0
        if minutes <= 0:
            return out
        step = timedelta(minutes=minutes)
        if first < now:
            missed = int((now - first) / step) + 1
            cursor = first + missed * step
        else:
            cursor = first + step
        while cursor < until and len(out) < _OCCURRENCE_CAP:
            out.append(cursor)
            cursor += step
        return out
    if kind == "cron":
        expr = schedule.get("expr")
        if not expr:
            return out
        try:
            from croniter import croniter

            itr = croniter(str(expr), cursor)
            while len(out) < _OCCURRENCE_CAP:
                nxt = itr.get_next(datetime)
                if nxt >= until:
                    break
                if nxt > first:
                    out.append(nxt)
        except Exception:
            logger.debug("dashboard upcoming: cron expression %r not expanded", expr, exc_info=True)
        return out
    return out


def _executions_since(path: Path, since: datetime, tz: Optional[tzinfo]) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with closing(_ro(path)) as conn:
        if not _columns(conn, "executions"):
            return []
        # claimed_at is ISO text in whatever offset the scheduler used; take
        # a day of slack in the text comparison, then filter exactly.
        loose = (since - timedelta(days=1)).isoformat()
        rows = conn.execute(
            "SELECT job_id, status, claimed_at, started_at, finished_at, error "
            "FROM executions WHERE claimed_at >= ? ORDER BY claimed_at DESC LIMIT 300",
            (loose,),
        ).fetchall()
    result = []
    for row in rows:
        at = _parse_iso(row["started_at"], tz) or _parse_iso(row["claimed_at"], tz)
        if at is None or at < since:
            continue
        result.append({
            "job_id": row["job_id"],
            "status": row["status"],
            "at": at,
            "finished_at": _parse_iso(row["finished_at"], tz),
        })
    return result


_EXECUTION_STATE = {
    "completed": "done",
    "failed": "failed",
    "running": "running",
    "claimed": "running",
    "unknown": "unknown",
}


def upcoming_section(
    agents: list[Agent], *, now: float, tz: Optional[tzinfo]
) -> dict[str, Any]:
    now_dt = _local(now, tz)
    day_start = _midnight(now, tz)
    day_end = day_start + timedelta(days=1)
    horizon = day_start + timedelta(days=7)
    week = [
        {"date": (day_start + timedelta(days=i)).date().isoformat(), "planned": 0, "done": 0, "failed": 0}
        for i in range(7)
    ]
    events: list[dict[str, Any]] = []
    next_later: Optional[dict[str, Any]] = None
    unreadable: list[str] = []
    jobs_total = 0
    jobs_active = 0

    for agent in agents:
        try:
            jobs = _read_jobs(agent.home / "cron" / "jobs.json")
        except (OSError, ValueError):
            logger.warning("dashboard upcoming: jobs of %s unreadable", agent.name, exc_info=True)
            unreadable.append(agent.label)
            continue
        by_id = {str(job.get("id")): job for job in jobs}
        jobs_total += len(jobs)

        for job in jobs:
            if not _job_runnable(job):
                continue
            jobs_active += 1
            planned = _occurrences(job, now=now_dt, until=horizon, tz=tz)
            today_planned = []
            for at in planned:
                offset = (at.astimezone(day_start.tzinfo) - day_start).days if at >= day_start else 0
                if 0 <= offset < 7:
                    week[offset]["planned"] += 1
                if at < day_end:
                    today_planned.append(at)
                elif next_later is None or at.timestamp() < next_later["at"]:
                    next_later = _event(agent, job, at, "planned")
            if today_planned:
                shown = today_planned[0]
                state = "late" if shown.timestamp() < now - _LATE_AFTER_SECONDS else "planned"
                event = _event(agent, job, shown, state)
                if len(today_planned) > _SERIES_THRESHOLD:
                    event["repeats_today"] = len(today_planned)
                events.append(event)

        try:
            runs = _executions_since(agent.home / "cron" / "executions.db", day_start, tz)
        except sqlite3.Error:
            logger.warning("dashboard upcoming: executions of %s unreadable", agent.name, exc_info=True)
            unreadable.append(agent.label)
            runs = []
        per_job: dict[str, list[dict[str, Any]]] = {}
        for run in runs:
            per_job.setdefault(str(run["job_id"]), []).append(run)
        for job_id, job_runs in per_job.items():
            job = by_id.get(job_id) or {"id": job_id, "name": "Удалённая задача"}
            failed = sum(1 for run in job_runs if run["status"] == "failed")
            done = sum(1 for run in job_runs if run["status"] == "completed")
            week[0]["done"] += done
            week[0]["failed"] += failed
            latest = max(job_runs, key=lambda run: run["at"])
            event = _event(agent, job, latest["at"], _EXECUTION_STATE.get(str(latest["status"]), "unknown"))
            if len(job_runs) > 1:
                event["runs_today"] = len(job_runs)
                event["failed_today"] = failed
            events.append(event)

    events.sort(key=lambda event: (event["at"], event["title"]))
    if jobs_total == 0 and not events:
        status = "error" if unreadable else "empty"
    else:
        status = "ok"
    return {
        "status": status,
        "now": now,
        "day_start": day_start.timestamp(),
        "day_end": day_end.timestamp(),
        "events": events[:_EVENTS_LIMIT],
        "next_later": next_later,
        "week": week,
        "jobs_total": jobs_total,
        "jobs_active": jobs_active,
        "unreadable": unreadable,
    }


def _event(agent: Agent, job: dict[str, Any], at: datetime, state: str) -> dict[str, Any]:
    return {
        "id": f"{agent.profile or 'default'}:{job.get('id')}:{int(at.timestamp())}:{state}",
        "job_id": str(job.get("id") or ""),
        "title": _trim(job.get("name") or "Задача", 80),
        "schedule": _trim(job.get("schedule_display") or "", 60) or None,
        "profile": agent.profile,
        "agent": agent.label,
        "at": at.timestamp(),
        "state": state,
        "href": "/cron",
    }


# ---------------------------------------------------------------------------
# Artifacts: finished files (K21-146 layout aware)
# ---------------------------------------------------------------------------

_KIND_BY_EXT = {
    **dict.fromkeys(("doc", "docx", "odt", "rtf", "pages"), "document"),
    **dict.fromkeys(("md", "markdown", "txt"), "text"),
    "pdf": "pdf",
    **dict.fromkeys(("xls", "xlsx", "ods", "csv", "tsv", "numbers"), "table"),
    **dict.fromkeys(("ppt", "pptx", "odp", "key"), "presentation"),
    **dict.fromkeys(("png", "jpg", "jpeg", "webp", "gif", "bmp", "heic", "avif", "svg"), "image"),
    **dict.fromkeys(("mp3", "wav", "ogg", "m4a", "flac", "opus"), "audio"),
    **dict.fromkeys(("mp4", "mov", "webm", "mkv", "avi"), "video"),
    **dict.fromkeys(("zip", "rar", "7z", "tar", "gz", "tgz"), "archive"),
    **dict.fromkeys(("html", "htm"), "page"),
}

#: Directory names never worth walking for results.
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".cache", ".next", ".turbo", ".venv", "venv",
    "__pycache__", "node_modules", "build", "dist", "target", ".trash",
    ".uploads", ".index",
})
#: Workspace roots the legacy walk leaves to their own sections.
_RESERVED_ROOT_DIRS = frozenset({"agents", "shared", "client"})
_SENSITIVE_NAMES = frozenset({"auth.json", "credentials.json", ".netrc", "id_rsa", "id_ed25519"})
_TEMP_SUFFIXES = (".tmp", ".part", ".crdownload", ".swp", ".lock")

_SCAN_BUDGET = 4000
_ARTIFACT_LIMIT = 12
_EXCERPT_LIMIT = 6
_RECENT_WINDOW_SECONDS = 7 * 86400
_REGISTRY_KEY = re.compile(r"^[0-9a-f]{16}$")


def _artifact_candidate(name: str) -> bool:
    lowered = name.lower()
    if not name or name.startswith(".") or name.startswith("~$"):
        return False
    if lowered in _SENSITIVE_NAMES or lowered.endswith((".env", ".pem", ".key")):
        return False
    return not lowered.endswith(_TEMP_SUFFIXES)


class _Workspace:
    """Read-only view of the workspace that can never step outside it.

    The same containment rule as the Files screen (``_resolve_managed_path``):
    the workspace root is resolved once — it may itself be a link, as the
    Files root may — and nothing below it may be a symbolic link. Here the
    rule is enforced at open time, not by a check before it: every component
    is opened relative to its already-open parent with ``O_NOFOLLOW``, so a
    results folder, ``shared``, an intermediate directory or a file replaced
    by a link is refused, including a link swapped in after the walk saw a
    real directory (R8, 0.21.13 review).

    Hosts without ``dir_fd`` support (native Windows) fall back to a lexical
    check of every component plus a resolved-path check, the Files policy.
    """

    _DIR_FLAGS = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                  | getattr(os, "O_CLOEXEC", 0))
    _FILE_FLAGS = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                   | getattr(os, "O_CLOEXEC", 0))
    _FD_SAFE = (
        os.open in os.supports_dir_fd
        and os.scandir in os.supports_fd
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
    )

    def __init__(self, root: Path):
        self.base = root.resolve(strict=True)
        if not self.base.is_dir():
            raise NotADirectoryError(str(self.base))
        self._root_fd: Optional[int] = None
        if self._FD_SAFE:
            self._root_fd = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))

    def close(self) -> None:
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None

    def __enter__(self) -> "_Workspace":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    @staticmethod
    def _check_parts(parts: tuple[str, ...]) -> None:
        for name in parts:
            if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
                raise OSError(errno.EINVAL, "invalid path component", name)

    def path(self, parts: tuple[str, ...]) -> str:
        return str(self.base.joinpath(*parts))

    def _lexical(self, parts: tuple[str, ...]) -> Path:
        """Fallback: no component is a link and the real path stays inside."""
        current = self.base
        for name in parts:
            current = current / name
            if stat.S_ISLNK(os.lstat(current).st_mode):
                raise OSError(errno.ELOOP, "symbolic link inside the workspace", str(current))
        real = Path(os.path.realpath(current))
        if real != self.base and self.base not in real.parents:
            raise OSError(errno.EXDEV, "path leaves the workspace", str(current))
        return current

    def open_dir(self, parts: tuple[str, ...]) -> Any:
        """A directory handle for ``scandir``: an fd, or a checked path."""
        self._check_parts(parts)
        if self._root_fd is None:
            return str(self._lexical(parts))
        fd = os.dup(self._root_fd)
        try:
            for name in parts:
                child = os.open(name, self._DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
        except BaseException:
            os.close(fd)
            raise
        return fd

    def read_head(self, parts: tuple[str, ...], size: int = 4096) -> Optional[bytes]:
        """First bytes of a regular file inside the workspace, else ``None``."""
        if not parts:
            return None
        try:
            if self._root_fd is None:
                path = self._lexical(parts)
                with open(path, "rb") as handle:
                    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                        return None
                    return handle.read(size)
            parent = self.open_dir(parts[:-1])
            try:
                fd = os.open(parts[-1], self._FILE_FLAGS, dir_fd=parent)
            finally:
                os.close(parent)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    return None
                return os.read(fd, size)
            finally:
                os.close(fd)
        except OSError:
            return None


def _scandir(handle: Any) -> Any:
    return os.scandir(handle)


def _close_handle(handle: Any) -> None:
    if isinstance(handle, int):
        os.close(handle)


class _Walk:
    """Newest files under a few workspace roots, within one shared entry budget."""

    def __init__(self, workspace: _Workspace, budget: int, keep: int, recent_since: float):
        self.workspace = workspace
        self.budget = budget
        self.keep = keep
        self.recent_since = recent_since
        self.truncated = False
        self.recent = 0
        self._heap: list[tuple[float, tuple[str, ...], dict[str, Any]]] = []

    def walk(
        self,
        parts: tuple[str, ...],
        *,
        depth: int,
        meta: dict[str, Any],
        skip_top: frozenset = frozenset(),
    ) -> None:
        stack = [(parts, 0)]
        while stack:
            current, level = stack.pop()
            try:
                handle = self.workspace.open_dir(current)
            except OSError:
                # A missing folder is normal; a linked one is refused here.
                continue
            try:
                with _scandir(handle) as entries:
                    for entry in entries:
                        if self.budget <= 0:
                            self.truncated = True
                            return
                        self.budget -= 1
                        name = entry.name
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if level + 1 < depth and name not in _SKIP_DIRS and not name.startswith(".") \
                                        and not (level == 0 and name in skip_top):
                                    stack.append((current + (name,), level + 1))
                                continue
                            if not entry.is_file(follow_symlinks=False) or not _artifact_candidate(name):
                                continue
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if info.st_mtime >= self.recent_since:
                            self.recent += 1
                        item = (info.st_mtime, current + (name,), {**meta, "size": info.st_size})
                        if len(self._heap) < self.keep:
                            heapq.heappush(self._heap, item)
                        elif item[0] > self._heap[0][0]:
                            heapq.heapreplace(self._heap, item)
            except OSError:
                continue
            finally:
                _close_handle(handle)

    def newest(self) -> list[tuple[float, tuple[str, ...], dict[str, Any]]]:
        return sorted(self._heap, key=lambda item: item[0], reverse=True)


def _registry(workspace: _Workspace) -> Optional[dict[str, Any]]:
    """The K21-146 folder registry, read without creating or following anything."""
    raw = workspace.read_head((".index", "file-organization-v1.json"), size=1024 * 1024)
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        return None
    profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
    archived = data.get("archived") if isinstance(data.get("archived"), dict) else {}
    return {"profiles": profiles, "archived": archived}


def _excerpt(head: Optional[bytes]) -> tuple[Optional[str], list[str]]:
    """Heading and first lines of a small text file's first bytes."""
    if not head:
        return None, []
    text = head.decode("utf-8", errors="replace")
    title: Optional[str] = None
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("```", "---", "|")):
            continue
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            if heading and title is None:
                title = _trim(heading, 90)
                continue
        cleaned = re.sub(r"^[-*+>]\s+|^\d+[.)]\s+", "", line)
        cleaned = re.sub(r"[*_`]", "", cleaned).strip()
        if cleaned:
            lines.append(_trim(cleaned, 120))
        if len(lines) >= 3:
            break
    return title, lines


def artifacts_section(
    agents: list[Agent], *, root: Path, now: float, limit: int = _ARTIFACT_LIMIT
) -> dict[str, Any]:
    def _empty(status: str) -> dict[str, Any]:
        return {"status": status, "items": [], "recent_count": 0, "organized": False,
                "root": str(root), "truncated": False}

    try:
        workspace = _Workspace(root)
    except (FileNotFoundError, NotADirectoryError):
        return _empty("empty")
    except OSError:
        return _empty("error")

    with workspace:
        try:
            _close_handle(workspace.open_dir(()))
        except OSError:
            return _empty("error")
        registry = _registry(workspace)
        walk = _Walk(workspace, _SCAN_BUDGET, limit, now - _RECENT_WINDOW_SECONDS)
        if registry is not None:
            owners: dict[str, tuple[str, str]] = {}
            for name, key in registry["profiles"].items():
                if isinstance(key, str):
                    owners[key] = (_wire(str(name)), _label_for(agents, str(name)) or str(name))
            for key, name in registry["archived"].items():
                if isinstance(key, str) and key not in owners:
                    owners[key] = (_wire(str(name)), f"{name} (архив)")
            keys: list[str] = []
            try:
                handle = workspace.open_dir(("agents",))
            except OSError:
                handle = None
            if handle is not None:
                try:
                    with _scandir(handle) as entries:
                        keys = sorted(
                            entry.name for entry in entries
                            if _REGISTRY_KEY.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False)
                        )
                except OSError:
                    keys = []
                finally:
                    _close_handle(handle)
            for key in keys:
                profile, label = owners.get(key, ("", None))
                walk.walk(("agents", key, "results"), depth=4,
                          meta={"section": "agent", "profile": profile, "agent": label})
            walk.walk(("shared",), depth=3, meta={"section": "shared", "profile": None, "agent": None})
        walk.walk((), depth=2, meta={"section": "workspace", "profile": None, "agent": None},
                  skip_top=_RESERVED_ROOT_DIRS if registry is not None else frozenset({"client"}))

        items: list[dict[str, Any]] = []
        for index, (mtime, parts, meta) in enumerate(walk.newest()):
            name = parts[-1]
            ext = Path(name).suffix.lower().lstrip(".")
            kind = _KIND_BY_EXT.get(ext, "file")
            item = {
                "path": workspace.path(parts),
                "name": name,
                "folder": workspace.path(parts[:-1]),
                "ext": ext,
                "kind": kind,
                "size": meta["size"],
                "modified_at": mtime,
                "section": meta["section"],
                "profile": meta["profile"],
                "agent": meta["agent"],
            }
            if kind == "text" and index < _EXCERPT_LIMIT and meta["size"] > 0:
                title, lines = _excerpt(workspace.read_head(parts))
                item["title"] = title
                item["excerpt"] = lines
            items.append(item)
        return {
            "status": "ok" if items else "empty",
            "items": items,
            "recent_count": walk.recent,
            "organized": registry is not None,
            "root": str(workspace.base),
            "truncated": walk.truncated,
        }


# ---------------------------------------------------------------------------
# Codex quota
# ---------------------------------------------------------------------------

QUOTA_WARN_PERCENT = 80.0
QUOTA_CRITICAL_PERCENT = 95.0
_QUOTA_STALE_SECONDS = 24 * 3600


def _codex_configured(agents: list[Agent]) -> bool:
    """Is a ChatGPT/Codex subscription connected anywhere in the installation?"""
    from korra_cli.profiles import _read_config_model

    for agent in agents:
        try:
            _model, provider = _read_config_model(agent.home)
        except Exception:
            provider = None
        if provider == "openai-codex":
            return True
        auth = agent.home / "auth.json"
        if not auth.is_file():
            continue
        try:
            store = json.loads(auth.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(store, dict):
            continue
        providers = store.get("providers") if isinstance(store.get("providers"), dict) else {}
        pool = store.get("credential_pool") if isinstance(store.get("credential_pool"), dict) else {}
        if providers.get("openai-codex") or pool.get("openai-codex"):
            return True
    return False


def _window_label(minutes: Optional[int]) -> str:
    if not minutes:
        return "окно"
    if minutes % 10080 == 0:
        weeks = minutes // 10080
        return "неделя" if weeks == 1 else f"{weeks} нед."
    if minutes % 1440 == 0:
        return "сутки" if minutes == 1440 else f"{minutes // 1440} дн."
    if minutes % 60 == 0:
        return f"{minutes // 60} ч"
    return f"{minutes} мин"


def quota_section(
    agents: list[Agent], *, now: float, root: Optional[Path] = None
) -> dict[str, Any]:
    from agent.rate_limit_tracker import load_codex_quota

    data = load_codex_quota(root=root)
    # Whether a subscription is connected changes rarely; the percent itself
    # is re-read every time, the per-profile config/auth scan once a minute.
    configured = _cached(
        ("codex-configured",) + tuple(str(agent.home) for agent in agents),
        60.0,
        lambda: _codex_configured(agents),
    )
    if data is None:
        if not configured:
            return {"available": False, "status": "absent"}
        return {"available": True, "status": "waiting"}

    windows = []
    for key in ("primary", "secondary"):
        raw = data.get(key)
        if not isinstance(raw, dict):
            continue
        try:
            used = float(raw.get("used_percent"))
        except (TypeError, ValueError):
            continue
        minutes = raw.get("window_minutes") if isinstance(raw.get("window_minutes"), int) else None
        resets_at = raw.get("resets_at") if isinstance(raw.get("resets_at"), (int, float)) else None
        windows.append({
            "key": key,
            "used_percent": used,
            "window_minutes": minutes,
            "label": _window_label(minutes),
            "resets_at": resets_at,
            # The window already rolled over: the stored percent is history.
            "expired": bool(resets_at and resets_at <= now),
        })
    live = [window for window in windows if not window["expired"]]
    captured_at = float(data.get("captured_at") or 0)
    base = {
        "available": True,
        "windows": windows,
        "plan_type": data.get("plan_type"),
        "captured_at": captured_at,
        "stale": now - captured_at > _QUOTA_STALE_SECONDS,
    }
    if not live:
        return {**base, "status": "reset", "level": "normal"}
    headline = max(live, key=lambda window: window["used_percent"])
    used = headline["used_percent"]
    level = (
        "critical" if used >= QUOTA_CRITICAL_PERCENT
        else "warn" if used >= QUOTA_WARN_PERCENT
        else "normal"
    )
    return {
        **base,
        "status": "ok",
        "level": level,
        "used_percent": used,
        "window_minutes": headline["window_minutes"],
        "window_label": headline["label"],
        "resets_at": headline["resets_at"],
    }


# ---------------------------------------------------------------------------
# Attention: «Требует внимания»
# ---------------------------------------------------------------------------

_SEVERITY_RANK = {"action": 0, "problem": 1, "info": 2}

_KANBAN_KINDS = {
    "question": ("action", "Исполнитель ждёт ответа", "Ответить"),
    "accept": ("action", "Результат ждёт вашей приёмки", "Принять"),
    "human_step": ("action", "Ваш шаг готов к выполнению", "Открыть"),
    "problem": ("problem", "Задача остановилась после сбоев", "Разобраться"),
}

_FAILURE_TEXT = {
    "auth": "Нет доступа к модели или сервису.",
    "rate_limit": "Упёрлась в лимит модели.",
    "timeout": "Не уложилась по времени.",
    "delivery": "Результат не удалось доставить.",
    "config": "В настройке задачи ошибка.",
    "script": "Скрипт задачи завершился ошибкой.",
}

SOURCE_LABELS = {
    "kanban": "канбан",
    "delivery": "доставку ответов",
    "cron": "расписание",
    "provider": "подключение модели",
    "updates": "обновление",
    "quota": "квоту Codex",
}


def _kanban_items(agents: list[Agent]) -> Optional[tuple[list[dict[str, Any]], list[str]]]:
    """Owner attention from every live board, via the kanban plugin itself.

    The plugin's ``/attention`` handler is the one source the board header,
    the navigation badge and this card share. A disabled or unmounted plugin
    is not a failure — the board simply is not part of this installation.
    """
    module = sys.modules.get("hermes_dashboard_plugin_kanban")
    handler = getattr(module, "get_attention", None) if module is not None else None
    if not callable(handler):
        return None
    try:
        from korra_cli.plugins_cmd import _get_disabled_set

        if "kanban" in _get_disabled_set():
            return None
    except Exception:
        pass
    from korra_cli import kanban_db

    # Reading a board that was never opened would create its database; an
    # installation without boards simply has nothing waiting there.
    if not kanban_db.kanban_db_path(kanban_db.DEFAULT_BOARD).exists() and not any(
        Path(kanban_db.boards_root()).glob("*/kanban.db")
    ):
        return [], []
    payload = handler(limit=50)
    items = []
    for raw in payload.get("items", []):
        kind = raw.get("kind")
        if kind not in _KANBAN_KINDS:
            continue
        severity, detail, action = _KANBAN_KINDS[kind]
        question = raw.get("question")
        if kind == "question" and question:
            detail = f"Вопрос: {_trim(question, 140)}"
        board = raw.get("board") or ""
        task = raw.get("task_id") or ""
        from urllib.parse import urlencode

        items.append({
            "id": f"kanban:{board}:{task}:{kind}",
            "source": "kanban",
            "kind": f"kanban_{kind}",
            "severity": severity,
            "title": _trim(raw.get("title") or "Задача на доске", 90),
            "detail": detail,
            "agent": _label_for(agents, raw.get("assignee")) if raw.get("assignee") else None,
            "profile": _wire(str(raw.get("assignee"))) if raw.get("assignee") else None,
            "href": "/kanban?" + urlencode({"board": board, "task": task}),
            "action": action,
            "at": raw.get("created_at"),
        })
    errors = [str(error.get("board") or "") for error in payload.get("errors", []) if isinstance(error, dict)]
    return items, errors


def _delivery_items(agents: list[Agent], *, now: float) -> list[dict[str, Any]]:
    since = now - 3 * 86400
    stuck_before = now - 15 * 60
    items: list[dict[str, Any]] = []
    for agent in agents:
        db = agent.home / "state.db"
        if not db.is_file():
            continue
        with closing(_ro(db)) as conn:
            if not _columns(conn, "delivery_obligations"):
                continue
            rows = conn.execute(
                "SELECT obligation_id, session_key, platform, state, updated_at, "
                "substr(content, 1, 240) AS preview FROM delivery_obligations "
                "WHERE updated_at >= ? AND (state IN ('failed', 'abandoned') "
                "OR (state IN ('pending', 'attempting') AND updated_at < ?)) "
                "ORDER BY updated_at DESC LIMIT 10",
                (since, stuck_before),
            ).fetchall()
            has_key = "session_key" in _columns(conn, "sessions")
            for row in rows:
                session_id = None
                if has_key and row["session_key"]:
                    found = conn.execute(
                        "SELECT id FROM sessions WHERE session_key = ? "
                        "ORDER BY started_at DESC LIMIT 1",
                        (row["session_key"],),
                    ).fetchone()
                    session_id = found["id"] if found else None
                platform = _PLATFORM_LABELS.get(str(row["platform"] or "").lower(), str(row["platform"] or "мессенджер"))
                preview = _trim(row["preview"], 110)
                items.append({
                    "id": f"delivery:{agent.profile or 'default'}:{row['obligation_id']}",
                    "source": "delivery",
                    "kind": "delivery",
                    "severity": "problem",
                    "title": f"Ответ не дошёл до {platform}",
                    "detail": f"«{preview}»" if preview else "Текст ответа сохранён в чате агента.",
                    "agent": agent.label,
                    "profile": agent.profile,
                    "href": agent_href(agent.profile, session_id),
                    "action": "Открыть чат",
                    "at": float(row["updated_at"] or 0),
                })
    return items


def _cron_items(agents: list[Agent], *, now: float, tz: Optional[tzinfo]) -> list[dict[str, Any]]:
    since = now - 7 * 86400
    items: list[dict[str, Any]] = []
    for agent in agents:
        db = agent.home / "cron" / "executions.db"
        if not db.is_file():
            continue
        with closing(_ro(db)) as conn:
            if not _columns(conn, "cron_incidents"):
                continue
            loose = _local(since - 86400, tz).isoformat()
            rows = conn.execute(
                "SELECT id, job_id, failure_type, last_seen_at, error FROM cron_incidents "
                "WHERE state != 'closed' AND last_seen_at >= ? "
                "ORDER BY last_seen_at DESC LIMIT 20",
                (loose,),
            ).fetchall()
        if not rows:
            continue
        try:
            jobs = {str(job.get("id")): job for job in _read_jobs(agent.home / "cron" / "jobs.json")}
        except (OSError, ValueError):
            jobs = {}
        for row in rows:
            seen = _parse_iso(row["last_seen_at"], tz)
            if seen is None or seen.timestamp() < since:
                continue
            job = jobs.get(str(row["job_id"]))
            # A deleted or paused job's old failure asks nothing of the owner.
            if job is None or not _job_runnable(job):
                continue
            detail = _FAILURE_TEXT.get(str(row["failure_type"] or ""), "") or _trim(row["error"], 120)
            items.append({
                "id": f"cron:{agent.profile or 'default'}:{row['id']}",
                "source": "cron",
                "kind": "cron_incident",
                "severity": "problem",
                "title": f"Задача «{_trim(job.get('name'), 60)}» не выполнилась",
                "detail": detail or "Запуск завершился ошибкой.",
                "agent": agent.label,
                "profile": agent.profile,
                "href": "/cron",
                "action": "Открыть задачи",
                "at": seen.timestamp(),
            })
    return items


def _provider_items(agents: list[Agent]) -> list[dict[str, Any]]:
    """A subscription or key the provider permanently refused.

    Only when *every* credential of that provider is refused: one revoked
    spare next to a working key is housekeeping, not something that stops
    the agents.
    """
    seen_files: set[str] = set()
    status: dict[str, dict[str, Any]] = {}
    for agent in agents:
        path = agent.home / "auth.json"
        try:
            key = str(path.resolve())
        except OSError:
            continue
        if key in seen_files or not path.is_file():
            continue
        seen_files.add(key)
        store = json.loads(path.read_text(encoding="utf-8"))
        pool = store.get("credential_pool") if isinstance(store, dict) else None
        if not isinstance(pool, dict):
            continue
        for provider, entries in pool.items():
            if not isinstance(entries, list):
                continue
            bucket = status.setdefault(str(provider), {"dead": 0, "usable": 0, "at": 0.0, "reason": ""})
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                state = entry.get("last_status")
                reason = str(entry.get("last_error_reason") or "").lower()
                if state == "dead" or (
                    state == "exhausted" and entry.get("last_error_code") == 401
                    and reason in _TERMINAL_AUTH_REASONS
                ):
                    bucket["dead"] += 1
                    bucket["reason"] = reason or bucket["reason"]
                    try:
                        bucket["at"] = max(bucket["at"], float(entry.get("last_status_at") or 0))
                    except (TypeError, ValueError):
                        pass
                elif state in (None, "ok"):
                    bucket["usable"] += 1
    items = []
    for provider, bucket in sorted(status.items()):
        if not bucket["dead"] or bucket["usable"]:
            continue
        label = _PROVIDER_LABELS.get(provider, provider)
        items.append({
            "id": f"provider:{provider}",
            "source": "provider",
            "kind": "provider_refused",
            "severity": "problem",
            "title": f"{label}: доступ отозван",
            "detail": "Провайдер больше не принимает вход. Подключите его заново, чтобы агенты продолжили отвечать.",
            "agent": None,
            "profile": None,
            "href": "/models",
            "action": "Подключить",
            "at": bucket["at"] or None,
        })
    return items


def _update_items(*, now: float) -> list[dict[str, Any]]:
    from korra_cli.web_routers import updates as updates_mod

    installed = updates_mod._installed()
    progress = updates_mod._progress(installed)
    if not progress or not progress.get("final"):
        return []
    status = progress.get("status")
    if status not in {"failed", "rolled_back", "rollback_failed"}:
        return []
    updated_at = progress.get("updated_at") or 0
    if now - float(updated_at) > 3 * 86400:
        return []
    evidence = " ".join(str(progress.get(key) or "") for key in ("phase", "error", "message")).lower()
    if "model_smoke" in evidence or "smoke" in str(progress.get("phase") or ""):
        detail = "Проверка модели после обновления не прошла. Прежняя версия продолжает работать."
    else:
        detail = _trim(progress.get("message"), 140) or "Прежняя версия продолжает работать."
    return [{
        "id": f"updates:{progress.get('release_id') or status}",
        "source": "updates",
        "kind": "update_failed",
        "severity": "problem" if status == "rollback_failed" else "info",
        "title": "Обновление не установилось" if status != "rollback_failed"
        else "Возврат прежней версии не завершился",
        "detail": detail,
        "agent": None,
        "profile": None,
        "href": "/updates",
        "action": "Подробнее",
        "at": float(updated_at) or None,
    }]


def attention_section(
    agents: list[Agent], *, now: float, tz: Optional[tzinfo], quota: dict[str, Any]
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    checked: list[str] = []

    def _collect(source: str, produce: Callable[[], Any]) -> None:
        try:
            produced = produce()
        except Exception:
            logger.warning("dashboard attention: %s unreadable", source, exc_info=True)
            errors.append({"source": source, "label": SOURCE_LABELS[source]})
            return
        if produced is None:
            return
        checked.append(source)
        items.extend(produced)

    def _kanban() -> Optional[list[dict[str, Any]]]:
        result = _kanban_items(agents)
        if result is None:
            return None
        found, broken = result
        for board in broken:
            errors.append({"source": "kanban", "label": f"доску «{board}»" if board else SOURCE_LABELS["kanban"]})
        return found

    _collect("kanban", _kanban)
    _collect("delivery", lambda: _delivery_items(agents, now=now))
    _collect("cron", lambda: _cron_items(agents, now=now, tz=tz))
    _collect("provider", lambda: _provider_items(agents))
    _collect("updates", lambda: _update_items(now=now))

    if quota.get("available"):
        checked.append("quota")
        if quota.get("status") == "ok" and quota.get("level") == "critical":
            resets_at = quota.get("resets_at")
            when = f"Лимит обновится {_human_moment(resets_at, now, tz)}." if resets_at else ""
            items.append({
                "id": "quota:codex",
                "source": "quota",
                "kind": "quota_critical",
                "severity": "problem",
                "title": f"Квота Codex почти исчерпана: {round(quota['used_percent'])} %",
                "detail": (when + " Агенты могут перестать отвечать до сброса.").strip(),
                "agent": None,
                "profile": None,
                "href": "/dashboard#codex-quota",
                "action": "Подробнее",
                "at": quota.get("captured_at"),
            })

    items.sort(key=lambda item: (_SEVERITY_RANK.get(item["severity"], 9), -(item.get("at") or 0)))
    return {
        "status": "ok" if checked or not errors else "error",
        "items": items[:20],
        "count": len(items),
        "errors": errors,
        "checked": checked,
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _section(name: str, compute: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return compute()
    except Exception:
        logger.exception("dashboard: section %s failed", name)
        return {"status": "error"}


def build_state(
    *,
    period: str = DEFAULT_PERIOD,
    now: Optional[float] = None,
    agents: Optional[list[Agent]] = None,
    workspace: Optional[Path] = None,
    quota_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Everything the dashboard shows, section by section.

    A failing section is reported as ``{"status": "error"}`` and never takes
    the other cards down with it.
    """
    from korra_constants import get_default_hermes_root

    current = time.time() if now is None else float(now)
    period = period if period in PERIOD_DAYS else DEFAULT_PERIOD
    tz = _timezone()
    root = get_default_hermes_root()
    workspace = workspace if workspace is not None else root / "workspace"
    roster = agents if agents is not None else _cached(
        ("agents-list", str(root)), 30.0, list_agents
    )
    scope = (str(root), str(workspace), tuple(agent.home for agent in roster))

    quota = _cached(("quota",) + scope, SECTION_TTL["quota"],
                    lambda: _section("quota", lambda: quota_section(roster, now=current, root=quota_root)))
    return {
        "version": STATE_VERSION,
        "generated_at": current,
        "timezone": _timezone_name(tz),
        "attention": _cached(
            ("attention",) + scope, SECTION_TTL["attention"],
            lambda: _section("attention", lambda: attention_section(roster, now=current, tz=tz, quota=quota)),
        ),
        "agents": _cached(
            ("agents",) + scope, SECTION_TTL["agents"],
            lambda: _section("agents", lambda: agents_section(roster)),
        ),
        "metrics": _cached(
            ("metrics", period) + scope, SECTION_TTL["metrics"],
            lambda: _section("metrics", lambda: metrics_section(roster, period=period, now=current, tz=tz)),
        ),
        "upcoming": _cached(
            ("upcoming",) + scope, SECTION_TTL["upcoming"],
            lambda: _section("upcoming", lambda: upcoming_section(roster, now=current, tz=tz)),
        ),
        "artifacts": _cached(
            ("artifacts",) + scope, SECTION_TTL["artifacts"],
            lambda: _section("artifacts", lambda: artifacts_section(roster, root=workspace, now=current)),
        ),
        "quota": quota,
    }
