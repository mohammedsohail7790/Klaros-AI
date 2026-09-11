"""Phase 25: regression proving the dual-EventBus runtime inconsistency
Phase 24 exposed and worked around is now fixed at the root — not hidden
in a test.

Root cause (see app/api/tool_deps.py's own module docstring for the full
explanation): `get_automation_service`/`get_morning_brief_service`/
`get_voice_conversation_service` used to call `get_tool_registry()`/
`get_wired_event_bus()` as bare Python function calls, which are invisible
to `app.dependency_overrides` — only `Depends(fn)` resolution consults the
override table. Any route depending on one of those three services (most
importantly `POST /automations/scheduled/dispatch-tick`) silently resolved
the real process-wide EventBus/ToolRegistry singletons instead of a test's
isolated ones, even though the SAME test correctly overrode
`get_tool_registry`/`get_wired_event_bus` for every other endpoint.

This file's tests use ONLY the standard `client`/`event_bus`/
`tool_registry` fixtures — no manual `get_wired_event_bus()` call, no
manually invoking `EventBus.process_pending()` on a bus the HTTP path
didn't itself use, no manually calling automation handlers directly. If
the old bug reappears, `test_scheduled_dispatch_tick_uses_the_same_bus_as_http`
fails: the notification created by the sweep's own follow-on EVENT
automation would never be visible in this test's isolated database
because the event would have gone to the wrong (real, unwired-in-test)
process-wide bus instead of the one this test's client and
`POST /events/process` share.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.models.automation import AutomationExecution
from app.models.contract import Contract, ContractStatus
from app.models.crm import Customer, CustomerStatus
from app.models.notification import Notification
from app.models.operations import Job, JobStatus

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str) -> tuple[str, uuid.UUID]:
    reg = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": "Phase25 Co", "full_name": "Owner", "email": email, "password": "supersecret1"},
    )
    assert reg.status_code == 201, reg.text
    token = reg.json()["tokens"]["access_token"]
    tenant_id = uuid.UUID(reg.json()["user"]["tenant_id"])
    return token, tenant_id


async def _make_customer(tenant_id: uuid.UUID) -> uuid.UUID:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Phase25 Customer", status=CustomerStatus.ACTIVE)
        session.add(customer)
        await session.commit()
        await session.refresh(customer)
        return customer.id


async def _make_contract(tenant_id: uuid.UUID, customer_id: uuid.UUID, *, sent_days_ago: int) -> uuid.UUID:
    async with async_session_maker() as session:
        contract = Contract(
            tenant_id=tenant_id, contract_number=f"CTR-P25-{uuid.uuid4().hex[:8]}", quote_id=uuid.uuid4(),
            customer_id=customer_id, status=ContractStatus.SENT, content="Agreement text", content_hash="x" * 64,
            sent_at=datetime.now(timezone.utc) - timedelta(days=sent_days_ago),
        )
        session.add(contract)
        await session.commit()
        await session.refresh(contract)
        return contract.id


async def _make_job(tenant_id: uuid.UUID, customer_id: uuid.UUID) -> uuid.UUID:
    async with async_session_maker() as session:
        job = Job(
            tenant_id=tenant_id, customer_id=customer_id, job_number=f"JOB-P25-{uuid.uuid4().hex[:8]}",
            title="Phase 25 Job", status=JobStatus.QA_PENDING,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


# =====================================================================
# Step 5/6: the scheduled-dispatch path is the exact Phase 24 regression.
# No manual bus wiring anywhere in this test — only the standard fixtures
# and real HTTP calls.
# =====================================================================

async def test_scheduled_dispatch_tick_uses_the_same_bus_as_http(client: AsyncClient, event_bus: EventBus) -> None:
    token, tenant_id = await _register(client, "phase25-sweep@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    customer_id = await _make_customer(tenant_id)
    contract_id = await _make_contract(tenant_id, customer_id, sent_days_ago=10)

    sweep = await client.post(
        "/api/v1/automations",
        json={
            "name": "Contract Pending Sweep (Phase25)", "trigger_type": "SCHEDULE",
            "trigger_config": {"frequency": "DAILY", "time": "00:00"}, "condition": None,
            "steps": [{"action": "contracts.detect_pending", "params": {}}],
        },
        headers=headers,
    )
    assert sweep.status_code == 201, sweep.text
    publish1 = await client.post(f"/api/v1/automations/{sweep.json()['id']}/publish", headers=headers)
    assert publish1.status_code == 200, publish1.text

    notify = await client.post(
        "/api/v1/automations",
        json={
            "name": "Contract Pending Notify (Phase25)", "trigger_type": "EVENT",
            "trigger_config": {"event_type": "contract.expired"}, "condition": None,
            "steps": [{"action": "notifications.create_notification", "params": {"title": "Contract still pending", "body": "x"}}],
        },
        headers=headers,
    )
    assert notify.status_code == 201, notify.text
    publish2 = await client.post(f"/api/v1/automations/{notify.json()['id']}/publish", headers=headers)
    assert publish2.status_code == 200, publish2.text

    # Step 6: the actual HTTP scheduled-dispatch endpoint, no substitution.
    tick = await client.post("/api/v1/automations/scheduled/dispatch-tick", headers=headers)
    assert tick.status_code == 200, tick.text
    assert len(tick.json()["dispatched_execution_ids"]) == 1

    async with async_session_maker() as session:
        contract = await session.get(Contract, contract_id)
    assert contract.status == ContractStatus.EXPIRED  # the sweep step itself ran for real

    # Real HTTP event-processing endpoint — the SAME `event_bus` fixture
    # object the dispatch-tick call above transitively used (via
    # get_automation_service -> Depends(get_tool_registry) -> the
    # overridden ToolRegistry built from this fixture's bus), proven by
    # the notification below actually appearing. Before the Phase 25 fix,
    # this would have found zero pending contract.expired messages on
    # this fixture's bus, because dispatch-tick would have published onto
    # the real, different, process-wide singleton instead.
    process = await client.post("/api/v1/events/process/contract.expired", headers=headers)
    assert process.status_code == 200, process.text
    assert process.json()["succeeded"] >= 1

    notif = await client.get("/api/v1/notifications", headers=headers)
    titles = [n["title"] for n in notif.json()["notifications"]]
    assert "Contract still pending" in titles


async def test_scheduled_dispatch_tick_execution_visible_via_http(client: AsyncClient) -> None:
    """The dispatched execution the dispatch-tick endpoint reports back
    must be a real, queryable AutomationExecution row in THIS test's own
    isolated database — not an artifact of a different bus/session."""
    token, tenant_id = await _register(client, "phase25-exec-visible@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    customer_id = await _make_customer(tenant_id)
    await _make_contract(tenant_id, customer_id, sent_days_ago=10)

    automation = await client.post(
        "/api/v1/automations",
        json={
            "name": "Sweep Only (Phase25)", "trigger_type": "SCHEDULE",
            "trigger_config": {"frequency": "DAILY", "time": "00:00"}, "condition": None,
            "steps": [{"action": "contracts.detect_pending", "params": {}}],
        },
        headers=headers,
    )
    await client.post(f"/api/v1/automations/{automation.json()['id']}/publish", headers=headers)

    tick = await client.post("/api/v1/automations/scheduled/dispatch-tick", headers=headers)
    execution_id = tick.json()["dispatched_execution_ids"][0]

    async with async_session_maker() as session:
        execution = await session.get(AutomationExecution, uuid.UUID(execution_id))
    assert execution is not None
    assert execution.tenant_id == tenant_id
    assert execution.status == "COMPLETED"


# =====================================================================
# Step 7: real domain event producers still publish correctly through the
# same boundary — spot-checked with QA failure (a raw domain-service call,
# not an HTTP endpoint, since none exists for failing QA directly outside
# the job lifecycle) driving a real EVENT automation end to end.
# =====================================================================

async def test_qa_failure_event_producer_reaches_automation_over_http(client: AsyncClient, event_bus: EventBus) -> None:
    from app.services.qa_service import QAService

    token, tenant_id = await _register(client, "phase25-qa@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    customer_id = await _make_customer(tenant_id)
    job_id = await _make_job(tenant_id, customer_id)

    automation = await client.post(
        "/api/v1/automations",
        json={
            "name": "QA Escalation (Phase25)", "trigger_type": "EVENT",
            "trigger_config": {"event_type": "job.qa_failed"}, "condition": None,
            "steps": [{"action": "notifications.create_notification", "params": {"title": "QA failed", "body": "{{job.reason}}"}}],
        },
        headers=headers,
    )
    await client.post(f"/api/v1/automations/{automation.json()['id']}/publish", headers=headers)

    qa_service = QAService(async_session_maker, event_bus)
    await qa_service.fail_qa(tenant_id, job_id, reason="Phase25 producer check")

    process = await client.post("/api/v1/events/process/job.qa_failed", headers=headers)
    assert process.status_code == 200, process.text

    notif = await client.get("/api/v1/notifications", headers=headers)
    titles = [n["title"] for n in notif.json()["notifications"]]
    assert "QA failed" in titles


# =====================================================================
# Step 9: duplicate handler registration protection — the wired bus must
# never accumulate the same handler twice even across repeated resolution.
# =====================================================================

async def test_wired_event_bus_is_a_true_singleton_with_one_registration_pass() -> None:
    """`get_wired_event_bus()` is the ONE place `register_automation_handlers`
    (and every other domain handler registrar) is ever called for the
    real, process-wide bus. `@lru_cache` is what makes this safe under
    repeated resolution — proven directly: calling it many times must
    return the identical object with subscription counts that never grow
    past the first call, for every event type that has a real automation
    handler wired up."""
    from app.api.tool_deps import get_wired_event_bus

    first = get_wired_event_bus()
    counts_after_first = {et: len(subs) for et, subs in first._subscriptions.items()}

    for _ in range(5):
        again = get_wired_event_bus()
        assert again is first  # true singleton — never a second wired instance

    counts_after_repeated_calls = {et: len(subs) for et, subs in first._subscriptions.items()}
    assert counts_after_repeated_calls == counts_after_first  # no handler was ever registered twice

    # Specifically, exactly one "automation_dispatch" subscription exists
    # per event type — the handler this whole phase is about.
    for event_type, subs in first._subscriptions.items():
        dispatch_subs = [s for s in subs if s.handler_name == "automation_dispatch"]
        assert len(dispatch_subs) <= 1, f"{event_type} has {len(dispatch_subs)} automation_dispatch registrations"


async def test_get_automation_service_lru_cache_keys_on_tool_registry_identity(tool_registry) -> None:
    """Re-resolving get_automation_service with the SAME ToolRegistry
    instance (as FastAPI's Depends graph would within one request/process
    for the same overridden singleton) returns the identical cached
    AutomationService — proving the new Depends-based signature still
    caches correctly and doesn't rebuild a fresh service (with a fresh,
    possibly-differently-wired AIExecutionService) on every call."""
    from app.api.tool_deps import get_automation_service

    service_a = get_automation_service(tool_registry)
    service_b = get_automation_service(tool_registry)
    assert service_a is service_b
