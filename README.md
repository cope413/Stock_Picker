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

## Roadmap

- Layer 1 — data + strategy library + parameter grid ✅
- Layer 2 — TBD
- Layer 3 — TBD
- Layer 4 — TBD
