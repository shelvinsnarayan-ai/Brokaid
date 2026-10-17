"""R1.5 — synthetic AU trust-account statements (fictional) in CBA / NAB / Westpac / ANZ CSV and OFX.

python -m app.scripts.bank_synth /app/docs/bank_samples
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path


def lines(items: list[tuple[str, str, str]], *, start=date(2026, 10, 1), opening="10000.00") -> list[dict]:
    """items: (crn, amount, client name). Returns chronological txns with running balances."""
    bal, out, d = Decimal(opening), [], start
    rows = [(f"BPAY {crn} {name.upper()[:18]}", Decimal(a)) for crn, a, name in items[:-1]]
    if items:  # last item is paid by EFT with the name but no CRN → fuzzy suggestion only
        rows.append((f"TRANSFER FROM {items[-1][2].upper()[:24]} PREMIUM", Decimal(items[-1][1])))
    rows += [("DIRECT CREDIT UNKNOWN REMITTER 4471", Decimal("312.40")),
             ("TRANSFER TO FICTIONAL CARRIER A PREMIUMS OCT", Decimal("-1500.00")),
             ("ACCOUNT FEE", Decimal("-15.00"))]
    for desc, amt in rows:
        d += timedelta(days=1)
        bal += amt
        out.append({"date": d, "amount": amt, "description": desc, "balance": bal})
    return out


def _dmy(d):
    return d.strftime("%d/%m/%Y")


def cba(ts):  # headerless: Date, Amount, Description, Balance (newest first, like NetBank)
    return "\n".join(f'{_dmy(t["date"])},"{t["amount"]:+.2f}","{t["description"]}","{t["balance"]:+.2f}"' for t in reversed(ts)) + "\n"


def anz(ts):  # headerless: Date, Amount, Description
    return "\n".join(f'{_dmy(t["date"])},"{t["amount"]:.2f}","{t["description"]}"' for t in ts) + "\n"


def nab(ts):
    h = "Date,Amount,Account Number,,Transaction Type,Transaction Details,Balance,Category,Merchant Name\n"
    return h + "\n".join(f'{t["date"].strftime("%d %b %y")},{t["amount"]:.2f},083-001 12-345-6789,,'
                         f'{"CREDIT" if t["amount"] > 0 else "DEBIT"},{t["description"]},{t["balance"]:.2f},,' for t in ts) + "\n"


def westpac(ts):
    h = "Bank Account,Date,Narrative,Debit Amount,Credit Amount,Balance,Categories,Serial\n"
    return h + "\n".join(f'032001123456,{_dmy(t["date"])},{t["description"]},{-t["amount"] if t["amount"] < 0 else ""},'
                         f'{t["amount"] if t["amount"] > 0 else ""},{t["balance"]:.2f},OTHER,' for t in ts) + "\n"


def ofx(ts):
    body = "".join(f"<STMTTRN><TRNTYPE>{'CREDIT' if t['amount'] > 0 else 'DEBIT'}<DTPOSTED>{t['date']:%Y%m%d}120000"
                   f"<TRNAMT>{t['amount']:.2f}<FITID>{t['date']:%Y%m%d}{i:04d}<NAME>{t['description'][:32]}</STMTTRN>\n"
                   for i, t in enumerate(ts))
    close = ts[-1]["balance"] if ts else Decimal("0")
    return ("OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\n\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>AUD<BANKTRANLIST>\n"
            f"{body}</BANKTRANLIST><LEDGERBAL><BALAMT>{close:.2f}<DTASOF>{ts[-1]['date']:%Y%m%d}</LEDGERBAL></STMTRS>"
            "</STMTTRNRS></BANKMSGSRSV1></OFX>\n")


WRITERS = {"cba": (cba, "csv"), "anz": (anz, "csv"), "nab": (nab, "csv"), "westpac": (westpac, "csv"), "ofx": (ofx, "ofx")}

if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/app/docs/bank_samples")
    out.mkdir(parents=True, exist_ok=True)
    demo = [("1234567897", "85.00", "Kai Pacific Logistics"), ("2345678907", "412.50", "Bayside Bakery")]
    ts = lines(demo)
    for k, (fn, ext) in WRITERS.items():
        (out / f"trust_{k}.{ext}").write_text(fn(ts))
    print("written", out)
