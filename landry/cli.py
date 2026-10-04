"""Landry System CLI.

    python -m landry score NVDA          # one ticker, from the workbook
    python -m landry score --all         # every scored candidate
    python -m landry score NVDA --store  # from approved scores (landry_scores.json)
    python -m landry refresh             # all objective inputs, holdings
    python -m landry refresh --tickers NVDA TSM --no-fundamentals
    python -m landry draft NVDA --evidence nvda_evidence.json
    python -m landry pending [NVDA]      # drafts awaiting approval
    python -m landry approve NVDA competitive_moat --by "Taylor" [--score 4]
    python -m landry reject NVDA competitive_moat --by "Taylor" --reason "..."
    python -m landry daily               # today's action items (Rules 30-44)
    python -m landry export              # fill a copy of the Excel workbook
    python -m landry import --by "Taylor"  # seed score store from workbook
    python -m landry doctor              # check this machine is ready to edit the workbook
    python -m landry audit               # check the workbook itself for structural drift
    python -m landry journal add --label DCA-CATCHUP-2 --notes-file -   # append a Journal entry (notes on stdin)
    python -m landry journal edit --row 62 --notes "SUPERSEDED ..."     # change an entry by its sheet row
    python -m landry drawdown add --date 2026-10-31 --value 780000      # log a portfolio value
    python -m landry prices status       # Price History health: duplicates, holdings with no column, weeks behind
    python -m landry prices append       # add the completed Friday(s); extends Returns (Calc), footer, charts
    python -m landry prices rebuild      # quarterly: rewrite every close from one adjusted pull
    python -m landry prices add VYM      # give a new holding a column (Returns + Correlation Matrix follow)
    python -m landry weekly              # Friday-close routine: prices append + Market Data/earnings refresh + one recalc + audit
    python -m landry market              # just Market Data + earnings dates, refreshed in place from yfinance
    python -m landry db status          # does the database agree with the generated tabs?
    python -m landry db pull|regenerate  # resolve disagreement: workbook wins | database wins

`score` reads analyst scores from the companion workbook (default: the
highest-numbered LANDRY_SYSTEM_WORKBOOK_<N>.xlsx beside the repo, currently
LANDRY_SYSTEM_WORKBOOK_25.xlsx), recomputes the
composite and Hard Rules 1-4, and flags any disagreement with the
workbook's own calculated cells.

`refresh` recomputes every automatable input (Part 12 boundary):
technicals, relative strength, beta, Rule 36 correlations, macro
overlay, fundamentals + quantitative rubric drafts — and writes
landry_snapshot.json. Tickers default to the workbook's equity holdings.

`draft` / `pending` / `approve` / `reject` run the Phase 3 human-in-the-
loop scoring workflow against landry_scores.json: `draft` proposes
quantitative rubric drafts (and, with an evidence file + API key, AI
drafts for the judgment indicators); nothing reaches a composite until
`approve` records who approved it and when.

`journal`, `drawdown` and `db` are the Phase C write path (LANDRY_DATABASE_DESIGN.md):
an entry goes into landry.db and the Journal / Portfolio Drawdown Log tab is regenerated
from it, atomically, instead of an ad hoc openpyxl edit. Every write refuses if Excel has
the workbook open or if the database and the tab disagree; `db pull` / `db regenerate`
resolve a disagreement in either direction. Run `audit` before committing the result.

`audit` checks the workbook itself for structural drift rather than the
environment `doctor` checks: Table refs that have fallen behind their
live data, hardcoded row bounds in xlsx_io.py readers that have gone
stale, cross-tab formula references pointing at now-blank columns or
truncated ranges, page setup/footer/gridlines that a sheet rebuild
silently dropped relative to the last commit, and Schema Reference
entries that no longer match the tabs they document. Run it after any
structural edit and before any commit that touches the workbook.
"""

from __future__ import annotations

import argparse
import os
import sys

from landry.scoring import score_stock
from landry.xlsx_io import latest_workbook, read_scoring_tab

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)


def _default_workbook() -> str:
    wb = latest_workbook(_REPO)
    if wb is None:
        sys.exit("no LANDRY_SYSTEM_WORKBOOK_<N>.xlsx found; pass --workbook")
    return wb


def _print_card(row, card) -> None:
    print(f"\n{card.ticker} — {row.company}")
    print(f"  Tier 1 weighted avg : {card.tier1_weighted_average:.2f} "
          f"(floor 3.0 -> {card.flags.rule1})")
    if card.composite is not None:
        print(f"  Tier contributions  : T1 {card.tier1_contribution:.2f} | "
              f"T2 {card.tier2_contribution:.2f} | "
              f"T3 {card.tier3_contribution:.2f}")
        print(f"  Composite Score     : {card.composite:.1f}")
        print(f"  Decision            : {card.decision}")
    else:
        print(f"  Composite Score     : — (Tier 1 gate failed)")
        print(f"  Decision            : {card.decision or 'REJECTED (Rule 1)'}")
    print(f"  Rules 1-4           : {card.flags.rule1} / {card.flags.rule2} / "
          f"{card.flags.rule3} / {card.flags.rule4}")
    for n in card.notes:
        print(f"  ! {n}")
    # cross-check against the workbook's own calculated cells
    if row.composite is not None and card.composite is not None:
        if abs(row.composite - card.composite) > 1e-6:
            print(f"  *** MISMATCH: workbook composite {row.composite} != "
                  f"engine {card.composite}")
    if row.decision and card.decision and row.decision != card.decision:
        print(f"  *** MISMATCH: workbook decision {row.decision} != "
              f"engine {card.decision}")


SCORES_FILE = "landry_scores.json"


def _store():
    from landry.approvals import ScoreStore
    return ScoreStore(os.path.join(_REPO, SCORES_FILE))


def _cmd_draft(args) -> int:
    """Propose quantitative drafts (always) and AI judgment drafts (when an
    evidence file and API key are available) into the score store."""
    import json

    from landry.fundamentals import (YFinanceFundamentals, compute_metrics,
                                     draft_quant_scores)

    ticker = args.ticker.upper()
    store = _store()

    metrics = None
    fund_inputs = None
    try:
        fund_inputs = YFinanceFundamentals().get(ticker)
        metrics = compute_metrics(fund_inputs)
        for name, d in draft_quant_scores(metrics).items():
            store.propose(ticker, d, source="quant_draft")
            print(f"proposed quant draft {name}: {d.score} ({d.confidence}) — "
                  f"{d.rationale}")
        for w in metrics.warnings:
            print(f"note: {w}")
    except Exception as e:
        print(f"! quantitative drafts unavailable: {e}")

    try:
        from landry.data_auto import (draft_relative_strength,
                                      draft_technical_trend,
                                      draft_volume_accumulation, fetch_daily,
                                      relative_strength, technical_state,
                                      weekly_beta, weekly_closes)
        daily = fetch_daily([ticker, "SPY"])
        df = daily.get(ticker)
        if df is None:
            raise RuntimeError(f"no price data for {ticker}")
        tech = technical_state(df)
        for d in (draft_technical_trend(tech), draft_volume_accumulation(tech)):
            if d is not None:
                store.propose(ticker, d, source="quant_draft")
                print(f"proposed quant draft {d.indicator}: {d.score} "
                      f"({d.confidence}) — {d.rationale}")
        spy_df = daily.get("SPY")
        beta = None
        if spy_df is not None:
            closes = weekly_closes(daily)
            rs = relative_strength(closes[ticker], closes["SPY"])
            d = draft_relative_strength(rs)
            if d is not None:
                store.propose(ticker, d, source="quant_draft")
                print(f"proposed quant draft {d.indicator}: {d.score} "
                      f"({d.confidence}) — {d.rationale}")
            beta = weekly_beta(closes[ticker], closes["SPY"]).beta

        if fund_inputs is not None and metrics is not None and beta is not None:
            from landry.fundamentals import (compute_wacc, draft_roic_vs_wacc,
                                             live_risk_free_rate)
            rf = live_risk_free_rate()
            wacc = compute_wacc(fund_inputs, beta, risk_free_pct=rf)
            series = metrics.roic_pct_series
            persistently_below = (
                len(series) >= 2 and wacc.wacc_pct is not None
                and all(r < wacc.wacc_pct for r in series[-2:]))
            d = draft_roic_vs_wacc(metrics.roic_pct, wacc.wacc_pct,
                                   persistently_below=persistently_below)
            if d is not None:
                d.rationale += (f" [WACC assumptions: Rf={wacc.risk_free_pct:.1f}% "
                                f"({wacc.risk_free_source}), "
                                f"ERP={wacc.equity_risk_premium_pct:.1f}%]")
                store.propose(ticker, d, source="quant_draft")
                print(f"proposed quant draft {d.indicator}: {d.score} "
                      f"({d.confidence}) — {d.rationale}")
    except Exception as e:
        print(f"! technical/relative-strength/roic-wacc drafts unavailable: {e}")

    try:
        from landry.data_auto import (correlation_vs_holdings, fetch_daily,
                                      weekly_closes)
        from landry.xlsx_io import equity_weights, read_positions
        holdings = sorted(equity_weights(read_positions(_default_workbook())))
        if not holdings:
            print("(no current holdings to check correlation against)")
        else:
            daily = fetch_daily([ticker] + holdings)
            closes = weekly_closes(daily)
            cc = correlation_vs_holdings(ticker, closes)
            if cc is None or not cc.correlations:
                print("! correlation-vs-holdings: insufficient overlapping price history")
            else:
                flag = " -- FLAGGED (Rule 36, >0.70)" if cc.flagged else ""
                print(f"correlation vs holdings ({cc.window_weeks}wk window): "
                     f"max {cc.max_correlation:+.2f} vs {cc.max_correlation_holding}{flag}")
    except Exception as e:
        print(f"! correlation-vs-holdings check unavailable: {e}")

    if args.evidence:
        from landry.ai_analyst import ClaudeAnalyst, EvidencePack
        pack = EvidencePack(ticker, fundamentals=metrics)
        with open(args.evidence) as f:
            for e in json.load(f):
                pack.add_evidence(e["text"], int(e["source_tier"]),
                                  e.get("source", "unspecified"),
                                  e.get("date", ""))
        try:
            result = ClaudeAnalyst(model=args.model).draft_judgment(pack)
        except RuntimeError as e:
            print(f"! AI drafts unavailable: {e}")
            return 1
        for name, d in result.drafts.items():
            store.propose(ticker, d)
            print(f"proposed AI draft {name}: {d.draft.score} "
                  f"({d.draft.confidence}) — {d.draft.rationale}")
        if result.structural_deterioration is not None:
            sd = result.structural_deterioration
            print(f"structural deterioration flag: {sd.value} — {sd.rationale}")
    else:
        print("(no --evidence file: judgment indicators not drafted — "
              "the Part 12 boundary requires evidence, not guesses)")
    print(f"\nreview with: python -m landry pending {ticker}")
    return 0


def _cmd_pending(args) -> int:
    store = _store()
    data = store.pending(args.ticker.upper()) if args.ticker else store.pending()
    if not data:
        print("nothing pending")
        return 0
    if args.ticker:
        data = {args.ticker.upper(): data}
    for t, drafts in data.items():
        print(f"\n{t}:")
        for name, d in drafts.items():
            print(f"  {name}: {d.get('score')} ({d.get('confidence')}) "
                  f"[{d.get('source')}] — {d.get('rationale', '')[:100]}")
    return 0


def _cmd_approve(args) -> int:
    ap = _store().approve(args.ticker.upper(), args.indicator,
                          approved_by=args.by, score=args.score,
                          confidence=args.conf)
    print(f"approved {args.ticker.upper()} {args.indicator}: "
          f"{ap.score} ({ap.confidence}) by {ap.approved_by} at {ap.approved_at}")
    return 0


def _cmd_reject(args) -> int:
    _store().reject(args.ticker.upper(), args.indicator,
                    approved_by=args.by, reason=args.reason)
    print(f"rejected {args.ticker.upper()} {args.indicator}: {args.reason}")
    return 0


def _cmd_daily(args) -> int:
    from landry.daily import print_actions, run_daily, write_actions
    wb = args.workbook or _default_workbook()
    if args.refresh:
        _cmd_refresh(args)
    actions = run_daily(wb, _REPO, store=_store())
    print_actions(actions)
    path = write_actions(actions, _REPO)
    print(f"\naction items written: {os.path.basename(path)}")
    return 0


def _cmd_export(args) -> int:
    from landry.export import export_workbook
    wb = args.workbook or _default_workbook()
    kwargs = {}
    # Market Data is NOT filled here any more (retired 2026-10-04): the only source was
    # landry_snapshot.json -- whatever the last `landry refresh` left, five days stale on 10/4 --
    # written into a copy. `landry market` / `landry weekly` refresh the live tab from yfinance.
    if args.scores:
        store = _store()
        approved = {t: store.approved_scores(t) for t in store.tickers()
                    if store.approved_scores(t)}
        if approved:
            import datetime as dt
            kwargs["approved_scores"] = approved
            kwargs["scored_date"] = dt.date.today()
    if args.drawdown:
        sys.exit("export --drawdown is retired: it wrote static values from drawdown.py's "
                 "regime logic over the Drawdown Log's formulas, and only the first 40 "
                 "rows. Log a value with `landry drawdown add --date YYYY-MM-DD --value N` "
                 "instead (landry/ledger.py).")
    out = export_workbook(wb, out_path=args.out, **kwargs)
    print(f"filled workbook written: {out}")
    return 0


def _cmd_import(args) -> int:
    from landry.export import import_scores
    wb = args.workbook or _default_workbook()
    counts = import_scores(wb, _store(), approved_by=args.by,
                           tickers=args.tickers)
    for t, n in counts.items():
        print(f"{t}: {n} indicator scores imported and approved")
    print(f"total: {sum(counts.values())} scores from {os.path.basename(wb)}")
    return 0


def _cmd_doctor(args) -> int:
    from landry.doctor import report, run_all
    checks = run_all()
    print(report(checks))
    return 1 if any(not c.ok for c in checks) else 0


def _cmd_audit(args) -> int:
    from landry.audit import report, run_all
    wb = args.workbook or _default_workbook()
    checks = run_all(wb, repo_dir=_REPO)
    print(report(checks, os.path.basename(wb)))
    return 1 if any(not c.ok for c in checks) else 0


def _cmd_refresh(args) -> int:
    from landry.refresh import build_snapshot, print_report, write_snapshot
    from landry.xlsx_io import equity_weights, read_positions

    weights = None
    if args.tickers:
        tickers = args.tickers
    else:
        wb = args.workbook or _default_workbook()
        weights = equity_weights(read_positions(wb))
        tickers = sorted(weights)
        print(f"holdings from {os.path.basename(wb)}: {', '.join(tickers)}")
    snap = build_snapshot(
        tickers, weights=weights, refresh_data=args.refresh_data,
        with_fundamentals=not args.no_fundamentals,
        with_market=not args.no_market)
    print_report(snap)
    path = write_snapshot(snap, _REPO)
    print(f"\nsnapshot written: {os.path.basename(path)}")
    return 0


def _read_notes(args):
    if args.notes is not None and args.notes_file is not None:
        sys.exit("--notes and --notes-file are mutually exclusive")
    if args.notes_file is None:
        return args.notes
    if args.notes_file == "-":
        return sys.stdin.read().rstrip("\n")
    with open(args.notes_file, encoding="utf8") as f:
        return f.read().rstrip("\n")


def _cmd_ledger(args) -> int:
    from landry import generate, ledger, models
    wb = args.workbook or _default_workbook()
    db = args.db or models.DEFAULT_DB_PATH
    kw = dict(recalc=not args.no_recalc, force=args.force)
    try:
        if args.cmd == "journal" and args.action == "add":
            result = ledger.journal_add(wb, db, _read_notes(args), date=args.date,
                                        label=args.label, **kw)
        elif args.cmd == "journal":
            fields = {k: v for k, v in (("date", args.date), ("label", args.label),
                                        ("notes", _read_notes(args))) if v is not None}
            if not fields:
                sys.exit("nothing to change: pass --date, --label and/or --notes")
            result = ledger.journal_edit(wb, db, args.row, fields, **kw)
        elif args.action == "add":
            result = ledger.drawdown_add(wb, db, args.date, args.value, args.notes, **kw)
        else:
            fields = {k: v for k, v in (("portfolio_value", args.value),
                                        ("notes", args.notes)) if v is not None}
            if not fields:
                sys.exit("nothing to change: pass --value and/or --notes")
            result = ledger.drawdown_edit(wb, db, args.date, fields, **kw)
    except (ledger.LedgerError, models.SchemaMismatch, generate.GenerateError,
            ValueError) as e:
        print(f"! {e}", file=sys.stderr)
        return 1
    print(f"{result['tab']} row {result['row']} {'added' if args.action == 'add' else 'changed'}.")
    if result.get("built_db_from_workbook"):
        print(f"  (no database yet: built {os.path.basename(db)} from the workbook first -- "
              f"{result['built_db_from_workbook']})")
    if result.get("recalc"):
        print(f"  recalc: {result['recalc'].get('status')}, "
              f"{result['recalc'].get('total_errors')} errors")
    if result.get("capped_rows"):
        print(f"  note: Journal rows {result['capped_rows']} exceed Excel's 409.5pt row height "
              f"and show their first lines in-cell")
    print("Next: `python -m landry audit`, then commit the workbook.")
    return 0


def _cmd_db(args) -> int:
    from landry import generate, ledger, models
    wb = args.workbook or _default_workbook()
    db = args.db or models.DEFAULT_DB_PATH
    try:
        if args.action == "pull":
            counts = ledger.pull(wb, db)
            print(f"{db}: rebuilt from {os.path.basename(wb)} -- "
                  + ", ".join(f"{k}: {n} entries" for k, n in counts.items()))
            return 0
        if args.action == "regenerate":
            result = ledger.regenerate(wb, db, recalc=not args.no_recalc, force=args.force)
            for key, info in result["tabs"].items():
                print(f"{key}: {info['rows']} entries written from the database")
            if result.get("recalc"):
                print(f"  recalc: {result['recalc'].get('status')}, "
                      f"{result['recalc'].get('total_errors')} errors")
            print("Next: `python -m landry audit`, then commit the workbook.")
            return 0
        if not os.path.exists(db):
            print(f"no database at {db} yet -- `python -m landry db pull` builds it from the workbook")
            return 1
        report = ledger.status(wb, db)
    except (ledger.LedgerError, models.SchemaMismatch, generate.GenerateError) as e:
        print(f"! {e}", file=sys.stderr)
        return 1
    drifted = False
    for key, info in report.items():
        line = f"{key}: {info['db_rows']} entries in the database; "
        if info["value_diffs"]:
            drifted = True
            first = "; ".join(f"{w}: {str(a)[:30]!r} (workbook) vs {str(b)[:30]!r} (database)"
                              for _, w, a, b in info["value_diffs"][:2])
            line += f"CONTENT DIFFERS in {len(info['value_diffs'])} cell(s) -- {first}"
        else:
            line += "content in sync"
        if info["format_diffs"]:
            line += f"; {len(info['format_diffs'])} formatting difference(s) the next write resets"
        print(line)
    if drifted:
        print("Resolve with `landry db pull` (workbook is right) or `landry db regenerate` (database is right).")
    return 1 if drifted else 0


def _cmd_prices(args) -> int:
    from landry import ledger, prices
    wb = args.workbook or _default_workbook()
    if args.action == "status":
        st = prices.status(wb)
        if st.get("last_date"):
            print(f"Price History: {st['weeks']} weeks through {st['last_date']} (row {st['last_row']}), "
                  f"{len(st['tickers'])} tickers, {st['weeks_behind']} completed week(s) behind"
                  + (f" ({', '.join(d.isoformat() for d in st['missing_fridays'])})" if st['weeks_behind'] else ""))
            if st["gaps"]:
                print(f"  free header slot(s): {', '.join(st['gaps'])}")
            if st["in_header_not_held"]:
                print(f"  tracked but no longer held: {', '.join(st['in_header_not_held'])}")
        for p in st["problems"]:
            print(f"! {p}")
        print("OK" if st["ok"] else "problems found -- see above")
        return 0 if st["ok"] else 1
    write = not args.dry_run
    if write and not args.force and ledger._excel_has_open(wb):
        print("! Excel appears to have the workbook open -- close it (or pass --force)", file=sys.stderr)
        return 1
    try:
        if args.action == "append":
            rep = prices.append_weeks(wb, write=write, force=args.allow_big_moves)
            if rep["up_to_date"]:
                print(f"up to date: last row {rep['last_date_before']}, no completed week is missing")
                return 0
            print(f"{'would append' if not write else 'appended'}: {', '.join(rep['appended'])}")
        elif args.action == "rebuild":
            rep = prices.rebuild(wb, write=write)
            print(f"{'would change' if not write else 'rewrote'} {rep['cells_changed']} cells "
                  f"({rep['cells_newly_filled']} newly filled; largest change {rep['largest_relative_change']:.1%}) "
                  f"across {rep['weeks']} weeks x {rep['tickers']} tickers")
        else:
            if not args.ticker:
                print("! prices add needs a ticker", file=sys.stderr)
                return 1
            rep = prices.add_ticker(wb, args.ticker, write=write)
            print(f"{'would add' if not write else 'added'} {rep['ticker']} at column {rep['column']}"
                  + (" (reused a free slot)" if rep.get("reused_slot") else ""))
    except prices.PricesError as e:
        print(f"! {e}", file=sys.stderr)
        return 1
    for w in rep.get("warnings", []):
        print(f"  warning: {w}")
    if write and not args.no_recalc:
        from landry.xlsx_recalc import recalc
        res = recalc(wb)
        print(f"  recalc: {res.get('status')}, {res.get('total_errors')} errors")
    print("Next: `python -m landry audit`, then commit the workbook.")
    return 0


def _cmd_weekly(args) -> int:
    """The Friday-close routine (``market`` is the same minus Price History). Exit status: 0 clean,
    1 refused (Excel has the workbook open; nothing changed), 2 ran but something needs attention."""
    from landry import ledger, weekly
    wb = args.workbook or _default_workbook()
    write = not args.dry_run
    if write and not args.force and ledger._excel_has_open(wb):
        print("! Excel appears to have the workbook open -- nothing was changed. Close it and run "
              f"`python -m landry {args.cmd}` again (a weekly run catches up any missed Friday).", file=sys.stderr)
        return 1
    rep = weekly.run(wb, write=write, do_prices=args.cmd == "weekly",
                     allow_big_moves=getattr(args, "allow_big_moves", False))
    weekly.verify(wb, rep, repo_dir=_REPO, recalc_now=not args.no_recalc)
    print(weekly.format_report(rep, workbook=os.path.basename(wb)))
    return 2 if rep["problems"] else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="landry")
    sub = p.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("score", help="recompute scores from the workbook")
    sc.add_argument("ticker", nargs="?", help="ticker symbol (or --all)")
    sc.add_argument("--all", action="store_true", dest="all_")
    sc.add_argument("--workbook", default=None)
    sc.add_argument("--store", action="store_true",
                    help="score from approved scores in landry_scores.json")

    dr = sub.add_parser("draft", help="propose drafts for approval")
    dr.add_argument("ticker")
    dr.add_argument("--evidence", help="JSON evidence file: "
                    '[{"text","source_tier","source","date"}, ...]')
    dr.add_argument("--model", default="claude-sonnet-5")

    pe = sub.add_parser("pending", help="drafts awaiting approval")
    pe.add_argument("ticker", nargs="?")

    av = sub.add_parser("approve", help="approve a pending draft")
    av.add_argument("ticker")
    av.add_argument("indicator")
    av.add_argument("--by", required=True, help="approver name")
    av.add_argument("--score", type=int, help="override the drafted score")
    av.add_argument("--conf", choices=("H", "M", "L"),
                    help="override the drafted confidence")

    rj = sub.add_parser("reject", help="reject a pending draft")
    rj.add_argument("ticker")
    rj.add_argument("indicator")
    rj.add_argument("--by", required=True)
    rj.add_argument("--reason", required=True)

    rf = sub.add_parser("refresh", help="recompute all automatable inputs")
    rf.add_argument("--tickers", nargs="+", metavar="T",
                    help="default: equity holdings from the workbook")
    rf.add_argument("--workbook", default=None)
    rf.add_argument("--refresh-data", action="store_true",
                    help="force fresh price download")
    rf.add_argument("--no-fundamentals", action="store_true")
    rf.add_argument("--no-market", action="store_true")

    dy = sub.add_parser("daily", help="today's action items")
    dy.add_argument("--workbook", default=None)
    dy.add_argument("--refresh", action="store_true",
                    help="run a full data refresh first")
    dy.add_argument("--tickers", nargs="+", default=None)
    dy.add_argument("--refresh-data", action="store_true")
    dy.add_argument("--no-fundamentals", action="store_true")
    dy.add_argument("--no-market", action="store_true")

    ex = sub.add_parser("export", help="fill a copy of the Excel workbook")
    ex.add_argument("--workbook", default=None, help="template workbook")
    ex.add_argument("--out", default=None)
    ex.add_argument("--no-prices", action="store_true",
                    help="ignored -- export no longer writes Price History (use `landry prices`)")
    ex.add_argument("--scores", action="store_true",
                    help="also write approved scores to the Scoring tab")
    ex.add_argument("--drawdown", action="store_true",
                    help="RETIRED -- use `landry drawdown add`")

    im = sub.add_parser("import", help="seed the score store from the workbook")
    im.add_argument("--workbook", default=None)
    im.add_argument("--by", required=True, help="approver of record")
    im.add_argument("--tickers", nargs="+", default=None)

    sub.add_parser("doctor", help="check this machine is ready to edit "
                   "the workbook safely (Python version, LibreOffice, "
                   "required packages)")

    au = sub.add_parser("audit", help="check the workbook for structural "
                        "drift (stale Table refs, reader bounds, cross-tab "
                        "references, page setup, Schema Reference)")
    au.add_argument("--workbook", default=None)

    def _ledger_flags(sp, writes=True):
        sp.add_argument("--workbook", default=None)
        sp.add_argument("--db", default=None, help="default: landry.db at the repo root")
        if writes:
            sp.add_argument("--no-recalc", action="store_true",
                            help="skip the LibreOffice recalc (the workbook is then NOT safe to commit)")
            sp.add_argument("--force", action="store_true",
                            help="write even if Excel appears to have the workbook open")

    def _notes_flags(sp):
        sp.add_argument("--notes", default=None)
        sp.add_argument("--notes-file", default=None,
                        help="read the notes from a file, or '-' for stdin (no shell quoting)")

    jn = sub.add_parser("journal", help="add or edit Journal entries (regenerates the tab)")
    jsub = jn.add_subparsers(dest="action", required=True)
    ja = jsub.add_parser("add", help="append an entry")
    ja.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    ja.add_argument("--label", default=None, help="the Ticker column: a ticker, a list, or an event label")
    _notes_flags(ja)
    _ledger_flags(ja)
    je = jsub.add_parser("edit", help="change an entry by its sheet row (the number the Journal cites)")
    je.add_argument("--row", type=int, required=True)
    je.add_argument("--date", default=None)
    je.add_argument("--label", default=None, help="'' clears it")
    _notes_flags(je)
    _ledger_flags(je)

    ddp = sub.add_parser("drawdown", help="add or edit Portfolio Drawdown Log entries (regenerates the tab)")
    dsub = ddp.add_subparsers(dest="action", required=True)
    da = dsub.add_parser("add", help="log a portfolio value (one per date)")
    da.add_argument("--date", required=True, help="YYYY-MM-DD")
    da.add_argument("--value", type=float, required=True, help="total portfolio value, $")
    da.add_argument("--notes", default=None)
    _ledger_flags(da)
    de = dsub.add_parser("edit", help="change the entry for a date")
    de.add_argument("--date", required=True)
    de.add_argument("--value", type=float, default=None)
    de.add_argument("--notes", default=None)
    _ledger_flags(de)

    pr = sub.add_parser("prices", help="Price History weekly closes: status / append / rebuild / add")
    pr.add_argument("action", choices=["status", "append", "rebuild", "add"])
    pr.add_argument("ticker", nargs="?", default=None, help="for `add`: the ticker to give a column")
    pr.add_argument("--workbook", default=None)
    pr.add_argument("--dry-run", action="store_true", help="show what would change; write nothing")
    pr.add_argument("--no-recalc", action="store_true",
                    help="skip the LibreOffice recalc (the workbook is then NOT safe to commit)")
    pr.add_argument("--force", action="store_true", help="write even if Excel appears to have the workbook open")
    pr.add_argument("--allow-big-moves", action="store_true",
                    help="append even if a close is >35%% from last week's (check the column first)")

    def _weekly_flags(sp):
        sp.add_argument("--workbook", default=None)
        sp.add_argument("--dry-run", action="store_true",
                        help="fetch and report what would change; write, recalc and save nothing")
        sp.add_argument("--no-recalc", action="store_true",
                        help="skip the LibreOffice recalc (the workbook is then NOT safe to commit)")
        sp.add_argument("--force", action="store_true", help="write even if Excel appears to have the workbook open")

    wk = sub.add_parser("weekly", help="the Friday-close routine: append Price History, refresh Market Data "
                        "and earnings dates, one recalc, audit (never commits)")
    _weekly_flags(wk)
    wk.add_argument("--allow-big-moves", action="store_true",
                    help="append even if a close is >35%% from last week's (check the column first)")
    mk = sub.add_parser("market", help="refresh Market Data and the Monitor tab's earnings dates in place "
                        "(the weekly routine minus Price History)")
    _weekly_flags(mk)

    dbp = sub.add_parser("db", help="keep landry.db and the generated tabs in sync")
    dbsub = dbp.add_subparsers(dest="action", required=True)
    _ledger_flags(dbsub.add_parser("status", help="does the database agree with the tabs?"), writes=False)
    _ledger_flags(dbsub.add_parser("pull", help="rebuild the database from the workbook (workbook wins)"),
                  writes=False)
    _ledger_flags(dbsub.add_parser("regenerate", help="rewrite the tabs from the database (database wins)"))

    args = p.parse_args(argv)

    if args.cmd in ("journal", "drawdown"):
        return _cmd_ledger(args)
    if args.cmd == "prices":
        return _cmd_prices(args)
    if args.cmd in ("weekly", "market"):
        return _cmd_weekly(args)
    if args.cmd == "db":
        return _cmd_db(args)
    if args.cmd == "doctor":
        return _cmd_doctor(args)
    if args.cmd == "audit":
        return _cmd_audit(args)
    if args.cmd == "refresh":
        return _cmd_refresh(args)
    if args.cmd == "daily":
        return _cmd_daily(args)
    if args.cmd == "export":
        return _cmd_export(args)
    if args.cmd == "import":
        return _cmd_import(args)
    if args.cmd == "draft":
        return _cmd_draft(args)
    if args.cmd == "pending":
        return _cmd_pending(args)
    if args.cmd == "approve":
        return _cmd_approve(args)
    if args.cmd == "reject":
        return _cmd_reject(args)

    if getattr(args, "store", False):
        if not args.ticker:
            sys.exit("--store requires a ticker")
        t = args.ticker.upper()
        scores = _store().approved_scores(t)
        if not scores:
            sys.exit(f"{t}: no approved scores in {SCORES_FILE}")
        card = score_stock(t, scores)
        print(f"scores: {SCORES_FILE} (approved only)")
        avg = card.tier1_weighted_average
        print(f"\n{t}")
        print(f"  Tier 1 weighted avg : {avg:.2f} -> {card.flags.rule1}")
        if card.composite is not None:
            print(f"  Composite Score     : {card.composite:.1f}")
        print(f"  Decision            : {card.decision}")
        for n in card.notes:
            print(f"  ! {n}")
        return 0

    wb = args.workbook or _default_workbook()
    rows = read_scoring_tab(wb)
    if args.all_:
        selected = rows
    elif args.ticker:
        selected = [r for r in rows if r.ticker.upper() == args.ticker.upper()]
        if not selected:
            sys.exit(f"{args.ticker}: not found in {os.path.basename(wb)} "
                     f"(has: {', '.join(r.ticker for r in rows)})")
    else:
        sys.exit("give a ticker or --all")

    print(f"workbook: {os.path.basename(wb)}")
    for row in selected:
        card = score_stock(row.ticker, row.scores)
        _print_card(row, card)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
