"""Web UI for the Stock_Picker pipeline.

A thin FastAPI layer over Layers 1–6. One background job at a time (build /
signals / data refresh), with captured logs streamed to the browser. Artifacts
(holdout table, clusters, portfolio spec, signals) are read from the same
files the CLI writes, so the web UI and the CLI stay interchangeable.

Run:
    pip install fastapi uvicorn
    python webapp.py            # http://127.0.0.1:8713
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import threading
import traceback
from typing import Dict, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import layer1_data_strategies as L
import layer5_validation as V
import layer6_portfolio as P

_HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(_HERE, "webui", "index.html")
SIGNALS_JSON = os.path.join(_HERE, "layer6_signals.json")

app = FastAPI(title="Stock_Picker")


# --------------------------------------------------------------------------- #
# Job runner — one at a time, logs captured
# --------------------------------------------------------------------------- #

class _Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.kind: Optional[str] = None
        self.state: str = "idle"        # idle | running | done | error
        self.log = io.StringIO()
        self.error: Optional[str] = None

    def snapshot(self) -> Dict:
        return {"kind": self.kind, "state": self.state,
                "log": self.log.getvalue()[-40000:], "error": self.error}


JOB = _Job()


def _launch(kind: str, fn) -> bool:
    """Start ``fn`` in a background thread unless a job is already running."""
    with JOB.lock:
        if JOB.state == "running":
            return False
        JOB.kind, JOB.state, JOB.error = kind, "running", None
        JOB.log = io.StringIO()

    def runner():
        try:
            with contextlib.redirect_stdout(JOB.log), \
                 contextlib.redirect_stderr(JOB.log):
                fn()
            JOB.state = "done"
        except Exception:
            JOB.error = traceback.format_exc(limit=8)
            JOB.state = "error"

    threading.Thread(target=runner, daemon=True).start()
    return True


# --------------------------------------------------------------------------- #
# Pipeline wrappers
# --------------------------------------------------------------------------- #

class BuildParams(BaseModel):
    corr_threshold: float = P.CORR_THRESHOLD
    target_vol: float = P.TARGET_VOL
    max_weight: float = P.MAX_WEIGHT
    max_gross: float = P.MAX_GROSS
    min_dsr: float = P.MIN_DSR
    require_confirmed: bool = True
    refresh: bool = False


def _write_signals(spec: Dict, data: Dict[str, pd.DataFrame]) -> None:
    sig = P.signals_today(spec, data)
    payload = {"rows": sig.to_dict(orient="records"),
               "net": (sig.groupby("asset")["exposure"].sum().to_dict()
                       if not sig.empty else {}),
               "as_of": (sig["as_of"].max() if not sig.empty else None),
               "generated_at": pd.Timestamp.now().isoformat()}
    with open(SIGNALS_JSON, "w") as f:
        json.dump(payload, f, indent=2)
    P.print_signals(sig)


def _do_build(params: BuildParams):
    data = L.load_universe(refresh=params.refresh)
    spec = P.build(data,
                   corr_threshold=params.corr_threshold,
                   target_vol=params.target_vol,
                   max_weight=params.max_weight,
                   max_gross=params.max_gross,
                   min_dsr=params.min_dsr,
                   require_confirmed=params.require_confirmed)
    if spec:
        _write_signals(spec, data)


def _do_signals(refresh: bool):
    if not os.path.exists(P.PORTFOLIO_JSON):
        raise RuntimeError("No saved portfolio spec — run a build first.")
    with open(P.PORTFOLIO_JSON) as f:
        spec = json.load(f)
    data = L.load_universe(refresh=refresh)
    _write_signals(spec, data)


def _do_refresh():
    L.load_universe(refresh=True)
    print("\nData refresh complete.")


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

@app.get("/")
def index():
    return FileResponse(INDEX_HTML)


@app.get("/api/status")
def status():
    spec = None
    if os.path.exists(P.PORTFOLIO_JSON):
        with open(P.PORTFOLIO_JSON) as f:
            spec = json.load(f)

    cache = {"tickers": 0, "newest_bar": None, "oldest_last_bar": None}
    if os.path.isdir(L.CACHE_DIR):
        last_bars = []
        for fn in os.listdir(L.CACHE_DIR):
            if not fn.endswith(".parquet"):
                continue
            try:
                idx = pd.read_parquet(
                    os.path.join(L.CACHE_DIR, fn), columns=[]).index
                if len(idx):
                    last_bars.append(idx.max())
            except Exception:
                continue
        if last_bars:
            cache = {"tickers": len(last_bars),
                     "newest_bar": max(last_bars).date().isoformat(),
                     "oldest_last_bar": min(last_bars).date().isoformat()}

    stale = None
    if cache["newest_bar"]:
        gap = len(pd.bdate_range(cache["newest_bar"],
                                 pd.Timestamp.today().normalize(),
                                 inclusive="neither"))
        stale = gap > L.MAX_END_GAP_BARS

    return {"job": JOB.snapshot(),
            "portfolio": spec,
            "cache": cache,
            "cache_stale": stale,
            "artifacts": {
                "holdout": os.path.exists(V.HOLDOUT_CSV),
                "clusters": os.path.exists(P.CLUSTERS_CSV),
                "portfolio": spec is not None,
                "signals": os.path.exists(SIGNALS_JSON),
            },
            "defaults": BuildParams().model_dump()}


@app.post("/api/run/build")
def run_build(params: BuildParams):
    if not _launch("build", lambda: _do_build(params)):
        raise HTTPException(409, "A job is already running.")
    return {"started": True}


@app.post("/api/run/signals")
def run_signals(refresh: bool = False):
    if not _launch("signals", lambda: _do_signals(refresh)):
        raise HTTPException(409, "A job is already running.")
    return {"started": True}


@app.post("/api/run/refresh")
def run_refresh():
    if not _launch("refresh", _do_refresh):
        raise HTTPException(409, "A job is already running.")
    return {"started": True}


@app.get("/api/log")
def log():
    return JOB.snapshot()


def _csv_json(path: str, index_name: Optional[str] = None):
    if not os.path.exists(path):
        raise HTTPException(404, f"{os.path.basename(path)} not found — "
                            "run a build first.")
    df = pd.read_csv(path)
    if index_name and df.columns[0].startswith("Unnamed"):
        df = df.rename(columns={df.columns[0]: index_name})
    return json.loads(df.to_json(orient="records"))


@app.get("/api/holdout")
def holdout():
    return _csv_json(V.HOLDOUT_CSV)


@app.get("/api/clusters")
def clusters():
    return _csv_json(P.CLUSTERS_CSV, index_name="strategy")


@app.get("/api/portfolio")
def portfolio():
    if not os.path.exists(P.PORTFOLIO_JSON):
        raise HTTPException(404, "No portfolio spec — run a build first.")
    with open(P.PORTFOLIO_JSON) as f:
        return json.load(f)


@app.get("/api/signals")
def signals():
    if not os.path.exists(SIGNALS_JSON):
        raise HTTPException(404, "No signals yet — run a build or "
                            "'Update signals' first.")
    with open(SIGNALS_JSON) as f:
        return json.load(f)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8713)
