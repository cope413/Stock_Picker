"""Offline tests for Phase C step 1 (landry.generate: Journal regenerated from
landry.db), plus the Journal pieces of models / migrate_to_db / xlsx_io it
rests on. Synthetic workbooks only, except the last test, which round-trips
the repo's own tracked workbook and skips if it isn't there."""

import datetime
import os
import sqlite3

import openpyxl
import pytest
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.filters import AutoFilter
from openpyxl.worksheet.table import Table

from landry import generate, models, xlsx_io
from landry.audit import check_schema_reference, check_table_refs
from landry.generate import GenerateError, generate_journal, verify_journal
from landry.migrate_to_db import migrate

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
    generate._apply_heights(ws, rows=3, last_row=7, pinned=(4,))
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
    assert result["rows"] == 3
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
    assert counts["journal"] > 0

    # Every value (dates, labels, notes) survives workbook -> DB -> tab. Look-only
    # differences are expected: this checkout's tracked workbook predates the
    # 2026-09-13 restyle the generator's canonical format mirrors (older fill and
    # font size), and column B's wrap is deliberately now on for every row.
    diffs = verify_journal(workbook, db)
    assert [d for d in diffs if d[0] == "value"] == []
    assert all(d[0].startswith("style:") for d in diffs)

    out = str(tmp_path / "regenerated.xlsx")
    generate.generate_workbook(workbook, db, out_path=out, recalc=False)
    assert verify_journal(out, db) == []        # generating twice changes nothing
