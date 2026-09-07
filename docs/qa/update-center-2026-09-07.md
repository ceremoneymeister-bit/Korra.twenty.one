# Korra 21 release and update center, 2026-09-07

Implementation scope: host updater, acceptance before publication, and an
operator view in the existing cabinet. Operator rollout and publication are
separate from this implementation. Independent review passed; implementation and
the repeat update/rollback drill are complete.

Local implementation commits: host `2c23286bd2883a571f7ee26d2f7a9c2a6e58866b`,
CI `af673460126b26948cf1e62a7217441a5512e195`, companion cabinet
`61e9c4f017cdbd33cb336db163226bee5db67519`. Each commit includes only its owned paths.

Validation: **125 deployment tests (85 updater + 40 CI)** passed under the actual
non-root `ghrunner`; **237 cabinet tests** passed, with desktop/mobile browser
checks and read-only native helper verification against the real fixture.
Host/helper require Python 3.11+; validation used host Python 3.12.

## Host update contract

`docs/client-deploy/update.sh` provides update, status, dry-run and rollback.
The operation is durable and continues after the initiating SSH connection or
browser closes. A DATA lock, immutable request identity and expected-current
comparison prevent concurrent or stale operations from changing another target.

The host fetches and probes the exact image before native gateway drain, stops
only after known work finishes, snapshots the complete state, rehearses native
SessionDB migration/read/integrity on disposable DB-only copies without networking
or gateways, and recreates via the existing `up.sh`. The rehearsal rejects a
candidate older than the existing native schema and rechecks backup immutability.
Other stores remain covered by full snapshot and post-start acceptance; this is
not a general reverse-migration engine. Document dependencies are installed through native
`tools.lazy_deps` with exact versions. Acceptance checks native JSON, live gateway
control, served profiles, configured resource limits and a local model request.

Rollback restores both the previous image and its validated data snapshot.
Current credential stores and revocations move forward. New writes are preserved
in a complete post-update export, a changed-path list and the displaced DATA
directory. Unknown candidate activity or invalid backups fail explicitly.
Updated bundled skills are removed only when provenance and entire content match
the previous bundled version and the candidate no longer includes the skill.

`docs/client-deploy/README.md` documents commands, deployment constraints,
credential policy, resource preservation and recovery artifacts. Existing
configured launchers must retain the installation's own settings when the kit
is installed.

## Release artifact contract

`.github/workflows/docker.yml` builds once, exports that exact image with revision,
image ID and archive SHA256, runs offline clean/smoke acceptance in a separate
job, then loads and publishes the accepted archive. A receipt binds acceptance
to the same artifact; publication never rebuilds it. Build and test resources
are bounded on the shared runner. Deployment script/test changes trigger this
gate even though they live under `docs/`.

`ci-acceptance.py` creates a fresh tmpfs container with networking disabled;
owner data, real credentials, Telegram and schedules are absent. It checks
bootstrap templates, dashboard/API JSON, CLI/assets/skills and the expected
missing-provider SSE error. Publication credentials belong only to publishing
jobs. No workflow was dispatched or image published by this task.

## Cabinet integration

The companion change in `/opt/korra-cabinet` adds `/cab/operator`, a separate
operator login and exact per-installation permissions. It reuses enabled clients
in the existing registry and keeps products distinct. SQLite jobs and a detached
worker survive browser/SSH interruption; version, target and operator permissions
are revalidated before dispatch.

A dedicated SSH key uses a fixed forced command and root-owned target allowlist.
The existing tunnel-only key gains no command privileges. Requests cannot choose
arbitrary hosts, shell commands, data paths or Docker settings. Tar delivery binds
the approved archive checksum to its actual Docker image ID. Setup and rollout
instructions are in the cabinet's `maintenance/README.md`; production operator
configuration, keys and services have not been enabled by this task.

## Integration evidence and limits

The private, sanitized fixture contains root plus seven owner profile metadata
and state databases from a verified backup. It has no real integration credentials,
all Telegram is disabled, and schedule names are retained with jobs disabled.
All model responses come from a deterministic loopback fixture.

Completed initial drill: `02ea3ebc1905` → `0cd222a8fe20` succeeded with eight served
profiles, all 13 dependencies and model response. Manual rollback restored the
old image/resources and preserved newer credentials and exported owner writes.
A second update exposed credential classification/ownership and SQLite source
sidecar problems. Credential overlay now excludes dependency source/cache/venv
payloads, preserves canonical credential caches and `.secrets`, and copies the
owner/mode of new credential parent directories. SQLite normalization opens only
the copied database, so reading a backup cannot create sidecars in its source.

The corrected `failure-05` drill reached the new image, received an intentionally
failed model response, and automatically restored the previous image and data.
All eight native state DBs passed rehearsal. Rotated auth, exported new writes,
original resource limits and unchanged backup checksums were verified. A following
update passed dependency warmup but exposed panel-before-gateway readiness timing;
acceptance now waits within a deadline for native readiness and checks the model
once afterward. Target/resource mismatch still fails immediately.

Final `repeat-07`: a subsequent update after rollback succeeded, then manual
detached rollback also succeeded. New auth and a newly created nested `.secrets`
store kept their current values, UID and directory/file modes; new owner writes
remained in the export. Original files, resources, eight served profiles and model
response passed, the before manifest stayed unchanged, and no partial dependency
trees were carried into restored DATA. The immediate rollback receipt was pending.
The tested updater SHA256 is
`1e934415d46b669326a0a1172851e2a6f447777d34600848d83526ebf6ca545c`.

Real Docker archive CLI verification also passed using the saved `02ea3` archive,
expected current/target ID and SHA256. It correctly returned `already_current`
without restarting the fixture. Independent CI pack → accept → load verification
passed with an artifact-bound receipt.

The literal requested `6d005658` → latest → `6d005658` cycle could not finish:
the old image disappeared from the Docker store during parallel image work.
The original fixture had started and answered a model request; the updater later
refused before stop when it could no longer resolve that image. A recoverable
archive was unavailable. The full drill uses the retained CI image `02ea3` and
pinned candidate `0cd222` instead. Both are revision `c9c42ecb375c56f853576f0d4a06519deeb96f78`;
this is a deployment/rollback drill, not evidence of a cross-schema upgrade.

Before an operator pilot, retain the actual previous image plus its data backup;
an `IMAGE.prev` string alone does not provide rollback. The new updater pins both
operation images under separate local references.

Detailed receipts, independent review, screenshots, recovery checkpoint and
final commit list: `/root/Antigravity/_work/korra21-release-planning-20260907/REPORT.md`.
Production containers, Larisa's server, live keys/registry/service/nginx config,
push and publication were outside this task's execution scope.
The private temporary fixture is closed after acceptance; its logs, snapshots and
receipts remain available. Real SSH installation, GitHub publication and a browser
operation against an installed remote maintenance channel remain operator pilot
checks. Native schema rehearsal passed on schema 26; a real cross-schema transition
was not exercised by the two retained CI images.

## Learning decision

Regression coverage now follows repeated state transitions, including an update
after rollback, credentials created after the snapshot, source snapshot
immutability and stale requests after transport loss. Operational lessons and
remaining pilot work are preserved in the deployment documentation and handoff.
