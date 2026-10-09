"""``landry rule12``: the Rule 12 pre-check -- can a name reach a 10% Base at all? (read-only)

Part 4 caps the terminal multiple at the lower of today's P/FCF and the normalized 5-year median, so with a flat
multiple the Base implied return is about per-share FCF growth plus the dividend yield WHATEVER THE PRICE. If
(1+g)^5 plus the cumulative dividend, over today's price, does not reach 10% a year at a flat multiple, the name
cannot enter and needs no further work. Bear = halved growth and a terminal multiple 0.81x the Base's. Growth is the
screening proxy (consensus EPS two-year CAGR, capped at 15%); confirm with per-share FCF before relying on a pass.
Built 2026-10-09 from the 10/8 scratch pre-check."""
import time
from typing import Optional

from landry.implied_return import BASE_MIN_RETURN, BEAR_MIN_RETURN, implied_return

GROWTH_CAP = 0.15
BEAR_MULT = 0.81


def _cum_div_factor(g: float) -> float:
    gd = min(max(g, 0.0), 0.06)
    return sum((1 + gd) ** k for k in range(5))


def screen(price: float, dividend: float, growth: float) -> dict:
    """Pure arithmetic: price and annual dividend per share, growth as a fraction. A flat multiple means year-5 value
    = price x (1+g)^5 (FCF per share scaled so P/FCF stays constant)."""
    g = min(growth, GROWTH_CAP)
    base = implied_return(price, price * (1 + g) ** 5, 1.0, dividend * _cum_div_factor(g))
    gb = g / 2
    bear = implied_return(price, price * (1 + gb) ** 5, BEAR_MULT, dividend * _cum_div_factor(gb))
    lo, hi = -0.10, 0.60
    for _ in range(100):
        mid = (lo + hi) / 2
        if implied_return(price, price * (1 + mid) ** 5, 1.0, dividend * _cum_div_factor(mid)) < BASE_MIN_RETURN:
            lo = mid
        else:
            hi = mid
    need = (lo + hi) / 2
    return dict(growth=g, base=base, bear=bear, need=need,
                passes=base > BASE_MIN_RETURN and bear >= BEAR_MIN_RETURN)


def fetch_inputs(ticker: str) -> Optional[dict]:
    import yfinance as yf
    tk = yf.Ticker(ticker)
    info = tk.info
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    ee = tk.earnings_estimate
    if not price or ee is None or not len(ee):
        return None
    e0, e2 = ee.loc["0y", "yearAgoEps"], ee.loc["+1y", "avg"]
    if not (e0 and e0 > 0 and e2 and e2 > 0):
        return None
    return dict(price=price, dividend=info.get("dividendRate") or 0.0, growth=(e2 / e0) ** 0.5 - 1,
                analysts=int(ee.loc["+1y", "numberOfAnalysts"]))


def run(tickers, pause: float = 1.2) -> str:
    out = [f"{'ticker':7s} {'price':>8s} {'yield':>6s} {'growth':>7s} {'need':>6s} {'Base':>7s} {'Bear':>7s}  verdict"]
    for t in tickers:
        try:
            x = fetch_inputs(t.upper())
        except Exception as e:
            out.append(f"{t.upper():7s} could not fetch ({type(e).__name__}: {str(e)[:50]})")
            continue
        if not x:
            out.append(f"{t.upper():7s} no usable price / EPS estimates (supply growth by hand)")
            continue
        s = screen(x["price"], x["dividend"], x["growth"])
        out.append(f"{t.upper():7s} {x['price']:8.2f} {x['dividend'] / x['price']:6.1%} {s['growth']:7.1%} {s['need']:6.1%} "
                   f"{s['base']:7.1%} {s['bear']:7.1%}  {'PASS (confirm with per-share FCF)' if s['passes'] else 'FAIL Rule 12'}")
        time.sleep(pause)
    out.append("growth = consensus EPS 2-year CAGR capped at 15%; need = growth required for a 10% Base at a flat multiple")
    return "\n".join(out)
