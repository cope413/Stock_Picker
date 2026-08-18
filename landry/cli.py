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
    python -m landry export --drawdown   # ...and append today's portfolio value to the Drawdown Log
    python -m landry import --by "Taylor"  # seed score store from workbook
    python -m landry doctor              # check this machine is ready to edit the workbook

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
    import json

    from landry.export import export_workbook
    wb = args.workbook or _default_workbook()
    kwargs = {}
    snap_path = os.path.join(_REPO, "landry_snapshot.json")
    if os.path.exists(snap_path):
        with open(snap_path) as f:
            snap = json.load(f)
        market = {t: e["market"] for t, e in snap.get("tickers", {}).items()
                  if e.get("market")}
        if market:
            kwargs["market"] = market
    if not args.no_prices:
        try:
            from landry.data_auto import fetch_daily, weekly_closes
            from landry.xlsx_io import equity_weights, read_positions
            tickers = sorted(equity_weights(read_positions(wb)))
            kwargs["weekly_closes"] = weekly_closes(fetch_daily(tickers))
        except Exception as e:
            print(f"! price fill skipped: {e}")
    if args.scores:
        store = _store()
        approved = {t: store.approved_scores(t) for t in store.tickers()
                    if store.approved_scores(t)}
        if approved:
            import datetime as dt
            kwargs["approved_scores"] = approved
            kwargs["scored_date"] = dt.date.today()
    if args.drawdown:
        try:
            import datetime as dt

            import pandas as pd

            from landry.drawdown import regime_frame
            from landry.xlsx_io import (read_drawdown_log, read_positions,
                                        total_portfolio_value)
            today = pd.Timestamp(dt.date.today())
            existing = read_drawdown_log(wb)
            today_value = total_portfolio_value(read_positions(wb))
            existing = existing[existing.index.normalize() != today]
            series = pd.concat([existing, pd.Series(
                [today_value], index=[today], name="value")]).sort_index()
            kwargs["drawdown"] = regime_frame(series)
            print(f"drawdown: {len(existing)} prior day(s) + today "
                 f"(${today_value:,.2f}) -> "
                 f"{kwargs['drawdown'].iloc[-1]['status']}")
        except Exception as e:
            print(f"! drawdown fill skipped: {e}")
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
                    help="skip the Price History fill (no download)")
    ex.add_argument("--scores", action="store_true",
                    help="also write approved scores to the Scoring tab")
    ex.add_argument("--drawdown", action="store_true",
                    help="append today's portfolio value (Current Positions "
                    "total) to the existing Drawdown Log history and refill "
                    "the regime columns")

    im = sub.add_parser("import", help="seed the score store from the workbook")
    im.add_argument("--workbook", default=None)
    im.add_argument("--by", required=True, help="approver of record")
    im.add_argument("--tickers", nargs="+", default=None)

    sub.add_parser("doctor", help="check this machine is ready to edit "
                   "the workbook safely (Python version, LibreOffice, "
                   "required packages)")
    args = p.parse_args(argv)

    if args.cmd == "doctor":
        return _cmd_doctor(args)
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
