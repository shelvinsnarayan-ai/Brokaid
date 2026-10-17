"""SEC-002: every CSV export goes through here, so spreadsheet apps never execute a cell as a formula."""
from __future__ import annotations

import csv
import re

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_csv_cell(v):
    if v is None or isinstance(v, (bool, int, float)):
        return v
    s = str(v)
    if s.startswith(_FORMULA_START):
        s = "'" + s
    return _CONTROL.sub("", s)


class _SafeWriter:
    def __init__(self, w):
        self._w = w

    def writerow(self, row):
        return self._w.writerow([safe_csv_cell(c) for c in row])

    def writerows(self, rows):
        for r in rows:
            self.writerow(r)


def safe_writer(buf, **kw) -> _SafeWriter:
    return _SafeWriter(csv.writer(buf, **kw))
