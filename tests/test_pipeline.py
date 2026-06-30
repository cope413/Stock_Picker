"""End-to-end unit tests for the four-layer strategy testing system.

All tests use synthetic OHLCV data (no network). Run with: pytest -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import layer1_data_strategies as L
import layer2_funnel as F
import layer3_robustness as R
import layer4_xsec_momentum as X


# --------------------------------------------------------------------------- #
# Fixtures
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
def df() -> pd.DataFrame:
    return _make_asset(0, 0.0004)


@pytest.fixture(scope="module")
def universe() -> dict:
    data = {f"A{k}": _make_asset(k, d) for k, d in
            enumerate([0.0008, 0.0006, 0.0004, 0.0002, 0.0, -0.0003])}
    data["BTC-USD"] = _make_asset(50, 0.0009)
    return data


# --------------------------------------------------------------------------- #
# Layer 1 — strategy library
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_families_and_categories():
    counts = {c: len(L.list_strategies(c)) for c in L.CATEGORIES}
    assert counts == {"trend": 19, "meanrev": 12, "volume": 6,
                      "volatility": 3, "pattern": 4, "composite": 3}
    assert len(L.STRATEGIES) == 47


@pytest.mark.unit
def test_configs_in_the_hundreds():
    configs = L.build_configs()
    assert len(configs) >= 100
    name, fn, params, cat = configs[0]
    assert isinstance(name, str) and callable(fn)
    assert isinstance(params, dict) and cat in L.CATEGORIES


@pytest.mark.unit
def test_every_config_valid_and_no_lookahead(df):
    for name, fn, params, _ in L.build_configs():
        pos = fn(df, **params)
        assert set(pd.unique(pos.dropna())).issubset({-1, 0, 1}), name
        assert pos.iloc[0] == 0, name          # starts flat
        assert pos.dtype.kind in "iu", name    # integer


@pytest.mark.unit
def test_no_lookahead_deep(df):
    """Perturbing the last bar must not change any earlier signal."""
    for s in ["ma_crossover", "supertrend", "turtle", "rsi_revert", "squeeze_breakout"]:
        base = L.run_strategy(s, df)
        d2 = df.copy()
        d2.iloc[-1, d2.columns.get_loc("Close")] *= 1.4
        d2.iloc[-1, d2.columns.get_loc("High")] *= 1.4
        assert base.iloc[:-1].equals(L.run_strategy(s, d2).iloc[:-1]), s


# --------------------------------------------------------------------------- #
# Layer 2 — backtest + walk-forward
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_strategy_returns_no_double_lag_and_costs():
    idx = pd.bdate_range("2020-01-01", periods=10)
    ret = pd.Series([0, .01, -.02, .03, .01, -.01, .02, 0, .01, -.01], index=idx)
    pos = pd.Series([0, 1, 1, 1, 0, 0, -1, -1, 0, 0], index=idx, dtype=float)
    sr = F.strategy_returns(pos, ret, cost_bps=10.0)
    expected_cost = pos.diff().abs().fillna(pos.abs()) * 10 * 1e-4
    assert np.allclose(sr.values, (pos * ret - expected_cost).values)
    assert np.isclose(expected_cost.sum(), 4 * 10 * 1e-4)  # 4 sides traded
    assert F.trade_count(pos) == 4


@pytest.mark.unit
def test_metric_primitives():
    assert F.sharpe(pd.Series(np.full(300, 0.001))) > 0
    assert F.sharpe(pd.Series([0.0, 0.0, 0.0])) == 0.0
    assert abs(F.max_drawdown(pd.Series([0, -0.5, 0.0])) - 0.5) < 1e-9


@pytest.mark.unit
def test_walk_forward_stitch(df):
    pos = L.run_strategy("ma_crossover", df)
    wf = F.walk_forward(pos, df)
    nbars = len(F.strategy_returns(pos, F.asset_returns(df)).dropna())
    assert 0.27 < wf.n_oos_bars / nbars < 0.33    # ~30% OOS
    assert len(wf.window_oos_sharpes) == 5


@pytest.mark.unit
def test_walk_forward_returns_matches(df):
    pos = L.run_strategy("ma_crossover", df)
    sr = F.strategy_returns(pos, F.asset_returns(df))
    a = F.walk_forward(pos, df)
    b = F.walk_forward_returns(sr, position=pos)
    assert abs(a.oos_sharpe - b.oos_sharpe) < 1e-12
    assert a.n_oos_trades == b.n_oos_trades


@pytest.mark.unit
def test_six_filters_logic():
    rows = [
        dict(is_sharpe=0.8, oos_sharpe=0.9, oos_max_drawdown=0.20, trades=50),   # ok
        dict(is_sharpe=2.0, oos_sharpe=3.0, oos_max_drawdown=0.10, trades=50),   # f3
        dict(is_sharpe=0.3, oos_sharpe=1.2, oos_max_drawdown=0.10, trades=50),   # f4
        dict(is_sharpe=0.8, oos_sharpe=0.9, oos_max_drawdown=0.10, trades=5),    # f5
        dict(is_sharpe=-0.1, oos_sharpe=0.9, oos_max_drawdown=0.50, trades=50),  # f1/f6
    ]
    for r in rows:
        r.update(config="c", family="c", category="trend", asset="A")
    out = F.apply_filters(pd.DataFrame(rows))
    assert list(out["survived"]) == [True, False, False, False, False]


@pytest.mark.integration
def test_run_sweep_shape(universe):
    configs = L.build_configs()[:20]
    res = F.run_sweep(universe, configs, csv_path="/tmp/_t_sweep.csv", verbose=False)
    cols = {"config", "family", "category", "asset", "is_sharpe", "oos_sharpe",
            "oos_max_drawdown", "trades", "oos_bars"}
    assert cols.issubset(res.columns)
    assert len(res) == 20 * len(universe)


# --------------------------------------------------------------------------- #
# Layer 3 — robustness
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_parameter_sensitivity_aggregation():
    sweep = pd.DataFrame([
        dict(family="f1", config="c1", asset="A", oos_sharpe=1.0),
        dict(family="f1", config="c1", asset="B", oos_sharpe=0.0),
        dict(family="f1", config="c2", asset="A", oos_sharpe=0.4),
        dict(family="f1", config="c2", asset="B", oos_sharpe=-0.2),
    ])
    s = R.parameter_sensitivity(sweep)
    assert abs(s.loc["f1", "mean_oos_sharpe"] - 0.3) < 1e-9
    assert abs(s.loc["f1", "std_oos_sharpe"] - 0.2) < 1e-9
    assert abs(s.loc["f1", "frac_positive"] - 1.0) < 1e-9


@pytest.mark.unit
def test_bootstrap_method():
    g = np.random.default_rng(1)
    rets = pd.Series(g.normal(0.0005, 0.01, 800))
    rep = R.bootstrap_stress(rets, n_reshuffles=300, replace=True)
    perm = R.bootstrap_stress(rets, n_reshuffles=300, replace=False)
    assert rep.sharpe_p5 <= rep.sharpe_p50 <= rep.sharpe_p95
    assert rep.worst_drawdown >= rep.median_drawdown
    assert rep.sharpes.std() > 1e-6              # resample varies Sharpe
    assert perm.sharpes.std() < 1e-9            # permutation does not
    assert R.classify(0.30, 0.50) == "solid"
    assert R.classify(0.70, 0.50) == "fragile"


# --------------------------------------------------------------------------- #
# Layer 4 — cross-sectional momentum
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_xsec_weights_dollar_neutral(universe):
    panel = X.close_panel(universe)
    w = X.xsec_weights(panel, lookback=126, skip=0)
    held = w[(w != 0).any(axis=1)]
    assert np.allclose(held.sum(axis=1).values, 0.0, atol=1e-9)      # neutral
    assert np.allclose(held.abs().sum(axis=1).values, 2.0, atol=1e-9)  # gross 2


@pytest.mark.unit
def test_xsec_no_lookahead_and_costs(universe):
    xs = X.cross_sectional_returns(universe, 126, 0)
    d2 = {k: v.copy() for k, v in universe.items()}
    d2["A0"].iloc[-1, d2["A0"].columns.get_loc("Close")] *= 1.5
    xs2 = X.cross_sectional_returns(d2, 126, 0)
    common = xs.index.intersection(xs2.index)[:-2]
    assert np.allclose(xs.loc[common].values, xs2.loc[common].values, atol=1e-12)
    hi = X.cross_sectional_returns(universe, 126, 0, cost_fn=lambda t: 20.0)
    lo = X.cross_sectional_returns(universe, 126, 0, cost_fn=lambda t: 0.0)
    assert hi.sum() < lo.sum()


@pytest.mark.integration
def test_run_layer4(universe):
    summary, returns_df = X.run_layer4(universe, write=False, verbose=False)
    assert list(summary["lookback"]) == ["3m", "6m", "12-1m"]
    need = {"xsec_oos_sharpe", "xsec_oos_drawdown", "single_asset_mean_oos_sharpe",
            "xsec_beats_single", "window_oos_sharpes", "regime_note"}
    assert need.issubset(summary.columns)
    assert returns_df.shape[1] == 3
