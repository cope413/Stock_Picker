"""Keeping the Monitor & Recheck Triggers tab current for HELD positions (added 2026-10-05).

Alan, 10/5/26: "we'll definitely want to keep Monitoring tab monthly-current for held positions". Two things on
that tab go stale, for different reasons, and both are handled here:

* **Last Score (cols C-G).** Instructions (A52): stamped every time a ticker is re-scored on the Scoring tab -- an
  event, not a calendar. Nothing in the code did it, so it was done by hand, and the 9/13/26 held-position re-score
  of 14 names (and the 9/30 reconfirmation of ADBE / PLD) was never stamped: on 10/5 CRWD still read "70.4 BUY,
  8/6/26" against a Scoring row of 63.4 AVOID. ``mirror_problems`` finds every held, scored ticker whose Monitor row
  does not say what its Scoring row says; ``stamp`` fixes them. Price at Last Score (G) is the last close on or
  before the date scored -- the 9/11 Friday close for a Sunday 9/13 -- the convention the V / GE / NFLX / ABT / VEEV
  stamps already follow.
* **Insider activity and analyst shift (cols K-N)**, "refreshed periodically, not live": SEC Form 4 filings over a
  30-day lookback and yfinance's aggregate consensus (``landry.data_auto``). They had been populated about 9/4; by
  10/5 ADBE's flag was already out of date (N -> Y). ``refresh_signals`` rewrites K-N for the held names and records
  the day in the as-of cell (defined name ``MON_SignalsAsOf``, Q1), which ``landry audit`` holds to a month.

Rules, each learned elsewhere in this codebase: a column's header is checked before it is written (a moved column
is refused, nothing written); a lookup that fails keeps what the cell holds and says so, never blanks it; a ticker
is stamped whole (all five cells) or not at all; nothing is added or removed (what the tab covers is Alan's call);
only cells named above are written. The caller recalculates (``landry monitor`` does).
"""

from __future__ import annotations

import datetime as dt
import re
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

SHEET = "Monitor & Recheck Triggers"
HEADER_ROW, FIRST_ROW = 2, 3
C_TICKER, C_CATEGORY, C_DATE, C_COMPOSITE, C_TIER1, C_DECISION, C_PRICE = 1, 2, 3, 4, 5, 6, 7
C_INSIDER, C_INSIDER_NOTE, C_ANALYST, C_ANALYST_NOTE = 11, 12, 13, 14

# column -> text its (whitespace-normalised, lower-cased) header must contain
HEADERS = {C_TICKER: "ticker", C_DATE: "last score", C_COMPOSITE: "composite", C_TIER1: "wtd avg",
           C_DECISION: "decision", C_PRICE: "price at", C_INSIDER: "insider", C_INSIDER_NOTE: "insider note",
           C_ANALYST: "analyst", C_ANALYST_NOTE: "analyst shift note"}

AS_OF_NAME = "MON_SignalsAsOf"
AS_OF_LABEL_CELL, AS_OF_CELL = "P1", "Q1"
MAX_SIGNAL_AGE_DAYS = 35                   # monthly, plus a week of grace (the insider lookback is 30 days)
COMPOSITE_TOLERANCE = 0.05                 # Scoring computes 74.8; the Monitor holds what was typed
TIER1_TOLERANCE = 0.006                    # the Monitor holds it rounded to 2 decimals

_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class MonitorError(RuntimeError):
    pass


def _L(col: int) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(col)


def _day(v) -> Optional[dt.date]:
    if isinstance(v, dt.datetime):
        return v.date()
    return v if isinstance(v, dt.date) else None


def _check_headers(ws, cols: Sequence[int]) -> None:
    for col in cols:
        head = " ".join(str(ws.cell(row=HEADER_ROW, column=col).value or "").split()).lower()
        if HEADERS[col] not in head:
            raise MonitorError(f"{ws.title}: the header of column {_L(col)} is {head!r}, not one mentioning "
                               f"{HEADERS[col]!r} -- the layout has moved; nothing written")


def _rows(ws) -> Dict[str, int]:
    """ticker -> row, content-based (a label or a footer in column A is not a ticker)."""
    out = {}
    for r in range(FIRST_ROW, ws.max_row + 1):
        v = ws.cell(row=r, column=C_TICKER).value
        if isinstance(v, str) and _TICKER.match(v.strip()):
            out[v.strip()] = r
    return out


# ------------------------------------------------------------- what is held --

def held_scored(path: str) -> List:
    """Scoring rows (``xlsx_io.WorkbookRow``) of every position held now that has a Date Scored. ETFs and cash funds
    have no Scoring row, so they drop out by themselves."""
    from landry import xlsx_io
    held = {p.ticker for p in xlsx_io.read_positions(path)}
    return [r for r in xlsx_io.read_scoring_tab(path) if r.ticker in held and _day(r.date_scored)]


def _desired(row) -> Dict[int, object]:
    """What a stamped Monitor row says for this Scoring row (price excluded)."""
    if row.composite is None or row.tier1_weighted_average is None or not row.decision:
        raise MonitorError(f"{row.ticker}: the Scoring row has no composite / Tier 1 average / decision -- "
                           f"recalculate the workbook first (a file saved by openpyxl has no cached values)")
    return {C_DATE: _day(row.date_scored), C_COMPOSITE: float(row.composite),
            C_TIER1: round(float(row.tier1_weighted_average), 2), C_DECISION: str(row.decision).strip()}


# ---------------------------------------------------------------- the mirror --

def mirror_problems(path: str) -> List[Tuple[str, List[str]]]:
    """(ticker, [what differs]) for every held, scored ticker whose Monitor row does not mirror its Scoring row --
    a missing row, a different date (older OR newer), composite, Tier 1 average or decision, or no price."""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        ws = wb[SHEET]
        rows = {}
        for r in ws.iter_rows(min_row=FIRST_ROW, values_only=True):
            if r and isinstance(r[0], str) and _TICKER.match(r[0].strip()):
                rows[r[0].strip()] = r
    finally:
        wb.close()
    out = []
    for srow in held_scored(path):
        want = _desired(srow)
        have = rows.get(srow.ticker)
        if have is None:
            out.append((srow.ticker, ["no row on the Monitor tab"]))
            continue
        probs = []
        d = _day(have[C_DATE - 1])
        if d != want[C_DATE]:
            probs.append(f"last scored {d:%m/%d/%y}" if d else "no Last Score Date")
            probs[-1] += f" on the Monitor, {want[C_DATE]:%m/%d/%y} on Scoring"
        comp = have[C_COMPOSITE - 1]
        if not isinstance(comp, (int, float)) or abs(comp - want[C_COMPOSITE]) > COMPOSITE_TOLERANCE:
            probs.append(f"composite {comp if comp is not None else 'blank'} vs {want[C_COMPOSITE]:g}")
        t1 = have[C_TIER1 - 1]
        if not isinstance(t1, (int, float)) or abs(t1 - want[C_TIER1]) > TIER1_TOLERANCE:
            probs.append(f"Tier 1 average {t1 if t1 is not None else 'blank'} vs {want[C_TIER1]:g}")
        dec = str(have[C_DECISION - 1] or "").strip()
        if dec.upper() != want[C_DECISION].upper():
            probs.append(f"decision {dec or 'blank'} vs {want[C_DECISION]}")
        px = have[C_PRICE - 1]
        if not isinstance(px, (int, float)) or px <= 0:
            probs.append("no Price at Last Score")
        if probs:
            out.append((srow.ticker, probs))
    return out


# ------------------------------------------------------------------- prices --

def last_close_on_or_before(ticker: str, day: dt.date, history: Optional[Callable] = None) -> Optional[float]:
    """The last (unadjusted) close on or before ``day``, rounded to cents: the Friday close for a weekend date.
    ``history(ticker, start, end)`` returns a frame with a Close column (yfinance's by default)."""
    if history is None:
        def history(t, start, end):
            import yfinance as yf
            return yf.Ticker(t).history(start=str(start), end=str(end), auto_adjust=False)
    h = history(ticker, day - dt.timedelta(days=7), day + dt.timedelta(days=1))
    if h is None or len(h) == 0:
        return None
    closes = [(i.date() if hasattr(i, "date") else i, float(c)) for i, c in h["Close"].items()]
    closes = [(d, c) for d, c in closes if d <= day and c > 0]
    return round(sorted(closes)[-1][1], 2) if closes else None


def _retry(fn: Callable, args: tuple, ok: Callable, attempts: int, pause: float, sleep: Callable):
    res = None
    for i in range(max(1, attempts)):
        try:
            res = fn(*args)
        except Exception:
            res = None
        if ok(res):
            return res
        if i + 1 < attempts:
            sleep(pause)
    return None


# -------------------------------------------------------------------- stamp --

def stamp(path: str, tickers: Optional[Sequence[str]] = None, *, price_fn: Optional[Callable] = None,
          write: bool = True, attempts: int = 2, pause: float = 1.5, sleep: Callable = time.sleep) -> dict:
    """Stamp Monitor C-G from the Scoring rows for held, scored tickers that do not mirror them (or just
    ``tickers``). Returns {'stamped': [(ticker, old date, new date)], 'unchanged': n, 'no_row': [...],
    'price_failed': [...], 'wrote': bool}. Saves only if something was stamped; the caller recalculates."""
    import openpyxl
    price_fn = price_fn or last_close_on_or_before
    wanted = {t.strip().upper() for t in tickers} if tickers else None
    bad = dict(mirror_problems(path))
    targets = [r for r in held_scored(path) if (r.ticker in wanted if wanted else r.ticker in bad)]
    rep = {"stamped": [], "unchanged": 0, "no_row": [], "price_failed": [], "wrote": False}
    wb = openpyxl.load_workbook(path)
    if SHEET not in wb.sheetnames:
        raise MonitorError(f"the workbook has no '{SHEET}' tab")
    ws = wb[SHEET]
    _check_headers(ws, (C_TICKER, C_DATE, C_COMPOSITE, C_TIER1, C_DECISION, C_PRICE))
    rows = _rows(ws)
    for srow in targets:
        r = rows.get(srow.ticker)
        if r is None:
            rep["no_row"].append(srow.ticker)
            continue
        want = _desired(srow)
        old_date = _day(ws.cell(row=r, column=C_DATE).value)
        px = ws.cell(row=r, column=C_PRICE).value
        if old_date != want[C_DATE] or not isinstance(px, (int, float)) or px <= 0:
            px = _retry(price_fn, (srow.ticker, want[C_DATE]), lambda v: isinstance(v, (int, float)) and v > 0,
                        attempts, pause, sleep)
            if px is None:
                rep["price_failed"].append(srow.ticker)               # whole or nothing: leave the row as it is
                continue
        new = {**want, C_PRICE: round(float(px), 2)}
        changed = False
        for col, val in new.items():
            cur = ws.cell(row=r, column=col).value
            same = (_day(cur) == val) if col == C_DATE else (
                (isinstance(cur, (int, float)) and not isinstance(val, str) and abs(cur - val) < 1e-9)
                or (isinstance(val, str) and str(cur or "").strip() == val))
            if not same:
                ws.cell(row=r, column=col).value = dt.datetime(val.year, val.month, val.day) if col == C_DATE else val
                changed = True
        if changed:
            rep["stamped"].append((srow.ticker, old_date, want[C_DATE]))
        else:
            rep["unchanged"] += 1
    if write and rep["stamped"]:
        wb.save(path)
        rep["wrote"] = True
    return rep


# ------------------------------------------------------------------ signals --

def signals_as_of(path: str) -> Optional[dt.date]:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        return _as_of(wb)
    finally:
        wb.close()


def _as_of_cell(wb):
    if AS_OF_NAME not in wb.defined_names:
        return None
    dest = list(wb.defined_names[AS_OF_NAME].destinations)
    if len(dest) != 1 or dest[0][0] != SHEET:
        return None
    return wb[SHEET][dest[0][1].replace("$", "")]


def _as_of(wb) -> Optional[dt.date]:
    cell = _as_of_cell(wb)
    return _day(cell.value) if cell is not None else None


def add_as_of_cell(wb) -> None:
    """One-time layout: P1 holds the label and Q1 the date (and the defined name MON_SignalsAsOf), so the title note
    narrows from A1:Q1 to A1:O1. Row 1 sits above the Table (A2:Q51), so no merge touches it."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.workbook.defined_name import DefinedName
    ws = wb[SHEET]
    if AS_OF_NAME in wb.defined_names:
        return
    if "A1:Q1" in [str(m) for m in ws.merged_cells.ranges]:
        ws.unmerge_cells("A1:Q1")
        ws.merge_cells("A1:O1")
    thin = Side(style="thin", color="FFA6A6A6")
    for ref, text in ((AS_OF_LABEL_CELL, "Insider / analyst signals refreshed"), (AS_OF_CELL, None)):
        c = ws[ref]
        c.value = text
        c.font = Font(name="Arial Narrow", sz=9, bold=(ref == AS_OF_LABEL_CELL), color="FF002060")
        c.alignment = Alignment(horizontal="right" if ref == AS_OF_LABEL_CELL else "center", vertical="center",
                                wrap_text=True)
        c.border = Border(left=thin, right=thin, top=thin, bottom=thin)
        if ref == AS_OF_CELL:
            c.fill = PatternFill("solid", fgColor="FFDDEBF7")
            c.number_format = "mm/dd/yyyy"
    wb.defined_names[AS_OF_NAME] = DefinedName(AS_OF_NAME, attr_text=f"'{SHEET}'!$Q$1")


def refresh_signals(path: str, tickers: Optional[Sequence[str]] = None, *, insider_fn: Optional[Callable] = None,
                    analyst_fn: Optional[Callable] = None, today: Optional[dt.date] = None, write: bool = True,
                    attempts: int = 2, pause: float = 2.0, sleep: Callable = time.sleep) -> dict:
    """Rewrite Monitor K-N (insider activity / note, analyst shift / note) for held, scored tickers (or just
    ``tickers``) from SEC Form 4 filings and yfinance's consensus, and date the refresh in the as-of cell -- but
    only when every ticker came back, so a partial run stays visibly overdue. A lookup returning (None, None)
    failed: that cell pair keeps what it holds. The one exception is a provider that answers without the history
    the analyst signal needs (``analyst_shift_detail``'s "no_history": yfinance had no 3-month-ago breakdown for six of
    the sixteen held names on 10/5): there is nothing to retry, so the cell keeps its value, the ticker is listed under
    'unavailable', and it does not hold the month open. Returns {'refreshed', 'changed': [(ticker, what)], 'failed',
    'unavailable', 'no_row', 'as_of', 'wrote'}."""
    import openpyxl
    from landry import data_auto
    insider_fn = insider_fn or data_auto.insider_activity_flag
    analyst_fn = analyst_fn or data_auto.analyst_shift_detail
    today = today or dt.date.today()
    wanted = {t.strip().upper() for t in tickers} if tickers else None
    targets = [r.ticker for r in held_scored(path) if wanted is None or r.ticker in wanted]
    wb = openpyxl.load_workbook(path)
    if SHEET not in wb.sheetnames:
        raise MonitorError(f"the workbook has no '{SHEET}' tab")
    ws = wb[SHEET]
    _check_headers(ws, (C_TICKER, C_INSIDER, C_INSIDER_NOTE, C_ANALYST, C_ANALYST_NOTE))
    rows = _rows(ws)
    rep = {"refreshed": 0, "changed": [], "failed": [], "unavailable": [], "no_row": [], "as_of": None, "wrote": False}
    # a result is (flag, note) or (flag, note, why); a flag of Y / N is an answer, and so is a "no_history" (None, None)
    answered = lambda res: res is not None and (res[0] in ("Y", "N") or (len(res) > 2 and res[2] == "no_history"))   # noqa: E731
    for t in targets:
        r = rows.get(t)
        if r is None:
            rep["no_row"].append(t)
            continue
        ins = _retry(insider_fn, (t,), answered, attempts, pause, sleep)
        ana = _retry(analyst_fn, (t,), answered, attempts, pause, sleep)
        failed = ins is None or ana is None
        if failed:
            rep["failed"].append(t + ("" if ins is not None else " (insider)") + ("" if ana is not None else " (analyst)"))
        for res, label in ((ins, "insider"), (ana, "analyst")):
            if res is not None and res[0] is None:
                rep["unavailable"].append(f"{t} ({label}: no history from the provider)")
        what = []
        for res, (cf, cn), label in ((ins, (C_INSIDER, C_INSIDER_NOTE), "insider"),
                                     (ana, (C_ANALYST, C_ANALYST_NOTE), "analyst")):
            if res is None or res[0] is None:
                continue                                            # failed, or nothing to write: the cell pair stays
            flag, note = res[0], res[1]
            old_flag, old_note = ws.cell(row=r, column=cf).value, ws.cell(row=r, column=cn).value
            if (old_flag, old_note or None) != (flag, note or None):
                ws.cell(row=r, column=cf).value = flag
                ws.cell(row=r, column=cn).value = note or None
                what.append(f"{label} {old_flag or '-'}->{flag}")
        if what:
            rep["changed"].append((t, ", ".join(what)))
        if not failed:
            rep["refreshed"] += 1
    cell = _as_of_cell(wb)
    return _day(cell.value) if cell is not None else None


def add_as_of_cell(wb) -> None:
    """One-time layout: P1 holds the label and Q1 the date (and the defined name MON_SignalsAsOf), so the title note
    narrows from A1:Q1 to A1:O1. Row 1 sits above the Table (A2:Q51), so no merge touches it."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.workbook.defined_name import DefinedName
    ws = wb[SHEET]
    if AS_OF_NAME in wb.defined_names:
        return
    if "A1:Q1" in [str(m) for m in ws.merged_cells.ranges]:
        ws.unmerge_cells("A1:Q1")
        ws.merge_cells("A1:O1")
    thin = Side(style="thin", color="FFA6A6A6")
    for ref, text in ((AS_OF_LABEL_CELL, "Insider / analyst signals refreshed"), (AS_OF_CELL, None)):
        c = ws[ref]
        c.value = text
        c.font = Font(name="Arial Narrow", sz=9, bold=(ref == AS_OF_LABEL_CELL), color="FF002060")
        c.alignment = Alignment(horizontal="right" if ref == AS_OF_LABEL_CELL else "center", vertical="center",
                                wrap_text=True)
        c.border = Border(left=thin, right=thin, top=thin, bottom=thin)
        if ref == AS_OF_CELL:
            c.fill = PatternFill("solid", fgColor="FFDDEBF7")
            c.number_format = "mm/dd/yyyy"
    wb.defined_names[AS_OF_NAME] = DefinedName(AS_OF_NAME, attr_text=f"'{SHEET}'!$Q$1")


def refresh_signals(path: str, tickers: Optional[Sequence[str]] = None, *, insider_fn: Optional[Callable] = None,
                    analyst_fn: Optional[Callable] = None, today: Optional[dt.date] = None, write: bool = True,
                    attempts: int = 2, pause: float = 2.0, sleep: Callable = time.sleep) -> dict:
    """Rewrite Monitor K-N (insider activity / note, analyst shift / note) for held, scored tickers (or just
    ``tickers``) from SEC Form 4 filings and yfinance's consensus, and date the refresh in the as-of cell -- but
    only when every ticker came back, so a partial run stays visibly overdue. A lookup returning (None, None)
    failed: that cell pair keeps what it holds. The one exception is a provider that answers without the history
    the analyst signal needs (``analyst_shift_detail``'s "no_history": yfinance had no 3-month-ago breakdown for six of
    the sixteen held names on 10/5): there is nothing to retry, so the cell keeps its value, the ticker is listed under
    'unavailable', and it does not hold the month open. Returns {'refreshed', 'changed': [(ticker, what)], 'failed',
    'unavailable', 'no_row', 'as_of', 'wrote'}."""
    import openpyxl
    from landry import data_auto
    insider_fn = insider_fn or data_auto.insider_activity_flag
    analyst_fn = analyst_fn or data_auto.analyst_shift_detail
    today = today or dt.date.today()
    wanted = {t.strip().upper() for t in tickers} if tickers else None
    targets = [r.ticker for r in held_scored(path) if wanted is None or r.ticker in wanted]
    wb = openpyxl.load_workbook(path)
    if SHEET not in wb.sheetnames:
        raise MonitorError(f"the workbook has no '{SHEET}' tab")
    ws = wb[SHEET]
    _check_headers(ws, (C_TICKER, C_INSIDER, C_INSIDER_NOTE, C_ANALYST, C_ANALYST_NOTE))
    rows = _rows(ws)
    rep = {"refreshed": 0, "changed": [], "failed": [], "unavailable": [], "no_row": [], "as_of": None, "wrote": False}
    # a result is (flag, note) or (flag, note, why); a flag of Y / N is an answer, and so is a "no_history" (None, None)
    answered = lambda res: res is not None and (res[0] in ("Y", "N") or (len(res) > 2 and res[2] == "no_history"))   # noqa: E731
    for t in targets:
        r = rows.get(t)
        if r is None:
            rep["no_row"].append(t)
            continue
        ins = _retry(insider_fn, (t,), answered, attempts, pause, sleep)
        ana = _retry(analyst_fn, (t,), answered, attempts, pause, sleep)
        if ins is None or ana is None:
            rep["failed"].append(t + ("" if ins is not None else " (insider)") + ("" if ana is not None else " (analyst)"))
        for res, label in ((ins, "insider"), (ana, "analyst")):
            if res is not None and res[0] is None:
                rep["unavailable"].append(f"{t} ({label}: no history from the provider)")
        if ins is not None and ins[0] is None:
            ins = None                                              # no answer to write: the cell pair stays
        if ana is not None and ana[0] is None:
            ana = None
        what = []
        for res, (cf, cn), label in ((ins, (C_INSIDER, C_INSIDER_NOTE), "insider"), (ana, (C_ANALYST, C_ANALYST_NOTE), "analyst")):
            if res is None:
                continue
            flag, note = res[0], res[1]
            old_flag, old_note = ws.cell(row=r, column=cf).value, ws.cell(row=r, column=cn).value
            if (old_flag, old_note or None) != (flag, note or None):
                ws.cell(row=r, column=cf).value = flag
                ws.cell(row=r, column=cn).value = note or None
                what.append(f"{label} {old_flag or '-'}->{flag}")
        if what:
            rep["changed"].append((t, ", ".join(what)))
        if t not in "".join(rep["failed"]).split(" ")[0:0] and not any(f.split(" ")[0] == t for f in rep["failed"]):
            rep["refreshed"] += 1
    cell = _as_of_cell(wb)
    complete = not rep["failed"] and not rep["no_row"] and rep["refreshed"] == len(targets) and targets
    dated = False
    if cell is not None and complete and wanted is None:
        rep["as_of"] = today
        if _day(cell.value) != today:
            cell.value = dt.datetime(today.year, today.month, today.day)
            dated = True
    if write and (rep["changed"] or dated):                       # a second run over unchanged data saves nothing
        wb.save(path)
        rep["wrote"] = True
    return rep
