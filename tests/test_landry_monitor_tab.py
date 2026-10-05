"""Offline tests for landry.monitor_tab, the Monitor audit checks and the `landry monitor` command -- synthetic
workbooks, fake price / insider / analyst providers (no network, no LibreOffice)."""

import datetime as dt
import hashlib
import os

import openpyxl
import pandas as pd
import pytest

from landry import cli, ledger, monitor_tab as mt
from landry.audit import check_monitor_last_score, check_monitor_signals
from landry.xlsx_io import latest_workbook

D = dt.date
_WB = latest_workbook(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
needs_workbook = pytest.mark.skipif(not _WB, reason="no workbook file")

MON_HEAD = ["Ticker", "Category", "Last Score\nDate", "Last\nComposite", "Last Tier 1\nWtd Avg", "Last\nDecision",
            "Price at\nLast Score", "Current\nPrice", "% Price\nChange", "Next/Last\nEarnings Date",
            "Insider\nActivity? (Y/N)", "Insider Note", "Analyst\nShift? (Y/N)", "Analyst Shift\nNote", "Days Since\nLast Score",
            "Recheck Status", "Notes"]
# ticker, date scored, tier 1 avg, composite, decision (Scoring) -- col A, C, N, AF, AG
SCORING = [("AAA", dt.datetime(2026, 9, 13), 4.2857142857, 89.2, "STRONG BUY"),
           ("BBB", dt.datetime(2026, 9, 30), 3.9285714286, 76.0, "BUY"),
           ("CCC", dt.datetime(2026, 9, 13), 3.5, 63.4, "AVOID"),              # scored but not held
           ("DDD", None, None, None, None)]                                     # never scored
# Monitor rows as they stand: ticker, category, date, composite, tier 1, decision, price, insider, note, analyst, note
MONITOR = [("AAA", "Owned", dt.datetime(2026, 8, 6), 93.2, 4.57, "STRONG BUY", 211.87, "Y", "2026-09-02: X sold", "N", None),
           ("BBB", "Owned", dt.datetime(2026, 9, 30), 76.0, 3.93, "BUY", 129.84, "N", None, "N", None),
           ("CCC", "Watch List", dt.datetime(2026, 8, 11), 70.0, 4.0, "BUY", 50.0, "N", None, "N", None),
           ("DDD", "Watch List", None, None, None, None, None, "N", None, "N", None)]


def make_book(tmp_path, monitor=MONITOR, held=("AAA", "BBB", "ETFX", "FZ", "DDD"), name="m.xlsx"):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    cp = wb.create_sheet("Current Positions")
    cp["A1"] = "Combined"
    for c, h in enumerate(["Account", "Ticker", "Description", "Asset Class", "Quantity", "Price ($)", "Market Value ($)",
                           "Cost Basis ($)", "Unrealized G/L ($)", "G/L %", "% of Account", "% of Combined", "Notes"], 1):
        cp.cell(row=2, column=c, value=h)
    for i, t in enumerate(held):
        r = 3 + i
        for c, v in enumerate(("Acct A", t, t + " Inc", "Cash" if t == "FZ" else "Equity", 10, 10.0, 100.0, 90.0, 10.0,
                               0.1, 0.1, 0.1, ""), 1):
            cp.cell(row=r, column=c, value=v)
    sc = wb.create_sheet("Scoring")
    sc["A1"] = "Scoring"
    sc.cell(row=2, column=1, value="Ticker")
    for i, (t, d, t1, comp, dec) in enumerate(SCORING):
        r = 3 + i
        sc.cell(row=r, column=1, value=t)
        sc.cell(row=r, column=2, value=t + " Inc")
        sc.cell(row=r, column=3, value=d)
        sc.cell(row=r, column=14, value=t1)
        sc.cell(row=r, column=32, value=comp)
        sc.cell(row=r, column=33, value=dec)
        for c in (34, 35, 36, 37):
            sc.cell(row=r, column=c, value="OK")
    mo = wb.create_sheet(mt.SHEET)
    mo["A1"] = "Recheck triggers for every name that's been scored ..."
    mo.merge_cells("A1:Q1")
    for c, h in enumerate(MON_HEAD, 1):
        mo.cell(row=2, column=c, value=h)
    for i, (t, cat, d, comp, t1, dec, px, ik, inote, ak, anote) in enumerate(monitor):
        r = 3 + i
        for col, v in ((1, t), (2, cat), (3, d), (4, comp), (5, t1), (6, dec), (7, px), (11, ik), (12, inote),
                       (13, ak), (14, anote)):
            mo.cell(row=r, column=col, value=v)
    mo.cell(row=3 + len(monitor) + 1, column=1, value="Footnote: not a ticker row")
    mt.add_as_of_cell(wb)
    path = str(tmp_path / name)
    wb.save(path)
    return path


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def cells(path, sheet=mt.SHEET):
    ws = openpyxl.load_workbook(path)[sheet]
    return {c.coordinate: c.value for row in ws.iter_rows() for c in row if c.value is not None}


# ------------------------------------------------------------------ the mirror --

def test_mirror_problems_names_every_way_a_row_can_differ(tmp_path):
    path = make_book(tmp_path)
    problems = dict(mt.mirror_problems(path))
    assert set(problems) == {"AAA"}                         # BBB mirrors; CCC is not held; DDD is not scored; ETFX / FZ have no score
    assert any("last scored 08/06/26 on the Monitor, 09/13/26 on Scoring" in p for p in problems["AAA"])
    assert any("composite 93.2 vs 89.2" in p for p in problems["AAA"])
    assert any("Tier 1 average 4.57 vs 4.29" in p for p in problems["AAA"])
    assert not any("decision" in p for p in problems["AAA"])      # STRONG BUY both


def test_a_missing_row_a_wrong_decision_a_newer_date_and_no_price_are_all_caught(tmp_path):
    mon = [("AAA", "Owned", dt.datetime(2026, 9, 14), 89.2, 4.29, "BUY", None, "N", None, "N", None),      # newer date, wrong decision, no price
           ("DDD", "Watch List", None, None, None, None, None, "N", None, "N", None)]                      # BBB has no row at all
    problems = dict(mt.mirror_problems(make_book(tmp_path, monitor=mon)))
    assert problems["BBB"] == ["no row on the Monitor tab"]
    assert any("09/14/26 on the Monitor, 09/13/26 on Scoring" in p for p in problems["AAA"])
    assert any("decision BUY vs STRONG BUY" in p for p in problems["AAA"]) and "no Price at Last Score" in problems["AAA"]


def test_a_book_that_mirrors_has_no_problems_and_a_composite_within_rounding_passes(tmp_path):
    mon = [("AAA", "Owned", dt.datetime(2026, 9, 13), 89.2, 4.29, "strong buy", 218.29, "N", None, "N", None),   # case-insensitive
           ("BBB", "Owned", dt.datetime(2026, 9, 30), 76.0, 3.93, "BUY", 129.84, "N", None, "N", None)]
    assert mt.mirror_problems(make_book(tmp_path, monitor=mon)) == []


# ----------------------------------------------------------------------- stamp --

def test_stamp_writes_all_five_cells_for_tickers_that_need_it_and_nothing_else(tmp_path):
    path = make_book(tmp_path)
    before = cells(path)
    seen = []
    rep = mt.stamp(path, price_fn=lambda t, d: seen.append((t, d)) or 218.294)
    assert rep["stamped"] == [("AAA", D(2026, 8, 6), D(2026, 9, 13))] and rep["wrote"] and seen == [("AAA", D(2026, 9, 13))]
    after = cells(path)
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed == {"C3", "D3", "E3", "G3"}                   # decision text was already right
    ws = openpyxl.load_workbook(path)[mt.SHEET]
    assert (ws["C3"].value, ws["D3"].value, ws["E3"].value, ws["F3"].value, ws["G3"].value) == \
        (dt.datetime(2026, 9, 13), 89.2, 4.29, "STRONG BUY", 218.29)
    # a second run finds nothing to do and saves nothing
    h = digest(path)
    rep2 = mt.stamp(path, price_fn=lambda t, d: pytest.fail("no lookup needed"))
    assert rep2["stamped"] == [] and not rep2["wrote"] and digest(path) == h


def test_a_price_that_cannot_be_found_leaves_the_whole_row_alone(tmp_path):
    path = make_book(tmp_path)
    before = cells(path)
    rep = mt.stamp(path, price_fn=lambda t, d: None, attempts=2, sleep=lambda s: None)
    assert rep["price_failed"] == ["AAA"] and rep["stamped"] == [] and not rep["wrote"] and cells(path) == before


def test_the_price_is_kept_when_only_the_decision_moved(tmp_path):
    """The 10/4 Rule 3 change moved Decisions without a re-score: same date, so the 'price at last score' stands."""
    mon = [("AAA", "Owned", dt.datetime(2026, 9, 13), 89.2, 4.29, "WATCH LIST", 218.29, "N", None, "N", None),
           ("BBB", "Owned", dt.datetime(2026, 9, 30), 76.0, 3.93, "BUY", 129.84, "N", None, "N", None)]
    path = make_book(tmp_path, monitor=mon)
    rep = mt.stamp(path, price_fn=lambda t, d: pytest.fail("the date did not change"))
    assert rep["stamped"] == [("AAA", D(2026, 9, 13), D(2026, 9, 13))]
    ws = openpyxl.load_workbook(path)[mt.SHEET]
    assert ws["F3"].value == "STRONG BUY" and ws["G3"].value == 218.29


def test_named_tickers_are_stamped_even_when_they_already_mirror(tmp_path):
    path = make_book(tmp_path)
    rep = mt.stamp(path, ["BBB"], price_fn=lambda t, d: pytest.fail("nothing to look up"))
    assert rep["stamped"] == [] and rep["unchanged"] == 1


def test_a_moved_column_is_refused_before_anything_is_written(tmp_path):
    path = make_book(tmp_path)
    wb = openpyxl.load_workbook(path)
    wb[mt.SHEET]["D2"].value = "Something else"
    wb.save(path)
    h = digest(path)
    with pytest.raises(mt.MonitorError, match="header of column D"):
        mt.stamp(path, price_fn=lambda t, d: 1.0)
    assert digest(path) == h


def test_a_dry_run_reports_and_writes_nothing(tmp_path):
    path = make_book(tmp_path)
    h = digest(path)
    rep = mt.stamp(path, price_fn=lambda t, d: 218.29, write=False)
    assert rep["stamped"] and not rep["wrote"] and digest(path) == h


def test_a_scoring_row_without_cached_values_asks_for_a_recalc(tmp_path):
    wb = openpyxl.load_workbook(make_book(tmp_path))
    wb["Scoring"]["AF3"].value = None
    p = str(tmp_path / "nocache.xlsx")
    wb.save(p)
    with pytest.raises(mt.MonitorError, match="recalculate"):
        mt.stamp(p, price_fn=lambda t, d: 1.0)


def test_last_close_on_or_before_takes_the_friday_for_a_sunday():
    idx = pd.to_datetime(["2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-14"])
    frame = pd.DataFrame({"Close": [10.0, 11.0, 12.0, 13.456, 14.0]}, index=idx)
    assert mt.last_close_on_or_before("X", D(2026, 9, 13), history=lambda t, s, e: frame) == 13.46     # Friday 9/11, not Monday 9/14
    assert mt.last_close_on_or_before("X", D(2026, 9, 10), history=lambda t, s, e: frame) == 12.0      # the day itself counts
    assert mt.last_close_on_or_before("X", D(2026, 9, 1), history=lambda t, s, e: frame) is None
    assert mt.last_close_on_or_before("X", D(2026, 9, 13), history=lambda t, s, e: pd.DataFrame({"Close": []})) is None


# --------------------------------------------------------------------- signals --

def test_refresh_signals_rewrites_k_to_n_for_held_names_and_dates_a_complete_run(tmp_path):
    path = make_book(tmp_path)
    ins = {"AAA": ("N", None), "BBB": ("Y", "2026-10-02: Y sold $1")}
    ana = {"AAA": ("N", None), "BBB": ("Y", "Consensus declined 0.20")}
    rep = mt.refresh_signals(path, insider_fn=lambda t: ins[t], analyst_fn=lambda t: ana[t], today=D(2026, 10, 5))
    assert rep["refreshed"] == 2 and rep["as_of"] == D(2026, 10, 5) and rep["wrote"] and not rep["failed"]
    assert dict(rep["changed"]) == {"AAA": "insider Y->N", "BBB": "insider N->Y, analyst N->Y"}
    ws = openpyxl.load_workbook(path)[mt.SHEET]
    assert (ws["K3"].value, ws["L3"].value) == ("N", None)                       # no activity: the old note goes
    assert (ws["K4"].value, ws["L4"].value, ws["M4"].value, ws["N4"].value) == ("Y", "2026-10-02: Y sold $1", "Y", "Consensus declined 0.20")
    assert ws["Q1"].value == dt.datetime(2026, 10, 5)
    assert ws["K5"].value == "N"                                                  # CCC is not held: untouched
    h = digest(path)
    rep2 = mt.refresh_signals(path, insider_fn=lambda t: ins[t], analyst_fn=lambda t: ana[t], today=D(2026, 10, 5))
    assert rep2["changed"] == [] and not rep2["wrote"] and digest(path) == h


def test_a_failed_lookup_keeps_the_cell_and_leaves_the_refresh_undated(tmp_path):
    path = make_book(tmp_path)
    rep = mt.refresh_signals(path, insider_fn=lambda t: (None, None) if t == "AAA" else ("N", None),
                             analyst_fn=lambda t: ("N", None), today=D(2026, 10, 5), attempts=2, sleep=lambda s: None)
    assert rep["failed"] == ["AAA (insider)"] and rep["as_of"] is None
    ws = openpyxl.load_workbook(path)[mt.SHEET]
    assert (ws["K3"].value, ws["L3"].value) == ("Y", "2026-09-02: X sold")        # what it held stays
    assert ws["Q1"].value is None                                                 # still overdue


def test_a_provider_without_history_is_unavailable_not_failed_and_does_not_hold_the_month_open(tmp_path):
    """yfinance had no 3-month-ago breakdown for six of the sixteen held names on 10/5 (VRTX, PLD, V, ANET, KLAC, TSM)."""
    path = make_book(tmp_path)
    ana = {"AAA": (None, None, "no_history"), "BBB": ("N", None, "ok")}
    rep = mt.refresh_signals(path, insider_fn=lambda t: ("N", None), analyst_fn=lambda t: ana[t], today=D(2026, 10, 5),
                             attempts=3, sleep=lambda s: pytest.fail("nothing to retry"))
    assert rep["failed"] == [] and rep["unavailable"] == ["AAA (analyst: no history from the provider)"]
    assert rep["as_of"] == D(2026, 10, 5)                                         # complete: the month is closed
    ws = openpyxl.load_workbook(path)[mt.SHEET]
    assert ws["M3"].value == "N" and ws["K3"].value == "N" and ws["Q1"].value == dt.datetime(2026, 10, 5)


def test_a_real_lookup_error_still_holds_the_month_open(tmp_path):
    path = make_book(tmp_path)
    rep = mt.refresh_signals(path, insider_fn=lambda t: ("N", None), analyst_fn=lambda t: (None, None, "error"),
                             today=D(2026, 10, 5), attempts=2, sleep=lambda s: None)
    assert rep["failed"] == ["AAA (analyst)", "BBB (analyst)"] and rep["as_of"] is None


def _fake_yfinance(monkeypatch, recs):
    import sys
    import types

    class Ticker:
        def __init__(self, t):
            self.t = t

        @property
        def recommendations(self):
            r = recs[self.t]
            if isinstance(r, Exception):
                raise r
            return r
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=Ticker))


def _breakdown(periods):
    """yfinance's recommendations frame: one row per period, counts of strongBuy / buy / hold / sell / strongSell."""
    return pd.DataFrame([{"period": p, "strongBuy": sb, "buy": b, "hold": h, "sell": 0, "strongSell": 0}
                         for p, (sb, b, h) in periods.items()])


def test_analyst_shift_detail_says_why_a_lookup_returned_nothing(monkeypatch):
    from landry import data_auto
    four = {"0m": (10, 10, 0), "-1m": (8, 10, 2), "-2m": (6, 10, 4), "-3m": (4, 10, 6)}      # consensus rose well over 0.15
    flat = {"0m": (5, 5, 5), "-1m": (5, 5, 5), "-2m": (5, 5, 5), "-3m": (5, 5, 5)}
    _fake_yfinance(monkeypatch, {"UP": _breakdown(four), "FLAT": _breakdown(flat), "SHORT": _breakdown({k: v for k, v in four.items() if k != "-3m"}),
                                 "EMPTY": pd.DataFrame(), "BOOM": RuntimeError("rate limited")})
    flag, note, why = data_auto.analyst_shift_detail("UP")
    assert (flag, why) == ("Y", "ok") and "improved" in note and "over 3 months" in note
    assert data_auto.analyst_shift_detail("FLAT") == ("N", None, "ok")
    assert data_auto.analyst_shift_detail("SHORT") == (None, None, "no_history")            # no 3-month-ago row
    assert data_auto.analyst_shift_detail("EMPTY") == (None, None, "no_history")
    assert data_auto.analyst_shift_detail("BOOM") == (None, None, "error")
    assert data_auto.analyst_shift_flag("UP") == (flag, note) and data_auto.analyst_shift_flag("SHORT") == (None, None)


def test_refreshing_named_tickers_never_dates_the_tab(tmp_path):
    path = make_book(tmp_path)
    rep = mt.refresh_signals(path, ["AAA"], insider_fn=lambda t: ("N", None), analyst_fn=lambda t: ("N", None),
                             today=D(2026, 10, 5))
    assert rep["refreshed"] == 1 and rep["as_of"] is None
    assert openpyxl.load_workbook(path)[mt.SHEET]["Q1"].value is None


def test_the_as_of_cell_narrows_the_title_merge_and_names_itself(tmp_path):
    path = make_book(tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb[mt.SHEET]
    assert [str(m) for m in ws.merged_cells.ranges] == ["A1:O1"]
    assert ws["P1"].value == "Insider / analyst signals refreshed" and mt.AS_OF_NAME in wb.defined_names
    assert list(wb.defined_names[mt.AS_OF_NAME].destinations) == [(mt.SHEET, "$Q$1")]
    mt.add_as_of_cell(wb)                                                          # idempotent
    assert [str(m) for m in ws.merged_cells.ranges] == ["A1:O1"]


# ----------------------------------------------------------------- audit checks --

def test_the_mirror_check_passes_fails_and_skips(tmp_path):
    (c,) = check_monitor_last_score(make_book(tmp_path))
    assert not c.ok and "AAA" in c.detail and "monitor stamp" in c.fix
    mon = [("AAA", "Owned", dt.datetime(2026, 9, 13), 89.2, 4.29, "STRONG BUY", 218.29, "N", None, "N", None),
           ("BBB", "Owned", dt.datetime(2026, 9, 30), 76.0, 3.93, "BUY", 129.84, "N", None, "N", None)]
    (c,) = check_monitor_last_score(make_book(tmp_path, monitor=mon, name="ok.xlsx"))
    assert c.ok and "2 tickers" in c.detail
    bare = tmp_path / "bare.xlsx"
    openpyxl.Workbook().save(bare)
    (c,) = check_monitor_last_score(str(bare))
    assert c.ok and "skipped" in c.detail


def test_the_signals_check_holds_the_refresh_to_a_month_and_a_week(tmp_path):
    path = make_book(tmp_path)
    (c,) = check_monitor_signals(path, today=D(2026, 10, 5))
    assert not c.ok and "no 'signals refreshed' date" in c.detail and "monitor refresh" in c.fix
    wb = openpyxl.load_workbook(path)
    wb[mt.SHEET]["Q1"].value = dt.datetime(2026, 9, 4)
    wb.save(path)
    (c,) = check_monitor_signals(path, today=D(2026, 10, 5))
    assert c.ok and "31 day(s) ago" in c.detail
    (c,) = check_monitor_signals(path, today=D(2026, 10, 10))                       # 36 days
    assert not c.ok and "the limit is 35" in c.detail
    bare = tmp_path / "bare.xlsx"
    openpyxl.Workbook().save(bare)
    (c,) = check_monitor_signals(str(bare))
    assert c.ok and "skipped" in c.detail


# ------------------------------------------------------------------------- CLI --

def _args(action, **kw):
    import argparse
    ns = dict(cmd="monitor", action=action, tickers=[], workbook="x.xlsx", dry_run=False, no_recalc=True, force=False)
    ns.update(kw)
    return argparse.Namespace(**ns)


def test_cli_parser_accepts_the_monitor_command(monkeypatch):
    monkeypatch.setattr(cli, "_cmd_monitor", lambda a: (a.action, a.tickers, a.dry_run))
    assert cli.main(["monitor", "stamp", "CRWD", "NVDA", "--dry-run", "--workbook", "x.xlsx"]) == ("stamp", ["CRWD", "NVDA"], True)
    assert cli.main(["monitor", "status"]) == ("status", [], False)


def test_cli_status_exits_nonzero_while_anything_is_out_of_date(tmp_path, capsys):
    path = make_book(tmp_path)
    assert cli._cmd_monitor(_args("status", workbook=path)) == 1
    out = capsys.readouterr().out
    assert "AAA:" in out and "1 held position(s) do not mirror Scoring" in out and "never dated" in out


def test_cli_stamp_refuses_while_excel_has_the_workbook_open(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ledger, "_excel_has_open", lambda p: True)
    assert cli._cmd_monitor(_args("stamp", workbook=make_book(tmp_path))) == 1
    assert "Excel appears to have the workbook open" in capsys.readouterr().err


def test_cli_stamp_and_refresh_report_and_exit_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ledger, "_excel_has_open", lambda p: False)
    monkeypatch.setattr(mt, "last_close_on_or_before", lambda t, d, history=None: 218.29)
    path = make_book(tmp_path)
    assert cli._cmd_monitor(_args("stamp", workbook=path)) == 0
    assert "stamped AAA: last scored 08/06/26 -> 09/13/26" in capsys.readouterr().out
    monkeypatch.setattr("landry.data_auto.insider_activity_flag", lambda t: ("N", None))
    monkeypatch.setattr("landry.data_auto.analyst_shift_flag", lambda t: ("N", None))
    assert cli._cmd_monitor(_args("refresh", workbook=path)) == 0
    assert "signals dated" in capsys.readouterr().out
    assert cli._cmd_monitor(_args("status", workbook=path)) == 0


# --------------------------------------------------------------- the live workbook --

@needs_workbook
def test_live_monitor_mirrors_scoring_for_every_held_position():
    assert mt.mirror_problems(_WB) == []
    (c,) = check_monitor_last_score(_WB)
    assert c.ok, c.detail


@needs_workbook
def test_live_signals_are_dated_and_within_a_month_of_the_last_refresh():
    as_of = mt.signals_as_of(_WB)
    assert as_of is not None and as_of >= D(2026, 10, 5)
