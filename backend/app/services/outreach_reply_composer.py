"""What to do with a classified clinic reply, and the drafted answer where the table calls for one. Pure, no I/O.

The model never decides an action: the table below does. Every answer is built only from the owner-approved blocks, the term sheet and the fixed neutral lines here,
and passes the same checker as the first email. A draft that fails the checker falls back to its single most relevant block, then to a person.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.models.clinic_outreach import OutreachStage
from app.services.outreach_composer import _PLACEHOLDER, ClinicRef, _commission, _plain_number, check
from app.services.outreach_reply_classifier import CONFIDENCE_THRESHOLD, Classification, ReplyLabel
from app.services.outreach_settings import Sender, TermSheet

AUTO_REPLY_DELAY = timedelta(days=3)

OPT_OUT_CONFIRMATION = "Understood. We have removed you from our list and will not contact you again."
THANKS_DECLINE = "Thank you for letting us know. We will not contact you again about this."
THANKS_LEAD_IN = "Thank you for your reply."
NEEDS_LICENCE_LINE = "If you would like us to consider your clinic, please reply with its licence or accreditation."


@dataclass(frozen=True)
class ReplyPlan:
    label: ReplyLabel
    next_stage: str | None  # None: the stage does not change
    draft: tuple[str, str] | None = None  # (subject, body), stored in shadow and never sent
    suppress: bool = False
    clear_contact: bool = False
    reschedule: timedelta | None = None
    handoff_reason: str | None = None
    problems: tuple[str, ...] = ()  # why a draft was dropped, for the owner to see


def _values(clinic: ClinicRef, sender: Sender, term_sheet: TermSheet, blocks: dict[str, str]) -> dict[str, str]:
    return {
        "clinic_name": clinic.name, "sender_name": sender.display_name, "sender_title": sender.signature, "business_name": sender.company_name,
        "postal_address": sender.postal_address, "commission": _commission(term_sheet), "payment_days": str(term_sheet.payment_days), "opt_out_line": blocks["opt_out_line"],
    }


def _fill(text: str, values: dict[str, str]) -> str:
    def sub(match):
        name = match.group(1).strip()
        if name not in values:
            raise ValueError(f"unknown placeholder {{{name}}}")
        return values[name]

    return _PLACEHOLDER.sub(sub, text)


def _wrap(paragraphs: list[str], clinic: ClinicRef, sender: Sender, values: dict[str, str]) -> tuple[str, str]:
    body = f"Dear {clinic.name} team,\n\n" + "\n\n".join(paragraphs) + f"\n\n{sender.display_name}\n{sender.company_name}\n{sender.postal_address}\n\n{values['opt_out_line']}"
    return f"Re: {sender.company_name}", body


def _draft(paragraphs: list[str], clinic: ClinicRef, sender: Sender, term_sheet: TermSheet, blocks: dict[str, str], fallback: list[str] | None) -> tuple[tuple[str, str] | None, tuple[str, ...]]:
    """Fill, wrap and check; on a problem try the shorter fallback paragraph list; return (draft or None, problems of the last attempt)."""
    values = _values(clinic, sender, term_sheet, blocks)
    allowed = frozenset({_plain_number(term_sheet.max_value), str(term_sheet.payment_days)})
    problems: tuple[str, ...] = ()
    for attempt in (paragraphs, fallback):
        if attempt is None:
            continue
        try:
            subject, body = _wrap([_fill(p, values) for p in attempt], clinic, sender, values)
        except (ValueError, KeyError) as exc:
            problems = (str(exc),)
            continue
        problems = tuple(check(subject, body, clinic=clinic, sender=sender, opt_out_line=blocks["opt_out_line"], allowed_figures=allowed,
                                     permitted_phrases=(_commission(term_sheet),)))
        if not problems:
            return (subject, body), ()
    return None, problems


def _handoff(label: ReplyLabel, reason: str, problems: tuple[str, ...] = ()) -> ReplyPlan:
    return ReplyPlan(label=label, next_stage=OutreachStage.NEEDS_PERSON, handoff_reason=reason, problems=problems)


def plan_reply(classification: Classification, *, stage: str | None, clinic: ClinicRef, sender: Sender, term_sheet: TermSheet, blocks: dict[str, str],
               repeated: bool = False) -> ReplyPlan:
    """`stage` is the clinic's current stage (None when trying a sample reply); `repeated` is true when the clinic asks what it asked last time."""
    label = classification.label
    if label is ReplyLabel.OPT_OUT:
        draft, problems = _draft([OPT_OUT_CONFIRMATION], clinic, sender, term_sheet, blocks, None)
        return ReplyPlan(label, OutreachStage.OPTED_OUT, draft, suppress=True, problems=problems)  # the opt-out is honoured even when the confirmation cannot be drafted
    if label is ReplyLabel.BOUNCE:
        return ReplyPlan(label, OutreachStage.NO_EMAIL, clear_contact=True)
    if label is ReplyLabel.AUTO_REPLY:
        return ReplyPlan(label, None, reschedule=AUTO_REPLY_DELAY)
    if label is ReplyLabel.HANDOFF:
        return _handoff(label, classification.handoff_reason or "A person needs to read this reply")
    if label is ReplyLabel.OTHER or classification.confidence < CONFIDENCE_THRESHOLD:
        return _handoff(ReplyLabel.OTHER if label is ReplyLabel.OTHER else label, classification.handoff_reason or "We were not sure what the reply is asking")
    if repeated:
        return _handoff(label, "The clinic asked the same thing again after our automatic answer")
    if label is ReplyLabel.NOT_INTERESTED:
        draft, problems = _draft([THANKS_DECLINE], clinic, sender, term_sheet, blocks, None)
        return ReplyPlan(label, OutreachStage.DECLINED, draft, suppress=True, problems=problems)
    if label is ReplyLabel.ASKS_TERMS and stage == OutreachStage.TERMS_PROPOSED:
        return _handoff(label, "The clinic asks about terms again after our one proposal")

    if label in (ReplyLabel.INTERESTED, ReplyLabel.ASKS_WHO):
        paragraphs = [THANKS_LEAD_IN, "{how_referrals_work}", NEEDS_LICENCE_LINE]
        fallback, next_stage = [THANKS_LEAD_IN, "{how_referrals_work}"], OutreachStage.REPLIED
    elif label is ReplyLabel.ASKS_TERMS:
        paragraphs = [THANKS_LEAD_IN, "{faq_commission}"]
        fallback, next_stage = [THANKS_LEAD_IN, "{faq_commission}"], OutreachStage.TERMS_PROPOSED
    elif label is ReplyLabel.ASKS_DOCS:
        paragraphs = [THANKS_LEAD_IN, "{faq_what_we_do}", "{faq_what_we_need}", "{faq_commission}"]
        fallback, next_stage = [THANKS_LEAD_IN, "{faq_what_we_need}"], OutreachStage.REPLIED
    else:  # pragma: no cover - every label is handled above
        return _handoff(label, "A person needs to read this reply")

    # The blocks hold the text; the placeholders inside them are filled in _draft.
    paragraphs = [blocks.get(p[1:-1], "") if p.startswith("{") and p.endswith("}") and p[1:-1] in blocks else p for p in paragraphs]
    fallback = [blocks.get(p[1:-1], "") if p.startswith("{") and p.endswith("}") and p[1:-1] in blocks else p for p in fallback]
    draft, problems = _draft(paragraphs, clinic, sender, term_sheet, blocks, fallback)
    if draft is None:
        return _handoff(label, "The drafted answer did not pass the safety check", problems)
    return ReplyPlan(label, next_stage, draft)
