"""Swing-trading pipeline -- Layer 1-6 methodology, swing-holding-period filter.

Reuses the exact tested Layer 1-6 machinery (walk-forward funnel, bootstrap
stress test, deflated Sharpe + true holdout, correlation-deduped ERC
portfolio construction) from layer1_data_strategies.py / layer2_funnel.py /
layer3_robustness.py / layer5_validation.py / layer6_portfolio.py --
no logic is duplicated or forked. The only things this module adds are:

  1. A curated universe (swing.universe.SWING_UNIVERSE) instead of the core
     pipeline's ~120-ticker default, and a separate on-disk cache
     (swing/data_cache/) so this track never shares or races the core
     pipeline's data_cache/.

  2. A 7th filter, applied after the standard six-filter funnel: average
     holding period must land in a swing-trading band (default 2-21 trading
     days, i.e. "days to about 4 weeks"). Approximated as
     oos_bars / trades (trades = OOS position changes) -- a coarse proxy
     for typical time-in-position, not an exact trade-by-trade duration.

  3. All output paths point at swing/outputs/ -- never at the core
     pipeline's sweep_results.csv, layer5_holdout.csv, layer6_portfolio.json,
     etc. This track cannot overwrite or be overwritten by the core one.

Run from the repo root:

    python -m swing.pipeline

Add --signals to print today's positions from an already-built spec instead
of rebuilding.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd

import layer1_data_strategies as L
import layer2_funnel as F
import layer3_robustness as R
import layer5_validation as V
import layer6_portfolio as P

from swing.universe import SWING_UNIVERSE, CACHE_DIR, OUTPUT_DIR

os.makedirs(OUTPUT_DIR, exist_ok=True)

DEV_SWEEP_CSV = os.path.join(OUTPUT_DIR, "sweep_results_dev.csv")
SENSITIVITY_CSV = os.path.join(OUTPUT_DIR, "parameter_sensitivity.csv")
BOOTSTRAP_CSV = os.path.join(OUTPUT_DIR, "bootstrap_results.csv")
BOOTSTRAP_SAMPLES_CSV = os.path.join(OUTPUT_DIR, "bootstrap_samples.csv")
HOLDOUT_CSV = os.path.join(OUTPUT_DIR, "holdout.csv")
CLUSTERS_CSV = os.path.join(OUTPUT_DIR, "clusters.csv")
PORTFOLIO_JSON = os.path.join(OUTPUT_DIR, "portfolio.json")

# Swing-trading holding-period band, in trading days.
MIN_HOLD_DAYS = 2.0
MAX_HOLD_DAYS = 21.0

MIN_DSR = 0.95
REQUIRE_CONFIRMED = True
CORR_THRESHOLD = 0.70
TARGET_VOL = 0.10
MAX_WEIGHT = 0.25
MAX_GROSS = 1.00


def load_data(refresh: bool = False):
    print(f"Swing track -- loading curated universe ({len(SWING_UNIVERSE)} "
          f"tickers) into {os.path.basename(CACHE_DIR)}/ ...\n")
    return L.load_universe(tickers=SWING_UNIVERSE, cache_dir=CACHE_DIR,
                           refresh=refresh)


def swing_filter(survivors: pd.DataFrame,
                 min_days: float = MIN_HOLD_DAYS,
                 max_days: float = MAX_HOLD_DAYS) -> pd.DataFrame:
    """Filter 6-filter-funnel survivors to a swing-appropriate holding period."""
    out = survivors.copy()
    out["avg_hold_days"] = out["oos_bars"] / out["trades"]
    swing = out[(out["avg_hold_days"] >= min_days)
                & (out["avg_hold_days"] <= max_days)]
    print(f"\nSwing-holding-period filter ({min_days:.0f}-{max_days:.0f} "
          f"trading days): {len(swing)}/{len(out)} funnel survivors qualify")
    return swing


def build(refresh: bool = False, verbose: bool = True):
    data = load_data(refresh=refresh)
    if not data:
        print("No data available.")
        return None

    dev = V.split_dev(data)
    configs = L.build_configs()
    res = F.run_sweep(dev, configs, csv_path=DEV_SWEEP_CSV, verbose=verbose)

    survivors = F.funnel_report(res) if verbose else \
        F.apply_filters(res).query("survived")
    if survivors.empty:
        print("\nNo funnel survivors -- nothing swing-eligible.")
        return None

    swing = swing_filter(survivors)
    if swing.empty:
        print("\nNo survivors fall in the swing holding-period band.")
        return None

    sens = R.parameter_sensitivity(res)
    sens.to_csv(SENSITIVITY_CSV)
    R.print_parameter_sensitivity(sens)
    boot_summary, _ = R.run_bootstrap(
        swing, dev, configs, n_reshuffles=200,
        summary_csv=BOOTSTRAP_CSV, samples_csv=BOOTSTRAP_SAMPLES_CSV,
        verbose=verbose)
    R.print_bootstrap(boot_summary)

    swing = V.deflate_survivors(swing, res, dev, configs)
    ho = V.holdout_evaluate(swing, data, configs)
    ho.to_csv(HOLDOUT_CSV, index=False)
    V.print_holdout(ho)

    eligible = ho[ho["dsr"] >= MIN_DSR]
    if REQUIRE_CONFIRMED:
        eligible = eligible[eligible["confirmed"]]
    if eligible.empty:
        print(f"\nNo swing survivors pass eligibility (DSR >= {MIN_DSR}"
              + (", confirmed on holdout" if REQUIRE_CONFIRMED else "")
              + ") -- refusing to build a portfolio from noise.")
        return None

    rets = P.survivor_return_matrix(eligible, data, configs)
    quality = pd.Series(
        eligible.set_index(eligible["config"] + " @ " + eligible["asset"])
        ["holdout_sharpe"])
    clusters = P.cluster_survivors(rets, quality=quality,
                                   corr_threshold=CORR_THRESHOLD)
    clusters.to_csv(CLUSTERS_CSV)
    edges = P.independent_edges(clusters)
    n_dupe = len(clusters) - len(edges)
    print(f"\nClustering: {len(clusters)} eligible survivors -> "
          f"{len(edges)} independent edges "
          f"({n_dupe} de-duplicated at |rho| >= {CORR_THRESHOLD})")

    spec = P.build_portfolio(rets[edges], target_vol=TARGET_VOL,
                             max_weight=MAX_WEIGHT, max_gross=MAX_GROSS)
    spec["corr_threshold"] = CORR_THRESHOLD
    spec["min_dsr"] = MIN_DSR
    spec["require_confirmed"] = REQUIRE_CONFIRMED
    spec["min_hold_days"] = MIN_HOLD_DAYS
    spec["max_hold_days"] = MAX_HOLD_DAYS
    spec["built_from_bars_through"] = rets.index.max().date().isoformat()

    with open(PORTFOLIO_JSON, "w") as f:
        json.dump(spec, f, indent=2)
    print(f"\nSwing portfolio spec written to "
          f"{os.path.relpath(PORTFOLIO_JSON, _ROOT)}")
    print(f"Swing portfolio: {spec['n_strategies']} strategies | "
          f"gross {spec['gross_leverage']:.2f} | "
          f"in-sample vol {spec['achieved_vol']:.1%} "
          f"(target {spec['target_vol']:.0%}"
          + (", capped by gross limit" if spec["vol_capped_by_gross_limit"]
             else "") + ")")
    return spec


def main():
    ap = argparse.ArgumentParser(description="Swing-trading track pipeline")
    ap.add_argument("--signals", action="store_true",
                    help="print today's signals from the saved spec instead "
                         "of rebuilding")
    ap.add_argument("--refresh", action="store_true",
                    help="force fresh data download before running")
    args = ap.parse_args()

    if args.signals:
        if not os.path.exists(PORTFOLIO_JSON):
            print(f"No saved swing portfolio "
                  f"({os.path.relpath(PORTFOLIO_JSON, _ROOT)}). "
                  "Run without --signals first.")
            return
        with open(PORTFOLIO_JSON) as f:
            spec = json.load(f)
        data = load_data(refresh=args.refresh)
        P.print_signals(P.signals_today(spec, data))
        return

    spec = build(refresh=args.refresh)
    if spec:
        data = load_data()
        P.print_signals(P.signals_today(spec, data))


if __name__ == "__main__":
    main()
