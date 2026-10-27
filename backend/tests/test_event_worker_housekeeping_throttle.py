"""The worker's database housekeeping (stuck-event check and on_tick hooks) runs at most once per housekeeping interval, not on every poll."""
import pytest

from app.events import worker as worker_module
from app.events.bus import ProcessingStats
from app.events.worker import EventWorker


class _Bus:
    def __init__(self) -> None:
        self.reconciles = 0
        self.transport = self
        self.polls = 0

    async def reconcile_stuck_events(self) -> int:
        self.reconciles += 1
        return 0

    async def set_heartbeat(self, *_a) -> None:
        return None

    def subscribed_event_types(self):
        return ["x"]

    async def process_pending(self, event_type, count=10):
        self.polls += 1

        return ProcessingStats()


@pytest.mark.asyncio
async def test_housekeeping_runs_once_per_interval_but_events_are_polled_every_tick(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(worker_module.time, "monotonic", lambda: clock["t"])
    hook_calls = []

    async def hook():
        hook_calls.append(clock["t"])

    bus = _Bus()
    w = EventWorker(bus, on_tick=hook, housekeeping_interval_seconds=30.0)
    for _ in range(5):  # five polls one second apart
        await w.tick()
        clock["t"] += 1.0
    assert bus.polls == 5            # events are still read on every tick
    assert bus.reconciles == 1       # housekeeping ran once (the first tick)
    assert len(hook_calls) == 1
    clock["t"] += 30.0
    await w.tick()
    assert bus.reconciles == 2 and len(hook_calls) == 2   # due again after the interval


@pytest.mark.asyncio
async def test_zero_interval_keeps_the_old_every_tick_behaviour():
    hook_calls = []

    async def hook():
        hook_calls.append(1)

    bus = _Bus()
    w = EventWorker(bus, on_tick=hook)
    for _ in range(3):
        await w.tick()
    assert bus.reconciles == 3 and len(hook_calls) == 3
