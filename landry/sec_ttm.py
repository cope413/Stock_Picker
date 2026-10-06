"""Trailing-12-month Tier 1 inputs from SEC XBRL company facts, and the comparison against the scores of record.

Why this exists (2026-10-05, Journal rows 92-94): the Tier 1 FCF indicators are drafted from fiscal-year statements,
so a company's score can trail its filings by most of a year. AVGO and VRTX sat in Exit Review under Rule 3 on FY2025
figures while their latest 10-Qs showed after-SBC free cash flow up 60% and 24% on the year. Alan directed a re-score
on the latest 10-Q figures, then (Open Items #31) adopted this policy: fiscal-year scores stay the record; every
US-filer holding is compared against its latest 10-Q at each quarterly review and before any rule-forced action; and a
re-score on trailing windows happens only where the comparison changes a hard-rule outcome or a Decision band, in
either direction. ``--holdings`` is that comparison.

Windows are ANNIVERSARY-ALIGNED: the 12 months ending at the newest 10-Q quarter-end, and the same 12 months one,
two, ... years earlier. They do not overlap, and they have the shape the rubric was written for ("five full fiscal
years where available"). TTM(e) = FY(ending before the YTD period) + YTD(s, e) - YTD(the prior year's s, e). When the
newest filing is a 10-K the windows are simply the fiscal years. Windows older than the first complete run of filings
are dropped (down to four), so a company whose old XBRL tags are patchy still gets its recent windows.

FCF follows Part 10: cash from operations less capital expenditure (and capitalised software, where reported) less
stock-based compensation. Part 10 also says FCF is "adjusted for ... acquisitions ... and other nonrecurring items",
which a mechanical series cannot know; ``landry_addbacks.json`` records each documented one-time operating cash cost
(VRTX's $4.4B Alpine payment, booked as acquired IPR&D through operating cash flow in May 2024) and the tool adds it
back to the window it falls in. An add-back is a judgment: it is Alan's to approve and is also written in the score's
``adaptation`` field.

SEC's company-facts feed can lag EDGAR by a quarter (Visa's June 2026 10-Q was listed on EDGAR but missing from its
facts on 10/5), so every check compares its newest window with EDGAR's newest 10-Q/10-K and says when they differ.

Usage: ``python -m landry.sec_ttm VRTX`` | ``python -m landry.sec_ttm --holdings [--workbook PATH]``.
``--holdings`` exits 2 when any holding's hard-rule outcome or Decision band differs between the two bases.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from landry import data_auto
from landry.fundamentals import (Draft, cagr, draft_fcf_margin_trend, draft_fcf_yield_trend,
                                 draft_revenue_growth, growth_cv, trend_direction)
from landry.scoring import ALL_WEIGHTS, IndicatorScore, classify, composite_score, rule_flags

#: line item -> us-gaap tags in priority order (the first tag that reports a period supplies it)
TAGS: Dict[str, Tuple[str, ...]] = {
    "cfo": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    "capsw": ("PaymentsToDevelopSoftware", "PaymentsForSoftware", "PaymentsToAcquireSoftware"),
    "sbc": ("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"),
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"),
}
REQUIRED = ("cfo", "capex", "sbc", "revenue")        # capitalised software is optional (most filers have none)
MIN_WINDOWS = 4                                      # what the pipeline's fiscal-year series has; trend needs 3+
DEFAULT_WINDOWS = 5                                  # Part 2: "use five full fiscal years where available"
STALE_DAYS = 20                                      # EDGAR period newer than the newest window by more than this
TIER1_QUANT = ("fcf_yield_trend", "revenue_growth_consistency", "fcf_margin_trend")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADD_BACKS_FILE = os.path.join(_REPO, "landry_addbacks.json")

Period = Tuple[dt.date, dt.date]


def _is_fiscal_year(p: Period) -> bool:
    return 355 <= (p[1] - p[0]).days <= 375


def _is_ytd(p: Period) -> bool:
    return 60 <= (p[1] - p[0]).days < 355


def _years_back(d: dt.date, n: int) -> dt.date:
    try:
        return d.replace(year=d.year - n)
    except ValueError:                               # Feb 29
        return d.replace(year=d.year - n, day=28)


# --------------------------------------------------------------------------- #
# SEC data
# --------------------------------------------------------------------------- #

def company_facts(ticker: str) -> dict:
    """The raw SEC companyfacts JSON for a ticker (same EDGAR helpers and polite User-Agent as the insider check)."""
    cik = data_auto._cik_for_ticker(ticker)
    if not cik:
        raise ValueError(f"no SEC CIK found for {ticker}")
    raw, _ = data_auto._edgar_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    time.sleep(0.2)
    return json.loads(raw)


def newest_periodic_filing(ticker: str) -> Optional[Tuple[str, dt.date, dt.date]]:
    """(form, period end, filing date) of the newest 10-Q or 10-K on EDGAR, or None (foreign filers file 20-F / 6-K)."""
    cik = data_auto._cik_for_ticker(ticker)
    if not cik:
        return None
    raw, _ = data_auto._edgar_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    time.sleep(0.2)
    recent = json.loads(raw).get("filings", {}).get("recent", {})
    best: Optional[Tuple[str, dt.date, dt.date]] = None
    for form, report, filed in zip(recent.get("form", []), recent.get("reportDate", []), recent.get("filingDate", [])):
        if form in ("10-Q", "10-K") and report:
            period = dt.date.fromisoformat(report)
            if best is None or period > best[1]:
                best = (form, period, dt.date.fromisoformat(filed))
    return best


def freshness_note(newest_window_end: dt.date, newest: Optional[Tuple[str, dt.date, dt.date]]) -> Optional[str]:
    """A warning when EDGAR lists a newer periodic filing than the company facts reach (None when they agree)."""
    if newest is None or newest[1] <= newest_window_end + dt.timedelta(days=STALE_DAYS):
        return None
    form, period, filed = newest
    return (f"EDGAR lists a {form} for the period ending {period} (filed {filed}) that SEC company facts do not "
            f"have yet; the trailing windows end {newest_window_end}, a quarter stale -- supply that quarter by hand")


def period_values(facts: Mapping, tags: Sequence[str]) -> Dict[Period, float]:
    """{(start, end): USD} for one line item. The newest filing wins for a period (restatements), and the first tag
    in priority order that reports a period supplies it, so two tags are never mixed within one period."""
    out: Dict[Period, float] = {}
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        best: Dict[Period, dict] = {}
        for unit, rows in gaap.get(tag, {}).get("units", {}).items():
            if unit != "USD":
                continue
            for f in rows:
                if "start" not in f:                 # instants (balance-sheet items) have no start
                    continue
                key = (dt.date.fromisoformat(f["start"]), dt.date.fromisoformat(f["end"]))
                if key not in best or f["filed"] > best[key]["filed"]:
                    best[key] = f
        for key, f in best.items():
            out.setdefault(key, float(f["val"]))
    return out


def _near(values: Mapping[Period, float], start: dt.date, end: dt.date, tol: int = 12) -> Optional[float]:
    """The value for the period closest to (start, end) when both ends are within ``tol`` days (52/53-week years)."""
    best: Optional[Tuple[int, float]] = None
    for (s, e), v in values.items():
        ds, de = abs((s - start).days), abs((e - end).days)
        if ds <= tol and de <= tol and (best is None or ds + de < best[0]):
            best = (ds + de, v)
    return None if best is None else best[1]


# --------------------------------------------------------------------------- #
# windows
# --------------------------------------------------------------------------- #

@dataclass
class Window:
    """One 12-month window, in USD."""
    end: dt.date
    cfo: float
    capex: float
    capsw: float
    sbc: float
    revenue: float
    add_back: float = 0.0

    @property
    def fcf(self) -> float:
        """Part 10 FCF: CFO - capex - capitalised software - SBC (+ any documented one-time add-back)."""
        return self.cfo + self.add_back - self.capex - self.capsw - self.sbc

    @property
    def margin_pct(self) -> float:
        return 100.0 * self.fcf / self.revenue


def _fiscal_year_ending_before(values: Mapping[Period, float], day: dt.date, tol: int = 12) -> Optional[Tuple[dt.date, float]]:
    ends = [(e, v) for (s, e), v in values.items() if _is_fiscal_year((s, e)) and e <= day + dt.timedelta(days=tol)]
    return max(ends, default=None)


def _ttm(values: Mapping[Period, float], start: dt.date, end: dt.date) -> Optional[float]:
    fy = _fiscal_year_ending_before(values, start)
    ytd = {p: v for p, v in values.items() if _is_ytd(p)}
    cur = _near(ytd, start, end)
    prior = _near(ytd, _years_back(start, 1), _years_back(end, 1))
    if fy is None or cur is None or prior is None:
        return None
    return fy[1] + cur - prior


def ttm_windows(facts: Mapping, n_windows: int = DEFAULT_WINDOWS,
                add_back: Optional[Mapping[dt.date, float]] = None) -> List[Window]:
    """The anniversary-aligned windows, oldest first. The run of complete windows must reach the newest one; older
    windows with a missing line item are dropped (to a minimum of ``MIN_WINDOWS``), but a missing line item in the
    newest windows is an error, because a silent zero capex or SBC would overstate FCF."""
    series = {k: period_values(facts, tags) for k, tags in TAGS.items()}
    cfo = series["cfo"]
    fiscal_ends = sorted({p[1] for p in cfo if _is_fiscal_year(p)})
    # Newest end date last, and among periods that share it the LONGEST (earliest start) last: a 10-Q reports the
    # quarter alone beside the fiscal year to date, and anchoring the windows on the quarter computes FY + one quarter
    # - the prior-year quarter, silently dropping the earlier quarters' change. Found 10/6/26: NFLX's TTM operating
    # cash flow read $9.5B against $12.0B from its filings, because the SEC listing put the Q2 row after the six-month
    # row (the result depended on that order; every other holding happened to list the year to date last).
    ytds = sorted((p for p in cfo if _is_ytd(p)), key=lambda p: (p[1], -p[0].toordinal()))
    if not ytds or (fiscal_ends and fiscal_ends[-1] >= ytds[-1][1]):
        anchors: List[Tuple[dt.date, Optional[dt.date]]] = [(e, None) for e in fiscal_ends[-n_windows:]]
    else:
        s0, e0 = ytds[-1]
        anchors = [(_years_back(e0, k), _years_back(s0, k)) for k in range(n_windows - 1, -1, -1)]
    built: List[Tuple[dt.date, Optional[Window], List[str]]] = []         # oldest first
    for end, start in anchors:
        vals: Dict[str, Optional[float]] = {}
        for item, values in series.items():
            if start is None:
                vals[item] = next((v for (s, e), v in values.items() if e == end and _is_fiscal_year((s, e))), None)
            else:
                vals[item] = _ttm(values, start, end)
        missing = [k for k in REQUIRED if vals[k] is None]
        if missing:
            built.append((end, None, missing))
            continue
        w = Window(end=end, cfo=vals["cfo"], capex=vals["capex"], capsw=vals["capsw"] or 0.0,
                   sbc=vals["sbc"], revenue=vals["revenue"])
        lo = _years_back(end, 1)
        for day, amount in (add_back or {}).items():
            if lo < day <= end + dt.timedelta(days=12):
                w.add_back += float(amount)
        built.append((end, w, []))
    run: List[Window] = []
    broke: Optional[Tuple[dt.date, List[str]]] = None
    for end, w, missing in reversed(built):                  # newest first, stop at the first incomplete window
        if w is None:
            if not run:
                raise ValueError(f"window ending {end}: SEC facts have no {', '.join(missing)} -- "
                                 "a silent zero would overstate FCF; supply the figure by hand")
            broke = (end, missing)
            break
        run.append(w)
    windows = run[::-1]
    if len(windows) < MIN_WINDOWS:
        why = f" (the window ending {broke[0]} lacks {', '.join(broke[1])})" if broke else ""
        raise ValueError(f"only {len(windows)} usable window(s){why}; need at least {MIN_WINDOWS}")
    return windows


# --------------------------------------------------------------------------- #
# drafts
# --------------------------------------------------------------------------- #

@dataclass
class TTMScore:
    ticker: str
    windows: List[Window]
    market_cap: float
    yield_pct: float
    drafts: Dict[str, Draft]
    notes: List[str] = field(default_factory=list)


def tier1_drafts(ticker: str, windows: Sequence[Window], market_cap: float) -> TTMScore:
    """The three Tier 1 quant drafts (FCF yield & trend, revenue growth consistency, FCF margin trend) from the
    windows, using the same rubric functions `landry draft` uses. FCF yield is the newest window's FCF over the
    current market cap, as in the fiscal-year drafts."""
    fcf = [w.fcf for w in windows]
    revenue = [w.revenue for w in windows]
    margins = [w.margin_pct for w in windows]
    yield_pct = 100.0 * fcf[-1] / market_cap
    basis = (f"TTM-anchored: {len(windows)} non-overlapping 12-month windows ending at the latest 10-Q "
             f"({windows[-1].end}); FCF after SBC $M {[round(x / 1e6) for x in fcf]}; "
             f"revenue $M {[round(x / 1e6) for x in revenue]}")
    notes: List[str] = []
    drafts: Dict[str, Draft] = {}
    y = draft_fcf_yield_trend(yield_pct, trend_direction(fcf))
    drafts["fcf_yield_trend"] = Draft(y.indicator, y.score, y.confidence, f"{y.rationale}. {basis}")
    g = draft_revenue_growth(cagr(revenue), growth_cv(revenue))
    if g is not None:
        drafts["revenue_growth_consistency"] = Draft(g.indicator, g.score, g.confidence, f"{g.rationale}. {basis}")
    m = draft_fcf_margin_trend(margins[-1], trend_direction(margins))
    if m is not None:
        drafts["fcf_margin_trend"] = Draft(m.indicator, m.score, m.confidence,
                                          f"{m.rationale}; margins % {[round(x, 1) for x in margins]}. {basis}")
    for w in windows:
        if w.add_back:
            notes.append(f"window ending {w.end}: ${w.add_back / 1e9:,.2f}B one-time cash cost added back (Part 10)")
    return TTMScore(ticker.upper(), list(windows), market_cap, yield_pct, drafts, notes)


# --------------------------------------------------------------------------- #
# add-back registry
# --------------------------------------------------------------------------- #

def load_add_backs(path: str = ADD_BACKS_FILE) -> Dict[str, Dict[dt.date, float]]:
    """{ticker: {date: USD}} from the add-back registry. A missing file is an empty registry. Each entry also carries
    a note, who approved it and when -- the registry is where a Part 10 adjustment is recorded, not just applied."""
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        raw = json.load(f)
    return {t.upper(): {dt.date.fromisoformat(e["date"]): float(e["usd"]) for e in entries}
            for t, entries in raw.items()}


def _parse_add_back(items: Sequence[str]) -> Dict[dt.date, float]:
    out: Dict[dt.date, float] = {}
    for item in items:
        day, _, amount = item.partition(":")
        out[dt.date.fromisoformat(day)] = float(amount)
    return out


# --------------------------------------------------------------------------- #
# comparison against the scores of record
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Comparison:
    """The three Tier 1 quant scores of record against the trailing-window drafts, and what each does to the
    composite, Rules 1 and 3, and the Decision (everything else stays as recorded)."""
    recorded: Dict[str, int]
    latest: Dict[str, int]
    composite_recorded: float
    composite_latest: float
    decision_recorded: str
    decision_latest: str
    rule1_recorded: str
    rule1_latest: str
    rule3_recorded: str
    rule3_latest: str

    @property
    def moved(self) -> bool:
        return self.recorded != self.latest

    @property
    def diverges(self) -> bool:
        """A hard-rule outcome or the Decision band differs between the two bases -- the trigger for a re-score."""
        return (self.decision_recorded != self.decision_latest or self.rule1_recorded != self.rule1_latest
                or self.rule3_recorded != self.rule3_latest)


def compare_scores(recorded: Mapping[str, IndicatorScore], drafts: Mapping[str, Draft]) -> Comparison:
    missing = sorted(set(ALL_WEIGHTS) - set(recorded))
    if missing:
        raise ValueError(f"scoring incomplete, missing {', '.join(missing)}")
    latest = dict(recorded)
    for k in TIER1_QUANT:
        d = drafts.get(k)
        if d is not None:
            latest[k] = IndicatorScore(d.score, d.confidence)
    fr, fl = rule_flags(recorded), rule_flags(latest)
    cr, cl = composite_score(recorded), composite_score(latest)
    return Comparison(
        recorded={k: recorded[k].score for k in TIER1_QUANT}, latest={k: latest[k].score for k in TIER1_QUANT},
        composite_recorded=cr, composite_latest=cl,
        decision_recorded=classify(cr, fr), decision_latest=classify(cl, fl),
        rule1_recorded=fr.rule1, rule1_latest=fl.rule1, rule3_recorded=fr.rule3, rule3_latest=fl.rule3)


def status_of(cmp: Comparison, alt: Optional[Comparison]) -> str:
    """"diverges" only when the hard-rule outcome or band differs on the full window count AND on the newest four
    windows; a divergence that appears on one and not the other is "knife-edge" -- ANET's first flag on 10/5/26
    rested on a revenue-growth variability of 0.3501 against a 0.35 line and vanished on four windows -- worth a
    look but not a re-score trigger by itself."""
    if alt is not None and cmp.diverges != alt.diverges:
        return "knife-edge"
    if cmp.diverges:
        return "diverges"
    return "differs" if (cmp.moved or (alt is not None and alt.moved)) else "same"


@dataclass
class HoldingCheck:
    ticker: str
    status: str                                  # "same" | "differs" | "diverges" | "knife-edge" | "by-hand"
    reason: str = ""                             # why a check is by-hand
    scored: object = None
    ttm_end: Optional[dt.date] = None
    windows: int = 0
    comparison: Optional[Comparison] = None
    alt: Optional[Comparison] = None             # the same comparison on the newest four windows only
    notes: List[str] = field(default_factory=list)


def check_holding(ticker: str, recorded: Mapping[str, IndicatorScore], scored: object, industry: Optional[str],
                  n_windows: int, add_back: Optional[Mapping[dt.date, float]],
                  facts_fn: Callable[[str], dict] = company_facts,
                  newest_fn: Callable[[str], Optional[tuple]] = newest_periodic_filing,
                  market_cap_fn: Optional[Callable[[str], Optional[float]]] = None) -> HoldingCheck:
    """One holding against its latest 10-Q. Anything the tool cannot do is reported as by-hand with the reason;
    nothing is guessed."""
    if industry and industry.upper().startswith("REIT"):
        return HoldingCheck(ticker, "by-hand", "REIT: the FCF-based Tier 1 indicators need the Part 10 AFFO adaptation", scored)
    if market_cap_fn is None:
        from landry.fundamentals import YFinanceFundamentals
        provider = YFinanceFundamentals()
        market_cap_fn = lambda t: provider.get(t).market_cap          # noqa: E731
    try:
        newest = newest_fn(ticker)
        if newest is None:
            return HoldingCheck(ticker, "by-hand", "no 10-Q or 10-K on EDGAR (a foreign filer: 20-F / 6-K) -- check by hand", scored)
        wins = ttm_windows(facts_fn(ticker), n_windows, add_back)
        mc = market_cap_fn(ticker)
        if not mc:
            return HoldingCheck(ticker, "by-hand", "no market cap from the provider", scored)
        res = tier1_drafts(ticker, wins, mc)
        cmp = compare_scores(recorded, res.drafts)
        alt = (compare_scores(recorded, tier1_drafts(ticker, wins[-MIN_WINDOWS:], mc).drafts)
               if len(wins) > MIN_WINDOWS else None)
    except ValueError as e:
        return HoldingCheck(ticker, "by-hand", str(e), scored)
    notes = list(res.notes)
    stale = freshness_note(wins[-1].end, newest)
    if stale:
        notes.append(stale)
    status = status_of(cmp, alt)
    return HoldingCheck(ticker, status, "", scored, wins[-1].end, len(wins), cmp, alt, notes)


def held_scored_rows(positions: Sequence, rows: Sequence) -> List:
    """Scoring rows for held individual stocks. ETFs and cash have no Scoring row, so they drop out here."""
    held = {p.ticker for p in positions if p.asset_class == "Equity"}
    return [r for r in rows if r.ticker in held]


def check_holdings(path: str, n_windows: int = DEFAULT_WINDOWS,
                   add_backs: Optional[Mapping[str, Mapping[dt.date, float]]] = None, **fns) -> List[HoldingCheck]:
    from landry import xlsx_io
    rows = held_scored_rows(xlsx_io.read_positions(path), xlsx_io.read_scoring_tab(path))
    out: List[HoldingCheck] = []
    for r in rows:
        out.append(check_holding(r.ticker, r.scores, r.date_scored, r.industry, n_windows,
                                 (add_backs or {}).get(r.ticker.upper()), **fns))
    return out


def format_holdings(checks: Sequence[HoldingCheck]) -> str:
    """The comparison as a table, then the names that need a look and the names the tool could not check."""
    done = [c for c in checks if c.comparison is not None]
    lines = [f"{'tkr':5s} {'scored':>10s} {'10-Q to':>10s} {'win':>3s}  {'yield/rev/margin: record -> 10-Q':>32s}  "
             f"{'composite':>15s}  decision"]
    for c in done:
        k = c.comparison
        rec = "/".join(str(k.recorded[i]) for i in TIER1_QUANT)
        new = "/".join(str(k.latest[i]) for i in TIER1_QUANT)
        dec = k.decision_recorded if k.decision_recorded == k.decision_latest else f"{k.decision_recorded} -> {k.decision_latest}"
        mark = {"diverges": "   <== REVIEW", "knife-edge": "   <== knife-edge"}.get(c.status, "")
        lines.append(f"{c.ticker:5s} {str(c.scored)[:10]:>10s} {c.ttm_end}  {c.windows:>3d}  {rec + '  ->  ' + new:>32s}  "
                     f"{k.composite_recorded:6.1f} -> {k.composite_latest:5.1f}  {dec}{mark}")
    diverging = [c for c in done if c.status == "diverges"]
    lines.append("")
    if diverging:
        lines.append("REVIEW -- a hard-rule outcome or Decision band differs between the bases (re-score only these, "
                     "with Part 10 adjustments written down):")
        for c in diverging:
            k = c.comparison
            what = []
            if k.decision_recorded != k.decision_latest:
                what.append(f"{k.decision_recorded} -> {k.decision_latest}")
            if k.rule3_recorded != k.rule3_latest:
                what.append(f"Rule 3 {k.rule3_recorded} -> {k.rule3_latest}")
            if k.rule1_recorded != k.rule1_latest:
                what.append(f"Rule 1 {k.rule1_recorded} -> {k.rule1_latest}")
            lines.append(f"  {c.ticker}: {'; '.join(what)} (composite {k.composite_recorded:.1f} -> {k.composite_latest:.1f})")
    else:
        lines.append("No hard-rule outcome or Decision band differs between the bases.")
    edge = [c for c in done if c.status == "knife-edge"]
    if edge:
        lines.append("")
        lines.append("KNIFE-EDGE -- the outcome flips with the window count (look, but not a re-score trigger by itself):")
        for c in edge:
            k, a = c.comparison, c.alt
            lines.append(f"  {c.ticker}: {c.windows} windows {k.decision_recorded} -> {k.decision_latest} "
                         f"(composite {k.composite_recorded:.1f} -> {k.composite_latest:.1f}); newest 4 windows "
                         f"{a.decision_recorded} -> {a.decision_latest} (composite {a.composite_latest:.1f})")
    byhand = [c for c in checks if c.status == "by-hand"]
    if byhand:
        lines.append("")
        lines.append("BY HAND -- the tool could not check these:")
        lines.extend(f"  {c.ticker}: {c.reason}" for c in byhand)
    notes = [(c.ticker, n) for c in checks for n in c.notes]
    if notes:
        lines.append("")
        lines.append("NOTES:")
        lines.extend(f"  {t}: {n}" for t, n in notes)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #

def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Trailing-12-month Tier 1 drafts from SEC filings; prints, writes nothing")
    p.add_argument("ticker", nargs="?", help="one ticker (its windows and drafts), or use --holdings")
    p.add_argument("--holdings", action="store_true",
                   help="compare every held, scored stock's fiscal-year scores with its latest 10-Q")
    p.add_argument("--workbook", help="workbook for --holdings (default: the live one)")
    p.add_argument("--windows", type=int, default=DEFAULT_WINDOWS)
    p.add_argument("--add-back", action="append", default=[], metavar="YYYY-MM-DD:USD",
                   help="a documented one-time operating cash cost to add back to the window containing that date")
    p.add_argument("--no-registry", action="store_true", help="ignore landry_addbacks.json")
    args = p.parse_args(argv)
    if bool(args.ticker) == bool(args.holdings):
        p.error("give a ticker or --holdings")
    registry = {} if args.no_registry else load_add_backs()
    if args.holdings:
        from landry.xlsx_io import latest_workbook
        path = args.workbook or latest_workbook(_REPO)
        checks = check_holdings(path, args.windows, registry)
        print(f"Holdings: fiscal-year scores of record against the latest 10-Q ({os.path.basename(path)})")
        print(format_holdings(checks))
        return 2 if any(c.status == "diverges" for c in checks) else 0
    ticker = args.ticker.upper()
    add_back = dict(registry.get(ticker, {}))
    add_back.update(_parse_add_back(args.add_back))
    from landry.fundamentals import YFinanceFundamentals
    market_cap = YFinanceFundamentals().get(ticker).market_cap
    wins = ttm_windows(company_facts(ticker), args.windows, add_back)
    res = tier1_drafts(ticker, wins, market_cap)
    print(f"{res.ticker}: market cap ${market_cap / 1e9:,.1f}B, FCF yield {res.yield_pct:.2f}%")
    print(f"{'window ends':12s} {'CFO':>9s} {'capex':>8s} {'SBC':>8s} {'add-back':>9s} {'FCF':>9s} {'revenue':>9s} {'margin':>7s}   ($M)")
    for w in res.windows:
        print(f"{w.end}  {w.cfo / 1e6:9,.0f} {w.capex / 1e6:8,.0f} {w.sbc / 1e6:8,.0f} {w.add_back / 1e6:9,.0f} "
              f"{w.fcf / 1e6:9,.0f} {w.revenue / 1e6:9,.0f} {w.margin_pct:6.1f}%")
    for d in res.drafts.values():
        print(f"  {d.indicator}: {d.score} ({d.confidence})  {d.rationale.split('. TTM-anchored')[0]}")
    for n in res.notes:
        print(f"  note: {n}")
    stale = freshness_note(wins[-1].end, newest_periodic_filing(ticker))
    if stale:
        print(f"  WARNING: {stale}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
