# Stock_Picker

A strategy testing system in Python, built in four layers.

## Walkthrough — from clean checkout to today's trade list

The layer-by-layer detail lives in the sections below; this is the short path
for actually using the tool.

### 1. Set up

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Start the web UI

```bash
python webapp.py        # then open http://127.0.0.1:8713
```

Everything below can also be done from the CLI (equivalents in step 6) — the
two write the same artifact files and stay interchangeable.

### 3. Run the first build

On the **Pipeline** tab, click **Run full build**. The first run downloads the
full universe via yfinance (~120 tickers) and sweeps every config across every
asset — ~20,000 backtests, so expect it to run for a while — then runs the
rest of the pipeline: validate survivors on the holdout → de-duplicate
correlated strategies → equal-risk-contribution weights → save the portfolio
spec → print today's signals. The log streams live; one job runs at a time.
The Universe box on the same tab lets you add tickers beyond the built-in
list (ETFs, crypto, S&P 100).

The build parameters next to the button are the portfolio knobs, prefilled
with the defaults:

| Knob | Default | What it controls |
|---|---|---|
| Correlation threshold | 0.70 | How aggressively near-duplicate strategies are merged |
| Vol target | 0.10 | Annualized portfolio volatility the weights are scaled to |
| Max weight | 0.25 | Cap on any single strategy's share of gross |
| Max gross | 1.0× | Gross-leverage ceiling |
| Min DSR | 0.95 | Deflated-Sharpe floor for a strategy to be eligible |

If nothing clears the DSR floor *and* a positive holdout Sharpe, the build
refuses to produce a portfolio — that's by design, not a bug (an empty book
beats a book of noise).

### 4. Read the results

- **Verdict strip** (top of every tab): independent edges, achieved vol,
  gross, and data freshness with a staleness lamp — the only numbers that
  matter at a glance.
- **Signals** tab: net exposure per asset — **the trade list** — plus
  per-strategy position × weight detail.
- **Holdout** tab: out-of-sample Sharpe per survivor. This is the number to
  believe; anything in-sample is descriptive only.
- **Clusters** tab: which strategies got merged as one idea, and which
  representative survived.
- **Portfolio** tab: the saved spec — weights, vol scaling, caps.

### 5. Daily routine

Once a spec exists you don't rebuild every day. On the **Signals** tab:

- **Update with fresh data** — pulls the latest bars, then recomputes today's
  positions from the saved spec. This is the one-click daily run.
- **Update signals** — same, but from cached data (no download).
- The staleness lamp in the verdict strip tells you when the cache is too old
  to trust.

Rebuild (step 3) only when you want to change the knobs or re-qualify the
strategy set; **Refresh data only** on the Pipeline tab just updates the cache.

### 6. CLI equivalents

```bash
python layer6_portfolio.py                      # = Run full build
python layer6_portfolio.py --signals            # = Update signals
python layer6_portfolio.py --signals --refresh  # = Update with fresh data
python layer6_portfolio.py --corr 0.6 --vol 0.08 --max-weight 0.2 \
                           --max-gross 1.5 --min-dsr 0.9   # custom knobs
```

Artifacts land next to the code: `layer5_holdout.csv`, `layer6_clusters.csv`,
`layer6_portfolio.json`, `layer6_signals.json`, with price data cached under
`data_cache/`.

Before trusting any output, read **Known caveats** below — the large-cap list
has survivorship bias and the cost model is simplified.

## Layer 1 — Data + Strategy Library (`layer1_data_strategies.py`)

**Data:** daily OHLCV via `yfinance` (`auto_adjust=True`), 2010-01-01 to 2025-01-01,
for ~120 liquid assets: index/sector ETFs, commodities/rates/intl, crypto,
and the S&P 100 individual stocks (a current-membership snapshot — see the
survivorship caveat below). Add your own tickers via `universe_custom.txt`
(one per line, `#` comments) or the web UI's Universe box; `universe_tickers()`
merges them in. Assets with under 500 bars are skipped. Results are cached to
parquet under `data_cache/` so later layers don't re-download.

**Strategy library:** 46 families spanning the popular-retail spectrum (trend 19,
meanrev 11, volume 6, volatility 3, pattern 4, composite 3). (A `gap_fade`
family was removed: the central one-bar shift meant it executed the day *after*
the gap — see TODO.md #7.) Each is a function
taking a price DataFrame (+ params) and returning a daily position series in
`{-1, 0, 1}` (long / flat / short) with **no look-ahead** — signals are shifted one
bar centrally by the `@strategy` decorator. Each family is tagged with a category:
`trend`, `meanrev`, `volume`, `volatility`, `pattern`, `composite`.

**Parameter grid:** each family carries a small grid of settings. `build_configs()`
expands every family across its grid and returns `(name, function, params, category)`
tuples — **168 configs**, so running all of them across ~30 assets yields several
thousand backtests.

### Usage

```bash
pip install -r requirements.txt
python layer1_data_strategies.py        # download universe + run self-test
```

```python
from layer1_data_strategies import (
    load_universe, download_data, STRATEGIES, run_strategy,
    list_strategies, build_configs,
)

data    = load_universe()                    # {ticker: OHLCV DataFrame}
pos     = run_strategy("ma_crossover", data["SPY"], fast=20, slow=100)
configs = build_configs()                     # [(name, fn, params, category), ...]
list_strategies("meanrev")                    # family names in a category

# run every config across every asset
for name, fn, params, category in configs:
    for ticker, df in data.items():
        signal = fn(df, **params)             # daily position in {-1, 0, 1}
```

### Data hygiene

`END` defaults to a `"today"` sentinel resolved at call time, so every run is
current; pass `--end 2024-01-01` (or `end=` in code) to pin a window. The
parquet cache carries a meta sidecar and invalidates itself when expected bars
are missing at the end (2-bar tolerance for weekends/holidays; calendar days
for crypto) or when the requested start predates the cached download. Stale
caches trigger a *full* re-download — never an incremental append, because
`auto_adjust` rewrites the whole history whenever a split or dividend lands.
Every frame is screened for non-positive prices, duplicate dates, OHLC
violations, split-sized returns, zero-volume days, and calendar gaps
(`--strict` drops critical failures). Returned frames are always sliced to the
requested `[start, end)`, so a wide cache can never leak bars past a holdout
boundary.

```bash
python layer1_data_strategies.py                      # through today
python layer1_data_strategies.py --end 2024-01-01    # pinned window
python layer1_data_strategies.py --refresh --strict  # force fresh + validate hard
```

## Layer 2 — Backtest, Walk-Forward & the Survival Funnel (`layer2_funnel.py`)

**Backtest:** a strategy's daily return is its (Layer-1, already-lagged) position
times the asset's return, minus a per-side transaction cost (default 1bp,
configurable, 10bp for crypto). Metrics on any return series: annualized Sharpe
(`mean/std * sqrt(bars per year)`, with the bar frequency inferred per asset —
~252 for equities, ~365 for crypto, so both are annualized consistently), max
drawdown, trade count, and a Sharpe standard error (`sharpe_se`).

**Walk-forward:** each asset's history is split into 5 sequential windows; within
each, the first 70% is in-sample and the last 30% out-of-sample. Only the 5 OOS
tails are kept and stitched into one series — Sharpe and max drawdown on that
stitched OOS series are the numbers that matter, because the strategy was never
tuned on them.

**Sweep:** `run_sweep()` runs every (config × asset) walk-forward (168 × ~28 ≈
4,700 backtests), records IS Sharpe / OOS Sharpe / OOS max drawdown / OOS trade
count to `sweep_results.csv`, and prints the total count.

**Six-filter survival funnel** (`apply_filters` / `funnel_report`, all thresholds
in `FunnelThresholds`). A config/asset survives only if it passes all six:

1. OOS max drawdown better than −35%
2. OOS Sharpe above 0.5
3. OOS Sharpe below 2.5 (above that, the asset did the work, not the strategy)
4. OOS Sharpe no more than ~30% above in-sample (a big gap is the overfit signature)
5. at least 30 trades (statistically meaningful)
6. in-sample Sharpe positive

`funnel_report()` prints the attrition, headline counts (positive OOS, cleared 0.5,
survived all six), survival rates by category and family with mean OOS Sharpe, and
a top-survivors table.

```bash
python layer2_funnel.py          # full sweep + funnel report, writes sweep_results.csv
```

## Layer 3 — Robustness Checks (`layer3_robustness.py`)

Two checks that catch survivors which only worked by luck or one magic setting.

**Parameter sensitivity** (`parameter_sensitivity`): for each family, group all
its parameter configs (each config's OOS Sharpe = its mean across the universe)
and report mean OOS Sharpe, std of OOS Sharpe across configs, and the fraction of
configs with positive OOS Sharpe. High mean + low std + frac⁺ near 1.0 means the
edge is parameter-robust; high mean with high std / low frac⁺ is the curve-fit
signature. Written to `parameter_sensitivity.csv`.

**Bootstrap stress test** (`bootstrap_stress` / `run_bootstrap`): for each top
survivor, resample its stitched OOS daily returns ~200× to get a distribution of
equity paths. Reports 5th/50th/95th-percentile Sharpe and worst-case drawdown,
and flags each survivor **solid** or **fragile** by whether its worst-case
drawdown is still survivable. Written to `bootstrap_results.csv` (summary) and
`bootstrap_samples.csv` (per-resample, for charting in Layer 4).

> Method note: the default is a **circular block bootstrap** (blocks of
> ~n^(1/3) days, wrap-around), which preserves autocorrelation and volatility
> clustering — iid resampling destroys vol clustering and understates
> worst-case drawdowns. `method="iid"` and `method="permute"` remain available
> (a pure permutation leaves Sharpe unchanged; only the drawdown path varies).

```bash
python layer3_robustness.py      # sensitivity + bootstrap, reuses sweep_results.csv
```

## Layer 4 — Cross-Sectional Momentum (`layer4_xsec_momentum.py`)

A standalone check. The main sweep tests momentum on each asset alone (scores near
zero); the strongest documented form is cross-sectional — ranking assets against
each other. Every 21 trading days the universe is ranked by trailing return at
three lookbacks (3m, 6m, and 12-months-minus-the-most-recent-month), going long
the top third and short the bottom third, equal weight, dollar-neutral, held to
the next rebalance, with realistic per-asset costs on turnover. It's scored with
the *same* walk-forward (`layer2.walk_forward_returns`), so the OOS Sharpe is
directly comparable to single-asset momentum, and the report states plainly
whether ranking assets against each other beat trading momentum on each alone.
Per-window OOS Sharpes are shown to flag any reliance on a single regime. Results
go to `layer4_xsec_momentum.csv` (summary) and `layer4_xsec_returns.csv` (daily
returns, for charting). Nothing is tuned — it reports what happens.

```bash
python layer4_xsec_momentum.py
```

## Layer 5 — Validation: Holdout, Deflated Sharpe, Cost Sensitivity (`layer5_validation.py`)

The funnel has a structural flaw if used alone: selecting survivors by their
walk-forward "OOS" Sharpe across ~4,700 backtests means that number is no longer
out-of-sample — with that many trials, chance alone clears any fixed threshold.
Layer 5 is the correction, and its output is the number to believe:

**True holdout.** The sweep and funnel run only on data before `HOLDOUT_START`
(default 2022-01-01). Survivors are then scored exactly once on the untouched
holdout years (positions are computed on full history so indicators stay warm;
only holdout returns are scored, with Sharpe ± standard error reported).

**Deflated Sharpe Ratio** (Bailey & López de Prado): for each survivor, the
probability that its OOS Sharpe exceeds the *expected maximum* Sharpe of the N
trials run on the same asset, given the trial count/variance and the return
series' length, skew, and kurtosis. DSR ≥ 0.95 means the edge is unlikely to be
a multiple-testing artifact. (Trials are correlated, so the benchmark is
conservative — clearing it is a strong sign.)

**Cost sensitivity** (`--costs`): re-scores the entire sweep at 1×/5×/10× the
base per-side cost, reusing positions (~1.3× the cost of one sweep), and reports
how the survivor count decays. Anything that dies at 5× base cost was never a
real edge once slippage is acknowledged.

```bash
python layer5_validation.py            # holdout + DSR pipeline
python layer5_validation.py --costs    # also run cost sensitivity
```

Writes `sweep_results_dev.csv`, `layer5_holdout.csv`, and (with `--costs`)
`cost_sensitivity.csv`.

## Layer 6 — Portfolio + Signals: the actual picker (`layer6_portfolio.py`)

Layers 1–5 test strategies; Layer 6 produces something you can trade.

**Eligibility.** Only survivors with DSR ≥ 0.95 *and* a positive holdout Sharpe
enter the portfolio (both thresholds are CLI flags; `--allow-unconfirmed`
relaxes the holdout requirement). If nothing qualifies, Layer 6 refuses to
build a portfolio — an empty book beats a book of noise.

**De-duplication.** Each eligible survivor's daily return stream is computed
and survivors are greedily clustered on absolute return correlation
(|ρ| ≥ 0.70, best-quality-first; a strategy and its mirror count as one idea).
One representative per cluster survives, turning e.g. ten near-identical
`ma_crossover` grid variants on the same asset into the single edge they
actually are. Cluster detail lands in `layer6_clusters.csv`.

**Weights.** Independent edges get equal-risk-contribution weights (cyclical
coordinate algorithm on the annualized sample covariance — no scipy), capped at
25% of gross per strategy, then scaled so in-sample portfolio vol hits a 10%
annual target subject to a 1.0× gross-leverage cap.

**Signals.** The spec persists to `layer6_portfolio.json`, so the daily run is
just:

```bash
python layer6_portfolio.py             # full build: validate → cluster →
                                       # weight → save spec → today's signals
python layer6_portfolio.py --signals            # from the saved spec
python layer6_portfolio.py --signals --refresh  # pull fresh bars first
python layer6_portfolio.py --corr 0.6 --vol 0.08 --max-weight 0.2 \
                           --max-gross 1.5 --min-dsr 0.9
```

`signals_today` prints each strategy's current position × weight and the net
exposure per asset — the trade list. Note the spec's `portfolio_sharpe` mixes
dev and holdout bars and is descriptive only; the Layer 5 holdout table remains
the number to believe.

## Web UI (`webapp.py`)

A local dashboard over the whole pipeline — same artifacts as the CLI, so the
two stay interchangeable:

```bash
pip install fastapi uvicorn
python webapp.py        # http://127.0.0.1:8713
```

Tabs: **Signals** (net exposure per asset — the trade list — plus per-strategy
detail, with one-click refresh from cache or fresh data), **Pipeline** (run the
full build with tunable correlation threshold, vol target, weight/gross caps,
and DSR floor; live log streaming), **Holdout**, **Clusters**, and
**Portfolio**. The verdict strip at the top shows the only numbers that
matter: independent edges, achieved vol, gross, and data freshness with a
staleness lamp wired to the actual cache dates. One job runs at a time;
binding is localhost-only.

To serve it publicly (Docker + Cloudflare Tunnel behind Cloudflare Access),
see **DEPLOY.md**.

## The v7 fundamental system (`v7_scoring.py`, `part11_tracker.py`, `docs/`)

Alongside the technical backtester above, `docs/` holds the **CLAUDE TRADING
SYSTEM v7** — a long-horizon (3–10 yr) fundamental stock-selection framework
scored by hand (1–5 per indicator) rather than backtested. The two systems are
independent; nothing in layers 1–6 reads the v7 scores.

- `docs/CLAUDE_TRADING_SYSTEM_v7.docx` — the framework (Parts 1–11)
- `docs/Notes-Response on Trading Strategy updates (from v4 on).docx` — the
  v4→v7 design discussion that produced Part 11
- `docs/PART8_Test_Run_Record_7-6-26.docx` — a hand-worked v6 run on
  SNEX / MFC / SHEL / CRM / ADBE (the pinned reference for the tests)
- `docs/PART11_Correlation_Drawdown_Tracker_v6.xlsx` — the manual tracker
  workbook; `docs/charts/` — the entry-checklist chart pulls
- `docs/CLAUDE Trading System.pdf` — the original (pre-v7) writeup

`v7_scoring.py` makes the framework's arithmetic and hard rules executable:
composite score `(Σ score × weight) × 20`, the Tier 1 gate (Rules 6–8),
decision thresholds, the Part 4 entry checklist and implied-return tests,
Part 5 sizing with modifiers, and the Part 11 rules (leverage hard cap, beta
overlay with the v7 round-down convention, bear-case return floor, drawdown
response bands, correlation cap). `tests/test_v7_scoring.py` pins all of it
to the PART8 test-run numbers.

`part11_tracker.py` automates the Part 11 tracker from real prices (same
yfinance cache as Layer 1), replacing manual monthly price entry:

```bash
python part11_tracker.py --tickers MFC CRM ADBE          # correlation matrix + rule flags
python part11_tracker.py --tickers MFC CRM ADBE --xlsx   # also fill a copy of the Excel tracker
python part11_tracker.py --values portfolio_values.csv   # drawdown log (date,value CSV)
```

Note: the PART8 record contains one arithmetic slip — ADBE's Tier 3
contribution is 0.14 there, but its own scores (2×0.04 + 3×0.02 + 2×0.01)
sum to 0.16, making the composite 84.6 rather than 84. The decision
(STRONG BUY) is unaffected; the code and tests use the correct sum.

## The Landry System (`landry/`)

The **Landry Family Equity Investment Operating System v1.0**
(`LANDRY_SYSTEM_v1-01_final.docx` + companion workbook
`LANDRY_SYSTEM_WORKBOOK_25.xlsx`, which supersedes the earlier
`LANDRY_SYSTEM_WORKBOOK_11.xlsx`) is the successor to the v7 framework,
implemented as a rule engine with a strict human-in-the-loop boundary:
market data, correlations, technicals, and rule math are automated;
judgment scores (moat, management, revenue visibility) are only ever
*drafted* — with cited evidence — and count for nothing until approved.

```bash
python -m landry score NVDA            # recompute from the workbook (Rules 1-4)
python -m landry refresh               # all objective inputs -> landry_snapshot.json
python -m landry draft NVDA --evidence ev.json   # AI drafts (needs ANTHROPIC_API_KEY)
python -m landry pending / approve / reject      # the human gate (audit-logged)
python -m landry score NVDA --store    # composite from approved scores
python -m landry daily                 # action items with rule citations + deadlines
python -m landry import --by "Name"    # seed the score store from the workbook
python -m landry export --scores       # fill a copy of the Excel workbook
```

The web UI (below) gains a **Landry** section: Dashboard (verdict strip +
score table with engine-vs-workbook cross-check), Actions, Approvals
(approve/reject pending drafts), and Risk (Rule 36 clusters, macro
overlay, beta/staging). Engine modules: `scoring` (Part 3 + Rules 1-4),
`data_auto` (Rule 36/20, technicals), `drawdown` (Part 7 state machine),
`fundamentals` + `macro`, `implied_return`/`entry`/`sizing`/`monitor`
(Rules 5-42), `ai_analyst`/`approvals` (Part 12 gate), `performance`
(Part 9 cohorts), `export`/`daily`. Tests pin the math to Workbook 25.

**Daily schedule:** run `python -m landry daily --refresh` each weekday
after close — e.g. cron `30 13 * * 1-5` (PT) — and review the ACT NOW
items; the same list is on the web UI's L·Actions tab.

## Known caveats

**Survivorship bias in the universe.** The individual-stock lists — the eight
hand-picked large caps *and* the S&P 100 snapshot — are today's known winners
backtested from 2010: every name earned its place in the index by going up.
Long-biased results on those groups are inflated for reasons that have nothing
to do with the strategies — discount them accordingly. The honest fix is
point-in-time index membership (scanning who was *in* the index on each
historical date, including the dropped and delisted); that needs a historical
constituents dataset and is on the roadmap (TODO.md #2).

**Costs are still simplified.** Per-side bps on turnover, no explicit slippage
model, and shorting is treated as free (no borrow cost or feasibility check).
Use the Layer 5 cost-sensitivity report to see which results are fragile to
this assumption (TODO.md #4).

## Testing

```bash
pip install pytest
pytest -q
```

`tests/test_pipeline.py` covers no-look-ahead, the metric primitives, the six
filters, walk-forward stitching, the bootstrap method, and the cross-sectional
portfolio (dollar-neutrality, costs, no look-ahead). `tests/test_validation.py`
covers the holdout split and Deflated Sharpe math. `tests/test_portfolio.py`
covers correlation clustering (including mirrored strategies and low-overlap
pairs), ERC weights (equal risk contributions, caps), vol targeting, and
`signals_today`. 36 tests, all offline.

## Roadmap

See **TODO.md** for the full prioritized list.

- Layer 1 — data + strategy library + parameter grid ✅
- Layer 2 — backtest + walk-forward + six-filter survival funnel ✅
- Layer 3 — parameter sensitivity + block-bootstrap stress test ✅
- Layer 4 — cross-sectional momentum vs single-asset ✅
- Layer 5 — true holdout + Deflated Sharpe + cost sensitivity ✅
- Layer 6 — de-duplication + ERC portfolio + `signals_today` ✅
- CI — GitHub Actions runs the suite on every push/PR ✅
