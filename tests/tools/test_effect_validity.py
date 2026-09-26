"""Result retirement never sends, edits an exact draft, or retries uncertainty."""
import sqlite3
from unittest.mock import Mock

import pytest

from tools import effect_decisions as d
from tools.effect_validity import migrate_lifecycle_states


def make(path, **changes):
    payload = {"message": "Отчёт", "source_label": "cron", "validity": {"version": 1}}
    payload.update(changes.pop("payload", {}))
    args = dict(kind="outbound_message", owner_id="owner", profile="default",
                source_session_id="session", source_session_key="session", payload=payload, path=path)
    args.update(changes)
    return d.create_pending(**args)[0]


def approve(path, item):
    return d.decide(item["id"], source_session_id="session", choice="once", path=path)


def claim(path, item):
    return d.claim_execution(item["id"], expected_payload_sha256=item["payload_sha256"], path=path)


@pytest.mark.parametrize("approved", [False, True])
def test_expiry_fenced_at_approval_and_execution(tmp_path, monkeypatch, approved):
    path = tmp_path / "effects.db"
    monkeypatch.setattr(d.time, "time", lambda: 100)
    item = make(path, payload={"validity": {"version": 1, "expires_at": 200}})
    if approved:
        approve(path, item)
    monkeypatch.setattr(d.time, "time", lambda: 200)
    with pytest.raises(d.DecisionConflict):
        claim(path, item) if approved else approve(path, item)
    current = d.get_decision(item["id"], path=path)
    assert current["status"] == "expired"
    assert current["payload_sha256"] == item["payload_sha256"]
    assert current["payload"] == item["payload"]
    assert d.approval_payload(current)["choices"] == []


def test_old_cron_is_reviewable_without_guessing_expiry(tmp_path):
    path = tmp_path / "effects.db"
    item = make(path, payload={"validity": None})
    assert item["status"] == "needs_review"
    assert d.list_profile_decisions(path=path, statuses=("pending",)) == []
    with pytest.raises(d.DecisionConflict):
        approve(path, item)
    closed = d.decide(item["id"], choice="deny", source_session_id="session", path=path)
    assert closed["status"] == "denied"
    assert closed["payload"] == item["payload"]
    assert make(path, payload={"validity": None, "source_label": "telegram"})["status"] == "pending"


def test_newer_result_replaces_only_same_binding_even_after_it_was_sent(tmp_path):
    path = tmp_path / "effects.db"
    def result(at, **extra):
        return make(path, payload={"validity": {"version": 1, "supersession_key": "job-target", "occurrence_at": at}}, **extra)
    first = result(100)
    newer = result(200)
    assert d.get_decision(first["id"], path=path)["status"] == "superseded"
    approve(path, newer)
    claim(path, newer)
    d.finish_execution(newer["id"], status="succeeded", outcome={"message_id": "1"}, path=path)
    late = result(150)
    assert late["status"] == "superseded"
    assert late["outcome"]["superseded_by"] == newer["id"]
    assert result(100, owner_id="other")["status"] == "pending"
    assert result(100, profile="other")["status"] == "pending"


def test_expiry_does_not_reclassify_inflight_or_uncertain_delivery(tmp_path, monkeypatch):
    path = tmp_path / "effects.db"
    monkeypatch.setattr(d.time, "time", lambda: 100)
    item = make(path, payload={"validity": {"version": 1, "expires_at": 200}})
    approve(path, item)
    claim(path, item)
    monkeypatch.setattr(d.time, "time", lambda: 300)
    assert d.get_decision(item["id"], path=path)["status"] == "executing"
    d.finish_execution(item["id"], status="unknown", outcome={"retry": False}, path=path)
    assert d.get_decision(item["id"], path=path)["status"] == "unknown"


def test_old_constraint_migrates_transactionally_and_preserves_payload_and_index():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE effect_decisions (id TEXT PRIMARY KEY, kind TEXT, owner_id TEXT, profile TEXT, status TEXT CHECK(status IN ('pending','unknown')), created_at REAL, payload_json TEXT)")
    conn.execute("CREATE UNIQUE INDEX old_unique ON effect_decisions(payload_json)")
    conn.execute("INSERT INTO effect_decisions VALUES ('old','outbound_message','owner','default','pending',1,'{\"message\":\"exact\"}')")
    conn.commit()
    migrate_lifecycle_states(conn)
    migrate_lifecycle_states(conn)
    row = conn.execute("SELECT * FROM effect_decisions").fetchone()
    assert row["payload_json"] == '{"message":"exact"}'
    conn.execute("UPDATE effect_decisions SET status='expired'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO effect_decisions(id,payload_json) VALUES ('new',?)", (row["payload_json"],))


def test_unrecognized_constraint_is_not_rewritten():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE effect_decisions (id TEXT PRIMARY KEY, status TEXT)")
    conn.execute("INSERT INTO effect_decisions VALUES ('old','pending')")
    conn.commit()
    with pytest.raises(sqlite3.DatabaseError):
        migrate_lifecycle_states(conn)
    assert conn.execute("SELECT * FROM effect_decisions").fetchall() == [("old", "pending")]


def test_stale_telegram_button_never_enters_transport(tmp_path, monkeypatch):
    from tools import send_message_tool as outbound
    path = tmp_path / "effects.db"
    item = make(path, payload={"validity": {"version": 1, "expires_at": 1}})
    original = d.get_decision
    monkeypatch.setattr(d, "get_decision", lambda ident: original(ident, path=path))
    transport = Mock(side_effect=AssertionError("must not send"))
    monkeypatch.setattr(outbound, "_dispatch_resolved_send", transport)
    assert outbound.resolve_outbound_message_decision(item["id"], "once", source_session_id="session")["status"] == "expired"
    transport.assert_not_called()


def test_reading_receipt_does_not_interrupt_a_live_executor(tmp_path, monkeypatch):
    import os
    path = tmp_path / "effects.db"
    item = make(path)
    approve(path, item)
    claim(path, item)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE effect_decisions SET executor_instance=? WHERE id=?", (f"{os.getppid()}:live-worker", item["id"]))
    assert d.get_decision(item["id"], path=path)["status"] == "executing"
