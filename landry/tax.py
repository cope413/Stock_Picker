"""Landry System v1.0 — Part 8 tax-aware portfolio management (Rules 38-42).

Part 8 governs HOW — never WHETHER — an already-justified trade executes.
That is structural here: every entry point takes a trade justification
produced elsewhere (a Part 6 trigger, a Part 5/7 cap breach, or a
documented discretionary trim). There is no API to originate a trade
from tax considerations (Rule 42).

Applies to taxable accounts only; the caller filters by account type.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

LONG_TERM_DAYS = 366          # > 1 year holding period
DELAY_WINDOW_DAYS = 30        # Rule 39 maximum discretionary delay

# trade reasons, by Part 10 conflict-resolution rank
FUNDAMENTAL_SELL = "fundamental_sell"      # Part 6 Rules 21-25 (+31/33 exits)
HARD_CAP_TRIM = "hard_cap_trim"            # Parts 5/7 mandated
DISCRETIONARY_TRIM = "discretionary_trim"  # valuation / sizing / rebalancing
_VALID_REASONS = (FUNDAMENTAL_SELL, HARD_CAP_TRIM, DISCRETIONARY_TRIM)


@dataclass
class Lot:
    """One tax lot of a position."""
    lot_id: str
    quantity: float
    cost_basis_per_share: float
    acquired: _dt.date

    def holding_days(self, asof: _dt.date) -> int:
        return (asof - self.acquired).days

    def is_long_term(self, asof: _dt.date) -> bool:
        return self.holding_days(asof) >= LONG_TERM_DAYS

    def days_to_long_term(self, asof: _dt.date) -> int:
        return max(0, LONG_TERM_DAYS - self.holding_days(asof))


# --------------------------------------------------------------------------- #
# Rules 38-39 — execution timing
# --------------------------------------------------------------------------- #

@dataclass
class TimingDecision:
    may_delay: bool
    max_delay_days: int
    rule: str
    reason: str


def execution_timing(trade_reason: str, lot: Lot, asof: _dt.date,
                     delay_violates_hard_rule: bool = False) -> TimingDecision:
    """Rule 38: a fundamentally-mandated sell (or hard-cap trim) executes
    within its required window regardless of holding period or tax lot.
    Rule 39: a discretionary trim may be delayed up to 30 days to let the
    lot reach long-term status, provided no hard rule is violated."""
    if trade_reason not in _VALID_REASONS:
        raise ValueError(f"unknown trade reason {trade_reason!r}")
    if trade_reason in (FUNDAMENTAL_SELL, HARD_CAP_TRIM):
        return TimingDecision(False, 0, "Rule 38",
                              "mandated sell/trim executes on its required "
                              "timeline regardless of holding period")
    d = lot.days_to_long_term(asof)
    if d == 0:
        return TimingDecision(False, 0, "Rule 39",
                              "lot already long-term — no reason to delay")
    if d > DELAY_WINDOW_DAYS:
        return TimingDecision(False, 0, "Rule 39",
                              f"{d} days to long-term exceeds the 30-day "
                              "delay window")
    if delay_violates_hard_rule:
        return TimingDecision(False, 0, "Rule 39",
                              "delay would violate a hard rule "
                              "(position/sector cap or drawdown response)")
    return TimingDecision(True, d, "Rule 39",
                          f"delay {d} days to capture long-term treatment")


# --------------------------------------------------------------------------- #
# Rule 40 — specific-lot selection
# --------------------------------------------------------------------------- #

@dataclass
class LotSelection:
    lots: List[Tuple[str, float]]          # (lot_id, shares to sell)
    estimated_tax: float
    note: str


def select_lots(lots: Sequence[Lot], shares_to_sell: float, price: float,
                asof: _dt.date, short_term_rate: float = 0.37,
                long_term_rate: float = 0.20,
                loss_carryforward: float = 0.0) -> LotSelection:
    """Rule 40: choose the lot combination expected to minimize after-tax
    cost. Greedy on per-share tax cost: losses first (most negative tax —
    harvest value), then long-term gains, then short-term gains. Do NOT
    assume highest basis is optimal (rates differ by holding period).
    Estimated tax is net of usable ``loss_carryforward`` (a simple offset
    — the analyst confirms specifics with the broker per the rule)."""
    if shares_to_sell <= 0:
        raise ValueError("shares_to_sell must be positive")
    if sum(l.quantity for l in lots) + 1e-9 < shares_to_sell:
        raise ValueError("not enough shares across lots")

    def per_share_tax(l: Lot) -> float:
        gain = price - l.cost_basis_per_share
        rate = long_term_rate if l.is_long_term(asof) else short_term_rate
        return gain * rate                  # negative = tax benefit (loss)

    remaining = shares_to_sell
    picks: List[Tuple[str, float]] = []
    tax = 0.0
    for l in sorted(lots, key=per_share_tax):
        if remaining <= 1e-9:
            break
        take = min(l.quantity, remaining)
        picks.append((l.lot_id, take))
        tax += per_share_tax(l) * take
        remaining -= take
    if tax > 0 and loss_carryforward > 0:
        tax = max(0.0, tax - loss_carryforward)
    return LotSelection(picks, round(tax, 2),
                        "confirm specific-lot instruction with the broker "
                        "before sale (Rule 40)")


# --------------------------------------------------------------------------- #
# Rule 41 — tax-loss harvesting gate
# --------------------------------------------------------------------------- #

@dataclass
class HarvestDecision:
    allowed: bool
    failures: List[str] = field(default_factory=list)


def harvest_gate(position_status: str, unrealized_loss: bool,
                 replacement_substantially_identical: bool,
                 coordinated_across_household: bool) -> HarvestDecision:
    """Rule 41: losses in Probationary Hold, Exit Review, or underwater
    positions may be harvested before a mandatory sell deadline only when
    the replacement is not substantially identical (wash sale) and the
    transaction is coordinated across household accounts."""
    fails = []
    eligible = position_status in ("Probationary Hold", "Exit Review") \
        or unrealized_loss
    if not eligible:
        fails.append("Rule 41: position is neither on watch status nor "
                     "carrying an unrealized loss")
    if replacement_substantially_identical:
        fails.append("Rule 41: replacement is substantially identical "
                     "(wash-sale risk)")
    if not coordinated_across_household:
        fails.append("Rule 41: transaction not coordinated across "
                     "household accounts")
    return HarvestDecision(not fails, fails)


# Rule 42 is structural: this module exposes no function that creates a
# trade — every entry point takes a justification produced by Parts 4-7.
RULE_42_NOTE = ("Tax considerations never independently trigger a trade; "
                "they only govern timing and lot selection of a decision "
                "already justified elsewhere.")
