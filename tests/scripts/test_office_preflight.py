"""Synthetic contracts for the exact-image Calc/Writer preflight."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest


@pytest.fixture
def office(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    sys.modules.pop("office_preflight", None)
    import office_preflight
    return office_preflight


def test_xlsx_fixture_starts_stale_and_accepts_independent_results(office, tmp_path):
    stale = tmp_path / "stale.xlsx"
    office.create_xlsx(stale)
    with pytest.raises(RuntimeError, match="cached value mismatch"):
        office.validate_xlsx(stale)

    recalculated = tmp_path / "recalculated.xlsx"
    office.create_xlsx(recalculated, cached=(60, 200, 260))
    report = office.validate_xlsx(recalculated)
    assert report == {
        "status": "PASS",
        "formula_cells": 3,
        "cached_values": {"C1": 60.0, "C2": 200.0, "C3": 260.0},
        "formulas_preserved": True,
        "formatting_preserved": True,
        "spreadsheet_errors": [],
    }


def test_writer_validation_requires_text_table_and_two_pages(office, tmp_path):
    pdf = tmp_path / "writer-source.pdf"
    pdf.write_bytes(b"%PDF-synthetic")
    text = (
        "Проверка Writer\nКириллица и структура документа сохранены.\n"
        "Показатель     Значение\nИтого     260\n\f"
        "Вторая страница — контроль вёрстки\nФинальная строка документа.\n"
    )
    assert office.validate_pdf(pdf, text, "Pages:           2\n")["page_layout"] is True
    with pytest.raises(RuntimeError, match="page-break layout"):
        office.validate_pdf(pdf, text, "Pages:           1\n")
    with pytest.raises(RuntimeError, match="table layout"):
        office.validate_pdf(pdf, text.replace("Итого     260", "Итого\n260"), "Pages:           2\n")


def test_preflight_refuses_launcher_only_environment(office, monkeypatch, tmp_path):
    monkeypatch.setattr(office.shutil, "which", lambda name: "/usr/bin/soffice" if name == "soffice" else None)
    with pytest.raises(RuntimeError, match="pdftotext, pdfinfo"):
        office.run_preflight(tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_count_cached_reads_ooxml_without_openpyxl(office, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(office.ENGINE / "skills/productivity/xlsx/scripts"))
    sys.modules.pop("xlsx_recalc", None)
    import xlsx_recalc
    workbook = tmp_path / "fixture.xlsx"
    office.create_xlsx(workbook, cached=(60, 200, 260))
    assert xlsx_recalc.count_cached(workbook) == (3, 3)
    invalidated = tmp_path / "invalidated.xlsx"
    assert xlsx_recalc.invalidate_formula_caches(workbook, invalidated) == 3
    assert xlsx_recalc.count_cached(invalidated) == (3, 0)
    cells, _ = office._cell_map(invalidated)
    assert cells["C1"]["formula"] == "SUM(A1:A3)"
