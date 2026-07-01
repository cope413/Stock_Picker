"""Layer 6 — the actual picker.

Turns Layer 5's validated survivors into something you can trade:

1.  **De-duplication (TODO #6).** The funnel counts each (config, asset) row
    separately, so ten nearly identical ``ma_crossover`` variants on SPY look
    like ten edges when they're one heavily grid-searched idea. We compute
    each survivor's daily strategy-return series, cluster by absolute return
    correlation (greedy, quality-ordered), and keep the best survivor per
    cluster. What remains is a set of approximately *independent* edges.

2.  **Portfolio construction (TODO #9).** Independent edges are combined with
    equal-risk-contribution (ERC) weights — solved by the standard cyclical
    algorithm, no scipy — then scaled to an annualized volatility target with
    a gross-leverage cap and a per-strategy weight cap.

3.  **``signals_today``.** Computes every portfolio strategy's position on the
    latest bar and prints net exposure per asset — the list of positions to
    actually hold. The portfolio spec is persisted to JSON so the daily signal
    run doesn't re-run validation.

Usage
-----
    python layer6_portfolio.py            # full build: validate -> cluster ->
                                          # weights -> save spec -> today's signals
    python layer6_portfolio.py --signals  # signals only, from saved spec

The full build re-uses Layer 5's discipline: survivors come from the dev-period
funnel, are deflated (DSR), and must be confirmed on the untouched holdout
before they're eligible for the portfolio.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import layer1_data_strategies as L
import layer2_funnel as F
import layer5_validation as V

_HERE = os.path.dirname(os.path.abspath(__file__))
PORTFOLIO_JSON = os.path.join(_HERE, "layer6_portfolio.json")
CLUSTERS_CSV = os.path.join(_HERE, "layer6_clusters.csv")

# Defaults — override via CLI flags.
CORR_THRESHOLD = 0.70      # |corr| above this => same cluster (same edge)
TARGET_VOL = 0.10          # annualized portfolio vol target
MAX_WEIGHT = 0.25          # cap on any single strategy's share of gross
MAX_GROSS = 1.00           # gross leverage cap (sum of |weights|)
MIN_DSR = 0.95             # eligibility: deflated Sharpe threshold
REQUIRE_CONFIRMED = True   # eligibility: positive holdout Sharpe


# --------------------------------------------------------------------------- #
# Survivor return matrix
# --------------------------------------------------------------------------- #

def survivor_return_matrix(survivors: pd.DataFrame,
                           data: Dict[str, pd.DataFrame],
                           configs: Optional[list] = None,
                           cost_fn=F.default_cost_bps) -> pd.DataFrame:
    """Daily net strategy returns, one column per survivor.

    Column labels are ``"config @ asset"``. Positions are computed on the full
    history passed in ``data``; rows are the union of all indices (missing
    values left as NaN so pairwise correlations use overlapping bars only).
    """
    configs = configs if configs is not None else L.build_configs()
    lookup = {name: (fn, params) for name, fn, params, _ in configs}

    cols = {}
    for _, row in survivors.iterrows():
        name, asset = row["config"], row["asset"]
        if name not in lookup or asset not in data:
            continue
        fn, params = lookup[name]
        df = data[asset]
        pos = fn(df, **params)
        sr = F.strategy_returns(pos, F.asset_returns(df),
                                cost_bps=cost_fn(asset))
        cols[f"{name} @ {asset}"] = sr
    return pd.DataFrame(cols)


# --------------------------------------------------------------------------- #
# TODO #6 — correlation clustering
# --------------------------------------------------------------------------- #

def cluster_survivors(returns: pd.DataFrame,
                      quality: Optional[pd.Series] = None,
                      corr_threshold: float = CORR_THRESHOLD,
                      min_overlap: int = 60) -> pd.DataFrame:
    """Greedy quality-ordered clustering on absolute return correlation.

    Survivors are visited best-first (by ``quality``; defaults to realized
    Sharpe of the return column). Each is assigned to the first existing
    cluster whose *representative* (the cluster's best member) it correlates
    with at ``|rho| >= corr_threshold``; otherwise it founds a new cluster.
    Negative correlation counts as the same edge: a strategy and its mirror
    are one idea, not two.

    Pairs with fewer than ``min_overlap`` common bars are treated as
    uncorrelated (kept separate) — too little data to claim they're the same.

    Returns a DataFrame indexed like ``returns.columns`` with columns
    ``cluster`` (int), ``representative`` (bool), ``quality`` (float),
    ``rho_to_rep`` (float; NaN for representatives).
    """
    names = list(returns.columns)
    if quality is None:
        ann = {c: F.sharpe(returns[c].dropna()) for c in names}
        quality = pd.Series(ann)
    quality = quality.reindex(names)

    order = quality.sort_values(ascending=False).index.tolist()
    corr = returns.corr(min_periods=min_overlap)

    reps: List[str] = []                 # representative of each cluster
    assign: Dict[str, Tuple[int, float]] = {}   # name -> (cluster id, rho)

    for name in order:
        placed = False
        for cid, rep in enumerate(reps):
            rho = corr.loc[name, rep]
            if pd.notna(rho) and abs(rho) >= corr_threshold:
                assign[name] = (cid, float(rho))
                placed = True
                break
        if not placed:
            assign[name] = (len(reps), np.nan)
            reps.append(name)

    out = pd.DataFrame({
        "cluster": [assign[n][0] for n in names],
        "rho_to_rep": [assign[n][1] for n in names],
        "quality": quality.values,
    }, index=names)
    out["representative"] = [n in reps for n in names]
    return out.sort_values(["cluster", "representative"],
                           ascending=[True, False])


def independent_edges(clusters: pd.DataFrame) -> List[str]:
    """The representatives — one (best) survivor per cluster."""
    return clusters.index[clusters["representative"]].tolist()


# --------------------------------------------------------------------------- #
# TODO #9 — ERC weights, vol targeting, caps
# --------------------------------------------------------------------------- #

def erc_weights(cov: pd.DataFrame, iters: int = 500,
                tol: float = 1e-10) -> pd.Series:
    """Equal-risk-contribution weights via the cyclical coordinate algorithm.

    Long-only in weight space (strategies already encode their own direction);
    with a diagonal covariance this reduces to inverse-vol.
    """
    c = cov.values
    n = c.shape[0]
    if n == 1:
        return pd.Series([1.0], index=cov.index)
    w = np.full(n, 1.0 / n)
    for _ in range(iters):
        w_prev = w.copy()
        for i in range(n):
            # solve for w_i s.t. its risk contribution equals the average
            sigma_i2 = c[i, i]
            rest = w @ c[:, i] - w[i] * sigma_i2
            # w_i * (w_i*sigma_i2 + rest) = (1/n) * w' C w  — quadratic in w_i
            port_var = w @ c @ w
            target = port_var / n
            disc = rest * rest + 4.0 * sigma_i2 * target
            w[i] = (-rest + np.sqrt(disc)) / (2.0 * sigma_i2)
        w = np.maximum(w, 1e-12)
        w /= w.sum()
        if np.max(np.abs(w - w_prev)) < tol:
            break
    return pd.Series(w, index=cov.index)


def build_portfolio(returns: pd.DataFrame,
                    target_vol: float = TARGET_VOL,
                    max_weight: float = MAX_WEIGHT,
                    max_gross: float = MAX_GROSS) -> Dict:
    """ERC weights on the given (independent) strategy returns, then scale.

    1. ERC weights on the sample covariance (annualized per column frequency).
    2. Cap any weight at ``max_weight`` of gross; renormalize.
    3. Scale gross so realized portfolio vol hits ``target_vol`` annualized,
       subject to ``max_gross``.

    Returns a dict spec: weights, achieved vol, gross, and diagnostics.
    """
    rets = returns.dropna(how="all")
    ppy = F.periods_per_year(rets.index)
    cov = rets.cov() * ppy

    w = erc_weights(cov)

    # cap + renormalize (repeat: capping can push others over)
    for _ in range(20):
        over = w > max_weight
        if not over.any():
            break
        w[over] = max_weight
        free = ~over
        slack = 1.0 - w[over].sum()
        if free.any() and w[free].sum() > 0:
            w[free] = w[free] / w[free].sum() * slack
        else:
            w = w / w.sum()
            break

    port = (rets.fillna(0.0) * w).sum(axis=1)
    realized = float(port.std(ddof=1) * np.sqrt(ppy))
    scale = (target_vol / realized) if realized > 0 else 1.0
    gross = min(scale, max_gross)  # w sums to 1, so gross == scale
    w_final = w * gross

    scaled = port * gross
    return {
        "weights": {k: float(v) for k, v in w_final.items()},
        "gross_leverage": float(w_final.abs().sum()),
        "target_vol": target_vol,
        "achieved_vol": float(scaled.std(ddof=1) * np.sqrt(ppy)),
        "portfolio_sharpe": F.sharpe(scaled, ppy),
        "portfolio_max_drawdown": F.max_drawdown(scaled),
        "n_strategies": int(len(w_final)),
        "periods_per_year": float(ppy),
        "vol_capped_by_gross_limit": bool(scale > max_gross),
    }


# --------------------------------------------------------------------------- #
# signals_today
# --------------------------------------------------------------------------- #

def _parse_key(key: str) -> Tuple[str, str]:
    name, asset = key.rsplit(" @ ", 1)
    return name, asset


def signals_today(spec: Dict, data: Dict[str, pd.DataFrame],
                  configs: Optional[list] = None) -> pd.DataFrame:
    """Current position for every strategy in the portfolio spec.

    Each strategy's position function runs on the latest data; the last bar's
    position (in {-1, 0, +1} or fractional) times the strategy weight is its
    exposure. Exposures are also netted per asset — that's the trade list.
    """
    configs = configs if configs is not None else L.build_configs()
    lookup = {name: (fn, params) for name, fn, params, _ in configs}

    rows = []
    for key, weight in spec["weights"].items():
        name, asset = _parse_key(key)
        if name not in lookup or asset not in data:
            continue
        fn, params = lookup[name]
        df = data[asset]
        pos = fn(df, **params)
        p = float(pos.iloc[-1]) if len(pos) else 0.0
        rows.append({
            "strategy": name, "asset": asset, "weight": weight,
            "position": p, "exposure": p * weight,
            "as_of": df.index[-1].date().isoformat(),
        })
    sig = pd.DataFrame(rows)
    if sig.empty:
        return sig
    return sig.sort_values("exposure", key=lambda s: s.abs(),
                           ascending=False).reset_index(drop=True)


def print_signals(sig: pd.DataFrame) -> None:
    print("\n" + "=" * 78)
    print("LAYER 6 — SIGNALS TODAY")
    print("=" * 78)
    if sig.empty:
        print("  Portfolio is empty — no validated edges to trade.")
        return
    print(f"  as of {sig['as_of'].max()}\n")
    print("  Per strategy:")
    for _, r in sig.iterrows():
        print(f"    {r['exposure']:+7.3f}  {r['asset']:<6} "
              f"(pos {r['position']:+.2f} x w {r['weight']:.3f})  {r['strategy']}")
    net = sig.groupby("asset")["exposure"].sum().sort_values(
        key=lambda s: s.abs(), ascending=False)
    print("\n  Net exposure per asset (the trade list):")
    for asset, e in net.items():
        tag = "LONG" if e > 1e-9 else ("SHORT" if e < -1e-9 else "FLAT")
        print(f"    {e:+7.3f}  {asset:<6} {tag}")
    print(f"\n  Gross: {sig['exposure'].abs().sum():.3f}   "
          f"Net: {sig['exposure'].sum():+.3f}")


# --------------------------------------------------------------------------- #
# Full build
# --------------------------------------------------------------------------- #

def build(data: Optional[Dict[str, pd.DataFrame]] = None,
          corr_threshold: float = CORR_THRESHOLD,
          target_vol: float = TARGET_VOL,
          max_weight: float = MAX_WEIGHT,
          max_gross: float = MAX_GROSS,
          min_dsr: float = MIN_DSR,
          require_confirmed: bool = REQUIRE_CONFIRMED,
          save: bool = True, verbose: bool = True) -> Optional[Dict]:
    """Validate (Layer 5) -> filter eligible -> cluster -> weight -> save."""
    if data is None:
        data = L.load_universe()
    if not data:
        print("No data available.")
        return None

    dev = V.split_dev(data)
    configs = L.build_configs()
    res = F.run_sweep(dev, configs, csv_path=V.DEV_SWEEP_CSV,
                      verbose=verbose)
    survivors = F.funnel_report(res) if verbose else \
        F.apply_filters(res).query("survived")
    if survivors.empty:
        print("\nNo funnel survivors — no portfolio to build.")
        return None

    survivors = V.deflate_survivors(survivors, res, dev, configs)
    ho = V.holdout_evaluate(survivors, data, configs)
    if verbose:
        V.print_holdout(ho)
    if ho.empty:
        print("\nNo survivors reached the holdout — no portfolio to build.")
        return None

    eligible = ho[ho["dsr"] >= min_dsr]
    if require_confirmed:
        eligible = eligible[eligible["confirmed"]]
    if eligible.empty:
        print(f"\nNo survivors pass eligibility (DSR >= {min_dsr}"
              + (", confirmed on holdout" if require_confirmed else "")
              + ") — refusing to build a portfolio from noise.")
        return None

    rets = survivor_return_matrix(eligible, data, configs)
    quality = pd.Series(
        eligible.set_index(
            eligible["config"] + " @ " + eligible["asset"]
        )["holdout_sharpe"])
    clusters = cluster_survivors(rets, quality=quality,
                                 corr_threshold=corr_threshold)
    clusters.to_csv(CLUSTERS_CSV)
    edges = independent_edges(clusters)

    if verbose:
        n_dupe = len(clusters) - len(edges)
        print(f"\nClustering: {len(clusters)} eligible survivors -> "
              f"{len(edges)} independent edges "
              f"({n_dupe} de-duplicated at |rho| >= {corr_threshold}). "
              f"Detail: {os.path.basename(CLUSTERS_CSV)}")

    spec = build_portfolio(rets[edges], target_vol=target_vol,
                           max_weight=max_weight, max_gross=max_gross)
    spec["corr_threshold"] = corr_threshold
    spec["min_dsr"] = min_dsr
    spec["require_confirmed"] = require_confirmed
    spec["built_from_bars_through"] = rets.index.max().date().isoformat()

    if save:
        with open(PORTFOLIO_JSON, "w") as f:
            json.dump(spec, f, indent=2)
        if verbose:
            print(f"Portfolio spec written to "
                  f"{os.path.basename(PORTFOLIO_JSON)}")

    if verbose:
        print(f"\nPortfolio: {spec['n_strategies']} strategies | "
              f"gross {spec['gross_leverage']:.2f} | "
              f"in-sample vol {spec['achieved_vol']:.1%} "
              f"(target {spec['target_vol']:.0%}"
              + (", capped by gross limit" if spec["vol_capped_by_gross_limit"]
                 else "") + ")")
        print("NOTE: portfolio_sharpe in the spec mixes dev+holdout bars and "
              "is descriptive only — the holdout table above is the number "
              "to believe.")
    return spec


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="Layer 6 — portfolio + signals")
    ap.add_argument("--signals", action="store_true",
                    help="print today's signals from the saved portfolio spec")
    ap.add_argument("--corr", type=float, default=CORR_THRESHOLD)
    ap.add_argument("--vol", type=float, default=TARGET_VOL)
    ap.add_argument("--max-weight", type=float, default=MAX_WEIGHT)
    ap.add_argument("--max-gross", type=float, default=MAX_GROSS)
    ap.add_argument("--min-dsr", type=float, default=MIN_DSR)
    ap.add_argument("--allow-unconfirmed", action="store_true",
                    help="drop the positive-holdout-Sharpe requirement")
    args = ap.parse_args()

    if args.signals:
        if not os.path.exists(PORTFOLIO_JSON):
            print(f"No saved portfolio ({os.path.basename(PORTFOLIO_JSON)}). "
                  "Run without --signals first.")
            return
        with open(PORTFOLIO_JSON) as f:
            spec = json.load(f)
        data = L.load_universe()
        print_signals(signals_today(spec, data))
        return

    data = L.load_universe()
    spec = build(data, corr_threshold=args.corr, target_vol=args.vol,
                 max_weight=args.max_weight, max_gross=args.max_gross,
                 min_dsr=args.min_dsr,
                 require_confirmed=not args.allow_unconfirmed)
    if spec:
        print_signals(signals_today(spec, data))


if __name__ == "__main__":
    main()
