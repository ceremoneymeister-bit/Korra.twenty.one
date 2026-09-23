"""Agent tool: the owner's Google Calendar through the connection made in Korra.

Contract «подключение = инструмент агента»:

* the tool exists in every profile of an installation whose Ceremoneymeister
  OAuth app is configured — no per-profile config, skill or terminal access
  is needed, so curated agents get it too;
* each call resolves the profile's *effective* grant at that moment (its own
  or one the owner explicitly shared with it).  Connecting in the owner's
  screen works on the next call, and revoking or detaching a shared grant
  denies the next call — even inside an already running conversation;
* the schema never changes with the connection state, so a connect or revoke
  never rebuilds tool schemas mid-conversation (prompt cache stays valid).

The tool reads events and can add a simple event without guests.  Credential
management stays with ``google_workspace_auth`` and the owner's screens.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from korra_constants import get_hermes_home
from korra_cli import google_calendar
from korra_cli import google_workspace as google
from tools.registry import registry


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
}


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
    return json.dumps({"ok": True, "created": _format_event(event)}, ensure_ascii=False)


def _handle(args: dict, **_kwargs) -> str:
    action = str((args or {}).get("action") or "list").strip().lower()
    try:
        if action == "list":
            return _list(args or {})
        if action == "create":
            return _create(args or {})
        return _error("invalid_request", f"Unsupported action: {action}")
    except google_calendar.CalendarError as exc:
        return _error(exc.code, str(exc), exc.action)


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
            "are invited. If the result says the calendar is not connected, "
            "explain the owner's next step instead of retrying."
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
    check_fn=google.app_ready,
    description="Owner's Google Calendar through the Korra connection.",
    emoji="📅",
)
