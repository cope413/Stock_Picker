"""Tests for the Landry section of the web UI backend. Offline; the real
workbook beside the repo is the only artifact used as-is — every JSON
artifact (score store, snapshot, actions) is pointed at a tmp dir.

Run with: pytest -q
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import auth as A                           # noqa: E402
import webapp as W                         # noqa: E402
from landry.approvals import ScoreStore    # noqa: E402
from landry.fundamentals import Draft      # noqa: E402

TEST_USER, TEST_PW = "tester", "correct-horse"

_WB_PRESENT = bool(glob.glob(os.path.join(
    os.path.dirname(os.path.abspath(W.__file__)),
    "LANDRY_SYSTEM_WORKBOOK_*.xlsx")))
needs_workbook = pytest.mark.skipif(
    not _WB_PRESENT, reason="no LANDRY_SYSTEM_WORKBOOK_*.xlsx in the repo")


@pytest.fixture()
def anon_client(tmp_path, monkeypatch):
    """App with one user; Landry JSON artifacts redirected to tmp_path.
    The workbook glob is left alone (tests that need it absent use the
    ``no_workbook`` fixture)."""
    monkeypatch.setattr(A, "USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setattr(A, "SECRET_FILE", str(tmp_path / "secret.key"))
    monkeypatch.setattr(W, "_FAILS", {})
    monkeypatch.setattr(W, "LANDRY_SCORES_FILE",
                        str(tmp_path / "landry_scores.json"))
    monkeypatch.setattr(W, "LANDRY_SNAPSHOT_FILE",
                        str(tmp_path / "landry_snapshot.json"))
    monkeypatch.setattr(W, "LANDRY_ACTIONS_FILE",
                        str(tmp_path / "landry_actions.json"))
    A.create_user(TEST_USER, TEST_PW)
    return TestClient(W.app)


@pytest.fixture()
def client(anon_client):
    r = anon_client.post("/api/login", json={"username": TEST_USER,
                                             "password": TEST_PW})
    assert r.status_code == 200
    return anon_client


@pytest.fixture()
def no_workbook(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "LANDRY_WORKBOOK_GLOB",
                        str(tmp_path / "LANDRY_SYSTEM_WORKBOOK_*.xlsx"))


def _propose(indicator="fcf_yield_trend", score=4, ticker="NVDA"):
    store = ScoreStore(W.LANDRY_SCORES_FILE)
    store.propose(ticker, Draft(indicator, score, "M", "test rationale"),
                  source="quant_draft")
    return store


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_landry_auth_required(anon_client):
    for ep in ("/api/landry/dashboard", "/api/landry/positions",
               "/api/landry/risk", "/api/landry/actions",
               "/api/landry/pending", "/api/landry/audit"):
        assert anon_client.get(ep).status_code == 401
    assert anon_client.post("/api/landry/approve", json={}).status_code == 401
    assert anon_client.post("/api/landry/reject", json={}).status_code == 401


# --------------------------------------------------------------------------- #
# dashboard
# --------------------------------------------------------------------------- #

@needs_workbook
@pytest.mark.unit
def test_dashboard_shape(client):
    r = client.get("/api/landry/dashboard")
    assert r.status_code == 200
    d = r.json()
    assert d["workbook"].startswith("LANDRY_SYSTEM_WORKBOOK_")
    assert d["rows"], "workbook has scored candidates"
    for row in d["rows"]:
        for key in ("ticker", "company", "date_scored", "tier1_avg",
                    "composite", "decision", "rule_flags", "recomputed",
                    "mismatch"):
            assert key in row
        assert isinstance(row["mismatch"], bool)
        assert len(row["rule_flags"]) == 4
    v = d["verdict"]
    assert set(v["actions"]) == {"act_now", "due_soon", "housekeeping"}
    assert sum(v["bands"].values()) > 0          # holdings are scored
    assert 0 < v["equity_pct"] < 1
    assert 0 < v["cash_pct"] < 1
    # no snapshot in the tmp dir -> stale, with a note saying so
    assert v["snapshot_age_days"] is None and v["snapshot_stale"]
    assert any("snapshot" in n for n in d["notes"])


@needs_workbook
@pytest.mark.unit
def test_dashboard_engine_agrees_with_workbook(client):
    d = client.get("/api/landry/dashboard").json()
    checked = 0
    for row in d["rows"]:
        rec = row["recomputed"]
        assert rec is not None and "error" not in rec
        if row["composite"] is not None:
            assert row["mismatch"] is False
            assert abs(rec["composite"] - row["composite"]) < 1e-6
            checked += 1
    assert checked > 0


@pytest.mark.unit
def test_dashboard_missing_workbook(client, no_workbook):
    r = client.get("/api/landry/dashboard")
    assert r.status_code == 200
    d = r.json()
    assert d["workbook"] is None and d["rows"] == []
    assert d["verdict"]["bands"] == {}
    assert any("workbook missing" in n for n in d["notes"])


# --------------------------------------------------------------------------- #
# positions
# --------------------------------------------------------------------------- #

@needs_workbook
@pytest.mark.unit
def test_positions(client):
    r = client.get("/api/landry/positions")
    assert r.status_code == 200
    d = r.json()
    assert d["positions"] and d["note"] is None
    for key in ("account", "ticker", "asset_class", "quantity",
                "market_value", "pct_of_portfolio"):
        assert key in d["positions"][0]
    assert d["equity_weights"]
    equity = {p["ticker"] for p in d["positions"]
              if p["asset_class"] == "Equity"}
    assert set(d["equity_weights"]) <= equity


@pytest.mark.unit
def test_positions_missing_workbook(client, no_workbook):
    r = client.get("/api/landry/positions")
    assert r.status_code == 200
    d = r.json()
    assert d["positions"] == [] and d["equity_weights"] == {}
    assert "workbook missing" in d["note"]


# --------------------------------------------------------------------------- #
# risk
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_risk_missing_snapshot(client):
    r = client.get("/api/landry/risk")
    assert r.status_code == 200
    d = r.json()
    assert d["asof"] is None and d["correlation"] == {}
    assert d["macro"] == {} and d["tickers"] == {}
    assert "snapshot missing" in d["note"]


@pytest.mark.unit
def test_risk_with_snapshot(client):
    asof = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    snap = {
        "asof": asof,
        "tickers": {"NVDA": {
            "technicals": {"above_200w_ma": True, "staging_ok": True,
                           "macd_positive_or_turning": True,
                           "supertrend_bullish": False},
            "beta": {"beta": 1.8, "source": "computed",
                     "size_reduction_pct": 25},
            "relative_strength": {"diff_blended": 0.12, "score": 5}}},
        "correlation": {"window_weeks": 104, "sufficient": True,
                        "flagged_pairs": [["NVDA", "AVGO", 0.82]],
                        "clusters": [["NVDA", "AVGO", "TSM"]],
                        "cluster_exposure": [{"cluster": ["NVDA", "AVGO",
                                                          "TSM"],
                                              "over_cap": True,
                                              "note": "32% > 25% cap"}],
                        "matrix": {}},
        "macro": {"conditions": {},
                  "active_effects": [{"condition": "curve inverted",
                                      "effects": ["reduce cyclicals"]}],
                  "unknown": ["hy_spreads"]},
    }
    with open(W.LANDRY_SNAPSHOT_FILE, "w") as f:
        json.dump(snap, f)

    d = client.get("/api/landry/risk").json()
    assert d["asof"] == asof and d["age_days"] == 0 and d["note"] is None
    assert d["correlation"]["flagged_pairs"] == [["NVDA", "AVGO", 0.82]]
    assert d["correlation"]["cluster_exposure"][0]["over_cap"] is True
    assert d["macro"]["active_effects"][0]["condition"] == "curve inverted"
    nvda = d["tickers"]["NVDA"]
    assert nvda["beta"] == 1.8 and nvda["size_reduction_pct"] == 25
    assert nvda["staging_ok"] is True and nvda["supertrend_bullish"] is False
    assert nvda["rs_diff_blended"] == 0.12


# --------------------------------------------------------------------------- #
# actions
# --------------------------------------------------------------------------- #

@needs_workbook
@pytest.mark.unit
def test_actions_live(client):
    _propose()                              # guarantees >= 1 open item
    r = client.get("/api/landry/actions")
    assert r.status_code == 200
    d = r.json()
    assert d["source"] == "live" and d["note"] is None
    assert d["actions"]
    for a in d["actions"]:
        assert {"priority", "ticker", "action", "rule"} <= set(a)
    assert any(a["rule"] == "Part 12" and "NVDA" in a["ticker"]
               for a in d["actions"])
    assert (d["counts"]["act_now"] + d["counts"]["due_soon"]
            + d["counts"]["housekeeping"]) == len(d["actions"])


@pytest.mark.unit
def test_actions_fallback_to_saved_file(client, no_workbook):
    with open(W.LANDRY_ACTIONS_FILE, "w") as f:
        json.dump({"date": "2026-08-09",
                   "actions": [{"priority": 1, "ticker": "NVDA",
                                "action": "Mandatory Sell (score 30)",
                                "rule": "Rule 33",
                                "deadline": "2026-09-08"}]}, f)
    d = client.get("/api/landry/actions").json()
    assert d["source"] == "file"
    assert "workbook missing" in d["note"] and "2026-08-09" in d["note"]
    assert len(d["actions"]) == 1 and d["counts"]["act_now"] == 1


@pytest.mark.unit
def test_actions_everything_missing(client, no_workbook):
    d = client.get("/api/landry/actions").json()
    assert d["source"] == "none" and d["actions"] == []
    assert "workbook missing" in d["note"]


# --------------------------------------------------------------------------- #
# approvals gate
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_pending_empty_then_populated(client):
    d = client.get("/api/landry/pending").json()
    assert d == {"pending": {}, "tickers": [], "count": 0}

    _propose("fcf_yield_trend", 4)
    _propose("fcf_margin_trend", 3)
    d = client.get("/api/landry/pending").json()
    assert d["count"] == 2 and d["tickers"] == ["NVDA"]
    assert set(d["pending"]["NVDA"]) == {"fcf_yield_trend",
                                         "fcf_margin_trend"}
    assert d["pending"]["NVDA"]["fcf_yield_trend"]["score"] == 4


@pytest.mark.unit
def test_approve_flow_with_override(client):
    _propose("fcf_yield_trend", 4)
    r = client.post("/api/landry/approve",
                    json={"ticker": "nvda", "indicator": "fcf_yield_trend",
                          "by": "Taylor", "score": 5, "confidence": "h"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and d["pending"] == {}
    assert d["approved"]["score"] == 5 and d["approved"]["confidence"] == "H"
    assert d["approved"]["original_score"] == 4
    assert d["approved"]["approved_by"] == "Taylor"
    # the store's Part 12 boundary now feeds the scorer
    approved = ScoreStore(W.LANDRY_SCORES_FILE).approved_scores("NVDA")
    assert approved["fcf_yield_trend"].score == 5
    # audit trail: newest first, approve on top with the override recorded
    au = client.get("/api/landry/audit").json()
    assert au["total"] == 2                  # propose + approve
    assert au["audit"][0]["action"] == "approve"
    assert au["audit"][0]["overridden"] is True


@pytest.mark.unit
def test_reject_flow(client):
    _propose("fcf_yield_trend", 4)
    r = client.post("/api/landry/reject",
                    json={"ticker": "NVDA", "indicator": "fcf_yield_trend",
                          "by": "Taylor", "reason": "stale filings"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "pending": {}}
    assert ScoreStore(W.LANDRY_SCORES_FILE).approved_scores("NVDA") == {}
    au = client.get("/api/landry/audit").json()
    assert au["audit"][0]["action"] == "reject"
    assert au["audit"][0]["reason"] == "stale filings"


@pytest.mark.unit
def test_approve_reject_validation(client):
    # nothing pending -> 400 with detail, never a 500
    r = client.post("/api/landry/approve",
                    json={"ticker": "NVDA", "indicator": "fcf_yield_trend",
                          "by": "Taylor"})
    assert r.status_code == 400 and "no pending draft" in r.json()["detail"]

    _propose("fcf_yield_trend", 4)
    bad = [
        {"ticker": "NVDA", "indicator": "fcf_yield_trend", "by": "  "},
        {"ticker": "", "indicator": "fcf_yield_trend", "by": "Taylor"},
        {"ticker": "NVDA", "indicator": "fcf_yield_trend", "by": "Taylor",
         "score": 7},
        {"ticker": "NVDA", "indicator": "fcf_yield_trend", "by": "Taylor",
         "confidence": "X"},
    ]
    for body in bad:
        r = client.post("/api/landry/approve", json=body)
        assert r.status_code == 400, body
        assert r.json()["detail"]

    r = client.post("/api/landry/reject",
                    json={"ticker": "NVDA", "indicator": "fcf_yield_trend",
                          "by": "Taylor", "reason": "   "})
    assert r.status_code == 400 and "reason" in r.json()["detail"]
    # a missing required field is a schema error (FastAPI 422)
    r = client.post("/api/landry/reject",
                    json={"ticker": "NVDA", "indicator": "fcf_yield_trend",
                          "by": "Taylor"})
    assert r.status_code == 422
    # after all the failures the draft is still pending
    assert client.get("/api/landry/pending").json()["count"] == 1


@pytest.mark.unit
def test_audit_caps_at_200_newest_first(client):
    entries = [{"at": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00",
                "action": "propose", "ticker": "T", "indicator": f"i{i}"}
               for i in range(250)]
    with open(W.LANDRY_SCORES_FILE, "w") as f:
        json.dump({"tickers": {}, "audit": entries}, f)
    d = client.get("/api/landry/audit").json()
    assert d["total"] == 250 and len(d["audit"]) == 200
    assert d["audit"][0]["indicator"] == "i249"     # newest first
    assert d["audit"][-1]["indicator"] == "i50"     # oldest 50 dropped


# --------------------------------------------------------------------------- #
# UI wiring
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_index_has_landry_views(client):
    html = client.get("/").text
    assert 'id="ltabs"' in html
    for tab in ("ldash", "lactions", "lapprovals", "lrisk"):
        assert f'data-t="{tab}"' in html
        assert f'<section id="{tab}"' in html
    for ep in ("/api/landry/dashboard", "/api/landry/actions",
               "/api/landry/pending", "/api/landry/approve",
               "/api/landry/reject", "/api/landry/audit",
               "/api/landry/risk"):
        assert ep in html
