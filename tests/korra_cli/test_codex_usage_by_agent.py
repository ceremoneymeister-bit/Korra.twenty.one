"""K21-321: real per-profile accounting, bounded read-only dashboard scans."""
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.background_review import _record_review_usage_to_parent
from korra_cli import dashboard_state as ds
from korra_state import SessionDB

pytestmark = pytest.mark.usefixtures("no_real_network")
NOW = 1_800_000_000.0
WEEK = 7 * 86400
START = NOW - 3 * 86400
WINDOWS = [{"window_minutes": 300, "resets_at": NOW + 3600},
           {"window_minutes": 10080, "resets_at": START + WEEK}]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ds.reset_cache()
    yield
    ds.reset_cache()


def agent(tmp_path, name):
    home = tmp_path if name == "" else tmp_path / "profiles" / name
    home.mkdir(parents=True, exist_ok=True)
    return ds.Agent(name, name or "default", home, name or "Корра")


def opened(a, monkeypatch, since=START - WEEK):
    with monkeypatch.context() as m:
        m.setattr(time, "time", lambda: since)
        return SessionDB(a.home / "state.db")


def call(db, monkeypatch, at, tokens, provider="openai-codex", **kwargs):
    with monkeypatch.context() as m:
        m.setattr(time, "time", lambda: at)
        db.update_token_counts("long-session", output_tokens=tokens,
                               billing_provider=provider, api_call_count=1, **kwargs)


def test_long_session_counts_only_window_and_subscription_including_learning(tmp_path, monkeypatch):
    main, writer = agent(tmp_path, ""), agent(tmp_path, "writer")
    with opened(main, monkeypatch) as db:
        call(db, monkeypatch, START - 1, 9000)
        call(db, monkeypatch, START, 100, reasoning_tokens=80, cache_read_tokens=1_000_000)
        call(db, monkeypatch, NOW - 1, 200, provider="openai")
        call(db, monkeypatch, NOW + 1, 5000)
        db.update_token_counts("long-session", output_tokens=999999, api_call_count=99,
                               billing_provider="openai-codex", absolute=True)
    with opened(writer, monkeypatch) as db:
        call(db, monkeypatch, NOW - 2, 200)
        with monkeypatch.context() as m:
            m.setattr(time, "time", lambda: NOW - 1)
            _record_review_usage_to_parent(SimpleNamespace(_session_db=db, session_id="learning"),
                {"provider": "openai-codex", "api_calls": 2, "output_tokens": 100})
    result = ds.codex_usage_by_agent([main, writer], WINDOWS, now=NOW)
    assert result["period"]["starts_at"] == START
    assert result["status"] == "ok"
    assert result["total"] == {"calls": 4, "output_tokens": 400}
    assert [(r["profile"], r["calls"], r["share_percent"]) for r in result["agents"]] == [
        ("writer", 3, 75), ("", 1, 25)]
    assert result["measure"] == "output_tokens"


def test_queue_preserves_individual_times_across_window_and_coalescing(tmp_path, monkeypatch):
    a = agent(tmp_path, "")
    with opened(a, monkeypatch) as db:
        entered, release = threading.Event(), threading.Event()
        original = db._apply_token_batch
        def delayed(batch):
            entered.set()
            assert release.wait(5)
            original(batch)
        monkeypatch.setattr(db, "_apply_token_batch", delayed)
        try:
            for at, tokens in [(START - 1, 500), (START, 100), (NOW, 200)]:
                with monkeypatch.context() as m:
                    m.setattr(time, "time", lambda: at)
                    db.queue_token_counts("s", billing_provider="openai-codex", output_tokens=tokens, api_call_count=1)
                assert entered.wait(5)
        finally:
            release.set()
        assert db.flush_token_counts()
        assert db._conn.execute("SELECT COUNT(*) FROM codex_usage_events").fetchone()[0] == 3
    assert ds.codex_usage_by_agent([a], WINDOWS, now=NOW)["total"] == {"calls": 2, "output_tokens": 300}


def test_empty_week_and_rolling_fallback(tmp_path, monkeypatch):
    a = agent(tmp_path, "")
    with opened(a, monkeypatch) as db:
        call(db, monkeypatch, NOW - WEEK - 1, 100)
        call(db, monkeypatch, NOW - 1, 300, provider="anthropic")
    result = ds.codex_usage_by_agent([a], [], now=NOW)
    assert result["status"] == "ok"
    assert result["period"]["starts_at"] == NOW - WEEK
    assert result["total"] == {"calls": 0, "output_tokens": 0}
    assert result["agents"][0]["share_percent"] == 0


def test_old_and_corrupt_databases_are_not_reported_as_empty(tmp_path, monkeypatch):
    main, old, broken = [agent(tmp_path, n) for n in ("", "old", "broken")]
    with opened(main, monkeypatch) as db:
        call(db, monkeypatch, NOW - 1, 100)
    with sqlite3.connect(old.home / "state.db") as conn:
        conn.execute("CREATE TABLE sessions (id TEXT)")
    (broken.home / "state.db").write_bytes(b"not a database")
    before = (old.home / "state.db").read_bytes()
    result = ds.codex_usage_by_agent([main, old, broken], WINDOWS, now=NOW)
    assert result["status"] == "partial"
    assert result["unreadable"] == ["broken"]
    assert result["total"]["output_tokens"] == 100
    assert result["agents"][0]["share_percent"] == 100
    assert {r["status"] for r in result["agents"]} == {"ok", "untracked", "error"}
    assert (old.home / "state.db").read_bytes() == before


def test_upgrade_does_not_backfill_lifetime_usage_and_marks_partial(tmp_path, monkeypatch):
    a = agent(tmp_path, "")
    with opened(a, monkeypatch) as db:
        call(db, monkeypatch, START - 1, 99999)
        db._conn.execute("DROP TABLE codex_usage_events")
        db._conn.execute("DELETE FROM state_meta WHERE key='codex_usage_since'")
        db._conn.commit()
    with opened(a, monkeypatch, since=NOW - 100) as db:
        call(db, monkeypatch, NOW - 10, 50)
    result = ds.codex_usage_by_agent([a], WINDOWS, now=NOW)
    assert result["status"] == "partial"
    assert result["agents"][0]["tracked_since"] == NOW - 100
    assert result["total"] == {"calls": 1, "output_tokens": 50}


def test_cached_for_minutes_but_new_window_and_roster_invalidate(tmp_path, monkeypatch):
    a = agent(tmp_path, "")
    with opened(a, monkeypatch) as db:
        call(db, monkeypatch, NOW - 1, 10)
    first = ds.codex_usage_by_agent([a], WINDOWS, now=NOW)
    with opened(a, monkeypatch) as db:
        call(db, monkeypatch, NOW, 20)
    assert ds.codex_usage_by_agent([a], WINDOWS, now=NOW + 60) is first
    with monkeypatch.context() as m:
        m.setattr(time, "time", lambda: time_now + 121)
        time_now = ds._cache[next(k for k in ds._cache if k[0] == "codex-usage")][0]
        assert ds.codex_usage_by_agent([a], WINDOWS, now=NOW)["total"]["calls"] == 2
    assert ds.codex_usage_by_agent([], WINDOWS, now=NOW)["total"]["calls"] == 0
    newer = [{"window_minutes": 10080, "resets_at": NOW + WEEK}]
    assert ds.codex_usage_by_agent([a], newer, now=NOW)["total"]["calls"] == 1


def test_25_profiles_read_only_and_cached(tmp_path, monkeypatch):
    agents = [agent(tmp_path, f"a{i}") for i in range(25)]
    for a in agents:
        with opened(a, monkeypatch) as db:
            call(db, monkeypatch, NOW - 1, 10)
    started = time.monotonic()
    result = ds.codex_usage_by_agent(agents, WINDOWS, now=NOW)
    assert result["total"] == {"calls": 25, "output_tokens": 250}
    assert time.monotonic() - started < 2
    def unexpected(*args, **kwargs):
        raise AssertionError("cached scan must not reopen databases")
    monkeypatch.setattr(ds, "_ro", unexpected)
    assert ds.codex_usage_by_agent(agents, WINDOWS, now=NOW + 60) is result


def test_roster_uses_dashboard_names_and_excludes_deleted_and_hidden_dirs(tmp_path, monkeypatch):
    from korra_constants import mark_named_profile_deleted

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for name in ("writer", "deleted", ".hidden"):
        a = agent(home, name)
        with opened(a, monkeypatch) as db:
            call(db, monkeypatch, NOW - 1, 100)
    (home / "profiles/writer/profile.yaml").write_text("display_name: Автор\n", encoding="utf-8")
    mark_named_profile_deleted(home / "profiles/deleted")
    roster = ds.list_agents()
    result = ds.codex_usage_by_agent(roster, WINDOWS, now=NOW)
    assert {(r["profile"], r["name"]) for r in result["agents"]} == {("", "Корра"), ("writer", "Автор")}
    assert result["total"] == {"calls": 1, "output_tokens": 100}


def test_locked_profile_is_partial_and_does_not_wait_for_writer(tmp_path):
    a = agent(tmp_path, "locked")
    with sqlite3.connect(a.home / "state.db") as writer:
        writer.execute("CREATE TABLE example (id INTEGER)")
        writer.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        result = ds.codex_usage_by_agent([a], WINDOWS, now=NOW)
        assert time.monotonic() - started < 2
        assert result["unreadable"] == ["locked"]
        assert result["agents"][0]["calls"] is None
        assert result["status"] == "partial"
