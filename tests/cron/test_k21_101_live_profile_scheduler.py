"""K21-101: a job added after ticker startup really executes in its profile."""

import threading
import time
from datetime import datetime, timedelta, timezone

from cron.scheduler_provider import InProcessCronScheduler


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Live scheduler did not reach the expected state")


def test_added_profile_runs_real_script_and_removed_profile_stays_deleted(
    tmp_path, monkeypatch
):
    from cron.jobs import create_job, get_job, update_job, use_cron_store
    from gateway.config import GatewayConfig
    from gateway.profile_lifecycle import profile_homes_snapshot
    from korra_cli.profiles import create_profile, mark_named_profile_deleted
    from korra_constants import set_hermes_home_override, reset_hermes_home_override

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("gateway:\n  multiplex_profiles: true\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("korra_constants.get_default_hermes_root", lambda: home)
    monkeypatch.setattr(
        "korra_cli.profiles._maybe_register_gateway_service", lambda name: None
    )
    config = GatewayConfig(
        multiplex_profiles=True, multiplex_profile_allowlist=["worker"]
    )
    stop = threading.Event()
    scanned = threading.Event()
    snapshots = []

    def homes():
        found = profile_homes_snapshot(config)
        snapshots.append([name for name, _ in found])
        scanned.set()
        return found

    thread = threading.Thread(
        target=InProcessCronScheduler().start,
        args=(stop,),
        kwargs={
            "profile_homes": homes,
            "interval": 0.02,
            "default_profile": "default",
        },
        daemon=True,
    )
    thread.start()
    try:
        assert scanned.wait(3)
        worker = create_profile("worker", no_alias=True, no_skills=True)
        excluded = create_profile("excluded", no_alias=True, no_skills=True)
        # No model and no delivery adapter: the actual scheduler launches this
        # harmless local script, persists completion and writes its output.
        script = worker / "scripts" / "probe.py"
        script.parent.mkdir(exist_ok=True)
        script.write_text(
            "import os\nfrom pathlib import Path\n"
            'home = Path(os.environ["HERMES_HOME"])\n'
            '(home / "ran.txt").write_text("K21_LIVE_PROFILE_OK")\n'
            'print("K21_LIVE_PROFILE_OK")\n'
        )
        token = set_hermes_home_override(str(worker))
        try:
            with use_cron_store(worker):
                job = create_job(
                    prompt=None,
                    schedule="every 1h",
                    repeat=1,
                    no_agent=True,
                    script="probe.py",
                    deliver="local",
                )
                update_job(
                    job["id"],
                    {
                        "next_run_at": (
                            datetime.now(timezone.utc) - timedelta(seconds=1)
                        ).isoformat()
                    },
                )

                def completed():
                    record = get_job(job["id"])
                    return record is not None and record.get("last_status") == "ok"

                wait_until(completed)
        finally:
            reset_hermes_home_override(token)
        assert (worker / "ran.txt").read_text() == "K21_LIVE_PROFILE_OK"
        assert not (home / "ran.txt").exists()
        assert not (excluded / "ran.txt").exists()
        assert all("excluded" not in names for names in snapshots)

        mark_named_profile_deleted(worker)
        worker.rename(tmp_path / "removed-worker")
        before = len(snapshots)
        wait_until(lambda: len(snapshots) >= before + 2)
        assert snapshots[-1] == ["default"]
        assert not worker.exists()
    finally:
        stop.set()
        thread.join(timeout=3)
    assert not thread.is_alive()
