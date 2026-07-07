"""Tests for v7_scoring.py and part11_tracker.py math.

The scoring fixtures reproduce docs/PART8_Test_Run_Record_7-6-26.docx
(SNEX / MFC / SHEL / CRM / ADBE, run 2026-07-06) so the code is pinned to a
documented, hand-checked run of the framework.

Note: the PART8 record shows ADBE Tier 3 contribution as 0.14; the correct
sum from its own scores (2x0.04 + 3x0.02 + 2x0.01) is 0.16, giving a
composite of 84.6 rather than 84. The decision (STRONG BUY) is unaffected.
These tests assert the *correct* arithmetic.
"""

import numpy as np
import pandas as pd
import pytest

from v7_scoring import (
    ScoreCard, beta_overlay_reduction, bear_case_return_test,
    base_case_return_test, composite_score, correlation_violations, decision,
    drawdown_response, entry_checklist, implied_5yr_return, position_size,
    score_stock, tier1_gate, tier1_weighted_average,
)
from part11_tracker import (
    correlation_matrix, drawdown_log, monthly_closes, monthly_returns,
)

# ---- PART8 test-run fixtures (scores straight from the record) ------------ #

T1 = dict  # alias for readability

TIER1 = {
    "SNEX": T1(fcf_yield_trend=4, revenue_growth_consistency=2,
               competitive_moat=3, revenue_visibility=2, fcf_margin_trend=3),
    "MFC":  T1(fcf_yield_trend=4, revenue_growth_consistency=3,
               competitive_moat=3, revenue_visibility=4, fcf_margin_trend=3),
    "SHEL": T1(fcf_yield_trend=4, revenue_growth_consistency=2,
               competitive_moat=2, revenue_visibility=2, fcf_margin_trend=3),
    "CRM":  T1(fcf_yield_trend=5, revenue_growth_consistency=3,
               competitive_moat=4, revenue_visibility=5, fcf_margin_trend=5),
    "ADBE": T1(fcf_yield_trend=5, revenue_growth_consistency=4,
               competitive_moat=4, revenue_visibility=5, fcf_margin_trend=5),
}

TIER2 = {
    "MFC":  dict(management_quality=4, roic_vs_wacc=2,
                 relative_strength=4, valuation_multiples=4),
    "CRM":  dict(management_quality=3, roic_vs_wacc=3,
                 relative_strength=1, valuation_multiples=5),
    "ADBE": dict(management_quality=3, roic_vs_wacc=5,
                 relative_strength=1, valuation_multiples=5),
}

TIER3 = {
    "MFC":  dict(technical_trend=4, analyst_consensus=4, volume_accumulation=3),
    "CRM":  dict(technical_trend=1, analyst_consensus=3, volume_accumulation=2),
    "ADBE": dict(technical_trend=2, analyst_consensus=3, volume_accumulation=2),
}


@pytest.mark.unit
class TestTier1Gate:
    def test_weighted_averages_match_part8(self):
        expected = {"SNEX": 2.93, "MFC": 3.43, "SHEL": 2.71,
                    "CRM": 4.36, "ADBE": 4.57}
        for t, want in expected.items():
            assert tier1_weighted_average(TIER1[t]) == pytest.approx(want, abs=0.005)

    def test_snex_fails_rule_6_and_8(self):
        g = tier1_gate(TIER1["SNEX"])
        assert not g.passes_floor          # 2.93 < 3.0
        assert g.auto_avoid                # two indicators at 2
        assert set(g.indicators_at_or_below_2) == {
            "revenue_growth_consistency", "revenue_visibility"}

    def test_shel_fails_with_three_sub3(self):
        g = tier1_gate(TIER1["SHEL"])
        assert not g.passes and g.auto_avoid
        assert len(g.indicators_at_or_below_2) == 3

    def test_passers(self):
        for t in ("MFC", "CRM", "ADBE"):
            assert tier1_gate(TIER1[t]).passes

    def test_score_stock_stops_at_gate(self):
        card = score_stock("SNEX", TIER1["SNEX"])   # no tier2/3 needed
        assert card.decision == "AVOID"
        assert card.composite is None and card.tier2_contribution is None


@pytest.mark.unit
class TestComposite:
    def test_contributions_match_part8(self):
        # (tier1, tier2, tier3) raw weighted contributions
        expected = {"MFC": (2.40, 0.80, 0.27), "CRM": (3.05, 0.75, 0.12),
                    "ADBE": (3.20, 0.87, 0.16)}   # ADBE t3: PART8 typo, see module docstring
        for t, (e1, e2, e3) in expected.items():
            card = score_stock(t, TIER1[t], TIER2[t], TIER3[t])
            assert card.tier1_contribution == pytest.approx(e1, abs=0.005)
            assert card.tier2_contribution == pytest.approx(e2, abs=0.005)
            assert card.tier3_contribution == pytest.approx(e3, abs=0.005)

    def test_composites_and_decisions(self):
        assert score_stock("MFC", TIER1["MFC"], TIER2["MFC"],
                           TIER3["MFC"]).composite == pytest.approx(69.4, abs=0.05)
        assert score_stock("CRM", TIER1["CRM"], TIER2["CRM"],
                           TIER3["CRM"]).composite == pytest.approx(78.4, abs=0.05)
        # 84.6 with correct tier-3 arithmetic (PART8 records 84)
        adbe = score_stock("ADBE", TIER1["ADBE"], TIER2["ADBE"], TIER3["ADBE"])
        assert adbe.composite == pytest.approx(84.6, abs=0.05)
        assert adbe.decision == "STRONG BUY"
        assert score_stock("MFC", TIER1["MFC"], TIER2["MFC"],
                           TIER3["MFC"]).decision == "BUY"

    def test_decision_thresholds(self):
        assert decision(80) == "STRONG BUY"
        assert decision(79.9) == "BUY"
        assert decision(65) == "BUY"
        assert decision(64.9) == "WATCH LIST"
        assert decision(49) == "AVOID"
        assert decision(34.9) == "PASS"

    def test_leverage_hard_cap(self):
        scores = {**TIER1["ADBE"], **TIER2["ADBE"], **TIER3["ADBE"]}
        assert composite_score(scores) == pytest.approx(84.6, abs=0.05)
        assert composite_score(scores, debt_to_fcf=5.5) == 65     # capped
        assert composite_score(scores, debt_to_fcf=5.0) == pytest.approx(84.6, abs=0.05)

    def test_rejects_bad_scores(self):
        bad = dict(TIER1["MFC"], fcf_yield_trend=6)
        with pytest.raises(ValueError):
            tier1_gate(bad)
        with pytest.raises(ValueError):
            tier1_gate({k: v for k, v in list(TIER1["MFC"].items())[:-1]})


@pytest.mark.unit
class TestEntryChecklist:
    def test_mfc_passes_crm_adbe_fail_technicals(self):
        mfc = entry_checklist(TIER1["MFC"], 69.4, above_200w_ma=True,
                              monthly_macd_ok=True)
        assert mfc.passes
        crm = entry_checklist(TIER1["CRM"], 78.4, above_200w_ma=False,
                              monthly_macd_ok=False)
        assert not crm.passes
        assert any("Rule 12" in f for f in crm.failures())
        assert any("Rule 13" in f for f in crm.failures())
        adbe = entry_checklist(TIER1["ADBE"], 84.6, above_200w_ma=False,
                               monthly_macd_ok=False, no_binary_event=False)
        assert not adbe.passes and len(adbe.failures()) == 3

    def test_implied_return_tests(self):
        # 5% yield + 10% growth, flat multiple: ~15% -> passes 10% floor
        assert implied_5yr_return(0.05, 0.10) == pytest.approx(0.15)
        assert base_case_return_test(0.05, 0.10)
        assert not base_case_return_test(0.02, 0.05)
        # bear case: growth halved must stay >= 0
        assert bear_case_return_test(0.05, 0.10)
        # negative bear-case growth with multiple compression can fail
        assert not bear_case_return_test(0.01, -0.10, exit_multiple_ratio=0.6)


@pytest.mark.unit
class TestSizing:
    def test_beta_overlay_matches_v7_examples(self):
        assert beta_overlay_reduction(0.89) == 0    # MFC
        assert beta_overlay_reduction(1.2) == 0     # CRM
        assert beta_overlay_reduction(1.3) == 0     # at baseline
        assert beta_overlay_reduction(1.43) == 1    # ADBE: 1 completed increment
        assert beta_overlay_reduction(1.5) == 2     # v7 example: 8% -> 6%
        assert beta_overlay_reduction(None) == 0

    def test_part8_sizing(self):
        mfc = position_size(69.4, beta=0.89)
        assert (mfc.initial_pct, mfc.full_pct) == (3.0, 5.0)
        crm = position_size(78.4, beta=1.2)
        assert (crm.initial_pct, crm.full_pct) == (3.0, 5.0)
        adbe = position_size(84.6, beta=1.43)     # Strong Buy 5/8 with -1
        assert (adbe.initial_pct, adbe.full_pct) == (4.0, 7.0)

    def test_modifiers_and_caps(self):
        r = position_size(85, insider_ownership_with_buys=True)
        assert r.full_pct == 8.0                   # +1 capped at 8% hard cap
        r = position_size(85, debt_to_fcf=4.5,
                          customer_concentration_over_30pct=True,
                          macro_headwind=True)
        assert (r.initial_pct, r.full_pct) == (2.0, 5.0)
        assert position_size(60).full_pct == 0.0   # below threshold


@pytest.mark.unit
class TestPart11:
    def test_drawdown_bands(self):
        assert drawdown_response(0.05).status == "Normal"
        assert drawdown_response(0.10).status == "Elevated"
        assert drawdown_response(0.15).cash_floor_pct == 15.0
        assert drawdown_response(0.20).status == "Severe"
        assert drawdown_response(0.30).status == "Critical"
        assert drawdown_response(-0.12).status == "Elevated"   # sign-agnostic

    def test_correlation_rule_needs_two_or_more(self):
        # CRM-ADBE at 0.9 is ONE pair each -> no violation (per PART8 note)
        corr = pd.DataFrame(
            [[1.0, 0.9, 0.1], [0.9, 1.0, 0.2], [0.1, 0.2, 1.0]],
            index=["CRM", "ADBE", "MFC"], columns=["CRM", "ADBE", "MFC"])
        assert correlation_violations(corr) == {}
        corr.loc["CRM", "MFC"] = corr.loc["MFC", "CRM"] = 0.8
        v = correlation_violations(corr)
        assert set(v) == {"CRM"} and set(v["CRM"]) == {"ADBE", "MFC"}

    def test_drawdown_log(self):
        idx = pd.date_range("2026-01-31", periods=6, freq="ME")
        vals = pd.Series([100.0, 110, 99, 85, 76, 105], index=idx)
        log = drawdown_log(vals)
        assert list(log["status"]) == ["Normal", "Normal", "Normal",
                                       "Severe", "Critical", "Normal"]
        assert log["peak"].iloc[-1] == 110
        assert log["drawdown"].min() == pytest.approx(76 / 110 - 1, abs=1e-4)

    def test_correlation_matrix_pipeline(self):
        rng = np.random.default_rng(7)
        idx = pd.date_range("2024-01-01", periods=700, freq="D")
        base = rng.normal(0, 0.01, len(idx))
        mk = lambda noise: pd.DataFrame(
            {"Close": 100 * np.exp(np.cumsum(base + rng.normal(0, noise, len(idx))))},
            index=idx)
        daily = {"A": mk(0.001), "B": mk(0.001), "C": mk(0.05)}
        closes = monthly_closes(daily)
        assert len(closes) <= 25 and list(closes.columns) == ["A", "B", "C"]
        corr = correlation_matrix(monthly_returns(closes))
        assert corr.loc["A", "B"] > 0.7          # shared factor
        assert abs(corr.loc["A", "C"]) < 0.7     # noise-dominated
        assert (np.diag(corr) == 1).all()
