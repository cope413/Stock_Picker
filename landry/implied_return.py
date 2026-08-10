"""Landry System v1.0 — Part 4 Standardized Implied-Return Methodology.

"For each scenario, calculate Expected annualized total return =
[(Estimated Year-5 equity value + cumulative cash distributions) /
current equity value]^(1/5) - 1."  Year-5 equity value is Year-5
normalized FCF x a justified terminal P/FCF multiple, and "the terminal
multiple may not exceed the lower of the current multiple or the
company's normalized 5-year median unless a written structural-change
case is approved."

Scenario documentation (Rules 11-12): Bull/Base/Bear together, each with
its own implied 5-year return and a Likely/Possible/Unlikely tag;
exactly one scenario tagged Likely, and it "shall agree with whichever
projection is used as the Base Case".  Numeric probabilities are never
assigned to the tags.

Analyst judgments (the growth haircut behind a Bear case, the
trough-cycle assumption for cyclicals) arrive as documented flags; this
module never invents them (Part 12 automation boundary).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

# Part 4 entry note: "Base Case must exceed 10%; Bear Case must be 0% or
# higher. If either requirement fails, do not enter regardless of
# indicator scores."
BASE_MIN_RETURN = 0.10
BEAR_MIN_RETURN = 0.0

HORIZON_YEARS = 5

SCENARIO_NAMES = ("base", "bear", "bull")
TAGS = ("L", "P", "U")          # Likely / Possible / Unlikely


def implied_return(current_price: float,
                   year5_fcf_per_share: float,
                   terminal_multiple: float,
                   cum_distributions_per_share: float = 0.0) -> float:
    """Part 4: [(Year-5 value + cumulative distributions) / current
    price]^(1/5) - 1, on per-share values.

    A non-positive total terminal value returns -1.0 (total loss) rather
    than a complex root.
    """
    if current_price <= 0:
        raise ValueError(f"current_price must be positive, got {current_price!r}")
    total = year5_fcf_per_share * terminal_multiple + cum_distributions_per_share
    if total <= 0:
        return -1.0
    return (total / current_price) ** (1.0 / HORIZON_YEARS) - 1.0


def terminal_multiple_cap(current_multiple: float,
                          median_5y_multiple: float,
                          structural_change_approved: bool = False) -> float:
    """Part 4: the terminal multiple "may not exceed the lower of the
    current multiple or the company's normalized 5-year median unless a
    written structural-change case is approved."

    Returns the maximum permissible terminal multiple; an approved
    structural-change case removes the cap (math.inf).
    """
    if structural_change_approved:
        return math.inf
    return min(current_multiple, median_5y_multiple)


# --------------------------------------------------------------------------- #
# Scenario Range (Bull / Base / Bear) — Rules 11-12
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Scenario:
    """One documented scenario with its own implied-return inputs and a
    Likely/Possible/Unlikely tag ("L"/"P"/"U")."""
    name: str                        # "base" | "bear" | "bull"
    year5_fcf_ps: float
    terminal_multiple: float
    cum_distributions_ps: float = 0.0
    tag: str = "P"                   # "L" | "P" | "U"

    def __post_init__(self):
        if self.name not in SCENARIO_NAMES:
            raise ValueError(f"scenario name must be one of {SCENARIO_NAMES}, "
                             f"got {self.name!r}")
        if self.tag not in TAGS:
            raise ValueError(f"tag must be one of {TAGS} "
                             f"(Likely/Possible/Unlikely), got {self.tag!r}")

    def implied_return(self, current_price: float) -> float:
        return implied_return(current_price, self.year5_fcf_ps,
                              self.terminal_multiple,
                              self.cum_distributions_ps)


@dataclass
class ScenarioSet:
    """The Bull/Base/Bear documentation required at entry and each annual
    re-score (Rule 12).

    ``bear_assumption_documented`` records the analyst attestation Rule 11
    requires: for non-cyclicals, that the Bear Case reduces Base Case FCF
    growth by at least 50% with a conservative terminal multiple; for
    cyclicals (``cyclical=True``), that a documented trough-cycle
    assumption was used.  The flag is accepted as data, not recomputed
    (the growth path behind ``year5_fcf_ps`` is analyst judgment).
    """
    current_price: float
    scenarios: Sequence[Scenario]
    cyclical: bool = False
    bear_assumption_documented: bool = False

    def get(self, name: str) -> Optional[Scenario]:
        for s in self.scenarios:
            if s.name == name:
                return s
        return None

    def implied(self, name: str) -> Optional[float]:
        s = self.get(name)
        return None if s is None else s.implied_return(self.current_price)


@dataclass(frozen=True)
class Check:
    rule: str            # citation, e.g. "Rule 11"
    description: str
    passed: bool


@dataclass
class ScenarioCheckResult:
    checks: List[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> List[Check]:
        return [c for c in self.checks if not c.passed]

    def rule_passes(self, rule_prefix: str) -> bool:
        """All checks citing ``rule_prefix`` pass (and at least one exists)."""
        hits = [c for c in self.checks if c.rule.startswith(rule_prefix)]
        return bool(hits) and all(c.passed for c in hits)


def check_scenarios(ss: ScenarioSet) -> ScenarioCheckResult:
    """Run the Rule 11/12 scenario checks and the Part 4 entry-note
    return thresholds; one Check per requirement, with rule citations."""
    checks: List[Check] = []

    # Rule 12 — all three scenarios present
    present = {s.name for s in ss.scenarios}
    missing = [n for n in SCENARIO_NAMES if n not in present]
    checks.append(Check(
        "Rule 12", "Bull, Base, and Bear scenarios all documented",
        not missing))

    # Rule 12 — exactly one scenario tagged Likely
    likely = [s for s in ss.scenarios if s.tag == "L"]
    checks.append(Check(
        "Rule 12", "exactly one scenario tagged Likely",
        len(likely) == 1))

    # Rule 12 / Part 4 — the Likely tag "shall agree with whichever
    # projection is used as the Base Case"
    checks.append(Check(
        "Rule 12", "the Likely scenario is the Base Case",
        len(likely) == 1 and likely[0].name == "base"))

    # Rule 11 — Bear Case implied return >= 0%
    bear_ret = ss.implied("bear")
    checks.append(Check(
        "Rule 11",
        "Bear Case implied 5-year return >= 0%",
        bear_ret is not None and bear_ret >= BEAR_MIN_RETURN))

    # Rule 11 — bear assumption documented (>=50% growth haircut for
    # non-cyclicals / trough-cycle assumption for cyclicals)
    kind = ("documented trough-cycle assumption" if ss.cyclical else
            "Base Case FCF growth reduced >= 50%, conservative terminal multiple")
    checks.append(Check(
        "Rule 11", f"Bear Case assumption documented ({kind})",
        ss.bear_assumption_documented))

    # Part 4 entry note (grouped with Rule 11 by the Entry Checklist) —
    # Base Case implied return > 10%
    base_ret = ss.implied("base")
    checks.append(Check(
        "Rule 11 (Part 4 note)",
        "Base Case implied 5-year return > 10%",
        base_ret is not None and base_ret > BASE_MIN_RETURN))

    return ScenarioCheckResult(checks)
