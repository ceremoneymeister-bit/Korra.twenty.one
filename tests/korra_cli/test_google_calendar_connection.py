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
    """Answers like the Calendar v3 events endpoints, per access token.

    Honors client-supplied event ids like Google: a second insert with the
    same id is 409, and ``GET events/<id>`` finds a stored event.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.calendars: dict[str, list[dict]] = {}
        self.fail_with: Exception | None = None

    def _find(self, token: str, event_id: str) -> dict | None:
        return next((e for e in self.calendars.get(token, []) if e.get("id") == event_id), None)

    def __call__(self, request):
        import urllib.error

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
        base = "/calendar/v3/calendars/primary/events"
        if request.get_method() == "POST":
            body = call["body"]
            event_id = body.get("id") or f"generated-{len(self.calls)}"
            if self._find(token, event_id) is not None:
                raise urllib.error.HTTPError(request.full_url, 409, "duplicate", {}, None)
            created = {
                "id": event_id,
                "summary": body["summary"],
                "start": body["start"],
                "end": body["end"],
                "htmlLink": f"https://calendar.google.com/event?eid={event_id}",
            }
            self.calendars.setdefault(token, []).append(created)
            return created
        if parsed.path.startswith(base + "/"):
            found = self._find(token, urllib.parse.unquote(parsed.path[len(base) + 1:]))
            if found is None:
                raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)
            return found
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


OWNER_CABINET = {"platform": "api_server"}


def _tool(monkeypatch, home: Path, args: dict, session: dict | None = None) -> dict:
    """Call the tool as a turn of ``session`` (default: the owner's cabinet chat)."""
    from gateway.session_context import clear_session_vars, set_session_vars
    from tools import google_calendar_tool

    monkeypatch.setattr(google_calendar_tool, "get_hermes_home", lambda: home)
    bound = dict(OWNER_CABINET if session is None else session)
    bound.setdefault("cron_session", "")
    tokens = set_session_vars(**bound)
    try:
        return json.loads(google_calendar_tool._handle(args))
    finally:
        clear_session_vars(tokens)


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


def test_restricted_profiles_never_gain_the_tool_silently(monkeypatch, tmp_path):
    """Default lists get the calendar; an explicit list only when it names it."""
    from korra_cli.tools_config import _get_platform_tools

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "probe"))
    curated = ["clarify", "memory", "session_search", "skills", "todo", "web"]
    restricted = {"platform_toolsets": {p: list(curated) for p in ("telegram", "api_server", "cron", "cli")}}
    opted_in = {"platform_toolsets": {p: [*curated, "google_calendar"] for p in ("telegram", "api_server")}}
    for platform in ("telegram", "api_server", "cron", "cli"):
        assert "google_calendar" in _get_platform_tools({}, platform), platform
        assert "google_calendar" not in _get_platform_tools(restricted, platform), platform
        # The owner-only credential tool stays opt-in.
        assert "google_workspace" not in _get_platform_tools(restricted, platform)
    for platform in ("telegram", "api_server"):
        assert "google_calendar" in _get_platform_tools(opted_in, platform), platform
    assert "google_calendar" not in _get_platform_tools({"platform_toolsets": {"telegram": ["web"]}}, "telegram")


def test_tool_schema_needs_the_installation_app_and_an_owner_turn(install, monkeypatch):
    from gateway.session_context import clear_session_vars, set_session_vars
    from tools import google_calendar_tool  # noqa: F401 — registers the tool
    from tools.registry import invalidate_check_fn_cache, registry

    def names(**session) -> list[str]:
        tokens = set_session_vars(cron_session="", **session)
        try:
            return [d["function"]["name"] for d in registry.get_definitions({"google_calendar"}, quiet=True)]
        finally:
            clear_session_vars(tokens)

    invalidate_check_fn_cache()
    # Present for the owner with no grant anywhere: the grant decides the answer.
    assert names(platform="api_server") == ["google_calendar"]
    assert names(platform="telegram", chat_type="dm", user_id="1", owner_principal="live") == ["google_calendar"]
    # Never offered to someone else's turn, and not cached across turns.
    assert names(platform="telegram", chat_type="dm", user_id="2") == []
    assert names(platform="telegram", chat_type="group", user_id="1", owner_principal="live") == []
    assert names(platform="api_server") == ["google_calendar"]

    monkeypatch.setenv("KORRA_GOOGLE_OAUTH_CLIENT_PATH", str(install / "missing.json"))
    assert names(platform="api_server") == []
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


# ---------------------------------------------------------------------------
# R2 (review 23.09): the principal, not only the grant
# ---------------------------------------------------------------------------

OUTSIDER_DM = {"platform": "telegram", "chat_type": "dm", "chat_id": "777", "user_id": "777"}
OWNER_DM = {"platform": "telegram", "chat_type": "dm", "chat_id": "42", "user_id": "42", "owner_principal": "live"}
OWNER_SYSTEM_TURN = {**OWNER_DM, "owner_principal": "delegated"}
OWNER_IN_GROUP = {"platform": "telegram", "chat_type": "group", "chat_id": "-100", "user_id": "42",
                  "owner_principal": "live"}
CREATE = {"action": "create", "title": "Встреча", "start": "2026-09-24T11:00:00+07:00"}
LIST = {"action": "list", "date": "2026-09-24"}


@pytest.fixture
def shared_calendar(install, fake_api):
    home = install / "profiles" / "assistant"
    _write_token(home, ("calendar",), access="tok-owner")
    fake_api.calendars["tok-owner"] = [
        _event("private", "Private owner meeting", "2026-09-24T09:00:00+07:00", "2026-09-24T10:00:00+07:00"),
    ]
    return home


def test_public_bot_visitor_gets_neither_schedule_nor_create(shared_calendar, fake_api, monkeypatch):
    """The review's `public-calendar`, with the safe expected result."""
    read = _tool(monkeypatch, shared_calendar, LIST, OUTSIDER_DM)
    created = _tool(monkeypatch, shared_calendar, CREATE, OUTSIDER_DM)
    assert read["ok"] is False and read["error"] == "owner_only"
    assert created["ok"] is False and created["error"] == "owner_only"
    assert "Private owner meeting" not in json.dumps(read, ensure_ascii=False)
    assert fake_api.calls == []  # the grant was never even used


def test_owner_speaking_in_a_group_is_not_the_owners_private_channel(shared_calendar, fake_api, monkeypatch):
    assert _tool(monkeypatch, shared_calendar, LIST, OWNER_IN_GROUP)["error"] == "owner_only"
    assert fake_api.calls == []


def test_owner_direct_chat_reads_and_creates(shared_calendar, fake_api, monkeypatch):
    read = _tool(monkeypatch, shared_calendar, LIST, OWNER_DM)
    assert [e["title"] for e in read["events"]] == ["Private owner meeting"]
    created = _tool(monkeypatch, shared_calendar, CREATE, OWNER_DM)
    assert created["ok"] is True and created["created"]["title"] == "Встреча"


def test_background_runs_may_read_but_never_create(shared_calendar, fake_api, monkeypatch):
    """R4 (restricted v1): external changes are never started implicitly."""
    from gateway.session_context import reset_background_owner, set_background_owner

    assert _tool(monkeypatch, shared_calendar, LIST, OWNER_SYSTEM_TURN)["ok"] is True
    refused = _tool(monkeypatch, shared_calendar, CREATE, OWNER_SYSTEM_TURN)
    assert refused["error"] == "owner_confirmation_required"

    token = set_background_owner(True)  # an owner-created scheduled job
    try:
        assert _tool(monkeypatch, shared_calendar, LIST, {"cron_session": "1"})["ok"] is True
        assert _tool(monkeypatch, shared_calendar, CREATE, {"cron_session": "1"})["error"] == (
            "owner_confirmation_required"
        )
    finally:
        reset_background_owner(token)

    token = set_background_owner(False)  # scheduled by somebody else
    try:
        assert _tool(monkeypatch, shared_calendar, LIST, {"cron_session": "1"})["error"] == "owner_only"
    finally:
        reset_background_owner(token)
    # A cron context whose scheduler bound no verdict fails closed.
    assert _tool(monkeypatch, shared_calendar, LIST, {"cron_session": "1"})["error"] == "owner_only"

    monkeypatch.setenv("KORRA_KANBAN_TASK", "t_1")  # a board worker on the owner's board
    assert _tool(monkeypatch, shared_calendar, LIST, {})["ok"] is True
    assert _tool(monkeypatch, shared_calendar, CREATE, {})["error"] == "owner_confirmation_required"
    assert [c["method"] for c in fake_api.calls].count("POST") == 0


def test_a_context_that_never_learned_the_speaker_is_not_the_owner(shared_calendar, fake_api, monkeypatch):
    import contextvars

    from gateway.session_context import clear_session_vars, set_session_vars
    from tools import google_calendar_tool

    clear_session_vars(set_session_vars(platform="telegram"))  # this process now serves sessions
    monkeypatch.setattr(google_calendar_tool, "get_hermes_home", lambda: shared_calendar)
    answer = json.loads(contextvars.Context().run(google_calendar_tool._handle, LIST))
    assert answer["error"] == "owner_only"


def test_two_multiplex_profiles_decide_by_their_own_owner_mapping(install, fake_api, monkeypatch):
    """Same person, two profiles in one process: owner of A, a visitor of B."""
    from korra_constants import reset_hermes_home_override, set_hermes_home_override

    a = install / "profiles" / "assistant"
    b = install / "profiles" / "designer"
    _write_token(a, ("calendar",), access="tok-a")
    google.configure_sharing(source_profile="assistant", profiles=["designer"])
    fake_api.calendars["tok-a"] = [
        _event("p", "Owner meeting", "2026-09-24T09:00:00+07:00", "2026-09-24T10:00:00+07:00"),
    ]
    person = {"platform": "telegram", "chat_type": "dm", "chat_id": "42", "user_id": "42"}
    for _ in range(2):  # interleaved turns, nothing cached across them
        token = set_hermes_home_override(str(a))
        try:
            answer_a = _tool(monkeypatch, a, LIST, {**person, "profile": "assistant", "owner_principal": "live"})
        finally:
            reset_hermes_home_override(token)
        token = set_hermes_home_override(str(b))
        try:
            answer_b = _tool(monkeypatch, b, LIST, {**person, "profile": "designer"})
        finally:
            reset_hermes_home_override(token)
        assert answer_a["ok"] is True and answer_a["events"][0]["title"] == "Owner meeting"
        assert answer_b["error"] == "owner_only"


def test_owner_mapping_decides_live_delegated_and_nobody():
    from gateway.credential_management import owner_principal

    config = {"gateway": {"credential_management": {"owners": {"telegram": ["42"]}}}}
    common = {"platform": "telegram", "chat_type": "dm", "internal": False}
    assert owner_principal(config, user_id="42", **common) == "live"
    assert owner_principal(config, user_id="42", **{**common, "internal": True}) == "delegated"
    assert owner_principal(config, user_id="777", **common) == ""
    assert owner_principal(config, user_id="42", **{**common, "chat_type": "group"}) == ""
    assert owner_principal({}, user_id="42", **common) == ""  # no mapping configured: nobody


def test_gateway_binds_the_owner_verdict_for_tools(monkeypatch):
    from types import SimpleNamespace

    from gateway.principal import current_principal
    from gateway.run import GatewayRunner
    from gateway.session_context import clear_session_vars

    runner = GatewayRunner.__new__(GatewayRunner)
    runner.adapters = {}
    from gateway.config import Platform

    source = SimpleNamespace(
        platform=Platform.TELEGRAM, chat_id="42", chat_type="dm", chat_name="",
        thread_id=None, user_id="42", user_id_alt=None, user_name=None, scope_id="", message_id=None,
        profile="", _credential_management_authorized=True, _owner_principal="live",
    )
    tokens = runner._set_session_env(SimpleNamespace(source=source, session_key="k"))
    try:
        assert current_principal().owner and current_principal().live
    finally:
        clear_session_vars(tokens)
    source._owner_principal = ""
    tokens = runner._set_session_env(SimpleNamespace(source=source, session_key="k"))
    try:
        assert current_principal().kind == "outsider"
    finally:
        clear_session_vars(tokens)


def test_revoke_during_a_running_conversation_denies_the_next_call(shared_calendar, fake_api, monkeypatch):
    assert _tool(monkeypatch, shared_calendar, LIST, OWNER_DM)["ok"] is True
    google.revoke(profile_home=shared_calendar, remote_revoke=lambda _value: None)
    after = _tool(monkeypatch, shared_calendar, LIST, OWNER_DM)  # same session, next call
    assert after["error"] == "not_connected"


def test_tool_definitions_are_cached_per_principal(install, monkeypatch):
    import model_tools
    from gateway.session_context import clear_session_vars, set_session_vars
    from tools.registry import invalidate_check_fn_cache

    invalidate_check_fn_cache()
    model_tools._clear_tool_defs_cache()

    def names(**session) -> set[str]:
        tokens = set_session_vars(cron_session="", **session)
        try:
            return {d["function"]["name"] for d in model_tools.get_tool_definitions(
                enabled_toolsets=["google_calendar"], quiet_mode=True)}
        finally:
            clear_session_vars(tokens)

    for _ in range(2):
        assert "google_calendar" in names(platform="api_server")
        assert "google_calendar" not in names(**OUTSIDER_DM)
    model_tools._clear_tool_defs_cache()


# ---------------------------------------------------------------------------
# Scheduled jobs: whose job is it?
# ---------------------------------------------------------------------------


def test_scheduled_job_acts_for_the_owner_only_when_the_owner_created_it():
    from gateway.principal import cron_job_acts_for_owner

    owners = {"gateway": {"credential_management": {"owners": {"telegram": ["42"]}}}}
    assert cron_job_acts_for_owner({"origin": None}, owners) is True  # cabinet / CLI
    assert cron_job_acts_for_owner({"origin": {"platform": "telegram", "user_id": "777", "owner": True}}, {})
    assert not cron_job_acts_for_owner({"origin": {"platform": "telegram", "user_id": "42", "owner": False}}, owners)
    assert cron_job_acts_for_owner({"origin": {"platform": "api_server", "chat_id": "x"}}, {})
    assert not cron_job_acts_for_owner({"origin": {"platform": "webhook", "chat_id": "x"}}, owners)
    # Jobs from before 0.21.13 carry no verdict: only a configured owner counts.
    legacy = {"origin": {"platform": "telegram", "chat_id": "42", "user_id": "42"}}
    assert cron_job_acts_for_owner(legacy, owners) is True
    assert cron_job_acts_for_owner(legacy, {}) is False


def test_job_created_in_chat_records_whether_the_owner_created_it():
    from gateway.session_context import clear_session_vars, set_session_vars
    from tools.cronjob_tools import _origin_from_env

    for session, expected in ((OUTSIDER_DM, False), (OWNER_DM, True)):
        tokens = set_session_vars(cron_session="", **session)
        try:
            assert _origin_from_env()["owner"] is expected
        finally:
            clear_session_vars(tokens)


# ---------------------------------------------------------------------------
# R9 (review 23.09): an unknown create outcome never becomes a duplicate
# ---------------------------------------------------------------------------


def _lost_answer_after(fake_api, *, lose: set[int], lookup_fails: bool = False, store: bool = True):
    """Transport that loses the answers of the given calls (1-based)."""
    def transport(request):
        number = len(fake_api.calls) + 1
        if number in lose:
            if request.get_method() == "POST" and store:
                fake_api(request)
            else:
                fake_api.calls.append({"method": request.get_method(), "lost": True})
            raise TimeoutError("synthetic: answer lost")
        if lookup_fails and request.get_method() == "GET" and "/events/" in request.full_url:
            fake_api.calls.append({"method": "GET", "lost": True})
            raise TimeoutError("synthetic: lookup lost too")
        return fake_api(request)
    return transport


def test_lost_answer_is_reconciled_and_a_repeat_never_duplicates(shared_calendar, fake_api, monkeypatch):
    """The review's `calendar-unknown-write`, with the safe expected result."""
    monkeypatch.setattr(google_calendar, "_http_json", _lost_answer_after(fake_api, lose={1}))
    first = _tool(monkeypatch, shared_calendar, CREATE, OWNER_DM)
    assert first["ok"] is True and "found in the calendar" in first["note"]
    again = _tool(monkeypatch, shared_calendar, CREATE, OWNER_DM)
    assert again["ok"] is True and again["already_existed"] is True
    stored = [e for e in fake_api.calendars["tok-owner"] if e["summary"] == "Встреча"]
    assert len(stored) == 1


def test_unverifiable_outcome_is_reported_as_unknown_not_as_retry(shared_calendar, fake_api, monkeypatch):
    monkeypatch.setattr(
        google_calendar, "_http_json", _lost_answer_after(fake_api, lose={1}, lookup_fails=True)
    )
    first = _tool(monkeypatch, shared_calendar, CREATE, OWNER_DM)
    assert first["error"] == "create_outcome_unknown"
    assert "Do not create it again" in first["next_step"]
    assert "trying again" not in first["next_step"]
    # Even if the agent repeats anyway, the stable id prevents a second event.
    monkeypatch.setattr(google_calendar, "_http_json", fake_api)
    again = _tool(monkeypatch, shared_calendar, CREATE, OWNER_DM)
    assert again["ok"] is True and again["already_existed"] is True
    assert len([e for e in fake_api.calendars["tok-owner"] if e["summary"] == "Встреча"]) == 1


def test_confirmed_not_stored_says_so_and_a_repeat_creates_once(shared_calendar, fake_api, monkeypatch):
    monkeypatch.setattr(google_calendar, "_http_json", _lost_answer_after(fake_api, lose={1}, store=False))
    first = _tool(monkeypatch, shared_calendar, CREATE, OWNER_DM)
    assert first["error"] == "create_not_stored"
    monkeypatch.setattr(google_calendar, "_http_json", fake_api)
    again = _tool(monkeypatch, shared_calendar, CREATE, OWNER_DM)
    assert again["ok"] is True and "already_existed" not in again
    assert len([e for e in fake_api.calendars["tok-owner"] if e["summary"] == "Встреча"]) == 1


def test_event_deleted_earlier_does_not_block_creating_it_again(shared_calendar, fake_api, monkeypatch):
    first = _tool(monkeypatch, shared_calendar, CREATE, OWNER_DM)
    stored = fake_api._find("tok-owner", first["created"].get("id") or "") or fake_api.calendars["tok-owner"][-1]
    stored["status"] = "cancelled"  # the owner deleted it in Google Calendar
    again = _tool(monkeypatch, shared_calendar, CREATE, OWNER_DM)
    assert again["ok"] is True and "already_existed" not in again
    ids = [e["id"] for e in fake_api.calendars["tok-owner"] if e["summary"] == "Встреча"]
    assert len(ids) == 2 and len(set(ids)) == 2


def test_reads_keep_plain_retry_advice(shared_calendar, fake_api, monkeypatch):
    import urllib.error

    fake_api.fail_with = urllib.error.URLError("offline")
    answer = _tool(monkeypatch, shared_calendar, LIST, OWNER_DM)
    assert answer["error"] == "google_unavailable" and "trying again" in answer["next_step"]
