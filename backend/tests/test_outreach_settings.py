from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.services.outreach_settings import Limits, OutreachConfig, Sender, TermSheet


def _term(**over):
    base = dict(
        basis="PERCENTAGE",
        min_value=Decimal("10"),
        max_value=Decimal("15"),
        payment_days=30,
        currency="usd",
        qualifying_outcome="Completed treatment",
    )
    base.update(over)
    return base


def _sender(**over):
    base = dict(
        display_name="Sam",
        signature="Sam, Klaros",
        company_name="Klaros",
        postal_address="1 Main St",
    )
    base.update(over)
    return base


def test_defaults_match_the_spec():
    limits = Limits()
    assert (
        limits.daily_cap,
        limits.ramp_start,
        limits.max_emails_per_clinic,
        limits.send_window_start,
        limits.send_window_end,
        limits.weekdays_only,
    ) == (20, 5, 3, 9, 17, True)


@pytest.mark.parametrize(
    "over",
    [
        dict(min_value=Decimal("20"), max_value=Decimal("15")),
        dict(max_value=Decimal("150")),
        dict(min_value=Decimal("-1")),
        dict(payment_days=0),
        dict(payment_days=121),
        dict(currency="US"),
        dict(min_value=Decimal("0")),
        dict(basis="FLAT", min_value=Decimal("500"), max_value=Decimal("100")),
        dict(qualifying_outcome="   "),
    ],
)
def test_term_sheet_rejects_nonsense(over):
    with pytest.raises(ValidationError):
        TermSheet(**_term(**over))


def test_percentage_term_sheet_accepts_10_to_15_and_30_days():
    ts = TermSheet(**_term())
    assert ts.min_value == Decimal("10")
    assert ts.max_value == Decimal("15")
    assert ts.payment_days == 30
    assert ts.currency == "USD"


def test_flat_term_sheet_allows_amounts_above_100():
    ts = TermSheet(**_term(basis="FLAT", min_value=Decimal("250"), max_value=Decimal("500")))
    assert ts.max_value == Decimal("500")


def test_term_sheet_round_trips_through_json():
    ts = TermSheet(**_term(min_value=Decimal("10.5"), max_value=Decimal("12.25")))
    again = TermSheet.model_validate(ts.model_dump(mode="json"))
    assert again == ts
    assert again.min_value == Decimal("10.5")
    assert isinstance(again.max_value, Decimal)


def test_limits_cannot_exceed_three_emails_per_clinic():
    with pytest.raises(ValidationError):
        Limits(max_emails_per_clinic=4)


def test_ramp_start_cannot_exceed_daily_cap():
    with pytest.raises(ValidationError):
        Limits(daily_cap=3, ramp_start=5)


def test_send_window_must_start_before_it_ends():
    with pytest.raises(ValidationError):
        Limits(send_window_start=17, send_window_end=9)
    with pytest.raises(ValidationError):
        Limits(send_window_start=9, send_window_end=9)


def test_sender_fields_cannot_be_blank_or_whitespace():
    for field in ("display_name", "signature", "company_name", "postal_address"):
        with pytest.raises(ValidationError):
            Sender(**_sender(**{field: "   "}))
    assert Sender(**_sender(display_name="  Sam  ")).display_name == "Sam"


def test_countries_must_be_two_letter_codes_and_not_empty():
    cfg = OutreachConfig(
        term_sheet=TermSheet(**_term()), sender=Sender(**_sender()), countries=[" tr ", "ae"]
    )
    assert cfg.countries == ["TR", "AE"]
    assert cfg.limits == Limits()
    for bad in ([], ["USA"], ["T1"], [""]):
        with pytest.raises(ValidationError):
            OutreachConfig(
                term_sheet=TermSheet(**_term()), sender=Sender(**_sender()), countries=bad
            )


@pytest.mark.parametrize("value", ["0", "-0.01", "100.01", "NaN", "Infinity", "-Infinity", "sNaN"])
def test_percentage_rejects_out_of_range_and_non_finite_values(value):
    with pytest.raises(ValidationError):
        TermSheet(**_term(min_value="1", max_value=value))
    with pytest.raises(ValidationError):
        TermSheet(**_term(min_value=value, max_value="50"))


@pytest.mark.parametrize("value", ["0.01", "100"])
def test_percentage_accepts_the_boundary_values(value):
    assert TermSheet(**_term(min_value=value, max_value=value)).max_value == Decimal(value)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_flat_rejects_non_finite_values(value):
    with pytest.raises(ValidationError):
        TermSheet(**_term(basis="FLAT", min_value="1", max_value=value))
    with pytest.raises(ValidationError):
        TermSheet(**_term(basis="FLAT", min_value=value, max_value="5"))


def test_non_finite_values_in_a_raw_json_body_are_rejected():
    body = (
        '{"term_sheet": {"basis": "FLAT", "min_value": "NaN", "max_value": "Infinity", "payment_days": 30, "currency": "USD",'
        ' "qualifying_outcome": "x"}, "sender": {"display_name": "a", "signature": "b", "company_name": "c", "postal_address": "d"}, "countries": ["TR"]}'
    )
    with pytest.raises(ValidationError):
        OutreachConfig.model_validate_json(body)
