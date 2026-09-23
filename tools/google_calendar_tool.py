"""Agent tool: the owner's Google Calendar through the connection made in Korra.

Contract «подключение = инструмент агента», with the owner's consent kept:

* the tool is a configurable toolset (``google_calendar``): profiles on the
  default toolset list get it wherever the installation OAuth app is
  configured, while a profile with an explicit toolset list gets it only when
  the list names it — a restricted profile never gains it silently;
* two independent checks on **every call**: the principal (who this turn
  acts for, :mod:`gateway.principal`) and the grant (the profile's own or
  explicitly shared connection, resolved at call time). Revoking or detaching
  a grant denies the next call, even inside a running conversation;
* reads: the owner, live or delegated (owner-created scheduled jobs, board
  workers). Adding an event: only the owner speaking in a live conversation —
  a background run never makes external changes on its own;
* creates are idempotent: the event id is derived from the request, so a
  repeated create — after a lost answer or a second attempt — cannot produce
  a duplicate, and an unknown outcome is reported as such, never as "retry".

Credential management stays with ``google_workspace_auth`` and the owner's
screens.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from gateway.principal import current_principal
from korra_constants import get_hermes_home
from korra_cli import google_calendar
from korra_cli import google_workspace as google
from tools.registry import no_cache_check_fn, registry


MAX_DAYS = 31

_OWNER_ACTIONS = {
    google_calendar.ACTION_CONNECT: (
        "Tell the user that Google Calendar is not connected for this agent. "
        "The owner connects it in Korra: Настройки → Сервисы (or from the "
        "«Календарь» card on the dashboard) and can share it with this agent there."
    ),
    google_calendar.ACTION_RECONNECT: (
        "Tell the user that the owner has to reconnect Google with Calendar "
        "selected in Korra: Настройки → Сервисы."
    ),
    google_calendar.ACTION_RETRY: "Google did not answer; suggest trying again a bit later.",
    google_calendar.ACTION_SUPPORT: "Google is not set up on this Korra server; suggest contacting Korra support.",
    google_calendar.ACTION_VERIFY: (
        "The result is unknown: the event may already be in the calendar. Do not "
        "create it again. Check with action=list for that time and tell the user "
        "exactly what you found."
    ),
}

_OWNER_ONLY = (
    "The owner's Google Calendar is available only in the owner's own "
    "conversation (the Korra cabinet, the owner's computer, or the owner's "
    "direct chat when the owner is configured for this bot). Do not reveal, "
    "guess or change the owner's schedule here; say that you cannot access it."
)
_LIVE_OWNER_ONLY = (
    "Adding events is done only when the owner asks for it in a live "
    "conversation. This is a background run (a scheduled task or a board "
    "step): do not create it. Describe the event you would add in your "
    "result or ask the owner, and let the owner confirm it in chat."
)


def _error(code: str, message: str, action: str | None = None) -> str:
    payload: dict[str, Any] = {"ok": False, "error": code, "message": message}
    if action:
        payload["next_step"] = _OWNER_ACTIONS.get(action, "")
    return json.dumps(payload, ensure_ascii=False)


def _resolve_day(value: str, today: date) -> date:
    text = (value or "today").strip().lower()
    if text in {"today", "сегодня"}:
        return today
    if text in {"tomorrow", "завтра"}:
        return today + timedelta(days=1)
    if text in {"yesterday", "вчера"}:
        return today - timedelta(days=1)
    return date.fromisoformat(text)


def _format_event(event: dict[str, Any]) -> dict[str, Any]:
    item = {key: event[key] for key in ("title", "start", "end", "all_day") if key in event}
    for key in ("location", "url", "join_url"):
        if event.get(key):
            item[key] = event[key]
    return item


def _list(args: dict[str, Any]) -> str:
    zone = google_calendar.owner_timezone()
    today = datetime.now(zone).date()
    try:
        if args.get("start") or args.get("end"):
            if not (args.get("start") and args.get("end")):
                return _error("invalid_request", "Give both start and end, or use date/days.")
            start = google_calendar.parse_moment(str(args["start"]), zone)
            end = google_calendar.parse_moment(str(args["end"]), zone)
        else:
            first = _resolve_day(str(args.get("date") or "today"), today)
            days = int(args.get("days") or 1)
            if days < 1 or days > MAX_DAYS:
                return _error("invalid_request", f"days must be between 1 and {MAX_DAYS}.")
            start = google_calendar.day_start(first, zone)
            end = google_calendar.day_start(first + timedelta(days=days), zone)
    except (TypeError, ValueError):
        return _error(
            "invalid_request",
            "Use date=today|tomorrow|YYYY-MM-DD with optional days, or ISO 8601 start and end.",
        )
    if end <= start or end - start > timedelta(days=MAX_DAYS):
        return _error("invalid_request", f"The range must be positive and at most {MAX_DAYS} days.")

    result = google_calendar.list_events(get_hermes_home(), start=start, end=end)
    events = [_format_event(event) for event in result["events"]]
    payload: dict[str, Any] = {
        "ok": True,
        "timezone": google_calendar.timezone_name(zone),
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "count": len(events),
        "events": events,
    }
    if result.get("truncated"):
        payload["truncated"] = True
    return json.dumps(payload, ensure_ascii=False)


def _create(args: dict[str, Any]) -> str:
    zone = google_calendar.owner_timezone()
    try:
        start = google_calendar.parse_moment(str(args.get("start") or ""), zone)
        if args.get("end"):
            end = google_calendar.parse_moment(str(args["end"]), zone)
        else:
            end = start + timedelta(minutes=int(args.get("duration_minutes") or 60))
    except (TypeError, ValueError):
        return _error("invalid_request", "start (and end) must be ISO 8601 date-times.")
    event = google_calendar.create_event(
        get_hermes_home(),
        title=str(args.get("title") or ""),
        start=start,
        end=end,
        location=str(args.get("location") or ""),
        description=str(args.get("description") or ""),
    )
    payload: dict[str, Any] = {"ok": True, "created": _format_event(event)}
    if event.get("already_existed"):
        payload["already_existed"] = True
        payload["note"] = "This exact event was already in the calendar; nothing new was added."
    if event.get("reconciled"):
        payload["note"] = "Google's answer was lost; the event was found in the calendar, so it exists once."
    return json.dumps(payload, ensure_ascii=False)


def _handle(args: dict, **_kwargs) -> str:
    action = str((args or {}).get("action") or "list").strip().lower()
    # Who this turn acts for is decided by the server on every call, before
    # the grant is touched. Grant/share/revoke stay the second, independent
    # check inside korra_cli.google_calendar.
    principal = current_principal()
    try:
        if action == "list":
            if not principal.owner:
                return _error("owner_only", _OWNER_ONLY)
            return _list(args or {})
        if action == "create":
            if not principal.owner:
                return _error("owner_only", _OWNER_ONLY)
            if not principal.live:
                return _error("owner_confirmation_required", _LIVE_OWNER_ONLY)
            return _create(args or {})
        return _error("invalid_request", f"Unsupported action: {action}")
    except google_calendar.CalendarError as exc:
        return _error(exc.code, str(exc), exc.action)


@no_cache_check_fn
def _calendar_available() -> bool:
    """Offer the schema only where it can be used: app configured, owner turn.

    Uncached on purpose: the answer depends on who is speaking, and the tool
    definitions cache keys on the principal (see model_tools).
    """
    return current_principal().owner and google.app_ready()


registry.register(
    name="google_calendar",
    toolset="google_calendar",
    schema={
        "name": "google_calendar",
        "description": (
            "Read or add events in the owner's Google Calendar through the Google "
            "connection the owner made in Korra. Use it for questions like "
            "'what do I have tomorrow' or 'am I free on Friday afternoon'. "
            "action=list: events for date (today, tomorrow or YYYY-MM-DD) and "
            "optional days, or for an explicit ISO 8601 start/end; times are in "
            "the owner's timezone. action=create: add one event (title, start, "
            "end or duration_minutes, optional location/description); no guests "
            "are invited; creating is for the owner in a live conversation only, "
            "and repeating the same create never duplicates the event. If the "
            "result says the calendar is not connected or not available here, "
            "explain the next step instead of retrying."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "create"]},
                "date": {
                    "type": "string",
                    "description": "list: today, tomorrow or YYYY-MM-DD (default today).",
                },
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_DAYS,
                    "description": "list: number of days starting at date (default 1).",
                },
                "start": {
                    "type": "string",
                    "description": "ISO 8601 date-time. list: range start (with end). create: event start.",
                },
                "end": {
                    "type": "string",
                    "description": "ISO 8601 date-time. list: range end. create: event end.",
                },
                "duration_minutes": {
                    "type": "integer",
                    "minimum": 5,
                    "maximum": 1440,
                    "description": "create: length when end is omitted (default 60).",
                },
                "title": {"type": "string", "description": "create: event title."},
                "location": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    handler=_handle,
    check_fn=_calendar_available,
    description="Owner's Google Calendar through the Korra connection.",
    emoji="📅",
)
