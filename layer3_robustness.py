"""
Layer 3 — Robustness Checks
===========================

Builds on the funnel (Layers 1-2). Two checks that catch survivors which only
worked by luck or by one magic parameter setting.

  * PARAMETER SENSITIVITY: for each strategy family, group all its parameter
    configs and report the mean OOS Sharpe, the std of OOS Sharpe across configs,
    and the fraction of configs with a positive OOS Sharpe. A family that only
    works on one exact setting and falls apart on the others is a red flag (curve
    fit). A tight spread with a high positive fraction means the edge is real and
    not dependent on one magic number.

    (Each config's OOS Sharpe is its mean across the asset universe, so the spread
    measured is the spread *across parameter settings*.)

  * BOOTSTRAP STRESS TEST: for each top survivor, take its stitched OOS daily
    returns and resample them a few hundred times (default 200). Each resample
    gives a different equity path, so you get a distribution of outcomes instead
    of the one that happened. Report the 5th / 50th / 95th percentile Sharpe and
    the worst-case drawdown across resamples, and flag each survivor solid or
    fragile by its worst-case drawdown. Results are written to CSV for charting.

    Note on method: a *pure* order-permutation leaves Sharpe unchanged (mean and
    std are order-invariant), so it can only vary the drawdown path. To also get a
    meaningful Sharpe distribution we resample WITH replacement by default
    (``replace=True``); pass ``replace=False`` for pure path-shuffling.

    from layer3_robustness import (
        parameter_sensitivity, bootstrap_stress, run_bootstrap,
    )

Dependencies: numpy, pandas (+ Layers 1-2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

import layer2_funnel as F

_HERE = os.path.dirname(os.path.abspath(__file__))
SENSITIVITY_CSV = os.path.join(_HERE, "parameter_sensitivity.csv")
BOOTSTRAP_CSV = os.path.join(_HERE, "bootstrap_results.csv")
BOOTSTRAP_SAMPLES_CSV = os.path.join(_HERE, "bootstrap_samples.csv")


# --------------------------------------------------------------------------- #
# Parameter sensitivity
# --------------------------------------------------------------------------- #

def parameter_sensitivity(res: pd.DataFrame) -> pd.DataFrame:
    """Per-family robustness across parameter settings.

    res : sweep DataFrame from layer2.run_sweep (columns include family, config,
          oos_sharpe).

    Returns a DataFrame indexed by family with: n_configs, mean_oos_sharpe,
    std_oos_sharpe, frac_positive (fraction of configs with positive OOS Sharpe).
    """
    # One OOS Sharpe per config = mean across the asset universe.
    per_config = res.groupby(["family", "config"])["oos_sharpe"].mean()
    per_config = per_config.reset_index()

    rows = []
    for family, grp in per_config.groupby("family"):
        s = grp["oos_sharpe"]
        rows.append({
            "family": family,
            "n_configs": len(s),
            "mean_oos_sharpe": s.mean(),
            "std_oos_sharpe": s.std(ddof=0),       # 0 for single-config families
            "frac_positive": (s > 0).mean(),
        })
    out = pd.DataFrame(rows).set_index("family")
    return out.sort_values("mean_oos_sharpe", ascending=False)


def print_parameter_sensitivity(table: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("PARAMETER SENSITIVITY  (per family, across parameter configs)")
    print("=" * 72)
    print(f"  {'family':22s}{'configs':>8}{'mean Sh':>9}{'std Sh':>8}{'frac+':>8}")
    for family, r in table.iterrows():
        print(f"  {str(family):22s}{int(r['n_configs']):>8}"
              f"{r['mean_oos_sharpe']:>9.3f}{r['std_oos_sharpe']:>8.3f}"
              f"{r['frac_positive']:>8.2f}")
    print("\n  Read: high mean + low std + frac+ near 1.0 => edge is parameter-robust.")
    print("        high mean but high std / low frac+ => likely curve-fit on one setting.")


# --------------------------------------------------------------------------- #
# Bootstrap stress test
# --------------------------------------------------------------------------- #

@dataclass
class BootResult:
    n: int
    sharpe_p5: float
    sharpe_p50: float
    sharpe_p95: float
    worst_drawdown: float
    median_drawdown: float
    sharpes: np.ndarray
    drawdowns: np.ndarray


def bootstrap_stress(returns: pd.Series, n_reshuffles: int = 200,
                     replace: bool = True, seed: int = 0) -> Optional[BootResult]:
    """Resample a return series ``n_reshuffles`` times and summarise the spread."""
    r = pd.Series(returns).dropna().values
    if len(r) < 2:
        return None
    rng = np.random.default_rng(seed)
    sharpes = np.empty(n_reshuffles)
    dds = np.empty(n_reshuffles)
    for i in range(n_reshuffles):
        sample = rng.choice(r, size=len(r), replace=True) if replace \
            else rng.permutation(r)
        sr = pd.Series(sample)
        sharpes[i] = F.sharpe(sr)
        dds[i] = F.max_drawdown(sr)
    return BootResult(
        n=n_reshuffles,
        sharpe_p5=float(np.percentile(sharpes, 5)),
        sharpe_p50=float(np.percentile(sharpes, 50)),
        sharpe_p95=float(np.percentile(sharpes, 95)),
        worst_drawdown=float(dds.max()),
        median_drawdown=float(np.median(dds)),
        sharpes=sharpes, drawdowns=dds,
    )


def classify(worst_drawdown: float, dd_threshold: float = 0.50) -> str:
    """A survivor is solid if its worst-case drawdown is still survivable."""
    return "solid" if worst_drawdown <= dd_threshold else "fragile"


def run_bootstrap(survivors: pd.DataFrame, data: Dict[str, pd.DataFrame],
                  configs: Optional[list] = None, n_reshuffles: int = 200,
                  replace: bool = True, dd_threshold: float = 0.50,
                  top_n: Optional[int] = None, cost_fn=F.default_cost_bps,
                  seed: int = 0, write: bool = True,
                  summary_csv: str = BOOTSTRAP_CSV,
                  samples_csv: str = BOOTSTRAP_SAMPLES_CSV,
                  verbose: bool = True):
    """Stress-test every survivor and write summary + per-resample samples to CSV.

    survivors : DataFrame of surviving (config, asset) rows (from funnel_report /
                apply_filters with survived == True).
    Returns (summary_df, samples_df).
    """
    from layer1_data_strategies import build_configs
    configs = configs if configs is not None else build_configs()
    lookup = {name: (fn, params) for name, fn, params, _ in configs}

    surv = survivors.sort_values("oos_sharpe", ascending=False)
    if top_n is not None:
        surv = surv.head(top_n)

    summ_rows, sample_rows = [], []
    if verbose:
        print(f"\nBootstrap stress test: {len(surv)} survivors x "
              f"{n_reshuffles} resamples ...")

    for k, (_, row) in enumerate(surv.iterrows()):
        name, asset = row["config"], row["asset"]
        if name not in lookup or asset not in data:
            continue
        fn, params = lookup[name]
        df = data[asset]
        pos = fn(df, **params)
        wf = F.walk_forward(pos, df, cost_bps=cost_fn(asset))
        boot = bootstrap_stress(wf.oos_returns, n_reshuffles, replace,
                                seed=seed + k)
        if boot is None:
            continue
        flag = classify(boot.worst_drawdown, dd_threshold)
        summ_rows.append({
            "config": name, "asset": asset, "family": row.get("family", ""),
            "category": row.get("category", ""),
            "oos_sharpe_actual": row["oos_sharpe"],
            "sharpe_p5": boot.sharpe_p5, "sharpe_p50": boot.sharpe_p50,
            "sharpe_p95": boot.sharpe_p95,
            "worst_drawdown": boot.worst_drawdown,
            "median_drawdown": boot.median_drawdown,
            "flag": flag,
        })
        for i in range(boot.n):
            sample_rows.append({"config": name, "asset": asset,
                                "reshuffle": i, "sharpe": boot.sharpes[i],
                                "drawdown": boot.drawdowns[i]})

    summary = pd.DataFrame(summ_rows)
    samples = pd.DataFrame(sample_rows)
    if write:
        summary.to_csv(summary_csv, index=False)
        samples.to_csv(samples_csv, index=False)
        if verbose:
            print(f"  wrote {summary_csv}")
            print(f"  wrote {samples_csv}  ({len(samples)} rows, for charting)")
    return summary, samples


def print_bootstrap(summary: pd.DataFrame, top_n: int = 25) -> None:
    print("\n" + "=" * 72)
    print("BOOTSTRAP STRESS TEST  (resampled OOS returns)")
    print("=" * 72)
    if summary.empty:
        print("  (no survivors to stress-test)")
        return
    n_solid = (summary["flag"] == "solid").sum()
    print(f"  {len(summary)} survivors stressed: "
          f"{n_solid} solid, {len(summary) - n_solid} fragile\n")
    print(f"  {'config':26s}{'asset':8s}{'Sh5':>6}{'Sh50':>6}{'Sh95':>6}"
          f"{'worstDD':>9}  flag")
    for _, r in summary.sort_values("sharpe_p50", ascending=False).head(top_n).iterrows():
        print(f"  {r['config'][:25]:26s}{r['asset']:8s}"
              f"{r['sharpe_p5']:>6.2f}{r['sharpe_p50']:>6.2f}{r['sharpe_p95']:>6.2f}"
              f"{r['worst_drawdown']:>9.2f}  {r['flag']}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main():
    from layer1_data_strategies import load_universe, build_configs

    print("Layer 3 — loading universe and sweep ...\n")
    data = load_universe()
    if not data:
        print("No data available.")
        return
    configs = build_configs()

    if os.path.exists(F.SWEEP_CSV):
        res = pd.read_csv(F.SWEEP_CSV)
        print(f"Loaded existing sweep ({len(res)} rows) from {F.SWEEP_CSV}")
    else:
        res = F.run_sweep(data, configs)

    # Check 1: parameter sensitivity (uses the whole sweep)
    sens = parameter_sensitivity(res)
    sens.to_csv(SENSITIVITY_CSV)
    print_parameter_sensitivity(sens)
    print(f"\n  wrote {SENSITIVITY_CSV}")

    # Check 2: bootstrap stress test on the funnel survivors
    survivors = F.apply_filters(res, F.FunnelThresholds())
    survivors = survivors[survivors["survived"]]
    summary, _ = run_bootstrap(survivors, data, configs,
                               n_reshuffles=200, top_n=100)
    print_bootstrap(summary)
    print("\nDone. parameter_sensitivity.csv, bootstrap_results.csv, "
          "bootstrap_samples.csv written for charting in Layer 4.")


if __name__ == "__main__":
    main()
