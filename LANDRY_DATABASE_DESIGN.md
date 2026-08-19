# Landry System: SQLite Database Design

**Status:** design proposal, not yet implemented. Supersedes the database
portion of `LANDRY_IMPLEMENTATION_PLAN.md` (see Finding below) — everything
else in that plan (rule engine, Part 12 boundary, phase numbering for
non-DB work) stands.

**Driving question:** the manual xlsx-edit-and-recalc workflow (openpyxl
edit → `landry.xlsx_recalc` LibreOffice pass → verify no `#REF!`/`#DIV/0!`
→ open in real Excel to confirm clean → commit) has become the main
friction point in running the Landry System day to day. This doc proposes
replacing it with SQLite as the system of record and the xlsx as a
**generated report** — a file written fresh from the database with
already-computed values, never hand-edited or formula-recalculated again.

---

## Finding: this was already decided, and never built

`LANDRY_IMPLEMENTATION_PLAN.md` opens with **"Decisions made: app-owned
SQLite DB with Excel import/export"**, sketches a schema (`tickers`,
`scores`, `composite_history`, `positions`, `entries`, `scenarios`,
`watchlist`, `sell_triggers`, `correlations`, `drawdown_log`, `journal`,
`action_items`), and marks Phases 1–5 "✅ done."

None of the SQLite layer exists. There is no `landry.db`, no `models.py`,
no `sqlite3`/`SQLAlchemy` usage anywhere in `landry/` or `webapp.py`. What
actually got built is Excel-native:

- `landry/xlsx_io.py` reads the workbook directly (positions, drawdown
  log, scoring tab).
- `landry/export.py`'s `export_workbook()` writes values into a *copy* of
  the workbook with openpyxl. Its own docstring says why the recalc pain
  exists: *"the workbook's formula tabs — Returns, Correlation Matrix,
  Scoring computed columns, Action Items — recalculate in Excel on
  open."* Those tabs were deliberately left as live formulas instead of
  app-computed values.
- `landry/approvals.py`'s `ScoreStore` is a JSON file (`landry_scores.json`),
  not a database — the Part 12 approval audit trail, and nothing else.
- Positions, market data, price history, drawdown log, entry checklist,
  monitor triggers, watch list, implied-return, holding monitor,
  performance tracking — all still live only in the xlsx.

So the ask isn't "should we add a database" — it's "finish the layer that
was already scoped, and this time make the Excel export **static computed
values**, not live formulas, so opening it never requires a recalc pass."

The good news: the *logic* that formula tabs currently compute already
exists in Python and is tested — `scoring.py` (composite/tiers/decision),
`entry.py` (Rules 5–13), `sizing.py`, `monitor.py`, `drawdown.py`
(`regime_frame`), `performance.py`, and `data_auto.py`'s
`correlation_report()` / `weekly_returns()` (the latter built this
session for Rule 36 checks). This is a storage-and-wiring job on top of
logic that's already been ported once, not a rebuild of the domain rules.

---

## Current real workbook structure (verified against the live Schema
## Reference tab + direct inspection, 2026-08-18 — supersedes the plan's
## 2026-vintage schema sketch)

18 tabs. `Instructions` is prose reference (no data). `Action Items`,
`Dashboard`, `Returns (Calc)`, `Correlation Matrix` are pure formula
summaries of other tabs — they disappear entirely as *stored* data in the
new design and become generated report views instead. `Schema Reference`
documents the schema itself — its role is superseded by this document plus
introspecting `models.py` directly.

The remaining 13 tabs hold real data:

| Tab | Structure today | Notes |
|---|---|---|
| Scoring | Fixed range, rows 3–45 (43 tickers), 2-row merged header | Ticker/Company/Date, Tier 1 (5 indicators × Score+Conf), Tier 2 (4×), Tier 3 (3×), weighted-avg/contribution columns, Composite, Decision, Rule 1–4 flags, Sector/Industry. One row per ticker = **latest score only** — no history today. |
| Market Data | Fixed range, rows 3–27 | Price, Volume, MarketCap, P/E, 52wk range, DivYield. Marked "manual entry" in the doc even though `data_auto.py` can fetch all of it. |
| Current Positions | Table, A2:M40 | Two accounts (JT ULTRA, Chase self-directed); qty/price/value/cost basis/unrealized G-L/% weights/classification. Tax-loss carryforward in two loose cells outside the table. |
| Monitor & Recheck Triggers | Table, A2:P42 | Mostly *derived* from Scoring + Market Data (LastComposite, CurrentPrice, %Change, DaysSinceScore) plus a few genuinely manual fields (InsiderY/N, InsiderNote, AnalystShiftY/N, RecheckStatus, Notes). |
| Watch List Tracker | Table, A2:L22 | Status/entry/current score/90-day remediation deadline tracking for names that failed Tier 1 but are being monitored. |
| Implied-Return Calculator | Fixed range, rows 4–23, 2-row merged header | Per ticker × {Base, Bear, Bull} scenario: FCF-yr5, terminal multiple, distributions, implied return, Likely/Unlikely tag, plus the three Rule 11/12 pass checks. |
| Holding Monitor | Fixed range, rows 4–23, 2-row merged header | Per ticker: position %, 5 fundamental indicators × {Prior, Current, Flag}, Debt/FCF, P/FCF, FCF growth, implied return, tier drift, Hold-Through Y/N, action status. |
| Performance Tracking | Table, A2:Q34 | Entry/exit lifecycle per position: dates, prices, entry score/confidence/band, SPY benchmark at entry and current/exit, total and excess return. |
| Price History | Fixed range, rows 3–162, up to 20 ticker columns | Weekly closes, week-ending Friday, manually maintained, Rule 36 windowing (12mo min/24mo target/36mo max). |
| Portfolio Drawdown Log | Fixed range, rows 3–42 | Date, portfolio value, running peak, drawdown %, regime status, required cash floor, new-position rule, notes. |
| Entry Checklist | Table, A2:R45 | Per ticker: Rules 5–13 pass/fail values and the final ENTRY AUTHORIZED? verdict. |
| Journal | Table, A2:C302 | Date/Ticker/Notes, freeform append-only log. |
| *(Part 12 approvals)* | `landry_scores.json`, not a workbook tab | Already outside Excel — the model below absorbs it. |

---

## Proposed schema

Plain `sqlite3` (stdlib), no ORM — matches this codebase's existing
minimal-dependency style (`layer1-6` uses only numpy/pandas, no scipy; no
reason to pull in SQLAlchemy for a single-writer local database). One
`landry/models.py` with table DDL + thin dataclass-returning read/write
functions, mirroring how `xlsx_io.py` is organized today.

**Design principle used throughout:** store what's genuinely an input or a
judgment call; compute everything else on read. Excel currently blurs this
— formula tabs are "derived" but still occupy sheet cells that must be
recalculated; a DB removes the distinction entirely by never storing a
derived value in the first place.

```sql
-- Reference
tickers(ticker PK, company, first_seen_date)

-- Scoring — HISTORY, not latest-only (see Design decisions)
scores(id PK, ticker FK, indicator, tier, score, confidence, evidence,
       scored_date, approved_by, approved_at)
composite_history(id PK, ticker FK, scored_date, tier1_wtd_avg, composite,
                   decision, rule1_flag, rule2_flag, rule3_flag, rule4_flag)
classification(ticker PK/FK, sector, industry, as_of_date)   -- yfinance-sourced

-- Market / price
market_data(ticker FK, price, volume, market_cap, pe, wk52_low, wk52_high,
            dividend_yield, as_of_date)
price_history(ticker FK, week_ending, close)                 -- long format

-- Positions / performance
positions(id PK, account, ticker FK, description, asset_class, quantity,
          price, market_value, cost_basis, unrealized_gl, unrealized_gl_pct,
          pct_of_account, pct_of_combined, classification, as_of_date)
tax_loss_carryforward(term, amount, as_of_date)
performance_cohort(id PK, ticker FK, entry_date, entry_price, entry_score,
                   entry_confidence, entry_band, spy_at_entry, status,
                   exit_date, exit_price, exit_reason)

-- Monitoring / watch list / holding checks
monitor_notes(ticker FK, insider_flag, insider_note, analyst_shift_flag,
              recheck_status, notes, updated_at)              -- manual fields only
watchlist(id PK, ticker FK, status, entry_date, entry_score,
          remediation_plan_yn, deadline_90day, action_status, notes)
holding_monitor(id PK, ticker FK, as_of_date, position_pct, max_full_pct,
                debt_fcf, p_fcf, fcf_growth, implied_return, current_tier,
                prior_tier, valuation_flags, hold_through_yn, action_status)
holding_monitor_indicator(holding_monitor_id FK, indicator_name,
                          prior_value, current_value, flag)

-- Entry workflow
implied_return_scenario(id PK, ticker FK, scenario, fcf_yr5, terminal_mult,
                        distributions, implied_return, tag, computed_date)
entry_checklist(id PK, ticker FK, computed_date, rule5_composite ... rule13_ceiling,
                risk_in_thesis_yn, entry_authorized, recommended_action)

-- Portfolio-level
drawdown_log(id PK, date, portfolio_value, running_peak, drawdown_pct,
            status, cash_floor, new_position_rule, notes)

-- Audit
journal(id PK, date, ticker FK nullable, notes)
```

Eliminated as *stored* tables entirely (become read-time queries against
the tables above, reusing existing Python functions):

- **Returns (Calc)** → `data_auto.weekly_returns(price_history)`
- **Correlation Matrix** → `data_auto.correlation_report()` /
  `correlation_vs_holdings()` (already built this session)
- **Dashboard** → latest row per ticker from `composite_history`
- **Action Items** → `landry daily`'s existing logic, reading the tables
  above instead of the workbook
- **Monitor & Recheck Triggers'** derived columns (LastComposite,
  CurrentPrice, %Change, DaysSinceScore) → joined from `composite_history`
  + `market_data` at read time; only `monitor_notes` above is real storage

## Design decisions to confirm before implementation

1. **Score history vs. latest-only.** Today's Scoring tab overwrites in
   place — one row per ticker. The proposed `scores`/`composite_history`
   tables are append-only history instead, which the workbook has never
   had (Monitor & Recheck Triggers approximates it with "LastComposite,"
   implying a memory of change that isn't actually stored anywhere). This
   is close to free once there's a real database and is genuinely useful
   (score drift over time, per Part 9 auditability) — recommend doing it,
   but flagging since it's a scope decision, not just a storage-format
   swap.
2. **Git and the approval audit trail.** `landry_scores.json` is
   deliberately git-tracked today specifically because a prior incident
   (2026-08, this session's earlier phase) silently wiped it via a
   `.gitignore` miscategorization, and nothing regenerates a lost approval
   history. A SQLite file is binary — it can't git-diff usefully and
   shouldn't be tracked wholesale the way `data_cache/` isn't. Proposal:
   gitignore `landry.db` itself, but keep writing (or nightly-export) the
   `scores` table's approval rows to a git-tracked JSON/CSV, continuing
   `landry_scores.json`'s exact role as a diffable backup — not reversing
   the lesson learned earlier, just relocating the live copy.
3. **Normalize vs. mirror Excel's wide layout.** Price History and the
   Implied-Return / Holding Monitor scenario blocks are wide (one column
   group per ticker or per scenario) in Excel because that's easy to
   eyeball in a spreadsheet. The schema above normalizes them to long/tidy
   tables, which is the natural SQL fit and also what `layer1-6` and
   `data_auto.py` already expect (`weekly_closes` etc. take tidy
   DataFrames). No real downside identified; flagging because it means the
   generated report's layout code has to pivot back to wide for the
   Excel view, rather than being a near-literal dump.
4. **`sqlite3` stdlib vs. SQLAlchemy.** The original plan listed both as
   an option. Recommend plain `sqlite3` — this is a single local writer,
   no concurrent access, no need for an ORM's migration/relationship
   machinery, and it keeps the dependency footprint at zero for the DB
   layer itself.
5. **Backend: local `sqlite3` file vs. Turso (2026-08-19, open — revisit
   at Phase B).** Alan's leaning toward Turso instead of a plain local
   file. Turso is hosted libSQL (a SQLite fork) with sync/replication;
   its Python client is largely `sqlite3`-API-compatible and supports an
   embedded-replica mode (local file that syncs to a remote database),
   so this doesn't necessarily invalidate decision 4's schema/query code
   — it mainly changes *where the source of truth lives* and *what
   `models.py`'s `connect()` does under the hood*. The concrete reason
   this matters here, not just in the abstract: it would directly answer
   the Taylor-collaboration problem from 2026-08-19 (see
   `taylor_landry_collaborator` in Claude's memory) — two people each
   running their own local `landry.db` is exactly the setup that produced
   the duplicate-DB-layer collision; a synced Turso database gives Alan
   and Taylor (and Claude sessions under either account) one shared live
   state instead of independently-diverging local files. Tradeoffs to
   weigh at Phase B: a real dependency (`libsql-client` or similar,
   replacing the zero-dependency stdlib approach in decision 4), and a
   database URL + auth token to handle as a secret (same treatment as
   `webapp_secret.key` — gitignored, never committed) rather than
   `landry.db` staying a plain gitignored local file.

## Migration phases

**Phase A — Schema + read-only migration.** Write `landry/models.py`
(DDL + read/write functions). Write a one-time migration script that reads
every tab via (extended) `xlsx_io.py` helpers and populates `landry.db`
from the current Workbook 25. No behavior change yet — CLI and webapp keep
reading the xlsx directly. This is purely a correctness proof: does the
schema actually capture everything, checked by diffing DB contents against
the workbook.

**Phase B — Cut over reads.** Point read paths at the database instead of
the workbook, tab by tab, safest first (Journal, Drawdown Log, Positions)
before the formula-heaviest (Scoring, Monitor triggers). `landry_scores.json`
migrates into `scores`; JSON export continues as the git-tracked backup
(decision 2 above).

**Phase C — Generated-report export replaces in-place editing.** Rebuild
`landry export` to write a **fresh** workbook from the database every time,
with static computed values everywhere that's currently a live formula
(Returns, Correlation Matrix, Action Items, Dashboard, Scoring's computed
columns) — using the Python equivalents that mostly already exist. This is
the phase that actually retires the recalc dependency: a freshly generated
file has no stale cached formulas to invalidate, so `xlsx_recalc.py`'s role
shrinks from "mandatory after every edit" to "final sanity pass, optional."
The old openpyxl-edit-in-place workflow is retired for anything migrated.

**Phase D — Parallel-run verification.** Same idea as the existing plan's
already-scoped one-month dry run (`LANDRY_IMPLEMENTATION_PLAN.md` Phase 6):
run the DB-backed export alongside the current hand-maintained workbook for
a stretch, diff for drift, before treating the database as sole source of
truth.

## Decisions (2026-08-18)

- **Score history: yes, keep it.** Confirmed useful for backtesting and
  for surfacing lag/lead relationships or correlations that a latest-only
  snapshot would hide. `scores` and `composite_history` are append-only
  as designed above.
- **Tabs in scope: all of them.** With the possible exception of Journal,
  every tab is populated by Claude/the CLI, not hand-typed — so there's no
  tab that needs to stay Excel-editable. All 13 data tabs migrate; the
  generated workbook stays useful for viewing, confirmation, and quick
  reference even though it's no longer the source of truth.
- **Sequencing: this work takes priority over the swing-trading track**
  (paused, see the "firm to-do" from 2026-08-18), but should be built to
  accommodate and eventually supplement it — e.g. `price_history` and the
  correlation/return helpers this schema formalizes are exactly what the
  swing track's universe-selection and Rule 36 checks already lean on.

## Phase A — done (2026-08-18)

Built and ran:

- `landry/models.py` — schema (above) via plain `sqlite3`.
- `landry/xlsx_io.py` — extended with readers for every remaining tab
  (Market Data, Price History, Monitor notes, Watch List, Performance
  Tracking, Holding Monitor, Implied-Return scenarios, Entry Checklist,
  Journal, tax-loss carryforward, a full-column Positions/Drawdown-Log
  reader) alongside the existing ones. `read_scoring_tab` also now reads
  the Sector/Industry columns (AL/AM) it previously skipped.
- `landry/migrate_to_db.py` — `python -m landry.migrate_to_db` reads
  Workbook 25 end to end into `landry.db`, plus `landry_scores.json`'s
  approved scores (with full provenance) into the same `scores` table.

**Verified, not just run:** MU's DB scores/composite/decision match the
workbook exactly (composite 71.8, BUY, Tier 1 wtd avg 3.642857...).
`price_history` holds 2,801 rows across 18 tickers, 156 weeks each except
`HELO` (149 — a newer position, consistent with a shorter history).
Two real bugs were caught and fixed during verification, not left for
Phase B to discover: `read_market_data` and `read_performance_tracking`
were unbounded and had started reading each tab's explanatory footnote
text below the real table as a bogus data row (both tabs are otherwise
genuinely empty right now — no live data lost, just a reader bug). Fixed
by bounding every table reader to its actual documented/Table-defined row
range rather than reading to the end of the sheet.

Current real-data population, for context: Scoring (43 tickers, 323
indicator scores), Positions (26), Price History (2,801), Monitor notes
(16), Entry Checklist (43), Journal (17), Drawdown Log (1 entry). Market
Data, Watch List, Performance Tracking, Holding Monitor, Implied-Return
Calculator, and `landry_scores.json`'s approved-scores section are
currently empty in the live workbook/store — the schema and migration
code are ready for them regardless.

`landry.db` is gitignored (derived, rebuildable); `landry/models.py` and
`landry/migrate_to_db.py` are tracked.

**Not yet done:** Phase B (cut over CLI/webapp read paths), Phase C
(generated-report export with static values, retiring the recalc
dependency), Phase D (parallel-run verification).
