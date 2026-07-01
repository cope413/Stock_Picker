"""Tests for Layer 6 — clustering, ERC portfolio, and signals_today.

Synthetic data only; no network. Run with: pytest -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import layer1_data_strategies as L
import layer2_funnel as F
import layer6_portfolio as P


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _make_asset(seed: int, drift: float, periods: int = 1200) -> pd.DataFrame:
    g = np.random.default_rng(seed)
    r = g.normal(drift, 0.012, periods)
    close = 100 * np.exp(np.cumsum(r))
    idx = pd.bdate_range("2016-01-01", periods=periods)
    return pd.DataFrame({"Open": np.r_[close[0], close[:-1]],
                         "High": close * 1.004, "Low": close * 0.996,
                         "Close": close,
                         "Volume": np.full(periods, 2e6)}, index=idx)


@pytest.fixture(scope="module")
def universe() -> dict:
    return {f"A{k}": _make_asset(k, d)
            for k, d in enumerate([0.0008, 0.0004, 0.0])}


@pytest.fixture(scope="module")
def ret_matrix() -> pd.DataFrame:
    """Four return streams: s0 ~ s1 (rho ~ 0.95), s2 = -s0 (mirror), s3 indep."""
    g = np.random.default_rng(42)
    idx = pd.bdate_range("2016-01-01", periods=1000)
    base = g.normal(0.0005, 0.01, 1000)
    s0 = base
    s1 = base + g.normal(0, 0.003, 1000)          # high positive corr with s0
    s2 = -base + g.normal(0, 0.001, 1000)         # strong negative corr
    s3 = g.normal(0.0004, 0.01, 1000)             # independent
    return pd.DataFrame({"s0": s0, "s1": s1, "s2": s2, "s3": s3}, index=idx)


# --------------------------------------------------------------------------- #
# Return matrix
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_survivor_return_matrix_shape_and_values(universe):
    configs = L.build_configs()[:4]
    survivors = pd.DataFrame([
        {"config": name, "asset": "A0"} for name, _, _, _ in configs[:2]
    ] + [{"config": configs[0][0], "asset": "A1"}])
    m = P.survivor_return_matrix(survivors, universe, configs)
    assert m.shape[1] == 3
    assert all(" @ " in c for c in m.columns)
    # spot-check one column against a manual computation
    name, fn, params, _ = configs[0]
    pos = fn(universe["A0"], **params)
    manual = F.strategy_returns(pos, F.asset_returns(universe["A0"]),
                                cost_bps=F.default_cost_bps("A0"))
    pd.testing.assert_series_equal(
        m[f"{name} @ A0"].dropna(), manual.dropna(), check_names=False)


@pytest.mark.unit
def test_return_matrix_skips_unknown(universe):
    survivors = pd.DataFrame([{"config": "nope", "asset": "A0"},
                              {"config": "x", "asset": "MISSING"}])
    m = P.survivor_return_matrix(survivors, universe, L.build_configs()[:2])
    assert m.empty


# --------------------------------------------------------------------------- #
# Clustering (#6)
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_clustering_groups_correlated_and_mirrored(ret_matrix):
    cl = P.cluster_survivors(ret_matrix, corr_threshold=0.7)
    # s0, s1 and the mirror s2 share a cluster; s3 stands alone
    c = cl["cluster"]
    assert c["s0"] == c["s1"] == c["s2"]
    assert c["s3"] != c["s0"]
    assert cl["representative"].sum() == 2
    edges = P.independent_edges(cl)
    assert "s3" in edges and len(edges) == 2


@pytest.mark.unit
def test_clustering_picks_best_quality_as_representative(ret_matrix):
    q = pd.Series({"s0": 0.5, "s1": 2.0, "s2": 0.1, "s3": 1.0})
    cl = P.cluster_survivors(ret_matrix, quality=q, corr_threshold=0.7)
    edges = P.independent_edges(cl)
    assert "s1" in edges                      # best of its cluster
    assert cl.loc["s0", "rho_to_rep"] == pytest.approx(
        ret_matrix.corr().loc["s0", "s1"])


@pytest.mark.unit
def test_low_overlap_pairs_not_merged():
    idx = pd.bdate_range("2016-01-01", periods=300)
    g = np.random.default_rng(7)
    a = pd.Series(g.normal(0, 0.01, 300), index=idx)
    b = a.copy()
    b.iloc[:270] = np.nan                    # only 30 overlapping bars
    m = pd.DataFrame({"a": a, "b": b})
    cl = P.cluster_survivors(m, corr_threshold=0.7, min_overlap=60)
    assert cl.loc["a", "cluster"] != cl.loc["b", "cluster"]


# --------------------------------------------------------------------------- #
# ERC + portfolio (#9)
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_erc_equal_vol_zero_corr_is_equal_weight():
    cov = pd.DataFrame(np.eye(4) * 0.04,
                       index=list("abcd"), columns=list("abcd"))
    w = P.erc_weights(cov)
    assert np.allclose(w.values, 0.25, atol=1e-6)


@pytest.mark.unit
def test_erc_risk_contributions_equalized():
    g = np.random.default_rng(3)
    a = g.normal(size=(5, 5))
    cov = pd.DataFrame(a @ a.T / 5 + np.eye(5) * 0.05)
    w = P.erc_weights(cov)
    rc = w.values * (cov.values @ w.values)
    assert w.min() > 0 and w.sum() == pytest.approx(1.0)
    assert rc.max() / rc.min() == pytest.approx(1.0, rel=1e-4)


@pytest.mark.unit
def test_build_portfolio_targets_vol_and_caps(ret_matrix):
    spec = P.build_portfolio(ret_matrix[["s0", "s3"]],
                             target_vol=0.08, max_weight=0.9, max_gross=3.0)
    assert spec["achieved_vol"] == pytest.approx(0.08, rel=1e-6)
    assert spec["n_strategies"] == 2
    # gross cap binds when target vol is unreachable
    spec2 = P.build_portfolio(ret_matrix[["s0", "s3"]],
                              target_vol=5.0, max_gross=1.0)
    assert spec2["vol_capped_by_gross_limit"]
    assert spec2["gross_leverage"] == pytest.approx(1.0)


@pytest.mark.unit
def test_max_weight_cap_respected(ret_matrix):
    # give one column tiny vol so ERC would overweight it heavily
    m = ret_matrix[["s0", "s3"]].copy()
    m["tiny"] = np.random.default_rng(1).normal(0, 0.0005, len(m))
    spec = P.build_portfolio(m, target_vol=0.05, max_weight=0.40,
                             max_gross=1.0)
    w = pd.Series(spec["weights"])
    shares = w.abs() / w.abs().sum()
    assert (shares <= 0.40 + 1e-9).all()


@pytest.mark.unit
def test_single_strategy_portfolio(ret_matrix):
    spec = P.build_portfolio(ret_matrix[["s3"]], target_vol=0.10,
                             max_gross=2.0)
    assert spec["n_strategies"] == 1
    assert spec["achieved_vol"] == pytest.approx(0.10, rel=1e-6)


# --------------------------------------------------------------------------- #
# signals_today
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_signals_today_matches_position_times_weight(universe):
    configs = L.build_configs()[:3]
    name, fn, params, _ = configs[0]
    spec = {"weights": {f"{name} @ A0": 0.6, f"{name} @ A1": -0.2,
                        "ghost @ A0": 0.1}}
    sig = P.signals_today(spec, universe, configs)
    assert len(sig) == 2                     # ghost skipped
    row = sig[sig["asset"] == "A0"].iloc[0]
    pos = fn(universe["A0"], **params)
    assert row["exposure"] == pytest.approx(float(pos.iloc[-1]) * 0.6)
    assert row["as_of"] == universe["A0"].index[-1].date().isoformat()


@pytest.mark.unit
def test_signals_today_empty_spec(universe):
    assert P.signals_today({"weights": {}}, universe,
                           L.build_configs()[:1]).empty
