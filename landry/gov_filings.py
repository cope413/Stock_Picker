"""Landry System v1.0 — SEC EDGAR filings (Part 10 tier-1 source: audited
filings/regulatory reports). Currently covers one thing: backlog /
Remaining Performance Obligations, for the Revenue Visibility indicator
(Tier 1, 10%).

EDGAR is public and needs no authentication — the only requirement is a
descriptive User-Agent header per SEC's fair-access policy
(https://www.sec.gov/os/webmaster-faq#developers); a bare/default UA gets
a 403. Every filing fetch is cached to disk by accession number, since a
filed 10-K/10-Q never changes.

Backlog disclosure is voluntary prose, not a reliably-tagged XBRL concept
across companies — checked directly before building this: Eaton's
``us-gaap:RevenueRemainingPerformanceObligation`` tag is stale since
2024Q1 even though its FY2026 10-Q discloses backlog in the text every
quarter; Parker-Hannifin's total-backlog tag is current but its
percentage-within-12-months tag hasn't been used since 2018. So this
module regex-extracts the disclosure sentence rather than trusting
structured XBRL, and always keeps the matched text in the result — a
wrong extraction should be visible on inspection, not silently trusted.

Deliberately NOT wired into landry.fundamentals.draft_quant_scores: unlike
the automated Tier 1/3 quant drafts (which read structured statement line
items), this is free-text extraction with real false-positive risk (the
wrong sentence near the word "backlog"). Review the matched snippet before
proposing it into the score store.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from typing import Optional, Sequence

from landry.fundamentals import Draft

USER_AGENT = os.environ.get("SEC_EDGAR_USER_AGENT",
                            "Landry Research Tool alan.landry@gmail.com")

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_HERE, "filings_cache")

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_ARCHIVE_URL = ("https://www.sec.gov/Archives/edgar/data/{cik}/"
                "{accession_nodash}/{doc}")


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _cache_path(*parts: str) -> str:
    return os.path.join(CACHE_DIR, *parts)


# --------------------------------------------------------------------------- #
# EDGAR lookup
# --------------------------------------------------------------------------- #

def cik_for_ticker(ticker: str) -> Optional[str]:
    """SEC CIK for a ticker (plain digit string, no zero-padding). Cached
    indefinitely -- CIK assignments don't change."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path("ticker_cik_map.json")
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(_get(_TICKER_MAP_URL))
    with open(path) as f:
        rows = json.load(f).values()
    for row in rows:
        if row["ticker"].upper() == ticker.upper():
            return str(row["cik_str"])
    return None


@dataclass
class FilingRef:
    """One filing's location, enough to build its document URL."""
    cik: str
    accession: str          # dashed, e.g. "0001551182-26-000030"
    form: str                # "10-Q" | "10-K"
    filed: str                # ISO date filed
    period: str               # ISO date, period of report
    primary_document: str

    @property
    def url(self) -> str:
        return _ARCHIVE_URL.format(cik=int(self.cik),
                                   accession_nodash=self.accession.replace("-", ""),
                                   doc=self.primary_document)


def latest_filing(ticker: str,
                  forms: Sequence[str] = ("10-Q", "10-K")) -> Optional[FilingRef]:
    """Most recent filing whose form is in ``forms`` (SEC returns each
    company's filing list newest-first, so this is a linear scan)."""
    cik = cik_for_ticker(ticker)
    if cik is None:
        return None
    data = json.loads(_get(_SUBMISSIONS_URL.format(cik=int(cik))))
    recent = data["filings"]["recent"]
    for i, form in enumerate(recent["form"]):
        if form in forms:
            return FilingRef(cik=cik, accession=recent["accessionNumber"][i],
                             form=form, filed=recent["filingDate"][i],
                             period=recent["reportDate"][i],
                             primary_document=recent["primaryDocument"][i])
    return None


def fetch_filing_text(ref: FilingRef) -> str:
    """Plain text of one filing (HTML tags stripped, whitespace
    collapsed). Cached forever by accession number -- a filed document
    never changes."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(f"{ref.accession}.txt")
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    html = _get(ref.url).decode("utf-8", errors="ignore")
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"\s+", " ", text)
    with open(path, "w") as f:
        f.write(text)
    return text


# --------------------------------------------------------------------------- #
# backlog extraction (Revenue Visibility, Tier 1, 10%)
# --------------------------------------------------------------------------- #

@dataclass
class BacklogDisclosure:
    amount_usd_billions: float
    pct_next_12mo: float
    snippet: str             # matched text, for human verification

    @property
    def next_12mo_usd_billions(self) -> float:
        return self.amount_usd_billions * self.pct_next_12mo / 100.0


# Matches both real phrasings seen in practice:
#   ETN: "total backlog at June 30, 2026 was approximately $24.1 billion.
#         At June 30, 2026, approximately 71% of this backlog is targeted
#         for delivery ... in the next twelve months" -- TWO sentences,
#         the amount and the percentage are on opposite sides of a period.
#   PH:  "Backlog at March 31, 2026 was $12.5 billion, of which
#         approximately 67 percent is expected to be recognized as
#         revenue within the next 12 months" -- ONE sentence.
# The gap between amount and percentage must therefore allow periods
# (bounded, so it can't wander into an unrelated number further down the
# filing); the gap right around "backlog" and right before "months"
# stays period-free since those are always tight, same-sentence spans.
_BACKLOG_RE = re.compile(
    r"backlog[^.]{0,120}?\$\s?([\d,.]+)\s?(billion|million)"
    r".{0,220}?(?:approximately\s+)?(\d+(?:\.\d+)?)\s?(?:%|percent)"
    r"[^.]{0,80}?(?:next|within)\s+(?:twelve|12)\s+months",
    re.IGNORECASE)


# A third phrasing (seen in GE's 10-Q, not ETN's or PH's): no "backlog"
# keyword at all -- "Remaining Performance Obligation" split into
# equipment-related and services-related pieces, each with its own list
# of cumulative percentages against a list of year milestones ("of which
# 32%, 54% and 91% is expected to be satisfied within 1, 2 and 5 years").
# Assumption, true in the filing checked: the FIRST percentage in each
# list is always the 1-year figure (years are listed ascending). Not a
# general list-matcher -- if a filing ever lists percentages out of
# order this would silently misread it, which is exactly why the match
# is kept as a snippet rather than trusted blindly.
_EQUIPMENT_SERVICES_RPO_RE = re.compile(
    r"aggregate amount of the contracted revenue.{0,150}?\$\s?([\d,]+)\s?million"
    r".{0,600}?equipment-related remaining performance obligation of "
    r"\$\s?([\d,]+)\s?million,\s?of which\s?(\d+(?:\.\d+)?)\s?%"
    r".{0,600}?services-related remaining performance obligation of "
    r"\$\s?([\d,]+)\s?million,\s?of which\s?(\d+(?:\.\d+)?)\s?%",
    re.IGNORECASE)


def _extract_equipment_services_rpo(text: str) -> Optional[BacklogDisclosure]:
    m = _EQUIPMENT_SERVICES_RPO_RE.search(text)
    if not m:
        return None
    total_m = float(m.group(1).replace(",", ""))
    equip_m, equip_1yr_pct = float(m.group(2).replace(",", "")), float(m.group(3))
    svc_m, svc_1yr_pct = float(m.group(4).replace(",", "")), float(m.group(5))
    next_12mo_m = equip_m * equip_1yr_pct / 100.0 + svc_m * svc_1yr_pct / 100.0
    return BacklogDisclosure(amount_usd_billions=total_m / 1000.0,
                             pct_next_12mo=100.0 * next_12mo_m / total_m,
                             snippet=m.group(0))


def extract_backlog(text: str) -> Optional[BacklogDisclosure]:
    """Regex-extract a backlog / remaining-performance-obligation
    disclosure from filing prose, trying each known phrasing in turn.
    Returns None (not a guess) when nothing matches -- absence of a
    match is the safe failure mode; the risk that matters is matching
    the *wrong* text, which is why the matched snippet is always kept."""
    m = _BACKLOG_RE.search(text)
    if m:
        amount = float(m.group(1).replace(",", ""))
        if m.group(2).lower() == "million":
            amount /= 1000.0
        return BacklogDisclosure(amount_usd_billions=amount,
                                 pct_next_12mo=float(m.group(3)),
                                 snippet=m.group(0))
    return _extract_equipment_services_rpo(text)


def revenue_visibility_band(pct_of_annual_revenue: float) -> int:
    """Same bands as landry.ai_analyst.REVENUE_VISIBILITY_RUBRIC:
    5 >= 80%; 4 = 60-80%; 3 = 40-60%; 2 = 20-40%; 1 < 20%."""
    if pct_of_annual_revenue >= 80:
        return 5
    if pct_of_annual_revenue >= 60:
        return 4
    if pct_of_annual_revenue >= 40:
        return 3
    if pct_of_annual_revenue >= 20:
        return 2
    return 1


def draft_revenue_visibility_from_backlog(
        disclosure: BacklogDisclosure, annual_revenue_usd_billions: float,
        source: str) -> Draft:
    """Revenue Visibility (Tier 1, 10%) from a filing's backlog
    disclosure. Confidence M -- a single primary-source snapshot, not H
    (that needs a multi-period trend, per the Part 10 confidence
    discipline used throughout this engine)."""
    pct = (100.0 * disclosure.next_12mo_usd_billions
          / annual_revenue_usd_billions)
    sc = revenue_visibility_band(pct)
    boundary = min(abs(pct - b) for b in (20, 40, 60, 80))
    note = (f" -- NOTE: {pct:.0f}% is within 2pp of a band boundary; "
           "treat as approximate, not a clean call." if boundary < 2 else "")
    return Draft(
        "revenue_visibility", sc, "M",
        f"{disclosure.pct_next_12mo:.0f}% of ${disclosure.amount_usd_billions:.1f}B "
        f"backlog targeted for next 12 months = "
        f"${disclosure.next_12mo_usd_billions:.2f}B, {pct:.0f}% of "
        f"~${annual_revenue_usd_billions:.1f}B annual revenue. Source: {source}. "
        f"Matched text: \"{disclosure.snippet}\"{note}")


def draft_revenue_visibility(ticker: str, annual_revenue_usd_billions: float,
                             forms: Sequence[str] = ("10-Q", "10-K")
                             ) -> Optional[Draft]:
    """End-to-end: latest 10-Q/10-K -> backlog extraction -> Draft.
    Returns None if no filing is found or no backlog sentence matches --
    callers should treat None as "needs a human/manual look," not retry
    with a guess."""
    ref = latest_filing(ticker, forms)
    if ref is None:
        return None
    text = fetch_filing_text(ref)
    disclosure = extract_backlog(text)
    if disclosure is None:
        return None
    source = f"{ref.form}, period {ref.period} (SEC EDGAR, filed {ref.filed})"
    return draft_revenue_visibility_from_backlog(
        disclosure, annual_revenue_usd_billions, source)
