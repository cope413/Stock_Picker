"""landry.sec_ttm: anniversary-aligned trailing-12-month windows, the Tier 1 drafts built on them, and the holdings
comparison against the scores of record (Journal rows 92-94). SEC data is synthetic, built so every expected figure
can be checked by hand."""

import datetime as dt
import json
from types import SimpleNamespace

import pytest

from landry import sec_ttm
from landry.fundamentals import Draft, draft_fcf_margin_trend, draft_fcf_yield_trend, trend_direction
from landry.scoring import ALL_WEIGHTS, IndicatorScore


def _facts(**series):
    """A minimal companyfacts payload: item -> {(start, end): USD}, each reported by the first tag for the item."""
    gaap = {}
    for item, periods in series.items():
        gaap[sec_ttm.TAGS[item][0]] = {"units": {"USD": [
            {"start": s, "end": e, "val": v, "filed": "2026-08-01", "form": "10-Q"}
            for (s, e), v in periods.items()]}}
    return {"facts": {"us-gaap": gaap}}


def _company(last_year=2026, with_h1_last=True):
    """Calendar-year filer. FY(y) for 2020..2025; H1(y) for 2020..last_year (the newest filing is the Q2 10-Q).
    cfo FY = 1000 + 100n, H1 = 450 + 50n; capex 100 / 45; sbc 200 / 90; revenue 5000 + 500n / 2400 + 250n (n = y - 2020)."""
    items = {k: {} for k in ("cfo", "capex", "sbc", "revenue")}
    base = {"cfo": (1000, 100, 450, 50), "capex": (100, 0, 45, 0), "sbc": (200, 0, 90, 0), "revenue": (5000, 500, 2400, 250)}
    for item, (fy0, fy_step, h0, h_step) in base.items():
        for y in range(2020, 2026):
            items[item][(f"{y}-01-01", f"{y}-12-31")] = fy0 + fy_step * (y - 2020)
        for y in range(2020, last_year + 1 if with_h1_last else last_year):
            items[item][(f"{y}-01-01", f"{y}-06-30")] = h0 + h_step * (y - 2020)
    return _facts(**items)


def _expected(item, y):
    """TTM to June of year y = FY(y-1) + H1(y) - H1(y-1), from the formulas in _company."""
    fy0, fy_step, h0, h_step = {"cfo": (1000, 100, 450, 50), "capex": (100, 0, 45, 0), "sbc": (200, 0, 90, 0),
                                "revenue": (5000, 500, 2400, 250)}[item]
    return (fy0 + fy_step * (y - 1 - 2020)) + (h0 + h_step * (y - 2020)) - (h0 + h_step * (y - 1 - 2020))


def test_windows_are_twelve_months_ending_at_the_latest_quarter_and_its_anniversaries():
    wins = sec_ttm.ttm_windows(_company(), n_windows=5)
    assert [w.end for w in wins] == [dt.date(y, 6, 30) for y in range(2022, 2027)]
    for w in wins:
        y = w.end.year
        assert (w.cfo, w.capex, w.sbc, w.revenue) == tuple(_expected(i, y) for i in ("cfo", "capex", "sbc", "revenue"))
    last = wins[-1]
    assert last.cfo == 1550 and last.fcf == 1550 - 100 - 200       # FY25 1500 + H1'26 750 - H1'25 700
    assert last.margin_pct == pytest.approx(100 * 1250 / last.revenue)


def test_windows_are_exactly_a_year_apart_so_they_do_not_overlap():
    wins = sec_ttm.ttm_windows(_company(), n_windows=5)
    assert all((b.end.year - a.end.year, b.end.month, b.end.day) == (1, a.end.month, a.end.day)
               for a, b in zip(wins, wins[1:]))


@pytest.mark.parametrize("quarter_first", [True, False])
def test_a_quarter_listed_beside_the_year_to_date_never_anchors_the_windows(quarter_first):
    """A 10-Q reports the quarter alone (Apr-Jun) next to the six months. Anchoring on the quarter computed FY + Q2 - prior
    Q2 and silently dropped Q1's change (NFLX, 10/6/26: TTM operating cash flow $9.5B instead of $12.0B). The result
    must not depend on which of the two rows the SEC listing puts last."""
    facts = _company()
    for node in facts["facts"]["us-gaap"].values():
        rows = node["units"]["USD"]
        quarters = [{"start": f"{y}-04-01", "end": f"{y}-06-30", "val": 7.0, "filed": "2026-08-01", "form": "10-Q"}
                    for y in range(2020, 2027)]
        node["units"]["USD"] = quarters + rows if quarter_first else rows + quarters
    wins = sec_ttm.ttm_windows(facts, n_windows=5)
    assert [w.end for w in wins] == [dt.date(y, 6, 30) for y in range(2022, 2027)]
    for w in wins:
        y = w.end.year
        assert (w.cfo, w.capex, w.sbc, w.revenue) == tuple(_expected(i, y) for i in ("cfo", "capex", "sbc", "revenue"))
    assert wins[-1].cfo == 1550


def test_newest_filing_a_10k_gives_plain_fiscal_year_windows():
    wins = sec_ttm.ttm_windows(_company(last_year=2025), n_windows=4)
    assert [w.end for w in wins] == [dt.date(y, 12, 31) for y in range(2022, 2026)]
    assert wins[-1].cfo == 1500 and wins[-1].revenue == 7500


def test_missing_line_item_is_an_error_not_a_silent_zero():
    facts = _company()
    del facts["facts"]["us-gaap"][sec_ttm.TAGS["capex"][0]]
    with pytest.raises(ValueError, match="capex"):
        sec_ttm.ttm_windows(facts)


def test_a_stale_fiscal_year_is_never_substituted_for_a_missing_one():
    """RL, 10/8/26: the newest 10-K had no capex fact, and the trailing capex was built from the PREVIOUS fiscal year plus the
    year-to-date change ($216M + $53M - $187M = $82M against about $296M). The newest year missing a line item must make
    the newest window incomplete -- an error -- not borrow an older year."""
    facts = _company()
    rows = facts["facts"]["us-gaap"][sec_ttm.TAGS["capex"][0]]["units"]["USD"]
    rows[:] = [r for r in rows if not (r["start"] == "2025-01-01" and r["end"] == "2025-12-31")]
    with pytest.raises(ValueError, match="capex"):
        sec_ttm.ttm_windows(facts, n_windows=5)


def test_too_little_history_is_an_error():
    with pytest.raises(ValueError, match="at least 4"):
        sec_ttm.ttm_windows(_company(), n_windows=3)


def test_add_back_lands_in_the_window_that_contains_the_date_and_only_that_one():
    base = sec_ttm.ttm_windows(_company(), n_windows=5)
    adj = sec_ttm.ttm_windows(_company(), n_windows=5, add_back={dt.date(2024, 5, 20): 400})
    assert [a.fcf - b.fcf for a, b in zip(adj, base)] == [0, 0, 400, 0, 0]
    assert adj[2].end == dt.date(2024, 6, 30) and adj[2].add_back == 400


def test_newest_filing_wins_and_the_first_tag_supplies_a_period():
    facts = _company()
    rows = facts["facts"]["us-gaap"][sec_ttm.TAGS["cfo"][0]]["units"]["USD"]
    rows.append({"start": "2025-01-01", "end": "2025-12-31", "val": 9999, "filed": "2026-09-01", "form": "10-K/A"})
    assert sec_ttm.period_values(facts, sec_ttm.TAGS["cfo"])[(dt.date(2025, 1, 1), dt.date(2025, 12, 31))] == 9999
    # a lower-priority revenue tag never overrides the first tag for a period it also reports
    facts["facts"]["us-gaap"]["SalesRevenueNet"] = {"units": {"USD": [
        {"start": "2025-01-01", "end": "2025-12-31", "val": 1, "filed": "2027-01-01", "form": "10-K"}]}}
    vals = sec_ttm.period_values(facts, sec_ttm.TAGS["revenue"])
    assert vals[(dt.date(2025, 1, 1), dt.date(2025, 12, 31))] == 7500


def test_tier1_drafts_are_the_projects_own_rubric_on_the_windows():
    wins = sec_ttm.ttm_windows(_company(), n_windows=5)
    res = sec_ttm.tier1_drafts("zzz", wins, market_cap=50_000.0)
    fcf = [w.fcf for w in wins]
    margins = [w.margin_pct for w in wins]
    want_y = draft_fcf_yield_trend(100 * fcf[-1] / 50_000.0, trend_direction(fcf))
    want_m = draft_fcf_margin_trend(margins[-1], trend_direction(margins))
    assert res.ticker == "ZZZ"
    assert res.drafts["fcf_yield_trend"].score == want_y.score
    assert res.drafts["fcf_margin_trend"].score == want_m.score
    assert set(res.drafts) == {"fcf_yield_trend", "revenue_growth_consistency", "fcf_margin_trend"}
    assert "TTM-anchored" in res.drafts["fcf_yield_trend"].rationale and "2026-06-30" in res.drafts["fcf_yield_trend"].rationale
    assert res.yield_pct == pytest.approx(100 * fcf[-1] / 50_000.0)


def test_a_one_time_add_back_is_recorded_in_the_notes():
    wins = sec_ttm.ttm_windows(_company(), n_windows=5, add_back={dt.date(2024, 5, 20): 400})
    res = sec_ttm.tier1_drafts("zzz", wins, market_cap=50_000.0)
    assert any("2024-06-30" in n and "add" in n for n in res.notes)


# --------------------------------------------------------------------------- #
# patchy old data, stale facts, the add-back registry, the holdings comparison
# --------------------------------------------------------------------------- #

def _drop(facts, item, start, end):
    """Remove one reported period of one line item (a patchy old filing)."""
    rows = facts["facts"]["us-gaap"][sec_ttm.TAGS[item][0]]["units"]["USD"]
    rows[:] = [f for f in rows if not (f["start"] == start and f["end"] == end)]


def test_an_old_window_with_a_missing_item_is_dropped_not_fatal():
    facts = _company()
    _drop(facts, "capex", "2021-01-01", "2021-06-30")           # the prior-year YTD the Jun-2022 window needs
    wins = sec_ttm.ttm_windows(facts, n_windows=5)
    assert [w.end for w in wins] == [dt.date(y, 6, 30) for y in range(2023, 2027)]


def test_a_gap_in_the_middle_ends_the_run_and_too_few_windows_is_an_error():
    facts = _company()
    _drop(facts, "capex", "2022-01-01", "2022-06-30")           # breaks Jun-2022 and Jun-2023
    with pytest.raises(ValueError, match="only 3 usable"):
        sec_ttm.ttm_windows(facts, n_windows=5)


def test_a_missing_item_in_the_newest_window_names_the_item():
    facts = _company()
    _drop(facts, "sbc", "2026-01-01", "2026-06-30")
    with pytest.raises(ValueError, match="sbc"):
        sec_ttm.ttm_windows(facts, n_windows=5)


def test_freshness_note_fires_only_when_edgar_is_a_quarter_ahead_of_the_facts():
    end = dt.date(2026, 3, 31)
    assert sec_ttm.freshness_note(end, None) is None
    assert sec_ttm.freshness_note(end, ("10-Q", dt.date(2026, 3, 31), dt.date(2026, 4, 29))) is None
    assert sec_ttm.freshness_note(end, ("10-Q", dt.date(2026, 4, 5), dt.date(2026, 5, 1))) is None       # 5 days: a calendar quirk
    note = sec_ttm.freshness_note(end, ("10-Q", dt.date(2026, 6, 30), dt.date(2026, 7, 29)))
    assert "2026-06-30" in note and "2026-03-31" in note and "quarter stale" in note


def test_add_back_registry_loads_by_ticker_and_a_missing_file_is_empty(tmp_path):
    p = tmp_path / "addbacks.json"
    p.write_text(json.dumps({"vrtx": [{"date": "2024-05-20", "usd": 4.4e9, "note": "x", "approved_by": "Alan"}]}))
    assert sec_ttm.load_add_backs(str(p)) == {"VRTX": {dt.date(2024, 5, 20): 4.4e9}}
    assert sec_ttm.load_add_backs(str(tmp_path / "none.json")) == {}


def test_the_live_registry_holds_vrtxs_alpine_payment():
    reg = sec_ttm.load_add_backs()
    assert reg["VRTX"] == {dt.date(2024, 5, 20): 4_400_000_000.0}


def _recorded(level=4, **over):
    base = {k: IndicatorScore(level, "M") for k in ALL_WEIGHTS}
    base.update({k: IndicatorScore(v, "M") for k, v in over.items()})
    return base


def _drafts(**scores):
    return {k: Draft(k, v, "M", "r") for k, v in scores.items()}


def test_compare_flags_a_rule_3_change_a_band_change_and_ignores_a_move_inside_a_band():
    trip = _recorded(fcf_yield_trend=2, fcf_margin_trend=2)                  # Rule 3: two Tier 1 at 2
    c = sec_ttm.compare_scores(trip, _drafts(fcf_yield_trend=3, revenue_growth_consistency=4, fcf_margin_trend=2))
    assert (c.rule3_recorded, c.rule3_latest) == ("AVOID", "OK") and c.diverges
    assert (c.decision_recorded, c.decision_latest) == ("AVOID", "BUY")
    assert c.recorded["fcf_yield_trend"] == 2 and c.latest["fcf_yield_trend"] == 3
    # band change: all fours composite 80.0 is STRONG BUY; two Tier 1 quant scores one point lower is 74.0, BUY
    c2 = sec_ttm.compare_scores(_recorded(), _drafts(fcf_yield_trend=3, fcf_margin_trend=3))
    assert (c2.composite_recorded, c2.composite_latest) == (pytest.approx(80.0), pytest.approx(74.0))
    assert c2.decision_recorded == "STRONG BUY" and c2.decision_latest == "BUY" and c2.diverges
    # moves a point, same band, same rules: differs but does not diverge (all threes is 60, WATCH LIST, either way)
    c3 = sec_ttm.compare_scores(_recorded(3), _drafts(revenue_growth_consistency=4))
    assert c3.moved and not c3.diverges and c3.decision_recorded == c3.decision_latest == "WATCH LIST"
    # unchanged
    c4 = sec_ttm.compare_scores(_recorded(), _drafts(fcf_yield_trend=4, revenue_growth_consistency=4, fcf_margin_trend=4))
    assert not c4.moved and not c4.diverges


def test_compare_refuses_an_incomplete_scoring_row():
    partial = _recorded()
    del partial["competitive_moat"]
    with pytest.raises(ValueError, match="competitive_moat"):
        sec_ttm.compare_scores(partial, {})


def _check(**over):
    kw = dict(ticker="ZZZ", recorded=_recorded(), scored="2026-09-13", industry="Software", n_windows=5, add_back=None,
              facts_fn=lambda t: _company(), newest_fn=lambda t: ("10-Q", dt.date(2026, 6, 30), dt.date(2026, 8, 1)),
              market_cap_fn=lambda t: 50_000.0)
    kw.update(over)
    return sec_ttm.check_holding(**kw)


def test_check_holding_reports_what_it_cannot_do_and_never_guesses():
    assert _check(industry="REIT - Industrial").status == "by-hand"
    assert "AFFO" in _check(industry="REIT - Industrial").reason
    foreign = _check(newest_fn=lambda t: None)
    assert foreign.status == "by-hand" and "20-F" in foreign.reason
    def boom(t):
        raise ValueError("SEC facts have no sbc")
    assert "no sbc" in _check(facts_fn=boom).reason
    assert _check(market_cap_fn=lambda t: None).status == "by-hand"


def test_check_holding_compares_and_warns_when_the_facts_are_a_quarter_behind():
    ok = _check()
    assert ok.comparison is not None and ok.windows == 5 and ok.ttm_end == dt.date(2026, 6, 30) and not ok.notes
    assert ok.status in ("same", "differs", "diverges")
    stale = _check(newest_fn=lambda t: ("10-Q", dt.date(2026, 9, 30), dt.date(2026, 11, 1)))
    assert any("quarter stale" in n for n in stale.notes)


def test_held_scored_rows_keeps_held_equities_that_have_a_scoring_row():
    positions = [SimpleNamespace(ticker="AAA", asset_class="Equity"), SimpleNamespace(ticker="BBB", asset_class="Cash"),
                 SimpleNamespace(ticker="ETF", asset_class="Equity")]
    rows = [SimpleNamespace(ticker="AAA"), SimpleNamespace(ticker="CCC"), SimpleNamespace(ticker="BBB")]
    assert [r.ticker for r in sec_ttm.held_scored_rows(positions, rows)] == ["AAA"]


def test_format_holdings_lists_the_review_names_and_the_by_hand_names():
    trip = _recorded(fcf_yield_trend=2, fcf_margin_trend=2)
    cmp = sec_ttm.compare_scores(trip, _drafts(fcf_yield_trend=3))
    checks = [sec_ttm.HoldingCheck("AAA", "diverges", "", "2026-09-13", dt.date(2026, 6, 30), 5, cmp, notes=["a note"]),
              sec_ttm.HoldingCheck("TSM", "by-hand", "no 10-Q or 10-K on EDGAR (a foreign filer: 20-F / 6-K) -- check by hand")]
    text = sec_ttm.format_holdings(checks)
    assert "<== REVIEW" in text and "AAA: AVOID -> BUY" in text and "Rule 3 AVOID -> OK" in text
    assert "BY HAND" in text and "TSM:" in text and "NOTES:" in text and "a note" in text


def test_the_command_line_wants_a_ticker_or_holdings_but_not_both_or_neither():
    for argv in ([], ["AAA", "--holdings"]):
        with pytest.raises(SystemExit):
            sec_ttm.main(argv)


def test_status_needs_both_window_counts_to_agree_before_it_calls_a_divergence():
    same = sec_ttm.compare_scores(_recorded(), _drafts(fcf_yield_trend=4))
    differs = sec_ttm.compare_scores(_recorded(3), _drafts(revenue_growth_consistency=4))
    diverges = sec_ttm.compare_scores(_recorded(), _drafts(fcf_yield_trend=3, fcf_margin_trend=3))     # 80 -> 74
    assert same.moved is False and differs.moved and not differs.diverges and diverges.diverges
    s = sec_ttm.status_of
    assert s(same, None) == "same" and s(differs, None) == "differs" and s(diverges, None) == "diverges"
    assert s(diverges, diverges) == "diverges" and s(differs, differs) == "differs"
    assert s(diverges, same) == "knife-edge" and s(diverges, differs) == "knife-edge" and s(differs, diverges) == "knife-edge"
    assert s(same, differs) == "differs"                                   # a move on either count is still worth a line


def test_check_holding_uses_the_newest_four_windows_as_the_second_opinion():
    res = _check()
    assert res.windows == 5 and res.alt is not None                        # the synthetic company has 5+ windows
    four = _check(n_windows=4)
    assert four.windows == 4 and four.alt is None


def test_format_holdings_has_a_knife_edge_section():
    trip = _recorded()
    cmp = sec_ttm.compare_scores(trip, _drafts(fcf_yield_trend=3, fcf_margin_trend=3))
    alt = sec_ttm.compare_scores(trip, _drafts())
    chk = sec_ttm.HoldingCheck("ANET", "knife-edge", "", "2026-09-13", dt.date(2026, 6, 30), 5, cmp, alt)
    text = sec_ttm.format_holdings([chk])
    assert "<== knife-edge" in text and "KNIFE-EDGE" in text and "newest 4 windows" in text
    assert "REVIEW --" not in text
