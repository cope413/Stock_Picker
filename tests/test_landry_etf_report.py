"""landry.etf_report (10/8/26): the pure computations on synthetic data whose answers are known, the reader against the live
workbook, and the whole report assembled from injected prices / facts / factors (no network)."""

import os

import numpy as np
import pandas as pd
import pytest

from landry import etf_report as er
from landry import xlsx_io

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKBOOK = xlsx_io.latest_workbook(REPO)


# ----------------------------------------------------------------------------------------------- helpers --

def _factors(n=72, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-08-31", periods=n, freq="ME")
    f = pd.DataFrame(rng.normal(0, 0.03, (n, 6)), index=idx, columns=["MKT", "SMB", "HML", "RMW", "CMA", "MOM"])
    f["RF"] = 0.002
    return f


def _prices(tickers, years=6, seed=2, drift=0.0004, vol=0.01):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end="2026-10-07", periods=int(years * 252))
    mkt = rng.normal(drift, vol, len(idx))
    cols = {}
    for k, t in enumerate(tickers):
        idio = rng.normal(0, vol * 0.6, len(idx))
        beta = 0.4 + 0.15 * (k % 8)
        cols[t] = 100 * np.cumprod(1 + beta * mkt + idio)
    cols["SPY"] = 100 * np.cumprod(1 + mkt)
    return pd.DataFrame(cols, index=idx)


# --------------------------------------------------------------------------------------------- the maths --

def test_factor_loadings_recover_known_exposures_and_refuse_short_histories():
    f = _factors()
    rng = np.random.default_rng(5)
    y = f["RF"] + 0.001 + 1.0 * f["MKT"] + 0.5 * f["SMB"] - 0.3 * f["HML"] + rng.normal(0, 0.002, len(f))
    res = er.factor_loadings(y, f)
    assert res["n"] == 60
    assert res["MKT"] == pytest.approx(1.0, abs=0.05) and res["SMB"] == pytest.approx(0.5, abs=0.05) and res["HML"] == pytest.approx(-0.3, abs=0.05)
    assert abs(res["RMW"]) < 0.1 and abs(res["MOM"]) < 0.1
    assert res["MKT_t"] > 20 and res["r2"] > 0.95
    assert res["alpha"] == pytest.approx(0.012, abs=0.006)                      # 0.1% a month, annualised
    assert er.factor_loadings(y.iloc[:20], f) is None


def test_average_linkage_groups_find_two_blocks_and_leave_the_loner_out():
    names = list("ABCDEFG")
    m = np.full((7, 7), 0.1)
    for blk in ("ABC", "DE"):
        for a in blk:
            for b in blk:
                m[names.index(a), names.index(b)] = 0.85
    np.fill_diagonal(m, 1.0)
    c = pd.DataFrame(m, index=names, columns=names)
    groups = er.average_linkage_groups(c, 0.70)
    assert sorted(sorted(g) for g in groups) == [["A", "B", "C"], ["D", "E"]]
    assert er.average_linkage_groups(c, 0.95) == []


def test_group_summary_flags_a_group_over_the_cluster_cap():
    c = pd.DataFrame([[1, .9, .9], [.9, 1, .9], [.9, .9, 1]], index=list("ABC"), columns=list("ABC"))
    w = pd.Series({"A": 0.12, "B": 0.11, "C": 0.05})
    rs = pd.Series({"A": 0.2, "B": 0.2, "C": 0.1})
    g = er.group_summary([["A", "B", "C"]], c, w, rs)[0]
    assert g["weight"] == pytest.approx(0.28) and g["over_cap"] and g["avg_corr"] == pytest.approx(0.9) and g["var_share"] == pytest.approx(0.5)
    assert g["members"] == ["A", "B", "C"]                                        # largest weight first


def test_risk_stats_beta_and_down_capture_of_a_half_beta_fund():
    px = _prices(["F"], years=4)
    r_spy = px["SPY"].pct_change()
    half = (1 + 0.5 * r_spy.fillna(0)).cumprod() * 100
    px["HALF"] = half
    rets = er.weekly_returns(px, 156)
    st = er.risk_stats(px, rets, ["HALF", "SPY"], "SPY")
    assert float(st.loc["HALF", "beta"]) == pytest.approx(0.5, abs=0.03)
    assert float(st.loc["HALF", "down_capture"]) == pytest.approx(0.5, abs=0.05)
    assert float(st.loc["SPY", "beta"]) == pytest.approx(1.0, abs=1e-9)
    assert float(st.loc["HALF", "max_dd"]) > float(st.loc["SPY", "max_dd"])      # a smaller drawdown (less negative)


def test_risk_contributions_sum_to_one_a_zero_weight_adds_nothing_and_independent_bets_count_assets():
    rng = np.random.default_rng(3)
    idx = pd.date_range("2023-01-06", periods=156, freq="W-FRI")
    R = pd.DataFrame(rng.normal(0, 0.02, (156, 5)), index=idx, columns=list("ABCDE"))
    R["Z"] = rng.normal(0, 0.02, 156)
    w = pd.Series({"A": 0.3, "B": 0.2, "C": 0.2, "D": 0.1, "E": 0.1, "Z": 0.0})
    tab, summ = er.risk_contributions(w, R)
    assert tab["risk_share"].sum() == pytest.approx(1.0)
    assert tab.loc["Z", "risk_share"] == pytest.approx(0.0, abs=1e-12)
    assert 5.0 < summ["bets"] <= 6.0                                              # six independent series: close to six
    bench = R["A"]
    _, s2 = er.risk_contributions(pd.Series({"A": 1.0}), R, bench)
    assert s2["beta"] == pytest.approx(1.0)


def test_sector_lookthrough_adds_direct_and_etf_dollars_against_the_benchmark():
    direct = {"AAA": 60.0, "BBB": 40.0}
    sect = {"AAA": "Technology", "BBB": "Healthcare"}
    etf = {"E1": 100.0}
    weights = {"E1": {"technology": 0.5, "energy": 0.5}}
    tab = er.sector_lookthrough(direct, sect, etf, weights, {"technology": 0.4, "healthcare": 0.1, "energy": 0.05}, total=250.0)
    assert tab.loc["Technology", "direct"] == pytest.approx(0.3) and tab.loc["Technology", "etf"] == pytest.approx(0.25)
    assert tab.loc["Technology", "total"] == pytest.approx(0.55) and tab.loc["Technology", "active"] == pytest.approx(0.15)
    assert tab.loc["Energy", "total"] == pytest.approx(0.25) and tab.loc["Healthcare", "of_portfolio"] == pytest.approx(0.16)
    assert tab["total"].sum() == pytest.approx(1.0)


def test_overlap_adds_the_etf_share_to_the_direct_position_and_maps_the_second_google_class():
    ov = er.holding_overlap({"NVDA": 1000.0}, {"E1": 2000.0, "E2": 500.0},
                            {"E1": {"NVDA": 0.10, "GOOG": 0.05}, "E2": {"NVDA": 0.20, "GOOGL": 0.04}})
    nv = ov[ov["name"] == "NVDA"].iloc[0]
    assert nv["direct"] == 1000.0 and nv["etf"] == pytest.approx(300.0) and nv["total"] == pytest.approx(1300.0)
    assert nv["via"] == {"E1": pytest.approx(200.0), "E2": pytest.approx(100.0)}
    gg = ov[ov["name"] == "GOOGL"].iloc[0]
    assert gg["etf"] == pytest.approx(100.0 + 20.0)
    assert not (ov["name"] == "GOOG").any()
    sem = er.semiconductor_exposure({"NVDA": 1000.0, "ADBE": 500.0}, {"E1": 2000.0}, {"E1": {"NVDA": 0.10, "MU": 0.05, "AAPL": 0.2}}, 5000.0)
    assert sem["direct"] == 1000.0 and sem["etf"] == pytest.approx(300.0) and sem["total_pct"] == pytest.approx(0.26)


def test_a_candidate_uncorrelated_with_the_book_cuts_volatility_and_a_clone_does_not():
    rng = np.random.default_rng(9)
    idx = pd.date_range("2023-01-06", periods=156, freq="W-FRI")
    R = pd.DataFrame(rng.normal(0.002, 0.02, (156, 4)), index=idx, columns=list("ABCD"))
    w = pd.Series({"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.20})                    # 5% in cash
    pc = (R * w).sum(axis=1)
    cands = pd.DataFrame({"IND": rng.normal(0.001, 0.02, 156), "CLONE": pc}, index=idx)
    out = er.candidate_effects(w, R, cands, R["A"], shift=0.07)
    assert out.loc["IND", "change"] < -0.003                                       # volatility falls
    assert abs(out.loc["CLONE", "change"]) < abs(out.loc["IND", "change"])
    assert out.loc["CLONE", "corr"] == pytest.approx(1.0)


# --------------------------------------------------------------------------------------- the live workbook --

def test_read_sleeve_takes_the_ledgers_held_etfs_and_ties_to_the_portfolio_total():
    s = er.read_sleeve(WORKBOOK)
    held = {r["ticker"] for r in xlsx_io.read_performance_tracking(WORKBOOK) if r.get("type") == "ETF" and r.get("status") == "Held"}
    assert set(s.etf) == held and held                                               # the ledger decides what is an ETF
    assert not (set(s.direct) & held)                                                # a held ETF is never also a direct stock
    assert s.total == pytest.approx(xlsx_io.total_portfolio_value(xlsx_io.read_positions(WORKBOOK)))
    assert s.etf_total + s.direct_total + s.other == pytest.approx(s.total)
    assert s.other >= 0 and 0.2 < s.etf_total / s.total < 0.8
    assert abs(s.weights().sum() - (s.etf_total + s.direct_total) / s.total) < 1e-9
    for acct, rows in s.accounts.items():
        assert acct and all(v[0] > 0 for v in rows.values())


# ----------------------------------------------------------------------------------------- the whole report --

def _inject():
    s = er.read_sleeve(WORKBOOK)
    tickers = list(s.etf) + list(s.direct) + list(er.CANDIDATES)
    px = _prices(tickers)
    secs = list(er.SECTOR_KEYS.values())
    facts = {t: {"name": f"{t} fund", "category": "Large Blend", "expense_pct": 0.3, "yield_pct": 2.0,
                 "sector_weights": {k: 1.0 / len(secs) for k in secs}, "top_holdings": {"NVDA": 0.05, "MU": 0.04, "AAPL": 0.06}} for t in s.etf}
    facts["SPY"] = {"sector_weights": {k: 1.0 / len(secs) for k in secs}}
    sector = {t: "Technology" for t in s.direct}
    return s, px, facts, sector


def test_build_report_from_injected_data_has_every_section_and_a_printable_docx(tmp_path):
    s, px, facts, sector = _inject()
    rep = er.build_report(WORKBOOK, prices=px, facts=facts, factors=_factors(), direct_sector=sector)
    assert set(rep.holdings.index) == set(s.etf)
    assert rep.risk["etf_weight"] == pytest.approx(s.etf_total / s.total)
    assert rep.risk["direct_var"] + rep.risk["etf_var"] == pytest.approx(1.0)
    assert not rep.sectors.empty and not rep.overlap.empty and not rep.candidates.empty and rep.factors is not None
    txt = er.to_text(rep)
    for head in ("THE SLEEVE", "GROUPS THAT ARE REALLY ONE BET", "FACTOR LOADINGS", "SECTOR LOOK-THROUGH", "NAMES HELD DIRECTLY AND THROUGH",
                 "WHAT IS MISSING", "RULES THE SLEEVE TOUCHES", "OPEN DESIGN QUESTIONS", "METHOD AND LIMITS"):
        assert head in txt
    assert any("Part 5 cash band" in r for r in rep.rules) and any("Rule 16" in r for r in rep.rules)
    pytest.importorskip("docx")
    out = er.write_docx(rep, str(tmp_path / "etf.docx"))
    assert os.path.getsize(out) > 8000


def test_build_report_without_factors_or_provider_facts_still_runs_and_says_what_it_left_out():
    s, px, facts, sector = _inject()
    rep = er.build_report(WORKBOOK, prices=px, facts={t: {} for t in list(s.etf) + ["SPY"]}, use_factors=False, direct_sector=sector)
    assert rep.factors is None and rep.sectors.empty and rep.overlap.empty
    notes = " ".join(rep.notes)
    assert "look-through left out" in notes and "overlap left out" in notes
    assert "FACTOR LOADINGS" not in er.to_text(rep)


def test_an_etf_with_no_price_history_is_named_and_left_out_of_the_risk_figures():
    s, px, facts, sector = _inject()
    gone = sorted(s.etf)[0]
    rep = er.build_report(WORKBOOK, prices=px.drop(columns=[gone]), facts=facts, use_factors=False, direct_sector=sector)
    assert any(gone in n and "no price history" in n for n in rep.notes)
    assert gone not in rep.corr.columns


def test_the_command_line_lists_its_options():
    from landry import cli
    with pytest.raises(SystemExit) as e:
        cli.main(["etf", "--help"])
    assert e.value.code == 0
