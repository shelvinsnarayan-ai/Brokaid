"""Jurisdiction packs — AU (default) and SG (v0).

Disclosure copy is deliberately generic and consumer-friendly so it
can render on any tenant's white-label surface. Each brokerage
overrides these on their own tenant record before going live; the
strings shipped here are safe defaults that reference the correct
regulator in each pack.

R1 #1 tester follow-up: the previous placeholder marker has been
removed. A CI test bans the token from reappearing.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JurisdictionPack:
    code: str
    display_name: str
    currency_code: str
    currency_symbol: str
    rail_label: str
    disclosure_strings: dict[str, str]
    locale: str
    regulator: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "display_name": self.display_name,
            "currency_code": self.currency_code,
            "currency_symbol": self.currency_symbol,
            "rail_label": self.rail_label,
            "disclosure_strings": dict(self.disclosure_strings),
            "locale": self.locale,
            "regulator": self.regulator,
        }


AU = JurisdictionPack(
    code="AU",
    display_name="Australia",
    currency_code="AUD",
    currency_symbol="$",
    rail_label="BPAY",
    locale="en-AU",
    regulator="ASIC",
    disclosure_strings={
        "privacy_notice": (
            "Your information is handled under the Australian Privacy "
            "Principles set out in the Privacy Act 1988. Your broker "
            "collects it to arrange your cover, keep it current and "
            "act on your instructions."
        ),
        "fsg": (
            "This Financial Services Guide explains the services your "
            "broker provides and how they are remunerated, as required "
            "by the Corporations Act 2001."
        ),
        "pds": (
            "Read the Product Disclosure Statement carefully before "
            "deciding whether the cover suits your needs and objectives."
        ),
        "duty_of_disclosure": (
            "You have a duty to take reasonable care not to make a "
            "misrepresentation to your insurer. If you're unsure about "
            "anything, ask your broker before you sign."
        ),
    },
)


SG = JurisdictionPack(
    code="SG",
    display_name="Singapore",
    currency_code="SGD",
    currency_symbol="$",
    rail_label="PayNow",
    locale="en-SG",
    regulator="MAS",
    disclosure_strings={
        "privacy_notice": (
            "Your information is handled under Singapore's Personal "
            "Data Protection Act (PDPA). Your broker collects it to "
            "arrange your cover and keep it current on your behalf."
        ),
        "fsg": (
            "This document sets out the services your broker provides "
            "and how they are remunerated, in line with the Financial "
            "Advisers Act."
        ),
        "pds": (
            "Read the Product Summary and Bundled Product Disclosure "
            "carefully before you decide the cover suits you."
        ),
        "duty_of_disclosure": (
            "You must disclose material facts before a contract of "
            "insurance is concluded. If you are unsure whether "
            "something is material, tell your broker."
        ),
    },
)


PACKS: dict[str, JurisdictionPack] = {"AU": AU, "SG": SG}


def get(code: str) -> JurisdictionPack:
    if code not in PACKS:
        raise KeyError(f"unknown jurisdiction: {code}")
    return PACKS[code]


def all_packs() -> list[JurisdictionPack]:
    return list(PACKS.values())
