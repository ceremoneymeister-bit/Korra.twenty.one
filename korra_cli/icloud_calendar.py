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
_lock = threading.RLock()


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
    return {"state": "connected", "account": username, "calendars": len(calendars)}


def disconnect(*, root: Path | None = None) -> dict:
    with _lock:
        _path(root).unlink(missing_ok=True)
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


def dashboard_feed(*, root: Path | None = None) -> dict:
    state = status(root)
    if state["state"] != "connected":
        return {**state, "fetched_at": None, "events": []}
    from korra_cli.google_calendar import owner_timezone
    zone = owner_timezone()
    today = datetime.now(zone).date()
    start = datetime.combine(today, time.min, zone)
    end = datetime.combine(today + timedelta(days=7), time.min, zone)
    result = list_events(start, end, root=root)
    return {"state": "connected", "timezone": str(zone), "now": datetime.now(zone).isoformat(), **result}
