"""Phase 5 tests: Excel round-trip (export/import), Part 9 performance
cohorts, and the daily action-items assembly."""

import datetime as dt
import glob
import os
from dataclasses import dataclass, field
from typing import Optional

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WBS = sorted(glob.glob(os.path.join(_REPO, "LANDRY_SYSTEM_WORKBOOK_*.xlsx")))

needs_workbook = pytest.mark.skipif(not _WBS, reason="no workbook file")


# --------------------------------------------------------------------------- #
# export / import
# --------------------------------------------------------------------------- #

@needs_workbook
def test_export_fills_values_and_preserves_formulas(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    pd = pytest.importorskip("pandas")
    from landry.drawdown import regime_frame
    from landry.export import export_workbook
    from landry.scoring import IndicatorScore

    idx = pd.date_range("2026-01-09", periods=20, freq="W-FRI")
    closes = pd.DataFrame({"NVDA": range(100, 120),
                           "TSM": range(50, 70)}, index=idx, dtype=float)
    values = pd.Series([100.0] * 5 + [88.0] * 6,
                       index=pd.bdate_range("2026-07-01", periods=11))
    scores = {"NVDA": {"competitive_moat": IndicatorScore(5, "H")}}
    market = {"NVDA": {"price": 219.22, "market_cap": 5.4e12, "pe": 55.0}}

    out = export_workbook(_WBS[-1], out_path=str(tmp_path / "filled.xlsx"),
                          weekly_closes=closes, market=market,
                          drawdown=regime_frame(values),
                          approved_scores=scores,
                          scored_date=dt.date(2026, 8, 10))

    wb = openpyxl.load_workbook(out)          # formulas view
    ph = wb["Price History"]
    assert ph.cell(row=2, column=2).value == "NVDA"
    assert ph.cell(row=3, column=2).value == 100.0
    assert ph.cell(row=22, column=3).value == 69.0
    assert ph.cell(row=23, column=2).value is None      # cleared beyond data

    md = wb["Market Data"]
    nvda_row = next(r for r in range(3, 28)
                    if md.cell(row=r, column=1).value == "NVDA")
    assert md.cell(row=nvda_row, column=3).value == 219.22
    assert md.cell(row=nvda_row, column=5).value == pytest.approx(5.4e6)  # $M

    dd = wb["Portfolio Drawdown Log"]
    assert dd.cell(row=3, column=2).value == 100.0
    statuses = [dd.cell(row=r, column=5).value for r in range(3, 14)]
    assert "Elevated" in statuses             # 6th day at -12% escalates

    sc = wb["Scoring"]
    nvda_row = next(r for r in range(3, 28)
                    if sc.cell(row=r, column=1).value == "NVDA")
    assert sc.cell(row=nvda_row, column=8).value == 5      # moat score col H
    assert sc.cell(row=nvda_row, column=9).value == "H"
    # computed columns must remain formulas (Excel recalculates)
    for col in (14, 32, 33):                  # T1 avg, composite, decision
        v = sc.cell(row=nvda_row, column=col).value
        assert isinstance(v, str) and v.startswith("=")


@needs_workbook
def test_import_seeds_store_and_reproduces_composite(tmp_path):
    pytest.importorskip("openpyxl")
    from landry.approvals import ScoreStore
    from landry.export import import_scores
    from landry.scoring import score_stock

    store = ScoreStore(str(tmp_path / "scores.json"))
    counts = import_scores(_WBS[-1], store, approved_by="Taylor",
                           tickers=["NVDA", "TSLA"])
    assert counts["NVDA"] == 12
    assert counts["TSLA"] == 5                # Tier 1 only (gate failed)
    card = score_stock("NVDA", store.approved_scores("NVDA"))
    assert card.composite == pytest.approx(93.2)
    assert card.decision == "STRONG BUY"
    assert store.pending() == {}              # everything auto-approved
    assert any(a["action"] == "approve" for a in store.audit)


# --------------------------------------------------------------------------- #
# Part 9 — performance cohorts
# --------------------------------------------------------------------------- #

def _entry(ticker="X", years_ago=6.0, entry=100.0, now=None, band="STRONG BUY",
           bench_entry=100.0, bench_now=150.0, exited=False, exit_years=5.5):
    from landry.performance import EntryRecord
    today = dt.date(2026, 8, 10)
    ed = today - dt.timedelta(days=int(years_ago * 365.25))
    kw = dict(ticker=ticker, entry_date=ed, entry_price=entry,
              entry_score=85.0 if band == "STRONG BUY" else 70.0, band=band,
              benchmark_price_at_entry=bench_entry,
              benchmark_price_now=bench_now)
    if exited:
        kw["exit_date"] = ed + dt.timedelta(days=int(exit_years * 365.25))
        kw["exit_price"] = now
    else:
        kw["current_price"] = now
    return EntryRecord(**kw)


def test_evaluate_entry_math():
    from landry.performance import evaluate_entry
    today = dt.date(2026, 8, 10)
    # 100 -> 200 over ~6 years held
    p = evaluate_entry(_entry(now=200.0), asof=today)
    assert p.matured and p.still_held
    assert p.cagr == pytest.approx(2.0 ** (1 / p.years_held) - 1, rel=1e-3)
    assert p.excess_cagr is not None
    # exited position measures entry -> exit
    p2 = evaluate_entry(_entry(now=150.0, exited=True), asof=today)
    assert not p2.still_held
    assert p2.years_held == pytest.approx(5.5, abs=0.05)


def test_cohort_trigger_requires_10_matured_and_double_underperformance():
    from landry.performance import cohort_review
    today = dt.date(2026, 8, 10)
    # 10 matured Strong Buys at ~5% CAGR, benchmark ~7% -> triggered
    bad = [_entry(ticker=f"B{i}", now=100 * 1.05 ** 6, bench_now=100 * 1.07 ** 6)
           for i in range(10)]
    rev = {r.band: r for r in cohort_review(bad, asof=today)}
    sb = rev["STRONG BUY"]
    assert sb.matured_count == 10 and sb.review_triggered
    assert "Tier 1 weight review" in sb.reason
    # only 9 matured -> no trigger
    rev9 = {r.band: r for r in cohort_review(bad[:9], asof=today)}
    assert not rev9["STRONG BUY"].review_triggered
    # beats its benchmark -> no trigger even below objective
    good_bench = [_entry(ticker=f"G{i}", now=100 * 1.05 ** 6,
                         bench_now=100 * 1.03 ** 6) for i in range(10)]
    revg = {r.band: r for r in cohort_review(good_bench, asof=today)}
    assert not revg["STRONG BUY"].review_triggered


@needs_workbook
def test_read_performance_tab_empty_is_ok():
    pytest.importorskip("openpyxl")
    from landry.performance import read_performance_tab
    assert read_performance_tab(_WBS[-1]) == []   # tab exists, no data yet


# --------------------------------------------------------------------------- #
# daily action items
# --------------------------------------------------------------------------- #

@dataclass
class _Row:
    ticker: str
    composite: Optional[float]
    date_scored: Optional[dt.date]
    rule_flags: tuple = ("OK", "OK", "OK", "OK")


@dataclass
class _Pos:
    ticker: str
    asset_class: str = "Equity"


TODAY = dt.date(2026, 8, 10)


def _actions(rows, positions, snapshot=None, pending=None):
    from landry.daily import build_action_items
    return build_action_items(rows, positions, snapshot, pending or {}, TODAY)


def test_band_actions_for_held_names():
    rows = [_Row("PROB", 60.0, TODAY), _Row("EXIT", 40.0, TODAY),
            _Row("SELL", 30.0, TODAY), _Row("OK", 85.0, TODAY),
            _Row("GATE", None, TODAY, ("FAIL", "OK", "AVOID", "OK"))]
    pos = [_Pos(t) for t in ("PROB", "EXIT", "SELL", "OK", "GATE")]
    acts = _actions(rows, pos)
    by = {a.ticker: a for a in acts}
    assert "Probationary Hold" in by["PROB"].action and by["PROB"].rule == "Rule 32"
    assert "Exit Review" in by["EXIT"].action and by["EXIT"].deadline
    assert "Mandatory Sell" in by["SELL"].action
    assert by["GATE"].rule.startswith("Rules 1/3")
    assert "OK" not in by                     # healthy holding: no action


def test_review_clocks():
    old = TODAY - dt.timedelta(days=400)
    mid = TODAY - dt.timedelta(days=120)
    rows = [_Row("ANNUAL", 85.0, old), _Row("QTR", 70.0, mid),
            _Row("WATCH", 55.0, mid)]
    pos = [_Pos("ANNUAL"), _Pos("QTR")]      # WATCH is not held
    acts = _actions(rows, pos)
    texts = {a.ticker: a for a in acts}
    assert texts["ANNUAL"].rule == "Rule 44"
    assert texts["QTR"].rule == "Rule 43"
    assert texts["WATCH"].rule == "Rule 43"   # quarterly watch-list review


def test_snapshot_driven_items():
    snap = {
        "asof": "2026-07-01T00:00:00+00:00",   # stale
        "correlation": {
            "clusters": [["A", "B", "C"]],
            "cluster_exposure": [{"cluster": ["A", "B", "C"],
                                  "over_cap": True,
                                  "note": "cluster A/B/C: 23.0% aggregate"}],
        },
        "macro": {"active_effects": [{"condition": "Broad market downtrend",
                                      "effects": ["Reduce sizes 50%"]}]},
    }
    acts = _actions([], [], snapshot=snap)
    rules = [a.rule for a in acts]
    assert "Rule 36" in rules and "Part 7" in rules and "Part 12" in rules
    over = next(a for a in acts if "23.0%" in a.action)
    assert over.priority == 1


def test_pending_drafts_surface():
    acts = _actions([], [], pending={"NVDA": {"competitive_moat": {}}})
    assert any("awaiting approval" in a.action and a.ticker == "NVDA"
               for a in acts)


def test_priority_ordering():
    rows = [_Row("SELL", 30.0, TODAY)]
    acts = _actions(rows, [_Pos("SELL")],
                    pending={"ZZZ": {"x": {}}})
    assert [a.priority for a in acts] == sorted(a.priority for a in acts)
    assert acts[0].ticker == "SELL"
