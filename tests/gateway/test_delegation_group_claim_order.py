"""K21-364 follow-up: a group waiting for a busy result must not burn its neighbours.

Real SQLite, a real second process for the old claim and the real watcher.
Several results of one session are restored together; one of them is still
leased by a previous process. Ready neighbours keep their delivery attempts
for the moment the group can really be injected, wherever the busy result
stands in the group.
"""

import pytest

from gateway.run import _profile_runtime_scope
from tools import async_delegation as ad
from tests.gateway.test_delegation_claim_restart import (  # noqa: F401
    CLAIM, fleet, _accepting_runner, _claim_in_previous_process, _dispatch,
    _expire_lease, _previous_process, _state, _watch,
)


def _attempts(profile, did):
    with _profile_runtime_scope(profile):
        return ad.get_durable_delegation(did)["delivery_attempts"]


def _delivered_results(seen):
    return sum(text.count("test result") for text in seen)


@pytest.mark.parametrize("busy_index", [0, 1, 2], ids=["first", "middle", "last"])
def test_busy_member_does_not_drop_ready_neighbours_while_it_waits(
    fleet, monkeypatch, busy_index,
):
    _root, profile, runner = fleet
    seen = _accepting_runner(runner)
    ids = [_dispatch(profile, web=False, restarted=True) for _ in range(3)]
    busy = ids[busy_index]
    _claim_in_previous_process(profile, busy)
    ready = [did for did in ids if did != busy]
    waiting = []

    def before_tick(tick):
        if tick <= 12:
            waiting.append([(_state(profile, did), _attempts(profile, did)) for did in ready])
        if tick == 12:
            assert _delivered_results(seen) == 2, "the busy result must not be delivered over"
            _expire_lease(profile, busy)

    _watch(monkeypatch, runner, before_tick, ticks=16)
    assert [_state(profile, did) for did in ids] == ["delivered"] * 3, (waiting, seen)
    assert _delivered_results(seen) == 3
    assert all(
        attempts <= 1 for tick in waiting for _state_, attempts in tick
    ), waiting


def test_wait_longer_than_the_attempt_budget_keeps_every_result(fleet, monkeypatch):
    _root, profile, runner = fleet
    seen = _accepting_runner(runner)
    first = _dispatch(profile, web=False, restarted=True)
    second = _dispatch(profile, web=False, restarted=True)
    _claim_in_previous_process(profile, first)
    ticks_waited = ad._MAX_DELIVERY_ATTEMPTS * 3

    def before_tick(tick):
        if tick == ticks_waited:
            assert _state(profile, second) != "dropped"
            _expire_lease(profile, first)

    _watch(monkeypatch, runner, before_tick, ticks=ticks_waited + 4)
    assert _state(profile, first) == "delivered"
    assert _state(profile, second) == "delivered"
    assert _delivered_results(seen) == 2


def test_previous_process_receipt_during_the_wait_means_no_repeat(fleet, monkeypatch):
    _root, profile, runner = fleet
    seen = _accepting_runner(runner)
    first = _dispatch(profile, web=False, restarted=True)
    second = _dispatch(profile, web=False, restarted=True)
    _claim_in_previous_process(profile, first)

    def before_tick(tick):
        if tick == 10:
            _previous_process(
                profile,
                f"assert ad.complete_completion_delivery({first!r}, {CLAIM!r})",
            )

    _watch(monkeypatch, runner, before_tick, ticks=14)
    assert _state(profile, first) == "delivered"
    assert _state(profile, second) == "delivered"
    assert _delivered_results(seen) == 1, seen
    assert "test result" in seen[0]

