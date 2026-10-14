"""Reconcile live counts even after other writers update the status file."""

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_busy_snapshot_uses_the_same_sample_it_remembers():
    from gateway.run import GatewayRunner
    from gateway.status import read_runtime_status, write_runtime_status
    from tests.gateway.restart_test_helpers import make_restart_runner

    runner, _ = make_restart_runner()
    runner._external_drain_active = False
    samples = iter([0, 1, 0, 0])
    runner._active_work_counts = lambda: {
        "messaging": 0, "cron": next(samples), "api": 0,
    }
    # The cron thread starts between the watcher's comparison and the
    # persistence helper's second read, then finishes before the next tick.
    write_runtime_status(gateway_state="running", active_agents=0)
    snapshots = []

    async def next_tick(_interval):
        snapshots.append(read_runtime_status()["active_agents"])
        if len(snapshots) == 3:
            runner._running = False

    with patch("gateway.run.asyncio.sleep", side_effect=next_tick):
        await GatewayRunner._drain_control_watcher(runner)
    assert snapshots[-1] == 0


@pytest.mark.asyncio
async def test_busy_snapshot_reconciles_turn_boundary_write_between_ticks():
    from gateway.run import GatewayRunner
    from gateway.status import read_runtime_status, write_runtime_status
    from tests.gateway.restart_test_helpers import make_restart_runner

    runner, _ = make_restart_runner()
    runner._external_drain_active = False
    count = [0]
    runner._active_cron_job_count = lambda: count[0]
    runner._active_api_run_count = lambda: 0
    write_runtime_status(gateway_state="running", active_agents=0)
    snapshots = []

    async def next_tick(_interval):
        snapshots.append(read_runtime_status().get("active_agents", 0))
        if len(snapshots) == 1:
            # A messaging turn completes while a short cron is still active.
            # Its real persistence helper writes 1; cron then completes before
            # the next watcher tick. No race between individual instructions.
            count[0] = 1
            runner._persist_active_agents()
            count[0] = 0
        if len(snapshots) == 3:
            runner._running = False

    with patch("gateway.run.asyncio.sleep", side_effect=next_tick):
        await GatewayRunner._drain_control_watcher(runner)
    assert snapshots[-1] == 0
