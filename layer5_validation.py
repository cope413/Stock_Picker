"""
Layer 5 — Validation: True Holdout, Deflated Sharpe, Cost Sensitivity
=====================================================================

Addresses the biggest flaw in Layers 1-4 as originally built: once the funnel
*selects* survivors by their walk-forward "OOS" Sharpe across thousands of
backtests, that number is no longer out-of-sample — with ~4,700 trials, chance
alone clears any fixed threshold many times over. Three corrections:

  * TRUE HOLDOUT: the sweep and funnel run ONLY on data before HOLDOUT_START
    (default 2022-01-01). Survivors are then scored exactly once on the holdout
    years they have never touched. Positions are computed on the full history
    (so indicator warm-up carries in) but only holdout-period returns are
    scored. A survivor whose edge was luck reverts to noise here.

  * DEFLATED SHARPE RATIO (Bailey & Lopez de Prado, 2014): for each survivor,
    the probability that its observed OOS Sharpe exceeds the expected MAXIMUM
    Sharpe of the N trials run on the same asset — accounting for the number
    and variance of trials, the length of the return series, and its skew and
    kurtosis. DSR >= 0.95 means the edge is unlikely to be a multiple-testing
    artifact. (Trials on one asset are correlated, so the expected-max
    benchmark is overstated and the DSR is conservative — clearing it is a
    strong sign.)

  * COST SENSITIVITY: re-scores the entire sweep at multiples of the base
    per-side cost (default 1x / 5x / 10x), reusing each config's positions so
    it costs ~1.3x one sweep rather than 3x, and reports how the funnel's
    survivor count decays. Anything that dies at 5x base cost was never a real
    edge once slippage is acknowledged.

Usage:

    python layer5_validation.py            # holdout + DSR pipeline
    python layer5_validation.py --costs    # also run cost sensitivity

    from layer5_validation import (
        split_dev, holdout_evaluate, deflate_survivors,
        expected_max_sharpe, probabilistic_sharpe, cost_sensitivity,
    )

Dependencies: numpy, pandas (+ Layers 1-2).
"""

from __future__ import annotations

import os
import sys
from statistics import NormalDist
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

import layer1_data_strategies as L
import layer2_funnel as F

_HERE = os.path.dirname(os.path.abspath(__file__))
DEV_SWEEP_CSV = os.path.join(_HERE, "sweep_results_dev.csv")
HOLDOUT_CSV = os.path.join(_HERE, "layer5_holdout.csv")
COST_SENSITIVITY_CSV = os.path.join(_HERE, "cost_sensitivity.csv")

HOLDOUT_START = "2022-01-01"   # sweep/funnel never see data on/after this date

EULER_GAMMA = 0.5772156649015329
_NORM = NormalDist()


# --------------------------------------------------------------------------- #
# Dev / holdout split
# --------------------------------------------------------------------------- #

def split_dev(data: Dict[str, pd.DataFrame],
              holdout_start: str = HOLDOUT_START,
              min_bars: int = L.MIN_BARS) -> Dict[str, pd.DataFrame]:
    """Truncate every asset to bars strictly BEFORE ``holdout_start``.

    Assets left with fewer than ``min_bars`` dev bars are dropped (they can't
    be swept meaningfully). The returned dict is what Layers 2-3 should see.
    """
    cutoff = pd.Timestamp(holdout_start)
    dev: Dict[str, pd.DataFrame] = {}
    for t, df in data.items():
        d = df.loc[df.index < cutoff]
        if len(d) >= min_bars:
            dev[t] = d
    return dev


# --------------------------------------------------------------------------- #
# Deflated Sharpe Ratio
# --------------------------------------------------------------------------- #

def expected_max_sharpe(n_trials: int, var_trials: float) -> float:
    """Expected maximum (per-period) Sharpe among ``n_trials`` zero-skill
    trials whose Sharpe estimates have variance ``var_trials``.

    E[max] ~= sqrt(V) * ((1-g)*Z(1 - 1/N) + g*Z(1 - 1/(N*e))), g = Euler-
    Mascheroni. This is the benchmark the observed Sharpe must beat.
    """
    if n_trials <= 1 or var_trials <= 0:
        return 0.0
    return float(np.sqrt(var_trials) * (
        (1 - EULER_GAMMA) * _NORM.inv_cdf(1 - 1 / n_trials)
        + EULER_GAMMA * _NORM.inv_cdf(1 - 1 / (n_trials * np.e))))


def probabilistic_sharpe(sr: float, sr_bench: float, n_obs: int,
                         skew: float = 0.0, kurt: float = 3.0) -> float:
    """PSR: probability the true (per-period) Sharpe exceeds ``sr_bench``,
    given ``n_obs`` observations with the stated skew and (non-excess)
    kurtosis. All Sharpes here are PER-PERIOD (mean/std), not annualized.
    """
    if n_obs <= 1:
        return 0.5
    var_term = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    denom = np.sqrt(max(var_term, 1e-12))
    z = (sr - sr_bench) * np.sqrt(n_obs - 1) / denom
    return float(_NORM.cdf(z))


def deflate_survivors(survivors: pd.DataFrame, dev_sweep: pd.DataFrame,
                      dev_data: Dict[str, pd.DataFrame],
                      configs: Optional[list] = None,
                      cost_fn=F.default_cost_bps) -> pd.DataFrame:
    """Add a ``dsr`` column to the survivors DataFrame.

    For each survivor, the trial set is every config swept on the SAME asset
    (that is what was searched over). The observed per-period OOS Sharpe is
    tested against the expected max of that many trials via the PSR, using the
    survivor's actual OOS return count, skew, and kurtosis.
    """
    configs = configs if configs is not None else L.build_configs()
    lookup = {name: (fn, params) for name, fn, params, _ in configs}

    out = survivors.copy()
    dsrs = []
    ppy_cache: Dict[str, float] = {}
    trials_cache: Dict[str, tuple] = {}
    for _, row in out.iterrows():
        name, asset = row["config"], row["asset"]
        if name not in lookup or asset not in dev_data:
            dsrs.append(np.nan)
            continue
        df = dev_data[asset]
        if asset not in ppy_cache:
            ppy_cache[asset] = F.periods_per_year(df.index)
            ann = dev_sweep.loc[dev_sweep["asset"] == asset, "oos_sharpe"]
            per_period = ann.values / np.sqrt(ppy_cache[asset])
            trials_cache[asset] = (len(per_period), float(np.var(per_period)))
        ppy = ppy_cache[asset]
        n_trials, var_trials = trials_cache[asset]
        sr0 = expected_max_sharpe(n_trials, var_trials)

        fn, params = lookup[name]
        pos = fn(df, **params)
        wf = F.walk_forward(pos, df, cost_bps=cost_fn(asset))
        oos = wf.oos_returns
        if len(oos) < 20:
            dsrs.append(np.nan)
            continue
        sr_p = wf.oos_sharpe / np.sqrt(ppy)          # back to per-period
        skew = float(oos.skew())
        kurt = float(oos.kurt()) + 3.0               # pandas kurt is excess
        dsrs.append(probabilistic_sharpe(sr_p, sr0, len(oos), skew, kurt))
    out["dsr"] = dsrs
    return out


# --------------------------------------------------------------------------- #
# Holdout evaluation
# --------------------------------------------------------------------------- #

def holdout_evaluate(survivors: pd.DataFrame, data: Dict[str, pd.DataFrame],
                     configs: Optional[list] = None,
                     holdout_start: str = HOLDOUT_START,
                     cost_fn=F.default_cost_bps) -> pd.DataFrame:
    """Score each surviving (config, asset) ONCE on the untouched holdout.

    ``data`` must be the FULL (untruncated) universe: positions are computed on
    the whole history so indicators are warm at the holdout boundary, then only
    returns on/after ``holdout_start`` are scored.
    """
    configs = configs if configs is not None else L.build_configs()
    lookup = {name: (fn, params) for name, fn, params, _ in configs}
    cutoff = pd.Timestamp(holdout_start)

    rows = []
    for _, row in survivors.iterrows():
        name, asset = row["config"], row["asset"]
        if name not in lookup or asset not in data:
            continue
        fn, params = lookup[name]
        df = data[asset]
        pos = fn(df, **params)
        sr = F.strategy_returns(pos, F.asset_returns(df),
                                cost_bps=cost_fn(asset))
        ho = sr.loc[sr.index >= cutoff]
        if len(ho) < 2:
            continue
        ppy = F.periods_per_year(df.index)
        hs = F.sharpe(ho, ppy)
        rows.append({
            "config": name, "asset": asset,
            "family": row.get("family", ""), "category": row.get("category", ""),
            "dev_oos_sharpe": row.get("oos_sharpe", np.nan),
            "dsr": row.get("dsr", np.nan),
            "holdout_sharpe": hs,
            "holdout_sharpe_se": F.sharpe_se(ho, ppy),
            "holdout_max_drawdown": F.max_drawdown(ho),
            "holdout_trades": F.trade_count(pos.loc[pos.index >= cutoff]),
            "holdout_bars": len(ho),
            "confirmed": bool(hs > 0),
        })
    return pd.DataFrame(rows)


def print_holdout(ho: pd.DataFrame, dsr_threshold: float = 0.95,
                  top_n: int = 30,
                  holdout_start: str = HOLDOUT_START) -> None:
    print("\n" + "=" * 78)
    print(f"LAYER 5 — TRUE HOLDOUT ({holdout_start} onward; "
          f"never seen by the sweep)")
    print("=" * 78)
    if ho.empty:
        print("  (no survivors reached the holdout)")
        return
    n = len(ho)
    n_pos = int(ho["confirmed"].sum())
    has_dsr = ho["dsr"].notna()
    n_dsr = int(((ho["dsr"] >= dsr_threshold) & has_dsr).sum())
    n_both = int((ho["confirmed"] & (ho["dsr"] >= dsr_threshold) & has_dsr).sum())
    print(f"  {n} funnel survivors evaluated on the holdout")
    print(f"    positive holdout Sharpe          : {n_pos}  ({100*n_pos/n:.0f}%)")
    print(f"    DSR >= {dsr_threshold:.2f} (multiple-testing)   : {n_dsr}")
    print(f"    BOTH (the real shortlist)        : {n_both}")
    print(f"\n  Top {top_n} by holdout Sharpe (SE shown — most are within noise of 0):")
    print(f"  {'config':28s}{'asset':8s}{'devOOS':>8}{'DSR':>6}"
          f"{'HO Sh':>7}{'±SE':>6}{'HO DD':>7}{'trd':>5}")
    top = ho.sort_values("holdout_sharpe", ascending=False).head(top_n)
    for _, r in top.iterrows():
        dsr_s = f"{r['dsr']:.2f}" if pd.notna(r["dsr"]) else "  -"
        print(f"  {r['config'][:27]:28s}{r['asset']:8s}"
              f"{r['dev_oos_sharpe']:>8.2f}{dsr_s:>6}"
              f"{r['holdout_sharpe']:>7.2f}{r['holdout_sharpe_se']:>6.2f}"
              f"{r['holdout_max_drawdown']:>7.2f}{int(r['holdout_trades']):>5}")
    print("\n  Read: dev-OOS Sharpe got these through the funnel, but it was used")
    print("  for selection — the holdout column is the first honest number.")


# --------------------------------------------------------------------------- #
# Cost sensitivity
# --------------------------------------------------------------------------- #

def cost_sensitivity(dev_data: Dict[str, pd.DataFrame],
                     configs: Optional[list] = None,
                     multipliers: Iterable[float] = (1.0, 5.0, 10.0),
                     th: F.FunnelThresholds = F.FunnelThresholds(),
                     base_cost_fn=F.default_cost_bps,
                     csv_path: str = COST_SENSITIVITY_CSV,
                     write: bool = True, verbose: bool = True) -> pd.DataFrame:
    """Re-score the whole sweep at each cost multiplier, reusing positions.

    Positions don't depend on costs, so each (config, asset) computes its
    position once and re-prices the return stream per multiplier. Returns the
    long-form results (one row per config x asset x multiplier) and prints how
    the funnel's survivor count decays with cost.
    """
    configs = configs if configs is not None else L.build_configs()
    multipliers = list(multipliers)
    records = []
    total = len(configs) * len(dev_data)
    if verbose:
        print(f"\nCost sensitivity: {len(configs)} configs x {len(dev_data)} "
              f"assets x {len(multipliers)} cost levels ...")
    done = 0
    for name, fn, params, cat in configs:
        family = name.split("[")[0]
        for ticker, df in dev_data.items():
            pos = fn(df, **params)
            ret = F.asset_returns(df)
            ppy = F.periods_per_year(df.index)
            base = base_cost_fn(ticker)
            for m in multipliers:
                sr = F.strategy_returns(pos, ret, cost_bps=base * m)
                wf = F.walk_forward_returns(sr, position=pos, periods=ppy)
                records.append({
                    "config": name, "family": family, "category": cat,
                    "asset": ticker, "cost_mult": m,
                    "is_sharpe": wf.is_sharpe, "oos_sharpe": wf.oos_sharpe,
                    "oos_max_drawdown": wf.oos_max_drawdown,
                    "trades": wf.n_oos_trades, "oos_bars": wf.n_oos_bars,
                })
            done += 1
        if verbose and done % 500 < len(dev_data):
            print(f"  ... {done}/{total}", end="\r")

    res = pd.DataFrame.from_records(records)
    if write:
        res.to_csv(csv_path, index=False)

    if verbose:
        print("\n" + "=" * 60)
        print("SURVIVOR DECAY WITH COSTS (six-filter funnel at each level)")
        print("=" * 60)
        base_set = None
        print(f"  {'cost':>6}{'survivors':>11}{'kept from 1x':>14}{'mean OOS Sh':>13}")
        for m in multipliers:
            sub = F.apply_filters(res[res["cost_mult"] == m], th)
            surv = sub[sub["survived"]]
            keys = set(zip(surv["config"], surv["asset"]))
            if base_set is None:
                base_set = keys
                kept = "-"
            else:
                kept = f"{len(keys & base_set)}"
            msh = surv["oos_sharpe"].mean() if len(surv) else float("nan")
            msh_s = f"{msh:.2f}" if msh == msh else "-"
            print(f"  {m:>5.0f}x{len(surv):>11}{kept:>14}{msh_s:>13}")
        print("\n  Anything that dies at 5x base cost was never a real edge")
        print("  once slippage and realistic frictions are acknowledged.")
        if write:
            print(f"\n  wrote {csv_path}")
    return res


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(run_costs: bool = False):
    print("Layer 5 — loading full universe (Layer 1) ...\n")
    data = L.load_universe()
    if not data:
        print("No data available.")
        return

    dev = split_dev(data)
    dropped = sorted(set(data) - set(dev))
    print(f"\nDev period: history before {HOLDOUT_START} "
          f"({len(dev)} assets"
          + (f"; dropped for short dev history: {', '.join(dropped)}" if dropped else "")
          + ")")
    print(f"Holdout   : {HOLDOUT_START} onward — touched exactly once, below.\n")

    configs = L.build_configs()
    res = F.run_sweep(dev, configs, csv_path=DEV_SWEEP_CSV)
    survivors = F.funnel_report(res)

    if survivors.empty:
        print("\nNo funnel survivors on the dev period — nothing to validate.")
    else:
        survivors = deflate_survivors(survivors, res, dev, configs)
        ho = holdout_evaluate(survivors, data, configs)
        ho.to_csv(HOLDOUT_CSV, index=False)
        print_holdout(ho)
        print(f"\n  wrote {HOLDOUT_CSV}")

    if run_costs:
        cost_sensitivity(dev, configs)

    print("\nDone. The holdout table above — not the funnel — is the number "
          "to believe.")


if __name__ == "__main__":
    main(run_costs="--costs" in sys.argv[1:])
