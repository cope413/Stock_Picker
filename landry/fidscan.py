"""``landry fidscan``: keep each Fidelity info-tab scan and compare Fidelity's Equity Summary Score with the Landry score.

The scan itself is read in Alan's Chrome with ``docs/ops/fidelity_scan.js`` (one "View All" page per ticker, see
``docs/ops/process-and-dca.md``). ``add`` parses that script's text output and stores it in ``landry_fidelity_scans.json``
together with what the Scoring tab and Market Data said on that day, so a later ``report`` can ask the one question this
exists for: when the two scores disagreed, which was right over the following months?

The Equity Summary Score (LSEG StarMine) is an accuracy-weighted blend of independent firms, several of them technical; it
tracks recent price far more than fundamentals. It is CONTEXT and a disagreement flag, never a Scoring input (Journal
2026-10-10). ``report`` returns are plain price changes from Market Data, not total returns."""
import datetime as dt
import json
import math
import os
import re
from typing import Dict, List, Optional

from landry import xlsx_io

STORE = "landry_fidelity_scans.json"
PERIODS = ("1m", "3m", "6m", "YTD", "1y", "2y", "5y")
_POS = ("buy", "outperform")
_NEG = ("sell", "underperform")


class FidScanError(Exception):
    pass


def _num(s: str) -> Optional[float]:
    s = s.strip().replace(",", "").replace("%", "").replace("$", "").replace("+", "")
    try:
        return float(s)
    except ValueError:
        return None


def _pair(s: str) -> List[Optional[float]]:
    """'54.93 ~ 49.03' -> [54.93, 49.03]; a missing side is None."""
    parts = [_num(x) for x in s.split("~")]
    return (parts + [None, None])[:2]


def parse_raw(text: str) -> Dict[str, dict]:
    """The extractor prints, per ticker, a ticker line then PERF / EVT / EPS / PE / EPSg / FA / ESS / OPS / KS lines."""
    out: Dict[str, dict] = {}
    cur = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", line):
            cur = out.setdefault(line, {})
            continue
        if cur is None or " " not in line:
            continue
        tag, rest = line.split(" ", 1)
        if tag == "PERF":
            perf = {}
            for part in rest.split(","):
                bits = part.split()
                if len(bits) == 2 and bits[0] in PERIODS:
                    perf[bits[0]] = _num(bits[1])
            cur["perf"] = perf
        elif tag == "EVT":
            cur["earnings"] = None if rest == "NA" else rest
        elif tag == "EPS":
            cur["surprises"] = [x.strip() for x in rest.split(";") if x.strip()]
        elif tag == "PE":
            m = re.match(r"ttm (.*?); 5y (.*?); fwd", rest)
            if m:
                cur["pe_ttm"], cur["pe_ttm_industry"] = _pair(m.group(1))
                cur["pe_5y"] = _pair(m.group(2))[0]
        elif tag == "ESS":
            m = re.match(r"(.*?)\s*(-?\d+(?:\.\d+)?)$", rest)
            if m:
                cur["ess_label"], cur["ess"] = m.group(1).strip(), float(m.group(2))
        elif tag == "OPS":
            ops = []
            for part in rest.split(", "):
                m = re.match(r"(.*):(.*)\((.*)\)$", part.strip())
                if m and m.group(2) != "--":
                    ops.append({"firm": m.group(1).strip(), "opinion": m.group(2).strip(), "accuracy": _num(m.group(3))})
            cur["opinions"] = ops
    for t, d in out.items():
        if "ess" not in d:
            raise FidScanError(f"{t}: no Equity Summary Score line in the scan text")
    if not out:
        raise FidScanError("no ticker blocks found in the scan text")
    return out


def split(opinions: List[dict]) -> Dict[str, int]:
    """Counts of positive / neutral / negative firm opinions."""
    c = {"positive": 0, "neutral": 0, "negative": 0}
    for o in opinions or []:
        k = o["opinion"].lower()
        c["positive" if k in _POS else "negative" if k in _NEG else "neutral"] += 1
    return c


def ranks(values: List[float], high_is_first: bool = True) -> List[float]:
    """Average ranks, 1 = best (highest by default); ties share the mean of their places."""
    order = sorted(range(len(values)), key=lambda i: values[i], reverse=high_is_first)
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def rank_correlation(a: List[float], b: List[float]) -> Optional[float]:
    """Spearman: Pearson correlation of the average ranks."""
    n = len(a)
    if n < 3 or n != len(b):
        return None
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    return None if va == 0 or vb == 0 else cov / math.sqrt(va * vb)


def load(path: str = STORE) -> dict:
    if not os.path.exists(path):
        return {"scans": []}
    with open(path) as f:
        return json.load(f)


def save(store: dict, path: str = STORE) -> None:
    with open(path, "w") as f:
        json.dump(store, f, indent=1, sort_keys=True)
        f.write("\n")


def landry_side(workbook: str) -> Dict[str, dict]:
    """What the Scoring tab and Market Data say now, per ticker."""
    prices = {r["ticker"]: r["price"] for r in xlsx_io.read_market_data(workbook)}
    out = {}
    for r in xlsx_io.read_scoring_tab(workbook):
        ds = r.date_scored
        rs = r.scores.get("relative_strength")
        out[r.ticker] = {
            "composite": r.composite, "decision": r.decision,
            "date_scored": ds.strftime("%Y-%m-%d") if hasattr(ds, "strftime") else (str(ds) if ds else None),
            "relative_strength": rs.score if rs else None,
            "price": float(prices[r.ticker]) if isinstance(prices.get(r.ticker), (int, float)) else None,
        }
    return out


def add(store: dict, text: str, as_of: dt.date, landry: Dict[str, dict], source: str = "") -> dict:
    """Parse a scan, attach the Landry side, and store it (a scan with the same date is replaced)."""
    rows = parse_raw(text)
    for t, d in rows.items():
        d["landry"] = landry.get(t) or {}
    scan = {"as_of": as_of.isoformat(), "source": source, "rows": rows}
    store["scans"] = sorted([s for s in store.get("scans", []) if s["as_of"] != scan["as_of"]] + [scan],
                            key=lambda s: s["as_of"])
    return scan


def compare(scan: dict) -> List[dict]:
    """One row per ticker scored on both sides. gap = Landry rank minus Fidelity rank (1 = best), so a NEGATIVE gap
    means Landry rates the name higher than Fidelity does. Flagged when the gap is a third of the list or more."""
    ts = [t for t, d in scan["rows"].items() if d.get("ess") is not None and (d.get("landry") or {}).get("composite") is not None]
    ts.sort()
    if not ts:
        return []
    rf = ranks([scan["rows"][t]["ess"] for t in ts])
    rl = ranks([scan["rows"][t]["landry"]["composite"] for t in ts])
    limit = math.ceil(len(ts) / 3)
    out = []
    for t, f, l in zip(ts, rf, rl):
        d = scan["rows"][t]
        gap = l - f
        out.append({"ticker": t, "ess": d["ess"], "ess_label": d.get("ess_label", ""), "composite": d["landry"]["composite"],
                    "decision": d["landry"].get("decision"), "date_scored": d["landry"].get("date_scored"),
                    "rank_fidelity": f, "rank_landry": l, "gap": gap, "split": split(d.get("opinions")),
                    "perf_3m": (d.get("perf") or {}).get("3m"), "price": d["landry"].get("price"),
                    "flag": "LANDRY HIGHER" if gap <= -limit else "FIDELITY HIGHER" if gap >= limit else ""})
    return sorted(out, key=lambda r: r["gap"])


def forward(scan: dict, prices_now: Dict[str, float]) -> Optional[dict]:
    """Price change since the scan, per ticker and averaged by flag group. None if no later prices differ."""
    rows = []
    for r in compare(scan):
        now = prices_now.get(r["ticker"])
        if r["price"] and isinstance(now, (int, float)):
            rows.append(dict(r, price_now=float(now), change=float(now) / r["price"] - 1))
    if not rows or all(abs(r["change"]) < 1e-12 for r in rows):
        return None
    groups = {}
    for name, sel in (("LANDRY HIGHER", lambda r: r["flag"] == "LANDRY HIGHER"),
                      ("FIDELITY HIGHER", lambda r: r["flag"] == "FIDELITY HIGHER"),
                      ("all", lambda r: True)):
        g = [r["change"] for r in rows if sel(r)]
        groups[name] = (sum(g) / len(g), len(g)) if g else (None, 0)
    return {"rows": rows, "groups": groups}


def format_report(store: dict, prices_now: Optional[Dict[str, float]] = None, as_of: Optional[str] = None) -> str:
    scans = store.get("scans", [])
    if not scans:
        return "no Fidelity scans stored yet (`landry fidscan add --file ... --as-of ...`)"
    scan = next((s for s in scans if s["as_of"] == as_of), None) if as_of else scans[-1]
    if scan is None:
        return f"no scan dated {as_of}; stored: {', '.join(s['as_of'] for s in scans)}"
    rows = compare(scan)
    L = [f"Fidelity scan {scan['as_of']}: {len(scan['rows'])} tickers, {len(rows)} with a Landry composite "
         f"({len(scans)} scan(s) stored)"]
    rc = rank_correlation([r["ess"] for r in rows], [r["composite"] for r in rows])
    with3 = [r for r in rows if r["perf_3m"] is not None]
    r3 = rank_correlation([r["ess"] for r in with3], [r["perf_3m"] for r in with3])
    l3 = rank_correlation([r["composite"] for r in with3], [r["perf_3m"] for r in with3])
    f = lambda x: "n/a" if x is None else f"{x:+.2f}"
    L.append(f"rank correlation, Fidelity score vs Landry composite: {f(rc)} (n={len(rows)}); "
             f"vs 3-month price: Fidelity {f(r3)}, Landry {f(l3)} (n={len(with3)})")
    L.append("gap = Landry rank minus Fidelity rank (1 = best); negative = Landry rates it higher")
    L.append(f"{'ticker':6} {'Fid':>4} {'label':12} {'+/0/-':7} {'Landry':>6} {'decision':10} {'scored':10} {'3mo%':>6} {'gap':>5}  flag")
    for r in rows:
        s = r["split"]
        p = "" if r["perf_3m"] is None else f"{r['perf_3m']:+.1f}"
        L.append(f"{r['ticker']:6} {r['ess']:>4} {r['ess_label'][:12]:12} {s['positive']}/{s['neutral']}/{s['negative']:<3} "
                 f"{r['composite']:>6} {str(r['decision'] or ''):10} {str(r['date_scored'] or ''):10} {p:>6} {r['gap']:>+5.1f}  {r['flag']}")
    if prices_now:
        for old in scans:
            fw = forward(old, prices_now)
            if not fw:
                continue
            g = fw["groups"]
            pct = lambda v: "n/a" if v[0] is None else f"{v[0] * 100:+.1f}% (n={v[1]})"
            L.append(f"since {old['as_of']} (price change to Market Data now): Landry-higher names {pct(g['LANDRY HIGHER'])}, "
                     f"Fidelity-higher names {pct(g['FIDELITY HIGHER'])}, all {pct(g['all'])}")
            flagged = [r for r in fw["rows"] if r["flag"]]
            if flagged:
                L.append("  " + "; ".join(f"{r['ticker']} {r['change'] * 100:+.1f}% [{r['flag'].split()[0].lower()}]" for r in flagged))
    return "\n".join(L)
