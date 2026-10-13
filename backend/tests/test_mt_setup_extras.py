"""The optional one-form setup answers that fill otherwise-Missing Blueprint sections.

Pure validation / mapping, no database. Only what the owner typed is kept.
"""

import pytest

from app.models.business_blueprint import BlueprintSectionKey
from app.services.mt_referrals import ReferralError
from app.services.mt_setup import _extra_sections, validate_extras


def test_nothing_given_means_nothing_added() -> None:
    assert validate_extras({}) == {}
    assert validate_extras({"channels": [], "currencies": [], "goals": ""}) == {}
    assert _extra_sections({}) == {}


def test_channels_currencies_and_goals_are_cleaned_and_mapped() -> None:
    extras = validate_extras(
        {"channels": ["Website enquiries", "  WhatsApp ", "website enquiries"], "currencies": ["aed", "USD", "aed"], "goals": " 50 referred patients in 6 months "}
    )
    assert extras["currencies"] == ["AED", "USD"]
    assert extras["goals"] == "50 referred patients in 6 months"
    assert extras["channels"][0] == "Website enquiries" and "WhatsApp" in extras["channels"]

    sections = _extra_sections(extras)
    assert sections[BlueprintSectionKey.CHANNELS] == {"channels": extras["channels"]}
    assert sections[BlueprintSectionKey.FINANCE] == {"currencies": ["AED", "USD"]}
    assert sections[BlueprintSectionKey.GOALS] == {"goals": ["50 referred patients in 6 months"]}


def test_only_answered_sections_are_written() -> None:
    sections = _extra_sections(validate_extras({"currencies": ["GBP"]}))
    assert set(sections) == {BlueprintSectionKey.FINANCE}


@pytest.mark.parametrize(
    "body",
    [
        {"currencies": ["dollars"]},
        {"currencies": ["A1D"]},
        {"currencies": ["AED", "USD", "GBP", "EUR", "INR", "TRY", "SAR", "QAR", "KWD"]},
        {"channels": ["x"] * 11},
        {"channels": "website"},
        {"goals": "g" * 501},
    ],
)
def test_bad_input_is_rejected(body) -> None:
    with pytest.raises(ReferralError):
        validate_extras(body)
