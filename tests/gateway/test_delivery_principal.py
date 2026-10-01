"""A real delivery child cannot borrow live owner authority (K21-241)."""
import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("owner,platform,read", [("", "", False), ("live", "", False),
    ("live", "telegram", False), ("delegated", "telegram", False), ("delegated", "local", True)])
def test_machine_child_is_never_live(tmp_path, owner, platform, read):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path),
           "HERMES_HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1",
           "KORRA_SINGLE_QUERY_SESSION": "1", "KORRA_SESSION_OWNER": owner,
           "KORRA_SESSION_PLATFORM": platform}
    code = """import json
from gateway.principal import current_principal, may_change_owner_services, origin_owner_verdict
p = current_principal()
print(json.dumps([p.kind, p.owner, p.live, may_change_owner_services(), origin_owner_verdict()]))
"""
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == ["delivery", read, False, False, False]


def test_interactive_owner_cli_unchanged(monkeypatch):
    from gateway.session_context import reset_session_vars
    from gateway.principal import current_principal, may_change_owner_services
    reset_session_vars()
    for prefix in ("KORRA", "HERMES"):
        for key in ("SINGLE_QUERY_SESSION", "ONESHOT_SESSION", "KANBAN_TASK", "SESSION_PLATFORM", "CRON_SESSION"):
            monkeypatch.delenv(f"{prefix}_{key}", raising=False)
    assert current_principal().owner and current_principal().live
    assert may_change_owner_services()


def test_dispatcher_worker_keeps_its_existing_delegated_verdict(monkeypatch):
    from gateway.session_context import reset_session_vars
    from gateway.principal import current_principal, origin_owner_verdict, may_change_owner_services
    reset_session_vars()
    monkeypatch.setenv("KORRA_KANBAN_TASK", "synthetic-card")
    monkeypatch.setenv("KORRA_SINGLE_QUERY_SESSION", "1")
    assert current_principal().kind == "kanban_worker"
    assert origin_owner_verdict()
    assert not may_change_owner_services()


@pytest.mark.parametrize("job,owner", [({}, True), ({"created_by_owner": True}, True),
                                     ({"created_by_owner": False}, False)])
def test_scheduler_binds_job_verdict_and_scrubs_foreign_turn(monkeypatch, job, owner):
    from cron.scheduler import _deliver_to_bot_chat
    monkeypatch.setenv("KORRA_SESSION_OWNER", "live")
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("KORRA_CRON_SESSION", "1")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "foreign")
    captured = []
    def run(argv, **kw):
        captured.append(kw["env"])
        return subprocess.CompletedProcess(argv, 0, "ok", "")
    monkeypatch.setattr("cron.scheduler.subprocess.run", run)
    assert _deliver_to_bot_chat({"id": "test", **job}, "report", "") is None
    env = captured[0]
    assert env["KORRA_SESSION_OWNER"] == ("delegated" if owner else "")
    assert env["KORRA_SESSION_PLATFORM"] == "local"
    assert not env.get("KORRA_CRON_SESSION") and not env.get("HERMES_KANBAN_TASK")
