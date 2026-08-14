"""Offline tests for landry.doctor -- no network, no LibreOffice required."""

import sys

from landry.doctor import (
    Check,
    _soffice_fix,
    check_packages,
    check_python,
    check_soffice,
    report,
)


def test_check_python_reflects_running_interpreter():
    c = check_python()
    assert c.name == "python"
    assert str(sys.version_info.major) in c.detail
    # this suite requires 3.9+ to run at all, so the check must pass here
    assert c.ok


def test_check_soffice_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    c = check_soffice()
    assert not c.ok
    assert "not found" in c.detail
    assert c.fix and "landry.xlsx_recalc" in c.fix


def test_check_soffice_found(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/soffice")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type(
        "R", (), {"stdout": "LibreOffice 7.6.0.0\n"})())
    c = check_soffice()
    assert c.ok
    assert "7.6" in c.detail


def test_soffice_fix_is_platform_specific(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    assert "brew" in _soffice_fix()
    monkeypatch.setattr("platform.system", lambda: "Windows")
    assert "winget" in _soffice_fix() or "libreoffice.org" in _soffice_fix()
    monkeypatch.setattr("platform.system", lambda: "Linux")
    assert "apt" in _soffice_fix() or "pkg" in _soffice_fix()


def test_check_packages_all_real_packages_importable():
    # every REQUIRED_PACKAGES entry must actually be importable in a repo
    # that follows its own requirements.txt
    checks = check_packages()
    assert all(c.ok for c in checks), [c.name for c in checks if not c.ok]


def test_report_summarizes_pass_and_fail():
    ok = [Check("a", True, "fine")]
    text = report(ok)
    assert "All checks passed" in text
    bad = [Check("a", True, "fine"), Check("b", False, "broken", fix="do X")]
    text = report(bad)
    assert "1 check(s) failed" in text
    assert "do X" in text
