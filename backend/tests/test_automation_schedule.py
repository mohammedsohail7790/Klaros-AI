from datetime import datetime, timezone

import pytest

from app.services.automation_schedule import (
    InvalidScheduleError,
    check_due,
    compute_next_run,
    validate_schedule_config,
    validate_timezone,
)

# 2026-09-04 is a Friday.
FRIDAY_08AM_UTC = datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc)


def test_valid_daily_schedule_passes() -> None:
    validate_schedule_config({"frequency": "DAILY", "time": "09:00"})


def test_valid_weekly_schedule_passes() -> None:
    validate_schedule_config({"frequency": "WEEKLY", "time": "09:00", "weekdays": [0, 2, 4]})


def test_invalid_frequency_rejected() -> None:
    with pytest.raises(InvalidScheduleError):
        validate_schedule_config({"frequency": "MONTHLY", "time": "09:00"})


def test_missing_time_rejected() -> None:
    with pytest.raises(InvalidScheduleError):
        validate_schedule_config({"frequency": "DAILY"})


def test_malformed_time_rejected() -> None:
    for bad in ["9am", "25:00", "09:60", "09", "09:00:00"]:
        with pytest.raises(InvalidScheduleError):
            validate_schedule_config({"frequency": "DAILY", "time": bad})


def test_weekly_without_weekdays_rejected() -> None:
    with pytest.raises(InvalidScheduleError):
        validate_schedule_config({"frequency": "WEEKLY", "time": "09:00"})


def test_weekly_with_out_of_range_weekday_rejected() -> None:
    with pytest.raises(InvalidScheduleError):
        validate_schedule_config({"frequency": "WEEKLY", "time": "09:00", "weekdays": [7]})


def test_weekly_with_duplicate_weekdays_rejected() -> None:
    with pytest.raises(InvalidScheduleError):
        validate_schedule_config({"frequency": "WEEKLY", "time": "09:00", "weekdays": [0, 0]})


def test_weekly_with_bool_weekday_rejected() -> None:
    """bool is a subclass of int in Python — must not silently pass."""
    with pytest.raises(InvalidScheduleError):
        validate_schedule_config({"frequency": "WEEKLY", "time": "09:00", "weekdays": [True]})


def test_invalid_timezone_rejected() -> None:
    with pytest.raises(InvalidScheduleError):
        validate_timezone("Not/A_Real_Zone")


def test_valid_timezone_passes() -> None:
    validate_timezone("America/New_York")
    validate_timezone("UTC")


def test_unbounded_weekdays_list_rejected() -> None:
    with pytest.raises(InvalidScheduleError):
        validate_schedule_config({"frequency": "WEEKLY", "time": "09:00", "weekdays": [0, 1, 2, 3, 4, 5, 6, 0]})


def test_daily_due_when_local_time_passed() -> None:
    # 08:00 UTC == 04:00 America/New_York — before the 09:00 target.
    result = check_due({"frequency": "DAILY", "time": "09:00"}, "America/New_York", now_utc=FRIDAY_08AM_UTC)
    assert result.is_due is False

    later = datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc)  # 10:00 America/New_York
    result = check_due({"frequency": "DAILY", "time": "09:00"}, "America/New_York", now_utc=later)
    assert result.is_due is True


def test_daily_due_uses_tenant_timezone_not_utc() -> None:
    """09:00 UTC must NOT be treated as 09:00 in every tenant's timezone."""
    nine_am_utc = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
    # In America/Los_Angeles this is only 02:00 — not due yet for a 09:00 local schedule.
    result = check_due({"frequency": "DAILY", "time": "09:00"}, "America/Los_Angeles", now_utc=nine_am_utc)
    assert result.is_due is False


def test_weekly_only_due_on_matching_weekday() -> None:
    later_friday = datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc)  # Friday, past 09:00 local UTC
    due_friday = check_due({"frequency": "WEEKLY", "time": "09:00", "weekdays": [4]}, "UTC", now_utc=later_friday)
    assert due_friday.is_due is True

    due_monday_only = check_due({"frequency": "WEEKLY", "time": "09:00", "weekdays": [0]}, "UTC", now_utc=later_friday)
    assert due_monday_only.is_due is False


def test_missed_schedule_still_fires_once_when_checked_late() -> None:
    """Policy A (Rule 8): a scheduler outage past the target time still
    fires the day's occurrence on the next tick, exactly once — never
    silently dropped, never fired for a stale prior day once the date
    has rolled over (that's a distinct, later occurrence)."""
    very_late = datetime(2026, 9, 4, 23, 0, tzinfo=timezone.utc)  # 09:00 UTC target, checked at 23:00
    result = check_due({"frequency": "DAILY", "time": "09:00"}, "UTC", now_utc=very_late)
    assert result.is_due is True
    assert result.occurrence_date == very_late.date()


def test_compute_next_run_daily_later_today() -> None:
    now = datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc)  # 08:00 UTC, target 09:00 UTC
    next_run = compute_next_run({"frequency": "DAILY", "time": "09:00"}, "UTC", now_utc=now)
    assert next_run.date() == now.date()
    assert next_run.hour == 9


def test_compute_next_run_daily_rolls_to_tomorrow_when_passed() -> None:
    now = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)  # past today's 09:00 target
    next_run = compute_next_run({"frequency": "DAILY", "time": "09:00"}, "UTC", now_utc=now)
    assert next_run.date() == (now.date().replace(day=now.day + 1))


def test_compute_next_run_weekly_finds_next_matching_weekday() -> None:
    now = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)  # Friday, past today's target
    # Next Monday (weekday 0) after this Friday.
    next_run = compute_next_run({"frequency": "WEEKLY", "time": "09:00", "weekdays": [0]}, "UTC", now_utc=now)
    assert next_run.weekday() == 0
    assert next_run > now
