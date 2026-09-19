"""Jurisdiction packs — AU (default) and SG (v0).

Every disclosure string in every pack is a `<FICTIONAL_PLACEHOLDER>`.
Real regulatory text will replace these before any client sees them
outside dev/demo.
"""
from __future__ import annotations

from dataclasses import dataclass, field


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
    rail_label="PayTo",
    locale="en-AU",
    regulator="ASIC",
    disclosure_strings={
        "privacy_notice": "<FICTIONAL_PLACEHOLDER> Your information is "
                          "handled under the Australian Privacy "
                          "Principles set out in the Privacy Act 1988.",
        "fsg": "<FICTIONAL_PLACEHOLDER> This Financial Services Guide "
               "explains the services and remuneration your broker "
               "receives, as required by the Corporations Act 2001.",
        "pds": "<FICTIONAL_PLACEHOLDER> Read the Product Disclosure "
               "Statement carefully before deciding whether the cover "
               "is right for you.",
        "duty_of_disclosure": "<FICTIONAL_PLACEHOLDER> You have a duty "
                              "to take reasonable care not to make a "
                              "misrepresentation to your insurer.",
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
        "privacy_notice": "<FICTIONAL_PLACEHOLDER> Your information is "
                          "handled under Singapore's Personal Data "
                          "Protection Act (PDPA).",
        "fsg": "<FICTIONAL_PLACEHOLDER> This document sets out the "
               "services and remuneration your broker receives, "
               "per the Financial Advisers Act.",
        "pds": "<FICTIONAL_PLACEHOLDER> Read the Product Summary and "
               "Bundled Product Disclosure carefully before deciding.",
        "duty_of_disclosure": "<FICTIONAL_PLACEHOLDER> You must disclose "
                              "material facts before a contract of "
                              "insurance is concluded.",
    },
)


PACKS: dict[str, JurisdictionPack] = {"AU": AU, "SG": SG}


def get(code: str) -> JurisdictionPack:
    if code not in PACKS:
        raise KeyError(f"unknown jurisdiction: {code}")
    return PACKS[code]


def all_packs() -> list[JurisdictionPack]:
    return list(PACKS.values())
