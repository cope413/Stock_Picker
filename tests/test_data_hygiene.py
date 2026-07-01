"""Tests for the Layer 1 data-hygiene additions (TODO #10).

All offline: staleness and slicing are tested through the cache path with a
stub yfinance module so no network is touched. Run with: pytest -q
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pandas as pd
import pytest

import layer1_data_strategies as L


def _frame(start="2020-01-01", periods=800, freq="B", seed=0):
    g = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=periods, freq=freq)
    close = 100 * np.exp(np.cumsum(g.normal(0.0004, 0.01, periods)))
    return pd.DataFrame({"Open": np.r_[close[0], close[:-1]],
                         "High": close * 1.004, "Low": close * 0.996,
                         "Close": close,
                         "Volume": np.full(periods, 2e6)}, index=idx)


# --------------------------------------------------------------------------- #
# resolve_end
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_resolve_end():
    today = pd.Timestamp.today().normalize().strftime("%Y-%m-%d")
    assert L.resolve_end("today") == today
    assert L.resolve_end(None) == today
    assert L.resolve_end("ToDay") == today
    assert L.resolve_end("2024-06-30") == "2024-06-30"


# --------------------------------------------------------------------------- #
# cache_is_stale
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_fresh_cache_not_stale():
    df = _frame()
    end = (df.index.max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    assert L.cache_is_stale(df, "SPY", "2020-01-01", end) is None


@pytest.mark.unit
def test_cache_stale_when_end_moves_forward():
    df = _frame()
    end = (df.index.max() + pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    reason = L.cache_is_stale(df, "SPY", "2020-01-01", end)
    assert reason is not None and "behind requested end" in reason


@pytest.mark.unit
def test_weekend_tolerance_no_false_staleness():
    # cache ends Friday 2024-06-28; requesting Monday 2024-07-01 must NOT
    # trigger a re-download (0 business days strictly between them)
    idx = pd.bdate_range("2023-01-02", "2024-06-28")
    df = _frame(periods=len(idx))
    df.index = idx
    assert L.cache_is_stale(df, "SPY", "2023-01-02", "2024-07-01") is None


@pytest.mark.unit
def test_crypto_staleness_uses_calendar_days():
    idx = pd.date_range("2023-01-01", "2024-06-28", freq="D")
    df = _frame(periods=len(idx), freq="D")
    df.index = idx
    # 2 calendar days missing (29th, 30th) -> within tolerance
    assert L.cache_is_stale(df, "BTC-USD", "2023-01-01", "2024-07-01") is None
    # 5 missing -> stale
    assert L.cache_is_stale(df, "BTC-USD", "2023-01-01", "2024-07-04") is not None


@pytest.mark.unit
def test_cache_stale_when_start_widens(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "CACHE_DIR", str(tmp_path))
    df = _frame()
    L._write_meta("SPY", "2020-01-01", "2023-01-01")
    end = (df.index.max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    assert L.cache_is_stale(df, "SPY", "2020-01-01", end) is None
    reason = L.cache_is_stale(df, "SPY", "2015-01-01", end)
    assert reason is not None and "requested start" in reason


# --------------------------------------------------------------------------- #
# validate_data
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_clean_frame_has_no_issues():
    assert L.validate_data(_frame(), "SPY") == []


@pytest.mark.unit
def test_detects_nonpositive_and_duplicates():
    df = _frame()
    df.iloc[10, df.columns.get_loc("Close")] = -1.0
    df = pd.concat([df, df.iloc[[20]]]).sort_index()
    sevs = [s for s, _ in L.validate_data(df, "SPY")]
    assert sevs.count("critical") >= 2


@pytest.mark.unit
def test_detects_split_sized_return():
    df = _frame()
    df.iloc[100:, df.columns.get_loc("Close")] /= 4.0     # fake unadjusted 4:1
    msgs = [m for s, m in L.validate_data(df, "SPY") if s == "warn"]
    assert any("unadjusted split" in m for m in msgs)


@pytest.mark.unit
def test_detects_zero_volume_and_gaps():
    df = _frame()
    df.iloc[:100, df.columns.get_loc("Volume")] = 0.0
    df = df.drop(df.index[200:230])                       # 30-bar hole
    msgs = [m for _, m in L.validate_data(df, "SPY")]
    assert any("zero volume" in m for m in msgs)
    assert any("gap" in m for m in msgs)


@pytest.mark.unit
def test_high_low_violation_warns():
    df = _frame()
    df.iloc[5, df.columns.get_loc("High")] = df.iloc[5]["Low"] - 1.0
    msgs = [m for s, m in L.validate_data(df, "SPY") if s == "warn"]
    assert any("High < Low" in m for m in msgs)


# --------------------------------------------------------------------------- #
# download_data through a stubbed yfinance (offline)
# --------------------------------------------------------------------------- #

@pytest.fixture()
def stub_yf(monkeypatch, tmp_path):
    """Stub yfinance; records calls; serves a long clean frame."""
    calls = []
    full = _frame(start="2018-01-01", periods=2000)

    mod = types.ModuleType("yfinance")

    def download(ticker, start=None, end=None, **kw):
        calls.append((ticker, start, end))
        return full.loc[(full.index >= pd.Timestamp(start))
                        & (full.index < pd.Timestamp(end))]

    mod.download = download
    monkeypatch.setitem(sys.modules, "yfinance", mod)
    monkeypatch.setattr(L, "CACHE_DIR", str(tmp_path))
    return calls, full


@pytest.mark.unit
def test_download_slices_to_requested_window(stub_yf, tmp_path):
    calls, full = stub_yf
    out = L.download_data(["SPY"], start="2018-01-01", end="2024-01-01",
                          min_bars=100, cache_dir=str(tmp_path),
                          verbose=False)
    assert len(calls) == 1
    # second call, narrower window, same cache: no new download, sliced result
    out2 = L.download_data(["SPY"], start="2019-01-01", end="2020-01-01",
                           min_bars=100, cache_dir=str(tmp_path),
                           verbose=False)
    assert len(calls) == 1
    assert out2["SPY"].index.min() >= pd.Timestamp("2019-01-01")
    assert out2["SPY"].index.max() < pd.Timestamp("2020-01-01")


@pytest.mark.unit
def test_download_refreshes_stale_cache(stub_yf, tmp_path):
    calls, full = stub_yf
    L.download_data(["SPY"], start="2018-01-01", end="2022-01-01",
                    min_bars=100, cache_dir=str(tmp_path), verbose=False)
    assert len(calls) == 1
    # asking for a later end must trigger a full re-download
    L.download_data(["SPY"], start="2018-01-01", end="2024-01-01",
                    min_bars=100, cache_dir=str(tmp_path), verbose=False)
    assert len(calls) == 2
    assert calls[1][1] == "2018-01-01"        # full range, not incremental


@pytest.mark.unit
def test_refresh_flag_forces_download(stub_yf, tmp_path):
    calls, _ = stub_yf
    for _ in range(2):
        L.download_data(["SPY"], start="2018-01-01", end="2024-01-01",
                        min_bars=100, cache_dir=str(tmp_path),
                        refresh=True, verbose=False)
    assert len(calls) == 2


@pytest.mark.unit
def test_strict_drops_critical_assets(monkeypatch, tmp_path):
    bad = _frame()
    bad.iloc[10, bad.columns.get_loc("Close")] = -5.0
    mod = types.ModuleType("yfinance")
    mod.download = lambda *a, **k: bad
    monkeypatch.setitem(sys.modules, "yfinance", mod)
    end = (bad.index.max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    kept = L.download_data(["SPY"], start="2020-01-01", end=end,
                           min_bars=100, cache_dir=str(tmp_path),
                           strict=True, verbose=False)
    assert kept == {}
    lax = L.download_data(["SPY"], start="2020-01-01", end=end,
                          min_bars=100, cache_dir=str(tmp_path),
                          strict=False, verbose=False)
    assert "SPY" in lax
