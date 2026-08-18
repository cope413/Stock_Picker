"""Landry System v1.0 — SQLite connection helper (landry.db).

The schema (landry/schema.sql) replaces the flat-JSON persistence
(landry_scores.json, landry_snapshot.json, landry_actions.json) and the
hand-edited workbook as the source of truth for Landry operational data.
This module owns exactly one thing: handing out a connection with the
right pragmas set and the schema applied on a fresh database. It does not
know about ScoreCard, ApprovedScore, or any other domain type -- those
stay in their existing modules.

SQLite defaults foreign-key enforcement OFF per connection, which would
silently defeat every REFERENCES/ON DELETE CASCADE in the schema -- so
PRAGMA foreign_keys = ON is set here, once, rather than left for callers
to remember.
"""

from __future__ import annotations

import os
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(_HERE, "schema.sql")
DEFAULT_DB_PATH = os.path.join(os.path.dirname(_HERE), "landry.db")


def connect(path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) a landry.db connection with pragmas set.

    A fresh/empty database gets the schema applied automatically. An
    existing database is left as-is -- this is not a migration runner.
    ``path=":memory:"`` is supported for tests.
    """
    is_new = path == ":memory:" or not os.path.exists(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    if is_new:
        apply_schema(conn)
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    """Execute schema.sql against ``conn``. Safe only on an empty database --
    CREATE TABLE/TRIGGER/VIEW have no IF NOT EXISTS guard here, matching the
    rest of the schema's fail-loud style (see landry/scoring.py's docstring:
    Hard Rules have no override path -- the schema shouldn't have a silent
    partial-apply path either)."""
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()


def is_initialized(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='score_events'"
    ).fetchone()
    return row is not None
