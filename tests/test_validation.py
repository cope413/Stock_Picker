"""Tests for layer5_validation: true holdout, Deflated Sharpe, cost sensitivity.

All tests use synthetic OHLCV data (no network). Run with: pytest -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import layer1_data_strategies as L
import layer2_funnel as F
import layer5_validation as V


# --------------------------------------------------------------------------- #
# Fixtures (synthetic, mirrors test_pipeline)
# --------------------------------------------------------------------------- #

def _make_asset(seed: int, drift: float, periods: int = 1600) -> pd.DataFrame:
    g = np.random.default_rng(seed)
    r = g.normal(drift, 0.012, periods) + 0.0004 * np.sin(np.arange(periods) / 30)
    close = 100 * np.exp(np.cumsum(r))
    high = close * (1 + np.abs(g.normal(0, 0.004, periods)))
    low = close * (1 - np.abs(g.normal(0, 0.004, periods)))
    op = np.r_[close[0], close[:-1]] * (1 + g.normal(0, 0.002, periods))
    vol = g.integers(1_000_000, 5_000_000, periods).astype(float)
    idx = pd.bdate_range("2015-01-01", periods=periods)
    return pd.DataFrame({"Open": op, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


@pytest.fixture(scope="module")
def universe() -> dict:
    return {f"A{k}": _make_asset(k, d) for k, d in
            enumerate([0.0008, 0.0004, 0.0, -0.0002])}


# 1600 business days from 2015-01-01 ends ~2021-02; use a cutoff inside that.
CUTOFF = "2019-01-01"


# --------------------------------------------------------------------------- #
# Holdout split + evaluation
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_split_dev_truncates(universe):
    dev = V.split_dev(universe, holdout_start=CUTOFF, min_bars=500)
    assert set(dev) == set(universe)
    for t, d in dev.items():
        assert d.index.max() < pd.Timestamp(CUTOFF)
        assert len(d) < len(universe[t])
    # impossible min_bars drops everything
    assert V.split_dev(universe, holdout_start="2015-06-01", min_bars=500) == {}


@pytest.mark.unit
def test_holdout_evaluate_scores_only_holdout(universe):
    configs = L.build_configs()[:6]
    survivors = pd.DataFrame([
        {"config": name, "asset": "A0", "family": name.split("[")[0],
         "category": cat, "oos_sharpe": 1.0}
        for name, _, _, cat in configs[:3]
    ])
    ho = V.holdout_evaluate(survivors, universe, configs,
                            holdout_start=CUTOFF)
    assert len(ho) == 3
    n_holdout = (universe["A0"].index >= pd.Timestamp(CUTOFF)).sum()
    assert (ho["holdout_bars"] == n_holdout).all()
    assert ho["holdout_sharpe"].notna().all()
    assert (ho["holdout_sharpe_se"] > 0).all()
    assert ho["confirmed"].equals(ho["holdout_sharpe"] > 0)
    # matches a manual full-history-position / holdout-slice computation
    name, fn, params, _ = configs[0]
    pos = fn(universe["A0"], **params)
    sr = F.strategy_returns(pos, F.asset_returns(universe["A0"]), cost_bps=1.0)
    manual = F.sharpe(sr.loc[sr.index >= pd.Timestamp(CUTOFF)],
                      F.periods_per_year(universe["A0"].index))
    assert abs(ho.iloc[0]["holdout_sharpe"] - manual) < 1e-12


# --------------------------------------------------------------------------- #
# Deflated Sharpe math
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_expected_max_sharpe_grows_with_trials():
    v = 0.02
    e1 = V.expected_max_sharpe(1, v)
    e10 = V.expected_max_sharpe(10, v)
    e100 = V.expected_max_sharpe(100, v)
    e4700 = V.expected_max_sharpe(4700, v)
    assert e1 == 0.0
    assert 0 < e10 < e100 < e4700          # more trials, higher bar
    assert V.expected_max_sharpe(100, 0.0) == 0.0


@pytest.mark.unit
def test_probabilistic_sharpe_behaviour():
    assert V.probabilistic_sharpe(0.10, 0.10, 500) == pytest.approx(0.5)
    assert V.probabilistic_sharpe(0.15, 0.05, 500) > 0.5
    assert V.probabilistic_sharpe(0.02, 0.10, 500) < 0.5
    # more observations => more confident about a positive edge
    lo = V.probabilistic_sharpe(0.10, 0.05, 100)
    hi = V.probabilistic_sharpe(0.10, 0.05, 2000)
    assert hi > lo
    assert 0.0 <= lo <= 1.0 and 0.0 <= hi <= 1.0


@pytest.mark.unit
def test_deflate_survivors_columns(universe):
    dev = V.split_dev(universe, holdout_start=CUTOFF, min_bars=500)
    configs = L.build_configs()[:8]
    sweep = F.run_sweep(dev, configs, csv_path="/tmp/_t_dev_sweep.csv",
                        verbose=False)
    pseudo = sweep.head(5).copy()          # pretend these survived
    out = V.deflate_survivors(pseudo, sweep, dev, configs)
    assert "dsr" in out.columns
    ok = out["dsr"].dropna()
    assert len(ok) == len(out)
    assert ((ok >= 0) & (ok <= 1)).all()


# --------------------------------------------------------------------------- #
# Cost sensitivity
# --------------------------------------------------------------------------- #

@pytest.mark.integration
def test_cost_sensitivity_monotone(universe):
    dev = V.split_dev(universe, holdout_start=CUTOFF, min_bars=500)
    configs = L.build_configs()[:8]
    res = V.cost_sensitivity(dev, configs, multipliers=(1.0, 10.0),
                             write=False, verbose=False)
    assert len(res) == len(configs) * len(dev) * 2
    # higher costs can never raise a config's OOS Sharpe
    lo = res[res["cost_mult"] == 1.0].set_index(["config", "asset"])
    hi = res[res["cost_mult"] == 10.0].set_index(["config", "asset"])
    joined = lo.join(hi, lsuffix="_lo", rsuffix="_hi")
    assert (joined["oos_sharpe_hi"] <= joined["oos_sharpe_lo"] + 1e-12).all()
    # positions (and therefore trade counts) are cost-independent
    assert (joined["trades_hi"] == joined["trades_lo"]).all()
