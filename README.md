# Stock_Picker

A strategy testing system in Python, built in four layers.

## Layer 1 — Data + Strategy Library (`layer1_data_strategies.py`)

**Data:** daily OHLCV via `yfinance` (`auto_adjust=True`), 2010-01-01 to 2025-01-01,
for ~30 liquid assets (index/sector ETFs, commodities/rates/intl, crypto, large caps).
Assets with under 500 bars are skipped. Results are cached to parquet under
`data_cache/` so later layers don't re-download.

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

## Known caveats

**Survivorship bias in the universe.** The large-cap list (AAPL, MSFT, NVDA,
TSLA, AMZN, GOOGL, META, JPM) is 2025's known winners backtested from 2010.
Long-biased results on that group are inflated for reasons that have nothing to
do with the strategies — discount them accordingly. A point-in-time universe
(or adding delisted/stagnant names) is on the roadmap (TODO.md #2).

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
