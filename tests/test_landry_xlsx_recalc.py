"""Tests for landry.xlsx_recalc. The error-scanning logic is tested
offline (it's pure openpyxl reading); the actual `recalc()` LibreOffice
call is skipped when soffice isn't installed -- this environment doesn't
have it, which is the whole reason this module exists (see doctor.py)."""

import pytest

openpyxl = pytest.importorskip("openpyxl")

from landry.xlsx_recalc import _scan_errors, recalc, soffice_path


def _make_workbook(path, cells):
    wb = openpyxl.Workbook()
    ws = wb.active
    for coord, value in cells.items():
        ws[coord] = value
    wb.save(path)


def test_scan_errors_clean_workbook(tmp_path):
    p = tmp_path / "clean.xlsx"
    _make_workbook(p, {"A1": 1, "A2": 2, "A3": "hello"})
    result = _scan_errors(str(p))
    assert result["status"] == "success"
    assert result["total_errors"] == 0


def test_scan_errors_detects_excel_error_strings(tmp_path):
    p = tmp_path / "broken.xlsx"
    _make_workbook(p, {"A1": "#REF!", "B2": "#DIV/0!", "C3": "fine"})
    result = _scan_errors(str(p))
    assert result["status"] == "errors_found"
    assert result["total_errors"] == 2
    assert "#REF!" in result["error_summary"]
    assert result["error_summary"]["#REF!"]["locations"] == ["Sheet!A1"]


def test_scan_errors_counts_formulas_from_formula_view(tmp_path):
    p = tmp_path / "formulas.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = 1
    ws["A2"] = 2
    ws["A3"] = "=SUM(A1:A2)"
    wb.save(p)
    result = _scan_errors(str(p))
    assert result["total_formulas"] == 1


def test_recalc_missing_file():
    result = recalc("/nonexistent/path/to/nowhere.xlsx")
    assert "error" in result
    assert "does not exist" in result["error"]


@pytest.mark.skipif(soffice_path() is None, reason="soffice not installed")
def test_recalc_live_roundtrip(tmp_path):
    p = tmp_path / "live.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = 2
    ws["A2"] = 3
    ws["A3"] = "=A1+A2"
    wb.save(p)
    result = recalc(str(p), timeout=60)
    assert result.get("status") == "success", result
    check = openpyxl.load_workbook(p, data_only=True)
    assert check.active["A3"].value == 5
