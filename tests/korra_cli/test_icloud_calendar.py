"""CalDAV protocol and credential lifecycle without contacting Apple."""

from __future__ import annotations

import base64
import json
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


def fake_client(monkeypatch, *, fail_status=0, malicious=False):
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
            return httpx.Response(207, content=HOME)
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
