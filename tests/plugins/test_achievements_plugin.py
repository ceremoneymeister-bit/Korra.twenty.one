"""Tests for the bundled hermes-achievements dashboard plugin.

These target the two behaviors that matter for official integration:

* The 200-session scan cap is removed — the plugin now walks the entire
  session history by default. Lifetime badges (tens of thousands of
  tool calls) were unreachable before this fix on long-running installs.
* First-ever scans run in a background thread so the dashboard request
  path never blocks, even on 8000+ session databases where a cold scan
  takes minutes.
* The scan attaches to ``state.db`` read-only. It is a pure reader, so it
  must not initialise the schema, must not take the write lock, and must
  not create the store on an install that has never written a session.
  These run against a real SQLite file with a real second writer — no fakes.

The upstream repo ships its own unittest suite under
``plugins/hermes-achievements/tests/`` covering the achievement engine
internals (tier math, secret-state handling, catalog invariants). These
tests live at the hermes-agent level and focus on the integration
contract: the plugin scans ALL of your sessions, not the first 200.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PLUGIN_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "hermes-achievements"
    / "dashboard"
    / "plugin_api.py"
)


@pytest.fixture
def plugin_api(tmp_path, monkeypatch):
    """Load plugin_api with isolated ~/.hermes so state/snapshot files don't collide.

    We load the module fresh per test because the plugin keeps module-level
    caches (``_SNAPSHOT_CACHE``, ``_SCAN_STATUS``, background thread handle).
    Reloading gives each test a clean world.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    spec = importlib.util.spec_from_file_location(
        f"plugin_api_test_{id(tmp_path)}", PLUGIN_MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Stash monkeypatch so ``_install_fake_session_db`` can use it to
    # swap ``sys.modules['korra_state']`` with auto-restoration. Without
    # this, a raw ``sys.modules[...] = fake`` assignment would leak the
    # fake into later tests in the same xdist worker — breaking every
    # test that does ``from korra_state import SessionDB``.
    module._test_monkeypatch = monkeypatch
    module._test_tmp_path = tmp_path
    yield module


class _FakeSessionDB:
    """Stand-in for korra_state.SessionDB that records scan calls."""

    def __init__(self, session_count: int, scan_delay: float = 0):
        self.session_count = session_count
        self.scan_delay = scan_delay
        self.last_limit: Optional[int] = None
        self.last_include_children: Optional[bool] = None
        self.list_calls = 0
        self.messages_calls = 0

    def list_sessions_rich(
        self,
        source: Optional[str] = None,
        exclude_sources: Optional[List[str]] = None,
        limit: int = 20,
        offset: int = 0,
        include_children: bool = False,
        project_compression_tips: bool = True,
    ) -> List[Dict[str, Any]]:
        if self.scan_delay:
            time.sleep(self.scan_delay)
        self.last_limit = limit
        self.last_include_children = include_children
        self.list_calls += 1
        # SQLite semantics: LIMIT -1 = unlimited. Honor that here.
        effective = self.session_count if limit == -1 else min(self.session_count, limit)
        now = int(time.time())
        return [
            {
                "id": f"sess-{i}",
                "title": f"Session {i}",
                "preview": f"preview {i}",
                "started_at": now - (self.session_count - i) * 60,
                "last_active": now - (self.session_count - i) * 60 + 30,
                "source": "cli",
                "model": "test-model",
            }
            for i in range(effective)
        ]

    def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        self.messages_calls += 1
        return [
            {"role": "user", "content": f"ask {session_id}"},
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "terminal"}}],
            },
            {"role": "tool", "tool_name": "terminal", "content": "ok"},
        ]

    def close(self) -> None:
        pass


def _install_fake_session_db(plugin_api, fake_db):
    """Inject a fake SessionDB so ``scan_sessions`` finds it via its local import.

    Uses the monkeypatch stashed on ``plugin_api`` by the fixture, so the
    ``sys.modules['korra_state']`` swap is auto-restored at test teardown
    and cannot leak into unrelated tests in the same xdist worker.
    """
    fake_module = type(sys)("korra_state")
    # ``scan_sessions`` resolves the store path and then attaches read-only,
    # so the stand-in module has to answer both names the real one does.
    fake_module.SessionDB = lambda **_kw: fake_db
    fake_db_path = plugin_api._test_tmp_path / "fake-state.db"
    fake_db_path.touch()
    fake_module._default_db_path = lambda: fake_db_path
    plugin_api._test_monkeypatch.setitem(sys.modules, "korra_state", fake_module)


@pytest.fixture
def real_store(tmp_path, monkeypatch):
    """A real on-disk ``state.db`` that ``scan_sessions()`` will resolve to.

    ``_default_db_path()`` honours a re-pointed ``DEFAULT_DB_PATH`` (the
    established test escape hatch), so the plugin opens THIS file — no fake
    module, no patched sqlite3.
    """
    import korra_state

    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    db_path = home / "state.db"
    monkeypatch.setattr(korra_state, "DEFAULT_DB_PATH", db_path)
    return db_path


def _seed_store(db_path, session_id="s1"):
    """Initialise the store the way a normal install does: an explicit writer."""
    from korra_state import SessionDB

    db = SessionDB(db_path=db_path)
    try:
        if session_id is not None:
            db.create_session(session_id, source="cli")
            db.append_message(session_id, "user", "hello")
            db.append_message(session_id, "assistant", "hi")
    finally:
        db.close()


def test_scan_sessions_attaches_read_only_and_never_writes(plugin_api, real_store, monkeypatch):
    """The scan is a pure read; a writable open makes it a second WRITER.

    ``SessionDB()`` runs ``_init_schema`` on every open — the whole
    ``SCHEMA_SQL`` script, column reconciliation and an unconditional
    ``UPDATE messages SET active = 1 WHERE active IS NULL`` — so the dashboard
    scan committed a write transaction and took the write lock on the user's
    live ``state.db`` each time it ran (per background scan, per ``/rescan``).
    ``PRAGMA data_version`` on an independent connection changes only when
    another connection COMMITS, so it is the direct evidence.

    Port of upstream Hermes PR #110934 (939a2f64b4).
    """
    from korra_state import SessionDB

    _seed_store(real_store)

    writable_opens: List[Dict[str, Any]] = []
    real_init = SessionDB.__init__

    def spy(self, *args, **kwargs):
        if not kwargs.get("read_only"):
            writable_opens.append(kwargs)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(SessionDB, "__init__", spy)

    probe = sqlite3.connect(str(real_store))
    try:
        before = probe.execute("PRAGMA data_version").fetchone()[0]
        result = plugin_api.scan_sessions()
        after = probe.execute("PRAGMA data_version").fetchone()[0]
    finally:
        probe.close()

    assert result.get("error") is None
    assert [s["session_id"] for s in result["sessions"]] == ["s1"]
    # The transcript read (``get_messages``) has to work on a ``mode=ro``
    # handle too, not just the session listing.
    assert result["sessions"][0]["message_count"] == 2
    assert writable_opens == [], "the achievements scan must attach read-only"
    assert after == before, (
        "the scan committed a write transaction to state.db "
        f"(data_version {before} -> {after}); a reader must not write"
    )


def test_scan_sessions_runs_while_a_writer_holds_the_state_db_lock(
    plugin_api, real_store
):
    """A live turn must not stall the achievements scan.

    A writable open has to get through ``_init_schema``'s DDL/UPDATE, so it
    queues behind any held write transaction for up to
    ``SessionDB._WRITE_PATIENCE_S`` (20 s) and then raises
    ``sqlite3.OperationalError: database is locked``. A read-only attach takes
    no write lock and returns immediately.

    The holder is an independent connection running a real ``BEGIN IMMEDIATE``
    — the same file lock a gateway turn takes while persisting a message. The
    cross-PROCESS shape behaves identically (verified by hand against a
    separate writer process); one connection keeps the test hermetic.
    """
    _seed_store(real_store)

    holder = sqlite3.connect(str(real_store), timeout=30, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO state_meta (key, value) VALUES ('scan_probe', '1')")

    box: Dict[str, Any] = {}

    def run_scan():
        try:
            box["result"] = plugin_api.scan_sessions()
        except BaseException as exc:  # noqa: BLE001 - reported by the assertions
            box["exc"] = f"{type(exc).__name__}: {exc}"

    scan_thread = threading.Thread(target=run_scan, daemon=True)
    try:
        scan_thread.start()
        # Loose bound: the read-only scan of a one-session store finishes in
        # milliseconds, the writable open cannot finish before the holder
        # releases (20 s of patience, then an error).
        scan_thread.join(timeout=10)
        still_blocked = scan_thread.is_alive()
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    assert not still_blocked, (
        "scan_sessions() was still blocked 10s into a concurrent write "
        "transaction — it must not open a writer on state.db"
    )
    assert "exc" not in box, box.get("exc")
    assert box["result"].get("error") is None
    assert [s["session_id"] for s in box["result"]["sessions"]] == ["s1"]


def test_scan_sessions_does_not_create_the_store_on_a_fresh_install(
    plugin_api, real_store
):
    """Schema creation belongs to an explicit writer, never to this reader.

    On an install where nothing has written a session yet, the writable open
    created ``state.db`` with the full schema plus its lock sidecars — a
    dashboard page view minting the agent's database. The scan now reports an
    empty history and leaves the path alone.
    """
    assert not real_store.exists()

    result = plugin_api.scan_sessions()

    assert not real_store.exists(), (
        "the achievements scan created state.db; schema initialisation is the "
        "writer's job"
    )
    assert result.get("error") is None
    assert result["sessions"] == []
    assert result["scan_meta"]["sessions_total"] == 0


def test_scan_sessions_reads_an_initialised_but_empty_store(plugin_api, real_store):
    """Read-only attach must still work right after a normal first init.

    The empty install is the case a ``mode=ro`` open can regress: the file
    exists, the schema is there, but there is no history. The scan has to
    return an empty result, not an error.
    """
    _seed_store(real_store, session_id=None)
    assert real_store.exists()

    result = plugin_api.scan_sessions()

    assert result.get("error") is None
    assert result["sessions"] == []
    assert result["scan_meta"]["sessions_total"] == 0


def test_scan_sessions_default_scans_all_history_not_first_200(plugin_api):
    """Bug regression: ``scan_sessions()`` used to cap at limit=200.

    A user with 8000+ sessions would only see ~2% of their history in
    achievement totals, making lifetime badges unreachable. The default
    now passes ``LIMIT -1`` (SQLite "unlimited") to ``list_sessions_rich``.
    """
    fake_db = _FakeSessionDB(session_count=500)  # > old 200 cap
    _install_fake_session_db(plugin_api, fake_db)

    result = plugin_api.scan_sessions()

    assert fake_db.last_limit == -1, (
        "scan_sessions() must pass LIMIT=-1 (unlimited) to list_sessions_rich "
        f"by default, got {fake_db.last_limit}"
    )
    assert fake_db.last_include_children is True, (
        "scan_sessions() must include subagent/compression child sessions so "
        "tool calls made in delegated agents still count toward achievements"
    )
    assert len(result["sessions"]) == 500
    assert result["scan_meta"]["sessions_total"] == 500


def test_evaluate_all_first_run_returns_pending_and_starts_background_scan(plugin_api):
    """First-ever evaluate_all with no cache returns a pending placeholder
    immediately and kicks off a background scan thread. Cold scans on
    large DBs take minutes — blocking the dashboard request path is not
    acceptable.
    """
    fake_db = _FakeSessionDB(session_count=50)
    _install_fake_session_db(plugin_api, fake_db)

    # Wrap _run_scan_and_update_cache so we can release it on demand,
    # simulating a slow cold scan without actually waiting.
    scan_started = threading.Event()
    allow_scan_finish = threading.Event()
    original_run = plugin_api._run_scan_and_update_cache

    def gated_run(*args, **kwargs):
        scan_started.set()
        allow_scan_finish.wait(timeout=5)
        original_run(*args, **kwargs)

    plugin_api._run_scan_and_update_cache = gated_run

    t0 = time.time()
    result = plugin_api.evaluate_all()
    elapsed = time.time() - t0

    # Immediate return — should not block waiting for the scan.
    assert elapsed < 1.0, f"evaluate_all blocked for {elapsed:.2f}s on first run"
    assert result["scan_meta"]["mode"] == "pending"
    assert result["unlocked_count"] == 0
    # Catalog still rendered so UI has something to draw.
    assert result["total_count"] >= 60

    # Background scan is running.
    assert scan_started.wait(timeout=2), "background scan did not start"

    # Let the scan complete, then a second call returns real data.
    allow_scan_finish.set()
    # Wait for thread to finish.
    thread = plugin_api._BACKGROUND_SCAN_THREAD
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()

    second = plugin_api.evaluate_all()
    assert second["scan_meta"]["mode"] != "pending"
    assert second["scan_meta"].get("sessions_total") == 50


def test_start_background_scan_is_idempotent_while_running(plugin_api):
    """Multiple concurrent dashboard requests must not spawn duplicate scans."""
    fake_db = _FakeSessionDB(session_count=5)
    _install_fake_session_db(plugin_api, fake_db)

    release = threading.Event()
    original_run = plugin_api._run_scan_and_update_cache

    def gated_run(*args, **kwargs):
        release.wait(timeout=5)
        original_run(*args, **kwargs)

    plugin_api._run_scan_and_update_cache = gated_run

    plugin_api._start_background_scan()
    first_thread = plugin_api._BACKGROUND_SCAN_THREAD
    assert first_thread is not None and first_thread.is_alive()

    plugin_api._start_background_scan()
    plugin_api._start_background_scan()

    assert plugin_api._BACKGROUND_SCAN_THREAD is first_thread

    release.set()
    first_thread.join(timeout=5)


def test_background_scan_publishes_partial_snapshots(plugin_api):
    """The background scanner publishes intermediate snapshots to the cache
    every ~N sessions. Each dashboard refresh during a long cold scan sees
    more badges unlocked instead of staring at zeros for minutes and then
    having everything pop at the end.
    """
    fake_db = _FakeSessionDB(session_count=750)
    _install_fake_session_db(plugin_api, fake_db)

    # Record every partial snapshot the scanner publishes.
    partial_snapshots: List[Dict[str, Any]] = []
    original_compute_from_scan = plugin_api._compute_from_scan

    def recording_compute(scan, *, is_partial=False):
        result = original_compute_from_scan(scan, is_partial=is_partial)
        if is_partial:
            partial_snapshots.append(result)
        return result

    plugin_api._compute_from_scan = recording_compute

    # scan 750 sessions with progress_every=250 → expect 2 intermediate
    # publications (at 250 and 500; the final 750 call goes through the
    # finished, non-partial path).
    plugin_api._run_scan_and_update_cache(publish_partial_snapshots=True)

    assert len(partial_snapshots) >= 2, (
        f"expected at least 2 partial publications on a 750-session scan with "
        f"progress_every=250, got {len(partial_snapshots)}"
    )
    # Partial snapshots should report growing session counts.
    counts = [p["scan_meta"].get("sessions_scanned_so_far") for p in partial_snapshots]
    assert counts == sorted(counts), f"partial session counts not monotonic: {counts}"
    assert counts[0] < 750 and counts[-1] < 750, (
        f"partial counts should be less than the final total; got {counts}"
    )
    # Every partial reports the expected end-state total so the UI can
    # show an accurate progress bar.
    for p in partial_snapshots:
        assert p["scan_meta"].get("sessions_expected_total") == 750

    # Final snapshot in cache is the real (non-partial) one.
    final = plugin_api._SNAPSHOT_CACHE
    assert final is not None
    assert final["scan_meta"].get("mode") != "in_progress"
    assert final["scan_meta"].get("sessions_total") == 750


def test_partial_snapshots_do_not_persist_unlock_timestamps(plugin_api):
    """Intermediate snapshots must not write to state.json — an unlock
    that appears at 30% scan progress could disappear when a later session
    rebalances the aggregate. Only the final snapshot records ``unlocked_at``.
    """
    fake_db = _FakeSessionDB(session_count=10)
    _install_fake_session_db(plugin_api, fake_db)

    # Seed empty state, then invoke partial compute directly.
    plugin_api.save_state({"unlocks": {}})
    partial_scan = {
        "sessions": [{"session_id": "x", "tool_call_count": 99999, "tool_names": set()}],
        "aggregate": {"max_tool_calls_in_session": 99999, "total_tool_calls": 99999},
        "scan_meta": {"mode": "in_progress"},
    }
    result = plugin_api._compute_from_scan(partial_scan, is_partial=True)

    # Some achievements should evaluate as unlocked in this aggregate...
    assert any(a["unlocked"] for a in result["achievements"])

    # ...but state.json on disk stays empty (no timestamps were recorded).
    persisted = plugin_api.load_state()
    assert persisted.get("unlocks", {}) == {}, (
        "partial scans must not record unlock timestamps — a later session "
        "could change whether the badge deserves to be unlocked yet"
    )
