"""Composes clinic outreach emails from wording blocks and checks the finished text (pure, no I/O)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.models.clinic_outreach import OutreachMessageKind
from app.services.outreach_blocks import (
    banned_phrase_hits,
    figure_scan,
    normalize_text,
    BODY_MAX,
    NUMBER_WORDS_PATTERN,
    SUBJECT_MAX,
    period_mentions,
)
from app.services.outreach_settings import Sender, TermSheet

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_BODY_KEY = {
    OutreachMessageKind.INTRO: "intro",
    OutreachMessageKind.FOLLOWUP_1: "followup_1",
    OutreachMessageKind.FOLLOWUP_2: "followup_2",
}
_MASK = "ENTITY"


@dataclass(frozen=True)
class ClinicRef:
    name: str


def _plain_number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _commission(term_sheet: TermSheet) -> str:
    n = _plain_number(term_sheet.max_value)
    if term_sheet.basis == "PERCENTAGE":
        return f"{n} percent of the treatment price"
    return f"a flat {n} {term_sheet.currency} per qualifying patient"


def compose(kind: OutreachMessageKind, clinic: ClinicRef, sender: Sender, term_sheet: TermSheet, blocks: dict[str, str]) -> tuple[str, str]:
    kind = OutreachMessageKind(kind)
    body_key = _BODY_KEY[kind]
    subject_key = "subject_intro" if kind is OutreachMessageKind.INTRO else f"subject_{body_key}"
    values = {
        "clinic_name": clinic.name,
        "sender_name": sender.display_name,
        "sender_title": sender.signature,
        "business_name": sender.company_name,
        "postal_address": sender.postal_address,
        "commission": _commission(term_sheet),
        "payment_days": str(term_sheet.payment_days),
    }
    for key in (subject_key, body_key, "opt_out_line"):
        if not isinstance(blocks.get(key), str):
            raise ValueError(f"block {key!r} is missing")
    values["opt_out_line"] = blocks["opt_out_line"]

    def fill(text: str) -> str:
        def sub(match: re.Match) -> str:
            name = match.group(1).strip()
            if name not in values:
                raise ValueError(f"unknown placeholder {{{name}}}")
            return values[name]

        return _PLACEHOLDER.sub(sub, text)

    return fill(blocks[subject_key]), fill(blocks[body_key])


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+?\(?\d[\d\s().-]*\d")
_DIGITS = re.compile(r"\d+(?:[.,]\d+)*")
_CURRENCY_SYMBOL = re.compile(r"[$€£¥₹]\s*\d|\d\s*[$€£¥₹]")
_CURRENCY_CODE = re.compile(r"\b[A-Za-z]{3}\s*\d|\d\s*[A-Za-z]{3}\b")
_NUMBER_WORD_UNIT = re.compile(rf"\b(?:{NUMBER_WORDS_PATTERN})\b", re.IGNORECASE)
_CURRENCY_CODES = frozenset(
    "USD EUR GBP TRY AED CAD AUD CHF JPY CNY INR SAR QAR KWD EGP MAD THB PLN CZK HUF RON BGN SEK NOK DKK ZAR MXN BRL KRW".split())


def _mask(text: str, clinic: ClinicRef, sender: Sender) -> str:
    fields = {clinic.name, sender.display_name, sender.signature, sender.company_name, sender.postal_address}
    names = sorted((n for n in fields if len(n) >= 3), key=len, reverse=True)
    for name in names:
        text = text.replace(name, _MASK)
    return text


def check(subject: str, body: str, *, clinic: ClinicRef, sender: Sender, opt_out_line: str, allowed_figures: frozenset[str] = frozenset(),
          permitted_phrases: tuple[str, ...] = ()) -> list[str]:
    """`permitted_phrases` are exact sentences the term sheet itself produced (for example the rendered commission); they are removed before the figure scans, so only the owner's own terms can pass."""
    problems: list[str] = []
    if len(subject) > SUBJECT_MAX:
        problems.append(f"subject is {len(subject)} characters; the limit is {SUBJECT_MAX}")
    if len(body) > BODY_MAX:
        problems.append(f"body is {len(body)} characters; the limit is {BODY_MAX}")

    raw = f"{subject}\n{body}"
    for token in sorted({t.strip() for t in _PLACEHOLDER.findall(raw)}):
        problems.append(f"unresolved placeholder {{{token}}}")
    for phrase in banned_phrase_hits(raw):
        problems.append(f"contains the claim '{phrase}', which we cannot make")
    if sender.postal_address not in body:
        problems.append("the postal address is missing")
    if sender.company_name not in body:
        problems.append("the business name is missing")
    if not opt_out_line.strip() or opt_out_line not in body:
        problems.append("the opt-out line is missing")

    text = normalize_text(_mask(raw, clinic, sender))
    for phrase in permitted_phrases:
        text = text.replace(normalize_text(phrase), " ")
    problems.extend(figure_scan(text))
    if _CURRENCY_SYMBOL.search(text) or any(m.group(0).split()[0].upper() in _CURRENCY_CODES or m.group(0).split()[-1].upper() in _CURRENCY_CODES
                                           for m in _CURRENCY_CODE.finditer(text)):
        problems.append("mentions a currency amount")
    for match in _EMAIL.finditer(text):
        problems.append(f"contains an email address ({match.group(0)})")
    text_no_email = _EMAIL.sub(" ", text)
    for match in _PHONE.finditer(text_no_email):
        if sum(c.isdigit() for c in match.group(0)) >= 7:
            problems.append(f"contains a phone number ({match.group(0).strip()})")
    for match in _DIGITS.finditer(text_no_email):
        if match.group(0) not in allowed_figures:
            problems.append(f"contains the figure {match.group(0)}")
    for phrase in period_mentions(text):
        words = [w for w in re.findall(r"[a-z]+", phrase) if re.fullmatch(NUMBER_WORDS_PATTERN, w)]
        if not words or not all(w in allowed_figures for w in words):
            problems.append(f"states a period in words ({phrase})")
    return problems
