"""Tests for the scan universe: S&P 100 subset + user-supplied custom tickers.

Run with: pytest -q
"""
from __future__ import annotations

import re

import pytest

import layer1_data_strategies as L

TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")


# --------------------------------------------------------------------------- #
# Built-in universe
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_sp100_group_present_and_liquid_sized():
    assert "sp100" in L.TICKERS
    assert len(L.TICKERS["sp100"]) >= 85          # top-100 minus large_cap dupes


@pytest.mark.unit
def test_no_duplicate_tickers_across_groups():
    flat = [t for group in L.TICKERS.values() for t in group]
    assert len(flat) == len(set(flat))


@pytest.mark.unit
def test_all_builtin_tickers_are_well_formed():
    for t in L.ALL_TICKERS:
        assert TICKER_RE.match(t), t


# --------------------------------------------------------------------------- #
# Custom tickers file
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_custom_tickers_missing_file_is_empty(tmp_path):
    assert L.custom_tickers(str(tmp_path / "nope.txt")) == []


@pytest.mark.unit
def test_custom_tickers_parses_comments_case_and_dupes(tmp_path):
    p = tmp_path / "universe_custom.txt"
    p.write_text(
        "# my watchlist\n"
        "\n"
        "brk-b\n"
        "SHOP  # growth\n"
        "shop\n"          # duplicate after uppercasing
        "AMD\n")
    assert L.custom_tickers(str(p)) == ["BRK-B", "SHOP", "AMD"]


@pytest.mark.unit
def test_custom_tickers_rejects_garbage(tmp_path):
    p = tmp_path / "universe_custom.txt"
    p.write_text("GOOD\nnot a ticker!!\n")
    with pytest.raises(ValueError, match="not a ticker"):
        L.custom_tickers(str(p))


@pytest.mark.unit
def test_universe_tickers_appends_custom_without_dupes(tmp_path, monkeypatch):
    p = tmp_path / "universe_custom.txt"
    p.write_text("SHOP\nSPY\n")                   # SPY already built in
    monkeypatch.setattr(L, "CUSTOM_TICKERS_FILE", str(p))
    uni = L.universe_tickers()
    assert uni == L.ALL_TICKERS + ["SHOP"]
    assert uni.count("SPY") == 1
