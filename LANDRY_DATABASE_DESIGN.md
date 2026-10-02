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
## Reference tab + direct inspection, originally 2026-08-18; tab count and
## the two new rows below refreshed 2026-09-11 — see "Revisited 2026-09-11"
## for what else changed)

**21 tabs as of 2026-09-11** (was 18 on 8/18 — `Process Checklist` and
`Open Items` are new, plus `Scratchpad (disregard)` was never in scope and
wasn't being counted consistently before either). `Instructions` is prose
reference (no data). `Action Items`, `Dashboard`, `Returns (Calc)`,
`Correlation Matrix` are pure formula summaries of other tabs — they
disappear entirely as *stored* data in the new design and become
generated report views instead. `Schema Reference` documents the schema
itself — its role is superseded by this document plus introspecting
`models.py` directly. `Scratchpad (disregard)` is explicitly out of
scope, as the name says.

The remaining 15 tabs hold real data:

| Tab | Structure today | Notes |
|---|---|---|
| Scoring | Fixed range, rows 3–45 (43 tickers), 2-row merged header | Ticker/Company/Date, Tier 1 (5 indicators × Score+Conf), Tier 2 (4×), Tier 3 (3×), weighted-avg/contribution columns, Composite, Decision, Rule 1–4 flags, Sector/Industry. One row per ticker = **latest score only** — no history today. |
| Market Data | Fixed range, rows 3–27 | Price, Volume, MarketCap, P/E, 52wk range, DivYield. Marked "manual entry" in the doc even though `data_auto.py` can fetch all of it. |
| Current Positions | Table, A2:M40 | Two accounts (JT ULTRA, Chase self-directed); qty/price/value/cost basis/unrealized G-L/% weights/classification. Tax-loss carryforward in two loose cells outside the table. |
| Monitor & Recheck Triggers | Table, A2:P42 | Mostly *derived* from Scoring + Market Data (LastComposite, CurrentPrice, %Change, DaysSinceScore) plus a few genuinely manual fields (InsiderY/N, InsiderNote, AnalystShiftY/N, RecheckStatus, Notes). |
| Watch List Tracker | Table, A2:L22 | Status/entry/current score/90-day remediation deadline tracking for names that failed Tier 1 but are being monitored. |
| Implied-Return Calculator | Fixed range, template through ~row 49 (corrected 2026-09-11 — was documented as rows 4–23, actually templated for the full 45-ticker universe, +1 row offset from Entry Checklist; only 2 rows actually populated, COST/V) | Per ticker × {Base, Bear, Bull} scenario: FCF-yr5, terminal multiple, distributions, implied return, Likely/Unlikely tag, plus the three Rule 11/12 pass checks. |
| Holding Monitor | Fixed range, rows 4–23, 2-row merged header (template caps at 20 tickers, unlike Implied-Return Calculator above — a real future ceiling, tracked as Open Items) | Per ticker: position %, 5 fundamental indicators × {Prior, Current, Flag}, Debt/FCF, P/FCF, FCF growth, implied return, tier drift, Hold-Through Y/N, action status. |
| Performance Tracking | Table, A2:Q34 | Entry/exit lifecycle per position: dates, prices, entry score/confidence/band, SPY benchmark at entry and current/exit, total and excess return. |
| Price History | Fixed range, rows 3–162, up to 20 ticker columns | Weekly closes, week-ending Friday, manually maintained, Rule 36 windowing (12mo min/24mo target/36mo max). |
| Portfolio Drawdown Log | Fixed range, rows 3–42 | Date, portfolio value, running peak, drawdown %, regime status, required cash floor, new-position rule, notes. |
| Entry Checklist | Table, A2:S47 (corrected 2026-09-11 — was A2:R45; grew a column, "Base Case >10%?", and 2 more tickers since 8/18) | Per ticker: Rules 5–13 pass/fail values and the final ENTRY AUTHORIZED? verdict. |
| Journal | Table, A2:C302 | Date/Ticker/Notes, freeform append-only log. |
| **Process Checklist** *(new 2026-09-08)* | Table, A4:F99 | One row per required step per recurring Journal event (tranches, quarterly reviews, fundamental refreshes). Due date and Done/OVERDUE/Pending status computed from a live lookup into Journal — see schema note below on why that lookup needs a real FK instead. |
| **Open Items** *(new 2026-09-11)* | Table, A4:F15 | Numbered, priority-ordered ad-hoc backlog. Simplest tab in the workbook; maps onto the schema below with no design questions attached. |
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

-- Process tracking (tabs added 2026-09-08/09-11, see "Revisited 2026-09-11")
recurring_event(label PK, event_type, due_date)   -- e.g. "DCA-CATCHUP-3", "QTR-REVIEW-1"
process_checklist_step(id PK, event_label FK, step_text, done_yn,
                       date_raised, date_done)
open_item(id PK, priority_rank, item_text, done_yn, date_raised,
         date_resolved, notes)
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
5. **Backend: local `sqlite3` file vs. Turso — decided 2026-08-24: Turso.**
   Turso is hosted libSQL (a SQLite fork) with sync/replication;
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

## Revisited 2026-08-24

Re-ran `python -m landry.migrate_to_db` against the workbook as it stands
today (45 tickers, up from 43; DIS/NFLX added, V/COST's Entry
Checklist/Implied-Return data filled in, Monitor & Recheck Triggers grown
to 45 rows) — this is exactly the "tab structure can drift" check the
"How to apply" note called for before resuming. It caught two real, live
reader bugs: `read_entry_checklist` and `read_monitor_notes` had
hardcoded `max_row=45` / `max_row=42` bounds dating to Phase A's original
(smaller) workbook snapshot, silently dropping DIS/NFLX from
`entry_checklist` (43 rows instead of 45) and the last 5 rows from
`monitor_notes` (40 instead of 45). Fixed by removing the bound on both —
unbounded `min_row=` scans, matching `read_scoring_tab`'s existing
pattern, are safe here since neither tab has trailing footnote text below
its real data (confirmed by direct inspection, the same failure mode that
originally required *adding* bounds to `read_market_data` /
`read_performance_tracking` during Phase A). Re-ran the migration after
the fix: `entry_checklist` and `monitor_notes` both now correctly show 45
rows. Change is in `landry/xlsx_io.py`, uncommitted as of this writing.

**Decision 5 (backend) is now settled: Turso**, not plain local sqlite3.
Direct motivation unchanged from the 2026-08-19 note — one shared live
database instead of Alan's and Taylor's local `landry.db` files
independently diverging, the same setup that produced the duplicate-DB-
layer collision (`taylor_landry_collaborator` in Claude's memory). Not yet
implemented: `models.py`'s `connect()` still opens a plain local sqlite3
file. Before Phase B work starts for real, still need: a Turso database +
auth token provisioned, secret handling decided (gitignored file vs. env
var, same treatment as `webapp_secret.key`), and the `libsql-client`
(or equivalent) dependency added.

**Sequencing (Phase B vs. C) — discussed, not settled. Timing — settled:
after the next tranche sequence.** Worth registering for next time: Phase
B alone (cutting CLI/webapp reads over to the DB) does not reduce the
openpyxl-edit-recalc pain that originally motivated this whole effort —
every real bug found across this entire multi-session engagement has been
on the *write* side, and only Phase C's DB-as-write-target flip touches
that. There's a real argument for doing minimal read-cutover and
prioritizing Phase C instead of the documented B-then-C order, since once
writes move to the DB, most reads naturally follow it too — a full
standalone Phase B first risks work that gets partly redone once C lands.
That specific ordering choice is still open.

What Alan did settle (2026-08-24): all DB work — deciding B-vs-C, standing
up Turso, and even creating the git branch it would live on — waits until
after DCA-TRANCHE-2 (rescheduled to 9/8/2026) and its fold-in verification
are done. A branch was proposed and explicitly deferred alongside the DB
work itself, not created early. Don't propose resuming before then;
check the Journal tab for a completed Tranche 2 entry as the signal.

## Revisited 2026-09-11 — gate cleared, this is prep, not cutover

**Status of this pass: design/validation work only, done on branch
`db-migration-prep` (a `git worktree`, separate directory from the live
repo) at Alan's explicit instruction — not merged, not touching the live
workbook.** Trigger: Tranche 2 executed 9/8/26 (the gate condition) plus
a full week (9/8–9/11) of tab-by-tab workbook review that surfaced a
concentrated, dated set of exactly the bugs this whole redesign exists to
eliminate. Alan's framing: "spreadsheet approach to maintenance/
enhancement proving more cumbersome... feeling like we should zero back
in on db development." He's about to travel with limited connectivity for
an extended stretch — deliberately not starting the actual cutover now,
which needs his active involvement in the still-open Phase B/C sequencing
call; this pass is about having a current, concrete design ready for that
conversation when he's back, not making the call in his absence.

**Tab count is now 21, not 18.** Two genuinely new tabs since this doc was
last touched (both built 2026-09-08/09-11, in response to the same class
of problem this doc addresses at the workbook-storage layer — Journal-
level and ad-hoc-tracking-level analogs of it):

- **Process Checklist** — one row per required step per recurring Journal
  event, with a live due-date lookup and an auto-computed Done/OVERDUE/
  Pending status. Maps directly onto this schema as a normalized table:
  `process_checklist_step(id PK, event_label, step_text, done_yn,
  date_raised, date_done)`, with `event_label` FK-joined to a proper
  `recurring_event(label PK, event_type, due_date)` table instead of
  Journal's free-text ticker column — which would have made the
  DCA-CATCHUP-1..8 label collision (below) a `UNIQUE` constraint
  violation at write time instead of a silent wrong-row match discovered
  by hand.
- **Open Items** — numbered ad-hoc backlog, `Done`/date/notes per row.
  Maps onto `open_item(id PK, priority_rank, item_text, done_yn,
  date_raised, date_resolved, notes)`. Straightforward; no design
  question here.

Also: the Piotroski F-Score cross-check columns added to Scoring
(cols AN-AS, 2026-08-25) were **tested on an 8-ticker batch and dropped
2026-09-11** as redundant (see Journal, search `PIOTROSKI-VERDICT`).
Historical data for those 8 tickers stays in the live workbook as a
record of the experiment. Recommend the new schema **not** carry a
dedicated `accruals_check`/`leverage_trajectory` column on `scores` or
`composite_history` — this was a tested-and-retired enhancement, not a
standing indicator, and modeling it as first-class schema would misstate
its status. If the historical data needs to survive the migration at all
(arguable either way — it never fed a live formula, confirmed by
inspection the same day it was dropped), a generic `notes`/`evidence`
field on the relevant `scores` rows is sufficient; it doesn't need its
own columns.

**Re-ran `python -m landry.migrate_to_db` against today's workbook (47
tickers, up from 45 on 8/24 — DIS/NFLX plus today's KGS addition) and
found a real, live bug, not just drift:** `read_scoring_tab` — the
function the 8/24 note explicitly cited as the *already-proven-safe
pattern* the other readers were fixed to match — turned out to have the
exact same "unbounded scan reads a footer as a bogus row" flaw itself.
Scoring's visible-ticker-count subtotal (`SUBTOTAL(103,...)`, a bare
number) lives at row 62, just past the tab's own documented row-60
formula/formatting headroom; an unbounded `read_scoring_tab` silently
returned a 47th "ticker" whose symbol was the string `"27"` (that
subtotal's current value). This is the **third** time this exact bug
shape has been found in this codebase (Phase A: `read_market_data`,
`read_performance_tracking`; 2026-08-24: `read_entry_checklist`,
`read_monitor_notes`; now `read_scoring_tab`) — each time in a reader
that looked safe until the workbook grew enough real rows to reach
whatever footer sits below them. Fixed the same way as the others
(bounded `max_row=60`, matching Scoring's own documented headroom).
Checked every other reader against its tab's actual current footer
position while already in there (Entry Checklist, Watch List Tracker,
Holding Monitor, Implied-Return Calculator, Process Checklist, Open
Items, Current Positions, Performance Tracking) — all clean, this was an
isolated case, not a second wave.

**This bug is itself the strongest concrete argument for Phase C that's
turned up since the doc's original writing.** A normalized `scores` table
queried with `SELECT ... WHERE ticker = ?` cannot accidentally include an
unrelated footer cell — there is no "read past the end of the real data"
failure mode in a relational table the way there is in an unbounded
sheet scan. Three independent instances of the identical bug, in three
different hand-written readers, over three separate sessions, is a
pattern a schema eliminates by construction, not something that needs
re-discovering and re-fixing a fourth time in whatever tab grows next.

**A second, independent case for the design, from the same week's work:**
adding KGS to Scoring with only 3 of its 5 Tier 1 indicators populated
(Moat and Revenue Visibility genuinely not yet assessed — Part 12,
correctly left blank) exposed a real formula bug in the live workbook's
`Tier 1 Wtd Avg` column: it only handled "all 5 populated" or "none"
correctly, silently treating missing indicators as zero-scored rather
than excluding them from the average, understating KGS's Tier 1 at 2.57
instead of its honest 4.0 on known indicators. Fixed in the workbook
(verified zero regressions across all 45 already-complete tickers) — but
worth naming explicitly here: this bug class cannot exist in the
`scores` schema above, because it's already normalized one-row-per-
indicator. "Average of the indicators that exist for this ticker" is
just `AVG(score) WHERE ticker = ? AND tier = 1`, weighted by whatever
indicators actually have rows — there's no fixed-width record to
zero-pad in the first place. The Excel version needed a human to notice
the formula's blind spot; the schema doesn't have the blind spot to
notice.

**`landry audit` (`landry/audit.py`, built 2026-09-08) didn't exist when
this doc was last substantively written and is worth naming here
explicitly:** it's a hand-built compensating control for exactly the
class of problem normalized storage removes structurally — Table ranges
vs. live data, cross-tab formula references pointing at stale rows/
columns, page-setup survival through a sheet rebuild. Once Phase C lands
(the xlsx becomes a generated report, no more hand-edited formulas or
manually-extended Table ranges), most of what `landry audit` checks for
becomes structurally impossible rather than something to keep checking
for. It doesn't become useless — page-setup/footer survival and Schema
Reference accuracy are about the *generated report's* correctness too —
but its cross-tab-reference and reader-bound checks specifically exist
to catch exactly the failure mode Phase C removes at the source.

**Sequencing (Phase B vs. C) — still the live open question, and this
week's evidence keeps landing on the same side.** The 8/24 note already
flagged that every bug up to that point was on the write side. Add to
that tally: the Scoring reader bug above (a read-path bug, actually —
the first one in a while that *isn't* write-side, worth being honest
about), the Tier 1 Wtd Avg zero-padding bug (write side — a formula, not
a reader), the DCA-CATCHUP-1..8 Journal label collision (write side — a
text-keyed lookup with no uniqueness constraint), and the recurring
Schema Reference / cross-tab-reference staleness that `landry audit` now
exists specifically to catch (write side). Four of five are still write-
side. The case for leaning toward Phase C sooner rather than the
documented B-then-C order hasn't weakened; if anything the Scoring
reader bug is a reminder that Phase A's migration code itself still
needs the same kind of re-validation this pass just did, on some
recurring cadence, regardless of which phase comes next.

**Not done in this pass, and deliberately out of scope for prep:**
Turso provisioning, the `libsql-client` dependency, any actual read or
write cutover, deciding B-vs-C. Those are cutover decisions, not prep,
and per Alan's own framing this pass exists specifically to avoid making
them while he's unreachable for weeks.

## Revisited 2026-09-30 — sequencing decided, resume queued for next session

Alan raised this unprompted ("looming need for use of db keeps coming
back") after tonight's session surfaced it again organically. Reviewed
this doc's own accumulated evidence with him — four of the five real
bugs logged across 8/18 through 9/11 are write-side (Tier 1 Wtd Avg
zero-padding, the DCA-CATCHUP-1..8 label collision, recurring Schema
Reference/cross-tab staleness that `landry audit` exists to catch), and
Phase B alone (read cutover only) would not have prevented any of them.

**Decision: prioritize Phase C over the documented B-then-C order.**
Alan's own framing: "sounds like C vs B is a no-brainer." Not yet
decided: whether that means C-only (skip a standalone Phase B entirely,
since writes moving to the DB pulls most reads along with them per the
8/24 note) or a minimal B as a stepping stone — that detail is for the
actual planning session, not resolved here.

**Explicitly not starting now.** Alan's call: flag it and pick this up
fresh next session, rather than start mid-flow tonight. Two concrete
things the next session should do before writing any migration code,
per this doc's own recurring lesson (every re-validation pass has found
a live bug from workbook drift):

1. Re-run `python -m landry.migrate_to_db` against `main`'s current
   workbook shape — this branch is frozen at 9/11 and `main` has since
   added GE/NFLX (Current Positions structural surgery), Rule 33 and its
   Watch List Tracker column, the Fidelity info-tab scan practice and
   `Process Checklist` step, and several Darryl-list screening rounds.
   Assume another reader-bound or cross-tab surprise turns up; it has
   every previous time.
2. Decide the C-only-vs-minimal-B question above, then provision Turso
   (account, auth token, secret handling, `libsql-client` dependency) —
   still not done from the 8/24 decision.

## Revisited 2026-10-01 — re-validation done, C-only decided, Turso deferred

**Step 1 above, done.** Re-ran `python -m landry.migrate_to_db` against
`main`'s current workbook (pointed the script directly at the main
checkout's live `LANDRY_SYSTEM_WORKBOOK_25.xlsx`/`landry_scores.json` via
its own path arguments, rather than copying anything into this worktree).
Found a fresh bug exactly as predicted — `read_price_history` had no row
bound and choked on Price History's "NN Weeks" footer, the fourth
occurrence of the unbounded-reader-treats-a-footer-as-data class. Fixed
with the same content-based-guard pattern as `read_scoring_tab` (real
rows have a `datetime` in col A, the footer has a bare int). Tracing it
also surfaced a genuine **data** bug in the live workbook, not just a
reader bug: two Price History rows both dated 9/4/26, the second
column-shifted relative to the first and actively feeding `Returns
(Calc)` → Correlation Matrix → the Rule 38 check. Alan's read: an
artifact of the original hand-entered 3-year price migration, not worth
forensically reconstructing — fixed by clearing the bad row outright
(zero formula rewrites needed; the existing `ISNUMBER` guards and the
live `COUNT()` footer both self-corrected). Full detail: CLAUDE.md,
Journal row 72 in the main workbook.

**Step 2, decided: C-only, not a separate Phase B.** The reasoning,
worked through directly rather than left as an open question: Phase B's
whole value is "point reads at the DB instead of the xlsx," but nothing
that has actually gone wrong in this project would be prevented by that
— every real incident (Tier 1 Wtd Avg zero-padding, the DCA-CATCHUP
label collision, Schema Reference drift, today's Price History row) came
from the xlsx still being the thing that gets hand-edited. Phase B alone
doesn't touch that risk surface at all — the xlsx stays the write
target, and the DB would just be a derived copy re-migrated periodically
(literally what today's re-validation pass already does ad hoc). The
read-from-DB benefit only becomes real once Phase C flips the xlsx to a
generated report, at which point Phase B's read-paths are needed as a
direct consequence of C, not a separate thing to build and then partly
redo — exactly the risk the 8/24 note already flagged, now with four
more write-side incidents backing it up.

**Refinement adopted in place of a separate Phase B: roll out Phase C
itself incrementally, tab by tab, instead of as one big-bang cutover.**
This gets the real benefit a "minimal B" was reaching for (de-risk before
committing to the whole thing) without building something that gets
superseded. Order: **Journal and Portfolio Drawdown Log first** — no
real formula complexity (freeform text; a simple regime state machine)
— then the formula-heavy, bug-prone tabs (Scoring's computed columns,
Correlation Matrix, Returns, Action Items, Dashboard) once the pattern's
proven on low-stakes tabs.

**Backend timing, also decided: local SQLite first, Turso later, not
Turso from day one.** The 8/24 Turso decision was specifically motivated
by avoiding Alan's and Taylor's local DBs diverging ([[taylor_landry_
collaborator]]-style collision risk). Taylor is currently suspended from
repo contributions until the workbook is verified-settled, so that
specific risk is dormant, not active, right now — re-weighed this
explicitly with Alan rather than assuming the original urgency still
holds unchanged. Alan confirmed he and Taylor have already discussed
this directly: Taylor's own assessment is that SQLite → Turso migration
is straightforward at the appropriate time, which independently backs
local-first. Plan: build and validate Phase C's generation logic against
a plain local SQLite file (faster iteration, no account/token setup in
the way of early development), provision Turso once that logic is
actually proven on real tabs — not before there's anything real to put
in it. `models.py`'s `connect()` can stay a plain local sqlite3 file for
this stage; the `libsql-client` dependency and Turso account/token work
are now explicitly deferred to the point where Journal + Drawdown Log
generation is working and validated, not before.

**Decided: 2026-10-01 is the planning/decision session, full stop — no Phase C code this session.** Alan's call: "Let's treat today as planning; start fresh next time." Everything above (C-only, incremental-by-tab, Journal + Drawdown Log first, local SQLite first) is the settled plan; the next session's job is to actually start writing it, not to re-litigate it. Concretely, next time: build the Phase C generation logic for Journal first (simplest — freeform text, no formulas to replicate), get it producing correct output against a local sqlite3 file, validate it matches the live tab exactly, then do the same for Portfolio Drawdown Log before touching anything else.

## Phase C, step 1 — Journal generation built and validated (2026-10-02)

First increment of the plan above, done the next morning as planned. `landry/generate.py`
regenerates the Journal tab's data region from the database. **Not merged and not
cut over** — the xlsx is still the Journal's source of truth. What exists is the
generator and the proof that it reproduces the live tab.

**What changed.**
- `generate.py` (new): `generate_journal(conn, ws)` works in memory;
  `generate_workbook()` is load → generate → save → mandatory recalc;
  `verify_journal()` regenerates in memory and diffs against the tab as it stands.
  CLI: `python -m landry.generate journal|verify --workbook W --db D`.
- `models.py`: `journal(id, date, label, notes)`. `label` is free text and is no longer a
  foreign key to `tickers` — the old schema made the migration invent a "ticker" for
  every event label (`DCA-CATCHUP-1`, `AZN,INTU`). Adds `journal_add`, `journal_rows`,
  `iso_date`. Order is `id` order = write order, never date order.
- `migrate_to_db.py`: Journal goes through `journal_add`, and `migrate` now **refuses an
  existing database file** unless `--overwrite`. Most tables have no natural key, so a
  re-run silently duplicated every row; once the DB is the Journal's source of truth, a
  re-run must not be able to double or clobber it.
- `xlsx_io.read_journal`: the fifth hardcoded-bound reader (`max_row=302` would silently drop
  every entry past the pre-formatted rows). Now unbounded, returns values verbatim (no
  strip), and raises on a row with content but no real date instead of skipping it.
- `tests/test_landry_generate.py`: 20 tests, including a round-trip of the repo's own workbook.

**Decisions, and why.**
1. *The generator owns the data region, not the whole tab.* Title row, header, column widths,
   sheet view and page setup stay in the workbook; other tabs read Journal by column
   (Process Checklist: `INDEX(Journal!$A:$A, MATCH(label, Journal!$B:$B, 0))`), so that layout is
   a contract. This is what "incremental by tab" means in practice: fill a skeleton, don't
   rebuild the file.
2. *Formatting is generated, not copied.* The canonical look lives in code and is re-applied
   on every run, so a hand-toggled wrap or font can't leak into a commit. Alan's compact-view
   habit (turn wrapping off to scan rows, back on before committing) is the motivating case:
   forgetting the second step is now harmless. The spec mirrors the live tab as of 10/2 with one
   deliberate change — column B wraps on every row (live: 35 of 300), since the long labels
   (a 54-character ticker list in row 58) were clipped.
3. *Row heights are derived layout, never stored.* Data rows are written without a height; the
   recalc pass fits them and flags them auto-height, which also makes Excel's wrap-off trick compact
   them. Only a note taller than Excel's 409.5pt ceiling (row 61: 538.8pt natural) is pinned, by
   measuring after the first pass and recalculating once more. An earlier estimator capped 7 rows
   and was dropped. (Live rows 54–74 had one-line heights: a compact-view reset, not a bug.)
4. *Refuses to do damage:* an empty `journal` table against a tab with entries raises rather than
   blanking the log (wrong database), and a missing `--db` raises instead of letting sqlite create an
   empty one. A note beginning with `=` is written as text, not a formula.

**Validation, against `main`'s live workbook (72 entries).** `verify`: zero value differences
across every date, label and note; the only formatting difference is column B's wrap (265 cells,
the deliberate change). End to end — regenerate, recalc, compare against an untouched control
through the same pipeline: **25,356 cells across all 21 sheets, 0 differences**, so downstream
formulas (Process Checklist's due-date lookups) are provably unaffected. Growth: 305 entries
extends the Table, filter and print area to row 307 and Schema Reference's row-range note; the
audit's `table_ref` (10/10) and `schema_ref` (20/20) pass. Suite: 378 passed; the 4 failures are
identical on a pristine export of `HEAD` (stale tests against the older tracked workbook).

**Not done — needed before this can be cut over.**
- The write path: `journal_add` exists, but there is no CLI/skill step yet, no way to *edit* an
  entry (CLAUDE.md says superseded entries get marked in place), and no git-tracked, diffable
  backup of the table (decision 2 above — `landry.db` is gitignored).
- Where the DB lives across machines before Turso: a fresh checkout has no `landry.db`, so the
  committed workbook must stay regenerable-from and re-migratable-into the DB.
- This branch is 22 commits behind `main`, with `xlsx_io.py`, `audit.py`, `export.py` and
  `__init__.py` differing — merge or rebase first.

**Next tab: Portfolio Drawdown Log (recon only, nothing built).** Inputs are just Date, Portfolio
Value and Notes; Running Peak, Drawdown %, Status, Required Cash Floor and New Position Initiation
are five live formulas in all 40 pre-built rows. Action Items reads the Status column
(`INDEX(...E3:E42, COUNT(...D3:D42))`). `drawdown.py`'s regime tracker has *different* semantics
(5-day entry / 10-day exit debounce on daily values) from the sheet's instantaneous -10/-20/-30%
bands — don't substitute one for the other. The DB should store inputs only and the generator
should write the same formulas row-relative (no Python re-implementation to drift), keeping static
values for the whole-workbook cutover at the end of Phase C.
