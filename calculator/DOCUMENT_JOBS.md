# Document preparation S0–S1

The operator API accepts an immutable folder snapshot before returning a job.
Identity is `(order_id, document_set_revision, manifest_digest, pipeline_fingerprint)`.
The document revision changes only when the manifest changes; orders.revision is
untouched. Source IDs are opaque and scoped to the order and manifest. Empty
directories participate in the manifest. The old calculation source_files/BOM
remain governed by the costing workflow.

`DocumentJobs` stores snapshots, jobs, attempts, per-source chunks and typed
results in the existing registry.db. SQLite transactions serialize admission,
claim and publication. A claim returns an opaque capability only to the worker;
the database stores its SHA256. The capability binds job, attempt and live lease.
Cancellation, expired lease and source change fence late publication. Typed
results are immutable; same digest is idempotent and a different digest conflicts.
Completed chunks survive retry/restart. A deterministic format error is retained
as a result with a reason and is not repeatedly parsed.

States: queued → running → completed/partial; operator cancel → cancelled;
changed source → stale; repeated worker loss or a systemic reader failure
(`reader_unavailable`, `reader_dependency_missing`, `reader_version_mismatch`,
`invalid_output_directory`, `output_directory_not_empty`) → blocked. Completed/partial
start is a read of the existing job. A new reader/version/options fingerprint
creates a separate job. Native run completion and chat replies never publish
domain results.

Per-source failures. A document-level reader outcome (`worker_timeout`,
`worker_exit`, `invalid_reader_artifact`) is not a typed result: the worker records
it in the additive `document_failures` table (immutable row per generation, same
capability/fence/lease/snapshot checks and provenance as publish, sanitized and
size-bounded) and continues with the remaining sources of the same pass. Such a
source is shown as `status: failed`, `accepted: false`, with a `failure` object
(code, generation, attempt, `retry_authorized`) and no `result_sha256`; its history
is readable via `DocumentJobs.failures(job_id, source_id)`. It is excluded from
later passes until an explicit operator retry, so restarts never re-read a poison
document automatically. Whatever the parser inventoried before the limit hit is
kept with the failure as an unverified `checkpoint`: validated like a publication
(source SHA, reader fingerprint/options, schema, server binding; never `complete`,
never verified, no human receipt), its temporary image discarded, and bounded to
512 KiB by dropping the largest non-core sections with `checkpoint_truncated` /
`checkpoint_dropped` recorded explicitly. A malformed partial is recorded as
`checkpoint_rejected` and does not block the pass. Checkpoints live only in the
failure history; the status view never loads them and they never become results. `finish` refuses while any source is still pending and
returns `partial` while retryable failures remain; `completed` requires every
source to hold a complete accepted result.

Coverage: `files_accounted` counts accepted typed results (complete, partial,
failed-deterministic, unsupported); `files_failed` counts sources awaiting
operator retry; `files_pending` counts sources not yet read or authorized for
re-reading. `file_accounting_complete` is true only when every source is accepted.
`lease_until`/`lease_expired` describe the live lease of a running job truthfully.

Retry: `retry(job_id)` (CLI `document-job-retry`, POST `/retry` with `{}`) authorizes
every recorded failure and re-queues a cancelled/blocked job or a partial job with
unaccepted sources. `retry(job_id, source_id=...)` (`{"source_id": ...}` /
`--source-id`) authorizes exactly one failed source; a source of another job, an
accepted source or a never-failed source is rejected. A stale job (even after the
manifest is reverted) and a running job with a live lease reject retry with a
Conflict. Cancel withdraws every retry permission no pass has consumed, so a later
addressed retry reads exactly its one source; if a cancelled/blocked job still holds
never-read sources, an addressed retry is refused (Conflict) in favour of the
job-level retry. Accepted results and failure history are never rewritten; a later
success sits next to its failure history.

Inventory coverage, selected text/crops, composition acceptance, position
calculation readiness and quote readiness are separate. `empty_composition()`
contains questions and null quantity, no assumed production quantity. S1 has no
human-receipt/model proposal writer. S2–S3 add the interaction and acceptance.

Operator routes (dashboard authentication + front profile):

- POST /api/calc/orders/{order_id}/document-jobs: `{}` for inventory;
  render uses `{command:"render", source_id, options:{page,dpi,crop}}`.
- GET /api/calc/orders/{order_id}/document-jobs: job summaries.
- GET /api/calc/document-jobs/{job_id}: pollable status and coverage.
- GET /api/calc/document-jobs/{job_id}/result: source/result references.
- GET /api/calc/document-jobs/{job_id}/sources/{source_id}: typed observation.
- GET .../sources/{source_id}/download: exact snapshot bytes, verified SHA.
- GET .../sources/{source_id}/image: published PNG, verified SHA.
- POST .../{job_id}/cancel or /retry: preserve accepted artifacts.

Downloads use one verified kernel read. The operator stream commands emit a
bounded JSON metadata line followed by the exact bytes described by that line;
the HTTP router validates the header before returning a response. A typed error
before the header remains an HTTP error, never a downloaded JSON error file.
Response and body iterator share ownership of the subprocess cleanup, including
disconnects before the first body chunk. Cleanup discards stdout in bounded
chunks while waiting, so a paused pipe cannot keep a killed child unreaped.
End-of-stream checks detect truncated/extra bytes and checksum or process errors;
they cannot retract bytes already sent. Integrity originates in the single
SHA-verified kernel read. Existing source/image URLs and front authorization
remain unchanged.

The independently supervised deterministic worker has no provider credentials
and performs no model calls. PDF/XLSX parsers run in bounded disposable processes
in their own document virtualenv; kernel arithmetic dependencies are unchanged.
Its CLI supports drain and single-tick execution for isolated acceptance. A drain
stops new claims; an in-progress source can publish before the worker yields.

Scoped native /v1/runs requires durable idempotency and an Idempotency-Key;
scope is captured into execution context, not stored in sessions or response
payloads. S1 does not dispatch autonomous model jobs. S2 must add a dedicated
document-only model tool surface with the same domain capability checks before
enabling model execution.

Rollback: stop/drain workers first. Removing only the additive `document_*`
tables in a disposable test database restores the pre-S0 schema; production
rollback retains tables and artifacts and runs the previous image. Never restore
an old orders database over later uploads. Acceptance must compare original
order state hashes, verify old code reads the upgraded database, and test
fresh migration after a test-only downgrade.
