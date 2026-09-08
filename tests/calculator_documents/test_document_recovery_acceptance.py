"""Independent acceptance: recover one bad source while retaining good artifacts."""
import hashlib
import io
from pathlib import Path
import sys
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "calculator/metal_calc"))
from metal_calc import document_worker
from metal_calc.document_jobs import DocumentJobs
from metal_calc.errors import MetalCalcError
from metal_calc.folder_intake import FolderIntake
from metal_calc.registry import Registry
from test_document_jobs_adversarial import observation


class Reader:
    version = "recovery-acceptance-v1"

    def __init__(self):
        self.bad = None
        self.seen = []

    def reader_fingerprint(self, command, options):
        return self.version

    def read_document(self, path, source, output, *, command, options, python):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
        self.seen.append(source["source_id"])
        result = observation(source, fingerprint=self.version)
        if source["source_id"] == self.bad or isinstance(self.bad, set) and source["source_id"] in self.bad:
            result.update(status="failed", complete=False,
                          errors=[{"code": "worker_timeout", "message": "synthetic resource limit"}])
            result["coverage"].update(inventory_complete=False, pages_inventoried=0,
                                       pages_accounted=0, selected_text_pages=[], text_pages_read=[])
            if getattr(self, "partial_checkpoint", False):
                result["inventory"] = [{"page": 1, "status": "inspected"}]
                result["text_pages"] = [{"page": 1, "status": "extracted_unverified", "markdown": "saved partial text"}]
                result["coverage"].update(pages_inventoried=1, pages_accounted=1,
                                           selected_text_pages=[1], text_pages_read=[1])
        return result


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "agent-home"))
    root = tmp_path / "orders"
    root.mkdir()
    intake = FolderIntake(root)
    uid = str(uuid4())
    try:
        intake.create({"upload_id": uid, "folder_name": "synthetic recovery",
                       "files": [{"path": name, "size": 1} for name in ("bad.pdf", "good.pdf", "last.pdf")]})
        for i in range(3):
            intake.upload(uid, i, io.BytesIO(b"x"))
        order_id = intake.complete(uid)["order_id"]
    finally:
        intake.close()
    reader = Reader()
    monkeypatch.setattr(document_worker, "load_reader", lambda: reader)
    jobs = DocumentJobs(root)
    job = jobs.start(order_id, reader_version=reader.version)
    baseline = Registry(root / "registry.db").get(order_id)
    yield jobs, job, reader
    assert Registry(root / "registry.db").get(order_id) == baseline


def test_bad_first_file_does_not_starve_rest_and_selected_retry_keeps_success(case):
    jobs, job, reader = case
    bad, good, last = [s["source_id"] for s in job["sources"]]
    reader.bad = bad
    document_worker.run_once(jobs)
    assert reader.seen == [bad, good, last], "A failing source must not block later independent sources"
    assert jobs.get(job["job_id"])["status"] != "completed"
    saved = {sid: jobs.result(job["job_id"], sid) for sid in (good, last)}
    restarted = DocumentJobs(jobs.orders_root)
    before_retry = list(reader.seen)
    document_worker.run_once(restarted)
    assert reader.seen == before_retry, "Persistent failure must not trigger an endless automatic retry"
    restarted.retry(job["job_id"], source_id=bad)
    document_worker.run_once(restarted)
    assert reader.seen == [bad, good, last, bad]
    assert all(restarted.result(job["job_id"], sid) == result for sid, result in saved.items())
    reader.bad = None
    restarted.retry(job["job_id"], source_id=bad)
    document_worker.run_once(restarted)
    assert reader.seen == [bad, good, last, bad, bad]
    assert restarted.get(job["job_id"])["status"] == "completed"
    assert all(restarted.result(job["job_id"], sid) == result for sid, result in saved.items())


def test_selected_retry_rejects_success_or_foreign_source(case):
    jobs, job, reader = case
    bad, good, _ = [s["source_id"] for s in job["sources"]]
    reader.bad = bad
    document_worker.run_once(jobs)
    for source in (good, "src_foreign"):
        with pytest.raises(MetalCalcError):
            jobs.retry(job["job_id"], source_id=source)


def test_selected_retry_after_cancel_does_not_revive_other_retry_authorizations(case):
    jobs, job, reader = case
    a, b, good = [s["source_id"] for s in job["sources"]]
    reader.bad = {a, b}
    document_worker.run_once(jobs)
    assert reader.seen == [a, b, good]
    jobs.retry(job["job_id"])
    jobs.cancel(job["job_id"])
    jobs.retry(job["job_id"], source_id=a)
    start = len(reader.seen)
    document_worker.run_once(jobs)
    assert reader.seen[start:] == [a], "Explicit source retry must not revive a cancelled all-source retry"


def test_failure_keeps_partial_reader_checkpoint_across_restart(case):
    jobs, job, reader = case
    bad = job["sources"][0]["source_id"]
    reader.bad = bad
    reader.partial_checkpoint = True
    document_worker.run_once(jobs)
    restarted = DocumentJobs(jobs.orders_root)
    checkpoint = restarted.failures(job["job_id"], bad)[-1].get("checkpoint")
    assert checkpoint, "Partial inventory/text must survive deletion of the disposable parser directory"
    assert checkpoint["source"]["source_id"] == bad
    assert checkpoint["text_pages"][0]["markdown"] == "saved partial text"
    assert checkpoint["use_for_calculation"] is False
