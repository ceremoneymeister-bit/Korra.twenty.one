"""Bounded semantic worker: deterministic dependencies, durable native runs.

Only operator-admitted AnalysisStore jobs are claimable. Network uncertainty
never creates a replacement attempt; native completion is not a proposal.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .analysis_recipe import (ANALYSIS_INPUT_TEMPLATE, ANALYSIS_INSTRUCTIONS,
                              ANALYSIS_MODEL, ANALYSIS_PROFILE, ANALYSIS_PROVIDER,
                              LIMITS, RENDER_OPTIONS)
from .document_runtime import load_reader
from .errors import Conflict, NotFound, OrderScopeDenied


PROFILE = ANALYSIS_PROFILE
_RUN_ID = re.compile(r"run_[A-Za-z0-9_-]{1,128}\Z")
_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class NativeUnavailable(Exception):
    """Sanitized transport failure; the outcome of POST may be unknown."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class NativeRunsClient:
    """Local authenticated native transport with no proxies or redirect leaks."""

    def __init__(self, base_url: str, token: str, *, timeout=12):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"} or not token):
            raise ValueError("Invalid local native configuration")
        self.base_url = base_url.rstrip("/") + f"/p/{PROFILE}"
        self.token = token
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def request(self, method, path, *, body=None, headers=None):
        raw = json.dumps(body, ensure_ascii=False, allow_nan=False).encode() if body is not None else None
        req = Request(self.base_url + path, data=raw, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
            **(headers or {}),
        })
        try:
            try:
                response = self.opener.open(req, timeout=self.timeout)
            except HTTPError as exc:
                response = exc
            with response:
                data = response.read(128 * 1024 + 1)
                if len(data) > 128 * 1024:
                    raise NativeUnavailable()
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    raise NativeUnavailable()
                return response.status, payload
        except (OSError, URLError, ValueError) as exc:
            raise NativeUnavailable() from exc

    def ensure_session(self, attempt):
        status, payload = self.request("POST", "/api/sessions", body={
            "id": attempt["session_id"], "title": "Разбор документа · " + attempt["attempt_id"][-16:],
            "source": "dashboard",
        })
        return status == 201 or (status == 409 and
                                (payload.get("error") or {}).get("code") == "session_exists")

    def dispatch(self, attempt):
        return self.request("POST", "/v1/runs", body=attempt["request_body"], headers={
            "Idempotency-Key": attempt["idempotency_key"],
            "X-Hermes-Tool-Scope": attempt["session_id"],
            "X-Korra-Session-Source": "dashboard",
        })

    def poll(self, attempt):
        run_id = attempt["run_id"]
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise NativeUnavailable()
        return self.request("GET", "/v1/runs/" + run_id)

    def stop(self, attempt):
        run_id = attempt["run_id"]
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise NativeUnavailable()
        return self.request("POST", "/v1/runs/" + run_id + "/stop", body={})


def request_body(source, attempt):
    """Only immutable recipe and trusted source identity enter native input."""
    return {"session_id": attempt["session_id"], "model": ANALYSIS_MODEL,
            "provider": ANALYSIS_PROVIDER, "instructions": ANALYSIS_INSTRUCTIONS,
            "input": ANALYSIS_INPUT_TEMPLATE.format(source_id=source["source_id"])}


class AnalysisWorker:
    def __init__(self, store, native, *, owner=None, lease_seconds=120):
        self.store, self.jobs, self.native = store, store.jobs, native
        self.owner = owner or f"analysis-worker-{os.getpid()}"
        self.lease_seconds = lease_seconds

    def _dependency(self, claim, source, command, options, job_id):
        if not job_id:
            reader = load_reader()
            options = reader.canonical_options(command, options)
            job = self.jobs.start(claim["order_id"], source_id=source["source_id"],
                                  reader_version=reader.reader_fingerprint(command, options),
                                  command=command, options=options)
        else:
            job = self.jobs.get(job_id)
        if job["snapshot_id"] != claim["snapshot_id"]:
            raise OrderScopeDenied("Document dependency snapshot changed")
        # get() binds the receipt to its current snapshot; result() verifies its
        # digest. Check command/options so a render cannot stand in for inspect.
        recipe = job["recipe"]
        if (recipe["command"] != command or
                command == "render" and any(recipe["options"].get(k) != v for k, v in options.items())):
            raise OrderScopeDenied("Document dependency recipe mismatch")
        try:
            result = self.jobs.result(job["job_id"], source["source_id"])
        except NotFound:
            result = None
        entry = next((row for row in job["sources"] if row["source_id"] == source["source_id"]), None)
        receipt = result.get("source") if result else entry
        if receipt is None or receipt["sha256"] != source["sha256"] or receipt["bytes"] != source["bytes"]:
            raise OrderScopeDenied("Document dependency source mismatch")
        # An immutable accepted source stays reusable if another source in its
        # older batch later failed or the remainder of that batch was cancelled.
        failed = job["status"] == "stale" or result is None and (
            job["status"] in {"blocked", "cancelled"} or bool(entry and entry["status"] == "failed"))
        return job["job_id"], result, failed

    def _inventory(self, claim, sources, stopping):
        for source in sources:
            if stopping():
                return
            if source["status"] not in {"pending", "reading"}:
                continue
            self.store.renew(claim, lease_seconds=self.lease_seconds)
            jid, result, failed = self._dependency(claim, source, "inspect", {}, source.get("inspect_job_id"))
            changes = {"inspect_job_id": jid, "status": "reading"}
            if failed:
                changes.update(status="failed", error_code="inventory_failed")
            elif result is not None:
                coverage = result.get("coverage", {})
                kind = result.get("document_type")
                count = coverage.get("pages_total")
                changes.update(document_type=kind, page_count=count if type(count) is int and count > 0 else None)
                if kind not in {"pdf", "xlsx"} or result["status"] == "unsupported":
                    changes.update(status="unsupported", error_code="unsupported_document")
                elif coverage.get("inventory_complete") is not True or result["status"] == "failed":
                    changes.update(status="failed", error_code="inventory_incomplete")
                elif kind == "xlsx" and (result["status"] != "complete" or not coverage.get("cells_complete")):
                    changes.update(status="failed", error_code="inventory_incomplete")
                elif kind == "pdf" and (type(coverage.get("pages_total")) is not int or coverage["pages_total"] < 1):
                    changes.update(status="failed", error_code="inventory_incomplete")
                else:
                    changes.update(status="ready" if kind == "xlsx" else "rendering", error_code=None)
            self.store.update_source(claim, source["source_id"], **changes)

    def _retry_dependencies(self, claim, sources):
        """Consume an explicit operator receipt; normal ticks never call retry."""
        for source in sources:
            if not source.get("retry_requested"):
                continue
            dependencies = [source.get("inspect_job_id"), *(source.get("render_jobs") or {}).values()]
            for job_id in dependencies:
                if not job_id:
                    continue
                job = self.jobs.get(job_id)
                row = next((s for s in job["sources"] if s["source_id"] == source["source_id"]), None)
                if row and not row["accepted"] and (row["status"] == "failed" or job["status"] in {"blocked", "cancelled"}):
                    if job["coverage"]["files_total"] == 1:
                        self.jobs.retry(job_id)
                    else:
                        self.jobs.retry(job_id, source_id=source["source_id"])
            self.store.update_source(claim, source["source_id"], retry_requested=False)

    def _render(self, claim, source, stopping):
        renders = dict(source.get("render_jobs") or {})
        all_ready = True
        for page in range(1, source["page_count"] + 1):
            if stopping():
                return False
            self.store.renew(claim, lease_seconds=self.lease_seconds)
            options = {**RENDER_OPTIONS, "page": page}
            jid, result, failed = self._dependency(claim, source, "render", options, renders.get(str(page)))
            renders[str(page)] = jid
            self.store.update_source(claim, source["source_id"], render_jobs=dict(renders), status="rendering")
            if failed or result is not None and (result["status"] != "complete" or not result.get("image")):
                self.store.update_source(claim, source["source_id"], status="failed", error_code="render_failed")
                return False
            if result is not None and result["image"].get("bytes", 0) > LIMITS["max_model_image_bytes"]:
                # The image tool cannot transport this accepted raster. Keep
                # its cache, but reject before creating a paid native attempt.
                self.store.update_source(claim, source["source_id"], status="blocked", error_code="model_image_limit")
                return False
            if result is None:
                all_ready = False
        if all_ready:
            self.store.update_source(claim, source["source_id"], status="ready", error_code=None)
        return all_ready

    def _fail_attempt(self, claim, source, attempt, code, *, unknown=False):
        self.store.update_attempt(claim, attempt["attempt_id"],
                                  status="unknown" if unknown else "failed", error_code=code)
        self.store.update_source(claim, source["source_id"],
                                 status="blocked" if unknown else "failed", error_code=code)

    def _dispatch(self, claim, source, attempt, stopping):
        if attempt["status"] != "prepared":
            # A process may die between marking dispatching and recording the
            # response. Absence of run_id cannot establish non-admission.
            self._fail_attempt(claim, source, attempt, "model_dispatch_unknown", unknown=True)
            return
        body = attempt.get("request_body") or request_body(source, attempt)
        attempt = self.store.update_attempt(claim, attempt["attempt_id"], request_body=body)
        try:
            if not self.native.ensure_session(attempt):
                self._fail_attempt(claim, source, attempt, "native_session_failed")
                return
        except NativeUnavailable:
            self._fail_attempt(claim, source, attempt, "native_session_failed")
            return
        if stopping():
            return
        self.store.renew(claim, lease_seconds=self.lease_seconds)
        attempt = self.store.update_attempt(claim, attempt["attempt_id"], status="dispatching")
        self.store.update_source(claim, source["source_id"], status="running", error_code=None)
        try:
            status, payload = self.native.dispatch(attempt)
        except NativeUnavailable:
            self._fail_attempt(claim, source, attempt, "model_dispatch_unknown", unknown=True)
            return
        if status not in {200, 202}:
            # Timeouts and server failures may occur after native admission.
            # Conflicts can denote an existing idempotency reservation as well.
            unknown = status >= 500 or status in {408, 409} or status < 400
            self._fail_attempt(claim, source, attempt,
                               "model_dispatch_unknown" if unknown else "model_dispatch_rejected",
                               unknown=unknown)
            return
        run_id = payload.get("run_id")
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            self._fail_attempt(claim, source, attempt, "model_dispatch_unknown", unknown=True)
            return
        try:
            self.store.update_attempt(claim, attempt["attempt_id"], run_id=run_id, status="running")
        except (OrderScopeDenied, Conflict):
            # Cancellation can win while POST is in flight. Save only the
            # late native identity for cleanup; do not restore source authority.
            self.store.record_cancelled_dispatch(attempt["attempt_id"], run_id)
            try:
                self.native.stop(attempt | {"run_id": run_id})
            except NativeUnavailable:
                pass
            raise

    def _reconcile(self, claim, source, attempt):
        try:
            status, payload = self.native.poll(attempt)
        except NativeUnavailable:
            # Read-only polling may retry against the same durable run ID.
            self.store.update_source(claim, source["source_id"], error_code="native_unavailable")
            return
        if status != 200:
            if status >= 500 or status == 429:
                self.store.update_source(claim, source["source_id"], error_code="native_unavailable")
            else:
                self._fail_attempt(claim, source, attempt, "model_run_unavailable", unknown=True)
            return
        if payload.get("session_id") not in {None, attempt["session_id"]}:
            self._fail_attempt(claim, source, attempt, "model_binding_mismatch", unknown=True)
            return
        state = payload.get("status")
        if state in {"queued", "running"}:
            self.store.update_source(claim, source["source_id"], error_code=None)
            return
        if state not in _TERMINAL:
            # Approval, unknown wire states and silent runtime changes do not
            # authorize additional tools, turns or a new model attempt.
            self._fail_attempt(claim, source, attempt, "model_run_unavailable", unknown=True)
            return
        # Re-read after polling: submit may have committed since this tick
        # loaded sources. Native prose is deliberately never parsed.
        current = next(s for s in self.store.sources(claim) if s["source_id"] == source["source_id"])
        if current.get("proposal") is not None:
            warning = None if state == "completed" and not payload.get("error") else "native_terminal_after_publication"
            self.store.update_attempt(claim, attempt["attempt_id"],
                                      status="completed" if warning is None else "failed", error_code=warning)
            self.store.update_source(claim, source["source_id"], status="complete", error_code=warning)
        elif state == "completed":
            self._fail_attempt(claim, source, attempt, "model_run_failed" if payload.get("error") else "model_proposal_missing")
        else:
            self._fail_attempt(claim, source, attempt, "model_run_" + state)

    def _cancel(self):
        for attempt in self.store.cancelled_attempts():
            if not attempt.get("run_id"):
                if attempt["status"] == "prepared":
                    self.store.mark_cancelled(attempt["attempt_id"])
                continue
            try:
                code, payload = self.native.poll(attempt)
                if code == 200 and payload.get("status") in _TERMINAL:
                    self.store.mark_cancelled(attempt["attempt_id"])
                elif code == 200:
                    # Stop acknowledgement is not proof the executor settled.
                    self.native.stop(attempt)
            except NativeUnavailable:
                pass

    def run_once(self, *, should_stop=lambda: False):
        self._cancel()
        if should_stop() or self.store.drained():
            return {"claimed": False, "status": "drained"}
        claim = self.store.claim(self.owner, lease_seconds=self.lease_seconds)
        if claim is None:
            return {"claimed": False}
        stopping = lambda: should_stop() or self.store.drained()
        try:
            sources = self.store.sources(claim)
            self._retry_dependencies(claim, sources)
            # Reattach an existing run even when submission has already saved
            # its proposal. Never let proposed rows mask an active native run.
            for source in sources:
                attempt = source.get("attempt")
                if attempt and source["status"] not in {"complete", "failed", "unsupported", "blocked"}:
                    # Recover a crash between persisting native terminal state
                    # and updating the source summary. No native write needed.
                    if attempt["status"] == "unknown":
                        self.store.update_source(claim, source["source_id"], status="blocked",
                                                 error_code=attempt.get("error_code") or "model_dispatch_unknown")
                    elif attempt["status"] in {"completed", "failed", "cancelled"}:
                        self.store.update_source(claim, source["source_id"],
                            status="complete" if source.get("proposal") is not None else "failed",
                            error_code=attempt.get("error_code") or
                            (None if source.get("proposal") is not None else "model_proposal_missing"))
                if attempt and attempt["status"] in {"prepared", "dispatching", "running"}:
                    if stopping():
                        break
                    if attempt.get("run_id"):
                        self._reconcile(claim, source, attempt)
                    else:
                        self._dispatch(claim, source, attempt, stopping)
                    return {"job_id": claim["job_id"], "status": "reconciled"}
            self._inventory(claim, sources, stopping)
            if stopping():
                return {"job_id": claim["job_id"], "status": "drained"}
            gate = self.store.gate(claim)
            if not gate["ready"]:
                if "inventory_pending" not in gate["blockers"]:
                    code = next(iter(gate["blockers"]), "inventory_incomplete")
                    for source in self.store.sources(claim):
                        if source["status"] not in {"complete", "failed", "unsupported", "blocked"}:
                            self.store.update_source(claim, source["source_id"], status="blocked", error_code=code)
                return {"job_id": claim["job_id"], "status": "inventory"}
            for source in self.store.sources(claim):
                if stopping():
                    break
                if source["status"] == "rendering":
                    self._render(claim, source, stopping)
            for source in self.store.sources(claim):
                if stopping():
                    break
                if source["status"] == "ready":
                    try:
                        attempt = self.store.create_attempt(claim, source["source_id"])
                    except Conflict:
                        # A different admitted job still owns the model slot.
                        break
                    self._dispatch(claim, source, attempt, stopping)
                    break
            return {"job_id": claim["job_id"], "status": "processed"}
        except (OrderScopeDenied, Conflict):
            return {"job_id": claim["job_id"], "status": "fenced"}
        finally:
            try:
                self.store.recompute(claim)
                self.store.release(claim)
            except (OrderScopeDenied, Conflict):
                pass


def run_once(store, native, *, should_stop=lambda: False, lease_seconds=120):
    return AnalysisWorker(store, native, lease_seconds=lease_seconds).run_once(should_stop=should_stop)


def main():
    from .intake_analysis import AnalysisStore
    from .intake_handoffs import IntakeHandoffs

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders-root", required=True, type=Path)
    parser.add_argument("--session-db", type=Path)
    parser.add_argument("--native-url", default="http://127.0.0.1:8642")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--lease-seconds", type=int, default=120)
    args = parser.parse_args()
    os.umask(0o077)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    native = NativeRunsClient(args.native_url, os.environ.get("API_SERVER_KEY", ""))
    store = AnalysisStore(IntakeHandoffs(args.orders_root, session_db=args.session_db))
    worker = AnalysisWorker(store, native, lease_seconds=args.lease_seconds)
    while not stop.is_set():
        try:
            result = worker.run_once(should_stop=stop.is_set)
        except Exception as exc:
            # No paths, document text, credentials or provider replies in logs.
            result = {"status": "worker_error", "error": type(exc).__name__}
        if result.get("claimed") is not False:
            print(json.dumps(result), flush=True)
        if args.once:
            return
        stop.wait(2)


if __name__ == "__main__":
    main()
