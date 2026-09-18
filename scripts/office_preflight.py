#!/usr/bin/env python3
"""Offline exact-image proof for Calc recalculation and Writer PDF export.

The runner must provide a disposable network-none container and a new writable
``--out`` directory. The probe uses only synthetic data and the runtime UID.
It deliberately exercises the shipped XLSX recalculation helper; merely finding
the ``soffice`` launcher is not a capability check.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from xml.etree import ElementTree as ET


ENGINE = Path(__file__).resolve().parents[1]
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
SHEET_NS = {"x": MAIN_NS}


def _write_archive(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in members.items():
            archive.writestr(name, body.strip() + "\n")


def create_xlsx(path: Path, *, cached: tuple[int, int, int] = (-999, -999, -999)) -> None:
    """Create a styled workbook with dependencies, SUM and SUMPRODUCT."""
    sum_value, product_value, dependency_value = cached
    cells = f"""
      <row r="1">
        <c r="A1" s="1"><v>10</v></c><c r="B1"><v>2</v></c>
        <c r="C1" s="2"><f>SUM(A1:A3)</f><v>{sum_value}</v></c>
      </row>
      <row r="2">
        <c r="A2"><v>20</v></c><c r="B2"><v>3</v></c>
        <c r="C2" s="2"><f>SUMPRODUCT(A1:A3,B1:B3)</f><v>{product_value}</v></c>
      </row>
      <row r="3">
        <c r="A3"><v>30</v></c><c r="B3"><v>4</v></c>
        <c r="C3" s="2"><f>C1+C2</f><v>{dependency_value}</v></c>
      </row>
    """
    _write_archive(path, {
        "[Content_Types].xml": """
          <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
          <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
            <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
            <Default Extension="xml" ContentType="application/xml"/>
            <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
            <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
            <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
          </Types>
        """,
        "_rels/.rels": """
          <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
          <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
            <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
          </Relationships>
        """,
        "xl/workbook.xml": """
          <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
          <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
            <sheets><sheet name="Расчёт" sheetId="1" r:id="rId1"/></sheets>
            <calcPr calcMode="auto" fullCalcOnLoad="1" forceFullCalc="1"/>
          </workbook>
        """,
        "xl/_rels/workbook.xml.rels": """
          <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
          <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
            <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
            <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
          </Relationships>
        """,
        "xl/worksheets/sheet1.xml": f"""
          <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
          <worksheet xmlns="{MAIN_NS}">
            <dimension ref="A1:C3"/><sheetViews><sheetView workbookViewId="0"/></sheetViews>
            <sheetFormatPr defaultRowHeight="15"/><cols><col min="1" max="3" width="16" customWidth="1"/></cols>
            <sheetData>{cells}</sheetData>
          </worksheet>
        """,
        "xl/styles.xml": f"""
          <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
          <styleSheet xmlns="{MAIN_NS}">
            <numFmts count="1"><numFmt numFmtId="164" formatCode="# ##0.00 [$₽-419]"/></numFmts>
            <fonts count="2"><font><sz val="11"/><name val="Liberation Sans"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Liberation Sans"/></font></fonts>
            <fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill></fills>
            <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
            <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
            <cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>
            <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
          </styleSheet>
        """,
    })


def _cell_map(path: Path) -> tuple[dict[str, dict], ET.Element]:
    with zipfile.ZipFile(path) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        styles = ET.fromstring(archive.read("xl/styles.xml"))
    cells = {}
    for cell in sheet.findall(".//x:c", SHEET_NS):
        formula = cell.find("x:f", SHEET_NS)
        value = cell.find("x:v", SHEET_NS)
        cells[cell.attrib["r"]] = {
            "formula": formula.text if formula is not None else None,
            "value": value.text if value is not None else None,
            "style": int(cell.attrib.get("s", "0")),
        }
    return cells, styles


def validate_xlsx(path: Path) -> dict:
    cells, styles = _cell_map(path)
    expected = {"C1": 60.0, "C2": 200.0, "C3": 260.0}
    for ref, value in expected.items():
        try:
            actual = float(cells[ref]["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"missing cached value for {ref}") from exc
        if abs(actual - value) > 1e-9:
            raise RuntimeError(f"cached value mismatch for {ref}: {actual}")
        if not cells[ref]["formula"]:
            raise RuntimeError(f"formula lost from {ref}")
    formula_text = " ".join(cells[ref]["formula"].upper() for ref in expected)
    if "SUM(" not in formula_text or "SUMPRODUCT(" not in formula_text:
        raise RuntimeError("SUM or SUMPRODUCT formula was not preserved")
    xfs = styles.find("x:cellXfs", SHEET_NS)
    if xfs is None:
        raise RuntimeError("workbook styles missing")
    a1_style = list(xfs)[cells["A1"]["style"]]
    c1_style = list(xfs)[cells["C1"]["style"]]
    formatting = (
        int(a1_style.attrib.get("fontId", "0")) > 0
        and int(a1_style.attrib.get("fillId", "0")) > 0
        and int(c1_style.attrib.get("numFmtId", "0")) > 0
    )
    if not formatting:
        raise RuntimeError("source formatting was not preserved")
    return {
        "status": "PASS", "formula_cells": 3, "cached_values": expected,
        "formulas_preserved": True, "formatting_preserved": True,
        "spreadsheet_errors": [],
    }


def create_docx(path: Path) -> None:
    """Create a two-page Cyrillic Word document with a bordered table."""
    _write_archive(path, {
        "[Content_Types].xml": """
          <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
          <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
            <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
            <Default Extension="xml" ContentType="application/xml"/>
            <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
          </Types>
        """,
        "_rels/.rels": """
          <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
          <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
            <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
          </Relationships>
        """,
        "word/_rels/document.xml.rels": """
          <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
          <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>
        """,
        "word/document.xml": """
          <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
          <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
            <w:body>
              <w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr><w:t>Проверка Writer</w:t></w:r></w:p>
              <w:p><w:r><w:t>Кириллица и структура документа сохранены.</w:t></w:r></w:p>
              <w:tbl><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="8"/><w:left w:val="single" w:sz="8"/><w:bottom w:val="single" w:sz="8"/><w:right w:val="single" w:sz="8"/><w:insideH w:val="single" w:sz="8"/><w:insideV w:val="single" w:sz="8"/></w:tblBorders></w:tblPr><w:tblGrid><w:gridCol w:w="4500"/><w:gridCol w:w="4500"/></w:tblGrid>
                <w:tr><w:tc><w:p><w:r><w:t>Показатель</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Значение</w:t></w:r></w:p></w:tc></w:tr>
                <w:tr><w:tc><w:p><w:r><w:t>Итого</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>260</w:t></w:r></w:p></w:tc></w:tr>
              </w:tbl>
              <w:p><w:r><w:br w:type="page"/></w:r></w:p>
              <w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Вторая страница — контроль вёрстки</w:t></w:r></w:p>
              <w:p><w:r><w:t>Финальная строка документа.</w:t></w:r></w:p>
              <w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>
            </w:body>
          </w:document>
        """,
    })


def validate_pdf(pdf: Path, text: str, info: str) -> dict:
    if not pdf.is_file() or not pdf.read_bytes().startswith(b"%PDF-"):
        raise RuntimeError("Writer did not produce a PDF")
    required = ["Проверка Writer", "Кириллица и структура документа сохранены.",
                "Показатель", "Значение", "Итого", "260",
                "Вторая страница — контроль вёрстки", "Финальная строка документа."]
    if any(fragment not in text for fragment in required):
        raise RuntimeError("DOCX text or Cyrillic was lost in PDF export")
    pages = re.search(r"^Pages:\s+(\d+)$", info, re.MULTILINE)
    if not pages or int(pages.group(1)) != 2:
        raise RuntimeError("DOCX page-break layout was not preserved")
    if not re.search(r"Итого[ \t]+260", text):
        raise RuntimeError("DOCX table layout was not preserved")
    if "\f" not in text:
        raise RuntimeError("PDF text does not retain the two-page boundary")
    return {"status": "PASS", "pages": 2, "cyrillic_text": True,
            "table_layout": True, "page_layout": True}


def _run(argv: list[str], *, env: dict[str, str] | None = None, timeout: int = 180) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                            timeout=timeout, env=env)
    if result.returncode:
        detail = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        raise RuntimeError(f"{Path(argv[0]).name} failed: {detail[-500:]}")
    return result.stdout


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_preflight(output: Path) -> dict:
    required = {name: shutil.which(name) for name in ("soffice", "pdftotext", "pdfinfo")}
    missing = [name for name, path in required.items() if path is None]
    if missing:
        raise RuntimeError("missing runtime components: " + ", ".join(missing))
    output.mkdir(parents=True, exist_ok=False)
    source_xlsx = output / "formula-source.xlsx"
    recalculated = output / "formula-recalculated.xlsx"
    create_xlsx(source_xlsx)
    recalc_script = ENGINE / "skills/productivity/xlsx/scripts/xlsx_recalc.py"
    recalc = json.loads(_run([
        sys.executable, str(recalc_script), str(source_xlsx), "--out", str(recalculated),
        "--timeout", "180",
    ]))
    if recalc.get("ok") is not True or recalc.get("recalculated") is not True:
        raise RuntimeError("shipped XLSX helper did not recalculate the workbook")
    calc = validate_xlsx(recalculated)
    if recalc.get("formula_cells") != 3 or recalc.get("with_cached_values") != 3:
        raise RuntimeError("shipped XLSX helper reported incomplete formula caches")

    source_docx = output / "writer-source.docx"
    create_docx(source_docx)
    with tempfile.TemporaryDirectory(dir=output, prefix="lo-profile-") as profile:
        env = {"HOME": profile, "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
        _run([
            required["soffice"], "-env:UserInstallation=" + Path(profile).resolve().as_uri(),
            "--headless", "--writer", "--convert-to", "pdf", "--outdir", str(output),
            str(source_docx),
        ], env=env)
    pdf = source_docx.with_suffix(".pdf")
    text = _run([required["pdftotext"], "-layout", str(pdf), "-"])
    info = _run([required["pdfinfo"], str(pdf)])
    writer = validate_pdf(pdf, text, info)
    artifacts = {}
    for name, path in (("xlsx", recalculated), ("pdf", pdf)):
        artifacts[name] = {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}
    return {"status": "PASS", "calc": calc, "writer": writer, "artifacts": artifacts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if os.environ.get("KORRA_OFFICE_PREFLIGHT") != "isolated" or not Path("/.dockerenv").exists():
        parser.error("Use a disposable container with KORRA_OFFICE_PREFLIGHT=isolated and --network none")
    report = {"kind": "offline_office_preflight", "model_calls": 0, "status": "FAIL"}
    started = time.monotonic()
    try:
        report.update(run_preflight(args.out.resolve()))
    except Exception as exc:  # noqa: BLE001 - report a bounded synthetic probe failure
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
