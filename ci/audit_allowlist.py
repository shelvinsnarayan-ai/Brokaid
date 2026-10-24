"""Reads ci/audit_allowlist.txt. `ids` prints the entries still in force (expired or malformed entries are reported and don't count).
`watch` checks PyPI for each watch=<pkg>:<dep>>=<major> and prints "upgrade available" when the latest <pkg> allows it."""
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

LINE = re.compile(r"^(\S+)\s+review=(\d{4}-\d{2}-\d{2})(?:\s+watch=(\S+))?\s+(\S.*)$")


def entries(today: date | None = None):
    today = today or date.today()
    for raw in (Path(__file__).parent / "audit_allowlist.txt").read_text().splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = LINE.match(raw.strip())
        if not m:
            print(f"allowlist: ignored (needs review=YYYY-MM-DD and a reason): {raw.strip()[:80]}", file=sys.stderr)
            continue
        vid, review, watch, _reason = m.groups()
        if date.fromisoformat(review) < today:
            print(f"allowlist: {vid} review date {review} has passed, so it no longer counts. Re-review it or fix it", file=sys.stderr)
            continue
        yield vid, review, watch


def _allows(spec: str, major: int) -> bool:
    """True if a requirement spec like '>=3.16.1,<4.0' admits version major.x."""
    for part in spec.split(","):
        m = re.match(r"\s*(<=|<|==|~=)\s*(\d+)(?:\.(\d+))?", part)
        if not m:
            continue
        op, hi, minor = m[1], int(m[2]), int(m[3] or 0)
        if op == "<" and (major > hi or (major == hi and minor == 0)):
            return False
        if op in ("<=", "==", "~=") and major > hi:
            return False
    return True


def watch() -> None:
    for w in sorted({w for _, _, w in entries() if w}):
        m = re.fullmatch(r"([\w.-]+):([\w.-]+)>=(\d+)", w)
        if not m:
            print(f"watch {w}: malformed")
            continue
        pkg, dep, major = m[1], m[2], int(m[3])
        try:
            info = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=10))["info"]
        except Exception as e:  # noqa: BLE001
            print(f"watch {w}: could not check PyPI ({type(e).__name__})")
            continue
        req = next((r for r in info.get("requires_dist") or [] if re.match(rf"{re.escape(dep)}\b", r, re.I) and "extra ==" not in r), None)
        spec = re.sub(rf"^{re.escape(dep)}\s*", "", req or "", flags=re.I).split(";")[0].strip("() ")
        if req is None or _allows(spec, major):
            print(f"watch {w}: UPGRADE AVAILABLE: {pkg} {info['version']} allows {dep} {spec or '(unpinned)'}")
        else:
            print(f"watch {w}: not yet ({pkg} {info['version']} requires {dep} {spec})")


if __name__ == "__main__":
    if sys.argv[1:] == ["watch"]:
        watch()
    else:
        print(" ".join(v for v, _, _ in entries()))
