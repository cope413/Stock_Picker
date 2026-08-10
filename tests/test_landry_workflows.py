"""Landry v1.0 Phase 4 — Parts 4-8 workflow rules (Hard Rules 5-42).

Offline unit tests for landry.implied_return, landry.entry,
landry.sizing, and landry.monitor.  Fixtures are plain dataclasses and
dicts; no network, no workbook file required.
"""

import math
from datetime import date, timedelta

import pytest

from landry.data_auto import TechnicalState
from landry.entry import entry_checklist
from landry.implied_return import (
    Scenario,
    ScenarioSet,
    check_scenarios,
    implied_return,
    terminal_multiple_cap,
)
from landry.monitor import (
    HoldingState,
    band_transition,
    fundamental_triggers,
    hold_through,
    replacement_gate,
    valuation_triggers,
)
from landry.scoring import RuleFlags, ScoreCard
from landry.sizing import (
    Position,
    effective_cash_floor,
    portfolio_constraints,
    position_size,
)
from landry.macro import MacroConditions

TODAY = date(2026, 8, 7)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

def make_card(composite=75.0, t1avg=3.5, rule2="OK"):
    flags = RuleFlags(rule1="OK" if t1avg >= 3.0 else "FAIL",
                      rule2=rule2, rule3="OK", rule4="OK")
    return ScoreCard("TST", t1avg, t1avg * 0.70, flags, composite=composite)


def make_tech(staging_ok=True, macd_ok=True):
    return TechnicalState(
        above_200w_ma=staging_ok, reclaimed_within_6m=False,
        staging_ok=staging_ok, monthly_macd=1.0 if macd_ok else -1.0,
        monthly_macd_signal=0.5, macd_positive_or_turning=macd_ok,
        supertrend_bullish=True, ad_line_score=3, technical_trend_score=4)


def good_scenarios(price=100.0, bear_value=110.0, base_value=200.0):
    """base ~14.9%, bull rich, bear >= 0 by default."""
    return ScenarioSet(price, [
        Scenario("base", year5_fcf_ps=base_value / 18.0,
                 terminal_multiple=18.0, tag="L"),
        Scenario("bear", year5_fcf_ps=bear_value / 10.0,
                 terminal_multiple=10.0, tag="P"),
        Scenario("bull", year5_fcf_ps=15.0, terminal_multiple=20.0, tag="U"),
    ], bear_assumption_documented=True)


def run_entry(card=None, tech=None, scenarios="default",
              binary_event_present=False, binary_event_in_thesis=False,
              p_fcf=25.0, consensus_fcf_growth_2yr=None, **kw):
    if scenarios == "default":
        scenarios = good_scenarios()
    return entry_checklist(card or make_card(), tech or make_tech(),
                           scenarios, binary_event_present,
                           binary_event_in_thesis, p_fcf,
                           consensus_fcf_growth_2yr, **kw)


def failed_rules(decision):
    return {r.rule for r in decision.failures}


# --------------------------------------------------------------------------- #
# Part 4 — Standardized Implied-Return Methodology
# --------------------------------------------------------------------------- #

class TestImpliedReturn:
    def test_doubling_over_five_years(self):
        # price 100, Y5 value 180 + 20 distributions = 200 -> 2^(1/5)-1
        r = implied_return(100.0, 10.0, 18.0, 20.0)
        assert r == pytest.approx(2.0 ** 0.2 - 1.0)
        assert r == pytest.approx(0.148698, abs=1e-6)

    def test_flat_value_is_zero(self):
        assert implied_return(100.0, 10.0, 10.0, 0.0) == pytest.approx(0.0)

    def test_decline_is_negative(self):
        assert implied_return(100.0, 8.0, 10.0, 0.0) < 0.0

    def test_nonpositive_terminal_value_is_total_loss(self):
        assert implied_return(100.0, -5.0, 10.0, 0.0) == -1.0

    def test_bad_price_raises(self):
        with pytest.raises(ValueError):
            implied_return(0.0, 10.0, 18.0)

    def test_terminal_multiple_cap_is_lower_of_the_two(self):
        assert terminal_multiple_cap(30.0, 25.0) == 25.0
        assert terminal_multiple_cap(20.0, 25.0) == 20.0

    def test_terminal_multiple_cap_lifted_by_structural_case(self):
        assert terminal_multiple_cap(30.0, 25.0,
                                     structural_change_approved=True) == math.inf

    def test_scenario_validation(self):
        with pytest.raises(ValueError):
            Scenario("middle", 10.0, 18.0)
        with pytest.raises(ValueError):
            Scenario("base", 10.0, 18.0, tag="Likely")


class TestScenarioChecks:
    def test_valid_set_passes_all_checks(self):
        res = check_scenarios(good_scenarios())
        assert res.passed
        assert res.failures == []

    def test_bear_exactly_zero_passes_rule_11(self):
        res = check_scenarios(good_scenarios(bear_value=100.0))
        assert res.passed

    def test_missing_bull_fails_rule_12(self):
        ss = good_scenarios()
        ss.scenarios = [s for s in ss.scenarios if s.name != "bull"]
        res = check_scenarios(ss)
        assert not res.rule_passes("Rule 12")
        assert any("all documented" in c.description for c in res.failures)

    def test_two_likely_tags_fail_rule_12(self):
        ss = good_scenarios()
        ss.scenarios = [Scenario("base", 11.0, 18.0, tag="L"),
                        Scenario("bear", 11.0, 10.0, tag="L"),
                        Scenario("bull", 15.0, 20.0, tag="U")]
        res = check_scenarios(ss)
        assert any("exactly one" in c.description for c in res.failures)

    def test_likely_on_bull_fails_rule_12(self):
        ss = good_scenarios()
        ss.scenarios = [Scenario("base", 11.0, 18.0, tag="P"),
                        Scenario("bear", 11.0, 10.0, tag="P"),
                        Scenario("bull", 15.0, 20.0, tag="L")]
        res = check_scenarios(ss)
        assert any("Likely scenario is the Base Case" in c.description
                   for c in res.failures)

    def test_no_likely_fails_rule_12(self):
        ss = good_scenarios()
        ss.scenarios = [Scenario("base", 11.0, 18.0, tag="P"),
                        Scenario("bear", 11.0, 10.0, tag="P"),
                        Scenario("bull", 15.0, 20.0, tag="U")]
        assert not check_scenarios(ss).rule_passes("Rule 12")

    def test_negative_bear_fails_rule_11(self):
        res = check_scenarios(good_scenarios(bear_value=80.0))
        assert not res.rule_passes("Rule 11")

    def test_base_below_10pct_fails(self):
        # base must EXCEED 10%: value 160 on price 100 is ~9.86%/yr
        res = check_scenarios(good_scenarios(base_value=160.0))
        assert not res.passed
        assert any("Base Case implied" in c.description for c in res.failures)

    def test_undocumented_bear_assumption_fails_rule_11(self):
        ss = good_scenarios()
        ss.bear_assumption_documented = False
        res = check_scenarios(ss)
        assert not res.rule_passes("Rule 11")
        assert any("documented" in c.description for c in res.failures)

    def test_cyclical_flag_changes_documentation_wording(self):
        ss = good_scenarios()
        ss.cyclical = True
        res = check_scenarios(ss)
        assert res.passed
        assert any("trough-cycle" in c.description for c in res.checks)


# --------------------------------------------------------------------------- #
# Part 4 — Entry Checklist (Rules 5-13)
# --------------------------------------------------------------------------- #

class TestEntryChecklist:
    def test_clean_pass_standard_staging(self):
        d = run_entry()
        assert d.authorized
        assert d.staging_fraction == 1.0
        assert d.review_required is None
        assert len(d.results) == 9          # Rules 5-13, one row each

    def test_rule5_composite_below_65_fails(self):
        d = run_entry(card=make_card(composite=60.0))
        assert not d.authorized
        assert "Rule 5" in failed_rules(d)

    def test_rule5_missing_composite_fails(self):
        d = run_entry(card=make_card(composite=None, t1avg=3.2))
        assert "Rule 5" in failed_rules(d)

    def test_rule6_tier1_average_below_3_fails(self):
        d = run_entry(card=make_card(t1avg=2.8))
        assert "Rule 6" in failed_rules(d)

    def test_rule7_tier1_indicator_at_1_fails(self):
        d = run_entry(card=make_card(rule2="REVIEW"))
        assert "Rule 7" in failed_rules(d)

    def test_rule8_below_ma_stages_at_half_with_review(self):
        d = run_entry(tech=make_tech(staging_ok=False))
        assert d.authorized                 # staging, not eligibility
        assert d.staging_fraction == 0.5
        assert d.review_required == "90-day technical review"

    def test_rule9_negative_macd_does_not_block_entry(self):
        d = run_entry(tech=make_tech(staging_ok=True, macd_ok=False))
        assert d.authorized
        assert d.staging_fraction == 0.5    # held at reduced size
        r9 = next(r for r in d.results if r.rule == "Rule 9")
        assert r9.passed                    # never a failure

    def test_rule9_and_rule8_do_not_compound_below_half(self):
        d = run_entry(tech=make_tech(staging_ok=False, macd_ok=False))
        assert d.staging_fraction == 0.5

    def test_rule10_binary_event_outside_thesis_fails(self):
        d = run_entry(binary_event_present=True, binary_event_in_thesis=False)
        assert "Rule 10" in failed_rules(d)

    def test_rule10_binary_event_in_thesis_passes(self):
        d = run_entry(binary_event_present=True, binary_event_in_thesis=True)
        assert d.authorized

    def test_scenarios_none_fails_rules_11_and_12(self):
        d = run_entry(scenarios=None)
        assert {"Rule 11", "Rule 12"} <= failed_rules(d)
        assert not d.authorized

    def test_negative_bear_case_fails_rule_11(self):
        d = run_entry(scenarios=good_scenarios(bear_value=80.0))
        assert "Rule 11" in failed_rules(d)

    def test_bad_tags_fail_rule_12(self):
        ss = good_scenarios()
        ss.scenarios = [Scenario("base", 11.5, 18.0, tag="P"),
                        Scenario("bear", 11.0, 10.0, tag="L"),
                        Scenario("bull", 15.0, 20.0, tag="U")]
        d = run_entry(scenarios=ss)
        assert "Rule 12" in failed_rules(d)

    def test_rule13_pfcf_over_50_without_growth_fails(self):
        d = run_entry(p_fcf=60.0)
        assert "Rule 13" in failed_rules(d)

    def test_rule13_growth_escape_needs_both_conditions(self):
        d = run_entry(p_fcf=60.0, consensus_fcf_growth_2yr=0.35)
        assert d.authorized                 # growth > 30% AND bear >= 0
        d2 = run_entry(p_fcf=60.0, consensus_fcf_growth_2yr=0.35,
                       scenarios=good_scenarios(bear_value=80.0))
        assert "Rule 13" in failed_rules(d2)   # bear < 0 kills the escape
        d3 = run_entry(p_fcf=60.0, consensus_fcf_growth_2yr=0.25)
        assert "Rule 13" in failed_rules(d3)   # growth too low

    def test_rule13_missing_pfcf_fails_for_nonfinancials(self):
        d = run_entry(p_fcf=None)
        assert "Rule 13" in failed_rules(d)

    def test_rule13_financials_use_sector_verdict(self):
        ok = run_entry(p_fcf=None, financial_sector=True,
                       sector_valuation_ok=True)
        assert ok.authorized
        bad = run_entry(p_fcf=None, financial_sector=True,
                        sector_valuation_ok=False)
        assert "Rule 13" in failed_rules(bad)
        unknown = run_entry(p_fcf=None, financial_sector=True)
        assert "Rule 13" in failed_rules(unknown)


# --------------------------------------------------------------------------- #
# Part 5 — position sizing (Rules 14-20) + Part 7 interactions
# --------------------------------------------------------------------------- #

class TestPositionSize:
    def test_strong_buy_band(self):
        r = position_size(85.0)
        assert (r.base_initial_pct, r.base_full_pct) == (5.0, 8.0)
        assert r.staged_initial_pct == 5.0
        assert r.initiation_allowed

    def test_buy_band(self):
        r = position_size(70.0)
        assert (r.max_initial_pct, r.max_full_pct) == (3.0, 5.0)

    def test_below_65_is_zero_rule_18(self):
        r = position_size(64.9)
        assert r.max_initial_pct == r.max_full_pct == 0.0
        assert not r.initiation_allowed
        assert any("Rule 18" in a for a in r.adjustments)

    def test_insider_modifier_and_rule14_cap(self):
        r = position_size(85.0, insider_buying=True)
        assert r.max_initial_pct == 6.0
        assert r.max_full_pct == 8.0        # 9 -> capped at 8
        assert any("Rule 14" in a for a in r.adjustments)

    def test_negative_modifiers_each_minus_one(self):
        r = position_size(70.0, debt_to_fcf=4.5,
                          customer_concentration_over_30pct=True,
                          macro_headwind_sector=True)
        assert r.max_initial_pct == 0.0     # 3 - 3, floored at 0
        assert r.max_full_pct == 2.0        # 5 - 3
        assert len(r.adjustments) == 3

    def test_debt_at_exactly_4x_no_modifier(self):
        r = position_size(70.0, debt_to_fcf=4.0)
        assert r.max_full_pct == 5.0

    def test_beta_overlay_bands(self):
        assert position_size(85.0, beta=1.39).max_full_pct == 8.0
        assert position_size(85.0, beta=1.50).max_full_pct == 7.0
        assert position_size(85.0, beta=1.70).max_full_pct == 6.0
        assert any("Rule 20" in a
                   for a in position_size(85.0, beta=1.70).adjustments)

    def test_initial_never_exceeds_full(self):
        r = position_size(70.0, insider_buying=True, beta=1.70)
        assert r.max_initial_pct <= r.max_full_pct == 4.0

    def test_staging_fractions_stack_multiplicatively(self):
        macro = MacroConditions(spy_below_200w_ma=True)
        r = position_size(85.0, staging_fraction=0.5, expands_cluster=True,
                          macro=macro)
        assert r.staged_initial_pct == pytest.approx(5.0 * 0.5 * 0.5 * 0.5)
        cites = " | ".join(r.adjustments)
        assert "Rule 8" in cites and "Rule 36" in cites and "Part 7" in cites

    def test_hy_spread_blowout_pauses_entries(self):
        r = position_size(85.0, macro=MacroConditions(hy_spreads_over_600=True))
        assert r.blocked
        assert r.staged_initial_pct == 0.0

    def test_drawdown_level1_blocks_below_80_only(self):
        blocked = position_size(75.0, drawdown_level=1)
        assert blocked.blocked and blocked.staged_initial_pct == 0.0
        open_ = position_size(85.0, drawdown_level=1)
        assert not open_.blocked and open_.staged_initial_pct == 5.0

    def test_drawdown_levels_2_and_3_block_everything(self):
        for lvl in (2, 3):
            r = position_size(95.0, drawdown_level=lvl)
            assert r.blocked
            assert r.staged_initial_pct == 0.0
            assert any("Part 7" in a for a in r.adjustments)


class TestPortfolioConstraints:
    @staticmethod
    def clean_book(n=12, pct=6.0, composite=70.0):
        positions = [Position(f"T{i}", pct, composite) for i in range(n)]
        sectors = {f"T{i}": f"S{i}" for i in range(n)}
        return positions, sectors

    def test_clean_portfolio_no_violations(self):
        pos, sec = self.clean_book()
        assert portfolio_constraints(pos, sec, cash_pct=10.0) == []

    def test_rule14_position_over_8pct(self):
        pos, sec = self.clean_book()
        pos[0] = Position("T0", 9.0, 70.0)
        v = portfolio_constraints(pos, sec, 10.0)
        assert any("Rule 14" in x and "T0" in x for x in v)

    def test_rule15_sector_over_25pct(self):
        pos, sec = self.clean_book()
        for i in range(5):                  # 5 x 6% = 30% in one sector
            sec[f"T{i}"] = "Tech"
        v = portfolio_constraints(pos, sec, 10.0)
        assert any("Rule 15" in x and "Tech" in x for x in v)

    def test_rule15_macro_risk_sector_capped_at_15(self):
        pos, sec = self.clean_book()
        for i in range(3):                  # 18% — fine at 25, not at 15
            sec[f"T{i}"] = "Health"
        macro = MacroConditions(sector_risk={"Health": "drug pricing"})
        assert portfolio_constraints(pos, sec, 10.0) == []
        v = portfolio_constraints(pos, sec, 10.0, macro=macro)
        assert any("Rule 15" in x and "15%" in x for x in v)

    def test_rule16_position_floor_is_a_note_not_forced(self):
        pos, sec = self.clean_book(n=5)
        v = portfolio_constraints(pos, sec, 10.0)
        assert any("Rule 16" in x and "Rule 18" in x for x in v)

    def test_rule17_cash_band(self):
        pos, sec = self.clean_book()
        assert any("Rule 17" in x
                   for x in portfolio_constraints(pos, sec, 3.0))
        assert any("ceiling" in x
                   for x in portfolio_constraints(pos, sec, 20.0))
        assert portfolio_constraints(pos, sec, 12.0) == []

    def test_rule17_floor_raised_by_drawdown_band(self):
        pos, sec = self.clean_book()
        v = portfolio_constraints(pos, sec, 18.0, drawdown_level=2)
        assert any("Rule 17" in x and "20%" in x for x in v)
        # ceiling suspended while the regime demands >= 20% cash
        assert portfolio_constraints(pos, sec, 22.0, drawdown_level=2) == []

    def test_rule17_floor_raised_by_macro_curve_inversion(self):
        pos, sec = self.clean_book()
        macro = MacroConditions(curve_inverted_3mo=True)
        v = portfolio_constraints(pos, sec, 8.0, macro=macro)
        assert any("Rule 17" in x and "10%" in x for x in v)
        assert portfolio_constraints(pos, sec, 14.0, macro=macro) == []

    def test_effective_cash_floor_takes_the_max(self):
        macro = MacroConditions(curve_inverted_3mo=True)
        assert effective_cash_floor() == 5.0
        assert effective_cash_floor(macro) == 10.0
        assert effective_cash_floor(macro, drawdown_level=1) == 15.0
        assert effective_cash_floor(None, drawdown_level=3) == 25.0


# --------------------------------------------------------------------------- #
# Part 6 — sell triggers, band transitions, Hold Through, replacement
# --------------------------------------------------------------------------- #

def make_holding(**kw):
    base = dict(ticker="HLD",
                prior_scores={"fcf_yield_trend": 3, "competitive_moat": 4,
                              "management_quality": 4},
                current_scores={"fcf_yield_trend": 3, "competitive_moat": 4,
                                "management_quality": 4},
                prior_composite=75.0, current_composite=74.0,
                position_pct=4.0, max_full_pct=5.0,
                debt_to_fcf=2.0, p_fcf=25.0, fcf_growth_pct=20.0,
                implied_5yr_return=0.12)
    base.update(kw)
    return HoldingState(**base)


def rules_of(triggers):
    return {t.rule for t in triggers}


class TestFundamentalTriggers:
    def test_clean_holding_no_triggers(self):
        assert fundamental_triggers(make_holding()) == []

    def test_rule21_two_quarters_at_1_via_streak(self):
        h = make_holding(consecutive_quarters_fcf_yield_at_1=2)
        assert "Rule 21" in rules_of(fundamental_triggers(h))
        assert fundamental_triggers(
            make_holding(consecutive_quarters_fcf_yield_at_1=1)) == []

    def test_rule21_via_prior_and_current_scores(self):
        h = make_holding(
            prior_scores={"fcf_yield_trend": 1, "competitive_moat": 4},
            current_scores={"fcf_yield_trend": 1, "competitive_moat": 4})
        assert "Rule 21" in rules_of(fundamental_triggers(h))

    def test_rule22_moat_durable_to_absent(self):
        h = make_holding(
            prior_scores={"competitive_moat": 4},
            current_scores={"competitive_moat": 2})
        assert "Rule 22" in rules_of(fundamental_triggers(h))
        # 3 -> 2 was never "durable"; 4 -> 3 is not "absent"
        for prior, cur in ((3, 2), (4, 3)):
            h = make_holding(prior_scores={"competitive_moat": prior},
                             current_scores={"competitive_moat": cur})
            assert "Rule 22" not in rules_of(fundamental_triggers(h))

    def test_rule23_roic_below_wacc_flag(self):
        h = make_holding(roic_below_wacc_two_years=True)
        assert "Rule 23" in rules_of(fundamental_triggers(h))

    def test_rule24_management_at_1(self):
        h = make_holding(current_scores={"management_quality": 1})
        assert "Rule 24" in rules_of(fundamental_triggers(h))

    def test_rule25_cyclical_rationale_defuses(self):
        h = make_holding(revenue_negative_two_quarters=True)
        assert "Rule 25" in rules_of(fundamental_triggers(h))
        h2 = make_holding(revenue_negative_two_quarters=True,
                          cyclical_rationale_documented=True)
        assert fundamental_triggers(h2) == []

    def test_triggers_carry_30_day_deadline(self):
        h = make_holding(roic_below_wacc_two_years=True)
        assert all(t.act_within_days == 30 for t in fundamental_triggers(h))


class TestValuationTriggers:
    def test_rule26_debt_needs_no_deleveraging_path(self):
        h = make_holding(debt_to_fcf=6.5)
        assert "Rule 26" in rules_of(valuation_triggers(h))
        h2 = make_holding(debt_to_fcf=6.5, deleveraging_path_documented=True)
        assert "Rule 26" not in rules_of(valuation_triggers(h2))
        assert valuation_triggers(make_holding(debt_to_fcf=5.9)) == []

    def test_rule27_pfcf_over_50_with_slow_growth(self):
        h = make_holding(p_fcf=55.0, fcf_growth_pct=10.0)
        assert "Rule 27" in rules_of(valuation_triggers(h))
        h2 = make_holding(p_fcf=55.0, fcf_growth_pct=20.0)
        assert "Rule 27" not in rules_of(valuation_triggers(h2))

    def test_rule28_implied_return_below_7pct(self):
        h = make_holding(implied_5yr_return=0.05)
        assert "Rule 28" in rules_of(valuation_triggers(h))
        assert valuation_triggers(
            make_holding(implied_5yr_return=0.08)) == []

    def test_rule29_appreciation_above_max_full(self):
        h = make_holding(position_pct=6.5, max_full_pct=5.0)
        assert "Rule 29" in rules_of(valuation_triggers(h))


class TestBandTransition:
    def test_strong_buy_is_core_hold(self):
        a = band_transition(78.0, 82.0, TODAY)
        assert a.status == "Core Hold / Add"
        assert a.deadline is None

    def test_rule30_strong_buy_to_buy_trims_within_30_days(self):
        a = band_transition(85.0, 70.0, TODAY)
        assert a.status == "Trim to Buy Maximum"
        assert a.rule == "Rule 30"
        assert a.deadline == TODAY + timedelta(days=30)

    def test_buy_to_buy_is_plain_hold(self):
        a = band_transition(70.0, 72.0, TODAY)
        assert a.status == "Hold"
        assert a.deadline is None

    def test_watch_list_probationary_hold_90_day_deadline(self):
        entered = date(2026, 7, 1)
        a = band_transition(70.0, 55.0, TODAY, band_entered_date=entered)
        assert a.status == "Probationary Hold"
        assert "no additions" in a.action
        assert a.deadline == entered + timedelta(days=90)

    def test_watch_list_defaults_deadline_from_today(self):
        a = band_transition(70.0, 55.0, TODAY)
        assert a.deadline == TODAY + timedelta(days=90)

    def test_rule31_exit_review_without_plan_sells_in_30(self):
        a = band_transition(60.0, 40.0, TODAY)
        assert a.status == "Exit Review"
        assert a.rule == "Rule 31"
        assert a.deadline == TODAY + timedelta(days=30)

    def test_rule31_approved_plan_gets_90_days(self):
        entered = TODAY - timedelta(days=10)
        a = band_transition(60.0, 40.0, TODAY, band_entered_date=entered,
                            remediation_plan_approved=True)
        assert a.status == "Exit Review"
        assert a.deadline == entered + timedelta(days=90)

    def test_rule32_no_extension_past_remediation_deadline(self):
        entered = TODAY - timedelta(days=100)
        a = band_transition(60.0, 40.0, TODAY, band_entered_date=entered,
                            remediation_plan_approved=True)
        assert a.status == "Mandatory Sell"
        assert a.rule == "Rule 32"
        assert a.deadline == TODAY + timedelta(days=30)

    def test_rule33_below_35_mandatory_sell(self):
        a = band_transition(55.0, 30.0, TODAY)
        assert a.status == "Mandatory Sell"
        assert a.rule == "Rule 33"
        assert a.deadline == TODAY + timedelta(days=30)


class TestHoldThrough:
    def test_market_selloff_alone_holds(self):
        assert hold_through(make_holding(), market_selloff=True)

    def test_each_condition_alone_holds(self):
        h = make_holding()
        assert hold_through(h, one_quarter_miss=True)
        assert hold_through(h, analyst_downgrades=True)
        assert hold_through(h, negative_press=True)
        assert hold_through(h, price_decline_pct=25.0)

    def test_decline_outside_20_30_band_is_not_protection(self):
        h = make_holding()
        assert not hold_through(h, price_decline_pct=35.0)
        assert not hold_through(h, price_decline_pct=15.0)

    def test_no_conditions_is_not_hold_through(self):
        assert not hold_through(make_holding())

    def test_fundamental_trigger_beats_hold_through(self):
        h = make_holding(roic_below_wacc_two_years=True)
        assert not hold_through(h, market_selloff=True,
                                price_decline_pct=25.0)

    def test_valuation_trigger_beats_hold_through(self):
        h = make_holding(implied_5yr_return=0.03)
        assert not hold_through(h, market_selloff=True)


class TestReplacementGate:
    GOOD = dict(existing_score_q1=60.0, existing_score_q2=58.0,
                new_score=80.0, new_passes_entry_and_ceiling=True,
                existing_hold_through=False,
                cash_insufficient_or_cap_breach=True,
                last_replacement_within_12mo=False)

    def test_all_conditions_met_allows(self):
        allowed, failed = replacement_gate(**self.GOOD)
        assert allowed and failed == []

    def test_gap_must_hold_across_both_quarters(self):
        args = dict(self.GOOD, existing_score_q1=70.0)   # gap 10 < 15
        allowed, failed = replacement_gate(**args)
        assert not allowed
        assert any("34(b)" in f for f in failed)

    def test_new_must_clear_entry_checklist(self):
        args = dict(self.GOOD, new_passes_entry_and_ceiling=False)
        assert any("34(a)" in f for f in replacement_gate(**args)[1])

    def test_hold_through_protection_blocks(self):
        args = dict(self.GOOD, existing_hold_through=True)
        assert any("34(c)" in f for f in replacement_gate(**args)[1])

    def test_needs_genuine_capital_constraint(self):
        args = dict(self.GOOD, cash_insufficient_or_cap_breach=False)
        assert any("34(d)" in f for f in replacement_gate(**args)[1])

    def test_needs_documentation_before_execution(self):
        allowed, failed = replacement_gate(
            **self.GOOD, documented_before_execution=False)
        assert not allowed
        assert any("34(e)" in f for f in failed)

    def test_rule35_once_per_12_months(self):
        args = dict(self.GOOD, last_replacement_within_12mo=True)
        assert any("Rule 35" in f for f in replacement_gate(**args)[1])

    def test_multiple_failures_all_reported(self):
        args = dict(self.GOOD, new_passes_entry_and_ceiling=False,
                    existing_hold_through=True,
                    last_replacement_within_12mo=True)
        allowed, failed = replacement_gate(**args)
        assert not allowed
        assert len(failed) == 3
