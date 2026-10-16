"""K21-364: a result held by a dead process's delivery claim is not lost.

Real SQLite and a real second process for the claim; only the receiving
adapter is replaced. The five-minute claim lease is expired by editing the
claim timestamp instead of waiting.
"""

import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace

from gateway.config import Platform
from gateway.run import _profile_runtime_scope
from tools import async_delegation as ad
from tests.gateway.test_multiplex_delegation_delivery import (  # noqa: F401
    fleet, _dispatch,
)

CLAIM = "previous-gateway:1:test"


def _previous_process(profile, code):
    subprocess.run(
        [sys.executable, "-c", "from tools import async_delegation as ad\n" + code],
        check=True, timeout=15,
        env=dict(os.environ, HERMES_HOME=str(profile), PYTHONDONTWRITEBYTECODE="1"),
    )


def _claim_in_previous_process(profile, did):
    _previous_process(
        profile, f"assert ad.claim_completion_delivery({did!r}, {CLAIM!r})",
    )


def _expire_lease(profile, did):
    with _profile_runtime_scope(profile), ad._transaction() as conn:
        conn.execute(
            "UPDATE async_delegations SET delivery_claimed_at=0 WHERE delegation_id=?",
            (did,),
        )


def _watch(monkeypatch, runner, before_tick, ticks=5):
    calls = 0

    async def sleep(_delay):
        nonlocal calls
        calls += 1
        before_tick(calls)
        if calls >= ticks:
            runner._running = False

    monkeypatch.setattr(asyncio, "sleep", sleep)
    asyncio.run(runner._async_delegation_watcher(interval=0))


def _accepting_runner(runner):
    seen = []

    async def accept(event):
        seen.append(event.text)

    runner.adapters = {}
    runner._profile_adapters = {
        "worker": {Platform.TELEGRAM: SimpleNamespace(handle_message=accept)}
    }
    return seen


def _state(profile, did):
    with _profile_runtime_scope(profile):
        return ad.get_durable_delegation(did)["delivery_state"]


def test_claim_of_dead_process_is_retried_once_after_lease_expires(fleet, monkeypatch):
    _root, profile, runner = fleet
    seen = _accepting_runner(runner)
    did = _dispatch(profile, web=False, restarted=True)
    _claim_in_previous_process(profile, did)

    def before_tick(tick):
        if tick == 3:
            assert seen == [], "a live claim must not be delivered over"
            _expire_lease(profile, did)

    _watch(monkeypatch, runner, before_tick)
    assert len(seen) == 1 and "test result" in seen[0]
    assert _state(profile, did) == "delivered"


def test_delivery_receipt_before_restart_means_no_retry(fleet, monkeypatch):
    _root, profile, runner = fleet
    seen = _accepting_runner(runner)
    did = _dispatch(profile, web=False, restarted=True)
    _claim_in_previous_process(profile, did)

    def before_tick(tick):
        if tick == 2:
            # The old consumer's acknowledgement lands while we are waiting.
            _previous_process(
                profile,
                f"assert ad.complete_completion_delivery({did!r}, {CLAIM!r})",
            )
            _expire_lease(profile, did)

    _watch(monkeypatch, runner, before_tick)
    assert seen == []
    assert _state(profile, did) == "delivered"


def test_acknowledged_result_is_not_restored_or_repeated(fleet, monkeypatch):
    _root, profile, runner = fleet
    seen = _accepting_runner(runner)
    did = _dispatch(profile, web=False, restarted=True)
    _claim_in_previous_process(profile, did)
    _previous_process(
        profile, f"assert ad.complete_completion_delivery({did!r}, {CLAIM!r})",
    )
    _watch(monkeypatch, runner, lambda _tick: None, ticks=3)
    assert seen == []
    assert _state(profile, did) == "delivered"


def test_busy_sibling_in_a_batch_is_retried_not_dropped(fleet, monkeypatch):
    _root, profile, runner = fleet
    seen = _accepting_runner(runner)
    first = _dispatch(profile, web=False, restarted=True)
    second = _dispatch(profile, web=False, restarted=True)
    _claim_in_previous_process(profile, second)

    def before_tick(tick):
        if tick == 3:
            _expire_lease(profile, second)

    _watch(monkeypatch, runner, before_tick)
    assert _state(profile, first) == "delivered"
    assert _state(profile, second) == "delivered"
    assert len(seen) == 2
