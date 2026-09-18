#!/usr/bin/env python3
"""Recalculate a workbook's formulas headlessly with LibreOffice.

openpyxl never computes formulas. This script shells out to `soffice`
(LibreOffice) to open the workbook, recalculate, and re-save it, so
cached formula results become available to `xlsx_read.py --data-only`
and `--formulas`.

Behavior:
  * soffice on PATH: converts the file to .xlsx in a temp dir (which
    recalculates all formulas) and replaces the original (or writes
    --out). Prints {"recalculated": true, ...} and exits 0.
  * soffice absent: prints {"recalculated": false, "reason": ...} with
    installation guidance and STILL exits 0 — callers can branch on the
    JSON instead of the exit code.

Note: LibreOffice recalculates .xlsx on load per its default
calculation settings; conversion re-saves with fresh cached values.

Usage:
  xlsx_recalc.py book.xlsx
  xlsx_recalc.py book.xlsx --out recalced.xlsx
  xlsx_recalc.py book.xlsx --timeout 120
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


_SHEET_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
ET.register_namespace("", _SHEET_NS["x"])


def invalidate_formula_caches(source, destination):
    """Copy an XLSX while clearing formula caches so Calc must recompute.

    A normal headless format conversion can trust a stale cached ``<v>`` and
    simply preserve it. Clearing only formula results in the disposable copy
    leaves formulas, values, formatting and the user's original untouched.
    """
    cleared = 0
    with zipfile.ZipFile(source) as source_archive, zipfile.ZipFile(
        destination, "w"
    ) as destination_archive:
        for member in source_archive.infolist():
            body = source_archive.read(member)
            if member.filename.startswith("xl/worksheets/") and member.filename.endswith(".xml"):
                root = ET.fromstring(body)
                for cell in root.findall(".//x:c", _SHEET_NS):
                    if cell.find("x:f", _SHEET_NS) is None:
                        continue
                    cached = cell.find("x:v", _SHEET_NS)
                    if cached is not None:
                        cell.remove(cached)
                    cleared += 1
                body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            destination_archive.writestr(member, body)
    return cleared


def count_cached(path):
    """Return formula/cached-value counts without another Python dependency.

    Cached results live beside formulas in worksheet XML. Reading them
    directly keeps this helper able to verify LibreOffice in a clean image;
    openpyxl remains the creation/editing dependency of the wider XLSX skill.
    """
    formulas = cached = 0
    with zipfile.ZipFile(path) as archive:
        sheets = sorted(
            name for name in archive.namelist()
            if name.startswith("xl/worksheets/") and name.endswith(".xml")
        )
        for name in sheets:
            root = ET.fromstring(archive.read(name))
            for cell in root.findall(".//x:c", _SHEET_NS):
                formula = cell.find("x:f", _SHEET_NS)
                if formula is None:
                    continue
                formulas += 1
                value = cell.find("x:v", _SHEET_NS)
                if value is not None and value.text not in (None, ""):
                    cached += 1
    return formulas, cached


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Recalculate .xlsx formulas headlessly via LibreOffice.")
    ap.add_argument("file", help="path to .xlsx file")
    ap.add_argument("--out", help="output path (default: replace input)")
    ap.add_argument("--timeout", type=int, default=180,
                    help="seconds to wait for soffice (default 180)")
    args = ap.parse_args(argv)

    src = Path(args.file).resolve()
    if not src.exists():
        print(json.dumps({"ok": False, "error": f"no such file: {src}"}),
              file=sys.stderr)
        return 1

    soffice = shutil.which("soffice")
    if not soffice:
        print(json.dumps({
            "ok": True, "recalculated": False,
            "reason": "LibreOffice (soffice) not found on PATH",
            "guidance": "Install LibreOffice (e.g. `apt install "
                        "libreoffice-calc` or `brew install --cask "
                        "libreoffice`), or open the file in Excel/"
                        "LibreOffice once and re-save it.",
        }, ensure_ascii=False))
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        prepared_dir = Path(tmp) / "input"
        prepared_dir.mkdir()
        prepared = prepared_dir / src.name
        formulas_to_recalculate = invalidate_formula_caches(src, prepared)
        proc = subprocess.run(
            [soffice, "--headless", "--calc", "--convert-to", "xlsx:Calc "
             "MS Excel 2007 XML", "--outdir", tmp, str(prepared)],
            capture_output=True, text=True, encoding="utf-8",
            timeout=args.timeout,
            env={"HOME": tmp, "PATH": Path(soffice).parent.as_posix()
                 + ":/usr/bin:/bin"})
        produced = Path(tmp) / (src.stem + ".xlsx")
        if proc.returncode != 0 or not produced.exists():
            diagnostic = "\n".join(
                part.strip() for part in (proc.stdout, proc.stderr) if part.strip()
            )
            print(json.dumps({"ok": False,
                              "error": "soffice conversion failed",
                              "diagnostic": diagnostic[-500:]}),
                  file=sys.stderr)
            return 1
        formulas, cached = count_cached(produced)
        if formulas != formulas_to_recalculate or cached != formulas_to_recalculate:
            print(json.dumps({"ok": False,
                              "error": "soffice changed formulas or left caches incomplete",
                              "formula_cells": formulas,
                              "with_cached_values": cached}),
                  file=sys.stderr)
            return 1
        dest = Path(args.out).resolve() if args.out else src
        shutil.copyfile(produced, dest)

    print(json.dumps({
        "ok": True, "recalculated": True, "output": str(dest),
        "formula_cells": formulas, "with_cached_values": cached,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        sys.exit(1)
