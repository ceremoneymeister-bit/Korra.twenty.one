"""Per-source failure records: fencing, retry addressing, restart and old-schema upgrade.

Real SQLite registry, real folder intake and a dependency-free fake reader.
No document bytes beyond one-byte synthetic sources.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "calculator" / "metal_calc"))

from metal_calc import document_worker
from metal_calc.document_jobs import DocumentJobs
from metal_calc.errors import Conflict, InvalidState, NotFound, OrderScopeDenied
from metal_calc.folder_intake import FolderIntake
from metal_calc.registry import Registry

READER = "queue-failures-v1"


class Clock:
    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def observation(source, code=None):
    return {"schema_version": 2, "command": "inspect", "status": "failed" if code else "complete",
            "complete": code is None, "document_type": "pdf", "use_for_calculation": False,
            "source": {**source, "sha256_verified": True},
            "reader": {"fingerprint": READER, "version": READER, "options": {}},
            "verification": {"numeric_facts": "unverified", "numeric_confidence": None,
                             "use_for_calculation": False},
            "coverage": {"inventory_complete": code is None, "pages_total": 1,
                         "pages_inventoried": 0 if code else 1, "pages_accounted": 0 if code else 1,
                         "selected_text_pages": [], "text_pages_read": [], "rendered_pages": [],
                         "text_truncated": False, "sheets_total": None, "sheets_inventoried": 0,
                         "cells_read": 0, "cells_complete": False},
            "errors": [{"code": code, "message": "synthetic " + code}] if code else []}


class FakeReader:
    def __init__(self):
        self.codes = {}
        self.seen = []

    def reader_fingerprint(self, command, options):
        return READER

    def read_document(self, path, source, output, *, command, options, python):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
        self.seen.append(source["source_id"])
        return observation(source, self.codes.get(source["source_id"]))


def upload(root, names, folder="queue-failures"):
    intake = FolderIntake(root)
    uid = str(uuid4())
    try:
        intake.create({"upload_id": uid, "folder_name": folder,
                       "files": [{"path": name, "size": 1} for name in names]})
        for index, name in enumerate(names):
            intake.upload(uid, index, io.BytesIO(name[:1].encode()))
        return intake.complete(uid)["order_id"]
    finally:
        intake.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "agent-home"))
    root = tmp_path / "orders"
    root.mkdir()
    clock = Clock()
    reader = FakeReader()
    monkeypatch.setattr(document_worker, "load_reader", lambda: reader)
    order_id = upload(root, ["a.pdf", "b.pdf", "c.pdf"])
    jobs = DocumentJobs(root, clock=clock)
    job = jobs.start(order_id, reader_version=READER)
    registry = Registry(root / "registry.db")
    baseline = registry.get(order_id)
    yield root, jobs, job, reader, clock, order_id
    assert registry.get(order_id) == baseline


def failure(code="worker_timeout", **extra):
    return {"code": code, "message": "synthetic " + code, "errors": [{"code": code, "message": code}],
            "reader": {"fingerprint": READER, "version": READER}} | extra


def run(jobs):
    return document_worker.run_once(jobs, document_python=None)


def test_failed_first_source_does_not_starve_later_sources_and_only_it_is_retried(env):
    root, jobs, job, reader, clock, _ = env
    a, b, c = [s["source_id"] for s in job["sources"]]
    reader.codes[a] = "worker_timeout"
    assert run(jobs) == {"job_id": job["job_id"], "status": "partial", "processed": 2, "failed": 1}
    assert reader.seen == [a, b, c]
    accepted = {sid: jobs.result(job["job_id"], sid) for sid in (b, c)}
    shas = {s["source_id"]: s["result_sha256"] for s in jobs.get(job["job_id"])["sources"]}
    assert shas[a] is None and shas[b] and shas[c]

    # Restart: partial with a failure is not claimable, no automatic retry.
    restarted = DocumentJobs(root, clock=clock)
    assert run(restarted) == {"claimed": False}
    assert restarted.get(job["job_id"])["coverage"]["files_failed"] == 1

    # Targeted retry re-reads only the failed one; second failure is generation 2.
    assert restarted.retry(job["job_id"], source_id=a)["status"] == "queued"
    assert run(restarted) == {"job_id": job["job_id"], "status": "partial", "processed": 0, "failed": 1}
    assert reader.seen == [a, b, c, a]
    assert [h["generation"] for h in restarted.failures(job["job_id"], a)] == [1, 2]

    # After repair the job completes and accepted SHAs are byte-identical.
    del reader.codes[a]
    restarted.retry(job["job_id"], source_id=a)
    assert run(restarted) == {"job_id": job["job_id"], "status": "completed", "processed": 1}
    final = restarted.get(job["job_id"])
    assert final["status"] == "completed" and final["coverage"]["file_accounting_complete"] is True
    assert {s["source_id"]: s["result_sha256"] for s in final["sources"] if s["source_id"] != a} == {
        b: shas[b], c: shas[c]}
    assert all(restarted.result(job["job_id"], sid) == value for sid, value in accepted.items())
    assert final["composition"]["quantity"]["value"] is None
    assert final["composition"]["calculation_ready"] is False
    with sqlite3.connect(root / "registry.db") as con:
        assert con.execute("SELECT count(*) FROM document_results").fetchone()[0] == 3
        assert con.execute("SELECT count(*) FROM document_failures").fetchone()[0] == 2


def test_job_level_retry_requeues_all_failed_sources_but_never_accepted_ones(env):
    root, jobs, job, reader, clock, _ = env
    a, b, c = [s["source_id"] for s in job["sources"]]
    reader.codes.update({a: "worker_exit", c: "invalid_reader_artifact"})
    assert run(jobs) == {"job_id": job["job_id"], "status": "partial", "processed": 1, "failed": 2}
    before = jobs.result(job["job_id"], b)
    reader.codes.clear()
    assert jobs.retry(job["job_id"])["status"] == "queued"
    assert run(jobs) == {"job_id": job["job_id"], "status": "completed", "processed": 2}
    assert reader.seen == [a, b, c, a, c]
    assert jobs.result(job["job_id"], b) == before
    # Nothing left to retry: explicit job retry on a completed job is a no-op.
    assert jobs.retry(job["job_id"])["status"] == "completed"
    assert run(jobs) == {"claimed": False}


def test_selected_retry_rejects_accepted_never_failed_foreign_running_and_stale(env):
    root, jobs, job, reader, clock, order_id = env
    a, b, c = [s["source_id"] for s in job["sources"]]
    other = jobs.start(upload(root, ["z.pdf"], folder="other"), reader_version=READER)
    reader.codes[a] = "worker_timeout"
    claim = jobs.claim(job["job_id"], owner="qa", lease_seconds=10)
    with pytest.raises(Conflict):
        jobs.retry(job["job_id"], source_id=a)  # running job
    jobs.release(claim)
    assert run(jobs)["failed"] == 1
    with pytest.raises(InvalidState):
        jobs.retry(job["job_id"], source_id=b)  # accepted result
    with pytest.raises(OrderScopeDenied):
        jobs.retry(job["job_id"], source_id=other["sources"][0]["source_id"])  # foreign source
    with pytest.raises(OrderScopeDenied):
        jobs.retry(job["job_id"], source_id="src_missing")
    assert jobs.get(other["job_id"])["status"] == "queued"
    assert jobs.get(job["job_id"])["coverage"]["files_failed"] == 1

    # Never-failed pending source on a cancelled job.
    pending_job = jobs.start(upload(root, ["p.pdf", "q.pdf"], folder="pending"), reader_version=READER)
    jobs.cancel(pending_job["job_id"])
    with pytest.raises(InvalidState):
        jobs.retry(pending_job["job_id"], source_id=pending_job["sources"][0]["source_id"])

    # Manifest change makes a job stale; even a reverted manifest keeps it stale.
    stale_order = upload(root, ["s.pdf"], folder="stale")
    stale_job = jobs.start(stale_order, reader_version=READER)
    s = stale_job["sources"][0]["source_id"]
    stale_claim = jobs.claim(stale_job["job_id"], owner="qa", lease_seconds=10)
    jobs.record_failure(stale_claim, s, failure())
    assert jobs.finish(stale_claim)["status"] == "partial"
    registry = Registry(root / "registry.db")
    registry.mutate(stale_order, lambda state: state["folder_intake"]["directories"].append("Новая"))
    jobs.start(stale_order, reader_version=READER)
    assert jobs.get(stale_job["job_id"])["status"] == "stale"
    registry.mutate(stale_order, lambda state: state["folder_intake"]["directories"].remove("Новая"))
    for source_id in (s, None):
        with pytest.raises(Conflict):
            jobs.retry(stale_job["job_id"], source_id=source_id)
    assert jobs.get(stale_job["job_id"])["status"] == "stale"
    assert [h["retry_authorized_at"] for h in jobs.failures(stale_job["job_id"], s)] == [None]


def test_record_failure_is_fenced_like_publish(env):
    root, jobs, job, reader, clock, order_id = env
    a, b, c = [s["source_id"] for s in job["sources"]]
    other = jobs.start(upload(root, ["z.pdf"], folder="other"), reader_version=READER)
    claim = jobs.claim(job["job_id"], owner="qa", lease_seconds=10)
    with pytest.raises(OrderScopeDenied):
        jobs.record_failure(claim, other["sources"][0]["source_id"], failure())
    with pytest.raises(OrderScopeDenied):
        jobs.record_failure({**claim, "fence": claim["fence"] + 1}, a, failure())
    with pytest.raises(OrderScopeDenied):
        jobs.record_failure({**claim, "capability": "x" * 43}, a, failure())
    for bad in ("worker_timeout", {"code": "malformed_document"}, {"code": "reader_unavailable"},
                failure(message="m" * 401), failure(errors=[{"code": "x", "message": 5}]),
                failure(errors=[{"code": "x", "message": "y"}] * 21)):
        with pytest.raises(InvalidState):
            jobs.record_failure(claim, a, bad)
    jobs.publish(claim, b, observation(job["sources"][1]))
    with pytest.raises(Conflict):
        jobs.record_failure(claim, b, failure())  # accepted result stays untouched
    receipt = jobs.record_failure(claim, a, failure())
    assert receipt["generation"] == 1
    with pytest.raises(Conflict):
        jobs.record_failure(claim, a, failure())  # bounded: one record per authorized pass
    assert [s["source_id"] for s in jobs.pending(claim)] == [c]
    with pytest.raises(Conflict):
        jobs.finish(claim)  # c is still pending
    # Expired lease: neither failure nor finish is accepted from the old claim.
    clock.advance(11)
    with pytest.raises(OrderScopeDenied):
        jobs.record_failure(claim, c, failure())
    fresh = jobs.claim(job["job_id"], owner="second", lease_seconds=10)
    assert [s["source_id"] for s in jobs.pending(fresh)] == [c]
    with pytest.raises(OrderScopeDenied):
        jobs.record_failure(claim, c, failure())  # superseded fence
    jobs.cancel(job["job_id"])
    with pytest.raises(OrderScopeDenied):
        jobs.record_failure(fresh, c, failure())
    state = jobs.get(job["job_id"])
    assert state["status"] == "cancelled"
    assert [s["status"] for s in state["sources"]] == ["failed", "complete", "pending"]
    assert jobs.failures(job["job_id"], a)[0]["failure_sha256"] == receipt["failure_sha256"]
    # Changed manifest fences failure recording of a fresh claim too.
    changed_order = upload(root, ["y.pdf"], folder="changed")
    changed_job = jobs.start(changed_order, reader_version=READER)
    third = jobs.claim(changed_job["job_id"], owner="third", lease_seconds=10)
    Registry(root / "registry.db").mutate(changed_order, lambda s: s["folder_intake"]["directories"].append("Новая"))
    with pytest.raises(OrderScopeDenied):
        jobs.record_failure(third, changed_job["sources"][0]["source_id"], failure())
    with sqlite3.connect(root / "registry.db") as con:
        assert con.execute("SELECT count(*) FROM document_failures").fetchone()[0] == 1


def test_failure_record_is_bounded_sanitized_and_carries_provenance(env):
    root, jobs, job, reader, clock, _ = env
    a = job["sources"][0]
    claim = jobs.claim(job["job_id"], owner="qa", lease_seconds=10)
    payload = failure(reader={"fingerprint": READER, "version": READER, "path": "/tmp/secret",
                              "options": {"private": True}}, extra="ignored", temporary="/tmp/leak")
    jobs.record_failure(claim, a["source_id"], payload)
    record = jobs.failures(job["job_id"], a["source_id"])[0]
    text = json.dumps(record)
    assert "/tmp" not in text and "ignored" not in text and "private" not in text
    assert record["reader"] == {"fingerprint": READER, "version": READER}
    assert record["source"] == {k: a[k] for k in ("source_id", "sha256", "bytes", "relative_path")}
    assert record["binding"] == {"job_id": job["job_id"], "snapshot_id": job["snapshot_id"],
                                 "document_set_revision": job["document_set_revision"],
                                 "manifest_digest": job["manifest_digest"],
                                 "pipeline_fingerprint": job["pipeline_fingerprint"]}
    assert record["attempt_id"] == claim["attempt_id"] and record["fence"] == claim["fence"]
    with sqlite3.connect(root / "registry.db") as con:
        con.execute("UPDATE document_failures SET failure_json=? WHERE job_id=?", (b'{"code":"tampered"}', job["job_id"]))
    with pytest.raises(Conflict):
        jobs.failures(job["job_id"], a["source_id"])


def test_cancel_revokes_unconsumed_authorizations_and_addressed_retry_reads_only_its_source(env):
    root, jobs, job, reader, clock, _ = env
    a, b, c = [s["source_id"] for s in job["sources"]]
    reader.codes.update({a: "worker_timeout", b: "worker_exit"})
    assert run(jobs)["failed"] == 2
    accepted = jobs.result(job["job_id"], c)
    assert jobs.retry(job["job_id"])["status"] == "queued"
    assert all(s["failure"]["retry_authorized"] for s in jobs.get(job["job_id"])["sources"][:2])
    cancelled = jobs.cancel(job["job_id"])
    assert cancelled["status"] == "cancelled"
    assert [s["status"] for s in cancelled["sources"]] == ["failed", "failed", "complete"]
    assert not any(s["failure"]["retry_authorized"] for s in cancelled["sources"][:2])
    assert run(jobs) == {"claimed": False}
    reader.codes.clear()
    jobs.retry(job["job_id"], source_id=a)
    assert run(jobs) == {"job_id": job["job_id"], "status": "partial", "processed": 1}
    assert reader.seen == [a, b, c, a]
    state = jobs.get(job["job_id"])
    assert [s["status"] for s in state["sources"]] == ["complete", "failed", "complete"]
    assert jobs.result(job["job_id"], c) == accepted
    # Failure JSON rows are unchanged by revocation; only the scheduling mark moved.
    assert [h["generation"] for h in jobs.failures(job["job_id"], b)] == [1]
    assert jobs.failures(job["job_id"], b)[0]["retry_authorized_at"] is None
    jobs.retry(job["job_id"], source_id=b)
    assert run(jobs)["status"] == "completed"
    assert reader.seen == [a, b, c, a, b]


def test_addressed_retry_rejects_job_with_never_read_sources_but_job_retry_works(env):
    root, jobs, job, reader, clock, _ = env
    a, b, c = [s["source_id"] for s in job["sources"]]
    claim = jobs.claim(job["job_id"], owner="qa", lease_seconds=10)
    jobs.record_failure(claim, a, failure())
    jobs.cancel(job["job_id"])  # b and c were never read
    with pytest.raises(Conflict):
        jobs.retry(job["job_id"], source_id=a)
    assert jobs.get(job["job_id"])["status"] == "cancelled"
    assert run(jobs) == {"claimed": False}
    assert jobs.retry(job["job_id"])["status"] == "queued"
    assert run(jobs) == {"job_id": job["job_id"], "status": "completed", "processed": 3}
    assert reader.seen == [a, b, c]


def partial_checkpoint(chunk, **extra):
    """Unverified partial reader result for `chunk`; `extra` overrides top-level keys (incl. `source`)."""
    result = observation(chunk, "worker_timeout")
    result["coverage"].update(pages_inventoried=1, pages_accounted=1, selected_text_pages=[1], text_pages_read=[1])
    result["inventory"] = [{"page": 1, "status": "inspected"}]
    result["text_pages"] = [{"page": 1, "status": "extracted_unverified", "markdown": "partial text"}]
    return result | extra


def test_failure_checkpoint_is_validated_bounded_and_kept_apart_from_accepted_results(env):
    root, jobs, job, reader, clock, _ = env
    a = job["sources"][0]
    claim = jobs.claim(job["job_id"], owner="qa", lease_seconds=10)
    good = partial_checkpoint(a)
    rejected = {
        "wrong_sha": partial_checkpoint(a, source={**a, "sha256": "0" * 64}),
        "wrong_reader": partial_checkpoint(a, reader={"fingerprint": "other", "version": "x", "options": {}}),
        "wrong_options": partial_checkpoint(a, reader={"fingerprint": READER, "version": READER, "options": {"p": 1}}),
        "claims_complete": partial_checkpoint(a, status="complete", complete=True, errors=[],
                                              coverage={**good["coverage"], "inventory_complete": True}),
        "self_verified": partial_checkpoint(a, verification={"numeric_facts": "verified", "use_for_calculation": False}),
        "human_receipt": partial_checkpoint(a, human_receipt={"actor": "model", "accepted": True}),
        "calculation": partial_checkpoint(a, use_for_calculation=True),
        "foreign_binding": partial_checkpoint(a, binding={"job_id": "doc_other"}),
        "not_object": "text",
    }
    for kind, checkpoint in rejected.items():
        with pytest.raises((InvalidState, Conflict)):
            jobs.record_failure(claim, a["source_id"], failure(checkpoint=checkpoint))
    assert jobs.failures(job["job_id"], a["source_id"]) == [], "rejected checkpoints record nothing"

    # Image of a partial render is discarded, never referenced as an asset.
    with_image = partial_checkpoint(a, image={"path": "page.png", "sha256": "f" * 64, "bytes": 10, "width": 1, "height": 1})
    receipt = jobs.record_failure(claim, a["source_id"], failure(checkpoint=with_image))
    record = jobs.failures(job["job_id"], a["source_id"])[0]
    checkpoint = record["checkpoint"]
    assert "image" not in checkpoint and record["checkpoint_image_discarded"] is True
    assert checkpoint["text_pages"][0]["markdown"] == "partial text"
    assert checkpoint["source"]["source_id"] == a["source_id"] and checkpoint["source"]["sha256"] == a["sha256"]
    assert checkpoint["reader"]["fingerprint"] == READER and checkpoint["coverage"]["pages_inventoried"] == 1
    assert checkpoint["use_for_calculation"] is False and checkpoint["complete"] is False
    assert checkpoint["binding"]["job_id"] == job["job_id"]
    assert checkpoint["binding"] == record["binding"]
    assert record["checkpoint_truncated"] is False and record["failure_sha256"] == receipt["failure_sha256"]
    # Compact status view carries the failure mark but never the checkpoint body.
    state = jobs.get(job["job_id"])
    assert state["sources"][0]["failure"]["code"] == "worker_timeout"
    assert "partial text" not in json.dumps(state)
    with pytest.raises(NotFound):
        jobs.result(job["job_id"], a["source_id"])

    # Oversized checkpoint: text dropped explicitly, failure still recorded, pass continues.
    b = job["sources"][1]
    huge = partial_checkpoint(b)
    huge["text_pages"] = [{"page": 1, "status": "extracted_unverified", "markdown": "x" * (600 * 1024)}]
    jobs.record_failure(claim, b["source_id"], failure(checkpoint=huge))
    record = jobs.failures(job["job_id"], b["source_id"])[0]
    assert record["checkpoint_truncated"] is True and record["checkpoint_dropped"] == ["text_pages"]
    assert "text_pages" not in record["checkpoint"] and record["checkpoint"]["inventory"] == huge["inventory"]
    assert record["checkpoint"]["coverage"] == huge["coverage"]
    assert [s["source_id"] for s in jobs.pending(claim)] == [job["sources"][2]["source_id"]]


def test_worker_keeps_reader_checkpoint_and_survives_malformed_partial(env):
    root, jobs, job, reader, clock, _ = env
    a, b, c = [s["source_id"] for s in job["sources"]]
    partial = {a: partial_checkpoint(job["sources"][0]),
               b: partial_checkpoint(job["sources"][1], human_receipt={"actor": "model", "accepted": True})}
    reader.read_document = lambda path, source, output, **kw: (reader.seen.append(source["source_id"])
                                                                or partial.get(source["source_id"], observation(source)))
    assert run(jobs) == {"job_id": job["job_id"], "status": "partial", "processed": 1, "failed": 2}
    assert reader.seen == [a, b, c]
    restarted = DocumentJobs(root, clock=clock)
    kept = restarted.failures(job["job_id"], a)[-1]
    assert kept["checkpoint"]["text_pages"][0]["markdown"] == "partial text"
    assert kept["checkpoint_rejected"] is False
    dropped = restarted.failures(job["job_id"], b)[-1]
    assert dropped["checkpoint"] is None and dropped["checkpoint_rejected"] is True
    assert dropped["code"] == "worker_timeout"
    assert restarted.result(job["job_id"], c)["status"] == "complete"


def test_lease_metadata_is_truthful_and_expired_lease_is_visible(env):
    root, jobs, job, reader, clock, _ = env
    state = jobs.get(job["job_id"])
    assert state["lease_until"] is None and state["lease_expired"] is False
    claim = jobs.claim(job["job_id"], owner="qa", lease_seconds=10)
    state = jobs.get(job["job_id"])
    assert state["lease_until"] == clock.now + 10 and state["lease_expired"] is False
    clock.advance(11)
    state = jobs.get(job["job_id"])
    assert state["status"] == "running" and state["lease_expired"] is True
    with pytest.raises(OrderScopeDenied):
        jobs.renew(claim, lease_seconds=10)
    jobs.cancel(job["job_id"])
    state = jobs.get(job["job_id"])
    assert state["lease_until"] is None and state["lease_expired"] is False


def test_upgrade_from_schema_without_failures_keeps_orders_and_accepted_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "agent-home"))
    root = tmp_path / "orders"
    root.mkdir()
    reader = FakeReader()
    monkeypatch.setattr(document_worker, "load_reader", lambda: reader)
    order_id = upload(root, ["a.pdf", "b.pdf"])
    jobs = DocumentJobs(root)
    job = jobs.start(order_id, reader_version=READER)
    reader.codes[job["sources"][1]["source_id"]] = "malformed_document"
    assert run(jobs)["status"] == "partial"
    # Simulate the pre-fix database: no failure table, historical partial job.
    with sqlite3.connect(root / "registry.db") as con:
        con.execute("DROP TABLE document_failures")
        orders_before = con.execute("SELECT order_id,revision,state_json FROM orders ORDER BY order_id").fetchall()
        results_before = con.execute("SELECT * FROM document_results ORDER BY source_id").fetchall()
        jobs_before = con.execute("SELECT job_id,status,fence,attempt_id FROM document_jobs").fetchall()
    upgraded = DocumentJobs(root)
    state = upgraded.get(job["job_id"])
    assert state["status"] == "partial" and state["coverage"]["files_failed"] == 0
    assert state["coverage"]["file_accounting_complete"] is True
    assert all(s["failure"] is None and s["accepted"] for s in state["sources"])
    assert upgraded.retry(job["job_id"])["status"] == "partial"
    assert run(upgraded) == {"claimed": False}
    assert len(reader.seen) == 2
    with sqlite3.connect(root / "registry.db") as con:
        assert con.execute("SELECT order_id,revision,state_json FROM orders ORDER BY order_id").fetchall() == orders_before
        assert con.execute("SELECT * FROM document_results ORDER BY source_id").fetchall() == results_before
        assert con.execute("SELECT job_id,status,fence,attempt_id FROM document_jobs").fetchall() == jobs_before
        assert con.execute("SELECT count(*) FROM document_failures").fetchone()[0] == 0
