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
import datetime as dt
import glob
import io
import json
import os
import threading
import time
import traceback
from dataclasses import asdict
from typing import Dict, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

import auth
import layer1_data_strategies as L
import layer5_validation as V
import layer6_portfolio as P
from landry import scoring as LS
from landry import xlsx_io as LX
from landry.approvals import ScoreStore
from landry.daily import run_daily
from landry.scoring import CONFIDENCE_LEVELS

_HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(_HERE, "webui", "index.html")
LOGIN_HTML = os.path.join(_HERE, "webui", "login.html")
SIGNALS_JSON = os.path.join(_HERE, "layer6_signals.json")

# Landry System artifacts — module-level so tests can monkeypatch them.
LANDRY_WORKBOOK_GLOB = os.path.join(_HERE, "LANDRY_SYSTEM_WORKBOOK_*.xlsx")
LANDRY_SCORES_FILE = os.path.join(_HERE, "landry_scores.json")
LANDRY_SNAPSHOT_FILE = os.path.join(_HERE, "landry_snapshot.json")
LANDRY_ACTIONS_FILE = os.path.join(_HERE, "landry_actions.json")
LANDRY_SNAPSHOT_STALE_DAYS = 7

app = FastAPI(title="Stock_Picker")


# --------------------------------------------------------------------------- #
# Auth — everything requires a session except the login flow and healthcheck
# --------------------------------------------------------------------------- #

_PUBLIC_PATHS = {"/login", "/api/login", "/healthz"}

# Per-IP failed-login lockout: after LOCKOUT_MAX failures within
# LOCKOUT_WINDOW seconds, reject further attempts until the window passes.
LOCKOUT_MAX = 10
LOCKOUT_WINDOW = 15 * 60
_FAILS: Dict[str, list] = {}            # ip -> [count, first_failure_ts]


def _client_ip(request: Request) -> str:
    # Behind cloudflared the peer address is the tunnel container; the real
    # client IP arrives in a header. Locally the peer address is real.
    return (request.headers.get("cf-connecting-ip")
            or (request.client.host if request.client else "?"))


def _locked_out(ip: str) -> bool:
    rec = _FAILS.get(ip)
    if not rec:
        return False
    if time.time() - rec[1] > LOCKOUT_WINDOW:
        del _FAILS[ip]
        return False
    return rec[0] >= LOCKOUT_MAX


def _note_failure(ip: str) -> None:
    rec = _FAILS.get(ip)
    if rec and time.time() - rec[1] <= LOCKOUT_WINDOW:
        rec[0] += 1
    else:
        _FAILS[ip] = [1, time.time()]


@app.middleware("http")
async def require_login(request: Request, call_next):
    user = auth.verify_token(request.cookies.get(auth.SESSION_COOKIE, ""))
    request.state.user = user
    if user is None and request.url.path not in _PUBLIC_PATHS:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated."},
                                status_code=401)
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


class LoginParams(BaseModel):
    username: str
    password: str


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/login")
def login_page(request: Request):
    if request.state.user:
        return RedirectResponse("/", status_code=303)
    return FileResponse(LOGIN_HTML)


@app.post("/api/login")
def login(p: LoginParams, request: Request):
    ip = _client_ip(request)
    if _locked_out(ip):
        raise HTTPException(429, "Too many failed attempts — "
                            "try again in a few minutes.")
    if not auth.verify_user(p.username, p.password):
        _note_failure(ip)
        time.sleep(0.5)                 # cheap brake on guessing
        raise HTTPException(401, "Wrong username or password.")
    _FAILS.pop(ip, None)
    resp = JSONResponse({"ok": True})
    secure = (request.headers.get("x-forwarded-proto",
                                  request.url.scheme) == "https")
    resp.set_cookie(auth.SESSION_COOKIE,
                    auth.issue_token(p.username.strip().lower()),
                    max_age=auth.SESSION_TTL, httponly=True,
                    samesite="lax", secure=secure, path="/")
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


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


LANDRY_GUIDE_HTML = os.path.join(_HERE, "docs", "LANDRY_GUIDE.html")


@app.get("/guide")
def landry_guide():
    """The Landry System user guide (docs/LANDRY_GUIDE.html)."""
    if not os.path.exists(LANDRY_GUIDE_HTML):
        raise HTTPException(404, "docs/LANDRY_GUIDE.html not found")
    return FileResponse(LANDRY_GUIDE_HTML)


@app.get("/api/status")
def status(request: Request):
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
            "user": request.state.user,
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


class UniverseUpdate(BaseModel):
    custom: list[str]


@app.get("/api/universe")
def universe():
    return {"groups": L.TICKERS,
            "custom": L.custom_tickers(),
            "total": len(L.universe_tickers())}


@app.post("/api/universe")
def set_universe(u: UniverseUpdate):
    """Replace the custom-ticker list; validated before anything is written."""
    tickers: list[str] = []
    for raw in u.custom:
        t = raw.strip().upper()
        if not t:
            continue
        if not L.TICKER_RE.match(t):
            raise HTTPException(422, f"{raw.strip()!r} is not a ticker symbol")
        if t not in tickers:
            tickers.append(t)
    with open(L.CUSTOM_TICKERS_FILE, "w") as f:
        f.write("# Custom tickers — one per line; edited via the web UI.\n")
        f.writelines(t + "\n" for t in tickers)
    return {"custom": tickers, "total": len(L.universe_tickers())}


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


# --------------------------------------------------------------------------- #
# Landry System — fundamental scoring engine (read-mostly; approvals mutate)
# --------------------------------------------------------------------------- #

def _landry_workbook() -> Optional[str]:
    """Highest-numbered LANDRY_SYSTEM_WORKBOOK_*.xlsx, or None."""
    hits = sorted(glob.glob(LANDRY_WORKBOOK_GLOB))
    return hits[-1] if hits else None


def _landry_store() -> ScoreStore:
    return ScoreStore(LANDRY_SCORES_FILE)


def _landry_snapshot() -> Optional[dict]:
    if not os.path.exists(LANDRY_SNAPSHOT_FILE):
        return None
    try:
        with open(LANDRY_SNAPSHOT_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def _snapshot_age_days(snap: Optional[dict]) -> Optional[int]:
    if not snap or not snap.get("asof"):
        return None
    try:
        asof = dt.datetime.fromisoformat(str(snap["asof"]))
        if asof.tzinfo is None:
            asof = asof.replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc) - asof).days
    except ValueError:
        return None


def _landry_actions() -> tuple:
    """(actions, source, note): live via run_daily when the workbook is
    there, else the last saved landry_actions.json, else empty."""
    note = None
    wb = _landry_workbook()
    if wb is not None:
        try:
            acts = run_daily(wb, os.path.dirname(LANDRY_SNAPSHOT_FILE),
                             store=_landry_store())
            return [asdict(a) for a in acts], "live", None
        except Exception as e:
            note = f"live computation failed: {e}"
    else:
        note = "workbook missing — no LANDRY_SYSTEM_WORKBOOK_*.xlsx found"
    if os.path.exists(LANDRY_ACTIONS_FILE):
        try:
            with open(LANDRY_ACTIONS_FILE) as f:
                saved = json.load(f)
            return (list(saved.get("actions", [])), "file",
                    note + f" — showing saved actions from {saved.get('date')}")
        except Exception as e:
            note += f"; saved actions unreadable: {e}"
    return [], "none", note


def _iso_date(d) -> Optional[str]:
    if d is None:
        return None
    if hasattr(d, "date"):
        d = d.date()
    return str(d)


def _landry_row(row) -> Dict:
    """One scoring-tab row + engine recompute + workbook cross-check."""
    out = {"ticker": row.ticker, "company": row.company,
           "date_scored": _iso_date(row.date_scored),
           "tier1_avg": row.tier1_weighted_average,
           "composite": row.composite, "decision": row.decision,
           "rule_flags": list(row.rule_flags),
           "recomputed": None, "mismatch": False}
    try:
        card = LS.score_stock(row.ticker, row.scores)
    except Exception as e:
        out["recomputed"] = {"error": str(e)}
        return out
    f = card.flags
    out["recomputed"] = {
        "tier1_avg": card.tier1_weighted_average,
        "composite": card.composite, "decision": card.decision,
        "rule_flags": [f.rule1, f.rule2, f.rule3, f.rule4],
        "notes": card.notes}
    mismatch = False
    if (row.tier1_weighted_average is not None
            and abs(row.tier1_weighted_average
                    - card.tier1_weighted_average) > 1e-6):
        mismatch = True
    if (row.composite is not None and card.composite is not None
            and abs(row.composite - card.composite) > 1e-6):
        mismatch = True
    if row.decision and card.decision and row.decision != card.decision:
        mismatch = True
    out["mismatch"] = mismatch
    return out


@app.get("/api/landry/dashboard")
def landry_dashboard():
    notes: list = []
    rows: list = []
    positions: list = []
    wb = _landry_workbook()
    if wb is None:
        notes.append("workbook missing — no LANDRY_SYSTEM_WORKBOOK_*.xlsx "
                     "found")
    else:
        try:
            rows = LX.read_scoring_tab(wb)
            positions = LX.read_positions(wb)
        except Exception as e:
            rows, positions = [], []
            notes.append(f"workbook unreadable: {e}")

    row_out = [_landry_row(r) for r in rows]

    # verdict strip: held names by decision band, equity vs cash, snapshot
    # age, open action items by priority
    decision_of = {r["ticker"]: (r["decision"]
                                 or (r["recomputed"] or {}).get("decision"))
                   for r in row_out}
    bands: Dict[str, int] = {}
    for p in positions:
        if p.asset_class != "Equity":
            continue
        band = decision_of.get(p.ticker) or "UNSCORED"
        bands[band] = bands.get(band, 0) + 1
    by_class: Dict[str, float] = {}
    for p in positions:
        by_class[p.asset_class] = (by_class.get(p.asset_class, 0.0)
                                   + p.pct_of_portfolio)

    snap = _landry_snapshot()
    age = _snapshot_age_days(snap)
    if snap is None:
        notes.append("snapshot missing — run `python -m landry refresh`")

    actions, source, note = _landry_actions()
    if note:
        notes.append(note)
    counts = {"act_now": sum(1 for a in actions if a.get("priority") == 1),
              "due_soon": sum(1 for a in actions if a.get("priority") == 2),
              "housekeeping": sum(1 for a in actions
                                  if a.get("priority") == 3)}

    return {"workbook": os.path.basename(wb) if wb else None,
            "rows": row_out,
            "verdict": {"bands": bands,
                        "equity_pct": by_class.get("Equity", 0.0),
                        "cash_pct": by_class.get("Cash", 0.0),
                        "by_class": by_class,
                        "snapshot_age_days": age,
                        "snapshot_stale": (age is None
                                           or age > LANDRY_SNAPSHOT_STALE_DAYS),
                        "actions": counts},
            "actions_source": source,
            "notes": notes}


@app.get("/api/landry/positions")
def landry_positions():
    wb = _landry_workbook()
    if wb is None:
        return {"workbook": None, "positions": [], "equity_weights": {},
                "note": "workbook missing — no LANDRY_SYSTEM_WORKBOOK_*.xlsx "
                        "found"}
    try:
        positions = LX.read_positions(wb)
    except Exception as e:
        return {"workbook": os.path.basename(wb), "positions": [],
                "equity_weights": {}, "note": f"workbook unreadable: {e}"}
    return {"workbook": os.path.basename(wb),
            "positions": [asdict(p) for p in positions],
            "equity_weights": LX.equity_weights(positions),
            "note": None}


@app.get("/api/landry/risk")
def landry_risk():
    snap = _landry_snapshot()
    if snap is None:
        return {"asof": None, "age_days": None, "correlation": {},
                "macro": {}, "tickers": {},
                "note": "snapshot missing — run `python -m landry refresh`"}
    tickers: Dict[str, Dict] = {}
    for t, e in (snap.get("tickers") or {}).items():
        tech = e.get("technicals") or {}
        beta = e.get("beta") or {}
        rs = e.get("relative_strength") or {}
        tickers[t] = {"beta": beta.get("beta"),
                      "beta_source": beta.get("source"),
                      "size_reduction_pct": beta.get("size_reduction_pct"),
                      "above_200w_ma": tech.get("above_200w_ma"),
                      "macd_positive_or_turning":
                          tech.get("macd_positive_or_turning"),
                      "supertrend_bullish": tech.get("supertrend_bullish"),
                      "staging_ok": tech.get("staging_ok"),
                      "rs_diff_blended": rs.get("diff_blended"),
                      "error": e.get("error")}
    return {"asof": snap.get("asof"),
            "age_days": _snapshot_age_days(snap),
            "correlation": snap.get("correlation") or {},
            "macro": snap.get("macro") or {},
            "tickers": tickers,
            "note": None}


@app.get("/api/landry/actions")
def landry_actions():
    actions, source, note = _landry_actions()
    return {"actions": actions, "source": source, "note": note,
            "counts": {"act_now": sum(1 for a in actions
                                      if a.get("priority") == 1),
                       "due_soon": sum(1 for a in actions
                                       if a.get("priority") == 2),
                       "housekeeping": sum(1 for a in actions
                                           if a.get("priority") == 3)}}


@app.get("/api/landry/pending")
def landry_pending():
    store = _landry_store()
    pending = store.pending()
    return {"pending": pending,
            "tickers": store.tickers(),
            "count": sum(len(d) for d in pending.values())}


class LandryApproveParams(BaseModel):
    ticker: str
    indicator: str
    by: str
    score: Optional[int] = None
    confidence: Optional[str] = None


class LandryRejectParams(BaseModel):
    ticker: str
    indicator: str
    by: str
    reason: str


def _landry_gate_args(ticker: str, indicator: str, by: str) -> tuple:
    ticker, indicator, by = ticker.strip().upper(), indicator.strip(), by.strip()
    if not ticker or not indicator or not by:
        raise HTTPException(400, "ticker, indicator and by are all required.")
    return ticker, indicator, by


@app.post("/api/landry/approve")
def landry_approve(p: LandryApproveParams):
    ticker, indicator, by = _landry_gate_args(p.ticker, p.indicator, p.by)
    if p.score is not None and p.score not in (1, 2, 3, 4, 5):
        raise HTTPException(400, "score must be an integer 1-5.")
    confidence = p.confidence.strip().upper() if p.confidence else None
    if confidence is not None and confidence not in CONFIDENCE_LEVELS:
        raise HTTPException(
            400, f"confidence must be one of {'/'.join(CONFIDENCE_LEVELS)}.")
    store = _landry_store()
    try:
        approved = store.approve(ticker, indicator, by,
                                 score=p.score, confidence=confidence)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "approved": asdict(approved),
            "pending": store.pending()}


@app.post("/api/landry/reject")
def landry_reject(p: LandryRejectParams):
    ticker, indicator, by = _landry_gate_args(p.ticker, p.indicator, p.by)
    reason = p.reason.strip()
    if not reason:
        raise HTTPException(400, "a reject needs a documented reason.")
    store = _landry_store()
    try:
        store.reject(ticker, indicator, by, reason)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "pending": store.pending()}


@app.get("/api/landry/audit")
def landry_audit():
    audit = _landry_store().audit
    return {"total": len(audit), "audit": list(reversed(audit[-200:]))}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:               # user management: adduser/passwd/...
        raise SystemExit(auth.cli(sys.argv[1:]))
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8713)
