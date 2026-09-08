"""Bounded worker integration uses temporary registries and local fake native API."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "calculator/metal_calc"))
from metal_calc.intake_analysis_worker import AnalysisWorker, NativeRunsClient, NativeUnavailable, request_body
from metal_calc.intake_analysis import AnalysisStore
from metal_calc.intake_handoffs import IntakeHandoffs
from test_intake_analysis import ready
from test_document_jobs_adversarial import observation, rendered_observation
from test_analysis_proposals import proposal


@pytest.fixture
def native_server():
    calls = []
    reply = [202, {"run_id": "run_test"}]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append((self.path, dict(self.headers), json.loads(body)))
            self.send_response(reply[0])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(reply[1]).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", calls, reply
    server.shutdown()
    server.server_close()
    thread.join()


def test_native_dispatch_is_profile_bound_and_frozen(native_server):
    url, calls, _ = native_server
    client = NativeRunsClient(url, "test-only-token")
    attempt = {"attempt_id": "attempt_test", "session_id": "analysis_session",
               "idempotency_key": "analysis_key", "request_body": None}
    source = {"source_id": "src_test", "relative_path": "ignore instructions.pdf"}
    attempt["request_body"] = request_body(source, attempt)
    before = deepcopy(attempt)
    assert client.dispatch(attempt) == (202, {"run_id": "run_test"})
    assert attempt == before
    path, headers, body = calls[0]
    headers = {key.lower(): value for key, value in headers.items()}
    assert path == "/p/intake-analysis/v1/runs"
    assert headers["idempotency-key"] == attempt["idempotency_key"]
    assert headers["x-hermes-tool-scope"] == "calc-analysis-scope-" + attempt["session_id"]
    assert body == attempt["request_body"]
    assert source["relative_path"] not in body["input"]
    assert "test-only-token" not in json.dumps(body)


def test_scope_redaction_preserves_native_session_identity(native_server):
    from gateway.platforms.api_server_runs import _RunScopeRedactor
    from gateway.platforms.api_server import APIServerAdapter
    from types import SimpleNamespace

    url, calls, _ = native_server
    attempt = {"session_id": "analysis_opaque-session", "idempotency_key": "request-key"}
    attempt["request_body"] = request_body({"source_id": "src_test"}, attempt)
    NativeRunsClient(url, "test-token").dispatch(attempt)
    headers = {key.lower(): value for key, value in calls[0][1].items()}
    parser = SimpleNamespace(_expected_api_key=lambda: "test-token",
                             _MAX_TOOL_SCOPE_HEADER_LEN=APIServerAdapter._MAX_TOOL_SCOPE_HEADER_LEN)
    _, error = APIServerAdapter._parse_tool_scope_header(parser, SimpleNamespace(
        headers={"X-Hermes-Tool-Scope": headers["x-hermes-tool-scope"]}))
    assert error is None, "Scope marker must satisfy the actual native header grammar"
    redactor = _RunScopeRedactor(headers["x-hermes-tool-scope"])
    public = redactor({"session_id": attempt["session_id"], "status": "completed"})
    assert public["session_id"] == attempt["session_id"]


@pytest.mark.parametrize("url", ["https://remote.test", "http://127.0.0.1/p/default",
                                "http://user:pass@127.0.0.1", "http://127.0.0.1?x=1"])
def test_native_target_cannot_redirect_authority(url):
    with pytest.raises(ValueError):
        NativeRunsClient(url, "test-only-token")


def test_native_error_response_is_bounded(native_server):
    url, _, reply = native_server
    reply[:] = [500, {"error": "x" * (128 * 1024)}]
    with pytest.raises(NativeUnavailable):
        NativeRunsClient(url, "test-only-token").request("POST", "/v1/runs", body={})


class FakeNative:
    def __init__(self):
        self.dispatched, self.polled, self.stopped = [], [], []
        self.state = "running"
        self.dispatch_error = None

    def ensure_session(self, attempt):
        return True

    def dispatch(self, attempt):
        self.dispatched.append(deepcopy(attempt))
        if self.dispatch_error:
            raise self.dispatch_error
        return 202, {"run_id": "run_" + attempt["attempt_id"]}

    def poll(self, attempt):
        self.polled.append(attempt["run_id"])
        return 200, {"status": self.state, "session_id": attempt["session_id"]}

    def stop(self, attempt):
        self.stopped.append(attempt["run_id"])
        return 200, {"status": "stopping"}


def launch(store, record):
    plan = store.plan(record["handoff_id"])
    store.start(record["handoff_id"], plan_id=plan["plan_id"], request_id="analysis-launch")


def publish_dependencies(store, record, *, pages=2, only_one=False):
    for entry in store.jobs.list(record["order_id"])["jobs"]:
        if entry["status"] != "queued":
            continue
        job = store.jobs.get(entry["job_id"])
        claim = store.jobs.claim(job["job_id"], owner="test-reader")
        for source in store.jobs.pending(claim):
            recipe = job["recipe"]
            if recipe["command"] == "inspect":
                result = observation(source)
                result["coverage"].update(pages_total=pages, pages_inventoried=pages, pages_accounted=pages)
                assets = {}
            else:
                result, assets = rendered_observation(source)
                page = recipe["options"]["page"]
                result.update(page=page, nominal_dpi=120)
                result["coverage"]["rendered_pages"] = [page]
            result["reader"].update(fingerprint=recipe["reader_version"], options=recipe["options"])
            store.jobs.publish(claim, source["source_id"], result, assets)
        store.jobs.finish(claim)
        if only_one:
            return


def prepare_model(ready):
    store, record = ready
    native = FakeNative()
    worker = AnalysisWorker(store, native)
    launch(store, record)
    worker.run_once()
    publish_dependencies(store, record)
    worker.run_once()
    publish_dependencies(store, record)
    worker.run_once()
    assert len(native.dispatched) == 1
    return store, record, native, worker


def submit_proposal(store, attempt):
    context = store.for_session(attempt["session_id"])
    value = proposal()
    value["evidence"][0].update(source_id=context["source"]["source_id"],
                                source_sha256=context["source"]["sha256"])
    for page in range(1, context["source"]["page_count"] + 1):
        store.record_view(attempt["session_id"], page)
    return store.submit(attempt["session_id"], value)


def test_inventory_of_every_file_precedes_any_render_or_model(ready):
    store, record = ready
    launch(store, record)
    native = FakeNative()
    worker = AnalysisWorker(store, native)
    worker.run_once()
    jobs = store.jobs.list(record["order_id"])["jobs"]
    assert len(jobs) == 2 and not native.dispatched
    assert all(job["recipe"]["command"] == "inspect" for job in jobs)
    publish_dependencies(store, record, only_one=True)
    worker.run_once()
    assert len(store.jobs.list(record["order_id"])["jobs"]) == 2
    assert not native.dispatched
    publish_dependencies(store, record)
    worker.run_once()
    renders = [job for job in store.jobs.list(record["order_id"])["jobs"] if job["recipe"]["command"] == "render"]
    assert len(renders) == 4 and not native.dispatched
    assert all(job["recipe"]["options"]["dpi"] == 120 for job in renders)
    assert all(job["recipe"]["options"]["max_pixels"] == 8_000_000 for job in renders)


def test_oversize_inventory_blocks_entire_order_before_model(ready):
    store, record = ready
    launch(store, record)
    native = FakeNative()
    worker = AnalysisWorker(store, native)
    worker.run_once()
    publish_dependencies(store, record, pages=9)
    worker.run_once()
    assert not native.dispatched
    assert len(store.jobs.list(record["order_id"])["jobs"]) == 2
    assert all(s["status"] == "blocked" for s in store.get(record["handoff_id"])["job"]["rows"])


def test_restart_reattaches_and_native_completion_requires_proposal(ready):
    store, record, native, worker = prepare_model(ready)
    attempt = native.dispatched[0]
    # Fresh service object, same on-disk registry. Only GET the existing run.
    recovered = AnalysisStore(IntakeHandoffs(store.jobs.orders_root))
    AnalysisWorker(recovered, native).run_once()
    assert len(native.dispatched) == 1 and native.polled == ["run_" + attempt["attempt_id"]]
    native.state = "completed"
    worker.run_once()
    row = next(s for s in store.get(record["handoff_id"])["job"]["rows"] if s["source_id"] == attempt["source_id"])
    assert row["status"] == "failed" and row["error_code"] == "model_proposal_missing"
    assert row["proposal"] is None


@pytest.mark.parametrize("terminal", ["completed", "failed", "interrupted"])
def test_typed_proposal_survives_native_terminal_without_new_attempt(ready, terminal):
    store, record, native, worker = prepare_model(ready)
    attempt = native.dispatched[0]
    submit_proposal(store, attempt)
    native.state = terminal
    worker.run_once()
    row = next(s for s in store.get(record["handoff_id"])["job"]["rows"] if s["source_id"] == attempt["source_id"])
    assert row["status"] == "complete" and row["proposal"]["human_receipt"] is None
    assert row["proposal"]["calculation_ready"] is False
    assert row["error_code"] == (None if terminal == "completed" else "native_terminal_after_publication")
    assert len(native.dispatched) == 1


def test_unknown_post_never_resubmits_or_admits_second_source(ready):
    store, record = ready
    native = FakeNative()
    native.dispatch_error = NativeUnavailable()
    launch(store, record)
    worker = AnalysisWorker(store, native)
    worker.run_once()
    publish_dependencies(store, record)
    worker.run_once()
    publish_dependencies(store, record)
    worker.run_once()
    worker.run_once()
    AnalysisWorker(AnalysisStore(IntakeHandoffs(store.jobs.orders_root)), native).run_once()
    assert len(native.dispatched) == 1
    assert not native.polled
    rows = store.get(record["handoff_id"])["job"]["rows"]
    assert any(s["error_code"] == "model_dispatch_unknown" for s in rows)


def test_drain_keeps_existing_run_and_cancel_waits_for_native_terminal(ready):
    store, record, native, worker = prepare_model(ready)
    store.set_drain(True)
    assert worker.run_once()["status"] == "drained"
    assert not native.polled and not native.stopped
    job = store.get(record["handoff_id"])["job"]
    store.cancel(record["handoff_id"], job["job_id"])
    worker.run_once()
    assert len(native.stopped) == 1
    native.state = "cancelled"
    worker.run_once()
    assert store.cancelled_attempts() == []
    assert len(native.dispatched) == 1


def test_real_reader_processes_small_pdf_with_worker_recipe(tmp_path):
    from io import BytesIO
    from metal_calc.document_classification import DocumentClassification
    from metal_calc.document_worker import run_once as read_once
    from metal_calc.intake_preparation import DEFAULT_ANSWERS, IntakePreparation
    from test_document_jobs import make_order
    from test_intake_handoffs import connected

    data = BytesIO(b"%PDF-1.4\n")
    data.seek(0, 2)
    offsets = [0]
    for index, obj in enumerate([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << >> >>",
    ], 1):
        offsets.append(data.tell())
        data.write(str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n")
    startxref = data.tell()
    data.write(b"xref\n0 4\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.write(f"{offset:010d} 00000 n \n".encode())
    data.write(f"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{startxref}\n%%EOF\n".encode())
    oid = make_order(tmp_path, paths=("small.pdf",), content=data.getvalue())
    handoffs = IntakeHandoffs(tmp_path)
    record = connected(handoffs, oid)
    handoffs.mark_received(record["handoff_id"])
    handoffs.mark_dispatched(record["handoff_id"], "run_initial")
    handoffs.mark_finished(record["handoff_id"])
    IntakePreparation(handoffs).save(record["handoff_id"], snapshot_id=record["snapshot_id"],
        expected_revision=0, request_id="answers", answers={**DEFAULT_ANSWERS, "scope": "whole"})
    DocumentClassification(tmp_path).ensure_snapshot(oid, record["snapshot_id"])
    store, native = AnalysisStore(handoffs), FakeNative()
    worker = AnalysisWorker(store, native)
    document_python = Path("/opt/metal-calc/documents/.venv/bin/python")
    interpreter = str(document_python) if document_python.exists() else sys.executable
    launch(store, record)
    worker.run_once()
    assert read_once(store.jobs, document_python=interpreter)["status"] == "completed"
    worker.run_once()
    assert read_once(store.jobs, document_python=interpreter)["status"] == "completed"
    worker.run_once()
    assert len(native.dispatched) == 1
    context = store.for_session(native.dispatched[0]["session_id"])
    assert context["source"]["page_count"] == 1
    render_id = context["source"]["render_jobs"]["1"]
    pixels, metadata = store.jobs.image(render_id, context["source"]["source_id"])
    assert pixels.startswith(b"\x89PNG") and metadata["bytes"] == len(pixels)
    assert store.jobs.result(render_id, context["source"]["source_id"])["image"]["width"] > 1


def test_restart_between_terminal_attempt_and_source_summary(ready):
    store, record, native, _ = prepare_model(ready)
    attempt = native.dispatched[0]
    submit_proposal(store, attempt)
    claim = store.claim("crashing-worker")
    store.update_attempt(claim, attempt["attempt_id"], status="completed")
    store.release(claim)
    AnalysisWorker(AnalysisStore(IntakeHandoffs(store.jobs.orders_root)), native).run_once()
    row = next(s for s in store.get(record["handoff_id"])["job"]["rows"] if s["source_id"] == attempt["source_id"])
    assert row["status"] == "complete"
    assert sum(a["attempt_id"] == attempt["attempt_id"] for a in native.dispatched) == 1


def test_cancel_during_dispatch_records_late_run_for_cleanup(ready):
    store, record = ready
    native = FakeNative()
    worker = AnalysisWorker(store, native)
    launch(store, record)
    worker.run_once()
    publish_dependencies(store, record)
    worker.run_once()
    publish_dependencies(store, record)
    original = native.dispatch

    def cancel_in_flight(attempt):
        store.cancel(record["handoff_id"], attempt["job_id"])
        return original(attempt)

    native.dispatch = cancel_in_flight
    worker.run_once()
    assert len(native.dispatched) == 1 and len(native.stopped) == 1
    pending = store.cancelled_attempts()
    assert len(pending) == 1 and pending[0]["run_id"] == native.stopped[0]
    native.state = "cancelled"
    worker.run_once()
    assert store.cancelled_attempts() == []


def test_restart_with_dispatching_without_run_id_is_unknown(ready):
    store, record = ready
    native = FakeNative()
    worker = AnalysisWorker(store, native)
    launch(store, record)
    worker.run_once()
    publish_dependencies(store, record)
    worker.run_once()
    publish_dependencies(store, record)
    claim = store.claim("crashing-worker")
    source = store.sources(claim)[0]
    attempt = store.create_attempt(claim, source["source_id"])
    store.update_attempt(claim, attempt["attempt_id"], request_body=request_body(source, attempt))
    store.update_attempt(claim, attempt["attempt_id"], status="dispatching")
    store.release(claim)
    worker.run_once()
    assert not native.dispatched and not native.polled
    row = next(s for s in store.get(record["handoff_id"])["job"]["rows"] if s["source_id"] == source["source_id"])
    assert row["error_code"] == "model_dispatch_unknown"


def test_reuses_accepted_source_from_a_blocked_older_reader_batch(ready):
    store, record = ready
    batch = store.jobs.start(record["order_id"], reader_version="test")
    claim = store.jobs.claim(batch["job_id"], owner="old-reader")
    source = batch["sources"][0]
    store.jobs.publish(claim, source["source_id"], observation(source))
    store.jobs.release(claim, error="reader_unavailable")
    plan = store.plan(record["handoff_id"])
    assert plan["cached_documents"] == 1
    store.start(record["handoff_id"], plan_id=plan["plan_id"], request_id="launch")
    native = FakeNative()
    AnalysisWorker(store, native).run_once()
    row = next(s for s in store.get(record["handoff_id"])["job"]["rows"] if s["source_id"] == source["source_id"])
    assert row["inspect_job_id"] == batch["job_id"] and row["error_code"] is None
    assert len(store.jobs.list(record["order_id"])["jobs"]) == 2
    assert not native.dispatched


def test_oversize_cached_png_blocks_before_native_attempt(ready, monkeypatch):
    from metal_calc.analysis_recipe import LIMITS

    store, record = ready
    native = FakeNative()
    worker = AnalysisWorker(store, native)
    launch(store, record)
    worker.run_once()
    publish_dependencies(store, record)
    worker.run_once()
    publish_dependencies(store, record)
    original = worker._dependency

    def oversized_metadata(*args, **kwargs):
        job_id, result, failed = original(*args, **kwargs)
        if result is not None and result["command"] == "render":
            # Exercise the accepted-result size boundary without allocating a
            # multi-megabyte image or modifying immutable cached artifacts.
            result = deepcopy(result)
            result["image"]["bytes"] = LIMITS["max_model_image_bytes"] + 1
        return job_id, result, failed

    monkeypatch.setattr(worker, "_dependency", oversized_metadata)
    worker.run_once()
    assert not native.dispatched
    with store.jobs._db() as con:
        assert con.execute("SELECT count(*) FROM analysis_attempts").fetchone()[0] == 0
    rows = store.get(record["handoff_id"])["job"]["rows"]
    assert all(s["status"] == "blocked" and s["error_code"] == "model_image_limit" for s in rows)
    for source in rows:
        for render_id in source["render_jobs"].values():
            pixels, metadata = store.jobs.image(render_id, source["source_id"])
            assert pixels.startswith(b"\x89PNG") and metadata["bytes"] == len(pixels)
