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


def test_metrics_never_write_to_the_store(tmp_path):
    main = _agent(tmp_path, "default", "Корра")
    conn = _state_db(main.home)
    _session(conn, "a", at=_msk(23, 10))
    conn.commit()
    conn.close()
    before = {p.name: p.stat().st_mtime_ns for p in main.home.iterdir()}
    ds.metrics_section([main], period="week", now=NOW, tz=MSK)
    ds.agents_section([main])
    after = {p.name: p.stat().st_mtime_ns for p in main.home.iterdir()}
    assert before == after


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


def test_quota_after_the_window_reset_is_not_shown_as_current(tmp_path):
    main = _agent(tmp_path, "default", "Корра")
    _connect_codex(main.home)
    _quota(tmp_path, 99.0, resets_in=-60)
    value = ds.quota_section([main], now=NOW, root=tmp_path)
    assert value["status"] == "reset" and "used_percent" not in value


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
    assert item["kind"] == "quota_critical" and "96 %" in item["title"]
    assert "завтра в 14:00" in item["detail"]


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
        return {str(p): p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}

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

    # Anonymous callers get nothing.
    assert TestClient(web_server.app).get("/api/dashboard/state").status_code == 401
