"""
v7_scoring.py — programmatic implementation of the CLAUDE TRADING SYSTEM v7
scoring framework (docs/CLAUDE_TRADING_SYSTEM_v7.docx).

This is the *fundamental* stock-selection system, distinct from the layer1–6
technical backtester in this repo. It implements:

  * Part 2/3 — indicator weights, composite score = (sum score×weight) × 20,
    Tier 1 gate and hard rules, decision thresholds
  * Part 4  — entry checklist and base-case implied 5-year return test
  * Part 5  — position sizing (base by tier + modifiers)
  * Part 11 — leverage hard cap, beta sizing overlay, bear-case return test,
    drawdown response bands, correlation cap check

The math is verified against docs/PART8_Test_Run_Record_7-6-26.docx in
tests/test_v7_scoring.py.

All indicator inputs are 1–5 analyst scores; this module does not fetch
fundamentals. It makes the arithmetic and the hard rules auditable and
repeatable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Part 2 — indicator weights
# --------------------------------------------------------------------------- #

TIER1_WEIGHTS: Dict[str, float] = {
    "fcf_yield_trend": 0.20,
    "revenue_growth_consistency": 0.15,
    "competitive_moat": 0.15,
    "revenue_visibility": 0.10,
    "fcf_margin_trend": 0.10,
}

TIER2_WEIGHTS: Dict[str, float] = {
    "management_quality": 0.08,
    "roic_vs_wacc": 0.06,
    "relative_strength": 0.03,
    "valuation_multiples": 0.06,
}

TIER3_WEIGHTS: Dict[str, float] = {
    "technical_trend": 0.04,
    "analyst_consensus": 0.02,
    "volume_accumulation": 0.01,
}

ALL_WEIGHTS: Dict[str, float] = {**TIER1_WEIGHTS, **TIER2_WEIGHTS, **TIER3_WEIGHTS}

TIER1_TOTAL_WEIGHT = 0.70  # sum of TIER1_WEIGHTS
TIER1_FLOOR = 3.0          # minimum Tier 1 weighted average (Rule 6)
TIER1_RAW_FLOOR = TIER1_FLOOR * TIER1_TOTAL_WEIGHT  # 2.10 raw contribution

# Part 11 — leverage hard cap
LEVERAGE_HARD_CAP_DEBT_FCF = 5.0   # Debt/FCF above this ...
LEVERAGE_SCORE_CAP = 65            # ... caps the composite score here

_VALID_SCORES = (1, 2, 3, 4, 5)


def _check_scores(scores: Dict[str, int], weights: Dict[str, float]) -> None:
    missing = set(weights) - set(scores)
    if missing:
        raise ValueError(f"missing indicator scores: {sorted(missing)}")
    for k in weights:
        if scores[k] not in _VALID_SCORES:
            raise ValueError(f"{k}={scores[k]!r}: scores must be integers 1-5")


def tier_contribution(scores: Dict[str, int], weights: Dict[str, float]) -> float:
    """Raw weighted contribution of one tier: sum(score × weight)."""
    _check_scores(scores, weights)
    return sum(scores[k] * w for k, w in weights.items())


def tier1_weighted_average(scores: Dict[str, int]) -> float:
    """Tier 1 weighted average = (sum Tier 1 score × weight) / 0.70."""
    return tier_contribution(scores, TIER1_WEIGHTS) / TIER1_TOTAL_WEIGHT


def composite_score(scores: Dict[str, int],
                    debt_to_fcf: Optional[float] = None) -> float:
    """Composite Score = (sum of all score × weight) × 20  (Part 3).

    If ``debt_to_fcf`` exceeds the Part 11 leverage hard cap (5x), the score
    is capped at 65 regardless of tier results (Rule: prevents a Strong Buy
    classification on a highly levered balance sheet).
    """
    raw = tier_contribution(scores, ALL_WEIGHTS)
    score = raw * 20.0
    if debt_to_fcf is not None and debt_to_fcf > LEVERAGE_HARD_CAP_DEBT_FCF:
        score = min(score, float(LEVERAGE_SCORE_CAP))
    return score


# --------------------------------------------------------------------------- #
# Part 3 — decision thresholds and hard rules
# --------------------------------------------------------------------------- #

@dataclass
class Tier1Gate:
    """Result of the Tier 1 screen ("How to Use", step 2 + Rules 6-8)."""
    weighted_average: float
    passes_floor: bool                 # Rule 6: weighted average >= 3.0
    indicators_at_1: List[str]         # Rule 7: mandatory qualitative review
    indicators_at_or_below_2: List[str]
    auto_avoid: bool                   # Rule 8: two or more Tier 1 scores <= 2

    @property
    def passes(self) -> bool:
        return self.passes_floor and not self.auto_avoid


def tier1_gate(scores: Dict[str, int]) -> Tier1Gate:
    _check_scores(scores, TIER1_WEIGHTS)
    avg = tier1_weighted_average(scores)
    at1 = [k for k in TIER1_WEIGHTS if scores[k] == 1]
    le2 = [k for k in TIER1_WEIGHTS if scores[k] <= 2]
    return Tier1Gate(
        weighted_average=avg,
        passes_floor=avg >= TIER1_FLOOR,
        indicators_at_1=at1,
        indicators_at_or_below_2=le2,
        auto_avoid=len(le2) >= 2,
    )


def decision(score: float, gate: Optional[Tier1Gate] = None) -> str:
    """Map a composite score to STRONG BUY / BUY / WATCH LIST / AVOID / PASS.

    A failed Tier 1 gate cannot qualify as Buy or Strong Buy (hard rule); an
    auto-avoid gate (two+ Tier 1 scores <= 2) classifies as AVOID outright.
    """
    if gate is not None and gate.auto_avoid:
        return "AVOID"
    if score >= 80:
        d = "STRONG BUY"
    elif score >= 65:
        d = "BUY"
    elif score >= 50:
        d = "WATCH LIST"
    elif score >= 35:
        d = "AVOID"
    else:
        d = "PASS"
    if d in ("STRONG BUY", "BUY") and gate is not None and not gate.passes:
        return "WATCH LIST"
    return d


# --------------------------------------------------------------------------- #
# Part 4 — entry checklist & implied return tests
# --------------------------------------------------------------------------- #

@dataclass
class EntryChecklist:
    """Rules 9-14 (Part 4). All must pass for an entry."""
    composite_ok: bool          # 9.  composite >= 65
    tier1_ok: bool              # 10. Tier 1 weighted avg >= 3.0
    no_tier1_at_1: bool         # 11. no Tier 1 indicator scored 1
    above_200w_ma: bool         # 12. price above / reclaimed 200-week MA
    monthly_macd_ok: bool       # 13. monthly MACD positive or turning
    no_binary_event: bool       # 14. no unhandled near-term binary event

    @property
    def passes(self) -> bool:
        return all((self.composite_ok, self.tier1_ok, self.no_tier1_at_1,
                    self.above_200w_ma, self.monthly_macd_ok,
                    self.no_binary_event))

    def failures(self) -> List[str]:
        names = {
            "composite_ok": "Rule 9: composite < 65",
            "tier1_ok": "Rule 10: Tier 1 weighted avg < 3.0",
            "no_tier1_at_1": "Rule 11: a Tier 1 indicator is scored 1",
            "above_200w_ma": "Rule 12: price below 200-week MA",
            "monthly_macd_ok": "Rule 13: monthly MACD negative",
            "no_binary_event": "Rule 14: unhandled binary event risk",
        }
        return [msg for f, msg in names.items() if not getattr(self, f)]


def entry_checklist(scores: Dict[str, int],
                    score: float,
                    above_200w_ma: bool,
                    monthly_macd_ok: bool,
                    no_binary_event: bool = True) -> EntryChecklist:
    gate = tier1_gate(scores)
    return EntryChecklist(
        composite_ok=score >= 65,
        tier1_ok=gate.passes_floor,
        no_tier1_at_1=not gate.indicators_at_1,
        above_200w_ma=above_200w_ma,
        monthly_macd_ok=monthly_macd_ok,
        no_binary_event=no_binary_event,
    )


def implied_5yr_return(fcf_yield: float,
                       fcf_growth: float,
                       exit_multiple_ratio: float = 1.0) -> float:
    """Approximate implied 5-year annualized return.

    Model (documented so the assumption is auditable, per Part 1):
    price appreciates with FCF per share at ``fcf_growth`` (constant P/FCF,
    scaled by ``exit_multiple_ratio`` = exit multiple / entry multiple over
    the 5 years), plus the current FCF yield as the cash-return component.

        implied = (1 + g) * ratio^(1/5) - 1 + fcf_yield

    Inputs are decimals (0.06 = 6%).
    """
    return (1.0 + fcf_growth) * exit_multiple_ratio ** (1.0 / 5.0) - 1.0 + fcf_yield


def base_case_return_test(fcf_yield: float, fcf_growth: float,
                          exit_multiple_ratio: float = 1.0) -> bool:
    """Part 4 NOTE: implied 5-year annual return must be >= 10%."""
    return implied_5yr_return(fcf_yield, fcf_growth, exit_multiple_ratio) >= 0.10


def bear_case_return_test(fcf_yield: float, base_fcf_growth: float,
                          bear_fcf_growth: Optional[float] = None,
                          exit_multiple_ratio: float = 1.0) -> bool:
    """Part 11 bear-case test: implied return with FCF growth cut 50% from
    the base case (or an explicit documented bear growth) must be >= 0."""
    g = bear_fcf_growth if bear_fcf_growth is not None else 0.5 * base_fcf_growth
    return implied_5yr_return(fcf_yield, g, exit_multiple_ratio) >= 0.0


# --------------------------------------------------------------------------- #
# Part 5 + Part 11 — position sizing
# --------------------------------------------------------------------------- #

BETA_OVERLAY_BASELINE = 1.3
BETA_OVERLAY_INCREMENT = 0.10


def beta_overlay_reduction(beta: Optional[float]) -> int:
    """Part 11 beta sizing overlay: -1% of max position per *completed*
    0.10 increment of 5-year beta above 1.3 (round down; a partial increment
    does not count). beta 1.43 -> 1, beta 1.5 -> 2, beta <= 1.3 -> 0."""
    if beta is None or beta <= BETA_OVERLAY_BASELINE:
        return 0
    # round-before-floor guards float artifacts: (1.5-1.3)/0.1 == 1.9999...
    increments = math.floor(round((beta - BETA_OVERLAY_BASELINE)
                                  / BETA_OVERLAY_INCREMENT, 9))
    return int(increments)


@dataclass
class SizingResult:
    tier: str
    base_initial_pct: float
    base_full_pct: float
    initial_pct: float
    full_pct: float
    adjustments: List[Tuple[str, float]] = field(default_factory=list)


def position_size(score: float,
                  beta: Optional[float] = None,
                  debt_to_fcf: Optional[float] = None,
                  insider_ownership_with_buys: bool = False,
                  customer_concentration_over_30pct: bool = False,
                  macro_headwind: bool = False) -> SizingResult:
    """Max initial / full position (% of portfolio) per Part 5 + Part 11.

    Base by composite tier (80+: 5/8, 65-79: 3/5, else 0/0), then modifiers:
    insider ownership >5% with open-market buys +1; Debt/FCF > 4x -1;
    single customer >30% of revenue -1; active macro headwind -1; and the
    Part 11 beta overlay (-1 per completed 0.10 of beta above 1.3).
    Hard cap: no position > 8% at cost; floors at 0.
    """
    if score >= 80:
        tier, base_init, base_full = "STRONG BUY", 5.0, 8.0
    elif score >= 65:
        tier, base_init, base_full = "BUY", 3.0, 5.0
    else:
        return SizingResult("BELOW THRESHOLD", 0.0, 0.0, 0.0, 0.0)

    adj: List[Tuple[str, float]] = []
    if insider_ownership_with_buys:
        adj.append(("insider ownership >5% with recent buys", +1.0))
    if debt_to_fcf is not None and debt_to_fcf > 4.0:
        adj.append(("Debt/FCF > 4x fragility discount", -1.0))
    if customer_concentration_over_30pct:
        adj.append(("single customer >30% of revenue", -1.0))
    if macro_headwind:
        adj.append(("macro headwind (Part 7)", -1.0))
    beta_red = beta_overlay_reduction(beta)
    if beta_red:
        adj.append((f"beta overlay: {beta_red} increment(s) above 1.3", -float(beta_red)))

    delta = sum(d for _, d in adj)
    full = min(max(base_full + delta, 0.0), 8.0)   # hard cap 8% at cost
    init = min(max(base_init + delta, 0.0), full)
    return SizingResult(tier, base_init, base_full, init, full, adj)


# --------------------------------------------------------------------------- #
# Part 11 — portfolio drawdown response bands
# --------------------------------------------------------------------------- #

@dataclass
class DrawdownBand:
    status: str
    cash_floor_pct: float
    new_positions: str


def drawdown_response(drawdown: float) -> DrawdownBand:
    """Map a peak-to-trough portfolio drawdown (positive fraction, 0.12 = 12%)
    to the Part 11 response band. Thresholds revert automatically on recovery.
    """
    dd = abs(drawdown)
    if dd < 0.10:
        return DrawdownBand("Normal", 5.0, "Standard sizing (Part 5)")
    if dd < 0.20:
        return DrawdownBand("Elevated", 15.0,
                            "Strong Buy (score >= 80) initiations only")
    if dd < 0.30:
        return DrawdownBand("Severe", 20.0, "No new position initiation")
    return DrawdownBand("Critical", 25.0,
                        "No new positions; re-score full portfolio within 2 weeks")


# --------------------------------------------------------------------------- #
# Part 11 — correlation cap (Rule: no position >0.70 pairwise with more than
# one other holding). Matrix math lives in part11_tracker.py; the rule check
# is here so it is testable without data.
# --------------------------------------------------------------------------- #

CORRELATION_CAP = 0.70


def correlation_violations(corr: "object") -> Dict[str, List[str]]:
    """Given a symmetric correlation DataFrame (tickers × tickers), return
    {ticker: [correlated tickers]} for every position whose pairwise
    correlation exceeds 0.70 with MORE THAN ONE other holding."""
    out: Dict[str, List[str]] = {}
    for t in corr.index:
        hits = [o for o in corr.columns
                if o != t and corr.loc[t, o] is not None
                and float(corr.loc[t, o]) > CORRELATION_CAP]
        if len(hits) >= 2:
            out[t] = hits
    return out


# --------------------------------------------------------------------------- #
# Full scoring run (reproduces the Part 8 template flow)
# --------------------------------------------------------------------------- #

@dataclass
class ScoreCard:
    ticker: str
    gate: Tier1Gate
    tier1_contribution: float
    tier2_contribution: Optional[float]
    tier3_contribution: Optional[float]
    composite: Optional[float]
    decision: str
    notes: List[str] = field(default_factory=list)


def score_stock(ticker: str,
                tier1: Dict[str, int],
                tier2: Optional[Dict[str, int]] = None,
                tier3: Optional[Dict[str, int]] = None,
                debt_to_fcf: Optional[float] = None) -> ScoreCard:
    """Run the v7 process for one stock: Tier 1 gate first ("How to Use"
    step 2); Tier 2/3 are only scored if the gate passes."""
    gate = tier1_gate(tier1)
    t1 = tier_contribution(tier1, TIER1_WEIGHTS)
    notes: List[str] = []
    if gate.indicators_at_1:
        notes.append("Rule 7: Tier 1 indicator scored 1 — mandatory "
                     f"qualitative review ({', '.join(gate.indicators_at_1)})")

    if not gate.passes:
        why = []
        if not gate.passes_floor:
            why.append("Rule 6: Tier 1 weighted average below 3.0")
        if gate.auto_avoid:
            why.append("Rule 8: two or more Tier 1 indicators <= 2")
        notes.extend(why)
        return ScoreCard(ticker, gate, t1, None, None, None, "AVOID", notes)

    if tier2 is None or tier3 is None:
        raise ValueError(f"{ticker}: Tier 1 gate passed — tier2 and tier3 "
                         "scores are required to complete the composite")

    t2 = tier_contribution(tier2, TIER2_WEIGHTS)
    t3 = tier_contribution(tier3, TIER3_WEIGHTS)
    comp = composite_score({**tier1, **tier2, **tier3}, debt_to_fcf=debt_to_fcf)
    if debt_to_fcf is not None and debt_to_fcf > LEVERAGE_HARD_CAP_DEBT_FCF:
        notes.append(f"Part 11 leverage hard cap: Debt/FCF {debt_to_fcf:.1f}x "
                     f"> 5x — composite capped at {LEVERAGE_SCORE_CAP}")
    return ScoreCard(ticker, gate, t1, t2, t3, comp,
                     decision(comp, gate), notes)
