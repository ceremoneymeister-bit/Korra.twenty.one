"""Kill a real parser worker process; recover from its durable checkpoints."""
import hashlib
import io
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

from pypdf import PdfWriter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "calculator/metal_calc"))
from metal_calc.document_jobs import DocumentJobs
from metal_calc.document_runtime import load_reader
from metal_calc.folder_intake import FolderIntake


def test_sigkill_restart_keeps_completed_sources_and_finishes_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "agent-home"))
    root = tmp_path / "orders"
    root.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=240, height=120)
    buf = io.BytesIO()
    writer.write(buf)
    data = buf.getvalue()
    uid = str(uuid4())
    intake = FolderIntake(root)
    try:
        intake.create({"upload_id": uid, "folder_name": "worker-crash",
                       "files": [{"path": f"{i}.pdf", "size": len(data)} for i in range(20)]})
        for i in range(20):
            intake.upload(uid, i, io.BytesIO(data))
        order = intake.complete(uid)["order_id"]
    finally:
        intake.close()
    reader = load_reader()
    jobs = DocumentJobs(root)
    recipe = {"reader_version": reader.reader_fingerprint(), "options": reader.canonical_options()}
    job = jobs.start(order, **recipe)
    cmd = [sys.executable, "-m", "metal_calc.document_worker", "--orders-root", str(root),
           "--document-python", sys.executable, "--lease-seconds", "10", "--once"]
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "LANG": "C.UTF-8",
           "PYTHONPATH": str(ROOT / "calculator/metal_calc")}
    process = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    accepted = {}
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            status = jobs.get(job["job_id"])
            if status["coverage"]["files_accounted"]:
                accepted = {s["source_id"]: s["result_sha256"] for s in status["sources"] if s["result_sha256"]}
                break
            assert process.poll() is None, process.communicate(timeout=2)
            time.sleep(0.02)
        assert accepted and len(accepted) < 20
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)
        assert process.returncode == -signal.SIGKILL
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
    restarted = DocumentJobs(root)
    assert restarted.start(order, **recipe)["job_id"] == job["job_id"]
    with sqlite3.connect(root / "registry.db") as con:
        lease = con.execute("SELECT lease_until FROM document_jobs WHERE job_id=?", (job["job_id"],)).fetchone()[0]
    assert restarted.claim(job["job_id"]) is None
    time.sleep(max(0, lease - time.time()) + 0.05)
    resumed = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, timeout=45)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    status = restarted.get(job["job_id"])
    assert status["status"] == "completed", resumed.stdout
    assert status["coverage"]["files_accounted"] == 20
    for source in status["sources"]:
        assert source["sha256"] == hashlib.sha256(data).hexdigest()
        if source["source_id"] in accepted:
            assert source["result_sha256"] == accepted[source["source_id"]]
    with sqlite3.connect(root / "registry.db") as con:
        outcomes = [r[0] for r in con.execute("SELECT outcome FROM document_attempts ORDER BY fence")]
        assert outcomes == ["interrupted", "completed"]
        assert con.execute("SELECT count(*) FROM document_results").fetchone()[0] == 20
