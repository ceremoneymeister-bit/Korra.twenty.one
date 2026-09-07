# Calculator document adapter

Standalone Linux CLI for the drawing trial and the future calculator document
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
`pypdfium2 5.13.0` / PDFium, and Pillow. It does not import PyMuPDF.

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
dumps. Its parent kills the process group on deadline. Both native parsers consume
the same **bytes verified before parsing**, so a file replaced at its original
path cannot change the revision being rendered. Input files are never rewritten.
No document is sent to an external service by this adapter.

Inventory checkpoints are atomically written every 25 pages and before extraction;
text checkpoints follow each selected page. If the native worker crashes or times
out, the parent retains the latest checkpoint and marks it incomplete with an error.
This is bounded local execution, not an operating-system security sandbox.

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
