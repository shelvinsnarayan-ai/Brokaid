"""Jurisdiction-pack disclosure sanity tests.

R1 #1 tester follow-up: bans the `<FICTIONAL_PLACEHOLDER>` marker
from every disclosure string in every pack. Real regulatory names +
consumer-friendly copy is required.
"""
from app.kernel.jurisdiction import all_packs, get


BANNED_TOKENS = ("<FICTIONAL_PLACEHOLDER>", "later phase", "awaits phase",
                 "BUILD unknown")


def test_disclosure_strings_have_no_banned_tokens():
    for pack in all_packs():
        for name, text in pack.disclosure_strings.items():
            for token in BANNED_TOKENS:
                assert token not in text, (
                    f"{pack.code}.{name} still contains banned token "
                    f"{token!r}: {text}"
                )


def test_disclosure_strings_are_non_empty():
    for pack in all_packs():
        for name in ("privacy_notice", "fsg", "pds", "duty_of_disclosure"):
            text = pack.disclosure_strings.get(name, "")
            assert len(text) >= 40, (
                f"{pack.code}.{name} is missing or too short ({len(text)}): "
                f"{text!r}"
            )


def test_au_regulator_wording():
    pack = get("AU")
    assert "Privacy Act 1988" in pack.disclosure_strings["privacy_notice"]
    assert "Corporations Act 2001" in pack.disclosure_strings["fsg"]


def test_sg_regulator_wording():
    pack = get("SG")
    assert "PDPA" in pack.disclosure_strings["privacy_notice"]
    assert "Financial Advisers Act" in pack.disclosure_strings["fsg"]
