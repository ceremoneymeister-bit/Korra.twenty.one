#!/usr/bin/env python3
"""Fail-closed full-CI witness and shared-host admission. No publish operations."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import urllib.request

REPOSITORY = "ceremoneymeister-bit/Korra.twenty.one"
WORKFLOW = ".github/workflows/ci.yaml"
FULL_EVENTS = {"workflow_dispatch", "schedule"}
REQUIRED_LANES = {"detect", "tests", "lint", "js-tests", "rust-tests",
                  "uv-lockfile", "docker-lint", "osv-scanner"}
# Native reusable-workflow names; renaming requires deliberate gate migration.
REQUIRED_JOBS = {
    "Detect affected areas", "Python tests / Run tests", "Python tests / e2e",
    "Python lints / ruff enforcement (blocking)",
    "Python lints / Windows footguns (blocking)",
    "JS & TS checks / JS & TS checks",
    "Rust tests / cargo test (bootstrap installer)",
    "Check uv.lock / uv lock --check",
    "Lint Docker scripts / Lint Dockerfile (hadolint)",
    "Lint Docker scripts / Lint docker/ shell scripts (shellcheck)",
    "OSV scan / Scan lockfiles", "All required checks pass",
}


class GateError(RuntimeError):
    pass


def evaluate(needs, event):
    if not isinstance(needs, dict) or not needs:
        raise GateError("Missing CI lane results")
    results = {}
    for name, item in needs.items():
        if not isinstance(item, dict) or item.get("result") not in {"success", "skipped"}:
            raise GateError("CI lane failed, cancelled or incomplete")
        results[name] = item["result"]
    if event in FULL_EVENTS and any(results.get(name) != "success" for name in REQUIRED_LANES):
        raise GateError("Full CI requires every mandatory lane to succeed")
    return results


def admission(workspace):
    """Read-only check inside the common workflow concurrency lease."""
    available = None
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        if line.startswith("MemAvailable:"):
            available = int(line.split()[1]) * 1024
    return validate_resources(os.getloadavg()[0], available, shutil.disk_usage(workspace).free)


def validate_resources(load, memory, disk):
    if (not isinstance(load, (int, float)) or not 0 <= load <= 6
            or not isinstance(memory, int) or memory < 6 * 1024**3
            or not isinstance(disk, int) or disk < 20 * 1024**3):
        raise GateError("Shared host is busy or lacks RAM/disk; retry later")
    return {"load1": load, "available_bytes": memory, "free_bytes": disk}


def api_get(path):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise GateError("Read-only Actions token unavailable")
    request = urllib.request.Request("https://api.github.com" + path, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise GateError("Oversized Actions response")
    return json.loads(raw)


def pages(get, path, key):
    result = []
    for page in range(1, 11):
        separator = "&" if "?" in path else "?"
        value = get(f"{path}{separator}per_page=100&page={page}")
        items = value.get(key) if isinstance(value, dict) else None
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise GateError("Malformed Actions collection")
        result.extend(items)
        if len(items) < 100:
            return result
    raise GateError("Actions pagination limit; cannot prove full CI")


def validate_run(run, revision, workflow_id):
    if (not isinstance(run, dict) or run.get("head_sha") != revision
            or run.get("workflow_id") != workflow_id
            or not isinstance(run.get("path"), str)
            or run["path"].split("@", 1)[0] != WORKFLOW
            or not isinstance(run.get("head_repository"), dict)
            or run["head_repository"].get("full_name") != REPOSITORY
            or run.get("event") not in FULL_EVENTS
            or run.get("status") != "completed" or run.get("conclusion") != "success"
            or type(run.get("id")) is not int or run["id"] <= 0
            or type(run.get("run_attempt")) is not int or run["run_attempt"] <= 0):
        raise GateError("Latest exact-SHA full CI is missing, incomplete or unsuccessful")


def validate_jobs(jobs, revision, run_id):
    seen = set()
    for job in jobs:
        if (not isinstance(job, dict) or job.get("head_sha") != revision or job.get("run_id") != run_id
                or job.get("status") != "completed"
                or job.get("conclusion") not in {"success", "skipped"}):
            raise GateError("CI job receipt mismatched or unsuccessful")
        name = job.get("name")
        if not isinstance(name, str) or not name or name in seen:
            raise GateError("Ambiguous CI job receipt")
        seen.add(name)
        if name in REQUIRED_JOBS and job["conclusion"] != "success":
            raise GateError("Mandatory full-CI job did not run successfully")
    if not REQUIRED_JOBS.issubset(seen):
        raise GateError("Missing mandatory full-CI jobs in this run attempt")


def verify_ci(revision, get=api_get):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise GateError("Expected exact commit SHA")
    base = f"/repos/{REPOSITORY}/actions"
    workflow = get(base + "/workflows/ci.yaml")
    if not isinstance(workflow, dict):
        raise GateError("Malformed CI workflow identity")
    workflow_id = workflow.get("id")
    if (type(workflow_id) is not int or workflow.get("path") != WORKFLOW
            or workflow.get("state") != "active"):
        raise GateError("Unexpected CI workflow identity")
    runs = pages(get, base + f"/workflows/{workflow_id}/runs?head_sha={revision}", "workflow_runs")
    # Newer red/cancelled runs invalidate old green witnesses. PR is not full CI.
    candidates = [run for run in runs if run.get("head_sha") == revision
                  and run.get("event") in FULL_EVENTS]
    if not candidates or any(type(run.get("id")) is not int for run in candidates):
        raise GateError("No exact-SHA full CI run")
    run = max(candidates, key=lambda item: item["id"])
    validate_run(run, revision, workflow_id)
    run_id, attempt = run["id"], run["run_attempt"]
    jobs = pages(get, base + f"/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
    validate_jobs(jobs, revision, run_id)
    latest = get(base + f"/runs/{run_id}")
    validate_run(latest, revision, workflow_id)
    if latest["run_attempt"] != attempt:
        raise GateError("CI run attempt changed during verification")
    return {"accepted": True, "revision": revision, "workflow_id": workflow_id,
            "run_id": run_id, "run_attempt": attempt,
            "url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
            "jobs": [{"id": job.get("id"), "name": job["name"],
                      "conclusion": job["conclusion"]} for job in jobs]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("verify-ci")
    check.add_argument("--revision", required=True)
    check.add_argument("--receipt", type=Path, required=True)
    resource = sub.add_parser("admission")
    resource.add_argument("--workspace", type=Path, default=Path.cwd())
    sub.add_parser("evaluate")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-ci":
            args.receipt.unlink(missing_ok=True)
            result = verify_ci(args.revision)
            args.receipt.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(f"Full CI accepted: run {result['run_id']} exact {args.revision}")
        elif args.command == "admission":
            print(json.dumps(admission(args.workspace), sort_keys=True))
        else:
            result = evaluate(json.loads(os.environ["NEEDS"]), os.environ["GITHUB_EVENT_NAME"])
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write("needs-json=" + json.dumps(result) + "\n")
            print("Required CI lanes passed")
    except (GateError, OSError, ValueError, TypeError, KeyError):
        print("Release gate refused: full CI/resource proof unavailable", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
