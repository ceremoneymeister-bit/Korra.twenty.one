"""Contract «подключение = инструмент агента» for Google Calendar (0.21.13).

Everything here runs on a fake Calendar API and fake grants: no OAuth, no
request ever leaves the process.  The grant is always resolved through the
real ``korra_cli.google_workspace`` code (sharing policy, locks, scope
contract), because that resolution *is* the contract under test.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from korra_cli import dashboard_calendar
from korra_cli import google_calendar
from korra_cli import google_workspace as google
from korra_cli.google_workspace_scopes import (
    TOKEN_REQUESTED_SCOPES_KEY,
    TOKEN_SERVICES_KEY,
    scopes_for_services,
)
from utils import atomic_json_write


ZONE = ZoneInfo("Asia/Novosibirsk")
FAR_FUTURE = 1893456000  # 2030-01-01, keeps google-auth away from refresh


# ---------------------------------------------------------------------------
# Fixtures: one installation, an operator app, fake grants, fake Google
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _operator_app_seam(monkeypatch):
    monkeypatch.setenv("KORRA_GOOGLE_OAUTH_CLIENT_PATH", "")
    monkeypatch.setattr(google, "_is_exact_read_only_mount", lambda _path: True)
    if os.geteuid() != 0:
        monkeypatch.setattr(google, "_operator_app_file_is_safe", lambda _stat: True)
    monkeypatch.setattr(google_calendar, "owner_timezone", lambda: ZONE)
    dashboard_calendar.reset_cache()
    yield
    dashboard_calendar.reset_cache()


def _write_app(root: Path) -> None:
    directory = google.installation_google_dir(root)
    directory.mkdir(parents=True, mode=0o750)
    path = directory / "oauth_client.json"
    path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "shared-client.apps.googleusercontent.com",
                    "client_secret": "operator-only-secret",
                    "auth_uri": google.AUTHORIZATION_ENDPOINT,
                    "token_uri": google.TOKEN_ENDPOINT,
                    "redirect_uris": ["http://localhost"],
                }
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o640)
    if os.geteuid() == 0:
        os.chown(directory, 0, os.getegid())
        os.chown(path, 0, os.getegid())
    os.environ["KORRA_GOOGLE_OAUTH_CLIENT_PATH"] = str(path)


def _write_token(home: Path, services: tuple[str, ...], *, access: str) -> None:
    scopes = scopes_for_services(services)
    directory = google.profile_google_dir(home)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    atomic_json_write(
        google.token_path(home),
        {
            "version": 1,
            "type": "authorized_user",
            "token": access,
            "refresh_token": f"refresh-for-{access}",
            "token_uri": google.TOKEN_ENDPOINT,
            "scopes": scopes,
            TOKEN_SERVICES_KEY: list(services),
            TOKEN_REQUESTED_SCOPES_KEY: scopes,
            "expires_at": FAR_FUTURE,
        },
        mode=0o600,
    )


@pytest.fixture
def install(tmp_path, monkeypatch):
    import korra_constants

    root = tmp_path / "install"
    root.mkdir()
    for name in ("assistant", "designer", "mentor"):
        (root / "profiles" / name).mkdir(parents=True)
    monkeypatch.setattr(google, "get_default_hermes_root", lambda: root)
    monkeypatch.setattr(korra_constants, "get_default_hermes_root", lambda: root)
    _write_app(root)
    return root


class FakeCalendarApi:
    """Answers like the Calendar v3 events endpoint, per access token."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.calendars: dict[str, list[dict]] = {}
        self.fail_with: Exception | None = None

    def __call__(self, request):
        parsed = urllib.parse.urlparse(request.full_url)
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        call = {
            "method": request.get_method(),
            "path": parsed.path,
            "query": dict(urllib.parse.parse_qsl(parsed.query)),
            "token": token,
        }
        if request.data:
            call["body"] = json.loads(request.data.decode("utf-8"))
        self.calls.append(call)
        if self.fail_with is not None:
            raise self.fail_with
        if request.get_method() == "POST":
            body = call["body"]
            created = {
                "id": "new-1",
                "summary": body["summary"],
                "start": body["start"],
                "end": body["end"],
                "htmlLink": "https://calendar.google.com/event?eid=new-1",
            }
            self.calendars.setdefault(token, []).append(created)
            return created
        return {
            "summary": f"owner-of-{token}@example.com",
            "timeZone": "Asia/Novosibirsk",
            "items": list(self.calendars.get(token, [])),
        }


@pytest.fixture
def fake_api(monkeypatch):
    api = FakeCalendarApi()
    monkeypatch.setattr(google_calendar, "_http_json", api)
    return api


def _event(event_id: str, title: str, start: str, end: str, **extra) -> dict:
    return {"id": event_id, "summary": title, "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


def _tool(monkeypatch, home: Path, args: dict) -> dict:
    from tools import google_calendar_tool

    monkeypatch.setattr(google_calendar_tool, "get_hermes_home", lambda: home)
    return json.loads(google_calendar_tool._handle(args))


# ---------------------------------------------------------------------------
# B3: connection = agent tool, immediately, and revocation = no access
# ---------------------------------------------------------------------------


def test_connect_in_cabinet_is_usable_by_the_agent_on_the_next_call(install, fake_api, monkeypatch):
    assistant = install / "profiles" / "assistant"
    before = _tool(monkeypatch, assistant, {"action": "list", "date": "tomorrow"})
    assert before["ok"] is False and before["error"] == "not_connected"
    assert "Настройки → Сервисы" in before["next_step"]
    assert fake_api.calls == []  # no grant, no request to Google

    # The owner completes the existing OAuth flow in the cabinet.
    flow = google.start("calendar,drive", profile_home=assistant)
    state = urllib.parse.parse_qs(urllib.parse.urlparse(flow["authorization_url"]).query)["state"][0]
    scopes = scopes_for_services(("calendar", "drive"))
    google.complete(
        f"http://localhost/?state={state}&code=one-time",
        profile_home=assistant,
        exchange=lambda *_: {
            "access_token": "tok-assistant",
            "refresh_token": "refresh-assistant",
            "expires_in": 3600,
            "scope": " ".join(scopes),
        },
    )
    tomorrow = datetime.now(ZONE).date() + timedelta(days=1)
    fake_api.calendars["tok-assistant"] = [
        _event("e1", "Созвон с поставщиком", f"{tomorrow}T10:00:00+07:00", f"{tomorrow}T11:00:00+07:00"),
    ]

    after = _tool(monkeypatch, assistant, {"action": "list", "date": "tomorrow"})
    assert after["ok"] is True
    assert [event["title"] for event in after["events"]] == ["Созвон с поставщиком"]
    assert after["timezone"] == "Asia/Novosibirsk"
    request = fake_api.calls[-1]
    assert request["path"] == "/calendar/v3/calendars/primary/events"
    assert request["token"] == "tok-assistant"
    # The day is the owner's day, not UTC's.
    assert request["query"]["timeMin"] == (
        datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=ZONE)
        .astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    assert request["query"]["singleEvents"] == "true"


def test_shared_grant_reaches_curated_agents_and_detach_or_revoke_cuts_it(install, fake_api, monkeypatch):
    assistant = install / "profiles" / "assistant"
    designer = install / "profiles" / "designer"
    mentor = install / "profiles" / "mentor"
    _write_token(assistant, ("calendar",), access="tok-assistant")
    fake_api.calendars["tok-assistant"] = [
        _event("e1", "Планёрка", "2026-09-24T09:00:00+07:00", "2026-09-24T09:30:00+07:00"),
    ]

    assert _tool(monkeypatch, designer, {"action": "list", "date": "2026-09-24"})["error"] == "not_connected"

    google.configure_sharing(source_profile="assistant", profiles=["designer", "mentor"])
    for consumer in (designer, mentor):
        answer = _tool(monkeypatch, consumer, {"action": "list", "date": "2026-09-24"})
        assert answer["ok"] is True, answer
        assert answer["events"][0]["title"] == "Планёрка"
        # One token, the source's: nothing was copied into the consumer.
        assert fake_api.calls[-1]["token"] == "tok-assistant"
        assert not google.token_path(consumer).exists()

    # Detaching one consumer cuts only that consumer, on the very next call.
    assert google.revoke(profile_home=designer)["status"] == "detached"
    assert _tool(monkeypatch, designer, {"action": "list", "date": "2026-09-24"})["error"] == "not_connected"
    assert _tool(monkeypatch, mentor, {"action": "list", "date": "2026-09-24"})["ok"] is True

    # Full revoke of the source (after the owner removes sharing) cuts everyone.
    google.configure_sharing(source_profile="assistant", profiles=[])
    google.revoke(profile_home=assistant, remote_revoke=lambda _value: None)
    calls_before = len(fake_api.calls)
    for home in (assistant, designer, mentor):
        assert _tool(monkeypatch, home, {"action": "list"})["error"] == "not_connected"
    assert len(fake_api.calls) == calls_before


def test_curated_profiles_get_the_tool_without_any_config(monkeypatch, tmp_path):
    """A ready-made agent with an explicit toolset list (even without terminal)."""
    from korra_cli.tools_config import _get_platform_tools

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "probe"))
    curated = ["clarify", "memory", "session_search", "skills", "todo", "web"]
    config = {"platform_toolsets": {p: list(curated) for p in ("telegram", "api_server", "cron", "cli")}}
    for platform in ("telegram", "api_server", "cron", "cli"):
        assert "google_calendar" in _get_platform_tools(config, platform), platform
        assert "google_calendar" in _get_platform_tools({}, platform), platform
        # The owner-only credential tool stays opt-in.
        assert "google_workspace" not in _get_platform_tools(config, platform)


def test_tool_schema_depends_only_on_the_installation_app(install, monkeypatch):
    from tools import google_calendar_tool  # noqa: F401 — registers the tool
    from tools.registry import invalidate_check_fn_cache, registry

    invalidate_check_fn_cache()
    names = [d["function"]["name"] for d in registry.get_definitions({"google_calendar"}, quiet=True)]
    assert names == ["google_calendar"]  # present with no grant anywhere

    monkeypatch.setenv("KORRA_GOOGLE_OAUTH_CLIENT_PATH", str(install / "missing.json"))
    invalidate_check_fn_cache()
    assert registry.get_definitions({"google_calendar"}, quiet=True) == []
    invalidate_check_fn_cache()


def test_tool_reports_missing_calendar_scope_and_revoked_google_access(install, fake_api, monkeypatch):
    assistant = install / "profiles" / "assistant"
    _write_token(assistant, ("drive",), access="tok-drive-only")
    answer = _tool(monkeypatch, assistant, {"action": "list"})
    assert answer["error"] == "calendar_not_selected"
    assert "reconnect" in answer["next_step"]
    assert fake_api.calls == []

    google.revoke(profile_home=assistant, remote_revoke=lambda _value: None)
    _write_token(assistant, ("calendar",), access="tok-revoked-at-google")
    import urllib.error

    fake_api.fail_with = urllib.error.HTTPError(
        google_calendar.EVENTS_ENDPOINT, 401, "Unauthorized", {}, None
    )
    answer = _tool(monkeypatch, assistant, {"action": "list"})
    assert answer["error"] == "reauthorization_required"


def test_expired_token_that_cannot_refresh_asks_for_reconnect(install, fake_api, monkeypatch):
    from google.oauth2 import credentials as google_credentials

    assistant = install / "profiles" / "assistant"
    _write_token(assistant, ("calendar",), access="tok-old")
    payload = json.loads(google.token_path(assistant).read_text(encoding="utf-8"))
    payload["expires_at"] = int(time.time()) - 60
    google.token_path(assistant).write_text(json.dumps(payload), encoding="utf-8")

    def refuse(self, _request):
        raise RuntimeError("invalid_grant")

    monkeypatch.setattr(google_credentials.Credentials, "refresh", refuse)
    answer = _tool(monkeypatch, assistant, {"action": "list"})
    assert answer["error"] == "reauthorization_required"
    assert fake_api.calls == []


def test_create_adds_one_event_without_inviting_anyone(install, fake_api, monkeypatch):
    assistant = install / "profiles" / "assistant"
    _write_token(assistant, ("calendar",), access="tok-assistant")
    answer = _tool(
        monkeypatch,
        assistant,
        {"action": "create", "title": "Встреча с юристом", "start": "2026-09-25T15:00:00", "duration_minutes": 30},
    )
    assert answer["ok"] is True
    call = fake_api.calls[-1]
    assert call["method"] == "POST" and call["query"] == {"sendUpdates": "none"}
    assert call["body"]["start"] == {"dateTime": "2026-09-25T15:00:00+07:00"}
    assert call["body"]["end"] == {"dateTime": "2026-09-25T15:30:00+07:00"}
    assert "attendees" not in call["body"]


def test_tool_rejects_ranges_it_cannot_answer(install, fake_api, monkeypatch):
    assistant = install / "profiles" / "assistant"
    _write_token(assistant, ("calendar",), access="tok-assistant")
    assert _tool(monkeypatch, assistant, {"action": "list", "days": 90})["error"] == "invalid_request"
    assert _tool(monkeypatch, assistant, {"action": "list", "date": "someday"})["error"] == "invalid_request"
    assert _tool(monkeypatch, assistant, {"action": "list", "start": "2026-09-24T10:00"})["error"] == "invalid_request"
    assert fake_api.calls == []


def test_event_normalization_keeps_only_what_the_owner_sees():
    raw = {
        "id": "x",
        "summary": " Обед ",
        "description": "private notes",
        "attendees": [{"email": "guest@example.com"}],
        "location": "Кафе",
        "start": {"date": "2026-09-24"},
        "end": {"date": "2026-09-25"},
        "htmlLink": "https://calendar.google.com/event?eid=x",
        "hangoutLink": "javascript:alert(1)",
    }
    event = google_calendar.normalize_event(raw)
    assert event == {
        "id": "x",
        "title": "Обед",
        "start": "2026-09-24",
        "end": "2026-09-25",
        "all_day": True,
        "location": "Кафе",
        "url": "https://calendar.google.com/event?eid=x",
    }
    assert google_calendar.normalize_event({**raw, "status": "cancelled"}) is None


# ---------------------------------------------------------------------------
# Installation overview (the «Сервисы» screen)
# ---------------------------------------------------------------------------


def test_overview_maps_every_agent_without_creating_state(install):
    assistant = install / "profiles" / "assistant"
    _write_token(assistant, ("calendar", "drive"), access="tok-assistant")
    google.configure_sharing(source_profile="assistant", profiles=["designer"])

    snapshot = google.overview()

    rows = {row["profile"]: row for row in snapshot["profiles"]}
    assert snapshot["app"] == {"configured": True}
    assert rows["assistant"]["access"] == "own"
    assert rows["assistant"]["services"] == ["calendar", "drive"]
    assert rows["assistant"]["shared_with"] == ["designer"]
    assert rows["designer"] == {
        "profile": "designer",
        "access": "shared",
        "state": "connected",
        "services": ["calendar", "drive"],
        "pending": False,
        "shared_from": "assistant",
    }
    assert rows["mentor"]["access"] == "none" and rows["mentor"]["services"] == []
    assert rows["default"]["access"] == "none"
    # Looking is not touching: untouched profiles get no Google directory.
    assert not (install / "profiles" / "mentor" / "google-workspace").exists()
    assert "token" not in json.dumps(snapshot).replace("token_", "")


def test_overview_ignores_stale_sharing_for_a_profile_with_its_own_grant(install):
    _write_token(install / "profiles" / "assistant", ("calendar",), access="tok-a")
    google.configure_sharing(source_profile="assistant", profiles=["designer"])
    _write_token(install / "profiles" / "designer", ("drive",), access="tok-d")

    rows = {row["profile"]: row for row in google.overview()["profiles"]}
    assert rows["designer"]["access"] == "own"
    assert rows["designer"]["services"] == ["drive"]
    assert rows["assistant"]["shared_with"] == []


def test_overview_reports_an_open_authorization_flow(install):
    google.start("calendar", profile_home=install / "profiles" / "mentor")
    rows = {row["profile"]: row for row in google.overview()["profiles"]}
    assert rows["mentor"]["pending"] is True
    assert rows["mentor"]["access"] == "none"


def test_connections_route_adds_labels_and_what_each_agent_can_use(install, monkeypatch):
    from korra_cli.web_routers import connections

    (install / "profiles" / "designer" / "profile.yaml").write_text("display_name: Дизайнер\n", encoding="utf-8")
    skill = install / "skills" / "productivity" / "google-workspace"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: google-workspace\n---\n", encoding="utf-8")

    payload = asyncio.run(connections.connections_overview())
    rows = {row["profile"]: row for row in payload["google"]["profiles"]}
    assert rows["designer"]["label"] == "Дизайнер"
    assert rows["default"]["tools"] == {"calendar": True, "workspace_skill": True}
    assert rows["designer"]["tools"] == {"calendar": True, "workspace_skill": False}


# ---------------------------------------------------------------------------
# Dashboard feed
# ---------------------------------------------------------------------------


def _overview(rows, configured=True):
    return {"app": {"configured": configured}, "profiles": rows}


def test_source_prefers_the_main_agent_and_explains_every_gap():
    pick = dashboard_calendar.pick_source
    own = {"profile": "assistant", "access": "own", "state": "connected", "services": ["calendar"]}
    assert pick(_overview([own], configured=False))["state"] == "app_unavailable"
    assert pick(_overview([{"profile": "default", "access": "none", "state": "not_connected", "services": []}])) == {
        "state": "not_connected",
        "profile": "default",
        "action": "connect",
    }
    assert pick(_overview([{"profile": "default", "access": "none", "services": []}, own]))["profile"] == "assistant"
    shared_main = {"profile": "default", "access": "shared", "state": "connected", "services": ["calendar"]}
    assert pick(_overview([shared_main, own]))["profile"] == "default"
    drive_only = {"profile": "default", "access": "own", "state": "connected", "services": ["drive"]}
    assert pick(_overview([drive_only]))["state"] == "calendar_not_selected"
    borrowed_drive = {"profile": "default", "access": "shared", "shared_from": "assistant",
                      "state": "connected", "services": ["drive"]}
    assert pick(_overview([borrowed_drive]))["profile"] == "assistant"  # fix it at the source
    broken = {"profile": "default", "access": "own", "state": "reauthorization_required", "services": []}
    assert pick(_overview([broken])) == {
        "state": "reauthorization_required",
        "profile": "default",
        "action": "reconnect",
    }


def _jobs(now: datetime) -> list[dict]:
    tomorrow_10 = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    return [
        {
            "id": "remind",
            "name": "Позвонить бухгалтеру",
            "profile": "default",
            "enabled": True,
            "state": "scheduled",
            "schedule": {"kind": "once", "run_at": tomorrow_10.isoformat()},
            "next_run_at": tomorrow_10.isoformat(),
            "schedule_display": "once",
        },
        {
            "id": "brief",
            "name": "Утренняя сводка",
            "profile": "assistant",
            "enabled": True,
            "state": "scheduled",
            "schedule": {"kind": "cron", "expr": "0 8 * * *"},
            "next_run_at": (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0).isoformat(),
            "schedule_display": "0 8 * * *",
        },
        {
            "id": "poll",
            "name": "Проверка почты",
            "profile": "assistant",
            "enabled": True,
            "state": "scheduled",
            "schedule": {"kind": "interval", "minutes": 30},
            "next_run_at": (now + timedelta(minutes=10)).isoformat(),
            "schedule_display": "every 30m",
        },
        {
            "id": "paused",
            "name": "На паузе",
            "profile": "default",
            "enabled": False,
            "state": "paused",
            "schedule": {"kind": "cron", "expr": "0 9 * * *"},
            "next_run_at": (now + timedelta(hours=2)).isoformat(),
        },
    ]


def test_feed_merges_meetings_tasks_and_runs_for_the_owners_week(install, fake_api):
    now = datetime(2026, 9, 23, 7, 30, tzinfo=ZONE)
    _write_token(install, ("calendar",), access="tok-main")
    fake_api.calendars["tok-main"] = [
        _event("m1", "Созвон", "2026-09-24T12:00:00+07:00", "2026-09-24T12:30:00+07:00",
               hangoutLink="https://meet.google.com/abc-defg-hij"),
        {"id": "d1", "summary": "Отпуск Ани", "start": {"date": "2026-09-23"}, "end": {"date": "2026-09-24"}},
    ]
    feed = dashboard_calendar.build_feed(jobs_loader=lambda: _jobs(now), now=now, zone=ZONE)

    assert feed["timezone"] == "Asia/Novosibirsk"
    assert feed["window"] == {
        "start": "2026-09-23T00:00:00+07:00",
        "end": "2026-09-30T00:00:00+07:00",
        "days": 7,
    }
    assert feed["google"]["state"] == "connected"
    assert feed["google"]["source_profile"] == "default"
    assert feed["google"]["account"] == "owner-of-tok-main@example.com"
    kinds = [(item["kind"], item["title"]) for item in feed["items"]]
    assert kinds[0] == ("event", "Отпуск Ани")  # all-day first on its day
    assert ("task", "Позвонить бухгалтеру") in kinds
    assert kinds.count(("agent_run", "Утренняя сводка")) == 6  # 24..29 at 08:00
    poll = [item for item in feed["items"] if item["title"] == "Проверка почты"]
    assert len(poll) == 1 and poll[0]["repeats"] is True and poll[0]["occurrences"] > 14
    assert all(item["title"] != "На паузе" for item in feed["items"])
    meeting = next(item for item in feed["items"] if item["title"] == "Созвон")
    assert meeting["join_url"] == "https://meet.google.com/abc-defg-hij"
    starts = [item["start"] for item in feed["items"] if item["kind"] != "event" or not item["all_day"]]
    parsed = [google_calendar.parse_moment(value, ZONE) for value in starts]
    assert parsed == sorted(parsed)


def test_feed_without_google_still_lists_the_schedule(install, fake_api):
    now = datetime(2026, 9, 23, 7, 30, tzinfo=ZONE)
    feed = dashboard_calendar.build_feed(jobs_loader=lambda: _jobs(now), now=now, zone=ZONE)
    assert feed["google"]["state"] == "not_connected"
    assert feed["google"]["action"] == "connect"
    assert feed["google"]["source_profile"] == "default"
    assert feed["items"] and all(item["kind"] != "event" for item in feed["items"])
    assert fake_api.calls == []


def test_feed_caches_meetings_and_shows_last_known_during_an_outage(install, fake_api):
    now = datetime(2026, 9, 23, 7, 30, tzinfo=ZONE)
    clock = [1000.0]
    _write_token(install, ("calendar",), access="tok-main")
    fake_api.calendars["tok-main"] = [
        _event("m1", "Созвон", "2026-09-24T12:00:00+07:00", "2026-09-24T12:30:00+07:00"),
    ]

    def feed(**kwargs):
        return dashboard_calendar.build_feed(
            jobs_loader=list, now=now, zone=ZONE, monotonic=lambda: clock[0], **kwargs
        )

    first = feed()
    assert len(fake_api.calls) == 1 and first["google"]["stale"] is False
    clock[0] += 60
    feed()
    assert len(fake_api.calls) == 1  # fresh cache
    clock[0] += 5
    feed(refresh=True)
    assert len(fake_api.calls) == 2  # «Обновить» bypasses the fresh window…
    clock[0] += 5
    feed(refresh=True)
    assert len(fake_api.calls) == 2  # …but cannot hammer Google
    clock[0] += 120
    import urllib.error

    fake_api.fail_with = urllib.error.URLError("offline")
    outage = feed()
    assert outage["google"]["stale"] is True
    assert outage["google"]["error"]["code"] == "google_unavailable"
    assert outage["google"]["action"] == "retry"
    assert [item["title"] for item in outage["items"]] == ["Созвон"]

    clock[0] += dashboard_calendar.STALE_LIMIT_SECONDS
    expired = feed()
    assert expired["google"]["state"] == "error" and expired["items"] == []


def test_feed_never_shows_cached_meetings_after_revoke(install, fake_api):
    now = datetime(2026, 9, 23, 7, 30, tzinfo=ZONE)
    _write_token(install, ("calendar",), access="tok-main")
    fake_api.calendars["tok-main"] = [
        _event("m1", "Созвон", "2026-09-24T12:00:00+07:00", "2026-09-24T12:30:00+07:00"),
    ]
    assert dashboard_calendar.build_feed(jobs_loader=list, now=now, zone=ZONE)["items"]

    google.revoke(profile_home=install, remote_revoke=lambda _value: None)
    after = dashboard_calendar.build_feed(jobs_loader=list, now=now, zone=ZONE)
    assert after["google"]["state"] == "not_connected"
    assert after["items"] == []


def test_feed_drops_cache_when_google_rejects_the_grant(install, fake_api):
    import urllib.error

    now = datetime(2026, 9, 23, 7, 30, tzinfo=ZONE)
    clock = [0.0]
    _write_token(install, ("calendar",), access="tok-main")
    fake_api.calendars["tok-main"] = [
        _event("m1", "Созвон", "2026-09-24T12:00:00+07:00", "2026-09-24T12:30:00+07:00"),
    ]
    dashboard_calendar.build_feed(jobs_loader=list, now=now, zone=ZONE, monotonic=lambda: clock[0])
    clock[0] += 500
    fake_api.fail_with = urllib.error.HTTPError(google_calendar.EVENTS_ENDPOINT, 401, "Unauthorized", {}, None)
    feed = dashboard_calendar.build_feed(jobs_loader=list, now=now, zone=ZONE, monotonic=lambda: clock[0])
    assert feed["google"]["state"] == "reauthorization_required"
    assert feed["google"]["action"] == "reconnect"
    assert feed["items"] == []


def test_feed_says_so_when_the_schedule_cannot_be_read(install, fake_api):
    def broken():
        raise OSError("jobs.json unreadable")

    feed = dashboard_calendar.build_feed(jobs_loader=broken, zone=ZONE)
    assert feed["schedule"] == {"state": "error"}


def test_calendar_route_reads_every_profile_schedule(install, fake_api, monkeypatch):
    from korra_cli.web_routers import connections

    seen = []
    monkeypatch.setattr(connections, "_list_cron_jobs_sync", lambda profile: seen.append(profile) or [])
    feed = asyncio.run(connections.dashboard_calendar_feed(refresh=False))
    assert seen == ["all"]
    assert feed["google"]["state"] == "not_connected"
