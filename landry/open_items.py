"""``landry open-items``: add a note to, or close, an Open Items row (the tab is a Table edited in place).

Replaces the throwaway openpyxl scripts that were rewritten for every Open Items update (ten of them on 10/8).
A note is APPENDED to column F (never replaces it) and the row height is re-fit; ``close`` also sets Done = Y and
the resolved date. Refuses if Excel has the workbook open or the row is missing. Adding a NEW row grows the Table
and Schema Reference, so it is deliberately not here. Recalc runs after the save; run ``landry audit`` before a commit."""
import datetime as dt
import math
from typing import Optional

import openpyxl

from landry import ledger, xlsx_recalc

SHEET = "Open Items"


class OpenItemsError(Exception):
    pass


def _row_of(ws, n: int) -> int:
    for r in range(5, ws.max_row + 1):
        if str(ws.cell(r, 1).value) == str(n):
            return r
    raise OpenItemsError(f"no Open Items row numbered #{n}")


def _fit(ws, r: int) -> None:
    item = len(str(ws.cell(r, 2).value or ""))
    notes = len(str(ws.cell(r, 6).value or ""))
    ws.row_dimensions[r].height = min(400.0, 13.5 * max(math.ceil(item / 92), math.ceil(notes / 82), 1) + 3)


def update(path: str, n: int, note: str, close: bool = False, when: Optional[dt.date] = None,
           recalc: bool = True) -> str:
    if ledger._excel_has_open(path):
        raise OpenItemsError("Excel has the workbook open; close it and retry")
    note = " ".join(note.split())
    if not note and not close:
        raise OpenItemsError("nothing to do: give a note and/or --close")
    wb = openpyxl.load_workbook(path)
    try:
        ws = wb[SHEET]
        r = _row_of(ws, n)
        if close and str(ws.cell(r, 3).value or "").upper() == "Y":
            raise OpenItemsError(f"#{n} is already Done")
        if note:
            old = str(ws.cell(r, 6).value or "").rstrip()
            ws.cell(r, 6).value = (old + " " + note).strip()
        if close:
            ws.cell(r, 3).value = "Y"
            ws.cell(r, 5).value = dt.datetime.combine(when or dt.date.today(), dt.time())
        _fit(ws, r)
        wb.save(path)
    finally:
        wb.close()
    if recalc:
        xlsx_recalc.recalc(path)
    return f"#{n} {'closed' if close else 'noted'} (row {r})"
