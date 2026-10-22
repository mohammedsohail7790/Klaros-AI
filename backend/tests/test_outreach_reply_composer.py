"""The reply action table and the drafted answers: built only from approved blocks and the term sheet, always checked, falling back to a person."""
import pytest

from app.models.clinic_outreach import OutreachStage
from app.services.outreach_blocks import DEFAULT_BLOCKS
from app.services.outreach_composer import ClinicRef, check
from app.services.outreach_reply_classifier import Classification, ReplyLabel, classify
from app.services.outreach_reply_composer import AUTO_REPLY_DELAY, plan_reply
from app.services.outreach_settings import OutreachConfig

CFG = OutreachConfig.model_validate({
    "term_sheet": {"basis": "PERCENTAGE", "min_value": "10", "max_value": "20", "payment_days": 30, "currency": "usd", "qualifying_outcome": "Treatment completed"},
    "sender": {"display_name": "Sam", "signature": "Sam, MedLuxe", "company_name": "MedLuxe Travel", "postal_address": "1 Main St, Dubai"},
    "countries": ["TR"],
})
CLINIC = ClinicRef("Alpha Clinic")
BLOCKS = dict(DEFAULT_BLOCKS)


def plan(text, stage=OutreachStage.INTRO_SENT, blocks=BLOCKS, **kw):
    return plan_reply(classify(text), stage=stage, clinic=CLINIC, sender=CFG.sender, term_sheet=CFG.term_sheet, blocks=blocks, **kw)


@pytest.mark.parametrize("text,label,stage", [
    ("We are interested", ReplyLabel.INTERESTED, OutreachStage.REPLIED),
    ("Who are you?", ReplyLabel.ASKS_WHO, OutreachStage.REPLIED),
    ("What commission do you pay?", ReplyLabel.ASKS_TERMS, OutreachStage.TERMS_PROPOSED),
    ("Please send more information", ReplyLabel.ASKS_DOCS, OutreachStage.REPLIED),
])
def test_answerable_replies_get_a_checked_draft_and_move_on(text, label, stage) -> None:
    p = plan(text)
    assert p.label == label and p.next_stage == stage and p.draft and not p.problems
    subject, body = p.draft
    assert "Alpha Clinic" in body and "MedLuxe Travel" in body and "1 Main St, Dubai" in body and BLOCKS["opt_out_line"] in body
    assert check(subject, body, clinic=CLINIC, sender=CFG.sender, opt_out_line=BLOCKS["opt_out_line"], allowed_figures=frozenset({"20", "30"}),
                 permitted_phrases=("20 percent of the treatment price",)) == []


def test_the_terms_proposal_uses_only_the_term_sheets_figures() -> None:
    _, body = plan("What commission do you pay?").draft
    assert "20 percent of the treatment price" in body and "30 days" in body and "10" not in body.replace("1 Main St", "")


def test_opt_out_suppresses_confirms_and_honours_the_request_even_if_drafting_fails() -> None:
    p = plan("unsubscribe")
    assert (p.next_stage, p.suppress) == (OutreachStage.OPTED_OUT, True) and p.draft and "will not contact you again" in p.draft[1]
    broken = {**BLOCKS, "opt_out_line": "{unknown}"}
    p = plan("unsubscribe", blocks=broken)
    assert p.next_stage == OutreachStage.OPTED_OUT and p.suppress and p.draft is None and p.problems


def test_not_interested_declines_thanks_and_suppresses() -> None:
    p = plan("No thanks")
    assert (p.next_stage, p.suppress) == (OutreachStage.DECLINED, True) and p.draft


def test_bounce_clears_the_address_and_auto_reply_only_reschedules() -> None:
    p = plan("Delivery Status Notification (Failure)")
    assert (p.next_stage, p.clear_contact, p.draft) == (OutreachStage.NO_EMAIL, True, None)
    p = plan("I am out of office")
    assert (p.next_stage, p.reschedule, p.draft) == (None, AUTO_REPLY_DELAY, None)


@pytest.mark.parametrize("text", ["Our lawyer will write", "How about 8 percent?", "ok", "What commission do you pay and who are you?"])
def test_handoffs_never_draft_and_say_why(text) -> None:
    p = plan(text)
    assert p.next_stage == OutreachStage.NEEDS_PERSON and p.draft is None and p.handoff_reason


def test_a_second_terms_question_after_the_one_proposal_goes_to_a_person() -> None:
    p = plan("What commission do you pay?", stage=OutreachStage.TERMS_PROPOSED)
    assert p.next_stage == OutreachStage.NEEDS_PERSON and "one proposal" in p.handoff_reason


def test_the_same_question_twice_goes_to_a_person() -> None:
    assert plan("We are interested", repeated=True).next_stage == OutreachStage.NEEDS_PERSON


def test_a_low_confidence_answerable_label_hands_off() -> None:
    p = plan_reply(Classification(ReplyLabel.INTERESTED, 0.4, ("x",)), stage=None, clinic=CLINIC, sender=CFG.sender, term_sheet=CFG.term_sheet, blocks=BLOCKS)
    assert p.next_stage == OutreachStage.NEEDS_PERSON and p.draft is None


def test_a_draft_that_fails_the_checker_falls_back_then_hands_off() -> None:
    long_what_we_need = {**BLOCKS, "faq_what_we_need": "We need your documents. " * 70}
    p = plan("What documents do you need from us?", blocks=long_what_we_need)  # the full answer is too long; the single-block fallback is also too long
    assert p.next_stage == OutreachStage.NEEDS_PERSON and p.draft is None and p.problems
    long_what_we_do = {**BLOCKS, "faq_what_we_do": "We help patients. " * 120}
    p = plan("Please send more information", blocks=long_what_we_do)  # the full answer is too long but the fallback (what we need) fits
    assert p.next_stage == OutreachStage.REPLIED and p.draft and "We help patients" not in p.draft[1]


def test_a_stray_percentage_is_still_blocked_when_only_the_term_sheets_phrase_is_permitted() -> None:
    sneaky = {**BLOCKS, "faq_commission": "Our arrangement is {commission}, and sometimes 25 percent."}
    p = plan("What commission do you pay?", blocks=sneaky)
    assert p.next_stage == OutreachStage.NEEDS_PERSON and p.draft is None and any("percentage" in x for x in p.problems)
