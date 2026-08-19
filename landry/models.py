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

import os
import sqlite3

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

CREATE TABLE IF NOT EXISTS drawdown_log (
    id                 INTEGER PRIMARY KEY,
    date               TEXT NOT NULL UNIQUE,
    portfolio_value    REAL,
    running_peak       REAL,
    drawdown_pct       REAL,
    status             TEXT,
    cash_floor         TEXT,
    new_position_rule  TEXT,
    notes              TEXT
);

CREATE TABLE IF NOT EXISTS journal (
    id      INTEGER PRIMARY KEY,
    date    TEXT NOT NULL,
    ticker  TEXT REFERENCES tickers(ticker),
    notes   TEXT
);
"""


def connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Create the database (idempotent) and return an open connection."""
    conn = connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
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
