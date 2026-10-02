"""SQLite schema for the Landry System (see ../LANDRY_DATABASE_DESIGN.md).

Plain stdlib ``sqlite3``, no ORM -- single local writer, no need for
migration/relationship machinery. Mirrors the pattern in ``xlsx_io.py``:
thin functions over dataclasses, not a framework.

``landry.db`` is gitignored (like ``data_cache/``); it is a derived store,
rebuildable from the workbook via ``landry.migrate_to_db``. The Part 12
approval audit trail additionally keeps exporting to the git-tracked
``landry_scores.json`` as a diffable backup -- see design decision 2 in
LANDRY_DATABASE_DESIGN.md. Losing that lesson once already cost a real
approval history; this schema does not get to relearn it.
"""

from __future__ import annotations

import datetime
import os
import sqlite3
from typing import List

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(os.path.dirname(_HERE), "landry.db")

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tickers (
    ticker          TEXT PRIMARY KEY,
    company         TEXT,
    first_seen_date TEXT
);

-- Scoring history (append-only -- one row per indicator per scoring pass,
-- not overwritten in place the way the Scoring tab is today).
CREATE TABLE IF NOT EXISTS scores (
    id            INTEGER PRIMARY KEY,
    ticker        TEXT NOT NULL REFERENCES tickers(ticker),
    indicator     TEXT NOT NULL,
    tier          INTEGER NOT NULL,
    score         REAL,
    confidence    TEXT,
    evidence      TEXT,
    scored_date   TEXT NOT NULL,
    approved_by   TEXT,
    approved_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_scores_ticker_date ON scores(ticker, scored_date);

CREATE TABLE IF NOT EXISTS composite_history (
    id               INTEGER PRIMARY KEY,
    ticker           TEXT NOT NULL REFERENCES tickers(ticker),
    scored_date      TEXT NOT NULL,
    tier1_wtd_avg    REAL,
    tier1_contrib    REAL,
    tier2_contrib    REAL,
    tier3_contrib    REAL,
    composite        REAL,
    decision         TEXT,
    rule1_flag       TEXT,
    rule2_flag       TEXT,
    rule3_flag       TEXT,
    rule4_flag       TEXT
);
CREATE INDEX IF NOT EXISTS idx_composite_ticker_date
    ON composite_history(ticker, scored_date);

CREATE TABLE IF NOT EXISTS classification (
    ticker    TEXT PRIMARY KEY REFERENCES tickers(ticker),
    sector    TEXT,
    industry  TEXT,
    as_of_date TEXT
);

CREATE TABLE IF NOT EXISTS market_data (
    id             INTEGER PRIMARY KEY,
    ticker         TEXT NOT NULL REFERENCES tickers(ticker),
    price          REAL,
    volume         REAL,
    market_cap_m   REAL,
    pe             REAL,
    wk52_low       REAL,
    wk52_high      REAL,
    dividend_yield REAL,
    as_of_date     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_data_ticker_date
    ON market_data(ticker, as_of_date);

CREATE TABLE IF NOT EXISTS price_history (
    ticker       TEXT NOT NULL REFERENCES tickers(ticker),
    week_ending  TEXT NOT NULL,
    close        REAL,
    PRIMARY KEY (ticker, week_ending)
);

CREATE TABLE IF NOT EXISTS positions (
    id                 INTEGER PRIMARY KEY,
    account            TEXT NOT NULL,
    ticker             TEXT NOT NULL REFERENCES tickers(ticker),
    description        TEXT,
    asset_class        TEXT,
    quantity           REAL,
    price              REAL,
    market_value       REAL,
    cost_basis         REAL,
    unrealized_gl      REAL,
    unrealized_gl_pct  REAL,
    pct_of_account     REAL,
    pct_of_combined    REAL,
    classification     TEXT,
    as_of_date         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_positions_asof ON positions(as_of_date);

CREATE TABLE IF NOT EXISTS tax_loss_carryforward (
    id         INTEGER PRIMARY KEY,
    term       TEXT NOT NULL,     -- 'short' or 'long'
    amount     REAL,
    as_of_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS performance_cohort (
    id                INTEGER PRIMARY KEY,
    ticker            TEXT NOT NULL REFERENCES tickers(ticker),
    entry_date        TEXT,
    entry_price       REAL,
    entry_score       REAL,
    entry_confidence  TEXT,
    entry_band        TEXT,
    spy_at_entry      REAL,
    status            TEXT,
    exit_date         TEXT,
    exit_price        REAL,
    exit_reason       TEXT
);

CREATE TABLE IF NOT EXISTS monitor_notes (
    ticker             TEXT PRIMARY KEY REFERENCES tickers(ticker),
    category           TEXT,
    insider_flag       TEXT,
    insider_note       TEXT,
    analyst_shift_flag TEXT,
    recheck_status     TEXT,
    notes              TEXT,
    updated_at         TEXT
);

CREATE TABLE IF NOT EXISTS watchlist (
    id                   INTEGER PRIMARY KEY,
    ticker               TEXT NOT NULL REFERENCES tickers(ticker),
    status               TEXT,
    entry_date           TEXT,
    entry_score          REAL,
    remediation_plan_yn  TEXT,
    deadline_90day       TEXT,
    action_status        TEXT,
    notes                TEXT
);

CREATE TABLE IF NOT EXISTS holding_monitor (
    id                INTEGER PRIMARY KEY,
    ticker            TEXT NOT NULL REFERENCES tickers(ticker),
    as_of_date        TEXT NOT NULL,
    position_pct      REAL,
    max_full_pct      REAL,
    debt_fcf          REAL,
    p_fcf             REAL,
    fcf_growth        REAL,
    implied_return    REAL,
    current_tier      TEXT,
    prior_tier        TEXT,
    valuation_flags   TEXT,
    hold_through_yn   TEXT,
    action_status     TEXT
);

CREATE TABLE IF NOT EXISTS holding_monitor_indicator (
    id                  INTEGER PRIMARY KEY,
    holding_monitor_id  INTEGER NOT NULL REFERENCES holding_monitor(id),
    indicator_name      TEXT NOT NULL,
    prior_value         TEXT,
    current_value       TEXT,
    flag                TEXT
);

CREATE TABLE IF NOT EXISTS implied_return_scenario (
    id              INTEGER PRIMARY KEY,
    ticker          TEXT NOT NULL REFERENCES tickers(ticker),
    scenario        TEXT NOT NULL,   -- base / bear / bull
    fcf_yr5         REAL,
    terminal_mult   REAL,
    distributions   REAL,
    implied_return  REAL,
    tag             TEXT,            -- L/P/U
    computed_date   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entry_checklist (
    id                       INTEGER PRIMARY KEY,
    ticker                   TEXT NOT NULL REFERENCES tickers(ticker),
    computed_date            TEXT NOT NULL,
    rule5_composite          TEXT,
    rule6_tier1              TEXT,
    rule7_no_tier1_eq1       TEXT,
    rule8_200wk              TEXT,
    rule9_macd               TEXT,
    staging_result           TEXT,
    rule10_binary_risk       TEXT,
    risk_in_thesis_yn        TEXT,
    rule10_result            TEXT,
    rule11_bear_case         TEXT,
    rule12_scenario_tags     TEXT,
    p_fcf_current            REAL,
    consensus_fcf_growth_2yr REAL,
    rule13_valuation_ceiling TEXT,
    entry_authorized         TEXT,
    recommended_action       TEXT
);

-- The Portfolio Drawdown Log tab's INPUTS only: date, value, notes. Running
-- peak, drawdown %, status, cash floor and new-position rule are derived (the
-- tab's formulas) and deliberately not stored -- a stored copy can only drift
-- from them. Generated in DATE order: the running peak is a chronological chain.
CREATE TABLE IF NOT EXISTS drawdown_log (
    id               INTEGER PRIMARY KEY,
    date             TEXT NOT NULL UNIQUE,
    portfolio_value  REAL NOT NULL,
    notes            TEXT
);

-- Decision log (the Journal tab). Append-only and kept in WRITE order (id),
-- never date-sorted: future-dated recurring-event placeholders would bury the
-- real recent entries. `label` is the tab's "Ticker" column and is free text --
-- a ticker, a comma list of them, or an event label like DCA-CATCHUP-1 -- so it
-- is deliberately NOT a foreign key to tickers (it used to be, which made the
-- migration invent a "ticker" for every event label).
CREATE TABLE IF NOT EXISTS journal (
    id     INTEGER PRIMARY KEY,
    date   TEXT NOT NULL,    -- ISO 8601: YYYY-MM-DD (full datetime only if a time is ever present)
    label  TEXT,
    notes  TEXT
);
"""


def connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA_VERSION = 2   # 1 = Phase A (journal.ticker FK, drawdown_log with derived columns)


class SchemaMismatch(RuntimeError):
    """The database file was built by an older schema than this code expects."""


def check_schema(conn: sqlite3.Connection) -> None:
    """Refuse a database built by an older schema with a clear message instead
    of letting a later query die on a missing column. ``landry.db`` is derived
    (rebuildable from the workbook), so the fix is always delete and rebuild."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise SchemaMismatch(
            f"this database is schema v{version} but the code needs "
            f"v{SCHEMA_VERSION}; it is a derived file -- delete it and rebuild "
            f"(`python -m landry db pull`, or migrate_to_db --overwrite)")


def init_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Create the database (idempotent) and return an open connection. A new
    file is stamped with SCHEMA_VERSION; an existing one is checked against it."""
    conn = connect(db_path)
    fresh = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0
    conn.executescript(SCHEMA_SQL)
    if fresh:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    try:
        check_schema(conn)
    except SchemaMismatch:
        conn.close()
        raise
    return conn


def ensure_ticker(conn: sqlite3.Connection, ticker: str,
                  company: str = "", as_of: str = "") -> None:
    """Insert into ``tickers`` if not already present (FK target for every
    other table -- every reader calls this before inserting a child row)."""
    conn.execute(
        "INSERT INTO tickers (ticker, company, first_seen_date) VALUES (?, ?, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET "
        "company=COALESCE(NULLIF(excluded.company, ''), tickers.company)",
        (ticker, company, as_of))


def iso_date(value) -> str:
    """Normalize a date, datetime, or ISO string to what TEXT date columns
    store: ``YYYY-MM-DD``, or a full ISO datetime only if a time is present.
    Raises on anything else rather than storing a string nothing can parse
    back (the generated report round-trips these)."""
    if isinstance(value, str):
        value = datetime.datetime.fromisoformat(value)
    if isinstance(value, datetime.datetime):
        return (value.date().isoformat() if value.time() == datetime.time(0)
                else value.isoformat())
    if isinstance(value, datetime.date):
        return value.isoformat()
    raise TypeError(f"not a date: {value!r}")


def journal_add(conn: sqlite3.Connection, date, label, notes) -> int:
    """Append one entry; returns its id. Does not commit (the caller does,
    like every other writer here). Order is id order, i.e. write order."""
    cur = conn.execute(
        "INSERT INTO journal (date, label, notes) VALUES (?,?,?)",
        (iso_date(date), label or None, notes))
    return cur.lastrowid


def journal_rows(conn: sqlite3.Connection) -> List[dict]:
    """Every entry in write order, as plain dicts (independent of the
    connection's row_factory)."""
    return [dict(id=i, date=d, label=l, notes=n) for i, d, l, n in conn.execute(
        "SELECT id, date, label, notes FROM journal ORDER BY id")]


def journal_update(conn: sqlite3.Connection, entry_id: int, fields: dict) -> None:
    """Change an entry in place (the Journal is append-only in spirit, but
    CLAUDE.md has superseded entries marked in place). ``fields`` may hold
    ``date``, ``label`` and ``notes``; raises KeyError if the id doesn't exist.
    Does not commit."""
    allowed = {"date", "label", "notes"}
    if not fields or set(fields) - allowed:
        raise ValueError(f"fields must be a non-empty subset of {sorted(allowed)}")
    values = dict(fields)
    if "date" in values:
        values["date"] = iso_date(values["date"])
    if "label" in values:
        values["label"] = values["label"] or None
    cur = conn.execute(
        f"UPDATE journal SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?",
        (*values.values(), entry_id))
    if cur.rowcount != 1:
        raise KeyError(entry_id)


def drawdown_update(conn: sqlite3.Connection, date, fields: dict) -> None:
    """Change the entry for ``date``; ``fields`` may hold ``portfolio_value``
    and ``notes``. Raises KeyError if that date has no entry. Does not commit."""
    allowed = {"portfolio_value", "notes"}
    if not fields or set(fields) - allowed:
        raise ValueError(f"fields must be a non-empty subset of {sorted(allowed)}")
    values = dict(fields)
    if "portfolio_value" in values:
        values["portfolio_value"] = float(values["portfolio_value"])
    if "notes" in values:
        values["notes"] = values["notes"] or None
    cur = conn.execute(
        f"UPDATE drawdown_log SET {', '.join(f'{k} = ?' for k in values)} WHERE date = ?",
        (*values.values(), iso_date(date)))
    if cur.rowcount != 1:
        raise KeyError(iso_date(date))


def drawdown_add(conn: sqlite3.Connection, date, portfolio_value: float,
                 notes=None) -> int:
    """Log one portfolio value; returns its id. One entry per date (a second
    raises sqlite3.IntegrityError). Does not commit."""
    cur = conn.execute(
        "INSERT INTO drawdown_log (date, portfolio_value, notes) VALUES (?,?,?)",
        (iso_date(date), float(portfolio_value), notes or None))
    return cur.lastrowid


def drawdown_rows(conn: sqlite3.Connection) -> List[dict]:
    """Every entry in DATE order (not write order: a backfilled date must
    land before later ones, since the running peak chains chronologically)."""
    return [dict(id=i, date=d, portfolio_value=v, notes=n) for i, d, v, n in conn.execute(
        "SELECT id, date, portfolio_value, notes FROM drawdown_log ORDER BY date")]
