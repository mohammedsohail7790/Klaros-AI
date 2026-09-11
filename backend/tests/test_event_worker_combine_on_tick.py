"""combine_on_tick (app/events/worker.py) — composes multiple scheduler
hooks (Morning Brief, the Automation Engine's SCHEDULE dispatch) onto the
one on_tick slot EventWorker exposes, isolating each hook's failures."""

import pytest

from app.events.worker import combine_on_tick

pytestmark = pytest.mark.asyncio


async def test_calls_every_hook() -> None:
    calls: list[str] = []

    async def hook_a() -> None:
        calls.append("a")

    async def hook_b() -> None:
        calls.append("b")

    await combine_on_tick(hook_a, hook_b)()
    assert calls == ["a", "b"]


async def test_one_hook_failing_does_not_block_the_others() -> None:
    calls: list[str] = []

    async def failing_hook() -> None:
        raise RuntimeError("boom")

    async def healthy_hook() -> None:
        calls.append("healthy")

    await combine_on_tick(failing_hook, healthy_hook)()
    assert calls == ["healthy"]


async def test_empty_hooks_is_a_noop() -> None:
    await combine_on_tick()()
