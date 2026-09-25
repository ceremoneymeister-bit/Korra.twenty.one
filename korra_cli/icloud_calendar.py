"""Installation-wide iCloud Calendar connection, reads and verified creates.

The owner's app-specific password is kept in a private file under the
installation root.  Callers receive status and events, never the password.
Every agent resolves the same file on each call, so disconnect takes effect
without copying credentials into profiles or rebuilding model tool schemas.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time as clock
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from korra_constants import get_default_hermes_root
from utils import atomic_json_write


BASE_URL = "https://caldav.icloud.com/"
NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
MAX_RESPONSE_BYTES = 2_000_000
MAX_CALENDARS = 30
MAX_EVENTS = 300
MAX_DAYS = 31
# The dashboard is visited far more often than calendars change. Reuse a
# recent response and refresh older results without blocking a page visit.
DASHBOARD_FRESH_SECONDS = 2 * 60
DASHBOARD_STALE_SECONDS = 30 * 60
DASHBOARD_RETRY_SECONDS = 30
DASHBOARD_MANUAL_SECONDS = 15
_lock = threading.RLock()


@dataclass
class _DashboardCache:
    key: tuple[tuple[int, int, int], str, str]
    payload: dict
    fetched_mono: float
    attempted_mono: float
    refreshing: bool = False
    error_code: str | None = None


_dashboard_cache: dict[Path, _DashboardCache] = {}
_dashboard_fetch_locks: dict[Path, threading.Lock] = {}
_dashboard_auth_errors: dict[Path, tuple[tuple[int, int, int], str]] = {}


class ICloudCalendarError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 502):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def _path(root: Path | None = None) -> Path:
    base = Path(root or get_default_hermes_root())
    folder = base / "icloud-calendar"
    if folder.is_symlink() or (folder / "credentials.json").is_symlink():
        raise ICloudCalendarError("unsafe_storage", "Хранилище iCloud небезопасно.", 409)
    return folder / "credentials.json"


def _credentials(root: Path | None = None) -> dict[str, str] | None:
    path = _path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ICloudCalendarError("storage_error", "Не удалось прочитать подключение iCloud.") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("username"), str) or not isinstance(raw.get("password"), str):
        raise ICloudCalendarError("storage_error", "Подключение iCloud повреждено.")
    return {"username": raw["username"], "password": raw["password"]}


def status(root: Path | None = None) -> dict:
    creds = _credentials(root)
    return {"state": "connected" if creds else "not_connected", "account": creds["username"] if creds else None}


def _safe_url(href: str, base: str) -> str:
    url = urljoin(base, href)
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443) or not re.fullmatch(r"(?:p\d+-)?caldav\.icloud\.com", host):
        raise ICloudCalendarError("unsafe_response", "iCloud вернул недопустимый адрес.")
    return url


def _xml(raw: bytes) -> ET.Element:
    try:
        source = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ICloudCalendarError("invalid_response", "iCloud вернул ответ в неизвестной кодировке.") from exc
    if len(raw) > MAX_RESPONSE_BYTES or "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
        raise ICloudCalendarError("invalid_response", "Ответ iCloud слишком большой или недопустим.")
    try:
        return ET.fromstring(source)
    except ET.ParseError as exc:
        raise ICloudCalendarError("invalid_response", "iCloud вернул повреждённый ответ.") from exc


def _request(client: httpx.Client, method: str, url: str, body: str, *, depth: str) -> ET.Element:
    url = _safe_url(url, BASE_URL)
    for _ in range(4):
        try:
            with client.stream(
                method, url, content=body.encode("utf-8"),
                headers={"Content-Type": "application/xml; charset=utf-8", "Depth": depth},
                timeout=15,
            ) as response:
                if response.status_code in (301, 302, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ICloudCalendarError("server_error", "iCloud вернул переход без адреса.")
                    url = _safe_url(location, url)
                    continue
                if response.status_code in (401, 403):
                    raise ICloudCalendarError("auth_error", "Пароль приложения iCloud не принят.", 424)
                if response.status_code < 200 or response.status_code >= 300:
                    raise ICloudCalendarError("server_error", "iCloud Calendar не ответил корректно.")
                chunks = bytearray()
                for part in response.iter_bytes():
                    chunks.extend(part)
                    if len(chunks) > MAX_RESPONSE_BYTES:
                        raise ICloudCalendarError("invalid_response", "Ответ iCloud слишком большой.")
                return _xml(bytes(chunks))
        except httpx.HTTPError as exc:
            raise ICloudCalendarError("network_error", "iCloud Calendar не ответил.") from exc
    raise ICloudCalendarError("server_error", "Слишком много переходов iCloud Calendar.")


def _href(root: ET.Element, expression: str, base: str) -> str:
    node = root.find(expression, NS)
    if node is None or not node.text:
        raise ICloudCalendarError("invalid_response", "В ответе iCloud нет адреса календаря.")
    return _safe_url(node.text.strip(), base)


def _calendars(client: httpx.Client) -> list[tuple[str, str]]:
    principal = _request(client, "PROPFIND", BASE_URL,
        '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>', depth="0")
    principal_url = _href(principal, ".//d:current-user-principal/d:href", BASE_URL)
    home = _request(client, "PROPFIND", principal_url,
        '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-home-set/></d:prop></d:propfind>', depth="0")
    home_url = _href(home, ".//c:calendar-home-set/d:href", principal_url)
    listing = _request(client, "PROPFIND", home_url,
        '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:displayname/><d:resourcetype/><c:supported-calendar-component-set/></d:prop></d:propfind>', depth="1")
    found: list[tuple[str, str]] = []
    for response in listing.findall(".//d:response", NS):
        if response.find(".//d:resourcetype/c:calendar", NS) is None:
            continue
        components = response.findall(".//c:supported-calendar-component-set/c:comp", NS)
        if components and not any(component.get("name") == "VEVENT" for component in components):
            continue
        href = response.find("d:href", NS)
        if href is None or not href.text:
            continue
        name = response.find(".//d:displayname", NS)
        found.append(((name.text or "Календарь") if name is not None else "Календарь", _safe_url(href.text.strip(), home_url)))
        if len(found) >= MAX_CALENDARS:
            break
    return found


def _properties(block: list[str]) -> dict[str, tuple[str, dict[str, str]]]:
    props: dict[str, tuple[str, dict[str, str]]] = {}
    for line in block:
        if ":" not in line:
            continue
        head, value = line.split(":", 1)
        bits = head.split(";")
        params = dict(part.split("=", 1) for part in bits[1:] if "=" in part)
        props[bits[0].upper()] = (value, params)
    return props


def _moment(raw: tuple[str, dict[str, str]] | None) -> tuple[datetime | None, bool]:
    if raw is None:
        return None, False
    value, params = raw
    try:
        if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
            return datetime.strptime(value[:8], "%Y%m%d").replace(tzinfo=timezone.utc), True
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc), False
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
        name = params.get("TZID", "UTC").strip('"')
        return parsed.replace(tzinfo=ZoneInfo(name)), False
    except (ValueError, ZoneInfoNotFoundError):
        return None, False


def _unescape(value: str) -> str:
    # Decode once: a literal backslash followed by n is not a newline.
    return re.sub(r"\\([nN,;\\])", lambda match: "\n" if match[1] in "nN" else match[1], value)


def _events(ics: str, calendar: str, start: datetime, end: datetime) -> list[dict]:
    lines: list[str] = []
    for line in ics.replace("\r\n", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    result: list[dict] = []
    block: list[str] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            block = []
        elif line == "END:VEVENT" and block is not None:
            props = _properties(block)
            begin, all_day = _moment(props.get("DTSTART"))
            finish, _ = _moment(props.get("DTEND"))
            if all_day:
                # DATE values are calendar dates, not UTC instants. Comparing
                # midnight UTC against the owner's local range can leak the
                # previous day's event into today's feed.
                visible = bool(begin and begin.date() < end.date()
                               and (finish or begin + timedelta(days=1)).date() > start.date())
            else:
                visible = bool(begin and begin < end and (finish or begin + timedelta(days=1)) > start)
            if visible and begin:
                title = _unescape(props.get("SUMMARY", ("Без названия", {}))[0])[:300]
                uid = props.get("UID", ("", {}))[0][:300]
                result.append({"id": f"{calendar}:{uid}:{begin.isoformat()}", "title": title,
                               "start": begin.isoformat(), "end": finish.isoformat() if finish else None,
                               "all_day": all_day, "calendar": calendar,
                               "location": _unescape(props.get("LOCATION", ("", {}))[0])[:300]})
            block = None
        elif block is not None:
            block.append(line)
    return result


def _read(client: httpx.Client, start: datetime, end: datetime) -> list[dict]:
    query = (f'<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
             f'<d:prop><c:calendar-data><c:expand start="{start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}" '
             f'end="{end.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"/></c:calendar-data></d:prop>'
             f'<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
             f'<c:time-range start="{start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}" '
             f'end="{end.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"/>'
             f'</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>')
    events: list[dict] = []
    for name, url in _calendars(client):
        feed = _request(client, "REPORT", url, query, depth="1")
        for node in feed.findall(".//c:calendar-data", NS):
            if node.text:
                events.extend(_events(node.text, name, start, end))
                if len(events) > MAX_EVENTS:
                    raise ICloudCalendarError("too_many_events", "В выбранном периоде слишком много событий.")
    events.sort(key=lambda item: datetime.fromisoformat(item["start"]))
    return events


def _client(username: str, password: str) -> httpx.Client:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return httpx.Client(headers={"Authorization": f"Basic {token}", "User-Agent": "Korra-iCloud-Calendar/1"}, follow_redirects=False)


def connect(username: str, password: str, *, root: Path | None = None) -> dict:
    username, password = username.strip(), password.strip()
    if not username or "@" not in username or not password or len(username) > 320 or len(password) > 200:
        raise ICloudCalendarError("invalid_credentials", "Введите Apple ID и пароль приложения.", 422)
    with _client(username, password) as client:
        calendars = _calendars(client)
    if not calendars:
        raise ICloudCalendarError("no_calendars", "В учётной записи нет календарей с событиями.", 422)
    with _lock:
        path = _path(root)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        atomic_json_write(path, {"username": username, "password": password}, mode=0o600)
        _dashboard_cache.pop(path.parent.parent, None)
        _dashboard_auth_errors.pop(path.parent.parent, None)
    return {"state": "connected", "account": username, "calendars": len(calendars)}


def disconnect(*, root: Path | None = None) -> dict:
    with _lock:
        path = _path(root)
        path.unlink(missing_ok=True)
        _dashboard_cache.pop(path.parent.parent, None)
        _dashboard_auth_errors.pop(path.parent.parent, None)
    return {"state": "not_connected", "account": None}


def list_events(start: datetime, end: datetime, *, root: Path | None = None) -> dict:
    if start.tzinfo is None or end.tzinfo is None or not start < end or end - start > timedelta(days=MAX_DAYS):
        raise ICloudCalendarError("invalid_range", "Период должен быть от 1 до 31 дня.", 422)
    creds = _credentials(root)
    if creds is None:
        raise ICloudCalendarError("not_connected", "iCloud Calendar не подключён.", 404)
    with _client(creds["username"], creds["password"]) as client:
        items = _read(client, start, end)
    return {"account": creds["username"], "fetched_at": datetime.now(timezone.utc).isoformat(), "events": items}


def _calendar_id(url: str) -> str:
    parsed = urlsplit(url)
    # Apple's discovery may spell the same HTTPS address with or without :443.
    canonical = f"https://{parsed.hostname}{parsed.path.rstrip('/')}/"
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


def list_calendars(*, root: Path | None = None) -> dict:
    """Return opaque selection IDs; no credential or private CalDAV URL."""
    creds = _credentials(root)
    if creds is None:
        raise ICloudCalendarError("not_connected", "iCloud Calendar не подключён.", 404)
    with _client(creds["username"], creds["password"]) as client:
        calendars = _calendars(client)
    return {"calendars": [{"id": _calendar_id(url), "name": name} for name, url in calendars]}


def _calendar_text(value: str, limit: int) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(value) > limit or any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ICloudCalendarError("invalid_request", "Текст события слишком длинный или содержит служебные символы.", 422)
    return value


def _ical_line(name: str, value: str) -> str:
    """RFC 5545 TEXT escaping and UTF-8 line folding at 75 octets."""
    escaped = value.replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")
    lines, line, size = [], "", 0
    for char in f"{name}:{escaped}":
        width = len(char.encode("utf-8"))
        if size + width > 75:
            lines.append(line)
            line, size = " ", 1
        line += char
        size += width
    return "\r\n".join([*lines, line])


def _object_request(client: httpx.Client, method: str, url: str, body: str = "") -> tuple[int, bytes]:
    """Bounded calendar-object I/O, with the same Apple-only redirect boundary."""
    url = _safe_url(url, BASE_URL)
    headers = {"Accept": "text/calendar"}
    if method == "PUT":
        headers.update({"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"})
    for _ in range(4):
        with client.stream(method, url, content=body.encode(), headers=headers, timeout=15) as response:
            if response.status_code in (301, 302, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise ICloudCalendarError("invalid_response", "iCloud вернул переход без адреса.")
                url = _safe_url(location, url)
                continue
            content = bytearray()
            for part in response.iter_bytes():
                content.extend(part)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise ICloudCalendarError("invalid_response", "Ответ iCloud слишком большой.")
            return response.status_code, bytes(content)
    raise ICloudCalendarError("invalid_response", "Слишком много переходов iCloud Calendar.")


def _verify_created(raw: bytes, uid: str, expected: dict, calendar: str) -> dict:
    """Verify actual stored fields, including an existing but user-edited object."""
    text = re.sub(r"\r?\n[ \t]", "", raw.decode("utf-8"))
    lines = text.replace("\r\n", "\n").split("\n")
    if lines.count("BEGIN:VEVENT") != 1 or lines.count("END:VEVENT") != 1:
        raise ValueError("Expected one event")
    block = lines[lines.index("BEGIN:VEVENT") + 1:lines.index("END:VEVENT")]
    props = _properties(block)
    begin, all_day = _moment(props.get("DTSTART"))
    finish, _ = _moment(props.get("DTEND"))
    matches = (
        props.get("UID", (None, {}))[0] == uid
        and begin == expected["start"] and finish == expected["end"] and not all_day
        and props.get("STATUS", ("", {}))[0].upper() != "CANCELLED"
        and not {"RRULE", "RDATE", "RECURRENCE-ID", "ATTENDEE", "ORGANIZER"}.intersection(props)
        and all(_unescape(props.get(key, ("", {}))[0]) == expected[field]
                for key, field in (("SUMMARY", "title"), ("LOCATION", "location"), ("DESCRIPTION", "description")))
    )
    if not matches:
        raise ICloudCalendarError("create_conflict", "Запись с этим ID отличается от поручения. Проверьте календарь; существующее событие не перезаписано.", 409)
    return {"id": uid, "calendar": calendar, "title": expected["title"],
            "start": begin.isoformat(), "end": finish.isoformat(), "all_day": False,
            "location": expected["location"], "description": expected["description"]}


def create_event(*, title: str, start: datetime, end: datetime, calendar_id: str = "",
                 location: str = "", description: str = "", root: Path | None = None) -> dict:
    """Create one timed event, no attendees; repeat-safe and read back from Apple.

    CalDAV conditional PUT (RFC 4791 §5.3.2) never overwrites an existing
    object. Ambiguous writes are verified by their deterministic UID before
    returning; an unavailable verification is explicitly an unknown outcome.
    """
    title = _calendar_text(title, 300)
    location, description = _calendar_text(location, 300), _calendar_text(description, 4000)
    if (not title or start.utcoffset() is None or end.utcoffset() is None
            or start.microsecond or end.microsecond or not start < end
            or end - start > timedelta(days=MAX_DAYS)):
        raise ICloudCalendarError("invalid_request", "Нужны название, время с часовым поясом и окончание после начала (не более 31 дня, точность до секунды).", 422)
    expected = {"title": title, "start": start.astimezone(timezone.utc),
                "end": end.astimezone(timezone.utc), "location": location, "description": description}
    identity = json.dumps({**expected, "start": expected["start"].isoformat(),
                           "end": expected["end"].isoformat()}, ensure_ascii=False, sort_keys=True)
    uid = "korra-" + hashlib.sha256(identity.encode()).hexdigest()
    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Korra//Calendar//RU", "BEGIN:VEVENT",
        f"UID:{uid}", f"DTSTAMP:{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
        f"DTSTART:{expected['start']:%Y%m%dT%H%M%SZ}", f"DTEND:{expected['end']:%Y%m%dT%H%M%SZ}",
        _ical_line("SUMMARY", title), _ical_line("LOCATION", location),
        _ical_line("DESCRIPTION", description), "END:VEVENT", "END:VCALENDAR", "",
    ])
    root_path = Path(root or get_default_hermes_root())
    with _lock:
        fetch_lock = _dashboard_fetch_locks.setdefault(root_path, threading.Lock())
    # Serialize with dashboard reads so a pre-create refresh cannot later
    # repopulate its cache with a feed missing the newly created event.
    with fetch_lock:
        creds = _credentials(root_path)
        if creds is None:
            raise ICloudCalendarError("not_connected", "iCloud Calendar не подключён.", 404)
        with _client(creds["username"], creds["password"]) as client:
            calendars = _calendars(client)
            selected = [(name, url) for name, url in calendars if _calendar_id(url) == calendar_id] if calendar_id else calendars
            if len(selected) != 1:
                raise ICloudCalendarError("calendar_selection_required", "Выберите один календарь из action=calendars и передайте его calendar_id.", 422)
            name, url = selected[0]
            object_url = _safe_url(url.rstrip("/") + "/" + uid + ".ics", BASE_URL)
            if _credentials(root_path) != creds:
                raise ICloudCalendarError("connection_changed", "Подключение изменилось. Проверьте выбранный календарь заново.", 409)
            status_code = None
            try:
                try:
                    status_code, _ = _object_request(client, "PUT", object_url, ics)
                except (httpx.HTTPError, ICloudCalendarError):
                    pass  # Verify the deterministic resource before considering a retry.
                if status_code == 401:
                    raise ICloudCalendarError("auth_error", "Пароль приложения iCloud не принят.", 424)
                if status_code == 403:
                    raise ICloudCalendarError("write_forbidden", "Выбранный календарь iCloud недоступен для записи.", 403)
                if status_code is not None and 400 <= status_code < 500 and status_code not in (408, 409, 412, 429):
                    raise ICloudCalendarError("create_rejected", "iCloud отклонил создание события. Проверьте календарь и параметры.", 422)
                try:
                    stored_status, raw = _object_request(client, "GET", object_url)
                    if stored_status != 200:
                        raise ValueError("Cannot verify stored event")
                    event = _verify_created(raw, uid, expected, name)
                except (httpx.HTTPError, ValueError, ICloudCalendarError) as exc:
                    if isinstance(exc, ICloudCalendarError) and exc.code == "create_conflict":
                        raise
                    raise ICloudCalendarError("create_outcome_unknown", "Создание не подтверждено: событие могло сохраниться. Проверьте календарь; не создавайте другую запись повторно.") from exc
                return {**event, "calendar_id": _calendar_id(url), "already_existed": status_code in (409, 412),
                        "reconciled": status_code not in (200, 201, 204, 409, 412)}
            finally:
                with _lock:
                    _dashboard_cache.pop(root_path, None)


def _credential_stamp(root: Path) -> tuple[int, int, int]:
    try:
        stat = _path(root).stat()
    except OSError as exc:
        raise ICloudCalendarError("storage_error", "Не удалось проверить подключение iCloud.") from exc
    return (stat.st_ino, stat.st_mtime_ns, stat.st_size)


def reset_dashboard_cache() -> None:
    """Drop in-memory calendar data; useful for isolated tests and recovery."""
    with _lock:
        _dashboard_cache.clear()
        _dashboard_fetch_locks.clear()
        _dashboard_auth_errors.clear()


def _refresh_dashboard(root: Path, key: tuple[tuple[int, int, int], str, str],
                       creds: dict[str, str], start: datetime, end: datetime) -> _DashboardCache | None:
    with _lock:
        fetch_lock = _dashboard_fetch_locks.setdefault(root, threading.Lock())
    with fetch_lock:
        with _lock:
            latest = _dashboard_cache.get(root)
            if latest is not None and latest.key == key and clock.monotonic() - latest.fetched_mono < DASHBOARD_MANUAL_SECONDS:
                latest.refreshing = False
                return latest
        try:
            with _client(creds["username"], creds["password"]) as client:
                events = _read(client, start, end)
        except ICloudCalendarError as exc:
            with _lock:
                if _credential_stamp(root) == key[0]:
                    if exc.code == "auth_error":
                        _dashboard_cache.pop(root, None)
                        _dashboard_auth_errors[root] = (key[0], exc.code)
                    else:
                        latest = _dashboard_cache.get(root)
                        if latest is not None and latest.key == key:
                            latest.refreshing = False
                            latest.error_code = exc.code
            raise
        except Exception as exc:
            with _lock:
                latest = _dashboard_cache.get(root)
                if latest is not None and latest.key == key:
                    latest.refreshing = False
                    latest.error_code = "network_error"
            raise ICloudCalendarError("network_error", "iCloud Calendar не ответил.") from exc
        entry = _DashboardCache(
            key=key,
            payload={"account": creds["username"], "fetched_at": datetime.now(timezone.utc).isoformat(),
                     "events": events},
            fetched_mono=clock.monotonic(),
            attempted_mono=clock.monotonic(),
        )
        with _lock:
            # A disconnected or replaced grant must never repopulate the card.
            try:
                current_stamp = _credential_stamp(root)
            except ICloudCalendarError:
                return None
            if current_stamp != key[0]:
                return None
            _dashboard_cache[root] = entry
            _dashboard_auth_errors.pop(root, None)
        return entry


def _refresh_dashboard_background(root: Path, key: tuple[tuple[int, int, int], str, str],
                                  creds: dict[str, str], start: datetime, end: datetime) -> None:
    try:
        _refresh_dashboard(root, key, creds, start, end)
    except ICloudCalendarError:
        # The next dashboard poll reads the classified error without exposing
        # the app password or a private calendar URL in process logs.
        pass


def _dashboard_response(entry: _DashboardCache, zone: str, now: datetime, *, stale: bool) -> dict:
    return {
        "state": "connected", "account": entry.payload["account"], "timezone": zone,
        "now": now.isoformat(), "fetched_at": entry.payload["fetched_at"],
        "events": [dict(event) for event in entry.payload["events"]],
        "stale": stale, "refreshing": entry.refreshing, "error_code": entry.error_code,
    }


def dashboard_feed(*, root: Path | None = None, refresh: bool = False) -> dict:
    from korra_cli.google_calendar import owner_timezone

    root_path = Path(root or get_default_hermes_root())
    with _lock:
        creds = _credentials(root_path)
        if creds is None:
            _dashboard_cache.pop(root_path, None)
            _dashboard_auth_errors.pop(root_path, None)
            return {"state": "not_connected", "account": None, "fetched_at": None,
                    "events": [], "stale": False, "refreshing": False, "error_code": None}
        stamp = _credential_stamp(root_path)
        auth_error = _dashboard_auth_errors.get(root_path)
        if auth_error is not None and auth_error[0] == stamp and not refresh:
            raise ICloudCalendarError("auth_error", "Пароль приложения iCloud не принят.", 424)
        zone = owner_timezone()
        now = datetime.now(zone)
        today = now.date()
        key = (stamp, today.isoformat(), str(zone))
        cached = _dashboard_cache.get(root_path)
        if cached is not None and cached.key != key:
            _dashboard_cache.pop(root_path, None)
            cached = None
        age = clock.monotonic() - cached.fetched_mono if cached else None
        if cached is not None and age is not None:
            if age < (DASHBOARD_MANUAL_SECONDS if refresh else DASHBOARD_FRESH_SECONDS):
                return _dashboard_response(cached, str(zone), now, stale=False)
            if not refresh and age < DASHBOARD_STALE_SECONDS:
                if not cached.refreshing and clock.monotonic() - cached.attempted_mono >= DASHBOARD_RETRY_SECONDS:
                    cached.refreshing = True
                    cached.error_code = None
                    cached.attempted_mono = clock.monotonic()
                    start = datetime.combine(today, time.min, zone)
                    end = datetime.combine(today + timedelta(days=7), time.min, zone)
                    threading.Thread(target=_refresh_dashboard_background,
                        args=(root_path, key, creds, start, end), daemon=True,
                        name="icloud-dashboard-refresh").start()
                return _dashboard_response(cached, str(zone), now, stale=True)
    start = datetime.combine(today, time.min, zone)
    end = datetime.combine(today + timedelta(days=7), time.min, zone)
    try:
        entry = _refresh_dashboard(root_path, key, creds, start, end)
    except ICloudCalendarError as exc:
        with _lock:
            cached = _dashboard_cache.get(root_path)
            if exc.code != "auth_error" and cached is not None and cached.key == key:
                age = clock.monotonic() - cached.fetched_mono
                if age < DASHBOARD_STALE_SECONDS:
                    return _dashboard_response(cached, str(zone), datetime.now(zone), stale=True)
        raise
    if entry is None:
        return dashboard_feed(root=root_path, refresh=refresh)
    return _dashboard_response(entry, str(zone), datetime.now(zone), stale=False)
