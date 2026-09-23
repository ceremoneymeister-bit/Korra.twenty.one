"""K21-133: per-user dashboard board with a server-owned CAS revision."""

from __future__ import annotations

import asyncio
import copy
import threading
import types

import pytest

from korra_cli import web_server as ws
from korra_cli.dashboard_auth.public_paths import PUBLIC_API_PATHS
from korra_cli.dashboard_layout import (
    LOCAL_USER_KEY,
    TILE_WIDGET_IDS,
    WIDGET_IDS,
    default_sizes,
    preference,
    storage_key,
    store,
    updated,
)
from korra_cli.web_models import DashboardLayoutSetBody


@pytest.fixture
def board_state(monkeypatch):
    """A config that behaves like the real one: read a copy, write the whole doc."""
    state = {"dashboard": {"theme": "dark"}, "unrelated": {"keep": True}}
    writes = []
    # The real config file is read and replaced atomically; mirror that here so
    # a concurrent reader cannot observe a half-written document. The race the
    # test is about — a browser holding a stale revision — is untouched.
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
    return state, writes


def _request(session=None, token_principal=None):
    """A request stub carrying only what auth verified, never a body claim."""
    state = types.SimpleNamespace()
    if session is not None:
        state.session = session
    if token_principal is not None:
        state.token_principal = token_principal
    return types.SimpleNamespace(state=state)


def _session(user_id, provider="portal"):
    return types.SimpleNamespace(user_id=user_id, provider=provider)


def _put(body, request=None):
    return asyncio.run(ws.set_dashboard_layout(body, request))


def _get(request=None):
    return asyncio.run(ws.get_dashboard_layout(request))


# ── Defaults and normalization ─────────────────────────────────────────────


def test_untouched_board_is_the_whole_catalog_at_the_default_size():
    value = preference({}, LOCAL_USER_KEY)
    assert value == {
        "version": 1,
        "revision": 0,
        "initialized": False,
        "order": list(WIDGET_IDS),
        "hidden": [],
        "sizes": default_sizes(),
    }


def test_stored_board_keeps_its_order_and_gains_cards_added_later():
    # A person personalized their board before `recent-results` existed: the
    # new card must appear, not vanish because their record predates it.
    value = preference(
        {
            "dashboard": {
                "layout": {
                    "users": {
                        LOCAL_USER_KEY: {
                            "revision": 3,
                            "initialized": True,
                            "order": ["metrics", "attention", "agents"],
                            "hidden": ["metrics"],
                            "sizes": {"metrics": "l"},
                        }
                    }
                }
            }
        },
        LOCAL_USER_KEY,
    )
    assert value["order"][:3] == ["metrics", "attention", "agents"]
    assert set(value["order"]) == set(WIDGET_IDS)
    assert value["hidden"] == ["metrics"]
    assert value["sizes"]["metrics"] == "l"
    assert value["sizes"]["agents"] == "m"
    assert value["revision"] == 3


def test_normalization_drops_unknown_cards_sizes_and_negative_revisions():
    value = preference(
        {
            "dashboard": {
                "layout": {
                    "users": {
                        LOCAL_USER_KEY: {
                            "revision": -7,
                            "order": ["agents", "../escape", "agents", 12],
                            "hidden": ["ghost", "agents", "agents"],
                            "sizes": {"agents": "xxl", "ghost": "l", "metrics": 3},
                        }
                    }
                }
            }
        },
        LOCAL_USER_KEY,
    )
    assert value["revision"] == 0
    assert value["order"] == ["agents"] + [w for w in WIDGET_IDS if w != "agents"]
    assert value["hidden"] == ["agents"]
    assert value["sizes"] == default_sizes()


def test_update_increments_the_server_revision_and_pins_the_attention_strip():
    value = updated(
        {"revision": 12},
        # A browser may serialize the pinned strip anywhere; it is not a tile.
        order=["metrics", "attention", "agents", "recent-results", "upcoming-tasks"],
        hidden=["upcoming-tasks", "ghost"],
        sizes={"metrics": "l", "agents": "s", "attention": "l"},
    )
    assert value["revision"] == 13
    assert value["initialized"] is True
    assert value["order"][0] == "attention"
    stored = ["metrics", "agents", "recent-results", "upcoming-tasks"]
    tiles = [w for w in value["order"] if w != "attention"]
    # The browser's order is kept; catalog cards it never mentioned follow it.
    assert tiles[: len(stored)] == stored
    assert tiles[len(stored):] == [w for w in TILE_WIDGET_IDS if w not in stored]
    assert value["hidden"] == ["upcoming-tasks"]
    assert value["sizes"]["metrics"] == "l"
    assert value["sizes"]["agents"] == "s"
    assert "attention" not in value["sizes"]


def test_every_tile_has_a_size_and_the_pinned_strip_has_none():
    assert set(default_sizes()) == set(TILE_WIDGET_IDS)
    assert "attention" not in TILE_WIDGET_IDS


# ── CAS, persistence and conflict ──────────────────────────────────────────


def test_round_trip_persists_the_board_and_keeps_unrelated_config(board_state):
    state, writes = board_state
    assert _get()["initialized"] is False

    saved = _put(
        DashboardLayoutSetBody(
            revision=0,
            order=["attention", "agents", "metrics", "upcoming-tasks", "recent-results"],
            hidden=["metrics"],
            sizes={"agents": "l", "recent-results": "s"},
        )
    )
    assert saved["revision"] == 1
    assert saved["hidden"] == ["metrics"]
    assert saved["sizes"]["agents"] == "l"
    assert saved["sizes"]["recent-results"] == "s"

    # Survives a fresh read of the durable configuration.
    assert _get() == saved
    assert state["dashboard"]["theme"] == "dark"
    assert state["unrelated"] == {"keep": True}
    assert len(writes) == 1


def test_second_browser_on_a_stale_revision_loses_and_is_handed_the_winner(board_state):
    _, writes = board_state
    winner = _put(
        DashboardLayoutSetBody(revision=0, order=list(WIDGET_IDS), hidden=["metrics"], sizes={})
    )

    conflict = _put(
        DashboardLayoutSetBody(revision=0, order=list(WIDGET_IDS), hidden=["agents"], sizes={})
    )
    assert conflict.status_code == 409
    import json

    body = json.loads(bytes(conflict.body))
    assert body["preference"] == winner
    assert body["detail"]

    # The loser changed nothing, and re-applying against the winner works.
    assert _get() == winner
    assert len(writes) == 1
    recovered = _put(
        DashboardLayoutSetBody(
            revision=winner["revision"], order=list(WIDGET_IDS), hidden=["agents"], sizes={}
        )
    )
    assert recovered["revision"] == winner["revision"] + 1
    assert recovered["hidden"] == ["agents"]


def test_concurrent_writers_serialize_and_nobody_reuses_a_revision(board_state):
    state, _ = board_state
    results: list[object] = []
    lock = threading.Lock()

    def writer(hidden):
        current = asyncio.run(ws.get_dashboard_layout(None))
        outcome = asyncio.run(
            ws.set_dashboard_layout(
                DashboardLayoutSetBody(
                    revision=current["revision"],
                    order=list(WIDGET_IDS),
                    hidden=[hidden],
                    sizes={},
                ),
                None,
            )
        )
        with lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=writer, args=(name,))
        for name in ("metrics", "agents", "upcoming-tasks", "recent-results")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    accepted = [item for item in results if isinstance(item, dict)]
    revisions = [item["revision"] for item in accepted]
    assert len(revisions) == len(set(revisions)), "a revision was handed out twice"
    assert all(item.status_code == 409 for item in results if not isinstance(item, dict))
    # Whatever the interleaving, the durable record is one of the accepted ones.
    final = asyncio.run(ws.get_dashboard_layout(None))
    assert final["revision"] == max(revisions)
    assert final in accepted
    assert state["unrelated"] == {"keep": True}


# ── Identity isolation ─────────────────────────────────────────────────────


def test_verified_identities_get_separate_keys_and_a_body_cannot_name_one():
    assert storage_key("alice@example.test", "portal") != storage_key(
        "bob@example.test", "portal"
    )
    # The same person at two providers is two accounts, not one board.
    assert storage_key("alice@example.test", "portal") != storage_key(
        "alice@example.test", "other"
    )
    # Hashed: no address ever reaches config.yaml.
    assert "alice" not in storage_key("alice@example.test", "portal")
    # Nothing verified → the single-owner loopback record.
    assert storage_key(None, None) == LOCAL_USER_KEY
    assert storage_key("  ", "portal") == LOCAL_USER_KEY


def test_the_key_comes_from_the_session_not_from_the_payload():
    alice = _request(session=_session("alice@example.test"))
    assert ws._dashboard_layout_key(alice) == storage_key("alice@example.test", "portal")
    # Even a body-shaped attribute on the request cannot redirect the record.
    assert ws._dashboard_layout_key(_request()) == LOCAL_USER_KEY
    service = _request(
        token_principal=types.SimpleNamespace(principal="agent-7", provider="tokens")
    )
    assert ws._dashboard_layout_key(service) == storage_key("agent-7", "tokens")


def test_one_persons_board_never_moves_another_persons(board_state):
    state, _ = board_state
    alice = _request(session=_session("alice@example.test"))
    bob = _request(session=_session("bob@example.test"))

    alice_board = _put(
        DashboardLayoutSetBody(
            revision=0, order=list(WIDGET_IDS), hidden=["metrics"], sizes={"agents": "l"}
        ),
        alice,
    )
    # Bob starts from defaults on his own revision 0, not from Alice's board.
    assert _get(bob)["revision"] == 0
    assert _get(bob)["hidden"] == []
    bob_board = _put(
        DashboardLayoutSetBody(
            revision=0, order=list(WIDGET_IDS), hidden=["agents"], sizes={"metrics": "s"}
        ),
        bob,
    )

    assert _get(alice) == alice_board
    assert _get(bob) == bob_board
    assert alice_board["hidden"] == ["metrics"] and bob_board["hidden"] == ["agents"]
    # Both records coexist in the durable configuration.
    users = state["dashboard"]["layout"]["users"]
    assert set(users) == {
        storage_key("alice@example.test", "portal"),
        storage_key("bob@example.test", "portal"),
    }

    # A later loopback write must not disturb either of them.
    _put(DashboardLayoutSetBody(revision=0, order=list(WIDGET_IDS), hidden=[], sizes={}))
    assert _get(alice) == alice_board
    assert _get(bob) == bob_board


def test_a_new_board_never_evicts_somebody_elses():
    # Making room by deleting a stranger's saved board would cost a person
    # their personalization silently; growth is bounded by who auth lets in.
    config: dict = {}
    for index in range(200):
        store(config, f"u:{index:03d}", {"revision": 1, "order": list(WIDGET_IDS)})
    users = config["dashboard"]["layout"]["users"]
    assert len(users) == 200
    assert users["u:000"] == {"revision": 1, "order": list(WIDGET_IDS)}

    # And an existing record is replaced in place, not duplicated.
    store(config, "u:000", {"revision": 2, "order": list(WIDGET_IDS)})
    assert len(users) == 200
    assert users["u:000"]["revision"] == 2


def test_dashboard_layout_is_not_a_public_endpoint():
    assert "/api/dashboard/layout" not in PUBLIC_API_PATHS
