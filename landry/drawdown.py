"""Landry System v1.0 — Part 7 portfolio drawdown response.

Implements the drawdown bands, the regime state machine (5 consecutive
trading days beyond a threshold to enter, 10 consecutive days inside the
less restrictive band to exit, high-water-mark reset), and the
cash-raising waterfall.

Doc-interpretation note (documented per Part 9): "a new high-water mark
resets the measurement" is implemented as an immediate return to Normal —
once the portfolio prints a new high, drawdown is zero by definition and
the hysteresis clock has nothing to measure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

import pandas as pd

# level, name, cash floor %, restriction on new deployment
BANDS = (
    (0, "Normal",   5.0,  "Standard sizing; 5-15% cash band (Part 5)"),
    (1, "Elevated", 15.0, "New positions: Strong Buy (score >= 80) only"),
    (2, "Severe",   20.0, "No new position initiation regardless of score"),
    (3, "Critical", 25.0, "No new positions; re-score full portfolio within 2 weeks"),
)

ENTER_DAYS = 5    # consecutive days beyond a threshold to enter a regime
EXIT_DAYS = 10    # consecutive days inside a less restrictive band to exit

CASH_RAISING_WATERFALL = [
    "1. Suspend new purchases as the band requires",
    "2. Retain dividends, interest, and ordinary sale proceeds",
    "3. Trim positions already above current position or sector caps",
    "4. Trim the lowest-scoring holdings not protected by Hold Through",
    "5. Trim the highest-beta, highest-correlation, or most-leveraged holdings",
    "(Sell a fundamentally intact core holding only after Critical has "
    "persisted 20 trading days and a documented review concludes aggregate "
    "risk remains excessive)",
]


def band_level(drawdown: float) -> int:
    """Band level for a drawdown given as a positive fraction (0.12 = 12%)."""
    dd = abs(drawdown)
    if dd < 0.10:
        return 0
    if dd < 0.20:
        return 1
    if dd < 0.30:
        return 2
    return 3


def band_info(level: int):
    return BANDS[level]


@dataclass
class RegimeDay:
    date: object
    value: float
    peak: float
    drawdown: float          # negative fraction (or 0)
    candidate_level: int     # where today's drawdown sits
    active_level: int        # regime after hysteresis
    active_status: str
    cash_floor_pct: float
    restriction: str
    new_high: bool


def track_regime(values: pd.Series) -> List[RegimeDay]:
    """Run the Part 7 state machine over daily portfolio values.

    * Escalation: active regime rises to level L only after 5 consecutive
      days with the candidate at or beyond L (the deepest such L wins).
    * De-escalation: after 10 consecutive days entirely inside bands less
      restrictive than the active one, the regime drops to the most
      restrictive band seen in those 10 days.
    * A new high-water mark resets to Normal immediately.

    ``values`` must be indexed by date, net of external flows (Part 7).
    """
    peak = float("-inf")
    active = 0
    history: List[int] = []      # candidate levels since last regime change
    out: List[RegimeDay] = []

    for dt, v in values.dropna().items():
        v = float(v)
        new_high = v > peak
        peak = max(peak, v)
        dd = v / peak - 1.0
        cand = band_level(dd)

        if new_high:
            active = 0
            history = []
        else:
            history.append(cand)
            if cand > active:
                # deepest level sustained for the last ENTER_DAYS days
                if len(history) >= ENTER_DAYS:
                    recent = history[-ENTER_DAYS:]
                    sustained = min(recent)
                    if sustained > active:
                        active = sustained
                        history = []
            elif cand < active:
                if len(history) >= EXIT_DAYS:
                    recent = history[-EXIT_DAYS:]
                    if max(recent) < active:
                        active = max(recent)
                        history = []
            else:
                history = []     # back at the active band: clocks reset

        lvl, status, floor, restr = BANDS[active]
        out.append(RegimeDay(
            date=dt, value=v, peak=peak, drawdown=round(dd, 6),
            candidate_level=cand, active_level=active, active_status=status,
            cash_floor_pct=floor, restriction=restr, new_high=new_high,
        ))
    return out


def regime_frame(values: pd.Series) -> pd.DataFrame:
    """track_regime as a DataFrame (mirrors the workbook Drawdown Log tab)."""
    days = track_regime(values)
    return pd.DataFrame([{
        "date": d.date, "value": d.value, "peak": d.peak,
        "drawdown": d.drawdown, "status": d.active_status,
        "cash_floor": f">={d.cash_floor_pct:.0f}%",
        "new_positions": d.restriction,
    } for d in days]).set_index("date")


def current_regime(values: pd.Series) -> Optional[RegimeDay]:
    days = track_regime(values)
    return days[-1] if days else None
