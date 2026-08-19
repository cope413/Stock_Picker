"""Curated swing-trading universe.

Liquid large-caps + major index/sector ETFs only (no crypto, commodities,
rates, or international ETFs -- keeps the first run fast and the results
easy to reason about; those groups are available in layer1_data_strategies
TICKERS if this track ever wants to widen).

Deliberately excludes every ticker currently held in the Landry System
(as of 2026-08-18: ADBE, ANET, ASML, AVGO, AVUV, CRWD, KLAC, MU, NVDA, PLD,
PLTR, SPMO, TSM, VFLO, VRT, VRTX) so this track's signals never compete
with or duplicate a position the core system already owns. Re-check this
list against current holdings before reusing it long-term -- holdings
change; this file doesn't auto-update.
"""

from __future__ import annotations

import os

INDEX_ETF = ["SPY", "QQQ", "IWM", "DIA"]
SECTOR_ETF = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLU", "XLY", "XLP"]
LARGE_CAP = [
    "AAPL", "MSFT", "TSLA", "AMZN", "GOOGL", "META", "JPM", "AMD", "NFLX",
    "DIS", "BA", "CAT", "GS", "XOM", "CVX", "HD", "COST", "UNH", "V", "MA",
]

SWING_UNIVERSE = INDEX_ETF + SECTOR_ETF + LARGE_CAP

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_HERE, "data_cache")
OUTPUT_DIR = os.path.join(_HERE, "outputs")
