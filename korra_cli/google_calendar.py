"""Google Calendar through a profile's effective Google grant.

One code path serves both the owner's dashboard and the agents' tool, so
"what the owner sees" and "what the agent can answer" cannot drift apart.

The grant is resolved on every call through :mod:`korra_cli.google_workspace`
(`_credentials`), which already follows the explicit sharing policy and
refreshes an expired access token under the profile lifecycle lock.  Nothing
here stores a token: connecting a calendar makes it usable on the very next
call, and revoking (or detaching a shared grant) makes the next call fail.

Only the ``primary`` calendar is read: the least-privilege scope Korra asks
for (``calendar.events``) covers events on the owner's calendars but not the
calendar list itself.
"""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable

from korra_cli import google_workspace as google


EVENTS_ENDPOINT = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
REQUEST_TIMEOUT_SECONDS = 10
PAGE_SIZE = 250
MAX_EVENTS = 500

# Owner-facing next step for every failure class.  The UI maps these to a
# button; the agent tool repeats them in words.
ACTION_CONNECT = "connect"
ACTION_RECONNECT = "reconnect"
ACTION_RETRY = "retry"
ACTION_SUPPORT = "support"
# A write whose result is unknown: check before doing anything again.
ACTION_VERIFY = "verify"


class CalendarError(RuntimeError):
    """A calendar failure the owner (or the agent) can act on."""

    def __init__(self, code: str, message: str, *, action: str, transient: bool = False) -> None:
        self.code = code
        self.action = action
        self.transient = transient
        super().__init__(message)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "action": self.action}


_APP_CODES = frozenset({"app_missing", "app_invalid", "app_permissions", "redirect_not_registered"})
_RECONNECT_CODES = frozenset(
    {
        "token_invalid",
        "token_refresh_failed",
        "scope_mismatch",
        "scope_contract_invalid",
    }
)


def _from_workspace_error(exc: google.GoogleWorkspaceError) -> CalendarError:
    code = exc.code
    if code in _APP_CODES:
        return CalendarError(
            "app_unavailable",
            "Google connection is not set up on this Korra server yet.",
            action=ACTION_SUPPORT,
        )
    if code == "token_missing":
        return CalendarError(
            "not_connected",
            "Google Calendar is not connected for this agent.",
            action=ACTION_CONNECT,
        )
    if code == "service_not_selected":
        return CalendarError(
            "calendar_not_selected",
            "Google is connected without Calendar access.",
            action=ACTION_RECONNECT,
        )
    if code in _RECONNECT_CODES:
        return CalendarError(
            "reauthorization_required",
            "Google access expired or was revoked; the owner must reconnect it.",
            action=ACTION_RECONNECT,
        )
    if code in {"flow_busy", "sharing_busy"}:
        return CalendarError(
            "google_unavailable",
            "Google connection is being changed right now; try again in a moment.",
            action=ACTION_RETRY,
            transient=True,
        )
    return CalendarError(
        "connection_invalid",
        "Google connection settings could not be read safely.",
        action=ACTION_SUPPORT,
    )


def _access_token(profile_home: Path) -> str:
    """Return a valid access token for the profile's effective grant."""
    try:
        credentials = google._credentials(profile_home, required_service="calendar")
    except google.GoogleWorkspaceError as exc:
        raise _from_workspace_error(exc) from exc
    token = str(getattr(credentials, "token", "") or "").strip()
    if not token:
        raise CalendarError(
            "reauthorization_required",
            "Google did not provide a usable access token; the owner must reconnect it.",
            action=ACTION_RECONNECT,
        )
    return token


def _google_reason(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8", errors="replace"))
        errors = payload.get("error", {}).get("errors") or []
        if errors and isinstance(errors[0], dict):
            return str(errors[0].get("reason") or "")
        return str(payload.get("error", {}).get("status") or "")
    except Exception:
        return ""


_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "RESOURCE_EXHAUSTED"})


def _http_json(request: urllib.request.Request) -> dict[str, Any]:
    """Execute one Calendar API request.  Tests replace this seam."""
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Calendar API returned a non-object response")
    return payload


class _WriteOutcomeUnknown(Exception):
    """A write left without an answer: Google may or may not have stored it."""


class _AlreadyExists(Exception):
    """Google refused a create because an event with this id already exists."""


def _call(request: urllib.request.Request, *, write: bool = False) -> dict[str, Any]:
    """Execute a request; ``write=True`` keeps unknown outcomes apart.

    For a read a lost answer is simply "try again". For a write it is not:
    Google may have stored the event before the answer was lost, so the
    caller has to reconcile instead of retrying blindly.
    """
    try:
        return _http_json(request)
    except urllib.error.HTTPError as exc:
        if write and exc.code == 409:
            raise _AlreadyExists() from exc
        if write and (exc.code >= 500 or exc.code == 408):
            raise _WriteOutcomeUnknown() from exc
        if exc.code == 401:
            raise CalendarError(
                "reauthorization_required",
                "Google rejected the stored access; the owner must reconnect it.",
                action=ACTION_RECONNECT,
            ) from exc
        if exc.code == 403:
            if _google_reason(exc) in _RATE_REASONS:
                raise CalendarError(
                    "google_unavailable",
                    "Google Calendar is rate limiting requests; try again later.",
                    action=ACTION_RETRY,
                    transient=True,
                ) from exc
            raise CalendarError(
                "reauthorization_required",
                "Google denied Calendar access; the owner must reconnect it with Calendar selected.",
                action=ACTION_RECONNECT,
            ) from exc
        if exc.code == 400:
            raise CalendarError(
                "invalid_request",
                "Google Calendar rejected the request parameters.",
                action=ACTION_RETRY,
            ) from exc
        raise CalendarError(
            "google_unavailable",
            f"Google Calendar did not answer (HTTP {exc.code}).",
            action=ACTION_RETRY,
            transient=True,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        if write and not _refused_before_sending(exc):
            raise _WriteOutcomeUnknown() from exc
        raise CalendarError(
            "google_unavailable",
            "Google Calendar could not be reached.",
            action=ACTION_RETRY,
            transient=True,
        ) from exc


def _refused_before_sending(exc: BaseException) -> bool:
    """Connection errors that prove the request never reached Google."""
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, (ConnectionRefusedError,)) or (
        isinstance(reason, OSError) and getattr(reason, "errno", None) in {-2, -3}  # DNS failure
    )


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("calendar bounds must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce a Calendar API event to the fields Korra shows.

    Descriptions and attendee lists are deliberately dropped: the dashboard
    and a schedule answer need time, title and place, not private notes.
    """
    if not isinstance(raw, dict) or raw.get("status") == "cancelled":
        return None
    start = raw.get("start") if isinstance(raw.get("start"), dict) else {}
    end = raw.get("end") if isinstance(raw.get("end"), dict) else {}
    all_day = bool(start.get("date")) and not start.get("dateTime")
    start_value = start.get("dateTime") or start.get("date")
    end_value = end.get("dateTime") or end.get("date") or start_value
    if not start_value:
        return None
    event: dict[str, Any] = {
        "id": str(raw.get("id") or ""),
        "title": str(raw.get("summary") or "").strip() or "(без названия)",
        "start": str(start_value),
        "end": str(end_value),
        "all_day": all_day,
    }
    location = str(raw.get("location") or "").strip()
    if location:
        event["location"] = location[:200]
    link = str(raw.get("htmlLink") or "")
    if link.startswith("https://"):
        event["url"] = link
    join = str(raw.get("hangoutLink") or "")
    if join.startswith("https://"):
        event["join_url"] = join
    return event


def list_events(profile_home: Path, *, start: datetime, end: datetime) -> dict[str, Any]:
    """Events of the primary calendar in ``[start, end)``, expanded and sorted."""
    if end <= start:
        raise CalendarError("invalid_request", "The time range is empty.", action=ACTION_RETRY)
    token = _access_token(profile_home)
    params = {
        "timeMin": _rfc3339(start),
        "timeMax": _rfc3339(end),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": str(PAGE_SIZE),
    }
    events: list[dict[str, Any]] = []
    account = ""
    calendar_timezone = ""
    page_token = ""
    while True:
        query = dict(params)
        if page_token:
            query["pageToken"] = page_token
        request = urllib.request.Request(
            f"{EVENTS_ENDPOINT}?{urllib.parse.urlencode(query)}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            method="GET",
        )
        payload = _call(request)
        account = account or str(payload.get("summary") or "")
        calendar_timezone = calendar_timezone or str(payload.get("timeZone") or "")
        for raw in payload.get("items") or []:
            event = normalize_event(raw)
            if event is not None:
                events.append(event)
        page_token = str(payload.get("nextPageToken") or "")
        if not page_token or len(events) >= MAX_EVENTS:
            break
    return {
        "account": account,
        "calendar_timezone": calendar_timezone,
        "events": events[:MAX_EVENTS],
        "truncated": bool(page_token),
    }


def event_id_for(
    *,
    title: str,
    start: datetime,
    end: datetime,
    location: str = "",
    description: str = "",
    generation: int = 0,
) -> str:
    """Stable Calendar event id for one requested event.

    The same request always maps to the same id, so a repeated create — after
    a lost answer, or the agent asking twice — cannot produce a second event:
    Google answers 409 and the existing event is returned instead. Google ids
    use base32hex (``0-9a-v``), 5–1024 characters.
    """
    key = "\x1f".join(
        [
            title.strip(),
            start.astimezone(timezone.utc).isoformat(),
            end.astimezone(timezone.utc).isoformat(),
            location.strip(),
            description.strip(),
            str(generation),
        ]
    )
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return "k21" + base64.b32hexencode(digest).decode("ascii").lower().rstrip("=")[:40]


def _event_request(token: str, event_id: str) -> urllib.request.Request:
    return urllib.request.Request(
        f"{EVENTS_ENDPOINT}/{urllib.parse.quote(event_id, safe='')}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )


def _stored_event(token: str, event_id: str) -> dict[str, Any] | None:
    """The event with this id as Google stores it, or None if there is none."""
    try:
        return _http_json(_event_request(token, event_id))
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return None
        raise


def create_event(
    profile_home: Path,
    *,
    title: str,
    start: datetime,
    end: datetime,
    location: str = "",
    description: str = "",
) -> dict[str, Any]:
    """Add one event to the primary calendar.  No guests, no invitations.

    Idempotent per request: the event id is derived from its content (see
    :func:`event_id_for`). When the answer to the insert is lost, the event is
    looked up by that id before anything is reported; if even that is
    impossible, the outcome is reported as unknown — never as a failure that
    invites a blind retry.

    Returns the normalized event plus ``already_existed`` (the same event was
    created earlier) and ``reconciled`` (confirmed after a lost answer).
    """
    title = (title or "").strip()
    if not title:
        raise CalendarError("invalid_request", "An event needs a title.", action=ACTION_RETRY)
    if end <= start:
        raise CalendarError("invalid_request", "An event must end after it starts.", action=ACTION_RETRY)
    token = _access_token(profile_home)
    body: dict[str, Any] = {
        "summary": title[:500],
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    if location.strip():
        body["location"] = location.strip()[:500]
    if description.strip():
        body["description"] = description.strip()[:4000]

    # A deleted event keeps its id reserved; the next generation gets a new one.
    for generation in range(3):
        event_id = event_id_for(
            title=title,
            start=start,
            end=end,
            location=location,
            description=description,
            generation=generation,
        )
        request = urllib.request.Request(
            f"{EVENTS_ENDPOINT}?{urllib.parse.urlencode({'sendUpdates': 'none'})}",
            data=json.dumps({**body, "id": event_id}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            event = normalize_event(_call(request, write=True))
            if event is not None:
                return {**event, "already_existed": False, "reconciled": False}
            # Accepted but unreadable: confirm by id below like a lost answer.
            raise _WriteOutcomeUnknown()
        except _AlreadyExists:
            stored = _lookup_after_write(token, event_id)
            event = normalize_event(stored) if stored else None
            if event is not None:
                return {**event, "already_existed": True, "reconciled": False}
            continue  # deleted earlier: this id is spent, take the next one
        except _WriteOutcomeUnknown:
            stored = _lookup_after_write(token, event_id)
            event = normalize_event(stored) if stored else None
            if event is not None:
                return {**event, "already_existed": False, "reconciled": True}
            # Checked: the event is not there. Repeating the same request is
            # safe — it carries the same id, so it can never add a second one.
            raise CalendarError(
                "create_not_stored",
                "Google did not store the event: its answer was lost and the event is not in the calendar. "
                "Repeating exactly the same request is safe (same event id, no duplicate).",
                action=ACTION_RETRY,
            ) from None
    raise CalendarError(
        "invalid_request",
        "This exact event was created and deleted several times; change its title or time.",
        action=ACTION_VERIFY,
    )


def _lookup_after_write(token: str, event_id: str) -> dict[str, Any] | None:
    """Look an event up after a write; an unanswerable lookup is 'unknown'."""
    try:
        return _stored_event(token, event_id)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise CalendarError(
                "reauthorization_required",
                "Google rejected the stored access; the owner must reconnect it.",
                action=ACTION_RECONNECT,
            ) from exc
        raise _outcome_unknown() from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise _outcome_unknown() from exc


def _outcome_unknown() -> CalendarError:
    return CalendarError(
        "create_outcome_unknown",
        "Google did not confirm whether the event was created, and it could not be checked.",
        action=ACTION_VERIFY,
    )


# ---------------------------------------------------------------------------
# Time helpers shared by the dashboard feed and the agent tool
# ---------------------------------------------------------------------------


def owner_timezone() -> tzinfo:
    """The installation's configured zone, or the server's local one."""
    try:
        from korra_time import get_timezone

        configured = get_timezone()
    except Exception:
        configured = None
    if configured is not None:
        return configured
    return datetime.now().astimezone().tzinfo or timezone.utc


def timezone_name(zone: tzinfo) -> str:
    key = getattr(zone, "key", None)
    if isinstance(key, str) and key:
        return key
    offset = datetime.now(zone).utcoffset() or timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"UTC{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def day_start(day: date, zone: tzinfo) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=zone)


def parse_moment(value: str, zone: tzinfo) -> datetime:
    """ISO 8601 date-time; a value without offset is read in the owner's zone."""
    text = (value or "").strip()
    if not text:
        raise ValueError("empty date-time")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone)
    return moment


Clock = Callable[[], datetime]
