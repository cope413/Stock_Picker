"""Offline tests for the Phase 2 landry modules: data_auto (Rule 36
correlations, RS, beta, technicals), drawdown (Part 7 state machine),
fundamentals (Part 10 math + rubric drafts), macro (Part 7 overlay).

All synthetic data — no network, no cache.
"""

import numpy as np
import pandas as pd
import pytest

from landry.data_auto import (
    CORR_WINDOW_MAX_WEEKS,
    beta_size_reduction,
    cluster_exposure,
    correlation_report,
    correlation_vs_holdings,
    draft_relative_strength,
    draft_technical_trend,
    draft_volume_accumulation,
    expands_flagged_cluster,
    ma_200w_state,
    monthly_macd,
    relative_strength,
    rs_score,
    technical_state,
    technical_trend_score,
    weekly_beta,
    weekly_closes,
    weekly_returns,
)
from landry.drawdown import band_level, track_regime
from landry.fundamentals import (
    AnalystRecPeriod,
    FundamentalInputs,
    analyst_consensus_band,
    cagr,
    combined_band,
    compute_metrics,
    compute_wacc,
    debt_to_fcf,
    draft_analyst_consensus,
    draft_fcf_margin_trend,
    draft_fcf_yield_trend,
    draft_quant_scores,
    draft_revenue_growth,
    draft_roic_vs_wacc,
    draft_valuation_multiples,
    effective_tax_rate,
    fcf_yield_level_score,
    growth_cv,
    normalized_fcf,
    roic_pct,
    roic_pct_series,
    trend_direction,
)
from landry.macro import (
    MacroConditions,
    active_effects,
    evaluate_curve_inversion,
    evaluate_hy_spreads,
    macro_cash_floor,
    new_position_size_multiplier,
    unknown_conditions,
)

RNG = np.random.default_rng(7)


def _daily_frame(n=1600, drift=0.0004, vol=0.015, seed=1, start="2018-01-02"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    rets = rng.normal(drift, vol, n)
    close = 100.0 * np.exp(np.cumsum(rets))
    close = pd.Series(close, index=idx)
    high = close * (1 + rng.uniform(0, 0.01, n))
    low = close * (1 - rng.uniform(0, 0.01, n))
    vol_ = pd.Series(rng.uniform(1e6, 2e6, n), index=idx)
    return pd.DataFrame({"Open": close, "High": high, "Low": low,
                         "Close": close, "Volume": vol_})


# --------------------------------------------------------------------------- #
# Rule 36 — correlation windowing and clusters
# --------------------------------------------------------------------------- #

def _correlated_universe(n_weeks=200):
    idx = pd.date_range("2021-01-08", periods=n_weeks, freq="W-FRI")
    base = RNG.normal(0, 0.02, n_weeks)
    mk = lambda w, seed: pd.Series(  # noqa: E731
        w * base + np.sqrt(max(0.0, 1 - w * w))
        * np.random.default_rng(seed).normal(0, 0.02, n_weeks), index=idx)
    return pd.DataFrame({
        "A": mk(0.95, 1), "B": mk(0.95, 2), "C": mk(0.95, 3),  # tight cluster
        "D": mk(0.1, 4), "E": mk(0.0, 5),
    })


def test_correlation_window_capped_at_36_months():
    rets = _correlated_universe(400)
    rep = correlation_report(rets)
    assert rep.window_weeks == CORR_WINDOW_MAX_WEEKS
    assert rep.sufficient


def test_correlation_insufficient_below_52_weeks():
    rep = correlation_report(_correlated_universe(30))
    assert not rep.sufficient


def test_cluster_detection_and_exposure():
    rep = correlation_report(_correlated_universe())
    assert ["A", "B", "C"] in rep.clusters
    assert all({a, b} <= {"A", "B", "C"} for a, b, _ in rep.flagged_pairs)
    checks = cluster_exposure(rep, {"A": 0.10, "B": 0.08, "C": 0.05, "D": 0.02})
    assert len(checks) == 1
    assert checks[0].aggregate_weight == pytest.approx(0.23)
    assert checks[0].over_cap        # 23% > 20%


def test_expands_flagged_cluster_staging():
    rep = correlation_report(_correlated_universe())
    assert expands_flagged_cluster("NEW", rep, {"A": 0.8, "B": 0.75, "D": 0.1})
    assert not expands_flagged_cluster("NEW", rep, {"A": 0.8, "D": 0.9})


def _closes_from_returns(rets):
    return 100.0 * (1.0 + rets).cumprod()


def test_correlation_vs_holdings_flags_a_correlated_candidate():
    rets = _correlated_universe()  # A/B/C tight cluster (w=0.95), D/E near-independent
    closes = _closes_from_returns(rets)
    # a candidate built like A/B/C (w=0.95) should flag against them
    cand = rets.copy()
    cand["NEW"] = 0.95 * rets["A"].values + 0.05 * RNG.normal(0, 0.02, len(rets))
    cc = correlation_vs_holdings("NEW", _closes_from_returns(cand))
    assert cc is not None
    assert cc.flagged
    assert cc.max_correlation_holding in ("A", "B", "C")
    assert set(cc.correlations) == {"A", "B", "C", "D", "E"}


def test_correlation_vs_holdings_independent_candidate_not_flagged():
    rets = _correlated_universe()
    cand = rets.copy()
    cand["NEW"] = RNG.normal(0, 0.02, len(rets))   # independent of everything
    cc = correlation_vs_holdings("NEW", _closes_from_returns(cand))
    assert cc is not None
    assert not cc.flagged
    assert abs(cc.max_correlation) < 0.70


def test_correlation_vs_holdings_none_cases():
    closes = _closes_from_returns(_correlated_universe())
    assert correlation_vs_holdings("NOTPRESENT", closes) is None       # ticker not in closes
    only_self = closes[["A"]]
    assert correlation_vs_holdings("A", only_self) is None             # no holdings to compare


# --------------------------------------------------------------------------- #
# Relative Strength + beta
# --------------------------------------------------------------------------- #

def test_rs_score_bands():
    assert rs_score(0.20) == 5
    assert rs_score(0.10) == 4
    assert rs_score(0.0) == 3
    assert rs_score(-0.10) == 2
    assert rs_score(-0.20) == 1


def test_relative_strength_outperformer():
    idx = pd.date_range("2023-01-06", periods=60, freq="W-FRI")
    spy = pd.Series(100 * 1.001 ** np.arange(60), index=idx)
    hot = pd.Series(100 * 1.006 ** np.arange(60), index=idx)
    rs = relative_strength(hot, spy)
    assert rs.diff_12m > 0.15
    assert rs.score == 5


def test_draft_relative_strength_wraps_the_score():
    idx = pd.date_range("2023-01-06", periods=60, freq="W-FRI")
    spy = pd.Series(100 * 1.001 ** np.arange(60), index=idx)
    hot = pd.Series(100 * 1.006 ** np.arange(60), index=idx)
    d = draft_relative_strength(relative_strength(hot, spy))
    assert d.indicator == "relative_strength"
    assert d.score == 5
    assert d.confidence == "M"
    assert "SPY" in d.rationale


def test_draft_relative_strength_none_when_no_score():
    from landry.data_auto import RelativeStrength
    assert draft_relative_strength(
        RelativeStrength(None, None, None, None)) is None


def test_weekly_beta_recovers_true_beta():
    n = 300
    idx = pd.date_range("2019-01-04", periods=n, freq="W-FRI")
    mkt_r = RNG.normal(0.002, 0.02, n)
    tick_r = 1.5 * mkt_r + RNG.normal(0, 0.005, n)
    spy = pd.Series(100 * np.exp(np.cumsum(mkt_r)), index=idx)
    tk = pd.Series(100 * np.exp(np.cumsum(tick_r)), index=idx)
    res = weekly_beta(tk, spy)
    assert res.source == "5y weekly"
    assert res.beta == pytest.approx(1.5, abs=0.1)
    assert res.size_reduction_pct in (1, 2)


def test_beta_fallback_and_bands():
    # Rule 20 banding (v1.0): 1.40-1.59 -> 1, >= 1.60 -> 2, cap at 2
    assert beta_size_reduction(None) == 0
    assert beta_size_reduction(1.39) == 0
    assert beta_size_reduction(1.40) == 1
    assert beta_size_reduction(1.59) == 1
    assert beta_size_reduction(1.60) == 2
    assert beta_size_reduction(2.50) == 2
    # short history -> 2y fallback
    n = 120
    idx = pd.date_range("2024-01-05", periods=n, freq="W-FRI")
    mkt_r = RNG.normal(0.002, 0.02, n)
    spy = pd.Series(100 * np.exp(np.cumsum(mkt_r)), index=idx)
    tk = pd.Series(100 * np.exp(np.cumsum(mkt_r * 1.2)), index=idx)
    assert weekly_beta(tk, spy).source == "2y weekly"


# --------------------------------------------------------------------------- #
# Technicals
# --------------------------------------------------------------------------- #

def test_ma_200w_state_uptrend_and_downtrend():
    up = _daily_frame(drift=0.001)
    above, _ = ma_200w_state(up["Close"])
    assert above is True
    down = _daily_frame(drift=-0.001, seed=3)
    above, _ = ma_200w_state(down["Close"])
    assert above is False


def test_monthly_macd_directions():
    up = _daily_frame(drift=0.002, vol=0.005)
    macd, sig, ok = monthly_macd(up["Close"])
    assert ok is True and macd > 0
    # deterministic monotonic decline: MACD negative and falling -> not ok
    idx = pd.bdate_range("2018-01-02", periods=1600)
    down = pd.Series(100.0 * np.exp(-0.002 * np.arange(1600)), index=idx)
    macd, sig, ok = monthly_macd(down)
    assert ok is False and macd < 0


def test_technical_trend_rubric():
    assert technical_trend_score(True, True, True) == 5
    assert technical_trend_score(True, True, False) == 4
    assert technical_trend_score(False, False, False) == 1
    assert technical_trend_score(False, True, True) == 4   # 2 bullish
    assert technical_trend_score(True, False, False) == 2  # MACD negative
    assert technical_trend_score(None, True, True) is None


def test_technical_state_bundle():
    st = technical_state(_daily_frame(drift=0.0015, vol=0.008))
    assert st.above_200w_ma is True
    assert st.staging_ok is True
    assert st.technical_trend_score in (4, 5)
    assert st.ad_line_score in (1, 2, 3, 4, 5)


def test_draft_technical_trend_and_volume_accumulation():
    st = technical_state(_daily_frame(drift=0.0015, vol=0.008))
    d = draft_technical_trend(st)
    assert d.indicator == "technical_trend"
    assert d.score == st.technical_trend_score
    assert d.confidence == "M"
    assert "MACD" in d.rationale and "Supertrend" in d.rationale

    dv = draft_volume_accumulation(st)
    assert dv.indicator == "volume_accumulation"
    assert dv.score == st.ad_line_score
    assert dv.confidence == "M"


def test_draft_technical_trend_none_on_short_history():
    st = technical_state(_daily_frame(n=100))
    assert draft_technical_trend(st) is None
    assert draft_volume_accumulation(st) is None


def test_short_history_returns_none():
    st = technical_state(_daily_frame(n=100))
    assert st.above_200w_ma is None
    assert st.staging_ok is None


# --------------------------------------------------------------------------- #
# Part 7 — drawdown state machine
# --------------------------------------------------------------------------- #

def _series(vals, start="2026-01-02"):
    return pd.Series(vals, index=pd.bdate_range(start, periods=len(vals)),
                     dtype=float)


def test_band_levels():
    assert band_level(0.05) == 0
    assert band_level(0.10) == 1
    assert band_level(0.199) == 1
    assert band_level(0.20) == 2
    assert band_level(0.30) == 3


def test_regime_needs_5_days_to_enter():
    # 4 days at -12% then recovery: never leaves Normal
    vals = [100] * 5 + [88] * 4 + [100]
    days = track_regime(_series(vals))
    assert all(d.active_status == "Normal" for d in days)
    # 5th consecutive day beyond 10% -> Elevated
    vals = [100] * 5 + [88] * 5
    days = track_regime(_series(vals))
    assert days[-1].active_status == "Elevated"
    assert days[-2].active_status == "Normal"


def test_regime_needs_10_days_inside_to_exit():
    vals = [100] * 5 + [88] * 5 + [95] * 9      # 9 days back inside
    days = track_regime(_series(vals))
    assert days[-1].active_status == "Elevated"  # not yet
    vals += [95]                                  # 10th day
    days = track_regime(_series(vals))
    assert days[-1].active_status == "Normal"


def test_new_high_resets_immediately():
    vals = [100] * 5 + [85] * 6 + [101]
    days = track_regime(_series(vals))
    assert days[-2].active_status == "Elevated"
    assert days[-1].active_status == "Normal" and days[-1].new_high


def test_straight_to_severe_and_cash_floor():
    vals = [100] * 5 + [75] * 5
    days = track_regime(_series(vals))
    assert days[-1].active_status == "Severe"
    assert days[-1].cash_floor_pct == 20.0


def test_deescalate_one_band_at_a_time_to_most_restrictive_seen():
    # Severe, then hover at -15% (Elevated) for 10 days -> Elevated, not Normal
    vals = [100] * 5 + [75] * 5 + [85] * 10
    days = track_regime(_series(vals))
    assert days[-1].active_status == "Elevated"


# --------------------------------------------------------------------------- #
# Fundamentals — Part 10 math
# --------------------------------------------------------------------------- #

def test_normalized_fcf_deducts_sbc():
    assert normalized_fcf(100, 30, 10) == 60
    assert normalized_fcf(100, -30, -10) == 60   # sign-insensitive


def test_debt_to_fcf():
    inp = FundamentalInputs("X", cfo=[100], capex=[20], sbc=[10],
                            total_debt=280, cash=70)
    assert debt_to_fcf(inp) == pytest.approx(3.0)   # 210 / 70
    # negative FCF -> None, not a number
    inp2 = FundamentalInputs("X", cfo=[10], capex=[50], total_debt=100, cash=0)
    assert debt_to_fcf(inp2) is None
    # net cash -> 0
    inp3 = FundamentalInputs("X", cfo=[100], capex=[20], total_debt=10, cash=90)
    assert debt_to_fcf(inp3) == 0.0


def test_cagr_and_cv():
    assert cagr([100, 121]) == pytest.approx(0.21)
    assert cagr([100, 110, 121]) == pytest.approx(0.10, abs=1e-9)
    assert cagr([]) is None
    steady = [100, 110, 121, 133.1, 146.41]
    assert growth_cv(steady) == pytest.approx(0.0, abs=1e-9)
    lumpy = [100, 150, 120, 200, 160]
    assert growth_cv(lumpy) > 0.75


def test_trend_direction():
    assert trend_direction([10, 11, 14, 16]) == "expanding"
    assert trend_direction([16, 14, 11, 10]) == "contracting"
    assert trend_direction([10, 10, 10, 10]) == "stable"


def test_fcf_yield_rubric():
    assert fcf_yield_level_score(7.0) == 5
    assert fcf_yield_level_score(5.0) == 4
    assert fcf_yield_level_score(3.0) == 3
    assert fcf_yield_level_score(1.0) == 2
    assert fcf_yield_level_score(-1.0) == 1
    assert combined_band(4.5) == 5
    assert combined_band(3.5) == 4
    assert combined_band(1.4) == 1


def test_draft_fcf_yield_trend_combination():
    # yield 5% (4) x0.6 + expanding (5) x0.4 = 4.4 -> band 4
    d = draft_fcf_yield_trend(5.0, "expanding")
    assert d.score == 4
    # yield 7% (5) + expanding (5) -> 5
    assert draft_fcf_yield_trend(7.0, "expanding").score == 5
    # structural deterioration caps at 2
    assert draft_fcf_yield_trend(7.0, "expanding",
                                 structural_deterioration=True).score == 2


def test_draft_revenue_growth_bands():
    assert draft_revenue_growth(0.20, 0.2).score == 5
    assert draft_revenue_growth(0.12, 0.4).score == 4
    assert draft_revenue_growth(0.12, 0.6).score == 3    # CV in 0.5-0.75
    assert draft_revenue_growth(0.02, 0.3).score == 2
    assert draft_revenue_growth(-0.05, 0.3).score == 1
    assert draft_revenue_growth(None, None) is None


def test_draft_fcf_margin_trend():
    assert draft_fcf_margin_trend(30.0, "expanding").score == 5
    assert draft_fcf_margin_trend(20.0, "stable").score == 4
    assert draft_fcf_margin_trend(10.0, "stable").score == 3
    assert draft_fcf_margin_trend(3.0, "stable").score == 2
    assert draft_fcf_margin_trend(20.0, "contracting").score == 2
    assert draft_fcf_margin_trend(-5.0, "contracting").score == 1


def test_draft_roic_vs_wacc():
    assert draft_roic_vs_wacc(25.0, 9.0).score == 5
    assert draft_roic_vs_wacc(17.0, 9.0).score == 4
    assert draft_roic_vs_wacc(12.0, 8.0).score == 3
    assert draft_roic_vs_wacc(8.0, 9.0).score == 2
    assert draft_roic_vs_wacc(6.0, 9.0, persistently_below=True).score == 1
    assert draft_roic_vs_wacc(6.0, 9.0).confidence == "L"


def _roic_inputs(**overrides):
    kw = dict(
        ticker="X", market_cap=1000.0, total_debt=200.0, cash=50.0,
        ebit=[80.0, 100.0], pretax_income=[70.0, 90.0],
        tax_provision=[14.7, 18.9], tax_rate_for_calcs=[0.21, 0.21],
        stockholders_equity=[400.0, 450.0], interest_expense=[10.0, 12.0],
    )
    kw.update(overrides)
    return FundamentalInputs(**kw)


def test_effective_tax_rate_prefers_yfinance_normalized_rate():
    inp = _roic_inputs()
    assert effective_tax_rate(inp) == pytest.approx(0.21)


def test_effective_tax_rate_falls_back_to_provision_over_pretax():
    inp = _roic_inputs(tax_rate_for_calcs=[])
    assert effective_tax_rate(inp) == pytest.approx(18.9 / 90.0)


def test_effective_tax_rate_none_when_nothing_available():
    inp = _roic_inputs(tax_rate_for_calcs=[], tax_provision=[], pretax_income=[])
    assert effective_tax_rate(inp) is None


def test_roic_pct_series_and_latest():
    inp = _roic_inputs()
    # invested capital = 200 (debt) + 450 (latest equity) - 50 (cash) = 600
    # NOPAT_latest = 100 * (1-0.21) = 79 -> ROIC = 79/600*100 = 13.1667%
    series = roic_pct_series(inp)
    assert len(series) == 2
    assert series[-1] == pytest.approx(100.0 * 100 * 0.79 / 600, rel=1e-6)
    assert roic_pct(inp) == pytest.approx(series[-1])


def test_roic_pct_none_without_required_inputs():
    assert roic_pct(_roic_inputs(tax_rate_for_calcs=[], tax_provision=[])) is None
    assert roic_pct(_roic_inputs(stockholders_equity=[])) is None
    assert roic_pct(_roic_inputs(total_debt=None)) is None


def test_compute_wacc_uses_capm_and_documents_assumptions():
    inp = _roic_inputs()
    w = compute_wacc(inp, beta=1.2, risk_free_pct=4.0)
    assert w.risk_free_source == "live 10y Treasury (^TNX)"
    assert w.risk_free_pct == pytest.approx(4.0)
    # cost of equity = 4.0 + 1.2*4.5 = 9.4; E=1000, D=200 -> weight_e=1000/1200
    # cost of debt = 12/200*100 = 6.0; tax 0.21 -> after-tax 4.74
    coe = 4.0 + 1.2 * 4.5
    cod = (12.0 / 200.0) * 100.0
    expected = (1000 / 1200) * coe + (200 / 1200) * cod * (1 - 0.21)
    assert w.wacc_pct == pytest.approx(expected, rel=1e-6)


def test_compute_wacc_falls_back_to_assumption_when_no_live_rate():
    w = compute_wacc(_roic_inputs(), beta=1.0, risk_free_pct=None)
    assert w.risk_free_source == "fallback assumption"
    assert w.risk_free_pct == pytest.approx(4.5)


def test_compute_wacc_none_without_beta_or_market_cap():
    assert compute_wacc(_roic_inputs(), beta=None).wacc_pct is None
    assert compute_wacc(_roic_inputs(market_cap=None), beta=1.0).wacc_pct is None


def test_draft_valuation_multiples_full_rubric_unchanged():
    assert draft_valuation_multiples(8.0, 10.0, 0.8).score == 5
    assert draft_valuation_multiples(8.0, 10.0, None).score == 4
    assert draft_valuation_multiples(10.0, 10.0, None).score == 3


def test_draft_valuation_multiples_peg_only_fallback_when_no_history():
    d = draft_valuation_multiples(15.0, None, 0.8)
    assert d.score == 4
    assert d.confidence == "L"
    assert "no P/FCF history" in d.rationale

    d2 = draft_valuation_multiples(15.0, None, 1.5)
    assert d2.score == 3
    d3 = draft_valuation_multiples(15.0, None, 3.0)
    assert d3.score == 2


def test_draft_valuation_multiples_none_when_nothing_available():
    assert draft_valuation_multiples(15.0, None, None) is None
    assert draft_valuation_multiples(None, None, 0.5) is None


def test_analyst_consensus_band():
    assert analyst_consensus_band(85.0, 0.0) == 5
    assert analyst_consensus_band(85.0, 3.7) == 4   # ETN-style: one dissenter blocks 5
    assert analyst_consensus_band(60.0, 10.0) == 4
    assert analyst_consensus_band(50.0, 15.0) == 3
    assert analyst_consensus_band(30.0, 5.0) == 2
    assert analyst_consensus_band(30.0, 40.0) == 2  # negative exceeds positive
    assert analyst_consensus_band(10.0, 60.0) == 1


def test_draft_analyst_consensus_pinned_to_etn_2026_08():
    # yfinance Ticker("ETN").recommendations, as pulled 2026-08-13
    recs = [
        AnalystRecPeriod("0m", 6, 17, 3, 0, 1),
        AnalystRecPeriod("-1m", 6, 16, 4, 0, 1),
        AnalystRecPeriod("-2m", 6, 16, 4, 0, 1),
        AnalystRecPeriod("-3m", 6, 16, 6, 0, 1),
    ]
    d = draft_analyst_consensus(recs)
    assert d.score == 4                # 85% buy-equiv, 3.7% negative
    assert d.confidence == "M"         # 27 analysts, well above the n<5 floor
    assert "23/27" in d.rationale
    assert "up" in d.rationale         # buy% rose vs 3 months back (75.9% -> 85.2%)


def test_draft_analyst_consensus_thin_sample_is_low_confidence():
    d = draft_analyst_consensus([AnalystRecPeriod("0m", 1, 2, 0, 0, 0)])
    assert d.confidence == "L"


def test_draft_analyst_consensus_empty_is_none():
    assert draft_analyst_consensus([]) is None
    assert draft_analyst_consensus([AnalystRecPeriod("0m", 0, 0, 0, 0, 0)]) is None


def test_compute_metrics_and_drafts_end_to_end():
    inp = FundamentalInputs(
        "ACME", market_cap=2000.0,
        revenue=[400, 460, 530, 610, 700],
        cfo=[100, 115, 132, 152, 175],
        capex=[20, 22, 25, 28, 30],
        sbc=[10, 11, 12, 13, 15],
        total_debt=150, cash=100,
        analyst_recs=[AnalystRecPeriod("0m", 6, 17, 3, 0, 1)])
    m = compute_metrics(inp)
    assert m.fcf == pytest.approx(130.0)          # 175-30-15
    assert m.fcf_yield_pct == pytest.approx(6.5)  # 130/2000
    assert m.fcf_trend == "expanding"
    assert m.revenue_cagr == pytest.approx(0.15, abs=0.005)
    assert m.debt_to_fcf == pytest.approx(50 / 130)
    drafts = draft_quant_scores(m)
    assert drafts["fcf_yield_trend"].score == 5
    assert drafts["revenue_growth_consistency"].score == 5
    assert drafts["analyst_consensus"].score == 4
    assert "moat" not in "".join(drafts)          # judgment never drafted
    assert all(d.confidence in ("M", "L") for d in drafts.values())


def test_thin_history_degrades_confidence():
    inp = FundamentalInputs("X", market_cap=1000.0, revenue=[100, 120],
                            cfo=[50, 60], capex=[10, 10],
                            analyst_recs=[AnalystRecPeriod("0m", 6, 17, 3, 0, 1)])
    m = compute_metrics(inp)
    drafts = draft_quant_scores(m)
    fcf_drafts = {k: d for k, d in drafts.items() if k != "analyst_consensus"}
    assert all(d.confidence == "L" for d in fcf_drafts.values())
    # analyst consensus confidence is driven by analyst sample size, not
    # fiscal-year history -- 27-equivalent sample here stays M
    assert drafts["analyst_consensus"].confidence == "M"


# --------------------------------------------------------------------------- #
# Macro overlay
# --------------------------------------------------------------------------- #

def test_macro_effects_only_for_true_conditions():
    cond = MacroConditions(hy_spreads_over_600=True, spy_below_200w_ma=False)
    effs = active_effects(cond)
    assert len(effs) == 1 and "Credit spreads" in effs[0].condition
    assert "fed_tightening" in unknown_conditions(cond)


def test_macro_multiplier_and_floor():
    assert new_position_size_multiplier(MacroConditions()) == 1.0
    assert new_position_size_multiplier(
        MacroConditions(spy_below_200w_ma=True)) == 0.5
    assert new_position_size_multiplier(
        MacroConditions(hy_spreads_over_600=True, spy_below_200w_ma=True)) == 0.0
    assert macro_cash_floor(MacroConditions(curve_inverted_3mo=True)) == 10.0


def test_macro_evaluators():
    idx = pd.bdate_range("2025-01-01", periods=100)
    inverted = pd.Series(-0.3, index=idx)
    assert evaluate_curve_inversion(inverted) is True
    mixed = pd.Series([-0.3] * 50 + [0.1] + [-0.3] * 49, index=idx)
    assert evaluate_curve_inversion(mixed) is False
    assert evaluate_curve_inversion(None) is None
    hy = pd.Series([3.5, 6.5], index=idx[:2])
    assert evaluate_hy_spreads(hy) is True
    assert evaluate_hy_spreads(pd.Series([4.0], index=idx[:1])) is False


# --------------------------------------------------------------------------- #
# Positions reader (workbook file, skipped when absent)
# --------------------------------------------------------------------------- #

def test_read_positions_from_workbook():
    import os
    from landry.xlsx_io import latest_workbook
    wb = latest_workbook(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if not wb:
        pytest.skip("no workbook file")
    pytest.importorskip("openpyxl")
    from landry.xlsx_io import equity_weights, read_positions

    pos = read_positions(wb)
    tickers = {p.ticker for p in pos}
    assert "NVDA" in tickers and "FZDXX" in tickers
    assert all(p.quantity > 0 for p in pos)       # sold rows excluded
    assert "TSLA" not in tickers                   # sold 08/07/26
    w = equity_weights(pos)
    assert "NVDA" in w and w["NVDA"] > 0.06        # summed across accounts
    assert "FZDXX" not in w                        # cash is not an equity
    assert 0.25 < sum(w.values()) < 0.60           # equities are a minority here


def test_total_portfolio_value_sums_every_position():
    from landry.xlsx_io import Position, total_portfolio_value
    positions = [
        Position("Acct A", "NVDA", "NVIDIA", "Equity", 10, 1000.0, 0.1),
        Position("Acct A", "FZDXX", "Cash sweep", "Cash", 5000, 5000.0, 0.5),
        Position("Acct B", "NVDA", "NVIDIA", "Equity", 5, 500.0, 0.05),
    ]
    assert total_portfolio_value(positions) == pytest.approx(6500.0)


def test_read_drawdown_log_from_workbook():
    import os
    from landry.xlsx_io import latest_workbook
    wb = latest_workbook(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if not wb:
        pytest.skip("no workbook file")
    pytest.importorskip("openpyxl")
    from landry.xlsx_io import read_drawdown_log

    series = read_drawdown_log(wb)
    assert len(series) >= 1
    assert series.index.is_monotonic_increasing
    assert (series > 0).all()
