"""The reply classifier: opt-out always wins, handoff beats every answerable label, and anything unclear hands off."""
import pytest

from app.services.outreach_reply_classifier import CONFIDENCE_THRESHOLD, ReplyLabel, classify

L = ReplyLabel


@pytest.mark.parametrize("text", [
    "Please unsubscribe us", "Remove me from your list", "stop", "STOP.", "Do not contact us again", "please stop emailing us", "no more emails", "I wish to opt-out",
    "Take us off your mailing list", "Quisiera darse de baja", "Merci de nous désabonner", "Bitte abmelden", "listemizden çıkarın", "unsubscribe. also this is illegal",
])
def test_opt_out_in_several_phrasings_and_languages_always_wins(text) -> None:
    assert classify(text).label == L.OPT_OUT and classify(text).confidence == 1.0


@pytest.mark.parametrize("text,label", [
    ("Mail Delivery Subsystem: 550 user unknown", L.BOUNCE), ("Delivery Status Notification (Failure)", L.BOUNCE),
    ("I am out of office until Monday", L.AUTO_REPLY), ("Automatic reply: Introduction", L.AUTO_REPLY), ("This is an automated message, I am currently away.", L.AUTO_REPLY),
])
def test_bounces_and_auto_replies(text, label) -> None:
    assert classify(text).label == label


@pytest.mark.parametrize("text", [
    "Our lawyer will contact you", "We will report this as spam", "This is a complaint", "I am a journalist writing about you", "Please send the patient information",
    "Can we negotiate a higher commission?", "We would do it for 8 percent", "How about 12%?", "We charge $500", "Contact the ministry of health",
    "I am interested but please send the contract", "Interested! we want 5 percent",
])
def test_legal_complaint_press_patient_price_and_any_figure_hand_off_even_when_interested(text) -> None:
    c = classify(text)
    assert c.label == L.HANDOFF and c.handoff_reason


@pytest.mark.parametrize("text,label", [
    ("We are not interested, thank you", L.NOT_INTERESTED), ("No thanks", L.NOT_INTERESTED), ("Not at this time", L.NOT_INTERESTED),
    ("What commission do you pay?", L.ASKS_TERMS), ("How much would the fee be?", L.ASKS_TERMS),
    ("Please send more information", L.ASKS_DOCS), ("What documents do you need from us?", L.ASKS_DOCS),
    ("Who are you?", L.ASKS_WHO), ("Which company is this?", L.ASKS_WHO),
    ("We are interested", L.INTERESTED), ("Sounds good, happy to talk", L.INTERESTED), ("Yes", L.INTERESTED),
])
def test_answerable_labels(text, label) -> None:
    c = classify(text)
    assert c.label == label and c.confidence >= CONFIDENCE_THRESHOLD


def test_not_interested_is_not_read_as_interested() -> None:
    assert classify("We are not interested").label == L.NOT_INTERESTED


@pytest.mark.parametrize("text", ["", "   ", "Merhaba, teşekkürler.", "ok", "Thanks for the message", "What commission do you pay and who are you and send more information?"])
def test_unclear_empty_non_english_or_conflicting_replies_are_other_and_below_threshold(text) -> None:
    c = classify(text)
    assert c.label == L.OTHER and c.confidence < CONFIDENCE_THRESHOLD and c.handoff_reason


def test_classification_is_deterministic_and_never_raises_on_odd_input() -> None:
    for text in ["​­unsubscribe", "x" * 20000, "\x00\x01", "ＵＮＳＵＢＳＣＲＩＢＥ"]:
        assert classify(text) == classify(text)
    assert classify("ＵＮＳＵＢＳＣＲＩＢＥ").label == L.OPT_OUT  # full-width letters are normalised
