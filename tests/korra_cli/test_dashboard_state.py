"""0.21.13: live data behind the dashboard cards (``/api/dashboard/state``).

Every store here is created with the product's own schema code and then read
back through the dashboard layer, so the tests pin how the two must relate:
what the owner sees is exactly what the installation recorded — an unreadable
source is an error for that source, never a quiet zero.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from korra_cli import dashboard_state as ds

# Nothing here may reach chatgpt.com: a socket to a foreign host fails the test.
pytestmark = pytest.mark.usefixtures("no_real_network")

MSK = ZoneInfo("Europe/Moscow")
# Wednesday 23.09.2026 13:00 in Moscow.
NOW = datetime(2026, 9, 23, 13, 0, tzinfo=MSK).timestamp()


@pytest.fixture(autouse=True)
def _fresh_cache():
    ds.reset_cache()
    yield
    ds.reset_cache()


def _agent(root: Path, name: str, label: str) -> ds.Agent:
    home = root if name == "default" else root / "profiles" / name
    home.mkdir(parents=True, exist_ok=True)
    return ds.Agent(profile=ds._wire(name), name=name, home=home, label=label)


def _state_db(home: Path) -> sqlite3.Connection:
    from korra_state import SessionDB

    db = SessionDB(db_path=home / "state.db")
    db.close()
    conn = sqlite3.connect(home / "state.db")
    return conn


def _session(conn, sid, *, at, source="dashboard", parent=None, messages=4, tokens=(100, 50), key=None):
    conn.execute(
        "INSERT INTO sessions (id, source, started_at, parent_session_id, message_count, "
        "input_tokens, output_tokens, session_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (sid, source, at, parent, messages, tokens[0], tokens[1], key),
    )


def _msk(day: int, hour: int, minute: int = 0) -> float:
    return datetime(2026, 9, day, hour, minute, tzinfo=MSK).timestamp()


# ── Metrics ────────────────────────────────────────────────────────────────


def test_metrics_count_owner_dialogs_by_local_day_and_keep_background_apart(tmp_path):
    main = _agent(tmp_path, "default", "Корра")
    writer = _agent(tmp_path, "writer", "Автор")
    conn = _state_db(main.home)
    # Today (Wed) two dialogs, one of them 00:30 Moscow = still "today" locally
    # although it is 21:30 UTC of the previous day.
    _session(conn, "a", at=_msk(23, 0, 30), source="dashboard", messages=6)
    _session(conn, "b", at=_msk(23, 11), source="telegram", messages=2)
    # Children, background and service sessions are not dialogs.
    _session(conn, "a-child", at=_msk(23, 1), source="dashboard", parent="a")
    _session(conn, "c", at=_msk(22, 9), source="cron")
    _session(conn, "k", at=_msk(21, 9), source="kanban")
    _session(conn, "m", at=_msk(23, 3), source="maintenance", tokens=(9999, 9999))
    # Previous week: one dialog.
    _session(conn, "old", at=_msk(15, 10), source="dashboard")
    conn.commit()
    conn.close()
    other = _state_db(writer.home)
    _session(other, "w", at=_msk(20, 18), source="dashboard", messages=10)
    other.commit()
    other.close()

    value = ds.metrics_section([main, writer], period="week", now=NOW, tz=MSK)

    assert value["status"] == "ok"
    assert value["labels"][-1] == "2026-09-23" and len(value["labels"]) == 7
    assert value["series"]["dialogs"] == [0, 0, 0, 1, 0, 0, 2]  # 17…23 Sep
    assert value["totals"]["dialogs"] == 3
    assert value["totals"]["messages"] == 6 + 2 + 10
    assert value["totals"]["background"] == 2
    assert value["previous"]["dialogs"] == 1
    # Maintenance smoke is neither a dialog nor billed work of the owner.
    assert value["totals"]["tokens"] == 150 * 6


def test_metrics_month_period_and_empty_installation(tmp_path):
    main = _agent(tmp_path, "default", "Корра")
    value = ds.metrics_section([main], period="month", now=NOW, tz=MSK)
    assert value["status"] == "empty"
    assert value["days"] == 30 and len(value["series"]["dialogs"]) == 30


def test_unreadable_store_is_an_error_not_a_zero(tmp_path):
    main = _agent(tmp_path, "default", "Корра")
    (main.home / "state.db").write_bytes(b"not a database at all" * 10)
    value = ds.metrics_section([main], period="week", now=NOW, tz=MSK)
    assert value["status"] == "error"
    assert value["unreadable"] == ["Корра"]


def _data_snapshot(root: Path) -> dict[str, int]:
    """mtime of every file except SQLite's WAL sidecars.

    Opening a quiescent WAL database even with ``mode=ro`` makes SQLite create
    an empty ``-wal`` and its ``-shm`` index (3.50+; CI runs 3.53). That is
    SQLite bookkeeping, not a write: the data files must stay untouched and
    any journal that appears must stay empty (checked by the callers).
    """
    return {str(p): p.stat().st_mtime_ns for p in root.rglob("*")
            if p.is_file() and not p.name.endswith(("-wal", "-shm"))}


def _no_journal_written(root: Path, before: set[str]) -> bool:
    return all(p.stat().st_size == 0 for p in root.rglob("*-wal") if str(p) not in before)


def test_metrics_never_write_to_the_store(tmp_path):
    main = _agent(tmp_path, "default", "Корра")
    conn = _state_db(main.home)
    _session(conn, "a", at=_msk(23, 10))
    conn.commit()
    conn.close()
    wal_before = {str(p) for p in main.home.rglob("*-wal")}
    before = _data_snapshot(main.home)
    ds.metrics_section([main], period="week", now=NOW, tz=MSK)
    ds.agents_section([main])
    assert _data_snapshot(main.home) == before
    assert _no_journal_written(main.home, wal_before)


def test_agents_section_reports_last_activity(tmp_path):
    main = _agent(tmp_path, "default", "Корра")
    idle = _agent(tmp_path, "idle", "Молчун")
    conn = _state_db(main.home)
    _session(conn, "a", at=_msk(23, 9))
    _session(conn, "m", at=_msk(23, 12), source="maintenance")
    conn.commit()
    conn.close()
    value = ds.agents_section([main, idle])
    rows = {row["profile"]: row for row in value["agents"]}
    assert rows[""]["last_active_at"] == _msk(23, 9)
    assert rows["idle"]["last_active_at"] is None


# ── Upcoming ───────────────────────────────────────────────────────────────


def _jobs(home: Path, jobs: list[dict]) -> None:
    (home / "cron").mkdir(parents=True, exist_ok=True)
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": jobs}), encoding="utf-8")


def _job(job_id, name, schedule, next_run, **extra):
    return {"id": job_id, "name": name, "prompt": name, "schedule": schedule,
            "next_run_at": next_run, "enabled": True, "state": "scheduled", **extra}


def _executions(home: Path, rows: list[tuple]) -> None:
    from cron import executions

    (home / "cron").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(home / "cron" / "executions.db")
    executions._initialize_schema(conn)
    for index, (job_id, status, started) in enumerate(rows):
        conn.execute(
            "INSERT INTO executions (id, job_id, source, process_id, pid, status, claimed_at, started_at) "
            "VALUES (?, ?, 'scheduler', 'p', 1, ?, ?, ?)",
            (f"e{index}", job_id, status, started, started),
        )
    conn.commit()
    conn.close()


def test_day_timeline_joins_runs_done_today_with_what_is_still_planned(tmp_path):
    main = _agent(tmp_path, "default", "Корра")
    iso = lambda day, hour, minute=0: datetime(2026, 9, day, hour, minute, tzinfo=MSK).isoformat()
    _jobs(main.home, [
        _job("morning", "Утренняя сводка", {"kind": "cron", "expr": "0 9 * * *"}, iso(24, 9)),
        _job("evening", "Итоги дня", {"kind": "cron", "expr": "0 18 * * *"}, iso(23, 18)),
        _job("often", "Проверка почты", {"kind": "interval", "minutes": 60}, iso(23, 13, 30)),
        _job("paused", "На паузе", {"kind": "cron", "expr": "0 15 * * *"}, iso(23, 15),
             enabled=False, state="paused", paused_at=iso(20, 1)),
        _job("once", "Разовое напоминание", {"kind": "once", "run_at": iso(25, 10)}, iso(25, 10)),
    ])
    _executions(main.home, [
        ("morning", "completed", iso(23, 9, 0)),
        ("often", "failed", iso(23, 12, 30)),
        ("often", "completed", iso(23, 11, 30)),
        ("morning", "completed", iso(22, 9, 0)),  # yesterday — not today's run
    ])

    value = ds.upcoming_section([main], now=NOW, tz=MSK)

    assert value["status"] == "ok"
    assert value["jobs_total"] == 5 and value["jobs_active"] == 4
    by_title = {(event["title"], event["state"]): event for event in value["events"]}
    assert ("Утренняя сводка", "done") in by_title
    assert ("Итоги дня", "planned") in by_title
    assert by_title[("Итоги дня", "planned")]["at"] == _msk(23, 18)
    # 13:30 … 23:30 — eleven runs today collapse into one series row.
    series = by_title[("Проверка почты", "planned")]
    assert series["repeats_today"] == 11
    latest = by_title[("Проверка почты", "failed")]
    assert latest["runs_today"] == 2 and latest["failed_today"] == 1
    assert not any(event["title"] == "На паузе" for event in value["events"])
    # Sorted by time, every event says whose it is and where to look.
    assert [event["at"] for event in value["events"]] == sorted(event["at"] for event in value["events"])
    assert all(event["agent"] == "Корра" and event["href"] == "/cron" for event in value["events"])
    # The week strip counts what is planned per local day.
    assert value["week"][0]["date"] == "2026-09-23"
    assert value["week"][0]["done"] == 2 and value["week"][0]["failed"] == 1
    assert value["week"][1]["planned"] >= 1 + 24  # morning + hourly checks
    assert value["week"][2]["planned"] >= 1 + 1 + 24  # + one-shot
    assert value["next_later"]["title"] in {"Утренняя сводка", "Проверка почты"}


def test_overdue_run_is_reported_late_once(tmp_path):
    main = _agent(tmp_path, "default", "Корра")
    stale = datetime(2026, 9, 23, 10, 0, tzinfo=MSK).isoformat()
    _jobs(main.home, [_job("stuck", "Отчёт", {"kind": "interval", "minutes": 30}, stale)])
    value = ds.upcoming_section([main], now=NOW, tz=MSK)
    rows = [event for event in value["events"] if event["title"] == "Отчёт"]
    assert len(rows) == 1 and rows[0]["state"] == "late" and rows[0]["at"] == _msk(23, 10)


def test_no_schedule_is_empty_and_broken_jobs_file_is_an_error(tmp_path):
    main = _agent(tmp_path, "default", "Корра")
    assert ds.upcoming_section([main], now=NOW, tz=MSK)["status"] == "empty"
    (main.home / "cron").mkdir()
    (main.home / "cron" / "jobs.json").write_text("{not json", encoding="utf-8")
    broken = ds.upcoming_section([main], now=NOW, tz=MSK)
    assert broken["status"] == "error" and broken["unreadable"] == ["Корра"]


# ── Artifacts ──────────────────────────────────────────────────────────────


def _touch(path: Path, text: str = "x", *, at: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (at, at))
    return path


def test_artifacts_follow_the_file_organization_and_skip_uploads_and_secrets(tmp_path):
    from korra_cli.file_organization import ensure_agent_results

    root = tmp_path / "workspace"
    main = _agent(tmp_path, "default", "Корра")
    writer = _agent(tmp_path, "writer", "Автор")
    results = ensure_agent_results(root, "writer")
    _touch(results / "Отчёт.md", "# Итоги недели\n\n- Выручка выросла\n- Новых клиентов: 3\n", at=NOW - 60)
    _touch(results / "deck" / "Презентация.pptx", at=NOW - 120)
    _touch(root / "shared" / "Прайс.xlsx", at=NOW - 180)
    _touch(root / "Старый план.docx", at=NOW - 30 * 86400)
    _touch(root / "client" / "inbox" / "2026-09-23" / "photo.jpg", at=NOW - 10)  # owner upload
    _touch(root / ".env", "SECRET=1", at=NOW - 5)
    _touch(root / "notes" / "~$lock.docx", at=NOW - 5)

    value = ds.artifacts_section([main, writer], root=root, now=NOW)

    assert value["status"] == "ok" and value["organized"] is True
    names = [item["name"] for item in value["items"]]
    assert names == ["Отчёт.md", "Презентация.pptx", "Прайс.xlsx", "Старый план.docx"]
    report = value["items"][0]
    assert report["agent"] == "Автор" and report["profile"] == "writer" and report["section"] == "agent"
    assert report["kind"] == "text" and report["title"] == "Итоги недели"
    assert report["excerpt"] == ["Выручка выросла", "Новых клиентов: 3"]
    assert value["items"][1]["kind"] == "presentation"
    assert value["items"][2]["section"] == "shared" and value["items"][2]["kind"] == "table"
    assert value["items"][3]["section"] == "workspace"
    assert value["recent_count"] == 3
    # Paths are the ones the Files API serves for download.
    assert all(Path(item["path"]).is_file() for item in value["items"])


# R8 (0.21.13 review): a link anywhere below the workspace root must never
# lead the dashboard outside it — neither for listing nor for excerpts. Each
# case runs with the dir_fd/O_NOFOLLOW walk and with the lexical fallback.

SECRET = "Synthetic secret outside workspace"


@pytest.fixture(params=["dir_fd", "lexical"])
def containment(request, monkeypatch):
    if request.param == "lexical":
        monkeypatch.setattr(ds._Workspace, "_FD_SAFE", False)
    elif not ds._Workspace._FD_SAFE:
        pytest.skip("dir_fd walk is not available on this host")
    return request.param


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside-workspace"
    (outside / "results").mkdir(parents=True)
    for folder in (outside, outside / "results"):
        _touch(folder / "private-note.md", f"# Private\n{SECRET}\n", at=NOW - 1)
    return outside


def _leaks(value: dict) -> bool:
    return any(
        SECRET in " ".join(item.get("excerpt") or []) or "outside-workspace" in item["path"]
        or item["name"] == "private-note.md"
        for item in value["items"]
    )


def _organized(tmp_path: Path) -> tuple[Path, Path]:
    from korra_cli.file_organization import ensure_agent_results

    root = tmp_path / "workspace"
    results = ensure_agent_results(root, "writer")
    _touch(root / "Легитимный.md", "# Свой файл\nвнутри\n", at=NOW - 30)
    return root, results


def test_results_root_link_does_not_leave_the_workspace(tmp_path, containment):
    root, results = _organized(tmp_path)
    outside = _outside(tmp_path)
    results.rmdir()
    results.symlink_to(outside, target_is_directory=True)

    value = ds.artifacts_section([], root=root, now=NOW)

    assert not _leaks(value)
    assert [item["name"] for item in value["items"]] == ["Легитимный.md"]


def test_shared_link_does_not_leave_the_workspace(tmp_path, containment):
    root, _results = _organized(tmp_path)
    outside = _outside(tmp_path)
    (root / "shared").rmdir()
    (root / "shared").symlink_to(outside, target_is_directory=True)
    assert not _leaks(ds.artifacts_section([], root=root, now=NOW))


@pytest.mark.parametrize("level", ["agents", "key"])
def test_intermediate_link_does_not_leave_the_workspace(tmp_path, containment, level):
    root, results = _organized(tmp_path)
    outside = _outside(tmp_path)
    key_dir = results.parent
    if level == "key":
        results.rmdir()
        key_dir.rmdir()
        key_dir.symlink_to(outside, target_is_directory=True)
    else:
        agents = root / "agents"
        moved = tmp_path / "moved-agents"
        agents.rename(moved)
        # The link target mirrors the real layout, so the walk would find
        # agents/<key>/results/private-note.md if it followed the link.
        (outside / key_dir.name / "results").mkdir(parents=True)
        _touch(outside / key_dir.name / "results" / "private-note.md", f"# Private\n{SECRET}\n", at=NOW - 1)
        agents.symlink_to(outside, target_is_directory=True)
    assert not _leaks(ds.artifacts_section([], root=root, now=NOW))


def test_file_and_folder_links_inside_results_are_skipped(tmp_path, containment):
    root, results = _organized(tmp_path)
    outside = _outside(tmp_path)
    (results / "note.md").symlink_to(outside / "private-note.md")
    (results / "linked").symlink_to(outside, target_is_directory=True)
    assert not _leaks(ds.artifacts_section([], root=root, now=NOW))


def test_excerpt_refuses_a_link_swapped_in_after_the_walk(tmp_path, containment):
    """The read re-opens the path without following links (open-time check)."""
    root, results = _organized(tmp_path)
    outside = _outside(tmp_path)
    _touch(results / "report.md", "# Отчёт\nсвоё\n", at=NOW - 5)
    with ds._Workspace(root) as workspace:
        parts = ("agents", results.parent.name, "results", "report.md")
        assert workspace.read_head(parts).startswith("# Отчёт".encode())
        (results / "report.md").unlink()
        (results / "report.md").symlink_to(outside / "private-note.md")
        assert workspace.read_head(parts) is None
        # A parent folder swapped for a link is refused the same way.
        (results / "report.md").unlink()
        results.rmdir()
        results.symlink_to(outside / "results", target_is_directory=True)
        assert workspace.read_head(("agents", results.parent.name, "results", "private-note.md")) is None


def test_linked_registry_is_not_trusted(tmp_path, containment):
    root, _results = _organized(tmp_path)
    fake = tmp_path / "fake-index"
    fake.mkdir()
    (fake / "file-organization-v1.json").write_text(
        json.dumps({"version": 1, "profiles": {"leak": "0123456789abcdef"}, "archived": {}}), encoding="utf-8")
    index = root / ".index"
    for child in index.iterdir():
        child.unlink()
    index.rmdir()
    index.symlink_to(fake, target_is_directory=True)
    value = ds.artifacts_section([], root=root, now=NOW)
    assert value["organized"] is False


def test_workspace_root_may_itself_be_a_link(tmp_path, containment):
    """Like the Files root, the workspace root is resolved once and allowed."""
    real = tmp_path / "volume" / "workspace"
    _touch(real / "План.docx", at=NOW - 10)
    link = tmp_path / "workspace"
    link.symlink_to(real, target_is_directory=True)
    value = ds.artifacts_section([], root=link, now=NOW)
    assert [item["name"] for item in value["items"]] == ["План.docx"]
    assert value["items"][0]["path"] == str(real.resolve() / "План.docx")


def test_artifacts_walk_is_bounded(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    for index in range(50):
        _touch(root / f"file-{index:02d}.txt", at=NOW - index)
    monkeypatch.setattr(ds, "_SCAN_BUDGET", 10)
    value = ds.artifacts_section([], root=root, now=NOW)
    assert value["truncated"] is True and len(value["items"]) <= 10


def test_artifacts_empty_workspace_is_a_first_step_not_an_error(tmp_path):
    assert ds.artifacts_section([], root=tmp_path / "missing", now=NOW)["status"] == "empty"
    (tmp_path / "workspace").mkdir()
    assert ds.artifacts_section([], root=tmp_path / "workspace", now=NOW)["status"] == "empty"


# ── Codex quota ────────────────────────────────────────────────────────────


def _quota(root: Path, used: float, *, resets_in: float = 86400, captured: float = NOW) -> None:
    from agent.rate_limit_tracker import CodexQuotaSnapshot, CodexQuotaWindow, record_codex_quota

    record_codex_quota(
        CodexQuotaSnapshot(
            primary=CodexQuotaWindow(used_percent=used, window_minutes=10080, resets_at=NOW + resets_in),
            plan_type="pro",
            captured_at=captured,
            source="headers",
        ),
        root=root,
    )


def _connect_codex(home: Path) -> None:
    """A ChatGPT subscription login, stored the way ``korra auth`` stores it."""
    tokens = {"access_token": "at-test", "refresh_token": "rt-test"}
    (home / "auth.json").write_text(json.dumps({
        "version": 1,
        "active_provider": "openai-codex",
        "providers": {"openai-codex": {"tokens": tokens, "last_refresh": "2026-09-23T10:00:00Z"}},
        "credential_pool": {"openai-codex": [{"id": "p1", "auth_type": "oauth", **tokens}]},
    }), encoding="utf-8")


def _codex_model(home: Path) -> None:
    (home / "config.yaml").write_text("model:\n  default: gpt-5\n  provider: openai-codex\n", encoding="utf-8")


def test_quota_card_is_absent_without_a_subscription(tmp_path):
    main = _agent(tmp_path, "default", "Корра")
    assert ds.quota_section([main], now=NOW, root=tmp_path) == {"available": False, "status": "absent"}


def test_quota_waits_for_the_first_reply_when_codex_is_connected(tmp_path):
    main = _agent(tmp_path, "default", "Корра")
    _codex_model(main.home)
    _connect_codex(main.home)
    assert ds.quota_section([main], now=NOW, root=tmp_path) == {"available": True, "status": "waiting"}


def test_a_profile_on_the_installation_login_counts_as_connected(tmp_path):
    # The profile has no login of its own and uses the root one, as the
    # runtime does; the root agent itself need not be on the roster.
    _connect_codex(tmp_path)
    writer = _agent(tmp_path, "writer", "Автор")
    _codex_model(writer.home)
    assert ds.quota_section([writer], now=NOW, root=tmp_path)["available"] is True


@pytest.mark.parametrize(("used", "level"), [(55.0, "normal"), (80.0, "warn"), (96.0, "critical")])
def test_quota_levels(tmp_path, used, level):
    main = _agent(tmp_path, "default", "Корра")
    _connect_codex(main.home)
    _quota(tmp_path, used)
    value = ds.quota_section([main], now=NOW, root=tmp_path)
    assert value["status"] == "ok" and value["level"] == level
    assert value["used_percent"] == used and value["window_label"] == "неделя"
    assert value["resets_at"] == NOW + 86400


def test_quota_after_the_window_reset_is_a_full_limit(tmp_path):
    main = _agent(tmp_path, "default", "Корра")
    _connect_codex(main.home)
    _quota(tmp_path, 99.0, resets_in=-60)
    value = ds.quota_section([main], now=NOW, root=tmp_path)
    week = 10080 * 60
    assert value["status"] == "ok" and value["level"] == "normal"
    window = value["windows"][0]
    assert window["used_percent"] == 0 and window["remaining_percent"] == 100
    assert window["renewed"] is True and window["resets_at"] == NOW - 60 + week
    # Several windows passed unnoticed: the next reset is the first one ahead.
    _quota(tmp_path, 99.0, resets_in=-60 - 2 * week)
    ds.reset_cache()
    assert ds.quota_section([main], now=NOW, root=tmp_path)["windows"][0]["resets_at"] == NOW - 60 + week


def test_old_quota_is_not_shown_without_a_subscription(tmp_path):
    """Audit P1: a snapshot left behind must not revive the card."""
    _quota(tmp_path, 96.0)
    assert ds.quota_section([], now=NOW, root=tmp_path) == {"available": False, "status": "absent"}
    # Choosing a Codex model is not a subscription: without a stored login
    # the agent cannot answer, and the percent is history.
    main = _agent(tmp_path, "default", "Корра")
    _codex_model(main.home)
    ds.reset_cache()
    assert ds.quota_section([main], now=NOW, root=tmp_path) == {"available": False, "status": "absent"}


def test_disconnecting_the_subscription_hides_the_last_percent(tmp_path, monkeypatch):
    """Disconnect through the product's own path, then read the card again."""
    from korra_cli.auth import clear_provider_auth

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    main = _agent(tmp_path, "default", "Корра")
    _codex_model(main.home)
    _connect_codex(main.home)
    _quota(tmp_path, 96.0)
    before = ds.quota_section([main], now=NOW, root=tmp_path)
    assert before["available"] is True and before["level"] == "critical"

    assert clear_provider_auth("openai-codex") is True
    # The model choice stays behind in config.yaml; the login is gone.
    assert "openai-codex" in (main.home / "config.yaml").read_text(encoding="utf-8")
    after = ds.quota_section([main], now=NOW, root=tmp_path)
    assert after == {"available": False, "status": "absent"}
    attention = ds.attention_section([main], now=NOW, tz=MSK, quota=after)
    assert "quota" not in attention["checked"]
    assert not [item for item in attention["items"] if item["source"] == "quota"]


# ── Attention ──────────────────────────────────────────────────────────────


def _board_in_use(root: Path, monkeypatch) -> None:
    """The owner has opened the kanban board at least once."""
    monkeypatch.setenv("KORRA_KANBAN_HOME", str(root))
    (root / "kanban.db").touch()


def test_unused_board_is_not_created_by_the_dashboard(tmp_path, monkeypatch):
    board = types.ModuleType("hermes_dashboard_plugin_kanban")
    board.get_attention = lambda limit=50: pytest.fail("an unused board must not be opened")
    monkeypatch.setitem(sys.modules, "hermes_dashboard_plugin_kanban", board)
    monkeypatch.setenv("KORRA_KANBAN_HOME", str(tmp_path))
    assert ds._kanban_items([]) == ([], [], 0)
    assert not (tmp_path / "kanban.db").exists()


def test_attention_lists_real_problems_with_links_to_the_exact_place(tmp_path, monkeypatch):
    from cron import incidents
    from gateway import delivery_ledger

    main = _agent(tmp_path, "default", "Корра")
    conn = _state_db(main.home)
    _session(conn, "tg-1", at=NOW - 3600, source="telegram", key="agent:main:telegram:dm:1")
    delivery_ledger._initialize_schema(conn)
    conn.execute(
        "INSERT INTO delivery_obligations (obligation_id, session_key, platform, chat_id, content, "
        "state, attempts, created_at, updated_at) VALUES "
        "('o1', 'agent:main:telegram:dm:1', 'telegram', '1', 'Готов отчёт за неделю', 'failed', 1, ?, ?),"
        "('o2', 'agent:main:telegram:dm:1', 'telegram', '1', 'Доставлено', 'delivered', 1, ?, ?)",
        (NOW - 600, NOW - 600, NOW - 600, NOW - 600),
    )
    conn.commit()
    conn.close()

    iso = lambda day, hour: datetime(2026, 9, day, hour, tzinfo=MSK).isoformat()
    _jobs(main.home, [
        _job("live", "Сводка", {"kind": "cron", "expr": "0 9 * * *"}, iso(24, 9)),
        _job("off", "Старое", {"kind": "cron", "expr": "0 9 * * *"}, iso(24, 9),
             enabled=False, state="paused", paused_at=iso(1, 1)),
    ])
    inc = sqlite3.connect(main.home / "cron" / "executions.db")
    incidents._initialize_schema(inc)
    for job_id in ("live", "off"):
        inc.execute(
            "INSERT INTO cron_incidents (id, job_id, error_sig, state, failure_type, first_seen_at, "
            "last_seen_at, error) VALUES (?, ?, 'sig', 'alerted', 'auth', ?, ?, '401')",
            (f"i-{job_id}", job_id, iso(23, 9), iso(23, 9)),
        )
    inc.commit()
    inc.close()

    (main.home / "auth.json").write_text(json.dumps({"credential_pool": {
        "openai-codex": [{"id": "a", "last_status": "dead", "last_error_code": 401,
                          "last_error_reason": "token_revoked", "last_status_at": NOW - 100}],
        "openrouter": [{"id": "b", "last_status": "dead"}, {"id": "c", "last_status": "ok"}],
    }}), encoding="utf-8")

    board = types.ModuleType("hermes_dashboard_plugin_kanban")
    board.get_attention = lambda limit=50: {
        "items": [
            {"board": "default", "task_id": "t1", "title": "Смета", "kind": "question",
             "assignee": "default", "question": "Какой бюджет?", "created_at": NOW - 50},
            {"board": "default", "task_id": "t2", "title": "Пауза", "kind": "paused",
             "assignee": None, "created_at": NOW - 40},
        ],
        "counts": {}, "count": 1, "errors": [],
    }
    monkeypatch.setitem(sys.modules, "hermes_dashboard_plugin_kanban", board)
    _board_in_use(tmp_path, monkeypatch)
    monkeypatch.setattr(ds, "_update_items", lambda now: [])

    value = ds.attention_section([main], now=NOW, tz=MSK, quota={"available": False})

    assert value["errors"] == []
    kinds = [item["kind"] for item in value["items"]]
    # The owner's decision comes first, problems after it.
    assert kinds[0] == "kanban_question"
    assert sorted(kinds) == sorted(["kanban_question", "delivery", "cron_incident", "provider_refused"])
    items = {item["kind"]: item for item in value["items"]}
    assert items["kanban_question"]["href"] == "/kanban?board=default&task=t1"
    assert items["kanban_question"]["detail"] == "Вопрос: Какой бюджет?"
    assert items["delivery"]["href"] == "/agents?agent=default&resume=tg-1"
    assert "Telegram" in items["delivery"]["title"]
    assert "Сводка" in items["cron_incident"]["title"]
    assert items["provider_refused"]["title"].startswith("ChatGPT (Codex)")
    assert set(value["checked"]) >= {"kanban", "delivery", "cron", "provider", "updates"}


def _ledger_row(conn, oid, state, profile, key, text, *, age=600):
    conn.execute(
        "INSERT INTO delivery_obligations (obligation_id, session_key, platform, chat_id, content, "
        "state, attempts, created_at, updated_at, adapter_profile) VALUES (?, ?, 'telegram', '1', ?, ?, 1, ?, ?, ?)",
        (oid, key, text, state, NOW - age, NOW - age, profile),
    )


def test_attention_shows_secondary_bot_deliveries_from_the_shared_ledger(tmp_path):
    from gateway import delivery_ledger

    main = _agent(tmp_path, "default", "Корра")
    second = _agent(tmp_path, "second", "Помощник")
    conn = _state_db(main.home)
    delivery_ledger._initialize_schema(conn)
    _ledger_row(conn, "m1", "failed", "default", "agent:main:telegram:dm:1", "ответ основного")
    _ledger_row(conn, "s1", "failed", "second", "agent:second:telegram:dm:2", "ответ помощника")
    _ledger_row(conn, "s2", "delivered", "second", "agent:second:telegram:dm:2", "доставлено")
    conn.commit()
    conn.close()
    sec = _state_db(second.home)
    _session(sec, "sec-1", at=NOW - 3600, source="telegram", key="agent:second:telegram:dm:2")
    delivery_ledger._initialize_schema(sec)
    # Left by 0.21.15 in the profile's own database.
    _ledger_row(sec, "old1", "failed", "second", "agent:second:telegram:dm:2", "старый ответ")
    _ledger_row(sec, "gone", "migrated", "second", "agent:second:telegram:dm:2", "уже перенесён")
    sec.commit()
    sec.close()

    items = ds._delivery_items([main, second], now=NOW)

    by_agent = {}
    for item in items:
        by_agent.setdefault(item["profile"], []).append(item["detail"])
    assert by_agent[""] == ["«ответ основного»"]
    assert set(by_agent["second"]) == {"«старый ответ»", "«ответ помощника»"}
    second_items = [item for item in items if item["profile"] == "second"]
    assert {item["href"] for item in second_items} == {"/agents?agent=second&resume=sec-1"}
    assert len({item["id"] for item in items}) == len(items)


def test_attention_reports_unreadable_sources_instead_of_all_clear(tmp_path, monkeypatch):
    main = _agent(tmp_path, "default", "Корра")
    (main.home / "state.db").write_bytes(b"garbage" * 100)
    board = types.ModuleType("hermes_dashboard_plugin_kanban")

    def broken(limit=50):
        raise RuntimeError("board locked")

    board.get_attention = broken
    monkeypatch.setitem(sys.modules, "hermes_dashboard_plugin_kanban", board)
    _board_in_use(tmp_path, monkeypatch)
    monkeypatch.setattr(ds, "_update_items", lambda now: [])

    value = ds.attention_section([main], now=NOW, tz=MSK, quota={"available": False})

    sources = {error["source"] for error in value["errors"]}
    assert {"kanban", "delivery"} <= sources
    assert value["items"] == []


def test_attention_count_uses_board_totals_not_the_fetched_page(tmp_path, monkeypatch):
    """P2: more than one page of kanban items — the total is the plugin's."""
    main = _agent(tmp_path, "default", "Корра")
    board = types.ModuleType("hermes_dashboard_plugin_kanban")
    page = [
        {"board": "default", "task_id": f"t{index}", "title": f"Задача {index}", "kind": "question",
         "assignee": "default", "question": "?", "created_at": NOW - index}
        for index in range(50)
    ]
    board.get_attention = lambda limit=50: {"items": page[:limit], "counts": {"question": 73},
                                            "count": 73, "errors": []}
    monkeypatch.setitem(sys.modules, "hermes_dashboard_plugin_kanban", board)
    _board_in_use(tmp_path, monkeypatch)
    monkeypatch.setattr(ds, "_update_items", lambda now: [])

    value = ds.attention_section([main], now=NOW, tz=MSK, quota={"available": False})

    assert value["count"] == 73
    assert value["truncated"] is True
    assert len(value["items"]) == ds._ATTENTION_LIMIT


def test_attention_short_list_is_not_marked_truncated(tmp_path, monkeypatch):
    main = _agent(tmp_path, "default", "Корра")
    monkeypatch.setattr(ds, "_update_items", lambda now: [])
    value = ds.attention_section([main], now=NOW, tz=MSK, quota={"available": False})
    assert value["count"] == len(value["items"]) and value["truncated"] is False


def test_attention_raises_the_quota_when_it_is_almost_spent(tmp_path, monkeypatch):
    main = _agent(tmp_path, "default", "Корра")
    monkeypatch.setattr(ds, "_update_items", lambda now: [])
    quota = {"available": True, "status": "ok", "level": "critical", "used_percent": 96.4,
             "resets_at": _msk(24, 14), "captured_at": NOW}
    value = ds.attention_section([main], now=NOW, tz=MSK, quota=quota)
    item = value["items"][0]
    assert item["kind"] == "quota_critical" and "осталось 4 %" in item["title"]
    assert "Квота" not in item["title"] and "завтра в 14:00" in item["detail"]


def test_update_that_failed_its_model_check_is_reported(tmp_path, monkeypatch):
    from korra_cli.web_routers import updates as updates_mod

    monkeypatch.setattr(updates_mod, "get_hermes_home", lambda: str(tmp_path))
    state = tmp_path / updates_mod.STATE_DIRNAME
    state.mkdir()
    (state / updates_mod.PROGRESS_FILE).write_text(json.dumps({
        "status": "rolled_back", "phase": "smoke", "release_id": "K21-2026.09.30",
        "error": "model_smoke_failed", "updated_at": NOW - 600,
    }), encoding="utf-8")
    items = ds._update_items(now=NOW)
    assert len(items) == 1 and "Проверка модели" in items[0]["detail"]
    assert items[0]["href"] == "/updates"


# ── HTTP ───────────────────────────────────────────────────────────────────


def test_state_route_serves_every_section_without_writing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import korra_constants
    from korra_cli import web_server

    main = _agent(tmp_path, "default", "Корра")
    conn = _state_db(main.home)
    _session(conn, "a", at=NOW - 3600)
    conn.commit()
    conn.close()
    monkeypatch.setattr(korra_constants, "get_default_hermes_root", lambda: tmp_path)
    monkeypatch.setattr(ds, "list_agents", lambda: [main])

    def snapshot() -> dict[str, int]:
        return _data_snapshot(tmp_path)

    wal_before = {str(p) for p in tmp_path.rglob("*-wal")}
    before = snapshot()
    client = TestClient(web_server.app)
    response = client.get(
        "/api/dashboard/state?period=month",
        headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == 1
    for section in ("attention", "agents", "metrics", "upcoming", "artifacts", "quota"):
        assert "status" in body[section], section
    assert body["metrics"]["period"] == "month"
    assert body["metrics"]["totals"]["dialogs"] == 1
    assert body["quota"] == {"available": False, "status": "absent"}
    assert snapshot() == before
    assert _no_journal_written(tmp_path, wal_before)

    # Anonymous callers get nothing.
    assert TestClient(web_server.app).get("/api/dashboard/state").status_code == 401


# ── 0.21.17: «Лимит Codex» — свежие данные, прогноз, запасной сброс ────────

import base64
import threading

import httpx

WEEK = 10080 * 60


def _jwt(*, exp: float = NOW + 3600, account: str = "acc-1") -> str:
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return ".".join([
        part({"alg": "none"}),
        part({"exp": exp, "https://api.openai.com/auth": {"chatgpt_account_id": account}}),
        "sig",
    ])


def _login(home: Path, token: str) -> bytes:
    tokens = {"access_token": token, "refresh_token": "rt-secret"}
    (home / "auth.json").write_text(json.dumps({
        "version": 1,
        "providers": {"openai-codex": {"tokens": tokens}},
        "credential_pool": {"openai-codex": [{"id": "p1", **tokens}]},
    }), encoding="utf-8")
    return (home / "auth.json").read_bytes()


def _usage(used: float = 36, *, resets_at: float = NOW + 5 * 86400, credits: int = 2,
           applicable: int = 0, reached: bool = False, window: int = WEEK) -> dict:
    return {
        "plan_type": "pro",
        "rate_limit": {
            "allowed": not reached, "limit_reached": reached,
            "primary_window": {"used_percent": used, "limit_window_seconds": window, "reset_at": resets_at},
            "secondary_window": None,
        },
        "credits": {"has_credits": True, "balance": "62500.0"},
        "rate_limit_reset_credits": {"available_count": credits, "applicable_available_count": applicable},
    }


@pytest.fixture
def wham(monkeypatch):
    """Replace the usage request; collect the calls. Any token refresh fails the test."""
    import agent.account_usage as account_usage
    from korra_cli import auth

    calls: list[dict] = []
    state = {"answer": _usage(), "error": None}

    def fake_fetch(token, *, account_id=None, base_url=None, timeout=5.0):
        calls.append({"token": token, "account_id": account_id, "timeout": timeout})
        if state["error"]:
            raise state["error"]
        return state["answer"]

    forbidden_calls: list[str] = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append("login touched")
        raise AssertionError("the panel must not refresh or recover a login")

    monkeypatch.setattr(account_usage, "fetch_codex_usage_payload", fake_fetch)
    for name in ("_refresh_codex_auth_tokens", "resolve_codex_runtime_credentials",
                 "_recover_codex_tokens_from_cli", "_save_auth_store"):
        monkeypatch.setattr(auth, name, forbidden)
    state["calls"] = calls
    yield state
    # The panel catches errors, so a swallowed call would otherwise go unseen.
    assert forbidden_calls == []


@pytest.fixture
def owner(tmp_path):
    main = _agent(tmp_path, "default", "Корра")
    _login(main.home, _jwt())
    return main


def test_card_asks_codex_when_nothing_is_stored(tmp_path, owner, wham):
    value = ds.quota_section([owner], now=NOW, root=tmp_path)
    assert value["status"] == "ok" and value["used_percent"] == 36
    assert value["windows"][0]["remaining_percent"] == 64 and value["plan_type"] == "pro"
    assert value["reset_credits"] == {"available": 2, "applicable": 0}
    assert value["captured_at"] == NOW
    assert len(wham["calls"]) == 1
    call = wham["calls"][0]
    assert call["account_id"] == "acc-1" and call["timeout"] <= 5
    # It is the same file the agents write.
    from agent.rate_limit_tracker import load_codex_quota

    assert load_codex_quota(root=tmp_path)["primary"]["used_percent"] == 36


def test_card_does_not_ask_again_within_two_minutes(tmp_path, owner, wham):
    ds.quota_section([owner], now=NOW, root=tmp_path)
    wham["answer"] = _usage(40)
    assert ds.quota_section([owner], now=NOW + 100, root=tmp_path)["used_percent"] == 36
    assert len(wham["calls"]) == 1
    assert ds.quota_section([owner], now=NOW + 130, root=tmp_path)["used_percent"] == 40
    assert len(wham["calls"]) == 2


def test_a_value_an_agent_just_wrote_is_not_asked_about(tmp_path, owner, wham):
    _quota(tmp_path, 12.0, resets_in=3 * 86400, captured=NOW - 30)
    assert ds.quota_section([owner], now=NOW, root=tmp_path)["used_percent"] == 12
    assert wham["calls"] == []


def test_an_old_value_is_replaced_by_the_fresh_one(tmp_path, owner, wham):
    _quota(tmp_path, 3.0, resets_in=3 * 86400, captured=NOW - 3 * 86400)
    value = ds.quota_section([owner], now=NOW, root=tmp_path)
    assert value["used_percent"] == 36 and value["captured_at"] == NOW
    assert len(wham["calls"]) == 1


@pytest.mark.parametrize("error", [
    httpx.ConnectError("no network"),
    httpx.ReadTimeout("slow"),
    httpx.HTTPStatusError("401", request=httpx.Request("GET", "https://x"), response=httpx.Response(401)),
    ValueError("not json"),
])
def test_a_failed_request_keeps_the_stored_value_and_pauses(tmp_path, owner, wham, error):
    _quota(tmp_path, 12.0, resets_in=3 * 86400, captured=NOW - 3600)
    wham["error"] = error
    value = ds.quota_section([owner], now=NOW, root=tmp_path)
    assert value["status"] == "ok" and value["used_percent"] == 12 and value["captured_at"] == NOW - 3600
    ds.quota_section([owner], now=NOW + 300, root=tmp_path)
    assert len(wham["calls"]) == 1
    wham["error"] = None
    assert ds.quota_section([owner], now=NOW + 601, root=tmp_path)["used_percent"] == 36
    assert len(wham["calls"]) == 2


def test_a_garbled_answer_is_a_failure_too(tmp_path, owner, wham):
    _quota(tmp_path, 12.0, resets_in=3 * 86400, captured=NOW - 3600)
    wham["answer"] = {"rate_limit": {}}
    assert ds.quota_section([owner], now=NOW, root=tmp_path)["used_percent"] == 12


def test_an_expired_or_foreign_token_is_not_used(tmp_path, wham):
    main = _agent(tmp_path, "default", "Корра")
    _login(main.home, _jwt(exp=NOW - 10))
    assert ds.quota_section([main], now=NOW, root=tmp_path) == {"available": True, "status": "waiting"}
    _login(main.home, "at-test")
    assert ds.quota_section([main], now=NOW, root=tmp_path) == {"available": True, "status": "waiting"}
    assert wham["calls"] == []


def test_no_subscription_means_no_request(tmp_path, wham):
    main = _agent(tmp_path, "default", "Корра")
    assert ds.quota_section([main], now=NOW, root=tmp_path)["status"] == "absent"
    assert wham["calls"] == []


def test_the_login_is_read_from_the_root_then_the_profiles_and_never_written(tmp_path, wham):
    root_token, profile_token = _jwt(account="root"), _jwt(account="writer")
    root_before = _login(tmp_path, root_token)
    writer = _agent(tmp_path, "writer", "Автор")
    writer_before = _login(writer.home, profile_token)
    ds.quota_section([writer], now=NOW, root=tmp_path)
    assert wham["calls"][0]["token"] == root_token and wham["calls"][0]["account_id"] == "root"
    assert (tmp_path / "auth.json").read_bytes() == root_before
    assert (writer.home / "auth.json").read_bytes() == writer_before
    # A profile on its own login is used when the root has none.
    (tmp_path / "auth.json").unlink()
    ds.reset_cache()
    ds.quota_section([writer], now=NOW + 500, root=tmp_path)
    assert wham["calls"][1]["token"] == profile_token


def test_pool_only_login_is_enough(tmp_path, wham):
    token = _jwt(account="pool")
    (tmp_path / "auth.json").write_text(json.dumps({
        "credential_pool": {"openai-codex": [{"id": "p", "access_token": token}]},
    }), encoding="utf-8")
    assert ds.quota_section([], now=NOW, root=tmp_path)["status"] == "ok"
    assert wham["calls"][0]["token"] == token


def test_parallel_polls_make_one_request(tmp_path, owner, wham, monkeypatch):
    import agent.account_usage as account_usage

    started, release = threading.Event(), threading.Event()
    real = account_usage.fetch_codex_usage_payload

    def slow(*args, **kwargs):
        started.set()
        release.wait(5)
        return real(*args, **kwargs)

    monkeypatch.setattr(account_usage, "fetch_codex_usage_payload", slow)
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        ds.quota_section([owner], now=NOW, root=tmp_path))) for _ in range(4)]
    for thread in threads:
        thread.start()
    assert started.wait(5)
    release.set()
    for thread in threads:
        thread.join(5)
    assert len(wham["calls"]) == 1
    assert [r["used_percent"] for r in results] == [36] * 4


def test_ghost_second_window_in_an_old_file_is_not_shown(tmp_path, owner):
    from agent.rate_limit_tracker import CodexQuotaSnapshot, CodexQuotaWindow, record_codex_quota

    record_codex_quota(CodexQuotaSnapshot(
        primary=CodexQuotaWindow(3.0, 10080, NOW + 86400),
        secondary=CodexQuotaWindow(0.0, None, NOW - 3 * 86400),
        captured_at=NOW, source="headers"), root=tmp_path)
    value = ds.quota_section([owner], now=NOW, root=tmp_path)
    assert [w["key"] for w in value["windows"]] == ["primary"]


# Прогноз и уровни. Окно — неделя, до сброса осталось ``left_days``.


def _forecast(tmp_path, owner, used, left_days, **extra):
    _quota(tmp_path, used, resets_in=left_days * 86400)
    return ds.quota_section([owner], now=NOW, root=tmp_path)


def test_forecast_of_a_fast_pace(tmp_path, owner):
    value = _forecast(tmp_path, owner, 36, 5.4)  # 1.6 days passed, 36 % used
    forecast = value["windows"][0]["forecast"]
    assert forecast["pace"] == pytest.approx(0.36 / (1.6 / 7))
    assert forecast["exhausts_at"] == pytest.approx(NOW + 64 * 1.6 * 86400 / 36)
    assert forecast["exhausts_before_reset"] is True
    assert value["level"] == "warn" and value["forecast"]["window_label"] == "неделя"


def test_forecast_of_a_calm_pace(tmp_path, owner):
    value = _forecast(tmp_path, owner, 20, 3.5)
    forecast = value["windows"][0]["forecast"]
    assert forecast["exhausts_before_reset"] is False and forecast["pace"] < 1
    assert value["level"] == "normal"


@pytest.mark.parametrize(("used", "left_days"), [
    (36, 6.5),   # only 7 % of the window has passed
    (4, 3.5),    # less than 5 % used
    (100, 3.5),  # nothing left to forecast
    (0, 3.5),
])
def test_no_forecast_when_it_would_be_a_guess(tmp_path, owner, used, left_days):
    assert _forecast(tmp_path, owner, used, left_days)["windows"][0]["forecast"] is None


@pytest.mark.parametrize(("used", "level"), [(79, "normal"), (80, "warn"), (95, "critical")])
def test_levels_follow_what_is_left(tmp_path, owner, used, level):
    assert _forecast(tmp_path, owner, used, 0.2)["level"] == level


def test_a_reached_limit_is_critical(tmp_path, owner, wham):
    wham["answer"] = _usage(97, resets_at=NOW + 86400, reached=True)
    assert ds.quota_section([owner], now=NOW, root=tmp_path)["level"] == "critical"
    ds.reset_cache()
    wham["answer"] = _usage(60, resets_at=NOW + 86400, reached=True)
    value = ds.quota_section([owner], now=NOW + 200, root=tmp_path)
    assert value["limit_reached"] is True and value["level"] == "critical"


def test_the_five_hour_window_and_the_week_are_two_windows(tmp_path, owner, wham):
    answer = _usage(18, resets_at=NOW + 3600, window=18000)
    answer["rate_limit"]["secondary_window"] = {
        "used_percent": 71, "limit_window_seconds": WEEK, "reset_at": NOW + 3 * 86400}
    wham["answer"] = answer
    value = ds.quota_section([owner], now=NOW, root=tmp_path)
    assert [(w["label"], w["remaining_percent"]) for w in value["windows"]] == [("5 ч", 82), ("неделя", 29)]
    assert value["resets_at"] == NOW + 3 * 86400  # the headline is the tighter window


# Запасной сброс


@pytest.mark.parametrize(("used", "applicable", "reached", "visible"), [
    (36, 0, False, False),
    (36, 1, False, True),
    (60, 0, True, True),
    (100, 0, False, True),
])
def test_when_the_reset_button_is_offered(tmp_path, owner, wham, used, applicable, reached, visible):
    wham["answer"] = _usage(used, applicable=applicable, reached=reached)
    assert ds.quota_section([owner], now=NOW, root=tmp_path)["can_reset"] is visible


def test_no_button_without_banked_resets(tmp_path, owner, wham):
    wham["answer"] = _usage(100, credits=0, reached=True)
    assert ds.quota_section([owner], now=NOW, root=tmp_path)["can_reset"] is False


def _two_windows(root: Path, *, five_used: float, week_used: float, five_resets_in: float,
                 reached: bool = True, applicable: int = 1) -> None:
    from agent.rate_limit_tracker import CodexQuotaSnapshot, CodexQuotaWindow, record_codex_quota

    record_codex_quota(
        CodexQuotaSnapshot(
            primary=CodexQuotaWindow(used_percent=five_used, window_minutes=300, resets_at=NOW + five_resets_in),
            secondary=CodexQuotaWindow(used_percent=week_used, window_minutes=10080, resets_at=NOW + 3 * 86400),
            plan_type="pro", captured_at=NOW - 10, source="usage", limit_reached=reached,
            reset_credits={"available": 2, "applicable": applicable},
        ),
        root=root, force=True,
    )


def test_a_rolled_over_blocker_does_not_pass_the_block_to_the_other_window(tmp_path, owner, wham):
    # The 5 hours were empty and have rolled over; the week is 30 % used; no fresh answer yet.
    _two_windows(tmp_path, five_used=100, week_used=30, five_resets_in=-1)
    value = ds.quota_section([owner], now=NOW, root=tmp_path)
    five, week = value["windows"]
    assert five["renewed"] and five["remaining_percent"] == 100 and five["level"] == "normal"
    assert week["remaining_percent"] == 70 and week["level"] == "normal"
    assert value["level"] == "normal" and value["limit_reached"] is False
    assert value["can_reset"] is False and value["natural_reset_at"] is None
    assert wham["calls"] == []
    assert ds._quota_attention(value, now=NOW, tz=MSK) is None


def test_the_block_stays_while_a_blocking_window_has_not_rolled_over(tmp_path, owner, wham):
    _two_windows(tmp_path, five_used=100, week_used=30, five_resets_in=2 * 3600)
    value = ds.quota_section([owner], now=NOW, root=tmp_path)
    five, week = value["windows"]
    assert (five["level"], week["level"]) == ("critical", "normal")
    assert value["limit_reached"] is True and value["can_reset"] is True
    assert value["natural_reset_at"] == NOW + 2 * 3600
    # The week alone blocking after the 5 hours returned: the block moves, with its own reset.
    ds.reset_cache()
    _two_windows(tmp_path, five_used=100, week_used=100, five_resets_in=-1)
    value = ds.quota_section([owner], now=NOW, root=tmp_path)
    assert value["limit_reached"] is True and value["level"] == "critical" and value["can_reset"] is True


def test_a_reached_flag_without_an_empty_window_blocks_the_busiest_one(tmp_path, owner, wham):
    _two_windows(tmp_path, five_used=40, week_used=90, five_resets_in=3600)
    value = ds.quota_section([owner], now=NOW, root=tmp_path)
    five, week = value["windows"]
    assert week["level"] == "critical" and five["level"] == "normal" and value["limit_reached"] is True


HOUR = 3600


def test_a_limit_that_returns_soon_is_marked_for_the_card(tmp_path, owner, wham):
    wham["answer"] = _usage(100, resets_at=NOW + 11 * HOUR, reached=True)
    value = ds.quota_section([owner], now=NOW, root=tmp_path)
    assert value["can_reset"] is True and value["natural_reset_at"] == NOW + 11 * HOUR


@pytest.mark.parametrize("hours", [12, 13, 72])
def test_a_limit_that_returns_in_twelve_hours_or_more_is_not(tmp_path, owner, wham, hours):
    wham["answer"] = _usage(100, resets_at=NOW + hours * HOUR, reached=True)
    value = ds.quota_section([owner], now=NOW, root=tmp_path)
    assert value["can_reset"] is True and value["natural_reset_at"] is None


def test_several_empty_windows_return_with_the_latest_reset(tmp_path, owner, wham):
    answer = _usage(100, resets_at=NOW + 2 * HOUR, window=18000, reached=True)
    answer["rate_limit"]["secondary_window"] = {
        "used_percent": 100, "limit_window_seconds": WEEK, "reset_at": NOW + 30 * HOUR}
    wham["answer"] = answer
    assert ds.quota_section([owner], now=NOW, root=tmp_path)["natural_reset_at"] is None
    ds.reset_cache()
    answer["rate_limit"]["secondary_window"]["reset_at"] = NOW + 5 * HOUR
    value = ds.quota_section([owner], now=NOW + 200, root=tmp_path)
    assert value["natural_reset_at"] == NOW + 5 * HOUR


def test_only_the_empty_window_counts_not_a_full_one(tmp_path, owner, wham):
    answer = _usage(100, resets_at=NOW + 3 * HOUR, window=18000, reached=True)
    answer["rate_limit"]["secondary_window"] = {
        "used_percent": 20, "limit_window_seconds": WEEK, "reset_at": NOW + 5 * 86400}
    wham["answer"] = answer
    assert ds.quota_section([owner], now=NOW, root=tmp_path)["natural_reset_at"] == NOW + 3 * HOUR


def test_nothing_to_wait_for_when_the_reset_is_not_on_offer_or_nothing_is_exhausted(tmp_path, owner, wham):
    wham["answer"] = _usage(36, applicable=1, resets_at=NOW + 2 * HOUR)
    value = ds.quota_section([owner], now=NOW, root=tmp_path)
    assert value["can_reset"] is True and value["natural_reset_at"] is None
    ds.reset_cache()
    wham["answer"] = _usage(100, credits=0, resets_at=NOW + 2 * HOUR, reached=True)
    value = ds.quota_section([owner], now=NOW + 200, root=tmp_path)
    assert value["can_reset"] is False and value["natural_reset_at"] is None


def _redeem(monkeypatch, wham, result):
    import agent.account_usage as account_usage

    seen = []

    def fake(**kwargs):
        seen.append(kwargs)
        if kwargs.get("on_usage"):
            kwargs["on_usage"](wham["answer"])
        if isinstance(result, Exception):
            raise result
        if result.redeemed:
            wham["answer"] = _usage(0, resets_at=NOW + WEEK, credits=result.available_count)
        return result

    monkeypatch.setattr(account_usage, "redeem_codex_reset_credit", fake)
    return seen


def _outcome(status, count=0):
    from agent.account_usage import CodexResetRedeemResult

    return CodexResetRedeemResult(status=status, message="English text that must not leak", available_count=count)


def test_reset_success_returns_the_new_state(tmp_path, owner, wham, monkeypatch):
    wham["answer"] = _usage(100, reached=True)
    seen = _redeem(monkeypatch, wham, _outcome("reset", 1))
    result = ds.reset_codex_limit(now=NOW, agents=[owner], root=tmp_path)
    assert result["ok"] is True and result["status"] == "reset"
    assert result["message"] == "Лимит сброшен — снова полный. В запасе осталось 1 сброс."
    assert result["quota"]["windows"][0]["remaining_percent"] == 100
    assert result["quota"]["reset_credits"]["available"] == 1 and result["quota"]["can_reset"] is False
    # The token goes in explicitly; the nearly-empty limit is redeemed on purpose.
    assert seen[0]["api_key"].count(".") == 2 and seen[0]["account_id"] == "acc-1" and seen[0]["force"] is True


def test_reset_leaves_the_decision_to_the_fresh_answer_of_the_backend(tmp_path, owner, wham, monkeypatch):
    seen = _redeem(monkeypatch, wham, _outcome("not_exhausted", 2))
    result = ds.reset_codex_limit(now=NOW, agents=[owner], root=tmp_path)
    assert seen[0]["require_offer"] is True and result["ok"] is False
    assert "сохранён" in result["message"] and "English" not in result["message"]
    assert len(wham["calls"]) == 0


@pytest.mark.parametrize(("status", "text"), [
    ("nothing_to_reset", "Сбрасывать нечего"),
    ("no_credits_banked", "Запасных сбросов нет"),
    ("no_credit", "Запасных сбросов нет"),
    ("already_redeemed", "уже применён"),
    ("unavailable", "Не удалось применить сброс"),
])
def test_reset_messages_are_russian(tmp_path, owner, wham, monkeypatch, status, text):
    _redeem(monkeypatch, wham, _outcome(status))
    result = ds.reset_codex_limit(now=NOW, agents=[owner], root=tmp_path)
    assert result["ok"] is False and result["status"] == status
    assert text in result["message"] and "English" not in result["message"]
    assert result["quota"]["status"] == "ok"


def test_reset_survives_a_crash_in_the_redeem(tmp_path, owner, wham, monkeypatch):
    _redeem(monkeypatch, wham, RuntimeError("boom"))
    result = ds.reset_codex_limit(now=NOW, agents=[owner], root=tmp_path)
    assert result["status"] == "unavailable" and "boom" not in result["message"]


def test_reset_without_a_valid_login_spends_nothing(tmp_path, wham, monkeypatch):
    main = _agent(tmp_path, "default", "Корра")
    _login(main.home, _jwt(exp=NOW - 5))
    seen = _redeem(monkeypatch, wham, _outcome("reset"))
    result = ds.reset_codex_limit(now=NOW, agents=[main], root=tmp_path)
    assert seen == [] and result["status"] == "unavailable" and "не потрачен" in result["message"]


def test_reset_without_a_subscription(tmp_path, wham, monkeypatch):
    seen = _redeem(monkeypatch, wham, _outcome("reset"))
    result = ds.reset_codex_limit(now=NOW, agents=[], root=tmp_path)
    assert result["status"] == "absent" and seen == []


def test_two_resets_at_once_spend_one(tmp_path, owner, wham, monkeypatch):
    wham["answer"] = _usage(100, reached=True)
    inside, release = threading.Event(), threading.Event()
    calls = []

    def slow(**kwargs):
        calls.append(kwargs)
        inside.set()
        release.wait(5)
        return _outcome("reset", 1)

    import agent.account_usage as account_usage

    monkeypatch.setattr(account_usage, "redeem_codex_reset_credit", slow)
    first = threading.Thread(target=lambda: ds.reset_codex_limit(now=NOW, agents=[owner], root=tmp_path))
    first.start()
    assert inside.wait(5)
    assert ds.reset_codex_limit(now=NOW, agents=[owner], root=tmp_path)["status"] == "busy"
    release.set()
    first.join(5)
    assert len(calls) == 1


def test_reset_route(tmp_path, owner, wham, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    import korra_constants
    from korra_cli import web_server

    # The route reads the real clock, so the login and the window follow it.
    _login(owner.home, _jwt(exp=time.time() + 3600))
    monkeypatch.setattr(korra_constants, "get_default_hermes_root", lambda: tmp_path)
    monkeypatch.setattr(ds, "list_agents", lambda: [owner])
    wham["answer"] = _usage(100, resets_at=time.time() + 86400, reached=True)
    _redeem(monkeypatch, wham, _outcome("reset", 1))
    client = TestClient(web_server.app)
    response = client.post(
        "/api/dashboard/codex-limit/reset", headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["quota"]["windows"][0]["remaining_percent"] == 100
    assert "сброс" in body["message"]
    assert TestClient(web_server.app).post("/api/dashboard/codex-limit/reset").status_code == 401


# Запасной сброс на уровне сети: настоящий redeem_codex_reset_credit, подменён только транспорт httpx.


@pytest.fixture
def backend(monkeypatch):
    """The Codex backend behind ``httpx.MockTransport``; every request is recorded."""
    import agent.account_usage as account_usage

    box = types.SimpleNamespace(requests=[], usage=_usage(), usage_errors=[])

    def handler(request: httpx.Request) -> httpx.Response:
        box.requests.append((request.method, request.url.host, request.url.path))
        if request.method == "GET" and request.url.path.endswith("/wham/usage"):
            if box.usage_errors:
                return httpx.Response(box.usage_errors.pop(0))
            return httpx.Response(200, json=box.usage)
        if request.method == "POST" and request.url.path.endswith("/rate-limit-reset-credits/consume"):
            box.usage = _usage(0, resets_at=NOW + WEEK, credits=1)
            return httpx.Response(200, json={"code": "reset", "windows_reset": 1})
        return httpx.Response(404)

    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(account_usage.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    box.consumes = lambda: [r for r in box.requests if r[0] == "POST"]
    return box


def test_a_stray_reset_post_spends_nothing_when_the_limit_is_not_exhausted(tmp_path, owner, backend):
    backend.usage = _usage(36, applicable=0, credits=2)
    result = ds.reset_codex_limit(now=NOW, agents=[owner], root=tmp_path)
    assert backend.consumes() == []
    assert result["ok"] is False and result["status"] == "not_exhausted"
    assert "запасной сброс сохранён" in result["message"]
    assert result["quota"]["reset_credits"]["available"] == 2
    # The requests really went to the Codex host, through the real code path.
    assert {host for _, host, _ in backend.requests} == {"chatgpt.com"}


def test_a_reset_post_for_an_exhausted_limit_spends_exactly_one(tmp_path, owner, backend):
    backend.usage = _usage(100, applicable=1, credits=2, reached=True)
    result = ds.reset_codex_limit(now=NOW, agents=[owner], root=tmp_path)
    assert len(backend.consumes()) == 1
    assert backend.consumes()[0][2].endswith("/rate-limit-reset-credits/consume")
    assert result["ok"] is True and result["status"] == "reset"
    assert result["quota"]["windows"][0]["remaining_percent"] == 100


def test_a_reset_lifts_the_cooldown_in_the_profile_store_the_token_came_from(tmp_path, backend):
    writer = _agent(tmp_path, "writer", "Писатель")
    token = _jwt()
    tokens = {"access_token": token, "refresh_token": "rt-secret"}
    frozen = {
        "id": "p1", **tokens, "last_status": "exhausted", "last_status_at": NOW - 60,
        "last_error_code": 429, "last_error_reason": "usage_limit_reached",
        "last_error_message": "The usage limit has been reached", "last_error_reset_at": NOW + 3600,
    }
    auth = writer.home / "auth.json"
    auth.write_text(json.dumps({
        "version": 1, "providers": {"openai-codex": {"tokens": tokens}},
        "credential_pool": {"openai-codex": [frozen]},
    }), encoding="utf-8")
    environment = dict(os.environ)
    backend.usage = _usage(100, applicable=1, credits=2, reached=True)
    result = ds.reset_codex_limit(now=NOW, agents=[writer], root=tmp_path)
    assert result["status"] == "reset" and len(backend.consumes()) == 1
    store = json.loads(auth.read_text(encoding="utf-8"))
    entry = store["credential_pool"]["openai-codex"][0]
    assert entry["last_status"] is None and entry["last_error_reset_at"] is None
    assert entry["access_token"] == token and entry["refresh_token"] == "rt-secret"
    assert store["providers"]["openai-codex"]["tokens"] == tokens
    assert dict(os.environ) == environment


def test_an_old_exhausted_file_does_not_unlock_a_reset_the_backend_would_not_offer(tmp_path, owner, backend):
    from agent.rate_limit_tracker import CodexQuotaSnapshot, CodexQuotaWindow, record_codex_quota

    # The file says "exhausted" ...
    record_codex_quota(
        CodexQuotaSnapshot(
            primary=CodexQuotaWindow(used_percent=100.0, window_minutes=10080, resets_at=NOW + 86400),
            plan_type="pro", captured_at=NOW - 3600, source="usage", limit_reached=True,
            reset_credits={"available": 2, "applicable": 1},
        ),
        root=tmp_path, force=True,
    )
    # ... and the fresh answer says the limit is fine.
    backend.usage = _usage(36, applicable=0, credits=2)
    result = ds.reset_codex_limit(now=NOW, agents=[owner], root=tmp_path)
    assert backend.consumes() == []
    assert result["status"] == "not_exhausted" and "запасной сброс сохранён" in result["message"]


def test_a_failed_decision_read_spends_nothing(tmp_path, owner, backend):
    backend.usage_errors = [500]
    result = ds.reset_codex_limit(now=NOW, agents=[owner], root=tmp_path)
    assert backend.consumes() == [] and result["status"] == "unavailable"


def test_rejected_resets_do_not_bypass_the_request_rate(tmp_path, owner, backend):
    backend.usage = _usage(36, applicable=0, credits=2)
    reads = lambda: [r for r in backend.requests if r[0] == "GET"]
    first = ds.reset_codex_limit(now=NOW, agents=[owner], root=tmp_path)
    assert first["status"] == "not_exhausted" and len(reads()) == 1
    # The answer of that one read is what the card shows now.
    assert first["quota"]["status"] == "ok" and first["quota"]["reset_credits"]["available"] == 2
    second = ds.reset_codex_limit(now=NOW + 30, agents=[owner], root=tmp_path)
    assert second["status"] == "not_exhausted" and len(reads()) == 1
    assert backend.consumes() == []
    # After two minutes a click may ask again, once.
    ds.reset_codex_limit(now=NOW + 125, agents=[owner], root=tmp_path)
    assert len(reads()) == 2 and backend.consumes() == []


def test_a_failed_attempt_rests_for_ten_minutes(tmp_path, owner, backend):
    backend.usage_errors = [500]
    ds.reset_codex_limit(now=NOW, agents=[owner], root=tmp_path)
    again = ds.reset_codex_limit(now=NOW + 300, agents=[owner], root=tmp_path)
    assert again["status"] == "unavailable" and len([r for r in backend.requests if r[0] == "GET"]) == 1
    ds.reset_codex_limit(now=NOW + 601, agents=[owner], root=tmp_path)
    assert len([r for r in backend.requests if r[0] == "GET"]) == 2


def test_a_confirmed_reset_reads_the_limit_once_more(tmp_path, owner, backend):
    backend.usage = _usage(100, applicable=1, credits=2, reached=True)
    result = ds.reset_codex_limit(now=NOW, agents=[owner], root=tmp_path)
    assert result["status"] == "reset" and len(backend.consumes()) == 1
    assert [r[0] for r in backend.requests] == ["GET", "POST", "GET"]
    assert result["quota"]["windows"][0]["remaining_percent"] == 100


def test_the_guard_refuses_a_real_host(no_real_network):
    with pytest.raises(Exception):
        httpx.get("https://chatgpt.com/backend-api/wham/usage")
    assert any("chatgpt.com" in host for host in no_real_network)
    no_real_network.clear()


def test_cached_quota_is_dropped_after_a_reset(tmp_path, owner, wham, monkeypatch):
    wham["answer"] = _usage(100, reached=True)
    cache_key = ("quota", str(tmp_path))
    ds._cache[cache_key] = (NOW + 9999, {"stale": True})
    _redeem(monkeypatch, wham, _outcome("nothing_to_reset"))
    ds.reset_codex_limit(now=NOW, agents=[owner], root=tmp_path)
    assert cache_key not in ds._cache


def test_attention_early_warning_replaces_the_critical_row(tmp_path, owner, monkeypatch):
    monkeypatch.setattr(ds, "_update_items", lambda now: [])
    quota = _forecast(tmp_path, owner, 58, 3.0)
    assert quota["forecast"]["exhausts_before_reset"] and quota["level"] == "warn"
    items = ds.attention_section([owner], now=NOW, tz=MSK, quota=quota)["items"]
    rows = [item for item in items if item["source"] == "quota"]
    assert len(rows) == 1 and rows[0]["title"] == "Лимит Codex кончится раньше сброса"
    assert "Осталось 42 %" in rows[0]["detail"] and "до сброса" in rows[0]["detail"]
    # Below half of the limit the forecast alone is not worth the owner's attention.
    calm = _forecast(tmp_path, owner, 36, 5.4)
    assert calm["windows"][0]["forecast"]["exhausts_before_reset"]
    assert not [i for i in ds.attention_section([owner], now=NOW, tz=MSK, quota=calm)["items"]
                if i["source"] == "quota"]
    # An exhausted limit is one row, not two.
    critical = {**quota, "level": "critical", "limit_reached": True, "used_percent": 100}
    rows = [i for i in ds.attention_section([owner], now=NOW, tz=MSK, quota=critical)["items"]
            if i["source"] == "quota"]
    assert len(rows) == 1 and rows[0]["title"] == "Лимит Codex исчерпан"
