"""Landry System v1.0 — Phase 3: AI-drafted judgment indicators.

Part 12 boundary: "the analyst-judgment fields — Moat, Management
Quality, Revenue Visibility, and the qualitative components of FCF
Yield & Trend — are never automated." Everything this module produces
is therefore a DRAFT: each one cites the evidence entries it used and
carries the model + prompt hash that produced it, and nothing enters
landry.scoring until a human approves it through
``landry.approvals.ScoreStore``.

The Part 10 data hierarchy is enforced *in code*, not just in the
prompt: the best (lowest) source tier cited for an indicator caps its
confidence — tier 3 caps at M, tier 4-5 (or no citations) caps at L —
regardless of what the model claimed.

The Claude client is injectable so every test runs offline.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from landry.fundamentals import Draft, FundamentalMetrics

# --------------------------------------------------------------------------- #
# Part 10 — data & source hierarchy (tier 1 = best)
# --------------------------------------------------------------------------- #

SOURCE_TIERS: Dict[int, str] = {
    1: "audited filings / regulatory reports",
    2: "company IR materials / earnings transcripts",
    3: "recognized market-data / consensus providers",
    4: "independent research",
    5: "secondary commentary",
}

_CONF_RANK = {"H": 0, "M": 1, "L": 2}       # higher rank = weaker


def confidence_ceiling(best_tier: Optional[int]) -> str:
    """Highest confidence the Part 10 hierarchy permits for evidence whose
    best (lowest) source tier is ``best_tier``. Tier > 2 caps at M,
    tier > 3 — or no cited evidence at all — caps at L."""
    if best_tier is None or best_tier > 3:
        return "L"
    if best_tier > 2:
        return "M"
    return "H"


def cap_confidence(confidence: str, best_tier: Optional[int]) -> str:
    """The weaker of the model's confidence and the Part 10 ceiling."""
    ceiling = confidence_ceiling(best_tier)
    return max(confidence, ceiling, key=lambda c: _CONF_RANK[c])


# --------------------------------------------------------------------------- #
# evidence pack — everything the model is allowed to see
# --------------------------------------------------------------------------- #

@dataclass
class Evidence:
    """One free-text evidence entry with its Part 10 provenance."""
    text: str
    source_tier: int        # 1-5, see SOURCE_TIERS
    source: str             # e.g. "FY2025 10-K", "Q2 earnings call"
    date: str = ""          # valuation date (Part 10: record for every input)


@dataclass
class EvidencePack:
    """Per-ticker bundle rendered into the drafting prompt.

    ``relative_strength`` / ``beta`` / ``technicals`` are the plain dicts
    landry.refresh writes into the snapshot; ``fundamentals`` is the
    Phase 2 metric bundle. Free-text evidence is added via
    :meth:`add_evidence` and rendered best sources first."""
    ticker: str
    company_name: str = ""
    sector: str = ""
    fundamentals: Optional[FundamentalMetrics] = None
    relative_strength: Dict[str, Any] = field(default_factory=dict)
    beta: Dict[str, Any] = field(default_factory=dict)
    technicals: Dict[str, Any] = field(default_factory=dict)
    evidence: List[Evidence] = field(default_factory=list)

    def add_evidence(self, text: str, source_tier: int, source: str,
                     date: str = "") -> Evidence:
        if source_tier not in SOURCE_TIERS:
            raise ValueError(f"source_tier must be 1-5 (Part 10 hierarchy), "
                             f"got {source_tier!r}")
        ev = Evidence(text=text, source_tier=int(source_tier),
                      source=source, date=date)
        self.evidence.append(ev)
        return ev

    def labeled_evidence(self) -> List[Tuple[str, Evidence]]:
        """Entries sorted best tier first (stable within a tier), labeled
        E1..En — the labels the model must cite."""
        ordered = sorted(self.evidence, key=lambda e: e.source_tier)
        return [(f"E{i}", ev) for i, ev in enumerate(ordered, 1)]

    def label_tiers(self) -> Dict[str, int]:
        """Citation label -> source tier, for ceiling enforcement."""
        return {label: ev.source_tier for label, ev in self.labeled_evidence()}

    def render(self) -> str:
        head = f"EVIDENCE PACK — {self.ticker}"
        if self.company_name:
            head += f" ({self.company_name})"
        if self.sector:
            head += f", sector: {self.sector}"
        lines = [head]
        if self.fundamentals is not None:
            lines.append("Phase 2 quantitative fundamentals (context only — "
                         "already drafted elsewhere):")
            lines.append(json.dumps(dataclasses.asdict(self.fundamentals),
                                    default=str))
        for name, d in (("relative_strength", self.relative_strength),
                        ("beta", self.beta), ("technicals", self.technicals)):
            if d:
                lines.append(f"{name}: {json.dumps(d, default=str)}")
        lines.append("Evidence entries, best sources first (Part 10 data "
                     "hierarchy — cite entries by label):")
        for label, ev in self.labeled_evidence():
            line = (f"[{label}] tier {ev.source_tier} "
                    f"({SOURCE_TIERS[ev.source_tier]}), source: {ev.source}")
            if ev.date:
                line += f", dated {ev.date}"
            lines.append(f"{line} — {ev.text}")
        if not self.evidence:
            lines.append("(no evidence entries)")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# rubrics (Part 2, judgment indicators only) — sent to the model verbatim
# --------------------------------------------------------------------------- #

JUDGMENT_INDICATORS = ("competitive_moat", "management_quality",
                       "revenue_visibility")

MOAT_RUBRIC = """\
competitive_moat — Competitive Moat Quality (Tier 1, 15%):
5 = wide moat, multiple reinforcing advantages; 4 = narrow but durable
single strong advantage; 3 = mild / contestable advantage; 2 = no clear
moat, competes on price; 1 = actively losing competitive position.
A score of 4-5 requires positive evidence on at least 2 of these four
cross-check tests:
  (a) pricing power above CPI without volume loss;
  (b) gross margin vs industry median (confidence support only);
  (c) customer retention / switching-cost evidence;
  (d) market-share trend over 3+ years.
The draft must state which cross-checks are evidenced."""

MANAGEMENT_RUBRIC = """\
management_quality — Management Quality & Capital Allocation (Tier 2, 8%):
5 = independently evidenced value-accretive capital allocation, sound
governance, aligned incentives, limited dilution, credible succession;
4 = strong with minor concerns; 3 = mixed record, limited tenure, or a
new CEO (new CEOs default to 3); 2 = value-destructive M&A, repeated
dilution, or governance concerns; 1 = serial misallocation.
Review at least 5 years of capital-allocation history."""

REVENUE_VISIBILITY_RUBRIC = """\
revenue_visibility — Revenue Visibility, ARR/Backlog (Tier 1, 10%):
5 = more than 80% of next-12-month revenue contracted or recurring;
4 = 60-80%; 3 = 40-60%; 2 = 20-40%; 1 = under 20%, fully transactional."""

STRUCTURAL_DETERIORATION_RUBRIC = """\
structural_deterioration — boolean flag (the qualitative component of
FCF Yield & Trend): true only when the evidence shows a structural break
in the business (secular demand loss, broken unit economics, obsolete
product) rather than a cyclical dip. When true, the FCF Yield & Trend
indicator is capped at 2 by draft_fcf_yield_trend."""

_INSTRUCTIONS = """\
Return ONLY a single JSON object — no prose, no markdown fences.
Schema:
{
  "competitive_moat": null | {"score": 1-5, "confidence": "H"|"M"|"L",
      "rationale": str, "citations": [evidence labels],
      "cross_checks": ["a".."d" that are positively evidenced]},
  "management_quality": null | {"score": 1-5, "confidence": "H"|"M"|"L",
      "rationale": str, "citations": [...]},
  "revenue_visibility": null | {"score": 1-5, "confidence": "H"|"M"|"L",
      "rationale": str, "citations": [...]},
  "structural_deterioration": null | {"value": true|false,
      "confidence": "H"|"M"|"L", "rationale": str, "citations": [...]}
}
Rules:
- For every score, cite the evidence entry labels (e.g. "E1") that
  support it in "citations".
- Confidence "H" only with multiple corroborating tier 1-2 sources AND a
  consistent multi-period trend.
- "M" for a single reliable source, or a mixed / short-lived trend.
- "L" when significant judgment or sector adaptation was required, or
  when sources materially disagree.
- NEVER score an indicator that has no relevant evidence — return null
  for that indicator instead. Do not guess.
- competitive_moat may only score 4-5 with at least 2 evidenced
  cross-checks; always list the evidenced ones in "cross_checks"."""


# --------------------------------------------------------------------------- #
# Claude integration
# --------------------------------------------------------------------------- #

DEFAULT_MODEL = "claude-sonnet-5"


@dataclass
class AIDraft:
    """A rubric Draft plus its provenance — still nothing until approved."""
    draft: Draft
    citations: List[str]
    model: str
    prompt_hash: str


@dataclass
class AIFlag:
    """A drafted boolean judgment flag (structural_deterioration)."""
    name: str
    value: bool
    confidence: str
    rationale: str
    citations: List[str]
    model: str
    prompt_hash: str


@dataclass
class AnalystResult:
    """Everything one drafting call produced. Indicators the model
    returned null for (no relevant evidence) are simply absent."""
    drafts: Dict[str, AIDraft]
    structural_deterioration: Optional[AIFlag]
    model: str
    prompt_hash: str


def _response_text(resp) -> str:
    """Text out of an anthropic Message (or anything shaped like one)."""
    content = getattr(resp, "content", resp)
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(text)
    return "".join(parts)


def _parse_json(text: str) -> Dict[str, Any]:
    s = text.strip()
    if s.startswith("```"):                     # tolerate a fenced block
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        s = s.rsplit("```", 1)[0]
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"model did not return valid JSON ({e}); "
                         f"got: {text[:200]!r}") from e
    if not isinstance(obj, dict):
        raise ValueError(f"model returned JSON but not an object: "
                         f"{text[:200]!r}")
    return obj


def _best_tier(citations: List[str], tiers: Dict[str, int]) -> Optional[int]:
    known = [tiers[c] for c in citations if c in tiers]
    return min(known) if known else None


class ClaudeAnalyst:
    """Drafts the judgment indicators from an EvidencePack via Claude.

    ``client`` is anything with ``.messages.create(...)`` (the anthropic
    SDK client, or a fake in tests). When omitted, an
    ``anthropic.Anthropic()`` is constructed lazily on first use."""

    def __init__(self, client=None, model: str = DEFAULT_MODEL,
                 max_tokens: int = 1500):
        self._client = client
        self.model = model
        self.max_tokens = max_tokens

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise RuntimeError(
                    "the 'anthropic' package is not installed — run "
                    "'pip install anthropic' to enable AI drafting") from e
            try:
                self._client = anthropic.Anthropic()
            except Exception as e:
                raise RuntimeError(
                    "could not construct the Anthropic client — set the "
                    f"ANTHROPIC_API_KEY environment variable ({e})") from e
        return self._client

    def build_prompt(self, pack: EvidencePack) -> str:
        return "\n\n".join([
            "You are drafting analyst-judgment indicator scores for the "
            "Landry Family Equity Investment Operating System v1.0. "
            "Scores are integers 1-5 with confidence tags H/M/L. Your "
            "output is a DRAFT for human review — never a final score "
            "(Part 12: analyst-judgment fields are never automated).",
            "== RUBRICS ==",
            MOAT_RUBRIC, MANAGEMENT_RUBRIC, REVENUE_VISIBILITY_RUBRIC,
            STRUCTURAL_DETERIORATION_RUBRIC,
            "== EVIDENCE ==",
            pack.render(),
            "== INSTRUCTIONS ==",
            _INSTRUCTIONS,
        ])

    def draft_judgment(self, pack: EvidencePack) -> AnalystResult:
        """One drafting call: prompt -> Claude -> parsed, ceiling-capped
        AIDrafts. Raises ValueError on malformed model output."""
        prompt = self.build_prompt(pack)
        phash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        resp = self.client.messages.create(
            model=self.model, max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}])
        payload = _parse_json(_response_text(resp))
        tiers = pack.label_tiers()

        drafts: Dict[str, AIDraft] = {}
        for ind in JUDGMENT_INDICATORS:
            entry = payload.get(ind)
            if entry is None:
                continue                        # no relevant evidence
            drafts[ind] = self._to_draft(ind, entry, tiers, phash)

        flag = None
        entry = payload.get("structural_deterioration")
        if entry is not None:
            flag = self._to_flag(entry, tiers, phash)
        return AnalystResult(drafts, flag, self.model, phash)

    # -- parsing helpers ---------------------------------------------------- #

    def _to_draft(self, indicator: str, entry: Any,
                  tiers: Dict[str, int], phash: str) -> AIDraft:
        if not isinstance(entry, dict):
            raise ValueError(f"malformed entry for {indicator}: {entry!r}")
        try:
            score = int(entry["score"])
            conf = str(entry["confidence"])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"malformed entry for {indicator}: "
                             f"{entry!r}") from e
        if score not in (1, 2, 3, 4, 5):
            raise ValueError(f"{indicator}: score must be 1-5, got {score!r}")
        if conf not in _CONF_RANK:
            raise ValueError(f"{indicator}: confidence must be H/M/L, "
                             f"got {conf!r}")
        rationale = str(entry.get("rationale", ""))
        citations = [str(c) for c in (entry.get("citations") or [])]
        if indicator == "competitive_moat":
            checks = entry.get("cross_checks") or []
            rationale += ("; cross-checks evidenced: "
                          + (", ".join(str(c) for c in checks)
                             if checks else "none"))
        best = _best_tier(citations, tiers)
        capped = cap_confidence(conf, best)
        if capped != conf:
            rationale += (f" [confidence capped {conf}->{capped}: best "
                          f"cited source tier {best if best else 'n/a'} "
                          f"(Part 10 hierarchy)]")
        return AIDraft(Draft(indicator, score, capped, rationale),
                       citations, self.model, phash)

    def _to_flag(self, entry: Any, tiers: Dict[str, int],
                 phash: str) -> AIFlag:
        if not isinstance(entry, dict) or not isinstance(entry.get("value"),
                                                         bool):
            raise ValueError("malformed structural_deterioration entry: "
                             f"{entry!r}")
        conf = str(entry.get("confidence", "M"))
        if conf not in _CONF_RANK:
            raise ValueError("structural_deterioration: confidence must be "
                             f"H/M/L, got {conf!r}")
        citations = [str(c) for c in (entry.get("citations") or [])]
        return AIFlag(
            name="structural_deterioration",
            value=entry["value"],
            confidence=cap_confidence(conf, _best_tier(citations, tiers)),
            rationale=str(entry.get("rationale", "")),
            citations=citations, model=self.model, prompt_hash=phash)
