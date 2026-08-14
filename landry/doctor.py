"""Landry System v1.0 — environment readiness check.

Exists because of a concrete incident: an xlsx edit was validated with
every check available in one environment (zip integrity, XML well-
formedness, openpyxl round-trip, content diffing) and still triggered
an Excel repair, because none of those checks is a real OOXML schema
validator or spreadsheet engine. The tool that actually is one --
LibreOffice, via `landry.xlsx_recalc` -- wasn't installed there. This
module exists so that gap is visible up front, in any environment,
before an edit is attempted, rather than discovered after Excel
silently repairs a file the user depends on.

Run: python -m landry doctor
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional

MIN_PYTHON = (3, 9)

REQUIRED_PACKAGES = ("openpyxl", "pandas", "numpy", "yfinance", "pytest")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: Optional[str] = None  # remediation, platform-specific where it matters


def _soffice_fix() -> str:
    system = platform.system()
    if system == "Darwin":
        return "brew install --cask libreoffice"
    if system == "Windows":
        return ("winget install --id TheDocumentFoundation.LibreOffice "
                "  (or download from https://www.libreoffice.org/download/)")
    if system == "Linux":
        return ("apt install libreoffice  (Debian/Ubuntu)  |  "
                "pkg install libreoffice  (Termux -- not reliably packaged; "
                "if unavailable, edit xlsx files on a Mac/Windows/Desktop "
                "environment instead and let this environment read-only)")
    return "install LibreOffice for your platform: https://www.libreoffice.org/download/"


def check_python() -> Check:
    v = sys.version_info
    ok = (v.major, v.minor) >= MIN_PYTHON
    return Check("python", ok, f"{v.major}.{v.minor}.{v.micro}",
                fix=None if ok else f"need Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+")


def check_soffice() -> Check:
    path = shutil.which("soffice") or shutil.which("libreoffice")
    if path is None:
        return Check("soffice", False, "not found on PATH",
                    fix=(_soffice_fix() + "  -- required for "
                         "landry.xlsx_recalc, which is the only reliable way "
                         "to validate an xlsx edit before shipping it"))
    try:
        out = subprocess.run([path, "--version"], capture_output=True,
                             text=True, timeout=15)
        detail = out.stdout.strip() or path
    except Exception as e:
        detail = f"{path} (version check failed: {e})"
    return Check("soffice", True, detail)


def check_packages() -> List[Check]:
    out = []
    for pkg in REQUIRED_PACKAGES:
        try:
            __import__(pkg)
            out.append(Check(f"package:{pkg}", True, "importable"))
        except ImportError:
            out.append(Check(f"package:{pkg}", False, "not importable",
                             fix=f"pip install {pkg}"))
    return out


def run_all() -> List[Check]:
    return [check_python(), check_soffice(), *check_packages()]


def report(checks: Optional[List[Check]] = None) -> str:
    checks = checks if checks is not None else run_all()
    lines = [f"Landry environment check — {platform.system()} "
            f"{platform.release()}, Python running from {sys.executable}"]
    for c in checks:
        mark = "OK" if c.ok else "MISSING"
        lines.append(f"  [{mark:>7}] {c.name}: {c.detail}")
        if not c.ok and c.fix:
            lines.append(f"           fix: {c.fix}")
    failed = [c for c in checks if not c.ok]
    if failed:
        lines.append("")
        lines.append(f"{len(failed)} check(s) failed. Editing "
                     "LANDRY_SYSTEM_WORKBOOK_*.xlsx here without fixing the "
                     "soffice check is unsafe -- see landry/xlsx_recalc.py.")
    else:
        lines.append("")
        lines.append("All checks passed — this environment is ready.")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
    sys.exit(1 if any(not c.ok for c in run_all()) else 0)
