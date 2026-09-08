"""Initial answers are durable operator decisions, never analysis admission."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_intake_handoffs import connected, order
from metal_calc.errors import Conflict, InvalidState
from metal_calc.intake_handoffs import IntakeHandoffs
from metal_calc.intake_preparation import DEFAULT_ANSWERS, IntakePreparation
from metal_calc.registry import Registry


@pytest.fixture
def ready(tmp_path):
    handoffs = IntakeHandoffs(tmp_path)
    record = connected(handoffs, order(tmp_path))
    handoffs.context(record["session_id"])
    handoffs.mark_dispatched(record["handoff_id"], "run_test")
    handoffs.mark_finished(record["handoff_id"])
    return handoffs, record, IntakePreparation(handoffs)


def save(store, record, **changes):
    args = {"snapshot_id": record["snapshot_id"], "expected_revision": 0,
            "request_id": "request-one", "answers": {**DEFAULT_ANSWERS, "scope": "whole"}}
    args.update(changes)
    return store.save(record["handoff_id"], **args)


def test_answers_reload_and_replay_do_not_change_order_or_admit_jobs(ready, tmp_path):
    handoffs, record, store = ready
    before = Registry(tmp_path / "registry.db").get(record["order_id"])
    assert store.answers(record["handoff_id"])["revision"] == 0
    first = save(store, record)
    assert first["answers"]["scope"] == "whole"
    assert first["receipt"]["source"] == "operator_cabinet"
    restored = IntakePreparation(IntakeHandoffs(tmp_path))
    assert restored.answers(record["handoff_id"]) == first
    assert save(restored, record) == first
    assert Registry(tmp_path / "registry.db").get(record["order_id"]) == before
    assert handoffs.jobs.list(record["order_id"])["jobs"] == []
    assert handoffs.context(record["session_id"])["human_approved"] is False


def test_concurrent_distinct_answers_require_current_version(ready):
    _, record, store = ready
    def attempt(index):
        try:
            return save(store, record, request_id=f"answer-{index}")
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert sum(result is not None for result in results) == 1
    assert store.answers(record["handoff_id"])["revision"] == 1


def test_reused_request_and_stale_snapshot_rejected(ready):
    _, record, store = ready
    save(store, record)
    with pytest.raises(Conflict):
        save(store, record, answers={**DEFAULT_ANSWERS, "scope": "unknown"})
    with pytest.raises(Conflict):
        save(store, record, snapshot_id="snap_other")
    with pytest.raises(Conflict):
        save(store, record, request_id="new")
    latest = save(store, record, request_id="new", expected_revision=1,
                  answers={**DEFAULT_ANSWERS, "scope": "selected", "scope_note": "Изделие А"})
    assert save(store, record) == latest


@pytest.mark.parametrize("answers", [
    {}, {**DEFAULT_ANSWERS, "scope": "selected"},
    {**DEFAULT_ANSWERS, "notes": "x" * 2001},
    {**DEFAULT_ANSWERS, "scope": []},
    {**DEFAULT_ANSWERS, "human_approved": True},
])
def test_invalid_answers_never_persist(ready, answers):
    _, record, store = ready
    with pytest.raises(InvalidState):
        save(store, record, answers=answers)
    assert store.answers(record["handoff_id"])["revision"] == 0


def test_changed_document_and_pending_receipt_deny_write(ready, tmp_path):
    handoffs, record, store = ready
    with handoffs.jobs._db() as db:
        db.execute("UPDATE intake_handoffs SET dispatch_status='running' WHERE handoff_id=?",
                   (record["handoff_id"],))
    with pytest.raises(Conflict):
        save(store, record)
    handoffs.mark_finished(record["handoff_id"])
    Registry(tmp_path / "registry.db").mutate(record["order_id"],
        lambda state: state["folder_intake"]["files"][0].update(sha256="f" * 64))
    with pytest.raises(Conflict):
        save(store, record)
