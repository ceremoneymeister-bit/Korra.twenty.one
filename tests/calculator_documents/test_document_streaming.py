"""Framed document downloads: HTTP → real CLI → verified bytes, without pypdf.

The positive chain is real (router, admin CLI subprocess, DocumentJobs, SQLite,
folder receipts). Protocol faults use a scripted stand-in for the CLI binary so
that truncation, oversize, timeouts and disconnects are deterministic.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote
from uuid import uuid4

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "calculator/metal_calc"))
from metal_calc.document_jobs import DocumentJobs  # noqa: E402
from metal_calc.folder_intake import FolderIntake  # noqa: E402
from metal_calc.registry import Registry  # noqa: E402
from korra_cli.web_routers import calc_documents, calc_files, calc_rates  # noqa: E402

# Deliberately starts with a typed-error JSON line and a PNG magic: the panel
# must serve these octets verbatim rather than sniff them.
DATA = (b'{"error": {"code": "NotFound", "message": "decoy"}}\n' + b"\x89PNG\r\n\x1a\n"
        + bytes(range(256)) * 300)

FAKE_ADMIN = r'''#!{python}
import json, os, sys, time
out = sys.stdout.buffer
pid_file = os.environ.get("STREAM_PID_FILE")
if pid_file:
    with open(pid_file, "w") as fh:
        fh.write(str(os.getpid()))
if sys.argv[1] == "document-job-retry":
    with open(os.environ["STREAM_ARGV_FILE"], "w") as fh:
        json.dump(sys.argv[1:], fh)
    out.write(b"{{}}\n"); out.flush(); sys.exit(0)
fault = os.environ["STREAM_FAULT"]
def header(**over):
    base = {{"protocol": "metal-calc-document-stream", "version": 1, "kind": "source",
            "name": "x.bin", "bytes": 5, "sha256": "{sha5}"}}
    base.update(over)
    return json.dumps(base).encode() + b"\n"
if fault == "garbage":
    out.write(b"not json at all\n")
elif fault == "oversized_line":
    out.write(b"{{" + b"a" * 70000); out.flush(); time.sleep(30)
elif fault == "oversized_json":
    out.write(header(name="n" * 20000))
elif fault == "bad_bytes_type":
    out.write(header(bytes="5"))
elif fault == "bool_bytes":
    out.write(header(bytes=True))
elif fault == "negative_bytes":
    out.write(header(bytes=-1))
elif fault == "huge_bytes":
    out.write(header(bytes=101 * 1024 ** 2))
elif fault == "bad_sha":
    out.write(header(sha256="xyz"))
elif fault == "bad_name":
    out.write(header(name="../x.bin"))
elif fault == "kind_mismatch":
    out.write(header(kind="image"))
elif fault == "wrong_version":
    out.write(header(version=2))
elif fault == "not_object":
    out.write(b"[1, 2]\n")
elif fault == "silent_exit":
    sys.exit(1)
elif fault == "slow_header":
    time.sleep(30)
elif fault == "error_exit2":
    out.write(b'{{"error": {{"code": "Conflict", "message": "typed conflict"}}}}\n'); out.flush(); sys.exit(2)
elif fault == "error_exit0":
    out.write(b'{{"error": {{"code": "NotFound", "message": "typed missing"}}}}\n')
elif fault == "truncated":
    out.write(header() + b"abc")
elif fault == "trailing":
    out.write(header() + b"hello!")
elif fault == "exit_failure":
    out.write(header() + b"hello"); out.flush(); sys.exit(3)
elif fault == "sha_mismatch":
    out.write(header(sha256="0" * 64) + b"hello")
elif fault == "slow_body":
    out.write(header(bytes=1000000, sha256="0" * 64) + b"x" * 1000); out.flush(); time.sleep(30)
elif fault == "ok":
    out.write(header() + b"hello")
out.flush()
'''


@pytest.fixture
def cabinet(tmp_path, monkeypatch):
    root = tmp_path / "orders"
    root.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "agent-home"))
    monkeypatch.delenv("METAL_CALC_ADMIN_BIN", raising=False)
    binary = tmp_path / "metal-calc-admin"
    binary.write_text(f"#!{sys.executable}\nimport sys\nsys.path.insert(0,{str(ROOT / 'calculator/metal_calc')!r})\n"
                      "from metal_calc.admin import main\nmain()\n")
    binary.chmod(0o700)
    config = {"mcp_servers": {"metal_calc": {"command": str(tmp_path / "metal-calc-mcp"),
              "env": {"METAL_CALC_ORDERS_ROOT": str(root), "METAL_CALC_ROLE": "front"}}}}
    monkeypatch.setattr(calc_rates, "load_config", lambda: config)
    app = FastAPI()
    app.include_router(calc_files.router)
    app.include_router(calc_documents.router)
    with TestClient(app) as client:
        yield client, root, config


def make_order(root, name, files=(("nested/report.bin", DATA), ("other.bin", b"other"))):
    intake = FolderIntake(root)
    try:
        uid = str(uuid4())
        intake.create({"upload_id": uid, "folder_name": name,
                       "files": [{"path": p, "size": len(b)} for p, b in files]})
        for index, (_, data) in enumerate(files):
            intake.upload(uid, index, io.BytesIO(data))
        return intake.complete(uid)["order_id"], uid
    finally:
        intake.close()


def start_job(root, order):
    return DocumentJobs(root).start(order, reader_version="stream-qa")


@pytest.fixture
def fake_admin(tmp_path, monkeypatch):
    sha5 = hashlib.sha256(b"hello").hexdigest()
    script = tmp_path / "fake-admin"
    script.write_text(FAKE_ADMIN.format(python=sys.executable, sha5=sha5))
    script.chmod(0o700)
    pid_file = tmp_path / "child.pid"
    argv_file = tmp_path / "argv.json"
    monkeypatch.setenv("METAL_CALC_ADMIN_BIN", str(script))
    monkeypatch.setenv("STREAM_PID_FILE", str(pid_file))
    monkeypatch.setenv("STREAM_ARGV_FILE", str(argv_file))

    def child_reaped():
        pid = int(pid_file.read_text())
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    return pid_file, argv_file, child_reaped


def test_download_serves_verified_bytes_only_after_validated_header(cabinet):
    client, root, _ = cabinet
    order, _ = make_order(root, "34219 stream")
    job = start_job(root, order)
    baseline = Registry(root / "registry.db").get(order)
    jid = job["job_id"]
    status = client.get(f"/api/calc/document-jobs/{jid}").json()
    source = status["sources"][0]
    sid = source["source_id"]
    assert source["download_url"] == f"/api/calc/document-jobs/{jid}/sources/{quote(sid, safe='')}/download"
    assert source["result_url"] == f"/api/calc/document-jobs/{jid}/sources/{quote(sid, safe='')}"
    response = client.get(source["download_url"])
    assert response.status_code == 200
    assert response.content == DATA
    assert hashlib.sha256(response.content).hexdigest() == source["sha256"]
    assert response.headers["content-length"] == str(len(DATA)) == str(source["bytes"])
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''report.bin"
    assert client.get(status["sources"][1]["download_url"]).content == b"other"
    assert Registry(root / "registry.db").get(order) == baseline


def test_cli_stream_frame_matches_legacy_operator_commands(cabinet, tmp_path):
    _, root, config = cabinet
    order, _ = make_order(root, "34219 cli")
    job = start_job(root, order)
    sid = job["sources"][0]["source_id"]
    env = {**os.environ, **config["mcp_servers"]["metal_calc"]["env"]}
    binary = str(tmp_path / "metal-calc-admin")

    def run(command):
        return subprocess.run([binary, command, "--job-id", job["job_id"], "--source-id", sid],
                              capture_output=True, timeout=60, env=env, check=False)

    stream = run("document-source-stream")
    assert stream.returncode == 0, stream.stderr
    line, _, body = stream.stdout.partition(b"\n")
    header = json.loads(line)
    assert header["protocol"] == "metal-calc-document-stream" and header["version"] == 1
    assert header["kind"] == "source" and header["name"] == "report.bin"
    assert header["bytes"] == len(DATA) and header["sha256"] == hashlib.sha256(DATA).hexdigest()
    assert body == DATA
    info = json.loads(run("document-source-info").stdout)
    assert (info["name"], info["bytes"], info["sha256"]) == (header["name"], header["bytes"], header["sha256"])
    assert run("document-source-read").stdout == DATA
    image = run("document-image-stream")
    assert image.returncode == 2
    assert json.loads(image.stdout)["error"]["code"] == "NotFound"


def test_kernel_errors_before_first_frame_become_statuses_not_bytes(cabinet):
    client, root, _ = cabinet
    order, uid = make_order(root, "34219 errors")
    other, _ = make_order(root, "34219 other", files=(("foreign.bin", b"foreign"),))
    job = start_job(root, order)
    foreign = start_job(root, other)["sources"][0]["source_id"]
    jid = job["job_id"]
    sid = job["sources"][0]["source_id"]
    base = f"/api/calc/document-jobs/{jid}/sources/"
    missing = client.get(f"/api/calc/document-jobs/job_missing/sources/{sid}/download")
    assert missing.status_code == 404 and "detail" in missing.json()
    assert client.get(base + f"{foreign}/download").status_code == 403
    assert client.get(base + f"{sid}/image").status_code == 404
    # A source file changed under the receipt: the kernel's SHA/size guard
    # must surface as 409 instead of 200 with mismatching bytes.
    (root / "folders" / uid / "files" / "0").write_bytes(b"\x00" * len(DATA))
    tampered = client.get(base + f"{sid}/download")
    assert tampered.status_code == 409
    assert tampered.content != DATA and "detail" in tampered.json()


@pytest.mark.parametrize("fault,status", [
    ("garbage", 503), ("oversized_line", 503), ("oversized_json", 503), ("bad_bytes_type", 503),
    ("bool_bytes", 503), ("negative_bytes", 503), ("huge_bytes", 503), ("bad_sha", 503),
    ("bad_name", 503), ("kind_mismatch", 503), ("wrong_version", 503), ("not_object", 503),
    ("silent_exit", 503), ("error_exit2", 409), ("error_exit0", 404),
])
def test_invalid_or_error_headers_never_stream_and_child_is_reaped(cabinet, fake_admin, monkeypatch, fault, status):
    client, _, _ = cabinet
    _, _, child_reaped = fake_admin
    monkeypatch.setenv("STREAM_FAULT", fault)
    response = client.get("/api/calc/document-jobs/job_x/sources/src_y/download")
    assert response.status_code == status, response.content
    assert "detail" in response.json()
    assert b"hello" not in response.content
    assert child_reaped()


def test_header_timeout_returns_504_and_kills_child(cabinet, fake_admin, monkeypatch):
    client, _, _ = cabinet
    _, _, child_reaped = fake_admin
    monkeypatch.setenv("STREAM_FAULT", "slow_header")
    monkeypatch.setattr(calc_documents, "HEADER_TIMEOUT_SECONDS", 0.5)
    response = client.get("/api/calc/document-jobs/job_x/sources/src_y/download")
    assert response.status_code == 504
    assert child_reaped()


def test_fake_protocol_happy_path_streams_exact_bytes(cabinet, fake_admin, monkeypatch):
    client, _, _ = cabinet
    _, _, child_reaped = fake_admin
    monkeypatch.setenv("STREAM_FAULT", "ok")
    response = client.get("/api/calc/document-jobs/job_x/sources/src_y/download")
    assert response.status_code == 200 and response.content == b"hello"
    assert response.headers["content-disposition"].endswith("x.bin")
    assert child_reaped()


@pytest.mark.parametrize("fault", ["truncated", "trailing", "exit_failure", "sha_mismatch"])
def test_body_faults_abort_response_instead_of_completing(cabinet, fake_admin, monkeypatch, fault):
    client, _, _ = cabinet
    _, _, child_reaped = fake_admin
    monkeypatch.setenv("STREAM_FAULT", fault)
    with pytest.raises(RuntimeError):
        client.get("/api/calc/document-jobs/job_x/sources/src_y/download")
    assert child_reaped()


def test_client_disconnect_mid_body_reaps_child(cabinet, fake_admin, monkeypatch):
    _, _, child_reaped = fake_admin
    monkeypatch.setenv("STREAM_FAULT", "slow_body")

    async def scenario():
        response = await calc_documents._download("job_x", "src_y", "source")
        assert response.headers["content-length"] == "1000000"
        iterator = response.body_iterator
        first = await anext(iterator)
        assert first == b"x" * 1000
        await iterator.aclose()

    asyncio.run(asyncio.wait_for(scenario(), 20))
    assert child_reaped()


@pytest.mark.parametrize("fail_on", ["http.response.start", "http.response.body"])
def test_asgi_send_failure_reaps_child(cabinet, fake_admin, monkeypatch, fail_on):
    _, _, child_reaped = fake_admin
    monkeypatch.setenv("STREAM_FAULT", "slow_body")

    async def scenario():
        response = await calc_documents._download("job_x", "src_y", "source")

        async def send(message):
            if message["type"] == fail_on:
                raise OSError("synthetic client disconnect")

        async def receive():
            await asyncio.Future()

        with pytest.raises(Exception):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    asyncio.run(asyncio.wait_for(scenario(), 20))
    assert child_reaped()


def test_asgi_legacy_disconnect_before_body_reaps_child(cabinet, fake_admin, monkeypatch):
    _, _, child_reaped = fake_admin
    monkeypatch.setenv("STREAM_FAULT", "slow_body")

    async def scenario():
        response = await calc_documents._download("job_x", "src_y", "source")
        response_start = asyncio.Event()

        async def send(message):
            assert message["type"] == "http.response.start"
            response_start.set()
            await asyncio.Future()

        async def receive():
            await response_start.wait()
            return {"type": "http.disconnect"}

        await response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)

    asyncio.run(asyncio.wait_for(scenario(), 20))
    assert child_reaped()


def test_reap_discards_paused_stdout_without_buffering_whole_payload():
    async def scenario():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            "import sys,time; sys.stdout.buffer.write(b'x'*1000000); "
            "sys.stdout.buffer.flush(); time.sleep(30)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        process.stdin.close()
        stderr_task = asyncio.create_task(calc_documents._drain_stderr(process.stderr))

        async def wait_for_backpressure():
            while not process.stdout._paused:
                await asyncio.sleep(0.01)

        try:
            await asyncio.wait_for(wait_for_backpressure(), 5)
            await asyncio.wait_for(calc_documents._reap(process, stderr_task), 2)
        finally:
            if process.returncode is None:
                process.kill()
            await process.stdout.read()
            await process.wait()
            await asyncio.gather(stderr_task, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 10))


@pytest.mark.parametrize("body,expected", [
    (None, None), (b"", None), (b"{}", None), (b'{"source_id": null}', None),
    (b'{"source_id": "src_abc"}', "src_abc"),
])
def test_retry_body_maps_to_optional_source_id(cabinet, fake_admin, body, expected):
    client, _, _ = cabinet
    _, argv_file, _ = fake_admin
    kwargs = {"content": body} if body is not None else {}
    response = client.post("/api/calc/document-jobs/job_x/retry", **kwargs)
    assert response.status_code == 200, response.content
    argv = json.loads(argv_file.read_text())
    assert argv[:3] == ["document-job-retry", "--job-id", "job_x"]
    assert argv[3:] == (["--source-id", expected] if expected else [])


@pytest.mark.parametrize("body,status", [
    (b'{"nope": 1}', 422), (b'{"source_id": 7}', 422), (b'{"source_id": ""}', 422),
    (b"[1]", 422), (b"{", 422), (b'{"source_id": "' + b"a" * 300 + b'"}', 422),
    (b'{"source_id": "' + b"a" * 5000 + b'"}', 413),
])
def test_retry_rejects_unexpected_bodies_before_spawning_cli(cabinet, fake_admin, body, status):
    client, _, config = cabinet
    _, argv_file, _ = fake_admin
    assert client.post("/api/calc/document-jobs/job_x/retry", content=body).status_code == status
    assert not argv_file.exists()
    config["mcp_servers"]["metal_calc"]["env"]["METAL_CALC_ROLE"] = "tech"
    assert client.post("/api/calc/document-jobs/job_x/retry").status_code == 403
    assert not argv_file.exists()


def test_real_kernel_retry_compatibility_and_foreign_source_guard(cabinet):
    client, root, _ = cabinet
    order, _ = make_order(root, "34219 retry")
    other, _ = make_order(root, "34219 retry-other", files=(("foreign.bin", b"foreign"),))
    job = start_job(root, order)
    foreign = start_job(root, other)["sources"][0]["source_id"]
    jid = job["job_id"]
    before = client.get(f"/api/calc/document-jobs/{jid}").json()
    assert client.post(f"/api/calc/document-jobs/{jid}/retry").status_code == 200
    if "source_id" not in inspect.signature(DocumentJobs.retry).parameters:
        pytest.skip("queue peer's DocumentJobs.retry(source_id=...) not landed yet")
    denied = client.post(f"/api/calc/document-jobs/{jid}/retry", json={"source_id": foreign})
    assert denied.status_code in {403, 404, 409, 422}, denied.content
    after = client.get(f"/api/calc/document-jobs/{jid}").json()
    assert [s["status"] for s in after["sources"]] == [s["status"] for s in before["sources"]]
