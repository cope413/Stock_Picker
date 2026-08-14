"""Offline tests for landry.gov_filings backlog extraction. Pinned to the
real filing text pulled from SEC EDGAR for ETN's Q2 2026 10-Q, PH's Q3
FY26 10-Q, and GE's Q2 2026 10-Q (2026-08-13) -- no network in these
tests, the sentences are copied in verbatim so extraction logic is
exercised without a live fetch.
"""

import pytest

from landry.gov_filings import (
    BacklogDisclosure,
    draft_revenue_visibility_from_backlog,
    extract_backlog,
    revenue_visibility_band,
)

# Verbatim (whitespace-collapsed) from Eaton Corp plc 10-Q, period 2026-06-30
ETN_10Q_TEXT = (
    "A significant portion of open orders placed with Eaton are by customers of "
    "electrical products and electrical system and services, original equipment "
    "manufacturers or distributors. These open orders are not considered firm as "
    "they have been historically subject to releases by customers. In measuring "
    "backlog of unsatisfied or partially satisfied obligations, only the amount "
    "of orders to which customers are firmly committed are included. Using this "
    "criterion, total backlog at June 30, 2026 was approximately $ 24.1 billion. "
    "At June 30, 2026, approximately 71 % of this backlog is targeted for "
    "delivery to customers in the next twelve months and the rest thereafter."
)

# Verbatim (whitespace-collapsed) from Parker-Hannifin 10-Q, period 2026-03-31
PH_10Q_TEXT = (
    "We believe our backlog represents our unsatisfied or partially unsatisfied "
    "performance obligations. Backlog at March 31, 2026 was $ 12.5 billion, of "
    "which approximately 67 percent is expected to be recognized as revenue "
    "within the next 12 months and the balance thereafter."
)

# Verbatim (whitespace-collapsed) from GE (GE Aerospace) 10-Q, period
# 2026-06-30 -- a third phrasing: no "backlog" keyword, equipment/services
# split, each with a list of cumulative percentages against year milestones.
GE_10Q_TEXT = (
    "REMAINING PERFORMANCE OBLIGATION. As of June 30, 2026, the aggregate "
    "amount of the contracted revenue allocated to our unsatisfied (or "
    "partially unsatisfied) performance obligations was $ 210,790 million. We "
    "expect to recognize revenue as we satisfy our remaining performance "
    "obligations as follows: (1) equipment-related remaining performance "
    "obligation of $ 32,085 million, of which 32 %, 54 % and 91 % is expected "
    "to be satisfied within 1 , 2 and 5 years, respectively; and (2) "
    "services-related remaining performance obligation of $ 178,705 million, "
    "of which 12 %, 40 %, 66 % and 82 % is expected to be recognized within "
    "1 , 5 , 10 and 15 years, respectively, and the remaining thereafter."
)


def test_extract_backlog_etn_two_sentence_phrasing():
    d = extract_backlog(ETN_10Q_TEXT)
    assert d.amount_usd_billions == pytest.approx(24.1)
    assert d.pct_next_12mo == pytest.approx(71.0)
    assert d.next_12mo_usd_billions == pytest.approx(17.111, abs=0.01)


def test_extract_backlog_ph_one_sentence_phrasing():
    d = extract_backlog(PH_10Q_TEXT)
    assert d.amount_usd_billions == pytest.approx(12.5)
    assert d.pct_next_12mo == pytest.approx(67.0)
    assert d.next_12mo_usd_billions == pytest.approx(8.375, abs=0.01)


def test_extract_backlog_millions_normalized_to_billions():
    text = ("Backlog at March 31, 2026 was $ 500 million, of which "
            "approximately 50 percent is expected within the next 12 months.")
    d = extract_backlog(text)
    assert d.amount_usd_billions == pytest.approx(0.5)


def test_extract_backlog_none_when_absent():
    assert extract_backlog("No backlog disclosure in this filing at all.") is None
    assert extract_backlog("Revenue grew 10% year over year.") is None


def test_extract_backlog_does_not_match_unrelated_percentage_far_away():
    # a "backlog... $X billion" followed, much later, by an unrelated
    # percentage should NOT be stitched together into a false match
    text = ("Total backlog was approximately $ 5.0 billion. " + ("filler. " * 60) +
            "Employee turnover was approximately 12 percent within the next 12 months.")
    assert extract_backlog(text) is None


def test_revenue_visibility_band():
    assert revenue_visibility_band(85) == 5
    assert revenue_visibility_band(65) == 4
    assert revenue_visibility_band(53) == 3
    assert revenue_visibility_band(39) == 2
    assert revenue_visibility_band(15) == 1


def test_draft_revenue_visibility_pinned_to_etn_2026_08():
    d = extract_backlog(ETN_10Q_TEXT)
    draft = draft_revenue_visibility_from_backlog(d, 32.0, "ETN 10-Q Q2 2026")
    assert draft.score == 3
    assert draft.confidence == "M"
    assert "17.11B" in draft.rationale or "17.1" in draft.rationale


def test_draft_revenue_visibility_pinned_to_ph_2026_08_boundary_flagged():
    d = extract_backlog(PH_10Q_TEXT)
    draft = draft_revenue_visibility_from_backlog(d, 21.5, "PH 10-Q Q3 FY26")
    assert draft.score == 2                # ~39%, just under the 40% band
    assert draft.confidence == "M"
    assert "boundary" in draft.rationale.lower()


def test_extract_equipment_services_rpo_ge_phrasing():
    # GE has no "backlog" keyword at all -- falls through the primary
    # regex to the equipment/services RPO fallback
    d = extract_backlog(GE_10Q_TEXT)
    assert d.amount_usd_billions == pytest.approx(210.79)
    # next-12mo = 32% of $32,085M + 12% of $178,705M = $31.7118B
    assert d.next_12mo_usd_billions == pytest.approx(31.7118, abs=0.01)


def test_draft_revenue_visibility_pinned_to_ge_2026_08_boundary_flagged():
    d = extract_backlog(GE_10Q_TEXT)
    draft = draft_revenue_visibility_from_backlog(d, 51.5, "GE 10-Q Q2 2026")
    assert draft.score == 4                # ~62%, just above the 60% band line
    assert draft.confidence == "M"
    assert "boundary" in draft.rationale.lower()


def test_extract_backlog_prefers_backlog_phrasing_over_rpo_fallback():
    # a filing with BOTH phrasings should match the more specific
    # backlog sentence first, not fall through to the RPO parser
    combined = ETN_10Q_TEXT + " " + GE_10Q_TEXT
    d = extract_backlog(combined)
    assert d.amount_usd_billions == pytest.approx(24.1)  # ETN's, not GE's
