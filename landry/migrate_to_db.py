"""Phase A of LANDRY_DATABASE_DESIGN.md: one-time, read-only-of-the-workbook
migration into landry.db.

This is a correctness proof, not a cutover -- the CLI/webapp keep reading
the xlsx directly after this runs. Run it, then diff row counts against
the source tabs before trusting the schema for anything real (Phase B).

    python -m landry.migrate_to_db [workbook_path] [--db path]
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from landry import models, xlsx_io


def migrate(workbook_path: str, db_path: str = models.DEFAULT_DB_PATH,
           scores_json_path: Optional[str] = None, verbose: bool = True) -> dict:
    """Populate ``db_path`` from ``workbook_path`` (and, if given, the
    Part 12 approval JSON store). Returns a dict of table -> row count
    inserted, for the caller to verify against the source tabs."""
    conn = models.init_db(db_path)
    counts: dict = {}

    def log(msg):
        if verbose:
            print(msg)

    # Scoring -> tickers, classification, composite_history, scores(tier)
    scoring_rows = xlsx_io.read_scoring_tab(workbook_path)
    n_scores = 0
    for row in scoring_rows:
        models.ensure_ticker(conn, row.ticker, row.company)
        scored_date = str(row.date_scored) if row.date_scored else ""
        conn.execute(
            "INSERT INTO composite_history "
            "(ticker, scored_date, tier1_wtd_avg, composite, decision, "
            " rule1_flag, rule2_flag, rule3_flag, rule4_flag) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (row.ticker, scored_date, row.tier1_weighted_average,
             row.composite, row.decision, *row.rule_flags))
        if row.sector or row.industry:
            conn.execute(
                "INSERT INTO classification (ticker, sector, industry, as_of_date) "
                "VALUES (?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET "
                "sector=excluded.sector, industry=excluded.industry, "
                "as_of_date=excluded.as_of_date",
                (row.ticker, row.sector, row.industry, scored_date))
        for indicator, ind_score in row.scores.items():
            tier = _indicator_tier(indicator)
            conn.execute(
                "INSERT INTO scores (ticker, indicator, tier, score, "
                "confidence, evidence, scored_date) VALUES (?,?,?,?,?,?,?)",
                (row.ticker, indicator, tier, ind_score.score,
                 ind_score.confidence, ind_score.evidence, scored_date))
            n_scores += 1
    counts["tickers"] = len(scoring_rows)
    counts["composite_history"] = len(scoring_rows)
    counts["scores (from Scoring tab)"] = n_scores
    log(f"Scoring: {len(scoring_rows)} tickers, {n_scores} indicator scores")

    # Part 12 approved scores (landry_scores.json) -> scores, with
    # provenance. Additive to the Scoring-tab rows above (same table,
    # different scored_date if the approval predates/postdates the last
    # workbook snapshot -- both are real history).
    if scores_json_path:
        from landry.approvals import ScoreStore
        store = ScoreStore(scores_json_path)
        n_approved = 0
        for ticker in store.tickers():
            approved = (store._data["tickers"].get(ticker, {})
                       .get("approved", {}))
            for indicator, rec in approved.items():
                models.ensure_ticker(conn, ticker)
                tier = _indicator_tier(indicator)
                conn.execute(
                    "INSERT INTO scores (ticker, indicator, tier, score, "
                    "confidence, evidence, scored_date, approved_by, "
                    "approved_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (ticker, indicator, tier, rec["score"], rec["confidence"],
                     rec.get("rationale"), rec["approved_at"],
                     rec["approved_by"], rec["approved_at"]))
                n_approved += 1
        counts["scores (from landry_scores.json, approved)"] = n_approved
        log(f"Approvals: {n_approved} approved indicator scores")

    # Positions + tax-loss carryforward
    positions = xlsx_io.read_positions_full(workbook_path)
    as_of = _today()
    for p in positions:
        models.ensure_ticker(conn, p["ticker"])
        conn.execute(
            "INSERT INTO positions (account, ticker, description, "
            "asset_class, quantity, price, market_value, cost_basis, "
            "unrealized_gl, unrealized_gl_pct, pct_of_account, "
            "pct_of_combined, classification, as_of_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (p["account"], p["ticker"], p["description"], p["asset_class"],
             p["quantity"], p["price"], p["market_value"], p["cost_basis"],
             p["unrealized_gl"], p["unrealized_gl_pct"], p["pct_of_account"],
             p["pct_of_combined"], p["classification"], as_of))
    counts["positions"] = len(positions)
    log(f"Positions: {len(positions)} rows")

    carryforward = xlsx_io.read_tax_loss_carryforward(workbook_path)
    for c in carryforward:
        conn.execute(
            "INSERT INTO tax_loss_carryforward (term, amount, as_of_date) "
            "VALUES (?,?,?)", (c["term"], c["amount"], as_of))
    counts["tax_loss_carryforward"] = len(carryforward)

    # Market data
    market = xlsx_io.read_market_data(workbook_path)
    for m in market:
        models.ensure_ticker(conn, m["ticker"], m["company"])
        conn.execute(
            "INSERT INTO market_data (ticker, price, volume, market_cap_m, "
            "pe, wk52_low, wk52_high, dividend_yield, as_of_date) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (m["ticker"], m["price"], m["volume"], m["market_cap_m"], m["pe"],
             m["wk52_low"], m["wk52_high"], m["dividend_yield"], as_of))
    counts["market_data"] = len(market)
    log(f"Market Data: {len(market)} rows")

    # Price history
    prices = xlsx_io.read_price_history(workbook_path)
    for row in prices:
        models.ensure_ticker(conn, row["ticker"])
        conn.execute(
            "INSERT OR REPLACE INTO price_history (ticker, week_ending, close) "
            "VALUES (?,?,?)",
            (row["ticker"], str(row["week_ending"]), row["close"]))
    counts["price_history"] = len(prices)
    log(f"Price History: {len(prices)} rows")

    # Monitor notes, watchlist, performance, holding monitor,
    # implied-return scenarios, entry checklist, journal, drawdown log
    monitor = xlsx_io.read_monitor_notes(workbook_path)
    for m in monitor:
        models.ensure_ticker(conn, m["ticker"])
        conn.execute(
            "INSERT INTO monitor_notes (ticker, category, insider_flag, "
            "insider_note, analyst_shift_flag, recheck_status, notes, "
            "updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(ticker) DO "
            "UPDATE SET category=excluded.category, "
            "insider_flag=excluded.insider_flag, "
            "insider_note=excluded.insider_note, "
            "analyst_shift_flag=excluded.analyst_shift_flag, "
            "recheck_status=excluded.recheck_status, notes=excluded.notes, "
            "updated_at=excluded.updated_at",
            (m["ticker"], m["category"], m["insider_flag"], m["insider_note"],
             m["analyst_shift_flag"], m["recheck_status"], m["notes"], as_of))
    counts["monitor_notes"] = len(monitor)

    watchlist = xlsx_io.read_watchlist(workbook_path)
    for w in watchlist:
        models.ensure_ticker(conn, w["ticker"])
        conn.execute(
            "INSERT INTO watchlist (ticker, status, entry_date, entry_score, "
            "remediation_plan_yn, deadline_90day, action_status, notes) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (w["ticker"], w["status"], str(w["entry_date"] or ""),
             w["entry_score"], w["remediation_plan_yn"],
             str(w["deadline_90day"] or ""), w["action_status"], w["notes"]))
    counts["watchlist"] = len(watchlist)

    perf = xlsx_io.read_performance_tracking(workbook_path)
    for p in perf:
        models.ensure_ticker(conn, p["ticker"])
        conn.execute(
            "INSERT INTO performance_cohort (ticker, entry_date, entry_price, "
            "entry_score, entry_confidence, entry_band, spy_at_entry, status, "
            "exit_date, exit_price, exit_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (p["ticker"], str(p["entry_date"] or ""), p["entry_price"],
             p["entry_score"], p["entry_confidence"], p["entry_band"],
             p["spy_at_entry"], p["status"], str(p["exit_date"] or ""),
             p["exit_price"], p["exit_reason"]))
    counts["performance_cohort"] = len(perf)

    holding = xlsx_io.read_holding_monitor(workbook_path)
    n_hm_ind = 0
    for h in holding:
        models.ensure_ticker(conn, h["ticker"], h["company"])
        cur = conn.execute(
            "INSERT INTO holding_monitor (ticker, as_of_date, position_pct, "
            "max_full_pct, debt_fcf, p_fcf, fcf_growth, implied_return, "
            "current_tier, prior_tier, valuation_flags, hold_through_yn, "
            "action_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (h["ticker"], as_of, h["position_pct"], h["max_full_pct"],
             h["debt_fcf"], h["p_fcf"], h["fcf_growth"], h["implied_return"],
             h["current_tier"], h["prior_tier"], h["valuation_flags"],
             h["hold_through_yn"], h["action_status"]))
        hm_id = cur.lastrowid
        for ind in h["indicators"]:
            conn.execute(
                "INSERT INTO holding_monitor_indicator (holding_monitor_id, "
                "indicator_name, prior_value, current_value, flag) "
                "VALUES (?,?,?,?,?)",
                (hm_id, ind["indicator_name"], ind["prior_value"],
                 ind["current_value"], ind["flag"]))
            n_hm_ind += 1
    counts["holding_monitor"] = len(holding)
    counts["holding_monitor_indicator"] = n_hm_ind

    scenarios = xlsx_io.read_implied_return_scenarios(workbook_path)
    for s in scenarios:
        models.ensure_ticker(conn, s["ticker"], s["company"])
        conn.execute(
            "INSERT INTO implied_return_scenario (ticker, scenario, fcf_yr5, "
            "terminal_mult, distributions, implied_return, tag, "
            "computed_date) VALUES (?,?,?,?,?,?,?,?)",
            (s["ticker"], s["scenario"], s["fcf_yr5"], s["terminal_mult"],
             s["distributions"], s["implied_return"], s["tag"], as_of))
    counts["implied_return_scenario"] = len(scenarios)

    entries = xlsx_io.read_entry_checklist(workbook_path)
    for e in entries:
        models.ensure_ticker(conn, e["ticker"], e["company"])
        conn.execute(
            "INSERT INTO entry_checklist (ticker, computed_date, "
            "rule5_composite, rule6_tier1, rule7_no_tier1_eq1, rule8_200wk, "
            "rule9_macd, staging_result, rule10_binary_risk, "
            "risk_in_thesis_yn, rule10_result, rule11_bear_case, "
            "rule12_scenario_tags, p_fcf_current, "
            "consensus_fcf_growth_2yr, rule13_valuation_ceiling, "
            "entry_authorized, recommended_action) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e["ticker"], as_of, e["rule5_composite"], e["rule6_tier1"],
             e["rule7_no_tier1_eq1"], e["rule8_200wk"], e["rule9_macd"],
             e["staging_result"], e["rule10_binary_risk"],
             e["risk_in_thesis_yn"], e["rule10_result"], e["rule11_bear_case"],
             e["rule12_scenario_tags"], e["p_fcf_current"],
             e["consensus_fcf_growth_2yr"], e["rule13_valuation_ceiling"],
             e["entry_authorized"], e["recommended_action"]))
    counts["entry_checklist"] = len(entries)

    journal = xlsx_io.read_journal(workbook_path)
    for j in journal:
        if j["ticker"]:
            models.ensure_ticker(conn, j["ticker"])
        conn.execute(
            "INSERT INTO journal (date, ticker, notes) VALUES (?,?,?)",
            (str(j["date"]), j["ticker"], j["notes"]))
    counts["journal"] = len(journal)
    log(f"Journal: {len(journal)} rows")

    drawdown = xlsx_io.read_drawdown_log_full(workbook_path)
    for d in drawdown:
        conn.execute(
            "INSERT OR REPLACE INTO drawdown_log (date, portfolio_value, "
            "running_peak, drawdown_pct, status, cash_floor, "
            "new_position_rule, notes) VALUES (?,?,?,?,?,?,?,?)",
            (str(d["date"]), d["portfolio_value"], d["running_peak"],
             d["drawdown_pct"], d["status"], d["cash_floor"],
             d["new_position_rule"], d["notes"]))
    counts["drawdown_log"] = len(drawdown)

    conn.commit()
    conn.close()
    return counts


def _indicator_tier(indicator: str) -> int:
    from landry.scoring import INDICATORS
    entry = INDICATORS.get(indicator)
    return entry[0] if entry else 0


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def main():
    ap = argparse.ArgumentParser(description="Migrate the Landry workbook "
                                             "into landry.db (Phase A)")
    ap.add_argument("workbook", nargs="?", help="path to the xlsx "
                    "(default: latest LANDRY_SYSTEM_WORKBOOK_*.xlsx)")
    ap.add_argument("--db", default=models.DEFAULT_DB_PATH)
    ap.add_argument("--scores-json", default=None,
                    help="path to landry_scores.json (default: repo root, "
                         "if present)")
    args = ap.parse_args()

    import os
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    workbook = args.workbook or xlsx_io.latest_workbook(repo_root)
    if not workbook:
        print("No workbook found.", file=sys.stderr)
        return 1
    scores_json = args.scores_json
    if scores_json is None:
        default_json = os.path.join(repo_root, "landry_scores.json")
        scores_json = default_json if os.path.exists(default_json) else None

    print(f"Migrating {workbook} -> {args.db}")
    if scores_json:
        print(f"  + approvals from {scores_json}")
    counts = migrate(workbook, args.db, scores_json)

    print("\nRow counts:")
    for table, n in counts.items():
        print(f"  {table:42s}{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
