# Stock_Picker

A strategy testing system in Python, built in four layers.

## Layer 1 — Data + Strategy Library (`layer1_data_strategies.py`)

**Data:** daily OHLCV via `yfinance` (`auto_adjust=True`), 2010-01-01 to 2025-01-01,
for ~30 liquid assets (index/sector ETFs, commodities/rates/intl, crypto, large caps).
Assets with under 500 bars are skipped. Results are cached to parquet under
`data_cache/` so later layers don't re-download.

**Strategy library:** 47 families spanning the popular-retail spectrum (trend 19,
meanrev 12, volume 6, volatility 3, pattern 4, composite 3). Each is a function
taking a price DataFrame (+ params) and returning a daily position series in
`{-1, 0, 1}` (long / flat / short) with **no look-ahead** — signals are shifted one
bar centrally by the `@strategy` decorator. Each family is tagged with a category:
`trend`, `meanrev`, `volume`, `volatility`, `pattern`, `composite`.

**Parameter grid:** each family carries a small grid of settings. `build_configs()`
expands every family across its grid and returns `(name, function, params, category)`
tuples — **171 configs**, so running all of them across ~30 assets yields several
thousand backtests.

### Usage

```bash
pip install yfinance numpy pandas pyarrow
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

## Layer 2 — Backtest, Walk-Forward & the Survival Funnel (`layer2_funnel.py`)

**Backtest:** a strategy's daily return is its (Layer-1, already-lagged) position
times the asset's return, minus a per-side transaction cost (default 1bp,
configurable, 10bp for crypto). Metrics on any return series: annualized Sharpe
(`mean/std * sqrt(252)`), max drawdown, trade count.

**Walk-forward:** each asset's history is split into 5 sequential windows; within
each, the first 70% is in-sample and the last 30% out-of-sample. Only the 5 OOS
tails are kept and stitched into one series — Sharpe and max drawdown on that
stitched OOS series are the numbers that matter, because the strategy was never
tuned on them.

**Sweep:** `run_sweep()` runs every (config × asset) walk-forward (171 × ~28 ≈
4,800 backtests), records IS Sharpe / OOS Sharpe / OOS max drawdown / OOS trade
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

> Method note: a pure order-permutation leaves Sharpe unchanged (mean/std are
> order-invariant), so to obtain a Sharpe distribution the bootstrap resamples
> *with replacement* by default; `replace=False` gives pure path-shuffling
> (Sharpe constant, only drawdown varies).

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

## Testing

```bash
pip install pytest
pytest -q
```

`tests/test_pipeline.py` covers no-look-ahead, the metric primitives, the six
filters, walk-forward stitching, the bootstrap method, and the cross-sectional
portfolio (dollar-neutrality, costs, no look-ahead).

## Roadmap

- Layer 1 — data + strategy library + parameter grid ✅
- Layer 2 — backtest + walk-forward + six-filter survival funnel ✅
- Layer 3 — parameter sensitivity + bootstrap stress test ✅
- Layer 4 — cross-sectional momentum vs single-asset ✅
