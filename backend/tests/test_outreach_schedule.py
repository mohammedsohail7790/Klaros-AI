from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.clinic_outreach import OutreachMessageKind
from app.services import outreach_schedule as s
from app.services.mt_clinic_sourcing import COUNTRY_NAMES
from app.services.outreach_settings import Limits

L = Limits()
FAR = datetime(2030, 1, 1, tzinfo=UTC)


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def test_timezone_lookup_known_and_unknown():
    assert s.timezone_for("TR") == ZoneInfo("Europe/Istanbul")
    assert s.timezone_for(" th ") == ZoneInfo("Asia/Bangkok")
    assert s.timezone_for("kr") == ZoneInfo("Asia/Seoul")
    assert s.timezone_for("CN") == ZoneInfo("Asia/Shanghai")
    assert s.timezone_for(None) is None
    assert s.timezone_for("") is None
    assert s.timezone_for("ZZ") is None


def test_warmup_cap_day_zero_one_and_capped():
    start = utc(2026, 10, 1, 12)
    assert s.warmup_cap(L, start, start) == 5
    assert s.warmup_cap(L, start, start + timedelta(days=1)) == 7
    assert s.warmup_cap(L, start, start + timedelta(days=100)) == L.daily_cap


def test_warmup_cap_uses_calendar_days_never_negative_and_none():
    start = utc(2026, 10, 1, 23)
    assert s.warmup_cap(L, start, utc(2026, 10, 2, 1)) == 7
    assert s.warmup_cap(L, start, utc(2026, 9, 1)) == 5
    assert s.warmup_cap(L, None, utc(2026, 10, 1)) == 5


def test_slot_inside_window_is_kept():
    t = utc(2026, 10, 21, 8)  # Wed, Istanbul 11:00
    assert s.next_send_slot(t, "TR", L, horizon_end=FAR) == t


def test_slot_before_window_moves_to_window_start():
    t = utc(2026, 10, 21, 3)  # Istanbul 06:00
    assert s.next_send_slot(t, "TR", L, horizon_end=FAR) == utc(2026, 10, 21, 6)


def test_slot_after_window_moves_to_next_allowed_day():
    t = utc(2026, 10, 21, 15)  # Istanbul 18:00 Wed
    assert s.next_send_slot(t, "TR", L, horizon_end=FAR) == utc(2026, 10, 22, 6)
    t = utc(2026, 10, 23, 15)  # Friday evening -> Monday
    assert s.next_send_slot(t, "TR", L, horizon_end=FAR) == utc(2026, 10, 26, 6)
    t = utc(2026, 10, 21, 14)  # exactly 17:00 local: end is exclusive
    assert s.next_send_slot(t, "TR", L, horizon_end=FAR) == utc(2026, 10, 22, 6)


def test_weekend_is_skipped_when_weekdays_only_and_kept_when_not():
    t = utc(2026, 10, 24, 8)  # Saturday
    assert s.next_send_slot(t, "TR", L, horizon_end=FAR) == utc(2026, 10, 26, 6)
    off = Limits(weekdays_only=False)
    assert s.next_send_slot(t, "TR", off, horizon_end=FAR) == t


def test_slot_uses_the_clinics_local_time_across_zones():
    t = utc(2026, 10, 21, 1)  # Seoul 10:00, Istanbul 04:00
    assert s.next_send_slot(t, "KR", L, horizon_end=FAR) == t
    assert s.next_send_slot(t, "TR", L, horizon_end=FAR) == utc(2026, 10, 21, 6)


def test_a_dst_boundary_uses_local_wall_time():
    assert s.timezone_for("PL") == ZoneInfo("Europe/Warsaw")
    before = s.next_send_slot(utc(2026, 3, 27, 0), "PL", L, horizon_end=FAR)  # Fri, CET
    after = s.next_send_slot(utc(2026, 3, 29, 23), "PL", L, horizon_end=FAR)  # Sun -> Mon, CEST
    assert before == utc(2026, 3, 27, 8)
    assert after == utc(2026, 3, 30, 7)


def test_spring_forward_gap_moves_to_next_valid_instant():
    lim = Limits(send_window_start=2, send_window_end=5, weekdays_only=False)
    got = s.next_send_slot(utc(2026, 3, 29, 0), "PL", lim, horizon_end=FAR)
    assert got == utc(2026, 3, 29, 1)  # 03:00 CEST, first valid instant


def test_fall_back_ambiguity_uses_first_occurrence():
    lim = Limits(send_window_start=2, send_window_end=5, weekdays_only=False)
    got = s.next_send_slot(utc(2026, 10, 24, 23, 30), "PL", lim, horizon_end=FAR)
    assert got == utc(2026, 10, 25, 0)  # 02:00 CEST, not 02:00 CET


def test_window_end_24_means_midnight():
    lim = Limits(send_window_start=9, send_window_end=24, weekdays_only=False)
    t = utc(2026, 10, 21, 20)  # Istanbul 23:00
    assert s.next_send_slot(t, "TR", lim, horizon_end=FAR) == t
    t = utc(2026, 10, 21, 21)  # Istanbul midnight
    assert s.next_send_slot(t, "TR", lim, horizon_end=FAR) == utc(2026, 10, 22, 6)


def test_slot_beyond_horizon_is_none():
    t = utc(2026, 10, 21, 15)
    assert s.next_send_slot(t, "TR", L, horizon_end=utc(2026, 10, 22, 6)) is None
    assert s.next_send_slot(t, "TR", L, horizon_end=utc(2026, 10, 22, 7)) == utc(2026, 10, 22, 6)


def test_unknown_country_is_none():
    assert s.next_send_slot(utc(2026, 10, 21, 8), "ZZ", L, horizon_end=FAR) is None
    assert s.next_send_slot(utc(2026, 10, 21, 8), None, L, horizon_end=FAR) is None


def test_naive_datetimes_raise():
    with pytest.raises(ValueError):
        s.next_send_slot(datetime(2026, 10, 21, 8), "TR", L, horizon_end=FAR)
    with pytest.raises(ValueError):
        s.next_send_slot(utc(2026, 10, 21, 8), "TR", L, horizon_end=datetime(2030, 1, 1))


def test_every_seeded_provider_country_has_a_timezone():
    # vertical_extension_seed.py has no provider countries; the importable country list is
    # mt_clinic_sourcing.COUNTRY_NAMES (every country clinics can be sourced for).
    missing = [c for c in COUNTRY_NAMES if s.timezone_for(c) is None]
    assert not missing
    assert {"TR", "TH", "KR", "CN"} <= set(s.COUNTRY_TIMEZONES)


def test_followup_constants():
    assert s.FOLLOWUP_GAPS == {
        OutreachMessageKind.FOLLOWUP_1: timedelta(days=4),
        OutreachMessageKind.FOLLOWUP_2: timedelta(days=5),
    }
    assert s.NO_RESPONSE_AFTER == timedelta(days=7)
