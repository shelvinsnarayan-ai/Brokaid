"""R1.4 — realistic synthetic iAdviser-style export (fictional data) with deliberate errors.

python -m app.scripts.iadviser_synth /app/docs/import_samples   → writes .xlsx (multi-sheet) + one CSV per sheet
"""
from __future__ import annotations

import csv
import io
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HEAD = {
    "Clients": ["Client Code", "Client Name", "ABN", "Email", "Phone", "Street Address", "State", "Postcode", "Industry",
                "Account Manager"],
    "Insurers": ["Insurer Code", "Insurer Name", "ABN"],
    "Policies": ["Policy No", "Client Code", "Insurer/Underwriter", "Class of Business", "Inception Date", "Expiry Date",
                 "Base Premium", "Total Premium", "Sum Insured", "Excess", "Status"],
    "Assets": ["Policy No", "Rego", "VIN/Chassis", "Make", "Model", "Year", "Agreed Value", "Use", "Garaged Address"],
    "Contacts": ["Client Code", "Contact Name", "Email", "Mobile", "Position"],
}
CLASSES = ["Motor Vehicle - Commercial", "Public Liability", "Business Pack", "Professional Indemnity", "Home & Contents"]
MAKES = [("Toyota", "Hilux"), ("Ford", "Ranger"), ("Isuzu", "D-Max"), ("Nissan", "Navara"), ("Mitsubishi", "Triton"),
         ("Toyota", "HiAce"), ("Hyundai", "iLoad")]
WORDS = ["Harbour", "Coastal", "Granite", "Wattle", "Southern", "Bluegum", "Riverside", "Summit", "Ironbark", "Saltbush"]
TRADES = ["Plumbing", "Electrical", "Bakery", "Freight", "Landscaping", "Joinery", "Cafe", "Marine", "Fitness", "Printing"]
STATES = [("NSW", "2000"), ("VIC", "3000"), ("QLD", "4000"), ("WA", "6000"), ("SA", "5000")]
VIN_CH = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"


def _abn(rnd) -> str:
    w = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
    while True:
        d = [rnd.randint(1, 9)] + [rnd.randint(0, 9) for _ in range(10)]
        if sum((x - (1 if i == 0 else 0)) * w[i] for i, x in enumerate(d)) % 89 == 0:
            return "".join(map(str, d))


def build(seed: int = 7, n_clients: int = 220, n_policies: int = 430, staff: tuple[str, ...] = ("Tess Account",)) -> dict:
    rnd = random.Random(seed)
    clients, policies, assets, contacts = [], [], [], []
    insurers = [[f"FC{c}", f"Fictional Carrier {c}", _abn(rnd)] for c in "ABCDE"]
    for i in range(1, n_clients + 1):
        st, pc = rnd.choice(STATES)
        name = f"{rnd.choice(WORDS)} {rnd.choice(TRADES)} Pty Ltd"
        clients.append([f"C{i:05d}", f"{name} #{i}", _abn(rnd), f"office{i}@client{i}.example", f"02 9{rnd.randint(100, 999)} {rnd.randint(1000, 9999)}",
                        f"{rnd.randint(1, 200)} {rnd.choice(WORDS)} Street", st, pc, rnd.choice(TRADES), rnd.choice(staff)])
        contacts.append([f"C{i:05d}", f"Contact {i}", f"contact{i}@client{i}.example", f"04{rnd.randint(10000000, 99999999)}", "Director"])
    for j in range(1, n_policies + 1):
        cls = CLASSES[0] if j % 2 else rnd.choice(CLASSES)
        start = date(2026, 1, 1) + timedelta(days=rnd.randint(0, 330))
        end = start.replace(year=start.year + 1)
        fmt = (lambda d: d.strftime("%d/%m/%Y")) if j % 3 else (lambda d: d.isoformat())
        base = round(rnd.uniform(600, 9000), 2)
        policies.append([f"P{j:06d}", f"C{(j % n_clients) + 1:05d}", f"FC{rnd.choice('ABCDE')}", cls, fmt(start), fmt(end),
                         f"${base:,.2f}", f"${base * 1.1:,.2f}", rnd.choice([250000, 500000, 1000000, 2000000, 5000000]),
                         rnd.choice([500, 1000, 2500]), "Current"])
    motor = [p for p in policies if p[3] == CLASSES[0]]
    for k, p in enumerate(motor[:170]):
        mk, md = rnd.choice(MAKES)
        assets.append([p[0], f"{rnd.choice('ABCDEFGHJK')}{rnd.choice('ABCDEFGHJK')}{rnd.randint(10, 99)}{rnd.choice('XYZ')}{k % 10}",
                       "".join(rnd.choice(VIN_CH) for _ in range(17)), mk, md, rnd.randint(2014, 2025),
                       rnd.choice([28000, 42000, 55000, 68000]), "Business", clients[int(p[1][1:]) - 1][5]])
    # deliberate errors (kept stable so tests can count them)
    if n_clients < 10 or n_policies < 25 or len(assets) < 2:
        clients[3][2], clients[4][2], clients[5][1] = "51 824 753 557", "1234", ""
        return {"Clients": clients, "Insurers": insurers, "Policies": policies, "Assets": assets, "Contacts": contacts}
    clients[3][2] = "51 824 753 557"        # ABN checksum fails
    clients[4][2] = "1234"                  # ABN wrong length
    clients[5][1] = ""                      # missing client name
    clients[6][9] = "Unknown Person"        # unknown account manager → warn
    policies[10][4] = "31/31/2026"          # bad date
    policies[11][5] = "01/01/2020"          # expiry before inception
    policies[12][6] = "-$50.00"             # negative premium
    policies[13][1] = "C99999"              # orphan policy (no client)
    policies.append(list(policies[20]))     # duplicate policy number
    assets[0][1] = "TOO-LONG-PLATE"         # bad rego
    assets[1][2] = "SHORTVIN"               # bad VIN → warn
    return {"Clients": clients, "Insurers": insurers, "Policies": policies, "Assets": assets, "Contacts": contacts}


def xlsx_bytes(data: dict) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in data.items():
        ws = wb.create_sheet(name)
        ws.append(HEAD[name])
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def csv_bytes(name: str, rows: list) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEAD[name])
    w.writerows(rows)
    return buf.getvalue().encode()


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/app/docs/import_samples")
    out.mkdir(parents=True, exist_ok=True)
    d = build()
    (out / "iadviser_sample.xlsx").write_bytes(xlsx_bytes(d))
    for n, rows in d.items():
        (out / f"iadviser_{n.lower()}.csv").write_bytes(csv_bytes(n, rows))
    print("written", out, {k: len(v) for k, v in d.items()}, datetime.now().isoformat(timespec="seconds"))
