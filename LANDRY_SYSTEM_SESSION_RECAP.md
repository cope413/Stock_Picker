# The Landry System — Session Recap
### From Rules to Running Portfolio: Key Findings, Surprises, Processes, and Resources

---

## PHASE 1 — Building the System (the Rules)

**What happened:** Reviewed and consolidated conflicting rule versions (v9.4 vs v9.5, 22 matched / 11 differed / 24 missing), then rebuilt cleanly as **Version 1.0** — 49 Hard Rules across 12 Parts, sequentially renumbered by topic rather than by append-order.

**Key finding worth remembering:** During the rebuild, three operationally important rules (Exit Review, Probationary Hold, Composite<35 mandatory sell) were nearly dropped when content got split into new Parts — caught and restored before finalizing, not after.

**Resolution:** Alan's own manual edits (Index merged into the main document, Appendix populated with the workbook example) were compared line-by-line against the working copy and verified as a **clean superset — no rule logic changed, nothing lost.** Finalized as **System v1.01** — the standing reference copy.

---

## PHASE 2 — Building the Workbook (17 → 20 tabs)

**What it does:** Scoring (Tier 1/2/3, Composite, Rules 1-4 flags), Current Positions, Watch List Tracker, Entry Checklist, Implied-Return Calculator, Holding Monitor, Performance Tracking, Price History/Returns/Correlation Matrix, Portfolio Drawdown Log, Monitor & Recheck Triggers, Action Items, Journal, Schema Reference.

### The most important recurring lesson of the entire session: **a clean recalc is not proof of correctness.**

- **The Excel Table + merged-cell bug happened twice**, not once. A merged "note" cell sitting inside a Table's defined range passes LibreOffice's recalc validation with zero errors — but Excel's own repair mechanism catches it and silently strips the Table. First occurrence: Current Positions and Performance Tracking. Second occurrence: the Dashboard tab, introduced while fixing something else in the same edit. **The fix both times was the same** (shrink the table range to exclude the merged row) — but the second occurrence happened because the standing audit script wasn't run before delivering, not because the fix was unknown.
- **A systematic input-cell mismarking gap**: cells where real data had been written (as opposed to left blank for future entry) were styled black like formulas, even though they were genuine manual inputs. Found and fixed by defining input columns by *design*, not by whatever color a cell happened to already be — touching 7,557 cells across 12 tabs in one pass.
- **Two separate COUNTIF bugs**: `COUNTIF()` doesn't accept a comma-separated list of non-contiguous cells as its range argument — caught via testing real scenarios, not via the recalc tool, which didn't flag it.
- **An `AND()` non-short-circuit bug**: Excel evaluates every argument of `AND()` regardless of earlier ones failing, so a blank-cell guard combined with arithmetic on that same cell still threw errors. Fixed by restructuring as nested `IF`s.
- **A silent DIV/0 fragility**: percentage-of-account formulas divided by a SUM that could reach zero (e.g., if every position in an account were sold) — invisible until the blank workbook template was built and exposed it.

**Standing resource:** `recalc.py` (LibreOffice-based) is mandatory for every change, but its limits are now explicit — it catches formula errors, not Table/merged-cell structural issues. Any Table modification now gets a dedicated merged-cell-overlap audit before delivery, not just a recalc pass.

---

## PHASE 3 — Portfolio Review & Rebalancing (real money, real accounts)

**What happened:** Aggregated two real brokerage accounts (JT ULTRA/Fidelity, Self-Directed/Chase) into Current Positions — ~$700K combined.

**Bugs caught before they reached Alan:**
- A **row-collision bug** where a subtotal row and the first data row of the next account landed on the same line, silently overwriting real position data.
- A **grand-total double-count**, where a blind contiguous SUM range accidentally re-counted a subtotal row sitting in the middle of it.

**Real decisions executed:**
- **SFM, TSLA, ONON sold** — all three triggered Rule 3 (2+ Tier 1 indicators ≤2, automatic Avoid).
- **VFLO mislabel caught**: described as "Vanguard US Multifactor ETF," actually **VictoryShares Free Cash Flow ETF** — a completely different fund, provider, and market-cap segment. This reframed an earlier "three redundant small-cap funds" conclusion — VFLO wasn't redundant with AVUV/CALF at all.

---

## PHASE 4 — Correlation & ETF Analysis: the most important reversal in the whole session

- **Stock-only correlation came back clean** — zero Rule 36 violations across all 13 held stocks, despite a strong prior expectation (stated explicitly, on the record) that it would come back problematic given the AI/semiconductor concentration.
- **Adding the 7 held ETFs completely reversed that conclusion**: 8 of 20 holdings suddenly violated the correlation cap. **VGT** was the worst offender (5 violations) — it was quietly re-buying NVDA/AVGO/TSM exposure already held directly. **JEPQ and SPMO** were both heavily loaded with the same mega-cap names despite different nominal strategies. **AVUV/CALF/VFLO** turned out to be a false story — AVUV and CALF were genuinely redundant with each other (0.81-0.92 correlation), but VFLO (once correctly identified) was a legitimately different, large-cap FCF-focused fund.
- **Action taken:** VGT sold, CALF consolidated into AVUV (chosen over CALF on the evidence — better flows, better recent performance, broader diversification, no in the questionable-holdings problem CALF showed). **HELO identified as the only genuinely differentiated ETF** — zero correlation violations, consistent with its hedged-equity design.

**The lesson stated plainly at the time, worth repeating here:** a partial check (stocks only) that comes back clean can be actively misleading if it's treated as the whole answer. The System's insistence on checking the full picture — including instruments, not just individual names — is what caught this.

---

## PHASE 5 — DCA & Calendar Discipline

- Cash was **49.8% of the combined portfolio** at the point this was addressed — far above the 5-15% target band.
- Built a **DCA + regime-gating mechanism**: monthly tranches, but each tranche checks Portfolio Drawdown Log's regime first — Normal deploys the full tranche, Elevated restricts to Strong Buy names only, Severe/Critical skips the tranche entirely (Rule 18's "don't force it" principle, made mechanical).
- Tranche 1 executed (~$26.5K, weighted toward the portfolio's actual diversifiers — VRTX, PLD, ADBE — specifically to avoid deepening the correlation problem while fixing it).
- **Quarterly reviews standardized to calendar quarter-ends** (9/30, 12/31, 3/31, 6/30) rather than anchored to trade dates, specifically because trade-date anchoring silently drifts if a tranche is ever skipped. **Fundamental refreshes separated out** to ~3-4 weeks after each quarter-end, so re-scoring uses actual reported earnings rather than stale numbers.

---

## PHASE 6 — Extensive Candidate Screening: the reversals worth remembering most

Three names **failed, then genuinely passed on re-examination** — not because the bar moved, but because the evidence changed or an earlier read was wrong:

| Ticker | What changed |
|---|---|
| **ASML** | Guidance raised twice (to €43-45B); backlog extended to 2027 — genuine improvement in Revenue Visibility and Growth Consistency |
| **MU** | Landed binding multi-year contracts (including a named Anthropic supply partnership) — same mechanism as ASML |
| **VRTX** | The original Avoid was **my own error** — a source (MacroTrends) showed internally inconsistent FCF figures, and I scored it without resolving the inconsistency first. Reconstructing FCF from primitive OCF/CapEx data showed strong, growing cash generation the whole time |

**The MacroTrends FCF pattern recurred a second time (ETSY)** — same garbled "$0B, 100% decline" phrasing. Caught faster the second time because the VRTX lesson was already learned: **verify FCF from primary filings (OCF − CapEx) whenever a summarized figure looks internally inconsistent, never trust the summary at face value.**

**GOOS (Canada Goose)** — the clearest illustration of why multi-indicator scoring matters: revenue grew 13.3%, which would pass a naive screen. Operating income fell 45.9% in the same year — "the DTC profit trap." A screen that stopped at revenue growth would have missed this entirely.

**Sector-by-sector pass rates, showing within-sector differentiation is the real finding, not sector labels:**

| Sector | Result |
|---|---|
| Energy | 1/4 (ET passed — contracted midstream vs. commodity E&P) |
| Basic Materials | 1/3 (LIN passed — industrial gas oligopoly vs. leveraged turnarounds) |
| Consumer Defensive | 1/4 (PG passed — three household names failed despite brand reputation) |
| Restaurants | 3/4 (asset-light franchise model is a strong structural fit) |
| Apparel | 0/3 (Nike, VF Corp, Canada Goose all failed, for three different reasons) |
| Retail/e-commerce | 1/3 (Etsy passed) |

**Running bench of passing candidates**, not yet all fully deployed: Visa, Costco, Energy Transfer, Linde, Chipotle, Domino's, Yum! Brands, Etsy — alongside the 13 originally-held, currently-passing stocks.

---

## PHASE 7 — A Late, Small, Instructive Bug: HELO's Trendline

Built bar+trendline combo charts for all 18 tracked tickers. **HELO's chart showed a sharp artificial decline to $0** — not real performance, but a blank cell (HELO's price data has a genuine gap at the end of its series) being treated as zero in Excel arithmetic. Checked all 18 tickers systematically before concluding HELO was the only one affected. Corrected trend: **+37.3%**, not a decline at all. A small bug, but a clean example of the same discipline running through the whole session: **verify the specific number, don't trust the visual.**

---

## PHASE 8 — Process & Governance Decisions

- **Who populates what, and when**: Watch List Tracker/Implied-Return Calculator/Holding Monitor/Performance Tracking are Claude's responsibility, triggered by specific events (a score crossing a threshold, a scheduled review, a confirmed trade) — not a manual chore for Alan.
- **Journal scope**: decisions, reasoning, and corrections — not a duplicate of what the Scoring/Correlation/Drawdown tabs already record as data.
- **Market Data platform**: Excel's built-in Stocks data type / STOCKHISTORY, chosen over Google Sheets' GOOGLEFINANCE specifically because the latter doesn't work in Excel at all — the whole workbook would need re-platforming to use it.

---

## PHASE 9 — Claude Code: Evaluated, Deliberately Not Yet Adopted

A detailed implementation plan (external, built by someone who read the source documents closely) was reviewed and given substantive feedback grounded in this session's actual experience — the automation boundary (Part 12) correctly treated as load-bearing, but gaps flagged around fundamentals-data-quality (the VRTX lesson, generalized), pinning-test risk (don't bake a workbook's residual errors in as "ground truth"), and reviewer-anchoring bias in human-approval workflows.

**Decision: not yet.** The reasoning was explicit — the *system* (rules, workbook) is ready, but the *process* (monthly DCA, quarterly review) had only run once end-to-end at the time of the discussion. Automating a process proven exactly once risks encoding a fluke as a standard. Revisit after Tranche 2 and the first quarterly review actually complete.

---

## The Resources That Made This Work

- **Real source data, not assumptions**: brokerage position exports (2 accounts), 3-year weekly price history (13 stocks + 7 ETFs, provided directly rather than reconstructed from search), the Darryl candidate list, an external Claude Code implementation draft.
- **`recalc.py`**, used on every single workbook change without exception — necessary, though its limits (Table/merged-cell structural issues) had to be learned the hard way, twice.
- **Web search**, used extensively for current fundamentals — with the standing lesson that summarized figures (especially FCF) need cross-checking against primitives when they look internally inconsistent.
- **Direct verification over trust, as a repeated discipline**: re-reading actual cell values instead of assuming a script worked, checking real computed numbers instead of trusting a clean validation pass, visually rendering charts instead of assuming correct data produces a correct picture.

---

## If This Document Has One Job

It's this: **almost everything that went wrong in this session was caught by verifying the actual output — not by generating cleaner code the first time.** The System's own philosophy (don't trust a summary number, check the primitives, don't let a good story override the discipline) turned out to describe the *build process* just as much as it describes the *stock-picking process*.
