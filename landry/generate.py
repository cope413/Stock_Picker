"""Phase C of LANDRY_DATABASE_DESIGN.md, rolled out one tab at a time:
regenerate a workbook tab's data region from ``landry.db``.

Journal is first -- freeform text, no formulas to replicate. What the
generator owns, and what it deliberately leaves alone:

  owns    the values in the Journal Table's data rows; those rows' cell
          formatting (so a hand-toggled wrap or font can't leak into a commit --
          regenerating restores it); their row heights; and, when the log
          outgrows its pre-formatted rows, the Table's ref / filter / print area
          and Schema Reference's row-range note for it.
  leaves  the title row, header row, column widths, sheet view, page setup and
          footer, and every other tab. Other tabs reference Journal by column
          (Process Checklist: ``INDEX(Journal!$A:$A, MATCH(label, Journal!$B:$B,
          0))``), so its layout is a contract this module must not disturb.

Order is the DB's id order, i.e. write order -- never date order. Future-dated
recurring-event placeholders would otherwise bury the real recent entries
(see CLAUDE.md).

Row heights are derived layout, not data, so they are never stored: data rows
are written without a height and the mandatory recalc pass (LibreOffice) fits
them and flags them auto-height, which also keeps Excel's wrap-off/wrap-on trick
for a compact view working. The one exception is a note that needs more than
Excel's 409.5pt row ceiling: after the first pass those rows (and only those,
measured, not guessed) are pinned to the ceiling and recalculated once more,
because LibreOffice would otherwise write a height Excel has to clamp.

    python -m landry.generate journal --workbook W.xlsx --db landry.db [--out OUT.xlsx] [--no-recalc]
    python -m landry.generate verify  --workbook W.xlsx --db landry.db
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import sqlite3
import sys
from collections import Counter
from typing import List, Optional, Tuple

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import range_boundaries

from landry import models

JOURNAL_SHEET = "Journal"
JOURNAL_TABLE = "JournalTable"
JOURNAL_HEADER_ROW = 2
JOURNAL_FIRST_ROW = 3
JOURNAL_HEADERS = ("Date", "Ticker", "Notes")

BLANK_ROW_PT = 14.25      # height of the pre-formatted capacity rows below the last entry
MAX_ROW_PT = 409.5        # Excel's hard row-height ceiling

# Canonical look of a Journal data row. Mirrors the live tab as of 2026-10-02
# (checked cell-by-cell by ``verify``) with one deliberate change: column B wraps
# on every row. It was on for 35 of 300 rows, mostly 12-53 -- where the long
# labels were -- so later rows (e.g. a 54-character ticker list) were clipped.
_NAVY = "FF002060"
_FILL = PatternFill(fill_type="solid", fgColor="FFECF4FA", bgColor="FFEBF1DE")
_EDGE = Side(style="thin", color="FF999999")
_BORDER = Border(left=_EDGE, right=_EDGE, top=_EDGE, bottom=_EDGE)
# per column A, B, C: (font size, horizontal, vertical, wrap, number format)
_COLUMNS = (
    (10, "center", "center", False, "mm/dd/yyyy"),
    (11, "center", "center", True, "General"),
    (10, "left", "top", True, "General"),
)
_COLUMN_STYLES = tuple(
    (Font(name="Arial Narrow", size=size, color=_NAVY),
     Alignment(horizontal=h, vertical=v, wrap_text=wrap), fmt)
    for size, h, v, wrap, fmt in _COLUMNS)


class GenerateError(RuntimeError):
    """The workbook or database isn't in a shape generation can trust."""


# ---------------------------------------------------------------- layout --

def _journal_layout(ws) -> Tuple[object, int]:
    """Validate the Journal tab's skeleton; return (Table, last Table row)."""
    if JOURNAL_TABLE not in ws.tables:
        raise GenerateError(f"'{ws.title}' has no table named {JOURNAL_TABLE}")
    table = ws.tables[JOURNAL_TABLE]      # bracket access: .items() yields ref strings
    min_col, min_row, max_col, max_row = range_boundaries(table.ref)
    if (min_col, min_row, max_col) != (1, JOURNAL_HEADER_ROW, 3):
        raise GenerateError(
            f"{JOURNAL_TABLE} ref {table.ref} is not A{JOURNAL_HEADER_ROW}:C<n>; "
            f"other tabs reference this layout by column, so it can't shift")
    headers = tuple(ws.cell(row=JOURNAL_HEADER_ROW, column=c).value for c in (1, 2, 3))
    if headers != JOURNAL_HEADERS:
        raise GenerateError(f"Journal headers are {headers}, expected {JOURNAL_HEADERS}")
    return table, max_row


def _put(cell, value) -> None:
    # Explicit attribute assignment: ws.cell(row, col, value=None) is a no-op on
    # a cell that already has content, which would leave stale rows behind.
    cell.value = value
    if isinstance(value, str) and value.startswith("="):
        cell.data_type = "s"        # a note that begins with '=' is text, not a formula


def _extend_print_area(ws, old_last: int, new_last: int) -> None:
    area = ws.print_area            # e.g. "'Journal'!$A$1:$C$302"
    if area and area.endswith(f"${old_last}"):
        ws.print_area = f"A1:C{new_last}"


def _update_schema_reference(wb, new_last: int) -> bool:
    """Keep Schema Reference's Journal row-range note ('A2:C302') in step with
    the Table when it grows -- ``landry audit`` flags it as stale otherwise.
    Same 4-row block layout the audit walks: tab name in column A at row r,
    the Table row at r+2 with its range text in column C. Best effort: if the
    block isn't there or doesn't parse, leave it for the audit to report."""
    if "Schema Reference" not in wb.sheetnames:
        return False
    ws = wb["Schema Reference"]
    for r in range(4, ws.max_row + 1, 4):
        if ws.cell(row=r, column=1).value == JOURNAL_SHEET:
            cell = ws.cell(row=r + 2, column=3)
            m = re.search(r"\b[A-Z]{1,3}\d+:[A-Z]{1,3}(\d+)\b", str(cell.value or ""))
            if not m:
                return False
            cell.value = cell.value[:m.start(1)] + str(new_last) + cell.value[m.end(1):]
            return True
    return False


# -------------------------------------------------------------- generate --

def _apply_heights(ws, rows: int, last_row: int, pinned=()) -> None:
    """The row-height policy. Entry rows get no stored height (the recalc
    pass fits them and flags them auto-height) unless pinned to Excel's
    ceiling; the pre-formatted capacity rows below them get the default."""
    for r in range(JOURNAL_FIRST_ROW, last_row + 1):
        if r - JOURNAL_FIRST_ROW >= rows:
            ws.row_dimensions[r].height = BLANK_ROW_PT
        else:
            ws.row_dimensions[r].height = MAX_ROW_PT if r in pinned else None


def generate_journal(conn: sqlite3.Connection, ws) -> dict:
    """Rewrite the Journal Table's data rows in ``ws`` from the database.

    Returns ``{rows, capacity, grew_to}``. Raises GenerateError
    rather than blank the log: an empty ``journal`` table against a tab that has
    entries means the wrong database, not an instruction to erase them."""
    table, last_row = _journal_layout(ws)
    entries = models.journal_rows(conn)
    if not entries and any(ws.cell(row=r, column=1).value is not None
                           for r in range(JOURNAL_FIRST_ROW, last_row + 1)):
        raise GenerateError("the database's journal table is empty but the tab has "
                            "entries -- wrong database? refusing to blank the log")

    old_last = last_row
    last_row = max(last_row, JOURNAL_HEADER_ROW + len(entries))
    grew_to = last_row if last_row > old_last else None
    if grew_to:
        table.ref = f"A{JOURNAL_HEADER_ROW}:C{last_row}"
        if table.autoFilter is not None:
            table.autoFilter.ref = table.ref
        _extend_print_area(ws, old_last, last_row)
        _update_schema_reference(ws.parent, last_row)

    for r in range(JOURNAL_FIRST_ROW, last_row + 1):
        i = r - JOURNAL_FIRST_ROW
        entry = entries[i] if i < len(entries) else None
        cells = [ws.cell(row=r, column=c) for c in (1, 2, 3)]
        if entry:
            _put(cells[0], datetime.datetime.fromisoformat(entry["date"]))
            _put(cells[1], entry["label"])
            _put(cells[2], entry["notes"])
        else:
            for cell in cells:
                _put(cell, None)
        for cell, (font, alignment, fmt) in zip(cells, _COLUMN_STYLES):
            cell.font, cell.alignment, cell.number_format = font, alignment, fmt
            cell.fill, cell.border = _FILL, _BORDER
    _apply_heights(ws, len(entries), last_row)
    return dict(rows=len(entries), capacity=last_row - JOURNAL_HEADER_ROW,
                grew_to=grew_to)


def _rows_over_ceiling(path: str) -> List[int]:
    """Journal rows whose (LibreOffice-fitted) height exceeds Excel's ceiling."""
    ws = openpyxl.load_workbook(path)[JOURNAL_SHEET]
    return [r for r in range(JOURNAL_FIRST_ROW, ws.max_row + 1)
            if (ws.row_dimensions[r].height or 0) > MAX_ROW_PT]


def _open_db(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"{db_path} does not exist (sqlite would silently create an empty one)")
    conn = models.connect(db_path)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='journal'").fetchone():
        conn.close()
        raise GenerateError(f"{db_path} has no journal table -- not a landry database")
    return conn


def generate_workbook(workbook_path: str, db_path: str, out_path: Optional[str] = None,
                      recalc: bool = True) -> dict:
    """Regenerate the Journal tab of ``workbook_path`` from ``db_path`` and
    save to ``out_path`` (default: in place). ``recalc`` runs the mandatory
    LibreOffice pass afterwards -- openpyxl wipes every cached formula value on
    save, and that pass also fits the auto-height rows."""
    out_path = out_path or workbook_path
    wb = openpyxl.load_workbook(workbook_path)
    conn = _open_db(db_path)
    try:
        result = generate_journal(conn, wb[JOURNAL_SHEET])
    finally:
        conn.close()
    wb.save(out_path)
    if recalc:
        from landry.xlsx_recalc import recalc as run_recalc
        result["recalc"] = run_recalc(out_path)
        tall = _rows_over_ceiling(out_path)
        if tall:
            wb = openpyxl.load_workbook(out_path)
            # Re-apply the policy rather than just re-save: openpyxl reads
            # LibreOffice's fitted heights back as explicit ones, which would
            # turn every auto-height row into a fixed one.
            _apply_heights(wb[JOURNAL_SHEET], result["rows"],
                           JOURNAL_HEADER_ROW + result["capacity"], pinned=tall)
            wb.save(out_path)
            result["recalc"] = run_recalc(out_path)
        result["capped_rows"] = tall
    return result


# ---------------------------------------------------------------- verify --

def _rgb(color) -> Optional[str]:
    return color.rgb if color is not None and color.type == "rgb" else None


def _style_sig(cell) -> tuple:
    f, fl, b, a = cell.font, cell.fill, cell.border, cell.alignment
    return (f.name, f.sz, bool(f.b), bool(f.i), _rgb(f.color),
            fl.fill_type, _rgb(fl.fgColor),
            tuple((s.style, _rgb(s.color)) for s in (b.left, b.right, b.top, b.bottom)),
            a.horizontal, a.vertical, bool(a.wrap_text), cell.number_format)


_STYLE_FIELDS = ("font", "size", "bold", "italic", "font color", "fill", "fill color",
                 "borders", "h-align", "v-align", "wrap", "number format")


def diff_journal(a, b) -> List[tuple]:
    """Differences between two Journal sheets as ``(kind, where, a, b)``.
    Row heights are excluded on purpose (derived layout, see module docstring)."""
    out = []
    ta, tb = a.tables[JOURNAL_TABLE], b.tables[JOURNAL_TABLE]
    if ta.ref != tb.ref:
        out.append(("table", "ref", ta.ref, tb.ref))
    fa = ta.autoFilter.ref if ta.autoFilter is not None else None
    fb = tb.autoFilter.ref if tb.autoFilter is not None else None
    if fa != fb:
        out.append(("table", "filter", fa, fb))
    if a.print_area != b.print_area:
        out.append(("print_area", "", a.print_area, b.print_area))
    for r in range(JOURNAL_FIRST_ROW, max(a.max_row, b.max_row) + 1):
        for c in (1, 2, 3):
            ca, cb = a.cell(row=r, column=c), b.cell(row=r, column=c)
            if (type(ca.value), ca.value) != (type(cb.value), cb.value):
                out.append(("value", ca.coordinate, ca.value, cb.value))
            sa, sb = _style_sig(ca), _style_sig(cb)
            if sa != sb:
                for field, x, y in zip(_STYLE_FIELDS, sa, sb):
                    if x != y:
                        out.append(("style:" + field, ca.coordinate, x, y))
    return out


def verify_journal(workbook_path: str, db_path: str) -> List[tuple]:
    """Regenerate the Journal in memory from the database and diff it against
    the workbook as it stands. ``[]`` means generating would change nothing --
    the DB and the tab agree on every value and every bit of formatting."""
    live = openpyxl.load_workbook(workbook_path)
    regenerated = openpyxl.load_workbook(workbook_path)
    conn = _open_db(db_path)
    try:
        generate_journal(conn, regenerated[JOURNAL_SHEET])
    finally:
        conn.close()
    return diff_journal(live[JOURNAL_SHEET], regenerated[JOURNAL_SHEET])


# ------------------------------------------------------------------- CLI --

def _summarize(diffs: List[tuple], show: int = 6) -> str:
    if not diffs:
        return "identical: generating from the database would change nothing"
    lines = []
    for kind, n in Counter(d[0] for d in diffs).most_common():
        lines.append(f"  {kind}: {n}")
        for _, where, x, y in [d for d in diffs if d[0] == kind][:show]:
            lines.append(f"      {where or '-'}: {str(x)[:48]!r} -> {str(y)[:48]!r}")
    return "differences (workbook -> regenerated):\n" + "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Regenerate workbook tabs from landry.db (Phase C, Journal first)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("journal", "rewrite the Journal tab from the database"),
                        ("verify", "diff the Journal tab against what the database would generate")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--workbook", required=True)
        p.add_argument("--db", default=models.DEFAULT_DB_PATH)
        if name == "journal":
            p.add_argument("--out", default=None, help="write here instead of in place")
            p.add_argument("--no-recalc", action="store_true",
                           help="skip the LibreOffice recalc (the workbook is then NOT safe to commit)")
    args = ap.parse_args(argv)

    if args.cmd == "verify":
        diffs = verify_journal(args.workbook, args.db)
        print(_summarize(diffs))
        return 1 if diffs else 0

    result = generate_workbook(args.workbook, args.db, args.out, recalc=not args.no_recalc)
    print(f"Journal: {result['rows']} entries written, capacity {result['capacity']}"
          + (f", Table grew to row {result['grew_to']}" if result["grew_to"] else ""))
    if result.get("capped_rows"):
        print(f"  {len(result['capped_rows'])} entries exceed Excel's 409.5pt row height "
              f"and show their first lines in-cell: rows {result['capped_rows']}")
    if "recalc" in result:
        print(f"  recalc: {result['recalc'].get('status')}, "
              f"{result['recalc'].get('total_errors')} errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
