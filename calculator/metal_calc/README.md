# metal_calc MCP

Typed stdio MCP server for the drawing-calculator contour. It exposes an exact
legacy surface plus role-scoped V9 workflow tools, and keeps file access,
geometry, arithmetic, registry writes, human decisions and XLSX rendering
outside free-form model reasoning.

## Runtime layout

Install in an isolated image-owned virtual environment. Do not install these
pins into the Korra engine environment; in particular, `numpy==2.5.2` is the
verified cadkit runtime and can conflict with engine packages.

```text
/opt/metal-calc/venv/                 root:root, immutable
/opt/metal-calc/lib/cadkit.py         root:root, immutable
/etc/metal-calc/order_schema.json     root:root, read-only
/etc/metal-calc/rates/*.json          root:root, read-only
/opt/data/cache/documents             read-only to metal_calc
/opt/data/orders                      writable registry and order data
/opt/data/delivery                    writable MEDIA staging only
```

Build/install:

```bash
python3 -m venv /opt/metal-calc/venv
/opt/metal-calc/venv/bin/pip install --require-virtualenv --no-deps \
  /image-build-context/metal_calc
```

Production image assembly should populate a wheelhouse from the exact pins in
`pyproject.toml` and install with `--no-index --require-hashes`. The application
does not download packages or access the network at runtime.

Required environment:

```text
METAL_CALC_IMAGE_DIGEST=sha256:<64 lowercase hex>
METAL_CALC_CACHE_ROOT=/opt/data/cache/documents
METAL_CALC_ORDERS_ROOT=/opt/data/orders
METAL_CALC_DELIVERY_ROOT=/opt/data/delivery
METAL_CALC_DELIVERY_PUBLIC_ROOT=/opt/data/delivery
METAL_CALC_RATES_ROOT=/etc/metal-calc/rates
METAL_CALC_ORDER_SCHEMA=/etc/metal-calc/order_schema.json
METAL_CALC_CADKIT_PATH=/opt/metal-calc/lib/cadkit.py
```

`METAL_CALC_IMAGE_DIGEST` has no default and is deliberately fail-closed. Image
assembly/deploy injects the exact digest of the deployed composite image; the
server does not infer or invent a digest from a mutable tag.

The four trusted roots are opened with `O_NOFOLLOW`; all untrusted descendants
are opened relative to root dirfds with Linux `openat2(RESOLVE_BENEATH |
RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS)`. A kernel without `openat2` fails
startup rather than falling back to string path checks.

## Supervised quantities

`order_upsert` can submit `manual_facts_propose`, but cannot approve a fact. A
quantity proposal contains only `part_quantity`, its `target_source_file_id`, a
positive integer `value`, unit `pcs`, and a Telegram/document `evidence_ref`.
The server assigns its id, digest, pending status and timestamp.

An operator approves outside the model/tool surface:

```bash
/opt/metal-calc/venv/bin/metal-calc-admin approve-fact \
  --order order-123 --fact fact_0123456789abcdef --approved-by operator:login
```

The admin utility sets the approval timestamp server-side. A pending proposal
passed to `calculate_quote` returns `abstain=true`; only an approved fact can
change effective part quantities. Every priced part requires exactly one
approved quantity fact: cadkit's geometry instance count is never treated as an
RFQ quantity. The immutable geometry stays unchanged; `calculation` stores the
exact effective quantities, effective mass, and sorted applied fact IDs.
Approving a corrected quantity atomically marks the previous approved proposal
for that part `superseded`, removes it from the current order projection, and
keeps its audit row. Superseded IDs can never be used for a calculation.
`metal-calc-admin` is not an MCP tool and is not included in `platform_toolsets`.

Customer identity can be corrected only before the first attachment is
ingested. Once intake has started, it is frozen so registry data, calculations,
and rendered artifacts cannot name different customers.

## Human-only workflow gates

Mechanical QA is computed by code. Technological/commercial verdicts, a formal
route return and completion of `manual_review_required` items are available
only through `metal-calc-admin` and the authenticated dashboard API. They are
absent from every MCP/model-visible role surface.

A manual-review receipt must cover every current item in order, with non-empty
evidence and reference fields. The receipt is signed by its content digest and
bound to the exact book digest, manual-item digest, calculation revision, pack
revision and pack fingerprint. Rebuilding the book or changing the active pack
makes the old receipt invalid. Commercial PASS and a customer `FINAL` report
fail closed until a current receipt exists.

## Rate pack v1

There are no client rates in this repository. A sanitized, client-approved pack
is mounted as `<revision>.json`:

```json
{
  "schema_version": 1,
  "revision": "client-v1",
  "pricing_policy_revision": "pricing-v1",
  "materials": {
    "steel-s235-4": {
      "grade": "S235",
      "thickness_mm": "4",
      "density_kg_m3": "7850",
      "rate_rub_per_kg": "1.00",
      "rate_source": {"kind": "client_canon", "ref": "approved-rate-card", "as_of": "2026-08-13"}
    }
  },
  "operations": [
    {
      "code": "laser_cut",
      "category": "cut",
      "quantity_from": "cut_length_m",
      "rate_rub": "1.00",
      "rate_source": {"kind": "client_canon", "ref": "approved-rate-card", "as_of": "2026-08-13"}
    }
  ],
  "offcut": {
    "kind": "percent_of_area",
    "percent": "0",
    "source": {"kind": "client_canon", "ref": "approved-policy", "as_of": "2026-08-13"}
  },
  "pricing": {
    "margin_basis": "on_cost",
    "margin_percent": "0",
    "vat_included": false,
    "vat_rate_pct": "0",
    "valid_days": 14,
    "rounding": "none"
  }
}
```

Supported quantity sources are `mass_kg`, `cut_length_m`, `pierces`,
`net_area_m2`, `outer_area_m2`, and `parts_pcs`. No expressions are evaluated.
Rates must be positive; a free operation is omitted from the pack instead of
being represented by an accidental zero. Percent values are bounded to 0..100
and booleans must be JSON booleans, not truthy strings.
All values are parsed through `Decimal`; line subtotals use bankers rounding to
kopecks. If VAT is included in the customer price, margin is still calculated
from the net-of-VAT selling price.

The published pricing policy chooses customer-total rounding explicitly:
`none` (kopecks), `half_up` (commercial rounding to a ruble), `bankers`,
`up_10`, or `up_100`. `half_up` and `bankers` intentionally differ at exact
50-kopeck boundaries.

The XLSX and registry keep `net_total_rub`, `vat_rate_pct`, `vat_amount_rub`, and
gross `total_rub` separately. The invariant is `gross = net + VAT`; economic
margin is `net - cost`.

Ingested inputs and order XLSX artifacts use deterministic destinations and
publish-or-adopt semantics. If the process stops after an atomic file publish
but before the SQLite commit, retry verifies exact size and SHA-256 and adopts
the file. A mismatched existing file remains fail-closed. XLSX metadata and ZIP
timestamps are canonicalized to make the rendered artifact byte-stable.

## Development verification

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

Tests use synthetic rates and drawings only. They include a real cadkit DXF
integration test when pinned CAD dependencies are installed.
