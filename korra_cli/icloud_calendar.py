"""Installation-wide iCloud Calendar connection and bounded CalDAV reads.

The owner's app-specific password is kept in a private file under the
installation root.  Callers receive status and events, never the password.
Every agent resolves the same file on each call, so disconnect takes effect
without copying credentials into profiles or rebuilding model tool schemas.
"""

from __future__ import annotations

import base64
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
    return value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


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
