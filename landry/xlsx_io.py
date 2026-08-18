"""Read the Scoring tab of a LANDRY_SYSTEM_WORKBOOK_*.xlsx.

Column map comes from the workbook's own Schema Reference tab:
Scoring -- rows 3-27, cols A-AK. A=Ticker, B=Company, C=Date,
D-M = Tier 1 (5 x score/conf pairs), N=Tier1 WtdAvg, O=Tier1 Contrib,
P-W = Tier 2 (4 x score/conf), X=Tier2 Contrib, Y-AD = Tier 3
(3 x score/conf), AE=Tier3 Contrib, AF=Composite, AG=Decision,
AH-AK = Rule 1-4 flags.

Phase 5 adds writing (export); Phase 1 only reads.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from landry.scoring import IndicatorScore

WORKBOOK_PREFIX = "LANDRY_SYSTEM_WORKBOOK_"
WORKBOOK_GLOB = f"{WORKBOOK_PREFIX}*.xlsx"


def pick_latest(paths: List[str]) -> Optional[str]:
    """Highest-numbered LANDRY_SYSTEM_WORKBOOK_<N>.xlsx among ``paths``, or None.

    Ignores non-numeric matches (e.g. ..._TEMPLATE_FINAL.xlsx) -- a plain
    lexical sort would rank 'T' above any digit and pick the blank template
    over the live workbook.
    """
    numbered = []
    for path in paths:
        stem = os.path.basename(path)[len(WORKBOOK_PREFIX):-len(".xlsx")]
        if stem.isdigit():
            numbered.append((int(stem), path))
    return max(numbered)[1] if numbered else None


def latest_workbook(repo_dir: str) -> Optional[str]:
    """Highest-numbered LANDRY_SYSTEM_WORKBOOK_<N>.xlsx in repo_dir, or None."""
    return pick_latest(glob.glob(os.path.join(repo_dir, WORKBOOK_GLOB)))


# (indicator, score column index 1-based); confidence is score col + 1
_SCORE_COLS = [
    ("fcf_yield_trend", 4),
    ("revenue_growth_consistency", 6),
    ("competitive_moat", 8),
    ("revenue_visibility", 10),
    ("fcf_margin_trend", 12),
    ("management_quality", 16),
    ("roic_vs_wacc", 18),
    ("relative_strength", 20),
    ("valuation_multiples", 22),
    ("technical_trend", 25),
    ("analyst_consensus", 27),
    ("volume_accumulation", 29),
]

_COL_TIER1_AVG, _COL_COMPOSITE, _COL_DECISION = 14, 32, 33
_COL_RULES = (34, 35, 36, 37)


@dataclass
class WorkbookRow:
    """One scored candidate as recorded in the workbook."""
    ticker: str
    company: str
    date_scored: Optional[object]
    scores: Dict[str, IndicatorScore]          # only the indicators present
    tier1_weighted_average: Optional[float]    # workbook-computed (col N)
    composite: Optional[float]                 # workbook-computed (col AF)
    decision: Optional[str]                    # workbook-computed (col AG)
    rule_flags: tuple                          # workbook cols AH-AK


@dataclass
class Position:
    """One row of the Current Positions tab (equities and cash alike)."""
    account: str
    ticker: str
    description: str
    asset_class: str
    quantity: float
    market_value: float
    pct_of_portfolio: float
    unrealized_pct: Optional[float] = None   # col J, fraction (-0.15 = -15%)
    notes: str = ""


def read_positions(path: str, sheet: str = "Current Positions") -> List[Position]:
    """Current Positions tab: A=Account, B=Ticker, C=Description,
    D=AssetClass, E=Quantity, G=MarketValue, L=%Combined, M=Notes.
    Skips subtotal/total rows (blank ticker) and zero-quantity rows
    (sold positions kept for the record)."""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet]
    out: List[Position] = []
    for r in ws.iter_rows(min_row=3, values_only=True):
        if not r or not r[0] or not r[1]:
            continue
        qty = float(r[4] or 0)
        if qty <= 0:
            continue
        out.append(Position(
            account=str(r[0]).strip(), ticker=str(r[1]).strip(),
            description=str(r[2] or "").strip(),
            asset_class=str(r[3] or "").strip(), quantity=qty,
            market_value=float(r[6] or 0),
            pct_of_portfolio=float(r[11] or 0),
            unrealized_pct=float(r[9]) if r[9] is not None else None,
            notes=str(r[12] or "").strip(),
        ))
    wb.close()
    return out


def equity_weights(positions: List[Position]) -> dict:
    """{ticker: combined portfolio fraction} for Equity rows, summed
    across accounts (NVDA appears in both)."""
    out: dict = {}
    for p in positions:
        if p.asset_class == "Equity":
            out[p.ticker] = out.get(p.ticker, 0.0) + p.pct_of_portfolio
    return out


def total_portfolio_value(positions: List[Position]) -> float:
    """Sum of market_value across every position and account (Part 7's
    drawdown series is portfolio-wide, cash included)."""
    return sum(p.market_value for p in positions)


def read_drawdown_log(path: str,
                      sheet: str = "Portfolio Drawdown Log") -> "pd.Series":
    """Date -> Portfolio Value from the tab's own manually-entered history
    (cols A, B; data from row 3). Empty Series if nothing's been logged
    yet. Only the raw value column is read -- Running Peak/Drawdown/Status
    are workbook formulas derived from it, not a second source of truth."""
    import openpyxl
    import pandas as pd

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet]
    dates, values = [], []
    for r in ws.iter_rows(min_row=3, values_only=True):
        if not r or r[0] is None or r[1] is None:
            continue
        dates.append(r[0])
        values.append(float(r[1]))
    wb.close()
    return pd.Series(values, index=pd.DatetimeIndex(dates),
                     name="value").sort_index()


def read_scoring_tab(path: str, sheet: str = "Scoring") -> List[WorkbookRow]:
    import openpyxl  # local import: optional dependency for non-Excel use

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet]
    rows: List[WorkbookRow] = []
    for r in ws.iter_rows(min_row=3, values_only=True):
        if not r or not r[0]:
            continue
        scores: Dict[str, IndicatorScore] = {}
        for name, col in _SCORE_COLS:
            raw, conf = r[col - 1], r[col]
            if raw is None:
                continue
            scores[name] = IndicatorScore(int(raw), str(conf or "M"))
        rows.append(WorkbookRow(
            ticker=str(r[0]).strip(),
            company=str(r[1] or "").strip(),
            date_scored=r[2],
            scores=scores,
            tier1_weighted_average=(float(r[_COL_TIER1_AVG - 1])
                                    if r[_COL_TIER1_AVG - 1] is not None else None),
            composite=(float(r[_COL_COMPOSITE - 1])
                       if r[_COL_COMPOSITE - 1] is not None else None),
            decision=(str(r[_COL_DECISION - 1]).strip()
                      if r[_COL_DECISION - 1] else None),
            rule_flags=tuple(str(r[c - 1] or "OK").strip() for c in _COL_RULES),
        ))
    wb.close()
    return rows
