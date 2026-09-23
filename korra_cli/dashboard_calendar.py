"""The dashboard «Календарь» feed: meetings, dated tasks and agent runs.

One list for the next seven days of the owner's time, starting at the
beginning of today in the installation's timezone:

* **meetings** come from the Google Calendar the owner connected, through the
  same grant the agents use (:mod:`korra_cli.google_calendar`);
* **tasks** are one-shot scheduled jobs («напомни завтра в 10»);
* **agent runs** are recurring scheduled jobs.

Honesty rules shared with the rest of the dashboard: an unknown value is
never shown as zero.  If Google cannot be read, the feed says why and which
action fixes it; meetings fetched earlier are shown only as "last known" and
only while the grant itself is still valid — a revoked grant hides them at
once.  Scheduled jobs are listed even when Google is not connected.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Callable, Iterable

from korra_cli import google_calendar
from korra_cli import google_workspace as google


logger = logging.getLogger(__name__)

WINDOW_DAYS = 7
# Fresh meetings are reused for this long; a dashboard reopened within two
# minutes does not call Google again.
FRESH_SECONDS = 120
# Last known meetings may be shown during a Google outage for this long,
# always marked as such.
STALE_LIMIT_SECONDS = 30 * 60
# An explicit «Обновить» still cannot hammer Google.
MIN_REFRESH_SECONDS = 15
# A job firing more often than twice a day would flood the week; it becomes
# one line with its schedule instead.
COLLAPSE_AFTER = 14
MAX_OCCURRENCES_PER_JOB = 400


@dataclass
class _CacheEntry:
    fetched_at: float
    token_stamp: tuple[int, int] | None
    payload: dict[str, Any]


_cache: dict[tuple[str, str, str], _CacheEntry] = {}
_cache_lock = threading.Lock()
_fetch_locks: dict[tuple[str, str, str], threading.Lock] = {}


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()
        _fetch_locks.clear()


# ---------------------------------------------------------------------------
# Which profile's Google grant shows on the owner's dashboard
# ---------------------------------------------------------------------------


def pick_source(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Choose the grant the dashboard reads, from the installation overview.

    The main agent comes first: that is the profile the owner connects from
    the dashboard itself.  Otherwise the first profile holding its own grant
    with Calendar wins.  The answer always names the profile, so the card can
    say whose connection it shows.
    """
    app = snapshot.get("app") or {}
    if not app.get("configured"):
        return {"state": "app_unavailable", "profile": None, "action": google_calendar.ACTION_SUPPORT}
    rows = [row for row in snapshot.get("profiles") or [] if isinstance(row, dict)]
    by_name = {row.get("profile"): row for row in rows}

    def usable(row: dict[str, Any] | None) -> bool:
        return bool(
            row
            and "calendar" in (row.get("services") or [])
            and (row.get("state") == "connected" or row.get("legacy_compatible"))
        )

    main = by_name.get("default")
    if usable(main):
        return {"state": "connected", "profile": "default", "action": None}
    for row in rows:
        if row.get("access") == "own" and usable(row):
            return {"state": "connected", "profile": row["profile"], "action": None}

    connected = [row for row in rows if row.get("access") != "none"]
    preferred = main if main and main.get("access") != "none" else (connected[0] if connected else None)
    if preferred is None:
        return {"state": "not_connected", "profile": "default", "action": google_calendar.ACTION_CONNECT}
    # Reconnecting happens where the grant lives: a borrowed grant is fixed
    # at its source, the borrower cannot start its own flow while it borrows.
    owner = preferred.get("shared_from") if preferred.get("access") == "shared" else preferred["profile"]
    if preferred.get("state") == "connected" or preferred.get("legacy_compatible"):
        return {
            "state": "calendar_not_selected",
            "profile": owner,
            "action": google_calendar.ACTION_RECONNECT,
        }
    return {
        "state": "reauthorization_required",
        "profile": owner,
        "action": google_calendar.ACTION_RECONNECT,
    }


def _token_stamp(profile: str) -> tuple[int, int] | None:
    """Identity of the grant file: a reconnect or revoke changes it."""
    try:
        home = google._profile_home_for_name(profile)
        stat = google._active_token_path(home).stat()
        return (stat.st_mtime_ns, stat.st_ino)
    except (OSError, ValueError, google.GoogleWorkspaceError):
        return None


# ---------------------------------------------------------------------------
# Scheduled jobs → dated items
# ---------------------------------------------------------------------------


def _aware(value: Any, zone: tzinfo) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return google_calendar.parse_moment(value, zone)
    except ValueError:
        return None


def _occurrences(job: dict[str, Any], *, start: datetime, end: datetime, zone: tzinfo) -> list[datetime]:
    schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
    kind = schedule.get("kind")
    anchor = _aware(job.get("next_run_at"), zone)
    if kind == "once":
        moment = anchor or _aware(schedule.get("run_at"), zone)
        return [moment] if moment and start <= moment < end else []
    if anchor is None:
        return []
    found: list[datetime] = []
    if kind == "interval":
        try:
            step = timedelta(minutes=int(schedule.get("minutes") or 0))
        except (TypeError, ValueError):
            return []
        if step <= timedelta(0):
            return []
        moment = anchor
        while moment < end and len(found) < MAX_OCCURRENCES_PER_JOB:
            if moment >= start:
                found.append(moment)
            moment += step
        return found
    if kind == "cron":
        expr = schedule.get("expr")
        if not isinstance(expr, str) or not expr.strip():
            return []
        try:
            from croniter import croniter

            iterator = croniter(expr, anchor)
        except Exception:
            return [anchor] if start <= anchor < end else []
        moment = anchor
        while moment < end and len(found) < MAX_OCCURRENCES_PER_JOB:
            if moment >= start:
                found.append(moment)
            try:
                moment = iterator.get_next(datetime)
            except Exception:
                break
        return found
    return [anchor] if start <= anchor < end else []


def _job_items(
    jobs: Iterable[dict[str, Any]],
    *,
    start: datetime,
    end: datetime,
    zone: tzinfo,
) -> list[dict[str, Any]]:
    from cron import jobs as cron_jobs

    items: list[dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        try:
            if not cron_jobs.is_job_runnable(job) or cron_jobs.is_terminal_job(job):
                continue
        except Exception:
            continue
        schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
        one_shot = schedule.get("kind") == "once"
        moments = _occurrences(job, start=start, end=end, zone=zone)
        if not moments:
            continue
        base = {
            "kind": "task" if one_shot else "agent_run",
            "job_id": str(job.get("id") or ""),
            "title": str(job.get("name") or "").strip() or "Задача без названия",
            "profile": str(job.get("profile") or "default"),
            "schedule": str(job.get("schedule_display") or ""),
        }
        if len(moments) > COLLAPSE_AFTER:
            items.append(
                {
                    **base,
                    "id": f"job:{base['job_id']}:{moments[0].isoformat()}",
                    "start": moments[0].isoformat(),
                    "repeats": True,
                    "occurrences": len(moments),
                }
            )
            continue
        for moment in moments:
            items.append(
                {
                    **base,
                    "id": f"job:{base['job_id']}:{moment.isoformat()}",
                    "start": moment.isoformat(),
                }
            )
    return items


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------


def _sort_key(item: dict[str, Any], zone: tzinfo) -> tuple[float, int]:
    value = str(item.get("start") or "")
    try:
        if item.get("all_day"):
            moment = google_calendar.day_start(date.fromisoformat(value[:10]), zone)
            return (moment.timestamp(), 0)
        return (google_calendar.parse_moment(value, zone).timestamp(), 1)
    except ValueError:
        return (float("inf"), 2)


def _google_block(
    source: dict[str, Any],
    *,
    start: datetime,
    end: datetime,
    refresh: bool,
    fetch: Callable[..., dict[str, Any]],
    monotonic: Callable[[], float],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    block: dict[str, Any] = {
        "state": source["state"],
        "source_profile": source.get("profile"),
        "action": source.get("action"),
        "account": "",
        "fetched_at": None,
        "stale": False,
        "error": None,
    }
    if source["state"] != "connected":
        return block, []

    profile = source["profile"]
    key = (profile, start.isoformat(), end.isoformat())
    stamp = _token_stamp(profile)
    now = monotonic()
    with _cache_lock:
        # Yesterday's windows are never read again.
        for old in [item for item in _cache if item[1] != key[1]]:
            _cache.pop(old, None)
        for old in [item for item in _fetch_locks if item[1] != key[1]]:
            _fetch_locks.pop(old, None)
        cached = _cache.get(key)
        if cached is not None and cached.token_stamp != stamp:
            # Reconnected or revoked since: the old meetings belong to a grant
            # that no longer exists and must not be shown.
            _cache.pop(key, None)
            cached = None
        fetch_lock = _fetch_locks.setdefault(key, threading.Lock())

    def serve(entry: _CacheEntry, *, stale: bool, error: dict[str, Any] | None = None):
        block.update(
            account=entry.payload.get("account", ""),
            fetched_at=entry.payload.get("fetched_at"),
            stale=stale,
            error=error,
        )
        if error is not None:
            block["action"] = error.get("action")
        return block, [dict(event, kind="event") for event in entry.payload.get("events", [])]

    if cached is not None:
        age = now - cached.fetched_at
        if age < (MIN_REFRESH_SECONDS if refresh else FRESH_SECONDS):
            return serve(cached, stale=False)

    with fetch_lock:
        with _cache_lock:
            latest = _cache.get(key)
        if latest is not None and latest is not cached and latest.token_stamp == stamp:
            return serve(latest, stale=False)
        try:
            home = google._profile_home_for_name(profile)
            result = fetch(home, start=start, end=end)
        except google_calendar.CalendarError as exc:
            error = exc.as_dict()
            if exc.transient and cached is not None and now - cached.fetched_at < STALE_LIMIT_SECONDS:
                return serve(cached, stale=True, error=error)
            with _cache_lock:
                _cache.pop(key, None)
            state = exc.code if exc.code in {
                "not_connected",
                "calendar_not_selected",
                "reauthorization_required",
                "app_unavailable",
            } else "error"
            block.update(state=state, error=error, action=exc.action)
            return block, []
        except Exception as exc:  # noqa: BLE001 — the card must answer, not 500
            logger.warning("Calendar feed failed for profile %s: %s", profile, type(exc).__name__)
            error = {
                "code": "google_unavailable",
                "message": "Google Calendar could not be read.",
                "action": google_calendar.ACTION_RETRY,
            }
            if cached is not None and now - cached.fetched_at < STALE_LIMIT_SECONDS:
                return serve(cached, stale=True, error=error)
            block.update(state="error", error=error, action=google_calendar.ACTION_RETRY)
            return block, []
        entry = _CacheEntry(
            fetched_at=monotonic(),
            token_stamp=_token_stamp(profile),
            payload={
                "account": result.get("account", ""),
                "events": result.get("events", []),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        with _cache_lock:
            _cache[key] = entry
        return serve(entry, stale=False)


def build_feed(
    *,
    jobs_loader: Callable[[], list[dict[str, Any]]],
    refresh: bool = False,
    now: datetime | None = None,
    zone: tzinfo | None = None,
    overview: Callable[[], dict[str, Any]] = google.overview,
    fetch: Callable[..., dict[str, Any]] = google_calendar.list_events,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    zone = zone or google_calendar.owner_timezone()
    current = (now or datetime.now(timezone.utc)).astimezone(zone)
    start = google_calendar.day_start(current.date(), zone)
    end = google_calendar.day_start(current.date() + timedelta(days=WINDOW_DAYS), zone)

    try:
        source = pick_source(overview())
    except google.GoogleWorkspaceError as exc:
        google_block: dict[str, Any] = {
            "state": "error",
            "source_profile": None,
            "action": google_calendar.ACTION_SUPPORT,
            "account": "",
            "fetched_at": None,
            "stale": False,
            "error": {"code": exc.code, "message": str(exc), "action": google_calendar.ACTION_SUPPORT},
        }
        events: list[dict[str, Any]] = []
    else:
        google_block, events = _google_block(
            source,
            start=start,
            end=end,
            refresh=refresh,
            fetch=fetch,
            monotonic=monotonic,
        )

    schedule_state = "ok"
    try:
        job_items = _job_items(jobs_loader(), start=current, end=end, zone=zone)
    except Exception as exc:  # noqa: BLE001 — meetings still deserve to show
        logger.warning("Calendar feed could not read scheduled jobs: %s", type(exc).__name__)
        schedule_state = "error"
        job_items = []

    items = events + job_items
    items.sort(key=lambda item: _sort_key(item, zone))
    return {
        "timezone": google_calendar.timezone_name(zone),
        "now": current.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": WINDOW_DAYS},
        "google": google_block,
        "schedule": {"state": schedule_state},
        "items": items,
    }
