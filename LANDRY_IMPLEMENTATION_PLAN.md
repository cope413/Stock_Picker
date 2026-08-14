# Landry System → Automated Stock Picker: Implementation Plan

**Driving documents:** `LANDRY_SYSTEM_v1-01_final.docx` (v1.0, 49 Hard Rules) and
`LANDRY_SYSTEM_WORKBOOK_25.xlsx` (live workbook: 40 scored names, ~$700k across
JT ULTRA/Fidelity and Self-Directed/Chase).

**Decisions made:** app-owned SQLite DB with Excel import/export · AI drafts
scores, analyst approves (Part 12 boundary preserved) · new Landry tabs in the
existing `webapp.py`.

**Supersedes:** the v7 framework (`v7_scoring.py`, `part11_tracker.py`, `docs/`).
Landry v1.0 is its successor; both modules become the starting point for the new
engine, not parallel systems. Within the workbook line, `LANDRY_SYSTEM_WORKBOOK_25.xlsx`
supersedes `LANDRY_SYSTEM_WORKBOOK_11.xlsx` — same structure, positions, and
worked values for every previously-pinned ticker, plus the later screening
pass's six additional scored candidates (Nike, V.F. Corp, Canada Goose, Advance
Auto Parts, Alibaba, Etsy) added to the Scoring tab. All references below now
point to Workbook 25; Workbook 11 is retained only as a superseded snapshot.

---

## Guiding constraints (from the doc itself)

1. **Automation boundary (Part 12).** Only raw market-data inputs are automated.
   Moat, Management Quality, Revenue Visibility, and the qualitative half of FCF
   Yield & Trend are analyst judgments. The app may *draft* them with cited
   evidence, but a score is not usable until explicitly approved.
2. **Hard Rules are code, not suggestions.** All 49 rules become pure functions
   with tests. No discretionary override path in the software (Part 1.5).
3. **Conflict resolution order (Part 10)** — fundamental sells → legal/tax
   compliance → hard caps/drawdown → entry eligibility → sizing → replacement →
   tax optimization — is encoded as the rule engine's evaluation order.
4. **Auditability (Part 9).** Every decision the app surfaces writes a journal
   record: inputs, rule results, evidence, timestamp. The workbook's Journal tab
   becomes an append-only DB table.

---

## Architecture

```
landry/
  models.py        # SQLAlchemy/sqlite3 schema (below)
  rules.py         # Hard Rules 1–49 as pure functions  ← extends v7_scoring.py
  scoring.py       # Composite score, tiers, confidence, decision bands
  data_auto.py     # Automatable inputs (prices, RS, beta, technicals, correlations)
  fundamentals.py  # FCF, revenue, margins, ROIC, Debt/FCF from filings data
  implied_return.py# Part 4 standardized implied-return + Bull/Base/Bear checks
  portfolio.py     # Sizing (Part 5), caps, cash floor, drawdown regimes (Part 7)
  monitor.py       # Sell triggers (Part 6), watch-list clocks, review schedule (Part 9)
  ai_analyst.py    # Draft scores + evidence via Claude API (never auto-final)
  xlsx_io.py       # Import from / export to LANDRY_SYSTEM_WORKBOOK_*.xlsx
  cli.py           # landry score/check/monitor/export commands
landry.db          # SQLite (gitignored)
```

`webapp.py` gains a **Landry** section: Dashboard, Score, Entry, Holdings,
Risk, Journal tabs. Data layer reuses the Layer-1 yfinance cache.

### Database schema (mirrors the workbook's Schema Reference tab)

`tickers`, `scores` (indicator × score × confidence × evidence × approved_by/at),
`composite_history`, `positions` (multi-account, lots for Part 8), `entries`
(checklist results), `scenarios` (Bull/Base/Bear + tags), `watchlist` (status,
band-entry date, 90-day deadline), `sell_triggers`, `correlations`,
`drawdown_log` (with regime state machine), `journal`, `action_items`.

---

## Phases

### Phase 1 — Rule engine + scoring core (the foundation)

Port and extend `v7_scoring.py` to Landry v1.0:

- Composite Score `(Σ score×weight)×20` with the v1.0 weight set (FCF Yield 20,
  Rev Growth 15, Moat 15, Rev Visibility 10, FCF Margin 10, Mgmt 8, ROIC 6,
  Valuation 8, RS 3, Technical 2, Analyst 2, Volume 1).
- Rules 1–4 (Tier 1 floor 3.0, mandatory review on any 1, auto-Avoid on two ≤2,
  Low-Confidence cap at Buy) and decision bands (80/65/50/35).
- Confidence tagging (H/M/L) as first-class data on every indicator.
- Sector-adaptation flags (Part 10): financials, REITs, utilities, early-stage,
  commodity — each substitution recorded beside the score; unadapted sectors
  forced to Low Confidence, Strong Buy blocked.
- **Test suite pins every worked number in Workbook 25** (e.g. NVDA 93.2 Strong
  Buy, MU 71.8 Buy, TSLA Tier 1 = 1.93 → FAIL/REVIEW/AVOID — unchanged from
  Workbook 11, which it supersedes), the way `test_v7_scoring.py` pins the
  PART8 record.

Deliverable: `landry score NVDA` reproduces the workbook exactly.

### Phase 2 — Data automation (the automatable layer)

Everything Part 12 permits, from the existing yfinance cache plus a fundamentals
source (yfinance financials first; pluggable interface for a better provider
later):

- Market Data tab equivalents: price, volume, market cap, P/E, 52-wk range,
  dividend yield.
- Weekly price history → weekly total returns → pairwise correlation matrix
  (Rule 36 windowing: ≤36 months, 24 target, 12 minimum) → cluster flags (3+
  holdings pairwise >0.70, 20% aggregate cap check, 50% staging on expansion).
- Portfolio drawdown tracker (Part 7): daily portfolio value from positions,
  high-water mark, regime state machine (5-day entry / 10-day exit hysteresis),
  required cash floor, cash-raising-waterfall checklist.
- Technicals for Rules 8–9 and Tier 3: 200-week MA position/reclaim, monthly
  MACD, Supertrend, A/D line.
- Relative Strength vs SPY (or sector ETF) 6–12 mo total return; 5-yr weekly
  beta with 2-yr fallback (Rule 20 sizing overlay).
- Computable fundamentals with the Part 10 definitions (FCF = CFO − capex − SBC
  grant value; Debt/FCF = net debt / normalized FCF; revenue CAGR + CV).
- Macro-overlay inputs (Part 7): Fed cycle, 2s/10s inversion duration, HY
  spreads, SPY vs 200-week MA — fetched and mapped to their prescribed effects.

Deliverable: `landry refresh` updates all objective columns; drafts of the
*quantitative* indicator scores (FCF yield level, RS, beta, technicals,
valuation multiples vs history) computed per rubric.

### Phase 3 — AI analyst drafts (human-in-the-loop)

`ai_analyst.py` builds a per-ticker evidence pack (filings summaries, metrics
from Phase 2, moat cross-check tests a–d, capital-allocation history) and calls
the Claude API to propose 1–5 scores **with rubric citations and a confidence
tag** for the judgment indicators. UI shows draft vs approved side by side;
nothing enters the Composite until the analyst clicks approve (recorded with
name + timestamp per Part 9 documentation standards). Data-source hierarchy
(Part 10) enforced in the evidence pack: filings > IR > consensus > research >
commentary, sources recorded per input.

Deliverable: scoring a new candidate drops from hours to a review session.

### Phase 4 — Entry, sizing, and exit workflows

- **Entry Checklist (Rules 5–13):** automated where possible (5–9, 13 from
  Phases 1–2), guided forms for judgment items (Rule 10 binary events, Rule 11–12
  scenarios). Implied-Return Calculator ported: Year-5 FCF/sh × terminal
  multiple (capped at min(current, 5-yr median)) + distributions; Base >10%,
  Bear ≥0%, exactly one Likely tag — hard blocks, not warnings.
- **Sizing (Rules 14–20):** max initial/full by band, all five modifiers, sector
  cap 25%, 12-position floor logic, 5–15% cash band, Debt/FCF >5x score cap,
  beta overlay, macro-overlay adjustments. Output: "authorized size: X% ($Y),
  staged at 50% because …".
- **Exit discipline (Rules 21–35):** Holding Monitor with prior/current
  indicator flags, valuation triggers, Hold Through guard (explicit — the app
  must refuse to recommend selling on noise), 30-day clocks, Probationary
  Hold / Exit Review state machine with 90-day deadlines, opportunity-cost
  replacement gate (15-pt gap × 2 quarters, once/12mo per holding).
- **Tax awareness (Rules 38–42):** lot-level positions, long-term-status
  countdown, wash-sale flag on harvest candidates, "how not whether" enforced —
  tax logic can only delay/route an already-justified trade.

Deliverable: the Action Items tab as a live app view — every open decision with
its rule citation and deadline.

### Phase 5 — Web UI + daily operations

- **Landry Dashboard:** verdict strip (portfolio value, drawdown regime, cash
  floor vs actual, Rule 36 status, open action items), sortable score table.
- **Score / Entry / Holdings / Risk / Journal tabs** over the Phase 1–4 engines.
- **Scheduled monitoring:** daily refresh job → recompute triggers, deadlines,
  drawdown regime; surface changes as action items. Quarterly/annual review
  scheduler (Rules 43–47) with post-mortem prompts after >20% losses.
- **Performance benchmarking (Part 9):** cohort tracking by decision band vs the
  12%/10% objectives and SPY, from original decision date, exits included.
- **Excel round-trip:** `landry export` fills a copy of Workbook 25 (Schema
  Reference tab is the contract); `landry import` seeds the DB from it —
  one-time migration plus ongoing escape hatch.

### Phase 6 — Verification & hardening

- Full rule-register test matrix: one test per Hard Rule minimum, plus the
  Workbook-25 pinning suite and conflict-resolution ordering tests.
- Property tests: no path lets a lower-ranked rule override a higher-ranked one;
  no judgment score reaches the Composite unapproved.
- Dry-run month: app output vs manual workbook side by side before retiring
  manual entry.

---

## Sequencing & effort

| Phase | Scope | Status |
|---|---|---|
| 1 | Rule engine + scoring, pinned tests | ✅ done — `landry/scoring.py`, `xlsx_io.py`, CLI `score`; pinned to Workbook 25 |
| 2 | Data automation | ✅ done — `data_auto.py`, `drawdown.py`, `fundamentals.py`, `macro.py`, CLI `refresh` |
| 3 | AI drafts | ✅ done — `ai_analyst.py`, `approvals.py`, CLI `draft`/`pending`/`approve`/`reject` |
| 4 | Entry/sizing/exit workflows | ✅ done — `implied_return.py`, `entry.py`, `sizing.py`, `monitor.py` |
| 5 | Web UI + scheduling + Excel I/O | ✅ done — Landry web tabs, `daily`/`export`/`import` CLI, `performance.py` cohorts |
| 6 | Verification | ✅ code-complete — Master Rule Register matrix (all 49 rules mapped; 45/47 declared procedural), ordering + Part 12 property tests, gap rules 38–42/48–49 implemented (`tax.py`, `financials.py`); 305 tests. **Remaining: the one-month dry run** — run `landry daily` and the web UI alongside manual workbook scoring before retiring hand entry. |

Phases 1→2 are strictly sequential; 3 and 4 can proceed in parallel after 2.

## Known risks

- **Fundamentals data quality.** yfinance fundamentals are shallow/inconsistent;
  the Part 10 FCF definition (SBC-adjusted, normalized) needs filings-grade
  data. Mitigation: pluggable provider interface, Low Confidence auto-tag when
  inputs are thin.
- **Regime/state correctness.** Drawdown hysteresis, 90-day clocks, and the
  replacement gate are stateful; they get dedicated state-machine tests.
- **Scope creep into auto-trading.** The system produces *decisions and sizes*,
  not orders. Broker execution stays manual by design (and by the doc).
