"""crm.check_availability must work when a crew member passes no dates (the Booking Coordinator did this and failed every run)."""
from datetime import datetime, timedelta, timezone

from app.tools.builtin.appointment_tools import DEFAULT_AVAILABILITY_WINDOW, CheckAvailabilityInput


def test_no_dates_defaults_to_next_two_weeks():
    before = datetime.now(timezone.utc)
    inp = CheckAvailabilityInput.model_validate({"lead_id": "0e15cabd-0190-4b44-b8b4-583230ffc408"})  # an unrelated field is ignored, as the model sent it
    assert before <= inp.date_from <= datetime.now(timezone.utc)
    assert inp.date_to - inp.date_from == DEFAULT_AVAILABILITY_WINDOW


def test_only_start_given_window_follows_it():
    start = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    inp = CheckAvailabilityInput(date_from=start)
    assert inp.date_to == start + timedelta(days=14)


def test_explicit_dates_are_kept():
    a, b = datetime(2026, 11, 2, tzinfo=timezone.utc), datetime(2026, 11, 3, tzinfo=timezone.utc)
    inp = CheckAvailabilityInput(date_from=a, date_to=b)
    assert (inp.date_from, inp.date_to) == (a, b)
