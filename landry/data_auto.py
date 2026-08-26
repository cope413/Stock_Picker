"""Landry System v1.0 — automatable price-based inputs (Part 12 boundary).

Everything here is derived from OHLCV data alone: weekly returns, the
Rule 36 correlation windowing and cluster flags, Relative Strength vs SPY,
the Rule 20 beta computation, and the technical structure used by Entry
Rules 8-9 and the Tier 3 rubric (200-week MA, monthly MACD, Supertrend,
A/D line).

All functions are pure (take DataFrames/Series, return results) so tests
run offline; live data enters only through ``fetch_daily`` which reuses
the Layer 1 yfinance cache.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Rule 36 constants (Part 7)
# --------------------------------------------------------------------------- #

CORRELATION_THRESHOLD = 0.70     # pairwise flag level
CORR_WINDOW_MAX_WEEKS = 156      # "up to 36 months"
CORR_WINDOW_TARGET_WEEKS = 104   # "24-month target"
CORR_WINDOW_MIN_WEEKS = 52       # "12-month minimum"
CLUSTER_MIN_SIZE = 3             # "cluster of three or more holdings"
CLUSTER_EXPOSURE_CAP = 0.20      # aggregate exposure cap without approval

# Rule 20 (Part 5) — beta sizing overlay
BETA_WINDOW_PRIMARY_WEEKS = 260  # 5-year weekly
BETA_WINDOW_FALLBACK_WEEKS = 104 # 2-year weekly fallback
BETA_MIN_WEEKS = 90


# --------------------------------------------------------------------------- #
# weekly series
# --------------------------------------------------------------------------- #

def weekly_closes(daily: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Week-end (Friday) closes, one column per ticker. auto_adjust closes
    are dividend-adjusted, so pct_change on them is a total return."""
    cols = {t: df["Close"].resample("W-FRI").last().dropna()
            for t, df in daily.items()}
    return pd.DataFrame(cols)


def weekly_returns(closes: pd.DataFrame) -> pd.DataFrame:
    return closes.pct_change().dropna(how="all")


# --------------------------------------------------------------------------- #
# Rule 36 — correlation matrix, windowing, clusters
# --------------------------------------------------------------------------- #

@dataclass
class CorrelationReport:
    matrix: pd.DataFrame                 # pairwise Pearson, rounded 2dp
    window_weeks: int                    # actual common window used
    sufficient: bool                     # >= 12-month minimum
    clusters: List[List[str]] = field(default_factory=list)   # size >= 3 cliques
    flagged_pairs: List[Tuple[str, str, float]] = field(default_factory=list)


def _cliques(adj: Dict[str, set]) -> List[List[str]]:
    """Maximal cliques (Bron-Kerbosch, fine for portfolio-sized graphs)."""
    out: List[List[str]] = []

    def bk(r: set, p: set, x: set):
        if not p and not x:
            out.append(sorted(r))
            return
        pivot = max(p | x, key=lambda v: len(adj[v]), default=None)
        for v in list(p - (adj[pivot] if pivot else set())):
            bk(r | {v}, p & adj[v], x & adj[v])
            p.discard(v)
            x.add(v)

    bk(set(), set(adj), set())
    return out


def correlation_report(returns: pd.DataFrame,
                       threshold: float = CORRELATION_THRESHOLD
                       ) -> CorrelationReport:
    """Rule 36: pairwise correlations of weekly total returns over the
    longest common period up to 36 months (24-month target, 12-month
    minimum). Flags every pair above the threshold and every clique of
    3+ holdings whose *pairwise* correlations all exceed it."""
    common = returns.dropna(how="any")
    window = min(len(common), CORR_WINDOW_MAX_WEEKS)
    tail = common.tail(window)
    corr = tail.corr().round(2)

    pairs = [(a, b, float(corr.loc[a, b]))
             for i, a in enumerate(corr.index)
             for b in corr.columns[i + 1:]
             if pd.notna(corr.loc[a, b]) and float(corr.loc[a, b]) > threshold]

    adj: Dict[str, set] = {t: set() for t in corr.index}
    for a, b, _ in pairs:
        adj[a].add(b)
        adj[b].add(a)
    clusters = [c for c in _cliques(adj) if len(c) >= CLUSTER_MIN_SIZE]

    return CorrelationReport(
        matrix=corr,
        window_weeks=window,
        sufficient=window >= CORR_WINDOW_MIN_WEEKS,
        clusters=sorted(clusters),
        flagged_pairs=sorted(pairs, key=lambda x: -x[2]),
    )


@dataclass
class ClusterCheck:
    cluster: List[str]
    aggregate_weight: float
    over_cap: bool                       # > 20% aggregate without approval
    note: str


def cluster_exposure(report: CorrelationReport,
                     weights: Mapping[str, float]) -> List[ClusterCheck]:
    """Aggregate portfolio exposure per flagged cluster vs the 20% cap.
    ``weights`` are portfolio fractions (0.05 = 5%)."""
    out = []
    for c in report.clusters:
        w = sum(float(weights.get(t, 0.0)) for t in c)
        out.append(ClusterCheck(
            cluster=c, aggregate_weight=w, over_cap=w > CLUSTER_EXPOSURE_CAP,
            note=(f"cluster {'/'.join(c)}: {w:.1%} aggregate"
                  + (" — EXCEEDS 20% cap (written approval required)"
                     if w > CLUSTER_EXPOSURE_CAP else " — within 20% cap")),
        ))
    return out


def expands_flagged_cluster(ticker: str, report: CorrelationReport,
                            candidate_corr: Mapping[str, float]) -> bool:
    """Rule 36 staging: a new position that expands a flagged cluster must
    be initiated at 50% of normal initial size. True if ``ticker``
    correlates > threshold with 2+ members of any flagged cluster."""
    for c in report.clusters:
        hits = sum(1 for m in c
                   if float(candidate_corr.get(m, 0.0)) > CORRELATION_THRESHOLD)
        if hits >= 2:
            return True
    return False


@dataclass
class HoldingsCorrelationCheck:
    """Rule 36 test for a SCREENING CANDIDATE (not yet held) against every
    currently-held equity -- the system's actual quantitative
    diversification test. A GICS/sector label (or a Tier A/B/C planning
    heuristic built from one) is a sequencing convenience, not a
    substitute for this; it should run for every candidate as standard
    practice, not only when a name happens to look suspicious."""
    ticker: str
    window_weeks: int
    sufficient: bool                       # >= 12-month minimum
    correlations: Dict[str, float]         # holding -> pairwise correlation
    max_correlation: Optional[float]
    max_correlation_holding: Optional[str]
    flagged: bool                          # max_correlation > threshold


def correlation_vs_holdings(ticker: str, closes: pd.DataFrame,
                            threshold: float = CORRELATION_THRESHOLD
                            ) -> Optional[HoldingsCorrelationCheck]:
    """Rule 36 test for a screening candidate against every OTHER column
    in ``closes`` (its currently-held equities), using the same windowing
    as correlation_report. Pure -- ``closes`` is already-fetched weekly
    prices (weekly_closes output); callers fetch live data via
    fetch_daily/weekly_closes and pass the result in. None if ``ticker``
    isn't in ``closes`` or there's no holding to compare against."""
    ticker = ticker.upper()
    if ticker not in closes.columns:
        return None
    holdings = [c for c in closes.columns if c.upper() != ticker]
    if not holdings:
        return None
    universe = [ticker] + holdings
    report = correlation_report(weekly_returns(closes[universe]), threshold=threshold)
    if ticker not in report.matrix.index:
        return None
    row = report.matrix.loc[ticker].drop(ticker, errors="ignore").dropna()
    if row.empty:
        return HoldingsCorrelationCheck(ticker, report.window_weeks,
                                        report.sufficient, {}, None, None, False)
    correlations = {k: float(v) for k, v in row.items()}
    max_holding = max(correlations, key=correlations.get)
    max_corr = correlations[max_holding]
    return HoldingsCorrelationCheck(
        ticker=ticker, window_weeks=report.window_weeks,
        sufficient=report.sufficient, correlations=correlations,
        max_correlation=max_corr, max_correlation_holding=max_holding,
        flagged=max_corr > threshold)


# --------------------------------------------------------------------------- #
# Relative Strength vs SPY (Tier 2 rubric, 3%)
# --------------------------------------------------------------------------- #

@dataclass
class RelativeStrength:
    diff_6m: Optional[float]
    diff_12m: Optional[float]
    diff_blended: Optional[float]        # mean of the available windows
    score: Optional[int]                 # Part 2 rubric draft


def _total_return(closes: pd.Series, weeks: int) -> Optional[float]:
    s = closes.dropna()
    if len(s) < weeks + 1:
        return None
    return float(s.iloc[-1] / s.iloc[-(weeks + 1)] - 1.0)


def rs_score(diff: float) -> int:
    """Rubric: 5 = beat SPY by >15%; 4 = 5-15%; 3 = within +/-5%;
    2 = lag 5-15%; 1 = lag >15%."""
    if diff > 0.15:
        return 5
    if diff > 0.05:
        return 4
    if diff >= -0.05:
        return 3
    if diff >= -0.15:
        return 2
    return 1


def relative_strength(ticker_closes: pd.Series,
                      spy_closes: pd.Series) -> RelativeStrength:
    """6-12 month total return vs SPY on weekly closes (dividend-adjusted,
    so total return per the Part 10 definition)."""
    diffs = {}
    for label, weeks in (("6m", 26), ("12m", 52)):
        rt = _total_return(ticker_closes, weeks)
        rb = _total_return(spy_closes, weeks)
        diffs[label] = None if rt is None or rb is None else rt - rb
    avail = [d for d in diffs.values() if d is not None]
    blended = float(np.mean(avail)) if avail else None
    return RelativeStrength(
        diff_6m=diffs["6m"], diff_12m=diffs["12m"], diff_blended=blended,
        score=None if blended is None else rs_score(blended),
    )


# --------------------------------------------------------------------------- #
# Rule 20 — beta (5-year weekly, 2-year fallback)
# --------------------------------------------------------------------------- #

@dataclass
class BetaResult:
    beta: Optional[float]
    window_weeks: Optional[int]
    source: str                          # "5y weekly" | "2y weekly" | "unavailable"
    size_reduction_pct: int              # Rule 20: 0, 1, or 2


def beta_size_reduction(beta: Optional[float]) -> int:
    """Rule 20: -1% for beta 1.40-1.59, -2% for beta >= 1.60, capped at 2.
    (v1.0 banding — NOT the v7 per-0.10-increment overlay.)"""
    if beta is None or beta < 1.40:
        return 0
    return 1 if beta < 1.60 else 2


def weekly_beta(ticker_closes: pd.Series, spy_closes: pd.Series) -> BetaResult:
    """Rule 20: 5-year weekly beta where available, 2-year weekly fallback."""
    both = pd.concat({"t": ticker_closes, "m": spy_closes}, axis=1).dropna()
    rets = both.pct_change().dropna()
    if len(rets) >= BETA_WINDOW_PRIMARY_WEEKS - 20:   # ~5y of history
        tail, label = rets.tail(BETA_WINDOW_PRIMARY_WEEKS), "5y weekly"
    elif len(rets) >= BETA_MIN_WEEKS:                 # thin history: 2y window
        tail, label = rets.tail(BETA_WINDOW_FALLBACK_WEEKS), "2y weekly"
    else:
        return BetaResult(None, None, "unavailable", 0)
    var = float(tail["m"].var())
    if var <= 0:
        return BetaResult(None, None, "unavailable", 0)
    b = float(tail["t"].cov(tail["m"]) / var)
    return BetaResult(round(b, 2), len(tail), label, beta_size_reduction(b))


# --------------------------------------------------------------------------- #
# Technicals — Entry Rules 8-9 + Tier 3 rubric
# --------------------------------------------------------------------------- #

@dataclass
class TechnicalState:
    above_200w_ma: Optional[bool]
    reclaimed_within_6m: Optional[bool]  # upward cross of the 200w MA in 26 wks
    staging_ok: Optional[bool]           # Rule 8: full initial size permitted
    monthly_macd: Optional[float]
    monthly_macd_signal: Optional[float]
    macd_positive_or_turning: Optional[bool]   # Rule 9
    supertrend_bullish: Optional[bool]
    ad_line_score: Optional[int]         # Tier 3 volume rubric draft
    technical_trend_score: Optional[int] # Tier 3 technical rubric draft


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def monthly_macd(closes_daily: pd.Series) -> Tuple[Optional[float], Optional[float], Optional[bool]]:
    """Monthly MACD in percentage form (PPO: (EMA12-EMA26)/EMA26, 9 signal).

    The percentage form is used so a steady exponential decline reads as
    flat negative momentum rather than "rising toward zero" (a raw
    price-level MACD artifact). Returns (macd, signal, ok) where ok is
    Rule 9's 'positive or turning positive': line above zero, or below
    zero but above its signal line and rising for two consecutive months
    (a one-month uptick in a downtrend is not 'turning')."""
    m = closes_daily.resample("ME").last().dropna()
    if len(m) < 30:
        return None, None, None
    e12, e26 = _ema(m, 12), _ema(m, 26)
    macd = 100.0 * (e12 - e26) / e26
    sig = _ema(macd, 9)
    last, prev, prev2 = (float(macd.iloc[-1]), float(macd.iloc[-2]),
                         float(macd.iloc[-3]))
    turning = last > float(sig.iloc[-1]) and last > prev > prev2
    ok = last > 0 or turning
    return round(last, 4), round(float(sig.iloc[-1]), 4), ok


def ma_200w_state(closes_daily: pd.Series) -> Tuple[Optional[bool], Optional[bool]]:
    """(above 200-week MA now, reclaimed it within the past 26 weeks)."""
    w = closes_daily.resample("W-FRI").last().dropna()
    if len(w) < 200:
        return None, None
    ma = w.rolling(200).mean()
    above = (w > ma).astype(bool)
    above_now = bool(above.iloc[-1])
    recent = above.tail(27)  # 26 transitions
    was_below = ~recent.shift(1, fill_value=True)
    crosses_up = bool((recent & was_below).any())
    return above_now, crosses_up


def supertrend_bullish(daily: pd.DataFrame, period: int = 10,
                       mult: float = 3.0) -> Optional[bool]:
    """Weekly Supertrend (ATR period 10, multiplier 3): True if the close
    is above the active Supertrend line."""
    w = daily.resample("W-FRI").agg(
        {"High": "max", "Low": "min", "Close": "last"}).dropna()
    if len(w) < period + 5:
        return None
    h, l, c = w["High"], w["Low"], w["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(),
                    (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    mid = (h + l) / 2.0
    upper, lower = mid + mult * atr, mid - mult * atr
    # standard band-carrying recursion
    fu, fl = upper.copy(), lower.copy()
    for i in range(1, len(w)):
        fu.iloc[i] = min(upper.iloc[i], fu.iloc[i - 1]) \
            if c.iloc[i - 1] <= fu.iloc[i - 1] else upper.iloc[i]
        fl.iloc[i] = max(lower.iloc[i], fl.iloc[i - 1]) \
            if c.iloc[i - 1] >= fl.iloc[i - 1] else lower.iloc[i]
    bullish = True
    for i in range(1, len(w)):
        if bullish and c.iloc[i] < fl.iloc[i]:
            bullish = False
        elif not bullish and c.iloc[i] > fu.iloc[i]:
            bullish = True
    return bullish


def ad_line_score(daily: pd.DataFrame) -> Optional[int]:
    """Tier 3 Volume & Accumulation rubric draft from the A/D line trend
    over 3 and 6 months: 5 strong accumulation, 4 mild, 3 neutral,
    2 distribution, 1 heavy distribution."""
    need = {"High", "Low", "Close", "Volume"}
    if not need.issubset(daily.columns) or len(daily) < 130:
        return None
    h, l, c, v = daily["High"], daily["Low"], daily["Close"], daily["Volume"]
    rng = (h - l).replace(0, np.nan)
    mfm = ((c - l) - (h - c)) / rng
    ad = (mfm.fillna(0) * v).cumsum()
    d3 = float(ad.iloc[-1] - ad.iloc[-63])    # ~3 months
    d6 = float(ad.iloc[-1] - ad.iloc[-126])   # ~6 months
    scale = float(v.tail(126).mean()) or 1.0  # normalize by typical volume
    s3, s6 = d3 / (63 * scale), d6 / (126 * scale)
    avg = (s3 + s6) / 2.0
    if avg > 0.15:
        return 5
    if avg > 0.03:
        return 4
    if avg >= -0.03:
        return 3
    if avg >= -0.15:
        return 2
    return 1


def technical_trend_score(above_ma: Optional[bool], macd_ok: Optional[bool],
                          st_bullish: Optional[bool]) -> Optional[int]:
    """Tier 3 Technical Trend rubric: 5 = all three aligned bullish;
    4 = most aligned; 1 = all bearish; 2 = below 200w MA or MACD negative;
    3 = mixed."""
    sigs = [above_ma, macd_ok, st_bullish]
    if any(s is None for s in sigs):
        return None
    bulls = sum(sigs)
    if bulls == 3:
        return 5
    if bulls == 2:
        return 4
    if bulls == 0:
        return 1
    return 2 if (not above_ma or not macd_ok) else 3


def technical_state(daily: pd.DataFrame) -> TechnicalState:
    """All Rule 8-9 and Tier 3 technicals for one asset's daily OHLCV."""
    closes = daily["Close"]
    above, reclaimed = ma_200w_state(closes)
    macd, sig, macd_ok = monthly_macd(closes)
    st = supertrend_bullish(daily)
    ad = ad_line_score(daily)
    staging = None if above is None else (above or bool(reclaimed))
    return TechnicalState(
        above_200w_ma=above, reclaimed_within_6m=reclaimed,
        staging_ok=staging, monthly_macd=macd, monthly_macd_signal=sig,
        macd_positive_or_turning=macd_ok, supertrend_bullish=st,
        ad_line_score=ad,
        technical_trend_score=technical_trend_score(above, macd_ok, st),
    )


# --------------------------------------------------------------------------- #
# Tier 3 draft wrappers -- these three scores were already computed above
# (rs_score, technical_trend_score, ad_line_score) but never turned into a
# Draft and proposed to the score store; landry.fundamentals.Draft is the
# same dataclass draft_quant_scores uses, so these slot into the same
# pending -> approve -> score_stock pipeline as the fundamentals drafts.
# --------------------------------------------------------------------------- #

def draft_relative_strength(rs: RelativeStrength) -> Optional["Draft"]:
    """Relative Strength vs SPY (Tier 3, 3%)."""
    from landry.fundamentals import Draft
    if rs.score is None:
        return None
    parts = []
    if rs.diff_6m is not None:
        parts.append(f"6mo {rs.diff_6m:+.1%}")
    if rs.diff_12m is not None:
        parts.append(f"12mo {rs.diff_12m:+.1%}")
    detail = ", ".join(parts) if parts else "insufficient window detail"
    return Draft("relative_strength", rs.score, "M",
                f"blended vs SPY {rs.diff_blended:+.1%} ({detail})")


def draft_technical_trend(tech: TechnicalState) -> Optional["Draft"]:
    """Technical Trend (Tier 3, 2%): 200-week MA + monthly MACD + Supertrend."""
    from landry.fundamentals import Draft
    if tech.technical_trend_score is None:
        return None
    ma = "above" if tech.above_200w_ma else "below"
    if tech.above_200w_ma is False and tech.reclaimed_within_6m:
        ma += " (reclaimed <6mo)"
    return Draft("technical_trend", tech.technical_trend_score, "M",
                f"200w MA {ma}, MACD "
                f"{'ok' if tech.macd_positive_or_turning else 'not ok'}, "
                f"Supertrend "
                f"{'bullish' if tech.supertrend_bullish else 'bearish'}")


def draft_volume_accumulation(tech: TechnicalState) -> Optional["Draft"]:
    """Volume & Accumulation (Tier 3, 1%): A/D line trend over 3/6 months."""
    from landry.fundamentals import Draft
    if tech.ad_line_score is None:
        return None
    return Draft("volume_accumulation", tech.ad_line_score, "M",
                f"A/D line trend score {tech.ad_line_score}/5 "
                "(3mo/6mo accumulation-distribution vs typical volume)")


# --------------------------------------------------------------------------- #
# live data plumbing (network only here)
# --------------------------------------------------------------------------- #

def fetch_daily(tickers: Sequence[str], years: int = 6,
                refresh: bool = False) -> Dict[str, pd.DataFrame]:
    """Daily OHLCV via the Layer 1 cache. 6 years covers the 200-week MA
    and the 5-year beta window."""
    from layer1_data_strategies import download_data
    start = (pd.Timestamp.today() - pd.DateOffset(years=years)).strftime("%Y-%m-%d")
    return download_data(list(dict.fromkeys([t.upper() for t in tickers])),
                         start=start, end="today", min_bars=200,
                         refresh=refresh, verbose=False)


def market_snapshot(ticker: str) -> Dict[str, Optional[float]]:
    """Workbook Market Data tab equivalents, best-effort from yfinance."""
    import yfinance as yf
    out: Dict[str, Optional[float]] = dict.fromkeys(
        ("price", "volume", "market_cap", "pe", "wk52_low", "wk52_high",
         "dividend_yield"))
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return out
    out.update(
        price=info.get("currentPrice") or info.get("regularMarketPrice"),
        volume=info.get("volume") or info.get("regularMarketVolume"),
        market_cap=info.get("marketCap"),
        pe=info.get("trailingPE"),
        wk52_low=info.get("fiftyTwoWeekLow"),
        wk52_high=info.get("fiftyTwoWeekHigh"),
        dividend_yield=info.get("dividendYield"),
    )
    return out


def classification(ticker: str) -> Dict[str, Optional[str]]:
    """Sector + Industry from yfinance, best-effort. Industry is the
    refined sub-class this exists for -- e.g. "Utilities - Independent
    Power Producers", not just "Utilities" -- which is what actually
    would have flagged NRG/VST's AI-power-narrative correlation with each
    other if it had been used for the original Darryl-list Tier A/B/C
    sequencing instead of the coarser sector label. Not a Hard Rule
    input; purely a Scoring-tab reference field."""
    import yfinance as yf
    out: Dict[str, Optional[str]] = {"sector": None, "industry": None}
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return out
    out["sector"] = info.get("sector")
    out["industry"] = info.get("industry")
    return out


def next_earnings_date(ticker: str):
    """Next (or most recent, if none is scheduled yet) earnings date from
    yfinance, best-effort. Feeds Monitor & Recheck Triggers col J -- Excel
    has no native formula for this (confirmed 2026-08-26: STOCKHISTORY only
    covers price/volume history, and the Stocks linked-data-type doesn't
    expose an earnings-date field without a 3rd-party add-in), so this is a
    periodic write, same maintenance shape as Price History's weekly closes:
    re-run and re-paste, not a live formula. Returns a `datetime.date` or
    None if yfinance has nothing for this ticker."""
    import yfinance as yf
    try:
        cal = yf.Ticker(ticker).calendar or {}
    except Exception:
        return None
    dates = cal.get("Earnings Date")
    if not dates:
        return None
    return dates[0]


# --------------------------------------------------------------------------- #
# Insider activity (Monitor & Recheck Triggers col K/L) -- SEC EDGAR Form 4
# --------------------------------------------------------------------------- #

_EDGAR_UA = "Landry System personal portfolio tool alan.landry@gmail.com"
_CIK_MAP_CACHE: Optional[Dict[str, str]] = None


def _edgar_get(url: str):
    import json
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": _EDGAR_UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
    return raw, json


def _cik_for_ticker(ticker: str) -> Optional[str]:
    """Ticker -> 10-digit zero-padded CIK, via SEC's own bulk mapping file.
    Cached in-process since the mapping (~10k companies) rarely changes and
    we look up the same 45 tickers repeatedly."""
    global _CIK_MAP_CACHE
    if _CIK_MAP_CACHE is None:
        try:
            raw, json_mod = _edgar_get("https://www.sec.gov/files/company_tickers.json")
            data = json_mod.loads(raw)
            _CIK_MAP_CACHE = {v["ticker"]: str(v["cik_str"]).zfill(10) for v in data.values()}
        except Exception:
            _CIK_MAP_CACHE = {}
    return _CIK_MAP_CACHE.get(ticker.upper())


def _recent_form4_accessions(cik: str, lookback_days: int) -> List[Tuple[str, str]]:
    """[(accessionNumber, filingDate), ...] for Form 4s filed in the window."""
    import datetime
    import json as json_mod

    try:
        raw, _ = _edgar_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        data = json_mod.loads(raw)
    except Exception:
        return []
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accns = recent.get("accessionNumber", [])
    cutoff = (datetime.date.today() - datetime.timedelta(days=lookback_days)).isoformat()
    out = []
    for form, date, accn in zip(forms, dates, accns):
        if form == "4" and date >= cutoff:
            out.append((accn, date))
    return out


def _form4_transactions(cik: str, accession: str) -> List[dict]:
    """Non-derivative P/S transactions from one Form 4 filing's raw XML.
    Returns [] for filings with no transactions (e.g. a pure Section-16-exit
    notice) or any fetch/parse failure -- best-effort, matching this module's
    other yfinance-based fetchers."""
    import time
    import xml.etree.ElementTree as ET

    accession_nodash = accession.replace("-", "")
    try:
        raw, _ = _edgar_get(
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/index.json"
        )
        import json as json_mod
        index = json_mod.loads(raw)
        xml_name = next(
            (it["name"] for it in index["directory"]["item"]
             if it["name"].endswith(".xml") and "xsl" not in it["name"].lower()),
            None,
        )
        if not xml_name:
            return []
        time.sleep(0.15)  # be polite -- two requests per filing
        xml_raw, _ = _edgar_get(
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{xml_name}"
        )
        root = ET.fromstring(xml_raw)
    except Exception:
        return []

    owner = root.find(".//reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find(".//reportingOwnerRelationship")
    title = "Officer" if rel is not None and rel.findtext("isOfficer") == "1" else (
        "Director" if rel is not None and rel.findtext("isDirector") == "1" else (
        "10%-owner" if rel is not None and rel.findtext("isTenPercentOwner") == "1" else "Other"))
    officer_title = (rel.findtext("officerTitle") or "").strip() if rel is not None else ""

    out = []
    for txn in root.findall(".//nonDerivativeTable/nonDerivativeTransaction"):
        code = txn.findtext(".//transactionCoding/transactionCode")
        if code not in ("P", "S"):
            continue
        try:
            shares = float(txn.findtext(".//transactionAmounts/transactionShares/value") or 0)
            price = float(txn.findtext(".//transactionAmounts/transactionPricePerShare/value") or 0)
            following = float(txn.findtext(".//postTransactionAmounts/sharesOwnedFollowingTransaction/value") or 0)
        except (TypeError, ValueError):
            continue
        acq_disp = txn.findtext(".//transactionAmounts/transactionAcquiredDisposedCode/value")
        prior = following - shares if acq_disp == "A" else following + shares
        pct_change = (shares / prior * 100) if prior > 0 else None
        out.append({
            "code": code,
            "owner": (owner.text if owner is not None else "Unknown"),
            "title": officer_title or title,
            "shares": shares,
            "price": price,
            "dollar_value": shares * price,
            "pct_change": pct_change,
        })
    return out


def insider_activity_flag(ticker: str, lookback_days: int = 30,
                           min_dollar: float = 100_000, min_pct: float = 10.0
                           ) -> Tuple[Optional[str], Optional[str]]:
    """Monitor & Recheck Triggers col K/L, from real SEC Form 4 filings
    (not a secondary aggregator). Flags "Y" for any Purchase or Sale
    transaction in the lookback window whose dollar value clears
    ``min_dollar`` OR whose magnitude of ownership change clears ``min_pct``
    -- OR, not AND, deliberately: this tab exists to catch anything that
    might warrant a fresh look across 45 already-known tickers, not to
    screen the whole market down to only the rarest, highest-conviction
    stories the way a discovery tool like OpenInsider's default AND-combined
    filters would (agreed with Alan 2026-08-26).

    Grant/gift/tax/option-exercise/inherited transactions (codes other than
    P/S) are excluded -- those don't reflect the insider's own buy/sell
    judgment. Returns (None, None) on any lookup failure or when there's
    simply nothing to report, so a blank K/L reads the same as "checked,
    nothing found" and "couldn't check" -- callers that care about the
    difference should treat any exception here as the latter."""
    cik = _cik_for_ticker(ticker)
    if not cik:
        return None, None

    hits = []
    for accession, filing_date in _recent_form4_accessions(cik, lookback_days):
        for txn in _form4_transactions(cik, accession):
            qualifies = (
                txn["dollar_value"] >= min_dollar
                or (txn["pct_change"] is not None and abs(txn["pct_change"]) >= min_pct)
                or (txn["pct_change"] is None and txn["code"] == "P")  # brand-new position
            )
            if qualifies:
                hits.append((filing_date, txn))

    if not hits:
        return "N", None

    hits.sort(key=lambda h: h[0], reverse=True)
    filing_date, txn = hits[0]
    action = "bought" if txn["code"] == "P" else "sold"
    pct_txt = f", {abs(txn['pct_change']):.0f}% of holding" if txn["pct_change"] is not None else ""
    note = (f"{filing_date}: {txn['owner']} ({txn['title']}) {action} "
            f"${txn['dollar_value']:,.0f}{pct_txt}")
    if len(hits) > 1:
        note += f" (+{len(hits)-1} more in last {lookback_days}d)"
    return "Y", note


# --------------------------------------------------------------------------- #
# Analyst consensus shift (Monitor & Recheck Triggers col M)
# --------------------------------------------------------------------------- #

_RATING_WEIGHTS = {"strongBuy": 5, "buy": 4, "hold": 3, "sell": 2, "strongSell": 1}


def _composite_rating(row) -> Tuple[Optional[float], int]:
    """Weighted 1-5 consensus score (Strong Buy=5..Strong Sell=1) from one row
    of yfinance's recommendations breakdown, plus the analyst count it's built
    from. None if there's no coverage at all for that period."""
    total = sum(int(row.get(k, 0) or 0) for k in _RATING_WEIGHTS)
    if total == 0:
        return None, 0
    score = sum(int(row.get(k, 0) or 0) * w for k, w in _RATING_WEIGHTS.items()) / total
    return score, total


def analyst_shift_flag(ticker: str, threshold: float = 0.15
                        ) -> Tuple[Optional[str], Optional[str]]:
    """Monitor & Recheck Triggers col M, from yfinance's aggregate analyst
    rating breakdown (``Ticker.recommendations``: Strong Buy/Buy/Hold/Sell/
    Strong Sell counts at 0/-1/-2/-3 months) -- a real composite consensus,
    not a single firm's action. (``Ticker.upgrades_downgrades`` is mostly
    "Maintains" reiterations with a price-target tweak, not an actual rating
    change, which is why this uses the aggregate breakdown instead.)

    Flags "Y" when the weighted consensus score has moved by >= ``threshold``
    between 3 months ago and today. 0.15 is the default, chosen empirically
    (2026-08-26): about three-quarters of the 45 tracked tickers showed under
    0.10 of drift over 3 months (ordinary noise); the real movers (NOV +0.21,
    PZZA -0.29, NKE -0.19, VRTX -0.14) sit well clear of that band.

    Caveat baked into the note, not the threshold logic: for lightly-covered
    tickers a single analyst's one-notch change can produce a shift this
    size on its own (e.g. 1 of 8 analysts moving a full grade shifts the
    average by 0.125) -- the note reports the current analyst count so a
    reviewer can tell "3 firms moved" from "1 firm moved out of 8" rather
    than the mechanism trying to adjudicate that itself.

    Returns (None, None) on any lookup failure or insufficient history."""
    import yfinance as yf
    try:
        rec = yf.Ticker(ticker).recommendations
    except Exception:
        return None, None
    if rec is None or rec.empty:
        return None, None
    by_period = {row["period"]: row for _, row in rec.iterrows()}
    now, n_now = _composite_rating(by_period.get("0m", {}))
    then, _n_then = _composite_rating(by_period.get("-3m", {}))
    if now is None or then is None:
        return None, None
    delta = now - then
    if abs(delta) < threshold:
        return "N", None
    direction = "improved" if delta > 0 else "declined"
    note = (f"Consensus {direction} {abs(delta):.2f} (1-5 scale) over 3 months "
            f"({then:.2f} -> {now:.2f}, {n_now} analysts currently)")
    return "Y", note
