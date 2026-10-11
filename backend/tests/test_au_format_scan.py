"""R0.2 — AU format guard: no locale-default date/number rendering in the UI."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
BAD = re.compile(r"\.toLocale(?:Date|Time)?String\(\s*\)|en-US")


def test_no_locale_default_formatting():
    hits = [f"{p.relative_to(SRC)}:{i}"
            for p in SRC.rglob("*.tsx")
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if BAD.search(line) and not line.lstrip().startswith(("*", "//"))]
    assert not hits, f"use lib/format.ts (dd/mm/yyyy, AUD $x,xxx.xx): {hits}"
