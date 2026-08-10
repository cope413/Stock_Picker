"""Landry System v1.0 — Part 9 performance benchmarking.

Track every initiated recommendation from its original decision date
(held AND exited — including exits avoids exit-selection bias). Strong
Buy entries carry a 12% annualized five-year objective, Buy entries 10%.
A cohort review triggers only after >= 10 matured five-year entries in a
band underperform BOTH the objective and the benchmark.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

OBJECTIVES = {"STRONG BUY": 0.12, "BUY": 0.10}   # annualized 5-yr planning objectives
MATURITY_YEARS = 5.0
COHORT_MIN_MATURED = 10


@dataclass
class EntryRecord:
    """One initiated recommendation (Performance Tracking tab row)."""
    ticker: str
    entry_date: _dt.date
    entry_price: float
    entry_score: float
    band: str                            # "STRONG BUY" | "BUY"
    confidence: str = "M"
    benchmark_price_at_entry: Optional[float] = None
    exit_date: Optional[_dt.date] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    current_price: Optional[float] = None
    benchmark_price_now: Optional[float] = None


@dataclass
class EntryPerformance:
    ticker: str
    band: str
    years_held: float
    matured: bool                        # >= 5 years since entry
    total_return: Optional[float]
    cagr: Optional[float]
    benchmark_cagr: Optional[float]
    excess_cagr: Optional[float]
    still_held: bool = True


def _cagr(start: float, end: float, years: float) -> Optional[float]:
    if start is None or end is None or start <= 0 or end <= 0 or years <= 0:
        return None
    return (end / start) ** (1.0 / years) - 1.0


def evaluate_entry(e: EntryRecord,
                   asof: Optional[_dt.date] = None) -> EntryPerformance:
    """Return metrics for one entry. Exited positions measure entry->exit;
    held positions measure entry->now. Benchmark measured over the same
    period (Part 9: same-period SPY or documented sector benchmark)."""
    asof = asof or _dt.date.today()
    end_date = e.exit_date or asof
    years = max((end_date - e.entry_date).days / 365.25, 0.0)
    end_price = e.exit_price if e.exit_date else e.current_price
    bench_end = e.benchmark_price_now
    tr = None if end_price is None or e.entry_price <= 0 \
        else end_price / e.entry_price - 1.0
    return EntryPerformance(
        ticker=e.ticker, band=e.band, years_held=round(years, 2),
        matured=years >= MATURITY_YEARS,
        total_return=tr,
        cagr=_cagr(e.entry_price, end_price, years),
        benchmark_cagr=_cagr(e.benchmark_price_at_entry, bench_end, years),
        excess_cagr=(None if (c := _cagr(e.entry_price, end_price, years)) is None
                     or (b := _cagr(e.benchmark_price_at_entry, bench_end,
                                    years)) is None else c - b),
        still_held=e.exit_date is None,
    )


@dataclass
class CohortReview:
    band: str
    objective: float
    matured_count: int
    avg_cagr: Optional[float]
    avg_excess: Optional[float]
    hit_rate: Optional[float]            # fraction beating the objective
    review_triggered: bool
    reason: str = ""


def cohort_review(entries: List[EntryRecord],
                  asof: Optional[_dt.date] = None) -> List[CohortReview]:
    """Part 9 cohort test per decision band. Includes matured entries
    whether still held or sold. Trigger requires: >= 10 matured entries
    AND the cohort average below BOTH its objective and the benchmark."""
    perfs = [evaluate_entry(e, asof) for e in entries]
    out: List[CohortReview] = []
    for band, objective in OBJECTIVES.items():
        matured = [p for p in perfs
                   if p.band == band and p.matured and p.cagr is not None]
        if not matured:
            out.append(CohortReview(band, objective, 0, None, None, None,
                                    False, "no matured entries"))
            continue
        avg = sum(p.cagr for p in matured) / len(matured)
        with_bench = [p for p in matured if p.excess_cagr is not None]
        avg_excess = (sum(p.excess_cagr for p in with_bench) / len(with_bench)
                      if with_bench else None)
        hits = sum(1 for p in matured if p.cagr >= objective) / len(matured)
        triggered = (len(matured) >= COHORT_MIN_MATURED
                     and avg < objective
                     and avg_excess is not None and avg_excess < 0)
        reason = ""
        if triggered:
            reason = (f"{band} cohort ({len(matured)} matured): avg CAGR "
                      f"{avg:.1%} < {objective:.0%} objective and below "
                      "benchmark — formal Tier 1 weight review required "
                      "(Rule 47 cadence)")
        elif len(matured) < COHORT_MIN_MATURED:
            reason = (f"only {len(matured)} matured entries "
                      f"(review needs {COHORT_MIN_MATURED})")
        out.append(CohortReview(band, objective, len(matured), avg,
                                avg_excess, hits, triggered, reason))
    return out


# --------------------------------------------------------------------------- #
# workbook reader (Performance Tracking tab)
# --------------------------------------------------------------------------- #

def read_performance_tab(path: str,
                         sheet: str = "Performance Tracking") -> List[EntryRecord]:
    """Columns (header row 2): A=Ticker, B=Company, C=EntryDate,
    D=EntryPrice, E=EntryScore, F=EntryConfidence, G=EntryDecisionBand,
    H=SPY@Entry, I=Status, J=ExitDate, K=ExitPrice, L=ExitReason,
    M=Current/ExitPrice, N=SPY(current/exit)."""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet]
    out: List[EntryRecord] = []
    for r in ws.iter_rows(min_row=3, values_only=True):
        if not r or not r[0] or r[2] is None or r[3] is None:
            continue
        def _d(v):
            if v is None:
                return None
            return v.date() if hasattr(v, "date") else v
        out.append(EntryRecord(
            ticker=str(r[0]).strip(),
            entry_date=_d(r[2]),
            entry_price=float(r[3]),
            entry_score=float(r[4] or 0),
            band=str(r[6] or ("STRONG BUY" if float(r[4] or 0) >= 80 else "BUY")).strip().upper(),
            confidence=str(r[5] or "M").strip(),
            benchmark_price_at_entry=float(r[7]) if r[7] is not None else None,
            exit_date=_d(r[9]),
            exit_price=float(r[10]) if r[10] is not None else None,
            exit_reason=str(r[11] or "").strip(),
            current_price=float(r[12]) if r[12] is not None else None,
            benchmark_price_now=float(r[13]) if r[13] is not None else None,
        ))
    wb.close()
    return out
