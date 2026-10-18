"""R2.0 — DRAFT compliance templates for a general insurance broker. Every brokerage-specific fact is a [PLACEHOLDER].

These are drafts for review by the brokerage's licensee/compliance adviser. They are not legal advice and are not
for client use until a Principal approves them and they are made live.
"""
from __future__ import annotations

WATERMARK = "DRAFT — for review by {brand}'s licensee/compliance adviser. Not for client use."

AFCA_CONTACT = ("Australian Financial Complaints Authority (AFCA)\n"
                "Website: www.afca.org.au\nPhone: 1800 931 678 (free call)\nEmail: info@afca.org.au\n"
                "Mail: GPO Box 3, Melbourne VIC 3001")

P = {  # placeholders, kept identical across documents so the owner can find-and-replace once
    "legal": "[PLACEHOLDER: legal name of the brokerage]",
    "abn": "[PLACEHOLDER: ABN]",
    "licence": "[PLACEHOLDER: AFSL number — or Authorised Representative number and the name + AFSL of the licensee]",
    "licensee": "[PLACEHOLDER: licensee name and AFSL number]",
    "address": "[PLACEHOLDER: street address]",
    "contact": "[PLACEHOLDER: phone, email and website]",
    "commission": "[PLACEHOLDER: commission rates or ranges, e.g. 0%–25% of base premium, by class of insurance]",
    "fees": "[PLACEHOLDER: broker fees charged and how they are calculated]",
    "staff_pay": "[PLACEHOLDER: how staff are paid, e.g. salary and any bonuses]",
    "associations": "[PLACEHOLDER: associations, relationships, premium funders, referral arrangements]",
    "pi": "[PLACEHOLDER: professional indemnity insurance and compensation arrangements (s912B)]",
    "complaints": "[PLACEHOLDER: complaints officer name, phone, email, postal address]",
    "privacy_officer": "[PLACEHOLDER: privacy officer contact details]",
    "overseas": "[PLACEHOLDER: countries where data may be disclosed or stored, or 'none']",
    "date": "[PLACEHOLDER: date of this document]",
    "version": "[PLACEHOLDER: version]",
}


def fsg() -> list[tuple[str, str]]:
    return [
        ("Financial Services Guide", f"Version {P['version']} · Date {P['date']}\n\n"
         "This Financial Services Guide (FSG) is an important document. It is designed to help you decide whether to use the "
         "financial services we offer. It explains who we are, the services we provide, how we and others are paid, any "
         "relationships that might influence our services, and what to do if you have a complaint. Please keep it for reference."),
        ("Who we are and who we act for", f"{P['legal']} (ABN {P['abn']}) — licence details: {P['licence']}.\n"
         f"Address: {P['address']}\nContact: {P['contact']}\n\n"
         f"{P['licensee']} is responsible for the financial services we provide under this FSG. "
         "When we arrange insurance we usually act for you, not the insurer. If we ever act under a binder or agency for an "
         "insurer, we will tell you before we provide that service."),
        ("Financial services and products we provide", "We are authorised to advise on and deal in general insurance products, "
         "including arranging cover, renewals, changes (endorsements) and helping you with claims. "
         "[PLACEHOLDER: list classes of insurance, e.g. motor, property, liability, business packages, and any premium funding]."),
        ("Other documents you may receive", "If we give you personal advice we will give you a Statement of Advice (SOA) "
         "where the law requires one. Before you buy a product you will receive the insurer's Product Disclosure Statement (PDS) "
         "and, where relevant, its Target Market Determination (TMD). PDSs and TMDs are issued by the insurer, not by us."),
        ("General advice and personal advice", "General advice does not take into account your objectives, financial situation or "
         "needs. Before acting on general advice you should consider whether it is appropriate for you and read the relevant PDS. "
         "Personal advice takes your circumstances into account; if you are a retail client and we give you personal advice, "
         "we will explain the basis of our advice."),
        ("How we are paid", f"Commission: we usually receive a commission from the insurer, calculated as a percentage of the base "
         f"premium (excluding taxes and charges). {P['commission']}.\nFees: {P['fees']}.\n"
         f"Our staff: {P['staff_pay']}.\n"
         "If you are a retail client, details of the commission and fees for a particular product will be provided when we give "
         "you advice or arrange the product, or you can ask us at any time. If a policy is cancelled we may keep our fee and "
         "commission unless the law or the insurer requires otherwise."),
        ("Associations and relationships", f"{P['associations']}. "
         "We will tell you about any conflict of interest that could reasonably be expected to influence the services we provide."),
        ("Your privacy", "We collect personal information to arrange and manage your insurance and claims, and we share it with "
         "insurers and others as described in our Privacy Policy. Our Privacy Policy explains how to access or correct your "
         "information and how to complain. [PLACEHOLDER: where the Privacy Policy is available]."),
        ("If you have a complaint", f"Please tell us first so we can try to fix it. Contact: {P['complaints']}.\n"
         "We will acknowledge your complaint within 1 business day (or as soon as practicable) and give you a written response "
         "within 30 calendar days. If you are not satisfied with our response, or we haven't responded in time, you can contact "
         f"AFCA, a free and independent external dispute resolution scheme:\n{AFCA_CONTACT}\n"
         "Time limits may apply to complaints to AFCA, so you should act promptly."),
        ("Compensation arrangements", f"{P['pi']}. These arrangements are designed to meet the compensation requirements of "
         "section 912B of the Corporations Act 2001 (Cth) and cover the conduct of current and former representatives, subject to "
         "the policy terms."),
        ("Contact us", f"{P['legal']}\n{P['address']}\n{P['contact']}"),
    ]


def privacy_policy() -> list[tuple[str, str]]:
    return [
        ("Privacy Policy", f"{P['legal']} (ABN {P['abn']}) · Version {P['version']} · Date {P['date']}\n\n"
         "We are bound by the Privacy Act 1988 (Cth) and the Australian Privacy Principles (APPs). This policy explains how we "
         "manage personal information (APP 1)."),
        ("Anonymity and pseudonyms (APP 2)", "You may deal with us anonymously or under a pseudonym where it is lawful and "
         "practicable, but we generally need your identity to arrange insurance or handle a claim."),
        ("What we collect (APP 3)", "Name, contact details, date of birth, business details (ABN, turnover, employees), details "
         "of property, vehicles and drivers, claims and insurance history, payment details, and other information needed to "
         "arrange cover. We only collect sensitive information (e.g. health or criminal history) with your consent or where the "
         "law allows, usually because an insurer needs it."),
        ("How we collect it (APP 3, APP 4, APP 5)", "Mostly directly from you — in our app, by email, phone or forms. Sometimes "
         "from others, such as insurers, premium funders, other brokers, your representatives or public sources. If we receive "
         "information we didn't ask for, we will decide whether we could have collected it and destroy or de-identify it if not. "
         "When we collect information we tell you the matters in our Collection Notice."),
        ("Why we collect, use and disclose it (APP 6)", "To arrange, renew, change and service insurance, help with claims, "
         "handle complaints, meet legal obligations (including record-keeping) and run our business. We disclose information to "
         "insurers, underwriting agencies, premium funders, claims assessors, loss adjusters, other intermediaries, our service "
         "providers (including IT and cloud hosting), and regulators where required."),
        ("Direct marketing (APP 7)", "We may send you information about our services. You can opt out at any time and we will "
         "act on your request."),
        ("Disclosure overseas (APP 8)", f"Some recipients may be located or store data overseas: {P['overseas']}. We take "
         "reasonable steps to ensure overseas recipients handle your information consistently with the APPs."),
        ("Government identifiers (APP 9)", "We do not adopt government identifiers (such as a driver licence or Medicare number) "
         "as our own identifiers."),
        ("Quality and security (APP 10, APP 11)", "We take reasonable steps to keep information accurate, up to date and complete, "
         "and to protect it from misuse, interference, loss and unauthorised access. We keep records for as long as the law "
         "requires and then destroy or de-identify them."),
        ("Access and correction (APP 12, APP 13)", f"You can ask to access or correct your information by contacting our "
         f"privacy officer: {P['privacy_officer']}. We will respond within 30 days. If we refuse, we will tell you why and how to "
         "complain."),
        ("Complaints", f"Contact our privacy officer first: {P['privacy_officer']}. If you are not satisfied with our response, "
         "you can contact AFCA (if the complaint relates to a financial service):\n" + AFCA_CONTACT +
         "\nor the Office of the Australian Information Commissioner (OAIC): www.oaic.gov.au, 1300 363 992."),
    ]


def collection_notice() -> str:
    return (f"{P['legal']} collects your personal information to arrange and manage your insurance, help with claims and meet "
            "our legal obligations. We disclose it to insurers and other parties involved in arranging your cover or claim, our "
            f"service providers and, where required, regulators. Some recipients may be overseas: {P['overseas']}. If you don't "
            "give us the information we need, we may not be able to arrange cover or a claim for you. Our Privacy Policy explains "
            f"how to access or correct your information and how to complain. Contact: {P['privacy_officer']}.")


def gaw() -> str:
    return ("This advice is general advice only. It has been prepared without taking into account your objectives, financial "
            "situation or needs. Before acting on it you should consider whether it is appropriate for you, and read the relevant "
            "Product Disclosure Statement (PDS) and Target Market Determination (TMD) from the insurer before making a decision.")


def afca() -> str:
    return (f"If you have a complaint, please contact us first: {P['complaints']}. We will acknowledge it promptly and give you "
            "a written response within 30 calendar days. If you are not satisfied with our response, or we haven't responded "
            "within 30 days, you can contact the Australian Financial Complaints Authority (AFCA), a free and independent "
            f"external dispute resolution scheme:\n{AFCA_CONTACT}\nTime limits may apply, so you should act promptly.")
