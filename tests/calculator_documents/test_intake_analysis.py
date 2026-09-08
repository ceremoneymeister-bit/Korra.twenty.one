"""Semantic admission and fenced publication use synthetic, isolated orders."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from test_intake_handoffs import connected, order
from test_document_jobs_adversarial import observation, rendered_observation, upload
from test_analysis_proposals import proposal
from metal_calc.document_classification import DocumentClassification
from metal_calc.errors import Conflict, InvalidState, OrderScopeDenied
from metal_calc.intake_analysis import AnalysisStore
from metal_calc.intake_handoffs import IntakeHandoffs
from metal_calc.folder_intake import FolderIntake
from metal_calc.intake_preparation import DEFAULT_ANSWERS, IntakePreparation
from metal_calc.registry import Registry


@pytest.fixture
def ready(tmp_path, request):
    handoffs = IntakeHandoffs(tmp_path)
    count = getattr(request, "param", 2)
    if count == 2:
        order_id = order(tmp_path)
    else:
        intake = FolderIntake(tmp_path)
        try:
            order_id = upload(intake, files=[(f"drawing-{i}.pdf", b"fake") for i in range(count)])
        finally:
            intake.close()
    record = connected(handoffs, order_id)
    handoffs.mark_received(record["handoff_id"])
    handoffs.mark_dispatched(record["handoff_id"], "run_test")
    handoffs.mark_finished(record["handoff_id"])
    preparation = IntakePreparation(handoffs)
    preparation.save(record["handoff_id"], snapshot_id=record["snapshot_id"], expected_revision=0,
                     request_id="answers-1", answers={**DEFAULT_ANSWERS, "scope": "whole"})
    DocumentClassification(tmp_path).ensure_snapshot(record["order_id"], record["snapshot_id"])
    return AnalysisStore(handoffs), record


def test_plan_is_immutable_and_does_not_enqueue_reader_or_change_order(ready):
    store, record = ready
    baseline = store.jobs.registry.get(record["order_id"])
    first = store.plan(record["handoff_id"])
    assert first == store.plan(record["handoff_id"])
    assert first["can_start"] and first["pages_unknown"] == 2
    assert first["documents_to_read"] == 2
    assert store.jobs.list(record["order_id"])["jobs"] == []
    assert store.get(record["handoff_id"])["job"] is None
    assert store.jobs.registry.get(record["order_id"]) == baseline


def test_concurrent_start_and_exact_replay_share_one_admission(ready):
    store, record = ready
    plan = store.plan(record["handoff_id"])
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: store.start(record["handoff_id"], plan_id=plan["plan_id"], request_id="launch"), range(3)))
    assert results[0] == results[1] == results[2]
    with store.jobs._db() as con:
        assert con.execute("SELECT count(*) FROM analysis_jobs").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM analysis_requests").fetchone()[0] == 1
    assert results[0]["summary"]["sources_total"] == 2
    assert store.jobs.list(record["order_id"])["jobs"] == []


def test_answer_change_fences_old_plan_and_reused_request_conflicts(ready):
    store, record = ready
    first = store.plan(record["handoff_id"])
    store.start(record["handoff_id"], plan_id=first["plan_id"], request_id="launch")
    store.preparation.save(record["handoff_id"], snapshot_id=record["snapshot_id"], expected_revision=1,
                           request_id="answers-2", answers={**DEFAULT_ANSWERS, "scope": "whole", "notes": "new"})
    assert store.get(record["handoff_id"])["job"]["status"] == "stale"
    with pytest.raises(Conflict):
        store.start(record["handoff_id"], plan_id=first["plan_id"], request_id="new-launch")
    second = store.plan(record["handoff_id"])
    assert second["plan_id"] != first["plan_id"]
    with pytest.raises(Conflict):
        store.start(record["handoff_id"], plan_id=second["plan_id"], request_id="launch")


def test_changed_snapshot_cannot_start_old_plan(ready):
    store, record = ready
    plan = store.plan(record["handoff_id"])
    store.jobs.registry.mutate(record["order_id"], lambda state: state["folder_intake"]["files"][0].update(sha256="f" * 64))
    with pytest.raises(Conflict):
        store.start(record["handoff_id"], plan_id=plan["plan_id"], request_id="launch")


def admit(store, record):
    plan = store.plan(record["handoff_id"])
    job = store.start(record["handoff_id"], plan_id=plan["plan_id"], request_id="launch")
    claim = store.claim("test-worker")
    return job, claim


def inventories(store, record, claim, *, pages=2):
    reader = store.jobs.start(record["order_id"], reader_version="test")
    reader_claim = store.jobs.claim(reader["job_id"], owner="reader")
    for source in reader["sources"]:
        result = observation(source)
        result["coverage"].update(pages_total=pages, pages_inventoried=pages, pages_accounted=pages)
        store.jobs.publish(reader_claim, source["source_id"], result)
        store.update_source(claim, source["source_id"], inspect_job_id=reader["job_id"], page_count=pages, document_type="pdf")
    store.jobs.finish(reader_claim)
    return reader


def test_lease_takeover_fences_previous_worker_and_drain_stops_claim(ready):
    store, record = ready
    now = [1000.0]
    store.clock = lambda: now[0]
    _, claim = admit(store, record)
    assert store.claim("other") is None
    store.renew(claim)
    source = store.sources(claim)[0]
    now[0] += 121
    replacement = store.claim("other")
    assert replacement["fence"] > claim["fence"]
    with pytest.raises(OrderScopeDenied):
        store.update_source(claim, source["source_id"], status="reading")
    store.release(claim)
    assert store.sources(replacement)
    store.release(replacement)
    store.set_drain(True)
    assert store.drained() and store.claim("other") is None


def test_inventory_gate_checks_all_real_results_and_limits(ready):
    store, record = ready
    _, claim = admit(store, record)
    assert store.gate(claim) == {"ready": False, "blockers": ["inventory_pending"], "known_pages": 0}
    inventories(store, record, claim, pages=9)
    gate = store.gate(claim)
    assert not gate["ready"] and "pdf_page_limit" in gate["blockers"]


def test_current_answers_guard_applies_to_source_writes(ready):
    store, record = ready
    _, claim = admit(store, record)
    source = store.sources(claim)[0]
    store.preparation.save(record["handoff_id"], snapshot_id=record["snapshot_id"], expected_revision=1,
                           request_id="changed", answers={**DEFAULT_ANSWERS, "scope": "whole", "notes": "changed"})
    with pytest.raises(OrderScopeDenied):
        store.update_source(claim, source["source_id"], status="reading")


def model_attempt(store, record):
    job, claim = admit(store, record)
    inventories(store, record, claim)
    source = store.sources(claim)[0]
    mapping = {}
    for page in (1, 2):
        options = {"page": page, "dpi": 120, "max_pixels": 8000000}
        reader = store.jobs.start(record["order_id"], reader_version="test", command="render",
                                  source_id=source["source_id"], options=options)
        reader_claim = store.jobs.claim(reader["job_id"], owner="renderer")
        result, assets = rendered_observation(source)
        result.update(page=page, nominal_dpi=120)
        result["coverage"]["rendered_pages"] = [page]
        result["reader"]["options"] = options
        store.jobs.publish(reader_claim, source["source_id"], result, assets)
        store.jobs.finish(reader_claim)
        mapping[str(page)] = reader["job_id"]
    store.update_source(claim, source["source_id"], render_jobs=mapping, status="ready")
    attempt = store.create_attempt(claim, source["source_id"])
    body = {"session_id": attempt["session_id"], "input": "synthetic test"}
    store.update_attempt(claim, attempt["attempt_id"], request_body=body)
    store.update_attempt(claim, attempt["attempt_id"], status="dispatching")
    return job, claim, source, attempt


def source_proposal(source):
    value = proposal()
    value["evidence"][0].update(source_id=source["source_id"], source_sha256=source["sha256"])
    return value


def test_attempt_body_frozen_and_exact_session_only(ready):
    store, record = ready
    _, claim, source, attempt = model_attempt(store, record)
    with pytest.raises(Conflict):
        store.update_attempt(claim, attempt["attempt_id"], request_body={"session_id": attempt["session_id"], "input": "changed"})
    with pytest.raises(OrderScopeDenied):
        store.for_session(attempt["session_id"] + "_copy")
    binding = store.for_session(attempt["session_id"])
    assert binding["source"]["source_id"] == source["source_id"]
    assert binding["observation"]["source"]["sha256"] == source["sha256"]
    with pytest.raises(Conflict):
        store.update_source(claim, source["source_id"], render_jobs={})


def test_all_pages_required_publication_replays_and_rejects_different_content(ready):
    store, record = ready
    _, claim, source, attempt = model_attempt(store, record)
    value = source_proposal(source)
    with pytest.raises(InvalidState):
        store.submit(attempt["session_id"], value)
    for page in (1, 2):
        store.record_view(attempt["session_id"], page)
    receipt = store.submit(attempt["session_id"], value)
    assert store.submit(attempt["session_id"], value) == receipt
    value["issues"][0]["question"] = "Другой вопрос"
    with pytest.raises(Conflict):
        store.submit(attempt["session_id"], value)
    assert store.sources(claim)[0]["proposal"] is not None
    assert store.sources(claim)[0]["status"] == "running"
    assert not receipt["human_approved"] and not receipt["use_for_calculation"]


def test_cancel_fences_native_writes_and_late_run_id_survives_cleanup(ready):
    store, record = ready
    job, claim, source, attempt = model_attempt(store, record)
    store.cancel(record["handoff_id"], job["job_id"])
    with pytest.raises(OrderScopeDenied):
        store.record_view(attempt["session_id"], 1)
    with pytest.raises(OrderScopeDenied):
        store.update_attempt(claim, attempt["attempt_id"], run_id="run_late", status="running")
    store.record_cancelled_dispatch(attempt["attempt_id"], "run_late")
    assert store.cancelled_attempts()[0]["run_id"] == "run_late"
    with pytest.raises(Conflict):
        store.retry(record["handoff_id"], job["job_id"], "retry-before-stop")
    store.mark_cancelled(attempt["attempt_id"])
    assert not store.cancelled_attempts()
    result = store.retry(record["handoff_id"], job["job_id"], "retry-after-stop")
    assert result["status"] == "queued"
    assert store.retry(record["handoff_id"], job["job_id"], "retry-after-stop") == result


def test_unknown_dispatch_never_authorizes_new_attempt_or_retry(ready):
    store, record = ready
    job, claim, source, attempt = model_attempt(store, record)
    store.update_attempt(claim, attempt["attempt_id"], status="unknown", error_code="model_dispatch_unknown")
    store.update_source(claim, source["source_id"], status="blocked", error_code="model_dispatch_unknown")
    other = store.sources(claim)[1]
    with pytest.raises(Conflict):
        store.create_attempt(claim, other["source_id"])
    with pytest.raises(Conflict):
        store.retry(record["handoff_id"], job["job_id"], "unsafe-retry")
    assert store.recompute(claim)["status"] == "blocked"


def test_retry_preserves_published_success_and_creates_one_new_explicit_attempt(ready):
    store, record = ready
    job, claim, source, attempt = model_attempt(store, record)
    store.update_attempt(claim, attempt["attempt_id"], run_id="run_complete", status="running")
    for page in (1, 2):
        store.record_view(attempt["session_id"], page)
    saved = store.submit(attempt["session_id"], source_proposal(source))
    store.update_attempt(claim, attempt["attempt_id"], status="completed")
    store.update_source(claim, source["source_id"], status="complete")
    other = store.sources(claim)[1]
    store.update_source(claim, other["source_id"], status="failed", error_code="render_failed")
    assert store.recompute(claim)["status"] == "partial"
    store.release(claim)
    retry = store.retry(record["handoff_id"], job["job_id"], "retry-failed")
    assert retry["rows"][0]["status"] == "complete"
    assert retry["rows"][1]["retry_requested"] is True
    assert store.submit(attempt["session_id"], source_proposal(source)) == saved
    before = retry["rows"]
    assert store.retry(record["handoff_id"], job["job_id"], "retry-failed")["rows"] == before


def test_recipe_drift_and_source_scope_guard(ready, monkeypatch):
    store, record = ready
    plan = store.plan(record["handoff_id"])
    from metal_calc import intake_analysis
    monkeypatch.setattr(intake_analysis, "recipe_fingerprint", lambda: "changed")
    with pytest.raises(Conflict):
        store.start(record["handoff_id"], plan_id=plan["plan_id"], request_id="launch")


@pytest.mark.parametrize("ready", [21], indirect=True)
def test_more_than_twenty_sources_cannot_be_admitted(ready):
    store, record = ready
    plan = store.plan(record["handoff_id"])
    assert not plan["can_start"] and "meaningful_files_limit" in plan["blockers"]
    with pytest.raises(Conflict):
        store.start(record["handoff_id"], plan_id=plan["plan_id"], request_id="oversized")
    assert store.jobs.list(record["order_id"])["jobs"] == []


@pytest.mark.parametrize("ready", [6], indirect=True)
def test_combined_pdf_pages_block_every_model_attempt(ready):
    store, record = ready
    _, claim = admit(store, record)
    inventories(store, record, claim, pages=8)
    assert store.gate(claim) == {"ready": False, "blockers": ["order_page_limit"], "known_pages": 48}
    for source in store.sources(claim):
        with pytest.raises(Conflict):
            store.create_attempt(claim, source["source_id"])
    with store.jobs._db() as con:
        assert con.execute("SELECT count(*) FROM analysis_attempts").fetchone()[0] == 0


def test_foreign_source_dependency_and_handoff_admission_rejected(ready):
    store, record = ready
    plan = store.plan(record["handoff_id"])
    with pytest.raises(OrderScopeDenied):
        store.start("intake_other", plan_id=plan["plan_id"], request_id="foreign")
    _, claim = admit(store, record)
    a, b = store.sources(claim)
    dep = store.jobs.start(record["order_id"], reader_version="test", source_id=b["source_id"])
    with pytest.raises(OrderScopeDenied):
        store.update_source(claim, a["source_id"], inspect_job_id=dep["job_id"])


def test_failed_inventory_without_result_stops_mixed_order_and_allows_explicit_retry(ready):
    from metal_calc.intake_analysis_worker import AnalysisWorker
    from test_intake_analysis_worker import FakeNative, publish_dependencies

    store, record = ready
    plan = store.plan(record["handoff_id"])
    store.start(record["handoff_id"], plan_id=plan["plan_id"], request_id="launch")
    native = FakeNative()
    worker = AnalysisWorker(store, native)
    worker.run_once()
    before = store.get(record["handoff_id"])["job"]
    failed, healthy = before["rows"]
    store.jobs.cancel(failed["inspect_job_id"])
    publish_dependencies(store, record)
    saved_healthy = store.jobs.result(healthy["inspect_job_id"], healthy["source_id"])
    worker.run_once()
    result = store.get(record["handoff_id"])["job"]
    assert result["status"] == "blocked"
    assert result["retryable"] is True
    assert result["rows"][0]["status"] == "failed"
    assert result["rows"][1]["error_code"] == "inventory_incomplete"
    assert store.jobs.result(healthy["inspect_job_id"], healthy["source_id"]) == saved_healthy
    assert not native.dispatched
    with store.jobs._db() as con:
        assert con.execute("SELECT count(*) FROM analysis_attempts").fetchone()[0] == 0
    assert store.retry(record["handoff_id"], result["job_id"], "retry-inventory")["status"] == "queued"


def test_missing_result_is_pending_only_while_dependency_can_still_publish(ready):
    store, record = ready
    _, claim = admit(store, record)
    a, b = store.sources(claim)
    for source in (a, b):
        dep = store.jobs.start(record["order_id"], reader_version="test", source_id=source["source_id"])
        store.update_source(claim, source["source_id"], inspect_job_id=dep["job_id"], status="reading")
    assert store.gate(claim)["blockers"] == ["inventory_pending"]
    for source in store.sources(claim):
        store.jobs.cancel(source["inspect_job_id"])
    assert store.gate(claim)["blockers"] == ["inventory_incomplete"]


def formula_workbook(*, long=False):
    """Valid XLSX with 1000 formulas; even the long variant fits reader limits."""
    from io import BytesIO
    from zipfile import ZipFile, ZIP_DEFLATED

    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package = "http://schemas.openxmlformats.org/package/2006/relationships"
    name = "Ж" * 31 if long else "Sheet 1"
    formula, cached = ('LEN("' + "Ж" * 30 + '")', "30") if long else ("1+1", "2")
    rows = ''.join(f'<row r="{i}"><c r="A{i}"><f>{formula}</f><v>{cached}</v></c></row>' for i in range(1, 1001))
    members = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "xl/workbook.xml": f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{package}"><Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{ns}"><sheetData>{rows}</sheetData></worksheet>',
    }
    data = BytesIO()
    with ZipFile(data, "w", ZIP_DEFLATED) as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return data.getvalue()


@pytest.mark.parametrize("long", [False, True])
def test_real_xlsx_formula_context_is_bounded_before_any_model_attempt(tmp_path, long):
    import json
    from pathlib import Path
    import sys
    from metal_calc.analysis_recipe import LIMITS, source_contents
    from metal_calc.document_worker import run_once as read_once
    from metal_calc.intake_analysis_worker import AnalysisWorker
    from test_document_jobs import make_order
    from test_intake_analysis_worker import FakeNative

    oid = make_order(tmp_path, paths=("formulas.xlsx",), content=formula_workbook(long=long))
    handoffs = IntakeHandoffs(tmp_path)
    record = connected(handoffs, oid)
    handoffs.mark_received(record["handoff_id"])
    handoffs.mark_dispatched(record["handoff_id"], "run_initial")
    handoffs.mark_finished(record["handoff_id"])
    IntakePreparation(handoffs).save(record["handoff_id"], snapshot_id=record["snapshot_id"], expected_revision=0,
        request_id="answers", answers={**DEFAULT_ANSWERS, "scope": "whole"})
    DocumentClassification(tmp_path).ensure_snapshot(oid, record["snapshot_id"])
    store, native = AnalysisStore(handoffs), FakeNative()
    plan = store.plan(record["handoff_id"])
    store.start(record["handoff_id"], plan_id=plan["plan_id"], request_id="launch")
    worker = AnalysisWorker(store, native)
    worker.run_once()
    interpreter = Path("/opt/metal-calc/documents/.venv/bin/python")
    assert read_once(store.jobs, document_python=str(interpreter) if interpreter.exists() else sys.executable)["status"] == "completed"
    row = store.get(record["handoff_id"])["job"]["rows"][0]
    inspected = store.jobs.result(row["inspect_job_id"], row["source_id"])
    assert inspected["status"] == "complete" and inspected["coverage"]["cells_read"] == 1000
    if long:
        with pytest.raises(InvalidState):
            source_contents(row, inspected)
    else:
        contents = source_contents(row, inspected)
        assert len(contents["cells"]) == 1000
        assert len(json.dumps(contents, ensure_ascii=False, separators=(",", ":")).encode()) <= LIMITS["max_source_context_bytes"]
    worker.run_once()
    job = store.get(record["handoff_id"])["job"]
    if long:
        assert job["status"] == "blocked"
        assert job["rows"][0]["error_code"] == "model_context_limit"
        assert not native.dispatched
        with store.jobs._db() as con:
            assert con.execute("SELECT count(*) FROM analysis_attempts").fetchone()[0] == 0
    else:
        assert len(native.dispatched) == 1


def test_image_byte_budget_is_checked_before_attempt_allocation(ready, monkeypatch):
    from metal_calc.analysis_recipe import LIMITS

    store, record = ready
    monkeypatch.setitem(LIMITS, "max_model_image_bytes", 1)
    with pytest.raises(Conflict, match="Изображение страницы превышает"):
        model_attempt(store, record)
    with store.jobs._db() as con:
        assert con.execute("SELECT count(*) FROM analysis_attempts").fetchone()[0] == 0
