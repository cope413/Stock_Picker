"""Landry System v1.0 — Part 6 exit & sell discipline (Rules 21-35) and
the Part 3 Existing Holding Action column.

Mirrors the workbook's Holding Monitor and Watch List Tracker tabs:
fundamental sell triggers "act within 30 days of confirmation"
(Rules 21-25); valuation triggers trim or sell "when price exceeds
intrinsic value" (Rules 26-29); band transitions follow the Part 3
Existing Holding Action column plus Rules 30-33; Hold Through conditions
are things you do NOT sell on alone; and the Opportunity-Cost
Replacement gate (Rules 34-35) is a narrow, all-conditions-required
exception.

Analyst confirmations (two-consecutive-quarter/year streaks, cyclical
rationales, deleveraging paths, remediation-plan approval) arrive as
documented flags — this module applies their consequences, it never
invents them (Part 12 automation boundary).

Units: indicator scores are 1-5 ints; composites 0-100; position sizes
portfolio percent (5.0 == 5%); ``fcf_growth_pct`` is percent (15.0);
``implied_5yr_return`` is a fraction (0.07 == 7%), matching
landry.implied_return.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from landry.scoring import DECISION_BANDS

ACT_WITHIN_DAYS = 30            # fundamental triggers + mandated sells
REVIEW_WINDOW_DAYS = 90         # probation / remediation window
IMPLIED_RETURN_SELL_FLOOR = 0.07     # Rule 28
DEBT_FCF_SELL_THRESHOLD = 6.0        # Rule 26
P_FCF_SELL_THRESHOLD = 50.0          # Rule 27
FCF_GROWTH_SELL_FLOOR_PCT = 15.0     # Rule 27
MOAT_DURABLE = 4                     # Rule 22 "durable"
MOAT_ABSENT = 2                      # Rule 22 "absent"
REPLACEMENT_MIN_GAP = 15.0           # Rule 34(b)


@dataclass
class HoldingState:
    """One holding's monitor row (Holding Monitor tab)."""
    ticker: str
    prior_scores: Dict[str, int] = field(default_factory=dict)
    current_scores: Dict[str, int] = field(default_factory=dict)
    prior_composite: Optional[float] = None
    current_composite: Optional[float] = None
    position_pct: Optional[float] = None
    max_full_pct: Optional[float] = None
    debt_to_fcf: Optional[float] = None
    p_fcf: Optional[float] = None
    fcf_growth_pct: Optional[float] = None
    implied_5yr_return: Optional[float] = None
    band_entered_date: Optional[date] = None
    # documented analyst confirmations (Part 12 boundary)
    consecutive_quarters_fcf_yield_at_1: Optional[int] = None
    roic_below_wacc_two_years: bool = False
    revenue_negative_two_quarters: bool = False
    cyclical_rationale_documented: bool = False
    deleveraging_path_documented: bool = False


@dataclass(frozen=True)
class Trigger:
    rule: str            # citation, e.g. "Rule 21"
    kind: str            # "fundamental" | "valuation"
    description: str
    act_within_days: int = ACT_WITHIN_DAYS


def fundamental_triggers(h: HoldingState) -> List[Trigger]:
    """Rules 21-25 — act within 30 days of confirmation."""
    out: List[Trigger] = []

    # Rule 21 — FCF Yield & Trend at 1 for two consecutive quarters
    streak = h.consecutive_quarters_fcf_yield_at_1
    both_at_1 = (h.prior_scores.get("fcf_yield_trend") == 1
                 and h.current_scores.get("fcf_yield_trend") == 1)
    if (streak is not None and streak >= 2) or both_at_1:
        out.append(Trigger("Rule 21", "fundamental",
                           "FCF Yield & Trend at 1 for two consecutive "
                           "quarters"))

    # Rule 22 — moat degrades from durable (>=4) to absent (<=2)
    moat_prior = h.prior_scores.get("competitive_moat")
    moat_now = h.current_scores.get("competitive_moat")
    if (moat_prior is not None and moat_now is not None
            and moat_prior >= MOAT_DURABLE and moat_now <= MOAT_ABSENT):
        out.append(Trigger("Rule 22", "fundamental",
                           f"Competitive Moat degraded from durable "
                           f"({moat_prior}) to absent ({moat_now})"))

    # Rule 23 — ROIC < WACC two consecutive years (documented flag)
    if h.roic_below_wacc_two_years:
        out.append(Trigger("Rule 23", "fundamental",
                           "ROIC below WACC for two consecutive years"))

    # Rule 24 — management quality at 1 (destructive capital allocation)
    if h.current_scores.get("management_quality") == 1:
        out.append(Trigger("Rule 24", "fundamental",
                           "Management quality at 1 — destructive capital "
                           "allocation"))

    # Rule 25 — revenue negative two consecutive quarters, no documented
    # cyclical rationale (Part 4 Cyclical Business Adjustment applies)
    if h.revenue_negative_two_quarters and not h.cyclical_rationale_documented:
        out.append(Trigger("Rule 25", "fundamental",
                           "Revenue growth negative two consecutive quarters "
                           "without a credible documented cyclical rationale"))

    return out


def valuation_triggers(h: HoldingState) -> List[Trigger]:
    """Rules 26-29 — trim or sell when price exceeds intrinsic value."""
    out: List[Trigger] = []

    if (h.debt_to_fcf is not None and h.debt_to_fcf > DEBT_FCF_SELL_THRESHOLD
            and not h.deleveraging_path_documented):
        out.append(Trigger("Rule 26", "valuation",
                           f"Debt/FCF {h.debt_to_fcf:.1f}x > 6x with no "
                           "credible deleveraging path"))

    if (h.p_fcf is not None and h.p_fcf > P_FCF_SELL_THRESHOLD
            and h.fcf_growth_pct is not None
            and h.fcf_growth_pct < FCF_GROWTH_SELL_FLOOR_PCT):
        out.append(Trigger("Rule 27", "valuation",
                           f"P/FCF {h.p_fcf:.1f}x > 50x while FCF growth "
                           f"{h.fcf_growth_pct:.1f}% < 15%"))

    if (h.implied_5yr_return is not None
            and h.implied_5yr_return < IMPLIED_RETURN_SELL_FLOOR):
        out.append(Trigger("Rule 28", "valuation",
                           f"Implied 5-year return "
                           f"{h.implied_5yr_return:.1%} < 7%"))

    if (h.position_pct is not None and h.max_full_pct is not None
            and h.position_pct > h.max_full_pct):
        out.append(Trigger("Rule 29", "valuation",
                           f"Position {h.position_pct:.1f}% above the "
                           f"{h.max_full_pct:.1f}% maximum full position — "
                           "trim the appreciation"))

    return out


# --------------------------------------------------------------------------- #
# Band transitions — Part 3 Existing Holding Action + Rules 30-33
# --------------------------------------------------------------------------- #

@dataclass
class ActionItem:
    status: str                  # e.g. "Probationary Hold"
    action: str
    rule: str
    deadline: Optional[date] = None


def _band(score: float) -> str:
    for floor, name in DECISION_BANDS:
        if score >= floor:
            return name
    return "PASS"  # pragma: no cover


def band_transition(prior_composite: float,
                    current_composite: float,
                    today: date,
                    band_entered_date: Optional[date] = None,
                    remediation_plan_approved: bool = False) -> ActionItem:
    """Existing-holding action for a re-score (Watch List Tracker tab).

    * Strong Buy -> Buy: trim only to the Buy maximum full position
      within 30 days (Rule 30).
    * 50-64: Probationary Hold — no additions, 90-day re-score deadline
      from the date the band was entered (Rules 30/32).
    * 35-49: Exit Review — sell within 30 days unless a dated 90-day
      remediation plan is approved (Rule 31); past that deadline without
      recovery to >= 50, sell within 30 days, no extension (Rule 32).
    * < 35: mandatory sell within 30 days (Rule 33).
    """
    entered = band_entered_date or today
    band = _band(current_composite)

    if band == "STRONG BUY":
        return ActionItem("Core Hold / Add",
                          "subject to sizing and entry rules", "Part 3")

    if band == "BUY":
        if _band(prior_composite) == "STRONG BUY":
            return ActionItem(
                "Trim to Buy Maximum",
                "re-score from Strong Buy to Buy — trim only to the Buy "
                "maximum full position within 30 days",
                "Rule 30", today + timedelta(days=ACT_WITHIN_DAYS))
        return ActionItem("Hold",
                          "trim only if above the Buy maximum or another "
                          "rule requires action", "Part 3 / Rule 30")

    if band == "WATCH LIST":
        return ActionItem(
            "Probationary Hold",
            "no additions permitted; 90-day re-score required",
            "Rule 30/32", entered + timedelta(days=REVIEW_WINDOW_DAYS))

    if band == "AVOID":                      # 35-49: Exit Review
        if not remediation_plan_approved:
            return ActionItem(
                "Exit Review",
                "no approved remediation plan with a credible documented "
                "path back to >= 50 — sell within 30 days",
                "Rule 31", today + timedelta(days=ACT_WITHIN_DAYS))
        deadline = entered + timedelta(days=REVIEW_WINDOW_DAYS)
        if today > deadline:
            return ActionItem(
                "Mandatory Sell",
                "remediation deadline passed without recovery to >= 50 — "
                "sell within 30 days; no extension is permitted",
                "Rule 32", today + timedelta(days=ACT_WITHIN_DAYS))
        return ActionItem(
            "Exit Review",
            "approved 90-day remediation plan — must recover to >= 50 "
            f"by {deadline.isoformat()}",
            "Rule 31/32", deadline)

    return ActionItem(                        # < 35
        "Mandatory Sell",
        "Composite below 35 — sell within 30 days unless a more urgent "
        "fundamental trigger requires earlier action",
        "Rule 33", today + timedelta(days=ACT_WITHIN_DAYS))


# --------------------------------------------------------------------------- #
# Hold Through — do NOT sell based on these alone
# --------------------------------------------------------------------------- #

HOLD_THROUGH_DECLINE_MIN_PCT = 20.0
HOLD_THROUGH_DECLINE_MAX_PCT = 30.0


def hold_through(h: HoldingState,
                 market_selloff: bool = False,
                 one_quarter_miss: bool = False,
                 analyst_downgrades: bool = False,
                 negative_press: bool = False,
                 price_decline_pct: Optional[float] = None) -> bool:
    """True when ONLY Hold Through conditions exist: a 20-30% price
    decline with Tier 1/2 fundamentals intact, a single-quarter miss,
    analyst downgrades, a market-wide selloff, or a narrative shift /
    negative press — and NO fundamental or valuation trigger has fired.

    Hold Through means do not sell on these alone; a fired trigger
    always wins over Hold Through.  A decline beyond 30% is outside the
    "this is noise" band and does not itself confer protection.
    """
    if fundamental_triggers(h) or valuation_triggers(h):
        return False
    decline_in_band = (price_decline_pct is not None
                       and HOLD_THROUGH_DECLINE_MIN_PCT <= price_decline_pct
                       <= HOLD_THROUGH_DECLINE_MAX_PCT)
    return any((market_selloff, one_quarter_miss, analyst_downgrades,
                negative_press, decline_in_band))


# --------------------------------------------------------------------------- #
# Opportunity-Cost Replacement — Rules 34-35
# --------------------------------------------------------------------------- #

def replacement_gate(existing_score_q1: float,
                     existing_score_q2: float,
                     new_score: float,
                     new_passes_entry_and_ceiling: bool,
                     existing_hold_through: bool,
                     cash_insufficient_or_cap_breach: bool,
                     last_replacement_within_12mo: bool,
                     documented_before_execution: bool = True
                     ) -> Tuple[bool, List[str]]:
    """Rules 34-35: replacement allowed only when ALL conditions hold.

    (a) the new opportunity clears the Entry Checklist and Valuation
    Ceiling; (b) the score gap is >= 15 across BOTH consecutive
    quarterly re-scores of the existing holding; (c) the existing
    holding is not protected by Hold Through; (d) a genuine capital
    constraint exists; (e) documented before execution; and (Rule 35)
    not invoked for this holding in a rolling 12-month period.
    """
    failed: List[str] = []
    if not new_passes_entry_and_ceiling:
        failed.append("Rule 34(a): new opportunity has not cleared the "
                      "Entry Checklist and Valuation Ceiling")
    gap1 = new_score - existing_score_q1
    gap2 = new_score - existing_score_q2
    if not (gap1 >= REPLACEMENT_MIN_GAP and gap2 >= REPLACEMENT_MIN_GAP):
        failed.append(f"Rule 34(b): score gap must be >= 15 across both "
                      f"consecutive re-scores (got {gap1:.0f} and {gap2:.0f})")
    if existing_hold_through:
        failed.append("Rule 34(c): existing holding is protected by Hold "
                      "Through — this process does not apply")
    if not cash_insufficient_or_cap_breach:
        failed.append("Rule 34(d): no genuine capital constraint — cash can "
                      "fund the required initial size without breaching a cap")
    if not documented_before_execution:
        failed.append("Rule 34(e): comparison, tax effect, and capital "
                      "constraint must be documented before execution")
    if last_replacement_within_12mo:
        failed.append("Rule 35: already invoked for this holding within a "
                      "rolling 12-month period")
    return (not failed, failed)
