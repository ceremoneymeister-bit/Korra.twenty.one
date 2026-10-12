"""One exit contract for kanban workers, and how the dispatcher reads it (K21-290).

CLI side: ``chat -q`` and ``chat -Q`` (including a late goal turn) exit with
75 (provider wait), 78 (access/config) or 1 (ordinary failure) inside a kanban
worker. Dispatcher side: wait is not a defect and stops after two automatic
retries, access errors block at once, a restart or drain is not a crash, a
real crash keeps its bounded retry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from korra_cli import kanban_db as kb


class FakeAgent:
    def __init__(self, results):
        self.results = list(results)
        self.session_id = "s1"
        self.calls = []

    def run_conversation(self, user_message, conversation_history=None, **kw):
        self.calls.append(user_message)
        return self.results.pop(0)


def _failed(reason):
    return {"final_response": "", "failed": True, "failure_reason": reason, "error": reason}


OK = {"final_response": "done", "failed": False}


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".hermes"
    h.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(h))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("KORRA_KANBAN_GOAL_MODE", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_GOAL_MODE", raising=False)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return h


def _run_main(monkeypatch, results, *, quiet):
    import cli as C

    agent = FakeAgent(results)
    monkeypatch.setattr(C.HermesCLI, "_ensure_runtime_credentials", lambda self: True)

    def init_agent(self, **kw):
        self.agent = agent
        return True

    monkeypatch.setattr(C.HermesCLI, "_init_agent", init_agent)
    if not quiet:
        def chat(self, message, images=None, **kw):
            self._last_turn_result = agent.run_conversation(message)

        monkeypatch.setattr(C.HermesCLI, "chat", chat)
        monkeypatch.setattr(C.HermesCLI, "_show_security_advisories", lambda self: None)
        monkeypatch.setattr(C.HermesCLI, "_print_exit_summary", lambda self, **kw: None)
    try:
        C.main(query="work kanban task t_x", quiet=quiet)
    except SystemExit as exc:
        return exc.code or 0, agent
    return 0, agent


@pytest.mark.parametrize("quiet", [True, False], ids=["Q", "q"])
@pytest.mark.parametrize(
    "reason,expected",
    [
        ("rate_limit", 75), ("billing", 75), ("timeout", 75),
        ("overloaded", 75), ("server_error", 75),
        ("auth", 78), ("auth_permanent", 78), ("model_not_found", 78),
        ("unknown", 1),
    ],
)
def test_worker_exit_code_names_the_reason(home, monkeypatch, quiet, reason, expected):
    monkeypatch.setenv("KORRA_KANBAN_TASK", "t_x")
    code, _ = _run_main(monkeypatch, [_failed(reason)], quiet=quiet)
    assert code == expected


@pytest.mark.parametrize("quiet", [True, False], ids=["Q", "q"])
def test_worker_success_exits_zero(home, monkeypatch, quiet):
    monkeypatch.setenv("KORRA_KANBAN_TASK", "t_x")
    code, _ = _run_main(monkeypatch, [OK], quiet=quiet)
    assert code == 0


def test_plain_q_outside_a_worker_keeps_exit_zero_on_failure(home, monkeypatch):
    monkeypatch.delenv("KORRA_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    code, _ = _run_main(monkeypatch, [_failed("rate_limit")], quiet=False)
    assert code == 0


def test_plain_Q_outside_a_worker_keeps_exit_one_on_failure(home, monkeypatch):
    monkeypatch.delenv("KORRA_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    code, _ = _run_main(monkeypatch, [_failed("rate_limit")], quiet=True)
    assert code == 1


def test_late_goal_turn_failure_decides_the_exit_code(home, monkeypatch):
    from korra_cli import goals

    with kb.connect() as conn:
        tid = kb.create_task(conn, title="goal", assignee="a")
        kb.claim_task(conn, tid)
    monkeypatch.setenv("KORRA_KANBAN_TASK", tid)
    monkeypatch.setenv("KORRA_KANBAN_GOAL_MODE", "1")
    monkeypatch.setattr(
        goals, "judge_goal", lambda *a, **k: ("continue", "more", False, False, False)
    )
    code, agent = _run_main(monkeypatch, [OK, _failed("billing")], quiet=True)
    assert len(agent.calls) == 2
    assert code == 75


def test_late_goal_turn_access_error_exits_78(home, monkeypatch):
    from korra_cli import goals

    with kb.connect() as conn:
        tid = kb.create_task(conn, title="goal", assignee="a")
        kb.claim_task(conn, tid)
    monkeypatch.setenv("KORRA_KANBAN_TASK", tid)
    monkeypatch.setenv("KORRA_KANBAN_GOAL_MODE", "1")
    monkeypatch.setattr(
        goals, "judge_goal", lambda *a, **k: ("continue", "more", False, False, False)
    )
    code, _ = _run_main(monkeypatch, [OK, _failed("auth")], quiet=True)
    assert code == 78


def test_failed_first_goal_turn_skips_the_goal_loop(home, monkeypatch):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="goal", assignee="a")
        kb.claim_task(conn, tid)
    monkeypatch.setenv("KORRA_KANBAN_TASK", tid)
    monkeypatch.setenv("KORRA_KANBAN_GOAL_MODE", "1")
    code, agent = _run_main(monkeypatch, [_failed("rate_limit")], quiet=True)
    assert code == 75
    assert len(agent.calls) == 1


# ---------------------------------------------------------------------------
# Dispatcher side
# ---------------------------------------------------------------------------


def _exited(code):
    return code << 8


@pytest.fixture
def dispatcher(home, monkeypatch):
    monkeypatch.setattr(kb, "_pid_alive", lambda _pid: False)
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")
    kb._recent_worker_exits.clear()
    return home


_pid = [91000]


def _worker_dies(conn, tid, code=None, *, started_before_dispatcher=False):
    """Claim ``tid`` as a worker and let it die; ``code=None`` = nobody reaped it."""
    host = kb._claimer_id().split(":", 1)[0]
    _pid[0] += 1
    pid = _pid[0]
    kb.claim_task(conn, tid, claimer=f"{host}:w{pid}")
    started = (
        int(kb._DISPATCHER_PROCESS_STARTED_AT) - 100
        if started_before_dispatcher else None
    )
    conn.execute(
        "UPDATE tasks SET worker_pid = ?, started_at = COALESCE(?, started_at) "
        "WHERE id = ?",
        (pid, started, tid),
    )
    conn.commit()
    if code is not None:
        kb._record_worker_exit(pid, _exited(code))
    return kb.detect_crashed_workers(conn)


def test_access_error_exit_blocks_at_once_without_a_retry(dispatcher):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="a")
        crashed = _worker_dies(conn, tid, kb.KANBAN_TERMINAL_PROVIDER_EXIT_CODE)
        task = kb.get_task(conn, tid)
        assert tid not in crashed
        assert task.status == "blocked"
        assert task.block_kind == "capability"
        assert "access" in task.block_reason
        assert task.consecutive_failures == 0
        assert "crashed" not in [r.outcome for r in kb.list_runs(conn, tid)]


def test_restart_that_took_the_worker_is_not_a_crash(dispatcher):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="a")
        crashed = _worker_dies(conn, tid, None, started_before_dispatcher=True)
        task = kb.get_task(conn, tid)
        assert tid not in crashed
        assert task.status == "ready"
        assert task.consecutive_failures == 0
        assert [r.outcome for r in kb.list_runs(conn, tid)] == ["interrupted"]


def test_worker_stopped_by_sigterm_is_not_a_crash(dispatcher):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="a")
        crashed = _worker_dies(conn, tid, 143)
        task = kb.get_task(conn, tid)
        assert tid not in crashed
        assert task.status == "ready"
        assert task.consecutive_failures == 0


def test_endless_interruptions_become_an_ordinary_crash(dispatcher):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="a")
        for _ in range(kb.KANBAN_AUTO_RETRY_LIMIT):
            _worker_dies(conn, tid, 143)
            assert kb.get_task(conn, tid).consecutive_failures == 0
        crashed = _worker_dies(conn, tid, 143)
        assert tid in crashed
        assert kb.get_task(conn, tid).consecutive_failures == 1


def test_worker_that_died_on_this_process_still_crashes(dispatcher):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="a")
        crashed = _worker_dies(conn, tid, None)
        assert tid in crashed
        assert kb.get_task(conn, tid).consecutive_failures == 1


def test_real_crash_keeps_its_bounded_retry(dispatcher):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="a")
        _worker_dies(conn, tid, 1)
        assert kb.get_task(conn, tid).status == "ready"
        assert kb.get_task(conn, tid).consecutive_failures == 1
        _worker_dies(conn, tid, 1)
        task = kb.get_task(conn, tid)
        assert task.status == "blocked"
        assert task.consecutive_failures == 2


def test_provider_wait_stops_after_two_retries_and_restarts_the_series(dispatcher):
    limit = kb.KANBAN_AUTO_RETRY_LIMIT
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="a")
        for _ in range(limit):
            _worker_dies(conn, tid, kb.KANBAN_RATE_LIMIT_EXIT_CODE)
            assert kb.get_task(conn, tid).status == "ready"
        _worker_dies(conn, tid, kb.KANBAN_RATE_LIMIT_EXIT_CODE)
        task = kb.get_task(conn, tid)
        assert task.status == "blocked" and task.block_kind == "transient"
        assert task.consecutive_failures == 0

        # A person unblocks it once the provider is back: a new series starts.
        assert kb.unblock_task(conn, tid)
        _worker_dies(conn, tid, kb.KANBAN_RATE_LIMIT_EXIT_CODE)
        assert kb.get_task(conn, tid).status == "ready"


def test_a_clean_run_between_waits_restarts_the_series(dispatcher):
    limit = kb.KANBAN_AUTO_RETRY_LIMIT
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="a")
        for _ in range(limit):
            _worker_dies(conn, tid, kb.KANBAN_RATE_LIMIT_EXIT_CODE)
        _worker_dies(conn, tid, 1)  # an ordinary failure in between
        _worker_dies(conn, tid, kb.KANBAN_RATE_LIMIT_EXIT_CODE)
        assert kb.get_task(conn, tid).status == "ready"
