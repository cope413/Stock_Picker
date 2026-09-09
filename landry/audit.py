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
from typing import List, Optional

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
            if prev_ws.page_setup.paperSize != cur_ws.page_setup.paperSize:
                mismatches.append(f"paperSize {prev_ws.page_setup.paperSize!r}"
                                  f"->{cur_ws.page_setup.paperSize!r}")
            if prev_ws.sheet_view.showGridLines != cur_ws.sheet_view.showGridLines:
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


def run_all(path: str, repo_dir: Optional[str] = None) -> List[Check]:
    return [
        *check_table_refs(path),
        *check_reader_bounds(path),
        *check_cross_tab_references(path),
        *check_page_setup_vs_last_commit(path, repo_dir),
        *check_schema_reference(path),
    ]


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
