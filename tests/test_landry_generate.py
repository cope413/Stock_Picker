"""Offline tests for Phase C step 1 (landry.generate: Journal regenerated from
landry.db), plus the Journal pieces of models / migrate_to_db / xlsx_io it
rests on. Synthetic workbooks only, except the last test, which round-trips
the repo's own tracked workbook and skips if it isn't there."""

import datetime
import os
import sqlite3

import openpyxl
import pytest
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.filters import AutoFilter
from openpyxl.worksheet.table import Table

from landry import generate, models, xlsx_io
from landry.audit import check_schema_reference, check_table_refs
from landry.generate import GenerateError, generate_journal, verify_journal
from landry.migrate_to_db import migrate
from landry.xlsx_recalc import soffice_path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _skeleton(capacity=5, prefill=()):
    """A minimal Journal tab shaped like the live one: merged title row,
    header row 2, a Table over A2:C<2+capacity>, print area to match."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Journal"
    ws["A1"] = "Freeform notes log"
    ws.merge_cells("A1:C1")
    for col, header in enumerate(("Date", "Ticker", "Notes"), start=1):
        ws.cell(row=2, column=col, value=header)
    for i, (d, label, notes) in enumerate(prefill):
        r = 3 + i
        ws.cell(row=r, column=1, value=d)
        ws.cell(row=r, column=2, value=label)
        ws.cell(row=r, column=3, value=notes)
    last = 2 + capacity
    ws.add_table(Table(displayName="JournalTable", ref=f"A2:C{last}",
                       autoFilter=AutoFilter(ref=f"A2:C{last}")))
    ws.print_area = f"A1:C{last}"
    return wb, ws


def _db(tmp_path, entries=()):
    conn = models.init_db(str(tmp_path / "t.db"))
    for date, label, notes in entries:
        models.journal_add(conn, date, label, notes)
    conn.commit()
    return conn


ENTRIES = [
    ("2026-08-07", "SFM", "Sold -- Rule 3 Avoid"),
    ("2026-12-01", "QTR-REVIEW-2", "future-dated placeholder, written second"),
    ("2026-09-30", "UNH,ACN", "written third, dated earlier than the row above"),
]


# ----------------------------------------------------------------- models --

def test_journal_label_is_free_text_not_a_foreign_key(tmp_path):
    conn = _db(tmp_path)
    models.journal_add(conn, datetime.date(2026, 10, 1), "DCA-CATCHUP-1", "x")
    models.journal_add(conn, datetime.datetime(2026, 10, 1), "AZN,INTU", "y")
    models.journal_add(conn, "2026-10-02", None, "z")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM tickers").fetchone()[0] == 0
    rows = models.journal_rows(conn)
    assert [r["label"] for r in rows] == ["DCA-CATCHUP-1", "AZN,INTU", None]
    assert [r["date"] for r in rows] == ["2026-10-01", "2026-10-01", "2026-10-02"]


def test_iso_date_normalizes_and_rejects_garbage():
    assert models.iso_date(datetime.datetime(2026, 10, 1)) == "2026-10-01"
    assert models.iso_date(datetime.datetime(2026, 10, 1, 9, 30)) == "2026-10-01T09:30:00"
    assert models.iso_date("2026-10-01") == "2026-10-01"
    with pytest.raises(TypeError):
        models.iso_date(20261001)
    with pytest.raises(ValueError):
        models.iso_date("not a date")


def test_migrate_refuses_an_existing_database(tmp_path):
    db = tmp_path / "landry.db"
    db.write_bytes(b"not empty")
    with pytest.raises(FileExistsError):
        migrate("no-such-workbook.xlsx", str(db))
    assert db.read_bytes() == b"not empty"      # untouched


# --------------------------------------------------------------- xlsx_io --

def _journal_file(tmp_path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Journal"
    ws.append(["title"])
    ws.append(["Date", "Ticker", "Notes"])
    for row in rows:
        ws.append(list(row))
    p = tmp_path / "j.xlsx"
    wb.save(p)
    return str(p)


def test_read_journal_has_no_row_bound_and_keeps_values_verbatim(tmp_path):
    rows = [(datetime.datetime(2026, 1, 1), f"L{i}", f"n{i}") for i in range(305)]
    rows[0] = (datetime.datetime(2026, 1, 1), " padded ", "n0")
    rows[1] = (datetime.datetime(2026, 1, 1), None, "no label")
    got = xlsx_io.read_journal(_journal_file(tmp_path, rows))
    assert len(got) == 305                      # the old max_row=302 dropped the last 3
    assert got[0]["ticker"] == " padded "      # not stripped: the DB must round-trip it
    assert got[1]["ticker"] is None


def test_read_journal_raises_on_content_without_a_date(tmp_path):
    p = _journal_file(tmp_path, [(datetime.datetime(2026, 1, 1), "A", "ok"),
                                 (None, "B", "orphan note")])
    with pytest.raises(ValueError, match="row 4"):
        xlsx_io.read_journal(p)


# --------------------------------------------------------------- generate --

def test_entries_land_in_write_order_not_date_order(tmp_path):
    wb, ws = _skeleton()
    result = generate_journal(_db(tmp_path, ENTRIES), ws)
    assert result == dict(rows=3, capacity=5, grew_to=None)
    assert [ws.cell(row=r, column=2).value for r in (3, 4, 5)] == ["SFM", "QTR-REVIEW-2", "UNH,ACN"]
    assert ws["A4"].value == datetime.datetime(2026, 12, 1)
    assert ws["C5"].value.startswith("written third")


def test_stale_rows_below_the_entries_are_really_cleared(tmp_path):
    stale = [(datetime.datetime(2020, 1, d), f"OLD{d}", "stale") for d in range(1, 6)]
    wb, ws = _skeleton(prefill=stale)
    generate_journal(_db(tmp_path, ENTRIES[:2]), ws)
    for r in (5, 6, 7):
        assert [ws.cell(row=r, column=c).value for c in (1, 2, 3)] == [None, None, None]
        assert ws.row_dimensions[r].height == generate.BLANK_ROW_PT


def test_refuses_to_blank_the_log_from_an_empty_database(tmp_path):
    wb, ws = _skeleton(prefill=[(datetime.datetime(2026, 1, 1), "A", "keep me")])
    with pytest.raises(GenerateError, match="refusing to blank"):
        generate_journal(_db(tmp_path), ws)
    assert ws["C3"].value == "keep me"


def test_table_filter_and_print_area_grow_past_the_template(tmp_path):
    wb, ws = _skeleton(capacity=3)
    entries = [(f"2026-01-{d:02d}", f"L{d}", f"note {d}") for d in range(1, 7)]
    result = generate_journal(_db(tmp_path, entries), ws)
    assert result == dict(rows=6, capacity=6, grew_to=8)
    assert ws.tables["JournalTable"].ref == "A2:C8"
    assert ws.tables["JournalTable"].autoFilter.ref == "A2:C8"
    assert ws.print_area.endswith("$C$8")
    assert ws["C8"].value == "note 6"
    assert ws["C8"].alignment.wrap_text and ws["A8"].number_format == "mm/dd/yyyy"
    # the audit's own check agrees the Table now covers every data row
    p = tmp_path / "grown.xlsx"
    wb.save(p)
    assert all(c.ok for c in check_table_refs(str(p)))


def test_schema_reference_note_follows_the_table_when_it_grows(tmp_path):
    wb, ws = _skeleton(capacity=3)
    sr = wb.create_sheet("Schema Reference")
    sr["A4"] = "Journal"                                    # block layout the audit walks:
    sr["B6"], sr["C6"] = "JournalTable", "A2:C5 (3 rows)"  # tab name at r, Table row at r+2
    entries = [(f"2026-01-{d:02d}", f"L{d}", f"note {d}") for d in range(1, 7)]
    generate_journal(_db(tmp_path, entries), ws)
    assert sr["C6"].value == "A2:C8 (3 rows)"
    p = tmp_path / "sr.xlsx"
    wb.save(p)
    assert all(c.ok for c in check_schema_reference(str(p)))


def test_generating_restores_hand_toggled_formatting(tmp_path):
    reference_wb, reference = _skeleton()
    generate_journal(_db(tmp_path, ENTRIES), reference)

    wb, ws = _skeleton(prefill=[(datetime.datetime(2026, 1, 1), "X", "y")] * 3)
    ws["C3"].alignment = Alignment(wrap_text=False)          # "turn off wrapping to look"
    ws["B4"].font = Font(name="Calibri", size=9)
    other = tmp_path / "other"
    other.mkdir()
    generate_journal(_db(other, ENTRIES), ws)
    assert generate.diff_journal(reference, ws) == []
    assert ws["C3"].alignment.wrap_text is True
    assert ws["B3"].alignment.wrap_text is True              # long labels must not clip


def test_a_note_starting_with_equals_stays_text(tmp_path):
    wb, ws = _skeleton()
    generate_journal(_db(tmp_path, [("2026-10-01", "X", "=SUM(A1:A2) is not a formula")]), ws)
    p = tmp_path / "eq.xlsx"
    wb.save(p)
    cell = openpyxl.load_workbook(p)["Journal"]["C3"]
    assert cell.data_type == "s"
    assert cell.value == "=SUM(A1:A2) is not a formula"


def test_height_policy_entries_auto_blanks_default_pinned_ceiling(tmp_path):
    wb, ws = _skeleton()
    generate_journal(_db(tmp_path, ENTRIES), ws)
    assert [ws.row_dimensions[r].height for r in (3, 4, 5)] == [None, None, None]
    assert [ws.row_dimensions[r].height for r in (6, 7)] == [generate.BLANK_ROW_PT] * 2
    generate._apply_journal_heights(ws, rows=3, last_row=7, pinned=(4,))
    assert ws.row_dimensions[4].height == generate.MAX_ROW_PT
    assert ws.row_dimensions[3].height is None


@pytest.mark.parametrize("mutate, message", [
    (lambda ws: ws.__setitem__("B2", "Label"), "headers"),
    (lambda ws: ws.tables.pop("JournalTable"), "no table"),
    (lambda ws: setattr(ws.tables["JournalTable"], "ref", "A3:C9"), "other tabs reference"),
])
def test_layout_drift_is_refused(tmp_path, mutate, message):
    wb, ws = _skeleton()
    mutate(ws)
    with pytest.raises(GenerateError, match=message):
        generate_journal(_db(tmp_path, ENTRIES), ws)


# ----------------------------------------------------------- workbook / CLI --

def test_generate_workbook_then_verify_is_clean_and_catches_drift(tmp_path):
    wb, _ = _skeleton()
    xlsx = tmp_path / "wb.xlsx"
    wb.save(xlsx)
    conn = _db(tmp_path, ENTRIES)
    conn.close()
    db = str(tmp_path / "t.db")

    result = generate.generate_workbook(str(xlsx), db, recalc=False)
    assert result["tabs"]["journal"]["rows"] == 3
    assert verify_journal(str(xlsx), db) == []

    conn = sqlite3.connect(db)
    models.journal_add(conn, "2026-10-02", "NEW", "added to the DB only")
    conn.commit()
    conn.close()
    drift = verify_journal(str(xlsx), db)
    assert {d[0] for d in drift} == {"value"}
    assert {d[1] for d in drift} == {"A6", "B6", "C6"}


def test_diff_ignores_row_heights(tmp_path):
    wb, ws = _skeleton()
    generate_journal(_db(tmp_path, ENTRIES), ws)
    other_wb, other = _skeleton()
    other_db = tmp_path / "o"
    other_db.mkdir()
    generate_journal(_db(other_db, ENTRIES), other)
    other.row_dimensions[3].height = 200
    assert generate.diff_journal(ws, other) == []


def test_missing_or_foreign_database_is_refused_without_creating_one(tmp_path):
    wb, _ = _skeleton()
    xlsx = tmp_path / "wb.xlsx"
    wb.save(xlsx)
    ghost = tmp_path / "typo.db"
    with pytest.raises(FileNotFoundError):
        generate.generate_workbook(str(xlsx), str(ghost), recalc=False)
    assert not ghost.exists()
    foreign = tmp_path / "other.db"
    sqlite3.connect(foreign).execute("CREATE TABLE unrelated (x)").connection.commit()
    with pytest.raises(GenerateError, match="no journal table"):
        generate.generate_workbook(str(xlsx), str(foreign), recalc=False)


# -------------------------------------------------- the repo's real workbook --

def test_roundtrip_against_the_repos_real_workbook(tmp_path):
    workbook = xlsx_io.latest_workbook(REPO_ROOT)
    if not workbook:
        pytest.skip("no tracked workbook in this checkout")
    db = str(tmp_path / "real.db")
    counts = migrate(workbook, db, verbose=False)
    assert counts["journal"] > 0 and counts["drawdown_log"] > 0

    # Every value survives workbook -> DB -> tab: Journal dates/labels/notes, and the
    # Drawdown Log's inputs AND its five formula columns (compared as text). Look-only
    # differences are expected: this checkout's tracked workbook predates later
    # restyles the canonical formats mirror (older Journal fill and font size, the
    # Drawdown notes alignment), and Journal column B's wrap is deliberately now on.
    results = generate.verify_workbook(workbook, db)
    assert set(results) == {"journal", "drawdown"}
    for key, diffs in results.items():
        assert [d for d in diffs if d[0] != "style" and not d[0].startswith("style:")] == [], key

    out = str(tmp_path / "regenerated.xlsx")
    generate.generate_workbook(workbook, db, out_path=out, recalc=False)
    assert all(diffs == [] for diffs in generate.verify_workbook(out, db).values())   # idempotent


# ------------------------------------------------------------ Drawdown Log --

DD_ENTRIES = [("2026-08-11", 700739.19, None),
              ("2026-09-04", 751223.12, "tranche 2 check"),
              ("2026-09-30", 776662.72, None)]
DD = "Portfolio Drawdown Log"


def _dd_skeleton(capacity=4, action_items=False, schema=False):
    """A minimal Drawdown Log tab shaped like the live one: merged title, header
    row 2, a Table over A2:H<2+capacity>, a status conditional format, and
    optionally the Action Items formulas that read it and its Schema Reference block."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = DD
    ws["A1"] = "Portfolio-level drawdown tracking"
    ws.merge_cells("A1:H1")
    for col, header in enumerate(generate.DRAWDOWN_HEADERS, start=1):
        ws.cell(row=2, column=col, value=header)
    last = 2 + capacity
    ws.add_table(Table(displayName="DrawdownLogTable", ref=f"A2:H{last}",
                       autoFilter=AutoFilter(ref=f"A2:H{last}")))
    ws.conditional_formatting.add(f"E3:E{last}", CellIsRule(
        operator="equal", formula=['"Normal"'],
        fill=PatternFill(start_color="FFC6EFCE", end_color="FFC6EFCE", fill_type="solid")))
    if action_items:
        wb.create_sheet("Elsewhere")
        ai = wb.create_sheet("Action Items")
        ref = f"'{DD}'!"
        ai["E18"] = f'=IFERROR(INDEX({ref}E3:E{last},COUNT({ref}D3:D{last})),"No entries yet")'
        ai["E19"] = f'=IFERROR(INDEX({ref}D3:D{last},COUNT({ref}D3:D{last})),"")'
        ai["E20"] = f'=IFERROR(INDEX({ref}F3:F{last},COUNT({ref}D3:D{last})),"")'
        ai["E21"] = f"=SUM('Elsewhere'!A3:A{last})"            # same row number, other sheet
    if schema:
        sr = wb.create_sheet("Schema Reference")
        sr["A4"] = DD
        sr["B6"], sr["C6"] = "DrawdownLogTable", f"A2:H{last} (as of test)"
    return wb, ws


def _dd_db(tmp_path, entries=()):
    conn = models.init_db(str(tmp_path / "dd.db"))
    for date, value, notes in entries:
        models.drawdown_add(conn, date, value, notes)
    conn.commit()
    return conn


def test_drawdown_rows_come_back_in_date_order_even_if_backfilled(tmp_path):
    conn = _dd_db(tmp_path, [("2026-09-30", 3.0, None), ("2026-08-11", 1.0, None),
                             ("2026-09-04", 2.0, "x")])
    assert [r["date"] for r in models.drawdown_rows(conn)] == [
        "2026-08-11", "2026-09-04", "2026-09-30"]


def test_drawdown_allows_one_entry_per_date(tmp_path):
    conn = _dd_db(tmp_path, [("2026-09-30", 1.0, None)])
    with pytest.raises(sqlite3.IntegrityError):
        models.drawdown_add(conn, datetime.date(2026, 9, 30), 2.0)


def test_drawdown_stores_inputs_only(tmp_path):
    conn = _dd_db(tmp_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(drawdown_log)")]
    assert cols == ["id", "date", "portfolio_value", "notes"]


def test_drawdown_writes_inputs_and_row_relative_formulas(tmp_path):
    wb, ws = _dd_skeleton(capacity=4)
    generate.generate_drawdown_log(_dd_db(tmp_path, DD_ENTRIES), ws)
    assert ws["A3"].value == datetime.datetime(2026, 8, 11)
    assert ws["B4"].value == 751223.12 and ws["H4"].value == "tranche 2 check"
    assert ws["H3"].value is None
    assert ws["C3"].value == '=IF(B3="","",B3)'                  # first row: nothing to chain from
    assert ws["C4"].value == '=IF(B4="","",MAX(C3,B4))'
    assert ws["D5"].value == '=IF(OR(B5="",C5=""),"",B5/C5-1)'
    # pre-formatted rows below the entries keep their formulas, returning ""
    assert ws["C6"].value == '=IF(B6="","",MAX(C5,B6))'
    assert ws["A6"].value is None and ws["B6"].value is None
    assert all(ws.cell(row=6, column=c).data_type == "f" for c in range(3, 8))
    assert ws.row_dimensions[6].height == generate.DRAWDOWN_ROW_PT


def test_drawdown_writes_in_date_order_so_a_backfill_lands_in_place(tmp_path):
    wb, ws = _dd_skeleton()
    generate.generate_drawdown_log(_dd_db(tmp_path, [
        ("2026-09-30", 3.0, None), ("2026-08-11", 1.0, "backfilled last")]), ws)
    assert ws["A3"].value == datetime.datetime(2026, 8, 11) and ws["H3"].value == "backfilled last"
    assert ws["A4"].value == datetime.datetime(2026, 9, 30)


def test_drawdown_refuses_to_blank_a_populated_tab(tmp_path):
    wb, ws = _dd_skeleton()
    ws["A3"], ws["B3"] = datetime.datetime(2026, 1, 1), 5
    with pytest.raises(GenerateError, match="refusing to blank"):
        generate.generate_drawdown_log(_dd_db(tmp_path), ws)
    assert ws["B3"].value == 5


def test_drawdown_growth_moves_everything_keyed_to_the_last_row(tmp_path):
    wb, ws = _dd_skeleton(capacity=3, action_items=True, schema=True)
    entries = [(f"2026-0{m}-15", 100.0 + m, None) for m in range(1, 7)]     # 6 > capacity 3
    result = generate.generate_drawdown_log(_dd_db(tmp_path, entries), ws)
    assert result == dict(rows=6, capacity=6, grew_to=8)
    table = ws.tables["DrawdownLogTable"]
    assert table.ref == "A2:H8" and table.autoFilter.ref == "A2:H8"
    assert [str(cf.sqref) for cf in ws.conditional_formatting] == ["E3:E8"]
    ai, ref = wb["Action Items"], "'Portfolio Drawdown Log'!"
    assert ai["E18"].value == f'=IFERROR(INDEX({ref}E3:E8,COUNT({ref}D3:D8)),"No entries yet")'
    assert ai["E19"].value == f'=IFERROR(INDEX({ref}D3:D8,COUNT({ref}D3:D8)),"")'
    assert ai["E20"].value == f'=IFERROR(INDEX({ref}F3:F8,COUNT({ref}D3:D8)),"")'
    assert ai["E21"].value == "=SUM('Elsewhere'!A3:A5)"          # another sheet's range: untouched
    assert wb["Schema Reference"]["C6"].value.startswith("A2:H8")
    assert ws["H8"].alignment.horizontal == "left" and ws["C8"].value == '=IF(B8="","",MAX(C7,B8))'
    p = tmp_path / "grown.xlsx"
    wb.save(p)
    assert all(c.ok for c in check_table_refs(str(p)))
    assert all(c.ok for c in check_schema_reference(str(p)))


def test_drawdown_generation_restores_formulas_and_formatting(tmp_path):
    ref_wb, reference = _dd_skeleton()
    generate.generate_drawdown_log(_dd_db(tmp_path, DD_ENTRIES), reference)

    wb, ws = _dd_skeleton()
    ws["D4"] = 0.5                                              # someone typed over a formula
    ws["C5"] = '=B5*2'                                          # ...or edited one
    ws["E3"].font = Font(name="Calibri", size=9)
    other = tmp_path / "other"
    other.mkdir()
    generate.generate_drawdown_log(_dd_db(other, DD_ENTRIES), ws)
    assert generate.diff_drawdown_log(reference, ws) == []


def test_drawdown_band_names_match_drawdown_py():
    from landry.drawdown import BANDS
    assert [b[1] for b in BANDS] == ["Normal", "Elevated", "Severe", "Critical"]
    status = generate._drawdown_formulas(4)[2]
    assert all(f'"{name}"' in status for _, name, *_ in BANDS)
    assert "-0.1" in status and "-0.2" in status and "-0.3" in status


def test_read_drawdown_log_inputs_is_unbounded_and_loud(tmp_path):
    def sheet(rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = DD
        ws.append(["title"])
        ws.append(list(generate.DRAWDOWN_HEADERS))
        for row in rows:
            ws.append(row)
        p = tmp_path / "dd.xlsx"
        wb.save(p)
        return str(p)

    many = [[datetime.datetime(2026, 1, 1) + datetime.timedelta(days=i), 100 + i,
             "=B3*2", None, None, None, None, f"n{i}" if i == 5 else None] for i in range(45)]
    got = xlsx_io.read_drawdown_log_inputs(sheet(many))
    assert len(got) == 45                                       # past the old 40-row Table
    assert got[5]["notes"] == "n5" and got[0]["notes"] is None
    assert set(got[0]) == {"date", "portfolio_value", "notes"}  # derived columns are not read
    with pytest.raises(ValueError, match="row 4"):
        xlsx_io.read_drawdown_log_inputs(sheet([many[0], [datetime.datetime(2026, 2, 1), None]]))


@pytest.mark.skipif(soffice_path() is None, reason="soffice not installed")
def test_drawdown_formulas_compute_the_bands_in_libreoffice(tmp_path):
    wb, ws = _dd_skeleton(capacity=8, action_items=True)
    xlsx = tmp_path / "bands.xlsx"
    wb.save(xlsx)
    # peak 100: -5%, -11%, -21%, -31%, then a new high resets to zero
    conn = _dd_db(tmp_path, [(f"2026-01-{i + 1:02d}", v, None)
                             for i, v in enumerate([100, 95, 89, 79, 69, 120])])
    conn.close()
    generate.generate_workbook(str(xlsx), str(tmp_path / "dd.db"))
    log = openpyxl.load_workbook(xlsx, data_only=True)[DD]
    col = lambda letter: [log[f"{letter}{r}"].value for r in range(3, 9)]
    assert col("E") == ["Normal", "Normal", "Elevated", "Severe", "Critical", "Normal"]
    assert col("F") == ["5-15%", "5-15%", "15%", "20%", "25%", "5-15%"]
    assert col("G") == ["Standard (score >=65)", "Standard (score >=65)",
                        "Restricted (score >=80 only)", "Suspended", "Suspended",
                        "Standard (score >=65)"]
    assert col("C") == [100, 100, 100, 100, 100, 120]
    assert log["E9"].value in (None, "")                        # unused rows compute to blank
    ai = openpyxl.load_workbook(xlsx, data_only=True)["Action Items"]
    assert ai["E18"].value == "Normal" and ai["E19"].value == 0
