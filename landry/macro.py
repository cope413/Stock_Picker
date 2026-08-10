"""Landry System v1.0 — Part 7 macro-overlay conditions and their
prescribed effects.

The overlay adjusts sizing/urgency only; it never changes a business
score (Part 1 hierarchy). Conditions the price cache can answer
(SPY vs 200-week MA) are computed; rates/credit conditions come from a
best-effort FRED fetch or explicit manual input — when unknown they stay
None and the report says so rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# FRED series for the best-effort fetch
FRED_SERIES = {
    "curve_2s10s": "T10Y2Y",          # 10yr minus 2yr, %
    "hy_spread_bps": "BAMLH0A0HYM2",  # ICE BofA HY OAS, %
    "fed_funds": "DFF",
}


@dataclass
class MacroConditions:
    """Tri-state inputs: True / False / None (unknown)."""
    fed_tightening: Optional[bool] = None
    curve_inverted_3mo: Optional[bool] = None     # 2s/10s inverted > 3 months
    hy_spreads_over_600: Optional[bool] = None
    spy_below_200w_ma: Optional[bool] = None
    sector_risk: Dict[str, str] = field(default_factory=dict)  # sector -> note
    sources: Dict[str, str] = field(default_factory=dict)


@dataclass
class OverlayEffect:
    condition: str
    effects: List[str]


# Part 7 macro-overlay table, verbatim consequences
_EFFECTS = {
    "fed_tightening": OverlayEffect(
        "Rising rate environment (Fed tightening cycle active)",
        ["Reduce max position size by 1% for high-multiple growth (P/FCF > 40x)",
         "Favor FCF yield and low-debt names"]),
    "curve_inverted_3mo": OverlayEffect(
        "Inverted yield curve (2s/10s inverted > 3 months)",
        ["Raise cash floor to 10% minimum",
         "No new positions in cyclicals or leveraged businesses",
         "Favor defensive compounders"]),
    "hy_spreads_over_600": OverlayEffect(
        "Credit spreads widening rapidly (HY spreads > 600 bps)",
        ["Pause all new entries",
         "Reassess existing holdings with Debt/FCF > 3x",
         "Hold cash"]),
    "spy_below_200w_ma": OverlayEffect(
        "Broad market downtrend (SPY below 200-week MA)",
        ["Reduce all new position sizes by 50%",
         "Continue holding fundamentally intact positions",
         "Accumulate Watch List candidates for entry when trend recovers"]),
}

SECTOR_RISK_EFFECTS = [
    "Reduce sector cap from 25% to 15% for the affected sector",
    "Re-evaluate moat scores for regulatory dependency",
]


def active_effects(cond: MacroConditions) -> List[OverlayEffect]:
    """Effects for every condition currently True. Unknown (None)
    conditions produce no effect — the overlay never guesses."""
    out = [eff for key, eff in _EFFECTS.items() if getattr(cond, key) is True]
    for sector, note in cond.sector_risk.items():
        out.append(OverlayEffect(
            f"Sector-specific regulatory/geopolitical risk: {sector} ({note})",
            SECTOR_RISK_EFFECTS))
    return out


def unknown_conditions(cond: MacroConditions) -> List[str]:
    return [k for k in _EFFECTS if getattr(cond, k) is None]


def new_position_size_multiplier(cond: MacroConditions) -> float:
    """Combined staging multiplier for new positions (worst case wins):
    HY blowout pauses entries (0), SPY downtrend halves them (0.5)."""
    if cond.hy_spreads_over_600 is True:
        return 0.0
    if cond.spy_below_200w_ma is True:
        return 0.5
    return 1.0


def macro_cash_floor(cond: MacroConditions) -> float:
    """Cash floor implied by the overlay alone (Part 7 drawdown bands may
    raise it further; the caller takes the max)."""
    return 10.0 if cond.curve_inverted_3mo is True else 5.0


# --------------------------------------------------------------------------- #
# best-effort live inputs
# --------------------------------------------------------------------------- #

def spy_below_200w(daily_spy) -> Optional[bool]:
    """From the Layer 1 cache: is SPY's weekly close under its 200-week MA?"""
    from landry.data_auto import ma_200w_state
    above, _ = ma_200w_state(daily_spy["Close"])
    return None if above is None else not above


def fetch_fred(series_id: str, days: int = 200) -> Optional["object"]:
    """Fetch one FRED series via the public fredgraph CSV endpoint.
    Returns a pandas Series or None on any failure (offline, blocked...)."""
    import io
    import urllib.request

    import pandas as pd
    url = ("https://fred.stlouisfed.org/graph/fredgraph.csv"
           f"?id={series_id}")
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            df = pd.read_csv(io.BytesIO(r.read()))
        df.columns = ["date", "value"]
        df["date"] = pd.to_datetime(df["date"])
        s = (df.set_index("date")["value"]
               .replace(".", None).astype(float).dropna())
        return s.tail(days)
    except Exception:
        return None


def evaluate_curve_inversion(t10y2y) -> Optional[bool]:
    """True when 2s/10s has been inverted (negative) for > 3 months
    (~63 business days) continuously."""
    if t10y2y is None or len(t10y2y) < 63:
        return None
    return bool((t10y2y.tail(63) < 0).all())


def evaluate_hy_spreads(hy_oas_pct) -> Optional[bool]:
    """True when the latest HY OAS exceeds 600 bps (series is in %)."""
    if hy_oas_pct is None or not len(hy_oas_pct):
        return None
    return bool(float(hy_oas_pct.iloc[-1]) * 100.0 > 600.0)


def evaluate_fed_tightening(fed_funds) -> Optional[bool]:
    """True when the effective funds rate is >= 25bp above its level
    ~6 months ago (a hiking cycle in progress)."""
    if fed_funds is None or len(fed_funds) < 130:
        return None
    return bool(float(fed_funds.iloc[-1]) - float(fed_funds.iloc[-130]) >= 0.25)


def fetch_conditions(daily_spy=None) -> MacroConditions:
    """Assemble MacroConditions from whatever sources respond. Anything
    unreachable stays None (report it, don't guess it)."""
    cond = MacroConditions()
    if daily_spy is not None:
        cond.spy_below_200w_ma = spy_below_200w(daily_spy)
        cond.sources["spy_below_200w_ma"] = "layer1 cache"
    t10y2y = fetch_fred(FRED_SERIES["curve_2s10s"])
    cond.curve_inverted_3mo = evaluate_curve_inversion(t10y2y)
    hy = fetch_fred(FRED_SERIES["hy_spread_bps"])
    cond.hy_spreads_over_600 = evaluate_hy_spreads(hy)
    ff = fetch_fred(FRED_SERIES["fed_funds"])
    cond.fed_tightening = evaluate_fed_tightening(ff)
    for k, sid in FRED_SERIES.items():
        cond.sources[k] = f"FRED {sid}"
    return cond
