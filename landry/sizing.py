"""Landry System v1.0 — Part 5 position sizing (Rules 14-20) plus the
Part 7 interactions that modify a new position's first purchase.

Base table (Part 5): 80-100 -> 5% max initial / 8% max full;
65-79 -> 3% / 5%; below 65 -> 0% / 0% ("If fewer than 12 qualifying
candidates ... do not lower the scoring threshold" — Rule 18).

Sizing modifiers are +/-1% each with a named reason; the Rule 20 beta
overlay reduces the *maximum full-position size* only (landry.data_auto
provides the v1.0 banding).  Staging multipliers — Rule 8 entry staging,
Rule 36 cluster expansion, and the Part 7 macro overlay — stack
multiplicatively on the INITIAL size only, and the Part 7 drawdown
regime gates initiation outright (Elevated: Strong Buy only; Severe/
Critical: no new initiation regardless of score).

All percentages are portfolio percent (5.0 == 5%).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Union

from landry.data_auto import beta_size_reduction
from landry.drawdown import BANDS
from landry.macro import MacroConditions, macro_cash_floor, new_position_size_multiplier

# Part 5 sizing table
SIZE_BANDS = (
    (80.0, 5.0, 8.0),    # Strong Buy
    (65.0, 3.0, 5.0),    # Buy
)
POSITION_CAP_PCT = 8.0       # Rule 14: no position > 8% at cost
SECTOR_CAP_PCT = 25.0        # Rule 15
SECTOR_CAP_MACRO_RISK_PCT = 15.0   # Part 7 sector-specific risk overlay
MIN_QUALIFYING_POSITIONS = 12      # Rule 16 (a floor, not forced — Rule 18)
CASH_FLOOR_PCT = 5.0         # Rule 17
CASH_CEILING_PCT = 15.0      # Rule 17
DEBT_FCF_MODIFIER_THRESHOLD = 4.0  # -1% fragility discount above 4x
CLUSTER_STAGING_FRACTION = 0.5     # Rule 36


@dataclass
class SizingResult:
    composite: float
    base_initial_pct: float
    base_full_pct: float
    max_initial_pct: float       # after modifiers/beta/caps
    max_full_pct: float          # after modifiers/beta/caps (Rule 14 <= 8)
    staged_initial_pct: float    # first purchase after staging + gates
    adjustments: List[str] = field(default_factory=list)
    blocked: bool = False        # drawdown/macro gate blocks initiation

    @property
    def initiation_allowed(self) -> bool:
        return not self.blocked and self.staged_initial_pct > 0.0


def position_size(composite: float, *,
                  insider_buying: bool = False,
                  debt_to_fcf: Optional[float] = None,
                  customer_concentration_over_30pct: bool = False,
                  macro_headwind_sector: bool = False,
                  beta: Optional[float] = None,
                  macro: Optional[MacroConditions] = None,
                  drawdown_level: int = 0,
                  expands_cluster: bool = False,
                  staging_fraction: float = 1.0) -> SizingResult:
    """Size one new position per Part 5 + the Part 7 overlays.

    ``staging_fraction`` is the Rule 8/9 entry staging (1.0 or 0.5) from
    landry.entry; ``expands_cluster`` the Rule 36 flag from
    landry.data_auto.expands_flagged_cluster; ``drawdown_level`` the
    active Part 7 regime (landry.drawdown).
    """
    adj: List[str] = []

    # base band
    base_initial = base_full = 0.0
    for floor, ini, full in SIZE_BANDS:
        if composite >= floor:
            base_initial, base_full = ini, full
            break
    if base_full == 0.0:
        adj.append(f"Rule 18: Composite {composite:.0f} < 65 — 0% size; "
                   "never lower the threshold to force investment")
        return SizingResult(composite, 0.0, 0.0, 0.0, 0.0, 0.0, adj)

    initial, full = base_initial, base_full

    # sizing modifiers (+/-1% each, applied to max initial and max full)
    if insider_buying:
        initial += 1.0
        full += 1.0
        adj.append("Part 5 modifier: insider ownership >5% with recent "
                   "open-market purchases — +1%")
    if debt_to_fcf is not None and debt_to_fcf > DEBT_FCF_MODIFIER_THRESHOLD:
        initial -= 1.0
        full -= 1.0
        adj.append(f"Part 5 modifier: Debt/FCF {debt_to_fcf:.1f}x > 4x — "
                   "-1% fragility discount")
    if customer_concentration_over_30pct:
        initial -= 1.0
        full -= 1.0
        adj.append("Part 5 modifier: single customer >30% of revenue — -1%")
    if macro_headwind_sector:
        initial -= 1.0
        full -= 1.0
        adj.append("Part 5 modifier / Part 7: macro headwind active in "
                   "sector — -1% to new positions")

    # Rule 20 beta overlay (max full only, capped at 2%)
    beta_cut = beta_size_reduction(beta)
    if beta_cut:
        full -= beta_cut
        adj.append(f"Rule 20: beta {beta:.2f} — max full position "
                   f"-{beta_cut}% (capped at 2%)")

    # caps and floors: Rule 14 hard 8% cap, nothing below 0, initial <= full
    if full > POSITION_CAP_PCT:
        adj.append(f"Rule 14: max full position capped at {POSITION_CAP_PCT:.0f}%")
    full = min(max(full, 0.0), POSITION_CAP_PCT)
    initial = min(max(initial, 0.0), full)

    # Part 7 drawdown gates on initiation
    blocked = False
    staged = initial
    if drawdown_level >= 2:
        blocked = True
        staged = 0.0
        adj.append(f"Part 7 drawdown ({BANDS[min(drawdown_level, 3)][1]}): "
                   "no new position initiation regardless of Composite Score")
    elif drawdown_level == 1 and composite < 80.0:
        blocked = True
        staged = 0.0
        adj.append("Part 7 drawdown (Elevated): new positions scored below "
                   "80 suspended — Strong Buy only")

    if not blocked:
        # staging multipliers stack multiplicatively on the initial size
        if staging_fraction != 1.0:
            staged *= staging_fraction
            adj.append(f"Rule 8/9 staging: initial size x{staging_fraction:g}"
                       f" + 90-day technical review")
        if expands_cluster:
            staged *= CLUSTER_STAGING_FRACTION
            adj.append("Rule 36: expands a flagged correlation cluster — "
                       "initiate at 50% of normal initial size")
        if macro is not None:
            m = new_position_size_multiplier(macro)
            if m == 0.0:
                blocked = True
                staged = 0.0
                adj.append("Part 7 macro overlay: HY spreads > 600 bps — "
                           "pause all new entries")
            elif m != 1.0:
                staged *= m
                adj.append(f"Part 7 macro overlay: SPY below 200-week MA — "
                           f"new position sizes x{m:g}")

    return SizingResult(composite, base_initial, base_full,
                        initial, full, staged, adj, blocked)


# --------------------------------------------------------------------------- #
# Portfolio-level constraints (Rules 14-17 at the book level)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Position:
    ticker: str
    pct_at_cost: float               # portfolio percent at cost
    composite: Optional[float] = None


PositionLike = Union[Position, Mapping]


def _pos(p: PositionLike) -> Position:
    if isinstance(p, Position):
        return p
    return Position(p["ticker"], p["pct_at_cost"], p.get("composite"))


def effective_cash_floor(macro: Optional[MacroConditions] = None,
                         drawdown_level: int = 0) -> float:
    """Rule 17 floor raised by the Part 7 overlays: max of the 5% base,
    the macro floor (10% when the curve is inverted), and the drawdown
    band floor (15/20/25% — landry.drawdown.BANDS)."""
    floor = CASH_FLOOR_PCT
    if macro is not None:
        floor = max(floor, macro_cash_floor(macro))
    floor = max(floor, BANDS[min(max(drawdown_level, 0), 3)][2])
    return floor


def portfolio_constraints(positions: Sequence[PositionLike],
                          sector_of: Mapping[str, str],
                          cash_pct: float,
                          macro: Optional[MacroConditions] = None,
                          drawdown_level: int = 0) -> List[str]:
    """Portfolio-level Hard Rules 14-17; returns violations with rule
    citations (empty list == compliant).

    Rule 16 is reported as a note-style violation: 12 qualifying
    positions is a floor, never forced — the remedy is holding cash
    (Rule 18), not lowering the threshold.  The Rule 17 15% ceiling is
    enforced only while the effective floor stays below it; a Part 7
    regime that demands >= 15% cash supersedes the ceiling.
    """
    pos = [_pos(p) for p in positions]
    out: List[str] = []

    # Rule 14 — no single position > 8% at cost
    for p in pos:
        if p.pct_at_cost > POSITION_CAP_PCT:
            out.append(f"Rule 14: {p.ticker} is {p.pct_at_cost:.1f}% at cost "
                       f"> {POSITION_CAP_PCT:.0f}% position cap")

    # Rule 15 — sector caps (15% for a macro sector_risk sector)
    risky = set(macro.sector_risk) if macro is not None else set()
    by_sector: Dict[str, float] = {}
    for p in pos:
        s = sector_of.get(p.ticker, "Unknown")
        by_sector[s] = by_sector.get(s, 0.0) + p.pct_at_cost
    for s, w in sorted(by_sector.items()):
        cap = SECTOR_CAP_MACRO_RISK_PCT if s in risky else SECTOR_CAP_PCT
        if w > cap:
            cite = ("Rule 15 / Part 7 overlay" if s in risky else "Rule 15")
            out.append(f"{cite}: sector {s} is {w:.1f}% "
                       f"> {cap:.0f}% cap")

    # Rule 16 — minimum 12 qualifying positions (floor, not forced)
    qualifying = sum(1 for p in pos
                     if p.composite is not None and p.composite >= 65.0)
    if qualifying < MIN_QUALIFYING_POSITIONS:
        out.append(f"Rule 16: only {qualifying} qualifying positions "
                   f"(Composite >= 65) vs the 12-position floor — hold the "
                   "difference in cash, do not lower the threshold (Rule 18)")

    # Rule 17 — cash band, floor raised by Part 7
    floor = effective_cash_floor(macro, drawdown_level)
    if cash_pct < floor:
        raised = "" if floor == CASH_FLOOR_PCT else " (floor raised by Part 7)"
        out.append(f"Rule 17: cash {cash_pct:.1f}% below the "
                   f"{floor:.0f}% floor{raised}")
    elif floor < CASH_CEILING_PCT and cash_pct > CASH_CEILING_PCT:
        out.append(f"Rule 17: cash {cash_pct:.1f}% above the "
                   f"{CASH_CEILING_PCT:.0f}% ceiling")

    return out
