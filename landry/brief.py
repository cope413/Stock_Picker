"""``landry brief``: a ~2-3K-token state digest for starting a fresh session (read-only).

CLAUDE.md's bootstrap says to read the Journal tail, Open Items and Process Checklist -- about 30K+ tokens raw.
This prints the same orientation compactly: recent decisions (label + first line), open items, steps overdue or
due soon, and the repo state. Read the full rows only for what the digest points at."""
import datetime as dt
import re
import subprocess
from typing import List

import openpyxl

from landry.xlsx_io import read_journal


def _first_line(text, n=150) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    m = re.match(r"(.{20,%d}?[.;:])\s" % n, s)
    return (m.group(1) if m else s[:n]).strip()


def _table(path: str, sheet: str, header_first: str):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(wb[sheet].iter_rows(values_only=True))
    finally:
        wb.close()
    for i, r in enumerate(rows):
        if r and r[0] == header_first:
            return rows[i + 1:]
    return []


def build(path: str, today: dt.date = None, journal_n: int = 8, soon_days: int = 14) -> str:
    today = today or dt.date.today()
    out: List[str] = [f"LANDRY BRIEF {today}  (workbook {path})"]
    J = read_journal(path)
    n0 = len(J)
    out.append(f"\nJOURNAL: last {journal_n} of {n0} entries (row = sheet row)")
    for k, e in enumerate(J[-journal_n:], start=n0 - journal_n):
        out.append(f"  row {k + 3} {e['date']:%m/%d} {str(e['ticker'])[:34]}: {_first_line(e['notes'])}")
    oi = [r for r in _table(path, "Open Items", "#") if r[0] is not None and str(r[2] or "").upper() != "Y"]
    out.append(f"\nOPEN ITEMS: {len(oi)} unresolved")
    for r in oi:
        out.append(f"  #{r[0]} {str(r[1])[:92]}")
    pc = [r for r in _table(path, "Process Checklist", "Event") if r[0] and str(r[5] or "") not in ("Done", "")]
    soon = today + dt.timedelta(days=soon_days)
    show = [r for r in pc if str(r[5]) == "OVERDUE" or (isinstance(r[4], dt.datetime) and r[4].date() <= soon)]
    out.append(f"\nPROCESS CHECKLIST: {len(show)} steps overdue or due within {soon_days} days")
    for r in show:
        due = r[4].strftime("%m/%d") if isinstance(r[4], dt.datetime) else "?"
        out.append(f"  {r[5]:8s} {due} {r[0]}: {str(r[1])[:70]}")
    try:
        g = subprocess.run(["git", "status", "-sb", "--porcelain"], capture_output=True, text=True, timeout=20).stdout.strip().splitlines()
        out.append("\nGIT: " + (g[0] if g else "?") + (f"  (+{len(g) - 1} changed/untracked)" if len(g) > 1 else "  clean"))
    except Exception:
        pass
    out.append("\nNot included (run when needed): landry audit, landry db status, landry stops, landry monitor status.")
    return "\n".join(out)
