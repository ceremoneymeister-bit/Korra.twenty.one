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
changed source → stale; repeated worker loss → blocked. Retry is explicit for
cancelled/blocked work and retains accepted chunks. Completed/partial start is a
read of the existing job. A new reader/version/options fingerprint creates a
separate job. Native run completion and chat replies never publish domain results.

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
