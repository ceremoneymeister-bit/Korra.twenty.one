# Calculator document adapter

Standalone Linux CLI and dependency-free bridge for the calculator document
worker. It adds no CORE tools, costing-kernel dependencies, model calls, OCR
models, queue, UI, or MCP endpoints. Its outputs are observations with source
links; numeric facts remain unverified.

## Isolated setup

Use a dedicated worktree, Python 3.12+, and the pinned dependencies here:

```bash
bash calculator/documents/setup.sh --test
```

The script creates a document-owned `.venv` in that worktree. It refuses an
existing unrelated `.venv`. Runtime-only setup omits `--test`; `pypdf` and pytest
are test dependencies only. Runtime uses `pdf-inspector 1.17.0`,
`pypdfium2 5.13.0` / PDFium, Pillow, and `defusedxml 0.7.1` for XLSX.
It does not import PyMuPDF or openpyxl.

The existing PDF skill's `_raster.py` uses the same PDFium backend but accepts a
path and a whole page. This adapter needs verified bytes, native crops and exact
coordinate conversion, so it calls pypdfium2 directly without changing the skill.

## CLI

```bash
.venv/bin/python calculator/documents/cli.py inspect /private/order.pdf \
  --sha256 EXPECTED_64_HEX_SHA256 --out /private/trial/inspect --pages 1,3-5

.venv/bin/python calculator/documents/cli.py render /private/order.pdf \
  --sha256 EXPECTED_64_HEX_SHA256 --out /private/trial/page-1 \
  --page 1 --crop 0 0 405 342 --dpi 180
```

`--out` must be new or empty. A repeated invocation must choose a new directory.
Stdout contains a short JSON result with the artifact path. Exit 0 means the
requested processing completed; it never means the numbers were verified.
Exit 2 means failure/incomplete processing; 124 means the worker exceeded its
wall-clock deadline. Arguments rejected before work starts use argparse stderr.

`inspect` accepts `.xlsx` with the same SHA256 requirement. `--document-type`
selects `pdf`, `xlsx`, or `unsupported` when a resolver supplies an opaque blob
filename; `--expected-bytes` also checks the immutable manifest size. Unsupported
formats still pass the bounded bytes/SHA checks before receiving a format error.

`inspect.json` contains:

- Source path, expected SHA256, verified SHA256, byte count and parser versions.
- A PDFium inventory of **every page**: dimensions, intrinsic rotation,
  MediaBox/CropBox/visible box, native character count, route and per-page errors.
- `inventory_complete` and `uninspected_page_count`; selected text pages do not
  limit inventory coverage. `--max-pages` failure reports the discovered total
  and leaves inventory explicitly incomplete.
- Optional source classification from pdf-inspector. Its confidence concerns
  source classification, may use sampling/early exit, and cannot establish
  numeric accuracy or inventory completeness.
- Bounded Markdown for `--pages` (default first eight), original character counts,
  truncation flags and per-page extraction errors. It reads existing text only.
- `verification.numeric_facts="unverified"`, `numeric_confidence=null`, and
  `use_for_calculation=false`.

All public page numbers are **one-based**. The pinned
`classify_pdf_bytes.pages_needing_ocr` uses zero-based indices; these are preserved
as explicitly labelled raw values and converted. The pinned
`extract_pages_markdown_bytes` takes zero-based indices and returns zero-based
`PageMarkdown.page`; those are checked before mapping results. The latter API's
aggregate `pages_needing_ocr` uses a different convention, so it is not consumed.
The mixed-page test covers the actual package behavior.

`render.json` links a `page-NNNN.png` SHA256 to the verified source revision,
page, crop, dimensions and parser versions. Coordinates are explicit:

- `--crop x0 y0 x1 y1`: displayed page **after intrinsic rotation**, top-left
  origin, x right/y down, PDF canvas units. Full page is the default.
- Source PDF coordinates: unrotated PDFium page coordinates with page-box
  offsets, obtained from `FPDF_DeviceToPage`, x right/y up.
- PNG coordinates: pixel edges, origin top-left, x right/y down.
- Four 3×3 affine matrices map between image/PDF and image/displayed-page frames.
  Apply a matrix to a column vector `[x, y, 1]`. The PDF transforms come from the
  native renderer and include rotation, page boxes and rounded crop offsets.

DPI is nominal at 72 canvas units/inch. PDFium does not expose PDF UserUnit here;
neither page units nor pixels establish the physical scale of a part. PDFium may
change antialiasing for glyphs clipped at crop edges; tests verify interior content,
ink bounds and known source coordinates. Keep enough margin around annotations.

## Bounds and failure behavior

Default limits: 100 MiB input, 1,000 pages, 32 selected text pages, 8,000 characters
per page / 40,000 total, 16 million rendered pixels, 60-second worker deadline.
CLI limits have hard ceilings (200 MiB, 2,000 pages, 20,000/200,000 characters,
40 million pixels, 180 seconds); rendering DPI is 50–600. Larger sheets should be
read as an overview plus bounded crops.

The disposable worker has a 2 GiB address-space limit, CPU limit and disabled core
dumps, plus a 64 MiB output-file limit. Its parent kills the process group on deadline. Both native parsers consume
the same **bytes verified before parsing**, so a file replaced at its original
path cannot change the revision being rendered. Input files are never rewritten.
No document is sent to an external service by this adapter.

Inventory checkpoints are atomically written every 25 pages and before extraction;
text checkpoints follow each selected page. If the native worker crashes or times
out, the parent retains the latest checkpoint and marks it incomplete with an error.
This is bounded local execution, not an operating-system security sandbox.

## Kernel bridge contract (schema 2)

The kernel imports `bridge.py`, which uses only the Python standard library.
Pass the document interpreter explicitly from the worker configuration. Fallback
is a document-owned repository `.venv` (ownership marker required), then the
current interpreter. There is no automatic installation or costing-venv mutation.

```python
from bridge import read_document, reader_fingerprint

result = read_document(
    resolved_blob_path,
    {"source_id": "source_opaque_id", "sha256": expected_sha256,
     "bytes": expected_bytes, "relative_path": "subfolder/drawing.pdf"},
    fresh_attempt_directory,
    command="inspect", options={"pages": "1,3"}, python=document_python,
)
```

The caller owns order authorization, resolving IDs to trusted regular files,
lease/fencing, cancellation, retries, publication, and access to artifacts.
`output_dir` must be new or empty; an exclusive `.attempt` marker rejects reuse.
The bridge invokes the existing CLI `_worker` entry point directly, with a clean
environment and one process group, and enforces its wall deadline. The worker
applies the same resource limits as the standalone CLI. Checkpoints remain
private; only the normalized return value should be published.

Always-present fields:

| Field | Meaning |
|---|---|
| `schema_version`, `command`, `document_type` | `2`, `inspect`/`render`, `pdf`/`xlsx`/`unsupported` |
| `source` | Allowlisted `source_id`, lowercase `sha256`, `bytes`, `relative_path`, `sha256_verified`; invalid metadata returns an empty source |
| `status`, `complete` | `complete` / `partial` / `failed` / `unsupported`; completion covers the requested reader operation only |
| `reader` | `version`, deterministic `fingerprint`, canonical `options`, and actual `parser_versions` when available |
| `verification`, `use_for_calculation` | Numeric observations remain `unverified`, confidence null, calculation use always false |
| `coverage` | Separate inventory, selected text, cells and image coverage; before parsing it only guarantees `inventory_complete=false` |
| `errors` | Stable `{code,message,page?,sheet?}`; messages are generated from allowlisted code syntax, without parser/local-path diagnostics |

`coverage` after parsing has `inventory_complete`, `pages_total`,
`pages_inventoried` (successful page metadata), `pages_accounted` (all inventory
records including errors), `selected_text_pages`, `text_pages_read`,
`text_truncated`, `sheets_total`, `sheets_inventoried`, `cells_read`,
`cells_complete`, `rendered_pages`. Unavailable totals are null. Complete PDF
inventory does not imply all-page text/vision reading. A render/crop reports the
single rendered page and keeps inventory incomplete. Truncated text produces
`status=partial`, even when all requested parser calls returned successfully.

Format data is retained as `inventory`, `text_pages`, `sheets`, optional page
geometry/transforms and classification. `image` contains only a checked child
basename (`page-NNNN.png`), SHA256, byte count, width and height. The caller must
perform its own immutable publication and protected image delivery. Public
results contain no machine-generated absolute source/output paths; document
text remains untrusted data and can itself contain path-like strings.

`canonical_options(command, options)` rejects unknown fields and invalid types
or bounds before creating an attempt. Options are CLI equivalents with
underscores: common timeout/byte/page/XLSX limits, inspect text/page-selection
limits, or render `page`, `dpi`, `crop`, `max_pixels`. Render requires `page`.
`reader_fingerprint(command, options)` includes the reader contract/version,
pinned parser versions and canonical options; `READER_FINGERPRINT` is the default
inspect recipe. Runtime parser-version mismatch is a failed result. The job cache
must also bind the exact source revision and operation; returned status alone
must never create human verification or semantic scope approval.

Typical errors: `source_sha256_mismatch`, `source_bytes_mismatch`,
`source_size_limit`, `unsupported_format`, `unsupported_command`,
`invalid_options`, `output_directory_not_empty`, `reader_unavailable`,
`reader_dependency_missing`, `reader_version_mismatch`, `worker_timeout`,
`worker_exit`, `invalid_reader_artifact`. PDF errors retain their existing codes.
Failures with retained inventory/sheet/image observations are `partial`; an
unreadable source is `failed`. Whole-document unsupported formats/features are
`unsupported`; unsupported individual sheets have explicit per-sheet records.

## XLSX observations

The reader supports transitional OOXML `.xlsx` worksheets. It reads verified ZIP
bytes in memory; it never extracts archive members to the filesystem. DTDs,
entities, duplicate/unsafe ZIP paths, encrypted members and macro-enabled
packages are rejected. Internal worksheet relationships are resolved inside the
package. External targets, chart/macro sheets and unsupported namespaces receive
explicit errors. Hyperlinks, external workbook links, formulae, macros, images
and embedded programs are never executed or fetched.

Each `sheets` entry keeps its one-based index, exact name, visibility state,
status, declared dimension and cells. The declared grid dimension does not
allocate empty rows or columns. Each stored cell has an exact A1 coordinate,
`value_type`, literal `value`, separate `formula` metadata and `cached_value`,
style index, truncation flag and source ID/SHA/sheet/cell provenance. A formula
cell's literal `value` is null; `cached_value.present=false` records a missing
cache. Formula text and its cache are never substituted for each other or
recalculated. Shared formula type/index/range are retained without inventing
expanded formula text. Rich strings are joined in order; phonetic annotations
remain excluded from the stored cell value.

Numbers stay exact XML strings (including decimal zeros). Styles are not
interpreted as units, dates or display formatting. Merged regions, drawings,
comments and business semantics are outside this basic cell reader. Thus
`cells_complete` means all stored worksheet cells were visited, while
`text_truncated` separately identifies incomplete exported cell content; neither
asserts complete visual/semantic interpretation of the workbook.

Defaults / hard ceilings: 128 / 256 sheets, 20,000 / 100,000 stored cells,
5,000 / 10,000 ZIP members, 128 / 512 MiB total expanded bytes, 64 / 128 MiB per
member. Shared string count is bounded by the cell limit. All XML parsers run
under the disposable process's memory/CPU/wall/file limits. Cell/formula exports
share the configured total character budget. Every sheet is accounted for or
retains an explicit document/sheet error; per-sheet checkpoints plus every 1,000
stored cells preserve bounded progress. A sheet-count limit reports the full
discovered count and explicit uninspected count.

## Validation

```bash
scripts/run_tests.sh tests/calculator_documents/test_document_cli.py \
  --confcutdir=tests/calculator_documents -q --file-retries 0
```

Use the canonical runner. `--confcutdir=...` keeps unrelated CORE autouse fixtures
out of this dependency-isolated suite; the runner still clears the environment
and isolates each test file, and this suite supplies a temporary HERMES_HOME.
In CORE-only environments the suite skips when its optional document dependencies
are absent; the dedicated setup installs them and runs the real checks.
Tests use synthetic PDFs and real native parsers/CLI subprocesses: SHA mismatch,
page bounds, complete inventory with selected text, existing OCR that remains
unverified, empty-page routing, limits, CropBox plus all four rotations, coordinate
round trips and a real worker deadline with an injected stall. Private customer
PDFs and model-call artifacts belong to the separate drawing trial.
