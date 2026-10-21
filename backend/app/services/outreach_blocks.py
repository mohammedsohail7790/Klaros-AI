"""Wording blocks for clinic outreach emails: the platform's draft text and the rules a block set must satisfy before an owner can approve it.

Pure functions, no I/O. The text carries no figure of any kind: numbers reach an email only through `{commission}` and `{payment_days}`, which are filled
from the campaign's term sheet.
"""

from __future__ import annotations

import re
from typing import Any

DEFAULT_BLOCKS: dict[str, str] = {
    'subject_intro': 'Introduction from {business_name}',
    'intro': 'Dear {clinic_name} team,\n\nI am {sender_name}, {sender_title} at {business_name}. We help international patients compare clinics abroad and arrange their travel, and we are building a list of clinics we can recommend. I am writing to introduce ourselves and to ask who in your team handles international patient enquiries.\n\nWe are not asking you to commit to anything. If it is useful, we can explain how our referral arrangement works and what we would need from your clinic, such as licence and accreditation details, before we could consider listing you.\n\n{sender_name}\n{business_name}\n{postal_address}\n\n{opt_out_line}',
    'subject_followup_1': 'Following up: {business_name}',
    'followup_1': 'Dear {clinic_name} team,\n\nI wrote a few days ago to introduce {business_name}. I would be glad to know who handles international patient enquiries at your clinic, and I can send a short description of how our referral arrangement works if that helps.\n\n{sender_name}\n{business_name}\n{postal_address}\n\n{opt_out_line}',
    'subject_followup_2': 'A last note from {business_name}',
    'followup_2': 'Dear {clinic_name} team,\n\nThis is my last message on this topic. If international patient enquiries are something your clinic would like to discuss, please reply and I will send details. If not, no action is needed and we will not write again.\n\n{sender_name}\n{business_name}\n{postal_address}\n\n{opt_out_line}',
    'opt_out_line': 'If you would rather not hear from us, reply "stop" and we will not contact you again.',
    'how_referrals_work': "We introduce patients who are looking for treatment abroad to clinics that we have checked. If a patient we introduced completes a qualifying treatment at your clinic, the clinic pays {business_name} a referral fee on the terms we agree in writing beforehand. We do not agree anything until your clinic's licence and accreditation have been checked by our team.",
    'faq_what_we_do': '{business_name} helps people compare clinics abroad and arranges their travel and aftercare. We do not provide medical care ourselves.',
    'faq_what_we_need': 'To consider listing your clinic we need your current medical licence, any international accreditation, the name of a contact for international patients, and a list of the treatments you offer to patients from abroad.',
    'faq_commission': 'Referral fees are agreed in writing and per clinic. Our standard arrangement is {commission}, payable within {payment_days} days of a qualifying treatment. We will confirm the final terms with you before anything is agreed.',
}

ALLOWED_PLACEHOLDERS: frozenset[str] = frozenset(
    {"clinic_name", "business_name", "sender_name", "sender_title", "postal_address", "opt_out_line", "commission", "payment_days"})
BANNED_PHRASES: tuple[str, ...] = ("partner of", "official partner", "endorsed", "guarantee", "guaranteed", "exclusive", "we send you patients", "patients per month")

SUBJECT_MAX, BODY_MAX = 120, 1500
COMPOSED_KINDS = ("intro", "followup_1", "followup_2")
REQUIRED_IN_COMPOSED = ("business_name", "postal_address", "opt_out_line")

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_PERCENT_WORDS = re.compile(r"%|\bper\s*cent\b|\bpercent(age)?\b", re.IGNORECASE)
_NUMBER_WORDS = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|"
                 "forty|fifty|sixty|seventy|eighty|ninety|hundred")
_WITHIN_PERIOD = re.compile(rf"\bwithin\s+(?:an?\s+)?(?:{_NUMBER_WORDS})(?:[\s-]+(?:{_NUMBER_WORDS}))*\s+(?:days?|weeks?|months?)\b", re.IGNORECASE)


def validate_blocks(blocks: dict[str, Any]) -> dict[str, list[str]]:
    """Block name -> plain-language problems. An empty dict means the set is valid."""
    problems: dict[str, list[str]] = {}

    def add(name: str, message: str) -> None:
        problems.setdefault(name, []).append(message)

    if not isinstance(blocks, dict):
        return {"blocks": ["must be a set of named blocks"]}
    for name in DEFAULT_BLOCKS:
        if name not in blocks:
            add(name, "is missing")
        elif not isinstance(blocks[name], str):
            add(name, "must be text")
    for name in blocks:
        if name not in DEFAULT_BLOCKS:
            add(str(name), "is not a known block")
    for name in DEFAULT_BLOCKS:
        text = blocks.get(name)
        if not isinstance(text, str):
            continue
        for token in sorted({t.strip() for t in _PLACEHOLDER.findall(text)} - ALLOWED_PLACEHOLDERS):
            add(name, f"uses an unknown placeholder {{{token}}}; allowed: " + ", ".join("{" + p + "}" for p in sorted(ALLOWED_PLACEHOLDERS)))
        leftover = _PLACEHOLDER.sub("", text)
        if "{" in leftover or "}" in leftover:
            add(name, "has a stray curly bracket")
        if re.search(r"\d", text):
            add(name, "contains a digit; figures can only come from placeholders")
        if _PERCENT_WORDS.search(text):
            add(name, "mentions a percentage; figures can only come from placeholders")
        if _WITHIN_PERIOD.search(text):
            add(name, "states a number of days, weeks or months in words; figures can only come from placeholders")
        lowered = text.lower()
        for phrase in BANNED_PHRASES:
            if phrase in lowered:
                add(name, f"contains the claim '{phrase}', which we cannot make")
        limit = SUBJECT_MAX if name.startswith("subject_") else BODY_MAX
        if len(text) > limit:
            add(name, f"is {len(text)} characters; the limit is {limit}")
    for kind in COMPOSED_KINDS:
        text = blocks.get(kind)
        if isinstance(text, str):
            for placeholder in REQUIRED_IN_COMPOSED:
                if "{" + placeholder + "}" not in text:
                    add(kind, f"must include {{{placeholder}}}")
    opt_out = blocks.get("opt_out_line")
    if isinstance(opt_out, str) and not opt_out.strip():
        add("opt_out_line", "cannot be blank")
    return problems
