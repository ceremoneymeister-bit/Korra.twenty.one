"""Retained transcripts must not lose their full tool results to cache cleanup."""

import os
import time

import pytest

from korra_state import SessionDB
from tools import tool_result_storage as storage


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def spill(home, name, days):
    path = home / "cache" / "spillover" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("complete result", encoding="utf-8")
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))
    return path


@pytest.mark.parametrize("kind", ["text", "api_content", "structured", "backslash"])
def test_retained_transcript_keeps_old_archive(home, kind):
    archive = spill(home, "owned.txt", 200)
    db = SessionDB(db_path=home / "state.db")
    try:
        db.create_session("owner", "cli")
        pointer = str(archive)
        kwargs = {}
        if kind == "api_content":
            kwargs["api_content"] = pointer
            pointer = "preview only"
        elif kind == "structured":
            pointer = [{"type": "text", "text": pointer}]
        elif kind == "backslash":
            pointer = pointer.replace("/", "\\")
        db.append_message("owner", "tool", pointer, **kwargs)
        assert storage.cleanup_spillover_cache(max_age_hours=24) == 0
        assert archive.read_text() == "complete result"
        db.delete_session("owner")
        # The ordinary orphan sweep reclaims it once no retained session needs it.
        assert storage.cleanup_spillover_cache(max_age_hours=24) == 1
    finally:
        db.close()


def test_unreadable_reference_database_keeps_archives(home):
    archive = spill(home, "unknown.txt", 200)
    (home / "state.db").write_bytes(b"not a sqlite database")
    assert storage.cleanup_spillover_cache(max_age_hours=24) == 0
    assert archive.exists()


def test_default_retention_preserves_recent_orphan_but_reclaims_old(home):
    (home / "config.yaml").write_text("sessions:\n  retention_days: 7\n")
    recent = spill(home, "recent.txt", 2)
    expired = spill(home, "expired.txt", 8)
    assert storage.cleanup_spillover_cache() == 1
    assert recent.exists() and not expired.exists()


@pytest.mark.parametrize("days", ["0", "-1", ".inf", ".nan", "bad"])
def test_invalid_or_zero_retention_never_deletes_fresh_result(home, days):
    (home / "config.yaml").write_text(f"sessions:\n  retention_days: {days}\n")
    fresh = spill(home, "fresh.txt", 0)
    assert storage.cleanup_spillover_cache() == 0
    assert fresh.exists()


def test_profile_cleanup_uses_its_own_references(home):
    from korra_constants import reset_hermes_home_override, set_hermes_home_override

    archive = spill(home, "same.txt", 200)
    sibling = home / "profiles" / "sibling"
    other = spill(sibling, "same.txt", 200)
    db = SessionDB(db_path=home / "state.db")
    try:
        db.create_session("owner", "cli")
        db.append_message("owner", "tool", str(archive))
        token = set_hermes_home_override(sibling)
        try:
            assert storage.cleanup_spillover_cache(max_age_hours=24) == 1
        finally:
            reset_hermes_home_override(token)
        assert not other.exists() and archive.exists()
        assert storage.cleanup_spillover_cache(max_age_hours=24) == 0
    finally:
        db.close()


def test_gateway_hourly_cleanup_respects_archive_retention(home, monkeypatch):
    import gateway.run as gateway_run

    (home / "config.yaml").write_text("sessions:\n  retention_days: 7\n")
    archive = spill(home, "recent.txt", 2)
    for target in (
        "agent.curator.maybe_run_curator",
        "tools.skills_sync_client.maybe_pull_skills",
        "tools.skills_sync_client.maybe_pull_org_skills",
        "korra_cli.mem_trim.trim_memory",
    ):
        monkeypatch.setattr(target, lambda *args, **kwargs: None)

    class OneHour:
        ticks = 0

        def is_set(self):
            return self.ticks >= 60

        def wait(self, timeout=None):
            self.ticks += 1

    gateway_run._start_gateway_housekeeping(OneHour(), interval=0)
    assert archive.read_text() == "complete result"
