"""
Layer 4 — Cross-Sectional Momentum (standalone check)
=====================================================

In the main sweep, momentum was tested on each asset by itself and scored near
zero. But the strongest documented form of momentum is cross-sectional: ranking
assets against each other. This layer builds that and compares it, apples to
apples, against single-asset momentum.

  * CROSS-SECTIONAL MOMENTUM: same universe and minimum-history filter as the main
    tester. Every 21 trading days (~monthly), rank all assets by trailing return.
    Lookbacks tested: 3 months (63d), 6 months (126d), and the standard
    12-months-minus-the-most-recent-month (skip the last 21d to avoid short-term
    reversal). Go long the top third of ranked assets, short the bottom third,
    equal weight, hold until the next rebalance. Realistic per-asset transaction
    costs are charged on the turnover.

  * VALIDATION: scored exactly like the main tester — an IS/OOS split with a
    walk-forward out-of-sample Sharpe and drawdown (layer2.walk_forward_returns),
    so the number is directly comparable to single-asset momentum from the sweep.

  * REPORT: OOS Sharpe at each lookback, side by side with the single-asset
    momentum result at the same lookback, and a plain statement of whether ranking
    assets against each other beat trading momentum on each one alone. Drawdowns
    are reported as they come (they tend to be deep), and the per-window OOS
    Sharpes are shown so you can see if the result leaned on one market regime.
    Results are written to CSV. Nothing is tuned to look good — it reports what
    happens.

    from layer4_xsec_momentum import cross_sectional_returns, run_layer4

Dependencies: numpy, pandas (+ Layers 1-2).
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

import layer2_funnel as F

_HERE = os.path.dirname(os.path.abspath(__file__))
XSEC_CSV = os.path.join(_HERE, "layer4_xsec_momentum.csv")
XSEC_RETURNS_CSV = os.path.join(_HERE, "layer4_xsec_returns.csv")

REBALANCE = 21  # trading days (~monthly)

# lookbacks: label -> (lookback_days, skip_days)
LOOKBACKS = {
    "3m": (63, 0),
    "6m": (126, 0),
    "12-1m": (252, 21),
}


# --------------------------------------------------------------------------- #
# Panel construction
# --------------------------------------------------------------------------- #

def close_panel(data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Aligned daily close panel (weekday calendar) for the whole universe."""
    panel = pd.DataFrame({t: df["Close"] for t, df in data.items()}).sort_index()
    panel = panel[panel.index.dayofweek < 5]   # drop crypto weekend bars
    return panel


# --------------------------------------------------------------------------- #
# Cross-sectional momentum portfolio
# --------------------------------------------------------------------------- #

def xsec_weights(panel: pd.DataFrame, lookback: int, skip: int = 0,
                 rebalance: int = REBALANCE) -> pd.DataFrame:
    """Daily target weights: long top third / short bottom third, equal weight,
    dollar-neutral, held between monthly rebalances. No look-ahead is applied here
    (weights are *target* on the rebalance date); the caller shifts by one bar."""
    if skip:
        trailing = panel.shift(skip) / panel.shift(lookback) - 1.0
    else:
        trailing = panel / panel.shift(lookback) - 1.0

    weights = pd.DataFrame(np.nan, index=panel.index, columns=panel.columns)
    for ri in range(lookback + skip, len(panel), rebalance):
        row = trailing.iloc[ri].dropna()
        third = len(row) // 3
        if third < 1:
            continue
        ranked = row.sort_values()
        w = pd.Series(0.0, index=panel.columns)
        w[ranked.index[-third:]] = 1.0 / third     # long top third
        w[ranked.index[:third]] = -1.0 / third     # short bottom third
        weights.iloc[ri] = w.values                # full vector incl. zeros
    return weights.ffill().fillna(0.0)


def cross_sectional_returns(data: Dict[str, pd.DataFrame], lookback: int,
                            skip: int = 0, rebalance: int = REBALANCE,
                            cost_fn=F.default_cost_bps) -> pd.Series:
    """Net daily return of the long/short cross-sectional momentum portfolio.

    No look-ahead: weights decided at a rebalance (using data through that day)
    are made effective the next day (shift by one bar). Per-asset transaction
    costs are charged on the change in effective weights.
    """
    panel = close_panel(data)
    daily_ret = panel.pct_change()
    weights = xsec_weights(panel, lookback, skip, rebalance)

    w_eff = weights.shift(1).fillna(0.0)                  # effective next day
    gross = (w_eff * daily_ret).reindex(panel.index).sum(axis=1)

    cost_bps = pd.Series({c: cost_fn(c) for c in panel.columns})
    turnover = w_eff.diff().abs()
    cost = (turnover * cost_bps).sum(axis=1) * 1e-4
    return (gross - cost).fillna(0.0)


# --------------------------------------------------------------------------- #
# Single-asset momentum benchmark (same lookback, apples to apples)
# --------------------------------------------------------------------------- #

def single_asset_mom_position(df: pd.DataFrame, lookback: int,
                              skip: int = 0) -> pd.Series:
    """Time-series momentum position in {-1, 0, 1}, lagged one bar (Layer-1
    convention) so it has no look-ahead."""
    c = df["Close"]
    raw = (c.shift(skip) / c.shift(lookback) - 1.0) if skip else \
          (c / c.shift(lookback) - 1.0)
    return np.sign(raw).shift(1).fillna(0.0)


def single_asset_mom_oos(data: Dict[str, pd.DataFrame], lookback: int,
                         skip: int = 0, cost_fn=F.default_cost_bps) -> Dict[str, float]:
    """Mean/median OOS Sharpe of single-asset momentum across the universe."""
    sharpes = []
    for ticker, df in data.items():
        pos = single_asset_mom_position(df, lookback, skip)
        wf = F.walk_forward(pos, df, cost_bps=cost_fn(ticker))
        sharpes.append(wf.oos_sharpe)
    arr = np.array(sharpes)
    return {"mean": float(arr.mean()), "median": float(np.median(arr)),
            "best": float(arr.max()), "n": len(arr)}


# --------------------------------------------------------------------------- #
# Orchestration + report
# --------------------------------------------------------------------------- #

def _regime_note(window_sharpes) -> str:
    """Flag whether a positive OOS result leaned on a single window/regime."""
    w = np.array(window_sharpes, dtype=float)
    if len(w) == 0:
        return "no windows"
    frac_pos = float((w > 0).mean())
    overall = float(np.mean(w))
    if overall <= 0:
        return f"{frac_pos:.0%} windows positive"
    # remove the single best window; does the edge survive?
    without_best = np.mean(np.sort(w)[:-1]) if len(w) > 1 else 0.0
    leaned = without_best <= 0 < overall
    tag = "LEANS ON ONE REGIME" if leaned else "broad across windows"
    return f"{frac_pos:.0%} windows positive; {tag}"


def run_layer4(data: Dict[str, pd.DataFrame], cost_fn=F.default_cost_bps,
               write: bool = True, verbose: bool = True
               ) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Run cross-sectional momentum at every lookback, validate, and report.
    Returns (summary_df, returns_df)."""
    rows = []
    ret_frames = {}
    for label, (lb, skip) in LOOKBACKS.items():
        xs = cross_sectional_returns(data, lb, skip, cost_fn=cost_fn)
        wf = F.walk_forward_returns(xs)
        sa = single_asset_mom_oos(data, lb, skip, cost_fn)
        rows.append({
            "lookback": label, "lookback_days": lb, "skip_days": skip,
            "xsec_oos_sharpe": wf.oos_sharpe,
            "xsec_oos_drawdown": wf.oos_max_drawdown,
            "xsec_is_sharpe": wf.is_sharpe,
            "xsec_fullsample_sharpe": F.sharpe(xs),
            "xsec_fullsample_drawdown": F.max_drawdown(xs),
            "single_asset_mean_oos_sharpe": sa["mean"],
            "single_asset_median_oos_sharpe": sa["median"],
            "single_asset_best_oos_sharpe": sa["best"],
            "xsec_beats_single": wf.oos_sharpe > sa["mean"],
            "window_oos_sharpes": ";".join(f"{s:.2f}" for s in wf.window_oos_sharpes),
            "regime_note": _regime_note(wf.window_oos_sharpes),
        })
        ret_frames[label] = xs
    summary = pd.DataFrame(rows)

    returns_df = pd.DataFrame(ret_frames)
    if write:
        summary.to_csv(XSEC_CSV, index=False)
        returns_df.to_csv(XSEC_RETURNS_CSV)
    if verbose:
        _print_report(summary)
        if write:
            print(f"\n  wrote {XSEC_CSV}")
            print(f"  wrote {XSEC_RETURNS_CSV}  (daily net returns, for charting)")
    return summary, returns_df


def _print_report(summary: pd.DataFrame) -> None:
    print("\n" + "=" * 74)
    print("LAYER 4 — CROSS-SECTIONAL vs SINGLE-ASSET MOMENTUM (out-of-sample)")
    print("=" * 74)
    print(f"  {'lookback':9s}{'XS OOS Sh':>11}{'XS OOS DD':>11}"
          f"{'1-asset mean':>14}{'winner':>10}")
    xs_wins = 0
    for _, r in summary.iterrows():
        winner = "cross-sec" if r["xsec_beats_single"] else "single"
        xs_wins += int(r["xsec_beats_single"])
        print(f"  {r['lookback']:9s}{r['xsec_oos_sharpe']:>11.3f}"
              f"{r['xsec_oos_drawdown']:>11.3f}"
              f"{r['single_asset_mean_oos_sharpe']:>14.3f}{winner:>10}")

    print("\n  Per-window OOS Sharpe (regime check):")
    for _, r in summary.iterrows():
        print(f"    {r['lookback']:7s} [{r['window_oos_sharpes']}]  {r['regime_note']}")

    print("\n  PLAIN STATEMENT:")
    n = len(summary)
    if xs_wins == n:
        verdict = ("Ranking assets against each other BEAT single-asset momentum "
                   "at every lookback tested.")
    elif xs_wins == 0:
        verdict = ("Ranking assets against each other did NOT beat single-asset "
                   "momentum at any lookback tested.")
    else:
        beat = ", ".join(summary.loc[summary["xsec_beats_single"], "lookback"])
        verdict = (f"Cross-sectional momentum beat single-asset momentum at "
                   f"{xs_wins}/{n} lookbacks ({beat}); single-asset won the rest.")
    print(f"    {verdict}")
    print("    Drawdowns are reported above as they came out — cross-sectional "
          "long/short\n    drawdowns tend to be deep. Check the regime notes: a "
          "result that leans on one\n    window is not a durable edge.")


def main():
    from layer1_data_strategies import load_universe

    print("Layer 4 — loading universe (Layer 1) ...\n")
    data = load_universe()
    if not data:
        print("No data available.")
        return
    print(f"\nUniverse: {len(data)} assets. Rebalance every {REBALANCE} trading days.")
    run_layer4(data)
    print("\nDone. Cross-sectional momentum reported — not tuned, just what happens.")


if __name__ == "__main__":
    main()
