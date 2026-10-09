# Stock_Picker — working notes for Claude

Fresh session? Run `python -m landry brief` first (recent Journal, open items, steps due, git state: ~2K tokens), then read
full Journal rows / Open Items only for what it points at. The Journal, Open Items and Process Checklist tabs are the living
record; don't assume any prior conversation. Detail that used to live here is in `docs/ops/` (index at the bottom): read the
named file BEFORE touching its area.

## Mandatory, every time

- **Run `python -m landry.xlsx_recalc LANDRY_SYSTEM_WORKBOOK_25.xlsx` after every single save**, even a pure styling change. openpyxl wipes cached formula values on save; skipping it makes the workbook look broken. (Never on a workbook Alan just saved from real Excel: it strips his live prices.)
- **Run `python -m landry audit` before committing anything that touches the workbook.** A floor, not a substitute for checking your own work.
- **Never commit or push without the user explicitly saying so in that instance.** Approval doesn't carry over.
- **Never hand-roll a portfolio-value or position-aggregation calculation — use `landry.xlsx_io.total_portfolio_value(read_positions(wb))`.** (An ad hoc sum once came out at exactly double the real figure and was quoted for days.) More generally: every number given to Alan should come from a `landry` command or tested function, with inputs shown beside the output; ad hoc arithmetic gets promoted to a tested function or cross-checked against a second path.
- **Prices:** Current Positions' Price column cannot be refreshed live by the tooling (STOCKHISTORY only resolves in real Excel); use Market Data (ticker -> column C, refreshed by `landry weekly` / `landry market`) for any price computed outside Excel. Read `docs/ops/prices-and-market.md` before touching prices or Price History.
- **Journal and Portfolio Drawdown Log entries are written ONLY through `python -m landry journal|drawdown|db`** (`journal add --label L [--date D] --notes-file - <<'EOF' … EOF`; `journal edit --row N`; `drawdown add|edit`). Never edit those tabs with openpyxl; a write refuses if Excel has the workbook open or `landry.db` and the tab disagree (`db status`, then `db pull` or `db regenerate`). Journal is a decision log: don't rewrite old entries to match current reality, add a new one (mark superseded in place only when the substance changed). Read `docs/ops/journal-db.md` first.
- **Python:** use `/Users/Alan/.venvs/stock_picker/bin/python` (with `PYTHONPATH` = the repo) if `python3` complains about the Xcode license; `landry doctor` checks the environment.
- **Approvals in the score store are Alan's words only:** approve exactly what he names, with each approval's own rationale. Any score finalized conversationally still needs `propose` + `approve` in `landry_scores.json`.
- **Do not spawn agents unless Alan asks.** Keep tool output small: print aggregates, not whole tabs or files.

## Never touch

- Files with "copy" in the name are Alan's scratch copies: don't read them as truth or write to them unless he names one; they may be deleted outright. **Exception: `LANDRY_SYSTEM_WORKBOOK_25_JOURNAL_COPY.xlsx`** is a Claude-built standing artifact (regeneration steps in `docs/ops/journal-db.md`); don't delete it.
- **Never sort or re-order the Scoring tab or Dashboard** (filter instead): Dashboard, Entry Checklist and Implied-Return mirror Scoring row for row. Never re-sort the live Journal either (write order).
- **No `insert_rows`** on workbook tabs: a position goes in through `landry.cp_tab.insert_position`, a Performance Tracking lot through `perf_tab.add_lot`. After any row insertion or structural change, ask Alan to open the file in real Excel once (LibreOffice and openpyxl accept files Excel rejects). Read `docs/ops/workbook-structure.md` first.

## File conventions

- New `.xlsx`: Arial Narrow 10pt. New `.docx`: Calibri 11pt. Not retroactive.
- A rebuilt worksheet starts with zero page setup, and `xlsx_recalc` normalizes it (custom scale does not survive): see `docs/ops/workbook-structure.md`.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Standing tools

- `python -m landry brief` (state digest) and `python -m landry usage [--last N]` (where a session's tokens went; compare before/after token-saving changes).
- `python -m landry rule12 TICKER...` (Rule 12 pre-check, read-only: run it on every new candidate BEFORE any judgment work; PASS still needs per-share FCF confirmation) and `python -m landry open-items N --note TEXT [--close]` (append to / close an Open Items row, then recalc; never write one-off scripts for this).
- `python -m landry audit` (structural drift), `db status`, `doctor`.
- Process Checklist tab (steps per recurring Journal event, OVERDUE auto-flag; reseed it when the Journal calendar changes) and Open Items tab (numbered backlog; mark Done with a date and a Journal pointer, never delete).
- `landry draft`, `landry_scores.json` / `ScoreStore`, `landry audit`'s `scoring_verification` check: `docs/ops/scoring-export-readers.md`.
- `landry prices` (Price History is maintained ONLY by this, never by hand or `export`), `landry weekly` (Saturday routine; never commits): `docs/ops/prices-and-market.md`.
- `landry.sec_ttm` (trailing-12-month Tier 1 second opinion; policy: fiscal-year scores stay the record): `docs/ops/sec-ttm.md`.
- `landry monitor`, `landry rules-list`, `landry stops`, `landry etf`: `docs/ops/monitor-rules-stops-etf.md`.
- Performance Tracking is a lot ledger (`perf_tab.add_lot` / `close_lot`): `docs/ops/performance-tracking.md`.

## Standing decisions in one line each (detail in the named file)

- Piotroski cross-checks: DROPPED; don't revive without new evidence. ETF strategy: open design work (Open Items #7), see `docs/ops/rulebook-and-rules.md`.
- Hard Rule numbers are one global auto-numbered field: inserting a rule cascades every later number and every citation. Read `docs/ops/rulebook-and-rules.md` before any rulebook edit. Rule 3 applies to holdings exactly as written (Avoid = Exit Review); the stop-review ladder is un-numbered (v1.05).
- DCA cadence, tranche prep steps, Fidelity info-tab scan, Rule 12 pre-check first, sector-targeted open screening: `docs/ops/process-and-dca.md` (and Journal `DCA-CATCHUP-PLAN-2`).
- Most of the Scoring tab predates the score store; an old score is not verified just because it looks plausible (`audit scoring_verification` lists the backlog).

## Index of `docs/ops/`

| File | Read before |
|---|---|
| workbook-structure.md | any structural edit, page setup, row insert, formula mirror, sort/filter, circular-reference risk |
| scoring-export-readers.md | `landry export`, adding a ticker to Scoring, reader bounds, `landry draft`, score-store audit |
| journal-db.md | Journal/Drawdown writes, database, JOURNAL_COPY regeneration |
| prices-and-market.md | prices, Price History, `weekly`/`market`, Excel-saved workbook promotion |
| sec-ttm.md | trailing-12-month scores, Rule 1/3 trips, NFLX/VRTX add-backs |
| monitor-rules-stops-etf.md | Monitor tab, hard-rules list, stop ladder, ETF report |
| rulebook-and-rules.md | rulebook edits, rule citations, Rules 3/5/34, ETF treatment |
| performance-tracking.md | Performance Tracking tab |
| process-and-dca.md | tranche prep, screening, Fidelity scan, Open Items/Process Checklist upkeep |
