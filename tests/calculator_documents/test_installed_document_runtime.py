"""Candidate-image smoke; run explicitly inside the built calculator image."""
import io
from pathlib import Path
from uuid import uuid4

import pytest


def test_installed_worker_uses_installed_document_reader(tmp_path):
    # This optional artifact test has no meaning in a source-only checkout.
    if not Path("/opt/metal-calc/documents/.venv/bin/python").is_file():
        pytest.skip("Run inside the candidate calculator image")
    from pypdf import PdfWriter
    import metal_calc.document_jobs as domain
    from metal_calc.document_runtime import load_reader
    from metal_calc.document_worker import run_once
    from metal_calc.folder_intake import FolderIntake
    assert str(Path(domain.__file__).resolve()).startswith("/opt/metal-calc/.venv/")
    reader = load_reader()
    assert str(reader.CLI) == "/opt/metal-calc/documents/cli.py"
    writer = PdfWriter()
    writer.add_blank_page(width=240, height=120)
    buf = io.BytesIO()
    writer.write(buf)
    data = buf.getvalue()
    orders = tmp_path / "orders"
    orders.mkdir()
    intake = FolderIntake(orders)
    try:
        uid = str(uuid4())
        intake.create({"upload_id": uid, "folder_name": "candidate-installed",
                       "files": [{"path": "sample.pdf", "size": len(data)}]})
        intake.upload(uid, 0, io.BytesIO(data))
        order = intake.complete(uid)["order_id"]
    finally:
        intake.close()
    jobs = domain.DocumentJobs(orders)
    job = jobs.start(order, reader_version=reader.reader_fingerprint(), options=reader.canonical_options())
    result = run_once(jobs, document_python="/opt/metal-calc/documents/.venv/bin/python")
    assert result["status"] == "completed", result
    evidence = jobs.result(job["job_id"], job["sources"][0]["source_id"])
    assert evidence["coverage"]["pages_inventoried"] == 1
    assert evidence["use_for_calculation"] is False
