"""`landry refresh` — recompute every automatable input (Part 12 boundary)
and write ``landry_snapshot.json``.

Per ticker: market snapshot, technical state (Rules 8-9 + Tier 3),
relative strength vs SPY, Rule 20 beta, fundamentals + quantitative
rubric drafts. Portfolio-wide: Rule 36 correlation report and cluster
exposure, macro-overlay conditions.

Drafted scores are *drafts* — they carry confidence ceilings and enter
nothing until approved (Phase 3+ workflow).
"""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from landry.data_auto import (
    cluster_exposure,
    correlation_report,
    fetch_daily,
    market_snapshot,
    relative_strength,
    technical_state,
    weekly_beta,
    weekly_closes,
    weekly_returns,
)
from landry.fundamentals import (
    YFinanceFundamentals,
    compute_metrics,
    draft_quant_scores,
)
from landry.macro import active_effects, fetch_conditions, unknown_conditions

SNAPSHOT_FILE = "landry_snapshot.json"


def _asdict(obj):
    if dataclasses.is_dataclass(obj):
        return {k: _asdict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _asdict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_asdict(v) for v in obj]
    if hasattr(obj, "item"):        # numpy scalar
        return obj.item()
    return obj


def build_snapshot(tickers: Sequence[str],
                   weights: Optional[Dict[str, float]] = None,
                   refresh_data: bool = False,
                   with_fundamentals: bool = True,
                   with_market: bool = True) -> Dict:
    """Compute the full snapshot. Network failures degrade per-section
    (recorded under ``errors``) rather than failing the run."""
    tickers = list(dict.fromkeys(t.upper() for t in tickers))
    errors: Dict[str, str] = {}
    snap: Dict = {
        "asof": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tickers": {}, "errors": errors,
    }

    try:
        daily = fetch_daily(tickers + ["SPY"], refresh=refresh_data)
    except Exception as e:
        errors["prices"] = f"price download failed: {e}"
        daily = {}

    spy = daily.get("SPY")
    closes = weekly_closes(daily) if daily else None
    provider = YFinanceFundamentals() if with_fundamentals else None

    for t in tickers:
        entry: Dict = {}
        df = daily.get(t)
        if df is None:
            entry["error"] = "no price data"
            snap["tickers"][t] = entry
            continue
        entry["technicals"] = _asdict(technical_state(df))
        if spy is not None:
            wt, ws = closes[t], closes["SPY"]
            entry["relative_strength"] = _asdict(relative_strength(wt, ws))
            entry["beta"] = _asdict(weekly_beta(wt, ws))
        if with_market:
            try:
                entry["market"] = market_snapshot(t)
            except Exception as e:
                entry["market_error"] = str(e)
        if provider is not None:
            try:
                metrics = compute_metrics(provider.get(t))
                entry["fundamentals"] = _asdict(metrics)
                entry["draft_scores"] = _asdict(draft_quant_scores(metrics))
            except Exception as e:
                entry["fundamentals_error"] = str(e)
        snap["tickers"][t] = entry

    # Rule 36 — holdings only (weights known), else all requested tickers
    corr_universe = [t for t in (weights or {}) if t in (daily or {})] \
        or [t for t in tickers if t in (daily or {})]
    if closes is not None and len(corr_universe) >= 2:
        rep = correlation_report(weekly_returns(closes[corr_universe]))
        snap["correlation"] = {
            "window_weeks": rep.window_weeks,
            "sufficient": rep.sufficient,
            "flagged_pairs": [[a, b, c] for a, b, c in rep.flagged_pairs],
            "clusters": rep.clusters,
            "matrix": {a: {b: (None if json is None else v)
                           for b, v in row.items()}
                       for a, row in rep.matrix.round(2).to_dict().items()},
        }
        if weights:
            snap["correlation"]["cluster_exposure"] = [
                _asdict(c) for c in cluster_exposure(rep, weights)]

    try:
        cond = fetch_conditions(daily_spy=spy)
        snap["macro"] = {
            "conditions": _asdict(cond),
            "active_effects": [_asdict(e) for e in active_effects(cond)],
            "unknown": unknown_conditions(cond),
        }
    except Exception as e:
        errors["macro"] = str(e)

    return snap


def write_snapshot(snap: Dict, directory: str) -> str:
    path = os.path.join(directory, SNAPSHOT_FILE)
    with open(path, "w") as f:
        json.dump(snap, f, indent=1, default=str)
    return path


def print_report(snap: Dict) -> None:
    print(f"snapshot as of {snap['asof']}")
    for t, e in snap["tickers"].items():
        if "error" in e:
            print(f"\n{t}: ! {e['error']}")
            continue
        tech = e.get("technicals", {})
        rs = e.get("relative_strength", {})
        beta = e.get("beta", {})
        print(f"\n{t}:")
        print(f"  200w MA: {'above' if tech.get('above_200w_ma') else 'BELOW'}"
              f" | monthly MACD ok: {tech.get('macd_positive_or_turning')}"
              f" | supertrend bull: {tech.get('supertrend_bullish')}"
              f" | staging (Rule 8): "
              f"{'full size' if tech.get('staging_ok') else '50% + 90d review'}")
        if rs.get("diff_blended") is not None:
            print(f"  RS vs SPY: {rs['diff_blended']:+.1%} -> draft {rs['score']}"
                  f" | beta {beta.get('beta')} ({beta.get('source')})"
                  f" -> Rule 20 reduction {beta.get('size_reduction_pct')}%")
        for name, d in (e.get("draft_scores") or {}).items():
            print(f"  draft {name}: {d['score']} ({d['confidence']}) — "
                  f"{d['rationale']}")
    corr = snap.get("correlation")
    if corr:
        print(f"\nRule 36 correlation: {corr['window_weeks']} common weeks"
              f" ({'ok' if corr['sufficient'] else 'INSUFFICIENT (<52w)'})")
        for a, b, c in corr["flagged_pairs"]:
            print(f"  {a} — {b}: {c:.2f} > 0.70")
        for cl in corr["clusters"]:
            print(f"  CLUSTER (3+ pairwise > 0.70): {', '.join(cl)}")
        for ce in corr.get("cluster_exposure", []):
            print(f"  {ce['note']}")
        if not corr["flagged_pairs"]:
            print("  no pair above 0.70")
    macro = snap.get("macro")
    if macro:
        print("\nMacro overlay:")
        for eff in macro["active_effects"]:
            print(f"  ACTIVE: {eff['condition']}")
            for a in eff["effects"]:
                print(f"    - {a}")
        if not macro["active_effects"]:
            print("  no active conditions")
        if macro["unknown"]:
            print(f"  unknown (no data source): {', '.join(macro['unknown'])}")
