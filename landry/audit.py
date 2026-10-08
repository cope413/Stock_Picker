"""Landry System v1.0 — workbook structural-integrity audit.

Exists because of a pattern, not a single incident: a 2026-09-08 tab-by-
tab review found five independent cases of the same failure shape in one
session -- a cross-tab formula reference, a hardcoded reader bound, or a
sheet's own page setup silently going stale as the workbook grew or was
rebuilt, with nothing to catch it except a human happening to look for
it. None of these show up in a recalc (they're not formula errors) or a
naive value diff (several are metadata, not cell values). This module is
that catch, automated.

Run after any structural edit and before any commit that touches the
workbook -- the same discipline `landry.xlsx_recalc` already has, extended
to cover correctness, not just "did the formulas recalculate."

Run: python -m landry audit
"""

from __future__ import annotations

import inspect
import os
import re
import subprocess
import tempfile
from typing import Dict, List, Optional

import openpyxl
from openpyxl.utils import range_boundaries

from landry.doctor import Check

# Header row for each sheet, where a cross-tab reference's target column
# can be sanity-checked against a real header. None = freeform tab, skip
# the header check. Sheets not listed default to row 2 (banner on row 1,
# headers on row 2 is the workbook's overwhelming convention).
_HEADER_ROW = {
    "Dashboard": 1,
    "Correlation Matrix": 5,
    "Holding Monitor": 3,               # row 2 is a merged tier-group banner
    "Implied-Return Calculator": 3,     # row 2 is a merged scenario-group banner
    "Journal": None,
    "Instructions": None,
    "Action Items": None,
    "Schema Reference": None,
}
_DEFAULT_HEADER_ROW = 2

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 'Sheet Name'!A1  or  SheetName!A1 , optionally a !A1:B2 range.
_XREF_RE = re.compile(
    r"(?:'([^']+)'|\b([A-Za-z][A-Za-z0-9 _.\-]*[A-Za-z0-9])\b)!"
    r"(\$?[A-Z]{1,3}\$?\d+)(?::(\$?[A-Z]{1,3}\$?\d+))?"
)


def check_table_refs(path: str) -> List[Check]:
    """Every registered Table's `ref` should cover all the real data in
    its column span. A Table that's fallen behind (new rows added past
    its declared end) silently loses the Table's filter/formatting for
    those rows, and any formula elsewhere that reads the Table by
    structured reference misses them entirely -- this is the exact shape
    of the Action Items E28/E29 bug (a plain range reference, not even a
    Table, but the same "declared end lags real data" failure).

    A row counts as "real missed data," not a footnote/subtotal row
    sitting just past the table on purpose (a common convention in this
    workbook -- Current Positions' notes row, Dashboard's ticker-count
    footer, Performance Tracking's methodology note), only if at least
    half the table's columns are populated in it. A footnote is sparse
    (1-2 cells of prose or a lone count); a real data row densely fills
    most of the table's fields."""
    wb = openpyxl.load_workbook(path, data_only=True)
    out = []
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        for name in ws.tables.keys():
            tbl = ws.tables[name]  # ws.tables.items() yields (name, ref-string)
                                    # in this openpyxl version; [name] gives the
                                    # real Table object with .ref -- inconsistent
                                    # but that's the API as installed here.
            min_col, min_row, max_col, max_row = range_boundaries(tbl.ref)
            width = max_col - min_col + 1
            actual_last = max_row
            for r in range(max_row + 1, ws.max_row + 1):
                filled = sum(1 for c in range(min_col, max_col + 1)
                            if ws.cell(row=r, column=c).value not in (None, ""))
                if filled >= max(2, width / 2):
                    actual_last = r
            ok = actual_last <= max_row
            out.append(Check(
                f"table_ref:{sheet}/{name}", ok,
                f"ref={tbl.ref}, live data in that column span extends to row {actual_last}",
                fix=(None if ok else
                     f"extend {name}'s ref past row {max_row} to row {actual_last} -- "
                     f"rows added beyond a Table's ref don't inherit its "
                     f"formatting, formulas, or filter")))
    wb.close()
    return out


def check_merges_inside_tables(path: str) -> List[Check]:
    """No merged range may overlap a Table. Excel cannot represent a merged cell
    inside a Table and answers such a file with "We found a problem with some
    content ... recover?" (then drops the Table), while openpyxl and LibreOffice
    accept it silently. Found 2026-10-02 on Current Positions: the 9/30 row
    insertion grew CurrentPositionsTable to row 51 but left the note's merge
    A50:M50 behind at its old row, now inside the Table. openpyxl's insert_rows
    moves neither merged ranges nor their heights."""
    wb = openpyxl.load_workbook(path)
    clashes = []
    for ws in wb.worksheets:
        for tname in ws.tables.keys():
            t_min_col, t_min_row, t_max_col, t_max_row = range_boundaries(ws.tables[tname].ref)
            for merged in ws.merged_cells.ranges:
                if (merged.min_row <= t_max_row and merged.max_row >= t_min_row
                        and merged.min_col <= t_max_col and merged.max_col >= t_min_col):
                    clashes.append(f"{ws.title}: merge {merged} overlaps {tname} ({ws.tables[tname].ref})")
    wb.close()
    if not clashes:
        return [Check("merges_inside_tables", True, "no merged range overlaps a Table")]
    return [Check("merges_inside_tables", False, f"{len(clashes)} clash(es): " + "; ".join(clashes[:4]),
                  fix="unmerge it, or move the merge outside the Table's rows (and give the Table's "
                      "own rows ordinary cells); Excel reports the file as corrupt otherwise")]


def check_row_height_ceiling(path: str) -> List[Check]:
    """No row may be taller than Excel's 409.5pt maximum. Excel answers a
    workbook holding one with "We found a problem with some content ... Do you
    want us to try to recover as much as we can?" -- which LibreOffice, openpyxl
    and every other check here accept without complaint. Found 2026-10-02: Current
    Positions row 52, a 1,049-character note wrapped in an 11-wide column, auto-fit
    by LibreOffice to 941pt and sitting in every commit since 9/30 (a row insert had
    left the note's merge and height behind at its old row). ``xlsx_recalc`` now
    caps such rows on every save; this catches a file that never went through it."""
    from landry.xlsx_recalc import EXCEL_MAX_ROW_PT
    wb = openpyxl.load_workbook(path)
    tall = [(ws.title, r, dim.height) for ws in wb.worksheets
            for r, dim in ws.row_dimensions.items()
            if dim.height and dim.height > EXCEL_MAX_ROW_PT]
    wb.close()
    if not tall:
        return [Check("row_height_ceiling", True, f"no row above Excel's {EXCEL_MAX_ROW_PT}pt maximum")]
    shown = ", ".join(f"{s}!{r} ({h:g}pt)" for s, r, h in tall[:6])
    return [Check("row_height_ceiling", False,
                  f"{len(tall)} row(s) above Excel's {EXCEL_MAX_ROW_PT}pt maximum: {shown}",
                  fix="Excel will report the file as corrupt. Merge the cell's note across the table "
                      "width, widen its column, or shorten the text; then recalc")]


def check_reader_bounds(path: str) -> List[Check]:
    """landry/xlsx_io.py has several readers with a hardcoded max_row,
    each one deliberately bounded (per its own docstring) to avoid
    reading footnote text below a table as a bogus row. Each is a
    landmine: the day the real table grows past that row, the reader
    starts silently dropping rows -- exactly what happened to
    read_entry_checklist and read_monitor_notes (caught 2026-08-24, see
    LANDRY_DATABASE_DESIGN.md) before being unbounded. This re-checks
    every *remaining* hardcoded bound on every run instead of waiting for
    the next manual migration pass to notice."""
    import landry.xlsx_io as xio
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    out = []
    for name, fn in inspect.getmembers(xio, inspect.isfunction):
        if not name.startswith("read_"):
            continue
        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):
            continue
        # only a bound on the real data loop counts -- a header-only read
        # (e.g. read_price_history's `min_row=2, max_row=2` ticker-name
        # row) isn't a "will silently drop future rows" risk. Every real
        # data-loop bound in this codebase pairs max_row with min_row=3
        # (row 3 = first data row, the workbook's universal convention).
        bound_m = re.search(r"min_row\s*=\s*3\s*,\s*max_row\s*=\s*(\d+)", src)
        if not bound_m:
            continue
        bound = int(bound_m.group(1))
        sheet_m = re.search(r'sheet:\s*str\s*=\s*"([^"]+)"', src)
        sheet = sheet_m.group(1) if sheet_m else None
        if not sheet or sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        width = ws.max_column
        actual_last = bound
        for i, row_vals in enumerate(ws.iter_rows(min_row=bound + 1, values_only=True)):
            r = bound + 1 + i
            filled = sum(1 for v in row_vals if v not in (None, ""))
            # same density rule as check_table_refs: a real data row fills
            # most of the row's columns; a footnote/subtotal past the
            # boundary (deliberately excluded, per several readers' own
            # docstrings) is sparse -- 1-2 cells of prose or a lone count.
            if filled >= max(2, width / 2):
                actual_last = r
        ok = actual_last <= bound
        out.append(Check(
            f"reader_bound:{name}", ok,
            f"hardcoded max_row={bound}, live data in '{sheet}' extends to row {actual_last}",
            fix=(None if ok else
                 f"landry/xlsx_io.py:{name} will silently drop rows "
                 f"{bound + 1}-{actual_last} of '{sheet}' -- raise or "
                 f"remove its max_row bound")))
    wb.close()
    return out


def _resolve_ref(m, sheetnames):
    sheet = m.group(1) or m.group(2)
    if sheet not in sheetnames:
        return None
    start_col, start_row = range_boundaries(m.group(3) + ":" + m.group(3))[0:2]
    end_row = None
    if m.group(4):
        end_row = range_boundaries(m.group(4) + ":" + m.group(4))[1]
    return sheet, start_col, start_row, end_row


def check_cross_tab_references(path: str) -> List[Check]:
    """Scans every formula in the workbook for cross-tab references
    ('Sheet'!A1 or Sheet!A1:A2) and sanity-checks each target: (1) does
    the target column still have a real header where the target sheet
    has a known header row -- catches a reference left pointing at a
    column that no longer means anything after a structural shift, the
    same failure as this session's Recommended Action bug; (2) for a
    range reference, does it cover the target sheet's actual data extent
    in that column -- catches a truncated range, the same failure as
    Action Items' E28/E29. This does NOT catch a reference that points at
    a column which still has *a* header, just the wrong one (Recommended
    Action's bug was exactly this before it was fixed by hand) -- that
    needs a human or a much more specific check, not a generic scan."""
    wb = openpyxl.load_workbook(path, data_only=False)
    sheetnames = set(wb.sheetnames)
    out = []
    seen = set()
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        for row in ws.iter_rows():
            for cell in row:
                if not (isinstance(cell.value, str) and cell.value.startswith("=")):
                    continue
                for m in _XREF_RE.finditer(cell.value):
                    resolved = _resolve_ref(m, sheetnames)
                    if resolved is None:
                        continue
                    tgt_sheet, tgt_col, tgt_row, tgt_end_row = resolved
                    header_row = _HEADER_ROW.get(tgt_sheet, _DEFAULT_HEADER_ROW)
                    if header_row is not None:
                        header = wb[tgt_sheet].cell(row=header_row, column=tgt_col).value
                        key = ("hdr", sheet, tgt_sheet, tgt_col)
                        if not header and key not in seen:
                            seen.add(key)
                            from openpyxl.utils import get_column_letter
                            out.append(Check(
                                f"xref_column:{sheet}->{tgt_sheet}!{get_column_letter(tgt_col)}",
                                False,
                                f"{sheet}!{cell.coordinate} references "
                                f"'{tgt_sheet}'!{get_column_letter(tgt_col)}{header_row}, "
                                f"which is blank",
                                fix=f"check whether {sheet}!{cell.coordinate}'s formula "
                                    f"still points at the right column in '{tgt_sheet}'"))
                    if tgt_end_row is not None:
                        tgt_ws = wb[tgt_sheet]
                        real_last = tgt_row
                        for r in range(tgt_row, tgt_ws.max_row + 1):
                            if tgt_ws.cell(row=r, column=tgt_col).value not in (None, ""):
                                real_last = r
                        key = ("range", sheet, tgt_sheet, tgt_col, tgt_end_row)
                        if real_last > tgt_end_row and key not in seen:
                            seen.add(key)
                            from openpyxl.utils import get_column_letter
                            out.append(Check(
                                f"xref_range:{sheet}->{tgt_sheet}!{get_column_letter(tgt_col)}",
                                False,
                                f"{sheet}!{cell.coordinate} covers '{tgt_sheet}'!"
                                f"{get_column_letter(tgt_col)}{tgt_row}:{tgt_end_row}, "
                                f"but that column has data through row {real_last}",
                                fix=f"extend {sheet}!{cell.coordinate}'s range to row "
                                    f"{real_last} (or later, to leave headroom)"))
    if not out:
        out.append(Check("cross_tab_references", True,
                          "no blank-target or truncated-range references found"))
    wb.close()
    return out


def check_page_setup_vs_last_commit(path: str, repo_dir: Optional[str] = None) -> List[Check]:
    """Compares every sheet's paperSize/footer/gridlines against the last
    git-committed version of this file. A full-teardown-rebuild of a
    sheet (wb.remove() + wb.create_sheet(), the standard pattern for any
    structural change) drops all of this silently -- it happened twice in
    one session (Entry Checklist, then Schema Reference) before being
    caught by hand each time. Neither recalc nor a value diff would ever
    catch it: none of this is a formula or a cell value."""
    repo_dir = repo_dir or _REPO
    try:
        rel = os.path.relpath(path, repo_dir)
        committed = subprocess.run(
            ["git", "show", f"HEAD:{rel}"], cwd=repo_dir,
            capture_output=True, check=True)
    except Exception as e:
        return [Check("page_setup_vs_commit", True,
                      f"skipped -- couldn't read last commit ({e})")]

    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(committed.stdout)
        prev_wb = openpyxl.load_workbook(tmp_path)
        cur_wb = openpyxl.load_workbook(path)
        out = []
        for sheet in cur_wb.sheetnames:
            if sheet not in prev_wb.sheetnames:
                continue
            prev_ws, cur_ws = prev_wb[sheet], cur_wb[sheet]
            mismatches = []
            # An absent attribute means the file-format default (ECMA-376: paperSize 1 = Letter, gridlines
            # shown), and Excel simply leaves default-valued attributes out when it saves -- so an Excel save
            # of an unchanged sheet reads "paperSize 1->None; showGridLines True->None" (found 2026-10-05 when
            # Alan's Excel-saved review copy became the live workbook: 6 false alarms). Compare the effective
            # values; a rebuild that dropped a non-default setup (A4 -> unset) is still caught.
            prev_paper = 1 if prev_ws.page_setup.paperSize is None else prev_ws.page_setup.paperSize
            cur_paper = 1 if cur_ws.page_setup.paperSize is None else cur_ws.page_setup.paperSize
            if prev_paper != cur_paper:
                mismatches.append(f"paperSize {prev_ws.page_setup.paperSize!r}"
                                  f"->{cur_ws.page_setup.paperSize!r}")
            prev_grid = True if prev_ws.sheet_view.showGridLines is None else prev_ws.sheet_view.showGridLines
            cur_grid = True if cur_ws.sheet_view.showGridLines is None else cur_ws.sheet_view.showGridLines
            if prev_grid != cur_grid:
                mismatches.append(f"showGridLines {prev_ws.sheet_view.showGridLines}"
                                  f"->{cur_ws.sheet_view.showGridLines}")
            for part in ("left", "center", "right"):
                pf = getattr(prev_ws.oddFooter, part)
                cf = getattr(cur_ws.oddFooter, part)
                if (pf.text or None) != (cf.text or None):
                    mismatches.append(f"oddFooter.{part} {pf.text!r}->{cf.text!r}")
            ok = not mismatches
            out.append(Check(
                f"page_setup:{sheet}", ok,
                "matches last commit" if ok else "; ".join(mismatches),
                fix=(None if ok else
                     "a sheet rebuild likely dropped page setup/footer -- "
                     "restore paperSize/oddFooter/showGridLines from the "
                     "last committed version before saving")))
        prev_wb.close()
        cur_wb.close()
        return out
    finally:
        os.unlink(tmp_path)


def check_schema_reference(path: str) -> List[Check]:
    """Schema Reference documents every tab's Table name and row range
    for programmatic access. Cross-checks each entry's claimed Table name
    and leading A1:Z99-style range against what's actually live --
    this doc drifted badly (found 2026-09-08: 3 tabs missing entirely,
    several others describing a layout from months ago) and needs the
    same automatic check as everything else, not just a periodic manual
    audit."""
    wb = openpyxl.load_workbook(path, data_only=True)
    if "Schema Reference" not in wb.sheetnames:
        return [Check("schema_reference", True, "tab not present, skipped")]
    ws = wb["Schema Reference"]
    range_re = re.compile(r"\b([A-Z]{1,3}\d+):([A-Z]{1,3}\d+)\b")
    out = []
    r = 4
    while r <= ws.max_row:
        name = ws.cell(row=r, column=1).value
        if not name:
            r += 4
            continue
        if name not in wb.sheetnames:
            out.append(Check(f"schema_ref:{name}", False,
                              "documents a tab that no longer exists",
                              fix="remove or rename this Schema Reference entry"))
            r += 4
            continue
        target = wb[name]
        tbl_name = ws.cell(row=r + 2, column=2).value
        data_rows_text = str(ws.cell(row=r + 2, column=3).value or "")
        if tbl_name and str(tbl_name).strip().lower() not in ("n/a",) and \
                not str(tbl_name).lower().startswith("n/a "):
            ok = tbl_name in target.tables
            out.append(Check(
                f"schema_ref:{name}/table_name", ok,
                f"documents Table '{tbl_name}'",
                fix=(None if ok else
                     f"no Table named '{tbl_name}' on '{name}' -- actual "
                     f"tables there: {list(target.tables.keys()) or 'none'}")))
        m = range_re.search(data_rows_text)
        if m and target.tables:
            doc_end_row = int(re.search(r"\d+", m.group(2)).group())
            first_name = list(target.tables.keys())[0]
            real_tbl = target.tables[first_name]  # see check_table_refs note
            _, _, _, real_end_row = range_boundaries(real_tbl.ref)
            ok = doc_end_row == real_end_row
            out.append(Check(
                f"schema_ref:{name}/row_range", ok,
                f"documents ending row {doc_end_row}, live Table ends at row {real_end_row}",
                fix=None if ok else f"Schema Reference's row range for '{name}' is stale"))
        r += 4
    wb.close()
    return out


def check_scoring_verification(path: str, repo_dir: Optional[str] = None) -> List[Check]:
    """Every ticker with a real Tier 1 Wtd Avg should have that score
    backed by a genuine landry_scores.json entry -- otherwise there's no
    way to tell whether it came from `landry draft`'s reproducible,
    evidence-cited calculation or from something else entirely, and no
    way to know it needs a recheck before being trusted for a live
    decision.

    Found 2026-09-13: LIN/ET/DPZ/PG/YUM/V/NFLX/GE/CMG/ETSY all predate
    the audit trail (landry_scores.json wasn't tracked in git until
    2026-08-18) -- their original Tier 1 scores came from a pre-tooling
    conversational chat analysis (Darryl_List_Analysis_v1.docx, careful
    work but not reproducible or evidence-linked the way `landry draft`
    is), not from this codebase's own scoring pipeline. Four of six
    rechecked so far (LIN, ET, DPZ, YUM) failed the ~4.0 prioritization
    bar on fresh data; V -- a live $18K+ held position -- did too
    (STRONG BUY -> BUY). This check makes "no real audit trail" and "has
    one, but it looks copied rather than reviewed" both visible without
    needing a human to notice and go digging, the way this one was
    found.

    Two independent signals:
    (1) none of a ticker's 5 Tier 1 indicators have ANY approved entry
        at all -- the score is completely unverified by this system.
        Expected to have a large baseline right now (most of the
        workbook predates this discipline) -- reported as one
        consolidated finding, not one failure per ticker, so it stays
        readable and a *new* addition to the list is still noticeable.
    (2) 3+ indicators approved with source="manual" for the same ticker
        share the identical approved_at timestamp to the second -- the
        fingerprint of a mechanical bulk copy (landry.export.
        import_scores backfilling whatever was already in the
        workbook), not independent per-indicator review. Restricted to
        source="manual" specifically because a scripted (but genuine)
        `landry draft` + one-`approve`-call-per-indicator session also
        lands multiple quant_draft approvals in the same wall-clock
        second -- that's real review, just fast, and must not trip this.
        This is exactly what happened to V on 2026-09-09: all 9 of its
        non-Tier-1-quant indicators were "approved" (source=manual) in
        the same second, nine days after it was bought, without anyone
        actually re-deriving them.

        Refined 2026-10-02: a same-second manual group only counts when
        it also looks mechanical -- an empty rationale, or the same
        rationale text on two or more of its entries (import_scores
        writes "imported from <workbook> (scored ...)" on every
        indicator; V's backfill wrote one identical "BACKFILLED ..." note
        on all nine). Found when VEEV's three researched judgments -- each
        with its own evidence-citing rationale, approved by Alan in one
        message -- were written by one scripted call, landed in the same
        second, and tripped the timestamp-only version of this check on a
        genuine review."""
    from collections import defaultdict
    from landry.approvals import ScoreStore
    from landry.xlsx_io import _COL_TIER1_AVG

    repo_dir = repo_dir or _REPO
    scores_path = os.path.join(repo_dir, "landry_scores.json")
    if not os.path.exists(scores_path):
        return [Check("scoring_verification", True,
                      "landry_scores.json not present, skipped")]
    store = ScoreStore(scores_path)
    raw_tickers = store._data.get("tickers", {})  # need approved_at + source
                                                    # together; approved_scores()
                                                    # (the public view) drops both

    TIER1_INDICATORS = ("fcf_yield_trend", "revenue_growth_consistency",
                        "competitive_moat", "revenue_visibility", "fcf_margin_trend")

    bulk_by_ticker: Dict[str, tuple] = {}
    for ticker, rec in raw_tickers.items():
        by_time = defaultdict(list)
        for ind, entry in rec.get("approved", {}).items():
            if entry.get("source") == "manual" and entry.get("approved_at"):
                by_time[entry["approved_at"]].append(
                    (ind, (entry.get("rationale") or "").strip()))
        for ts, items in by_time.items():
            whys = [why for _, why in items]
            mechanical = not all(whys) or len(set(whys)) < len(whys)
            if len(items) >= 3 and mechanical:
                bulk_by_ticker[ticker] = (ts, [ind for ind, _ in items])

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["Scoring"]
    out = []
    unverified = []
    for row in range(3, ws.max_row + 1):
        ticker = ws.cell(row=row, column=1).value
        if not ticker:
            continue
        tier1_val = ws.cell(row=row, column=_COL_TIER1_AVG).value
        if not isinstance(tier1_val, (int, float)):
            continue
        ticker = str(ticker).strip().upper()
        approved = store.approved_scores(ticker)
        if not any(i in approved for i in TIER1_INDICATORS):
            unverified.append(ticker)
        if ticker in bulk_by_ticker:
            ts, inds = bulk_by_ticker[ticker]
            out.append(Check(
                f"scoring_verification:{ticker}/bulk_import", False,
                f"row {row}: {len(inds)} indicators approved manually at the identical "
                f"timestamp {ts} ({', '.join(sorted(inds))}) -- looks like a mechanical "
                f"import, not independent per-indicator review",
                fix=f"rerun `landry draft {ticker}` and approve one at a time for at least "
                    f"the Tier 1 quant indicators before trusting this score"))
    wb.close()
    if unverified:
        out.append(Check(
            "scoring_verification:no_audit_trail", False,
            f"{len(unverified)} ticker(s) with a real Tier 1 Wtd Avg have no approved "
            f"Tier 1 entry in landry_scores.json at all -- unverified by this system: "
            f"{', '.join(unverified)}",
            fix="run `landry draft <TICKER>` and approve before trusting any of these "
                "for a live decision -- expected to shrink over time as the queue works "
                "through it, not something to clear in one pass"))
    if not any(not c.ok for c in out):
        out.append(Check("scoring_verification", True,
                         "every scored ticker's Tier 1 has a real, non-bulk-imported audit trail"))
    return out


def check_price_history(path: str) -> List[Check]:
    """Price History is the one tab whose structure the whole Rule 38 check hangs on, and it can be
    silently wrecked: on ~2026-10-01 `landry export` rewrote its header from the first 16 tickers
    alphabetically, so SPMO and V each appeared twice, VRT/VRTX/HELO lost their columns, JEPQ/VFLO
    and the second SPMO/V sat five weeks out of alignment (their correlations with everything read
    ~0), eight held positions had no column at all, and the 19 charts -- titled by ticker, reading
    Returns (Calc) columns by position -- plotted the wrong instruments. Nothing flagged any of it
    for days. This catches every one of those: a duplicate or missing header ticker, a holding with
    no column, a blank in the newest row, a non-Friday date, and any chart whose title is not the
    header of the column it reads."""
    import html
    import zipfile
    from openpyxl.utils import column_index_from_string
    from landry import prices

    wb = openpyxl.load_workbook(path, data_only=True)
    if prices.PH_SHEET not in wb.sheetnames:
        wb.close()
        return [Check("price_history", True, "no Price History tab, skipped")]
    head = {c: wb[prices.PH_SHEET].cell(row=prices.HEADER_ROW, column=c).value
            for c in range(2, wb[prices.PH_SHEET].max_column + 1)}
    wb.close()
    out: List[Check] = []
    st = prices.status(path)
    for problem in st["problems"]:
        out.append(Check("price_history:header", False, problem,
                         fix="`python -m landry prices status`; add a missing holding with `python -m landry prices add TICKER`, "
                             "and never let `landry export` near Price History (its block is retired)"))
    try:
        with zipfile.ZipFile(path) as z:
            charts = sorted(n for n in z.namelist() if re.match(r"xl/charts/chart\d+\.xml$", n))
            for name in charts:
                xml = z.read(name).decode("utf8", "replace")
                title = re.findall(r"<a:t>([^<]*)</a:t>", xml)
                refs = [html.unescape(r) for r in re.findall(r"<(?:c:)?f>([^<]*)</(?:c:)?f>", xml)]
                value_cols = [m.group(1) for r in refs if "Returns (Calc)" in r and r.endswith("$500")
                              for m in [re.search(r"\$([A-Z]+)\$3:", r)] if m and "$A$3" not in r]
                if not title or not value_cols:
                    continue
                col = column_index_from_string(value_cols[0])
                shown = str(head.get(col) or "").strip()
                if html.unescape(title[0]).strip() != shown:
                    out.append(Check("price_history:chart", False,
                                     f"{os.path.basename(name)} is titled '{html.unescape(title[0]).strip()}' but plots "
                                     f"Returns (Calc) column {value_cols[0]}, which is {shown or 'blank'}",
                                     fix="the chart's series references (and title) must follow the Price History header; "
                                         "re-point or retitle it"))
    except zipfile.BadZipFile:
        pass
    if not out:
        out.append(Check("price_history", True,
                         f"{len(st['tickers'])} tickers, every holding has a column, last row complete, "
                         f"{st['weeks_behind']} completed week(s) behind; charts match the header"))
    return out


def check_performance_tracking_ties(path: str) -> List[Check]:
    """The Performance Tracking lot ledger ties to Current Positions (added 2026-10-05 with the summary block).

    Seven checks, each a way the summary could quietly stop meaning what its label says (the "sources" and "cycles"
    checks are below): (1) for every ticker the
    held lots' Shares add up to its quantity on Current Positions -- a System lot larger than the position, a
    position closed on one tab but not the other; (2) no held baseline stock or ETF lot has drifted from its typed
    8/5/26 share count by more than $1,000 or 1% -- a baseline lot's Shares follow Current Positions by themselves,
    so a purchase recorded only there would otherwise be measured from the 8/5 close without anybody noticing;
    (3) the held lots' Current Value adds up to the portfolio total (``total_portfolio_value`` of Current
    Positions) within 0.5% -- prices on both tabs come from Market Data, so any bigger gap is a lot priced wrongly
    or not at all; (4) the summary's TOTAL line shows that same figure; (5) the by-source lines (System lots only,
    Legacy stocks, Dry-powder ETFs, Cash -- added 2026-10-05) each match the ledger rows that belong to them, every
    lot belongs to exactly one, and the four add up to the Cumulative TOTAL -- a lot with no Basis or no Type would
    be in that total but in none of the four; (6) the benchmark cells (SPY now, SPY at
    8/5/26 and 12/31/25, the as-of date, the inception date) exist and hold numbers; (7) no formula on the tab
    depends on itself through a range -- Excel reports that as a circular reference, LibreOffice does not (the
    whole-column ranges in Shares did exactly that on 2026-10-05, found before the file reached Excel). Values are read from the cached results, so the
    workbook must have been recalculated. A tab still in the old A:Q layout is skipped."""
    from collections import defaultdict
    from landry import perf_tab, xlsx_io
    name = "performance_tracking_ties"
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        if not {"Current Positions", perf_tab.SHEET} <= set(wb.sheetnames):
            return [Check(name, True, "Current Positions / Performance Tracking not both present, skipped")]
        ws = wb[perf_tab.SHEET]
        if perf_tab.TABLE not in ws.tables or len(ws.tables[perf_tab.TABLE].tableColumns) < perf_tab.LAST_COL:
            return [Check(name, True, "the tab has no lot-ledger columns (R:AC), skipped")]
        lots = perf_tab.read_lots(wb)
        _first, last = perf_tab.table_bounds(ws)
        top = perf_tab.block_top(last)
        total_row = next((r for r in range(top, top + perf_tab.BLOCK_ROWS)
                          if ws.cell(row=r, column=1).value == "TOTAL"), None)
        summary_total = ws.cell(row=total_row, column=5).value if total_row else None
        block = {ws.cell(row=r, column=1).value: (ws.cell(row=r, column=4).value, ws.cell(row=r, column=5).value)
                 for r in range(top, top + perf_tab.BLOCK_ROWS) if isinstance(ws.cell(row=r, column=1).value, str)}
        bench = {k: (perf_tab._name_ref(wb, k) and ws[perf_tab._name_ref(wb, k)[1]].value) for k in perf_tab.NAMES}
        positions = xlsx_io.read_positions(path)
    except Exception as e:
        return [Check(name, False, f"could not read the lot ledger / Current Positions: {e}",
                      fix="run `python -m landry audit` after fixing the workbook read error")]
    finally:
        wb.close()
    out: List[Check] = []

    held_shares, held_value, problems = defaultdict(float), 0.0, []
    for lot in lots:
        if lot["status"] != "Held":
            continue
        sh, val = lot["shares"], lot["cur_value"]
        if not isinstance(sh, (int, float)) or sh < 0:
            problems.append(f"row {lot['row']} ({lot['ticker']}) has no usable Shares ({sh!r}) -- recalc, or a System "
                            f"lot larger than the position")
            continue
        held_shares[lot["ticker"]] += sh
        if isinstance(val, (int, float)):
            held_value += val
        else:
            problems.append(f"row {lot['row']} ({lot['ticker']}) has no Current Value -- priced by Market Data?")
    cp = defaultdict(float)
    for p in positions:
        cp[p.ticker] += p.quantity
    for t in sorted(set(held_shares) | set(cp)):
        if t not in held_shares:
            continue                                     # a holding with no lot at all: performance_tracking_coverage
        have, want = held_shares[t], cp.get(t, 0.0)
        if abs(have - want) > max(1e-6, 1e-9 * abs(want)):
            problems.append(f"{t}: the held lots add to {have:,.4f} shares, Current Positions holds {want:,.4f}")
    out.append(Check(f"{name}:shares", not problems,
                     "; ".join(problems[:6]) + (f" ... and {len(problems) - 6} more" if len(problems) > 6 else "")
                     if problems else f"held lots add up to Current Positions' quantity for all {len(held_shares)} tickers",
                     fix=None if not problems else "a purchase or sale reached Current Positions but not the lot ledger: "
                         "landry.perf_tab.add_lot / close_lot, then recalc (a baseline lot follows Current Positions by itself)"))

    # a held baseline stock / ETF lot follows Current Positions, so a purchase recorded only there would be measured
    # from the 8/5/26 close without anybody noticing; its typed 8/5 snapshot (Lot Shares) is what exposes it
    drift = []
    for lot in lots:
        if (lot["basis"] == "Baseline" and lot["status"] == "Held" and lot["type"] != "Cash"
                and isinstance(lot["lot_shares"], (int, float)) and isinstance(lot["shares"], (int, float))):
            gap_sh = lot["shares"] - lot["lot_shares"]
            price = lot["current"] if isinstance(lot["current"], (int, float)) else (lot["entry_price"] or 0)
            worth = abs(gap_sh) * price
            if worth > max(perf_tab.BASELINE_DRIFT_USD, perf_tab.BASELINE_DRIFT_PCT * lot["lot_shares"] * price):
                drift.append(f"{lot['ticker']}: Current Positions implies {lot['shares']:,.4f} sh, the 8/5/26 snapshot "
                             f"says {lot['lot_shares']:,.4f} ({gap_sh:+,.4f} sh, about ${worth:,.0f})")
    out.append(Check(f"{name}:baseline", not drift,
                     "; ".join(drift[:6]) if drift else "no held baseline lot has drifted from its 8/5/26 share count "
                     "by more than the tolerance",
                     fix=None if not drift else "a purchase or sale reached Current Positions with no lot: "
                         "landry.perf_tab.add_lot / close_lot; if it is only dividend reinvestment, "
                         "landry.perf_tab.rebase_baseline(wb) resets the snapshot"))

    total = xlsx_io.total_portfolio_value(positions)
    gap = abs(held_value - total) / total if total else 0.0
    out.append(Check(f"{name}:value", gap <= 0.005,
                     f"held lots' Current Value {held_value:,.2f} vs the portfolio total {total:,.2f} ({gap:.2%} apart)",
                     fix=None if gap <= 0.005 else "a lot is priced wrongly or not at all -- compare Market Data with "
                         "Current Positions' prices, then check which lot's Current Price is blank"))

    ok = isinstance(summary_total, (int, float)) and abs(summary_total - held_value) < 1.0
    out.append(Check(f"{name}:summary", ok,
                     f"the summary's TOTAL line shows {summary_total!r}, the held lots add to {held_value:,.2f}" if not ok
                     else f"the summary's TOTAL line ({summary_total:,.2f}) equals the held lots' Current Value",
                     fix=None if ok else "the summary block is generated: landry.perf_tab.rebuild_block(wb) rewrites it, "
                                          "then recalc"))

    # the by-source lines partition the ledger: each shows what its own lots add up to, every lot is in exactly one,
    # and together they are the Cumulative TOTAL (the "All sources" line is built as their sum, not as a second total)
    src_labels = {k: label for k, label, _how in perf_tab.SOURCE_LINES}
    cum_label = next((lab for lab in block if isinstance(lab, str) and lab.startswith("Cumulative TOTAL")), None)
    problems = []
    unclassified = [f"row {l['row']} ({l['ticker']})" for l in lots if perf_tab.source_of(l["basis"], l["type"]) is None]
    if unclassified:
        problems.append("no usable Basis or Type: " + ", ".join(unclassified[:6]))
    if not all(label in block for label in src_labels.values()) or cum_label is None:
        problems.append("the by-source lines are missing from the summary block")
    else:
        want = {k: [0.0, 0.0] for k in src_labels}
        for l in lots:
            k = perf_tab.source_of(l["basis"], l["type"])
            if k:
                want[k][0] += l["entry_value"] if isinstance(l["entry_value"], (int, float)) else 0.0
                want[k][1] += l["cur_value"] if isinstance(l["cur_value"], (int, float)) else 0.0
        want["all"] = [sum(want[k][0] for k in want if k != "all"), sum(want[k][1] for k in want if k != "all")]
        for k, label in src_labels.items():
            shown = block[label]
            if not all(isinstance(v, (int, float)) for v in shown) or any(abs(s - w) >= 1.0 for s, w in zip(shown, want[k])):
                problems.append(f"'{label}' shows {shown!r}, its lots add to ({want[k][0]:,.2f}, {want[k][1]:,.2f})")
        cum = block[cum_label]
        allsrc = block[src_labels["all"]]
        if all(isinstance(v, (int, float)) for v in cum + allsrc) and any(abs(a - c) >= 1.0 for a, c in zip(allsrc, cum)):
            problems.append(f"the four lines add to ({allsrc[0]:,.2f}, {allsrc[1]:,.2f}), the Cumulative TOTAL line shows "
                            f"({cum[0]:,.2f}, {cum[1]:,.2f})")
    out.append(Check(f"{name}:sources", not problems,
                     "; ".join(problems[:4]) if problems
                     else "every lot is in exactly one by-source line and the four lines add up to the Cumulative TOTAL",
                     fix=None if not problems else "give every ledger row a Basis (System / Baseline) and a Type "
                         "(Stock / ETF / Cash); the block is generated -- landry.perf_tab.rebuild_block(wb), then recalc"))

    try:
        cycles = perf_tab.find_cycles(openpyxl.load_workbook(path))
    except Exception as e:
        cycles = [f"could not look: {e}"]
    out.append(Check(f"{name}:cycles", not cycles,
                     "circular reference(s) on Performance Tracking (Excel reports them, LibreOffice does not): "
                     + "; ".join(c if len(c) < 160 else c[:157] + "..." for c in cycles[:2]) if cycles
                     else "no circular reference among the tab's formulas",
                     fix=None if not cycles else "a formula reads a range that contains a cell depending on it -- bound "
                         "its ranges to the Table's rows (landry.perf_tab.calc_formulas) instead of whole columns"))

    bad = [k for k, v in bench.items()
           if v in (None, "") or (k in ("spy_now", "spy_0805", "spy_1231") and not (isinstance(v, (int, float)) and v > 0))]
    out.append(Check(f"{name}:benchmark", not bad,
                     f"benchmark cells missing or not numbers: {', '.join(perf_tab.NAMES[k] for k in bad)}" if bad
                     else "the benchmark cells (SPY now, SPY at 8/5/26 and 12/31/25, as-of and inception dates) are in place",
                     fix=None if not bad else "the weekly run writes PT_SPY_Now and PT_AsOf; the others are typed constants"))
    return out


def check_monitor_last_score(path: str) -> List[Check]:
    """Every held, scored position's row on Monitor & Recheck Triggers mirrors its Scoring row: the same Date Scored,
    composite, Tier 1 average and decision, and a Price at Last Score. Alan, 2026-10-05: "we'll definitely want to
    keep Monitoring tab monthly-current for held positions". The Instructions (A52) say cols C-G are stamped every
    time a ticker is re-scored, but nothing in the code did it, so it was left to hand and the 9/13/26 re-score of 14
    held names (and the 9/30 reconfirmation of ADBE / PLD) was never stamped -- found 10/5 when the tab still showed
    CRWD at "70.4 BUY, 8/6/26" against a Scoring row of 63.4 AVOID. The same drift fails here whenever a re-score, or
    a rule that moves a Decision, reaches Scoring without the Monitor: ``python -m landry monitor stamp`` fixes it."""
    from landry import monitor_tab
    name = "monitor_last_score"
    wb = openpyxl.load_workbook(path, read_only=True)
    have = {monitor_tab.SHEET, "Scoring", "Current Positions"} <= set(wb.sheetnames)
    wb.close()
    if not have:
        return [Check(name, True, "Monitor / Scoring / Current Positions not all present, skipped")]
    try:
        problems = monitor_tab.mirror_problems(path)
        n = len(monitor_tab.held_scored(path))
    except Exception as e:
        return [Check(name, False, f"could not compare the Monitor tab with Scoring: {e}",
                      fix="recalculate the workbook (the Scoring composite and decision are formulas), then rerun")]
    if problems:
        shown = "; ".join(f"{t}: {', '.join(p)}" for t, p in problems[:6])
        more = f" ... and {len(problems) - 6} more" if len(problems) > 6 else ""
        return [Check(name, False, f"{len(problems)} held position(s) whose Monitor row does not mirror Scoring: {shown}{more}",
                      fix="`python -m landry monitor stamp` stamps Last Score (cols C-G) from the Scoring rows, with the "
                          "close on or before the date scored; then recalc")]
    return [Check(name, True, f"every held scored position's Monitor row mirrors its Scoring row ({n} tickers)")]


def check_monitor_signals(path: str, today: Optional[object] = None) -> List[Check]:
    """The Monitor tab's insider-activity and analyst-shift columns (K-N) are "refreshed periodically, not live" --
    SEC Form 4 over a 30-day lookback, yfinance's consensus. Alan, 2026-10-05: keep the tab monthly-current for held
    positions. The day of the last complete refresh sits in Q1 (defined name MON_SignalsAsOf); a month and a week
    without one fails here, so the weekly routine's audit says so every Saturday until
    ``python -m landry monitor refresh`` has run. (They had last been populated about 9/4: by 10/5 ADBE's flag was
    already out of date.)"""
    import datetime as dt
    from landry import monitor_tab
    name = "monitor_signals"
    wb = openpyxl.load_workbook(path, read_only=True)
    has = monitor_tab.SHEET in wb.sheetnames
    wb.close()
    if not has:
        return [Check(name, True, "no Monitor tab, skipped")]
    as_of = monitor_tab.signals_as_of(path)
    if as_of is None:
        return [Check(name, False, "the Monitor tab has no 'signals refreshed' date (Q1, defined name MON_SignalsAsOf)",
                      fix="`python -m landry monitor refresh` refreshes the insider / analyst columns for held positions "
                          "and dates it")]
    age = ((today or dt.date.today()) - as_of).days
    ok = age <= monitor_tab.MAX_SIGNAL_AGE_DAYS
    return [Check(name, ok,
                  f"insider / analyst signals last refreshed {as_of:%m/%d/%y}, {age} day(s) ago"
                  + ("" if ok else f" (the limit is {monitor_tab.MAX_SIGNAL_AGE_DAYS})"),
                  fix=None if ok else "`python -m landry monitor refresh` (SEC Form 4 + yfinance consensus, held positions, "
                                      "a few minutes), then recalc")]


def check_scoring_row_order(path: str) -> List[Check]:
    """Dashboard and Entry Checklist mirror the Scoring tab ROW FOR ROW by formula, and the Implied-Return Calculator keeps typed tickers
    on the row after theirs (Scoring row + 1). Sorting Scoring in Excel moves every ticker to a new row but leaves those tabs' typed inputs
    where they were: Alan's 10/8/26 review copy had Scoring sorted by Tier 1 average and 48 of 49 Entry Checklist rows then showed another
    ticker beside the Rule 9-14 inputs typed for VEEV, SLB, GE and the rest, with nothing in Excel or LibreOffice complaining. The
    typed Implied-Return tickers are the canary: each must still sit one row below its Scoring row. Never sort or re-order Scoring
    (Scoring A1 says so); filter it instead."""
    import re
    name = "scoring_row_order"
    wb = openpyxl.load_workbook(path, read_only=True)
    if not {"Scoring", "Implied-Return Calculator"} <= set(wb.sheetnames):
        wb.close()
        return [Check(name, True, "Scoring / Implied-Return Calculator not both present, skipped")]
    scoring = {}
    for i, r in enumerate(wb["Scoring"].iter_rows(min_row=1, max_row=120, max_col=1, values_only=True), 1):
        scoring[i] = r[0] if r else None
    typed = {}
    for i, r in enumerate(wb["Implied-Return Calculator"].iter_rows(min_row=1, max_row=121, max_col=1, values_only=True), 1):
        v = r[0] if r else None
        if i >= 4 and isinstance(v, str) and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", v.strip()):
            typed[i] = v.strip()
    wb.close()
    bad = [f"Implied-Return row {r} says {t} but Scoring row {r - 1} is {scoring.get(r - 1)!s}" for r, t in sorted(typed.items())
           if str(scoring.get(r - 1) or "").strip() != t]
    if bad:
        return [Check(name, False, f"the Scoring tab's row order no longer matches the rows other tabs key to it: {'; '.join(bad[:5])}"
                                   + (f" ... and {len(bad) - 5} more" if len(bad) > 5 else ""),
                      fix="undo the sort (reopen the last good copy or `git checkout` the workbook) -- never sort Scoring: Dashboard, Entry Checklist "
                          "and Implied-Return read it by row, so every typed input would sit beside the wrong ticker")]
    return [Check(name, True, f"Scoring's row order still matches the Implied-Return Calculator's typed tickers ({len(typed)} anchors)")]


def check_stop_ladder(path: str, today: Optional[object] = None) -> List[Check]:
    """The stop-review ladder (Journal row 109): a held scored stock 20% or more below its cost basis with weak relative strength or
    a price under its 200-day average needs a current re-underwrite on file (landry_stops.json) and no additions; 30% below cost with
    deterioration found trims a third; 40% below with 8 weeks under the average is an Exit Review. Alan, 2026-10-08, after ADBE sat
    21% below cost with Relative Strength 1 and nothing in the System said "look again". Runs from the workbook alone (weekly closes,
    Market Data prices, the recorded Relative Strength score), so it is the same check every Saturday; ``python -m landry stops --live``
    gives today's numbers. Fails whenever a holding on a rung has no current review, until `landry stops record` writes one."""
    from landry import stops
    name = "stop_review_ladder"
    wb = openpyxl.load_workbook(path, read_only=True)
    have = {"Current Positions", "Scoring", "Price History", "Market Data"} <= set(wb.sheetnames)
    wb.close()
    if not have:
        return [Check(name, True, "Current Positions / Scoring / Price History / Market Data not all present, skipped")]
    try:
        st = stops.ladder(path, today=today)
    except Exception as e:
        return [Check(name, False, f"could not run the ladder: {e}", fix="run `python -m landry stops` to see the error")]
    att = [s for s in st if s.needs_attention]
    on = [s for s in st if s.rung >= 1]
    if att:
        shown = "; ".join(f"{s.ticker} rung {s.rung} ({-s.loss * 100:.1f}% below cost): {s.action}" for s in att)
        return [Check(name, False, f"{len(att)} holding(s) need attention: {shown}",
                      fix="write the re-underwrite (a Journal entry via `landry journal add`, the Rule 5 review concluded as reaffirmed / "
                          "resized / referred), then `python -m landry stops record TICKER --conclusion ... --journal-row N --next DATE`")]
    detail = (f"{len(on)} of {len(st)} held scored stocks on a rung, each with a current re-underwrite on file ("
              + ", ".join(f"{s.ticker} rung {s.rung}" for s in on) + ")") if on else f"none of {len(st)} held scored stocks is on a rung"
    return [Check(name, True, detail)]


def run_all(path: str, repo_dir: Optional[str] = None) -> List[Check]:
    return [
        *check_table_refs(path),
        *check_row_height_ceiling(path),
        *check_merges_inside_tables(path),
        *check_reader_bounds(path),
        *check_cross_tab_references(path),
        *check_page_setup_vs_last_commit(path, repo_dir),
        *check_schema_reference(path),
        *check_scoring_verification(path, repo_dir),
        *check_price_history(path),
        *check_held_positions_tracked(path),
        *check_performance_tracking_coverage(path),
        *check_performance_tracking_ties(path),
        *check_monitor_last_score(path),
        *check_monitor_signals(path),
        *check_scoring_row_order(path),
        *check_stop_ladder(path),
    ]


def check_held_positions_tracked(path: str) -> List[Check]:
    """Part 6 / Rules 32-36: every held, scored position whose Decision is Watch List (composite 50-64) must sit
    in the Watch List Tracker as Probationary Hold, and one whose Decision is Avoid or Pass as Exit Review. The
    Decision column applies Rule 3's automatic Avoid (since 2026-10-04), so a holding that trips it shows up here too.
    Found 2026-10-04: AVGO, VRTX and CRWD tripped Rule 3 on the 9/13 re-score, the Rule 3 column said AVOID, and
    for three weeks nothing surfaced it -- the Decision column read Buy / Buy / Watch List and no tab or check
    compared a holding's classification with the tracker. ETFs and other unscored holdings have no Decision and
    are skipped."""
    from landry import xlsx_io
    name = "held_positions_tracked"
    try:
        held = {p.ticker for p in xlsx_io.read_positions(path) if p.asset_class == "Equity"}
        decision = {r.ticker: (r.decision or "") for r in xlsx_io.read_scoring_tab(path)}
    except Exception as e:
        return [Check(name, False, f"could not read Current Positions / Scoring: {e}",
                      fix="run `python -m landry audit` after fixing the workbook read error")]
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    tracked: Dict[str, str] = {}
    if "Watch List Tracker" in wb.sheetnames:
        for row in wb["Watch List Tracker"].iter_rows(min_row=3, values_only=True):
            if row and isinstance(row[0], str) and row[0].strip():
                tracked[row[0].strip()] = str(row[2] or "").strip() if len(row) > 2 else ""
    wb.close()
    expected = {"WATCH LIST": "Probationary Hold", "AVOID": "Exit Review", "PASS": "Exit Review"}
    problems = []
    for t in sorted(held):
        want = expected.get(decision.get(t, ""))
        if want and tracked.get(t) != want:
            problems.append(f"{t} is {decision[t]} and needs a Watch List Tracker row as {want} "
                            f"(found {tracked.get(t) or 'none'})")
    if problems:
        return [Check(name, False, "; ".join(problems),
                      fix="add or correct the row in the Watch List Tracker (Rules 32-36): Probationary Hold for a "
                          "Watch List composite, Exit Review for Avoid -- which includes any Rule 3 trip; see Journal row 89")]
    n = sum(1 for t in held if expected.get(decision.get(t, "")))
    return [Check(name, True, f"every held scored position classified Watch List / Avoid / Pass is in the Watch List "
                              f"Tracker with the matching status ({n} of {len(held)} held equities)")]


def check_performance_tracking_coverage(path: str) -> List[Check]:
    """Every current holding has a row in Performance Tracking. Alan, 2026-10-05: "update the Performance Tracking
    tab to include all positions" -- legacy stocks, dry-powder ETFs and the cash / money-market funds as well as the
    System's own entries. The tab is hand-kept (Instructions: populated the same moment a confirmed trade updates
    Current Positions), and a new position bought on a DCA date is exactly how it would fall out of date without
    anyone noticing, so this fails until the row exists. Cash funds count; a ticker the tab lists that nothing holds
    any more is fine (an exited position keeps its row with Status Exited). Positions are read with
    ``read_positions``, which skips zero-quantity rows (sold names kept on Current Positions for the record)."""
    from landry import xlsx_io
    name = "performance_tracking_coverage"
    wb = openpyxl.load_workbook(path, read_only=True)
    both = {"Current Positions", "Performance Tracking"} <= set(wb.sheetnames)
    wb.close()
    if not both:
        return [Check(name, True, "Current Positions / Performance Tracking not both present, skipped")]
    try:
        held = sorted({p.ticker for p in xlsx_io.read_positions(path)})
        tracked = {r["ticker"] for r in xlsx_io.read_performance_tracking(path)}
    except Exception as e:
        return [Check(name, False, f"could not read Current Positions / Performance Tracking: {e}",
                      fix="run `python -m landry audit` after fixing the workbook read error")]
    missing = [t for t in held if t not in tracked]
    if missing:
        return [Check(name, False,
                      f"{len(missing)} current holding(s) have no Performance Tracking row: {', '.join(missing)}",
                      fix="add the lot with landry.perf_tab.add_lot (a System purchase: its real entry date, price, "
                          "shares and SPY at entry; anything else: a Baseline lot), then run the recalc -- it uses the "
                          "next spare row, grows the Table and moves the summary block when there is none, and shades "
                          "the Ticker cell for an ETF or cash fund (the tab's A1 note says how)")]
    return [Check(name, True, f"every current holding has a Performance Tracking row ({len(held)} tickers)")]


def report(checks: List[Check], workbook_name: str = "") -> str:
    lines = [f"Landry workbook audit — {workbook_name}"] if workbook_name else \
            ["Landry workbook audit"]
    failed = [c for c in checks if not c.ok]
    passed = [c for c in checks if c.ok]
    for c in failed:
        lines.append(f"  [ FAIL ] {c.name}: {c.detail}")
        if c.fix:
            lines.append(f"           fix: {c.fix}")
    lines.append("")
    lines.append(f"{len(passed)} passed, {len(failed)} failed "
                 f"(out of {len(checks)} checks).")
    if failed:
        lines.append("Review the failures above before committing this workbook.")
    else:
        lines.append("No structural drift detected.")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    from landry.xlsx_io import latest_workbook
    wb_path = sys.argv[1] if len(sys.argv) > 1 else latest_workbook(_REPO)
    checks = run_all(wb_path)
    print(report(checks, os.path.basename(wb_path)))
    sys.exit(1 if any(not c.ok for c in checks) else 0)
