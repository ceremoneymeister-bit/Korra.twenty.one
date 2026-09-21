"""K21-133: whose board a request may touch, proven through the real gate.

The sibling unit file calls the handlers directly with a hand-made request.
That cannot show what a browser actually gets, because the identity is
attached by ``gated_auth_middleware`` — several layers above the handler. So
these tests drive real HTTP through the whole app (host check → OAuth gate →
route) with the in-process stub IDP, and assert on the durable record.
"""

from __future__ import annotations

import copy
import threading
import time

import pytest
from fastapi.testclient import TestClient

from korra_cli import web_server as ws
from korra_cli.dashboard_auth import clear_providers, register_provider
from korra_cli.dashboard_auth.cookies import (
    SESSION_AT_COOKIE,
    SESSION_PROVIDER_COOKIE,
)
from korra_cli.dashboard_layout import LOCAL_USER_KEY, WIDGET_IDS, storage_key
from tests.korra_cli.conftest_dashboard_auth import StubAuthProvider, _sign

BASE_URL = "https://fly-app.fly.dev"


@pytest.fixture
def board_state(monkeypatch):
    """A config that behaves like the real one: read a copy, write the whole doc."""
    state = {"dashboard": {"theme": "dark"}, "unrelated": {"keep": True}}
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
    # A fail-closed 503 is a 5xx, and the health middleware counts those into a
    # process-wide window. Keep our refusals out of another test's snapshot.
    monkeypatch.setattr(ws, "DASHBOARD_HEALTH", ws.DashboardHealth())
    return state, writes


@pytest.fixture
def gated():
    """The app as a public bind: the OAuth gate is the only way in."""
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


def _sign_in(client: TestClient, person: str, *, ttl: int = 3600) -> None:
    """Hand the browser the same cookies a completed stub login would leave."""
    now = int(time.time())
    client.cookies.clear()
    client.cookies.set(
        SESSION_AT_COOKIE,
        _sign(
            {
                "sub": person,
                "email": f"{person}@example.test",
                "name": person,
                "org_id": "stub-org-1",
                "exp": now + ttl,
            }
        ),
    )
    client.cookies.set(SESSION_PROVIDER_COOKIE, "stub")


def _board(hidden: str) -> dict:
    return {"revision": 0, "order": list(WIDGET_IDS), "hidden": [hidden], "sizes": {}}


def _users(state: dict) -> dict:
    return state.get("dashboard", {}).get("layout", {}).get("users", {})


def test_a_completed_login_owns_a_board_that_carries_no_address(gated, board_state):
    """The whole round trip: no cookie → login → callback → a keyed record."""
    state, _ = board_state
    client = gated()

    start = client.get("/auth/login?provider=stub", follow_redirects=False)
    assert start.status_code == 302
    callback = client.get(start.headers["location"], follow_redirects=False)
    assert callback.status_code == 302

    saved = client.put("/api/dashboard/layout", json=_board("metrics"))
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1

    key = storage_key("stub-user-1", "stub")
    assert set(_users(state)) == {key}
    # Hashed on the way in: config.yaml is read and pasted into tickets.
    assert "stub-user-1" not in str(state)
    assert client.get("/api/dashboard/layout").json() == saved.json()


def test_without_a_session_nobody_reads_or_writes_a_board(gated, board_state):
    state, writes = board_state
    client = gated()
    client.cookies.clear()

    read = client.get("/api/dashboard/layout", follow_redirects=False)
    write = client.put("/api/dashboard/layout", json=_board("agents"), follow_redirects=False)

    assert read.status_code == 401
    assert write.status_code == 401
    assert writes == []
    assert _users(state) == {}


def test_two_people_behind_one_gate_keep_separate_boards(gated, board_state):
    state, writes = board_state
    alice, bob = gated(), gated()
    _sign_in(alice, "alice")
    _sign_in(bob, "bob")

    alice_board = alice.put("/api/dashboard/layout", json=_board("metrics")).json()
    # Bob starts from his own defaults, not from what Alice just saved.
    assert bob.get("/api/dashboard/layout").json()["revision"] == 0
    assert bob.get("/api/dashboard/layout").json()["hidden"] == []
    bob_board = bob.put("/api/dashboard/layout", json=_board("agents")).json()

    assert alice.get("/api/dashboard/layout").json() == alice_board
    assert bob.get("/api/dashboard/layout").json() == bob_board
    assert set(_users(state)) == {
        storage_key("alice", "stub"),
        storage_key("bob", "stub"),
    }
    assert state["unrelated"] == {"keep": True}
    assert state["dashboard"]["theme"] == "dark"
    # Each write is scoped to its own subtree, so one board cannot be dropped
    # by a policy that preserves only the writer's keys.
    for _config, kwargs in writes:
        preserved = {path[3] for path in kwargs["preserve_keys"]}
        assert len(preserved) == 1


@pytest.mark.parametrize(
    "forged",
    [
        {"user_id": "alice"},
        {"key": storage_key("alice", "stub")},
        {"owner": "alice", "user": "alice"},
    ],
)
def test_a_body_that_names_somebody_cannot_reach_their_board(gated, board_state, forged):
    state, _ = board_state
    alice, mallory = gated(), gated()
    _sign_in(alice, "alice")
    _sign_in(mallory, "mallory")
    alice_board = alice.put("/api/dashboard/layout", json=_board("metrics")).json()

    attempt = mallory.put(
        "/api/dashboard/layout", json={**_board("recent-results"), **forged}
    )

    assert attempt.status_code == 200
    # The claim was ignored, not honoured: the write landed on the caller.
    assert attempt.json()["hidden"] == ["recent-results"]
    assert alice.get("/api/dashboard/layout").json() == alice_board
    assert _users(state)[storage_key("alice", "stub")]["hidden"] == ["metrics"]
    assert storage_key("mallory", "stub") in _users(state)


@pytest.mark.parametrize(
    "header", ["X-User-Id", "X-Forwarded-User", "X-Remote-User", "X-Auth-Request-User"]
)
def test_a_header_that_names_somebody_cannot_reach_their_board(gated, board_state, header):
    state, _ = board_state
    alice, mallory = gated(), gated()
    _sign_in(alice, "alice")
    _sign_in(mallory, "mallory")
    alice_board = alice.put("/api/dashboard/layout", json=_board("metrics")).json()

    read = mallory.get("/api/dashboard/layout", headers={header: "alice"})
    mallory.put("/api/dashboard/layout", json=_board("agents"), headers={header: "alice"})

    # Mallory sees her own empty board, and her write stays on it.
    assert read.json()["revision"] == 0
    assert read.json()["hidden"] == []
    assert alice.get("/api/dashboard/layout").json() == alice_board
    assert _users(state)[storage_key("alice", "stub")]["hidden"] == ["metrics"]


def test_one_person_on_two_devices_gets_one_winner_and_the_winner_back(gated, board_state):
    state, writes = board_state
    laptop, phone = gated(), gated()
    _sign_in(laptop, "alice")
    _sign_in(phone, "alice")

    winner = laptop.put("/api/dashboard/layout", json=_board("metrics"))
    loser = phone.put("/api/dashboard/layout", json=_board("agents"))

    assert winner.status_code == 200
    assert loser.status_code == 409
    # The loser is handed the record that won, so it can show what happened
    # instead of guessing — and re-apply on that revision.
    assert loser.json()["preference"] == winner.json()
    assert loser.json()["detail"]
    assert len(writes) == 1

    recovered = phone.put(
        "/api/dashboard/layout",
        json={**_board("agents"), "revision": winner.json()["revision"]},
    )
    assert recovered.status_code == 200
    assert recovered.json()["revision"] == winner.json()["revision"] + 1
    assert laptop.get("/api/dashboard/layout").json() == recovered.json()
    assert set(_users(state)) == {storage_key("alice", "stub")}


def test_devices_racing_the_same_board_never_reuse_a_revision(gated, board_state):
    """Runtime CAS in the shape this actually ships in: one process, many threads."""
    state, _ = board_state
    clients = [gated() for _ in range(4)]
    for client in clients:
        _sign_in(client, "alice")
    outcomes: list = []
    lock = threading.Lock()

    def race(client, hidden):
        current = client.get("/api/dashboard/layout").json()["revision"]
        response = client.put(
            "/api/dashboard/layout", json={**_board(hidden), "revision": current}
        )
        with lock:
            outcomes.append((response.status_code, response.json()))

    threads = [
        threading.Thread(target=race, args=(client, hidden))
        for client, hidden in zip(
            clients, ("metrics", "agents", "upcoming-tasks", "recent-results")
        )
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    accepted = [body for status, body in outcomes if status == 200]
    revisions = [body["revision"] for body in accepted]
    assert revisions, "every concurrent write lost — the lock is not serializing"
    assert len(revisions) == len(set(revisions)), "a revision was handed out twice"
    assert all(status in (200, 409) for status, _ in outcomes)
    final = clients[0].get("/api/dashboard/layout").json()
    assert final["revision"] == max(revisions)
    assert final in accepted
    assert set(_users(state)) == {storage_key("alice", "stub")}


def test_a_login_without_an_identity_fails_closed_instead_of_sharing(gated, board_state):
    """A provider that verified a session but named nobody must not open the
    single-owner board: that record belongs to whoever sits at the machine."""
    state, writes = board_state
    state["dashboard"]["layout"] = {
        "users": {LOCAL_USER_KEY: {"revision": 9, "initialized": True, "hidden": ["agents"]}}
    }
    client = gated()
    _sign_in(client, "")

    read = client.get("/api/dashboard/layout")
    write = client.put("/api/dashboard/layout", json=_board("metrics"))

    assert read.status_code == 503
    assert write.status_code == 503
    assert "дашборд" in read.json()["detail"].lower()
    # The owner's board was neither shown to the nameless caller nor touched.
    assert "agents" not in read.text
    assert writes == []
    assert _users(state)[LOCAL_USER_KEY]["revision"] == 9


def test_the_loopback_owner_still_has_one_board(board_state):
    """No gate, no identity: one machine, one owner, one record."""
    state, _ = board_state
    previous = (
        getattr(ws.app.state, "bound_host", None),
        getattr(ws.app.state, "bound_port", None),
        getattr(ws.app.state, "auth_required", None),
    )
    ws.app.state.bound_host = "127.0.0.1"
    ws.app.state.bound_port = 9119
    ws.app.state.auth_required = False
    try:
        client = TestClient(
            ws.app,
            base_url="http://127.0.0.1:9119",
            headers={ws._SESSION_HEADER_NAME: ws._SESSION_TOKEN},
        )
        saved = client.put("/api/dashboard/layout", json=_board("metrics"))
        assert saved.status_code == 200
        assert client.get("/api/dashboard/layout").json() == saved.json()
        assert set(_users(state)) == {LOCAL_USER_KEY}
    finally:
        (
            ws.app.state.bound_host,
            ws.app.state.bound_port,
            ws.app.state.auth_required,
        ) = previous
