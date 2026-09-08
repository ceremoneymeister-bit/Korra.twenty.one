"""Supervised deterministic worker. Server state owns lifetime and recovery."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import tempfile
import threading

from .document_contract import RETRYABLE_FAILURES, SYSTEMIC_FAILURES
from .document_jobs import DocumentJobs
from .document_runtime import load_reader
from .errors import InvalidState, OrderScopeDenied


def run_once(jobs: DocumentJobs, *, document_python=None, max_sources=None, should_stop=lambda: False,
             lease_seconds=240):
    reader = load_reader()
    claim = jobs.claim(owner=f"document-worker-{os.getpid()}", lease_seconds=lease_seconds)
    if claim is None:
        return {"claimed": False}
    job_id = claim["job_id"]
    processed = 0
    failed = 0
    try:
        job = jobs.get(job_id)
        recipe = job["recipe"]
        if reader.reader_fingerprint(recipe["command"], recipe["options"]) != recipe["reader_version"]:
            jobs.release(claim, error="reader_version_changed")
            return {"job_id": job_id, "status": "blocked", "processed": 0}
        for source in jobs.pending(claim):
            if should_stop() or jobs.drained() or max_sources is not None and processed >= max_sources:
                jobs.release(claim)
                return {"job_id": job_id, "status": "queued", "processed": processed}
            jobs.renew(claim, lease_seconds=lease_seconds)
            data, checked = jobs.read_source(job_id, source["source_id"], claim)
            # Temporary paths are private implementation details, never tool
            # parameters or published provenance. Parsers see only this copy.
            with tempfile.TemporaryDirectory(prefix="calc21-document-") as temporary:
                directory = Path(temporary)
                path = directory / "source"
                path.write_bytes(data)
                del data
                output = directory / "output"
                result = reader.read_document(path, checked, output, command=recipe["command"],
                                              options=recipe["options"], python=document_python)
                errors = result.get("errors", []) if isinstance(result, dict) else []
                codes = [e.get("code") for e in errors if isinstance(e, dict)]
                systemic = next((code for code in codes if code in SYSTEMIC_FAILURES), None)
                if systemic:
                    # The environment, not this document, is broken: stop the
                    # pass and keep the job blocked until an operator retry.
                    jobs.release(claim, error=systemic)
                    return {"job_id": job_id, "status": "blocked", "error": systemic, "processed": processed}
                failure = next((code for code in codes if code in RETRYABLE_FAILURES), None)
                if failure:
                    # One document hit a resource limit or produced no usable
                    # artifact. Record it durably for a targeted retry and move
                    # on so the rest of the folder is not starved.
                    message = next((e.get("message") for e in errors if e.get("code") == failure), "")
                    record = {"code": failure, "message": message if isinstance(message, str) else "",
                              "errors": errors[:20], "reader": result.get("reader")}
                    try:
                        # Whatever the parser inventoried before the limit hit
                        # survives the disposable directory as an unverified
                        # checkpoint next to the failure.
                        jobs.record_failure(claim, source["source_id"], record | {"checkpoint": result})
                    except InvalidState:
                        # A malformed partial must not block the folder; the
                        # failure itself is still recorded durably.
                        jobs.record_failure(claim, source["source_id"], record | {"checkpoint_rejected": True})
                    failed += 1
                    continue
                assets = {}
                if result.get("image"):
                    name = result["image"].get("path", "")
                    if not name or Path(name).name != name or (output / name).is_symlink():
                        raise ValueError("Invalid image artifact name")
                    with (output / name).open("rb") as stream:
                        assets["image"] = stream.read(64 * 1024**2 + 1)
                jobs.publish(claim, source["source_id"], result, assets)
            processed += 1
        finished = jobs.finish(claim)
        summary = {"job_id": job_id, "status": finished["status"], "processed": processed}
        return summary | ({"failed": failed} if failed else {})
    except OrderScopeDenied:
        # Cancellation, newer snapshot or another lease has final authority.
        return {"job_id": job_id, "status": "fenced", "processed": processed}
    except Exception as exc:
        try:
            jobs.release(claim, error=type(exc).__name__)
        except OrderScopeDenied:
            pass
        # No document text, paths, credentials or raw capability in logs.
        return {"job_id": job_id, "status": "blocked", "error": type(exc).__name__, "processed": processed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders-root", required=True, type=Path)
    parser.add_argument("--document-python", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--lease-seconds", type=int, default=240)
    args = parser.parse_args()
    os.umask(0o077)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    jobs = DocumentJobs(args.orders_root)
    while not stop.is_set():
        result = run_once(jobs, document_python=args.document_python, should_stop=stop.is_set,
                          lease_seconds=args.lease_seconds)
        if result.get("claimed") is not False:
            print(json.dumps(result), flush=True)
        if args.once:
            return
        stop.wait(2)


if __name__ == "__main__":
    main()
