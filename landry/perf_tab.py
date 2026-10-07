"""The Performance Tracking tab as a lot ledger with a summary block (added 2026-10-05).

One row per purchase LOT, held or sold. Basis "System" rows are the System's own purchases (real entry date,
price, score and band; only they feed the Rule-46 cohort). Basis "Baseline" rows are everything that was
already held on the 8/5/26 first snapshot -- legacy stocks, the dry-powder ETFs, cash and money-market funds,
the shares of ADBE / VRTX / PLD held before the System bought more, and the names sold on 8/7/26 -- measured as
if held since the 8/5/26 close against SPY's 8/5/26 close (Alan's "common baseline date", 10/5/26). Everything
here is a PRICE return on today's holdings: no dividends, interest, deposits or withdrawals (the Drawdown Log is
the portfolio-value record).

Columns A:Q are the original ones. R:AC were added 10/5/26 and feed the summary block under the table:

    R Type            Stock / ETF / Cash. ETF and Cash are "Cash / Cash Equivalents" as Current Positions counts
                      them (Alan's light-green Ticker cells); every Subtotal line leaves them out.
    S Basis           System / Baseline
    T Lot Shares      typed: the shares a System lot bought or an exited lot sold; for a held baseline stock or ETF
                      the share count at the 8/5/26 snapshot -- NOT used in the arithmetic, only by the audit, which
                      flags a gap to Shares (a purchase that reached Current Positions but was never added as a lot);
                      blank for a cash fund, whose balance moves with every flow
    U Shares          = Lot Shares for a System or exited lot; for a held baseline lot the ticker's quantity on
                      Current Positions less its held System lots, so it follows Current Positions by itself
    V Entry Value     = Shares x Entry Price           W Current / Exit Value = Shares x Current / Exit Price
    X Gain / (Loss)   = W - V
    Y YTD Base Price  the 12/31/25 close (baseline lots, a typed constant) or the entry price (System lots)
    Z YTD Base Value  = Shares x YTD Base Price        AA YTD Gain / (Loss) = W - Z      AB YTD Return = W / Z - 1
    AC SPY Gain       = Entry Value x SPY Return Same Period (what the same dollars would have made in SPY)

Current / Exit Price (M) of a held stock or ETF is an INDEX/MATCH into Market Data, so it follows every weekly
refresh with nothing written to this tab; cash funds hold $1.00 and sold lots keep their sale price. The only
things ``landry weekly`` writes here are the two benchmark cells below the table (``set_benchmark``): SPY now
and the as-of date. They are found by their defined names (PT_SPY_Now, PT_AsOf, ...), never by position, because
the block under the table moves whenever the table grows.

The summary block is GENERATED (``build_block``): its formulas carry explicit row bounds, so it is rewritten
below the new last row whenever ``extend_table`` / ``add_lot`` grows the table. That is the one safe way to grow
this tab with openpyxl -- ``insert_rows`` leaves merges, heights and every formula behind (CLAUDE.md, 10/2/26).

The block's second section splits the ledger BY SOURCE (Alan, 10/5/26, after deciding to keep the pre-8/5 shares of
ADBE / VRTX / PLD: "stick with the System"): System lots only, Legacy stocks, Dry-powder ETFs and Cash. Every ledger
row falls in exactly one of them (``source_of``), held and sold alike, so the four add up to the Cumulative TOTAL
line -- the audit checks that -- and the System's own record from entry, against SPY over each lot's own period,
is on the tab without any trade being made to tidy it.
"""

from __future__ import annotations

import copy
import datetime as dt
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

SHEET = "Performance Tracking"
TABLE = "PerformanceTrackingTable"
HEADER_ROW, FIRST_ROW = 2, 3
INCEPTION = dt.date(2026, 8, 5)

# original columns A:Q
(TICKER, COMPANY, ENTRY_DATE, ENTRY_PRICE, SCORE, CONF, BAND, SPY_ENTRY, STATUS, EXIT_DATE, EXIT_PRICE,
 EXIT_REASON, CURRENT, SPY_NOW, RET, SPY_RET, EXCESS) = range(1, 18)
# added columns R:AC
(TYPE, BASIS, LOT_SHARES, SHARES, ENTRY_VALUE, CUR_VALUE, GAIN, YTD_PRICE, YTD_VALUE, YTD_GAIN, YTD_RET,
 SPY_GAIN) = range(18, 30)
LAST_COL = SPY_GAIN

NEW_HEADERS = {
    TYPE: "Type", BASIS: "Basis", LOT_SHARES: "Lot\nShares", SHARES: "Shares", ENTRY_VALUE: "Entry\nValue",
    CUR_VALUE: "Current /\nExit Value", GAIN: "Gain /\n(Loss)", YTD_PRICE: "YTD Base\nPrice",
    YTD_VALUE: "YTD Base\nValue", YTD_GAIN: "YTD Gain /\n(Loss)", YTD_RET: "YTD\nReturn", SPY_GAIN: "SPY Gain\n(same $)"}
NEW_WIDTHS = {TYPE: 7.5, BASIS: 9.5, LOT_SHARES: 9.5, SHARES: 10.5, ENTRY_VALUE: 11.5, CUR_VALUE: 11.5, GAIN: 11.5,
              YTD_PRICE: 10.5, YTD_VALUE: 11.5, YTD_GAIN: 11.5, YTD_RET: 9.0, SPY_GAIN: 11.0}
NEW_FORMATS = {LOT_SHARES: "#,##0.00", SHARES: "#,##0.00", ENTRY_VALUE: "\\$#,##0", CUR_VALUE: "\\$#,##0",
               GAIN: "\\$#,##0_);\\(\\$#,##0\\)", YTD_PRICE: "\\$#,##0.00", YTD_VALUE: "\\$#,##0",
               YTD_GAIN: "\\$#,##0_);\\(\\$#,##0\\)", YTD_RET: "0.0%", SPY_GAIN: "\\$#,##0_);\\(\\$#,##0\\)"}
INPUT_COLS = (TYPE, BASIS, LOT_SHARES, YTD_PRICE)          # typed values: the navy-on-blue style of A:N
TYPES = ("Stock", "ETF", "Cash")
BASES = ("System", "Baseline")
CASH_LIKE = ("ETF", "Cash")                                # Current Positions' "Cash / Cash Equivalents"

NAMES = {"as_of": "PT_AsOf", "spy_now": "PT_SPY_Now", "spy_0805": "PT_SPY_0805", "spy_1231": "PT_SPY_1231",
         "inception": "PT_Inception"}

LIGHT_GREEN, DARK_GREEN = "FFC6EFCE", "FF006100"           # the pair this tab's colour rules already use
LIGHT_RED, DARK_RED = "FFFFC7CE", "FF9C0006"

BASELINE_DRIFT_USD = 1000.0                                # a baseline lot may drift this far (or ...
BASELINE_DRIFT_PCT = 0.01                                  # ... this share of its value) from its 8/5/26 share count
BLOCK_ROWS = 24                                            # header, 7 summary lines, by-source section (header + 5
                                                           # lines), benchmark block (header + 5 lines), footnote
MD_SHEET = "Market Data"
MD_RANGE_END = 300                                         # Market Data is not a Table: a generous fixed range


def L(col: int) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(col)


class PerfTabError(RuntimeError):
    pass


@dataclass
class Lot:
    """One row of the tab. ``basis`` decides which cells are typed and which are formulas."""
    ticker: str
    company: str
    type: str
    basis: str
    status: str
    entry_date: dt.date
    entry_price: float
    lot_shares: Optional[float] = None
    spy_entry: Optional[float] = None                      # None on a baseline lot: it reads PT_SPY_0805
    score: Optional[float] = None
    confidence: Optional[str] = None
    band: Optional[str] = None
    ytd_price: Optional[float] = None                      # a baseline lot's 12/31/25 close; System lots start at entry
    exit_date: Optional[dt.date] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    spy_exit: Optional[float] = None

    def check(self) -> None:
        if self.type not in TYPES:
            raise PerfTabError(f"{self.ticker}: type must be one of {TYPES}, not {self.type!r}")
        if self.basis not in BASES:
            raise PerfTabError(f"{self.ticker}: basis must be one of {BASES}, not {self.basis!r}")
        if self.status not in ("Held", "Exited"):
            raise PerfTabError(f"{self.ticker}: status must be Held or Exited, not {self.status!r}")
        if self.status == "Exited" and (self.exit_price is None or self.exit_date is None or self.lot_shares is None
                                        or self.spy_exit is None):
            raise PerfTabError(f"{self.ticker}: an exited lot needs exit_date, exit_price, lot_shares and spy_exit")
        if self.basis == "System" and self.lot_shares is None:
            raise PerfTabError(f"{self.ticker}: a System lot needs lot_shares (the shares the System bought)")
        if self.basis == "Baseline" and self.status == "Held" and self.type == "Cash" and self.lot_shares is not None:
            raise PerfTabError(f"{self.ticker}: a cash fund's balance moves with every flow -- no Lot Shares snapshot")
        if self.basis == "Baseline" and self.ytd_price is None:
            raise PerfTabError(f"{self.ticker}: a baseline lot needs its 12/31/25 close (ytd_price)")


# ----------------------------------------------------------------- layout --

def table_bounds(ws) -> Tuple[int, int]:
    """(first data row, last data row) from the Table's own ref -- never a fixed number."""
    from openpyxl.utils import range_boundaries
    if TABLE not in ws.tables:
        raise PerfTabError(f"'{ws.title}' has no Table named {TABLE}")
    _c1, _r1, _c2, r2 = range_boundaries(ws.tables[TABLE].ref)
    return FIRST_ROW, r2


def _ticker(v) -> Optional[str]:
    return v.strip() if isinstance(v, str) and re.match(r"^[A-Z][A-Z0-9.\-]{0,9}$", v.strip()) else None


def lot_rows(ws) -> List[int]:
    first, last = table_bounds(ws)
    return [r for r in range(first, last + 1) if _ticker(ws.cell(row=r, column=TICKER).value)]


def free_row(ws) -> Optional[int]:
    first, last = table_bounds(ws)
    for r in range(first, last + 1):
        if ws.cell(row=r, column=TICKER).value in (None, ""):
            return r
    return None


def capture_styles(ws) -> Dict[int, object]:
    """The cell styles of the first data row whose Ticker is not shaded green, for every column: the template for
    new rows (A:Q exist; R:AC after ensure_columns)."""
    first, last = table_bounds(ws)
    src = first
    for r in range(first, last + 1):
        a = ws.cell(row=r, column=TICKER)
        if not (a.fill.fill_type == "solid" and a.fill.fgColor.rgb == LIGHT_GREEN):
            src = r
            break
    return {c: copy.copy(ws.cell(row=src, column=c)._style) for c in range(1, LAST_COL + 1)}


def ensure_columns(ws) -> None:
    """Add R:AC to the Table (header cells, Table columns, widths, styles on every data row). Idempotent."""
    from openpyxl.worksheet.table import TableColumn
    first, last = table_bounds(ws)
    tbl = ws.tables[TABLE]
    have = len(tbl.tableColumns)
    if have >= LAST_COL:
        return
    if have != EXCESS:                                      # the original 17 columns, A:Q
        raise PerfTabError(f"{TABLE} has {have} columns; expected the original {EXCESS} before adding R:AC")
    head_style = copy.copy(ws.cell(row=HEADER_ROW, column=EXCESS)._style)
    input_style = copy.copy(ws.cell(row=FIRST_ROW, column=SPY_ENTRY)._style)
    calc_style = copy.copy(ws.cell(row=FIRST_ROW, column=RET)._style)
    for c in range(TYPE, LAST_COL + 1):
        ws.cell(row=HEADER_ROW, column=c).value = NEW_HEADERS[c]
        ws.cell(row=HEADER_ROW, column=c)._style = copy.copy(head_style)
        ws.column_dimensions[L(c)].width = NEW_WIDTHS[c]
        tbl.tableColumns.append(TableColumn(id=c, name=NEW_HEADERS[c]))
        for r in range(first, last + 1):
            cell = ws.cell(row=r, column=c)
            cell._style = copy.copy(input_style if c in INPUT_COLS else calc_style)
            if c in NEW_FORMATS:
                cell.number_format = NEW_FORMATS[c]
    tbl.ref = f"A{HEADER_ROW}:{L(LAST_COL)}{last}"
    if tbl.autoFilter is not None:
        tbl.autoFilter.ref = tbl.ref


# --------------------------------------------------------------- formulas --

def _md_price(r: int) -> str:
    return (f"=IFERROR(INDEX('{MD_SHEET}'!$C$3:$C${MD_RANGE_END},"
            f"MATCH($A{r},'{MD_SHEET}'!$A$3:$A${MD_RANGE_END},0)),\"\")")


def calc_formulas(r: int, last: int) -> Dict[int, str]:
    """The computed cells of row r. Guarded so a blank spare row stays blank.

    Shares reads other rows of this Table through ranges bounded to rows 3..``last``, never whole columns: column I
    also holds the summary block's Excess figures, which depend on Shares, and a whole-column reference makes that a
    circular reference in Excel (LibreOffice evaluates SUMIFS lazily and does not notice -- ``find_cycles`` does).
    Growing the Table rewrites these ranges (``_refresh_calc``)."""
    rng = lambda col: f"${L(col)}$3:${L(col)}${last}"            # noqa: E731
    return {
        RET: f'=IF(OR(D{r}="",M{r}=""),"",M{r}/D{r}-1)',
        SPY_RET: f'=IF(OR(H{r}="",N{r}=""),"",N{r}/H{r}-1)',
        EXCESS: f'=IF(OR(O{r}="",P{r}=""),"",O{r}-P{r})',
        SHARES: (f'=IF($A{r}="","",IF(OR($S{r}="System",$I{r}="Exited"),$T{r},'
                 f'SUMIFS(CurrentPositionsTable[Quantity],CurrentPositionsTable[Ticker],$A{r})'
                 f'-SUMIFS({rng(LOT_SHARES)},{rng(TICKER)},$A{r},{rng(BASIS)},"System",{rng(STATUS)},"Held")))'),
        ENTRY_VALUE: f'=IF(OR($U{r}="",$D{r}=""),"",$U{r}*$D{r})',
        CUR_VALUE: f'=IF(OR($U{r}="",$M{r}=""),"",$U{r}*$M{r})',
        GAIN: f'=IF(OR($V{r}="",$W{r}=""),"",$W{r}-$V{r})',
        YTD_VALUE: f'=IF(OR($U{r}="",$Y{r}=""),"",$U{r}*$Y{r})',
        YTD_GAIN: f'=IF(OR($Z{r}="",$W{r}=""),"",$W{r}-$Z{r})',
        YTD_RET: f'=IF(OR($Z{r}="",$W{r}=""),"",IF($Z{r}=0,"",$W{r}/$Z{r}-1))',
        SPY_GAIN: f'=IF(OR($V{r}="",$P{r}=""),"",$V{r}*$P{r})',
    }


def _clear_inputs(ws, r: int) -> None:
    for c in list(range(TICKER, CURRENT + 1)) + [SPY_NOW, TYPE, BASIS, LOT_SHARES, YTD_PRICE]:
        ws.cell(row=r, column=c).value = None


def _paint_row(ws, r: int, styles: Dict[int, object]) -> None:
    for c in range(1, LAST_COL + 1):
        ws.cell(row=r, column=c)._style = copy.copy(styles[c])
    ws.row_dimensions[r].height = 15.0


def _green(cell) -> None:
    from openpyxl.styles import Font, PatternFill
    f = cell.font
    cell.fill = PatternFill("solid", fgColor=LIGHT_GREEN)
    cell.font = Font(name=f.name, sz=f.sz, b=f.b, i=f.i, color=DARK_GREEN)


def blank_row(ws, r: int, styles: Dict[int, object], last: Optional[int] = None) -> None:
    """Return row r to a pre-built spare row: styled, formulas in place, nothing typed. ``last`` is the Table's last
    row (its own, unless the Table is about to grow)."""
    last = last or table_bounds(ws)[1]
    _paint_row(ws, r, styles)
    _clear_inputs(ws, r)
    for c, f in calc_formulas(r, last).items():
        ws.cell(row=r, column=c).value = f


def _refresh_calc(ws, last: int) -> None:
    """Rewrite every row's computed formulas for a new last row (their ranges are bounded to it)."""
    for r in range(FIRST_ROW, last + 1):
        for c, f in calc_formulas(r, last).items():
            ws.cell(row=r, column=c).value = f


def write_lot(ws, r: int, lot: Lot, styles: Dict[int, object], last: Optional[int] = None) -> None:
    lot.check()
    blank_row(ws, r, styles, last)
    held = lot.status == "Held"
    v = {TICKER: lot.ticker, COMPANY: lot.company, ENTRY_DATE: _dt(lot.entry_date), ENTRY_PRICE: lot.entry_price,
         SCORE: lot.score, CONF: lot.confidence, BAND: lot.band, STATUS: lot.status, TYPE: lot.type,
         BASIS: lot.basis, LOT_SHARES: lot.lot_shares}
    v[SPY_ENTRY] = lot.spy_entry if lot.spy_entry is not None else f"={NAMES['spy_0805']}"
    if held:
        v[CURRENT] = 1.0 if lot.type == "Cash" else _md_price(r)
        v[SPY_NOW] = f"={NAMES['spy_now']}"
    else:
        v.update({EXIT_DATE: _dt(lot.exit_date), EXIT_PRICE: lot.exit_price, EXIT_REASON: lot.exit_reason,
                  CURRENT: lot.exit_price, SPY_NOW: lot.spy_exit})
    v[YTD_PRICE] = f"=$D{r}" if lot.basis == "System" else lot.ytd_price
    for c, val in v.items():
        if val is not None:
            ws.cell(row=r, column=c).value = val
    if held and lot.type in CASH_LIKE:
        _green(ws.cell(row=r, column=TICKER))


def _dt(d):
    return dt.datetime(d.year, d.month, d.day) if isinstance(d, dt.date) and not isinstance(d, dt.datetime) else d


# ---------------------------------------------------------- summary block --

SUMMARY_LINES = (
    ("Subtotal", "stocks held now -- not the cash & cash equivalents (green); each lot from its entry (8/5/26 close for "
                 "baseline lots); SPY = the same dollars in SPY over each lot's own period"),
    ("TOTAL", "every position held now, cash & cash equivalents included; Value ties to Current Positions' combined total"),
    ("YTD Subtotal", "stocks, held + sold; from the 12/31/25 close (System lots from their entry); SPY 12/31/25 -> now"),
    ("YTD TOTAL", "every position, held + sold; Value includes the proceeds of the sold names"),
    ("Cumulative Subtotal", "stocks, held + sold, since inception 8/5/26 (System lots from their entry); SPY 8/5/26 -> now"),
    ("Cumulative TOTAL (since inception 8/5)", "every position, held + sold; price return only -- no dividends, interest "
                                               "or cash flows"),
    ("Avg Annual Gain/(Loss)", "Cumulative TOTAL / years since 8/5/26, simple not compounded -- extrapolated from under "
                               "a year, not a forecast"),
)
SOURCE_HEADING = "By source (since 8/5; held + sold)"
SOURCE_LINES = (
    ("system", "System lots only (from entry)",
     "the System's own purchases (Basis = System), held + sold, from each entry date; SPY = the same dollars in SPY over "
     "each lot's own period"),
    ("legacy", "Legacy stocks (from the 8/5 close)",
     "Basis = Baseline, stocks: held at the 8/5/26 snapshot (pre-System shares of ADBE / VRTX / PLD included) and sold "
     "8/7/26; same SPY measure"),
    ("etf", "Dry-powder ETFs (from the 8/5 close)",
     "Basis = Baseline, ETFs: held at the 8/5/26 snapshot (and any sold since); same SPY measure"),
    ("cash", "Cash & money funds",
     "Basis = Baseline, cash: money-market and sweep funds at $1.00 -- price return 0%, interest not captured"),
    ("all", "All sources (= Cumulative TOTAL)", None),          # its note is a formula: does the split still add up?
)
SOURCE_ADDS_UP = ("adds up to the Cumulative TOTAL line above (SPY here is each lot's own period, so it can differ a "
                  "little from that line's 8/5 -> now)")
SOURCE_BROKEN = "DOES NOT add up to the Cumulative TOTAL -- a lot has no Basis or no Type"
BENCHMARK_LINES = (
    ("as_of", "Prices as of", "written by `landry weekly` (the last close it saw)"),
    ("spy_now", "SPY current", "written by `landry weekly`"),
    ("spy_0805", "SPY close at inception (8/5/26)", "typed; every baseline lot's SPY at Entry"),
    ("spy_1231", "SPY close 12/31/25", "typed; the YTD benchmark's start"),
    ("inception", "Inception date", "typed; the first Current Positions snapshot"),
)
FOOTNOTE = ("Total Return Since Entry is a simple price return (excludes dividends unless you fold them into "
            "Current/Exit Price manually). At each annual review, snapshot the return here rather than trying to "
            "back-calculate historical 1/3/5-year prices Excel doesn't have. Current prices read Market Data and SPY is "
            "written by the weekly run, so both refresh every Saturday; sold lots keep their sale price; cash and "
            "money-market funds are carried at $1.00 (price return 0%, interest not captured). The summary lines are "
            "holdings-based -- today's shares measured from each lot's baseline -- so realized trades, deposits and "
            "withdrawals and the timing of post-8/5 purchases are not reflected; the Drawdown Log is the portfolio-value "
            "record. Add a position with landry.perf_tab.add_lot.")


def block_top(last: int) -> int:
    return last + 2


def _name_ref(wb, key: str):
    """(sheet, cell) a benchmark name points at, or None."""
    name = NAMES[key]
    if name not in wb.defined_names:
        return None
    dest = list(wb.defined_names[name].destinations)
    if len(dest) != 1:
        return None
    sheet, ref = dest[0]
    return sheet, ref.replace("$", "")


def _read_benchmark(wb, ws) -> Dict[str, object]:
    out = {}
    for key in NAMES:
        ref = _name_ref(wb, key)
        if ref and ref[0] == ws.title:
            out[key] = ws[ref[1]].value
    return out


def _set_name(wb, key: str, sheet: str, cell: str) -> None:
    from openpyxl.workbook.defined_name import DefinedName
    name = NAMES[key]
    if name in wb.defined_names:
        del wb.defined_names[name]
    col = re.match(r"[A-Z]+", cell).group()
    row = cell[len(col):]
    wb.defined_names[name] = DefinedName(name, attr_text=f"'{sheet}'!${col}${row}")


def _clear_block(ws, top: int) -> None:
    """Empty rows top .. top+BLOCK_ROWS-1: merges first (a write into a merged range is silently dropped), then
    values and styles, then the heights."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill
    bottom = top + BLOCK_ROWS - 1
    for m in list(ws.merged_cells.ranges):
        if m.min_row >= top and m.max_row <= bottom:
            ws.unmerge_cells(str(m))
    for r in range(top, bottom + 1):
        ws.row_dimensions[r].height = None
        for c in range(1, LAST_COL + 1):
            cell = ws.cell(row=r, column=c)
            cell.value = None
            cell.font, cell.fill, cell.border, cell.alignment = Font(), PatternFill(), Border(), Alignment()
            cell.number_format = "General"


def source_of(basis, type_) -> Optional[str]:
    """Which by-source line a lot belongs to -- 'system', 'legacy', 'etf' or 'cash' -- or None when its Basis or
    Type is missing or unknown (such a lot would be in the Cumulative TOTAL but in none of the four lines, which is
    exactly what the 'All sources' note and the audit's ``sources`` check exist to catch)."""
    if basis == "System":
        return "system"
    if basis == "Baseline":
        return {"Stock": "legacy", "ETF": "etf", "Cash": "cash"}.get(type_)
    return None


def block_lines(ws, last: int) -> Dict[str, int]:
    """{label: row} of every labelled line in the summary block (column A), for the audit and the tests."""
    top = block_top(last)
    return {ws.cell(row=r, column=1).value: r for r in range(top, top + BLOCK_ROWS)
            if isinstance(ws.cell(row=r, column=1).value, str)}


def _paint_header(ws, r: int, heads: Dict[int, str], base: Dict[str, object], navy: str) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    for c in range(1, 18):
        cell = ws.cell(row=r, column=c)
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.font = Font(bold=True, color="FFFFFFFF", **base)
        cell.alignment = Alignment(horizontal="left" if c in (1, 10) else "center", vertical="center")
        cell.value = heads.get(c)
    ws.row_dimensions[r].height = 18.0


def build_block(wb, ws, last: int, benchmark: Optional[Dict[str, object]] = None) -> int:
    """(Re)write the summary block below the table. Returns its top row. ``benchmark`` carries the five
    benchmark values (as_of, spy_now, spy_0805, spy_1231, inception) -- read from the old block when it moves."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    bm = dict(benchmark or {})
    top = block_top(last)
    _clear_block(ws, top)
    thin = Side(style="thin", color="FFA6A6A6")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    base = dict(name="Arial Narrow", sz=9)
    navy = "FF1F3864"

    # header
    heads = {1: "Performance summary", 4: "Basis ($)", 5: "Value ($)", 6: "Gain/(Loss) ($)", 7: "Return", 8: "SPY",
             9: "Excess", 10: "How it is measured"}
    _paint_header(ws, top, heads, base, navy)

    rng = lambda col: f"${L(col)}$3:${L(col)}${last}"            # noqa: E731
    stock = f'{rng(TYPE)},"Stock"'
    held = f'{rng(STATUS)},"Held"'
    yrs = f"({NAMES['as_of']}-{NAMES['inception']})/365"
    r1 = top + 1
    rows = {k: r1 + i for i, k in enumerate(("sub", "tot", "ysub", "ytot", "csub", "ctot", "avg"))}
    f = {}
    f["sub"] = (f"=SUMIFS({rng(ENTRY_VALUE)},{stock},{held})", f"=SUMIFS({rng(CUR_VALUE)},{stock},{held})",
                f"=SUMIFS({rng(SPY_GAIN)},{stock},{held})")
    f["tot"] = (f"=SUMIFS({rng(ENTRY_VALUE)},{held})", f"=SUMIFS({rng(CUR_VALUE)},{held})",
                f"=SUMIFS({rng(SPY_GAIN)},{held})")
    f["ysub"] = (f"=SUMIFS({rng(YTD_VALUE)},{stock})", f"=SUMIFS({rng(CUR_VALUE)},{stock})", None)
    f["ytot"] = (f"=SUM({rng(YTD_VALUE)})", f"=SUM({rng(CUR_VALUE)})", None)
    f["csub"] = (f"=SUMIFS({rng(ENTRY_VALUE)},{stock})", f"=SUMIFS({rng(CUR_VALUE)},{stock})", None)
    f["ctot"] = (f"=SUM({rng(ENTRY_VALUE)})", f"=SUM({rng(CUR_VALUE)})", None)
    spy_bench = {"ysub": f"={NAMES['spy_now']}/{NAMES['spy_1231']}-1", "ytot": f"={NAMES['spy_now']}/{NAMES['spy_1231']}-1",
                 "csub": f"={NAMES['spy_now']}/{NAMES['spy_0805']}-1", "ctot": f"={NAMES['spy_now']}/{NAMES['spy_0805']}-1"}
    usd = "\\$#,##0_);\\(\\$#,##0\\)"
    pct = "0.0%_);\\(0.0%\\)"
    for i, (label, how) in enumerate(SUMMARY_LINES):
        key = tuple(rows)[i]
        r = rows[key]
        total = key in ("tot", "ytot", "ctot")
        ws.cell(row=r, column=1).value = label
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
        for c in range(1, 11):
            cell = ws.cell(row=r, column=c)
            cell.font = Font(bold=total or key == "avg", **base)
            cell.border = box
            cell.fill = PatternFill("solid", fgColor="FFDDEBF7" if total or key == "avg" else "FFF2F2F2")
            cell.alignment = Alignment(horizontal="left" if c == 1 else "center", vertical="center")
        if key == "avg":
            ws.cell(row=r, column=6).value = f'=IF({yrs}>0,F{rows["ctot"]}/({yrs}),"")'
            ws.cell(row=r, column=7).value = f'=IF({yrs}>0,G{rows["ctot"]}/({yrs}),"")'
            ws.cell(row=r, column=8).value = f'=IF({yrs}>0,H{rows["ctot"]}/({yrs}),"")'
        else:
            basis, value, spy_gain = f[key]
            ws.cell(row=r, column=4).value = basis
            ws.cell(row=r, column=5).value = value
            ws.cell(row=r, column=6).value = f"=E{r}-D{r}"
            ws.cell(row=r, column=7).value = f'=IF(D{r}=0,"",F{r}/D{r})'
            ws.cell(row=r, column=8).value = (f'=IF(D{r}=0,"",{spy_gain[1:]}/D{r})' if spy_gain else spy_bench[key])
        ws.cell(row=r, column=9).value = f'=IF(OR(G{r}="",H{r}=""),"",G{r}-H{r})'
        for c in (4, 5, 6):
            ws.cell(row=r, column=c).number_format = usd
        for c in (7, 8, 9):
            ws.cell(row=r, column=c).number_format = pct
        d = ws.cell(row=r, column=10)
        d.value = how
        d.font = Font(italic=True, color="FF808080", name="Arial Narrow", sz=8)
        d.border = Border()
        d.fill = PatternFill()
        d.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[r].height = 15.0

    # by source: every ledger row is in exactly one of the four lines (``source_of``), held and sold alike, so they
    # add up to the Cumulative TOTAL. SPY is the same dollars over each lot's own period on every line -- the right
    # yardstick for System lots, which start on different days.
    sh = r1 + len(SUMMARY_LINES) + 1
    _paint_header(ws, sh, {**heads, 1: SOURCE_HEADING}, base, navy)
    skeys = tuple(k for k, _label, _how in SOURCE_LINES)
    srows = {k: sh + 1 + i for i, k in enumerate(skeys)}
    parts = [k for k in skeys if k != "all"]
    crit = {"system": f'{rng(BASIS)},"System"',
            "legacy": f'{rng(BASIS)},"Baseline",{rng(TYPE)},"Stock"',
            "etf": f'{rng(BASIS)},"Baseline",{rng(TYPE)},"ETF"',
            "cash": f'{rng(BASIS)},"Baseline",{rng(TYPE)},"Cash"'}
    for key, label, how in SOURCE_LINES:
        r = srows[key]
        total = key == "all"
        ws.cell(row=r, column=1).value = label
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
        for c in range(1, 11):
            cell = ws.cell(row=r, column=c)
            cell.font = Font(bold=total, **base)
            cell.border = box
            cell.fill = PatternFill("solid", fgColor="FFDDEBF7" if total else "FFF2F2F2")
            cell.alignment = Alignment(horizontal="left" if c == 1 else "center", vertical="center")
        if total:
            ws.cell(row=r, column=4).value = "=" + "+".join(f"D{srows[k]}" for k in parts)
            ws.cell(row=r, column=5).value = "=" + "+".join(f"E{srows[k]}" for k in parts)
            spy_sum = "+".join(f"SUMIFS({rng(SPY_GAIN)},{crit[k]})" for k in parts)
            ws.cell(row=r, column=8).value = f'=IF(D{r}=0,"",({spy_sum})/D{r})'
            how = (f'=IF(AND(ABS(D{r}-D{rows["ctot"]})<0.5,ABS(E{r}-E{rows["ctot"]})<0.5),'
                   f'"{SOURCE_ADDS_UP}","{SOURCE_BROKEN}")')
        else:
            ws.cell(row=r, column=4).value = f"=SUMIFS({rng(ENTRY_VALUE)},{crit[key]})"
            ws.cell(row=r, column=5).value = f"=SUMIFS({rng(CUR_VALUE)},{crit[key]})"
            ws.cell(row=r, column=8).value = f'=IF(D{r}=0,"",SUMIFS({rng(SPY_GAIN)},{crit[key]})/D{r})'
        ws.cell(row=r, column=6).value = f"=E{r}-D{r}"
        ws.cell(row=r, column=7).value = f'=IF(D{r}=0,"",F{r}/D{r})'
        ws.cell(row=r, column=9).value = f'=IF(OR(G{r}="",H{r}=""),"",G{r}-H{r})'
        for c in (4, 5, 6):
            ws.cell(row=r, column=c).number_format = usd
        for c in (7, 8, 9):
            ws.cell(row=r, column=c).number_format = pct
        d = ws.cell(row=r, column=10)
        d.value = how
        d.font = Font(italic=True, color="FF808080", name="Arial Narrow", sz=8)
        d.border = Border()
        d.fill = PatternFill()
        d.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[r].height = 15.0

    # benchmark & dates
    bh = sh + len(SOURCE_LINES) + 2
    ws.cell(row=bh, column=1).value = "Benchmark & dates"
    for c in range(1, 18):
        cell = ws.cell(row=bh, column=c)
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.font = Font(bold=True, color="FFFFFFFF", **base)
        cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[bh].height = 18.0
    defaults = {"as_of": None, "spy_now": None, "spy_0805": None, "spy_1231": None, "inception": INCEPTION}
    for i, (key, label, how) in enumerate(BENCHMARK_LINES):
        r = bh + 1 + i
        ws.cell(row=r, column=1).value = label
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
        for c in range(1, 5):
            cell = ws.cell(row=r, column=c)
            cell.font = Font(**base)
            cell.border = box
            cell.alignment = Alignment(horizontal="left" if c == 1 else "center", vertical="center")
        val = bm.get(key, defaults[key])
        cell = ws.cell(row=r, column=4)
        cell.value = _dt(val) if key in ("as_of", "inception") else val
        cell.number_format = "mm/dd/yyyy" if key in ("as_of", "inception") else "\\$#,##0.00"
        cell.fill = PatternFill("solid", fgColor="FFDDEBF7")
        cell.font = Font(color="FF002060", **base)
        d = ws.cell(row=r, column=5)
        d.value = how
        d.font = Font(italic=True, color="FF808080", name="Arial Narrow", sz=8)
        d.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[r].height = 15.0
        _set_name(wb, key, ws.title, f"D{r}")

    # footnote
    fr = bh + len(BENCHMARK_LINES) + 2
    note = ws.cell(row=fr, column=1)
    note.value = FOOTNOTE
    note.font = Font(italic=True, color="FF808080", name="Arial Narrow", sz=11)
    note.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.merge_cells(start_row=fr, start_column=1, end_row=fr, end_column=17)
    ws.row_dimensions[fr].height = 72.0
    assert fr == top + BLOCK_ROWS - 1, (fr, top)
    _set_rules(ws, last, [rows[k] for k in rows] + [srows[k] for k in skeys])
    return top


# ------------------------------------------------- validations and colours --

def _set_rules(ws, last: int, summary_rows: List[int]) -> None:
    """Data validations (Status, Band, Type, Basis) and the green / red colour rules, all to ``last``."""
    from openpyxl.formatting.formatting import ConditionalFormattingList
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.cell_range import MultiCellRange
    from openpyxl.worksheet.datavalidation import DataValidation
    ws.data_validations.dataValidation = [
        dv for dv in ws.data_validations.dataValidation
        if not any(str(dv.sqref).startswith(x) for x in ("I", "G", "R", "S"))]
    for col, items in ((STATUS, "Held,Exited"), (BAND, "Strong Buy,Buy"), (TYPE, ",".join(TYPES)),
                       (BASIS, ",".join(BASES))):
        dv = DataValidation(type="list", formula1=f'"{items}"', allow_blank=True)
        dv.sqref = MultiCellRange(f"{L(col)}3:{L(col)}{last}")
        ws.data_validations.dataValidation.append(dv)
    cf = ConditionalFormattingList()
    ranges = [f"{L(c)}3:{L(c)}{last}" for c in (RET, SPY_RET, EXCESS, GAIN, YTD_GAIN, YTD_RET, SPY_GAIN)]
    ranges += [f"F{r}:I{r}" for r in summary_rows]
    for rng in ranges:
        cf.add(rng, CellIsRule(operator="greaterThan", formula=["0"], font=Font(color=DARK_GREEN),
                               fill=PatternFill(bgColor=LIGHT_GREEN)))
        cf.add(rng, CellIsRule(operator="lessThan", formula=["0"], font=Font(color=DARK_RED),
                               fill=PatternFill(bgColor=LIGHT_RED)))
    ws.conditional_formatting = cf


# ------------------------------------------------------------ table growth --

def _grow(wb, ws, add: int) -> int:
    """Extend the Table by ``add`` spare rows and regenerate the summary block below them. Returns the new last
    data row. The block is cleared BEFORE its rows become Table rows (a merged cell inside a Table is something
    Excel cannot represent)."""
    if add <= 0:
        return table_bounds(ws)[1]
    _first, last = table_bounds(ws)
    bm = _read_benchmark(wb, ws)
    styles = capture_styles(ws)
    _clear_block(ws, block_top(last))
    new_last = last + add
    for r in range(last + 1, new_last + 1):
        blank_row(ws, r, styles, new_last)
    _refresh_calc(ws, new_last)
    tbl = ws.tables[TABLE]
    tbl.ref = f"A{HEADER_ROW}:{L(LAST_COL)}{new_last}"
    if tbl.autoFilter is not None:
        tbl.autoFilter.ref = tbl.ref
    build_block(wb, ws, new_last, bm)
    return new_last


def extend_table(wb, add: int = 5) -> int:
    """Grow the Table by ``add`` spare rows (the summary block moves below them). Returns the new last row."""
    return _grow(wb, wb[SHEET], add)


def rebuild_block(wb) -> int:
    """Regenerate the summary block in place, carrying the benchmark values over -- after a hand edit damaged it, or
    after a change to this module's formulas. Returns the block's top row."""
    ws = wb[SHEET]
    _first, last = table_bounds(ws)
    return build_block(wb, ws, last, _read_benchmark(wb, ws))


def add_lot(wb, lot: Lot, grow_by: int = 5) -> int:
    """Write ``lot`` into the first spare row, growing the Table (and moving the summary block) when there is none.
    Returns the row. A new DCA purchase is one System lot: the ticker's baseline lot then gives up those shares
    automatically (its Shares = the quantity on Current Positions less its held System lots)."""
    ws = wb[SHEET]
    r = free_row(ws)
    if r is None:
        _grow(wb, ws, grow_by)
        r = free_row(ws)
    write_lot(ws, r, lot, capture_styles(ws))
    return r


def close_lot(ws, row: int, exit_date: dt.date, exit_price: float, reason: str, spy_exit: float,
              shares: Optional[float] = None) -> None:
    """Mark a held lot Exited. An exited lot's Shares are its typed Lot Shares, so a baseline lot -- whose Lot Shares
    is only the 8/5/26 snapshot, and whose quantity on Current Positions no longer includes what was sold --
    needs ``shares``, the shares sold, which replaces the snapshot. A System lot keeps its own."""
    if ws.cell(row=row, column=STATUS).value != "Held":
        raise PerfTabError(f"row {row} is not a held lot")
    if ws.cell(row=row, column=BASIS).value == "Baseline":
        if shares is None:
            raise PerfTabError("a baseline lot needs the number of shares sold")
        ws.cell(row=row, column=LOT_SHARES).value = shares
    elif shares is not None and shares != ws.cell(row=row, column=LOT_SHARES).value:
        raise PerfTabError("a System lot is sold whole (its Lot Shares); record a partial sale as a new lot")
    ws.cell(row=row, column=STATUS).value = "Exited"
    ws.cell(row=row, column=EXIT_DATE).value = _dt(exit_date)
    ws.cell(row=row, column=EXIT_PRICE).value = exit_price
    ws.cell(row=row, column=EXIT_REASON).value = reason
    ws.cell(row=row, column=CURRENT).value = exit_price
    ws.cell(row=row, column=SPY_NOW).value = spy_exit
    ticker = ws.cell(row=row, column=TICKER)
    from openpyxl.styles import PatternFill
    if ticker.fill.fill_type == "solid" and ticker.fill.fgColor.rgb == LIGHT_GREEN:      # sold: no longer a holding
        ticker.fill = copy.copy(ws.cell(row=FIRST_ROW, column=COMPANY).fill)
        f = ticker.font
        from openpyxl.styles import Font
        ticker.font = Font(name=f.name, sz=f.sz, b=f.b, i=f.i, color="FF002060")


def split_baseline_sale(wb, ticker: str, shares: float, exit_date: dt.date, exit_price: float, spy_exit: float,
                        reason: str) -> Tuple[int, int]:
    """Record a PARTIAL sale of a held baseline lot (the 10/6/26 SPMO sale: 30 of 176.94 shares, which ``close_lot`` cannot
    do -- it closes a lot whole). The sold shares become an Exited baseline lot with the same entry (8/5 close, the 12/31/25
    price, SPY at 8/5) and the sale's price, date and SPY; the held lot keeps the rest, its typed Lot Shares reduced by
    what was sold so the audit's share tie still reads zero. Returns (the sold lot's row, the held lot's row).

    The ticker must have exactly one held baseline lot (two accounts' shares share it: SPMO's 100 at Fidelity and 76.9 at
    Chase are one lot of 176.94 on this tab)."""
    ws = wb[SHEET]
    held = [r for r in lot_rows(ws) if _ticker(ws.cell(row=r, column=TICKER).value) == ticker
            and ws.cell(row=r, column=BASIS).value == "Baseline" and ws.cell(row=r, column=STATUS).value == "Held"]
    if len(held) != 1:
        raise PerfTabError(f"{ticker}: expected exactly one held baseline lot, found {len(held)}")
    row = held[0]
    old = ws.cell(row=row, column=LOT_SHARES).value
    if not isinstance(old, (int, float)) or not 0 < shares < old - 1e-9:
        raise PerfTabError(f"{ticker}: {shares} shares is not a part of the held lot's {old}")
    entry = ws.cell(row=row, column=ENTRY_DATE).value
    sold = Lot(ticker, ws.cell(row=row, column=COMPANY).value, ws.cell(row=row, column=TYPE).value, "Baseline", "Exited",
               entry.date() if isinstance(entry, dt.datetime) else entry, ws.cell(row=row, column=ENTRY_PRICE).value,
               lot_shares=shares, ytd_price=ws.cell(row=row, column=YTD_PRICE).value, exit_date=exit_date,
               exit_price=exit_price, exit_reason=reason, spy_exit=spy_exit)
    sold_row = add_lot(wb, sold)
    ws.cell(row=row, column=LOT_SHARES).value = round(old - shares, 6)
    return sold_row, row


def rebase_baseline(wb) -> List[Tuple[str, float, float]]:
    """Set every held baseline stock / ETF lot's typed Lot Shares to what Current Positions implies now (its
    quantity less the ticker's held System lots) -- the step after a gap the audit flagged turns out to be dividend
    reinvestment or another change that is no purchase. Returns (ticker, old, new) for each lot changed. Quantities
    are read from the typed cells of Current Positions, so this works on a workbook that was not recalculated."""
    from collections import defaultdict
    ws = wb[SHEET]
    cp = wb["Current Positions"]
    qty = defaultdict(float)
    for r in range(3, cp.max_row + 1):
        t, q = cp.cell(row=r, column=2).value, cp.cell(row=r, column=5).value
        if _ticker(t) and isinstance(q, (int, float)) and q > 0:
            qty[t.strip()] += q
    system = defaultdict(float)
    rows = lot_rows(ws)
    for r in rows:
        if (ws.cell(row=r, column=BASIS).value == "System" and ws.cell(row=r, column=STATUS).value == "Held"
                and isinstance(ws.cell(row=r, column=LOT_SHARES).value, (int, float))):
            system[ws.cell(row=r, column=TICKER).value.strip()] += ws.cell(row=r, column=LOT_SHARES).value
    changed = []
    for r in rows:
        if (ws.cell(row=r, column=BASIS).value == "Baseline" and ws.cell(row=r, column=STATUS).value == "Held"
                and ws.cell(row=r, column=TYPE).value != "Cash"):
            t = ws.cell(row=r, column=TICKER).value.strip()
            new = round(qty.get(t, 0.0) - system.get(t, 0.0), 6)
            old = ws.cell(row=r, column=LOT_SHARES).value
            if not (isinstance(old, (int, float)) and abs(old - new) < 5e-7):
                ws.cell(row=r, column=LOT_SHARES).value = new
                changed.append((t, old, new))
    return changed


# --------------------------------------------------------------- benchmark --

def set_benchmark(wb, spy_now: Optional[float] = None, as_of: Optional[dt.date] = None) -> dict:
    """Write SPY now and the as-of date (what ``landry weekly`` calls). Only a changed value is written."""
    rep = {"present": False, "changed": False, "spy_old": None, "spy_new": None, "as_of_old": None, "as_of_new": None}
    refs = {k: _name_ref(wb, k) for k in ("spy_now", "as_of")}
    if SHEET not in wb.sheetnames or not all(refs.values()) or any(r[0] != SHEET for r in refs.values()):
        return rep
    rep["present"] = True
    ws = wb[SHEET]
    spy_cell, date_cell = ws[refs["spy_now"][1]], ws[refs["as_of"][1]]
    rep["spy_old"] = spy_cell.value
    old_d = date_cell.value
    rep["as_of_old"] = old_d.date() if isinstance(old_d, dt.datetime) else old_d
    if spy_now is not None and not (isinstance(spy_cell.value, (int, float)) and abs(spy_cell.value - spy_now) < 5e-5):
        spy_cell.value = round(float(spy_now), 4)
        rep["changed"] = True
    if as_of is not None and rep["as_of_old"] != as_of:
        date_cell.value = _dt(as_of)
        rep["changed"] = True
    rep["spy_new"], rep["as_of_new"] = spy_cell.value, as_of if as_of is not None else rep["as_of_old"]
    return rep


def held_pricing_rows(ws) -> List[Tuple[int, str]]:
    """(row, ticker) of every held lot whose price is a Market Data lookup (a held stock or ETF)."""
    out = []
    for r in lot_rows(ws):
        if ws.cell(row=r, column=STATUS).value == "Held" and ws.cell(row=r, column=TYPE).value != "Cash":
            out.append((r, ws.cell(row=r, column=TICKER).value.strip()))
    return out


# ------------------------------------------------------- circular references --

def find_cycles(wb) -> List[str]:
    """Range-level dependency cycles among this sheet's own formulas, each as 'A1 -> B2 -> ... -> A1'; [] when none.

    This is what Excel reports as a circular reference: a formula depends on EVERY cell of any range it reads,
    whether or not the function would need that cell's value. LibreOffice evaluates SUMIFS lazily and calculates
    such a workbook without a murmur, so a recalc proves nothing here -- the whole-column ranges in Shares did
    exactly that on 2026-10-05 (column I also holds the summary's Excess formulas). References to other sheets and
    to other tables' structured columns cannot lead back to this sheet and are skipped; defined names resolve to
    their cells."""
    from openpyxl.formula import Tokenizer
    from openpyxl.utils import range_boundaries
    ws = wb[SHEET]
    max_row = max(ws.max_row, 1)
    name_cells = {}
    for key, name in NAMES.items():
        ref = _name_ref(wb, key)
        if ref and ref[0] == SHEET:
            name_cells[name] = ref[1]

    def cells(ref: str):
        b = range_boundaries(ref.replace("$", ""))
        c1, r1, c2, r2 = b
        r1, r2 = r1 or 1, min(r2 or max_row, max_row)
        return {f"{L(c)}{r}" for c in range(c1, c2 + 1) for r in range(r1, r2 + 1)}

    deps: Dict[str, set] = {}
    for row in ws.iter_rows():
        for cell in row:
            v = cell.value
            if not (isinstance(v, str) and v.startswith("=")):
                continue
            refs = set()
            for tok in Tokenizer(v).items:
                if tok.type != "OPERAND" or tok.subtype != "RANGE":
                    continue
                ref = tok.value
                if "[" in ref:
                    continue                                    # a structured reference: another table's columns
                if "!" in ref:
                    sheet, ref = ref.rsplit("!", 1)
                    if sheet.strip("'") != SHEET:
                        continue
                ref = name_cells.get(ref, ref)
                try:
                    refs |= cells(ref)
                except Exception:
                    continue
            deps[cell.coordinate] = refs

    state: Dict[str, int] = {}                                  # 1 = on the DFS path, 2 = done
    cycles: List[str] = []
    for start in deps:
        if state.get(start):
            continue
        stack = [(start, iter(sorted(deps.get(start, ()))))]
        path = [start]
        state[start] = 1
        while stack and len(cycles) < 3:
            node, it = stack[-1]
            nxt = next((n for n in it if n in deps or state.get(n) == 1), None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                path.pop()
                continue
            if state.get(nxt) == 1:
                cycles.append(" -> ".join(path[path.index(nxt):] + [nxt]))
                continue
            if state.get(nxt) == 2:
                continue
            state[nxt] = 1
            path.append(nxt)
            stack.append((nxt, iter(sorted(deps.get(nxt, ())))))
    return cycles


# ----------------------------------------------------------------- reading --

def read_lots(path_or_wb) -> List[dict]:
    """Every lot as a dict keyed by field name, from the cached values (the workbook must have been recalculated).
    Used by the audit and the tests; a row counts only inside the Table and only with a ticker in column A."""
    import openpyxl
    wb = openpyxl.load_workbook(path_or_wb, data_only=True) if isinstance(path_or_wb, str) else path_or_wb
    ws = wb[SHEET]
    keys = {TICKER: "ticker", COMPANY: "company", ENTRY_DATE: "entry_date", ENTRY_PRICE: "entry_price", SCORE: "score",
            CONF: "confidence", BAND: "band", SPY_ENTRY: "spy_entry", STATUS: "status", EXIT_DATE: "exit_date",
            EXIT_PRICE: "exit_price", EXIT_REASON: "exit_reason", CURRENT: "current", SPY_NOW: "spy_now",
            RET: "ret", SPY_RET: "spy_ret", EXCESS: "excess", TYPE: "type", BASIS: "basis", LOT_SHARES: "lot_shares",
            SHARES: "shares", ENTRY_VALUE: "entry_value", CUR_VALUE: "cur_value", GAIN: "gain", YTD_PRICE: "ytd_price",
            YTD_VALUE: "ytd_value", YTD_GAIN: "ytd_gain", YTD_RET: "ytd_ret", SPY_GAIN: "spy_gain"}
    _first, last = table_bounds(ws)
    out = []
    for r in lot_rows(ws):
        row = {name: ws.cell(row=r, column=c).value for c, name in keys.items()}
        row["row"] = r
        out.append(row)
    return out
