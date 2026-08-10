"""Landry System v1.0 — Part 10 financial-sector hard rules (48-49).

The doc flags these thresholds as "reasoned defaults, not derived from
empirical backtesting … reviewed and confirmed before being relied
upon" — every result carries that caveat.

Rule 48 outputs feed ``composite_score(financial_cap_triggered=...)``;
Rule 49 is an entry-ceiling check the entry workflow consumes via
``entry_checklist(sector_valuation_ok=...)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

CAVEAT = ("threshold is a reasoned default, not empirically backtested — "
          "review before relying on it (Part 10)")

# Rule 48 floors/caps
BANK_CET1_MIN = 0.09          # CET1 below 9% caps composite at 65
INSURER_RBC_MIN = 3.50        # RBC below 350%
BROKER_LEVERAGE_MAX = 3.0     # net leverage > 3x tangible equity
ASSET_MGR_DEBT_EBITDA_MAX = 3.0

# Rule 49 valuation ceilings for new positions
BANK_PTBV_MAX = 2.5
INSURER_PB_MAX = 2.0
INSURER_PE_MAX = 20.0
BROKER_PE_MAX = 25.0

FINANCIAL_KINDS = ("bank", "insurer", "broker", "asset_manager")


@dataclass
class FinancialCapResult:
    triggered: bool               # True -> composite capped at 65 (Rule 48)
    rule: str
    detail: str


def rule48_cap(kind: str,
               cet1: Optional[float] = None,
               rbc: Optional[float] = None,
               net_leverage: Optional[float] = None,
               debt_ebitda: Optional[float] = None) -> FinancialCapResult:
    """Rule 48 balance-sheet caps. Ratios as decimals (CET1 0.09 = 9%,
    RBC 3.5 = 350%). A missing metric for the relevant kind triggers the
    cap — an unverified financial balance sheet cannot support Strong Buy
    (Low-Confidence principle, Part 10)."""
    if kind not in FINANCIAL_KINDS:
        raise ValueError(f"kind must be one of {FINANCIAL_KINDS}")
    if kind == "bank":
        if cet1 is None:
            return FinancialCapResult(True, "Rule 48",
                                      f"CET1 unavailable — cap applies; {CAVEAT}")
        bad = cet1 < BANK_CET1_MIN
        return FinancialCapResult(bad, "Rule 48",
                                  f"CET1 {cet1:.1%} vs 9% floor; {CAVEAT}")
    if kind == "insurer":
        if rbc is None:
            return FinancialCapResult(True, "Rule 48",
                                      f"RBC unavailable — cap applies; {CAVEAT}")
        bad = rbc < INSURER_RBC_MIN
        return FinancialCapResult(bad, "Rule 48",
                                  f"RBC {rbc:.0%} vs 350% floor; {CAVEAT}")
    # broker / asset manager
    if kind == "broker":
        if net_leverage is None:
            return FinancialCapResult(True, "Rule 48",
                                      f"net leverage unavailable — cap applies; {CAVEAT}")
        bad = net_leverage > BROKER_LEVERAGE_MAX
        return FinancialCapResult(bad, "Rule 48",
                                  f"net leverage {net_leverage:.1f}x vs 3x cap "
                                  f"(ex client matched-book); {CAVEAT}")
    if debt_ebitda is None:
        return FinancialCapResult(True, "Rule 48",
                                  f"Debt/EBITDA unavailable — cap applies; {CAVEAT}")
    bad = debt_ebitda > ASSET_MGR_DEBT_EBITDA_MAX
    return FinancialCapResult(bad, "Rule 48",
                              f"Debt/EBITDA {debt_ebitda:.1f}x vs 3x cap; {CAVEAT}")


@dataclass
class ValuationCeilingResult:
    ok: bool                      # feeds entry_checklist(sector_valuation_ok=...)
    rule: str
    detail: str


def rule49_valuation_ceiling(kind: str,
                             p_tbv: Optional[float] = None,
                             p_b: Optional[float] = None,
                             p_e: Optional[float] = None,
                             roe_growth_justified: bool = False,
                             bear_case_clears: bool = False
                             ) -> ValuationCeilingResult:
    """Rule 49: do not initiate a new financial-company position above the
    sector ceiling — unless BOTH a documented ROE/growth justification AND
    a tested sector-specific Bear Case clear the Part 4 threshold."""
    if kind not in FINANCIAL_KINDS:
        raise ValueError(f"kind must be one of {FINANCIAL_KINDS}")

    if kind == "bank":
        if p_tbv is None:
            return ValuationCeilingResult(False, "Rule 49",
                                          "P/TBV unavailable — ceiling unverified")
        over, desc = p_tbv > BANK_PTBV_MAX, f"P/TBV {p_tbv:.2f}x vs 2.5x"
    elif kind == "insurer":
        if p_b is None and p_e is None:
            return ValuationCeilingResult(False, "Rule 49",
                                          "P/B and P/E unavailable — ceiling unverified")
        over = (p_b is not None and p_b > INSURER_PB_MAX) or \
               (p_e is not None and p_e > INSURER_PE_MAX)
        desc = f"P/B {p_b}x vs 2.0x / P/E {p_e}x vs 20x"
    else:  # broker / asset manager
        if p_e is None:
            return ValuationCeilingResult(False, "Rule 49",
                                          "P/E unavailable — ceiling unverified")
        over, desc = p_e > BROKER_PE_MAX, f"P/E {p_e:.1f}x vs 25x"

    if not over:
        return ValuationCeilingResult(True, "Rule 49",
                                      f"{desc} — within ceiling; {CAVEAT}")
    if roe_growth_justified and bear_case_clears:
        return ValuationCeilingResult(True, "Rule 49",
                                      f"{desc} — over ceiling but documented "
                                      f"ROE/growth case AND sector Bear Case "
                                      f"both clear; {CAVEAT}")
    return ValuationCeilingResult(False, "Rule 49",
                                  f"{desc} — over ceiling and exception "
                                  "conditions not met; " + CAVEAT)
