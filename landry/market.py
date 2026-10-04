"""Market Data and earnings dates, refreshed in place from yfinance (added 2026-10-04).

What this replaces: ``landry refresh`` (writes landry_snapshot.json) followed by ``landry export``
(fills a *copy* of the workbook from that file) -- two commands, a ``_filled`` copy to reconcile by
hand, and numbers that were whatever the last refresh happened to leave behind. The Market Data
tab's 9/29 figures were five days stale on 10/4 for exactly that reason.

``refresh`` goes straight from yfinance to the live workbook, for the tickers the two tabs already
list, and touches nothing else:

* Market Data C:I -- price, volume, market cap ($M), P/E, 52-week low/high, dividend yield (%);
* Monitor & Recheck Triggers col J -- Next/Last Earnings Date (Excel has no native field for it).

Rules it keeps, each because of something that has gone wrong in this workbook before:

* never adds, removes or reorders a row -- what the tabs cover is Alan's call (Instructions,
  WORKFLOW POLICY), and row surgery with openpyxl is what made Excel refuse the file on 10/2;
* never blanks a cell the provider has no figure for -- yfinance has no market cap for funds, and
  the two ETF caps in the tab were entered from another source;
* never replaces a future earnings date with a past one (yfinance sometimes knows only last
  quarter's) and never moves a past one further back;
* checks the header of every column it writes, so an inserted column cannot receive the wrong
  numbers, and recognises tickers by content, not by a row bound (a footer is not a ticker);
* writes only value cells -- styles, formulas, other tabs and the Table definitions are untouched.

It does not recalculate or audit; the caller does (``landry weekly`` / ``landry market``).
"""

from __future__ import annotations

import datetime as dt
import math
import re
import time
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

MD_SHEET = "Market Data"
MON_SHEET = "Monitor & Recheck Triggers"
HEADER_ROW = 2
FIRST_ROW = 3

# snapshot field -> (column, text the column's header must contain)
MD_FIELDS = (
    ("price", 3, "price"),
    ("volume", 4, "volume"),
    ("market_cap_m", 5, "market cap"),
    ("pe", 6, "p/e"),
    ("wk52_low", 7, "52-wk low"),
    ("wk52_high", 8, "52-wk high"),
    ("dividend_yield", 9, "div yield"),
)
MON_CATEGORY_COL = 2
MON_EARNINGS_COL = 10
MON_EARNINGS_HEADER = "earnings"

PRICE_MOVE_WARN = 0.30         # vs the cell's previous price: worth a look (a split, bad data), not a refusal
REFERENCE_TOLERANCE = 0.03     # vs the newest Price History close (weekend runs only)
UPCOMING_DAYS = 14             # "held names reporting soon" window in the report

_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class MarketError(RuntimeError):
    pass


def _L(col: int) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(col)


# ------------------------------------------------------------------ layout --

def _rows(ws) -> List[Tuple[int, str]]:
    """(row, ticker) for every row whose column A holds a ticker. Content-based, not a row bound: a
    footer, a label or a formula in column A is not a ticker (the reader-bound bug class, CLAUDE.md)."""
    out = []
    for r in range(FIRST_ROW, ws.max_row + 1):
        v = ws.cell(row=r, column=1).value
        if isinstance(v, str) and _TICKER.match(v.strip()):
            out.append((r, v.strip()))
    return out


def _check_header(ws, col: int, needle: str) -> None:
    head = " ".join(str(ws.cell(row=HEADER_ROW, column=col).value or "").split()).lower()
    if needle not in head:
        raise MarketError(f"{ws.title}: the header of column {_L(col)} is {head!r}, not one mentioning "
                          f"{needle!r} -- the layout has moved; nothing written")


# --------------------------------------------------------------- normalise --

def _num(v) -> Optional[float]:
    """A finite real number, else None (yfinance hands back None, 'Infinity', NaN, strings...)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _same(old, new: float) -> bool:
    o = _num(old)
    return o is not None and math.isclose(o, new, rel_tol=1e-9, abs_tol=1e-12)


def normalize(raw: Mapping, previous: Optional[Mapping] = None) -> Tuple[Dict[str, float], List[str]]:
    """A ``data_auto.market_snapshot`` dict -> ({field: cell value}, warnings). Anything missing or
    unusable is simply absent from the result, which the caller reads as "keep the cell's value"; a
    field present with the value None means "clear the cell" (only a dividend yield the provider
    reports as nothing paid -- see below).
    ``previous`` is the row's current cell values, used only to notice a suspicious change."""
    previous = previous or {}
    out: Dict[str, float] = {}
    warns: List[str] = []

    price = _num(raw.get("price"))
    if price is not None and price > 0:
        out["price"] = price
        old = _num(previous.get("price"))
        if old and abs(price / old - 1) > PRICE_MOVE_WARN:
            warns.append(f"price {price:g} is {price / old - 1:+.0%} from the previous entry {old:g} "
                         f"(a split? a bad quote?)")
    elif raw.get("price") is not None:
        warns.append(f"unusable price {raw.get('price')!r}; left as is")

    vol = _num(raw.get("volume"))
    if vol is not None and vol >= 0:
        out["volume"] = int(round(vol))

    cap = _num(raw.get("market_cap"))
    if cap is not None and cap > 0:
        out["market_cap_m"] = cap / 1e6

    pe = _num(raw.get("pe"))
    if pe is not None and pe > 0:               # yfinance has none for negative earnings
        out["pe"] = pe

    lo, hi = _num(raw.get("wk52_low")), _num(raw.get("wk52_high"))
    if lo is not None and hi is not None and 0 < lo <= hi:
        out["wk52_low"], out["wk52_high"] = lo, hi
    elif lo is not None or hi is not None:
        warns.append(f"52-week range {raw.get('wk52_low')!r}..{raw.get('wk52_high')!r} is not usable; left as is")

    dy = _num(raw.get("dividend_yield"))
    rate = _num(raw.get("dividend_rate"))
    if dy is None or (dy == 0 and not (rate and rate > 0)):
        # Silence from the provider keeps the cell. But a provider that affirmatively reports nothing paid makes
        # a yield left in the cell stale, so it is cleared (found 10/4/26: ETSY, NFLX, PLTR and MLPI carried
        # 0.5-0.8% yields in this tab although none had paid anything). "Nothing paid" is either a trailing
        # twelve-month rate of 0 or a yield of exactly 0 with no dividend rate -- Yahoo returns None for a
        # non-payer on one call and 0.0 on the next (it flipped for CRWD, NFLX and VEEV within two hours on
        # 10/4), and a blank cell is this tab's convention for a non-payer, so both read as blank and a week
        # with nothing new leaves the file alone.
        if dy == 0 or _num(raw.get("dividend_trailing_rate")) == 0:
            out["dividend_yield"] = None
    elif dy >= 0:
        old_dy = _num(previous.get("dividend_yield"))
        if rate and price:                      # a stock: rate / price pins the unit down
            implied = rate / price * 100
            tol = max(0.1, 0.25 * implied)
            if abs(dy - implied) > tol and abs(dy * 100 - implied) <= tol:
                warns.append(f"dividend yield {dy:g} reads as a fraction (rate/price = {implied:.2f}%); "
                             f"stored as {dy * 100:.2f}")
                dy *= 100
        elif old_dy and dy and not (0.05 <= dy / old_dy <= 20):
            warns.append(f"dividend yield {dy:g} against the previous {old_dy:g} -- a unit change upstream? "
                         f"left as is")
            dy = None
        if dy is not None:
            out["dividend_yield"] = dy
    return out, warns


def pick_earnings_date(old, new, today: dt.date) -> Optional[dt.date]:
    """The date to write, or None to leave the cell alone. ``old`` is the cell (datetime/date/None),
    ``new`` what the provider says."""
    if isinstance(new, dt.datetime):
        new = new.date()
    if not isinstance(new, dt.date):
        return None
    old_d = old.date() if isinstance(old, dt.datetime) else old if isinstance(old, dt.date) else None
    if old_d == new:
        return None
    if old_d is None:
        return new
    if old_d >= today:                          # a date still to come: only a later-confirmed date replaces it
        return new if new >= today else None
    return new if (new >= today or new > old_d) else None   # old is past: take a future date or a newer past one


# ------------------------------------------------------------------- fetch --

def _fetch_all(tickers: Sequence[str], fn: Callable, ok: Callable, attempts: int, pause: float,
               sleep: Callable) -> Tuple[Dict[str, object], List[str]]:
    results: Dict[str, object] = {}
    failed: List[str] = []
    for t in tickers:
        res = None
        for i in range(max(1, attempts)):
            try:
                res = fn(t)
            except Exception:
                res = None
            if ok(res):
                break
            if i + 1 < attempts:
                sleep(pause)
        if ok(res):
            results[t] = res
        else:
            failed.append(t)
    return results, failed


# ----------------------------------------------------------------- refresh --

def refresh(path: str, *, snapshot: Optional[Callable] = None, earnings: Optional[Callable] = None,
            today: Optional[dt.date] = None, write: bool = True, tickers: Optional[Sequence[str]] = None,
            do_market: bool = True, do_earnings: bool = True,
            reference: Optional[Mapping[str, float]] = None,
            attempts: int = 2, pause: float = 1.5, sleep: Callable = time.sleep) -> dict:
    """Refresh Market Data and the Monitor tab's earnings dates in ``path``.

    ``reference`` maps ticker -> the newest Price History close; a quote more than 3% away from it is
    reported (pass it only when the quote should equal that close -- the weekend after it).
    Writes (and saves) only if something changed, so a second run over unchanged data leaves the
    file alone. Returns a report; raises ``MarketError`` for a layout problem, before writing."""
    import openpyxl
    if snapshot is None or earnings is None:
        from landry import data_auto
        snapshot = snapshot or data_auto.market_snapshot
        earnings = earnings or data_auto.next_earnings_date
    today = today or dt.date.today()
    wanted = {t.strip().upper() for t in tickers} if tickers else None

    wb = openpyxl.load_workbook(path)
    for name in (MD_SHEET, MON_SHEET):
        if name not in wb.sheetnames:
            raise MarketError(f"the workbook has no '{name}' tab")
    md, mon = wb[MD_SHEET], wb[MON_SHEET]
    for _field, col, needle in MD_FIELDS:
        _check_header(md, col, needle)
    _check_header(mon, MON_EARNINGS_COL, MON_EARNINGS_HEADER)

    report = {
        "market": {"rows": 0, "refreshed": 0, "rows_changed": 0, "cells_changed": 0,
                   "failed": [], "kept": [], "cleared": [], "warnings": []},
        "earnings": {"rows": 0, "updated": [], "unchanged": 0, "kept": [], "no_date": [], "upcoming": []},
        "wrote": False,
    }

    if do_market:
        rep = report["market"]
        rows = [(r, t) for r, t in _rows(md) if wanted is None or t in wanted]
        rep["rows"] = len(rows)
        snaps, rep["failed"] = _fetch_all([t for _, t in rows], snapshot,
                                          lambda s: s is not None and _num(s.get("price")) is not None,
                                          attempts, pause, sleep)
        rep["refreshed"] = len(snaps)
        for r, t in rows:
            raw = snaps.get(t)
            if raw is None:
                continue
            previous = {f: md.cell(row=r, column=c).value for f, c, _ in MD_FIELDS}
            values, warns = normalize(raw, previous)
            ref = (reference or {}).get(t)
            if ref and "price" in values and abs(values["price"] / ref - 1) > REFERENCE_TOLERANCE:
                warns.append(f"price {values['price']:g} is {values['price'] / ref - 1:+.1%} from the newest "
                             f"Price History close {ref:g}")
            rep["warnings"] += [f"{t}: {w}" for w in warns]
            changed = 0
            for f, c, _ in MD_FIELDS:
                cell = md.cell(row=r, column=c)
                if f not in values:
                    if cell.value not in (None, ""):
                        rep["kept"].append((t, f))      # the provider had nothing; what is there stays
                    continue
                new = values[f]
                if new is None:                         # the provider affirmatively reports there is nothing
                    if cell.value not in (None, ""):
                        cell.value = None
                        rep["cleared"].append((t, f))
                        changed += 1
                    continue
                if _same(cell.value, new):
                    continue
                cell.value = new
                changed += 1
            rep["cells_changed"] += changed
            rep["rows_changed"] += 1 if changed else 0

    if do_earnings:
        rep = report["earnings"]
        rows = [(r, t) for r, t in _rows(mon) if wanted is None or t in wanted]
        rep["rows"] = len(rows)
        # a None can be "no date scheduled" as well as a failed call, so it is not retried as hard
        found, _none = _fetch_all([t for _, t in rows], earnings, lambda d: d is not None, attempts, pause, sleep)
        horizon = today + dt.timedelta(days=UPCOMING_DAYS)
        for r, t in rows:
            cell = mon.cell(row=r, column=MON_EARNINGS_COL)
            old = cell.value
            new = found.get(t)
            if new is None:
                rep["no_date"].append(t)
                eff = old
            else:
                pick = pick_earnings_date(old, new, today)
                if pick is None:
                    new_d = new.date() if isinstance(new, dt.datetime) else new
                    old_d = old.date() if isinstance(old, dt.datetime) else old
                    if old_d == new_d:
                        rep["unchanged"] += 1
                    else:
                        rep["kept"].append((t, f"provider's {new_d} against the sheet's {old_d}"))
                    eff = old
                else:
                    cell.value = dt.datetime(pick.year, pick.month, pick.day)
                    rep["updated"].append((t, old.date() if isinstance(old, dt.datetime) else old, pick))
                    eff = pick
            eff_d = eff.date() if isinstance(eff, dt.datetime) else eff
            category = str(mon.cell(row=r, column=MON_CATEGORY_COL).value or "")
            if isinstance(eff_d, dt.date) and today <= eff_d <= horizon and category.startswith("Owned"):
                rep["upcoming"].append((t, eff_d))
        rep["upcoming"].sort(key=lambda x: (x[1], x[0]))

    if write and (report["market"]["cells_changed"] or report["earnings"]["updated"]):
        wb.save(path)
        report["wrote"] = True
    return report
