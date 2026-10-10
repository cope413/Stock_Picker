"""landry.rs_test on synthetic weekly closes: the panel's signal/streak/state logic and the same-date comparisons."""
import numpy as np
import pandas as pd
import pytest

from landry import rs_test


def _closes(weeks=260, n_up=5, n_down=5, n_flat=4):
    """SPY flat; 'UPn' names compound up steadily, 'DNn' down, 'FLn' flat. Trends persist, so leaders keep leading."""
    idx = pd.date_range("2020-01-03", periods=weeks, freq="W-FRI")
    k = np.arange(weeks)
    d = {"SPY": np.full(weeks, 100.0)}
    for i in range(n_up):
        d[f"UP{i}"] = 100 * (1 + 0.004 + 0.0005 * i) ** k
    for i in range(n_down):
        d[f"DN{i}"] = 100 * (1 - 0.004 - 0.0005 * i) ** k
    for i in range(n_flat):
        d[f"FL{i}"] = 100.0 + 0 * k
    return pd.DataFrame(d, index=idx)


def test_panel_signal_matches_the_rubric_by_hand():
    c = _closes()
    p = rs_test.panel(c)
    last = p[p["date"] == c.index[-1]].set_index("ticker")
    up = c["UP0"]
    by_hand = ((up.iloc[-1] / up.iloc[-27] - 1) + (up.iloc[-1] / up.iloc[-53] - 1)) / 2     # SPY is flat
    assert last.loc["UP0", "rs"] == pytest.approx(by_hand)
    assert last.loc["UP0", "score"] == 5 and last.loc["DN4", "score"] == 1 and last.loc["FL0", "score"] == 3
    assert last.loc["FL0", "state"] == "flat" and last.loc["FL0", "streak"] == 0
    assert pd.isna(last.loc["UP0", "fwd_26"])                       # the future is not in yet
    first = p[p["ticker"] == "UP0"].iloc[0]
    i = list(c.index).index(first["date"])
    assert first["fwd_26"] == pytest.approx(up.iloc[i + 26] / up.iloc[i] - 1)
    assert first["streak"] == 1 and first["state"] == "up, not persistent"


def test_streak_counts_and_resets_and_persistence_needs_a_further_move():
    idx = pd.date_range("2020-01-03", periods=200, freq="W-FRI")
    k = np.arange(200)
    falling_then_rising = np.where(k < 120, 100 * 0.995 ** k, 100 * 0.995 ** 120 * 1.02 ** (k - 120))
    c = pd.DataFrame({"SPY": 100.0, "X": falling_then_rising, "A": 100.0, "B": 100.0}, index=idx)
    p = rs_test.panel(c)
    x = p[p["ticker"] == "X"].reset_index(drop=True)
    assert list(x["streak"][:3]) == [-1, -2, -3]
    assert (x["streak"] > 0).any() and x["streak"].iloc[-1] > 0     # the sign flip restarts the count
    flip = x.index[x["streak"] == 1][0]
    assert x["streak"].iloc[flip - 1] < 0
    # a constant-rate decline has a constant gap: same sign for 3+ readings but not moving further -> not persistent
    assert x["state"].iloc[2] == "down, not persistent"


def test_comparisons_find_persistence_where_it_exists():
    c = _closes()
    p = rs_test.panel(c)
    s = rs_test.tests(p, 26, 13)["leaders (score 4-5) minus laggards (score 1-2)"]
    assert s["mean"] > 0.15 and s["positive"] == 1.0 and s["dates"] >= 3 and s["t"] > 2
    ic = rs_test.information_coefficient(p, 26, 13)
    assert ic["mean"] > 0.8 and ic["dates"] >= 3
    g = {r["group"]: r for r in rs_test.groups(p, "score", 26)}
    assert g[5]["mean"] > 0 > g[1]["mean"] and g[5]["beat"] == 1.0 and g[1]["beat"] == 0.0


def test_spacing_is_non_overlapping_and_report_prints():
    dates = list(range(10))
    assert rs_test._spaced(dates, 13, 26) == [1, 3, 5, 7, 9] and rs_test._spaced(dates, 13, 52) == [1, 5, 9]
    assert rs_test._tstat([1.0, 1.0, 1.0]) is None and rs_test._tstat([1.0, 2.0]) is None
    text = rs_test.format_report(_closes())
    assert "next 26 weeks" in text and "next 52 weeks" in text and "limits:" in text
    with pytest.raises(ValueError):
        rs_test.panel(_closes().drop(columns=["SPY"]))


def test_scoring_trend_matches_the_test_definition_and_drives_the_actions():
    from landry import rs_trend
    from landry.data_auto import draft_relative_strength, relative_strength
    idx = pd.date_range("2020-01-03", periods=260, freq="W-FRI")
    k = np.arange(260)
    c = pd.DataFrame({
        "SPY": 100.0 + 0 * k,
        "ACC": 100 * np.exp(-0.00002 * k ** 2),          # falling faster and faster: down and widening
        "LIN": 100 * 0.996 ** k,                         # steady decline: constant gap, not widening
        "RISE": 100 * np.exp(0.00002 * k ** 2),          # rising faster and faster
        "FLAT": 100.0 + 0 * k}, index=idx)
    p = rs_test.panel(c)
    last = p[p["date"] == idx[-1]].set_index("ticker")
    for t in ("ACC", "LIN", "RISE", "FLAT"):
        rs = relative_strength(c[t], c["SPY"])
        assert rs.trend == last.loc[t, "state"] and rs.diff_blended == pytest.approx(last.loc[t, "rs"])
        assert rs.streak == max(-8, min(8, int(last.loc[t, "streak"])))          # the scoring read keeps 8 readings
    acc, lin, rise = (relative_strength(c[t], c["SPY"]) for t in ("ACC", "LIN", "RISE"))
    assert acc.persistent_down and acc.score == 1 and acc.diff_3m < 0
    assert lin.trend == "down, not persistent" and rise.persistent_up
    assert "PERSISTENT DOWN (Rule 5 review)" in draft_relative_strength(acc).rationale
    assert "persistent up" in draft_relative_strength(rise).rationale and "3mo" in draft_relative_strength(lin).rationale
    assert rs_trend.action(acc, held=True).startswith("RULE 5 (persistent)")
    assert rs_trend.action(acc, held=False).startswith("Rule 5 review")
    assert rs_trend.action(rise, held=False).startswith("tie-breaker") and rs_trend.action(rise, held=True) == ""
    assert rs_trend.action(lin, held=True) == ""
    rws = rs_trend.rows(c, ["ACC", "LIN", "RISE", "NOPE"], held=["ACC", "LIN"])
    assert rs_trend.persistent_down_held(rws) == ["ACC"]
    text = rs_trend.format_report(rws)
    assert "held and persistent down (Rule 5 review, no additions): ACC" in text and "no price history" in text
