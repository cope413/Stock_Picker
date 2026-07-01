# TODO — ordered by priority

Status legend: `[x]` done · `[~]` partially addressed · `[ ]` open

## P0 — Issues that undermine trust in the results

- [x] **1. Fix selection-on-OOS.** Selecting survivors by their walk-forward "OOS"
  Sharpe across ~4,700 backtests makes that number no longer out-of-sample —
  with that many trials, chance alone clears any fixed threshold. **Done in
  `layer5_validation.py`:** (a) a true holdout — the sweep and funnel run only
  on data before `HOLDOUT_START` (2022-01-01) and survivors get exactly one
  scoring pass on the untouched holdout years; (b) a Deflated Sharpe Ratio
  (Bailey & López de Prado) per survivor, benchmarked against the expected max
  Sharpe of the N trials run on that asset.
- [~] **2. Survivorship bias in the universe.** The large-cap list (AAPL, NVDA,
  TSLA, META…) is 2025's known winners backtested from 2010; long-biased
  results on that group are inflated for reasons unrelated to the strategies.
  **Documented prominently in the README caveats section.** Full fix (a
  point-in-time universe, or adding delisted/stagnant names) still open.
- [x] **3. Crypto annualization/calendar inconsistency.** BTC/ETH trade ~365
  bars/year but Sharpe annualized everything with √252, inflating crypto
  Sharpes ~20% against the same funnel thresholds. **Done:** `layer2.sharpe`
  (and everything downstream) now annualizes by each asset's observed bar
  frequency via `periods_per_year()`; Layer 3's bootstrap receives the correct
  frequency per asset.
- [x] **4. Cost realism / sensitivity.** 1bp per side with zero slippage and free
  shorting is optimistic. **Done (first half):** `layer5_validation.py`
  provides `cost_sensitivity()` — re-scores the entire sweep at 1×/5×/10× base
  costs (reusing cached positions, so ~1.3× the cost of one sweep) and reports
  survivor decay. Run with `python layer5_validation.py --costs`.
  Still open: explicit slippage model and short borrow costs/feasibility.

## P1 — Robustness gaps

- [x] **5. Block bootstrap.** IID resampling destroys autocorrelation and vol
  clustering, understating worst-case drawdowns. **Done:** circular block
  bootstrap is now the default in `layer3.bootstrap_stress`
  (`method="block"`, block length ~n^(1/3) by default); `"iid"` and
  `"permute"` remain available.
- [x] **6. De-duplicate survivors.** The funnel counts each (config × asset) row
  separately, so ten near-identical `ma_crossover` variants on SPY read as ten
  edges. **Done in `layer6_portfolio.py`:** `cluster_survivors()` computes each
  survivor's daily return stream and greedily clusters on absolute return
  correlation (|ρ| ≥ 0.70, best-quality-first, negative correlation counts as
  the same edge); one representative per cluster survives. Cluster detail is
  written to `layer6_clusters.csv`.
- [x] **7. `gap_fade` removed.** The central one-bar signal shift meant the fade
  executed the day *after* the gap — the strategy as coded could not do what
  its name claimed. Removed (46 families / 168 configs now) rather than
  backtest a broken version. Re-add only with an explicit intraday execution
  model (enter at open, exit at close).
- [~] **8. Statistical significance alongside Sharpe.** ~4.5 years of stitched
  OOS bars gives a Sharpe standard error around ±0.45. **Partially done:**
  `layer2.sharpe_se()` added and reported in the Layer 5 holdout table; the
  DSR is itself a significance measure. Still open: add SE to the Layer 2
  funnel report tables.

## P2 — The missing product

- [x] **9. Build the actual picker (Layer 6).** **Done in
  `layer6_portfolio.py`:** eligible survivors (DSR ≥ 0.95 *and* confirmed on
  the holdout by default) are de-duplicated (#6), combined with
  equal-risk-contribution weights (cyclical algorithm, no scipy), capped per
  strategy (25% of gross), and scaled to a 10% annualized vol target under a
  1.0× gross-leverage cap. The spec persists to `layer6_portfolio.json`;
  `python layer6_portfolio.py --signals` prints per-strategy exposures and the
  per-asset net trade list without re-running validation. All knobs are CLI
  flags. Still open (folded into #4/#10): slippage-aware sizing and live data
  refresh for the daily signal run.
- [ ] **10. Parameterize the date range + data hygiene.** `END` is hardcoded to
  2025-01-01 and the parquet cache never invalidates. Add `--end today`,
  cache staleness checks, and data-quality validation (gap detection, split
  anomalies, zero-volume days).

## P3 — Engineering hygiene

- [x] **11. `requirements.txt`** with pinned minimum versions.
- [x] **12. CI** — `.github/workflows/tests.yml` runs `pytest -q` on push/PR
  (Python 3.12, installs from `requirements.txt`; suite is fast and offline).
- [ ] **13. Small cleanups** — move `CLAUDE Trading System.pdf` into `docs/`;
  either add the plotting module the "for charting" comments promise or fix
  the comments; add a LICENSE.
