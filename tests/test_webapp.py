"""Tests for the web UI backend. Offline; heavy pipeline calls are stubbed.

Run with: pytest -q
"""
from __future__ import annotations

import json
import time

import pandas as pd
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import layer5_validation as V              # noqa: E402
import layer6_portfolio as P               # noqa: E402
import webapp as W                         # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "HOLDOUT_CSV", str(tmp_path / "holdout.csv"))
    monkeypatch.setattr(P, "CLUSTERS_CSV", str(tmp_path / "clusters.csv"))
    monkeypatch.setattr(P, "PORTFOLIO_JSON", str(tmp_path / "portfolio.json"))
    monkeypatch.setattr(W, "SIGNALS_JSON", str(tmp_path / "signals.json"))
    monkeypatch.setattr(W, "JOB", W._Job())
    return TestClient(W.app)


@pytest.mark.unit
def test_status_empty_state(client):
    st = client.get("/api/status").json()
    assert st["job"]["state"] == "idle"
    assert st["portfolio"] is None
    assert st["artifacts"] == {"holdout": False, "clusters": False,
                               "portfolio": False, "signals": False}
    assert "corr_threshold" in st["defaults"]


@pytest.mark.unit
def test_artifact_endpoints_404_then_200(client, tmp_path):
    for ep in ("/api/holdout", "/api/clusters", "/api/portfolio",
               "/api/signals"):
        assert client.get(ep).status_code == 404

    pd.DataFrame([{"config": "c", "asset": "SPY", "holdout_sharpe": 0.4,
                   "confirmed": True}]).to_csv(V.HOLDOUT_CSV, index=False)
    with open(P.PORTFOLIO_JSON, "w") as f:
        json.dump({"weights": {"c @ SPY": 0.5}, "n_strategies": 1,
                   "achieved_vol": 0.1, "gross_leverage": 0.5}, f)

    ho = client.get("/api/holdout").json()
    assert ho[0]["asset"] == "SPY"
    spec = client.get("/api/portfolio").json()
    assert spec["n_strategies"] == 1
    st = client.get("/api/status").json()
    assert st["artifacts"]["holdout"] and st["artifacts"]["portfolio"]
    assert st["portfolio"]["n_strategies"] == 1


@pytest.mark.unit
def test_job_runner_and_conflict(client, monkeypatch):
    release = {"go": False}

    def slow():
        while not release["go"]:
            time.sleep(0.01)
        print("finished")

    monkeypatch.setattr(W, "_do_refresh", slow)
    assert client.post("/api/run/refresh").json() == {"started": True}
    assert client.post("/api/run/refresh").status_code == 409
    assert client.post("/api/run/signals").status_code == 409

    release["go"] = True
    for _ in range(200):
        snap = client.get("/api/log").json()
        if snap["state"] == "done":
            break
        time.sleep(0.01)
    assert snap["state"] == "done"
    assert "finished" in snap["log"]
    # runner is free again
    assert client.post("/api/run/refresh").json() == {"started": True}


@pytest.mark.unit
def test_job_error_captured(client, monkeypatch):
    monkeypatch.setattr(W, "_do_refresh",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    client.post("/api/run/refresh")
    for _ in range(200):
        snap = client.get("/api/log").json()
        if snap["state"] in ("done", "error"):
            break
        time.sleep(0.01)
    assert snap["state"] == "error"
    assert "boom" in snap["error"]


@pytest.mark.unit
def test_signals_job_requires_spec(client, monkeypatch):
    client.post("/api/run/signals")
    for _ in range(200):
        snap = client.get("/api/log").json()
        if snap["state"] in ("done", "error"):
            break
        time.sleep(0.01)
    assert snap["state"] == "error"
    assert "No saved portfolio spec" in snap["error"]


@pytest.mark.unit
def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Stock_Picker" in r.text
