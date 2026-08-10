"""Phase 6 — the Master Rule Register test matrix.

Mirrors the doc's INDEX (Master Rule Register): every numbered Hard Rule
1-49 is mapped to its implementation (or explicitly declared procedural),
and the register test fails if any rule is unaccounted for. Behavioral
tests here cover the Phase 6 additions (tax Rules 38-42, financial Rules
48-49) and the cross-cutting properties: conflict-resolution ordering
(Part 10) and the Part 12 approval boundary.
"""

import datetime as dt

import pytest

# --------------------------------------------------------------------------- #
# The register: rule -> implementing callable(s) or "procedural"
# --------------------------------------------------------------------------- #

def _register():
    from landry import (ai_analyst, approvals, daily, data_auto, drawdown,
                        entry, financials, implied_return, monitor, scoring,
                        sizing, tax)
    P = "procedural"          # governance/documentation — surfaced, not coded
    return {
        1: scoring.rule_flags, 2: scoring.rule_flags, 3: scoring.rule_flags,
        4: scoring.rule_flags,
        5: entry.entry_checklist, 6: entry.entry_checklist,
        7: entry.entry_checklist, 8: entry.entry_checklist,
        9: entry.entry_checklist, 10: entry.entry_checklist,
        11: (entry.entry_checklist, implied_return.check_scenarios),
        12: (entry.entry_checklist, implied_return.check_scenarios),
        13: entry.entry_checklist,
        14: sizing.position_size, 15: sizing.portfolio_constraints,
        16: sizing.portfolio_constraints, 17: sizing.portfolio_constraints,
        18: sizing.position_size,
        19: scoring.composite_score, 20: data_auto.beta_size_reduction,
        21: monitor.fundamental_triggers, 22: monitor.fundamental_triggers,
        23: monitor.fundamental_triggers, 24: monitor.fundamental_triggers,
        25: monitor.fundamental_triggers,
        26: monitor.valuation_triggers, 27: monitor.valuation_triggers,
        28: monitor.valuation_triggers, 29: monitor.valuation_triggers,
        30: monitor.band_transition, 31: monitor.band_transition,
        32: monitor.band_transition, 33: monitor.band_transition,
        34: monitor.replacement_gate, 35: monitor.replacement_gate,
        36: (data_auto.correlation_report, data_auto.cluster_exposure,
             data_auto.expands_flagged_cluster),
        37: drawdown.track_regime,
        38: tax.execution_timing, 39: tax.execution_timing,
        40: tax.select_lots, 41: tax.harvest_gate,
        42: tax.RULE_42_NOTE,          # structural: no trade-originating API
        43: daily.build_action_items, 44: daily.build_action_items,
        45: P,                          # record sell reason after every exit
        46: daily.build_action_items,   # >20% loss -> post-mortem action
        47: P,                          # 2-year weight review cadence
        48: financials.rule48_cap, 49: financials.rule49_valuation_ceiling,
    }


def test_every_hard_rule_is_accounted_for():
    reg = _register()
    assert sorted(reg) == list(range(1, 50))
    procedural = [n for n, v in reg.items() if v == "procedural"]
    assert procedural == [45, 47]      # consciously manual, nothing else
    for n, v in reg.items():
        if v == "procedural":
            continue
        impls = v if isinstance(v, tuple) else (v,)
        for impl in impls:
            assert callable(impl) or isinstance(impl, str), n


# --------------------------------------------------------------------------- #
# Rules 38-39 — execution timing
# --------------------------------------------------------------------------- #

TODAY = dt.date(2026, 8, 10)


def _lot(days_held, lot_id="L1", qty=100, basis=100.0):
    from landry.tax import Lot
    return Lot(lot_id, qty, basis, TODAY - dt.timedelta(days=days_held))


def test_rule38_mandated_sell_never_delays():
    from landry.tax import (FUNDAMENTAL_SELL, HARD_CAP_TRIM, execution_timing)
    lot = _lot(360)                     # 6 days from long-term
    for reason in (FUNDAMENTAL_SELL, HARD_CAP_TRIM):
        d = execution_timing(reason, lot, TODAY)
        assert not d.may_delay and d.rule == "Rule 38"


def test_rule39_discretionary_delay_window():
    from landry.tax import DISCRETIONARY_TRIM, execution_timing
    # 6 days from long-term: delay permitted
    d = execution_timing(DISCRETIONARY_TRIM, _lot(360), TODAY)
    assert d.may_delay and d.max_delay_days == 6
    # already long-term: nothing to gain
    assert not execution_timing(DISCRETIONARY_TRIM, _lot(400), TODAY).may_delay
    # 60 days out: beyond the 30-day window
    assert not execution_timing(DISCRETIONARY_TRIM, _lot(306), TODAY).may_delay
    # delay would violate a hard rule
    d = execution_timing(DISCRETIONARY_TRIM, _lot(360), TODAY,
                         delay_violates_hard_rule=True)
    assert not d.may_delay


def test_rule40_lot_selection_prefers_losses_then_long_term():
    from landry.tax import select_lots
    lots = [_lot(30, "st_gain", 50, basis=80.0),    # short-term gain
            _lot(400, "lt_gain", 50, basis=80.0),   # long-term gain
            _lot(400, "loss", 50, basis=120.0)]     # loss
    sel = select_lots(lots, 100, price=100.0, asof=TODAY)
    picked = [lid for lid, _ in sel.lots]
    assert picked[0] == "loss"
    assert picked[1] == "lt_gain"       # 20% rate beats 37% rate
    # tax: loss -20*0.20*50 = -200; lt gain +20*0.20*50 = +200 -> net 0
    assert sel.estimated_tax == pytest.approx(0.0)
    assert "broker" in sel.note


def test_rule40_carryforward_offsets_gains_only():
    from landry.tax import select_lots
    lots = [_lot(400, "g", 100, basis=80.0)]
    sel = select_lots(lots, 100, price=100.0, asof=TODAY,
                      loss_carryforward=150.0)
    assert sel.estimated_tax == pytest.approx(20 * 0.20 * 100 - 150.0)


def test_rule41_harvest_gate():
    from landry.tax import harvest_gate
    ok = harvest_gate("Exit Review", True, False, True)
    assert ok.allowed
    wash = harvest_gate("Exit Review", True, True, True)
    assert not wash.allowed and any("wash" in f for f in wash.failures)
    uncoord = harvest_gate("Probationary Hold", False, False, False)
    assert not uncoord.allowed
    healthy = harvest_gate("Core Hold", False, False, True)
    assert not healthy.allowed          # Rule 42: no loss, no watch status


def test_rule42_no_trade_originating_api():
    """Structural: the tax module must expose no callable that produces a
    trade decision without a justification argument."""
    from landry import tax
    assert isinstance(tax.RULE_42_NOTE, str)
    # execution_timing demands a trade_reason and rejects unknown ones
    with pytest.raises(ValueError):
        tax.execution_timing("tax_optimization", _lot(360), TODAY)


# --------------------------------------------------------------------------- #
# Rules 48-49 — financial-sector caps
# --------------------------------------------------------------------------- #

def test_rule48_thresholds_and_unknown_data():
    from landry.financials import rule48_cap
    assert not rule48_cap("bank", cet1=0.11).triggered
    assert rule48_cap("bank", cet1=0.08).triggered
    assert rule48_cap("bank").triggered              # unknown -> cap
    assert rule48_cap("insurer", rbc=3.0).triggered
    assert not rule48_cap("insurer", rbc=4.0).triggered
    assert rule48_cap("broker", net_leverage=3.5).triggered
    assert rule48_cap("asset_manager", debt_ebitda=3.5).triggered
    with pytest.raises(ValueError):
        rule48_cap("utility")


def test_rule48_feeds_composite_cap():
    from landry.financials import rule48_cap
    from landry.scoring import IndicatorScore, composite_score
    scores = {k: IndicatorScore(5, "H") for k in (
        "fcf_yield_trend", "revenue_growth_consistency", "competitive_moat",
        "revenue_visibility", "fcf_margin_trend", "management_quality",
        "roic_vs_wacc", "relative_strength", "valuation_multiples",
        "technical_trend", "analyst_consensus", "volume_accumulation")}
    cap = rule48_cap("bank", cet1=0.07)
    assert composite_score(scores,
                           financial_cap_triggered=cap.triggered) == 65.0


def test_rule49_ceiling_and_exception():
    from landry.financials import rule49_valuation_ceiling as r49
    assert r49("bank", p_tbv=2.0).ok
    assert not r49("bank", p_tbv=3.0).ok
    # exception needs BOTH conditions
    assert not r49("bank", p_tbv=3.0, roe_growth_justified=True).ok
    assert r49("bank", p_tbv=3.0, roe_growth_justified=True,
               bear_case_clears=True).ok
    assert not r49("insurer", p_b=1.5, p_e=22.0).ok   # either metric over
    assert r49("insurer", p_b=1.5, p_e=15.0).ok
    assert not r49("broker").ok                        # unknown -> not ok


# --------------------------------------------------------------------------- #
# Property: Part 10 conflict-resolution ordering
# --------------------------------------------------------------------------- #

def test_ordering_fundamental_trigger_beats_hold_through():
    from landry.monitor import HoldingState, hold_through
    h = HoldingState(ticker="X", prior_scores={"competitive_moat": 4},
                     current_scores={"competitive_moat": 2},
                     prior_composite=80.0, current_composite=70.0,
                     position_pct=4.0, max_full_pct=8.0)
    # moat degradation fires Rule 22 — hold-through protection must lose
    assert hold_through(h, market_selloff=True,
                        price_decline_pct=-0.25) is False


def test_ordering_drawdown_gate_beats_conviction():
    from landry.sizing import position_size
    for level in (2, 3):
        r = position_size(95.0, drawdown_level=level)
        assert r.staged_initial_pct == 0.0
    r1 = position_size(75.0, drawdown_level=1)     # Elevated: SB only
    assert r1.staged_initial_pct == 0.0
    assert position_size(85.0, drawdown_level=1).staged_initial_pct > 0


def test_ordering_rule3_avoid_beats_any_composite():
    from landry.scoring import IndicatorScore, classify, rule_flags
    t1 = {"fcf_yield_trend": IndicatorScore(2, "H"),
          "revenue_growth_consistency": IndicatorScore(2, "H"),
          "competitive_moat": IndicatorScore(5, "H"),
          "revenue_visibility": IndicatorScore(5, "H"),
          "fcf_margin_trend": IndicatorScore(5, "H")}
    flags = rule_flags(t1)
    for score in (35.0, 66.0, 99.9):
        assert classify(score, flags) == "AVOID"


def test_ordering_rule19_cap_beats_perfect_scores():
    from landry.scoring import IndicatorScore, composite_score
    scores = {k: IndicatorScore(5, "H") for k in (
        "fcf_yield_trend", "revenue_growth_consistency", "competitive_moat",
        "revenue_visibility", "fcf_margin_trend", "management_quality",
        "roic_vs_wacc", "relative_strength", "valuation_multiples",
        "technical_trend", "analyst_consensus", "volume_accumulation")}
    for d in (5.01, 8.0, 50.0):
        assert composite_score(scores, debt_to_fcf=d) == 65.0


def test_ordering_tax_never_blocks_mandated_sell():
    """Rank 1 (fundamental sell) vs rank 7 (tax optimization): even a lot
    one day from long-term status cannot delay a Rule 21-25 sell."""
    from landry.tax import FUNDAMENTAL_SELL, execution_timing
    one_day_out = _lot(365)
    assert one_day_out.days_to_long_term(TODAY) == 1
    assert not execution_timing(FUNDAMENTAL_SELL, one_day_out, TODAY).may_delay


# --------------------------------------------------------------------------- #
# Property: Part 12 approval boundary
# --------------------------------------------------------------------------- #

def test_no_unapproved_score_reaches_a_composite(tmp_path):
    """Fuzz the store with propose/reject sequences: approved_scores must
    only ever contain explicitly approved indicators, and score_stock must
    refuse an incomplete set rather than fill gaps."""
    import random

    from landry.approvals import ScoreStore
    from landry.fundamentals import Draft
    from landry.scoring import TIER1_WEIGHTS, score_stock

    rng = random.Random(42)
    inds = list(TIER1_WEIGHTS)
    store = ScoreStore(str(tmp_path / "s.json"))
    approved = set()
    for i in range(200):
        ind = rng.choice(inds)
        op = rng.random()
        if op < 0.5:
            store.propose("X", Draft(ind, rng.randint(1, 5), "M", f"fuzz {i}"))
        elif op < 0.75 and store.pending("X").get(ind):
            store.approve("X", ind, approved_by="fuzz")
            approved.add(ind)
        elif store.pending("X").get(ind):
            store.reject("X", ind, approved_by="fuzz", reason="fuzz")
    got = set(store.approved_scores("X"))
    assert got == approved
    if got < set(inds):                  # incomplete Tier 1 -> refuse
        with pytest.raises(ValueError):
            score_stock("X", store.approved_scores("X"))


def test_judgment_indicators_never_in_quant_drafts():
    from landry.fundamentals import (FundamentalInputs, compute_metrics,
                                     draft_quant_scores)
    inp = FundamentalInputs("X", market_cap=1e9,
                            revenue=[1, 2, 3, 4, 5], cfo=[1, 2, 3, 4, 5],
                            capex=[0] * 5, sbc=[0] * 5,
                            total_debt=0, cash=0)
    drafts = draft_quant_scores(compute_metrics(inp))
    forbidden = {"competitive_moat", "management_quality",
                 "revenue_visibility"}
    assert not forbidden & set(drafts)
