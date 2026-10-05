"""Offline tests for landry.audit -- synthetic workbooks only, no
dependency on the real LANDRY_SYSTEM_WORKBOOK_*.xlsx."""

import json
import subprocess

import openpyxl
from openpyxl.worksheet.table import Table, TableColumn

from landry.audit import (
    check_merges_inside_tables,
    check_scoring_verification,
    check_row_height_ceiling,
    check_cross_tab_references,
    check_held_positions_tracked,
    check_price_history,
    check_page_setup_vs_last_commit,
    check_reader_bounds,
    check_schema_reference,
    check_table_refs,
    report,
)
from landry.doctor import Check


def _add_table(ws, name, ref):
    tbl = Table(displayName=name, ref=ref)
    ws.add_table(tbl)


def test_table_ref_flags_a_genuinely_missed_row(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Ticker", "Company", "Qty"])
    ws.append(["MU", "Micron", 10])
    ws.append(["NEW", "New Position", 5])  # added past the table's ref
    _add_table(ws, "T", "A1:C2")
    p = tmp_path / "wb.xlsx"
    wb.save(p)
    checks = check_table_refs(str(p))
    assert len(checks) == 1
    assert not checks[0].ok
    assert "row 3" in checks[0].detail


def test_table_ref_ignores_a_sparse_footnote_row(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Ticker", "Company", "Qty"])
    ws.append(["MU", "Micron", 10])
    ws.append(["This is a freeform footnote, not a data row", None, None])
    _add_table(ws, "T", "A1:C2")
    p = tmp_path / "wb.xlsx"
    wb.save(p)
    checks = check_table_refs(str(p))
    assert len(checks) == 1
    assert checks[0].ok


def test_reader_bounds_flags_a_stale_hardcoded_bound(tmp_path, monkeypatch):
    import landry.xlsx_io as xio

    def fake_reader(path: str, sheet: str = "Fake") -> list:
        """A=Ticker,B=Company."""
        wb, ws = xio._open(path, sheet)
        out = []
        for r in ws.iter_rows(min_row=3, max_row=5, values_only=True):
            if not r or not r[0]:
                continue
            out.append(dict(ticker=r[0]))
        wb.close()
        return out

    monkeypatch.setattr(xio, "read_fake_tab", fake_reader, raising=False)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fake"
    ws.append(["header"])
    ws.append(["header2"])
    for i in range(3, 8):
        ws.append([f"T{i}", "Co"])
    p = tmp_path / "wb.xlsx"
    wb.save(p)

    checks = check_reader_bounds(str(p))
    hit = [c for c in checks if c.name == "reader_bound:read_fake_tab"]
    assert len(hit) == 1
    assert not hit[0].ok
    assert "row 7" in hit[0].detail


def test_cross_tab_reference_flags_blank_target_column(tmp_path):
    wb = openpyxl.Workbook()
    a = wb.active
    a.title = "A"
    a["A1"] = "=Target!C2"
    b = wb.create_sheet("Target")
    b["A2"] = "Ticker"
    b["B2"] = "Company"
    # C2 deliberately left blank -- the formula points at a dead column
    p = tmp_path / "wb.xlsx"
    wb.save(p)
    checks = check_cross_tab_references(str(p))
    bad = [c for c in checks if not c.ok]
    assert any("Target" in c.detail and "C2" in c.detail for c in bad)


def test_cross_tab_reference_flags_truncated_range(tmp_path):
    wb = openpyxl.Workbook()
    a = wb.active
    a.title = "A"
    a["A1"] = "=COUNTIF(Target!B3:B5,\"x\")"
    b = wb.create_sheet("Target")
    for r in range(3, 8):
        b.cell(row=r, column=2, value="x")  # real data through row 7
    p = tmp_path / "wb.xlsx"
    wb.save(p)
    checks = check_cross_tab_references(str(p))
    bad = [c for c in checks if not c.ok]
    assert any("row 7" in c.detail for c in bad)


def test_cross_tab_reference_clean_workbook_reports_ok(tmp_path):
    wb = openpyxl.Workbook()
    a = wb.active
    a.title = "A"
    a["A1"] = "=Target!B2"
    b = wb.create_sheet("Target")
    b["B2"] = "Header"
    p = tmp_path / "wb.xlsx"
    wb.save(p)
    checks = check_cross_tab_references(str(p))
    assert len(checks) == 1
    assert checks[0].ok


def test_page_setup_skips_cleanly_when_not_a_git_repo(tmp_path, monkeypatch):
    def fake_run(*a, **k):
        raise subprocess.CalledProcessError(128, "git")
    monkeypatch.setattr(subprocess, "run", fake_run)

    wb = openpyxl.Workbook()
    p = tmp_path / "wb.xlsx"
    wb.save(p)
    checks = check_page_setup_vs_last_commit(str(p), repo_dir=str(tmp_path))
    assert len(checks) == 1
    assert checks[0].ok
    assert "skipped" in checks[0].detail


def test_schema_reference_flags_wrong_table_name(tmp_path):
    wb = openpyxl.Workbook()
    sr = wb.active
    sr.title = "Schema Reference"
    sr["A4"] = "Target"
    sr["A6"] = "Table"
    sr["B6"] = "WrongName"
    sr["C6"] = "A2:B10"
    target = wb.create_sheet("Target")
    target["A2"] = "Ticker"
    target["B2"] = "Company"
    _add_table(target, "RealName", "A2:B10")
    p = tmp_path / "wb.xlsx"
    wb.save(p)
    checks = check_schema_reference(str(p))
    hit = [c for c in checks if c.name == "schema_ref:Target/table_name"]
    assert len(hit) == 1
    assert not hit[0].ok


def test_report_formats_pass_and_fail_counts():
    checks = [Check("a", True, "fine"), Check("b", False, "broken", fix="do X")]
    text = report(checks, "wb.xlsx")
    assert "wb.xlsx" in text
    assert "1 passed, 1 failed" in text
    assert "do X" in text
    assert "Review the failures" in text


def test_report_all_clean():
    text = report([Check("a", True, "fine")])
    assert "No structural drift detected." in text


def test_row_height_ceiling_flags_a_row_excel_would_call_corrupt(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Current Positions"
    ws["A52"] = "a long note"
    ws.row_dimensions[52].height = 941.25
    ws.row_dimensions[10].height = 409.5               # exactly the ceiling is fine
    p = tmp_path / "wb.xlsx"
    wb.save(p)
    checks = check_row_height_ceiling(str(p))
    assert len(checks) == 1 and not checks[0].ok
    assert "Current Positions!52 (941.25pt)" in checks[0].detail
    assert "merge" in checks[0].fix.lower()


def test_row_height_ceiling_passes_a_workbook_within_the_limit(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.row_dimensions[3].height = 409.5
    p = tmp_path / "wb.xlsx"
    wb.save(p)
    assert [c.ok for c in check_row_height_ceiling(str(p))] == [True]


def test_merges_inside_tables_flags_the_stale_merge_left_by_a_row_insert(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Current Positions"
    ws.append(["Ticker", "Company", "Qty"])
    for i in range(5):
        ws.append([f"T{i}", "Co", i])
    _add_table(ws, "CurrentPositionsTable", "A1:C6")
    ws.merge_cells("A5:C5")                              # a merge that now sits inside the Table
    p = tmp_path / "wb.xlsx"
    wb.save(p)
    checks = check_merges_inside_tables(str(p))
    assert len(checks) == 1 and not checks[0].ok
    assert "A5:C5" in checks[0].detail and "CurrentPositionsTable" in checks[0].detail


def test_merges_inside_tables_allows_a_merge_just_below_the_table(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Ticker", "Company", "Qty"])
    ws.append(["MU", "Micron", 10])
    _add_table(ws, "T", "A1:C2")
    ws.merge_cells("A4:C4")                              # a footnote banner, outside the Table
    p = tmp_path / "wb.xlsx"
    wb.save(p)
    assert [c.ok for c in check_merges_inside_tables(str(p))] == [True]


# --------------------------------------------------------------------------- #
# scoring_verification: the same-second "bulk import" fingerprint
# --------------------------------------------------------------------------- #

_TIER1 = ("fcf_yield_trend", "revenue_growth_consistency", "competitive_moat",
          "revenue_visibility", "fcf_margin_trend")


def _scoring_fixture(tmp_path, entries):
    """A one-ticker Scoring tab (XYZ with a real Tier 1 Wtd Avg in col N) plus
    a landry_scores.json whose approved section is `entries`:
    [(indicator, source, approved_at, rationale), ...]."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Scoring"
    ws.append(["title"])
    ws.append(["Ticker"])
    ws.cell(row=3, column=1, value="XYZ")
    ws.cell(row=3, column=14, value=3.5)
    wb_path = tmp_path / "wb.xlsx"
    wb.save(wb_path)
    approved = {ind: {"indicator": ind, "score": 4, "confidence": "M",
                      "approved_by": "Alan", "approved_at": at, "source": src,
                      "rationale": why}
                for ind, src, at, why in entries}
    (tmp_path / "landry_scores.json").write_text(json.dumps(
        {"tickers": {"XYZ": {"pending": {}, "approved": approved, "rejected": {}}},
         "audit": []}))
    return str(wb_path)


def _bulk(checks):
    return [c for c in checks if c.name.endswith("/bulk_import")]


def test_bulk_import_flagged_when_same_second_manual_entries_share_a_rationale(tmp_path):
    ts = "2026-09-09T00:13:54+00:00"
    wb = _scoring_fixture(tmp_path, [
        (ind, "manual", ts, "BACKFILLED after the fact") for ind in _TIER1[:3]])
    flagged = _bulk(check_scoring_verification(wb, repo_dir=str(tmp_path)))
    assert len(flagged) == 1 and not flagged[0].ok
    assert "XYZ" in flagged[0].name


def test_bulk_import_flagged_when_same_second_manual_entries_have_no_rationale(tmp_path):
    ts = "2026-09-09T00:13:54+00:00"
    wb = _scoring_fixture(tmp_path, [
        ("competitive_moat", "manual", ts, "a genuine, specific note"),
        ("revenue_visibility", "manual", ts, ""),
        ("management_quality", "manual", ts, "another genuine, specific note")])
    assert len(_bulk(check_scoring_verification(wb, repo_dir=str(tmp_path)))) == 1


def test_bulk_import_not_flagged_for_a_genuine_review_written_in_one_call(tmp_path):
    """VEEV, 2026-10-02: three researched judgments, each with its own
    evidence-citing rationale, approved in one scripted call (same second)."""
    ts = "2026-10-02T21:28:47+00:00"
    wb = _scoring_fixture(tmp_path, [
        ("competitive_moat", "manual", ts, "Switching costs, 12 of top-20 committed ..."),
        ("revenue_visibility", "manual", ts, "Subscription 82.6% of Q2 revenue ..."),
        ("management_quality", "manual", ts, "Founder-CEO, aligned pay, buybacks ...")])
    checks = check_scoring_verification(wb, repo_dir=str(tmp_path))
    assert _bulk(checks) == []


def test_bulk_import_ignores_quant_drafts_and_pairs(tmp_path):
    ts = "2026-09-13T21:02:14+00:00"
    wb = _scoring_fixture(tmp_path, [
        (ind, "quant_draft", ts, "same text") for ind in _TIER1[:3]] + [
        ("management_quality", "manual", ts, "same text"),
        ("roic_vs_wacc", "manual", ts, "same text")])        # only two manual in that second
    assert _bulk(check_scoring_verification(wb, repo_dir=str(tmp_path))) == []


# --------------------------------------------------------------------------- #
# price_history: the header / holdings / chart-title guard
# --------------------------------------------------------------------------- #

def _ph_workbook(tmp_path, header, chart=None, last_row_values=True):
    import datetime as _dt
    from openpyxl.chart import LineChart, Reference
    wb = openpyxl.Workbook()
    ph = wb.active
    ph.title = "Price History"
    ph["A2"] = "Week Ending"
    for i, h in enumerate(header):
        ph.cell(row=2, column=2 + i, value=h)
    for r, d in enumerate((_dt.datetime(2026, 9, 25), _dt.datetime(2026, 10, 2)), start=3):
        ph.cell(row=r, column=1, value=d)
        for i, h in enumerate(header):
            if h and (last_row_values or r == 3):
                ph.cell(row=r, column=2 + i, value=100.0 + i)
    ret = wb.create_sheet("Returns (Calc)")
    if chart:
        title, col = chart
        ch = LineChart()
        ch.title = title
        ch.add_data(Reference(ret, min_col=col, min_row=3, max_row=500))
        ret.add_chart(ch, "A164")
    p = tmp_path / "ph.xlsx"
    wb.save(p)
    return str(p)


def _patch_positions(monkeypatch, tickers):
    class Pos:
        def __init__(self, ticker):
            self.ticker, self.quantity = ticker, 10
    import landry.xlsx_io as xio
    monkeypatch.setattr(xio, "read_positions", lambda p: [Pos(t) for t in tickers])


def test_price_history_check_passes_a_clean_tab(tmp_path, monkeypatch):
    _patch_positions(monkeypatch, ["AAA", "BBB"])
    checks = check_price_history(_ph_workbook(tmp_path, ["AAA", "BBB"], chart=("BBB", 3)))
    assert len(checks) == 1 and checks[0].ok


def test_price_history_check_flags_a_duplicate_header(tmp_path, monkeypatch):
    _patch_positions(monkeypatch, ["AAA", "BBB"])
    checks = check_price_history(_ph_workbook(tmp_path, ["AAA", "BBB", "AAA"]))
    assert any(not c.ok and "AAA twice" in c.detail for c in checks)


def test_price_history_check_flags_a_holding_with_no_column(tmp_path, monkeypatch):
    _patch_positions(monkeypatch, ["AAA", "BBB", "NEWHOLD"])
    checks = check_price_history(_ph_workbook(tmp_path, ["AAA", "BBB"]))
    assert any(not c.ok and "NEWHOLD" in c.detail for c in checks)


def test_price_history_check_flags_a_blank_in_the_newest_row(tmp_path, monkeypatch):
    _patch_positions(monkeypatch, ["AAA", "BBB"])
    checks = check_price_history(_ph_workbook(tmp_path, ["AAA", "BBB"], last_row_values=False))
    assert any(not c.ok and "last row" in c.detail for c in checks)


def test_price_history_check_flags_a_chart_plotting_the_wrong_ticker(tmp_path, monkeypatch):
    _patch_positions(monkeypatch, ["AAA", "BBB"])
    # titled AAA, but reads Returns (Calc) column C -- the BBB column
    checks = check_price_history(_ph_workbook(tmp_path, ["AAA", "BBB"], chart=("AAA", 3)))
    bad = [c for c in checks if c.name == "price_history:chart"]
    assert len(bad) == 1 and not bad[0].ok and "'AAA'" in bad[0].detail and "BBB" in bad[0].detail


# ---------------------------------------------------------------- held positions vs the Watch List Tracker --

def _tracker_book(tmp_path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Watch List Tracker"
    ws.append(["Tracks every holding in Probationary Hold or Exit Review"])           # row 1, like the real tab
    ws.append(["Ticker", "Company", "Status"])                                        # header on row 2, data from row 3
    for ticker, status in rows:
        ws.append([ticker, ticker + " Inc", status])
    p = tmp_path / "wl.xlsx"
    wb.save(p)
    return str(p)


def _patch_held(monkeypatch, held, decisions):
    class Pos:
        def __init__(self, ticker, asset_class="Equity"):
            self.ticker, self.quantity, self.asset_class = ticker, 10, asset_class

    class Row:
        def __init__(self, ticker, decision):
            self.ticker, self.decision = ticker, decision
    import landry.xlsx_io as xio
    monkeypatch.setattr(xio, "read_positions",
                        lambda p: [Pos(t, "ETF-as-equity" if t == "ETFX" else "Equity") for t in held] + [Pos("FZDXX", "Cash")])
    monkeypatch.setattr(xio, "read_scoring_tab", lambda p: [Row(t, d) for t, d in decisions.items()])


def test_held_positions_tracked_passes_when_every_flagged_holding_is_in_the_tracker(tmp_path, monkeypatch):
    _patch_held(monkeypatch, ["AAA", "BBB", "CCC", "ETFX"],
                {"AAA": "AVOID", "BBB": "WATCH LIST", "CCC": "BUY", "DDD": "AVOID"})   # DDD is not held: ignored
    path = _tracker_book(tmp_path, [("AAA", "Exit Review"), ("BBB", "Probationary Hold")])
    (check,) = check_held_positions_tracked(path)
    assert check.ok and "2 of" in check.detail


def test_held_positions_tracked_flags_a_rule_3_holding_nobody_put_in_the_tracker(tmp_path, monkeypatch):
    """The 9/13 incident: AVGO, VRTX and CRWD tripped Rule 3 and nothing put two of them in the tracker."""
    _patch_held(monkeypatch, ["AVGO", "VRTX", "CRWD"], {"AVGO": "AVOID", "VRTX": "AVOID", "CRWD": "AVOID"})
    path = _tracker_book(tmp_path, [("CRWD", "Exit Review")])
    (check,) = check_held_positions_tracked(path)
    assert not check.ok
    assert "AVGO is AVOID" in check.detail and "VRTX is AVOID" in check.detail and "CRWD" not in check.detail


def test_held_positions_tracked_flags_the_wrong_status(tmp_path, monkeypatch):
    _patch_held(monkeypatch, ["AAA"], {"AAA": "AVOID"})
    path = _tracker_book(tmp_path, [("AAA", "Probationary Hold")])                  # Avoid needs Exit Review
    (check,) = check_held_positions_tracked(path)
    assert not check.ok and "found Probationary Hold" in check.detail


def test_held_positions_tracked_needs_no_tracker_when_nothing_is_flagged(tmp_path, monkeypatch):
    _patch_held(monkeypatch, ["AAA", "BBB"], {"AAA": "BUY", "BBB": "STRONG BUY"})
    wb = openpyxl.Workbook()
    p = tmp_path / "none.xlsx"
    wb.save(p)
    (check,) = check_held_positions_tracked(str(p))
    assert check.ok


# ------------------------------------------------ page setup: defaults an Excel save leaves out --

def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True, capture_output=True)


def _book(path, paper, grid, footer="page 1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "S"
    ws["A1"] = 1
    ws.page_setup.paperSize = paper
    ws.sheet_view.showGridLines = grid
    ws.oddFooter.center.text = footer
    wb.save(path)


def test_page_setup_treats_an_omitted_attribute_as_its_default(tmp_path):
    """Excel leaves default-valued attributes out (paperSize 1 = Letter, gridlines on): an Excel save of an unchanged
    sheet must not read as a lost page setup."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    wb = repo / "wb.xlsx"
    _book(wb, paper=1, grid=True)
    _git(repo, "add", "wb.xlsx")
    _git(repo, "commit", "-qm", "base")
    _book(wb, paper=None, grid=None)                                   # what Excel writes for the same sheet
    (check,) = check_page_setup_vs_last_commit(str(wb), str(repo))
    assert check.ok, check.detail


def test_page_setup_still_flags_a_dropped_non_default_setup_or_footer(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    wb = repo / "wb.xlsx"
    _book(wb, paper=9, grid=False)                                     # A4, gridlines off
    _git(repo, "add", "wb.xlsx")
    _git(repo, "commit", "-qm", "base")
    _book(wb, paper=None, grid=None, footer="")                        # a sheet rebuild drops all of it
    (check,) = check_page_setup_vs_last_commit(str(wb), str(repo))
    assert not check.ok
    assert "paperSize 9->None" in check.detail and "showGridLines False->None" in check.detail and "oddFooter.center" in check.detail
