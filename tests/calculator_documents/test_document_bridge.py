"""Real bridge subprocess checks across PDF, XLSX, malformed and bounded input."""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
import time
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

pytest.importorskip("defusedxml")
pytest.importorskip("pdf_inspector")
pytest.importorskip("pypdf")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "calculator/documents"))
import bridge
from pypdf import PdfWriter

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"


def workbook(*, worksheets=None, extras=None, relationship=None, workbook_xml=None):
    worksheets = worksheets or [f'<worksheet xmlns="{MAIN}"><sheetData><row r="1">'
        '<c r="A1" t="s"><v>0</v></c><c r="B1"><v>1.500</v></c>'
        '<c r="C1"><f>B1*2</f><v>3.000</v></c><c r="D1"><f>1+1</f></c>'
        '</row></sheetData></worksheet>']
    elements = ''.join(f'<sheet name="Sheet {i}" sheetId="{i}" r:id="rId{i}" state="hidden"/>'
                       for i in range(1, len(worksheets) + 1))
    relations = ''.join(f'<Relationship Id="rId{i}" Type="{RELS}/worksheet" Target="worksheets/sheet{i}.xml"/>'
                        for i in range(1, len(worksheets) + 1))
    members = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "xl/workbook.xml": workbook_xml or f'<workbook xmlns="{MAIN}" xmlns:r="{RELS}"><sheets>{elements}</sheets></workbook>',
        "xl/_rels/workbook.xml.rels": relationship or f'<Relationships xmlns="{PACKAGE}">{relations}</Relationships>',
        "xl/sharedStrings.xml": f'<sst xmlns="{MAIN}"><si><t>Part -01</t></si></sst>',
        **{f"xl/worksheets/sheet{i}.xml": text for i, text in enumerate(worksheets, 1)},
        **(extras or {}),
    }
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return output.getvalue()


def inspect(tmp_path, data, *, relative="nested/book.xlsx", metadata=None, command="inspect", options=None):
    path = tmp_path / "opaque-blob"
    path.write_bytes(data)
    source = {"source_id": "source_001", "sha256": hashlib.sha256(data).hexdigest(),
              "bytes": len(data), "relative_path": relative, **(metadata or {})}
    return bridge.read_document(path, source, tmp_path / "attempt", command=command,
                                options=options, python=sys.executable)


def pdf_data(pages=2):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_xlsx_raw_values_formula_cache_and_cell_provenance_are_distinct(tmp_path):
    result = inspect(tmp_path, workbook())
    assert result["status"] == "complete", result
    sheet = result["sheets"][0]
    assert sheet["state"] == "hidden"
    cells = {cell["cell"]: cell for cell in sheet["cells"]}
    assert cells["A1"]["value"] == "Part -01"
    assert cells["B1"]["value"] == "1.500"
    assert cells["C1"]["formula"]["text"] == "B1*2"
    assert cells["C1"]["value"] is None
    assert cells["C1"]["cached_value"] == {"value": "3.000", "value_type": "n", "present": True}
    assert cells["D1"]["cached_value"]["present"] is False
    assert cells["D1"]["cached_value"]["value"] is None
    assert cells["B1"]["provenance"] == {"source_id": "source_001", "source_sha256": result["source"]["sha256"],
        "sheet": 1, "sheet_name": "Sheet 1", "cell": "B1"}
    assert result["formulas_evaluated"] is False
    assert result["external_links_followed"] is False
    assert result["source"]["sha256_verified"] is True
    assert result["use_for_calculation"] is False
    assert result["coverage"]["cells_read"] == 4
    assert result["coverage"]["cells_complete"] is True
    assert str(tmp_path) not in json.dumps(result)


def test_pdf_inventory_and_selected_text_are_separate(tmp_path):
    result = inspect(tmp_path, pdf_data(), relative="drawing.pdf", options={"pages": "2"})
    assert result["status"] == "complete", result
    assert result["coverage"]["pages_total"] == 2
    assert result["coverage"]["pages_inventoried"] == 2
    assert result["coverage"]["inventory_complete"] is True
    assert result["coverage"]["selected_text_pages"] == [2]
    assert result["coverage"]["text_pages_read"] == [2]


def test_render_exports_checked_relative_image_and_source_revision(tmp_path):
    result = inspect(tmp_path, pdf_data(), relative="drawing.pdf", command="render", options={"page": 2})
    assert result["status"] == "complete", result
    assert result["image"]["path"] == "page-0002.png"
    payload = (tmp_path / "attempt" / result["image"]["path"]).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == result["image"]["sha256"]
    assert len(payload) == result["image"]["bytes"]
    assert result["coverage"]["rendered_pages"] == [2]
    assert result["coverage"]["inventory_complete"] is False
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.parametrize("relative", ["Thumbs.db", "old.xls", "macro.xlsm", "archive.zip"])
def test_unsupported_files_have_verified_sha_and_explicit_records(tmp_path, relative):
    result = inspect(tmp_path, b"unsupported data", relative=relative)
    assert result["status"] == "unsupported"
    assert result["source"]["sha256_verified"] is True
    assert result["errors"][0]["code"] == "unsupported_format"


@pytest.mark.parametrize("metadata,code", [({"sha256": "0" * 64}, "source_sha256_mismatch"),
                                          ({"bytes": 1}, "source_bytes_mismatch")])
def test_unsupported_revision_is_checked_before_format_status(tmp_path, metadata, code):
    result = inspect(tmp_path, b"unsupported data", relative="Thumbs.db", metadata=metadata)
    assert result["status"] == "failed"
    assert result["source"]["sha256_verified"] is False
    assert result["errors"][0]["code"] == code


@pytest.mark.parametrize("relative,data", [("bad.pdf", b"%PDF-1.4\nbroken"), ("bad.xlsx", b"not a zip")])
def test_corrupt_supported_source_is_an_explicit_failure(tmp_path, relative, data):
    result = inspect(tmp_path, data, relative=relative)
    assert result["status"] == "failed"
    assert result["coverage"]["inventory_complete"] is False
    assert result["errors"]


def test_corrupt_sheet_does_not_hide_later_sheet(tmp_path):
    result = inspect(tmp_path, workbook(worksheets=["bad XML",
        f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1"><v>7</v></c></row></sheetData></worksheet>']))
    assert result["status"] == "partial", result
    assert result["coverage"]["inventory_complete"] is True
    assert result["coverage"]["cells_complete"] is False
    assert result["coverage"]["sheets_inventoried"] == 2
    assert result["sheets"][0]["error"] == "xlsx_corrupt_xml"
    assert result["sheets"][1]["cells"][0]["value"] == "7"


@pytest.mark.parametrize("options,code", [({"max_cells": 2}, "xlsx_cell_limit"),
    ({"max_expanded_bytes": 100}, "xlsx_expansion_limit"),
    ({"max_member_bytes": 100}, "xlsx_member_limit"),
    ({"max_zip_members": 2}, "xlsx_member_count_limit")])
def test_xlsx_resource_limits_preserve_explicit_incompleteness(tmp_path, options, code):
    result = inspect(tmp_path, workbook(), options=options)
    assert result["complete"] is False
    assert result["errors"][0]["code"] == code
    assert result["coverage"]["cells_complete"] is False


def test_xlsx_truncation_does_not_claim_complete_read(tmp_path):
    result = inspect(tmp_path, workbook(), options={"max_total_text_chars": 2})
    assert result["status"] == "partial"
    assert result["coverage"]["text_truncated"] is True
    assert result["coverage"]["inventory_complete"] is True


def test_sparse_sheet_dimension_is_not_expanded_into_grid(tmp_path):
    sheet = f'<worksheet xmlns="{MAIN}"><dimension ref="A1:XFD1048576"/><sheetData><row r="1048576"><c r="XFD1048576"><v>7</v></c></row></sheetData></worksheet>'
    result = inspect(tmp_path, workbook(worksheets=[sheet]), options={"max_cells": 1})
    assert result["status"] == "complete", result
    assert result["coverage"]["cells_read"] == 1
    assert result["sheets"][0]["cells"][0]["cell"] == "XFD1048576"


def test_phonetic_annotations_are_separate_from_rich_inline_value(tmp_path):
    sheet = f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><r><t>Part</t></r><r><t> -01</t></r><rPh sb="0" eb="4"><t>phonetic</t></rPh></is></c></row></sheetData></worksheet>'
    result = inspect(tmp_path, workbook(worksheets=[sheet, f'<worksheet xmlns="{MAIN}"><sheetData/></worksheet>']),
                     options={"max_cells": 1})
    assert result["status"] == "complete", result
    assert result["sheets"][0]["cells"][0]["value"] == "Part -01"
    assert result["sheets"][1]["cells_complete"] is True


def test_missing_worksheet_identity_is_corrupt_not_complete(tmp_path):
    result = inspect(tmp_path, workbook(workbook_xml=f'<workbook xmlns="{MAIN}"><sheets/></workbook>'))
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "xlsx_invalid_workbook"


def test_external_sheet_is_explicitly_unsupported_without_following_link(tmp_path):
    relation = f'<Relationships xmlns="{PACKAGE}"><Relationship Id="rId1" Type="{RELS}/worksheet" Target="http://127.0.0.1:1/private" TargetMode="External"/></Relationships>'
    result = inspect(tmp_path, workbook(relationship=relation))
    assert result["status"] == "partial"
    assert result["sheets"][0]["status"] == "unsupported"
    assert result["sheets"][0]["error"] == "xlsx_external_sheet"
    assert result["external_links_followed"] is False


@pytest.mark.parametrize("extras,code", [({"xl/vbaProject.bin": b"macro"}, "xlsx_macros_unsupported"),
                                         ({"../outside.xml": "data"}, "xlsx_unsafe_archive")])
def test_macros_and_unsafe_package_paths_are_rejected(tmp_path, extras, code):
    result = inspect(tmp_path, workbook(extras=extras))
    assert result["complete"] is False
    assert result["errors"][0]["code"] == code
    assert result["source"]["sha256_verified"] is True


def test_xml_entity_is_rejected_and_never_reads_local_file(tmp_path):
    secret = tmp_path / "must-not-read"
    secret.write_text("PRIVATE_SENTINEL")
    hostile = f'<!DOCTYPE workbook [<!ENTITY exploit SYSTEM "file://{secret}">]><workbook xmlns="{MAIN}">&exploit;</workbook>'
    result = inspect(tmp_path, workbook(workbook_xml=hostile))
    assert result["errors"][0]["code"] == "xlsx_unsafe_xml"
    assert "PRIVATE_SENTINEL" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.parametrize("options", [{"timeout": True}, {"max_cells": 0}, {"max_cells": 100001},
                                     {"python": "untrusted"}, {"pages": "1; touch /tmp/pwn"}])
def test_invalid_options_fail_before_any_process_or_output(tmp_path, options):
    result = inspect(tmp_path, workbook(), options=options)
    assert result["errors"][0]["code"] == "invalid_options"
    assert not (tmp_path / "attempt").exists()


def test_fingerprint_changes_with_reading_recipe_and_canonicalizes_defaults():
    assert bridge.reader_fingerprint() == bridge.reader_fingerprint(options={"timeout": 60.0})
    assert bridge.reader_fingerprint() != bridge.reader_fingerprint(options={"pages": "2"})
    assert bridge.reader_fingerprint() != bridge.reader_fingerprint(options={"max_cells": 500})


def test_attempt_directory_is_never_overwritten(tmp_path):
    result = inspect(tmp_path, workbook())
    assert result["complete"] is True
    before = (tmp_path / "attempt/inspect.json").read_bytes()
    second = inspect(tmp_path, workbook())
    assert second["errors"][0]["code"] == "output_directory_not_empty"
    assert (tmp_path / "attempt/inspect.json").read_bytes() == before


def test_real_bridge_timeout_kills_worker_and_retains_verified_checkpoint(tmp_path, monkeypatch):
    isolated = tmp_path / "worker"
    isolated.mkdir()
    shutil.copyfile(bridge.CLI, isolated / "cli.py")
    source = (ROOT / "calculator/documents/adapter.py").read_text()
    source += "\ndef inspect_document(*args, **kwargs):\n    import time\n    time.sleep(20)\n"
    (isolated / "adapter.py").write_text(source)
    monkeypatch.setattr(bridge, "CLI", isolated / "cli.py")
    start = time.monotonic()
    result = inspect(tmp_path, pdf_data(), relative="test.pdf", options={"timeout": 1})
    assert time.monotonic() - start < 10
    assert result["status"] == "failed"
    assert result["source"]["sha256_verified"] is True
    assert result["errors"][-1]["code"] == "worker_timeout"
