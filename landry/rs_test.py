"""``landry rs-test``: did Relative Strength vs SPY, and its persistence, predict later returns vs SPY?

Raised 2026-10-10 (Alan): is Relative Strength under-weighted at 3%, especially when its trend runs the same way period
after period? This is the evidence step before any weight or rule change (Journal ``RS-WEIGHT-QUESTION``).

The signal is the Scoring rubric's own: the mean of the 26- and 52-week total-return gap vs SPY, banded 1-5 by
``data_auto.rs_score``. Readings are taken every ``step`` weeks (13 = quarterly). A reading is PERSISTENT when the gap has
had the same sign for three or more readings in a row and moved further that way since the last one (down and widening,
or up and rising). The outcome is the total-return gap vs SPY over the next 26 and 52 weeks.

Every comparison is made within a reading date (one group's mean minus another's on the same date) and then averaged
over dates spaced a full horizon apart, so one market regime or one overlapping window is not counted many times. The
t-statistic is that average over its standard error across those dates; under about 2 it is not distinguishable from zero.

Limits, printed with every report: the universe is today's scored and candidate lists, so names that failed and were
delisted are missing (flatters the laggards), and the years covered are mostly one long bull market."""
import math
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from landry.data_auto import rs_score
from landry.fidscan import rank_correlation

HORIZONS = (26, 52)
PERSIST_READINGS = 3


def panel(closes: pd.DataFrame, step: int = 13, horizons: Sequence[int] = HORIZONS, spy: str = "SPY") -> pd.DataFrame:
    """One row per (reading date, ticker): rs (blended gap), score, streak (signed count of same-sign readings in a row),
    moved (rs minus the previous reading's), state, and fwd_<h> (later gap vs SPY, NaN where the future is not in yet)."""
    if spy not in closes.columns:
        raise ValueError(f"no {spy} column in the closes")
    closes = closes.sort_index()
    bench = closes[spy]
    names = [c for c in closes.columns if c != spy]

    def gap(k: int) -> pd.DataFrame:
        return closes[names].pct_change(k, fill_method=None).sub(bench.pct_change(k, fill_method=None), axis=0)

    rs = (gap(26) + gap(52)) / 2
    fwd = {h: (closes[names].shift(-h) / closes[names] - 1).sub(bench.shift(-h) / bench - 1, axis=0) for h in horizons}
    pos = list(range(len(closes) - 1, 51, -step))[::-1]        # anchored on the latest week, so today's reading is one
    rows = []
    for t in names:
        streak, prev = 0, None
        for i in pos:
            v = rs[t].iat[i]
            if pd.isna(v):
                streak, prev = 0, None
                continue
            sign = 1 if v > 0 else -1 if v < 0 else 0
            streak = sign if sign == 0 or streak * sign <= 0 else streak + sign
            moved = None if prev is None else v - prev
            further = moved is not None and moved * sign > 0
            persistent = abs(streak) >= PERSIST_READINGS and further
            state = ("flat" if sign == 0 else
                     ("down" if sign < 0 else "up") + (", persistent" if persistent else ", not persistent"))
            row = {"date": closes.index[i], "ticker": t, "rs": float(v), "score": rs_score(float(v)), "streak": streak,
                   "moved": moved, "state": state}
            for h in horizons:
                f = fwd[h][t].iat[i]
                row[f"fwd_{h}"] = None if pd.isna(f) else float(f)
            rows.append(row)
            prev = v
    return pd.DataFrame(rows)


def _spaced(dates: List, step: int, h: int) -> List:
    """Reading dates a full horizon apart (newest kept), so the outcomes do not overlap."""
    k = max(1, math.ceil(h / step))
    return dates[::-1][::k][::-1]


def _tstat(x: List[float]) -> Optional[float]:
    if len(x) < 3:
        return None
    sd = float(np.std(x, ddof=1))
    return None if sd == 0 else float(np.mean(x)) / (sd / math.sqrt(len(x)))


def groups(p: pd.DataFrame, by: str, h: int) -> List[dict]:
    """Pooled description per group: n, mean and median later gap, share that beat SPY. Descriptive (windows overlap)."""
    col = f"fwd_{h}"
    d = p.dropna(subset=[col])
    out = []
    for key, g in d.groupby(by):
        out.append({"group": key, "n": len(g), "mean": float(g[col].mean()), "median": float(g[col].median()),
                    "beat": float((g[col] > 0).mean())})
    return out


def spread(p: pd.DataFrame, a, b, h: int, step: int, min_names: int = 3) -> dict:
    """Mean later gap of group ``a`` minus group ``b`` (boolean masks over ``p``), date by date on non-overlapping
    dates; then the average, its t-statistic, the share of dates above zero and the date count."""
    col = f"fwd_{h}"
    d = p[p[col].notna()]
    vals = []
    for dt_ in _spaced(sorted(d["date"].unique()), step, h):
        day = d[d["date"] == dt_]
        ga, gb = day[a.loc[day.index]][col], day[b.loc[day.index]][col]
        if len(ga) >= min_names and len(gb) >= min_names:
            vals.append(float(ga.mean() - gb.mean()))
    return {"mean": float(np.mean(vals)) if vals else None, "t": _tstat(vals),
            "positive": float(np.mean([v > 0 for v in vals])) if vals else None, "dates": len(vals)}


def information_coefficient(p: pd.DataFrame, h: int, step: int, min_names: int = 8) -> dict:
    """Rank correlation between rs and the later gap, per non-overlapping date; then mean, t, share positive."""
    col = f"fwd_{h}"
    d = p[p[col].notna()]
    ics = []
    for dt_ in _spaced(sorted(d["date"].unique()), step, h):
        day = d[d["date"] == dt_]
        if len(day) >= min_names:
            ic = rank_correlation(list(day["rs"]), list(day[col]))
            if ic is not None:
                ics.append(ic)
    return {"mean": float(np.mean(ics)) if ics else None, "t": _tstat(ics),
            "positive": float(np.mean([v > 0 for v in ics])) if ics else None, "dates": len(ics)}


def tests(p: pd.DataFrame, h: int, step: int) -> Dict[str, dict]:
    """The comparisons the question turns on."""
    down, up = p["rs"] < 0, p["rs"] > 0
    pd_, pu = p["state"] == "down, persistent", p["state"] == "up, persistent"
    return {
        "leaders (score 4-5) minus laggards (score 1-2)": spread(p, p["score"] >= 4, p["score"] <= 2, h, step),
        "persistent down minus other down": spread(p, pd_, down & ~pd_, h, step),
        "persistent up minus other up": spread(p, pu, up & ~pu, h, step),
        "persistent up minus persistent down": spread(p, pu, pd_, h, step),
        "persistent down minus everything else": spread(p, pd_, ~pd_, h, step),
    }


def format_report(closes: pd.DataFrame, step: int = 13, horizons: Sequence[int] = HORIZONS) -> str:
    p = panel(closes, step, horizons)
    if p.empty:
        return "rs-test: no readings (need more than 52 weeks of closes)"
    pct = lambda v: "   n/a" if v is None else f"{v * 100:+6.1f}%"
    num = lambda v: " n/a" if v is None else f"{v:+.2f}"
    L = [f"Relative Strength test: {p['ticker'].nunique()} names, readings every {step} weeks "
         f"{p['date'].min():%Y-%m-%d} to {p['date'].max():%Y-%m-%d}, {len(p)} readings",
         "signal = mean of the 26- and 52-week total-return gap vs SPY (the Scoring rubric); outcome = later gap vs SPY",
         f"persistent = same sign {PERSIST_READINGS}+ readings in a row AND further that way than the last reading"]
    for h in horizons:
        L.append(f"\n--- next {h} weeks ---")
        ic = information_coefficient(p, h, step)
        L.append(f"rank correlation of the signal with the later gap: mean {num(ic['mean'])}, t {num(ic['t'])}, "
                 f"positive on {pct(ic['positive']).strip()} of {ic['dates']} non-overlapping dates")
        L.append("by Relative Strength score (pooled, descriptive):   n    mean  median  beat SPY")
        for g in groups(p, "score", h):
            L.append(f"  score {g['group']}                                    {g['n']:>5} {pct(g['mean'])} {pct(g['median'])}  {g['beat'] * 100:5.1f}%")
        L.append("by state (pooled, descriptive):")
        for g in groups(p, "state", h):
            L.append(f"  {g['group']:26}                 {g['n']:>5} {pct(g['mean'])} {pct(g['median'])}  {g['beat'] * 100:5.1f}%")
        L.append("same-date comparisons on non-overlapping dates:        mean      t   dates>0  dates")
        for name, s in tests(p, h, step).items():
            L.append(f"  {name:48} {pct(s['mean'])}  {num(s['t'])}  {pct(s['positive'])}  {s['dates']:>4}")
        dates = sorted(p["date"].unique())
        mid = dates[len(dates) // 2]
        halves = []
        for label, part in (("first half", p[p["date"] < mid]), ("second half", p[p["date"] >= mid])):
            s = spread(part, part["score"] >= 4, part["score"] <= 2, h, step)
            halves.append(f"{label} {pct(s['mean']).strip()} (t {num(s['t'])}, {s['dates']} dates)")
        L.append("  leaders minus laggards by half: " + "; ".join(halves))
    L.append("\nlimits: today's scored and candidate lists only (delisted failures are missing, which flatters the laggards); "
             "mostly one bull market; t under about 2 is not distinguishable from zero.")
    return "\n".join(L)
