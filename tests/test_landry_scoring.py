"""Pin the landry v1.0 engine to LANDRY_SYSTEM_WORKBOOK_25.xlsx.

Two layers:
1. Hard-coded fixtures transcribed from the workbook Scoring tab
   (2026-08-06/07 scoring session, 16 names) -- run everywhere, no
   dependencies beyond the engine. Originally transcribed from
   LANDRY_SYSTEM_WORKBOOK_25.xlsx; WORKBOOK_25.xlsx supersedes it with
   identical worked values for all 16 of these tickers (verified) plus
   later-screened candidates not covered by these fixtures.
2. A live round-trip against the workbook file itself (skipped if
   openpyxl or the file is absent) -- catches transcription drift.
"""

import os

import pytest

from landry.scoring import (
    ALL_WEIGHTS,
    TIER1_WEIGHTS,
    TIER1_TOTAL_WEIGHT,
    TIER2_TOTAL_WEIGHT,
    TIER3_TOTAL_WEIGHT,
    IndicatorScore,
    RuleFlags,
    classify,
    composite_score,
    decision_formula,
    rule_flags,
    score_stock,
)

S = IndicatorScore  # shorthand

_ORDER = [
    "fcf_yield_trend", "revenue_growth_consistency", "competitive_moat",
    "revenue_visibility", "fcf_margin_trend",
    "management_quality", "roic_vs_wacc", "relative_strength",
    "valuation_multiples",
    "technical_trend", "analyst_consensus", "volume_accumulation",
]


def mk(pairs):
    """Build a scores dict from [(score, conf), ...] in _ORDER order."""
    return {k: S(sc, cf) for k, (sc, cf) in zip(_ORDER, pairs)}


# ticker -> (pairs, expected_tier1_avg, expected_composite, expected_decision,
#            expected flags (r1, r2, r3, r4))
# Transcribed from the Scoring tab, LANDRY_SYSTEM_WORKBOOK_25.xlsx.
WORKBOOK_25 = {
    "MU":   ([(3,"M"),(3,"M"),(4,"H"),(5,"H"),(4,"M"),
              (4,"M"),(5,"M"),(5,"H"),(1,"H"),
              (5,"H"),(3,"M"),(3,"L")],
             3.642857142857143, 71.8, "BUY", ("OK","OK","OK","OK")),
    "AVGO": ([(3,"M"),(4,"H"),(5,"H"),(4,"M"),(5,"H"),
              (4,"H"),(4,"L"),(4,"M"),(4,"M"),
              (3,"M"),(5,"H"),(3,"M")],
             4.071428571428572, 80.8, "STRONG BUY", ("OK","OK","OK","OK")),
    "PLTR": ([(2,"H"),(5,"H"),(5,"M"),(4,"M"),(5,"H"),
              (3,"M"),(5,"H"),(3,"M"),(2,"M"),
              (3,"L"),(4,"M"),(3,"L")],
             4.0, 75.2, "BUY", ("OK","OK","OK","OK")),
    "NVDA": ([(4,"H"),(5,"H"),(5,"H"),(4,"H"),(5,"H"),
              (5,"H"),(5,"H"),(5,"H"),(5,"M"),
              (4,"M"),(5,"M"),(3,"L")],
             4.571428571428571, 93.2, "STRONG BUY", ("OK","OK","OK","OK")),
    "VRTX": ([(4,"H"),(3,"M"),(4,"M"),(4,"M"),(4,"H"),
              (3,"M"),(4,"M"),(4,"M"),(4,"M"),
              (4,"M"),(5,"H"),(3,"L")],
             3.785714285714286, 75.6, "BUY", ("OK","OK","OK","OK")),
    "PLD":  ([(4,"M"),(4,"M"),(5,"H"),(4,"M"),(5,"H"),
              (5,"H"),(3,"L"),(4,"L"),(2,"H"),
              (4,"L"),(4,"M"),(3,"L")],
             4.357142857142857, 82.0, "STRONG BUY", ("OK","OK","OK","OK")),
    "ADBE": ([(5,"H"),(4,"H"),(4,"M"),(5,"H"),(5,"H"),
              (3,"M"),(5,"H"),(1,"H"),(5,"H"),
              (2,"M"),(3,"M"),(2,"L")],
             4.571428571428572, 85.8, "STRONG BUY", ("OK","OK","OK","OK")),
    "ANET": ([(4,"M"),(5,"H"),(5,"H"),(4,"M"),(5,"H"),
              (4,"H"),(5,"H"),(5,"M"),(1,"H"),
              (4,"M"),(5,"H"),(2,"M")],
             4.571428571428571, 85.0, "STRONG BUY", ("OK","OK","OK","OK")),
    "ASML": ([(3,"M"),(3,"M"),(5,"H"),(4,"M"),(3,"M"),
              (4,"M"),(5,"H"),(5,"H"),(2,"M"),
              (5,"M"),(5,"H"),(3,"L")],
             3.5714285714285716, 73.2, "BUY", ("OK","OK","OK","OK")),
    "CRWD": ([(3,"M"),(4,"M"),(4,"M"),(5,"H"),(5,"H"),
              (3,"M"),(2,"M"),(4,"H"),(1,"H"),
              (4,"M"),(3,"M"),(2,"M")],
             4.0, 70.4, "BUY", ("OK","OK","OK","OK")),
    "KLAC": ([(2,"M"),(3,"M"),(5,"H"),(3,"M"),(5,"H"),
              (4,"H"),(5,"H"),(3,"M"),(2,"M"),
              (2,"M"),(5,"H"),(2,"L")],
             3.4285714285714293, 68.6, "BUY", ("OK","OK","OK","OK")),
    "TSM":  ([(3,"M"),(5,"H"),(5,"H"),(4,"M"),(3,"M"),
              (4,"H"),(5,"H"),(4,"M"),(4,"M"),
              (3,"M"),(5,"H"),(3,"L")],
             4.0, 81.0, "STRONG BUY", ("OK","OK","OK","OK")),
    "VRT":  ([(4,"H"),(5,"H"),(4,"M"),(4,"M"),(5,"H"),
              (4,"M"),(5,"H"),(4,"M"),(2,"M"),
              (4,"M"),(4,"M"),(3,"L")],
             4.357142857142857, 82.8, "STRONG BUY", ("OK","OK","OK","OK")),
}

# Tier-1-gate failures: scoring stopped at Tier 1 (Tier 2/3 blank in workbook).
WORKBOOK_25_REJECTED = {
    "SFM":  ([(2,"M"),(2,"M"),(2,"M"),(2,"M"),(1,"H")],
             1.8571428571428574, ("FAIL","REVIEW","AVOID","OK")),
    "ONON": ([(2,"M"),(4,"H"),(3,"M"),(2,"M"),(2,"H")],
             2.642857142857143, ("FAIL","OK","AVOID","OK")),
    "TSLA": ([(1,"H"),(2,"M"),(3,"M"),(3,"M"),(1,"H")],
             1.9285714285714288, ("FAIL","REVIEW","AVOID","OK")),
}


# --------------------------------------------------------------------------- #
# Weights are the v1.0 set
# --------------------------------------------------------------------------- #

def test_weights_sum_to_tiers():
    assert sum(ALL_WEIGHTS.values()) == pytest.approx(1.0)
    assert TIER1_TOTAL_WEIGHT == pytest.approx(0.70)
    assert TIER2_TOTAL_WEIGHT == pytest.approx(0.25)
    assert TIER3_TOTAL_WEIGHT == pytest.approx(0.05)


def test_v1_weights_differ_from_v7():
    # v1.0 moved valuation 6% -> 8% and technical 4% -> 2% vs v7
    assert ALL_WEIGHTS["valuation_multiples"] == pytest.approx(0.08)
    assert ALL_WEIGHTS["technical_trend"] == pytest.approx(0.02)


# --------------------------------------------------------------------------- #
# Pinned to Workbook 25
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ticker", sorted(WORKBOOK_25))
def test_workbook_scored_names(ticker):
    pairs, t1avg, comp, dec, flags = WORKBOOK_25[ticker]
    card = score_stock(ticker, mk(pairs))
    assert card.tier1_weighted_average == pytest.approx(t1avg)
    assert card.composite == pytest.approx(comp)
    assert card.decision == dec
    assert (card.flags.rule1, card.flags.rule2,
            card.flags.rule3, card.flags.rule4) == flags


@pytest.mark.parametrize("ticker", sorted(WORKBOOK_25_REJECTED))
def test_workbook_rejected_names(ticker):
    pairs, t1avg, flags = WORKBOOK_25_REJECTED[ticker]
    scores = {k: S(sc, cf) for k, (sc, cf) in zip(_ORDER[:5], pairs)}
    card = score_stock(ticker, scores)
    assert card.tier1_weighted_average == pytest.approx(t1avg)
    assert card.composite is None          # workbook leaves it blank
    assert card.tier2_contribution is None  # Tier 2/3 never scored
    assert (card.flags.rule1, card.flags.rule2,
            card.flags.rule3, card.flags.rule4) == flags
    assert card.decision == "AVOID"        # all three trip Rule 3


# --------------------------------------------------------------------------- #
# Rules 1-4 unit behavior
# --------------------------------------------------------------------------- #

def _uniform(t1=4, conf="H"):
    return {k: S(t1, conf) for k in _ORDER[:5]}


def test_rule1_floor_blocks_buy_but_not_avoid():
    # Tier 1 avg exactly 3.0 passes; just below fails
    ok = rule_flags(_uniform(3))
    assert ok.rule1 == "OK"
    # 3,3,3,3,2 -> avg = (0.6+.45+.45+.3+.2)/0.7 = 2.857 -> FAIL, only one <=2
    scores = mk([(3,"H")]*4 + [(2,"H")] + [(3,"H")]*7)
    flags = rule_flags(scores)
    assert flags.rule1 == "FAIL" and flags.rule3 == "OK"
    # capped at WATCH LIST even with a high composite
    assert classify(70.0, flags) == "WATCH LIST"


def test_rule2_any_tier1_at_1_flags_review():
    scores = mk([(1,"H")] + [(5,"H")]*11)
    assert rule_flags(scores).rule2 == "REVIEW"


def test_rule3_two_at_or_below_2_is_avoid_regardless_of_score():
    scores = mk([(2,"H"),(2,"H")] + [(5,"H")]*10)
    flags = rule_flags(scores)
    assert flags.rule3 == "AVOID"
    assert classify(85.0, flags) == "AVOID"


def test_rule4_two_low_confidence_caps_strong_buy_at_buy():
    scores = mk([(5,"L"),(5,"L")] + [(5,"H")]*10)
    flags = rule_flags(scores)
    assert flags.rule4 == "CAP AT BUY"
    assert classify(95.0, flags) == "BUY"
    # ...but does not lift a BUY or lower band
    assert classify(70.0, flags) == "BUY"
    assert classify(55.0, flags) == "WATCH LIST"


def test_decision_bands():
    ok = rule_flags(_uniform(5))
    for score, want in [(100, "STRONG BUY"), (80, "STRONG BUY"),
                        (79.9, "BUY"), (65, "BUY"), (64.9, "WATCH LIST"),
                        (50, "WATCH LIST"), (49.9, "AVOID"), (35, "AVOID"),
                        (34.9, "PASS")]:
        assert classify(score, ok) == want, score


def test_unadapted_sector_blocks_strong_buy():
    ok = rule_flags(_uniform(5))
    assert classify(90.0, ok, unadapted_sector=True) == "BUY"


# --------------------------------------------------------------------------- #
# Leverage caps (Rules 19 / 48) feed the composite
# --------------------------------------------------------------------------- #

def _all5():
    return {k: S(5, "H") for k in _ORDER}


def test_rule19_leverage_cap():
    assert composite_score(_all5()) == pytest.approx(100.0)
    assert composite_score(_all5(), debt_to_fcf=5.0) == pytest.approx(100.0)
    assert composite_score(_all5(), debt_to_fcf=5.1) == pytest.approx(65.0)
    card = score_stock("X", _all5(), debt_to_fcf=6.0)
    assert card.composite == pytest.approx(65.0)
    assert card.decision == "BUY"  # cap prevents Strong Buy


def test_rule48_financial_cap():
    assert composite_score(_all5(),
                           financial_cap_triggered=True) == pytest.approx(65.0)


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #

def test_score_and_confidence_validation():
    with pytest.raises(ValueError):
        S(0, "H")
    with pytest.raises(ValueError):
        S(3, "X")
    with pytest.raises(ValueError):
        score_stock("X", {})  # missing Tier 1
    with pytest.raises(ValueError):
        # gate passes but Tier 2/3 missing -> must raise
        score_stock("X", _uniform(4))


# --------------------------------------------------------------------------- #
# Live round-trip against the workbook file
# --------------------------------------------------------------------------- #

from landry.xlsx_io import latest_workbook  # noqa: E402

_WB = latest_workbook(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@pytest.mark.skipif(not _WB, reason="no workbook file present")
def test_roundtrip_against_workbook_file():
    openpyxl = pytest.importorskip("openpyxl")  # noqa: F841
    from landry.xlsx_io import read_scoring_tab

    rows = read_scoring_tab(_WB)  # highest-numbered workbook (excludes TEMPLATE_FINAL etc.)
    assert len(rows) >= 16
    checked = 0
    for row in rows:
        # A candidate with Tier 1 only part-scored (the Darryl-list screening leaves several like that) cannot be
        # evaluated by the engine at all; the workbook's own composite must be blank for it.
        if not set(TIER1_WEIGHTS) <= set(row.scores):
            assert row.composite is None, row.ticker
            continue
        # Skip candidates still mid-scoring: Tier 1 gate passed but Tier 2/3
        # not yet entered in the workbook (score_stock requires all 12 once
        # the gate passes; a gate-fail/auto-avoid row needs only Tier 1 and
        # is still fully checkable).
        if (rule_flags(row.scores).tier1_passes
                and not set(ALL_WEIGHTS) <= set(row.scores)):
            assert row.composite is None, row.ticker      # the composite formula waits for all 12 indicators
            continue
        card = score_stock(row.ticker, row.scores)
        if row.tier1_weighted_average is not None:
            assert card.tier1_weighted_average == pytest.approx(
                row.tier1_weighted_average), row.ticker
        if row.composite is not None:
            # The engine stops at a failed Tier 1 gate (composite empty, decision AVOID) while the workbook still
            # prints the composite -- AVGO, VRTX and CRWD: FCF yield and FCF margin both 2, Rule 3. So the
            # arithmetic is checked against composite_score, and the DECISION must agree: since 2026-10-04 (Alan:
            # apply Rule 3 as written) the Scoring tab's Decision column applies the gates too.
            expected = card.composite if card.composite is not None else composite_score(row.scores)
            assert expected == pytest.approx(row.composite), row.ticker
            assert card.decision == row.decision, row.ticker
        assert (card.flags.rule1, card.flags.rule2, card.flags.rule3,
                card.flags.rule4) == row.rule_flags, row.ticker
        checked += 1
    assert checked >= 16


@pytest.mark.skipif(not _WB, reason="no workbook file present")
def test_every_scoring_row_uses_the_engines_decision_formula():
    """A pasted-over or copied-down formula from before 2026-10-04 (a bare composite band) would silently stop
    applying the Hard Rule gates; every Decision cell must be exactly landry.scoring.decision_formula(row)."""
    openpyxl = pytest.importorskip("openpyxl")
    ws = openpyxl.load_workbook(_WB)["Scoring"]
    formula_rows = [r for r in range(3, ws.max_row + 1)
                    if isinstance(ws.cell(row=r, column=33).value, str) and ws.cell(row=r, column=33).value.startswith("=")]
    assert len(formula_rows) >= 50
    wrong = [r for r in formula_rows if ws.cell(row=r, column=33).value != decision_formula(r)]
    assert wrong == [], f"Scoring!AG differs from decision_formula on rows {wrong}"


def _split_args(s):
    parts, depth, cur, in_str = [], 0, "", False
    for ch in s:
        if ch == '"':
            in_str = not in_str
        if not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
                continue
        cur += ch
    parts.append(cur)
    return parts


def _eval_cell(expr, env):
    """Just enough of Excel for decision_formula: nested IF(...), "text", and ref="text" / ref>=number tests."""
    import re
    expr = expr.strip()
    if expr.startswith("IF(") and expr.endswith(")"):
        cond, yes, no = _split_args(expr[3:-1])
        return _eval_cell(yes if _eval_cond(cond.strip(), env) else no, env)
    assert expr.startswith('"') and expr.endswith('"'), expr
    return expr[1:-1]


def _eval_cond(cond, env):
    import re
    m = re.fullmatch(r'([A-Z]+)\d+(=|>=)(.+)', cond)
    assert m, cond
    col, op, rhs = m.groups()
    val = env[col]
    if op == "=":
        return (val if val is not None else "") == rhs.strip('"')
    return val is not None and val != "" and val >= float(rhs)


@pytest.mark.parametrize("score", [None, 20.0, 34.9, 35.0, 49.9, 50.0, 64.9, 65.0, 79.9, 80.0, 95.0])
@pytest.mark.parametrize("rule1", ["OK", "FAIL"])
@pytest.mark.parametrize("rule3", ["OK", "AVOID"])
@pytest.mark.parametrize("rule4", ["OK", "CAP AT BUY"])
def test_decision_formula_agrees_with_classify(score, rule1, rule3, rule4):
    formula = decision_formula(7)
    assert formula.count("(") == formula.count(")") and formula.startswith("=IF(AF7")
    got = _eval_cell(formula[1:], {"AF": score, "AH": rule1, "AJ": rule3, "AK": rule4})
    if score is None:
        assert got == ""                                   # no composite yet: the cell stays blank
    else:
        assert got == classify(score, RuleFlags(rule1, "OK", rule3, rule4))


def rule_flags_ok():
    return RuleFlags("OK", "OK", "OK", "OK")


def test_a_failed_tier1_gate_is_avoid_whatever_the_composite_says():
    """FCF yield 2 + FCF margin 2 (two Tier 1 indicators <= 2) with every other indicator strong: the composite alone
    would read Buy or better, but Part 3's Rule 3 classifies the stock Avoid -- in the engine (decision AVOID, composite
    left empty) and, since 2026-10-04, in the Scoring tab's Decision column."""
    scores = {k: S(5, "H") for k in _ORDER}
    scores["fcf_yield_trend"] = S(2, "M")
    scores["fcf_margin_trend"] = S(2, "M")
    card = score_stock("GAP", scores)
    assert card.flags.rule3 == "AVOID" and card.decision == "AVOID" and card.composite is None
    plain = composite_score(scores)
    assert classify(plain, rule_flags_ok()) in ("BUY", "STRONG BUY")        # what a band-only lookup would say
    assert classify(plain, card.flags) == "AVOID"
