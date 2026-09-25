"""CalDAV protocol and credential lifecycle without contacting Apple."""

from __future__ import annotations

import base64
import json
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from korra_cli import icloud_calendar as ic


PRINCIPAL = b'''<d:multistatus xmlns:d="DAV:"><d:response><d:propstat><d:prop><d:current-user-principal><d:href>/123/principal/</d:href></d:current-user-principal></d:prop></d:propstat></d:response></d:multistatus>'''
HOME = b'''<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:propstat><d:prop><c:calendar-home-set><d:href>/123/calendars/</d:href></c:calendar-home-set></d:prop></d:propstat></d:response></d:multistatus>'''
LISTING = '''<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>/123/calendars/work/</d:href><d:propstat><d:prop><d:displayname>Работа</d:displayname><d:resourcetype><c:calendar/></d:resourcetype><c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set></d:prop></d:propstat></d:response></d:multistatus>'''.encode()
EVENTS = '''<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:propstat><d:prop><c:calendar-data>BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:meeting-1\r\nSUMMARY:Встреча с \r\n клиентом\r\nDTSTART:20260923T090000Z\r\nDTEND:20260923T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR</c:calendar-data></d:prop></d:propstat></d:response></d:multistatus>'''.encode()


def fake_client(monkeypatch, *, fail_status=0, malicious=False, home_href=None):
    requests = []

    def respond(request):
        requests.append(request)
        if fail_status:
            return httpx.Response(fail_status)
        if request.method == "REPORT":
            assert b"<c:expand" in request.content
            return httpx.Response(207, content=EVENTS)
        if request.url.path == "/":
            return httpx.Response(207, content=PRINCIPAL.replace(b"/123/principal/", b"https://evil.example/" if malicious else b"/123/principal/"))
        if request.url.path.endswith("/principal/"):
            return httpx.Response(207, content=HOME.replace(b"/123/calendars/", home_href) if home_href else HOME)
        return httpx.Response(207, content=LISTING)

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(ic, "_client", lambda username, password: httpx.Client(
        transport=transport,
        headers={"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()},
        follow_redirects=False,
    ))
    return requests


def test_connect_read_and_disconnect_for_entire_installation(tmp_path, monkeypatch):
    requests = fake_client(monkeypatch)
    assert ic.status(tmp_path)["state"] == "not_connected"
    connected = ic.connect("person@icloud.com", "app-secret", root=tmp_path)
    assert connected == {"state": "connected", "account": "person@icloud.com", "calendars": 1}
    secret_file = tmp_path / "icloud-calendar" / "credentials.json"
    assert secret_file.stat().st_mode & 0o777 == 0o600
    assert "app-secret" not in str(ic.status(tmp_path))
    result = ic.list_events(datetime(2026, 9, 23, tzinfo=timezone.utc), datetime(2026, 9, 24, tzinfo=timezone.utc), root=tmp_path)
    assert result["events"][0]["title"] == "Встреча с клиентом"
    assert result["events"][0]["calendar"] == "Работа"
    assert requests[-1].method == "REPORT"
    ic.disconnect(root=tmp_path)
    with pytest.raises(ic.ICloudCalendarError, match="не подключён"):
        ic.list_events(datetime(2026, 9, 23, tzinfo=timezone.utc), datetime(2026, 9, 24, tzinfo=timezone.utc), root=tmp_path)


def test_bad_password_does_not_replace_live_credentials(tmp_path, monkeypatch):
    fake_client(monkeypatch)
    ic.connect("person@icloud.com", "old-secret", root=tmp_path)
    fake_client(monkeypatch, fail_status=401)
    with pytest.raises(ic.ICloudCalendarError) as error:
        ic.connect("person@icloud.com", "new-secret", root=tmp_path)
    assert error.value.code == "auth_error"
    assert "old-secret" in (tmp_path / "icloud-calendar" / "credentials.json").read_text()
    assert "new-secret" not in (tmp_path / "icloud-calendar" / "credentials.json").read_text()


def test_discovery_rejects_external_href_and_does_not_save(tmp_path, monkeypatch):
    requests = fake_client(monkeypatch, malicious=True)
    with pytest.raises(ic.ICloudCalendarError) as error:
        ic.connect("person@icloud.com", "secret", root=tmp_path)
    assert error.value.code == "unsafe_response"
    assert len(requests) == 1
    assert not (tmp_path / "icloud-calendar" / "credentials.json").exists()


def test_discovery_accepts_icloud_home_with_explicit_https_port(tmp_path, monkeypatch):
    requests = fake_client(monkeypatch, home_href=b"https://p184-caldav.icloud.com:443/123/calendars/")
    connected = ic.connect("person@icloud.com", "app-secret", root=tmp_path)
    assert connected["calendars"] == 1
    assert requests[-1].url.host == "p184-caldav.icloud.com"
    assert ic._safe_url("https://p184-caldav.icloud.com:443/123/calendars/", ic.BASE_URL).startswith(
        "https://p184-caldav.icloud.com:443/"
    )


@pytest.mark.parametrize("href", [
    "https://p184-caldav.icloud.com:444/123/calendars/",
    "https://p184-caldav.icloud.com:invalid/123/calendars/",
])
def test_discovery_rejects_non_https_ports(href):
    with pytest.raises(ic.ICloudCalendarError) as error:
        ic._safe_url(href, ic.BASE_URL)
    assert error.value.code == "unsafe_response"


def test_calendar_parser_handles_all_day_and_folded_lines():
    text = "BEGIN:VEVENT\nUID:one\nSUMMARY:Очень длинное \n название\nDTSTART;VALUE=DATE:20260923\nDTEND;VALUE=DATE:20260924\nEND:VEVENT"
    events = ic._events(text, "Личное", datetime(2026, 9, 23, tzinfo=timezone.utc), datetime(2026, 9, 24, tzinfo=timezone.utc))
    assert len(events) == 1
    assert events[0]["all_day"] is True
    assert events[0]["title"] == "Очень длинное название"


def test_all_day_uses_owner_calendar_dates_not_utc_midnight():
    text = "BEGIN:VEVENT\nUID:old\nDTSTART;VALUE=DATE:20260922\nDTEND;VALUE=DATE:20260923\nEND:VEVENT"
    zone = ZoneInfo("Europe/Moscow")
    events = ic._events(text, "Личное", datetime(2026, 9, 23, tzinfo=zone), datetime(2026, 9, 24, tzinfo=zone))
    assert events == []


def test_browser_routes_share_one_installation_and_never_return_password(tmp_path, monkeypatch):
    from korra_cli.web_routers.connections import router

    fake_client(monkeypatch)
    monkeypatch.setattr(ic, "get_default_hermes_root", lambda: tmp_path)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/api/connections/icloud-calendar").json()["state"] == "not_connected"
    response = client.post("/api/connections/icloud-calendar", json={"username": "person@icloud.com", "app_password": "secret"})
    assert response.status_code == 200
    assert "secret" not in response.text
    assert client.get("/api/connections/icloud-calendar").json()["state"] == "connected"
    assert client.get("/api/dashboard/icloud-calendar").status_code == 200
    assert client.get("/api/dashboard/icloud-calendar?refresh=1").status_code == 200
    assert client.delete("/api/connections/icloud-calendar").json()["state"] == "not_connected"
    assert not (tmp_path / "icloud-calendar" / "credentials.json").exists()


def test_agent_tool_reads_same_connection_and_disconnect_revokes_it(tmp_path, monkeypatch):
    from tools import icloud_calendar_tool

    fake_client(monkeypatch)
    monkeypatch.setattr(ic, "get_default_hermes_root", lambda: tmp_path)
    ic.connect("person@icloud.com", "secret", root=tmp_path)
    response = json.loads(icloud_calendar_tool._handle({"date": "2026-09-23"}))
    assert response["ok"] is True
    assert response["events"][0]["title"] == "Встреча с клиентом"
    assert "secret" not in str(response)
    ic.disconnect(root=tmp_path)
    denied = json.loads(icloud_calendar_tool._handle({"date": "2026-09-23"}))
    assert denied["error"] == "not_connected"


def test_dashboard_reuses_feed_and_refreshes_expired_data_without_blocking(tmp_path, monkeypatch):
    ic.reset_dashboard_cache()
    fake_client(monkeypatch)
    ic.connect("person@icloud.com", "secret", root=tmp_path)
    started = threading.Event()
    release = threading.Event()
    reads = []

    def read(_client, _start, _end):
        reads.append(len(reads) + 1)
        if len(reads) == 2:
            started.set()
            assert release.wait(3)
        return [{"id": str(reads[-1]), "title": "Встреча", "start": "2026-09-23T09:00:00Z",
                 "end": None, "all_day": False, "calendar": "Работа", "location": ""}]

    monkeypatch.setattr(ic, "_read", read)
    first = ic.dashboard_feed(root=tmp_path)
    assert first["stale"] is False
    assert ic.dashboard_feed(root=tmp_path)["events"] == first["events"]
    assert reads == [1]

    cache = ic._dashboard_cache[tmp_path]
    cache.fetched_mono -= ic.DASHBOARD_FRESH_SECONDS + 1
    cache.attempted_mono -= ic.DASHBOARD_FRESH_SECONDS + 1
    began = time.perf_counter()
    stale = ic.dashboard_feed(root=tmp_path)
    assert time.perf_counter() - began < 0.5
    assert stale["stale"] is True and stale["refreshing"] is True
    assert stale["events"] == first["events"]
    assert started.wait(2)
    release.set()
    for _ in range(100):
        if ic._dashboard_cache[tmp_path].payload["events"][0]["id"] == "2":
            break
        time.sleep(0.01)
    fresh = ic.dashboard_feed(root=tmp_path)
    assert fresh["stale"] is False
    assert fresh["events"][0]["id"] == "2"
    assert reads == [1, 2]


def test_dashboard_drops_cached_events_when_credential_is_replaced(tmp_path, monkeypatch):
    ic.reset_dashboard_cache()
    fake_client(monkeypatch)
    ic.connect("person@icloud.com", "old-secret", root=tmp_path)
    reads = []
    monkeypatch.setattr(ic, "_read", lambda *_: reads.append(1) or [])
    ic.dashboard_feed(root=tmp_path)
    assert len(reads) == 1
    ic.connect("person@icloud.com", "new-secret", root=tmp_path)
    ic.dashboard_feed(root=tmp_path)
    assert len(reads) == 2
    ic.disconnect(root=tmp_path)
    assert ic.dashboard_feed(root=tmp_path)["state"] == "not_connected"
    assert tmp_path not in ic._dashboard_cache


def test_dashboard_revoked_password_clears_stale_events(tmp_path, monkeypatch):
    ic.reset_dashboard_cache()
    fake_client(monkeypatch)
    ic.connect("person@icloud.com", "secret", root=tmp_path)
    reads = []

    def read(*_args):
        reads.append(1)
        if len(reads) > 1:
            raise ic.ICloudCalendarError("auth_error", "Пароль приложения iCloud не принят.", 424)
        return [{"id": "private-event"}]

    monkeypatch.setattr(ic, "_read", read)
    ic.dashboard_feed(root=tmp_path)
    cache = ic._dashboard_cache[tmp_path]
    cache.fetched_mono -= ic.DASHBOARD_FRESH_SECONDS + 1
    cache.attempted_mono -= ic.DASHBOARD_FRESH_SECONDS + 1
    assert ic.dashboard_feed(root=tmp_path)["stale"] is True
    for _ in range(100):
        if tmp_path in ic._dashboard_auth_errors:
            break
        time.sleep(0.01)
    with pytest.raises(ic.ICloudCalendarError) as error:
        ic.dashboard_feed(root=tmp_path)
    assert error.value.code == "auth_error"
    assert tmp_path not in ic._dashboard_cache


def test_caldav_redirects_stay_on_icloud(monkeypatch):
    urls = []
    def respond(request):
        urls.append(str(request.url))
        if len(urls) == 1:
            return httpx.Response(302, headers={"Location": "https://p01-caldav.icloud.com/123/principal/"})
        return httpx.Response(207, content=PRINCIPAL)
    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False) as client:
        assert ic._request(client, "PROPFIND", ic.BASE_URL, "<x/>", depth="0") is not None
    assert len(urls) == 2
    assert urls[1].startswith("https://p01-caldav.icloud.com/")

    urls.clear()
    with httpx.Client(transport=httpx.MockTransport(lambda request: (urls.append(str(request.url)), httpx.Response(302, headers={"Location": "https://evil.example/"}))[1]), follow_redirects=False) as client:
        with pytest.raises(ic.ICloudCalendarError, match="недопустимый адрес"):
            ic._request(client, "PROPFIND", ic.BASE_URL, "<x/>", depth="0")
    assert len(urls) == 1


def test_agent_tool_is_owner_only_like_google_calendar(tmp_path, monkeypatch):
    # Review R2 applies to every owner service: a visitor of a public bot
    # must not read the owner's iCloud Calendar.
    from gateway.session_context import clear_session_vars, set_session_vars
    from tools import icloud_calendar_tool

    requests = fake_client(monkeypatch)
    monkeypatch.setattr(ic, "get_default_hermes_root", lambda: tmp_path)
    ic.connect("person@icloud.com", "secret", root=tmp_path)
    before = len(requests)
    tokens = set_session_vars(platform="telegram", user_id="outsider", chat_id="outsider",
                              chat_type="dm", profile="assistant", credential_management_authorized=False)
    try:
        denied = json.loads(icloud_calendar_tool._handle({"date": "2026-09-23"}))
        offered = icloud_calendar_tool._calendar_available()
    finally:
        clear_session_vars(tokens)
    assert denied["error"] == "owner_only" and "events" not in denied
    assert offered is False
    assert len(requests) == before  # Apple is not even asked


@pytest.fixture
def writable_calendar(tmp_path, monkeypatch):
    """Stateful CalDAV wire peer; real discovery, serialization and handlers."""
    from korra_cli import google_calendar

    state = {"requests": [], "objects": {}, "mode": "normal", "listing": LISTING}

    def respond(request):
        state["requests"].append(request)
        path = request.url.path
        if request.method == "PUT":
            assert request.headers["if-none-match"] == "*"
            assert request.headers["content-type"].startswith("text/calendar")
            if state["mode"] == "forbidden":
                return httpx.Response(403)
            if state["mode"] == "unsafe_redirect":
                return httpx.Response(307, headers={"Location": "https://evil.example/event.ics"})
            if path in state["objects"]:
                return httpx.Response(412)
            if state["mode"] != "timeout_missing":
                state["objects"][path] = request.content
            if state["mode"] in {"timeout_stored", "timeout_missing"}:
                raise httpx.ReadTimeout("synthetic lost answer", request=request)
            return httpx.Response(201)
        if request.method == "GET":
            if state["mode"] == "unavailable_readback":
                return httpx.Response(503)
            raw = state["objects"].get(path)
            return httpx.Response(200, content=raw) if raw is not None else httpx.Response(404)
        if request.method == "REPORT":
            root = ic.ET.Element("{DAV:}multistatus")
            for path, raw in state["objects"].items():
                if path.startswith(request.url.path):
                    node = ic.ET.SubElement(root, "{urn:ietf:params:xml:ns:caldav}calendar-data")
                    node.text = raw.decode()
            return httpx.Response(207, content=ic.ET.tostring(root))
        if path == "/":
            return httpx.Response(207, content=PRINCIPAL)
        if path.endswith("/principal/"):
            return httpx.Response(207, content=HOME)
        return httpx.Response(207, content=state["listing"])

    monkeypatch.setattr(ic, "_client", lambda *_: httpx.Client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(ic, "get_default_hermes_root", lambda: tmp_path)
    monkeypatch.setattr(google_calendar, "owner_timezone", lambda: ZoneInfo("Asia/Novosibirsk"))
    ic.reset_dashboard_cache()
    ic.connect("calendar@example.invalid", "synthetic-app-password", root=tmp_path)
    yield state
    ic.reset_dashboard_cache()


def create_args(**overrides):
    return {"action": "create", "title": "Встреча", "start": "2026-09-29T15:00:00+07:00",
            "end": "2026-09-29T16:00:00+07:00", **overrides}


def test_create_from_agent_is_visible_in_calendar_and_repeat_is_not_a_duplicate(writable_calendar):
    from tools import icloud_calendar_tool as tool

    first = json.loads(tool._handle(create_args()))
    assert first["ok"] and not first["already_existed"]
    assert first["created"]["start"] == "2026-09-29T15:00:00+07:00"
    assert first["created"]["calendar"] == "Работа"
    repeated = json.loads(tool._handle(create_args()))
    assert repeated["ok"] and repeated["already_existed"]
    assert repeated["created"]["id"] == first["created"]["id"]
    assert len(writable_calendar["objects"]) == 1
    listing = json.loads(tool._handle({"date": "2026-09-29"}))  # old action-less calls still work
    assert listing["count"] == 1
    assert listing["events"][0]["title"] == "Встреча"
    assert datetime.fromisoformat(listing["events"][0]["start"]) == datetime.fromisoformat(first["created"]["start"])


def test_cyrillic_and_escaped_text_round_trip_without_calendar_injection(writable_calendar):
    from tools import icloud_calendar_tool as tool

    title = 'Обсуждение проекта; "Весна", ' + "Ю" * 90
    description = "Путь C:\\new\nBEGIN:VEVENT\nATTENDEE:someone@example.invalid\nEND:VEVENT"
    result = json.loads(tool._handle(create_args(title=title, description=description, location="Зал, 2; офис")))
    assert result["ok"], result
    assert result["created"]["title"] == title
    assert result["created"]["description"] == description
    raw = next(iter(writable_calendar["objects"].values()))
    assert raw.count(b"\r\nBEGIN:VEVENT\r\n") == 1
    assert b"\r\nATTENDEE:" not in raw
    assert all(len(line) <= 75 for line in raw.split(b"\r\n"))


def test_multiple_calendars_require_selection_and_do_not_guess_by_name(writable_calendar):
    from tools import icloud_calendar_tool as tool

    root = ic._xml(LISTING)
    second = ic._xml(LISTING.replace(b"/work/", b"/personal/"))
    root.append(second.find("{DAV:}response"))  # same display name, distinct IDs
    writable_calendar["listing"] = ic.ET.tostring(root)
    denied = json.loads(tool._handle(create_args()))
    assert denied["error"] == "calendar_selection_required"
    assert not writable_calendar["objects"]
    calendars = json.loads(tool._handle({"action": "calendars"}))["calendars"]
    assert len(calendars) == 2 and calendars[0]["id"] != calendars[1]["id"]
    assert all(set(item) == {"id", "name"} for item in calendars)
    selected = json.loads(tool._handle(create_args(calendar_id=calendars[1]["id"])))
    assert selected["ok"]
    assert all("/personal/" in path for path in writable_calendar["objects"])
    assert json.loads(tool._handle(create_args(calendar_id="https://evil.example/")))["error"] == "calendar_selection_required"
    assert all(request.url.host == "caldav.icloud.com" for request in writable_calendar["requests"])


@pytest.mark.parametrize("mode", ["timeout_stored", "timeout_missing", "unavailable_readback"])
def test_lost_write_answer_is_reconciled_or_reported_unknown_without_duplicate(writable_calendar, mode):
    from tools import icloud_calendar_tool as tool

    writable_calendar["mode"] = mode
    result = json.loads(tool._handle(create_args()))
    if mode == "timeout_stored":
        assert result["ok"] and result["reconciled"]
    else:
        assert not result["ok"] and result["error"] == "create_outcome_unknown"
        assert "action=list" in result["next_step"]
    writable_calendar["mode"] = "normal"
    assert json.loads(tool._handle(create_args()))["ok"]
    assert len(writable_calendar["objects"]) == 1


def test_repeat_does_not_overwrite_a_user_edited_event(writable_calendar):
    from tools import icloud_calendar_tool as tool

    assert json.loads(tool._handle(create_args()))["ok"]
    path, raw = next(iter(writable_calendar["objects"].items()))
    edited = raw.replace("SUMMARY:Встреча".encode(), "SUMMARY:Изменено владельцем".encode())
    writable_calendar["objects"][path] = edited
    result = json.loads(tool._handle(create_args()))
    assert result["error"] == "create_conflict"
    assert writable_calendar["objects"][path] == edited


@pytest.mark.parametrize("mode,error", [("forbidden", "write_forbidden"), ("unsafe_redirect", "create_outcome_unknown")])
def test_write_refusal_and_external_redirect_do_not_switch_destination(writable_calendar, mode, error):
    from tools import icloud_calendar_tool as tool

    writable_calendar["mode"] = mode
    result = json.loads(tool._handle(create_args()))
    assert result["error"] == error
    assert not writable_calendar["objects"]
    assert all(request.url.host == "caldav.icloud.com" for request in writable_calendar["requests"])


@pytest.mark.parametrize("owner,live,error", [(False, True, "owner_only"), (True, False, "owner_confirmation_required")])
def test_create_is_denied_before_network_for_visitor_or_background(writable_calendar, monkeypatch, owner, live, error):
    from gateway.principal import Principal
    from tools import icloud_calendar_tool as tool

    monkeypatch.setattr(tool, "current_principal", lambda: Principal("test", owner=owner, live=live))
    before = len(writable_calendar["requests"])
    result = json.loads(tool._handle(create_args()))
    assert result["error"] == error
    assert len(writable_calendar["requests"]) == before


@pytest.mark.parametrize("fields", [
    {"title": ""}, {"title": "x" * 301}, {"description": "unsafe\x00control"},
    {"start": "2026-09-30T10:00:00Z"}, {"start": "2026-09-29T15:00:00.123+07:00"},
    {"start": "not-a-date"}, {"end": "", "duration_minutes": 0},
])
def test_invalid_create_never_contacts_apple(writable_calendar, fields):
    from tools import icloud_calendar_tool as tool

    before = len(writable_calendar["requests"])
    assert json.loads(tool._handle(create_args(**fields)))["error"] == "invalid_request"
    assert len(writable_calendar["requests"]) == before


def test_create_invalidates_dashboard_and_disconnect_revokes_next_write(writable_calendar, tmp_path):
    from tools import icloud_calendar_tool as tool

    assert ic.dashboard_feed(root=tmp_path)["events"] == []
    assert tmp_path in ic._dashboard_cache
    assert json.loads(tool._handle(create_args()))["ok"]
    assert tmp_path not in ic._dashboard_cache
    ic.disconnect(root=tmp_path)
    before = len(writable_calendar["requests"])
    assert json.loads(tool._handle(create_args()))["error"] == "not_connected"
    assert len(writable_calendar["requests"]) == before


def test_connection_changed_during_discovery_prevents_write(writable_calendar, tmp_path, monkeypatch):
    from tools import icloud_calendar_tool as tool

    discover = ic._calendars

    def disconnect_after_discovery(client):
        calendars = discover(client)
        ic.disconnect(root=tmp_path)
        return calendars

    monkeypatch.setattr(ic, "_calendars", disconnect_after_discovery)
    result = json.loads(tool._handle(create_args()))
    assert result["error"] == "connection_changed"
    assert not any(request.method == "PUT" for request in writable_calendar["requests"])


def test_same_instants_with_different_offsets_are_one_event(writable_calendar):
    from tools import icloud_calendar_tool as tool

    first = json.loads(tool._handle(create_args()))
    repeated = json.loads(tool._handle(create_args(start="2026-09-29T08:00:00Z", end="2026-09-29T09:00:00Z")))
    assert first["ok"] and repeated["ok"] and repeated["already_existed"]
    assert len(writable_calendar["objects"]) == 1
    assert ic._calendar_id("https://p01-caldav.icloud.com:443/123/work/") == ic._calendar_id("https://p01-caldav.icloud.com/123/work/")


def test_cancelled_stored_event_is_not_reported_as_created(writable_calendar):
    from tools import icloud_calendar_tool as tool

    assert json.loads(tool._handle(create_args()))["ok"]
    path, raw = next(iter(writable_calendar["objects"].items()))
    cancelled = raw.replace(b"END:VEVENT", b"STATUS:CANCELLED\r\nEND:VEVENT")
    writable_calendar["objects"][path] = cancelled
    assert json.loads(tool._handle(create_args()))["error"] == "create_conflict"
    assert writable_calendar["objects"][path] == cancelled
