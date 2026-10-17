"""R1.6 — Australian public holidays 2026–2028 (national + each state), used by every business-day rule.

Dates are the gazetted/observed days published by each state government (substitute days included).
Part-day holidays (Christmas Eve / NYE evenings in SA/NT), the NSW bank-only August holiday, and
regional-only days (Royal Queensland Show, AFL Grand Final Friday — date set yearly) are not counted.
Outside 2026–2028 only weekends are skipped; refresh this table every year.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant

SYD = ZoneInfo("Australia/Sydney")
YEARS = (2026, 2027, 2028)
STATES = ("NSW", "VIC", "QLD", "SA", "WA", "TAS", "ACT", "NT")
DEFAULT_STATE = "NSW"


def _d(*items: str) -> dict[date, str]:
    out = {}
    for it in items:
        day, name = it.split(" ", 1)
        out[date.fromisoformat(day)] = name
    return out


NATIONAL = _d(
    "2026-01-01 New Year's Day", "2026-01-26 Australia Day", "2026-04-03 Good Friday", "2026-04-06 Easter Monday",
    "2026-12-25 Christmas Day", "2026-12-28 Boxing Day (observed)",
    "2027-01-01 New Year's Day", "2027-01-26 Australia Day", "2027-03-26 Good Friday", "2027-03-29 Easter Monday",
    "2027-12-27 Christmas Day (observed)", "2027-12-28 Boxing Day (observed)",
    "2028-01-03 New Year's Day (observed)", "2028-01-26 Australia Day", "2028-04-14 Good Friday", "2028-04-17 Easter Monday",
    "2028-04-25 Anzac Day", "2028-12-25 Christmas Day", "2028-12-26 Boxing Day",
)
_KINGS_JUNE = ("2026-06-08 King's Birthday", "2027-06-14 King's Birthday", "2028-06-12 King's Birthday")
_LABOUR_OCT = ("2026-10-05 Labour Day", "2027-10-04 Labour Day", "2028-10-02 Labour Day")
STATE = {
    "NSW": _d(*_KINGS_JUNE, *_LABOUR_OCT),
    "VIC": _d(*_KINGS_JUNE, "2026-03-09 Labour Day", "2027-03-08 Labour Day", "2028-03-13 Labour Day",
              "2026-11-03 Melbourne Cup", "2027-11-02 Melbourne Cup", "2028-11-07 Melbourne Cup"),
    "QLD": _d("2026-05-04 Labour Day", "2027-05-03 Labour Day", "2028-05-01 Labour Day",
              "2026-10-05 King's Birthday", "2027-10-04 King's Birthday", "2028-10-02 King's Birthday"),
    "SA": _d(*_KINGS_JUNE, *_LABOUR_OCT, "2026-03-09 Adelaide Cup", "2027-03-08 Adelaide Cup", "2028-03-13 Adelaide Cup"),
    "WA": _d("2026-03-02 Labour Day", "2027-03-01 Labour Day", "2028-03-06 Labour Day", "2026-04-27 Anzac Day (observed)",
             "2027-04-26 Anzac Day (observed)", "2026-06-01 WA Day", "2027-06-07 WA Day", "2028-06-05 WA Day",
             "2026-09-28 King's Birthday", "2027-09-27 King's Birthday", "2028-09-25 King's Birthday"),
    "TAS": _d(*_KINGS_JUNE, "2026-03-09 Eight Hours Day", "2027-03-08 Eight Hours Day", "2028-03-13 Eight Hours Day"),
    "ACT": _d(*_KINGS_JUNE, *_LABOUR_OCT, "2026-03-09 Canberra Day", "2027-03-08 Canberra Day", "2028-03-13 Canberra Day",
              "2026-04-27 Anzac Day (observed)", "2026-06-01 Reconciliation Day", "2027-05-31 Reconciliation Day",
              "2028-05-29 Reconciliation Day"),
    "NT": _d(*_KINGS_JUNE, "2026-05-04 May Day", "2027-05-03 May Day", "2028-05-01 May Day",
             "2026-08-03 Picnic Day", "2027-08-02 Picnic Day", "2028-08-07 Picnic Day"),
}


def holiday_name(d: date, state: str | None = None) -> str | None:
    return NATIONAL.get(d) or STATE.get(state or DEFAULT_STATE, {}).get(d)


def is_business_day(d: date, state: str | None = None) -> bool:
    return d.weekday() < 5 and not holiday_name(d, state)


def add_business_days(d: date, n: int, state: str | None = None) -> date:
    step = 1 if n >= 0 else -1
    left = abs(n)
    while left:
        d += timedelta(days=step)
        left -= is_business_day(d, state)
    return d


def on_or_before(d: date, state: str | None = None) -> date:
    while not is_business_day(d, state):
        d -= timedelta(days=1)
    return d


def business_days_between(a: date, b: date, state: str | None = None) -> int:
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += is_business_day(d, state)
    return n


def next_business_dt(dt: datetime, state: str | None = None) -> datetime:
    """+1 business day at the same Sydney wall-clock time."""
    d = dt.astimezone(SYD)
    nd = add_business_days(d.date(), 1, state)
    return datetime.combine(nd, d.timetz().replace(tzinfo=None), SYD).astimezone(timezone.utc)


def in_quiet_hours(dt: datetime) -> bool:
    h = dt.astimezone(SYD).hour
    return h >= 21 or h < 7


async def tenant_settings(store, tid: str) -> dict:
    async with use_tenant(tid):
        safe = wrap(store)
        row = await safe.tenant_settings.find_one({}, {"_id": 0}) or {}
        t = await safe.system("tenants").find_one({"tenant_id": tid}) or {}
    return {"holiday_state": row.get("holiday_state") or DEFAULT_STATE,
            "renewal_lead_days": int(row.get("renewal_lead_days") or 60),
            "quoting_mode": row.get("quoting_mode") or default_quoting_mode(t),
            "mocked_allowed_possible": mocked_possible(t)}


def mocked_possible(tenant: dict) -> bool:
    """R1.6 ruling: MOCKED quotes/payment details/e-sign only in the demo tenant and test tenants, never a real one."""
    return tenant.get("slug") == "harbourline" or bool(tenant.get("test"))


def default_quoting_mode(tenant: dict) -> str:
    return "mocked_allowed" if mocked_possible(tenant) else "manual_only"


async def quoting_mode(store, tid: str) -> str:
    return (await tenant_settings(store, tid))["quoting_mode"]


async def state_for(store, tid: str) -> str:
    return (await tenant_settings(store, tid))["holiday_state"]
