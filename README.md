# Stock_Picker

A strategy testing system in Python, built in four layers.

## Layer 1 — Data + Strategy Library (`layer1_data_strategies.py`)

**Data:** daily OHLCV via `yfinance` (`auto_adjust=True`), 2010-01-01 to 2025-01-01,
for ~30 liquid assets (index/sector ETFs, commodities/rates/intl, crypto, large caps).
Assets with under 500 bars are skipped. Results are cached to parquet under
`data_cache/` so later layers don't re-download.

**Strategy library:** 31 strategies spanning the popular-retail spectrum. Each is a
function taking a price DataFrame (+ params) and returning a daily position series in
`{-1, 0, 1}` (long / flat / short) with **no look-ahead** — signals are shifted one bar
centrally by the `@strategy` decorator. Each strategy is tagged with a category:
`trend`, `meanrev`, `volume`, `volatility`, `pattern`, `composite`.

### Usage

```bash
pip install yfinance numpy pandas pyarrow
python layer1_data_strategies.py        # download universe + run self-test
```

```python
from layer1_data_strategies import (
    load_universe, download_data, STRATEGIES, run_strategy, list_strategies,
)

data = load_universe()                       # {ticker: OHLCV DataFrame}
pos  = run_strategy("sma_crossover", data["SPY"], fast=20, slow=100)
list_strategies("meanrev")                   # names in a category
```

## Roadmap

- Layer 1 — data + strategy library ✅
- Layer 2 — TBD
- Layer 3 — TBD
- Layer 4 — TBD
