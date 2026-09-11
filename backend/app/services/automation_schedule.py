"""The Automation Engine's SCHEDULE trigger — validation, due-detection,
and next-run calculation. Deliberately bounded, like automation_condition.py:
no cron syntax, no arbitrary expressions — only DAILY and WEEKLY(+weekdays)
at a fixed tenant-local time-of-day, which is what the frontend's own
schedule editor exposes (Rule 5/15: don't offer a syntax the UI can't build
or the backend can't validate safely).

Timezone handling (Rule 4): a schedule's "time" is always interpreted in
the tenant's own Organization.timezone, never the server's. Missed-schedule
policy (Rule 8): "fire at most once per tenant-local calendar date, whenever
the next tick after the target time happens" — a scheduler outage never
causes a double-fire (idempotency is enforced by AutomationService via a
deterministic per-occurrence key — see check_and_dispatch_scheduled), and a
late tick still fires the day's occurrence exactly once rather than
silently dropping it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FREQUENCIES = frozenset({"DAILY", "WEEKLY"})


class InvalidScheduleError(Exception):
    pass


def validate_schedule_config(trigger_config: dict) -> None:
    """Validates the automation's own trigger_config shape only — never the
    tenant's Organization.timezone (that's a separate, tenant-settings-level
    concern validated by validate_timezone below; an automation's schedule
    config carries no timezone of its own, it always resolves against
    whatever the tenant's Organization.timezone is at dispatch time)."""
    frequency = trigger_config.get("frequency")
    if frequency not in FREQUENCIES:
        raise InvalidScheduleError(f"trigger_config.frequency must be one of {sorted(FREQUENCIES)}")

    time_str = trigger_config.get("time")
    if not isinstance(time_str, str):
        raise InvalidScheduleError("trigger_config.time is required (HH:MM, 24-hour, tenant-local)")
    _parse_time(time_str)

    if frequency == "WEEKLY":
        weekdays = trigger_config.get("weekdays")
        if not isinstance(weekdays, list) or not weekdays:
            raise InvalidScheduleError("trigger_config.weekdays is required for WEEKLY (list of 0=Mon..6=Sun)")
        if len(weekdays) > 7 or len(set(weekdays)) != len(weekdays):
            raise InvalidScheduleError("trigger_config.weekdays must not contain duplicates")
        for w in weekdays:
            if not isinstance(w, int) or isinstance(w, bool) or not (0 <= w <= 6):
                raise InvalidScheduleError("trigger_config.weekdays entries must be integers 0-6")


def validate_timezone(timezone_name: str) -> None:
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise InvalidScheduleError(f"invalid timezone: {timezone_name!r}") from exc


def _parse_time(time_str: str) -> tuple[int, int]:
    parts = time_str.split(":")
    if len(parts) != 2:
        raise InvalidScheduleError("trigger_config.time must be HH:MM")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise InvalidScheduleError("trigger_config.time must be HH:MM with integer parts") from exc
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        raise InvalidScheduleError("trigger_config.time out of range")
    return hour, minute


def _resolve_tz(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


@dataclass
class DueCheck:
    is_due: bool
    local_now: datetime
    occurrence_date: date


def check_due(trigger_config: dict, timezone_name: str, *, now_utc: datetime) -> DueCheck:
    """Is this schedule due, evaluated against the tenant's local clock?
    `now_utc` is passed in (never read from the system clock internally) so
    this stays a pure, deterministically testable function — the caller
    (AutomationService.check_and_dispatch_scheduled) supplies the real
    current time in production and a fixed time in tests."""
    tz = _resolve_tz(timezone_name)
    local_now = now_utc.astimezone(tz)
    hour, minute = _parse_time(trigger_config.get("time", "00:00"))
    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if trigger_config.get("frequency") == "WEEKLY":
        weekdays = trigger_config.get("weekdays") or []
        if local_now.weekday() not in weekdays:
            return DueCheck(is_due=False, local_now=local_now, occurrence_date=local_now.date())

    return DueCheck(is_due=local_now >= target, local_now=local_now, occurrence_date=local_now.date())


def compute_next_run(trigger_config: dict, timezone_name: str, *, now_utc: datetime) -> datetime:
    """For display only (Rule 3/21) — never persisted, always recomputed
    from the current version's trigger_config and the tenant's timezone."""
    tz = _resolve_tz(timezone_name)
    local_now = now_utc.astimezone(tz)
    hour, minute = _parse_time(trigger_config.get("time", "00:00"))
    frequency = trigger_config.get("frequency")
    weekdays = trigger_config.get("weekdays") if frequency == "WEEKLY" else None

    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)

    if weekdays:
        for _ in range(8):
            if candidate.weekday() in weekdays:
                break
            candidate += timedelta(days=1)

    return candidate
