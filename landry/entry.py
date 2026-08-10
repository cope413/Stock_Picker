"""Landry System v1.0 — Part 4 Entry Checklist, Hard Rules 5-13.

Mirrors the workbook's Entry Checklist tab: "A Buy or Strong Buy
classification does not automatically create an entry."  Rules 5-7 are
re-derived from the Part 3 ScoreCard/RuleFlags (landry.scoring); Rules
8-9 read the automatable TechnicalState (landry.data_auto); Rules 11-12
read the documented ScenarioSet (landry.implied_return).

Two rules govern staging, not eligibility:
* Rule 8 — "Technical trend determines entry staging - not fundamental
  eligibility": below an unreclaimed 200-week MA, "limit the first
  purchase to 50% of the normal initial size and require a 90-day
  technical review."
* Rule 9 — a negative monthly MACD holds the position at that reduced
  size but never blocks entry: "Technical weakness alone shall not
  override a qualifying fundamental case."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from landry.data_auto import TechnicalState
from landry.implied_return import ScenarioSet, check_scenarios
from landry.scoring import TIER1_FLOOR, ScoreCard

# Rule 5 / Part 5 sizing threshold
COMPOSITE_ENTRY_FLOOR = 65.0
# Rule 13 valuation ceiling
VALUATION_CEILING_P_FCF = 50.0
VALUATION_CEILING_GROWTH_ESCAPE = 0.30   # consensus FCF growth > 30%/yr, 2 yrs
# Rule 8 staging
REDUCED_STAGING_FRACTION = 0.5
TECHNICAL_REVIEW = "90-day technical review"


@dataclass(frozen=True)
class RuleResult:
    rule: str            # citation, e.g. "Rule 5"
    passed: bool
    detail: str


@dataclass
class EntryDecision:
    """Per-rule results for one Entry Checklist run.

    ``authorized`` — every hard rule passed (Rules 8-9 shape staging and
    cannot fail).  ``staging_fraction`` — 1.0 standard, 0.5 reduced
    (Rules 8-9); ``review_required`` — the Rule 8 90-day technical
    review whenever staged at 50%.
    """
    ticker: str
    results: List[RuleResult] = field(default_factory=list)
    staging_fraction: float = 1.0

    @property
    def authorized(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> List[RuleResult]:
        return [r for r in self.results if not r.passed]

    @property
    def review_required(self) -> Optional[str]:
        return (TECHNICAL_REVIEW
                if self.staging_fraction < 1.0 else None)


def entry_checklist(card: ScoreCard,
                    tech: TechnicalState,
                    scenarios: Optional[ScenarioSet],
                    binary_event_present: bool,
                    binary_event_in_thesis: bool,
                    p_fcf: Optional[float],
                    consensus_fcf_growth_2yr: Optional[float],
                    financial_sector: bool = False,
                    sector_valuation_ok: Optional[bool] = None) -> EntryDecision:
    """Run Hard Rules 5-13 for one candidate.

    ``scenarios=None`` means the Rule 11/12 documentation does not exist,
    so Rules 11 and 12 FAIL (they are entry preconditions, never
    skippable).  ``sector_valuation_ok`` carries the caller's Rule 49
    sector-specific valuation verdict for financials (Part 10
    substitute); None there is treated as not evaluated and fails
    Rule 13 — the ceiling "no combination of other scores can override"
    cannot pass unverified.
    """
    results: List[RuleResult] = []

    # Rule 5 — Composite Score >= 65 (a card whose Tier 1 gate stopped
    # scoring has no composite and cannot qualify)
    comp = card.composite
    results.append(RuleResult(
        "Rule 5", comp is not None and comp >= COMPOSITE_ENTRY_FLOOR,
        f"Composite Score {comp if comp is not None else 'not computed'} "
        f"vs >= {COMPOSITE_ENTRY_FLOOR:.0f} floor"))

    # Rule 6 — Tier 1 weighted average >= 3.0
    results.append(RuleResult(
        "Rule 6", card.tier1_weighted_average >= TIER1_FLOOR,
        f"Tier 1 weighted average {card.tier1_weighted_average:.2f} "
        f"vs >= {TIER1_FLOOR} floor"))

    # Rule 7 — no Tier 1 indicator scored at 1 (Rule 2 flag carries it)
    results.append(RuleResult(
        "Rule 7", card.flags.rule2 == "OK",
        "no Tier 1 indicator at 1" if card.flags.rule2 == "OK"
        else "a Tier 1 indicator is scored 1 (Rule 2 REVIEW flag)"))

    # Rule 8 — staging: above/reclaimed 200w MA -> standard; otherwise
    # 50% of normal initial size + 90-day technical review.  Unknown
    # technical state stages conservatively at the reduced size.
    staging = 1.0
    if tech.staging_ok:
        results.append(RuleResult(
            "Rule 8", True,
            "above or reclaimed 200-week MA — standard initial sizing"))
    else:
        staging = REDUCED_STAGING_FRACTION
        why = ("below 200-week MA, not reclaimed within 6 months"
               if tech.staging_ok is False else
               "200-week MA state unknown — staged conservatively")
        results.append(RuleResult(
            "Rule 8", True,
            f"{why} — 50% of normal initial size + {TECHNICAL_REVIEW}"))

    # Rule 9 — negative monthly MACD holds staging at the reduced size;
    # it never blocks entry.
    if tech.macd_positive_or_turning:
        results.append(RuleResult(
            "Rule 9", True, "monthly MACD positive or turning positive — "
            "standard staging applies"))
    else:
        staging = min(staging, REDUCED_STAGING_FRACTION)
        why = ("monthly MACD negative" if tech.macd_positive_or_turning
               is False else "monthly MACD unknown")
        results.append(RuleResult(
            "Rule 9", True,
            f"{why} — initiate only at the reduced size (technical "
            "weakness alone shall not override a qualifying fundamental case)"))

    # Rule 10 — no near-term binary event unless the thesis includes it
    r10 = not binary_event_present or binary_event_in_thesis
    results.append(RuleResult(
        "Rule 10", r10,
        "no unaddressed near-term binary event" if r10 else
        "near-term binary event present and not explicitly in the thesis"))

    # Rules 11-12 — scenario documentation; absent documentation fails
    if scenarios is None:
        results.append(RuleResult(
            "Rule 11", False,
            "no documented Bear Case — implied-return floor unverified"))
        results.append(RuleResult(
            "Rule 12", False,
            "Bull/Base/Bear scenarios not documented"))
        bear_ok = False
    else:
        sc = check_scenarios(scenarios)
        r11 = [c for c in sc.checks if c.rule.startswith("Rule 11")]
        r12 = [c for c in sc.checks if c.rule == "Rule 12"]
        r11_bad = [c.description for c in r11 if not c.passed]
        r12_bad = [c.description for c in r12 if not c.passed]
        results.append(RuleResult(
            "Rule 11", not r11_bad,
            "Bear Case >= 0% and Base Case > 10%" if not r11_bad
            else "; ".join(r11_bad)))
        results.append(RuleResult(
            "Rule 12", not r12_bad,
            "scenario set complete, exactly one Likely = Base"
            if not r12_bad else "; ".join(r12_bad)))
        bear = scenarios.implied("bear")
        bear_ok = bear is not None and bear >= 0.0

    # Rule 13 — valuation ceiling
    if financial_sector:
        results.append(RuleResult(
            "Rule 13", sector_valuation_ok is True,
            "Part 10 / Rule 49 sector valuation substitute "
            + ("passed" if sector_valuation_ok is True else
               "failed" if sector_valuation_ok is False else
               "not evaluated — ceiling unverified")))
    elif p_fcf is None:
        results.append(RuleResult(
            "Rule 13", False,
            "P/FCF unavailable — valuation ceiling unverified "
            "(use the Part 10 substitute where P/FCF is not meaningful)"))
    elif p_fcf <= VALUATION_CEILING_P_FCF:
        results.append(RuleResult(
            "Rule 13", True,
            f"P/FCF {p_fcf:.1f}x within the 50x ceiling"))
    else:
        growth_ok = (consensus_fcf_growth_2yr is not None
                     and consensus_fcf_growth_2yr > VALUATION_CEILING_GROWTH_ESCAPE)
        ok = growth_ok and bear_ok
        results.append(RuleResult(
            "Rule 13", ok,
            f"P/FCF {p_fcf:.1f}x > 50x — "
            + ("both escape conditions met (consensus FCF growth > 30% "
               "for 2 years AND Bear Case >= 0%)" if ok else
               "requires consensus FCF growth > 30% annualized for 2 "
               "years AND a Bear Case >= 0%")))

    return EntryDecision(card.ticker, results, staging)
