"""Landry System v1.0 — computable fundamentals (Part 10 definitions) and
the quantitative rubric drafts they support.

Part 10 core definitions implemented here:
* FCF = cash from operations - capex - grant-date SBC (SBC is a real
  economic expense, not a non-cash add-back).
* FCF Yield = normalized equity FCF / market capitalization.
* Debt/FCF = net debt / normalized annual FCF.
* Revenue variability = coefficient of variation of annual growth rates
  (std / |mean|), five fiscal years where available.

The math is pure (plain numbers in, scores out) so tests run offline.
``YFinanceFundamentals`` is the first provider behind the pluggable
interface; it tags everything it produces with a confidence ceiling of
"M" because yfinance statements are not filings-grade (Part 10 data
hierarchy).

Judgment indicators (moat, management, revenue visibility) are NEVER
drafted here — Part 12 boundary; Phase 3 handles evidence-backed drafts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #

@dataclass
class AnalystRecPeriod:
    """One month's sell-side recommendation tally (e.g. yfinance
    Ticker.recommendations: period "0m" = current, "-1m" = one month
    back, ...)."""
    period: str
    strong_buy: int
    buy: int
    hold: int
    sell: int
    strong_sell: int

    @property
    def total(self) -> int:
        return self.strong_buy + self.buy + self.hold + self.sell + self.strong_sell

    @property
    def buy_pct(self) -> Optional[float]:
        return None if self.total == 0 else 100.0 * (self.strong_buy + self.buy) / self.total

    @property
    def negative_pct(self) -> Optional[float]:
        return None if self.total == 0 else 100.0 * (self.sell + self.strong_sell) / self.total


@dataclass
class FundamentalInputs:
    """Raw inputs for one company, most recent first where lists are used."""
    ticker: str
    market_cap: Optional[float] = None
    # annual series, most recent LAST (chronological), up to 5 fiscal years
    revenue: List[float] = field(default_factory=list)
    cfo: List[float] = field(default_factory=list)
    capex: List[float] = field(default_factory=list)          # positive numbers
    sbc: List[float] = field(default_factory=list)
    total_debt: Optional[float] = None
    cash: Optional[float] = None
    # ROIC/WACC inputs (most recent LAST, same convention as revenue/cfo)
    ebit: List[float] = field(default_factory=list)
    pretax_income: List[float] = field(default_factory=list)
    tax_provision: List[float] = field(default_factory=list)
    tax_rate_for_calcs: List[float] = field(default_factory=list)  # yfinance's own effective rate, preferred over deriving one
    stockholders_equity: List[float] = field(default_factory=list)
    interest_expense: List[float] = field(default_factory=list)
    peg: Optional[float] = None
    # analyst recommendation history, most recent period FIRST (0m, -1m, ...)
    analyst_recs: List[AnalystRecPeriod] = field(default_factory=list)
    source: str = "manual"
    warnings: List[str] = field(default_factory=list)


def normalized_fcf(cfo: float, capex: float, sbc: float = 0.0) -> float:
    """Part 10: FCF = CFO - capex - SBC (capex, sbc as positive costs)."""
    return cfo - abs(capex) - abs(sbc)


def fcf_series(inp: FundamentalInputs) -> List[float]:
    n = min(len(inp.cfo), len(inp.capex))
    sbc = inp.sbc if len(inp.sbc) >= n else [0.0] * n
    return [normalized_fcf(inp.cfo[i], inp.capex[i], sbc[i] if i < len(sbc) else 0.0)
            for i in range(-n, 0)]


def net_debt(inp: FundamentalInputs) -> Optional[float]:
    if inp.total_debt is None:
        return None
    return inp.total_debt - (inp.cash or 0.0)


def debt_to_fcf(inp: FundamentalInputs) -> Optional[float]:
    """Net debt / normalized FCF. None when FCF <= 0 (ratio meaningless —
    treat as a Low-Confidence red flag upstream, not a number)."""
    nd = net_debt(inp)
    fcfs = fcf_series(inp)
    if nd is None or not fcfs or fcfs[-1] <= 0:
        return None
    if nd <= 0:
        return 0.0
    return nd / fcfs[-1]


# --------------------------------------------------------------------------- #
# ROIC vs WACC (Tier 2, 6%)
# --------------------------------------------------------------------------- #

# Equity risk premium: a documented assumption, not a computed figure --
# there is no single market-observable "true" ERP. 4.5 sits within the
# range Damodaran's implied-ERP series has occupied for most of the
# 2020s; review this periodically rather than treat it as derived.
EQUITY_RISK_PREMIUM_PCT = 4.5

# Fallback risk-free rate (10-year Treasury) used only when a live quote
# isn't available -- stale by construction, which is why callers that
# hit this path should treat the result as no better than L confidence.
RISK_FREE_RATE_FALLBACK_PCT = 4.5


def effective_tax_rate(inp: FundamentalInputs) -> Optional[float]:
    """Prefer yfinance's own normalized effective rate (Tax Rate For
    Calcs, already adjusted for one-time items); fall back to
    Tax Provision / Pretax Income for the most recent period."""
    if inp.tax_rate_for_calcs:
        r = inp.tax_rate_for_calcs[-1]
        if r is not None and 0 <= r <= 1:
            return r
    if inp.tax_provision and inp.pretax_income:
        pretax = inp.pretax_income[-1]
        if pretax and pretax > 0:
            rate = inp.tax_provision[-1] / pretax
            if 0 <= rate <= 1:
                return rate
    return None


def roic_pct_series(inp: FundamentalInputs) -> List[float]:
    """ROIC for every fiscal year with the needed line items (chronological,
    most recent last). ROIC = NOPAT / Invested Capital; NOPAT = EBIT x
    (1 - effective tax rate); Invested Capital = Total Debt + Stockholders
    Equity - Cash (a common simplified definition, excludes minority-
    interest adjustments). Total Debt/Cash are only available as
    most-recent-period snapshots, so every year in the series is priced
    against today's balance sheet, not each year's own -- a real
    simplification, not a full time series."""
    tax = effective_tax_rate(inp)
    if tax is None or inp.total_debt is None or not inp.stockholders_equity:
        return []
    invested_capital = inp.total_debt + inp.stockholders_equity[-1] - (inp.cash or 0.0)
    if invested_capital <= 0:
        return []
    n = min(len(inp.ebit), len(inp.stockholders_equity))
    return [100.0 * inp.ebit[i] * (1 - tax) / invested_capital
           for i in range(len(inp.ebit) - n, len(inp.ebit))]


def roic_pct(inp: FundamentalInputs) -> Optional[float]:
    series = roic_pct_series(inp)
    return series[-1] if series else None


@dataclass
class WaccResult:
    wacc_pct: Optional[float]
    risk_free_pct: float
    risk_free_source: str      # "live 10y Treasury (^TNX)" | "fallback assumption"
    equity_risk_premium_pct: float = EQUITY_RISK_PREMIUM_PCT


def compute_wacc(inp: FundamentalInputs, beta: Optional[float],
                 risk_free_pct: Optional[float] = None,
                 risk_free_source: str = "live 10y Treasury (^TNX)"
                 ) -> WaccResult:
    """A quick, fully-documented WACC -- not a precision instrument.
    Cost of equity via CAPM (Rf + beta x ERP); pretax cost of debt as
    Interest Expense / Total Debt; weighted by market cap (equity) vs
    total debt, tax-affecting the debt leg. Every assumption (as opposed
    to a fetched company fact) is named on the result so a caller can see
    exactly what's fixed."""
    if risk_free_pct is None:
        risk_free_pct = RISK_FREE_RATE_FALLBACK_PCT
        risk_free_source = "fallback assumption"
    if beta is None or inp.market_cap is None:
        return WaccResult(None, risk_free_pct, risk_free_source)
    cost_of_equity = risk_free_pct + beta * EQUITY_RISK_PREMIUM_PCT
    debt = inp.total_debt or 0.0
    equity = inp.market_cap
    total = debt + equity
    if total <= 0:
        return WaccResult(None, risk_free_pct, risk_free_source)
    tax = effective_tax_rate(inp) or 0.0
    cost_of_debt = 0.0
    if debt > 0 and inp.interest_expense:
        cost_of_debt = 100.0 * abs(inp.interest_expense[-1]) / debt
    wacc = ((equity / total) * cost_of_equity
           + (debt / total) * cost_of_debt * (1 - tax))
    return WaccResult(wacc, risk_free_pct, risk_free_source)


def live_risk_free_rate() -> Optional[float]:
    """10-year Treasury yield (^TNX), already quoted in percentage-point
    form (e.g. 4.7 means 4.7%). None on any fetch failure -- callers fall
    back to RISK_FREE_RATE_FALLBACK_PCT and must say so in the rationale."""
    try:
        import yfinance as yf
        v = yf.Ticker("^TNX").fast_info.get("lastPrice")
        return float(v) if v else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# growth statistics (Part 2, Revenue Growth Consistency)
# --------------------------------------------------------------------------- #

def cagr(series: Sequence[float]) -> Optional[float]:
    """CAGR over the chronological series (needs >= 2 positive endpoints)."""
    if len(series) < 2 or series[0] <= 0 or series[-1] <= 0:
        return None
    years = len(series) - 1
    return (series[-1] / series[0]) ** (1.0 / years) - 1.0


def growth_cv(series: Sequence[float]) -> Optional[float]:
    """Coefficient of variation of annual growth rates: std / |mean|."""
    if len(series) < 3:
        return None
    rates = [series[i] / series[i - 1] - 1.0
             for i in range(1, len(series)) if series[i - 1] > 0]
    if len(rates) < 2:
        return None
    import statistics
    mean = statistics.fmean(rates)
    if mean == 0:
        return None
    return statistics.pstdev(rates) / abs(mean)


def trend_direction(series: Sequence[float], rel_tol: float = 0.10) -> str:
    """'expanding' / 'stable' / 'contracting': recent (last 2) average vs
    prior average, with a +/-10% relative tolerance for 'stable'."""
    if len(series) < 3:
        return "stable"
    recent = sum(series[-2:]) / 2.0
    prior = sum(series[:-2]) / len(series[:-2])
    if prior == 0:
        return "stable"
    chg = (recent - prior) / abs(prior)
    if chg > rel_tol:
        return "expanding"
    if chg < -rel_tol:
        return "contracting"
    return "stable"


# --------------------------------------------------------------------------- #
# rubric mappers (Part 2) — quantitative indicators only
# --------------------------------------------------------------------------- #

@dataclass
class Draft:
    """A drafted score awaiting analyst approval (never final by itself)."""
    indicator: str
    score: int
    confidence: str          # ceiling from data quality, "H"/"M"/"L"
    rationale: str


def fcf_yield_level_score(yield_pct: float) -> int:
    """Yield rubric: 5 > 6%; 4 = 4-6%; 3 = 2-4%; 2 = 0-2%; 1 negative."""
    if yield_pct < 0:
        return 1
    if yield_pct < 2:
        return 2
    if yield_pct < 4:
        return 3
    if yield_pct <= 6:
        return 4
    return 5


def fcf_trend_score(direction: str) -> int:
    """Trend rubric: 5 expanding, 3 stable, 1 contracting."""
    return {"expanding": 5, "stable": 3, "contracting": 1}[direction]


def combined_band(combined: float) -> int:
    """Combine rubric: 5 >= 4.5; 4 = 3.5-4.49; 3 = 2.5-3.49; 2 = 1.5-2.49;
    1 < 1.5."""
    for floor, sc in ((4.5, 5), (3.5, 4), (2.5, 3), (1.5, 2)):
        if combined >= floor:
            return sc
    return 1


def draft_fcf_yield_trend(yield_pct: float, direction: str,
                          structural_deterioration: bool = False) -> Draft:
    """FCF Yield & Trend (Tier 1, 20%): yield level = 60% of the indicator,
    trend = 40%; structural deterioration caps the indicator at 2 (that
    flag is an analyst judgment — defaults False and is drafted M)."""
    lvl, trd = fcf_yield_level_score(yield_pct), fcf_trend_score(direction)
    combined = 0.6 * lvl + 0.4 * trd
    score = combined_band(combined)
    note = ""
    if structural_deterioration and score > 2:
        score, note = 2, " (capped at 2: structural deterioration)"
    return Draft("fcf_yield_trend", score, "M",
                 f"yield {yield_pct:.1f}% -> {lvl}, trend {direction} -> {trd}, "
                 f"combined {combined:.2f} -> {score}{note}")


def draft_revenue_growth(cagr_val: Optional[float],
                         cv: Optional[float]) -> Optional[Draft]:
    """Revenue Growth Consistency (Tier 1, 15%). CAGR and CV per Part 2."""
    if cagr_val is None:
        return None
    g = cagr_val * 100.0
    if g < 0:
        sc, why = 1, f"negative CAGR {g:.1f}%"
    elif cv is None:
        sc, why = 2, f"CAGR {g:.1f}% but CV unavailable (thin history)"
    elif g >= 15 and cv <= 0.35:
        sc, why = 5, f"CAGR {g:.1f}% >= 15%, CV {cv:.2f} <= 0.35"
    elif g >= 10 and cv <= 0.50:
        sc, why = 4, f"CAGR {g:.1f}% in 10-15%, CV {cv:.2f} <= 0.50"
    elif 5 <= g < 10 or 0.50 < cv <= 0.75:
        sc, why = 3, f"CAGR {g:.1f}% / CV {cv:.2f}"
    else:
        sc, why = 2, f"CAGR {g:.1f}% < 5% or CV {cv:.2f} > 0.75"
    return Draft("revenue_growth_consistency", sc, "M", why)


def draft_fcf_margin_trend(margin_pct: Optional[float],
                           direction: str) -> Optional[Draft]:
    """FCF Margin Trend (Tier 1, 10%): 5 > 25%/expanding; 4 = 15-25
    stable/expanding; 3 = 5-15 flat; 2 < 5% or contracting; 1 negative
    and deteriorating."""
    if margin_pct is None:
        return None
    m = margin_pct
    if m < 0 and direction == "contracting":
        sc = 1
    elif m < 5 or direction == "contracting":
        sc = 2
    elif m > 25:
        sc = 5
    elif m >= 15 and direction in ("stable", "expanding"):
        sc = 4
    else:
        sc = 3
    return Draft("fcf_margin_trend", sc, "M",
                 f"FCF margin {m:.1f}%, {direction}")


def draft_roic_vs_wacc(roic_pct: Optional[float], wacc_pct: Optional[float],
                       persistently_below: bool = False) -> Optional[Draft]:
    """ROIC vs WACC (Tier 2, 6%): 5 = ROIC > 20% and spread > 10pp;
    4 = 15-20% and spread 5-10pp; 3 modestly above; 2 below/converging;
    1 persistently below."""
    if roic_pct is None or wacc_pct is None:
        return None
    spread = roic_pct - wacc_pct
    if persistently_below and spread < 0:
        sc = 1
    elif spread < 0:
        sc = 2
    elif roic_pct > 20 and spread > 10:
        sc = 5
    elif roic_pct >= 15 and spread >= 5:
        sc = 4
    elif spread <= 2:
        sc = 2          # converging to WACC
    else:
        sc = 3
    return Draft("roic_vs_wacc", sc, "L",
                 f"ROIC {roic_pct:.1f}% vs WACC {wacc_pct:.1f}% "
                 f"(spread {spread:+.1f}pp)")


def analyst_consensus_band(buy_pct: float, negative_pct: float) -> int:
    """Analyst Consensus rubric (Tier 3, 2%; consensus-rating half only —
    estimate-revision direction is not scored here): 5 = >=80% buy-
    equivalent (StrongBuy+Buy) AND 0% negative (Sell+StrongSell); 4 = >=60%
    buy-equivalent AND <=10% negative; 3 = >=40% buy-equivalent, or
    negative share exceeds 10%; 2 = >=20% buy-equivalent, or negative
    share meets/exceeds positive; 1 = below 20% buy-equivalent."""
    if negative_pct >= buy_pct and negative_pct > 0:
        return 2 if buy_pct >= 20 else 1
    if buy_pct >= 80 and negative_pct == 0:
        return 5
    if buy_pct >= 60 and negative_pct <= 10:
        return 4
    if buy_pct >= 40:
        return 3
    if buy_pct >= 20:
        return 2
    return 1


def draft_analyst_consensus(recs: List[AnalystRecPeriod]) -> Optional[Draft]:
    """Analyst Consensus & Estimate Revisions (Tier 3, 2%). Scores the
    consensus-rating half from recommendation counts (most recent period
    first); estimate-revision direction is a separate, not-yet-implemented
    half of this indicator (tracked, not scored). Fewer than 5 analysts
    caps confidence at L -- too thin a sample to trust."""
    if not recs or recs[0].total == 0:
        return None
    latest = recs[0]
    sc = analyst_consensus_band(latest.buy_pct, latest.negative_pct)
    confidence = "L" if latest.total < 5 else "M"
    trend = ""
    older = next((r for r in reversed(recs) if r.total > 0 and r is not latest), None)
    if older is not None:
        delta = latest.buy_pct - older.buy_pct
        if abs(delta) >= 5:
            trend = (f", buy% {'up' if delta > 0 else 'down'} "
                     f"{abs(delta):.0f}pp vs {older.period}")
    return Draft(
        "analyst_consensus", sc, confidence,
        f"{latest.strong_buy + latest.buy}/{latest.total} buy-equivalent "
        f"({latest.buy_pct:.0f}%), {latest.negative_pct:.0f}% negative{trend} "
        "-- consensus-rating half only, estimate revisions not scored")


def draft_valuation_multiples(p_fcf: Optional[float],
                              p_fcf_5y_avg: Optional[float],
                              peg: Optional[float],
                              fundamentals_declining: bool = False
                              ) -> Optional[Draft]:
    """Valuation Multiples (Tier 2, 8%), P/FCF primary per the doc:
    5 = PEG < 1.0 AND P/FCF below 5-yr avg; 4 = modest discount;
    3 = in line; 2 = premium with declining fundamentals; 1 = extreme.
    Peer comparison is analyst judgment, so drafts cap at M confidence.

    Without a P/FCF history to compare against (p_fcf_5y_avg missing),
    this falls back to a PEG-only read at L confidence rather than
    guessing "in line with history" -- that specific claim needs the
    trend data to back it up, and shouldn't be the default when there
    isn't any."""
    if p_fcf is None or p_fcf <= 0:
        return None
    rel = None if not p_fcf_5y_avg else p_fcf / p_fcf_5y_avg
    if rel is None:
        if peg is None:
            return None
        if peg < 1.0:
            sc, why = 4, f"PEG {peg:.2f} < 1 (no P/FCF history available to corroborate)"
        elif peg <= 2.0:
            sc, why = 3, f"PEG {peg:.2f} in the 1-2x range (no P/FCF history available)"
        else:
            sc, why = 2, f"PEG {peg:.2f} > 2 (no P/FCF history available)"
        return Draft("valuation_multiples", sc, "L", why)
    if peg is not None and 0 < peg < 1.0 and rel < 1.0:
        sc, why = 5, f"PEG {peg:.2f} < 1 and P/FCF {rel:.0%} of 5y avg"
    elif rel < 0.90:
        sc, why = 4, f"P/FCF {rel:.0%} of 5y avg (modest discount)"
    elif rel > 1.5 and (fundamentals_declining or p_fcf > 50):
        sc, why = (1 if fundamentals_declining else 2,
                   f"P/FCF {p_fcf:.0f}x, {rel:.0%} of 5y avg")
    elif rel > 1.10:
        sc = 2 if fundamentals_declining else 3
        why = f"P/FCF {rel:.0%} of 5y avg (premium)"
    else:
        sc, why = 3, f"P/FCF {p_fcf:.0f}x, in line with history"
    return Draft("valuation_multiples", sc, "M", why)


# --------------------------------------------------------------------------- #
# metric bundle + draft assembly
# --------------------------------------------------------------------------- #

@dataclass
class FundamentalMetrics:
    ticker: str
    fcf: Optional[float] = None
    fcf_yield_pct: Optional[float] = None
    fcf_trend: str = "stable"
    fcf_margin_pct: Optional[float] = None
    margin_trend: str = "stable"
    revenue_cagr: Optional[float] = None
    revenue_cv: Optional[float] = None
    debt_to_fcf: Optional[float] = None
    p_fcf: Optional[float] = None
    p_fcf_5y_avg: Optional[float] = None  # see compute_metrics -- a documented proxy, not a true rolling average
    peg: Optional[float] = None
    roic_pct: Optional[float] = None
    roic_pct_series: List[float] = field(default_factory=list)
    years_of_data: int = 0
    analyst_recs: List[AnalystRecPeriod] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def compute_metrics(inp: FundamentalInputs) -> FundamentalMetrics:
    m = FundamentalMetrics(ticker=inp.ticker, warnings=list(inp.warnings))
    fcfs = fcf_series(inp)
    m.years_of_data = len(fcfs)
    if fcfs:
        m.fcf = fcfs[-1]
        m.fcf_trend = trend_direction(fcfs)
        if inp.market_cap:
            m.fcf_yield_pct = 100.0 * fcfs[-1] / inp.market_cap
            m.p_fcf = (inp.market_cap / fcfs[-1]) if fcfs[-1] > 0 else None
            # Proxy for a 5-year average P/FCF: today's market cap over the
            # multi-year AVERAGE FCF, not a true time-weighted average of
            # each year's own P/FCF (that needs historical prices, which
            # aren't fetched here). Needs 2+ years and a positive average.
            if len(fcfs) >= 2:
                avg_fcf = sum(fcfs) / len(fcfs)
                if avg_fcf > 0:
                    m.p_fcf_5y_avg = inp.market_cap / avg_fcf
        n = min(len(fcfs), len(inp.revenue))
        if n:
            margins = [100.0 * f / r for f, r in
                       zip(fcfs[-n:], inp.revenue[-n:]) if r]
            if margins:
                m.fcf_margin_pct = margins[-1]
                m.margin_trend = trend_direction(margins)
    m.revenue_cagr = cagr(inp.revenue)
    m.revenue_cv = growth_cv(inp.revenue)
    m.debt_to_fcf = debt_to_fcf(inp)
    m.analyst_recs = list(inp.analyst_recs)
    m.roic_pct_series = roic_pct_series(inp)
    m.roic_pct = m.roic_pct_series[-1] if m.roic_pct_series else None
    m.peg = inp.peg
    if m.years_of_data < 5:
        m.warnings.append(f"only {m.years_of_data} fiscal years of FCF data "
                          "(doc asks for five where available)")
    return m


def draft_quant_scores(m: FundamentalMetrics) -> Dict[str, Draft]:
    """Every rubric draft the fundamentals support. Judgment indicators
    are deliberately absent (Part 12). roic_vs_wacc is NOT drafted here --
    it needs beta, which lives in landry.data_auto, not in fundamentals'
    inputs; see landry.cli._cmd_draft for that wiring."""
    out: Dict[str, Draft] = {}
    if m.fcf_yield_pct is not None:
        out["fcf_yield_trend"] = draft_fcf_yield_trend(m.fcf_yield_pct, m.fcf_trend)
    d = draft_revenue_growth(m.revenue_cagr, m.revenue_cv)
    if d:
        out["revenue_growth_consistency"] = d
    d = draft_fcf_margin_trend(m.fcf_margin_pct, m.margin_trend)
    if d:
        out["fcf_margin_trend"] = d
    d = draft_valuation_multiples(m.p_fcf, m.p_fcf_5y_avg, m.peg)
    if d:
        out["valuation_multiples"] = d
    # thin history degrades confidence one notch (Tier 1/2 fundamentals only --
    # analyst consensus has its own, independent confidence basis below)
    if m.years_of_data < 4:
        for d in out.values():
            d.confidence = "L"
    d = draft_analyst_consensus(m.analyst_recs)
    if d:
        out["analyst_consensus"] = d
    return out


# --------------------------------------------------------------------------- #
# providers
# --------------------------------------------------------------------------- #

class FundamentalsProvider(Protocol):
    def get(self, ticker: str) -> FundamentalInputs: ...


class YFinanceFundamentals:
    """Best-effort provider from yfinance annual statements. Not
    filings-grade: everything downstream should treat it as confidence
    ceiling M (and L when history is thin)."""

    def get(self, ticker: str) -> FundamentalInputs:
        import yfinance as yf
        t = yf.Ticker(ticker)
        inp = FundamentalInputs(ticker=ticker, source="yfinance")

        def _row(df, *names):
            if df is None or df.empty:
                return []
            for n in names:
                if n in df.index:
                    s = df.loc[n].dropna()
                    return [float(v) for v in s.iloc[::-1]]  # chronological
            return []

        try:
            cf, is_, bs = t.cashflow, t.income_stmt, t.balance_sheet
        except Exception as e:  # network / parsing
            inp.warnings.append(f"yfinance statements unavailable: {e}")
            return inp
        inp.cfo = _row(cf, "Operating Cash Flow",
                       "Cash Flow From Continuing Operating Activities")
        inp.capex = [abs(v) for v in _row(cf, "Capital Expenditure")]
        inp.sbc = [abs(v) for v in _row(cf, "Stock Based Compensation")]
        inp.revenue = _row(is_, "Total Revenue", "Operating Revenue")
        debt = _row(bs, "Total Debt")
        cash = _row(bs, "Cash And Cash Equivalents",
                    "Cash Cash Equivalents And Short Term Investments")
        inp.total_debt = debt[-1] if debt else None
        inp.cash = cash[-1] if cash else None
        inp.ebit = _row(is_, "EBIT")
        inp.pretax_income = _row(is_, "Pretax Income")
        inp.tax_provision = _row(is_, "Tax Provision")
        inp.tax_rate_for_calcs = _row(is_, "Tax Rate For Calcs")
        inp.stockholders_equity = _row(bs, "Stockholders Equity")
        inp.interest_expense = _row(is_, "Interest Expense",
                                    "Interest Expense Non Operating")
        try:
            inp.market_cap = t.fast_info.get("marketCap")
        except Exception:
            pass
        try:
            info = t.info or {}
            inp.peg = info.get("trailingPegRatio") or info.get("pegRatio")
        except Exception:
            pass
        try:
            rec = t.recommendations
            if rec is not None and not rec.empty:
                inp.analyst_recs = [
                    AnalystRecPeriod(
                        period=str(row["period"]),
                        strong_buy=int(row["strongBuy"]), buy=int(row["buy"]),
                        hold=int(row["hold"]), sell=int(row["sell"]),
                        strong_sell=int(row["strongSell"]))
                    for _, row in rec.iterrows()
                ]
        except Exception as e:
            inp.warnings.append(f"yfinance recommendations unavailable: {e}")
        if not inp.sbc:
            inp.warnings.append("SBC not reported — FCF may be overstated "
                                "vs the Part 10 definition")
        return inp
