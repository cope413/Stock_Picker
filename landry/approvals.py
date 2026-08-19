"""Landry System v1.0 — Phase 3 human approval gate (Part 12).

Drafts — AI-generated or quantitative — are *pending* until a named
human approves them. :meth:`ScoreStore.approved_scores` is built
exclusively from the approved section of the store, so an unapproved
draft is structurally incapable of reaching
``landry.scoring.score_stock``; there is no code path from pending to
the scorer that does not pass through :meth:`ScoreStore.approve`.

Every propose / approve / reject appends to an audit trail with UTC
timestamps, and an approval with an analyst override records both the
original draft values and the final ones.

Backed by landry.db's ``score_events`` table (see landry/schema.sql):
an append-only event log, never an in-place dict, so every prior score
and every audit entry survives a later re-score. ``v_score_current_*``
(the SQL views built on ``MAX(id)`` per ticker/indicator) reproduce the
exact overwrite semantics the old JSON store had for "current" state,
while keeping full history queryable underneath.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from landry import db as _db
from landry.fundamentals import Draft
from landry.scoring import CONFIDENCE_LEVELS, IndicatorScore

#: where an approved score came from
SOURCES = ("ai_draft", "manual", "quant_draft")

_VALID_SCORES = (1, 2, 3, 4, 5)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ApprovedScore:
    """One human-approved indicator score, with full provenance."""
    indicator: str
    score: int
    confidence: str                          # "H" | "M" | "L"
    approved_by: str
    approved_at: str                         # UTC ISO-8601
    source: str                              # "ai_draft"|"manual"|"quant_draft"
    rationale: str = ""
    adaptation: Optional[str] = None         # Part 10 substitution note
    original_score: Optional[int] = None     # draft values before any override
    original_confidence: Optional[str] = None

    def __post_init__(self):
        if self.score not in _VALID_SCORES:
            raise ValueError(f"score must be an integer 1-5, got {self.score!r}")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}, "
                             f"got {self.confidence!r}")
        if self.source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}, "
                             f"got {self.source!r}")

    def as_indicator_score(self) -> IndicatorScore:
        return IndicatorScore(score=self.score, confidence=self.confidence,
                              adaptation=self.adaptation,
                              evidence=self.rationale or None)


class ScoreStore:
    """SQLite-backed store implementing the Part 12 boundary.

    ``path`` may be a filesystem path (opened, and initialized with the
    schema if new, via :func:`landry.db.connect`) or an existing
    ``sqlite3.Connection`` to share with another caller. Every mutation
    commits immediately, so a fresh ``ScoreStore`` on the same path sees
    the same state -- the same guarantee the old JSON store made."""

    def __init__(self, path: Union[str, "os.PathLike[str]", sqlite3.Connection]):
        if isinstance(path, sqlite3.Connection):
            self._conn = path
            self.path: Optional[str] = None
        else:
            self.path = str(path)
            self._conn = _db.connect(self.path)

    # -- internals ---------------------------------------------------------- #

    def _ensure_ticker(self, ticker: str) -> str:
        t = ticker.upper()
        self._conn.execute("INSERT OR IGNORE INTO tickers (ticker) VALUES (?)", (t,))
        return t

    def _ensure_user(self, username: str) -> None:
        # approved_by/rejected_by is free text at this API boundary (matching
        # the pre-DB contract); score_events.actor is a real FK to users(username)
        # for audit integrity, so the first time a name approves/rejects anything
        # it's registered here rather than requiring pre-provisioning.
        self._conn.execute(
            "INSERT OR IGNORE INTO users (username) VALUES (?)", (username,))

    def _current_pending(self, ticker: str, indicator: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM v_score_current_pending WHERE ticker=? AND indicator=?",
            (ticker, indicator)).fetchone()

    @staticmethod
    def _draft_dict(r: sqlite3.Row) -> Dict[str, Any]:
        return {
            "indicator": r["indicator"], "score": r["score"],
            "confidence": r["confidence"], "rationale": r["rationale"],
            "source": r["source"],
            "citations": json.loads(r["citations"]) if r["citations"] else [],
            "model": r["model"], "prompt_hash": r["prompt_hash"],
            "proposed_at": r["occurred_at"],
        }

    # -- the gate ----------------------------------------------------------- #

    def propose(self, ticker: str, draft, source: str = "ai_draft") -> None:
        """Record a pending draft. ``draft`` is a landry.fundamentals.Draft
        or a landry.ai_analyst.AIDraft (its citations/model/prompt hash
        are kept for the audit trail). Pending drafts confer nothing."""
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
        citations, model, prompt_hash = [], None, None
        inner = getattr(draft, "draft", None)        # AIDraft (duck-typed)
        if isinstance(inner, Draft):
            citations = list(getattr(draft, "citations", []) or [])
            model = getattr(draft, "model", None)
            prompt_hash = getattr(draft, "prompt_hash", None)
            draft = inner
        t = self._ensure_ticker(ticker)
        occurred_at = _now()
        self._conn.execute(
            """INSERT INTO score_events
               (ticker, indicator, action, score, confidence, source,
                rationale, citations, model, prompt_hash, occurred_at)
               VALUES (?, ?, 'propose', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (t, draft.indicator, int(draft.score), draft.confidence, source,
             draft.rationale, json.dumps(citations), model, prompt_hash,
             occurred_at))
        self._conn.commit()

    def approve(self, ticker: str, indicator: str, approved_by: str,
                score: Optional[int] = None, confidence: Optional[str] = None,
                adaptation: Optional[str] = None) -> ApprovedScore:
        """Convert a pending draft into an ApprovedScore. ``score`` /
        ``confidence`` optionally override the draft; the original draft
        values are recorded either way."""
        t = ticker.upper()
        row = self._current_pending(t, indicator)
        if row is None:
            raise ValueError(f"no pending draft for {t}:"
                             f"{indicator} — nothing to approve")
        final_score = int(score) if score is not None else row["score"]
        final_confidence = confidence if confidence is not None else row["confidence"]
        self._ensure_user(approved_by)
        occurred_at = _now()
        self._conn.execute(
            """INSERT INTO score_events
               (ticker, indicator, action, score, confidence, source,
                rationale, adaptation, original_score, original_confidence,
                actor, occurred_at)
               VALUES (?, ?, 'approve', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (t, indicator, final_score, final_confidence, row["source"],
             row["rationale"], adaptation, row["score"], row["confidence"],
             approved_by, occurred_at))
        self._conn.commit()
        return ApprovedScore(
            indicator=indicator, score=final_score, confidence=final_confidence,
            approved_by=approved_by, approved_at=occurred_at, source=row["source"],
            rationale=row["rationale"], adaptation=adaptation,
            original_score=row["score"], original_confidence=row["confidence"])

    def reject(self, ticker: str, indicator: str, approved_by: str,
               reason: str) -> None:
        """Discard a pending draft with a documented reason."""
        t = ticker.upper()
        row = self._current_pending(t, indicator)
        if row is None:
            raise ValueError(f"no pending draft for {t}:"
                             f"{indicator} — nothing to reject")
        self._ensure_user(approved_by)
        occurred_at = _now()
        self._conn.execute(
            """INSERT INTO score_events
               (ticker, indicator, action, score, confidence, source,
                rationale, actor, reason, occurred_at)
               VALUES (?, ?, 'reject', ?, ?, ?, ?, ?, ?, ?)""",
            (t, indicator, row["score"], row["confidence"], row["source"],
             row["rationale"], approved_by, reason, occurred_at))
        self._conn.commit()

    # -- views -------------------------------------------------------------- #

    def pending(self, ticker: Optional[str] = None) -> Dict[str, Dict]:
        """Read-only copy of the pending drafts (for review UIs).
        With no ticker: {ticker: {indicator: draft}} for every ticker
        that has pending drafts."""
        if ticker is not None:
            rows = self._conn.execute(
                "SELECT * FROM v_score_current_pending WHERE ticker=?",
                (ticker.upper(),)).fetchall()
            return {r["indicator"]: self._draft_dict(r) for r in rows}
        rows = self._conn.execute("SELECT * FROM v_score_current_pending").fetchall()
        out: Dict[str, Dict] = {}
        for r in rows:
            out.setdefault(r["ticker"], {})[r["indicator"]] = self._draft_dict(r)
        return out

    def tickers(self) -> list:
        """All tickers with any record (pending, approved, or rejected)."""
        rows = self._conn.execute(
            "SELECT DISTINCT ticker FROM score_events ORDER BY ticker").fetchall()
        return [r["ticker"] for r in rows]

    def approved_scores(self, ticker: str) -> Dict[str, IndicatorScore]:
        """{indicator: IndicatorScore} ready for landry.scoring.score_stock.
        Built ONLY from the current-approved view -- a pending or rejected
        draft cannot appear here (Part 12 boundary)."""
        rows = self._conn.execute(
            "SELECT * FROM v_score_current_approved WHERE ticker=?",
            (ticker.upper(),)).fetchall()
        return {r["indicator"]: IndicatorScore(
                    score=r["score"], confidence=r["confidence"],
                    adaptation=r["adaptation"], evidence=r["rationale"] or None)
                for r in rows}

    @property
    def audit(self) -> List[Dict[str, Any]]:
        """Full audit trail (append-only), oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM score_events ORDER BY id").fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            if r["action"] == "propose":
                out.append({"at": r["occurred_at"], "action": "propose",
                            "ticker": r["ticker"], "indicator": r["indicator"],
                            "score": r["score"], "confidence": r["confidence"],
                            "source": r["source"]})
            elif r["action"] == "approve":
                out.append({"at": r["occurred_at"], "action": "approve",
                            "ticker": r["ticker"], "indicator": r["indicator"],
                            "approved_by": r["actor"],
                            "original_score": r["original_score"],
                            "original_confidence": r["original_confidence"],
                            "final_score": r["score"],
                            "final_confidence": r["confidence"],
                            "overridden": (r["score"] != r["original_score"]
                                          or r["confidence"] != r["original_confidence"])})
            else:  # reject
                out.append({"at": r["occurred_at"], "action": "reject",
                            "ticker": r["ticker"], "indicator": r["indicator"],
                            "approved_by": r["actor"], "reason": r["reason"]})
        return out
