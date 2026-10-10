"""``landry rs-trend``: the Relative Strength TREND per stock, and the two uses Alan approved on 2026-10-10 (Open Items #47).

Relative Strength vs SPY is scored from one snapshot (the mean of the 26- and 52-week gap). This reads the same gap
every 13 weeks back (``data_auto.rs_trend``) and reports, per stock, the 3 / 6 / 12-month gaps, the last quarterly
readings, the streak and the state.

* HELD and PERSISTENT DOWN with a Relative Strength score of 2 or lower: the Rule 5 review extended to persistence.
  No additions (DCA included) and a written re-underwrite within 10 trading days, repeated every 90 days while it lasts.
  A review trigger, never a sale on price alone. (Persistent-down with a score of 3 was 10 of 423 cases in the test and
  not weak afterwards, so the score floor costs nothing and keeps a -1% drift from tripping a review.)
* A CANDIDATE and PERSISTENT UP: the tie-breaker among names that already pass every gate for a tranche. A candidate
  that is persistent down needs the Rule 5 review before a Buy / Strong Buy is relied on.

PERSISTENT = the same sign for three or more quarterly readings in a row AND further that way than the last reading.
Evidence and its limits: ``landry rs-test``, Journal ``RS-WEIGHT-QUESTION`` / ``RS-TREND-POLICY``. The composite weight
(3%) is unchanged."""
from typing import Dict, Iterable, List, Optional

import pandas as pd

from landry.data_auto import RelativeStrength, relative_strength

REVIEW_MAX_SCORE = 2


def action(rs: RelativeStrength, held: bool) -> str:
    if rs.persistent_down and rs.score is not None and rs.score <= REVIEW_MAX_SCORE:
        return ("RULE 5 (persistent): no additions; re-underwrite within 10 trading days" if held
                else "Rule 5 review before relying on Buy / Strong Buy")
    if rs.persistent_up and not held:
        return "tie-breaker: favored among gate-passing candidates"
    return ""


def rows(closes: pd.DataFrame, tickers: Iterable[str], held: Iterable[str], spy: str = "SPY") -> List[dict]:
    held = set(held)
    out = []
    for t in tickers:
        if t not in closes.columns:
            out.append({"ticker": t, "held": t in held, "rs": None, "action": "no price history"})
            continue
        rs = relative_strength(closes[t], closes[spy])
        out.append({"ticker": t, "held": t in held, "rs": rs, "action": action(rs, t in held)})
    return out


def persistent_down_held(rws: List[dict]) -> List[str]:
    return [r["ticker"] for r in rws if r["held"] and r["action"].startswith("RULE 5")]


def format_report(rws: List[dict]) -> str:
    pct = lambda v: "   n/a" if v is None else f"{v * 100:+6.1f}%"
    L = ["Relative Strength trend vs SPY (quarterly readings of the blended 6/12-month gap, newest first)",
         f"{'ticker':6} {'':4} {'score':>5} {'3mo':>7} {'6mo':>7} {'12mo':>7}  {'last 4 readings (pts)':24} {'streak':>6}  state / action"]
    for r in rws:
        rs: Optional[RelativeStrength] = r["rs"]
        if rs is None or rs.score is None:
            L.append(f"{r['ticker']:6} {'held' if r['held'] else '':4}  {r['action'] or 'not enough history'}")
            continue
        last = " ".join(f"{x * 100:+.0f}" for x in rs.readings[:4])
        L.append(f"{r['ticker']:6} {'held' if r['held'] else '':4} {rs.score:>5} {pct(rs.diff_3m)} {pct(rs.diff_6m)} {pct(rs.diff_12m)}  "
                 f"{last:24} {rs.streak:>+6}  {rs.trend}" + (f" -- {r['action']}" if r["action"] else ""))
    flagged = persistent_down_held(rws)
    L.append("held and persistent down (Rule 5 review, no additions): " + (", ".join(flagged) if flagged else "none"))
    return "\n".join(L)


def held_scored(path: str) -> List[str]:
    from landry import xlsx_io
    held = {p.ticker for p in xlsx_io.read_positions(path) if p.quantity}
    return [r.ticker for r in xlsx_io.read_scoring_tab(path) if r.ticker in held]


def summary(path: str) -> Dict[str, object]:
    """For the weekly report: the held names on the persistent-down review."""
    from landry.data_auto import fetch_daily, weekly_closes
    held = held_scored(path)
    closes = weekly_closes(fetch_daily(held + ["SPY"]))
    rws = rows(closes, held, held)
    return {"held": len(held), "persistent_down": persistent_down_held(rws),
            "persistent_up": [r["ticker"] for r in rws if r["rs"] is not None and r["rs"].persistent_up]}
