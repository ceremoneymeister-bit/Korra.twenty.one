"""Real SQLite and source receipts; never uses a live HOME or order."""
import io
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "calculator/metal_calc"))
from metal_calc.document_jobs import DocumentJobs
from metal_calc.errors import OrderScopeDenied
from metal_calc.folder_intake import FolderIntake
from metal_calc.registry import Registry


def make_order(root, paths=("drawing.pdf", "Thumbs.db"), content=b"data"):
    root.mkdir(exist_ok=True)
    with_intake = FolderIntake(root)
    try:
        uid = str(uuid4())
        with_intake.create({"upload_id": uid, "folder_name": "34219 test " + uid,
                            "files": [{"path": p, "size": len(content)} for p in paths]})
        for index, _ in enumerate(paths):
            with_intake.upload(uid, index, io.BytesIO(content))
        return with_intake.complete(uid)["order_id"]
    finally:
        with_intake.close()


def test_concurrent_start_single_job_without_changing_order(tmp_path):
    order = make_order(tmp_path)
    baseline = Registry(tmp_path / "registry.db").get(order)
    jobs = DocumentJobs(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: DocumentJobs(tmp_path).start(order, reader_version="1"), range(2)))
    assert results[0]["job_id"] == results[1]["job_id"]
    assert len(jobs.list(order)["jobs"]) == 1
    assert Registry(tmp_path / "registry.db").get(order) == baseline
    assert results[0]["composition"]["quantity"]["value"] is None
    assert results[0]["coverage"]["files_pending"] == 2


def test_chat_revision_preserves_snapshot_and_reader_change_creates_job(tmp_path):
    order = make_order(tmp_path)
    jobs = DocumentJobs(tmp_path)
    first = jobs.start(order, reader_version="1")
    Registry(tmp_path / "registry.db").mutate(order, lambda state: state.update(warnings=["note"]))
    assert jobs.start(order, reader_version="1")["job_id"] == first["job_id"]
    changed = jobs.start(order, reader_version="2")
    assert changed["snapshot_id"] == first["snapshot_id"]
    assert changed["job_id"] != first["job_id"]


def test_other_order_source_is_not_admitted(tmp_path):
    order = make_order(tmp_path)
    other = make_order(tmp_path)
    jobs = DocumentJobs(tmp_path)
    first = jobs.start(order, reader_version="1")
    with pytest.raises(OrderScopeDenied):
        jobs.start(other, reader_version="1", command="render", source_id=first["sources"][0]["source_id"])
