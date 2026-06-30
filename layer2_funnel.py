"""
Layer 2 — Backtest, Walk-Forward, and the Six-Filter Survival Funnel
====================================================================

Builds on Layer 1. Provides:

  * BACKTEST: a strategy's daily return is its (lagged) position times the
    asset's return, minus a per-side transaction cost (default 1bp, configurable,
    higher for crypto). Metrics on any return series: annualized Sharpe
    (mean/std * sqrt(252)), max drawdown, and trade count (position changes).

  * WALK-FORWARD: split each asset's history into 5 sequential windows. Within
    each window the first 70% is in-sample, the last 30% out-of-sample. Keep ONLY
    the OOS tail of each window, stitch all 5 tails into one combined series, and
    score Sharpe + max drawdown on that stitched OOS series. The combined OOS
    number is the one that matters — the strategy was never tuned on it.

  * SWEEP: run every (config x asset). Record IS Sharpe, OOS Sharpe, OOS max
    drawdown, and OOS trade count to sweep_results.csv; print the total count.

  * SIX-FILTER SURVIVAL FUNNEL (applied to OOS results, all thresholds
    configurable). A config/asset survives only if it passes ALL of:
        1. OOS max drawdown better than -35%
        2. OOS Sharpe above 0.5
        3. OOS Sharpe below 2.5      (above that, the asset did the work)
        4. OOS Sharpe not more than ~30% above IS Sharpe  (gap = overfit)
        5. at least 30 trades        (statistically meaningful)
        6. IS Sharpe positive
    funnel_report() prints the attrition, the headline counts, survival rates by
    category and family, and a top-survivors table. This funnel is the centerpiece.

Lag convention
--------------
Layer 1 already shifts every position one bar (no look-ahead): position[t] is the
position *held during* day t, decided from data through t-1. So the strategy's
day-t return is position[t] * asset_return[t] — we do NOT shift again here. This
is exactly "position times next-day return" on Layer 1's lagged series.

    from layer2_funnel import (
        backtest, walk_forward, run_sweep, apply_filters, funnel_report,
        FunnelThresholds, default_cost_bps,
    )

Dependencies: numpy, pandas (+ Layer 1).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

TRADING_DAYS = 252
_HERE = os.path.dirname(os.path.abspath(__file__))
SWEEP_CSV = os.path.join(_HERE, "sweep_results.csv")


# --------------------------------------------------------------------------- #
# Return / metric primitives
# --------------------------------------------------------------------------- #

def asset_returns(df: pd.DataFrame) -> pd.Series:
    """Simple daily close-to-close returns."""
    return df["Close"].pct_change().fillna(0.0)


def default_cost_bps(ticker: str, equity: float = 1.0, crypto: float = 10.0) -> float:
    """Per-side transaction cost in basis points; crypto gets a higher default."""
    return crypto if str(ticker).upper().endswith("-USD") else equity


def strategy_returns(position: pd.Series, ret: pd.Series,
                     cost_bps: float = 1.0) -> pd.Series:
    """Net daily strategy return.

    position : Layer-1 (already lagged) position series in {-1, 0, 1}
    ret      : asset daily returns, same index
    cost_bps : per-side cost in basis points, charged on traded turnover
    """
    pos, r = position.align(ret, join="inner")
    pos = pos.fillna(0.0)
    r = r.fillna(0.0)
    turnover = pos.diff().abs().fillna(pos.abs())   # |Δposition| each day
    cost = turnover * (cost_bps * 1e-4)             # per-side cost per side traded
    return pos * r - cost


def sharpe(returns: pd.Series, periods: int = TRADING_DAYS) -> float:
    """Annualized Sharpe = mean/std * sqrt(periods). Risk-free assumed 0."""
    r = pd.Series(returns).dropna()
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=0)
    if sd == 0 or not np.isfinite(sd):
        return 0.0
    return float(r.mean() / sd * np.sqrt(periods))


def max_drawdown(returns: pd.Series) -> float:
    """Max drawdown as a positive fraction (0.23 == -23% peak-to-trough)."""
    r = pd.Series(returns).fillna(0.0)
    if len(r) == 0:
        return 0.0
    equity = (1.0 + r).cumprod()
    dd = equity / equity.cummax() - 1.0
    return float(-dd.min())


def trade_count(position: pd.Series) -> int:
    """Number of times the position changes."""
    p = pd.Series(position).fillna(0.0)
    if len(p) == 0:
        return 0
    return int((p.diff().fillna(p.iloc[0]) != 0).sum())


# --------------------------------------------------------------------------- #
# Backtest
# --------------------------------------------------------------------------- #

@dataclass
class BacktestResult:
    returns: pd.Series
    sharpe: float
    max_drawdown: float
    trades: int
    bars: int

    def summary(self) -> dict:
        return {"sharpe": round(self.sharpe, 3),
                "max_drawdown": round(self.max_drawdown, 3),
                "trades": self.trades, "bars": self.bars}


def backtest(position: pd.Series, df: pd.DataFrame,
             cost_bps: float = 1.0) -> BacktestResult:
    """Full-sample backtest of a Layer-1 position series against ``df``."""
    sr = strategy_returns(position, asset_returns(df), cost_bps)
    return BacktestResult(sr, sharpe(sr), max_drawdown(sr),
                          trade_count(position), len(sr))


# --------------------------------------------------------------------------- #
# Walk-forward
# --------------------------------------------------------------------------- #

@dataclass
class WalkForwardResult:
    oos_returns: pd.Series          # stitched out-of-sample tails
    oos_sharpe: float               # <-- the number that matters
    oos_max_drawdown: float
    is_sharpe: float                # stitched in-sample (for overfit comparison)
    is_max_drawdown: float
    n_windows: int
    n_oos_bars: int
    n_oos_trades: int               # trades occurring within the OOS tails
    window_oos_sharpes: List[float] = field(default_factory=list)

    def summary(self) -> dict:
        return {"oos_sharpe": round(self.oos_sharpe, 3),
                "oos_max_drawdown": round(self.oos_max_drawdown, 3),
                "is_sharpe": round(self.is_sharpe, 3),
                "n_oos_bars": self.n_oos_bars, "n_oos_trades": self.n_oos_trades,
                "window_oos_sharpes": [round(s, 2) for s in self.window_oos_sharpes]}


def walk_forward(position: pd.Series, df: pd.DataFrame,
                 cost_bps: float = 1.0, n_windows: int = 5,
                 oos_frac: float = 0.30) -> WalkForwardResult:
    """Split history into ``n_windows`` sequential windows; in each keep the last
    ``oos_frac`` as out-of-sample. Stitch the OOS tails and score them.

    The strategy carries no fitted state, so positions are computed once on the
    full history (preserving indicator warm-up) and the windows simply slice the
    resulting series — the in-sample heads are discarded, never tuned. Trade count
    is measured within the OOS tails, since that is the series being scored.
    """
    ret = asset_returns(df)
    sr = strategy_returns(position, ret, cost_bps).dropna()
    pos = position.reindex(sr.index).fillna(0.0)
    n = len(sr)
    bounds = [round(i * n / n_windows) for i in range(n_windows + 1)]

    oos_parts, is_parts, win_sharpes = [], [], []
    oos_trades = 0
    for i in range(n_windows):
        seg = sr.iloc[bounds[i]:bounds[i + 1]]
        w = len(seg)
        if w == 0:
            continue
        cut = w - int(round(oos_frac * w))
        is_parts.append(seg.iloc[:cut])
        oos_seg = seg.iloc[cut:]
        oos_parts.append(oos_seg)
        win_sharpes.append(sharpe(oos_seg))
        oos_trades += trade_count(pos.iloc[bounds[i]:bounds[i + 1]].iloc[cut:])

    oos = pd.concat(oos_parts) if oos_parts else pd.Series(dtype=float)
    is_ = pd.concat(is_parts) if is_parts else pd.Series(dtype=float)
    return WalkForwardResult(
        oos_returns=oos, oos_sharpe=sharpe(oos), oos_max_drawdown=max_drawdown(oos),
        is_sharpe=sharpe(is_), is_max_drawdown=max_drawdown(is_),
        n_windows=n_windows, n_oos_bars=len(oos), n_oos_trades=oos_trades,
        window_oos_sharpes=win_sharpes,
    )


# --------------------------------------------------------------------------- #
# Sweep
# --------------------------------------------------------------------------- #

def run_sweep(data: Dict[str, pd.DataFrame],
              configs: Optional[list] = None,
              cost_fn: Callable[[str], float] = default_cost_bps,
              csv_path: str = SWEEP_CSV,
              n_windows: int = 5, oos_frac: float = 0.30,
              verbose: bool = True) -> pd.DataFrame:
    """Run every (config x asset) walk-forward and write results to CSV.

    Each record holds: config, family, category, asset, is_sharpe, oos_sharpe,
    oos_max_drawdown, trades (OOS), oos_bars.
    """
    from layer1_data_strategies import build_configs
    configs = configs if configs is not None else build_configs()

    records = []
    total = len(configs) * len(data)
    if verbose:
        print(f"Sweep: {len(configs)} configs x {len(data)} assets "
              f"= {total} walk-forward backtests ...")
    done = 0
    for name, fn, params, cat in configs:
        family = name.split("[")[0]
        for ticker, df in data.items():
            pos = fn(df, **params)
            wf = walk_forward(pos, df, cost_bps=cost_fn(ticker),
                              n_windows=n_windows, oos_frac=oos_frac)
            records.append({
                "config": name, "family": family, "category": cat, "asset": ticker,
                "is_sharpe": wf.is_sharpe, "oos_sharpe": wf.oos_sharpe,
                "oos_max_drawdown": wf.oos_max_drawdown, "trades": wf.n_oos_trades,
                "oos_bars": wf.n_oos_bars,
            })
            done += 1
        if verbose and done % 500 < len(data):
            print(f"  ... {done}/{total}", end="\r")

    res = pd.DataFrame.from_records(records)
    res.to_csv(csv_path, index=False)
    if verbose:
        print(f"\nTOTAL BACKTESTS: {len(res)}  ->  {csv_path}")
    return res


# --------------------------------------------------------------------------- #
# Six-filter survival funnel
# --------------------------------------------------------------------------- #

@dataclass
class FunnelThresholds:
    max_drawdown: float = 0.35      # OOS dd magnitude must be < this (> -35%)
    sharpe_min: float = 0.5         # OOS Sharpe above this
    sharpe_max: float = 2.5         # OOS Sharpe below this
    overfit_ratio: float = 0.30     # OOS Sharpe <= IS * (1 + this)
    min_trades: int = 30            # OOS trades at least this
    # filter 6 (IS Sharpe positive) needs no threshold


FILTER_LABELS = [
    "1. OOS maxDD better than -35%",
    "2. OOS Sharpe > 0.5",
    "3. OOS Sharpe < 2.5",
    "4. OOS Sharpe <= IS * 1.30",
    "5. >= 30 trades",
    "6. IS Sharpe > 0",
]


def apply_filters(res: pd.DataFrame,
                  th: FunnelThresholds = FunnelThresholds()) -> pd.DataFrame:
    """Add boolean columns f1..f6 and 'survived' to the sweep DataFrame."""
    out = res.copy()
    out["f1"] = out["oos_max_drawdown"] < th.max_drawdown
    out["f2"] = out["oos_sharpe"] > th.sharpe_min
    out["f3"] = out["oos_sharpe"] < th.sharpe_max
    out["f4"] = out["oos_sharpe"] <= out["is_sharpe"] * (1 + th.overfit_ratio)
    out["f5"] = out["trades"] >= th.min_trades
    out["f6"] = out["is_sharpe"] > 0
    out["survived"] = out[["f1", "f2", "f3", "f4", "f5", "f6"]].all(axis=1)
    return out


def funnel_report(res: pd.DataFrame,
                  th: FunnelThresholds = FunnelThresholds(),
                  top_n: int = 25) -> pd.DataFrame:
    """Print the funnel: attrition, headline counts, survival by category and
    family, and the top survivors. Returns the survivors DataFrame."""
    f = apply_filters(res, th)
    total = len(f)

    print("\n" + "=" * 72)
    print("SIX-FILTER SURVIVAL FUNNEL")
    print("=" * 72)
    print(f"Total backtests: {total}")
    print(f"  positive OOS Sharpe : {(f['oos_sharpe'] > 0).sum()}")
    print(f"  cleared 0.5         : {(f['oos_sharpe'] > th.sharpe_min).sum()}")
    print(f"  survived all six    : {f['survived'].sum()}")

    print("\nAttrition (cumulative pass, filters applied in order):")
    cols = ["f1", "f2", "f3", "f4", "f5", "f6"]
    mask = pd.Series(True, index=f.index)
    remaining = total
    for label, c in zip(FILTER_LABELS, cols):
        mask &= f[c]
        kept = int(mask.sum())
        print(f"  {label:34s} {kept:6d}  ({100*kept/total:5.1f}%)"
              f"   -{remaining - kept}")
        remaining = kept

    surv = f[f["survived"]].copy()

    def _grouptable(key: str, title: str):
        print(f"\nSurvival by {title}:")
        g = f.groupby(key)
        rows = []
        for name, grp in g:
            s = grp[grp["survived"]]
            rows.append((name, len(s), len(grp), 100 * len(s) / len(grp),
                         s["oos_sharpe"].mean() if len(s) else float("nan")))
        rows.sort(key=lambda x: x[3], reverse=True)
        print(f"  {'':14s}{'survived':>9}{'total':>7}{'rate%':>8}{'mean OOS Sh':>13}")
        for name, ns, nt, rate, msh in rows:
            msh_s = f"{msh:.2f}" if msh == msh else "  -"
            print(f"  {str(name):14s}{ns:>9}{nt:>7}{rate:>8.1f}{msh_s:>13}")

    _grouptable("category", "category")
    _grouptable("family", "family")

    print(f"\nTop {top_n} survivors by OOS Sharpe:")
    if surv.empty:
        print("  (none survived)")
    else:
        cols_show = ["config", "asset", "oos_sharpe", "is_sharpe",
                     "oos_max_drawdown", "trades"]
        top = surv.sort_values("oos_sharpe", ascending=False).head(top_n)
        print(f"  {'config':28s}{'asset':8s}{'OOS':>7}{'IS':>7}{'maxDD':>8}{'trd':>6}")
        for _, r in top.iterrows():
            print(f"  {r['config'][:27]:28s}{r['asset']:8s}"
                  f"{r['oos_sharpe']:>7.2f}{r['is_sharpe']:>7.2f}"
                  f"{r['oos_max_drawdown']:>8.2f}{int(r['trades']):>6}")
    return surv


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main():
    from layer1_data_strategies import load_universe, build_configs

    print("Layer 2 — loading universe (Layer 1) ...\n")
    data = load_universe()
    if not data:
        print("No data available; cannot run the sweep.")
        return

    res = run_sweep(data, build_configs())
    funnel_report(res, FunnelThresholds())
    print("\nDone. sweep_results.csv written. The funnel above is the centerpiece.")


if __name__ == "__main__":
    main()
