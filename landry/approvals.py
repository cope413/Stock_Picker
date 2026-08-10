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
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

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
    """JSON-file store implementing the Part 12 boundary.

    Layout: ``{"tickers": {TICKER: {"pending": {...}, "approved": {...},
    "rejected": {...}}}, "audit": [...]}``. Every mutation persists
    immediately, so a fresh ScoreStore on the same path sees the same
    state."""

    def __init__(self, path):
        self.path = str(path)
        self._data: Dict[str, Any] = {"tickers": {}, "audit": []}
        if os.path.exists(self.path):
            with open(self.path) as f:
                self._data = json.load(f)
            self._data.setdefault("tickers", {})
            self._data.setdefault("audit", [])

    # -- internals ---------------------------------------------------------- #

    def _save(self) -> None:
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=1)

    def _ticker(self, ticker: str) -> Dict[str, Dict]:
        return self._data["tickers"].setdefault(
            ticker.upper(), {"pending": {}, "approved": {}, "rejected": {}})

    def _log(self, action: str, ticker: str, indicator: str,
             **detail) -> None:
        self._data["audit"].append({"at": _now(), "action": action,
                                    "ticker": ticker.upper(),
                                    "indicator": indicator, **detail})

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
        rec = {"indicator": draft.indicator, "score": int(draft.score),
               "confidence": draft.confidence, "rationale": draft.rationale,
               "source": source, "citations": citations, "model": model,
               "prompt_hash": prompt_hash, "proposed_at": _now()}
        self._ticker(ticker)["pending"][draft.indicator] = rec
        self._log("propose", ticker, draft.indicator, score=rec["score"],
                  confidence=rec["confidence"], source=source)
        self._save()

    def approve(self, ticker: str, indicator: str, approved_by: str,
                score: Optional[int] = None, confidence: Optional[str] = None,
                adaptation: Optional[str] = None) -> ApprovedScore:
        """Convert a pending draft into an ApprovedScore. ``score`` /
        ``confidence`` optionally override the draft; the original draft
        values are recorded either way."""
        t = self._ticker(ticker)
        if indicator not in t["pending"]:
            raise ValueError(f"no pending draft for {ticker.upper()}:"
                             f"{indicator} — nothing to approve")
        rec = t["pending"].pop(indicator)
        approved = ApprovedScore(
            indicator=indicator,
            score=int(score) if score is not None else rec["score"],
            confidence=confidence if confidence is not None
            else rec["confidence"],
            approved_by=approved_by, approved_at=_now(),
            source=rec["source"], rationale=rec["rationale"],
            adaptation=adaptation,
            original_score=rec["score"],
            original_confidence=rec["confidence"])
        t["approved"][indicator] = asdict(approved)
        self._log("approve", ticker, indicator, approved_by=approved_by,
                  original_score=rec["score"],
                  original_confidence=rec["confidence"],
                  final_score=approved.score,
                  final_confidence=approved.confidence,
                  overridden=(approved.score != rec["score"]
                              or approved.confidence != rec["confidence"]))
        self._save()
        return approved

    def reject(self, ticker: str, indicator: str, approved_by: str,
               reason: str) -> None:
        """Discard a pending draft with a documented reason."""
        t = self._ticker(ticker)
        if indicator not in t["pending"]:
            raise ValueError(f"no pending draft for {ticker.upper()}:"
                             f"{indicator} — nothing to reject")
        rec = t["pending"].pop(indicator)
        t["rejected"][indicator] = {**rec, "rejected_by": approved_by,
                                    "rejected_at": _now(), "reason": reason}
        self._log("reject", ticker, indicator, approved_by=approved_by,
                  reason=reason)
        self._save()

    # -- views -------------------------------------------------------------- #

    def pending(self, ticker: Optional[str] = None) -> Dict[str, Dict]:
        """Read-only copy of the pending drafts (for review UIs).
        With no ticker: {ticker: {indicator: draft}} for every ticker
        that has pending drafts."""
        if ticker is not None:
            return dict(self._data["tickers"].get(ticker.upper(), {})
                        .get("pending", {}))
        return {t: dict(rec.get("pending", {}))
                for t, rec in self._data["tickers"].items()
                if rec.get("pending")}

    def tickers(self) -> list:
        """All tickers with any record (pending, approved, or rejected)."""
        return sorted(self._data["tickers"])

    def approved_scores(self, ticker: str) -> Dict[str, IndicatorScore]:
        """{indicator: IndicatorScore} ready for landry.scoring.score_stock.
        Built ONLY from the approved section — a pending or rejected
        draft cannot appear here (Part 12 boundary)."""
        out: Dict[str, IndicatorScore] = {}
        for ind, rec in (self._data["tickers"].get(ticker.upper(), {})
                         .get("approved", {})).items():
            out[ind] = IndicatorScore(score=rec["score"],
                                      confidence=rec["confidence"],
                                      adaptation=rec.get("adaptation"),
                                      evidence=rec.get("rationale") or None)
        return out

    @property
    def audit(self):
        """Copy of the full audit trail (append-only in the store)."""
        return list(self._data["audit"])
