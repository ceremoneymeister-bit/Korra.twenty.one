# Calculator on Korra 21 — internal pilot

The calculator keeps the V9 arithmetic package in a separate Python environment
and uses the Korra 21 gateway and dashboard. Company rates, operator state and
provider credentials live in deployment volumes and are not image inputs.

## Reproduce the image

The recipe pins the Korra 21 engine digest and verifies the old CAD donor image
ID. The old image contributes only LibreDWG and its source/license directory.
The kernel dependency lock includes optional XML serialization dependencies
because these affect Excel bytes even when all monetary values agree.

From the repository root, build the interface, then the image:

```sh
cd web
npm run build
cd ..
python3 calculator/image/build.py --target pilot --tag korra-calculator:21-pilot-local
```

`calculator/image/pilot-receipt.json` records every copied source/asset hash and
the resulting image ID. It is a local build artifact. The engine revision in
the image label identifies the pinned base; the receipt identifies the overlay.

Preseed `ROOT/rates` with a verified pack including `_active.json`.
`deploy/bootstrap.py` prepares new state from explicitly supplied role prompts
and refuses to overwrite an existing configuration. Missing rates or role
prompts are rejected before writing any state. Configure the pilot's own
provider after bootstrap. `deploy/compose.yaml` is the server-specific example:
loopback dashboard 9231, shared profile API 8661, separate pilot directories.
Pass a verified immutable ID through `CALC_IMAGE` when starting or rolling back.

## Verification

Run repository Python tests with `scripts/run_tests.sh` and `HERMES_PYTHON`
pointing to the relevant interpreter, as required by the root AGENTS.md.
Kernel tests use the isolated calculation interpreter; gateway and dashboard
tests use the Korra interpreter. Frontend tests support `--maxWorkers=4` to
avoid timeout noise during concurrent builds.

`image/probe_stdio.py` runs with the engine interpreter against the isolated MCP
server. It checks interactive front access and rejection of an unscoped stage
role. Its orders are temporary and it makes zero model calls.

`image/probe_parity.py` runs with the kernel interpreter in both old and new
images. Its synthetic fixture is `image/fixtures/synthetic_pack.json`; pass an
explicit `--pack` when mounting the script separately. Compare the book digest,
cost, price, and Excel SHA-256. The fixture is a software migration case, not an
approved enterprise quotation.

## Current scope

- Four fixed roles, orders, rates, files and help in the Korra 21 interface.
- Trusted order capabilities for autonomous stage requests; a shared gateway
  key must be present in each profile. The interactive front does not require
  an autonomous order capability.
- General file writes are limited to inbox. Order folders use a dedicated,
  authenticated intake backed by the canonical registry and secure orders root.
  Settings and runtime secrets remain outside both surfaces.
- Telegram and automatic handoff are disabled in the initial deployment.
- The Files page accepts one folder including its Excel brief, drawings and
  subdirectories. Uploads stream three files at a time and can resume after an
  interruption by reselecting the same folder in the same browser tab. A draft
  appears in both Files and Orders only after every file has been stored.
  The intake supports up to 10,000 files, 100 MiB per file and 20 GiB per folder.
  Repeated completion returns the same order; a new upload with an existing
  folder name is rejected without overwriting it.
- Folder registration never invokes a model. Its full manifest lives in
  `folder_intake`; calculation `source_files` remain empty until document
  extraction and validation are implemented. Results are shown after that
  separate workflow is connected; intake alone does not start calculation.
- Incomplete uploads remain private and resumable. Automatic expiry/retention
  is not implemented in this pilot; cleanup must preserve all completed orders.
- Manual stage chats opened from an order do not yet carry its signed scope.
  Complete order-bound dispatch before testing the full production workflow.
- Existing company data is a baseline. New standards, commercial rules and
  approved real order examples require separate extraction and validation.

Deployment acceptance and credentials are recorded outside this repository.
An internal technical pilot does not establish commercial calculation accuracy.
