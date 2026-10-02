"""Tests for landry.xlsx_recalc. The error-scanning logic is tested
offline (it's pure openpyxl reading); the actual `recalc()` LibreOffice
call is skipped when soffice isn't installed -- this environment doesn't
have it, which is the whole reason this module exists (see doctor.py)."""

import pytest

openpyxl = pytest.importorskip("openpyxl")

from landry.xlsx_recalc import EXCEL_MAX_ROW_PT, _scan_errors, clamp_row_heights, recalc, soffice_path


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


# --- Excel's 409.5pt row-height ceiling ------------------------------------
# Excel reports a workbook holding a taller row as corrupt ("We found a problem
# with some content ... recover?"); LibreOffice, openpyxl and every other check
# accept it. Found 2026-10-02 on Current Positions row 52 (941pt, since 9/30).

def test_clamp_row_heights_caps_only_the_rows_above_the_ceiling(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Monitor & Recheck Triggers"            # the ampersand is XML-escaped in workbook.xml
    ws["A1"], ws["B7"] = "kept", 42
    ws.row_dimensions[5].height = 941.25
    ws.row_dimensions[7].height = 409.5                 # exactly the ceiling: legitimate
    ws.row_dimensions[8].height = 100
    other = wb.create_sheet("Plain")
    other["A1"] = "also kept"
    other.row_dimensions[3].height = 1200
    p = str(tmp_path / "tall.xlsx")
    wb.save(p)

    clamped = clamp_row_heights(p)
    assert sorted((c["sheet"], c["row"], c["was"]) for c in clamped) == [
        ("Monitor & Recheck Triggers", 5, 941.25), ("Plain", 3, 1200.0)]

    wb2 = openpyxl.load_workbook(p)
    assert wb2["Monitor & Recheck Triggers"].row_dimensions[5].height == EXCEL_MAX_ROW_PT
    assert wb2["Plain"].row_dimensions[3].height == EXCEL_MAX_ROW_PT
    assert wb2["Monitor & Recheck Triggers"].row_dimensions[8].height == 100          # untouched
    assert (wb2["Monitor & Recheck Triggers"]["A1"].value, wb2["Monitor & Recheck Triggers"]["B7"].value) == ("kept", 42)
    assert wb2["Plain"]["A1"].value == "also kept"
    assert clamp_row_heights(p) == []                                                 # idempotent


def test_clamp_row_heights_leaves_a_clean_workbook_byte_identical(tmp_path):
    p = tmp_path / "clean.xlsx"
    _make_workbook(p, {"A1": 1, "B2": "x"})
    before = p.read_bytes()
    assert clamp_row_heights(str(p)) == []
    assert p.read_bytes() == before


@pytest.mark.skipif(soffice_path() is None, reason="soffice not installed")
def test_recalc_caps_the_tall_row_libreoffice_itself_produces(tmp_path):
    from openpyxl.styles import Alignment
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.column_dimensions["A"].width = 6                 # a long wrapped note in a narrow column
    ws["A1"] = "word " * 2500
    ws["A1"].alignment = Alignment(wrap_text=True)
    ws["B1"] = "=1+1"
    p = str(tmp_path / "note.xlsx")
    wb.save(p)
    result = recalc(p)
    assert result["status"] == "success"
    assert [(c["sheet"], c["row"]) for c in result["clamped_rows"]] == [(ws.title, 1)]
    assert result["clamped_rows"][0]["was"] > EXCEL_MAX_ROW_PT
    assert openpyxl.load_workbook(p).active.row_dimensions[1].height == EXCEL_MAX_ROW_PT
    assert openpyxl.load_workbook(p, data_only=True).active["B1"].value == 2          # cached values survive the patch
