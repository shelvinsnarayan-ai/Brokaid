"""Dependency-audit allowlist: entries need a review date and stop counting after it. The watch check parses requirement pins."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ci"))
import audit_allowlist as al  # noqa: E402


def test_allowlist_expires_after_review_date():
    ids = {v for v, _, _ in al.entries(date(2026, 11, 21))}
    assert {"CVE-2026-112940", "CVE-2026-112913", "CVE-2026-112907"} <= ids
    assert not [v for v, review, _ in al.entries(date(2026, 11, 22)) if review == "2026-11-21"]


def test_watch_spec_parsing():
    assert not al._allows(">=3.16.1,<4.0", 4) and not al._allows("<4", 4)
    assert al._allows(">=3.16.1,<5", 4) and al._allows(">=3.16.1", 4) and al._allows("", 4)
