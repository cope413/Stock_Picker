"""Landry System v1.0 — Part 2 indicators, Part 3 composite score & Hard Rules 1-4.

Every number here is pinned to LANDRY_SYSTEM_WORKBOOK_11.xlsx by
tests/test_landry_scoring.py, the way test_v7_scoring.py pins the v7 math
to the PART8 record.

Design rules (from the document):
* Composite Score = (sum of score x weight) x 20 -- never tier-averaged.
* Analyst-judgment scores arrive as data; this module never invents them
  (Part 12 automation boundary).
* Hard Rules have no override path (Part 1.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

# --------------------------------------------------------------------------- #
# Part 2 — indicator framework (v1.0 weights)
# --------------------------------------------------------------------------- #

#: indicator -> (tier, weight, display name)
INDICATORS: Dict[str, tuple] = {
    "fcf_yield_trend":            (1, 0.20, "FCF Yield & Trend"),
    "revenue_growth_consistency": (1, 0.15, "Revenue Growth Consistency"),
    "competitive_moat":           (1, 0.15, "Competitive Moat Quality"),
    "revenue_visibility":         (1, 0.10, "Revenue Visibility (ARR/Backlog)"),
    "fcf_margin_trend":           (1, 0.10, "FCF Margin Trend"),
    "management_quality":         (2, 0.08, "Management Quality & Capital Allocation"),
    "roic_vs_wacc":               (2, 0.06, "ROIC vs. WACC Spread"),
    "relative_strength":          (2, 0.03, "Relative Strength vs. SPY (6-12 mo.)"),
    "valuation_multiples":        (2, 0.08, "Valuation Multiples (P/FCF, EV/EBITDA, PEG)"),
    "technical_trend":            (3, 0.02, "Technical Trend Structure"),
    "analyst_consensus":          (3, 0.02, "Analyst Consensus & Estimate Revisions"),
    "volume_accumulation":        (3, 0.01, "Volume & Accumulation Signals"),
}

TIER1_WEIGHTS: Dict[str, float] = {k: w for k, (t, w, _) in INDICATORS.items() if t == 1}
TIER2_WEIGHTS: Dict[str, float] = {k: w for k, (t, w, _) in INDICATORS.items() if t == 2}
TIER3_WEIGHTS: Dict[str, float] = {k: w for k, (t, w, _) in INDICATORS.items() if t == 3}
ALL_WEIGHTS: Dict[str, float] = {k: w for k, (_, w, _) in INDICATORS.items()}

TIER1_TOTAL_WEIGHT = 0.70   # Tier 1 = 70% of decision
TIER2_TOTAL_WEIGHT = 0.25
TIER3_TOTAL_WEIGHT = 0.05
TIER1_FLOOR = 3.0           # Rule 1 / Rule 6
TIER1_RAW_FLOOR = 2.10      # equivalent raw Tier 1 contribution (doc, Rule 1 note)

CONFIDENCE_LEVELS = ("H", "M", "L")

_VALID_SCORES = (1, 2, 3, 4, 5)

# Rule 19 (Part 5): Debt/FCF above 5x caps the Composite Score at 65
# (nonfinancial operating companies). Rule 48 applies the same cap to
# financials via sector-specific balance-sheet tests.
LEVERAGE_HARD_CAP_DEBT_FCF = 5.0
LEVERAGE_SCORE_CAP = 65.0


@dataclass(frozen=True)
class IndicatorScore:
    """One analyst-approved indicator score (Part 2 + confidence tagging).

    ``adaptation`` records any Part 10 sector substitution used (e.g.
    "REIT: AFFO yield for FCF yield"); the doc requires every substitution
    written beside the affected score.
    """
    score: int
    confidence: str                       # "H" | "M" | "L"
    adaptation: Optional[str] = None
    evidence: Optional[str] = None

    def __post_init__(self):
        if self.score not in _VALID_SCORES:
            raise ValueError(f"score must be an integer 1-5, got {self.score!r}")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}, "
                             f"got {self.confidence!r}")


Scores = Mapping[str, IndicatorScore]


def _require(scores: Scores, weights: Dict[str, float]) -> None:
    missing = set(weights) - set(scores)
    if missing:
        raise ValueError(f"missing indicator scores: {sorted(missing)}")


def tier_contribution(scores: Scores, weights: Dict[str, float]) -> float:
    """Raw weighted contribution of one tier: sum(score x weight)."""
    _require(scores, weights)
    return sum(scores[k].score * w for k, w in weights.items())


def tier1_weighted_average(scores: Scores) -> float:
    """Tier 1 weighted average = (sum Tier 1 score x weight) / 0.70."""
    return tier_contribution(scores, TIER1_WEIGHTS) / TIER1_TOTAL_WEIGHT


def composite_score(scores: Scores,
                    debt_to_fcf: Optional[float] = None,
                    financial_cap_triggered: bool = False) -> float:
    """Composite Score = (sum of all score x weight) x 20  (Part 3).

    ``debt_to_fcf`` > 5x applies the Rule 19 cap at 65 (nonfinancials).
    ``financial_cap_triggered`` applies the Rule 48 sector cap at 65
    (CET1 < 9% banks / RBC < 350% insurers / leverage tests for brokers) —
    the caller evaluates the sector test; this applies its consequence.
    """
    _require(scores, ALL_WEIGHTS)
    score = tier_contribution(scores, ALL_WEIGHTS) * 20.0
    if debt_to_fcf is not None and debt_to_fcf > LEVERAGE_HARD_CAP_DEBT_FCF:
        score = min(score, LEVERAGE_SCORE_CAP)
    if financial_cap_triggered:
        score = min(score, LEVERAGE_SCORE_CAP)
    return score


# --------------------------------------------------------------------------- #
# Part 3 — Hard Rules 1-4 and decision bands
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RuleFlags:
    """Workbook-style flags for Hard Rules 1-4 (Scoring tab cols AH-AK)."""
    rule1: str  # "OK" | "FAIL"        Tier 1 weighted avg < 3.0
    rule2: str  # "OK" | "REVIEW"      any Tier 1 indicator scored 1
    rule3: str  # "OK" | "AVOID"       two or more Tier 1 indicators <= 2
    rule4: str  # "OK" | "CAP AT BUY"  2+ Tier 1 Low-Confidence blocks Strong Buy

    @property
    def tier1_passes(self) -> bool:
        return self.rule1 == "OK" and self.rule3 == "OK"


def rule_flags(scores: Scores) -> RuleFlags:
    """Evaluate Hard Rules 1-4 on the Tier 1 scores + confidence tags."""
    _require(scores, TIER1_WEIGHTS)
    avg = tier1_weighted_average(scores)
    at1 = [k for k in TIER1_WEIGHTS if scores[k].score == 1]
    le2 = [k for k in TIER1_WEIGHTS if scores[k].score <= 2]
    low = [k for k in TIER1_WEIGHTS if scores[k].confidence == "L"]
    return RuleFlags(
        rule1="OK" if avg >= TIER1_FLOOR else "FAIL",
        rule2="REVIEW" if at1 else "OK",
        rule3="AVOID" if len(le2) >= 2 else "OK",
        rule4="CAP AT BUY" if len(low) >= 2 else "OK",
    )


DECISION_BANDS = (
    (80.0, "STRONG BUY"),
    (65.0, "BUY"),
    (50.0, "WATCH LIST"),
    (35.0, "AVOID"),
    (float("-inf"), "PASS"),
)


def _band(score: float) -> str:
    for floor, name in DECISION_BANDS:
        if score >= floor:
            return name
    return "PASS"  # pragma: no cover


def classify(score: float, flags: RuleFlags,
             unadapted_sector: bool = False) -> str:
    """Map a composite score + Rules 1-4 to the Part 3 decision.

    * Rule 3: two+ Tier 1 indicators <= 2 -> AVOID outright.
    * Rule 1: Tier 1 avg < 3.0 cannot qualify Buy/Strong Buy -> capped
      at WATCH LIST.
    * Rule 4: 2+ Tier 1 Low Confidence caps STRONG BUY at BUY.
    * Part 1.3 / Part 10: a sector scored without a documented adaptation
      is Low Confidence and shall not permit Strong Buy.
    """
    if flags.rule3 == "AVOID":
        return "AVOID"
    d = _band(score)
    if d in ("STRONG BUY", "BUY") and flags.rule1 == "FAIL":
        return "WATCH LIST"
    if d == "STRONG BUY" and (flags.rule4 == "CAP AT BUY" or unadapted_sector):
        return "BUY"
    return d


# --------------------------------------------------------------------------- #
# Full scoring run (reproduces the workbook Scoring-tab flow)
# --------------------------------------------------------------------------- #

@dataclass
class ScoreCard:
    ticker: str
    tier1_weighted_average: float
    tier1_contribution: float
    flags: RuleFlags
    tier2_contribution: Optional[float] = None
    tier3_contribution: Optional[float] = None
    composite: Optional[float] = None
    decision: Optional[str] = None
    notes: List[str] = field(default_factory=list)


def score_stock(ticker: str,
                scores: Scores,
                debt_to_fcf: Optional[float] = None,
                financial_cap_triggered: bool = False,
                unadapted_sector: bool = False) -> ScoreCard:
    """Run the v1.0 process for one stock ("How to Use", steps 2-3).

    Tier 1 is scored first. If the Tier 1 gate fails (Rule 1 floor or
    Rule 3 auto-Avoid), scoring stops -- Tier 2/3 are not required and
    the composite stays empty, exactly as the workbook leaves those
    cells blank. Otherwise all 12 indicators are required.
    """
    _require(scores, TIER1_WEIGHTS)
    flags = rule_flags(scores)
    t1 = tier_contribution(scores, TIER1_WEIGHTS)
    avg = tier1_weighted_average(scores)
    notes: List[str] = []
    if flags.rule2 == "REVIEW":
        bad = [k for k in TIER1_WEIGHTS if scores[k].score == 1]
        notes.append("Rule 2: Tier 1 indicator scored 1 -- mandatory "
                     f"qualitative review ({', '.join(bad)})")

    if not flags.tier1_passes:
        if flags.rule1 == "FAIL":
            notes.append(f"Rule 1: Tier 1 weighted average {avg:.2f} < 3.0 "
                         "-- stop and reject (How to Use, step 2)")
        if flags.rule3 == "AVOID":
            notes.append("Rule 3: two or more Tier 1 indicators <= 2 -- "
                         "auto-classified AVOID")
        decision = "AVOID" if flags.rule3 == "AVOID" else None
        return ScoreCard(ticker, avg, t1, flags, notes=notes, decision=decision)

    _require(scores, ALL_WEIGHTS)
    t2 = tier_contribution(scores, TIER2_WEIGHTS)
    t3 = tier_contribution(scores, TIER3_WEIGHTS)
    comp = composite_score(scores, debt_to_fcf=debt_to_fcf,
                           financial_cap_triggered=financial_cap_triggered)
    if debt_to_fcf is not None and debt_to_fcf > LEVERAGE_HARD_CAP_DEBT_FCF:
        notes.append(f"Rule 19: Debt/FCF {debt_to_fcf:.1f}x > 5x -- "
                     f"composite capped at {LEVERAGE_SCORE_CAP:.0f}")
    if financial_cap_triggered:
        notes.append("Rule 48: financial-sector balance-sheet cap -- "
                     f"composite capped at {LEVERAGE_SCORE_CAP:.0f}")
    if flags.rule4 == "CAP AT BUY":
        notes.append("Rule 4: 2+ Tier 1 indicators Low Confidence -- "
                     "Strong Buy capped at Buy until re-scored")
    if unadapted_sector:
        notes.append("Part 10: no documented sector adaptation -- "
                     "Low Confidence, Strong Buy not permitted")
    return ScoreCard(ticker, avg, t1, flags, t2, t3, comp,
                     classify(comp, flags, unadapted_sector), notes)
