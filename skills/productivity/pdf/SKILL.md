---
name: pdf
description: "PDF: Korra design, create, read, merge, forms, OCR."
version: 1.2.0
author: Nous Research
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [pdf, documents, korra, html, design, forms, ocr, text-extraction, reportlab, pypdf, pdfplumber, pymupdf, marker]
    category: productivity
    related_skills: [docx, xlsx, powerpoint]
---

# PDF Skill

Create new documents from editable **Korra HTML/CSS templates by default**. Read
[Korra document design](references/korra-documents.md), choose a starter, adapt it to
the task, print with the installed Chromium, and review the actual PDF. A requested
client brand, supplied design, or official form takes precedence. When the user's
company name is already known from the task, profile or provided materials, pass
`--company "Company name"` to `init`: it replaces the Korra logo with the company
name and updates the title and running footer. Do not ask for known information
again or infer a company from unrelated people; if unknown, keep Korra unless
the task requires clarification. A supplied company logo can replace the text
in the editable HTML. Preserve editable
sources alongside the PDF; do not use the plain JSON/ReportLab route as the default
for a designed proposal, report, or presentation.

Also create PDFs from structured specs, build and fill AcroForm forms, extract
text/tables/metadata, merge/split/rotate/watermark/stamp pages, export page images,
manage metadata and attachments, and encrypt/decrypt. Read the matching reference:

- **Scanned/image-only PDFs and OCR** (pymupdf fast path, marker-pdf quality path, scripts/extract_pymupdf.py + scripts/extract_marker.py): [OCR extraction](references/ocr-extraction.md).
- **Editing text inside an existing PDF via natural-language prompts**: [nano-pdf editing](references/nano-pdf-editing.md).

## When to Use

- Generate a proposal, report, brief, guide, invoice, presentation, or multi-page document as PDF, including requests in Russian.
- Build a fillable AcroForm (text/checkbox/radio/dropdown) from a JSON spec, linting the layout first.
- Pull text, tables (JSON/CSV), metadata, or form-field values out of a PDF.
- Merge, split, rotate, extract page subsets, watermark, stamp text/images at coordinates, bookmark, or compress PDFs.
- Export pages as PNGs for visual review or for OCR hand-off; set/clear document metadata; add/extract file attachments.
- Fill or flatten AcroForm forms; encrypt or decrypt with passwords.
- For scanned/image-only PDFs, follow the OCR reference. For editable PPTX output, also use `powerpoint`.

## Prerequisites

- Korra templates: Python 3.10+ (standard library only) and installed Chrome/Chromium 131+; the bundled browser cache is discovered automatically. Local Onest font and OFL license ship with the templates. No automatic downloads.
- Existing PDF operations and JSON creation: `pypdf`, `reportlab`, `pdfplumber`:
  `python -m pip install pypdf reportlab pdfplumber`
- Optional, for page rasterization (`pdf_page_image.py`, overlay rendering): `python -m pip install pypdfium2`, or poppler's `pdftoppm` on PATH. Scripts fall back pypdfium2 → pdftoppm and report `{"rendered": false, "missing": [...]}` (exit 0) when neither exists.
- Each helper script checks imports lazily and prints an install hint if a dependency is missing.

## How to Run

All helpers live in `scripts/` and are argparse CLIs — run them with the `terminal` tool; every one supports `--help`. They read/write JSON strictly as UTF-8, print JSON results to stdout, and exit non-zero on failure.

```bash
python scripts/pdf_style.py init proposal --output-dir /absolute/task/document
# When the company is known, use --company during init:
python scripts/pdf_style.py init report --company 'Моя компания' --output-dir /absolute/task/company-report
# Edit document.html and theme.css in that new directory, then:
python scripts/pdf_style.py render /absolute/task/document/document.html -o /absolute/task/document.pdf
python scripts/pdf_create.py spec.json -o out.pdf         # plain structured PDF, not the design default
python scripts/pdf_make_form.py formspec.json -o form.pdf # build fillable AcroForm from JSON spec
python scripts/pdf_form_layout.py formspec.json           # lint form layout BEFORE building
python scripts/pdf_form_layout.py formspec.json --render-overlay boxes.png [--pdf form.pdf]
python scripts/pdf_read.py doc.pdf --text                 # per-page text (JSON)
python scripts/pdf_read.py doc.pdf --tables --csv-dir t/  # tables to JSON + CSV files
python scripts/pdf_read.py doc.pdf --meta                 # metadata, page sizes, encrypted/scanned flags
python scripts/pdf_read.py form.pdf --fields              # form fields: name, type, value
python scripts/pdf_merge.py a.pdf b.pdf -o merged.pdf [--bookmarks]
python scripts/pdf_split.py doc.pdf --pages 1-3,7 -o part.pdf [--rotate 90]
python scripts/pdf_fill_form.py form.pdf --fields-json values.json -o filled.pdf [--flatten]
python scripts/pdf_secure.py doc.pdf --encrypt -o enc.pdf --user-password your-password
python scripts/pdf_secure.py enc.pdf --decrypt -o dec.pdf --password your-password
python scripts/pdf_watermark.py doc.pdf --stamp mark.pdf -o stamped.pdf [--under]
python scripts/pdf_stamp.py doc.pdf -o out.pdf --text "DRAFT" --x 150 --y 400 \
    --font-size 60 --rotation 45 --opacity 0.3 --color "#cc0000" [--pages 1-3]
python scripts/pdf_stamp.py doc.pdf -o out.pdf --image sig.png --x 400 --y 60 --width 120
python scripts/pdf_page_image.py doc.pdf --pages 1-3 --dpi 150 --out-dir imgs/
python scripts/pdf_meta.py doc.pdf --set-meta --title "T" --author "A" -o out.pdf
python scripts/pdf_meta.py doc.pdf --attach data.csv -o out.pdf
python scripts/pdf_meta.py doc.pdf --list-attachments | --extract-attachments dir/
```

## Quick Reference

| Task | Tool | Command / API |
|---|---|---|
| Designed PDF; editable HTML | [Korra kit](references/korra-documents.md) + Chromium | `pdf_style.py init brief/proposal/report/guide/presentation --output-dir DIR`, then `render HTML -o PDF` |
| Plain structured PDF / compatibility | reportlab platypus | `pdf_create.py spec.json -o out.pdf` |
| Build fillable form | reportlab acroForm | `pdf_make_form.py formspec.json -o form.pdf` |
| Lint form layout / overlay image | pure python + PIL | `pdf_form_layout.py formspec.json [--render-overlay o.png]` |
| Per-page text | pdfplumber | `pdf_read.py f.pdf --text` |
| Tables → JSON/CSV | pdfplumber | `pdf_read.py f.pdf --tables` |
| Metadata / sizes / encrypted / scanned | pypdf + pdfplumber | `pdf_read.py f.pdf --meta` |
| Merge (+ outline) | pypdf | `pdf_merge.py a.pdf b.pdf -o m.pdf` |
| Split / extract / rotate | pypdf | `pdf_split.py f.pdf --pages 2-5 --rotate 90` |
| List / fill / flatten form | pypdf | `pdf_read.py --fields`, `pdf_fill_form.py` |
| Encrypt / decrypt (AES-256) | pypdf | `pdf_secure.py --encrypt/--decrypt` |
| Watermark / stamp PDF page | pypdf | `pdf_watermark.py f.pdf --stamp w.pdf` |
| Stamp text/image at coordinates | reportlab + pypdf | `pdf_stamp.py f.pdf --text "Sign here" --x 400 --y 60` |
| Pages → PNG (review / OCR hand-off) | pypdfium2 or pdftoppm | `pdf_page_image.py f.pdf --pages 1-3 --out-dir imgs/` |
| Set/clear metadata, attachments | pypdf | `pdf_meta.py --set-meta / --attach / --extract-attachments` |
| Compress content streams | pypdf | `pdf_split.py f.pdf --pages 1-N --compress` |

## Procedure

1. **Inspect existing input.** For an existing PDF, run `pdf_read.py file.pdf --meta`. Check `encrypted` (if true, decrypt first with `pdf_secure.py --decrypt`) and `likely_scanned_pages`. If pages are image-only, export them with `pdf_page_image.py --pages <scanned> --dpi 300 --out-dir imgs/` and follow [OCR extraction](references/ocr-extraction.md) — do not report empty text as "no content".
2. **Create.** For a new document, read [Korra document design](references/korra-documents.md), copy a starter with [pdf_style.py](scripts/pdf_style.py), edit local HTML/CSS, and render. Replace demonstration content with verified facts; the report starter contains clearly marked fictitious data. Use `terminal` or `patch` for source edits. Extract the final PDF text and inspect every rendered page with `vision_analyze` before delivery. For an explicitly plain structured PDF or an existing JSON workflow, use `pdf_create.py` (elements: `heading`, `paragraph`, `table`, `image`, `pagebreak`; optional title/author metadata).
3. **Extract.** `--text` gives a JSON list of per-page strings; `--tables` gives row arrays per page and can also emit CSV files. Read results with `read_file`; never eyeball a binary PDF directly.
4. **Manipulate.** `pdf_merge.py` concatenates and can add one bookmark per source file; `pdf_split.py` handles page ranges (1-based, e.g. `1-3,5,9-`), rotation in 90° steps, and `--compress`. Watermark by preparing a single-page stamp PDF (e.g. via `pdf_create.py`) and overlaying it with `pdf_watermark.py`; for one-liner stamps ("sign here", diagonal DRAFT, corner labels) use `pdf_stamp.py` with text or an image at explicit coordinates.
5. **Build forms.** Write one form-spec JSON (fields with `label_box`/`entry_box` in PDF points — see [forms](references/forms.md)), lint it with `pdf_form_layout.py` and fix every reported problem, optionally review the `--render-overlay` PNG with `vision_analyze`, then build with `pdf_make_form.py` and confirm with `pdf_read.py --fields`.
6. **Fill forms.** List fields (`--fields`) to learn exact names and types, write a UTF-8 JSON of `{"FieldName": "value"}` using `terminal` or `patch` (checkboxes accept `true`/`false`; radio/choice values must match the field's export options), then `pdf_fill_form.py`. Re-read with `--fields` to confirm values landed.
7. **Metadata & attachments.** `pdf_meta.py --set-meta` writes Title/Author/Subject/Keywords (DocInfo); `--clear-meta` drops them; `--attach`/`--list-attachments`/`--extract-attachments` round-trip embedded files.
8. **Secure.** Encrypt with distinct user/owner passwords and AES-256. To remove a password you know, `--decrypt` writes an unencrypted copy.
9. **Verify** (see below) before reporting success.

## Pitfalls

- **Design is not guaranteed by printing**: inspect the PDF, not just the HTML. Check Cyrillic, the last paragraph, table continuation, empty pages, and slide overflow. Never hide overflow or turn all text into a screenshot to make a page fit.
- **Templates are editable references**: remove irrelevant blocks, adapt the brand when requested, and never present the starter's demonstration text or invented metrics as real findings. Keep customizations in the task copy so bundled-skill updates preserve them.
- **Scanned PDFs**: empty `extract_text()` plus page images means there is no text layer. Route to `references/ocr-extraction.md`; do not fabricate text.
- **Flattening limits**: `pdf_fill_form.py --flatten` uses pypdf's flatten support, which converts widget appearances into page content. It is reliable for plain text fields and checkboxes but can drop or misrender exotic widgets (rich text, custom appearance streams, some radio groups). Verify the flattened output visually with `vision_analyze`; for bulletproof flattening use an external renderer (e.g. Ghostscript or `pdftoppm`+reassembly) as a fallback.
- **NeedAppearances**: after filling, viewers only render values if appearance streams exist. The fill script sets the AcroForm `NeedAppearances` flag so conforming viewers regenerate them; some minimal viewers ignore it — flatten if display fidelity matters.
- **Non-Latin form values**: values are stored correctly (UTF-16), but the field's default font may lack glyphs, so a viewer can show blanks even though the data round-trips. Verify with `--fields`, not just visually.
- **Compression expectations**: `--compress` only deflates content streams. Typical savings are 0–20%; it does nothing for PDFs dominated by images or already-compressed streams. It is not a substitute for image downsampling (Ghostscript territory).
- **Permission flags don't enforce**: owner-password permission bits (no-print, no-copy) are polite requests that viewers may honor; any library (including pypdf) can read and strip them. Only the user password actually gates content via encryption. Never present permission flags as security.
- **Table extraction is heuristic**: pdfplumber detects tables from ruling lines/word alignment; borderless or merged-cell tables may need `table_settings` tuning or manual cleanup.
- **Page indexing**: helper CLIs take 1-based pages; pypdf APIs are 0-based. The scripts convert — don't double-convert.
- **Rotated stamp text extraction**: pdfplumber's line grouping scrambles rotated glyphs (a 45° "DRAFT" extracts as stray letters); verify rotated stamps with `pypdf`'s `extract_text()` or a rendered image instead.
- **Radio groups**: reportlab needs ≥2 `radio()` widgets per group, fills need the slashed export value (`"/red"`), and flatten fidelity is worst for radios — see `references/forms.md`.
- **Metadata scope**: `pdf_meta.py` writes the classic DocInfo dictionary only; embedded XMP metadata (if any) is left untouched and may show different values in some viewers.
- **PDF/A is out of scope**: pypdf/reportlab cannot produce or validate conformant PDF/A. If archival conformance is required, run Ghostscript via the `terminal` tool (e.g. `gs -dPDFA=2 -dPDFACompatibilityPolicy=1 -sColorConversionStrategy=UseDeviceIndependentColor -sDEVICE=pdfwrite -o out.pdf in.pdf` with a suitable ICC profile) and validate with veraPDF — both are external installs, and the result still needs validation, not assumption.
- Rotation must be a multiple of 90; encrypted inputs must be decrypted before any other operation.

## Verification

- For a new designed PDF: follow [the kit's acceptance steps](references/korra-documents.md). Review text and all pages, then deliver the PDF with editable HTML/CSS/assets. Successful printing alone is not visual acceptance.
- After create/merge/split: `pdf_read.py out.pdf --meta` — confirm `page_count`, and per-page `rotation` when you rotated.
- After extraction: check the JSON is non-empty and spot-check a known string or cell.
- Form design loop: `pdf_form_layout.py spec.json` must exit 0; then `--render-overlay boxes.png --pdf form.pdf` and review the PNG with `vision_analyze` (red = entry boxes with field names, blue = label boxes) asking about overlaps, misalignment, and labels detached from their fields. Iterate spec → lint → overlay until clean.
- After building a form: `pdf_read.py form.pdf --fields` lists every spec field with the right type and options.
- After form fill: `pdf_read.py filled.pdf --fields` and compare values (exact match, including non-ASCII).
- After stamping: re-extract text (pypdf for rotated stamps) or render the page with `pdf_page_image.py` and inspect with `vision_analyze`.
- After metadata/attachment edits: `pdf_read.py --meta` / `pdf_meta.py --list-attachments`, and re-extract an attachment to byte-compare.
- After encrypt: `--meta` shows `"encrypted": true` and opening without a password fails; after decrypt, text extraction matches the original.
- For anything visual (watermarks, flattened forms), render and inspect with `vision_analyze`.
