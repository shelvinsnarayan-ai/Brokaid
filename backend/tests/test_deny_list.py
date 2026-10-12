"""CI copy deny-list — ensures placeholder tokens don't ship to prod.

Scans all Python + TypeScript sources for banned tokens listed in
Build Brief v2.1 §8 R1 #7 ("no placeholders, 'later phase' or 'awaits
phase' text, 'BUILD unknown'") plus the `<FICTIONAL_PLACEHOLDER>` token
from R1 #1 tester follow-up.

Allow-listed paths carry historical references in docs / test fixtures
that are explicitly labelled as such; every other file is scanned.
"""
from __future__ import annotations

from pathlib import Path


BANNED_TOKENS = (
    "<FICTIONAL_PLACEHOLDER>",
    "later phase",
    "awaits phase",
    "BUILD unknown",
)

# Files that legitimately reference the tokens (docs about the
# deny-list itself, tests, memory artefacts).
ALLOWLIST = {
    "backend/tests/test_jurisdiction.py",
    "backend/tests/test_deny_list.py",
    "ci/deny_list_source.js",
    "ci/deny_list_dom.py",
    "backend/tests/__pycache__",
    "docs/SEEDS_ARE_FICTIONAL.md",
    "packages/kernel/jurisdiction/AU.json",
    "packages/kernel/jurisdiction/SG.json",
    "backend/scripts/phase5_msg.txt",
    # Memory artefacts (uploaded briefs, test reports)
    "memory",
}

SCAN_ROOT = Path(__file__).resolve().parents[2]
SCAN_EXTENSIONS = {".py", ".ts", ".tsx", ".jsx", ".js", ".html", ".css"}


def _is_allowlisted(rel: str) -> bool:
    for prefix in ALLOWLIST:
        if rel == prefix or rel.startswith(prefix + "/"):
            return True
    return False


def _iter_source_files():
    for path in SCAN_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in SCAN_EXTENSIONS:
            continue
        rel = str(path.relative_to(SCAN_ROOT))
        if any(part.startswith(".") for part in path.parts):
            continue  # skip .git, .venv etc
        if "node_modules" in path.parts or "__pycache__" in path.parts:
            continue
        if "dist" in path.parts or "build" in path.parts:
            continue
        if _is_allowlisted(rel):
            continue
        yield path, rel


def test_no_banned_tokens_in_shipping_source():
    offences: list[tuple[str, str, str]] = []
    for path, rel in _iter_source_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for token in BANNED_TOKENS:
            if token in text:
                offences.append((rel, token, text[
                    max(0, text.find(token) - 40):
                    text.find(token) + 80
                ]))
    assert not offences, (
        "banned token(s) found in shipping source:\n"
        + "\n".join(f"  {rel}  ← {tok}  … {ctx.strip()[:100]} …"
                    for rel, tok, ctx in offences)
    )
