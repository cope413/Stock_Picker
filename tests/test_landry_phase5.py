"""Phase 5 tests: Excel round-trip (export/import), Part 9 performance
cohorts, and the daily action-items assembly."""

import datetime as dt
import os
from dataclasses import dataclass, field
from typing import Optional

import pytest

from landry.xlsx_io import latest_workbook

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WB = latest_workbook(_REPO)

needs_workbook = pytest.mark.skipif(not _WB, reason="no workbook file")


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

    values = pd.Series([100.0] * 5 + [88.0] * 6,
                       index=pd.bdate_range("2026-07-01", periods=11))
    scores = {"NVDA": {"competitive_moat": IndicatorScore(5, "H")}}
    market = {"NVDA": {"price": 219.22, "market_cap": 5.4e12, "pe": 55.0}}

    out = export_workbook(_WB, out_path=str(tmp_path / "filled.xlsx"),
                          market=market,
                          drawdown=regime_frame(values),
                          approved_scores=scores,
                          scored_date=dt.date(2026, 8, 10))

    wb = openpyxl.load_workbook(out)          # formulas view
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
    counts = import_scores(_WB, store, approved_by="Taylor",
                           tickers=["NVDA", "TSLA"])
    assert counts["NVDA"] == 12
    assert counts["TSLA"] == 5                # Tier 1 only (gate failed)
    from landry.xlsx_io import read_scoring_tab
    row = next(r for r in read_scoring_tab(_WB) if r.ticker == "NVDA")
    card = score_stock("NVDA", store.approved_scores("NVDA"))
    # the engine, fed the imported scores, must reproduce the workbook's OWN composite and decision -- not a number
    # pinned in August (93.2; NVDA's FCF yield score has moved since, and the composite with it)
    assert row.composite is not None
    assert card.composite == pytest.approx(row.composite)
    assert card.decision == row.decision
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


def _performance_workbook(tmp_path, rows=()):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Performance Tracking"
    ws["A1"] = "Performance tracking"
    for c, h in enumerate(["Ticker", "Company", "Entry Date", "Entry Price", "Entry Score", "Confidence", "Band",
                           "SPY @ Entry", "Status", "Exit Date", "Exit Price", "Exit Reason", "Current/Exit Price",
                           "SPY now"], 1):
        ws.cell(row=2, column=c, value=h)
    for i, row in enumerate(rows):
        for c, v in enumerate(row, 1):
            ws.cell(row=3 + i, column=c, value=v)
    path = str(tmp_path / "perf.xlsx")
    wb.save(path)
    return path


def test_read_performance_tab_empty_is_ok(tmp_path):
    from landry.performance import read_performance_tab
    # a synthetic tab: the live one was empty when this was written (August) and has entries now
    assert read_performance_tab(_performance_workbook(tmp_path)) == []


def test_read_performance_tab_reads_a_populated_row(tmp_path):
    from landry.performance import read_performance_tab
    path = _performance_workbook(tmp_path, [
        ("NVDA", "NVIDIA", dt.datetime(2026, 8, 7), 180.0, 89.2, "M", "STRONG BUY", 640.0, "Held", None, None, None,
         227.0, 660.0),
        ("SFM", "Sprouts", dt.datetime(2026, 8, 7), 100.0, 70.0, "H", "BUY", 640.0, "Sold", dt.datetime(2026, 9, 1),
         104.0, "Rule 3", 104.0, 655.0)])
    a, b = read_performance_tab(path)
    assert (a.ticker, a.entry_date, a.entry_price, a.band, a.exit_date, a.current_price) == (
        "NVDA", dt.date(2026, 8, 7), 180.0, "STRONG BUY", None, 227.0)
    assert (b.ticker, b.exit_date, b.exit_price, b.exit_reason) == ("SFM", dt.date(2026, 9, 1), 104.0, "Rule 3")


def test_read_performance_tab_skips_rows_that_are_not_system_entries(tmp_path):
    """Since 2026-10-05 the tab lists every holding. Legacy, ETF and cash rows carry an average-cost Entry Price and no
    Entry Date, Score or Band, so none of them may reach the Rule-46 cohort -- a dated row without a score or a band
    included (the tab's A1 note: a blank Decision Band excludes it)."""
    from landry.performance import read_performance_tab
    path = _performance_workbook(tmp_path, [
        ("ANET", "Arista", None, 146.47, None, None, None, None, "Held", None, None, None, 207.35, None),   # legacy, no date
        ("FZDXX", "Fidelity MM", None, 1.0, None, None, None, None, "Held", None, None, None, 1.0, None),   # cash fund
        ("OLD", "Dated legacy", dt.datetime(2020, 1, 2), 10.0, None, None, None, None, "Held", None, None, None,
         12.0, None),                                                                                       # date, no score/band
        ("NVDA", "NVIDIA", dt.datetime(2026, 8, 7), 180.0, 89.2, "M", "STRONG BUY", 640.0, "Held", None, None, None,
         227.0, 660.0),
        ("NOBAND", "Score only", dt.datetime(2026, 8, 7), 50.0, 71.0, "M", None, 640.0, "Held", None, None, None,
         52.0, 660.0),
        ("Total Return Since Entry is a simple price return (a footnote, not a position)",),
    ])
    got = read_performance_tab(path)
    assert [e.ticker for e in got] == ["NVDA", "NOBAND"]
    assert got[1].band == "BUY"                       # a missing band is still derived when there is a score


def test_read_performance_tracking_lists_every_ticker_row_and_never_the_footnote(tmp_path):
    """No row bound: the table was extended past the old fixed bound (33) and a footnote sits right below it."""
    from landry.xlsx_io import read_performance_tracking
    path = _performance_workbook(tmp_path, [
        ("NVDA", "NVIDIA", dt.datetime(2026, 8, 7), 180.0, 89.2, "M", "STRONG BUY", 640.0, "Held"),
        ("ANET", "Arista", None, 146.47, None, None, None, None, "Held"),
        *[(None,)] * 40,                                                       # pre-built blank rows
        ("LATE", "Row past the old bound", None, 10.0, None, None, None, None, "Held"),
        ("Total Return Since Entry is a simple price return (a footnote, not a position)",),
    ])
    got = read_performance_tracking(path)
    assert [g["ticker"] for g in got] == ["NVDA", "ANET", "LATE"]
    assert got[1]["entry_date"] is None and got[1]["entry_price"] == 146.47 and got[1]["status"] == "Held"
    assert got[2]["entry_band"] is None


@needs_workbook
def test_live_performance_tracking_lists_every_holding_with_cash_equivalents_in_green():
    """Alan, 2026-10-05: include all positions; cash / cash-equivalent Ticker cells light green with dark-green text.
    "Cash / Cash Equivalents" is Current Positions' own aggregate (money-market and sweep funds plus the dry-powder
    ETFs), so the set is read from that row's formula rather than typed in a second place."""
    openpyxl = pytest.importorskip("openpyxl")
    import re
    from landry.xlsx_io import read_positions
    wb = openpyxl.load_workbook(_WB)
    cp, pt = wb["Current Positions"], wb["Performance Tracking"]
    agg = next(r for r in range(3, cp.max_row + 1) if cp.cell(r, 3).value == "Cash / Cash Equivalents")
    formula = cp.cell(agg, 7).value
    rows = {int(m.group(1)) for m in re.finditer(r"(?<![:\d])G(\d+)(?![\d:])", formula)}   # single cells, not range ends
    for m in re.finditer(r"G(\d+):G(\d+)", formula):
        rows |= set(range(int(m.group(1)), int(m.group(2)) + 1))
    cash_like = {str(cp.cell(r, 2).value).strip() for r in rows}
    assert {"FZDXX", "VMFXX", "QACDS"} <= cash_like                       # the formula parsed to something sensible

    # one entry per LOT row: a ticker can have several (a held baseline lot and the exited lot of a partial sale, as SPMO does
    # since 10/6/26), and only a lot that is still held keeps the green
    rows = [(str(pt.cell(r, 1).value).strip(), pt.cell(r, 1), pt.cell(r, 9).value) for r in range(3, pt.max_row + 1)
            if isinstance(pt.cell(r, 1).value, str) and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", pt.cell(r, 1).value.strip())]
    held = {p.ticker for p in read_positions(_WB)}
    assert held <= {t for t, _, _ in rows}, sorted(held - {t for t, _, _ in rows})        # every current holding has a row
    for t, cell, status in rows:
        green = cell.fill.fill_type == "solid" and cell.fill.fgColor.rgb == "FFC6EFCE"
        dark = cell.font.color is not None and cell.font.color.rgb == "FF006100"
        assert (green and dark) == (status == "Held" and t in cash_like and t in held), (t, status)


@needs_workbook
def test_read_performance_tab_reads_the_live_entries():
    pytest.importorskip("openpyxl")
    from landry.performance import read_performance_tab
    for e in read_performance_tab(_WB):               # however many there are today
        assert e.ticker and isinstance(e.entry_date, dt.date) and e.entry_price > 0
        assert e.band in ("STRONG BUY", "BUY")
        assert e.exit_date is None or e.exit_date >= e.entry_date


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
