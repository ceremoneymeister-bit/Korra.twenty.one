"""A direct send from another profile must remain answerable in its origin chat."""

import asyncio
import sqlite3
from urllib.parse import unquote, urlparse

import httpx
import pytest
from fastapi import FastAPI

from korra_cli import kanban_db as kb, web_server
from korra_state import SessionDB
from tools import effect_decisions as effects


@pytest.fixture
def direct_send(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    sender = home / "profiles" / "sender"
    sender.mkdir(parents=True)
    (sender / "config.yaml").write_text("{}\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("API_SERVER_KEY", "test-internal-key")
    for key in ("HERMES_KANBAN_HOME", "KORRA_KANBAN_HOME", "HERMES_KANBAN_DB"):
        monkeypatch.delenv(key, raising=False)
    db = SessionDB(home / "state.db")
    db.create_session("origin", source="dashboard")
    db.create_session("other-chat", source="dashboard")
    db.close()
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    # Same class as the real incident: origin has cards, but none assigned
    # to the profile that owns this direct-send approval ledger.
    with kb.connect_closing() as conn:
        kb.create_task(conn, title="unrelated work", assignee="default", session_id="origin")
    effect, _ = effects.create_pending(
        kind="outbound_message", owner_id="owner", profile="sender",
        source_session_id="origin", source_session_key="origin",
        payload={"platform": "telegram", "target": "1234", "content": "test message"},
        path=sender / "effect_decisions.sqlite3",
    )
    return home, effect["id"]


async def call_proxy(monkeypatch, home, method, path, body=None, *, unavailable=()):
    real_client = httpx.AsyncClient
    calls = []

    class Upstream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def __init__(self, *args, **kwargs):
            pass

        async def get(self, url, **kwargs):
            calls.append(("GET", url))
            parts = unquote(urlparse(url).path).strip("/").split("/")
            profile = parts[1] if parts[0] == "p" else "default"
            if profile in unavailable:
                return httpx.Response(503, json={"error": "unavailable"})
            sid = parts[-2]
            directory = home if profile == "default" else home / "profiles" / profile
            rows = effects.list_session_decisions(sid, profile=profile, path=directory / "effect_decisions.sqlite3")
            return httpx.Response(200, json={"data": [{"request_id": r["id"]} for r in rows]})

        async def post(self, url, **kwargs):
            calls.append(("POST", url))
            # Record routing only: no approval and no external transport.
            assert kwargs["headers"]["Authorization"] == "Bearer test-internal-key"
            assert kwargs["headers"]["X-Korra-Attended"] == "1"
            parts = unquote(urlparse(url).path).strip("/").split("/")
            profile = parts[1] if parts[0] == "p" else "default"
            if profile in unavailable:
                return httpx.Response(503, json={"error": "unavailable"})
            directory = home if profile == "default" else home / "profiles" / profile
            row = effects.get_decision(kwargs["json"]["request_id"], path=directory / "effect_decisions.sqlite3")
            return httpx.Response(200, json={"resolved": 1, "status": row["status"] if row else "missing"})

    app = FastAPI()
    app.router.routes.extend(r for r in web_server.app.router.routes
                             if getattr(r, "path", None) in {"/api/chat/approvals", "/api/chat/approval"})
    async with real_client(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        with monkeypatch.context() as scoped:
            scoped.setattr(httpx, "AsyncClient", Upstream)
            response = await (client.post(path, json=body) if method == "POST" else client.get(path))
    return response, calls


def test_direct_send_is_visible_without_a_kanban_link(direct_send, monkeypatch):
    home, effect_id = direct_send
    # Keep the actual session/board lookup: mocking it to [] would miss the
    # exact regression in the release candidate.
    assert kb.executors_for_sessions({"origin"}) == {"default": {"origin"}}
    assert web_server._chat_executor_links("origin", None) == [("sender", "origin")]
    response, _ = asyncio.run(call_proxy(monkeypatch, home, "GET", "/api/chat/approvals?session_id=origin"))
    assert response.status_code == 200
    assert [r["request_id"] for r in response.json()["data"]] == [effect_id]


def test_direct_send_answer_routes_to_its_ledger_without_a_kanban_link(direct_send, monkeypatch):
    home, effect_id = direct_send
    response, calls = asyncio.run(call_proxy(monkeypatch, home, "POST", "/api/chat/approval", {
        "session_id": "origin", "choice": "once", "request_id": effect_id,
    }))
    assert response.status_code == 200
    posts = [url for method, url in calls if method == "POST"]
    assert len(posts) == 1
    assert posts[0].endswith("/p/sender/api/sessions/origin/approval")
    row = effects.get_decision(effect_id, path=home / "profiles" / "sender" / "effect_decisions.sqlite3")
    assert row["status"] == "pending"  # this regression never executes an effect


def _post(monkeypatch, home, effect_id, *, session="origin", profile="", unavailable=()):
    suffix = "?profile=" + profile if profile else ""
    return asyncio.run(call_proxy(monkeypatch, home, "POST", "/api/chat/approval" + suffix, {
        "session_id": session, "choice": "once", "request_id": effect_id,
    }, unavailable=unavailable))


@pytest.mark.parametrize("session", ["other-chat", "nonexistent-chat"])
def test_foreign_or_missing_origin_never_forwards_the_answer(direct_send, monkeypatch, session):
    home, effect_id = direct_send
    if session == "nonexistent-chat":
        # A matching ledger string must not manufacture a real origin chat.
        with sqlite3.connect(home / "profiles" / "sender" / "effect_decisions.sqlite3") as conn:
            conn.execute("UPDATE effect_decisions SET source_session_id=? WHERE id=?", (session, effect_id))
    response, calls = _post(monkeypatch, home, effect_id, session=session)
    assert response.status_code == 409
    assert not [url for method, url in calls if method == "POST"]


def test_session_stamped_for_another_profile_does_not_link_a_foreign_ledger(direct_send, monkeypatch):
    home, effect_id = direct_send
    with sqlite3.connect(home / "state.db") as conn:
        conn.execute("UPDATE sessions SET profile_name='sender' WHERE id='origin'")
    response, calls = _post(monkeypatch, home, effect_id)
    assert response.status_code == 409
    assert not [url for method, url in calls if method == "POST"]


def test_unrelated_row_profile_cannot_redirect_the_physical_sender(direct_send, monkeypatch):
    stored_profile = "other-sender"
    home, effect_id = direct_send
    with sqlite3.connect(home / "profiles" / "sender" / "effect_decisions.sqlite3") as conn:
        conn.execute("UPDATE effect_decisions SET profile=? WHERE id=?", (stored_profile, effect_id))
    response, calls = _post(monkeypatch, home, effect_id)
    assert response.status_code == 409
    assert not [url for method, url in calls if method == "POST"]
    assert web_server._chat_executor_links("origin", None) == []


@pytest.mark.parametrize("second_profile", ["default", "another-sender"])
def test_ambiguous_exact_id_is_rejected_including_own_versus_foreign(direct_send, monkeypatch, second_profile):
    home, effect_id = direct_send
    directory = home if second_profile == "default" else home / "profiles" / second_profile
    directory.mkdir(parents=True, exist_ok=True)
    second, _ = effects.create_pending(
        kind="outbound_message", owner_id="owner", profile=second_profile,
        source_session_id="origin", source_session_key="origin",
        payload={"content": "another draft"}, path=directory / "effect_decisions.sqlite3",
    )
    with sqlite3.connect(directory / "effect_decisions.sqlite3") as conn:
        conn.execute("UPDATE effect_decisions SET id=? WHERE id=?", (effect_id, second["id"]))
    response, calls = _post(monkeypatch, home, effect_id)
    assert response.status_code == 409
    assert not [url for method, url in calls if method == "POST"]


def test_corrupt_ledger_is_preserved_and_never_falls_back_to_another_owner(direct_send, monkeypatch):
    home, effect_id = direct_send
    path = home / "profiles" / "sender" / "effect_decisions.sqlite3"
    path.write_bytes(b"corrupt-ledger-evidence")
    response, calls = _post(monkeypatch, home, effect_id)
    assert response.status_code == 503
    assert not [url for method, url in calls if method == "POST"]
    assert path.read_bytes() == b"corrupt-ledger-evidence"


def test_unreadable_origin_state_never_falls_back_to_another_owner(direct_send, monkeypatch):
    home, effect_id = direct_send
    (home / "state.db").write_bytes(b"corrupt-origin-evidence")
    response, calls = _post(monkeypatch, home, effect_id)
    assert response.status_code == 503
    assert not [url for method, url in calls if method == "POST"]
    assert (home / "state.db").read_bytes() == b"corrupt-origin-evidence"


def test_old_origin_schema_is_not_migrated_to_manufacture_a_link(direct_send, monkeypatch):
    home, effect_id = direct_send
    path = home / "state.db"
    path.unlink()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE forensic_marker (value TEXT)")
    before = path.read_bytes()
    response, calls = _post(monkeypatch, home, effect_id)
    assert response.status_code == 503
    assert not [url for method, url in calls if method == "POST"]
    assert path.read_bytes() == before


def test_worker_origin_does_not_gain_an_owner_link(direct_send, monkeypatch):
    home, effect_id = direct_send
    with sqlite3.connect(home / "state.db") as conn:
        conn.execute("UPDATE sessions SET source='cron' WHERE id='origin'")
    response, calls = _post(monkeypatch, home, effect_id)
    assert response.status_code == 409
    assert not [url for method, url in calls if method == "POST"]


def test_direct_send_follows_the_verified_reset_chain(direct_send, monkeypatch):
    home, effect_id = direct_send
    db = SessionDB(home / "state.db")
    db.end_session("origin", "session_reset")
    db.create_session("after-reset", source="dashboard", session_key="same-chat",
                      parent_session_id="origin", model_config={"_reset_from": "origin"})
    db.close()
    with sqlite3.connect(home / "state.db") as conn:
        conn.execute("UPDATE sessions SET session_key='same-chat' WHERE id='origin'")
    response, calls = _post(monkeypatch, home, effect_id, session="after-reset")
    assert response.status_code == 200
    assert [url for method, url in calls if method == "POST"][0].endswith(
        "/p/sender/api/sessions/origin/approval")


def test_executor_own_center_keeps_the_stored_origin_even_without_its_local_chat(direct_send, monkeypatch):
    home, effect_id = direct_send
    response, calls = _post(monkeypatch, home, effect_id, profile="sender")
    assert response.status_code == 200
    assert [url for method, url in calls if method == "POST"][0].endswith(
        "/p/sender/api/sessions/origin/approval")


def test_foreign_gateway_down_never_uses_the_origin_gateway_for_the_decision(direct_send, monkeypatch):
    home, effect_id = direct_send
    response, calls = _post(monkeypatch, home, effect_id, unavailable=("sender",))
    assert response.status_code == 503
    posts = [url for method, url in calls if method == "POST"]
    assert len(posts) == 1 and "/p/sender/" in posts[0]
    assert effects.get_decision(effect_id, path=home / "profiles" / "sender" / "effect_decisions.sqlite3")["status"] == "pending"


def test_own_decision_is_independent_of_an_unrelated_foreign_gateway(direct_send, monkeypatch):
    home, _ = direct_send
    own, _ = effects.create_pending(
        kind="outbound_message", owner_id="owner", profile="default",
        source_session_id="origin", source_session_key="origin",
        payload={"content": "own draft"}, path=home / "effect_decisions.sqlite3",
    )
    response, calls = _post(monkeypatch, home, own["id"], unavailable=("sender",))
    assert response.status_code == 200
    assert calls == [("POST", "http://127.0.0.1:8642/api/sessions/origin/approval")]


def test_repeating_a_succeeded_decision_keeps_its_original_ledger_and_receipt(direct_send, monkeypatch):
    home, effect_id = direct_send
    path = home / "profiles" / "sender" / "effect_decisions.sqlite3"
    receipt = '{"transport":{"success":true,"message_id":"test-receipt"}}'
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE effect_decisions SET status='succeeded', outcome_json=? WHERE id=?", (receipt, effect_id))
    for _ in range(2):
        response, calls = _post(monkeypatch, home, effect_id)
        assert response.status_code == 200 and response.json()["status"] == "succeeded"
        # No pending-only GET and no fallback to default after completion.
        assert calls == [("POST", "http://127.0.0.1:8642/p/sender/api/sessions/origin/approval")]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT status, outcome_json, execution_started_at FROM effect_decisions WHERE id=?",
                            (effect_id,)).fetchone() == ("succeeded", receipt, None)


def test_historical_origin_profile_keeps_the_physical_sender(direct_send, monkeypatch):
    home, effect_id = direct_send
    with sqlite3.connect(home / "profiles/sender/effect_decisions.sqlite3") as conn:
        conn.execute("UPDATE effect_decisions SET profile='default' WHERE id=?", (effect_id,))
    response, calls = _post(monkeypatch, home, effect_id)
    assert response.status_code == 200
    assert [url for method, url in calls if method == "POST"] == [
        "http://127.0.0.1:8642/p/sender/api/sessions/origin/approval"]
