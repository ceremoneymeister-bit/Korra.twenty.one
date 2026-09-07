"""Independent S0 adversarial acceptance on real temporary SQLite and folder blobs.

These tests exercise domain capabilities and durable publication without parsers,
provider credentials, model calls, or production state.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import base64
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "calculator" / "metal_calc"))

from metal_calc.document_contract import empty_composition
from metal_calc.document_jobs import DocumentJobs
from metal_calc.errors import Conflict, InvalidState, NotFound, OrderScopeDenied
from metal_calc.folder_intake import FolderIntake
from metal_calc.registry import Registry
from metal_calc.securefs import SecureRoot


class Clock:
    def __init__(self):
        self.now = 10_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "agent-home"))
    orders = tmp_path / "orders"
    orders.mkdir()
    intake = FolderIntake(orders)
    clock = Clock()
    jobs = DocumentJobs(orders, clock=clock)
    yield intake, jobs, clock
    intake.close()


def upload(intake, name="34219", files=None, directories=None):
    # PDF-only admission must not acquire an XLSX prerequisite.
    files = files if files is not None else [("деталь.pdf", b"same bytes")]
    request = {"upload_id": str(uuid4()), "folder_name": name,
               "files": [{"path": path, "size": len(data)} for path, data in files],
               "directories": directories or []}
    intake.create(request)
    for index, (_, data) in enumerate(files):
        intake.upload(request["upload_id"], index, io.BytesIO(data))
    return intake.complete(request["upload_id"])["order_id"]


def observation(source, *, status="complete", fingerprint="test"):
    return {"schema_version": 2, "command": "inspect", "status": status,
            "complete": status == "complete", "use_for_calculation": False, "document_type": "pdf",
            "source": {"source_id": source["source_id"], "sha256": source["sha256"],
                       "bytes": source["bytes"], "relative_path": source["relative_path"], "sha256_verified": True},
            "coverage": {"inventory_complete": True, "pages_total": 2, "pages_inventoried": 2,
                         "pages_accounted": 2, "selected_text_pages": [1], "text_pages_read": [1],
                         "text_truncated": False, "sheets_total": None, "sheets_inventoried": 0,
                         "cells_read": 0, "cells_complete": False, "rendered_pages": []},
            "reader": {"fingerprint": fingerprint, "version": "1", "options": {}},
            "verification": {"numeric_facts": "unverified", "numeric_confidence": None,
                             "use_for_calculation": False},
            "errors": [] if status == "complete" else [{"code": "unsupported_format", "message": "Unsupported test source"}]}


def admitted(harness, *, files=None):
    intake, jobs, _ = harness
    order_id = upload(intake, files=files)
    job = jobs.start(order_id, reader_version="test")
    claim = jobs.claim(job["job_id"], owner="qa", lease_seconds=10)
    assert claim is not None
    return order_id, job, claim


def test_concurrent_admission_and_claim_have_one_durable_winner(harness):
    intake, jobs, _ = harness
    order_id = upload(intake)
    baseline = Registry(intake.orders_root / "registry.db").get(order_id)
    with ThreadPoolExecutor(max_workers=6) as pool:
        starts = list(pool.map(lambda _: jobs.start(order_id, reader_version="test"), range(6)))
    assert len({job["job_id"] for job in starts}) == 1
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(lambda _: jobs.claim(starts[0]["job_id"], owner="qa"), range(6)))
    assert sum(claim is not None for claim in claims) == 1
    assert Registry(intake.orders_root / "registry.db").get(order_id) == baseline
    with sqlite3.connect(intake.orders_root / "registry.db") as con:
        assert con.execute("SELECT count(*) FROM document_jobs").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM document_attempts").fetchone()[0] == 1


def test_identical_sha_keeps_distinct_occurrences_and_order_bound_sources(harness):
    intake, jobs, _ = harness
    a = upload(intake, "34219", [("a/деталь.pdf", b"same"), ("b/деталь.pdf", b"same")])
    b = upload(intake, "34220", [("a/деталь.pdf", b"same")])
    job_a, job_b = [jobs.start(order, reader_version="test") for order in (a, b)]
    sources = job_a["sources"] + job_b["sources"]
    assert len({source["sha256"] for source in sources}) == 1
    assert len({source["source_id"] for source in sources}) == 3
    assert job_a["coverage"]["files_total"] == 2
    assert job_a["composition"]["quantity"]["value"] is None
    assert job_a["composition"]["positions"] == []
    claim = jobs.claim(job_a["job_id"], owner="qa")
    foreign = job_b["sources"][0]
    with pytest.raises(OrderScopeDenied):
        jobs.read_source(job_a["job_id"], foreign["source_id"], claim)
    with pytest.raises(OrderScopeDenied):
        jobs.read_source(job_b["job_id"], foreign["source_id"], claim)
    with pytest.raises(OrderScopeDenied):
        jobs.publish(claim, foreign["source_id"], observation(foreign))
    with pytest.raises(OrderScopeDenied):
        jobs.result(job_a["job_id"], foreign["source_id"])
    with pytest.raises(OrderScopeDenied):
        jobs.start(a, reader_version="test", command="render", source_id=foreign["source_id"])
    with pytest.raises(InvalidState):
        jobs.publish(claim, job_a["sources"][0]["source_id"], observation(foreign))
    assert jobs.get(job_a["job_id"])["coverage"]["files_accounted"] == 0
    assert jobs.get(job_b["job_id"])["coverage"]["files_accounted"] == 0


@pytest.mark.parametrize("field,wrong", [("capability", "guessed"), ("fence", 999), ("attempt_id", "att_foreign")])
def test_capability_components_cannot_be_forged_and_are_not_public(harness, field, wrong):
    intake, jobs, _ = harness
    order_id, job, claim = admitted(harness)
    forged = {**claim, field: wrong}
    source = job["sources"][0]
    for operation in (lambda: jobs.pending(forged), lambda: jobs.renew(forged),
                      lambda: jobs.read_source(job["job_id"], source["source_id"], forged),
                      lambda: jobs.publish(forged, source["source_id"], observation(source)),
                      lambda: jobs.finish(forged)):
        with pytest.raises(OrderScopeDenied):
            operation()
    public = json.dumps([jobs.get(job["job_id"]), jobs.list(order_id)])
    assert claim["capability"] not in public
    assert "capability" not in public
    with sqlite3.connect(intake.orders_root / "registry.db") as con:
        stored = con.execute("SELECT capability_sha256 FROM document_attempts").fetchone()[0]
    assert stored == hashlib.sha256(claim["capability"].encode()).hexdigest()


def test_same_order_new_recipe_does_not_accept_other_job_capability(harness):
    _, jobs, _ = harness
    order_id, first, claim = admitted(harness)
    other = jobs.start(order_id, reader_version="v2")
    assert first["job_id"] != other["job_id"]
    assert first["document_set_revision"] == other["document_set_revision"] == 1
    with pytest.raises(OrderScopeDenied):
        jobs.pending({**claim, "job_id": other["job_id"]})


def test_expiry_fences_every_worker_operation_without_waiting_for_reclaim(harness):
    _, jobs, clock = harness
    _, job, claim = admitted(harness)
    source = job["sources"][0]
    clock.advance(10)  # The boundary itself is expired, not one second later.
    for operation in (lambda: jobs.pending(claim), lambda: jobs.renew(claim),
                      lambda: jobs.read_source(job["job_id"], source["source_id"], claim),
                      lambda: jobs.publish(claim, source["source_id"], observation(source)),
                      lambda: jobs.finish(claim)):
        with pytest.raises(OrderScopeDenied):
            operation()
    assert jobs.get(job["job_id"])["coverage"]["files_accounted"] == 0
    fresh = jobs.claim(job["job_id"], owner="replacement", lease_seconds=10)
    assert fresh["fence"] > claim["fence"]
    assert fresh["capability"] != claim["capability"]
    with pytest.raises(OrderScopeDenied):
        jobs.publish(claim, source["source_id"], observation(source))
    jobs.publish(fresh, source["source_id"], observation(source))


def test_renew_extends_live_lease_but_cannot_resurrect_expired_claim(harness):
    _, jobs, clock = harness
    _, _, claim = admitted(harness)
    clock.advance(9)
    jobs.renew(claim, lease_seconds=10)
    clock.advance(2)
    assert len(jobs.pending(claim)) == 1
    clock.advance(8)
    with pytest.raises(OrderScopeDenied):
        jobs.renew(claim, lease_seconds=10)


def test_cancel_preserves_publication_and_explicit_retry_only_reads_pending(harness):
    _, jobs, _ = harness
    order_id, job, claim = admitted(harness, files=[("a.pdf", b"a"), ("b.pdf", b"b")])
    a, b = job["sources"]
    receipt = jobs.publish(claim, a["source_id"], observation(a))
    before = jobs.result(job["job_id"], a["source_id"])
    assert jobs.cancel(job["job_id"])["status"] == "cancelled"
    assert jobs.start(order_id, reader_version="test")["status"] == "cancelled"
    assert jobs.claim(job["job_id"], owner="qa") is None
    with pytest.raises(OrderScopeDenied):
        jobs.publish(claim, b["source_id"], observation(b))
    assert jobs.result(job["job_id"], a["source_id"]) == before
    assert jobs.retry(job["job_id"])["status"] == "queued"
    fresh = jobs.claim(job["job_id"], owner="retry")
    assert [source["source_id"] for source in jobs.pending(fresh)] == [b["source_id"]]
    assert jobs.publish(fresh, a["source_id"], observation(a))["result_sha256"] == receipt["result_sha256"]
    jobs.publish(fresh, b["source_id"], observation(b))
    assert jobs.finish(fresh)["status"] == "completed"
    assert jobs.result(job["job_id"], a["source_id"]) == before


def test_restart_after_lost_publication_response_replays_without_overwriting(harness):
    intake, jobs, clock = harness
    _, job, claim = admitted(harness, files=[("a.pdf", b"a"), ("b.pdf", b"b")])
    a, b = job["sources"]
    first = jobs.publish(claim, a["source_id"], observation(a))
    restarted = DocumentJobs(intake.orders_root, clock=clock)
    assert restarted.publish(claim, a["source_id"], observation(a)) == {
        "result_sha256": first["result_sha256"], "replayed": True}
    conflicting = observation(a)
    conflicting["coverage"]["selected_text_pages"] = [2]
    with pytest.raises(Conflict):
        restarted.publish(claim, a["source_id"], conflicting)
    clock.advance(10)
    fresh = restarted.claim(job["job_id"], owner="after-crash")
    assert [source["source_id"] for source in restarted.pending(fresh)] == [b["source_id"]]
    restarted.publish(fresh, b["source_id"], observation(b, status="unsupported"))
    done = restarted.finish(fresh)
    assert done["status"] == "partial"
    assert done["coverage"]["file_accounting_complete"] is True
    assert done["coverage"]["customer_request_complete"] is None
    assert done["composition"]["calculation_ready"] is False
    assert done["composition"]["quote_ready"] is False
    assert restarted.retry(job["job_id"])["status"] == "partial"
    assert restarted.claim(job["job_id"], owner="repeat") is None
    with sqlite3.connect(intake.orders_root / "registry.db") as con:
        assert con.execute("SELECT count(*) FROM document_results").fetchone()[0] == 2


def test_missing_typed_result_cannot_be_completed_by_chat_or_native_status(harness):
    _, jobs, _ = harness
    _, job, claim = admitted(harness)
    source = job["sources"][0]
    for reply in ("готово", {"status": "completed", "text": "готово"}, {"native_run_state": "completed"}):
        with pytest.raises(InvalidState):
            jobs.publish(claim, source["source_id"], reply)
    with pytest.raises(Conflict):
        jobs.finish(claim)
    assert jobs.get(job["job_id"])["status"] == "running"
    with pytest.raises(NotFound):
        jobs.result(job["job_id"], source["source_id"])


@pytest.mark.parametrize("kind", ["self_verified", "human_receipt", "approved_by", "calculation_ready", "bom", "bad_reader", "wrong_sha", "old_binding"])
def test_untrusted_results_cannot_authorize_costing_or_change_source_binding(harness, kind):
    _, jobs, _ = harness
    _, job, claim = admitted(harness)
    source = job["sources"][0]
    payload = observation(source)
    if kind == "self_verified":
        payload["verification"]["numeric_facts"] = "verified"
    elif kind == "bad_reader":
        payload["reader"]["fingerprint"] = "old-reader"
    elif kind == "wrong_sha":
        payload["source"]["sha256"] = "0" * 64
    elif kind == "old_binding":
        payload["binding"] = {"job_id": job["job_id"], "document_set_revision": 0}
    else:
        payload[kind] = {"actor": "model", "accepted": True}
    with pytest.raises((InvalidState, Conflict)):
        jobs.publish(claim, source["source_id"], payload)
    assert jobs.get(job["job_id"])["coverage"]["files_accounted"] == 0


@pytest.mark.parametrize("field,value", [("pages_total", "many"), ("pages_inventoried", -1),
                                         ("pages_inventoried", True), ("pages_inventoried", 3),
                                         ("inventory_complete", "yes"), ("text_pages_read", "all")])
def test_malformed_typed_coverage_is_rejected_before_durable_publication(harness, field, value):
    _, jobs, _ = harness
    _, job, claim = admitted(harness)
    source = job["sources"][0]
    payload = observation(source)
    payload["coverage"][field] = value
    with pytest.raises(InvalidState):
        jobs.publish(claim, source["source_id"], payload)
    assert jobs.get(job["job_id"])["coverage"]["files_accounted"] == 0


def test_document_revision_is_distinct_from_order_revision_and_empty_directories_count(harness):
    intake, jobs, _ = harness
    order_id, first, claim = admitted(harness)
    registry = Registry(intake.orders_root / "registry.db")
    registry.mutate(order_id, lambda state: state["customer"].update(name="Changed contact"))
    ordinary_revision, baseline = registry.get(order_id)
    assert ordinary_revision == 2
    unchanged = jobs.start(order_id, reader_version="test")
    assert unchanged["job_id"] == first["job_id"]
    assert unchanged["document_set_revision"] == 1
    assert jobs.pending(claim)
    registry.mutate(order_id, lambda state: state["folder_intake"]["directories"].append("Новая пустая папка"))
    with pytest.raises(OrderScopeDenied):
        jobs.publish(claim, first["sources"][0]["source_id"], observation(first["sources"][0]))
    with pytest.raises(Conflict):
        jobs.retry(first["job_id"])
    second = jobs.start(order_id, reader_version="test")
    assert second["document_set_revision"] == 2
    assert second["manifest_digest"] != first["manifest_digest"]
    assert second["sources"][0]["source_id"] != first["sources"][0]["source_id"]
    assert jobs.get(first["job_id"])["status"] == "stale"
    assert registry.get(order_id)[0] == 3  # Only our two operator mutations.
    assert baseline["source_files"] == [] and baseline["status"] == "draft"
    fresh = jobs.claim(second["job_id"], owner="fresh")
    with pytest.raises(OrderScopeDenied):
        jobs.publish(fresh, first["sources"][0]["source_id"], observation(first["sources"][0]))


def test_reader_and_options_change_jobs_but_never_document_revision(harness):
    intake, jobs, _ = harness
    order_id = upload(intake)
    jobs_started = [jobs.start(order_id, reader_version=version, options=options)
                    for version, options in [("test", {}), ("v2", {}), ("test", {"pages": "2"})]]
    assert len({job["job_id"] for job in jobs_started}) == 3
    assert {job["document_set_revision"] for job in jobs_started} == {1}
    assert len({job["snapshot_id"] for job in jobs_started}) == 1


def test_drain_stops_new_claims_and_preserves_active_publication(harness):
    intake, jobs, _ = harness
    _, job, claim = admitted(harness)
    other = jobs.start(upload(intake, "34220"), reader_version="test")
    assert jobs.set_drain(True)["active_leases"] == 1
    source = job["sources"][0]
    jobs.publish(claim, source["source_id"], observation(source))
    assert jobs.finish(claim)["status"] == "completed"
    assert jobs.claim(other["job_id"], owner="qa") is None
    restarted = DocumentJobs(intake.orders_root)
    assert restarted.claim(other["job_id"], owner="qa") is None
    jobs.set_drain(False)
    assert jobs.claim(other["job_id"], owner="qa") is not None


def test_three_worker_losses_block_until_explicit_retry_without_erasing_result(harness):
    _, jobs, clock = harness
    _, job, claim = admitted(harness, files=[("a.pdf", b"a"), ("b.pdf", b"b")])
    a, b = job["sources"]
    jobs.publish(claim, a["source_id"], observation(a))
    for index in range(2):
        clock.advance(10)
        assert jobs.claim(job["job_id"], owner=f"crash-{index}", lease_seconds=10)
    clock.advance(10)
    assert jobs.claim(job["job_id"], owner="crash-3", lease_seconds=10) is None
    assert jobs.get(job["job_id"])["status"] == "blocked"
    assert jobs.get(job["job_id"])["coverage"]["files_accounted"] == 1
    jobs.retry(job["job_id"])
    fresh = jobs.claim(job["job_id"], owner="operator-retry")
    assert [item["source_id"] for item in jobs.pending(fresh)] == [b["source_id"]]


def test_snapshot_reader_rejects_changed_bytes_even_with_same_length(harness):
    intake, jobs, _ = harness
    order_id, job, claim = admitted(harness)
    _, state = Registry(intake.orders_root / "registry.db").get(order_id)
    source = job["sources"][0]
    data, info = jobs.read_source(job["job_id"], source["source_id"], claim)
    assert hashlib.sha256(data).hexdigest() == info["sha256"]
    stored = intake.orders_root / "folders" / state["folder_intake"]["upload_id"] / "files" / "0"
    stored.write_bytes(b"x" * len(data))
    with pytest.raises(Conflict):
        jobs.read_source(job["job_id"], source["source_id"], claim)
    with pytest.raises(Conflict):
        jobs.read_source(job["job_id"], source["source_id"])


def test_lease_expires_during_source_read_before_bytes_are_delivered(harness, monkeypatch):
    _, jobs, clock = harness
    _, job, claim = admitted(harness)
    original = SecureRoot.read_bytes

    def slow_read(root, path, **kwargs):
        data = original(root, path, **kwargs)
        if path.startswith("folders/"):
            clock.advance(10)
        return data

    monkeypatch.setattr(SecureRoot, "read_bytes", slow_read)
    with pytest.raises(OrderScopeDenied):
        jobs.read_source(job["job_id"], job["sources"][0]["source_id"], claim)


def rendered_observation(source):
    # Valid fixed one-pixel PNG; no parser subprocess is needed for publication QA.
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=")
    result = observation(source)
    result.update(command="render", page=1, nominal_dpi=180, requested_crop=[0, 0, 0.4, 0.4],
                  page_info={"width": 0.4, "height": 0.4},
                  image={"path": "page-0001.png", "sha256": hashlib.sha256(image).hexdigest(),
                         "bytes": len(image), "width": 1, "height": 1})
    result["reader"]["options"] = {"page": 1, "dpi": 180, "crop": None}
    result["coverage"].update(inventory_complete=False, pages_inventoried=0, pages_accounted=0,
                               selected_text_pages=[], text_pages_read=[], rendered_pages=[1])
    return result, {"image": image}


def render_job(harness):
    intake, jobs, _ = harness
    order_id = upload(intake)
    inventory = jobs.start(order_id, reader_version="test")
    source = inventory["sources"][0]
    job = jobs.start(order_id, reader_version="test", command="render", source_id=source["source_id"],
                     options={"page": 1, "dpi": 180, "crop": None})
    return job, source, jobs.claim(job["job_id"], owner="renderer", lease_seconds=10)


def test_lease_expires_during_asset_write_without_publishing_pointer(harness, monkeypatch):
    _, jobs, clock = harness
    job, source, claim = render_job(harness)
    result, assets = rendered_observation(source)
    original = SecureRoot.atomic_write

    def slow_write(root, path, data, **kwargs):
        value = original(root, path, data, **kwargs)
        if path.startswith("document-artifacts/"):
            clock.advance(10)
        return value

    monkeypatch.setattr(SecureRoot, "atomic_write", slow_write)
    with pytest.raises(OrderScopeDenied):
        jobs.publish(claim, source["source_id"], result, assets)
    with pytest.raises(NotFound):
        jobs.result(job["job_id"], source["source_id"])
    assert jobs.get(job["job_id"])["coverage"]["files_accounted"] == 0


def test_crash_between_asset_fsync_and_db_pointer_recovers_immutable_asset(harness):
    intake, jobs, clock = harness
    job, source, claim = render_job(harness)
    result, assets = rendered_observation(source)
    with sqlite3.connect(intake.orders_root / "registry.db") as con:
        con.execute("CREATE TRIGGER fail_publication BEFORE INSERT ON document_results "
                    "BEGIN SELECT RAISE(ABORT, 'simulated publication crash'); END")
    with pytest.raises(sqlite3.IntegrityError, match="simulated publication crash"):
        jobs.publish(claim, source["source_id"], result, assets)
    with pytest.raises(NotFound):
        jobs.result(job["job_id"], source["source_id"])
    asset_files = list((intake.orders_root / "document-artifacts").iterdir())
    assert len(asset_files) == 1
    assert asset_files[0].read_bytes() == assets["image"]
    with sqlite3.connect(intake.orders_root / "registry.db") as con:
        con.execute("DROP TRIGGER fail_publication")
    restarted = DocumentJobs(intake.orders_root, clock=clock)
    restarted.publish(claim, source["source_id"], result, assets)
    assert restarted.finish(claim)["status"] == "completed"
    assert restarted.image(job["job_id"], source["source_id"])[0] == assets["image"]
    assert len(list((intake.orders_root / "document-artifacts").iterdir())) == 1


def test_additive_migration_and_disposable_downgrade_preserve_legacy_order(harness):
    intake, jobs, _ = harness
    order_id = upload(intake)
    registry = Registry(intake.orders_root / "registry.db")
    baseline = registry.get(order_id)
    jobs.start(order_id, reader_version="test")
    assert Registry(registry.path).get(order_id) == baseline
    with sqlite3.connect(registry.path) as con:
        # Test-only downgrade. This registry belongs exclusively to tmp_path.
        for table in ("document_results", "document_chunks", "document_attempts", "document_jobs", "document_snapshots", "document_control"):
            con.execute(f"DROP TABLE {table}")
    assert Registry(registry.path).get(order_id) == baseline
    fresh = DocumentJobs(intake.orders_root)
    assert fresh.start(order_id, reader_version="test")["document_set_revision"] == 1
    assert registry.get(order_id) == baseline


@pytest.fixture
def composition_validator():
    schema = json.loads((ROOT / "calculator/review/document_composition.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def proposed_composition():
    result = empty_composition()
    result["status"] = "proposed"
    result["evidence"] = [{"evidence_id": "ev1", "source_id": "src_" + "a" * 40,
                           "source_sha256": "b" * 64, "locator": {"kind": "xlsx", "sheet": "Заявка", "cell": "D7"}}]
    result["facts"] = [{"fact_id": "f1", "subject_id": "p1", "field_key": "order_quantity",
                        "raw_text": "7", "normalized_value": "7", "unit": None,
                        "evidence_ids": ["ev1"], "method": "xlsx_cell", "version": "1",
                        "status": "needs_review", "unknown_reason": None}]
    result["positions"] = [{"position_id": "p1", "product_id": "product1", "role": "unknown",
                            "role_evidence_ids": [], "quantity": deepcopy(result["quantity"]),
                            "scope_state": "needs_review", "blocking_issue_ids": []}]
    result["relations"] = [{"relation_id": "r1", "kind": "component_of",
                            "from": {"kind": "assembly_component", "id": "component1"},
                            "to": {"kind": "assembly", "id": "product1"},
                            "evidence_ids": ["ev1"], "status": "needs_review",
                            "quantity_per_parent": {"value": "2", "unit": "шт", "basis": "explicit_source", "evidence_ids": ["ev1"]}}]
    return result


def test_empty_and_proposed_composition_validate_without_quantity_default(composition_validator):
    empty = empty_composition()
    composition_validator.validate(empty)
    proposal = proposed_composition()
    composition_validator.validate(proposal)
    assert empty["quantity"]["value"] is None
    assert proposal["positions"][0]["quantity"]["value"] is None
    assert proposal["relations"][0]["quantity_per_parent"]["value"] == "2"
    assert {issue["blocks"] for issue in empty["issues"]} == {"composition_acceptance", "calculation"}
    assert "default" not in json.dumps(composition_validator.schema["$defs"]["quantity"]["properties"])


@pytest.mark.parametrize("kind", ["receipt", "verified_fact", "accepted", "position_accepted", "calculation", "quote", "invented_quantity", "file_count_quantity", "float_quantity", "bbox_without_coordinates"])
def test_proposal_schema_rejects_self_acceptance_and_invented_quantity(composition_validator, kind):
    proposal = proposed_composition()
    if kind == "receipt":
        proposal["human_receipt"] = {"actor": "model", "accepted": True}
    elif kind == "verified_fact":
        proposal["facts"][0]["status"] = "verified"
    elif kind == "accepted":
        proposal["status"] = "accepted"
    elif kind == "position_accepted":
        proposal["positions"][0]["scope_state"] = "accepted"
    elif kind in {"calculation", "quote"}:
        proposal[kind + "_ready"] = True
    elif kind == "invented_quantity":
        proposal["quantity"]["value"] = "1"
    elif kind == "file_count_quantity":
        proposal["quantity"].update(value="179", basis="file_count", evidence_ids=["ev1"])
    elif kind == "float_quantity":
        proposal["quantity"].update(value=1.1, basis="explicit_source", evidence_ids=["ev1"])
    else:
        proposal["evidence"][0]["locator"] = {"kind": "pdf", "page": 1, "bbox": [0, 0, 10, 10]}
    with pytest.raises(ValidationError):
        composition_validator.validate(proposal)
