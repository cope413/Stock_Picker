"""
Layer 1 — Data + Strategy Library
=================================

Foundation layer of a 4-layer strategy testing system.

  * DATA: daily OHLCV via yfinance (auto_adjust=True), 2010-01-01 .. 2025-01-01,
    ~30 liquid assets. Assets with < 500 bars are skipped. On-disk caching so
    later layers don't re-download.

  * STRATEGY LIBRARY: the full popular-retail spectrum (46 families). Every
    strategy is a function taking a price DataFrame (+ params) and returning a
    daily position series in {-1, 0, 1} (long / flat / short) with NO look-ahead
    — signals are shifted one bar centrally so today's position only uses data up
    to yesterday. Each family is tagged: trend, meanrev, volume, volatility,
    pattern, composite.

  * PARAMETER GRID: each family carries a small grid of settings. build_configs()
    expands every family across its grid and returns (name, function, params,
    category) tuples — a few hundred configs, so running every config across all
    ~30 assets yields thousands of backtests.

Designed to be imported by later layers:

    from layer1_data_strategies import (
        load_universe, download_data, STRATEGIES, run_strategy,
        list_strategies, build_configs,
    )

Dependencies: yfinance, numpy, pandas.

Run directly (`python layer1_data_strategies.py`) to download the universe,
build the config grid, print the total count, and self-test every strategy.
"""

from __future__ import annotations

import itertools
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Tuple

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

START = "2010-01-01"
END = "2025-01-01"
MIN_BARS = 500
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_cache")

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
    return os.path.join(CACHE_DIR, f"{ticker.replace('/', '_')}.parquet")


def _normalize(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Coerce a yfinance result into a clean Open/High/Low/Close/Volume frame."""
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        if ticker in df.columns.get_level_values(-1):
            df = df.xs(ticker, axis=1, level=-1)
        elif ticker in df.columns.get_level_values(0):
            df = df.xs(ticker, axis=1, level=0)
        else:
            df.columns = df.columns.get_level_values(0)
    df = df[[c for c in OHLCV if c in df.columns]].copy()
    df = df.apply(pd.to_numeric, errors="coerce").dropna(subset=["Close"])
    df.index = pd.to_datetime(df.index)
    return df[~df.index.duplicated(keep="last")].sort_index()


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
                raw = yf.download(t, start=start, end=end, auto_adjust=True,
                                  progress=False, threads=False)
            except Exception as e:
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
                pass

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

def sma(s, n):
    return s.rolling(n, min_periods=n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wma(s, n):
    w = np.arange(1, n + 1)
    return s.rolling(n, min_periods=n).apply(lambda x: np.dot(x, w) / w.sum(),
                                             raw=True)


def rolling_std(s, n):
    return s.rolling(n, min_periods=n).std(ddof=0)


def true_range(df):
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def atr(df, n=14):
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(s, n=14):
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    al = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(s, fast=12, slow=26, signal=9):
    line = ema(s, fast) - ema(s, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, sig, line - sig


def stochastic(df, n=14, d=3):
    ll = df["Low"].rolling(n, min_periods=n).min()
    hh = df["High"].rolling(n, min_periods=n).max()
    k = 100 * (df["Close"] - ll) / (hh - ll).replace(0, np.nan)
    return k, k.rolling(d, min_periods=d).mean()


def williams_r(df, n=14):
    hh = df["High"].rolling(n, min_periods=n).max()
    ll = df["Low"].rolling(n, min_periods=n).min()
    return -100 * (hh - df["Close"]) / (hh - ll).replace(0, np.nan)


def cci(df, n=20):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    ma = tp.rolling(n, min_periods=n).mean()
    md = (tp - ma).abs().rolling(n, min_periods=n).mean()
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def adx(df, n=14):
    up = df["High"].diff()
    down = -df["Low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr_n = true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / atr_n
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / atr_n
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean(), plus_di, minus_di


def aroon(df, n=25):
    up = df["High"].rolling(n + 1, min_periods=n + 1).apply(
        lambda x: 100 * np.argmax(x) / n, raw=True)
    down = df["Low"].rolling(n + 1, min_periods=n + 1).apply(
        lambda x: 100 * np.argmin(x) / n, raw=True)
    return up, down


def vortex(df, n=14):
    tr_sum = true_range(df).rolling(n, min_periods=n).sum()
    vmp = (df["High"] - df["Low"].shift(1)).abs().rolling(n, min_periods=n).sum()
    vmm = (df["Low"] - df["High"].shift(1)).abs().rolling(n, min_periods=n).sum()
    return vmp / tr_sum, vmm / tr_sum


def trix(s, n=15):
    e3 = ema(ema(ema(s, n), n), n)
    return e3.pct_change() * 100


def hull_ma(s, n):
    half = max(int(n / 2), 1)
    sq = max(int(np.sqrt(n)), 1)
    return wma(2 * wma(s, half) - wma(s, n), sq)


def kama(s, n=10, fast=2, slow=30):
    vals = s.values
    change = np.abs(vals - np.concatenate([np.full(n, np.nan), vals[:-n]]))
    vol = pd.Series(np.abs(np.diff(vals, prepend=vals[0])),
                    index=s.index).rolling(n, min_periods=n).sum().values
    er = np.divide(change, vol, out=np.full_like(change, np.nan),
                   where=vol != 0)
    sc = (er * (2 / (fast + 1) - 2 / (slow + 1)) + 2 / (slow + 1)) ** 2
    res = np.full(len(vals), np.nan)
    if len(vals) > n:
        res[n] = vals[n]
        for i in range(n + 1, len(vals)):
            prev = res[i - 1]
            res[i] = prev + (sc[i] if not np.isnan(sc[i]) else 0) * (vals[i] - prev)
    return pd.Series(res, index=s.index)


def parabolic_sar(df, af_step=0.02, af_max=0.2):
    high, low = df["High"].values, df["Low"].values
    n = len(df)
    sar = np.full(n, np.nan)
    if n < 2:
        return pd.Series(sar, index=df.index)
    trend, af, ep, sar[0] = 1, af_step, high[0], low[0]
    for i in range(1, n):
        sar_i = sar[i - 1] + af * (ep - sar[i - 1])
        if trend == 1:
            sar_i = min(sar_i, low[i - 1], low[max(i - 2, 0)])
            if low[i] < sar_i:
                trend, sar_i, ep, af = -1, ep, low[i], af_step
            elif high[i] > ep:
                ep, af = high[i], min(af + af_step, af_max)
        else:
            sar_i = max(sar_i, high[i - 1], high[max(i - 2, 0)])
            if high[i] > sar_i:
                trend, sar_i, ep, af = 1, ep, high[i], af_step
            elif low[i] < ep:
                ep, af = low[i], min(af + af_step, af_max)
        sar[i] = sar_i
    return pd.Series(sar, index=df.index)


def ichimoku(df, tenkan=9, kijun=26, senkou=52):
    conv = (df["High"].rolling(tenkan).max() + df["Low"].rolling(tenkan).min()) / 2
    base = (df["High"].rolling(kijun).max() + df["Low"].rolling(kijun).min()) / 2
    span_a = ((conv + base) / 2).shift(kijun)
    span_b = ((df["High"].rolling(senkou).max()
               + df["Low"].rolling(senkou).min()) / 2).shift(kijun)
    return conv, base, span_a, span_b


def elder_ray(df, n=13):
    e = ema(df["Close"], n)
    return df["High"] - e, df["Low"] - e, e


def obv(df):
    return (np.sign(df["Close"].diff()).fillna(0) * df["Volume"]).cumsum()


def mfi(df, n=14):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    rmf = tp * df["Volume"]
    pos = rmf.where(tp > tp.shift(1), 0.0).rolling(n, min_periods=n).sum()
    neg = rmf.where(tp < tp.shift(1), 0.0).rolling(n, min_periods=n).sum()
    return 100 - 100 / (1 + pos / neg.replace(0, np.nan))


def adl(df):
    rng = (df["High"] - df["Low"]).replace(0, np.nan)
    mfm = ((df["Close"] - df["Low"]) - (df["High"] - df["Close"])) / rng
    return (mfm * df["Volume"]).fillna(0).cumsum()


def cmf(df, n=20):
    rng = (df["High"] - df["Low"]).replace(0, np.nan)
    mfm = ((df["Close"] - df["Low"]) - (df["High"] - df["Close"])) / rng
    mfv = mfm * df["Volume"]
    return (mfv.rolling(n, min_periods=n).sum()
            / df["Volume"].rolling(n, min_periods=n).sum().replace(0, np.nan))


def force_index(df, n=13):
    return ema(df["Close"].diff() * df["Volume"], n)


def chaikin_osc(df, fast=3, slow=10):
    a = adl(df)
    return ema(a, fast) - ema(a, slow)


def rolling_vwap(df, n=20):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    pv = (tp * df["Volume"]).rolling(n, min_periods=n).sum()
    vv = df["Volume"].rolling(n, min_periods=n).sum().replace(0, np.nan)
    return pv / vv


def ultimate_osc(df, s1=7, s2=14, s3=28):
    pc = df["Close"].shift(1)
    low_min = pd.concat([df["Low"], pc], axis=1).min(axis=1)
    bp = df["Close"] - low_min
    tr = pd.concat([df["High"], pc], axis=1).max(axis=1) - low_min
    a1 = bp.rolling(s1).sum() / tr.rolling(s1).sum().replace(0, np.nan)
    a2 = bp.rolling(s2).sum() / tr.rolling(s2).sum().replace(0, np.nan)
    a3 = bp.rolling(s3).sum() / tr.rolling(s3).sum().replace(0, np.nan)
    return 100 * (4 * a1 + 2 * a2 + a3) / 7


def connors_rsi(s, rsi_n=3, streak_n=2, rank_n=100):
    diff = s.diff().values
    streak = np.zeros(len(s))
    for i in range(1, len(s)):
        if diff[i] > 0:
            streak[i] = streak[i - 1] + 1 if streak[i - 1] > 0 else 1
        elif diff[i] < 0:
            streak[i] = streak[i - 1] - 1 if streak[i - 1] < 0 else -1
    rsi_price = rsi(s, rsi_n)
    rsi_streak = rsi(pd.Series(streak, index=s.index), streak_n)
    ret = s.pct_change()
    pr = ret.rolling(rank_n, min_periods=rank_n).apply(
        lambda x: 100 * np.mean(x[:-1] < x[-1]), raw=True)
    return (rsi_price + rsi_streak + pr) / 3


def linreg_slope(s, n):
    x = np.arange(n)
    xm = x.mean()
    denom = ((x - xm) ** 2).sum()
    return s.rolling(n, min_periods=n).apply(
        lambda y: np.dot(x - xm, y - y.mean()) / denom, raw=True)


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
    grid: Dict = field(default_factory=dict)

    def __call__(self, df: pd.DataFrame, **overrides) -> pd.Series:
        return self.func(df, **{**self.params, **overrides})


STRATEGIES: Dict[str, Strategy] = {}


def strategy(name, category, grid=None, **default_params):
    """Register a strategy. The wrapped function returns raw signals; this
    decorator applies the no-look-ahead shift and {-1,0,1} clamp centrally.
    ``grid`` maps param -> list of values for build_configs()."""
    if category not in CATEGORIES:
        raise ValueError(f"bad category {category!r}; pick {CATEGORIES}")

    def deco(fn):
        def wrapped(df, **params):
            return _finalize(fn(df, **params), df.index)
        STRATEGIES[name] = Strategy(name, category, wrapped,
                                    dict(default_params), grid or {})
        return wrapped
    return deco


def _finalize(sig, index) -> pd.Series:
    """Align, reduce to direction, shift one bar (kill look-ahead), clamp."""
    s = sig if isinstance(sig, pd.Series) else pd.Series(sig, index=index)
    s = np.sign(s.reindex(index).astype(float)).fillna(0.0)
    return s.shift(1).fillna(0.0).clip(-1, 1).astype(int)


def _hold(pos: pd.Series) -> pd.Series:
    """Forward-fill a sparse long/short signal into a held position."""
    return pos.replace(0, np.nan).ffill().fillna(0)


def list_strategies(category: str | None = None) -> List[str]:
    return [n for n, s in STRATEGIES.items()
            if category is None or s.category == category]


def run_strategy(name: str, df: pd.DataFrame, **overrides) -> pd.Series:
    return STRATEGIES[name](df, **overrides)


# =========================================================================== #
# TREND
# =========================================================================== #

@strategy("ma_crossover", "trend", grid={"fast": [5, 10, 20, 50],
          "slow": [50, 100, 150, 200]}, fast=20, slow=100)
def _ma_crossover(df, fast, slow):
    return np.where(sma(df["Close"], fast) > sma(df["Close"], slow), 1, -1)


@strategy("ts_momentum", "trend", grid={"n": [60, 120, 180, 252]}, n=120)
def _ts_momentum(df, n):
    return np.sign(df["Close"] / df["Close"].shift(n) - 1)


@strategy("roc_momentum", "trend",
          grid={"n": [20, 60, 120, 200], "threshold": [0.0, 0.02]},
          n=60, threshold=0.0)
def _roc_momentum(df, n, threshold):
    roc = df["Close"].pct_change(n)
    return np.where(roc > threshold, 1, np.where(roc < -threshold, -1, 0))


@strategy("macd", "trend",
          grid={"params": [(12, 26, 9), (8, 21, 5), (19, 39, 9)]},
          params=(12, 26, 9))
def _macd(df, params):
    line, sig, _ = macd(df["Close"], *params)
    return np.where(line > sig, 1, -1)


@strategy("donchian_breakout", "trend", grid={"n": [20, 40, 55, 100]}, n=20)
def _donchian(df, n):
    hi = df["High"].rolling(n, min_periods=n).max().shift(1)
    lo = df["Low"].rolling(n, min_periods=n).min().shift(1)
    pos = pd.Series(0.0, index=df.index)
    pos[df["Close"] > hi] = 1
    pos[df["Close"] < lo] = -1
    return _hold(pos)


@strategy("bollinger_breakout", "trend",
          grid={"n": [20, 50, 100], "k": [1.5, 2.0, 2.5]}, n=20, k=2.0)
def _bollinger_breakout(df, n, k):
    m, sd = sma(df["Close"], n), rolling_std(df["Close"], n)
    pos = pd.Series(0.0, index=df.index)
    pos[df["Close"] > m + k * sd] = 1
    pos[df["Close"] < m - k * sd] = -1
    return _hold(pos)


@strategy("supertrend", "trend",
          grid={"n": [7, 10, 14, 20], "mult": [2.0, 3.0]}, n=10, mult=3.0)
def _supertrend(df, n, mult):
    hl2 = (df["High"] + df["Low"]) / 2
    a = atr(df, n)
    upper, lower = (hl2 + mult * a).values, (hl2 - mult * a).values
    close = df["Close"].values
    dir_ = np.ones(len(df))
    fu, fl = upper.copy(), lower.copy()
    for i in range(1, len(df)):
        fu[i] = min(upper[i], fu[i - 1]) if close[i - 1] <= fu[i - 1] else upper[i]
        fl[i] = max(lower[i], fl[i - 1]) if close[i - 1] >= fl[i - 1] else lower[i]
        dir_[i] = (1 if close[i] > fu[i - 1] else
                   -1 if close[i] < fl[i - 1] else dir_[i - 1])
    return pd.Series(dir_, index=df.index)


@strategy("parabolic_sar", "trend", grid={"af_step": [0.01, 0.02, 0.03]},
          af_step=0.02, af_max=0.2)
def _psar(df, af_step, af_max):
    return np.sign(df["Close"] - parabolic_sar(df, af_step, af_max))


@strategy("adx_trend", "trend", grid={"threshold": [20, 25, 30]},
          n=14, threshold=25)
def _adx_trend(df, n, threshold):
    a, plus_di, minus_di = adx(df, n)
    strong = a > threshold
    return np.where(strong & (plus_di > minus_di), 1,
                    np.where(strong & (minus_di > plus_di), -1, 0))


@strategy("ichimoku", "trend",
          grid={"params": [(9, 26, 52), (7, 22, 44), (12, 30, 60)]},
          params=(9, 26, 52))
def _ichimoku(df, params):
    conv, base, span_a, span_b = ichimoku(df, *params)
    cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_bot = pd.concat([span_a, span_b], axis=1).min(axis=1)
    pos = pd.Series(0.0, index=df.index)
    pos[(df["Close"] > cloud_top) & (conv > base)] = 1
    pos[(df["Close"] < cloud_bot) & (conv < base)] = -1
    return _hold(pos)


@strategy("linreg_slope", "trend", grid={"n": [20, 50, 100, 150]}, n=50)
def _linreg(df, n):
    return np.sign(linreg_slope(df["Close"], n))


@strategy("aroon", "trend", grid={"n": [14, 25]}, n=25)
def _aroon(df, n):
    up, down = aroon(df, n)
    return np.where(up > down, 1, -1)


@strategy("vortex", "trend", grid={"n": [14, 21]}, n=14)
def _vortex(df, n):
    vip, vim = vortex(df, n)
    return np.where(vip > vim, 1, -1)


@strategy("trix", "trend", grid={"n": [9, 15]}, n=15, signal=9)
def _trix(df, n, signal):
    t = trix(df["Close"], n)
    return np.where(t > ema(t, signal), 1, -1)


@strategy("hull_ma", "trend", grid={"n": [9, 16, 25, 49]}, n=16)
def _hull(df, n):
    h = hull_ma(df["Close"], n)
    return np.sign(h.diff())


@strategy("kama", "trend", grid={"n": [10, 20]}, n=10)
def _kama(df, n):
    k = kama(df["Close"], n)
    return np.sign(df["Close"] - k)


@strategy("turtle", "trend", grid={"entry": [20, 55], "exit": [10, 20]},
          entry=20, exit=10)
def _turtle(df, entry, exit):
    hi = df["High"].rolling(entry, min_periods=entry).max().shift(1)
    lo = df["Low"].rolling(entry, min_periods=entry).min().shift(1)
    xlo = df["Low"].rolling(exit, min_periods=exit).min().shift(1)
    xhi = df["High"].rolling(exit, min_periods=exit).max().shift(1)
    state = np.zeros(len(df))
    c = df["Close"].values
    hi_v, lo_v, xlo_v, xhi_v = hi.values, lo.values, xlo.values, xhi.values
    cur = 0
    for i in range(len(df)):
        if cur == 0:
            if c[i] > hi_v[i]:
                cur = 1
            elif c[i] < lo_v[i]:
                cur = -1
        elif cur == 1 and c[i] < xlo_v[i]:
            cur = 0
        elif cur == -1 and c[i] > xhi_v[i]:
            cur = 0
        state[i] = cur
    return pd.Series(state, index=df.index)


@strategy("dual_momentum", "trend", grid={"n": [120, 252]}, n=252)
def _dual_momentum(df, n):
    # Single-asset absolute momentum: long when trailing return positive, else flat.
    return (df["Close"] / df["Close"].shift(n) - 1 > 0).astype(float)


@strategy("elder_ray", "trend", grid={"n": [13, 21]}, n=13)
def _elder_ray(df, n):
    bull, bear, e = elder_ray(df, n)
    rising = e.diff() > 0
    pos = pd.Series(0.0, index=df.index)
    pos[rising & (bull > 0)] = 1
    pos[(~rising) & (bear < 0)] = -1
    return _hold(pos)


# =========================================================================== #
# MEAN REVERSION
# =========================================================================== #

@strategy("rsi_revert", "meanrev",
          grid={"n": [2, 3, 7, 14], "bounds": [(10, 90), (20, 80), (30, 70)]},
          n=14, bounds=(30, 70))
def _rsi_revert(df, n, bounds):
    low, high = bounds
    r = rsi(df["Close"], n)
    pos = pd.Series(np.nan, index=df.index)
    pos[r < low] = 1
    pos[r > high] = -1
    return pos.ffill().fillna(0)


@strategy("bollinger_revert", "meanrev",
          grid={"n": [20, 50], "k": [2.0, 2.5, 3.0]}, n=20, k=2.0)
def _bollinger_revert(df, n, k):
    m, sd = sma(df["Close"], n), rolling_std(df["Close"], n)
    pos = pd.Series(np.nan, index=df.index)
    pos[df["Close"] < m - k * sd] = 1
    pos[df["Close"] > m + k * sd] = -1
    cross = ((df["Close"] >= m) & (df["Close"].shift(1) < m)) | \
            ((df["Close"] <= m) & (df["Close"].shift(1) > m))
    pos[cross] = 0
    return pos.ffill().fillna(0)


@strategy("zscore_revert", "meanrev",
          grid={"n": [20, 50], "entry": [1.0, 1.5, 2.0]},
          n=20, entry=1.5, exit=0.5)
def _zscore_revert(df, n, entry, exit):
    z = (df["Close"] - sma(df["Close"], n)) / rolling_std(df["Close"], n).replace(0, np.nan)
    pos = pd.Series(np.nan, index=df.index)
    pos[z <= -entry] = 1
    pos[z >= entry] = -1
    pos[z.abs() <= exit] = 0
    return pos.ffill().fillna(0)


@strategy("stochastic", "meanrev", grid={"n": [14, 21]}, n=14, d=3,
          low=20, high=80)
def _stochastic(df, n, d, low, high):
    _, dline = stochastic(df, n, d)
    pos = pd.Series(np.nan, index=df.index)
    pos[dline < low] = 1
    pos[dline > high] = -1
    return pos.ffill().fillna(0)


@strategy("cci", "meanrev", grid={"n": [20, 40], "level": [100, 150]},
          n=20, level=100)
def _cci(df, n, level):
    c = cci(df, n)
    pos = pd.Series(np.nan, index=df.index)
    pos[c < -level] = 1
    pos[c > level] = -1
    return pos.ffill().fillna(0)


@strategy("williams_r", "meanrev", grid={"n": [10, 14, 21]}, n=14,
          low=-80, high=-20)
def _williams_r(df, n, low, high):
    wr = williams_r(df, n)
    pos = pd.Series(np.nan, index=df.index)
    pos[wr < low] = 1
    pos[wr > high] = -1
    return pos.ffill().fillna(0)


@strategy("keltner_revert", "meanrev", grid={"mult": [1.5, 2.0, 2.5]},
          n=20, atr_n=10, mult=2.0)
def _keltner_revert(df, n, atr_n, mult):
    mid, a = ema(df["Close"], n), atr(df, atr_n)
    pos = pd.Series(np.nan, index=df.index)
    pos[df["Close"] < mid - mult * a] = 1
    pos[df["Close"] > mid + mult * a] = -1
    cross = ((df["Close"] >= mid) & (df["Close"].shift(1) < mid)) | \
            ((df["Close"] <= mid) & (df["Close"].shift(1) > mid))
    pos[cross] = 0
    return pos.ffill().fillna(0)


@strategy("vwap_revert", "meanrev",
          grid={"n": [20, 50], "k": [1.0, 1.5]}, n=20, k=1.0)
def _vwap_revert(df, n, k):
    vw = rolling_vwap(df, n)
    dev = df["Close"] - vw
    band = k * dev.rolling(n, min_periods=n).std(ddof=0)
    pos = pd.Series(np.nan, index=df.index)
    pos[dev < -band] = 1
    pos[dev > band] = -1
    pos[dev.abs() <= 1e-12] = 0
    return pos.ffill().fillna(0)


@strategy("percent_b", "meanrev", grid={"n": [20, 50]}, n=20, k=2.0)
def _percent_b(df, n, k):
    m, sd = sma(df["Close"], n), rolling_std(df["Close"], n)
    upper, lower = m + k * sd, m - k * sd
    pb = (df["Close"] - lower) / (upper - lower).replace(0, np.nan)
    pos = pd.Series(np.nan, index=df.index)
    pos[pb < 0] = 1
    pos[pb > 1] = -1
    pos[(pb >= 0.5 - 1e-6) & (pb <= 0.5 + 1e-6)] = 0
    return pos.ffill().fillna(0)


@strategy("connors_rsi", "meanrev", grid={"bounds": [(10, 90), (20, 80)]},
          rsi_n=3, streak_n=2, rank_n=100, bounds=(10, 90))
def _connors(df, rsi_n, streak_n, rank_n, bounds):
    low, high = bounds
    crsi = connors_rsi(df["Close"], rsi_n, streak_n, rank_n)
    pos = pd.Series(np.nan, index=df.index)
    pos[crsi < low] = 1
    pos[crsi > high] = -1
    return pos.ffill().fillna(0)


@strategy("ultimate_oscillator", "meanrev", grid={"bounds": [(30, 70), (35, 65)]},
          bounds=(30, 70))
def _ult(df, bounds):
    low, high = bounds
    u = ultimate_osc(df)
    pos = pd.Series(np.nan, index=df.index)
    pos[u < low] = 1
    pos[u > high] = -1
    return pos.ffill().fillna(0)


# NOTE: a "gap_fade" family used to live here. It was removed: the central
# one-bar signal shift in _finalize means any signal computed from today's Open
# executes tomorrow — a gap fade that trades the day AFTER the gap is not a gap
# fade. Re-add only with an explicit intraday execution model (see TODO.md #7).


# =========================================================================== #
# VOLUME
# =========================================================================== #

@strategy("obv_trend", "volume", grid={"n": [20, 50, 100]}, n=20)
def _obv_trend(df, n):
    o = obv(df)
    return np.where(o > sma(o, n), 1, -1)


@strategy("chaikin_money_flow", "volume",
          grid={"n": [20], "threshold": [0.05, 0.10]}, n=20, threshold=0.05)
def _cmf(df, n, threshold):
    c = cmf(df, n)
    return np.where(c > threshold, 1, np.where(c < -threshold, -1, 0))


@strategy("money_flow_index", "volume", grid={"bounds": [(20, 80), (15, 85)]},
          n=14, bounds=(20, 80))
def _mfi(df, n, bounds):
    low, high = bounds
    m = mfi(df, n)
    pos = pd.Series(np.nan, index=df.index)
    pos[m < low] = 1
    pos[m > high] = -1
    return pos.ffill().fillna(0)


@strategy("volume_surge", "volume", grid={"mult": [1.5, 2.0]},
          n=20, mult=1.5)
def _volume_surge(df, n, mult):
    surge = df["Volume"] > mult * sma(df["Volume"], n)
    ret = df["Close"].pct_change()
    pos = pd.Series(np.nan, index=df.index)
    pos[surge & (ret > 0)] = 1
    pos[surge & (ret < 0)] = -1
    return pos.ffill().fillna(0)


@strategy("force_index", "volume", grid={"n": [13, 21, 34]}, n=13)
def _force(df, n):
    return np.sign(force_index(df, n))


@strategy("chaikin_oscillator", "volume",
          grid={"params": [(3, 10), (5, 20)]}, params=(3, 10))
def _chaikin(df, params):
    return np.sign(chaikin_osc(df, *params))


# =========================================================================== #
# VOLATILITY
# =========================================================================== #

@strategy("atr_breakout", "volatility",
          grid={"mult": [1.5, 2.0, 2.5, 3.0]}, n=20, atr_n=14, mult=2.0)
def _atr_breakout(df, n, atr_n, mult):
    mid, a = sma(df["Close"], n), atr(df, atr_n)
    upper, lower = (mid + mult * a).shift(1), (mid - mult * a).shift(1)
    pos = pd.Series(0.0, index=df.index)
    pos[df["Close"] > upper] = 1
    pos[df["Close"] < lower] = -1
    return _hold(pos)


@strategy("volatility_breakout", "volatility",
          grid={"k": [0.5, 1.0, 1.5, 2.0]}, k=1.0, atr_n=14)
def _vol_breakout(df, k, atr_n):
    a = atr(df, atr_n)
    up = df["Close"].shift(1) + k * a
    dn = df["Close"].shift(1) - k * a
    pos = pd.Series(0.0, index=df.index)
    pos[df["Close"] > up] = 1
    pos[df["Close"] < dn] = -1
    return _hold(pos)


@strategy("squeeze_breakout", "volatility",
          grid={"kc_mult": [1.5, 2.0]}, n=20, k=2.0, atr_n=20, kc_mult=1.5)
def _squeeze(df, n, k, atr_n, kc_mult):
    m, sd = sma(df["Close"], n), rolling_std(df["Close"], n)
    a = atr(df, atr_n)
    bb_up, bb_dn = m + k * sd, m - k * sd
    kc_up, kc_dn = m + kc_mult * a, m - kc_mult * a
    squeeze_on = (bb_up < kc_up) & (bb_dn > kc_dn)
    released = squeeze_on.shift(1).fillna(False) & ~squeeze_on
    mom = np.sign(df["Close"] - m)
    pos = pd.Series(np.nan, index=df.index)
    pos[released] = mom[released]
    return pos.ffill().fillna(0)


# =========================================================================== #
# PATTERN
# =========================================================================== #

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


@strategy("three_bar_reversal", "pattern")
def _three_bar(df):
    c = df["Close"]
    down = (c.shift(2) < c.shift(3)) & (c.shift(1) < c.shift(2))
    up = (c.shift(2) > c.shift(3)) & (c.shift(1) > c.shift(2))
    pos = pd.Series(np.nan, index=df.index)
    pos[down & (c > c.shift(1))] = 1
    pos[up & (c < c.shift(1))] = -1
    return pos.ffill().fillna(0)


@strategy("higher_highs_lows", "pattern", grid={"n": [5, 10, 20]}, n=10)
def _hh_ll(df, n):
    hh = (df["High"] > df["High"].shift(1)) & (df["Low"] > df["Low"].shift(1))
    ll = (df["High"] < df["High"].shift(1)) & (df["Low"] < df["Low"].shift(1))
    up = hh.rolling(n, min_periods=n).sum() == n
    dn = ll.rolling(n, min_periods=n).sum() == n
    pos = pd.Series(np.nan, index=df.index)
    pos[up] = 1
    pos[dn] = -1
    return pos.ffill().fillna(0)


@strategy("pivot_bounce", "pattern", grid={"n": [10, 20]}, n=10)
def _pivot_bounce(df, n):
    pivot = ((df["High"] + df["Low"] + df["Close"]) / 3).shift(1)
    s1 = 2 * pivot - df["High"].shift(1)
    r1 = 2 * pivot - df["Low"].shift(1)
    pos = pd.Series(np.nan, index=df.index)
    pos[df["Low"] <= s1] = 1
    pos[df["High"] >= r1] = -1
    cross = ((df["Close"] >= pivot) & (df["Close"].shift(1) < pivot)) | \
            ((df["Close"] <= pivot) & (df["Close"].shift(1) > pivot))
    pos[cross] = 0
    return pos.ffill().fillna(0)


# =========================================================================== #
# COMPOSITE
# =========================================================================== #

@strategy("macd_rsi_confirm", "composite", grid={"rsi_min": [45, 50, 55]},
          rsi_n=14, rsi_min=50)
def _macd_rsi(df, rsi_n, rsi_min):
    line, sig, _ = macd(df["Close"])
    r = rsi(df["Close"], rsi_n)
    return np.where((line > sig) & (r > rsi_min), 1,
                    np.where((line < sig) & (r < 100 - rsi_min), -1, 0))


@strategy("triple_screen", "composite", grid={"trend_n": [100, 200]},
          trend_n=200, k_low=30, k_high=70)
def _triple_screen(df, trend_n, k_low, k_high):
    trend = np.sign(ema(df["Close"], trend_n).diff())
    _, dline = stochastic(df, 14, 3)
    pos = pd.Series(np.nan, index=df.index)
    pos[(trend > 0) & (dline < k_low)] = 1     # buy pullbacks in uptrend
    pos[(trend < 0) & (dline > k_high)] = -1   # sell rallies in downtrend
    pos[trend == 0] = 0
    return pos.ffill().fillna(0)


@strategy("chandelier", "composite", grid={"mult": [2.0, 2.5, 3.0]},
          n=22, atr_n=22, mult=3.0)
def _chandelier(df, n, atr_n, mult):
    a = atr(df, atr_n)
    long_stop = df["High"].rolling(n, min_periods=n).max() - mult * a
    short_stop = df["Low"].rolling(n, min_periods=n).min() + mult * a
    pos = pd.Series(np.nan, index=df.index)
    pos[df["Close"] > short_stop] = 1
    pos[df["Close"] < long_stop] = -1
    return pos.ffill().fillna(0)


# --------------------------------------------------------------------------- #
# Parameter grid -> config builder
# --------------------------------------------------------------------------- #

def build_configs() -> List[Tuple[str, Strategy, Dict, str]]:
    """Expand every family across its parameter grid.

    Returns a list of (name, function, params, category) tuples. The function is
    the registered Strategy (callable as ``fn(df, **params)``). Invalid param
    combinations (e.g. fast >= slow) are filtered out.
    """
    configs: List[Tuple[str, Strategy, Dict, str]] = []
    for name, st in STRATEGIES.items():
        grid = st.grid if st.grid else {}
        if not grid:
            configs.append((name, st, dict(st.params), st.category))
            continue
        keys = list(grid.keys())
        for combo in itertools.product(*(grid[k] for k in keys)):
            params = dict(zip(keys, combo))
            full = {**st.params, **params}
            if "fast" in full and "slow" in full and full["fast"] >= full["slow"]:
                continue
            if "entry" in full and "exit" in full and full["exit"] >= full["entry"]:
                continue
            label = ",".join(f"{k}={v}" for k, v in params.items())
            configs.append((f"{name}[{label}]", st, full, st.category))
    return configs


# --------------------------------------------------------------------------- #
# Self-test / CLI
# --------------------------------------------------------------------------- #

def _self_test(data: Dict[str, pd.DataFrame]) -> None:
    configs = build_configs()
    by_cat_fam: Dict[str, int] = {c: 0 for c in CATEGORIES}
    for st in STRATEGIES.values():
        by_cat_fam[st.category] += 1
    by_cat_cfg: Dict[str, int] = {c: 0 for c in CATEGORIES}
    for _, _, _, cat in configs:
        by_cat_cfg[cat] += 1

    print("\n" + "=" * 70)
    print("STRATEGY LIBRARY")
    print("=" * 70)
    print(f"{'category':12s}{'families':>10}{'configs':>10}")
    for c in CATEGORIES:
        print(f"{c:12s}{by_cat_fam[c]:>10}{by_cat_cfg[c]:>10}")
    print("-" * 32)
    print(f"{'TOTAL':12s}{len(STRATEGIES):>10}{len(configs):>10}")
    n_assets = len(data) if data else len(ALL_TICKERS)
    print(f"\nTOTAL CONFIGS: {len(configs)}")
    print(f"x {n_assets} assets = {len(configs) * n_assets} backtests")

    if not data:
        print("\n(no market data available — skipping live signal test)")
        return

    key = "SPY" if "SPY" in data else next(iter(data))
    sample = data[key]
    print(f"\nValidating every config on {key} ({len(sample)} bars) ...")
    fails = 0
    for name, fn, params, _ in configs:
        pos = fn(sample, **params)
        vals = set(pd.unique(pos.dropna()))
        ok = (vals.issubset({-1, 0, 1}) and pos.iloc[0] == 0
              and len(pos) == len(sample) and pos.dtype.kind in "iu")
        if not ok:
            fails += 1
            print(f"  FAIL {name}: vals={vals} first={pos.iloc[0]}")
    print(f"Validation: {len(configs) - fails}/{len(configs)} configs passed"
          + ("  ALL PASS" if fails == 0 else f"  {fails} FAILED"))


def main():
    print("Layer 1 — downloading universe ...\n")
    data = download_data()
    _self_test(data)
    print("\nDone. Import from later layers via:")
    print("    from layer1_data_strategies import "
          "load_universe, build_configs, run_strategy")


if __name__ == "__main__":
    main()
