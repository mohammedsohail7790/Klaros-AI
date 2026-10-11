"""Recovery of Halla webhook events left in RECEIVED by a crashed worker: an atomic lease/claim on the event row. A stale RECEIVED event is reclaimed
exactly once; a fresh one (another worker still owns it) and a PROCESSED one are duplicates; a FAILED one is retryable; a late worker can never
regress a finished event. SQLite + in-process HTTP (the claim is a single compare-and-set UPDATE, also exercised on real PostgreSQL in CI)."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.integration import WebhookEvent, WebhookProcessingStatus
from app.services.halla_event_processor import HallaEventProcessor
from tests.test_halla_integration import HALLA_TENANT, _body, _connected, _deliver, _lead, _lead_state, halla  # noqa: F401


async def _row(tid, eid) -> WebhookEvent:
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return (await s.execute(select(WebhookEvent).where(WebhookEvent.external_event_id == f"{tid}:{eid}"))).scalar_one()


async def _age(tid, eid, seconds: int) -> None:
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        await s.execute(update(WebhookEvent).where(WebhookEvent.external_event_id == f"{tid}:{eid}").values(updated_at=datetime.now(timezone.utc) - timedelta(seconds=seconds)))
        await s.commit()


async def _plant(tid, eid, etype, status, age_seconds) -> None:
    """The state a crash leaves behind: the event row exists, nothing was applied."""
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        s.add(WebhookEvent(provider="halla", external_event_id=f"{tid}:{eid}", event_type=etype, tenant_id=tid, status=status,
                           raw_payload={"id": eid, "type": etype, "timestamp": None, "tenant_id": HALLA_TENANT}))
        await s.commit()
    await _age(tid, eid, age_seconds)


def _counting(monkeypatch):
    calls = {"n": 0}
    real = HallaEventProcessor.process

    async def wrapped(self, *a, **k):
        calls["n"] += 1
        return await real(self, *a, **k)

    monkeypatch.setattr(HallaEventProcessor, "process", wrapped)
    return calls


async def test_an_event_stranded_in_received_after_a_crash_is_reclaimed_and_applied_once(client, halla, monkeypatch) -> None:  # noqa: F811
    token, tid = await _connected(client, "Rec Crash", "reccrash@example.com")
    lead = await _lead(client, token)
    calls = _counting(monkeypatch)
    eid = "evt-crash-1"
    await _plant(tid, eid, "lead.qualified", WebhookProcessingStatus.RECEIVED, age_seconds=get_settings().HALLA_WEBHOOK_LEASE_SECONDS + 60)
    raw = _body("lead.qualified", eid=eid, data={"klaros_lead_id": lead, "qualification": "qualified"})
    assert (await _deliver(client, tid, raw)).json() == {"status": "ok"}
    assert calls["n"] == 1 and await _lead_state(client, token, lead) == ("QUALIFIED", "QUALIFIED")
    assert (await _row(tid, eid)).status == WebhookProcessingStatus.PROCESSED
    assert (await _deliver(client, tid, raw)).json() == {"status": "duplicate_ignored"} and calls["n"] == 1    # a processed event is never replayed


async def test_a_received_event_whose_lease_has_not_expired_belongs_to_another_worker(client, halla, monkeypatch) -> None:  # noqa: F811
    token, tid = await _connected(client, "Rec Fresh", "recfresh@example.com")
    lead = await _lead(client, token)
    calls = _counting(monkeypatch)
    eid = "evt-fresh-1"
    await _plant(tid, eid, "lead.qualified", WebhookProcessingStatus.RECEIVED, age_seconds=5)
    raw = _body("lead.qualified", eid=eid, data={"klaros_lead_id": lead, "qualification": "qualified"})
    assert (await _deliver(client, tid, raw)).json() == {"status": "duplicate_ignored"}
    assert calls["n"] == 0 and await _lead_state(client, token, lead) == ("NEW", "PENDING")
    assert (await _row(tid, eid)).status == WebhookProcessingStatus.RECEIVED               # untouched; its owner may still finish
    await _age(tid, eid, get_settings().HALLA_WEBHOOK_LEASE_SECONDS + 5)                   # now the lease has genuinely expired
    assert (await _deliver(client, tid, raw)).json() == {"status": "ok"} and calls["n"] == 1


async def test_two_simultaneous_deliveries_of_a_stranded_event_process_it_exactly_once(client, halla, monkeypatch) -> None:  # noqa: F811
    token, tid = await _connected(client, "Rec Race", "recrace@example.com")
    lead = await _lead(client, token)
    calls = _counting(monkeypatch)
    eid = "evt-race-1"
    await _plant(tid, eid, "lead.qualified", WebhookProcessingStatus.RECEIVED, age_seconds=get_settings().HALLA_WEBHOOK_LEASE_SECONDS + 60)
    raw = _body("lead.qualified", eid=eid, data={"klaros_lead_id": lead, "qualification": "qualified"})
    results = await asyncio.gather(*[_deliver(client, tid, raw) for _ in range(6)])
    bodies = sorted(r.json()["status"] for r in results)
    assert bodies.count("ok") == 1 and bodies.count("duplicate_ignored") == 5, bodies
    assert calls["n"] == 1


async def test_a_failed_event_is_retried_and_a_retry_failure_stays_failed_and_visible(client, halla, monkeypatch) -> None:  # noqa: F811
    token, tid = await _connected(client, "Rec Fail", "recfail@example.com")
    lead = await _lead(client, token)
    real = HallaEventProcessor.process
    state = {"fail": True, "n": 0}

    async def flaky(self, *a, **k):
        state["n"] += 1
        if state["fail"]:
            raise RuntimeError("boom with no patient data")
        return await real(self, *a, **k)

    monkeypatch.setattr(HallaEventProcessor, "process", flaky)
    eid = "evt-fail-1"
    raw = _body("lead.qualified", eid=eid, data={"klaros_lead_id": lead, "qualification": "qualified"})
    r = await _deliver(client, tid, raw)
    assert r.status_code == 500 and r.json() == {"status": "processing_failed"}
    row = await _row(tid, eid)
    assert row.status == WebhookProcessingStatus.FAILED and row.error_detail == "RuntimeError" and "patient" not in (row.error_detail or "")
    assert (await _deliver(client, tid, raw)).status_code == 500                       # retried (and failed again), not swallowed as a duplicate
    assert state["n"] == 2 and (await _row(tid, eid)).status == WebhookProcessingStatus.FAILED
    state["fail"] = False
    assert (await _deliver(client, tid, raw)).json() == {"status": "ok"}
    assert (await _row(tid, eid)).status == WebhookProcessingStatus.PROCESSED and await _lead_state(client, token, lead) == ("QUALIFIED", "QUALIFIED")


async def test_a_worker_that_lost_its_lease_cannot_regress_a_finished_event(client, halla, monkeypatch) -> None:  # noqa: F811
    token, tid = await _connected(client, "Rec Late", "reclate@example.com")
    lead = await _lead(client, token)
    real = HallaEventProcessor.process
    entered, release, order = asyncio.Event(), asyncio.Event(), []

    async def slow_then_crash(self, *a, **k):
        order.append("first-started")
        entered.set()
        await release.wait()
        raise RuntimeError("the first worker finally fails")

    eid = "evt-late-1"
    raw = _body("lead.qualified", eid=eid, data={"klaros_lead_id": lead, "qualification": "qualified"})
    monkeypatch.setattr(HallaEventProcessor, "process", slow_then_crash)
    first = asyncio.create_task(_deliver(client, tid, raw))
    await entered.wait()
    await _age(tid, eid, get_settings().HALLA_WEBHOOK_LEASE_SECONDS + 60)              # the first worker is presumed dead
    monkeypatch.setattr(HallaEventProcessor, "process", real)
    assert (await _deliver(client, tid, raw)).json() == {"status": "ok"}               # a second worker reclaims and finishes it
    release.set()
    await first                                                                        # the late worker now fails
    assert (await _row(tid, eid)).status == WebhookProcessingStatus.PROCESSED          # and cannot undo the finished result
    assert await _lead_state(client, token, lead) == ("QUALIFIED", "QUALIFIED")


async def test_recovery_is_tenant_scoped_and_signature_is_still_required(client, halla, monkeypatch) -> None:  # noqa: F811
    token, tid = await _connected(client, "Rec Scope", "recscope@example.com")
    lead = await _lead(client, token)
    calls = _counting(monkeypatch)
    eid = "evt-scope-1"
    await _plant(tid, eid, "lead.qualified", WebhookProcessingStatus.RECEIVED, age_seconds=get_settings().HALLA_WEBHOOK_LEASE_SECONDS + 60)
    raw = _body("lead.qualified", eid=eid, data={"klaros_lead_id": lead, "qualification": "qualified"})
    assert (await _deliver(client, tid, raw, sig="bad")).status_code == 401           # a stranded event is not reclaimed by an unauthenticated request
    assert (await _deliver(client, tid, _body("lead.qualified", tenant="another-halla-tenant", eid=eid, data={"klaros_lead_id": lead}))).status_code == 403
    assert calls["n"] == 0 and (await _row(tid, eid)).status == WebhookProcessingStatus.RECEIVED
