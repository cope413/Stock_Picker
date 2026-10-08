"""The stop-review ladder (Alan, 2026-10-08; Journal row 109): what the System does with a holding whose price keeps falling.

The rulebook has no price stop-loss -- Part 6's Hold Through says a decline of 20-30% with unchanged Tier 1/2 fundamentals is
noise -- and a test of mechanical stops on 110 stocks (Journal row 108) found they cost 3-14 points of average return and
whip-saw about 2.4 times for every loss they save. What was missing was an escalation path for a holding that is falling, has
weak relative strength and shows leading-indicator trouble before the next earnings date: ADBE sat 21% below cost with
Relative Strength at 1 and nothing in the System said "look again". The ladder adopted on 10/8 makes price force the work
without ever forcing a sale on price alone. Measured from the position's cost basis (Current Positions, every account) and the
weekly closes (Price History):

* RUNG 1 -- 20% or more below cost AND (Relative Strength vs SPY scored 1 -- behind the S&P by more than 15% over six months --
  OR the price under its 200-day average): no additions of any kind (DCA included) and a documented re-underwrite within 10
  trading days (fresh Tier 1 reread, the leading indicators for the business model, the Rule 5 review written in the Journal as
  reaffirmed, resized or referred). A reaffirmation lasts 90 days and is then repeated.
* RUNG 2 -- 30% or more below cost on the same confirmation AND the re-underwrite found deterioration in a leading indicator or
  cut a Tier 1/2 score: trim one third of the shares (as Rule 34 does for a Probationary Hold); a second confirmation at the
  next review trims another third.
* RUNG 3 -- 40% or more below cost with Relative Strength 1 and the price under its 200-day average for 8 consecutive weeks: Exit
  Review (sell within 30 days unless a documented 90-day remediation plan with a credible path back exists).

This module only REPORTS. It never trims, sells or edits a tab. What it cannot know -- whether a re-underwrite was done and what it
found -- lives in ``landry_stops.json`` (one entry per ticker: date, conclusion, whether deterioration was found, the next check,
the Journal row), written by ``python -m landry stops record``. The 200-day average is the average of the last 40 weekly closes;
Relative Strength is the 26-week return against SPY's when SPY's weekly closes are supplied (``--live`` fetches them) and the
recorded Relative Strength score on the Scoring tab otherwise, so the offline check the audit runs every Saturday leans on the
price-under-average leg.  ``python -m landry stops`` prints the status; ``landry audit`` fails (`stop_review_ladder`) whenever a
holding is on a rung without a current review, so the weekly routine's report says NEEDS ATTENTION until it is written.
"""

from __future__ import annotations

import bisect
import datetime as dt
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_FILE = os.path.join(REPO, "landry_stops.json")

RUNG1_LOSS = -0.20            # below cost
RUNG2_LOSS = -0.30
RUNG3_LOSS = -0.40
APPROACH = 0.03               # within three points of rung 1 and confirmed: shown as "approaching"
RS_FLOOR_PTS = -15.0          # Part 12: Relative Strength scores 1 when behind SPY by more than 15%
MA_WEEKS = 40                 # the 200-day average, from weekly closes
RS_WEEKS = 26                 # six months
RUNG3_WEEKS_UNDER = 8
REVIEW_DAYS = 14              # ten trading days
REVIEW_VALID_DAYS = 90        # a reaffirmation lasts 90 days
CONCLUSIONS = ("reaffirmed", "resized", "referred")

Closes = Sequence[Tuple[dt.date, float]]


class StopsError(RuntimeError):
    pass


# --------------------------------------------------------------------------- the registry

def load_registry(path: Optional[str] = None) -> Dict[str, dict]:
    """{ticker: review} from the registry file. A missing file is an empty registry."""
    path = path or REGISTRY_FILE
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        raw = json.load(f)
    return {t.upper(): dict(v) for t, v in raw.get("reviews", {}).items()}


def record_review(ticker: str, conclusion: str, *, on: Optional[dt.date] = None, adds_frozen: bool = True, deterioration: bool = False,
                  next_check: Optional[dt.date] = None, journal_row: Optional[int] = None, note: str = "",
                  path: Optional[str] = None) -> dict:
    """Write (replace) a ticker's review. The Journal entry is the record of the reasoning; this is the machine-readable pointer."""
    path = path or REGISTRY_FILE
    if conclusion not in CONCLUSIONS:
        raise StopsError(f"conclusion must be one of {', '.join(CONCLUSIONS)}")
    on = on or dt.date.today()
    entry = {"date": on.isoformat(), "conclusion": conclusion, "adds_frozen": bool(adds_frozen), "deterioration": bool(deterioration),
             "next_check": next_check.isoformat() if next_check else None, "journal_row": journal_row, "note": note}
    reg = {}
    if os.path.exists(path):
        with open(path) as f:
            reg = json.load(f)
    reg.setdefault("reviews", {})[ticker.upper()] = entry
    reg["policy"] = {"rung1": "20% below cost and (Relative Strength 1 or under the 200-day average): no additions, re-underwrite within 10 "
                              "trading days, repeat every 90 days", "rung2": "30% below cost and deterioration found: trim one third",
                     "rung3": "40% below cost, Relative Strength 1 and 8 weeks under the 200-day average: Exit Review",
                     "source": "Journal row 109 (Alan, 2026-10-08)"}
    with open(path, "w") as f:
        json.dump(reg, f, indent=1, sort_keys=True)
        f.write("\n")
    return entry


# --------------------------------------------------------------------------- the numbers

@dataclass
class Holding:
    ticker: str
    quantity: float
    cost: float                   # total cost basis, every account
    price: float                  # current price (Market Data, or live)
    weight: float                 # share of the combined portfolio
    closes: Closes = field(default_factory=list)       # weekly closes, oldest first
    rs_score: Optional[int] = None                      # the Relative Strength score recorded on the Scoring tab
    spy: Optional[Mapping[dt.date, float]] = None       # SPY's weekly closes, when supplied


def ma(closes: Closes, weeks: int = MA_WEEKS, end: Optional[int] = None) -> Optional[float]:
    """Average of the ``weeks`` closes ending at index ``end`` (inclusive; default the last)."""
    end = len(closes) - 1 if end is None else end
    if end + 1 < weeks:
        return None
    window = [c for _, c in closes[end + 1 - weeks:end + 1]]
    return sum(window) / weeks


def weeks_under(closes: Closes, weeks: int = MA_WEEKS) -> int:
    """How many of the most recent weeks in a row closed under their own 40-week average."""
    n = 0
    for i in range(len(closes) - 1, -1, -1):
        m = ma(closes, weeks, i)
        if m is None or closes[i][1] >= m:
            break
        n += 1
    return n


def _spy_at(spy: Mapping[dt.date, float], day: dt.date) -> Optional[float]:
    days = sorted(spy)
    i = bisect.bisect_right(days, day) - 1
    return spy[days[i]] if i >= 0 else None


def rs_points(closes: Closes, spy: Optional[Mapping[dt.date, float]], weeks: int = RS_WEEKS) -> Optional[float]:
    """The stock's return over ``weeks`` minus SPY's over the same dates, in percentage points (None without SPY or history)."""
    if not spy or len(closes) <= weeks:
        return None
    (d0, c0), (d1, c1) = closes[-weeks - 1], closes[-1]
    s0, s1 = _spy_at(spy, d0), _spy_at(spy, d1)
    if not (s0 and s1 and c0):
        return None
    return ((c1 / c0 - 1) - (s1 / s0 - 1)) * 100.0


@dataclass
class Status:
    ticker: str
    rung: int
    weight: float
    loss: float
    from_high: Optional[float]
    rs_pts: Optional[float]
    rs_score: Optional[int]
    rs1: bool
    under_ma: Optional[bool]
    weeks_under: int
    since: Optional[dt.date]
    review: Optional[dict]
    review_state: str                 # "none" (rung 0), "current", "due", "overdue", "expired"
    adds_frozen: bool
    action: str
    needs_attention: bool
    approaching: bool = False
    note: str = ""


def _since(h: Holding) -> Optional[dt.date]:
    """First week of the unbroken run, ending now, that sat 20% or more under cost AND under the 40-week average (best effort:
    cost per share is today's, so a position that was added to is measured as if it had always been this size)."""
    if h.quantity <= 0 or h.cost <= 0 or not h.closes:
        return None
    per_share = h.cost / h.quantity
    first = None
    for i in range(len(h.closes) - 1, -1, -1):
        d, c = h.closes[i]
        m = ma(h.closes, MA_WEEKS, i)
        if c / per_share - 1 <= RUNG1_LOSS + 1e-9 and m is not None and c < m:
            first = d
        else:
            break
    return first


def classify(h: Holding, review: Optional[dict] = None, today: Optional[dt.date] = None) -> Status:
    today = today or dt.date.today()
    loss = h.price * h.quantity / h.cost - 1.0 if h.cost > 0 else 0.0
    last = h.closes[-1][1] if h.closes else None
    m40 = ma(h.closes)
    under = (last < m40) if (last is not None and m40 is not None) else None
    wu = weeks_under(h.closes)
    rs = rs_points(h.closes, h.spy)
    rs1 = (rs < RS_FLOOR_PTS) if rs is not None else (h.rs_score == 1)
    confirm = bool(rs1 or under)
    high = max((c for _, c in h.closes[-52:]), default=None)
    from_high = (h.price / high - 1.0) if high else None
    eps = 1e-9                                              # 80.00 x 100 / 10,000 - 1 is -0.19999999999999996, and that is 20%
    if loss <= RUNG3_LOSS + eps and rs1 and wu >= RUNG3_WEEKS_UNDER:
        rung = 3
    elif loss <= RUNG2_LOSS + eps and confirm:
        rung = 2
    elif loss <= RUNG1_LOSS + eps and confirm:
        rung = 1
    else:
        rung = 0
    approaching = rung == 0 and confirm and RUNG1_LOSS + eps < loss <= RUNG1_LOSS + APPROACH
    since = _since(h) if rung >= 1 else None

    # --- the re-underwrite on file
    state, attention, note = "none", False, ""
    rdate = dt.date.fromisoformat(review["date"]) if review and review.get("date") else None
    nxt = dt.date.fromisoformat(review["next_check"]) if review and review.get("next_check") else None
    review_current = bool(rdate is not None and (today - rdate).days <= REVIEW_VALID_DAYS and (nxt is None or today <= nxt))
    if rung >= 1:
        if review is None or rdate is None:
            late = since is not None and (today - since).days > REVIEW_DAYS
            state, attention = ("overdue" if late else "due"), True
        elif not review_current:
            state, attention = "expired", True
        else:
            state = "current"
    # a current review that froze additions keeps them frozen even when the price wobbles back above 20% below cost
    frozen = rung >= 1 or bool(review and review.get("adds_frozen") and review_current)

    if rung == 0:
        action = "approaching rung 1: no additions without a look" if approaching else "no action"
    elif rung == 1:
        if state == "current":
            action = (f"no additions; re-underwrite on file ({review['conclusion']} {rdate:%m/%d}"
                      + (f", next check {nxt:%m/%d}" if nxt else "") + ")")
        elif state == "due":
            action = "no additions; RE-UNDERWRITE DUE within 10 trading days (fresh Tier 1 reread, leading indicators, Rule 5 review in the Journal)"
        elif state == "overdue":
            action = "no additions; RE-UNDERWRITE OVERDUE (more than 10 trading days on rung 1)"
        else:
            action = "no additions; the re-underwrite on file has EXPIRED (90 days, or past its next check): repeat it"
    elif rung == 2:
        if state == "current" and review.get("deterioration") and review.get("conclusion") != "resized":
            action, attention = "TRIM ONE THIRD (rung 2: 30% below cost and the re-underwrite found deterioration)", True
        elif state == "current":
            action = "no additions; the re-underwrite on file found no deterioration (trim a third if the next check does)"
        else:
            action, attention = f"no additions; rung 2: the re-underwrite for deterioration is {state.upper()}; trim one third if it is found", True
    else:
        if state == "current" and review.get("conclusion") == "referred":
            action = "Exit Review opened (referred): sell within 30 days unless the documented 90-day plan is approved"
        else:
            action, attention = "EXIT REVIEW (rung 3): sell within 30 days unless a documented 90-day remediation plan with a credible path back exists", True
    if rung >= 1 and h.spy is None and rs is None:
        note = "Relative Strength from the recorded Scoring score (no SPY series; --live fetches it)"
    return Status(h.ticker, rung, h.weight, loss, from_high, rs, h.rs_score, bool(rs1), under, wu, since, review, state, frozen, action,
                  attention, approaching, note)


# --------------------------------------------------------------------------- reading the workbook

def _cost_by_ticker(path: str) -> Dict[str, Tuple[float, float]]:
    """{ticker: (quantity, cost basis)} summed over every account, zero-quantity rows skipped. No row bound."""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb["Current Positions"]
    out: Dict[str, Tuple[float, float]] = {}
    for r in ws.iter_rows(min_row=3, values_only=True):
        if not r or not r[0] or not r[1] or not isinstance(r[1], str):
            continue
        qty = float(r[4] or 0)
        if qty <= 0 or r[7] is None:
            continue
        q0, c0 = out.get(r[1].strip(), (0.0, 0.0))
        out[r[1].strip()] = (q0 + qty, c0 + float(r[7]))
    wb.close()
    return out


def read_holdings(path: str, *, spy: Optional[Mapping[dt.date, float]] = None, live_prices: Optional[Mapping[str, float]] = None,
                  today: Optional[dt.date] = None) -> List[Holding]:
    """The held scored stocks (a holding with a Scoring row; ETFs and cash funds have none), from the workbook alone."""
    from landry import xlsx_io
    positions = xlsx_io.read_positions(path)
    total = xlsx_io.total_portfolio_value(positions)
    value: Dict[str, float] = {}
    for p in positions:
        if p.asset_class == "Equity":
            value[p.ticker] = value.get(p.ticker, 0.0) + p.market_value
    scored = {r.ticker: r for r in xlsx_io.read_scoring_tab(path)}
    prices = {m["ticker"]: m["price"] for m in xlsx_io.read_market_data(path) if m["price"] is not None}
    series: Dict[str, List[Tuple[dt.date, float]]] = {}
    for r in xlsx_io.read_price_history(path):
        series.setdefault(r["ticker"], []).append((r["week_ending"].date(), r["close"]))
    cost = _cost_by_ticker(path)
    today = today or dt.date.today()
    out = []
    for t in sorted(value):
        if t not in scored or t not in cost:
            continue
        closes = sorted(series.get(t, []))
        price = (live_prices or {}).get(t) or prices.get(t) or (closes[-1][1] if closes else None)
        if price is None:
            continue
        if live_prices and t in live_prices and closes and today > closes[-1][0]:
            closes = closes + [(today, float(live_prices[t]))]          # this week so far, as the last point
        rs = scored[t].scores.get("relative_strength")
        out.append(Holding(t, cost[t][0], cost[t][1], float(price), value[t] / total if total else 0.0, closes, rs.score if rs else None, spy))
    return out


def ladder(path: str, *, spy: Optional[Mapping[dt.date, float]] = None, live_prices: Optional[Mapping[str, float]] = None,
           registry: Optional[Mapping[str, dict]] = None, today: Optional[dt.date] = None) -> List[Status]:
    reg = registry if registry is not None else load_registry()
    today = today or dt.date.today()
    out = [classify(h, reg.get(h.ticker), today) for h in read_holdings(path, spy=spy, live_prices=live_prices, today=today)]
    return sorted(out, key=lambda s: (-s.rung, s.loss))


def live_inputs(tickers: Sequence[str]) -> Tuple[Dict[dt.date, float], Dict[str, float]]:
    """SPY's weekly closes and the latest close of each ticker, from Yahoo in ONE request (it rate-limits repeated ones)."""
    import yfinance as yf
    px = yf.download(sorted(set(tickers) | {"SPY"}), period="2y", interval="1d", auto_adjust=True, progress=False, threads=False)["Close"]
    if px is None or px.empty:
        raise StopsError("Yahoo returned no prices (rate-limited?)")
    spy_weekly = px["SPY"].dropna().resample("W-FRI").last().dropna()
    spy = {d.date(): float(v) for d, v in spy_weekly.items()}
    last = {t: float(px[t].dropna().iloc[-1]) for t in tickers if t in px and px[t].notna().any()}
    return spy, last


# --------------------------------------------------------------------------- reporting

def _pct(x: Optional[float], plus: bool = False) -> str:
    return "  n/a" if x is None else f"{x * 100:+.1f}%" if plus else f"{x * 100:.1f}%"


def format_status(statuses: Sequence[Status]) -> str:
    lines = [f"{'ticker':6s} {'wt':>5s} {'vs cost':>8s} {'vs 52wk hi':>10s} {'RS 6m':>7s} {'40wk avg':>13s}  rung  status"]
    for s in statuses:
        rs = f"{s.rs_pts:+.0f}pts" if s.rs_pts is not None else (f"score {s.rs_score}" if s.rs_score else "n/a")
        ma_txt = "n/a" if s.under_ma is None else (f"under {s.weeks_under}w" if s.under_ma else "above")
        flag = "!" if s.needs_attention else " "
        lines.append(f"{s.ticker:6s} {s.weight * 100:4.1f}% {_pct(s.loss, True):>8s} {_pct(s.from_high, True):>10s} {rs:>7s} {ma_txt:>13s}  {s.rung}{flag}    {s.action}")
    on = [s for s in statuses if s.rung >= 1]
    att = [s for s in statuses if s.needs_attention]
    lines.append("")
    lines.append(f"{len(on)} of {len(statuses)} held scored stocks on a rung" + (f"; {len(att)} need attention" if att else "; none need attention"))
    notes = sorted({s.note for s in statuses if s.note})
    lines += [f"  note: {n}" for n in notes]
    return "\n".join(lines)


def summary(path: str, **kw) -> dict:
    """What the weekly report shows: who is on a rung, who is close, who needs attention."""
    st = ladder(path, **kw)
    return {"n": len(st), "on_rung": [(s.ticker, s.rung, s.loss, s.review_state) for s in st if s.rung >= 1],
            "approaching": [(s.ticker, s.loss) for s in st if s.approaching],
            "attention": [(s.ticker, s.rung, s.action) for s in st if s.needs_attention]}


def one_line(sm: dict) -> str:
    if not sm["on_rung"] and not sm["approaching"]:
        return f"stop-review ladder: none of {sm['n']} held scored stocks is on a rung"
    parts = [f"{t} rung {r} ({-l * 100:.1f}% below cost, review {state})" for t, r, l, state in sm["on_rung"]]
    parts += [f"{t} approaching ({-l * 100:.1f}% below cost)" for t, l in sm["approaching"]]
    return "stop-review ladder: " + "; ".join(parts) + (f" -- {len(sm['attention'])} need attention" if sm["attention"] else "")
