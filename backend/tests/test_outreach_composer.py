from decimal import Decimal

import pytest

from app.models.clinic_outreach import OutreachMessageKind as K
from app.services.outreach_blocks import BANNED_PHRASES, DEFAULT_BLOCKS
from app.services.outreach_composer import ClinicRef, check, compose
from app.services.outreach_settings import Sender, TermSheet

SENDER = Sender(display_name="Amira Hale", signature="Partnerships Lead", company_name="Medluxe Travel", postal_address="1 Harbour Road, Leeds")
CLINIC = ClinicRef(name="Sunrise Clinic")


def terms(basis="PERCENTAGE", value="10.00"):
    return TermSheet(basis=basis, min_value=Decimal("5"), max_value=Decimal(value), payment_days=30, currency="usd", qualifying_outcome="completed treatment")


def good(kind=K.INTRO, clinic=CLINIC, sender=SENDER):
    return compose(kind, clinic, sender, terms(), DEFAULT_BLOCKS)


def blocks_with(**over):
    return {**DEFAULT_BLOCKS, **over}


def test_compose_fills_every_placeholder_and_is_deterministic():
    s1, b1 = good()
    assert (s1, b1) == good()
    assert "{" not in s1 + b1 and "Sunrise Clinic" in b1 and "Amira Hale" in b1 and "Partnerships Lead" in b1
    assert SENDER.postal_address in b1 and 'reply "stop"' in b1
    with pytest.raises(ValueError):
        compose(K.INTRO, CLINIC, SENDER, terms(), blocks_with(intro="Hi {nope} {opt_out_line} {business_name} {postal_address}"))
    broken = dict(DEFAULT_BLOCKS)
    del broken["followup_2"]
    with pytest.raises(ValueError):
        compose(K.FOLLOWUP_2, CLINIC, SENDER, terms(), broken)


def test_commission_rendering_for_percentage_and_flat():
    b = blocks_with(intro="{commission} / {payment_days} {business_name} {postal_address} {opt_out_line}")
    assert compose(K.INTRO, CLINIC, SENDER, terms("PERCENTAGE", "10.00"), b)[1].startswith("10 percent of the treatment price / 30")
    assert compose(K.INTRO, CLINIC, SENDER, terms("FLAT", "250.50"), b)[1].startswith("a flat 250.5 USD per qualifying patient")
    assert compose(K.INTRO, CLINIC, SENDER, terms("FLAT", "100"), b)[1].startswith("a flat 100 USD")


@pytest.mark.parametrize("kind", list(K))
def test_default_blocks_compose_and_pass_the_checker_for_all_three_kinds(kind):
    s, b = good(kind)
    assert check(s, b, clinic=CLINIC, sender=SENDER) == []


def test_address_digits_and_clinic_name_digits_do_not_false_block():
    sender = Sender(display_name="Amira Hale", signature="Lead", company_name="Medluxe Travel", postal_address="12 Main St 34000")
    clinic = ClinicRef(name="Clinic 24")
    s, b = good(clinic=clinic, sender=sender)
    assert check(s, b, clinic=clinic, sender=sender) == []


@pytest.mark.parametrize("stray", ["Our fee is 10%.", "Please reply within five days.", "Fee of USD 200.", "It costs $50.", "Call +90 555 123 4567 now.",
                                   "Reply in ten days.", "We pay 7 days after.", "About 12 patients."])
def test_a_stray_percentage_amount_or_day_count_blocks(stray):
    s, b = good()
    assert check(s, b + "\n" + stray, clinic=CLINIC, sender=SENDER)


def test_allowed_figures_pass():
    s, b = good()
    extra = b + "\nPayable in 30 days, 10 percent."
    assert check(s, extra, clinic=CLINIC, sender=SENDER, allowed_figures=frozenset({"10", "30"})) == ["mentions a percentage"]
    assert check(s, b + "\nPayment in 30 days.", clinic=CLINIC, sender=SENDER, allowed_figures=frozenset({"30"})) == []
    assert check(s, b + "\nPayment in 30 days.", clinic=CLINIC, sender=SENDER)
    assert check(s, b + "\nwithin five days", clinic=CLINIC, sender=SENDER, allowed_figures=frozenset({"five"})) == []


def test_unresolved_placeholder_blocks():
    s, b = good()
    assert any("unresolved" in p for p in check(s, b + " {oops}", clinic=CLINIC, sender=SENDER))


@pytest.mark.parametrize("phrase", BANNED_PHRASES)
def test_each_banned_phrase_blocks(phrase):
    s, b = good()
    assert any(phrase in p for p in check(s, b + f"\nWe are {phrase.upper()}.", clinic=CLINIC, sender=SENDER))


def test_banned_phrase_in_clinic_name_is_not_masked_away():
    clinic = ClinicRef(name="Exclusive Smiles")
    s, b = good(clinic=clinic)
    assert any("exclusive" in p for p in check(s, b, clinic=clinic, sender=SENDER))


def test_stray_email_and_phone_block():
    s, b = good()
    assert any("email" in p for p in check(s, b + "\nwrite to a.b@clinic.example", clinic=CLINIC, sender=SENDER))
    assert any("phone" in p for p in check(s, b + "\ncall (555) 123-4567", clinic=CLINIC, sender=SENDER))


def test_missing_opt_out_or_address_blocks():
    s, b = good()
    assert any("opt-out" in p for p in check(s, b.replace("stop", "x"), clinic=CLINIC, sender=SENDER))
    assert any("postal" in p for p in check(s, b.replace(SENDER.postal_address, "elsewhere"), clinic=CLINIC, sender=SENDER))
    assert any("business name" in p for p in check(s, b.replace("Medluxe Travel", "Us"), clinic=CLINIC, sender=SENDER))


def test_length_limits_block():
    s, b = good()
    assert any("subject" in p for p in check("x" * 121, b, clinic=CLINIC, sender=SENDER))
    assert any("body" in p for p in check(s, b + "y" * 1500, clinic=CLINIC, sender=SENDER))
