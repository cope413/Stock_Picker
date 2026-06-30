"""
Layer 1 — Data + Strategy Library
=================================

Foundation layer of a 4-layer strategy testing system.

  * DATA: daily OHLCV via yfinance (auto_adjust=True), 2010-01-01 .. 2025-01-01,
    ~30 liquid assets. Assets with < 500 bars are skipped. On-disk caching so
    later layers don't re-download.
  * STRATEGY LIBRARY: the popular-retail spectrum. Every strategy is a function
    taking a price DataFrame (+ params) and returning a daily position series in
    {-1, 0, 1} (long / flat / short) with NO look-ahead — signals are shifted one
    bar so today's position only uses data up to yesterday. Each is tagged with a
    category: trend, meanrev, volume, volatility, pattern, composite.

Designed to be imported by later layers:

    from layer1_data_strategies import (
        load_universe, download_data, STRATEGIES, run_strategy, list_strategies,
    )

Dependencies: yfinance, numpy, pandas.

Run directly (`python layer1_data_strategies.py`) to download the universe and
print a self-test of every strategy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

START = "2010-01-01"
END = "2025-01-01"
MIN_BARS = 500
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_cache")

# ~30 liquid assets grouped by theme.
TICKERS: Dict[str, List[str]] = {
    "index_etf": ["SPY", "QQQ", "IWM", "DIA"],
    "sector_etf": ["XLK", "XLF", "XLE", "XLV", "XLI", "XLU", "XLY", "XLP"],
    "commodity_rates_intl": ["GLD", "USO", "TLT", "HYG", "EFA", "EEM", "EWZ"],
    "crypto": ["BTC-USD", "ETH-USD"],
    "large_cap": ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "JPM"],
}

ALL_TICKERS: List[str] = [t for group in TICKERS.values() for t in group]
OHLCV = ["Open", "High", "Low", "Close", "Volume"]


# --------------------------------------------------------------------------- #
# Data layer
# --------------------------------------------------------------------------- #

def _cache_path(ticker: str) -> str:
    safe = ticker.replace("/", "_")
    return os.path.join(CACHE_DIR, f"{safe}.parquet")


def _normalize(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Coerce a yfinance result into a clean Open/High/Low/Close/Volume frame."""
    df = raw.copy()
    # yfinance returns a MultiIndex column frame when given a list; flatten it.
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = df.columns.get_level_values(0)
        if ticker in df.columns.get_level_values(-1):
            df = df.xs(ticker, axis=1, level=-1)
        elif ticker in lvl0:
            df = df.xs(ticker, axis=1, level=0)
        else:
            df.columns = df.columns.get_level_values(0)
    df = df[[c for c in OHLCV if c in df.columns]].copy()
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["Close"])
    df.index = pd.to_datetime(df.index)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def download_data(
    tickers: List[str] | None = None,
    start: str = START,
    end: str = END,
    min_bars: int = MIN_BARS,
    cache_dir: str = CACHE_DIR,
    use_cache: bool = True,
    verbose: bool = True,
) -> Dict[str, pd.DataFrame]:
    """Download daily OHLCV for ``tickers`` and return {ticker: DataFrame}.

    Assets with fewer than ``min_bars`` rows are skipped. Results are cached to
    parquet so repeat runs (and later layers) are instant.
    """
    import yfinance as yf

    tickers = tickers or ALL_TICKERS
    os.makedirs(cache_dir, exist_ok=True)
    out: Dict[str, pd.DataFrame] = {}
    skipped: List[str] = []

    for t in tickers:
        cpath = _cache_path(t)
        df = None
        if use_cache and os.path.exists(cpath):
            try:
                df = pd.read_parquet(cpath)
            except Exception:
                df = None
        if df is None:
            try:
                raw = yf.download(
                    t, start=start, end=end, auto_adjust=True,
                    progress=False, threads=False,
                )
            except Exception as e:  # network / ticker errors
                if verbose:
                    print(f"  ! {t}: download failed ({e})")
                skipped.append(t)
                continue
            if raw is None or raw.empty:
                if verbose:
                    print(f"  ! {t}: no data returned")
                skipped.append(t)
                continue
            df = _normalize(raw, t)
            try:
                df.to_parquet(cpath)
            except Exception:
                pass  # caching is best-effort

        if len(df) < min_bars:
            if verbose:
                print(f"  - {t}: only {len(df)} bars (< {min_bars}) -> skipped")
            skipped.append(t)
            continue

        out[t] = df
        if verbose:
            print(f"  + {t}: {len(df)} bars  "
                  f"[{df.index[0].date()} .. {df.index[-1].date()}]")

    if verbose:
        print(f"\nUniverse: {len(out)} kept, {len(skipped)} skipped"
              + (f"  ({', '.join(skipped)})" if skipped else ""))
    return out


def load_universe(**kwargs) -> Dict[str, pd.DataFrame]:
    """Convenience alias for later layers."""
    return download_data(**kwargs)


# --------------------------------------------------------------------------- #
# Indicator helpers (pure numpy/pandas, no external TA lib)
# --------------------------------------------------------------------------- #

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rolling_std(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).std(ddof=0)


def true_range(df: pd.DataFrame) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    # Wilder's smoothing
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(s, fast) - ema(s, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, sig, line - sig


def stochastic(df: pd.DataFrame, n: int = 14, d: int = 3):
    ll = df["Low"].rolling(n, min_periods=n).min()
    hh = df["High"].rolling(n, min_periods=n).max()
    k = 100 * (df["Close"] - ll) / (hh - ll).replace(0, np.nan)
    return k, k.rolling(d, min_periods=d).mean()


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hh = df["High"].rolling(n, min_periods=n).max()
    ll = df["Low"].rolling(n, min_periods=n).min()
    return -100 * (hh - df["Close"]) / (hh - ll).replace(0, np.nan)


def cci(df: pd.DataFrame, n: int = 20) -> pd.Series:
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    ma = tp.rolling(n, min_periods=n).mean()
    md = (tp - ma).abs().rolling(n, min_periods=n).mean()
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def adx(df: pd.DataFrame, n: int = 14):
    up = df["High"].diff()
    down = -df["Low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(df)
    atr_n = tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1 / n, adjust=False, min_periods=n).mean() / atr_n
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1 / n, adjust=False, min_periods=n).mean() / atr_n
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_n = dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return adx_n, plus_di, minus_di


def obv(df: pd.DataFrame) -> pd.Series:
    sign = np.sign(df["Close"].diff()).fillna(0)
    return (sign * df["Volume"]).cumsum()


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    rmf = tp * df["Volume"]
    pos = rmf.where(tp > tp.shift(1), 0.0)
    neg = rmf.where(tp < tp.shift(1), 0.0)
    pos_n = pos.rolling(n, min_periods=n).sum()
    neg_n = neg.rolling(n, min_periods=n).sum()
    mr = pos_n / neg_n.replace(0, np.nan)
    return 100 - 100 / (1 + mr)


def cmf(df: pd.DataFrame, n: int = 20) -> pd.Series:
    rng = (df["High"] - df["Low"]).replace(0, np.nan)
    mfm = ((df["Close"] - df["Low"]) - (df["High"] - df["Close"])) / rng
    mfv = mfm * df["Volume"]
    return mfv.rolling(n, min_periods=n).sum() / \
        df["Volume"].rolling(n, min_periods=n).sum().replace(0, np.nan)


def rolling_vwap(df: pd.DataFrame, n: int = 20) -> pd.Series:
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    pv = (tp * df["Volume"]).rolling(n, min_periods=n).sum()
    vv = df["Volume"].rolling(n, min_periods=n).sum().replace(0, np.nan)
    return pv / vv


# --------------------------------------------------------------------------- #
# Strategy registry
# --------------------------------------------------------------------------- #

CATEGORIES = ("trend", "meanrev", "volume", "volatility", "pattern", "composite")


@dataclass
class Strategy:
    name: str
    category: str
    func: Callable[..., pd.Series]
    params: Dict = field(default_factory=dict)

    def __call__(self, df: pd.DataFrame, **overrides) -> pd.Series:
        p = {**self.params, **overrides}
        return self.func(df, **p)


STRATEGIES: Dict[str, Strategy] = {}


def strategy(name: str, category: str, **default_params):
    """Register a strategy. The wrapped function must return raw signals; this
    decorator handles the no-look-ahead shift and {-1,0,1} clipping centrally."""
    if category not in CATEGORIES:
        raise ValueError(f"bad category {category!r}; pick one of {CATEGORIES}")

    def deco(fn):
        def wrapped(df: pd.DataFrame, **params) -> pd.Series:
            raw = fn(df, **params)
            return _finalize(raw, df.index)
        STRATEGIES[name] = Strategy(name, category, wrapped, dict(default_params))
        return wrapped
    return deco


def _finalize(sig, index) -> pd.Series:
    """Align, shift one bar (kill look-ahead), clamp to {-1,0,1}."""
    s = pd.Series(sig, index=index) if not isinstance(sig, pd.Series) else sig
    s = s.reindex(index).astype(float)
    s = np.sign(s).fillna(0.0)          # any nonzero magnitude -> direction
    s = s.shift(1).fillna(0.0)          # today uses data up to yesterday
    return s.clip(-1, 1).astype(int)


def list_strategies(category: str | None = None) -> List[str]:
    return [n for n, s in STRATEGIES.items()
            if category is None or s.category == category]


def run_strategy(name: str, df: pd.DataFrame, **overrides) -> pd.Series:
    return STRATEGIES[name](df, **overrides)


# --------------------------------------------------------------------------- #
# TREND
# --------------------------------------------------------------------------- #

@strategy("sma_crossover", "trend", fast=20, slow=100)
def _sma_crossover(df, fast, slow):
    f, s = sma(df["Close"], fast), sma(df["Close"], slow)
    return np.where(f > s, 1, -1)


@strategy("ema_crossover", "trend", fast=12, slow=26)
def _ema_crossover(df, fast, slow):
    f, s = ema(df["Close"], fast), ema(df["Close"], slow)
    return np.where(f > s, 1, -1)


@strategy("price_vs_sma", "trend", n=200)
def _price_vs_sma(df, n):
    # Classic long/flat trend filter.
    return (df["Close"] > sma(df["Close"], n)).astype(float)


@strategy("macd", "trend", fast=12, slow=26, signal=9)
def _macd(df, fast, slow, signal):
    line, sig, _ = macd(df["Close"], fast, slow, signal)
    return np.where(line > sig, 1, -1)


@strategy("donchian_breakout", "trend", n=20)
def _donchian(df, n):
    hi = df["High"].rolling(n, min_periods=n).max().shift(1)
    lo = df["Low"].rolling(n, min_periods=n).min().shift(1)
    pos = pd.Series(0.0, index=df.index)
    pos[df["Close"] > hi] = 1
    pos[df["Close"] < lo] = -1
    return pos.replace(0, np.nan).ffill().fillna(0)


@strategy("adx_trend", "trend", n=14, threshold=25)
def _adx_trend(df, n, threshold):
    adx_n, plus_di, minus_di = adx(df, n)
    strong = adx_n > threshold
    return np.where(strong & (plus_di > minus_di), 1,
                    np.where(strong & (minus_di > plus_di), -1, 0))


@strategy("supertrend", "trend", n=10, mult=3.0)
def _supertrend(df, n, mult):
    hl2 = (df["High"] + df["Low"]) / 2
    a = atr(df, n)
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    close = df["Close"]
    dir_ = pd.Series(1, index=df.index)
    fu, fl = upper.copy(), lower.copy()
    for i in range(1, len(df)):
        fu.iloc[i] = (min(upper.iloc[i], fu.iloc[i - 1])
                      if close.iloc[i - 1] <= fu.iloc[i - 1] else upper.iloc[i])
        fl.iloc[i] = (max(lower.iloc[i], fl.iloc[i - 1])
                      if close.iloc[i - 1] >= fl.iloc[i - 1] else lower.iloc[i])
        if close.iloc[i] > fu.iloc[i - 1]:
            dir_.iloc[i] = 1
        elif close.iloc[i] < fl.iloc[i - 1]:
            dir_.iloc[i] = -1
        else:
            dir_.iloc[i] = dir_.iloc[i - 1]
    return dir_.astype(float)


@strategy("roc_momentum", "trend", n=126)
def _roc_momentum(df, n):
    roc = df["Close"].pct_change(n)
    return np.sign(roc)


# --------------------------------------------------------------------------- #
# MEAN REVERSION
# --------------------------------------------------------------------------- #

@strategy("rsi_reversion", "meanrev", n=14, low=30, high=70)
def _rsi_reversion(df, n, low, high):
    r = rsi(df["Close"], n)
    pos = pd.Series(np.nan, index=df.index)
    pos[r < low] = 1
    pos[r > high] = -1
    pos[(r >= 50 - 1e-9) & (r <= 50 + 1e-9)] = 0  # rare exact-50 exit
    return pos.ffill().fillna(0)


@strategy("bollinger_reversion", "meanrev", n=20, k=2.0)
def _bollinger_reversion(df, n, k):
    m = sma(df["Close"], n)
    sd = rolling_std(df["Close"], n)
    upper, lower = m + k * sd, m - k * sd
    pos = pd.Series(np.nan, index=df.index)
    pos[df["Close"] < lower] = 1
    pos[df["Close"] > upper] = -1
    pos[df["Close"].between(m - 1e-9, m + 1e-9)] = 0
    # exit back to flat once price crosses the mean
    cross_up = (df["Close"] >= m) & (df["Close"].shift(1) < m)
    cross_dn = (df["Close"] <= m) & (df["Close"].shift(1) > m)
    pos[cross_up | cross_dn] = 0
    return pos.ffill().fillna(0)


@strategy("zscore_reversion", "meanrev", n=20, entry=1.5, exit=0.5)
def _zscore_reversion(df, n, entry, exit):
    m = sma(df["Close"], n)
    z = (df["Close"] - m) / rolling_std(df["Close"], n).replace(0, np.nan)
    pos = pd.Series(np.nan, index=df.index)
    pos[z <= -entry] = 1
    pos[z >= entry] = -1
    pos[z.abs() <= exit] = 0
    return pos.ffill().fillna(0)


@strategy("stochastic_reversion", "meanrev", n=14, d=3, low=20, high=80)
def _stochastic_reversion(df, n, d, low, high):
    _, dline = stochastic(df, n, d)
    pos = pd.Series(np.nan, index=df.index)
    pos[dline < low] = 1
    pos[dline > high] = -1
    return pos.ffill().fillna(0)


@strategy("williams_r", "meanrev", n=14, low=-80, high=-20)
def _williams_r(df, n, low, high):
    wr = williams_r(df, n)
    pos = pd.Series(np.nan, index=df.index)
    pos[wr < low] = 1
    pos[wr > high] = -1
    return pos.ffill().fillna(0)


@strategy("cci_reversion", "meanrev", n=20, level=100)
def _cci_reversion(df, n, level):
    c = cci(df, n)
    pos = pd.Series(np.nan, index=df.index)
    pos[c < -level] = 1
    pos[c > level] = -1
    pos[c.abs() < 1e-9] = 0
    return pos.ffill().fillna(0)


# --------------------------------------------------------------------------- #
# VOLUME
# --------------------------------------------------------------------------- #

@strategy("obv_trend", "volume", n=20)
def _obv_trend(df, n):
    o = obv(df)
    return np.where(o > sma(o, n), 1, -1)


@strategy("mfi_reversion", "volume", n=14, low=20, high=80)
def _mfi(df, n, low, high):
    m = mfi(df, n)
    pos = pd.Series(np.nan, index=df.index)
    pos[m < low] = 1
    pos[m > high] = -1
    return pos.ffill().fillna(0)


@strategy("cmf_trend", "volume", n=20, threshold=0.05)
def _cmf(df, n, threshold):
    c = cmf(df, n)
    return np.where(c > threshold, 1, np.where(c < -threshold, -1, 0))


@strategy("volume_breakout", "volume", n=20, vol_n=20, vol_mult=1.5)
def _volume_breakout(df, n, vol_n, vol_mult):
    hi = df["High"].rolling(n, min_periods=n).max().shift(1)
    lo = df["Low"].rolling(n, min_periods=n).min().shift(1)
    vol_ok = df["Volume"] > vol_mult * sma(df["Volume"], vol_n)
    pos = pd.Series(np.nan, index=df.index)
    pos[(df["Close"] > hi) & vol_ok] = 1
    pos[(df["Close"] < lo) & vol_ok] = -1
    return pos.ffill().fillna(0)


@strategy("vwap_reversion", "volume", n=20, k=1.0)
def _vwap_reversion(df, n, k):
    vw = rolling_vwap(df, n)
    dev = (df["Close"] - vw)
    band = k * dev.rolling(n, min_periods=n).std(ddof=0)
    pos = pd.Series(np.nan, index=df.index)
    pos[dev < -band] = 1
    pos[dev > band] = -1
    pos[dev.abs() <= 1e-12] = 0
    return pos.ffill().fillna(0)


# --------------------------------------------------------------------------- #
# VOLATILITY
# --------------------------------------------------------------------------- #

@strategy("bollinger_breakout", "volatility", n=20, k=2.0)
def _bollinger_breakout(df, n, k):
    m = sma(df["Close"], n)
    sd = rolling_std(df["Close"], n)
    pos = pd.Series(np.nan, index=df.index)
    pos[df["Close"] > m + k * sd] = 1
    pos[df["Close"] < m - k * sd] = -1
    return pos.ffill().fillna(0)


@strategy("atr_channel_breakout", "volatility", n=20, atr_n=14, mult=2.0)
def _atr_channel(df, n, atr_n, mult):
    mid = sma(df["Close"], n)
    a = atr(df, atr_n)
    upper = (mid + mult * a).shift(1)
    lower = (mid - mult * a).shift(1)
    pos = pd.Series(np.nan, index=df.index)
    pos[df["Close"] > upper] = 1
    pos[df["Close"] < lower] = -1
    return pos.ffill().fillna(0)


@strategy("keltner_breakout", "volatility", n=20, atr_n=10, mult=2.0)
def _keltner(df, n, atr_n, mult):
    mid = ema(df["Close"], n)
    a = atr(df, atr_n)
    pos = pd.Series(np.nan, index=df.index)
    pos[df["Close"] > mid + mult * a] = 1
    pos[df["Close"] < mid - mult * a] = -1
    return pos.ffill().fillna(0)


@strategy("vol_regime_trend", "volatility", vol_n=20, vol_lookback=100, trend_n=50)
def _vol_regime(df, vol_n, vol_lookback, trend_n):
    # Follow trend only when realized vol is below its median regime.
    ret = df["Close"].pct_change()
    vol = ret.rolling(vol_n, min_periods=vol_n).std(ddof=0)
    calm = vol < vol.rolling(vol_lookback, min_periods=vol_lookback).median()
    trend = np.sign(df["Close"] - sma(df["Close"], trend_n))
    return np.where(calm, trend, 0)


# --------------------------------------------------------------------------- #
# PATTERN
# --------------------------------------------------------------------------- #

@strategy("high_low_52w_breakout", "pattern", n=252)
def _hl_52w(df, n):
    hi = df["High"].rolling(n, min_periods=n).max().shift(1)
    lo = df["Low"].rolling(n, min_periods=n).min().shift(1)
    pos = pd.Series(np.nan, index=df.index)
    pos[df["Close"] >= hi] = 1
    pos[df["Close"] <= lo] = -1
    return pos.ffill().fillna(0)


@strategy("gap_fade", "pattern", gap_pct=0.02)
def _gap_fade(df, gap_pct):
    gap = df["Open"] / df["Close"].shift(1) - 1
    # Fade the gap: gap up -> short, gap down -> long (held one bar).
    pos = pd.Series(0.0, index=df.index)
    pos[gap > gap_pct] = -1
    pos[gap < -gap_pct] = 1
    return pos


@strategy("inside_bar_breakout", "pattern")
def _inside_bar(df):
    inside = (df["High"] < df["High"].shift(1)) & (df["Low"] > df["Low"].shift(1))
    prev_inside = inside.shift(1).fillna(False)
    pos = pd.Series(np.nan, index=df.index)
    pos[prev_inside & (df["Close"] > df["High"].shift(1))] = 1
    pos[prev_inside & (df["Close"] < df["Low"].shift(1))] = -1
    return pos.ffill().fillna(0)


@strategy("engulfing", "pattern")
def _engulfing(df):
    o, c = df["Open"], df["Close"]
    po, pc = o.shift(1), c.shift(1)
    bull = (pc < po) & (c > o) & (c >= po) & (o <= pc)
    bear = (pc > po) & (c < o) & (c <= po) & (o >= pc)
    pos = pd.Series(np.nan, index=df.index)
    pos[bull] = 1
    pos[bear] = -1
    return pos.ffill().fillna(0)


@strategy("three_bar_momentum", "pattern", n=3)
def _three_bar(df, n):
    up = (df["Close"] > df["Close"].shift(1)).rolling(n).sum() == n
    dn = (df["Close"] < df["Close"].shift(1)).rolling(n).sum() == n
    pos = pd.Series(np.nan, index=df.index)
    pos[up] = 1
    pos[dn] = -1
    return pos.ffill().fillna(0)


# --------------------------------------------------------------------------- #
# COMPOSITE
# --------------------------------------------------------------------------- #

@strategy("trend_filtered_reversion", "composite",
          trend_n=200, rsi_n=2, low=10, high=90)
def _trend_filtered_reversion(df, trend_n, rsi_n, low, high):
    # Connors-style: only buy dips in an uptrend, sell rips in a downtrend.
    up = df["Close"] > sma(df["Close"], trend_n)
    r = rsi(df["Close"], rsi_n)
    pos = pd.Series(np.nan, index=df.index)
    pos[up & (r < low)] = 1
    pos[up & (r > high)] = 0
    pos[~up & (r > high)] = -1
    pos[~up & (r < low)] = 0
    return pos.ffill().fillna(0)


@strategy("golden_cross_rsi", "composite", fast=50, slow=200, rsi_n=14, rsi_min=50)
def _golden_cross_rsi(df, fast, slow, rsi_n, rsi_min):
    bull = sma(df["Close"], fast) > sma(df["Close"], slow)
    r = rsi(df["Close"], rsi_n)
    return np.where(bull & (r > rsi_min), 1,
                    np.where(~bull & (r < (100 - rsi_min)), -1, 0))


@strategy("majority_vote", "composite",
          members=("sma_crossover", "macd", "rsi_reversion",
                   "bollinger_breakout", "donchian_breakout"))
def _majority_vote(df, members):
    # Note: members already shift internally; we re-derive raw votes by calling
    # the registered (shifted) strategies and summing, then sign. The outer
    # _finalize shift would double-shift, so we pre-undo one shift here.
    votes = pd.Series(0.0, index=df.index)
    for m in members:
        s = STRATEGIES[m](df).shift(-1).fillna(0)  # undo the member's shift
        votes = votes.add(s, fill_value=0)
    return np.sign(votes)


# --------------------------------------------------------------------------- #
# Self-test / CLI
# --------------------------------------------------------------------------- #

def _self_test(data: Dict[str, pd.DataFrame]) -> None:
    print("\n" + "=" * 70)
    print(f"STRATEGY LIBRARY — {len(STRATEGIES)} strategies")
    print("=" * 70)
    by_cat: Dict[str, List[str]] = {c: [] for c in CATEGORIES}
    for name, st in STRATEGIES.items():
        by_cat[st.category].append(name)
    for cat in CATEGORIES:
        print(f"  {cat:11s}: {', '.join(by_cat[cat])}")

    if not data:
        print("\n(no market data available — skipping live signal test)")
        return

    sample_key = "SPY" if "SPY" in data else next(iter(data))
    sample = data[sample_key]
    print(f"\nSignal sanity check on {sample_key} ({len(sample)} bars):")
    print(f"{'strategy':28s}{'cat':11s}{'long':>6}{'flat':>6}{'short':>6}  ok")
    for name, st in STRATEGIES.items():
        pos = st(sample)
        vals = set(pd.unique(pos.dropna()))
        in_set = vals.issubset({-1, 0, 1})
        no_lookahead = pos.iloc[0] == 0
        ok = in_set and no_lookahead
        n_long = int((pos == 1).sum())
        n_flat = int((pos == 0).sum())
        n_short = int((pos == -1).sum())
        print(f"{name:28s}{st.category:11s}{n_long:6d}{n_flat:6d}"
              f"{n_short:6d}  {'PASS' if ok else 'FAIL'}")


def main():
    print("Layer 1 — downloading universe ...\n")
    data = download_data()
    _self_test(data)
    print("\nDone. Import this module from later layers via:")
    print("    from layer1_data_strategies import "
          "load_universe, STRATEGIES, run_strategy")


if __name__ == "__main__":
    main()
