"""Personal agents-screen view on a phone: tabs or list, per verified person.

Same contract as the dashboard board (K21-133): the person comes from the
verified session, the revision is server-owned, a stale browser loses with the
winner in hand, and one person's choice never moves another's.
"""

from __future__ import annotations

import asyncio
import copy
import json
import threading
import time
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from korra_cli import web_server as ws
from korra_cli.dashboard_auth import clear_providers, register_provider
from korra_cli.dashboard_auth.cookies import SESSION_AT_COOKIE, SESSION_PROVIDER_COOKIE
from korra_cli.dashboard_auth.public_paths import PUBLIC_API_PATHS
from korra_cli.dashboard_layout import LOCAL_USER_KEY, storage_key
from korra_cli.dashboard_view import (
    AGENTS_MOBILE_MODES,
    DEFAULT_AGENTS_MOBILE,
    MAX_PINNED,
    cache_scope,
    preference,
    store,
    updated,
)
from korra_cli.web_models import DashboardViewSetBody
from tests.korra_cli.conftest_dashboard_auth import StubAuthProvider, _sign

BASE_URL = "https://fly-app.fly.dev"


@pytest.fixture
def view_state(monkeypatch):
    """A config that behaves like the real one: read a copy, write the whole doc."""
    state = {"dashboard": {"theme": "dark", "layout": {"users": {"x": {"revision": 4}}}},
             "unrelated": {"keep": True}}
    writes: list[tuple[dict, dict]] = []
    io_lock = threading.Lock()

    def load():
        with io_lock:
            return copy.deepcopy(state)

    def save(config, **kwargs):
        with io_lock:
            writes.append((copy.deepcopy(config), kwargs))
            state.clear()
            state.update(copy.deepcopy(config))

    monkeypatch.setattr(ws, "load_config", load)
    monkeypatch.setattr(ws, "save_config", save)
    monkeypatch.setattr(ws, "DASHBOARD_HEALTH", ws.DashboardHealth())
    return state, writes


def _request(session=None):
    state = types.SimpleNamespace()
    if session is not None:
        state.session = session
    return types.SimpleNamespace(state=state)


def _session(user_id, provider="portal"):
    return types.SimpleNamespace(user_id=user_id, provider=provider)


def _put(body, request=None):
    return asyncio.run(ws.set_dashboard_view(body, request))


def _get(request=None):
    return asyncio.run(ws.get_dashboard_view(request))


def _users(state: dict) -> dict:
    return state.get("dashboard", {}).get("view", {}).get("users", {})


# ── Defaults and normalization ─────────────────────────────────────────────


def test_untouched_view_is_the_tab_strip_with_nothing_pinned():
    value = preference({}, LOCAL_USER_KEY)
    assert value == {
        "version": 1,
        "revision": 0,
        "agents_mobile": DEFAULT_AGENTS_MOBILE,
        "pinned": [],
    }
    assert DEFAULT_AGENTS_MOBILE in AGENTS_MOBILE_MODES


def test_normalization_drops_unknown_modes_foreign_names_and_negative_revisions():
    value = preference(
        {
            "dashboard": {
                "view": {
                    "users": {
                        LOCAL_USER_KEY: {
                            "revision": -3,
                            "agents_mobile": "carousel",
                            "pinned": ["designer", "../etc", "designer", "Designer", 7, "default"],
                        }
                    }
                }
            }
        },
        LOCAL_USER_KEY,
    )
    assert value["revision"] == 0
    assert value["agents_mobile"] == DEFAULT_AGENTS_MOBILE
    assert value["pinned"] == ["designer", "default"]


def test_update_increments_the_server_revision_and_bounds_the_pins():
    many = [f"agent-{index}" for index in range(MAX_PINNED + 5)]
    value = updated({"revision": 7}, agents_mobile="list", pinned=many)
    assert value["revision"] == 8
    assert value["agents_mobile"] == "list"
    assert value["pinned"] == many[:MAX_PINNED]


def test_body_rejects_a_mode_the_screen_does_not_have():
    with pytest.raises(Exception):
        DashboardViewSetBody(revision=0, agents_mobile="carousel")
    with pytest.raises(Exception):
        DashboardViewSetBody(revision=-1, agents_mobile="list")


# ── CAS, persistence and conflict ──────────────────────────────────────────


def test_round_trip_persists_the_choice_and_keeps_unrelated_config(view_state):
    state, writes = view_state
    assert _get()["agents_mobile"] == "tabs"

    saved = _put(DashboardViewSetBody(revision=0, agents_mobile="list", pinned=["designer"]))
    assert saved["revision"] == 1
    assert saved["agents_mobile"] == "list"
    assert saved["pinned"] == ["designer"]
    assert saved["scope"] == cache_scope(LOCAL_USER_KEY)

    assert _get() == saved
    assert state["dashboard"]["theme"] == "dark"
    # The dashboard board is a sibling record and stays untouched.
    assert state["dashboard"]["layout"] == {"users": {"x": {"revision": 4}}}
    assert state["unrelated"] == {"keep": True}
    assert len(writes) == 1
    # The durable record carries no derived browser-only field.
    assert "scope" not in _users(state)[LOCAL_USER_KEY]
    preserved = writes[0][1]["preserve_keys"]
    assert ("dashboard", "view", "users", LOCAL_USER_KEY, "agents_mobile") in preserved


def test_second_device_on_a_stale_revision_loses_and_is_handed_the_winner(view_state):
    _, writes = view_state
    winner = _put(DashboardViewSetBody(revision=0, agents_mobile="list"))

    conflict = _put(DashboardViewSetBody(revision=0, agents_mobile="tabs"))
    assert conflict.status_code == 409
    body = json.loads(bytes(conflict.body))
    assert body["preference"] == winner
    assert body["detail"]

    assert _get() == winner
    assert len(writes) == 1
    recovered = _put(
        DashboardViewSetBody(revision=winner["revision"], agents_mobile="tabs")
    )
    assert recovered["revision"] == winner["revision"] + 1
    assert recovered["agents_mobile"] == "tabs"


def test_a_write_the_config_policy_drops_is_not_acknowledged(view_state, monkeypatch):
    state, _ = view_state
    monkeypatch.setattr(ws, "save_config", lambda config, **kwargs: None)
    response = _put(DashboardViewSetBody(revision=0, agents_mobile="list"))
    assert response.status_code == 409
    assert json.loads(bytes(response.body))["preference"]["agents_mobile"] == "tabs"
    assert _users(state) == {}


def test_concurrent_writers_serialize_and_nobody_reuses_a_revision(view_state):
    results: list[object] = []
    lock = threading.Lock()

    def writer(mode):
        current = asyncio.run(ws.get_dashboard_view(None))
        outcome = asyncio.run(
            ws.set_dashboard_view(
                DashboardViewSetBody(revision=current["revision"], agents_mobile=mode), None
            )
        )
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=writer, args=(mode,)) for mode in ("list", "tabs", "list", "tabs")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    accepted = [item for item in results if isinstance(item, dict)]
    revisions = [item["revision"] for item in accepted]
    assert revisions
    assert len(revisions) == len(set(revisions)), "a revision was handed out twice"
    assert all(item.status_code == 409 for item in results if not isinstance(item, dict))
    final = asyncio.run(ws.get_dashboard_view(None))
    assert final["revision"] == max(revisions)
    assert final in accepted


# ── Identity isolation ─────────────────────────────────────────────────────


def test_one_persons_view_never_moves_another_persons(view_state):
    state, _ = view_state
    owner = _request(session=_session("owner@example.test"))
    assistant = _request(session=_session("assistant@example.test"))

    owner_view = _put(DashboardViewSetBody(revision=0, agents_mobile="list"), owner)
    # The assistant starts from the default, not from the owner's list.
    assert _get(assistant)["agents_mobile"] == "tabs"
    assert _get(assistant)["revision"] == 0
    assistant_view = _put(
        DashboardViewSetBody(revision=0, agents_mobile="tabs", pinned=["secretary"]), assistant
    )

    assert _get(owner) == owner_view
    assert _get(assistant) == assistant_view
    assert owner_view["scope"] != assistant_view["scope"]
    assert set(_users(state)) == {
        storage_key("owner@example.test", "portal"),
        storage_key("assistant@example.test", "portal"),
    }
    # Hashed on the way in: config.yaml is read and pasted into tickets.
    assert "owner@example.test" not in str(state)


def test_dashboard_view_is_not_a_public_endpoint():
    assert "/api/dashboard/view" not in PUBLIC_API_PATHS


# ── Through the real OAuth gate ────────────────────────────────────────────


@pytest.fixture
def gated():
    clear_providers()
    register_provider(StubAuthProvider())
    previous = (
        getattr(ws.app.state, "bound_host", None),
        getattr(ws.app.state, "bound_port", None),
        getattr(ws.app.state, "auth_required", None),
    )
    ws.app.state.bound_host = "fly-app.fly.dev"
    ws.app.state.bound_port = 443
    ws.app.state.auth_required = True
    try:
        yield lambda: TestClient(ws.app, base_url=BASE_URL)
    finally:
        clear_providers()
        (
            ws.app.state.bound_host,
            ws.app.state.bound_port,
            ws.app.state.auth_required,
        ) = previous


def _sign_in(client: TestClient, person: str) -> None:
    now = int(time.time())
    client.cookies.clear()
    client.cookies.set(
        SESSION_AT_COOKIE,
        _sign({"sub": person, "email": f"{person}@example.test", "name": person,
               "org_id": "stub-org-1", "exp": now + 3600}),
    )
    client.cookies.set(SESSION_PROVIDER_COOKIE, "stub")


def test_without_a_session_nobody_reads_or_writes_a_view(gated, view_state):
    state, writes = view_state
    client = gated()
    client.cookies.clear()
    read = client.get("/api/dashboard/view", follow_redirects=False)
    write = client.put(
        "/api/dashboard/view", json={"revision": 0, "agents_mobile": "list"}, follow_redirects=False
    )
    assert read.status_code == 401
    assert write.status_code == 401
    assert writes == []
    assert _users(state) == {}


def test_a_body_that_names_somebody_cannot_reach_their_view(gated, view_state):
    state, _ = view_state
    owner, mallory = gated(), gated()
    _sign_in(owner, "owner")
    _sign_in(mallory, "mallory")
    owner_view = owner.put("/api/dashboard/view", json={"revision": 0, "agents_mobile": "list"}).json()

    attempt = mallory.put(
        "/api/dashboard/view",
        json={"revision": 0, "agents_mobile": "tabs", "user_id": "owner",
              "key": storage_key("owner", "stub")},
        headers={"X-Forwarded-User": "owner"},
    )
    assert attempt.status_code == 200
    assert owner.get("/api/dashboard/view").json() == owner_view
    assert _users(state)[storage_key("owner", "stub")]["agents_mobile"] == "list"
    assert storage_key("mallory", "stub") in _users(state)


# ── Pre-paint bootstrap in the page ────────────────────────────────────────


def _spa(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(
        '<html><head></head><body><div id="root"></div></body></html>'
    )
    monkeypatch.setattr(ws, "WEB_DIST", tmp_path)
    monkeypatch.setattr(ws, "get_install_id", lambda: "a" * 32)
    app = FastAPI()
    ws.mount_spa(app)
    return TestClient(app)


def test_loopback_page_carries_the_owners_view_before_any_request(view_state, tmp_path, monkeypatch):
    state, writes = view_state
    store(state, LOCAL_USER_KEY, updated({}, agents_mobile="list", pinned=["designer"]))
    previous = getattr(ws.app.state, "auth_required", None)
    ws.app.state.auth_required = False
    try:
        head = _spa(tmp_path, monkeypatch).get("/agents").text.split("</head>")[0]
    finally:
        ws.app.state.auth_required = previous
    assert "window.__KORRA_VIEW_PREF__=" in head
    assert '"agents_mobile":"list"' in head
    assert f'"scope":"{cache_scope(LOCAL_USER_KEY)}"' in head
    assert writes == []


def test_gated_page_without_a_person_carries_no_view(view_state, tmp_path, monkeypatch):
    state, _ = view_state
    # The single-owner record belongs to whoever sits at the machine; an
    # unauthenticated page request must not reveal it.
    store(state, LOCAL_USER_KEY, updated({}, agents_mobile="list", pinned=[]))
    previous = getattr(ws.app.state, "auth_required", None)
    ws.app.state.auth_required = True
    try:
        assert ws._dashboard_view_bootstrap(_request()) is None
        signed = ws._dashboard_view_bootstrap(_request(session=_session("owner@example.test")))
    finally:
        ws.app.state.auth_required = previous
    assert signed is not None
    assert signed["agents_mobile"] == "tabs"
    assert signed["scope"] == cache_scope(storage_key("owner@example.test", "portal"))
