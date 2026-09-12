"""Full-CI witness negative paths and read-only shared resource admission."""
import copy
import importlib.util
from pathlib import Path
import pytest

SOURCE = Path(__file__).resolve().parents[2] / "scripts/ci/release_gate.py"
SPEC = importlib.util.spec_from_file_location("release_gate", SOURCE)
g = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(g)
SHA = "b" * 40


@pytest.fixture
def api():
    run = {"id": 7, "head_sha": SHA, "workflow_id": 3, "run_attempt": 1,
           "path": g.WORKFLOW, "event": "workflow_dispatch", "status": "completed",
           "conclusion": "success", "head_repository": {"full_name": g.REPOSITORY}}
    state = {"runs": [run], "latest": run, "jobs": [
        {"id": i, "run_id": 7, "head_sha": SHA, "name": name,
         "status": "completed", "conclusion": "success"}
        for i, name in enumerate(sorted(g.REQUIRED_JOBS), 1)], "calls": []}
    def get(path):
        state["calls"].append(path)
        if path.endswith("/workflows/ci.yaml"):
            return {"id": 3, "path": g.WORKFLOW, "state": "active"}
        if "/workflows/3/runs?" in path:
            return {"workflow_runs": state["runs"]}
        if "/attempts/1/jobs?" in path:
            return {"jobs": state["jobs"]}
        if path.endswith("/runs/7"):
            return state["latest"]
        raise AssertionError(path)
    return state, get


def test_exact_full_success_produces_revision_bound_receipt(api):
    state, get = api
    receipt = g.verify_ci(SHA, get)
    assert receipt["accepted"] is True
    assert (receipt["revision"], receipt["run_id"], receipt["run_attempt"]) == (SHA, 7, 1)
    assert len(receipt["jobs"]) == len(g.REQUIRED_JOBS)
    assert "/attempts/1/jobs?" in state["calls"][-2]


@pytest.mark.parametrize("damage", [
    "missing", "red", "cancelled", "queued", "wrong_sha", "pr", "fork",
    "wrong_workflow", "missing_job", "skipped_job", "failed_job", "job_sha",
    "newer_red", "rerun", "incomplete_rerun",
])
def test_unproven_full_ci_never_yields_receipt(api, damage):
    state, get = api
    run = state["runs"][0]
    if damage == "missing": state["runs"] = []
    elif damage == "red": run["conclusion"] = "failure"
    elif damage == "cancelled": run["conclusion"] = "cancelled"
    elif damage == "queued": run["status"] = "queued"
    elif damage == "wrong_sha": run["head_sha"] = "c" * 40
    elif damage == "pr": run["event"] = "pull_request"
    elif damage == "fork": run["head_repository"] = {"full_name": "synthetic/fork"}
    elif damage == "wrong_workflow": run["workflow_id"] = 4
    elif damage == "missing_job": state["jobs"].pop()
    elif damage == "skipped_job": state["jobs"][0]["conclusion"] = "skipped"
    elif damage == "failed_job": state["jobs"][0]["conclusion"] = "failure"
    elif damage == "job_sha": state["jobs"][0]["head_sha"] = "c" * 40
    elif damage == "newer_red": state["runs"].append({**run, "id": 8, "conclusion": "failure"})
    elif damage == "rerun": state["latest"] = {**run, "run_attempt": 2}
    else:
        state["jobs"] = [job for job in state["jobs"] if job["name"] == "All required checks pass"]
    with pytest.raises(g.GateError):
        g.verify_ci(SHA, get)


def test_full_ci_api_timeout_propagates_without_receipt(api):
    _, get = api
    def timeout(path):
        if "/jobs?" in path:
            raise TimeoutError
        return get(path)
    with pytest.raises(TimeoutError):
        g.verify_ci(SHA, timeout)


def test_job_receipts_are_fully_paginated(api):
    state, get = api
    originals = copy.deepcopy(state["jobs"])
    additional = [{**originals[0], "id": 100 + i, "name": f"advisory {i}"} for i in range(100)]
    def paged(path):
        if "/jobs?" in path:
            return {"jobs": additional if path.endswith("&page=1") else originals}
        return get(path)
    assert len(g.verify_ci(SHA, paged)["jobs"]) == 100 + len(originals)


@pytest.mark.parametrize("load,memory,disk", [
    (7, 10 * 1024**3, 30 * 1024**3), (0, None, 30 * 1024**3),
    (0, 5 * 1024**3, 30 * 1024**3), (0, 10 * 1024**3, 19 * 1024**3),
    (float("nan"), 10 * 1024**3, 30 * 1024**3),
])
def test_resource_uncertainty_or_pressure_refuses(load, memory, disk):
    with pytest.raises(g.GateError):
        g.validate_resources(load, memory, disk)


def test_resource_admission_accepts_bounded_healthy_host():
    assert g.validate_resources(1, 8 * 1024**3, 25 * 1024**3)["load1"] == 1


def test_full_ci_evaluation_and_pr_lane_skips_have_distinct_contracts():
    needs = {name: {"result": "success"} for name in g.REQUIRED_LANES}
    assert len(g.evaluate(needs, "schedule")) == len(needs)
    needs["tests"]["result"] = "skipped"
    assert g.evaluate(needs, "pull_request")["tests"] == "skipped"
    with pytest.raises(g.GateError):
        g.evaluate(needs, "workflow_dispatch")


def test_failed_cli_gate_removes_stale_success_receipt(tmp_path, monkeypatch):
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"accepted":true}')
    def fail(_revision):
        raise g.GateError("synthetic missing CI")
    monkeypatch.setattr(g, "verify_ci", fail)
    assert g.main(["verify-ci", "--revision", SHA, "--receipt", str(receipt)]) == 1
    assert not receipt.exists()


def test_resource_admission_waits_for_a_transient_spike(monkeypatch, tmp_path):
    """--wait keeps polling while the shared host is busy, then admits.

    12.09.2026: a PR went red in eleven seconds because a local test run had
    pushed load1 to 40 on the shared production host. The floor stays as
    strict as before; only the instant refusal is replaced by bounded polling.
    """
    loads = iter([40.0, 12.0, 2.0])
    monkeypatch.setattr(g.os, "getloadavg", lambda: (next(loads), 0.0, 0.0))
    monkeypatch.setattr(g.shutil, "disk_usage", lambda _p: type("du", (), {"free": 25 * 1024**3})())
    monkeypatch.setattr(
        g.Path, "read_text", lambda self, encoding="ascii": "MemTotal: 1 kB\nMemAvailable: 8388608 kB\n"
    )
    ticks = iter([0.0, 31.0, 62.0, 93.0])
    slept = []

    result = g.admission(tmp_path, wait=120, sleep=slept.append, clock=lambda: next(ticks))

    assert result["load1"] == 2.0
    assert slept == [30, 30]


def test_resource_admission_still_refuses_after_the_wait(monkeypatch, tmp_path):
    monkeypatch.setattr(g.os, "getloadavg", lambda: (40.0, 0.0, 0.0))
    monkeypatch.setattr(g.shutil, "disk_usage", lambda _p: type("du", (), {"free": 25 * 1024**3})())
    monkeypatch.setattr(
        g.Path, "read_text", lambda self, encoding="ascii": "MemAvailable: 8388608 kB\n"
    )
    ticks = iter([0.0, 31.0, 61.0, 91.0])

    with pytest.raises(g.GateError):
        g.admission(tmp_path, wait=60, sleep=lambda _s: None, clock=lambda: next(ticks))
