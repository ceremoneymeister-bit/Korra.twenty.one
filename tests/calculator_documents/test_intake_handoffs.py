"""First handoff acceptance against synthetic receipts and temporary SQLite."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "calculator/metal_calc"))
from metal_calc.document_jobs import DocumentJobs
from metal_calc.errors import Conflict, InvalidState, OrderScopeDenied
from metal_calc.folder_intake import FolderIntake
from metal_calc.intake_handoffs import IntakeHandoffs
from metal_calc.intake_mcp import build_mcp
from metal_calc.registry import Registry
from test_document_jobs_adversarial import observation


def order(root, name=None):
    root.mkdir(exist_ok=True)
    intake = FolderIntake(root)
    uid = str(uuid4())
    try:
        intake.create({"upload_id": uid, "folder_name": name or uid,
                       "files": [{"path": "sub/a.pdf", "size": 4}, {"path": "b.pdf", "size": 4}]})
        for index in range(2):
            intake.upload(uid, index, io.BytesIO(b"fake"))
        return intake.complete(uid)["order_id"]
    finally:
        intake.close()


def connected(store, order_id):
    record = store.prepare(order_id)
    store.claim(record["handoff_id"])
    return store.mark_session_created(record["handoff_id"])


def test_prepare_claim_are_single_durable_admission_without_job(tmp_path):
    oid = order(tmp_path)
    baseline = Registry(tmp_path / "registry.db").get(oid)
    with ThreadPoolExecutor(max_workers=6) as pool:
        records = list(pool.map(lambda _: IntakeHandoffs(tmp_path).prepare(oid), range(6)))
    assert len({r["handoff_id"] for r in records}) == 1
    store = IntakeHandoffs(tmp_path)
    hid = records[0]["handoff_id"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(lambda _: IntakeHandoffs(tmp_path).claim(hid), range(6)))
    assert sum(record["claimed"] for record in claims) == 1
    assert store.jobs.list(oid)["jobs"] == []
    assert Registry(tmp_path / "registry.db").get(oid) == baseline
    store.mark_error(hid, "dispatch_unknown")
    assert not IntakeHandoffs(tmp_path).claim(hid)["claimed"]
    assert store.get(hid)["status"] == "needs_attention"


@pytest.mark.parametrize("code", ["session_create_failed", "runtime_unavailable"])
def test_only_confirmed_pre_submission_failure_can_retry(tmp_path, code):
    store = IntakeHandoffs(tmp_path)
    record = store.prepare(order(tmp_path))
    hid = record["handoff_id"]
    assert store.claim(hid)["claimed"]
    store.mark_error(hid, code)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: IntakeHandoffs(tmp_path).claim(hid), range(4)))
    assert sum(item["claimed"] for item in claims) == 1
    assert store.get(hid)["error_code"] is None
    assert store.get(hid)["session_id"] == record["session_id"]
    store.mark_session_created(hid)
    store.mark_dispatched(hid, "run_test")
    store.mark_error(hid, code)
    assert store.claim(hid)["claimed"] is False


def test_receipt_does_not_finish_run_or_approve_composition(tmp_path):
    store = IntakeHandoffs(tmp_path)
    record = connected(store, order(tmp_path))
    hid = record["handoff_id"]
    result = store.context(record["session_id"])
    assert result["receipt"] == "context_delivered"
    assert result["human_approved"] is False
    assert result["use_for_calculation"] is False
    store.mark_dispatched(hid, "run_test")
    assert store.get(hid)["initial_run_active"] is True
    finished = store.mark_finished(hid)
    assert finished["status"] == "received"
    assert finished["initial_run_active"] is False
    assert finished["error_code"] is None
    assert store.mark_error(hid, "run_unavailable")["status"] == "received"


def test_finished_run_without_context_is_not_received(tmp_path):
    store = IntakeHandoffs(tmp_path)
    record = connected(store, order(tmp_path))
    hid = record["handoff_id"]
    store.mark_dispatched(hid, "run_test")
    final = store.mark_finished(hid)
    assert final["status"] == "needs_attention"
    assert final["error_code"] == "acknowledgment_missing"
    assert final["received_at"] is None


def test_terminal_run_failure_survives_late_poll(tmp_path):
    store = IntakeHandoffs(tmp_path)
    record = connected(store, order(tmp_path))
    hid = record["handoff_id"]
    store.mark_dispatched(hid, "run_test")
    store.mark_error(hid, "run_failed")
    assert store.mark_error(hid, "run_unavailable")["error_code"] == "run_failed"
    assert store.mark_finished(hid)["error_code"] == "run_failed"


def test_metadata_revision_and_source_change(tmp_path):
    oid = order(tmp_path)
    store = IntakeHandoffs(tmp_path)
    record = connected(store, oid)
    registry = Registry(tmp_path / "registry.db")
    registry.mutate(oid, lambda state: state.update(warnings=["note"]))
    assert store.prepare(oid)["handoff_id"] == record["handoff_id"]
    registry.mutate(oid, lambda state: state["folder_intake"]["files"][0].update(sha256="f" * 64))
    assert store.get(record["handoff_id"])["status"] == "stale"
    with pytest.raises(OrderScopeDenied):
        store.context(record["session_id"])
    with pytest.raises(Conflict):
        store.claim(record["handoff_id"])
    newer = store.prepare(oid)
    assert newer["handoff_id"] != record["handoff_id"]
    assert newer["document_set_revision"] == 2
    assert store.jobs.list(oid)["jobs"] == []


def test_restoring_old_manifest_does_not_revive_historical_handoff(tmp_path):
    oid = order(tmp_path)
    store = IntakeHandoffs(tmp_path)
    registry = Registry(tmp_path / "registry.db")
    original = connected(store, oid)
    original_sha = registry.get(oid)[1]["folder_intake"]["files"][0]["sha256"]
    registry.mutate(oid, lambda state: state["folder_intake"]["files"][0].update(sha256="f" * 64))
    second = store.prepare(oid)
    registry.mutate(oid, lambda state: state["folder_intake"]["files"][0].update(sha256=original_sha))
    # Even before a third prepare, the rev2 snapshot prevents rev1 revival.
    assert store.get(original["handoff_id"])["status"] == "stale"
    restored = store.prepare(oid)
    assert restored["document_set_revision"] == 3
    assert restored["manifest_digest"] == original["manifest_digest"]
    assert restored["handoff_id"] not in {original["handoff_id"], second["handoff_id"]}
    assert store.get(original["handoff_id"])["status"] == "stale"
    with pytest.raises(OrderScopeDenied):
        store.context(original["session_id"])
    with pytest.raises(OrderScopeDenied):
        store.sources(original["session_id"])
    with pytest.raises(Conflict):
        store.claim(original["handoff_id"])


def test_cached_bounded_reads_and_cross_order_denial(tmp_path, monkeypatch):
    oid, other = order(tmp_path), order(tmp_path)
    jobs = DocumentJobs(tmp_path)
    job = jobs.start(oid, reader_version="test")
    claim = jobs.claim(job["job_id"])
    source = job["sources"][0]
    jobs.publish(claim, source["source_id"], observation(source))
    store = IntakeHandoffs(tmp_path)
    record = connected(store, oid)
    second = connected(store, other)
    def forbidden(*args, **kwargs):
        raise AssertionError("reader/source/job execution forbidden")
    monkeypatch.setattr(DocumentJobs, "start", forbidden)
    monkeypatch.setattr(DocumentJobs, "read_source", forbidden)
    monkeypatch.setattr(DocumentJobs, "image", forbidden)
    assert store.context(record["session_id"])["cached_observations"] == 1
    page = store.sources(record["session_id"], offset=1, limit=1)
    assert page["total"] == 2 and len(page["sources"]) == 1
    assert page["sources"][0]["observation_cached"] is False
    excerpt = store.observation(record["session_id"], source["source_id"], limit=17)
    assert len(excerpt["excerpt"]) == 17 and excerpt["has_more"]
    assert store.observation(record["session_id"], page["sources"][0]["source_id"])["status"] == "not_cached"
    with pytest.raises(OrderScopeDenied):
        store.observation(second["session_id"], source["source_id"])
    for sid in (None, "", "random", {"session_id": record["session_id"]}):
        with pytest.raises(OrderScopeDenied):
            store.context(sid)
    for offset, limit in ((-1, 1), (True, 1), (0, 101), (0, False)):
        with pytest.raises(InvalidState):
            store.sources(record["session_id"], offset=offset, limit=limit)
    with jobs._db() as con:
        con.execute("UPDATE document_results SET result_json=? WHERE job_id=? AND source_id=?",
                    (b"{}", job["job_id"], source["source_id"]))
    with pytest.raises(Conflict):
        store.observation(record["session_id"], source["source_id"])


@pytest.mark.parametrize("status", ["unsupported", "partial", "failed"])
def test_cached_result_status_is_distinct_from_successful_read(tmp_path, status):
    oid = order(tmp_path)
    store = IntakeHandoffs(tmp_path)
    record = connected(store, oid)
    job = store.jobs.start(oid, reader_version="test")
    claim = store.jobs.claim(job["job_id"])
    first, second = job["sources"]
    store.jobs.publish(claim, first["source_id"], observation(first))
    store.jobs.publish(claim, second["source_id"], observation(second, status=status))
    result = store.context(record["session_id"])
    assert result["cached_observations"] == 2
    assert result["files_without_observation"] == 0
    expected = {"complete": 1, "partial": 0, "failed": 0, "unsupported": 0}
    expected[status] = 1
    assert result["cached_status_counts"] == expected
    assert result["human_approved"] is False and result["use_for_calculation"] is False
    sources = store.sources(record["session_id"])["sources"]
    assert [item["observation_status"] for item in sources] == ["complete", status]


def test_only_unique_compression_descendant_inherits_binding(tmp_path):
    store = IntakeHandoffs(tmp_path, session_db=tmp_path / "state.db")
    record = connected(store, order(tmp_path))
    with sqlite3.connect(store.session_db) as db:
        db.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,source TEXT,"
                   "started_at REAL,ended_at REAL,end_reason TEXT,model_config TEXT)")
        db.execute("INSERT INTO sessions VALUES(?,NULL,'api_server',1,2,'compression',NULL)", (record["session_id"],))
        db.execute("INSERT INTO sessions VALUES('child',?,'api_server',1.9,NULL,NULL,NULL)", (record["session_id"],))
    assert store.for_session("child")["handoff_id"] == record["handoff_id"]
    with sqlite3.connect(store.session_db) as db:
        db.execute("UPDATE sessions SET end_reason='user' WHERE id=?", (record["session_id"],))
    with pytest.raises(OrderScopeDenied):
        store.for_session("child")
    with sqlite3.connect(store.session_db) as db:
        db.execute("UPDATE sessions SET end_reason='compression' WHERE id=?", (record["session_id"],))
        db.execute("INSERT INTO sessions VALUES('fork',?,'api_server',3,NULL,NULL,json_object('_branched_from',?))", (record["session_id"],) * 2)
    assert store.for_session("child")["handoff_id"] == record["handoff_id"]
    with pytest.raises(OrderScopeDenied):
        store.for_session("fork")
    with sqlite3.connect(store.session_db) as db:
        db.execute("UPDATE sessions SET model_config=NULL WHERE id='fork'")
    with pytest.raises(OrderScopeDenied):
        store.for_session("child")


@pytest.mark.parametrize("boundary", ["binding", "artifact"])
def test_source_revision_change_between_reads_fails_closed(tmp_path, monkeypatch, boundary):
    oid = order(tmp_path)
    store = IntakeHandoffs(tmp_path)
    record = connected(store, oid)
    job = store.jobs.start(oid, reader_version="test")
    source = job["sources"][0]
    claim = store.jobs.claim(job["job_id"])
    store.jobs.publish(claim, source["source_id"], observation(source))
    def change():
        Registry(tmp_path / "registry.db").mutate(
            oid, lambda state: state["folder_intake"]["files"][0].update(sha256="f" * 64))
    if boundary == "binding":
        original = store.for_session
        def changed_binding(sid):
            result = original(sid)
            change()
            return result
        monkeypatch.setattr(store, "for_session", changed_binding)
        with pytest.raises(OrderScopeDenied):
            store.sources(record["session_id"])
    else:
        original = store.jobs.result
        def changed_result(*args):
            result = original(*args)
            change()
            return result
        monkeypatch.setattr(store.jobs, "result", changed_result)
        with pytest.raises(OrderScopeDenied):
            store.observation(record["session_id"], source["source_id"])


def test_exact_narrow_mcp_schema_and_friendly_unbound(tmp_path):
    server = build_mcp(IntakeHandoffs(tmp_path))
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == {"intake_context", "intake_sources", "intake_observation"}
    for tool in tools:
        assert "session_id" in tool.inputSchema["properties"]
        assert "session_id" not in tool.inputSchema.get("required", [])
        assert "order_id" not in tool.inputSchema["properties"]
    response = asyncio.run(server.call_tool("intake_context", {}))
    assert "OrderScopeDenied" in str(response)


def test_cli_denies_intake_mode_for_non_front(tmp_path, monkeypatch):
    from metal_calc import mcp_server
    monkeypatch.setenv("METAL_CALC_ROLE", "qa")
    monkeypatch.setattr(sys, "argv", ["metal-calc-mcp", "--intake-only"])
    with pytest.raises(RuntimeError, match="requires the front role"):
        mcp_server.main()
