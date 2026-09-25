"""Read and add events through the installation's existing iCloud connection."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from gateway.principal import current_principal
from korra_cli import google_calendar, icloud_calendar
from tools.registry import no_cache_check_fn, registry

_OWNER_ONLY = (
    "The iCloud Calendar belongs to the owner and is available only in the "
    "owner's own conversation (the Korra cabinet, the owner's computer, or the "
    "owner's direct chat when the owner is configured for this bot). Do not "
    "describe the owner's calendar here."
)


def _handle(args: dict, **_kwargs) -> str:
    args = args or {}
    # Same boundary as google_calendar: decided by the server on every call,
    # so a visitor of a public bot never reads the owner's calendar.
    principal = current_principal()
    if not principal.owner:
        return json.dumps({"ok": False, "error": "owner_only", "message": _OWNER_ONLY}, ensure_ascii=False)
    action = str(args.get("action") or "list").strip().lower()
    if action == "create" and not principal.live:
        return json.dumps({"ok": False, "error": "owner_confirmation_required",
                           "message": "Создание события доступно по просьбе владельца в личном диалоге; фоновый запуск может подготовить параметры встречи."}, ensure_ascii=False)
    if action not in {"list", "calendars", "create"}:
        return json.dumps({"ok": False, "error": "invalid_request", "message": "Неизвестное действие календаря."}, ensure_ascii=False)
    try:
        return _perform(action, args)
    except icloud_calendar.ICloudCalendarError as exc:
        if exc.code in {"not_connected", "auth_error"}:
            step = "Откройте «Настройки → Ключи и доступы → iCloud Calendar» и подключите календарь."
        elif exc.code in {"create_outcome_unknown", "create_conflict"}:
            step = "Прочитайте события за нужное время через action=list. Не создавайте изменённую копию события вслепую."
        elif exc.code in {"calendar_selection_required", "connection_changed", "write_forbidden"}:
            step = "Получите action=calendars и уточните календарь у владельца. Не выбирайте другой календарь без его решения."
        else:
            step = "Проверьте параметры запроса. Если iCloud недоступен, повторите чтение позже."
        return json.dumps({"ok": False, "error": exc.code, "message": str(exc), "next_step": step}, ensure_ascii=False)


def _perform(action: str, args: dict) -> str:
    if action == "calendars":
        return json.dumps({"ok": True, **icloud_calendar.list_calendars()}, ensure_ascii=False)
    zone = google_calendar.owner_timezone()
    if action == "create":
        try:
            start = google_calendar.parse_moment(str(args.get("start") or ""), zone)
            if args.get("end"):
                end = google_calendar.parse_moment(str(args["end"]), zone)
            else:
                duration = int(args.get("duration_minutes", 60))
                if not 5 <= duration <= 1440:
                    raise ValueError("Invalid duration")
                end = start + timedelta(minutes=duration)
        except (ValueError, TypeError, OverflowError):
            return json.dumps({"ok": False, "error": "invalid_request", "message": "Укажите start и end в ISO 8601 либо duration_minutes от 5 до 1440."}, ensure_ascii=False)
        event = icloud_calendar.create_event(
            title=str(args.get("title") or ""), start=start, end=end,
            calendar_id=str(args.get("calendar_id") or ""),
            location=str(args.get("location") or ""), description=str(args.get("description") or ""),
        )
        for field in ("start", "end"):
            event[field] = datetime.fromisoformat(event[field]).astimezone(zone).isoformat()
        already_existed, reconciled = event.pop("already_existed"), event.pop("reconciled")
        return json.dumps({"ok": True, "timezone": str(zone), "created": event,
                           "already_existed": already_existed, "reconciled": reconciled}, ensure_ascii=False)
    today = datetime.now(zone).date()
    try:
        raw_date = str(args.get("date") or "today").lower()
        day = today if raw_date in {"today", "сегодня"} else today + timedelta(days=1) if raw_date in {"tomorrow", "завтра"} else date.fromisoformat(raw_date)
        days = int(args.get("days") or 1)
        if days < 1 or days > icloud_calendar.MAX_DAYS:
            raise ValueError("days out of range")
        start = google_calendar.day_start(day, zone)
        end = google_calendar.day_start(day + timedelta(days=days), zone)
    except (ValueError, TypeError):
        return json.dumps({"ok": False, "error": "invalid_request", "message": "Укажите date=today|tomorrow|YYYY-MM-DD и days от 1 до 31."}, ensure_ascii=False)
    result = icloud_calendar.list_events(start, end)
    return json.dumps({"ok": True, "timezone": str(zone), "date": day.isoformat(), "days": days,
                       "count": len(result["events"]), "events": result["events"]}, ensure_ascii=False)


@no_cache_check_fn
def _calendar_available() -> bool:
    """Offer the schema to owner turns only; uncached like google_calendar."""
    return current_principal().owner


registry.register(
    name="icloud_calendar",
    toolset="icloud_calendar",
    schema={
        "name": "icloud_calendar",
        "description": (
            "Read or add events through the owner's iCloud connection in Настройки → Ключи и доступы. "
            "action=list (default) reads meetings and availability; action=calendars lists calendar IDs and names. "
            "action=create adds one timed event without invitations, only for the owner speaking live. "
            "Choose calendar_id from calendars; if several exist, ask which one unless already specified. "
            "Repeating the same request in the same calendar does not duplicate or overwrite it. "
            "No editing, deletion or recurring events. On unknown outcome, read the calendar before doing anything else."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "calendars", "create"]},
                "date": {"type": "string", "description": "today, tomorrow or YYYY-MM-DD; defaults to today"},
                "days": {"type": "integer", "minimum": 1, "maximum": 31},
                "calendar_id": {"type": "string", "description": "create: ID from action=calendars, required when several calendars exist."},
                "title": {"type": "string", "description": "create: event title, up to 300 characters."},
                "start": {"type": "string", "description": "create: ISO 8601 date-time, preferably with UTC offset. Otherwise owner's timezone."},
                "end": {"type": "string", "description": "create: ISO 8601 end; alternatively duration_minutes."},
                "duration_minutes": {"type": "integer", "minimum": 5, "maximum": 1440, "description": "create: duration when end omitted, default 60."},
                "location": {"type": "string", "description": "create: location, up to 300 characters."},
                "description": {"type": "string", "description": "create: notes, up to 4000 characters."},
            },
            "additionalProperties": False,
        },
    },
    handler=_handle,
    check_fn=_calendar_available,
    description="Owner's iCloud Calendar for all agents on this installation.",
    emoji="📅",
)
