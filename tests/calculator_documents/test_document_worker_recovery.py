"""Deterministic worker recovery with real jobs and a dependency-free reader.

The fake replaces only document parsing. Folder intake, SHA resolution, worker
control flow, capabilities, SQLite artifacts and restart/retry all remain real.
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
from metal_calc.errors import NotFound
from metal_calc.folder_intake import FolderIntake
from metal_calc.registry import Registry


class FakeReader:
    def __init__(self, callback=None):
        self.version = "worker-qa1"
        self.callback = callback
        self.seen = []
        self.temporary_paths = []

    def reader_fingerprint(self, command, options):
        return self.version

    def read_document(self, path, source, output, *, command, options, python):
        data = path.read_bytes()
        assert len(data) == source["bytes"]
        assert hashlib.sha256(data).hexdigest() == source["sha256"]
        assert python == "qa-document-python"
        self.seen.append(source["source_id"])
        self.temporary_paths.append(path)
        code = self.callback(source) if self.callback else None
        return {"schema_version": 2, "command": command, "status": "failed" if code else "complete",
                "complete": code is None, "document_type": "pdf", "use_for_calculation": False,
                "source": {**source, "sha256_verified": True},
                "reader": {"fingerprint": self.version, "version": self.version, "options": options},
                "verification": {"numeric_facts": "unverified", "numeric_confidence": None,
                                 "use_for_calculation": False},
                "coverage": {"inventory_complete": code is None, "pages_total": 1,
                             "pages_inventoried": 0 if code else 1, "pages_accounted": 0 if code else 1,
                             "selected_text_pages": [], "text_pages_read": [], "rendered_pages": [],
                             "text_truncated": False, "sheets_total": None, "sheets_inventoried": 0,
                             "cells_read": 0, "cells_complete": False},
                "errors": [{"code": code, "message": code}] if code else []}


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "agent-home"))
    orders_root = tmp_path / "orders"
    orders_root.mkdir()
    intake = FolderIntake(orders_root)
    request = {"upload_id": str(uuid4()), "folder_name": "34219-worker-qa",
               "files": [{"path": "Папка/a.pdf", "size": 1}, {"path": "Папка/b.pdf", "size": 1}]}
    intake.create(request)
    for index, data in enumerate((b"a", b"b")):
        intake.upload(request["upload_id"], index, io.BytesIO(data))
    order_id = intake.complete(request["upload_id"])["order_id"]
    jobs = DocumentJobs(orders_root)
    reader = FakeReader()
    monkeypatch.setattr(document_worker, "load_reader", lambda: reader)
    job = jobs.start(order_id, reader_version=reader.version)
    baseline = Registry(orders_root / "registry.db").get(order_id)
    yield jobs, job, reader
    assert Registry(orders_root / "registry.db").get(order_id) == baseline
    assert all(not path.exists() for path in reader.temporary_paths)
    intake.close()


def run(jobs, **kwargs):
    return document_worker.run_once(jobs, document_python="qa-document-python", **kwargs)


@pytest.mark.parametrize("code", ["reader_unavailable", "reader_dependency_missing", "reader_version_mismatch",
                                  "invalid_output_directory", "output_directory_not_empty"])
def test_systemic_reader_failure_blocks_job_and_retry_preserves_first_artifact(work, code):
    """Environment-level failures stop the pass; nothing is parsed twice or lost."""
    jobs, job, reader = work
    a, b = job["sources"]
    reader.callback = lambda source: code if source["source_id"] == b["source_id"] else None
    stopped = run(jobs)
    assert stopped == {"job_id": job["job_id"], "status": "blocked", "error": code, "processed": 1}
    snapshot = jobs.get(job["job_id"])
    assert snapshot["status"] == "blocked"
    assert snapshot["execution_outcome"] == "error:" + code
    assert snapshot["coverage"]["files_accounted"] == 1
    assert snapshot["coverage"]["files_pending"] == 1
    assert snapshot["coverage"]["files_failed"] == 0
    before = jobs.result(job["job_id"], a["source_id"])
    with pytest.raises(NotFound):
        jobs.result(job["job_id"], b["source_id"])
    assert run(jobs) == {"claimed": False}
    assert reader.seen == [a["source_id"], b["source_id"]]

    restarted = DocumentJobs(jobs.orders_root)
    restarted.retry(job["job_id"])
    reader.callback = None
    assert run(restarted) == {"job_id": job["job_id"], "status": "completed", "processed": 1}
    assert reader.seen == [a["source_id"], b["source_id"], b["source_id"]]
    assert restarted.result(job["job_id"], a["source_id"]) == before
    with sqlite3.connect(jobs.orders_root / "registry.db") as con:
        assert con.execute("SELECT count(*) FROM document_results").fetchone()[0] == 2
        assert con.execute("SELECT count(*) FROM document_failures").fetchone()[0] == 0
        attempts = con.execute("SELECT outcome, ended_at FROM document_attempts ORDER BY started_at").fetchall()
        assert [row[0] for row in attempts] == ["error:" + code, "completed"]
        assert all(row[1] is not None for row in attempts)


@pytest.mark.parametrize("code", ["worker_timeout", "worker_exit", "invalid_reader_artifact"])
def test_per_source_failure_is_recorded_and_pass_finishes_without_blocking(work, code):
    """A document-level resource failure is a durable retryable failure, not a job block."""
    jobs, job, reader = work
    a, b = job["sources"]
    reader.callback = lambda source: code if source["source_id"] == a["source_id"] else None
    assert run(jobs) == {"job_id": job["job_id"], "status": "partial", "processed": 1, "failed": 1}
    assert reader.seen == [a["source_id"], b["source_id"]]
    snapshot = jobs.get(job["job_id"])
    assert snapshot["status"] == "partial"
    assert snapshot["execution_outcome"] == "partial"
    assert snapshot["coverage"] | {"pages_total_known": 0, "pages_inventoried": 0} == {
        "files_total": 2, "files_accounted": 1, "files_failed": 1, "files_pending": 0,
        "pages_total_known": 0, "pages_inventoried": 0, "file_accounting_complete": False,
        "customer_request_complete": None}
    failed, accepted = snapshot["sources"]
    assert failed["status"] == "failed" and failed["accepted"] is False and failed["result_sha256"] is None
    assert failed["failure"]["code"] == code and failed["failure"]["retry_authorized"] is False
    assert accepted["status"] == "complete" and accepted["accepted"] is True and accepted["failure"] is None
    with pytest.raises(NotFound):
        jobs.result(job["job_id"], a["source_id"])
    history = jobs.failures(job["job_id"], a["source_id"])
    assert [h["code"] for h in history] == [code]
    assert history[0]["source"]["source_id"] == a["source_id"]
    assert history[0]["binding"]["job_id"] == job["job_id"]
    assert history[0]["retryable"] is True

    # Restart never retries a poison document by itself.
    restarted = DocumentJobs(jobs.orders_root)
    assert run(restarted) == {"claimed": False}
    assert len(reader.seen) == 2
    before = restarted.result(job["job_id"], b["source_id"])
    restarted.retry(job["job_id"], source_id=a["source_id"])
    reader.callback = None
    assert run(restarted) == {"job_id": job["job_id"], "status": "completed", "processed": 1}
    assert reader.seen == [a["source_id"], b["source_id"], a["source_id"]]
    assert restarted.result(job["job_id"], b["source_id"]) == before
    assert restarted.result(job["job_id"], a["source_id"])["status"] == "complete"
    # History survives the later success and is still marked as authorized.
    history = restarted.failures(job["job_id"], a["source_id"])
    assert len(history) == 1 and history[0]["retry_authorized_at"] is not None
    assert restarted.get(job["job_id"])["sources"][0]["failure"]["retry_authorized"] is True


def test_deterministic_format_error_is_accounted_and_not_reparsed_on_retry(work):
    jobs, job, reader = work
    b = job["sources"][1]
    reader.callback = lambda source: "malformed_document" if source["source_id"] == b["source_id"] else None
    assert run(jobs) == {"job_id": job["job_id"], "status": "partial", "processed": 2}
    result = jobs.result(job["job_id"], b["source_id"])
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "malformed_document"
    assert jobs.get(job["job_id"])["coverage"]["file_accounting_complete"] is True
    assert jobs.retry(job["job_id"])["status"] == "partial"
    assert run(jobs) == {"claimed": False}
    assert len(reader.seen) == 2


def test_cancel_during_reader_fences_late_result_and_preserves_completed_source(work):
    jobs, job, reader = work
    a, b = job["sources"]

    def cancel_second(source):
        if source["source_id"] == b["source_id"]:
            jobs.cancel(job["job_id"])
        return None

    reader.callback = cancel_second
    assert run(jobs) == {"job_id": job["job_id"], "status": "fenced", "processed": 1}
    snapshot = jobs.get(job["job_id"])
    assert snapshot["status"] == "cancelled"
    assert snapshot["coverage"]["files_accounted"] == 1
    before = jobs.result(job["job_id"], a["source_id"])
    with pytest.raises(NotFound):
        jobs.result(job["job_id"], b["source_id"])
    assert run(jobs) == {"claimed": False}
    jobs.retry(job["job_id"])
    reader.callback = None
    assert run(jobs)["processed"] == 1
    assert jobs.result(job["job_id"], a["source_id"]) == before


def test_drain_during_reader_publishes_current_source_and_releases_remaining(work):
    jobs, job, reader = work
    a, b = job["sources"]

    def drain_after_parse(source):
        jobs.set_drain(True)
        return None

    reader.callback = drain_after_parse
    assert run(jobs) == {"job_id": job["job_id"], "status": "queued", "processed": 1}
    assert reader.seen == [a["source_id"]]
    snapshot = jobs.get(job["job_id"])
    assert snapshot["execution_outcome"] == "checkpoint"
    assert snapshot["coverage"]["files_accounted"] == 1
    assert jobs.set_drain(True)["active_leases"] == 0
    restarted = DocumentJobs(jobs.orders_root)
    assert run(restarted) == {"claimed": False}
    reader.callback = None
    restarted.set_drain(False)
    assert run(restarted) == {"job_id": job["job_id"], "status": "completed", "processed": 1}
    assert reader.seen == [a["source_id"], b["source_id"]]


@pytest.mark.parametrize("control", ["max_sources", "stop_before", "stop_after"])
def test_bounded_tick_or_signal_releases_claim_and_restart_only_reads_pending(work, control):
    jobs, job, reader = work
    stop = {"requested": control == "stop_before"}

    def request_stop(source):
        if control == "stop_after":
            stop["requested"] = True
        return None

    reader.callback = request_stop
    options = {"max_sources": 1} if control == "max_sources" else {"should_stop": lambda: stop["requested"]}
    processed = 0 if control == "stop_before" else 1
    assert run(jobs, **options) == {"job_id": job["job_id"], "status": "queued", "processed": processed}
    assert jobs.get(job["job_id"])["execution_outcome"] == "checkpoint"
    assert len(reader.seen) == processed
    reader.callback = None
    assert run(jobs) == {"job_id": job["job_id"], "status": "completed", "processed": 2 - processed}
    assert reader.seen == [source["source_id"] for source in job["sources"]]


def test_reader_version_change_blocks_before_any_document_is_read(work):
    jobs, job, reader = work
    reader.version = "worker-qa2"
    assert run(jobs) == {"job_id": job["job_id"], "status": "blocked", "processed": 0}
    assert jobs.get(job["job_id"])["execution_outcome"] == "error:reader_version_changed"
    assert reader.seen == []
    reader.version = "worker-qa1"
    jobs.retry(job["job_id"])
    assert run(jobs)["status"] == "completed"


def test_reader_exception_is_sanitized_and_does_not_replace_previous_result(work):
    jobs, job, reader = work
    a, b = job["sources"]

    def fail_second(source):
        if source["source_id"] == b["source_id"]:
            raise RuntimeError("private source path /sensitive/document with credential=secret")
        return None

    reader.callback = fail_second
    stopped = run(jobs)
    assert stopped == {"job_id": job["job_id"], "status": "blocked", "error": "RuntimeError", "processed": 1}
    state = jobs.get(job["job_id"])
    assert state["coverage"]["files_accounted"] == 1
    assert jobs.result(job["job_id"], a["source_id"])["status"] == "complete"
    assert "private" not in json.dumps([state, stopped])
    assert "secret" not in json.dumps([state, stopped])
    with pytest.raises(NotFound):
        jobs.result(job["job_id"], b["source_id"])
    assert run(jobs) == {"claimed": False}
    jobs.retry(job["job_id"])
    reader.callback = None
    assert run(jobs)["processed"] == 1
