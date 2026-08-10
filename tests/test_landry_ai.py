"""Offline tests for Phase 3: AI-drafted judgment indicators
(landry.ai_analyst) and the Part 12 human approval gate
(landry.approvals).

No network, no API key — the Claude client is a fake with canned JSON.
"""

import json
import sys
from types import SimpleNamespace

import pytest

from landry.ai_analyst import (
    AIDraft,
    ClaudeAnalyst,
    EvidencePack,
    JUDGMENT_INDICATORS,
    cap_confidence,
    confidence_ceiling,
)
from landry.approvals import ApprovedScore, ScoreStore
from landry.fundamentals import Draft, FundamentalMetrics
from landry.scoring import INDICATORS, IndicatorScore, score_stock


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

class FakeClient:
    """Anything with .messages.create(...): records calls, returns canned
    text shaped like an anthropic Message (content blocks with .text)."""

    def __init__(self, text):
        self.calls = []

        def create(**kw):
            self.calls.append(kw)
            return SimpleNamespace(content=[SimpleNamespace(text=text)])

        self.messages = SimpleNamespace(create=create)


def _pack(tiers=(1, 2)):
    pack = EvidencePack(
        ticker="ACME", company_name="Acme Corp", sector="Industrials",
        fundamentals=FundamentalMetrics(ticker="ACME", fcf_yield_pct=5.0),
        relative_strength={"diff_blended": 0.08, "score": 4},
        beta={"beta": 1.1, "size_reduction_pct": 0},
        technicals={"above_200w_ma": True})
    for i, t in enumerate(tiers, 1):
        pack.add_evidence(f"evidence-{i} tier{t}", t, source=f"src{i}",
                          date="2026-08-01")
    return pack


CANNED = {
    "competitive_moat": {"score": 4, "confidence": "H",
                         "rationale": "switching costs and pricing power",
                         "citations": ["E1", "E2"], "cross_checks": ["a", "c"]},
    "management_quality": None,
    "revenue_visibility": {"score": 5, "confidence": "H",
                           "rationale": "85% recurring ARR",
                           "citations": ["E1"]},
    "structural_deterioration": {"value": False, "confidence": "M",
                                 "rationale": "cyclical dip only",
                                 "citations": ["E2"]},
}


def _analyst(payload, **kw):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    fake = FakeClient(text)
    return ClaudeAnalyst(client=fake, **kw), fake


# --------------------------------------------------------------------------- #
# EvidencePack
# --------------------------------------------------------------------------- #

def test_evidence_ordered_best_tier_first():
    pack = EvidencePack(ticker="ACME")
    pack.add_evidence("blog take", 5, "blog", "2026-01-01")
    pack.add_evidence("10-K segment data", 1, "FY2025 10-K", "2026-02-01")
    pack.add_evidence("earnings call guidance", 2, "Q2 call", "2026-07-01")
    labeled = pack.labeled_evidence()
    assert [ev.source_tier for _, ev in labeled] == [1, 2, 5]
    assert [label for label, _ in labeled] == ["E1", "E2", "E3"]
    text = pack.render()
    assert (text.index("10-K segment data") < text.index("earnings call")
            < text.index("blog take"))
    assert "[E1] tier 1 (audited filings / regulatory reports)" in text
    assert "dated 2026-02-01" in text


def test_add_evidence_rejects_bad_tier():
    pack = EvidencePack(ticker="ACME")
    with pytest.raises(ValueError, match="source_tier"):
        pack.add_evidence("x", 0, "src")
    with pytest.raises(ValueError, match="source_tier"):
        pack.add_evidence("x", 6, "src")


def test_prompt_contains_rubrics_evidence_and_instructions():
    analyst, _ = _analyst(CANNED)
    p = analyst.build_prompt(_pack())
    # rubrics verbatim markers
    assert "wide moat, multiple reinforcing advantages" in p
    assert "new CEOs default to 3" in p
    assert "more than 80% of next-12-month revenue" in p
    assert "structural break" in p
    # strict output rules
    assert "ONLY a single JSON object" in p
    assert "return null" in p
    assert "multiple corroborating tier 1-2 sources" in p
    # the pack itself
    assert "ACME" in p and "evidence-1 tier1" in p


# --------------------------------------------------------------------------- #
# ClaudeAnalyst parsing
# --------------------------------------------------------------------------- #

def test_parses_drafts_citations_and_skips_null():
    analyst, fake = _analyst(CANNED)
    res = analyst.draft_judgment(_pack())
    # management_quality was null (no evidence) -> absent, never scored
    assert set(res.drafts) == {"competitive_moat", "revenue_visibility"}
    moat = res.drafts["competitive_moat"]
    assert isinstance(moat, AIDraft) and isinstance(moat.draft, Draft)
    assert moat.draft.score == 4 and moat.draft.confidence == "H"
    assert moat.citations == ["E1", "E2"]
    assert "cross-checks evidenced: a, c" in moat.draft.rationale
    assert moat.model == "claude-sonnet-5"
    assert len(moat.prompt_hash) == 16 and moat.prompt_hash == res.prompt_hash
    # boolean flag draft
    flag = res.structural_deterioration
    assert flag.value is False and flag.citations == ["E2"]
    # the API call actually carried the evidence pack
    sent = fake.calls[0]["messages"][0]["content"]
    assert "evidence-1 tier1" in sent
    assert fake.calls[0]["model"] == "claude-sonnet-5"


def test_confidence_ceiling_helper():
    assert confidence_ceiling(1) == "H" and confidence_ceiling(2) == "H"
    assert confidence_ceiling(3) == "M"
    assert confidence_ceiling(4) == "L" and confidence_ceiling(5) == "L"
    assert confidence_ceiling(None) == "L"
    assert cap_confidence("H", 4) == "L"
    assert cap_confidence("L", 1) == "L"       # never upgrades


def test_tier4_only_evidence_caps_at_L_even_if_model_says_H():
    canned = {"competitive_moat": {"score": 4, "confidence": "H",
                                   "rationale": "r", "citations": ["E1"],
                                   "cross_checks": ["a", "b"]},
              "management_quality": None, "revenue_visibility": None,
              "structural_deterioration": None}
    analyst, _ = _analyst(canned)
    res = analyst.draft_judgment(_pack(tiers=(4,)))
    d = res.drafts["competitive_moat"].draft
    assert d.confidence == "L"
    assert "capped H->L" in d.rationale


def test_tier3_evidence_caps_at_M():
    canned = {"competitive_moat": None, "management_quality": None,
              "revenue_visibility": {"score": 4, "confidence": "H",
                                     "rationale": "r", "citations": ["E1"]},
              "structural_deterioration": None}
    analyst, _ = _analyst(canned)
    res = analyst.draft_judgment(_pack(tiers=(3,)))
    assert res.drafts["revenue_visibility"].draft.confidence == "M"


def test_score_with_no_citations_caps_at_L():
    canned = {"competitive_moat": None,
              "management_quality": {"score": 3, "confidence": "H",
                                     "rationale": "r", "citations": []},
              "revenue_visibility": None, "structural_deterioration": None}
    analyst, _ = _analyst(canned)
    res = analyst.draft_judgment(_pack(tiers=(1, 2)))
    assert res.drafts["management_quality"].draft.confidence == "L"


def test_malformed_json_raises_valueerror():
    analyst, _ = _analyst("Sure! Here's my qualitative take: the moat is...")
    with pytest.raises(ValueError, match="JSON"):
        analyst.draft_judgment(_pack())


def test_invalid_score_or_confidence_raises_valueerror():
    bad = dict(CANNED)
    bad["competitive_moat"] = {"score": 7, "confidence": "H",
                               "rationale": "", "citations": []}
    analyst, _ = _analyst(bad)
    with pytest.raises(ValueError, match="1-5"):
        analyst.draft_judgment(_pack())
    bad["competitive_moat"] = {"score": 4, "confidence": "HIGH",
                               "rationale": "", "citations": []}
    analyst, _ = _analyst(bad)
    with pytest.raises(ValueError, match="H/M/L"):
        analyst.draft_judgment(_pack())


def test_missing_anthropic_package_names_the_fix(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # force ImportError
    with pytest.raises(RuntimeError, match="pip install anthropic"):
        ClaudeAnalyst().client


# --------------------------------------------------------------------------- #
# ScoreStore — the Part 12 gate
# --------------------------------------------------------------------------- #

def _ai_draft(indicator="competitive_moat", score=4, conf="H"):
    return AIDraft(draft=Draft(indicator, score, conf, "drafted rationale"),
                   citations=["E1"], model="claude-sonnet-5", prompt_hash="ph")


def test_pending_draft_never_reaches_approved_scores(tmp_path):
    store = ScoreStore(tmp_path / "scores.json")
    store.propose("acme", _ai_draft())
    assert store.approved_scores("ACME") == {}          # the boundary
    assert "competitive_moat" in store.pending("ACME")


def test_approve_flow_and_json_roundtrip(tmp_path):
    path = tmp_path / "scores.json"
    store = ScoreStore(path)
    store.propose("ACME", _ai_draft())
    ap = store.approve("ACME", "competitive_moat", approved_by="taylor")
    assert isinstance(ap, ApprovedScore)
    assert ap.source == "ai_draft" and ap.approved_at.endswith("+00:00")
    scores = store.approved_scores("ACME")
    sc = scores["competitive_moat"]
    assert isinstance(sc, IndicatorScore)
    assert sc.score == 4 and sc.confidence == "H"
    assert sc.evidence == "drafted rationale"
    assert store.pending("ACME") == {}                  # consumed
    # a fresh store on the same file sees identical state
    store2 = ScoreStore(path)
    assert store2.approved_scores("ACME")["competitive_moat"].score == 4
    assert len(store2.audit) == len(store.audit)


def test_override_records_original_and_final(tmp_path):
    store = ScoreStore(tmp_path / "scores.json")
    store.propose("ACME", _ai_draft(score=4, conf="H"))
    ap = store.approve("ACME", "competitive_moat", approved_by="taylor",
                       score=3, confidence="M")
    assert ap.score == 3 and ap.confidence == "M"
    assert ap.original_score == 4 and ap.original_confidence == "H"
    assert store.approved_scores("ACME")["competitive_moat"].score == 3
    last = store.audit[-1]
    assert last["action"] == "approve" and last["overridden"] is True
    assert last["original_score"] == 4 and last["final_score"] == 3


def test_reject_removes_draft_and_blocks_approval(tmp_path):
    store = ScoreStore(tmp_path / "scores.json")
    store.propose("ACME", _ai_draft())
    store.reject("ACME", "competitive_moat", approved_by="taylor",
                 reason="citations do not support a 4")
    assert store.approved_scores("ACME") == {}
    assert store.pending("ACME") == {}
    with pytest.raises(ValueError, match="nothing to approve"):
        store.approve("ACME", "competitive_moat", approved_by="taylor")


def test_audit_trail_grows_with_every_action(tmp_path):
    store = ScoreStore(tmp_path / "scores.json")
    assert store.audit == []
    store.propose("ACME", _ai_draft("competitive_moat"))
    store.propose("ACME", _ai_draft("revenue_visibility"))
    store.approve("ACME", "competitive_moat", approved_by="taylor")
    store.reject("ACME", "revenue_visibility", approved_by="taylor",
                 reason="thin evidence")
    actions = [a["action"] for a in store.audit]
    assert actions == ["propose", "propose", "approve", "reject"]
    assert all("at" in a and a["ticker"] == "ACME" for a in store.audit)


def test_bad_source_and_missing_pending_raise(tmp_path):
    store = ScoreStore(tmp_path / "scores.json")
    with pytest.raises(ValueError, match="source"):
        store.propose("ACME", _ai_draft(), source="robo_approved")
    with pytest.raises(ValueError, match="nothing to approve"):
        store.approve("ACME", "competitive_moat", approved_by="taylor")


# --------------------------------------------------------------------------- #
# end-to-end: AI drafts + quant drafts -> approvals -> composite score
# --------------------------------------------------------------------------- #

def test_approved_scores_feed_score_stock_end_to_end(tmp_path):
    canned = {
        "competitive_moat": {"score": 4, "confidence": "H",
                             "rationale": "narrow but durable moat",
                             "citations": ["E1", "E2"],
                             "cross_checks": ["a", "d"]},
        "management_quality": {"score": 3, "confidence": "M",
                               "rationale": "new CEO — defaults to 3",
                               "citations": ["E2"]},
        "revenue_visibility": {"score": 4, "confidence": "H",
                               "rationale": "70% contracted",
                               "citations": ["E1"]},
        "structural_deterioration": {"value": False, "confidence": "M",
                                     "rationale": "no break",
                                     "citations": ["E1"]},
    }
    analyst, _ = _analyst(canned)
    res = analyst.draft_judgment(_pack(tiers=(1, 2)))
    assert set(res.drafts) == set(JUDGMENT_INDICATORS)

    store = ScoreStore(tmp_path / "scores.json")
    for d in res.drafts.values():
        store.propose("ACME", d, source="ai_draft")
    quant = [k for k in INDICATORS if k not in JUDGMENT_INDICATORS]
    for k in quant:
        store.propose("ACME", Draft(k, 4, "M", "quant rubric draft"),
                      source="quant_draft")

    # approve everything except one Tier 1 indicator: scoring must refuse
    for k in list(INDICATORS):
        if k != "fcf_yield_trend":
            store.approve("ACME", k, approved_by="taylor")
    with pytest.raises(ValueError, match="missing indicator"):
        score_stock("ACME", store.approved_scores("ACME"))

    # approve the last draft: full composite
    store.approve("ACME", "fcf_yield_trend", approved_by="taylor")
    scores = store.approved_scores("ACME")
    assert set(scores) == set(INDICATORS)
    card = score_stock("ACME", scores, debt_to_fcf=1.2)
    # eleven 4s + management_quality 3 (weight 0.08): 80 - 1*0.08*20 = 78.4
    assert card.composite == pytest.approx(78.4)
    assert card.decision == "BUY"
    assert card.flags.tier1_passes
