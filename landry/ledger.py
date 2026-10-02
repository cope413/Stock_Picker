"""Phase C cutover mechanics: the one way entries get written to a generated tab.

Until now every Journal entry and Drawdown Log row went into the xlsx through
an ad hoc openpyxl script (which, at least once, got the formatting wrong).
Once a tab is generated from ``landry.db``, an entry is written like this:

    guard    refuse if Excel has the workbook open, or if the database and the
             tab disagree about what's in it (someone hand-edited the tab, or a
             newer workbook was pulled) -- never write on top of a disagreement
    write    the row goes into the database inside a transaction
    render   the tab is regenerated from that same connection into a temporary
             copy of the workbook, and the mandatory recalc runs on the copy
    swap     only if all of that worked is the copy moved over the workbook and
             the transaction committed; any failure rolls the database back and
             leaves the workbook byte-for-byte as it was

The committed workbook stays the persisted source of truth for now: ``landry.db``
is gitignored and local, so a fresh checkout has none. ``pull`` builds (or
rebuilds) it from the workbook's own tabs, and a write does that automatically
when the file is missing. When Turso replaces the local file this inverts -- the
database becomes the truth and the workbook pure output.
"""

from __future__ import annotations

import datetime
import os
import shutil
import sqlite3
import subprocess
import tempfile
from typing import Callable, Dict, List, Optional, Sequence

import openpyxl

from landry import generate, models, xlsx_io


class LedgerError(RuntimeError):
    """A write was refused or failed; the workbook and database are unchanged."""


class DriftError(LedgerError):
    """The database and the workbook disagree about what a generated tab holds."""


class ExcelOpenError(LedgerError):
    """Excel has the workbook open; writing underneath it would lose changes."""


def _excel_has_open(workbook_path: str) -> bool:
    """Excel leaves a ``~$<name>`` lock file beside an open workbook, but stale
    ones outlive a crash (``~$CANDIDATES LIST.xlsx`` sat for days), so a lock
    file only counts while an Excel process is actually running."""
    lock = os.path.join(os.path.dirname(os.path.abspath(workbook_path)),
                        "~$" + os.path.basename(workbook_path))
    if not os.path.exists(lock):
        return False
    try:
        return subprocess.run(["pgrep", "-f", "Microsoft Excel"],
                              capture_output=True).returncode == 0
    except OSError:
        return True         # can't tell, and a lock file is present: assume open


# ----------------------------------------------------------- DB <-> workbook --

_PULLERS = {
    "journal": (xlsx_io.read_journal,
                lambda conn, r: models.journal_add(conn, r["date"], r["ticker"], r["notes"])),
    "drawdown": (xlsx_io.read_drawdown_log_inputs,
                 lambda conn, r: models.drawdown_add(conn, r["date"], r["portfolio_value"], r["notes"])),
}


def pull(workbook_path: str, db_path: str, tabs: Optional[Sequence[str]] = None) -> Dict[str, int]:
    """Replace the database's rows for the generated tabs with what the
    workbook holds -- the workbook wins. Creates the database if it doesn't
    exist. All or nothing. Returns ``{tab: rows}``."""
    names = openpyxl.load_workbook(workbook_path, read_only=True)
    try:
        selected = generate._select_tabs(names, tabs)
    finally:
        names.close()
    conn = models.init_db(db_path)
    counts: Dict[str, int] = {}
    try:
        for t in selected:
            read, insert = _PULLERS[t.key]
            rows = read(workbook_path)
            conn.execute(f"DELETE FROM {t.db_table}")
            for row in rows:
                insert(conn, row)
            counts[t.key] = len(rows)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    return counts


def value_drift(workbook_path: str, conn: sqlite3.Connection,
                tabs: Optional[Sequence[str]] = None) -> Dict[str, list]:
    """``{tab: [value differences]}`` for tabs where the workbook and the
    database disagree about content (for the Drawdown Log that includes its
    formulas). Formatting differences are not drift: regenerating resets them."""
    results = generate.verify_workbook(workbook_path, conn, tabs)
    return {key: [d for d in diffs if d[0] == "value"]
            for key, diffs in results.items() if any(d[0] == "value" for d in diffs)}


def _describe(drift: Dict[str, list]) -> str:
    lines = []
    for key, diffs in drift.items():
        first = "; ".join(f"{where}: {str(wb)[:40]!r} in the workbook vs {str(db)[:40]!r} from the database"
                          for _, where, wb, db in diffs[:3])
        lines.append(f"  {key}: {len(diffs)} cell(s) differ -- {first}")
    return "\n".join(lines)


def status(workbook_path: str, db_path: str, tabs: Optional[Sequence[str]] = None) -> Dict[str, dict]:
    """Per tab: database row count, content drift, and formatting differences
    that the next regeneration would reset."""
    with generate._connection(db_path, ["journal", "drawdown_log"]) as conn:
        results = generate.verify_workbook(workbook_path, conn, tabs)
        counts = {"journal": len(models.journal_rows(conn)),
                  "drawdown": len(models.drawdown_rows(conn))}
    return {key: dict(db_rows=counts[key],
                      value_diffs=[d for d in diffs if d[0] == "value"],
                      format_diffs=[d for d in diffs if d[0] != "value"])
            for key, diffs in results.items()}


# ---------------------------------------------------------------- the write --

def apply_write(workbook_path: str, db_path: str, tabs: Sequence[str],
                mutate: Callable[[sqlite3.Connection], Optional[dict]], *,
                recalc: bool = True, force: bool = False) -> dict:
    """Guard, write to the database, regenerate ``tabs`` into a temporary copy
    (with the recalc), then swap the copy in and commit. Any failure rolls the
    database back and leaves the workbook untouched. ``mutate`` receives the
    open connection and returns a dict describing what it wrote."""
    workbook_path = os.path.abspath(workbook_path)
    if not force and _excel_has_open(workbook_path):
        raise ExcelOpenError(
            f"Excel has {os.path.basename(workbook_path)} open -- close it first "
            f"(writing underneath it would lose changes), or pass --force")
    built = None
    if not os.path.exists(db_path):
        built = pull(workbook_path, db_path)
    conn = models.connect(db_path)
    try:
        models.check_schema(conn)
        drift = value_drift(workbook_path, conn, tabs)
        if drift:
            raise DriftError(
                "the database and the workbook disagree, so nothing was written:\n"
                + _describe(drift)
                + "\nIf the workbook is right (someone edited the tab, or you pulled a newer copy), run "
                  "`landry db pull`. If the database is right, run `landry db regenerate`. "
                  "(A formula difference in the Drawdown Log means one was edited by hand: "
                  "regenerate restores it.)")
        info = mutate(conn) or {}
        result = _render_and_swap(workbook_path, conn, tabs, recalc)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    return dict(info, built_db_from_workbook=built, recalc=result.get("recalc"),
                capped_rows=result.get("capped_rows", []))


def _render_and_swap(workbook_path: str, conn: sqlite3.Connection,
                     tabs: Optional[Sequence[str]], recalc: bool) -> dict:
    """Regenerate ``tabs`` from ``conn`` into a temporary copy beside the
    workbook, recalc the copy, and only then move it over the workbook (same
    directory, so the swap is atomic). The workbook is untouched on any failure."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(workbook_path),
                               prefix=".landry-gen-", suffix=".xlsx")
    os.close(fd)
    try:
        result = generate.generate_workbook(workbook_path, conn, out_path=tmp,
                                            recalc=recalc, tabs=tabs)
        rc = result.get("recalc")
        if rc is not None and (rc.get("status") != "success" or rc.get("total_errors")):
            raise LedgerError(f"the recalc reported {rc}; the workbook was left unchanged")
        shutil.copymode(workbook_path, tmp)
        os.replace(tmp, workbook_path)
        tmp = None
        return result
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def regenerate(workbook_path: str, db_path: str, tabs: Optional[Sequence[str]] = None, *,
               recalc: bool = True, force: bool = False) -> dict:
    """Rewrite the generated tabs from the database -- the way out of drift when
    the database is the one that's right (it resets hand edits, formulas
    included). Not subject to the drift guard, since resolving drift is its job."""
    workbook_path = os.path.abspath(workbook_path)
    if not force and _excel_has_open(workbook_path):
        raise ExcelOpenError(
            f"Excel has {os.path.basename(workbook_path)} open -- close it first, or pass --force")
    if not os.path.exists(db_path):
        raise LedgerError(f"{db_path} does not exist -- `landry db pull` builds it from the workbook")
    conn = models.connect(db_path)
    try:
        models.check_schema(conn)
        return _render_and_swap(workbook_path, conn, tabs, recalc)
    finally:
        conn.close()


# -------------------------------------------------------------- the entries --

def journal_add(workbook_path: str, db_path: str, notes: str, *, date=None, label=None,
                recalc: bool = True, force: bool = False) -> dict:
    """Append a Journal entry (dated today unless ``date`` is given)."""
    if not notes or not str(notes).strip():
        raise LedgerError("a Journal entry needs notes")

    def mutate(conn):
        entry_id = models.journal_add(conn, date or datetime.date.today(), label, notes)
        position = [r["id"] for r in models.journal_rows(conn)].index(entry_id)
        return dict(tab="Journal", id=entry_id, row=generate.FIRST_ROW + position)

    return apply_write(workbook_path, db_path, ("journal",), mutate, recalc=recalc, force=force)


def journal_edit(workbook_path: str, db_path: str, row: int, fields: dict, *,
                 recalc: bool = True, force: bool = False) -> dict:
    """Change the entry at sheet row ``row`` (the number the Journal and CLAUDE.md
    cite, e.g. "Journal row 62"). ``fields``: any of date, label, notes."""
    if "notes" in fields and not str(fields["notes"] or "").strip():
        raise LedgerError("a Journal entry needs notes")

    def mutate(conn):
        entries = models.journal_rows(conn)
        index = row - generate.FIRST_ROW
        if not 0 <= index < len(entries):
            raise LedgerError(
                f"the Journal has no entry at row {row} (entries occupy rows "
                f"{generate.FIRST_ROW}-{generate.FIRST_ROW + len(entries) - 1})")
        models.journal_update(conn, entries[index]["id"], fields)
        return dict(tab="Journal", id=entries[index]["id"], row=row)

    return apply_write(workbook_path, db_path, ("journal",), mutate, recalc=recalc, force=force)


def drawdown_add(workbook_path: str, db_path: str, date, value: float, notes: Optional[str] = None, *,
                 recalc: bool = True, force: bool = False) -> dict:
    """Log a portfolio value (one entry per date)."""
    def mutate(conn):
        try:
            entry_id = models.drawdown_add(conn, date, value, notes)
        except sqlite3.IntegrityError:
            raise LedgerError(f"the Drawdown Log already has an entry for {models.iso_date(date)} -- "
                              f"use `landry drawdown edit` to change it") from None
        dates = [r["date"] for r in models.drawdown_rows(conn)]
        return dict(tab="Portfolio Drawdown Log", id=entry_id,
                    row=generate.FIRST_ROW + dates.index(models.iso_date(date)))

    return apply_write(workbook_path, db_path, ("drawdown",), mutate, recalc=recalc, force=force)


def drawdown_edit(workbook_path: str, db_path: str, date, fields: dict, *,
                  recalc: bool = True, force: bool = False) -> dict:
    """Change the entry for ``date``. ``fields``: portfolio_value and/or notes."""
    def mutate(conn):
        try:
            models.drawdown_update(conn, date, fields)
        except KeyError:
            raise LedgerError(f"the Drawdown Log has no entry for {models.iso_date(date)}") from None
        dates = [r["date"] for r in models.drawdown_rows(conn)]
        return dict(tab="Portfolio Drawdown Log", row=generate.FIRST_ROW + dates.index(models.iso_date(date)))

    return apply_write(workbook_path, db_path, ("drawdown",), mutate, recalc=recalc, force=force)
