"""The ETF sleeve report (added 2026-10-08; Alan: "Turn it into an ETF Report").

``python -m landry etf [--docx [PATH]] [--pdf]`` reads the workbook's positions and prints -- or, with ``--docx``,
writes ``docs/Landry ETF Report <date>.docx`` -- a read-only picture of the ETF sleeve: what it holds and what it costs,
which funds are really the same bet (average-linkage correlation groups at Rule 38's 0.70), what each is made of
(Fama-French 5-factor + momentum loadings), the sector look-through and the overlap with the direct stocks, where the
portfolio's variance sits, what is missing (bond / defensive / international candidates tested by shifting 7% of the
portfolio into them), the rules the sleeve touches, and the open design questions (Open Items #7).

It writes nothing to the workbook. It exists because the ETF strategy is a wanted addition to the strategy and the
rules (Journal row 115) and the sleeve is 45% of the portfolio: every number in the 10/8/26 discussion came from
throwaway scripts, and the discussion will recur.

THE LIMITS, stated in the report too: top-10 holdings only (so overlaps are lower bounds), a 3-year weekly window
dominated by one bull market (correlations of bonds and defensives can flip), factor regressions on 60 monthly
points, and no history before an ETF's launch (MLPI had about 42 weeks on 10/8/26). Prices are dividend-adjusted
closes from yfinance; factor data are Kenneth French's monthly files (reachable without a key).

The computation is pure functions over DataFrames so the tests run offline; the network sits in ``fetch_*``.
"""

from __future__ import annotations

import datetime as dt
import io
import os
import time
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = "SPY"
WEEKS = 156                                   # three years of weekly returns
FACTOR_MONTHS = 60
CLUSTER_CORR = 0.70                           # Rule 38's pairwise line
CLUSTER_CAP = 0.20                            # Rule 38's cluster cap, as a share of the portfolio
SECTOR_CAP = 0.25                             # Rule 16
CASH_BAND = (0.05, 0.15)                      # Part 5
SHIFT = 0.07                                  # the candidate test moves 7% of the portfolio
FRENCH_BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
FACTOR_COLS = ["MKT", "SMB", "HML", "RMW", "CMA", "MOM"]

#: ticker -> what it would add; chosen as the exposures the sleeve lacks (no bonds, no international, few defensives)
CANDIDATES = {
    "BIL": "T-bills (cash-like)", "AGG": "US aggregate bonds", "TLT": "long Treasuries", "XLP": "Consumer Staples",
    "GLD": "gold", "XLU": "Utilities", "XLV": "Health Care", "USMV": "minimum-volatility equities",
    "RSP": "equal-weight S&P 500", "VXUS": "international ex-US", "XLF": "Financials", "XLC": "Communication Services",
    "QUAL": "quality factor", "XLY": "Consumer Discretionary",
}

SECTOR_KEYS = {
    "Technology": "technology", "Healthcare": "healthcare", "Financial Services": "financial_services",
    "Consumer Cyclical": "consumer_cyclical", "Consumer Defensive": "consumer_defensive", "Energy": "energy",
    "Industrials": "industrials", "Utilities": "utilities", "Basic Materials": "basic_materials",
    "Communication Services": "communication_services", "Real Estate": "realestate",
}
SECTOR_LABEL = {v: k for k, v in SECTOR_KEYS.items()}
#: a hand list, used only to total the semiconductor names inside ETF top-10s; extend it as holdings change
SEMIS = {"NVDA", "AVGO", "TSM", "ASML", "KLAC", "MU", "AMD", "INTC", "LRCX", "AMAT", "SNDK", "QCOM", "TXN", "MRVL", "ON", "NXPI", "MCHP", "ADI"}

DESIGN_QUESTIONS = [
    "Role: is the sleeve a reservoir to draw down into single stocks, a permanent core allocation with its own target weight, or both?",
    "Classification: do ETFs count as cash for Part 5's 5-15% band, and does the 25% sector cap use look-through?",
    "Ballast: which exposures should ETFs supply -- defensive sectors, bonds, gold, international -- now that Rule 12 screens out low-growth defensive single names?",
    "Selection rules: a cost cap, a size and liquidity floor, and no overlap with direct holdings?",
    "Caps and funding: per-ETF and per-cluster caps (Rule 38), and a draw-down order by correlation to the direct book rather than a fixed SPMO-first?",
    "Scoring: a light ETF scorecard (cost, diversification contribution, drawdown behaviour) in place of Part 12 scoring?",
]


# --------------------------------------------------------------------------------------------- the data model --

@dataclass
class Sleeve:
    total: float                                   # whole portfolio, cash included
    etf: Dict[str, float]                          # ticker -> market value
    direct: Dict[str, float]                       # directly held equities
    other: float                                   # cash and anything else
    accounts: Dict[str, Dict[str, Tuple[float, float]]] = field(default_factory=dict)   # account -> ticker -> (value, unrealized $)

    @property
    def etf_total(self) -> float:
        return sum(self.etf.values())

    @property
    def direct_total(self) -> float:
        return sum(self.direct.values())

    def weights(self) -> pd.Series:
        """Weights as a share of the WHOLE portfolio (cash is the remainder)."""
        return pd.Series({**self.direct, **self.etf}, dtype=float) / self.total


@dataclass
class EtfReport:
    as_of: dt.date
    workbook: str
    sleeve: Sleeve
    holdings: pd.DataFrame
    stats: pd.DataFrame
    corr: pd.DataFrame
    groups: List[dict]
    factors: Optional[pd.DataFrame]
    book_factors: Optional[dict]
    sectors: pd.DataFrame
    overlap: pd.DataFrame
    semis: dict
    risk: dict
    candidates: pd.DataFrame
    rules: List[str]
    notes: List[str]
    weeks: int = WEEKS


def read_sleeve(path: str) -> Sleeve:
    """The ETFs are the held lots the Performance Tracking ledger types 'ETF' (the workbook's own classification); direct
    equities are the other Equity-class positions; whatever is left of the total is cash and the like."""
    from landry import xlsx_io
    positions = xlsx_io.read_positions(path)
    total = xlsx_io.total_portfolio_value(positions)
    etfs = {r["ticker"] for r in xlsx_io.read_performance_tracking(path) if r.get("type") == "ETF" and r.get("status") == "Held"}
    etf: Dict[str, float] = {}
    direct: Dict[str, float] = {}
    accounts: Dict[str, Dict[str, Tuple[float, float]]] = {}
    for p in positions:
        if p.market_value <= 0:
            continue
        if p.ticker in etfs:
            etf[p.ticker] = etf.get(p.ticker, 0.0) + p.market_value
            pct = p.unrealized_pct if p.unrealized_pct is not None else 0.0
            gain = p.market_value - p.market_value / (1.0 + pct)
            a = accounts.setdefault(p.account, {})
            v0, g0 = a.get(p.ticker, (0.0, 0.0))
            a[p.ticker] = (v0 + p.market_value, g0 + gain)
        elif p.asset_class == "Equity":
            direct[p.ticker] = direct.get(p.ticker, 0.0) + p.market_value
    return Sleeve(total=total, etf=etf, direct=direct, other=total - sum(etf.values()) - sum(direct.values()), accounts=accounts)


def read_direct_sectors(path: str, tickers: Sequence[str]) -> Dict[str, str]:
    from landry import xlsx_io
    sec = {r.ticker: r.sector for r in xlsx_io.read_scoring_tab(path) if r.ticker in tickers and r.sector}
    return {t: sec.get(t, "Unknown") for t in tickers}


# ----------------------------------------------------------------------------------------------- the network --

def fetch_prices(tickers: Sequence[str], years: int = 6) -> pd.DataFrame:
    """Daily dividend-adjusted closes, one column per ticker (yfinance)."""
    import yfinance as yf
    px = yf.download(list(dict.fromkeys(tickers)), period=f"{years}y", auto_adjust=True, progress=False)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame(tickers[0])
    px.index = pd.to_datetime(px.index).tz_localize(None)
    return px.loc[:, ~px.columns.duplicated()]


def fetch_fund_facts(tickers: Sequence[str], pause: float = 1.0) -> Dict[str, dict]:
    """{ticker: {name, category, aum, expense_pct, yield_pct, sector_weights, top_holdings}} from yfinance. A field the
    provider lacks is simply absent."""
    import yfinance as yf
    out: Dict[str, dict] = {}
    for t in tickers:
        rec: dict = {}
        tk = yf.Ticker(t)
        try:
            info = tk.info
            rec["name"] = info.get("longName")
            rec["category"] = info.get("category")
            rec["aum"] = info.get("totalAssets")
            rec["expense_pct"] = info.get("netExpenseRatio")
            y = info.get("yield")
            rec["yield_pct"] = None if y is None else float(y) * 100.0
        except Exception:
            pass
        try:
            fd = tk.funds_data
            sw = dict(fd.sector_weightings)
            rec["sector_weights"] = {k: float(v) for k, v in sw.items() if v}
            th = fd.top_holdings
            rec["top_holdings"] = {str(k): float(v) for k, v in th["Holding Percent"].items()}
        except Exception:
            pass
        out[t] = rec
        time.sleep(pause)
    return out


def fetch_french() -> pd.DataFrame:
    """Kenneth French's monthly factors: MKT, SMB, HML, RMW, CMA, RF (5-factor file) and MOM, as decimals, month-end index."""
    import urllib.request

    def one(name: str) -> pd.DataFrame:
        req = urllib.request.Request(FRENCH_BASE + name, headers={"User-Agent": "Mozilla/5.0 landry-research"})
        z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(req, timeout=40).read()))
        lines = z.read(z.namelist()[0]).decode("latin1").splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.split(",")[0].strip().isdigit() and len(ln.split(",")[0].strip()) == 6)
        rows = []
        for ln in lines[start:]:
            parts = [c.strip() for c in ln.split(",")]
            if len(parts[0]) != 6 or not parts[0].isdigit():
                break
            rows.append(parts)
        df = pd.DataFrame(rows).set_index(0).astype(float) / 100.0
        df.index = pd.to_datetime(df.index, format="%Y%m") + pd.offsets.MonthEnd(0)
        return df

    f5 = one("F-F_Research_Data_5_Factors_2x3_CSV.zip")
    f5.columns = ["MKT", "SMB", "HML", "RMW", "CMA", "RF"]
    mom = one("F-F_Momentum_Factor_CSV.zip")
    mom.columns = ["MOM"]
    return f5.join(mom, how="inner")


# --------------------------------------------------------------------------------------- the pure computation --

def weekly_returns(prices: pd.DataFrame, weeks: int = WEEKS) -> pd.DataFrame:
    return prices.resample("W-FRI").last().pct_change().iloc[-weeks:]


def risk_stats(prices: pd.DataFrame, returns: pd.DataFrame, tickers: Sequence[str], bench: str = BENCH,
               worst_frac: float = 0.10) -> pd.DataFrame:
    """Annualised volatility, beta to the benchmark, down-capture (average return in the benchmark's worst weeks over the
    benchmark's), maximum drawdown since the series starts and since 2025, and the first date of data."""
    rows = {}
    b = returns[bench]
    for t in tickers:
        if t not in returns or returns[t].dropna().empty:
            continue
        r = returns[t].dropna()
        bb = b.reindex(r.index)
        ok = bb.notna()
        beta = float(np.polyfit(bb[ok], r[ok], 1)[0]) if ok.sum() > 10 else np.nan
        worst = bb[ok].nsmallest(max(5, int(ok.sum() * worst_frac))).index
        down = float(r.reindex(worst).mean() / bb.reindex(worst).mean()) if len(worst) else np.nan
        s = prices[t].dropna()
        dd_all = float((s / s.cummax() - 1).min())
        s25 = s.loc["2025-01-01":]
        dd_25 = float((s25 / s25.cummax() - 1).min()) if len(s25) > 2 else np.nan
        rows[t] = {"since": s.index[0].date(), "weeks": int(len(r)), "vol": float(r.std() * np.sqrt(52)), "beta": beta,
                   "down_capture": down, "max_dd": dd_all, "max_dd_2025": dd_25}
    return pd.DataFrame(rows).T


def average_linkage_groups(corr: pd.DataFrame, threshold: float = CLUSTER_CORR) -> List[List[str]]:
    """Merge the two groups with the highest average pairwise correlation until none averages at least ``threshold``;
    groups of two or more members come back. (Rule 38's line is pairwise 0.70; an average-linkage group is the stricter
    'these are really one bet' reading.)"""
    groups = [[c] for c in corr.columns]
    while len(groups) > 1:
        best, bi, bj = -2.0, -1, -1
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                a = float(corr.loc[groups[i], groups[j]].values.mean())
                if a > best:
                    best, bi, bj = a, i, j
        if best < threshold:
            break
        groups[bi] = groups[bi] + groups[bj]
        del groups[bj]
    return [g for g in groups if len(g) > 1]


def group_summary(groups: List[List[str]], corr: pd.DataFrame, weights: pd.Series, risk_share: pd.Series) -> List[dict]:
    out = []
    for g in groups:
        sub = corr.loc[g, g].values
        n = len(g)
        avg = float((sub.sum() - n) / (n * (n - 1)))
        w = float(weights.reindex(g).sum())
        out.append({"members": sorted(g, key=lambda t: -float(weights.get(t, 0.0))), "weight": w, "avg_corr": avg,
                    "var_share": float(risk_share.reindex(g).sum()), "over_cap": w > CLUSTER_CAP})
    return sorted(out, key=lambda d: -d["weight"])


def risk_contributions(weights: pd.Series, returns: pd.DataFrame, bench: Optional[pd.Series] = None) -> Tuple[pd.DataFrame, dict]:
    """Each holding's share of portfolio variance (weights are shares of the whole portfolio; cash earns nothing), the
    portfolio's volatility and beta, and how many independent bets the correlations leave (the exponential of the entropy
    of the correlation matrix's eigenvalue shares; PC1 is the first component's share)."""
    names = [n for n in weights.index if n in returns.columns]
    w = weights.reindex(names).astype(float)
    R = returns[names].fillna(0.0)
    cov = R.cov().values * 52.0
    var = float(w.values @ cov @ w.values)
    rc = w.values * (cov @ w.values) / var
    pr = (R * w).sum(axis=1)
    beta = np.nan
    if bench is not None:
        bb = bench.reindex(pr.index).fillna(0.0)
        beta = float(np.polyfit(bb, pr, 1)[0])
    ev = np.clip(np.linalg.eigvalsh(R.corr().fillna(0.0).values)[::-1], 0.0, None)
    p = ev / ev.sum()
    bets = float(np.exp(-(p[p > 0] * np.log(p[p > 0])).sum()))
    table = pd.DataFrame({"weight": w.values, "risk_share": rc}, index=names)
    return table, {"vol": float(np.sqrt(var)), "beta": beta, "bets": bets, "pc1": float(p[0]), "weeks": int(len(R))}


def factor_loadings(monthly_ret: pd.Series, factors: pd.DataFrame, months: int = FACTOR_MONTHS, min_months: int = 24) -> Optional[dict]:
    """OLS of the series' excess monthly return on MKT, SMB, HML, RMW, CMA and MOM (the last ``months`` months that both
    have). None when there are fewer than ``min_months``."""
    d = pd.concat([monthly_ret.rename("y"), factors], axis=1, sort=True).dropna().iloc[-months:]
    if len(d) < min_months:
        return None
    y = (d["y"] - d["RF"]).values
    X = np.column_stack([np.ones(len(d)), d[FACTOR_COLS].values])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ b
    dof = len(d) - X.shape[1]
    sigma2 = float(resid @ resid) / dof
    se = np.sqrt(np.diag(sigma2 * np.linalg.inv(X.T @ X)))
    ss_tot = float(((y - y.mean()) ** 2).sum())
    out = {"n": int(len(d)), "alpha": float(b[0] * 12), "r2": 1.0 - float(resid @ resid) / ss_tot}
    for i, name in enumerate(FACTOR_COLS, start=1):
        out[name] = float(b[i])
        out[name + "_t"] = float(b[i] / se[i])
    return out


def sector_lookthrough(direct: Dict[str, float], direct_sector: Dict[str, str], etf: Dict[str, float],
                       etf_weights: Dict[str, Dict[str, float]], bench_weights: Dict[str, float], total: float) -> pd.DataFrame:
    """Sector weights of the invested equity (direct stocks + ETFs, cash excluded) against the benchmark's."""
    invested = sum(direct.values()) + sum(etf.values())
    d = {k: 0.0 for k in SECTOR_LABEL}
    e = {k: 0.0 for k in SECTOR_LABEL}
    for t, v in direct.items():
        k = SECTOR_KEYS.get(direct_sector.get(t, ""), None)
        if k:
            d[k] += v
    for t, v in etf.items():
        for k, w in (etf_weights.get(t) or {}).items():
            if k in e:
                e[k] += v * w
    rows = []
    for k, label in SECTOR_LABEL.items():
        tot = d[k] + e[k]
        rows.append({"sector": label, "direct": d[k] / invested, "etf": e[k] / invested, "total": tot / invested,
                     "bench": bench_weights.get(k, 0.0), "active": tot / invested - bench_weights.get(k, 0.0), "of_portfolio": tot / total})
    return pd.DataFrame(rows).set_index("sector").sort_values("total", ascending=False)


def holding_overlap(direct: Dict[str, float], etf: Dict[str, float], top_holdings: Dict[str, Dict[str, float]]) -> pd.DataFrame:
    """Dollar exposure to each name through the direct position and the ETFs' top-10 lists (a LOWER bound for the ETF part)."""
    rows: Dict[str, dict] = {}
    for t, v in direct.items():
        rows.setdefault(t, {"direct": 0.0, "etf": 0.0, "via": {}})["direct"] += v
    for e, v in etf.items():
        for sym, w in (top_holdings.get(e) or {}).items():
            s = "GOOGL" if sym == "GOOG" else sym
            r = rows.setdefault(s, {"direct": 0.0, "etf": 0.0, "via": {}})
            r["etf"] += v * w
            r["via"][e] = r["via"].get(e, 0.0) + v * w
    df = pd.DataFrame([{"name": n, "direct": r["direct"], "etf": r["etf"], "total": r["direct"] + r["etf"], "via": r["via"]} for n, r in rows.items() if r["etf"] > 0])
    return df.sort_values("total", ascending=False).reset_index(drop=True) if not df.empty else df


def semiconductor_exposure(direct: Dict[str, float], etf: Dict[str, float], top_holdings: Dict[str, Dict[str, float]], total: float) -> dict:
    d = sum(v for t, v in direct.items() if t in SEMIS)
    e = sum(v * w for f, v in etf.items() for sym, w in (top_holdings.get(f) or {}).items() if sym in SEMIS)
    return {"direct": d, "etf": e, "direct_pct": d / total, "etf_pct": e / total, "total_pct": (d + e) / total}


def candidate_effects(weights: pd.Series, returns: pd.DataFrame, candidates: pd.DataFrame, bench: pd.Series,
                      shift: float = SHIFT) -> pd.DataFrame:
    """For each candidate: its correlation with the portfolio's weekly return and the change in portfolio volatility and
    beta from moving ``shift`` of the portfolio into it, taken pro rata from the current holdings."""
    names = [n for n in weights.index if n in returns.columns]
    w = weights.reindex(names).astype(float)
    rows = {}
    for c in candidates.columns:
        s = candidates[c].dropna()
        idx = returns.index.intersection(s.index)
        if len(idx) < 60:
            continue
        R = returns.loc[idx, names].fillna(0.0)
        pc = (R * w).sum(axis=1)
        w2 = pd.concat([w * (1.0 - shift / w.sum()), pd.Series({c: shift})])
        R2 = R.copy()
        R2[c] = s.loc[idx]
        v1 = float(np.sqrt(w.values @ (R.cov().values * 52.0) @ w.values))
        v2 = float(np.sqrt(w2.values @ (R2[list(w2.index)].cov().values * 52.0) @ w2.values))
        bb = bench.reindex(idx).fillna(0.0)
        beta2 = float(np.polyfit(bb, (R2[list(w2.index)] * w2).sum(axis=1), 1)[0])
        rows[c] = {"weeks": int(len(idx)), "corr": float(np.corrcoef(pc, s.loc[idx])[0, 1]), "vol_before": v1, "vol_after": v2,
                   "change": v2 - v1, "beta_after": beta2}
    df = pd.DataFrame(rows).T
    return df.sort_values("change") if not df.empty else df


# ------------------------------------------------------------------------------------------- assembling it --

def build_report(path: str, *, today: Optional[dt.date] = None, weeks: int = WEEKS, prices: Optional[pd.DataFrame] = None,
                 facts: Optional[Dict[str, dict]] = None, factors: Optional[pd.DataFrame] = None, use_factors: bool = True,
                 candidates: Optional[Sequence[str]] = None, direct_sector: Optional[Dict[str, str]] = None,
                 progress=lambda s: None) -> EtfReport:
    """Everything the report shows, computed from the workbook and (unless injected) yfinance and French's files."""
    sleeve = read_sleeve(path)
    notes: List[str] = []
    cand = list(CANDIDATES if candidates is None else candidates)
    tickers = list(sleeve.etf) + list(sleeve.direct) + [BENCH] + cand
    if prices is None:
        progress("prices ...")
        prices = fetch_prices(tickers)
    missing = [t for t in list(sleeve.etf) + list(sleeve.direct) if t not in prices.columns or prices[t].dropna().empty]
    if missing:
        notes.append(f"no price history for {', '.join(missing)}: left out of the correlation and risk figures")
    if facts is None:
        progress("fund facts ...")
        facts = fetch_fund_facts(list(sleeve.etf) + [BENCH])
    if direct_sector is None:
        direct_sector = read_direct_sectors(path, list(sleeve.direct))
    rets = weekly_returns(prices, weeks)
    held = [t for t in list(sleeve.etf) + list(sleeve.direct) if t in rets.columns and rets[t].dropna().size > 20]
    w_all = sleeve.weights().reindex(held).dropna()

    # ---- per-ETF table
    etf_names = [t for t in sleeve.etf]
    stats = risk_stats(prices, rets, etf_names + [BENCH], BENCH)
    hold_rows = []
    gain = {t: sum(a.get(t, (0.0, 0.0))[1] for a in sleeve.accounts.values()) for t in etf_names}
    for t in sorted(etf_names, key=lambda x: -sleeve.etf[x]):
        f = facts.get(t, {})
        s = stats.loc[t] if t in stats.index else None
        hold_rows.append({"ticker": t, "value": sleeve.etf[t], "pct": sleeve.etf[t] / sleeve.total, "name": f.get("name") or "", "category": f.get("category") or "",
                          "expense": f.get("expense_pct"), "yield": f.get("yield_pct"), "aum": f.get("aum"), "gain": gain.get(t, 0.0),
                          "beta": None if s is None else s["beta"], "down": None if s is None else s["down_capture"],
                          "vol": None if s is None else s["vol"], "weeks": None if s is None else s["weeks"]})
    holdings = pd.DataFrame(hold_rows).set_index("ticker")

    # ---- correlations, groups, risk
    direct_held = [t for t in sleeve.direct if t in rets.columns]
    etf_held = [t for t in etf_names if t in rets.columns]
    book = (rets[direct_held].fillna(0.0) * (pd.Series(sleeve.direct)[direct_held] / sum(sleeve.direct[t] for t in direct_held))).sum(axis=1)
    sl = (rets[etf_held].fillna(0.0) * (pd.Series(sleeve.etf)[etf_held] / sum(sleeve.etf[t] for t in etf_held))).sum(axis=1)
    cmat = rets[etf_held + [BENCH]].copy()
    cmat["BOOK"] = book
    cmat["SLEEVE"] = sl
    corr = cmat.corr()
    table, risk = risk_contributions(w_all, rets, rets[BENCH])
    risk["spy_vol"] = float(rets[BENCH].std() * np.sqrt(52))
    grp_corr = rets[list(w_all.index)].fillna(0.0).corr()
    groups = group_summary(average_linkage_groups(grp_corr), grp_corr, w_all, table["risk_share"])
    risk["direct_weight"] = float(sleeve.direct_total / sleeve.total)
    risk["direct_var"] = float(table.loc[[t for t in direct_held if t in table.index], "risk_share"].sum())
    risk["etf_weight"] = float(sleeve.etf_total / sleeve.total)
    risk["etf_var"] = float(table.loc[[t for t in etf_held if t in table.index], "risk_share"].sum())
    risk["cash_weight"] = float(sleeve.other / sleeve.total)

    # ---- factors
    fac = None
    book_fac = None
    if use_factors:
        try:
            if factors is None:
                progress("factor data ...")
                factors = fetch_french()
            monthly = prices.resample("ME").last().pct_change()
            res = {}
            for t in etf_names:
                if t in monthly.columns:
                    r = factor_loadings(monthly[t], factors)
                    if r:
                        res[t] = r
            fac = pd.DataFrame(res).T if res else None
            bw = pd.Series(sleeve.direct)[direct_held]
            bm = (monthly[direct_held].fillna(0.0) * (bw / bw.sum())).sum(axis=1)
            book_fac = factor_loadings(bm, factors)
            notes.append(f"factor data run through {factors.index[-1].date()}")
        except Exception as e:                                       # the report is still useful without them
            notes.append(f"factor regressions skipped: {type(e).__name__}: {str(e)[:80]}")

    # ---- look-through and overlap
    sector = pd.DataFrame()
    over = pd.DataFrame()
    semis = {"direct": 0.0, "etf": 0.0, "direct_pct": 0.0, "etf_pct": 0.0, "total_pct": 0.0}
    sw = {t: facts.get(t, {}).get("sector_weights") or {} for t in etf_names}
    th = {t: facts.get(t, {}).get("top_holdings") or {} for t in etf_names}
    if any(sw.values()):
        sector = sector_lookthrough(sleeve.direct, direct_sector, sleeve.etf, sw, facts.get(BENCH, {}).get("sector_weights") or {}, sleeve.total)
    else:
        notes.append("no sector weights from the provider: look-through left out")
    if any(th.values()):
        over = holding_overlap(sleeve.direct, sleeve.etf, th)
        semis = semiconductor_exposure(sleeve.direct, sleeve.etf, th, sleeve.total)
    else:
        notes.append("no ETF holdings from the provider: overlap left out")

    # ---- candidates
    cand_cols = [c for c in cand if c in rets.columns]
    cands = candidate_effects(w_all, rets, rets[cand_cols], rets[BENCH]) if cand_cols else pd.DataFrame()

    # ---- rules
    rules: List[str] = []
    for g in groups:
        ets = [m for m in g["members"] if m in sleeve.etf]
        tag = "OVER" if g["over_cap"] else "under"
        rules.append(f"Rule 38 groups (average pairwise correlation at least {CLUSTER_CORR:.2f}): {', '.join(g['members'])} = {g['weight']:.1%} of the portfolio "
                     f"(average correlation {g['avg_corr']:.2f}) -- {tag} the {CLUSTER_CAP:.0%} cluster cap." + (f" ETFs in it: {', '.join(ets)}." if ets and len(ets) < len(g['members']) else ""))
    cw = sleeve.other / sleeve.total
    rules.append(f"Part 5 cash band {CASH_BAND[0]:.0%}-{CASH_BAND[1]:.0%}: cash and equivalents are {cw:.1%} if the ETFs count as invested equity, "
                 f"{(sleeve.other + sleeve.etf_total) / sleeve.total:.1%} if they count as cash -- the workbook's Cash / Cash Equivalents aggregate counts them as cash.")
    dsec: Dict[str, float] = {}
    for t, v in sleeve.direct.items():
        dsec[direct_sector.get(t, "Unknown")] = dsec.get(direct_sector.get(t, "Unknown"), 0.0) + v
    if dsec:
        k, v = max(dsec.items(), key=lambda kv: kv[1])
        line = f"Rule 16 sector cap {SECTOR_CAP:.0%} (market value, direct holdings): the largest is {k} at {v / sleeve.total:.1%} of the portfolio"
        if not sector.empty:
            lt = sector.iloc[0]
            line += f"; with the ETFs looked through {sector.index[0]} is {lt['of_portfolio']:.1%}"
        rules.append(line + ".")
    book_corr = corr.loc[etf_held, "BOOK"].sort_values(ascending=False)
    rules.append("Funding order today is SPMO first; ranked by correlation to the direct stock book the sleeve reads " +
                 ", ".join(f"{t} {c:.2f}" for t, c in book_corr.items()) + " (the highest duplicate the book most).")
    notes.append(f"{weeks} weekly returns (three years where the fund is old enough); prices are dividend-adjusted closes; top-10 holdings only, so overlap and semiconductor "
                 "figures are lower bounds; candidate tests use a bull-market window and bond or defensive correlations can flip")
    return EtfReport(as_of=today or dt.date.today(), workbook=os.path.basename(path), sleeve=sleeve, holdings=holdings, stats=stats, corr=corr, groups=groups,
                     factors=fac, book_factors=book_fac, sectors=sector, overlap=over, semis=semis, risk=risk, candidates=cands, rules=rules, notes=notes, weeks=weeks)


# ------------------------------------------------------------------------------------------------ rendering --

def _pct(x, d=1):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{d}f}%"


def _num(x, d=2):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}"


def _k(x):
    return f"${x / 1000:,.1f}K"


def short_history(rep: EtfReport) -> Dict[str, int]:
    """ETFs with clearly fewer weekly returns than the window: their beta and down-capture are less reliable."""
    return {t: int(r["weeks"]) for t, r in rep.holdings.iterrows() if r["weeks"] is not None and not pd.isna(r["weeks"]) and r["weeks"] < rep.weeks - 4}


def weighted_expense(rep: EtfReport) -> float:
    """Value-weighted expense ratio of the funds that report one, in percent."""
    h = rep.holdings[rep.holdings["expense"].notna()]
    return float((h["value"] * h["expense"].astype(float)).sum() / h["value"].sum()) if len(h) else float("nan")


def glance(rep: EtfReport) -> List[str]:
    s, r = rep.sleeve, rep.risk
    lines = [f"The sleeve is {len(s.etf)} ETFs, {_k(s.etf_total)} = {s.etf_total / s.total:.1%} of the {_k(s.total)} portfolio (direct stocks {s.direct_total / s.total:.1%}, "
             f"cash and other {s.other / s.total:.1%}); weighted expense ratio "
             f"{_pct(weighted_expense(rep) / 100, 2)} a year."]
    for g in rep.groups:
        if g["weight"] >= 0.05:
            lines.append(f"{', '.join(g['members'])} behave as one bet: {g['weight']:.1%} of the portfolio, average correlation {g['avg_corr']:.2f}, "
                         f"{g['var_share']:.1%} of its variance" + (f" -- over Rule 38's {CLUSTER_CAP:.0%} cluster cap." if g["over_cap"] else "."))
    lines.append(f"Direct stocks are {r['direct_weight']:.1%} of the portfolio but {r['direct_var']:.1%} of its variance; the sleeve is {r['etf_weight']:.1%} of weight and {r['etf_var']:.1%} of variance. "
                 f"Portfolio volatility {_pct(r['vol'])} against {BENCH}'s {_pct(r['spy_vol'])}, beta {_num(r['beta'])}, about {r['bets']:.1f} effective independent bets (first component {r['pc1']:.0%}).")
    if rep.semis["total_pct"]:
        lines.append(f"Semiconductor names: {rep.semis['direct_pct']:.1%} directly plus at least {rep.semis['etf_pct']:.1%} through ETF top-10 lists = {rep.semis['total_pct']:.1%} of the portfolio.")
    if not rep.candidates.empty:
        best = rep.candidates.head(4)
        lines.append(f"Most efficient diversifiers tested (move {SHIFT:.0%} of the portfolio into each, change in volatility): " +
                     ", ".join(f"{t} ({CANDIDATES.get(t, t)}) {v['change'] * 100:+.1f} pts" for t, v in best.iterrows()) +
                     ". The sleeve holds no bonds and no international.")
    return lines


def to_text(rep: EtfReport) -> str:
    out: List[str] = []
    s = rep.sleeve
    out.append(f"LANDRY SYSTEM -- ETF SLEEVE REPORT   as of {rep.as_of}   ({rep.workbook}; read-only)")
    out.append("")
    out.extend("* " + ln for ln in glance(rep))
    out.append("")
    out.append("THE SLEEVE")
    out.append(f"{'ETF':6s} {'value':>10s} {'% port':>7s} {'ER':>6s} {'yield':>6s} {'beta':>6s} {'down':>6s}  {'unrealized':>10s}  what it is")
    short = short_history(rep)
    for t, r in rep.holdings.iterrows():
        mark = "†" if t in short else " "
        out.append(f"{t:6s} {r['value']:10,.0f} {r['pct'] * 100:6.1f}% {_num(r['expense'])+'%':>6s} {_pct(r['yield'] / 100 if r['yield'] is not None and not pd.isna(r['yield']) else None):>6s} "
                   f"{_num(r['beta']):>5s}{mark} {_num(r['down']):>5s}{mark} {r['gain']:+10,.0f}  {r['category'] or r['name']}")
    if short:
        out.append("  † " + "; ".join(f"{t}: {w} weeks of history" for t, w in short.items()) + " -- beta and down-capture are less reliable.")
    out.append("")
    out.append("GROUPS THAT ARE REALLY ONE BET (average-linkage, average correlation at least 0.70)")
    for g in rep.groups:
        out.append(f"  {g['weight'] * 100:5.1f}% of portfolio  avg corr {g['avg_corr']:.2f}  variance {g['var_share'] * 100:4.1f}%  {', '.join(g['members'])}" + ("   OVER CAP" if g["over_cap"] else ""))
    if rep.factors is not None and not rep.factors.empty:
        out.append("")
        out.append("FACTOR LOADINGS (monthly, up to 60 months; [t-stat])")
        for t, r in rep.factors.iterrows():
            out.append("  " + _factor_line(t, r))
        if rep.book_factors:
            out.append("  " + _factor_line("BOOK", rep.book_factors) + "   <- the direct stock book, constant weights")
    if not rep.sectors.empty:
        out.append("")
        out.append("SECTOR LOOK-THROUGH (share of invested equity)")
        out.append(f"  {'sector':24s} {'direct':>7s} {'ETFs':>7s} {'total':>7s} {BENCH:>7s} {'active':>7s}")
        for k, r in rep.sectors.iterrows():
            out.append(f"  {k:24s} {r['direct'] * 100:6.1f}% {r['etf'] * 100:6.1f}% {r['total'] * 100:6.1f}% {r['bench'] * 100:6.1f}% {r['active'] * 100:+6.1f}")
    if not rep.overlap.empty:
        out.append("")
        out.append("NAMES HELD DIRECTLY AND THROUGH ETF TOP-10 LISTS (lower bound)")
        for _, r in rep.overlap.head(10).iterrows():
            via = ", ".join(f"{e} {v:,.0f}" for e, v in sorted(r["via"].items(), key=lambda kv: -kv[1]))
            out.append(f"  {r['name']:7s} direct {r['direct']:9,.0f}  ETFs {r['etf']:8,.0f}  = {r['total'] / s.total * 100:4.1f}% of portfolio   via {via}")
    if not rep.candidates.empty:
        out.append("")
        out.append(f"WHAT IS MISSING -- effect of moving {SHIFT:.0%} of the portfolio into a candidate (pro rata from the holdings)")
        out.append(f"  {'candidate':34s} {'corr to portfolio':>17s} {'vol change':>11s} {'beta after':>10s}")
        for t, r in rep.candidates.iterrows():
            out.append(f"  {t + ' ' + CANDIDATES.get(t, ''):34s} {r['corr']:17.2f} {r['change'] * 100:+10.2f} pts {r['beta_after']:10.2f}")
    out.append("")
    out.append("RULES THE SLEEVE TOUCHES")
    out.extend("  - " + ln for ln in rep.rules)
    out.append("")
    out.append("OPEN DESIGN QUESTIONS (Open Items #7)")
    out.extend(f"  {i}. {q}" for i, q in enumerate(DESIGN_QUESTIONS, start=1))
    out.append("")
    out.append("METHOD AND LIMITS")
    out.extend("  - " + n for n in rep.notes)
    return "\n".join(out)


def _factor_line(t, r) -> str:
    cells = " ".join(f"{k} {r[k]:+.2f}" + (f" [{r[k + '_t']:+.1f}]" if k not in ("MKT",) else "") for k in FACTOR_COLS)
    alpha = "" if t == "BOOK" else f"  alpha {r['alpha'] * 100:+.1f}%/yr"        # the book is today's holdings looked back: its alpha is hindsight
    return f"{t:6s} {cells}  R2 {r['r2']:.2f}{alpha}  n={int(r['n'])}"


# ---------------------------------------------------------------------------------------------- the docx --

def default_out(today: Optional[dt.date] = None) -> str:
    return os.path.join(REPO, "docs", f"Landry ETF Report {(today or dt.date.today()).isoformat()}.docx")


def write_docx(rep: EtfReport, out_path: Optional[str] = None) -> str:
    """The printable report, in the house style of the Hard Rules list (Calibri, Letter, narrow margins)."""
    from landry import rules_list as rl
    rl._need_docx()
    from docx import Document
    from docx.enum.text import WD_TAB_ALIGNMENT
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt
    out = out_path or default_out(rep.as_of)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    sec.left_margin = sec.right_margin = Inches(0.65)
    sec.top_margin, sec.bottom_margin = Inches(0.6), Inches(0.7)
    sec.footer_distance = Inches(0.4)
    st = doc.styles["Normal"]
    st.font.name = rl.FONT
    st.font.size = Pt(rl.BODY_PT)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), rl.FONT)
    zoom = doc.settings.element.find(qn("w:zoom"))
    if zoom is not None and zoom.get(qn("w:percent")) is None:
        zoom.set(qn("w:percent"), "100")
    cp = doc.core_properties
    cp.title = f"Landry ETF Report {rep.as_of}"
    cp.author = "Landry System"
    cp.subject = "Read-only report on the ETF sleeve"
    s, r = rep.sleeve, rep.risk
    short = short_history(rep)
    p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(0)
    rl._font(p.add_run("THE LANDRY SYSTEM — ETF SLEEVE REPORT"), size=18, bold=True, color=rl.NAVY)
    p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(4)
    rl._font(p.add_run(f"As of {rep.as_of.strftime('%B')} {rep.as_of.day}, {rep.as_of.year}  |  {rep.workbook}  |  read-only: nothing here is written to the workbook"), size=10.5, color=rl.GREY)
    rl.heading(doc, "At a glance", size=13, space_before=4)
    rl.bullets(doc, glance(rep), size=10)

    rl.heading(doc, "The sleeve")
    rows = []
    for t, h in rep.holdings.iterrows():
        mark = "†" if t in short else ""
        rows.append([t, f"{h['value'] / 1000:,.1f}", f"{h['pct'] * 100:.1f}%", (h["category"] or h["name"] or "")[:34], _num(h["expense"]) + "%",
                     _pct(h["yield"] / 100) if h["yield"] is not None and not pd.isna(h["yield"]) else "n/a", _num(h["beta"]) + mark, _num(h["down"]) + mark, f"{h['gain'] / 1000:+,.1f}"])
    rows.append(["Total", f"{s.etf_total / 1000:,.1f}", f"{s.etf_total / s.total:.1%}", "", "", "", "", "", f"{sum(h['gain'] for _, h in rep.holdings.iterrows()) / 1000:+,.1f}"])
    rl.grid_table(doc, ["ETF", "$K", "% port.", "Category", "Cost", "Yield", "Beta", "Down", "Unreal. $K"], rows,
                  [760, 900, 800, 3300, 800, 800, 700, 700, 1608], size=9, bold_first=True)
    rl.note(doc, "Cost = expense ratio; Down = share of the benchmark's worst 10% of weeks the fund also lost (beta and Down use the last three years where the fund is old enough)." +
            ("  † " + "; ".join(f"{t}: {w} weeks of history" for t, w in short.items()) + " -- less reliable." if short else ""), size=9, space_before=3)

    rl.heading(doc, "What the sleeve is made of")
    if rep.groups:
        grows = [[", ".join(g["members"]), f"{g['weight'] * 100:.1f}%", f"{g['avg_corr']:.2f}", f"{g['var_share'] * 100:.1f}%", "OVER" if g["over_cap"] else "under"] for g in rep.groups]
        rl.grid_table(doc, ["Members (average correlation at least 0.70)", "% of portfolio", "Avg corr", "% of variance", "20% cap"], grows, [5200, 1400, 1100, 1400, 1268], size=9.5)
    if rep.factors is not None and not rep.factors.empty:
        fnote = rl.note(doc, "Fama-French five factors plus momentum, monthly returns, up to 60 months; t-statistics in brackets (|t| above 2 is distinguishable from noise). SMB = size, HML = value, RMW = profitability, CMA = low investment, MOM = momentum.", size=9, space_before=4)
        fnote.paragraph_format.keep_with_next = True
        frows = []
        for t, f in rep.factors.iterrows():
            frows.append([t] + [f"{f[k]:+.2f}" if k == "MKT" else f"{f[k]:+.2f} [{f[k + '_t']:+.1f}]" for k in FACTOR_COLS] + [f"{f['r2']:.2f}"])
        if rep.book_factors:
            f = rep.book_factors
            frows.append(["BOOK"] + [f"{f[k]:+.2f}" if k == "MKT" else f"{f[k]:+.2f} [{f[k + '_t']:+.1f}]" for k in FACTOR_COLS] + [f"{f['r2']:.2f}"])
        rl.grid_table(doc, ["", "MKT", "SMB", "HML", "RMW", "CMA", "MOM", "R²"], frows, [800, 800, 1450, 1450, 1450, 1450, 1450, 1518], size=9, bold_first=True)
        rl.note(doc, "BOOK = today's directly held stocks at constant weights looked back (hindsight, so read its loadings, not its alpha): the sleeve leans value / small / low-investment and the book leans growth.", size=9, space_before=3)

    if not rep.sectors.empty:
        rl.heading(doc, "Look-through and overlap")
        srows = [[k, f"{v['direct'] * 100:.1f}%", f"{v['etf'] * 100:.1f}%", f"{v['total'] * 100:.1f}%", f"{v['bench'] * 100:.1f}%", f"{v['active'] * 100:+.1f}"] for k, v in rep.sectors.iterrows()]
        rl.grid_table(doc, ["Sector (share of invested equity)", "Direct", "ETFs", "Total", BENCH, "Active, pts"], srows, [3600, 1300, 1300, 1300, 1300, 1568], size=9, keep=False)
    if not rep.overlap.empty:
        orows = []
        for _, v in rep.overlap.head(8).iterrows():
            via = ", ".join(f"{e} {x / 1000:.1f}" for e, x in sorted(v["via"].items(), key=lambda kv: -kv[1]))
            orows.append([v["name"], f"{v['direct'] / 1000:,.1f}", f"{v['etf'] / 1000:,.1f}", f"{v['total'] / s.total * 100:.1f}%", via])
        p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(6); p.paragraph_format.space_after = Pt(2); p.paragraph_format.keep_with_next = True
        rl._font(p.add_run("Names held directly and through ETF top-10 lists ($K; a lower bound)"), size=10, bold=True)
        rl.grid_table(doc, ["Name", "Direct", "Via ETFs", "% of portfolio", "Through"], orows, [900, 1100, 1100, 1500, 5768], size=9.5, bold_first=True)

    rl.heading(doc, "Where the risk is")
    rrows = [[", ".join(g["members"]), f"{g['weight'] * 100:.1f}%", f"{g['var_share'] * 100:.1f}%"] for g in rep.groups]
    rrows += [["Direct stocks (all)", f"{r['direct_weight'] * 100:.1f}%", f"{r['direct_var'] * 100:.1f}%"], ["ETF sleeve (all)", f"{r['etf_weight'] * 100:.1f}%", f"{r['etf_var'] * 100:.1f}%"]]
    rl.grid_table(doc, ["Group", "Weight", "Share of variance"], rrows, [6400, 1900, 2068], size=9.5)
    rl.note(doc, f"Portfolio volatility {_pct(r['vol'])} against {BENCH}'s {_pct(r['spy_vol'])}; beta {_num(r['beta'])}; about {r['bets']:.1f} effective independent bets; the first principal component explains {r['pc1']:.0%} of the correlation structure.", size=9.5, space_before=3)

    if not rep.candidates.empty:
        rl.heading(doc, "What is missing")
        crows = [[f"{t}  {CANDIDATES.get(t, '')}", f"{v['corr']:.2f}", f"{v['change'] * 100:+.2f} pts", f"{v['beta_after']:.2f}"] for t, v in rep.candidates.iterrows()]
        rl.grid_table(doc, [f"Candidate (move {SHIFT:.0%} of the portfolio into it)", "Correlation with portfolio", "Change in volatility", "Beta after"], crows, [4600, 2100, 2000, 1668], size=9, keep=False)
        rl.note(doc, "The sleeve holds no bonds and no international. These are three-year weekly correlations from one bull market; bond and defensive correlations can change sign.", size=9, space_before=3)

    rl.heading(doc, "Rules the sleeve touches", space_before=6)
    rl.bullets(doc, rep.rules, size=9.5)
    rl.heading(doc, "Open design questions (Open Items #7)", space_before=6)
    rl.bullets(doc, DESIGN_QUESTIONS, size=9.5, numbered=True)
    rl.heading(doc, "Method and limits", size=11.5)
    rl.bullets(doc, rep.notes, size=9)
    fp = sec.footer.paragraphs[0]
    fp.style = doc.styles["Normal"]
    fp.paragraph_format.tab_stops.add_tab_stop(Inches(7.2), WD_TAB_ALIGNMENT.RIGHT)
    rl._font(fp.add_run(f"Landry System — ETF Report {rep.as_of}\tPage "), size=8.5, color=rl.GREY)
    rl._field(fp, "PAGE", 8.5, rl.GREY)
    rl._font(fp.add_run(" of "), size=8.5, color=rl.GREY)
    rl._field(fp, "NUMPAGES", 8.5, rl.GREY)
    doc.save(out)
    return out
