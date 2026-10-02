"""K21-274: undelivered replies 0.21.15 left in a secondary profile's state.db are redelivered once."""
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway import delivery_ledger as dl


@pytest.fixture(autouse=True)
def _shared_db(tmp_path, monkeypatch):
    home = tmp_path / "root"
    home.mkdir()
    monkeypatch.setattr(dl, "_db_path", lambda: home / "state.db")
    return home


def _profile_with_rows(tmp_path, name, rows):
    """A profile home whose state.db holds rows written the 0.21.15 way."""
    home = tmp_path / "root" / "profiles" / name
    home.mkdir(parents=True)
    conn = sqlite3.connect(home / "state.db")
    dl._initialize_schema(conn)
    now = time.time()
    for oid, state, owner_pid in rows:
        conn.execute(
            "INSERT INTO delivery_obligations (obligation_id, session_key, platform, chat_id, "
            "content, state, attempts, created_at, updated_at, owner_pid, adapter_profile) "
            "VALUES (?, ?, 'telegram', '1', ?, ?, 0, ?, ?, ?, ?)",
            (oid, f"agent:{name}:telegram:dm:1", f"text {oid}", state, now, now, owner_pid, name),
        )
    conn.commit()
    conn.close()
    return home


def _states(path):
    conn = sqlite3.connect(path)
    try:
        return dict(conn.execute("SELECT obligation_id, state FROM delivery_obligations"))
    finally:
        conn.close()


def test_undelivered_rows_move_to_the_shared_ledger_once(tmp_path, _shared_db):
    home = _profile_with_rows(
        tmp_path, "second",
        [("a", "pending", 999999), ("b", "attempting", 999999), ("c", "delivered", 999999)],
    )

    assert dl.import_legacy_profile_rows({"second": home}) == 2

    assert _states(_shared_db / "state.db") == {"a": "pending", "b": "attempting"}
    assert _states(home / "state.db") == {"a": "migrated", "b": "migrated", "c": "delivered"}
    assert dl.import_legacy_profile_rows({"second": home}) == 0


def test_moved_rows_are_swept_once_for_their_bot(tmp_path):
    home = _profile_with_rows(tmp_path, "second", [("a", "attempting", 999999)])
    dl.import_legacy_profile_rows({"second": home})

    first = dl.sweep_recoverable(deliverable_targets={("telegram", "second")})
    again = dl.sweep_recoverable(deliverable_targets={("telegram", "second")})

    assert [(r["obligation_id"], r["profile"], r["needs_marker"]) for r in first] == [
        ("a", "second", True)
    ]
    assert again == []
    dl.import_legacy_profile_rows({"second": home})
    assert dl.sweep_recoverable(deliverable_targets={("telegram", "second")}) == []


def test_rows_of_a_live_owner_stay_where_they_are(tmp_path, _shared_db):
    import os

    home = _profile_with_rows(tmp_path, "second", [("a", "pending", os.getpid())])

    assert dl.import_legacy_profile_rows({"second": home}) == 0
    assert _states(home / "state.db") == {"a": "pending"}


def test_row_already_in_the_shared_ledger_is_not_duplicated(tmp_path, _shared_db):
    home = _profile_with_rows(tmp_path, "second", [("a", "pending", 999999)])
    dl.record_obligation(
        obligation_id="a", session_key="k", platform="telegram", chat_id="1",
        thread_id=None, content="x", adapter_profile="second",
    )
    with dl._transaction() as conn:
        conn.execute("UPDATE delivery_obligations SET state='delivered'")

    dl.import_legacy_profile_rows({"second": home})

    assert _states(_shared_db / "state.db") == {"a": "delivered"}
    assert dl.sweep_recoverable(deliverable_targets={("telegram", "second")}) == []


def test_missing_database_table_or_garbage_is_skipped(tmp_path):
    empty = tmp_path / "root" / "profiles" / "none"
    empty.mkdir(parents=True)
    no_table = tmp_path / "root" / "profiles" / "plain"
    no_table.mkdir(parents=True)
    sqlite3.connect(no_table / "state.db").close()
    broken = tmp_path / "root" / "profiles" / "broken"
    broken.mkdir(parents=True)
    (broken / "state.db").write_bytes(b"not sqlite" * 50)

    assert dl.import_legacy_profile_rows({"none": empty, "plain": no_table, "broken": broken}) == 0
    assert not (empty / "state.db").exists()


def test_the_shared_database_itself_is_never_imported(_shared_db):
    dl.record_obligation(
        obligation_id="r", session_key="k", platform="telegram", chat_id="1",
        thread_id=None, content="x", adapter_profile="default",
    )

    assert dl.import_legacy_profile_rows({"default": _shared_db}) == 0


@pytest.mark.asyncio
async def test_gateway_startup_redelivers_a_legacy_secondary_reply_exactly_once(tmp_path, monkeypatch):
    from gateway.config import Platform
    from gateway.run import GatewayRunner

    home = _profile_with_rows(tmp_path, "second", [("a", "attempting", 999999)])
    monkeypatch.setattr("gateway.run._multiplex_profile_homes", lambda _c: [("default", tmp_path), ("second", home)])
    monkeypatch.setattr(dl, "ledger_enabled", lambda config=None: True)

    adapter = MagicMock()
    adapter.send = AsyncMock(return_value=MagicMock(success=True, error=""))
    runner = object.__new__(GatewayRunner)
    runner.config = MagicMock(multiplex_profiles=True)
    runner.adapters = {}
    runner._profile_adapters = {"second": {Platform.TELEGRAM: adapter}}
    store = MagicMock()
    store.clear_resume_pending = AsyncMock()
    store._store = None
    runner.session_store = None
    runner._async_session_store = store

    assert await runner._redeliver_pending_obligations() == 1
    assert await runner._redeliver_pending_obligations() == 0

    adapter.send.assert_awaited_once()
    assert adapter.send.call_args.kwargs["content"].endswith("text a")
    assert _states(home / "state.db") == {"a": "migrated"}
