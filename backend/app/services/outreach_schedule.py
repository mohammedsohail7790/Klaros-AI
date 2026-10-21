"""Send-slot and warm-up arithmetic for clinic outreach. Pure: no I/O."""
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models.clinic_outreach import OutreachMessageKind
from app.services.outreach_settings import Limits

# Every country clinics can be sourced for (mt_clinic_sourcing.COUNTRY_NAMES) plus CN.
COUNTRY_TIMEZONES: dict[str, str] = {
    "TR": "Europe/Istanbul",
    "TH": "Asia/Bangkok",
    "KR": "Asia/Seoul",
    "CN": "Asia/Shanghai",
    "IN": "Asia/Kolkata",
    "AE": "Asia/Dubai",
    "MX": "America/Mexico_City",  # Mexico spans several zones; capital's zone
    "MY": "Asia/Kuala_Lumpur",
    "PL": "Europe/Warsaw",
    "HU": "Europe/Budapest",
    "CZ": "Europe/Prague",
    "ES": "Europe/Madrid",  # mainland/capital zone (Canary Islands differ)
    "DE": "Europe/Berlin",
    "CR": "America/Costa_Rica",
    "CO": "America/Bogota",
    "SG": "Asia/Singapore",
}

# Gap AFTER the intro before follow-up 1; gap AFTER follow-up 1 before follow-up 2.
FOLLOWUP_GAPS: dict[OutreachMessageKind, timedelta] = {
    OutreachMessageKind.FOLLOWUP_1: timedelta(days=4),  # after the intro
    OutreachMessageKind.FOLLOWUP_2: timedelta(days=5),  # after follow-up 1
}
NO_RESPONSE_AFTER = timedelta(days=7)  # after follow-up 2


def timezone_for(country: str | None) -> ZoneInfo | None:
    if not country:
        return None
    name = COUNTRY_TIMEZONES.get(country.strip().upper())
    return ZoneInfo(name) if name else None


def warmup_cap(limits: Limits, shadow_started_at: datetime | None, now: datetime) -> int:
    n = 0
    if shadow_started_at is not None:
        n = max(0, (now.astimezone(UTC).date() - shadow_started_at.astimezone(UTC).date()).days)
    return min(limits.daily_cap, limits.ramp_start + 2 * n)


def _local_instant(day: date, hour: int, zone: ZoneInfo) -> datetime:
    """UTC instant of a local wall-clock hour. Hour 24 is the next midnight.

    fold=0 gives the first occurrence of an ambiguous time and, for a
    nonexistent (spring-forward) time, the first valid instant after the gap.
    """
    if hour == 24:
        day, hour = day + timedelta(days=1), 0
    return datetime.combine(day, time(hour), tzinfo=zone).astimezone(UTC)


def next_send_slot(
    earliest: datetime, country: str | None, limits: Limits, *, horizon_end: datetime
) -> datetime | None:
    if earliest.tzinfo is None or horizon_end.tzinfo is None:
        raise ValueError("earliest and horizon_end must be timezone-aware")
    zone = timezone_for(country)
    if zone is None:
        return None
    earliest_utc = earliest.astimezone(UTC)
    day = earliest_utc.astimezone(zone).date()
    for _ in range(15):
        if not (limits.weekdays_only and day.weekday() >= 5):
            start = _local_instant(day, limits.send_window_start, zone)
            end = _local_instant(day, limits.send_window_end, zone)
            if earliest_utc < end:
                slot = max(earliest_utc, start)
                return slot if slot < horizon_end.astimezone(UTC) else None
        day += timedelta(days=1)
    return None
