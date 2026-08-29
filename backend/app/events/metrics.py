"""Minimal in-process metrics for the event worker (section: event metrics).

No Prometheus/OpenTelemetry dependency exists in this repository, so this is
a deliberately small, dependency-free counter/gauge registry — real numbers
computed from real processing outcomes, not a fabricated dashboard. It lives
in the worker process's memory: restart the worker and counters reset to
zero, same as any other in-process counter. Point-in-time state (queue
depth, dead-letter count) is always read fresh from the database via
`EventBus`/transport, never cached here.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class EventWorkerMetrics:
    events_processed: int = 0
    events_failed: int = 0
    events_retried: int = 0
    events_dead_lettered: int = 0
    events_deduplicated: int = 0
    ticks: int = 0
    started_at: datetime | None = None
    last_tick_at: datetime | None = None
    last_tick_duration_ms: float | None = None
    total_handler_duration_ms: float = 0.0
    per_event_type: dict[str, dict[str, int]] = field(default_factory=dict)

    def record_start(self) -> None:
        self.started_at = datetime.now(timezone.utc)

    def record_tick(self, event_type: str, stats, duration_ms: float) -> None:
        self.ticks += 1
        self.last_tick_at = datetime.now(timezone.utc)
        self.last_tick_duration_ms = duration_ms
        self.total_handler_duration_ms += duration_ms

        self.events_processed += stats.succeeded
        self.events_failed += stats.failed_retrying
        self.events_dead_lettered += stats.dead_lettered
        self.events_deduplicated += stats.duplicates_skipped
        if stats.failed_retrying:
            self.events_retried += stats.failed_retrying

        bucket = self.per_event_type.setdefault(
            event_type,
            {"read": 0, "succeeded": 0, "failed_retrying": 0, "dead_lettered": 0, "duplicates_skipped": 0},
        )
        bucket["read"] += stats.read
        bucket["succeeded"] += stats.succeeded
        bucket["failed_retrying"] += stats.failed_retrying
        bucket["dead_lettered"] += stats.dead_lettered
        bucket["duplicates_skipped"] += stats.duplicates_skipped

    def snapshot(self) -> dict:
        return {
            "events_processed": self.events_processed,
            "events_failed": self.events_failed,
            "events_retried": self.events_retried,
            "events_dead_lettered": self.events_dead_lettered,
            "events_deduplicated": self.events_deduplicated,
            "ticks": self.ticks,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "last_tick_duration_ms": self.last_tick_duration_ms,
            "per_event_type": self.per_event_type,
        }


# Process-wide singleton — one worker per process in this architecture (the
# API process runs at most one in-process worker task in dev/memory-transport
# mode; a real deployment runs a dedicated `event-worker` process against
# Redis, each with its own metrics instance, same as any horizontally-scaled
# consumer).
worker_metrics = EventWorkerMetrics()
