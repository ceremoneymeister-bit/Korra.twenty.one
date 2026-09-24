"""Read the installation owner's iCloud Calendar from any agent profile."""

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
    if not current_principal().owner:
        return json.dumps({"ok": False, "error": "owner_only", "message": _OWNER_ONLY}, ensure_ascii=False)
    zone = google_calendar.owner_timezone()
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
    try:
        result = icloud_calendar.list_events(start, end)
    except icloud_calendar.ICloudCalendarError as exc:
        step = "Откройте «Настройки → Ключи и доступы → iCloud Calendar» и подключите календарь." if exc.code in {"not_connected", "auth_error"} else "Повторите запрос позже."
        return json.dumps({"ok": False, "error": exc.code, "message": str(exc), "next_step": step}, ensure_ascii=False)
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
        "description": "Read events from the owner's connected iCloud Calendar. Use for questions about Apple Calendar meetings and availability. The owner connects it under Настройки → Ключи и доступы. Read-only; do not claim to create or edit events.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "today, tomorrow or YYYY-MM-DD; defaults to today"},
                "days": {"type": "integer", "minimum": 1, "maximum": 31},
            },
            "additionalProperties": False,
        },
    },
    handler=_handle,
    check_fn=_calendar_available,
    description="Owner's iCloud Calendar for all agents on this installation.",
    emoji="📅",
)
