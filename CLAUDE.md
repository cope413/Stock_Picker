# Stock_Picker — working notes for Claude

This file travels with the repo on any device. If you're picking this up in a
fresh session (new machine, new Claude Code terminal) with no memory of prior
work, start here, then read the last ~15-20 rows of the Journal tab, the Open
Items tab, and Process Checklist for current state — those three are the
living record of what's actually happening and why. Don't assume you have
access to any prior conversation history or accumulated memory beyond this
file and the repo's own contents.

## Mandatory, every time

- **Run `python -m landry.xlsx_recalc LANDRY_SYSTEM_WORKBOOK_25.xlsx` after every single save**, even a pure styling/no-formula change. openpyxl wipes cached formula values workbook-wide on save; skipping this makes the workbook look broken (blank cells) when it isn't.
- **Run `python -m landry audit` before committing anything that touches the workbook.** Checks Table ranges vs. live data, hardcoded reader bounds in `landry/`, cross-tab formula references, page setup/footer survival, and Schema Reference accuracy. A clean run doesn't replace judgment on whatever you just changed — it's a floor, not a substitute for actually checking your work.
- **Never commit without the user explicitly saying so in that instance.** Approval doesn't carry over from a prior commit.

## Never touch

- Any file with "copy" in the name (e.g. `..._copy.xlsx`, `TAB UPDATING..._copy.docx`) is the user's own scratch/viewing copy — never read it as a source of truth or write to it, *unless* the user explicitly names it and asks you to look at it (that overrides the default, since there's no risk of confusing it with a canonical file at that point).

## File conventions

- New `.xlsx` deliverables: Arial Narrow, 10pt. New `.docx` deliverables: Calibri, 11pt. Not retroactive — match whatever an existing file already uses.
- A full-teardown-rebuild of a worksheet (`wb.remove()` + `wb.create_sheet()`, the standard pattern for any structural change) starts with **zero** page setup. Explicitly capture and restore `page_setup` (paperSize/scale/orientation), `page_margins`, and `oddFooter`/`oddHeader` (text+font+size per section) from the last git commit or another already-correct tab — not just font/fill/column-widths. This has been missed and had to be re-fixed more than once; neither recalc nor a value diff will ever catch it.
- When a rebuild shifts a tab's column or row positions, grep every *other* tab for `'<TabName>'!` references before calling it done — a within-sheet check never surfaces a stale cross-tab reference. `landry audit` catches most of this automatically now, but it doesn't catch every case (e.g. a formula that still resolves to a *valid but wrong* cell after a column shift — see the Recommended Action bug, or the DCA-CATCHUP-1..8 label collision, both found by hand).
- `ws.tables.items()` in this openpyxl version yields `(name, ref-string)`, not `(name, Table object)` — use `ws.tables[name]` (bracket access) to get the real Table object with `.ref`, `.tableColumns`, etc.
- Journal is a **decision log**, not living documentation — entries reflect what was true and decided *at the time they were written*. Don't retroactively edit an old entry to match current reality (that erases the record); add a new entry that references the old one instead. A stale fact in an old entry (e.g. a row number that's since shifted) is expected aging, not a bug — unless the entry's *substance* (a decision, a rationale) has actually been superseded, in which case mark it superseded in place rather than silently leaving it to look current.
- When a Journal event label gets reused for a revised plan (e.g. replacing a 12-tranche schedule with an 8-tranche one that reuses `DCA-CATCHUP-1..8`), relabel the *old* entries first (a `-V1` suffix or similar) before writing new ones with the clean label — Process Checklist's due-date lookup keys off Journal's ticker/label column by exact match, and two rows with the same label will silently resolve to whichever one comes first.

## Standing tools (all built 2026-09-08 through 2026-09-11)

- **`python -m landry audit`** (`landry/audit.py`) — structural drift checker for the workbook itself.
- **Process Checklist tab** — one row per required step for every recurring Journal event (tranches, quarterly reviews, fundamental refreshes). `Status` auto-flags `OVERDUE` once the event's Journal date passes with a step unchecked. Whenever Journal's calendar gets a new recurring event (or an existing one's steps change), reseed this tab in the same pass — the seed script is not currently checked into the repo, it's been rebuilt ad hoc each time; consider writing a real one if this keeps recurring.
- **Open Items tab** — numbered, priority-ordered backlog for ad hoc (non-recurring) open questions. Mark items Done with a date and a one-line pointer to the Journal entry with full reasoning; don't delete resolved rows.
- **`landry_scores.json`** (via `landry.approvals.ScoreStore`) — the Part 12 approval audit trail. Any ticker score finalized conversationally (not through `landry draft`'s CLI path) needs to be logged here too (`propose` + `approve`) or it's silently missing from the audit trail despite being live in the Scoring tab. This has already happened once (V, Sept 2026) — there's no tooling that catches the omission, it's a discipline habit only.

## Current standing decisions worth knowing before touching related areas

- **Piotroski F-Score cross-checks (Scoring cols AN-AS): DROPPED**, tested on an 8-ticker batch, confirmed redundant. Historical data for those 8 tickers stays in place as a record. Don't populate further tickers; don't propose reviving without genuinely new evidence (see Journal, search for `PIOTROSKI-VERDICT`).
- **ETF treatment: still an open policy question**, not resolved. Current holdings (AVUV/SPMO/VFLO/DVY/SCHD/VYM/MLPI/JEPQ/HELO/JEPI) are treated as dry powder for now (a tactical call), but "how should ETFs formally be treated as portfolio positions" is a genuine to-do, grouped with database-migration work as "later." Don't describe this as settled.
- **DCA deployment**: search Journal for `DCA-CATCHUP-PLAN-2` for the current (as of 2026-09-11) cadence, sizing, and reasoning. An S&P-ATH-based acceleration policy also applies on top (search `DCA-ACCELERATION-POLICY`) — the portfolio's own Drawdown Log regime overrides it if Severe/Critical.
- **Database migration**: Phase A shipped; Phases B/C deliberately paused, gate cleared 2026-09-08, not yet resumed. Don't propose resuming unprompted.
