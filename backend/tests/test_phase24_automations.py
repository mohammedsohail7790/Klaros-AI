"""Phase 24: three real, deterministic Automation Engine scenarios closing
the gaps Phase 23 identified — QA-failure escalation, contract-pending
follow-up, and referral-opportunity notification. All three reuse the
existing Automation Engine/EventBus/ToolRegistry/ActionPolicy/AuditLog
unchanged; no new orchestration, scheduler, approval, or audit system.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.ai.execution_service import AIExecutionService
from app.db.session import async_session_maker
from app.models.audit_log import AuditLog
from app.models.automation import AutomationExecution, ExecutionStatus, TriggerType
from app.models.contract import Contract, ContractStatus
from app.models.crm import Customer, CustomerStatus
from app.models.event import EventType
from app.models.notification import Notification
from app.models.operations import Job, JobStatus
from app.services.automation_service import AutomationService
from app.services.exception_service import ExceptionService
from app.services.qa_service import QAService
from app.services.retention_service import RetentionService

pytestmark = pytest.mark.asyncio


def _service(tool_registry, bus) -> AutomationService:
    return AutomationService(async_session_maker, AIExecutionService(tool_registry))


async def _make_customer(tenant_id: uuid.UUID, *, name: str = "Phase24 Customer") -> uuid.UUID:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name=name, status=CustomerStatus.ACTIVE)
        session.add(customer)
        await session.commit()
        await session.refresh(customer)
        return customer.id


async def _make_job(tenant_id: uuid.UUID, customer_id: uuid.UUID) -> uuid.UUID:
    async with async_session_maker() as session:
        job = Job(
            tenant_id=tenant_id, customer_id=customer_id, job_number=f"JOB-{uuid.uuid4().hex[:8]}",
            title="Phase 24 Job", status=JobStatus.QA_PENDING,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


async def _make_contract(tenant_id: uuid.UUID, customer_id: uuid.UUID, *, sent_days_ago: int) -> uuid.UUID:
    async with async_session_maker() as session:
        contract = Contract(
            tenant_id=tenant_id, contract_number=f"CTR-{uuid.uuid4().hex[:8]}", quote_id=uuid.uuid4(),
            customer_id=customer_id, status=ContractStatus.SENT, content="Agreement text", content_hash="x" * 64,
            sent_at=datetime.now(timezone.utc) - timedelta(days=sent_days_ago),
        )
        session.add(contract)
        await session.commit()
        await session.refresh(contract)
        return contract.id


# =====================================================================
# A. QA FAILURE ESCALATION
# =====================================================================

async def test_qa_failure_triggers_owner_notification(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry, event_bus)
    customer_id = await _make_customer(tenant_id)
    job_id = await _make_job(tenant_id, customer_id)

    automation = await service.create_automation(
        tenant_id, name="QA Failure Escalation", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": EventType.JOB_QA_FAILED}, condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "QA failed", "body": "{{job.reason}}"}}],
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)

    qa_service = QAService(async_session_maker, event_bus)
    await qa_service.fail_qa(tenant_id, job_id, reason="Roof flashing not sealed correctly")

    stats = await event_bus.process_pending(EventType.JOB_QA_FAILED)
    assert stats.succeeded >= 1

    async with async_session_maker() as session:
        executions = (
            await session.execute(select(AutomationExecution).where(AutomationExecution.automation_id == automation.id))
        ).scalars().all()
    assert len(executions) == 1
    assert executions[0].status == ExecutionStatus.COMPLETED

    async with async_session_maker() as session:
        notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id, Notification.title == "QA failed"))
        ).scalars().all()
    assert len(notifications) == 1
    assert "Roof flashing not sealed correctly" in notifications[0].body


async def test_qa_failure_audit_trail_is_real(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry, event_bus)
    customer_id = await _make_customer(tenant_id)
    job_id = await _make_job(tenant_id, customer_id)

    automation = await service.create_automation(
        tenant_id, name="QA Failure Escalation Audit", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": EventType.JOB_QA_FAILED}, condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "QA failed", "body": "{{job.reason}}"}}],
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)

    qa_service = QAService(async_session_maker, event_bus)
    await qa_service.fail_qa(tenant_id, job_id, reason="Missing permit photo")
    await event_bus.process_pending(EventType.JOB_QA_FAILED)

    async with async_session_maker() as session:
        audit_rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.tool == "notifications.create_notification")
            )
        ).scalars().all()
    assert len(audit_rows) == 1
    assert audit_rows[0].result == "success"


# =====================================================================
# B. CONTRACT PENDING FOLLOW-UP
# =====================================================================

async def test_contract_pending_sweep_and_notify(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry, event_bus)
    customer_id = await _make_customer(tenant_id)
    stale_contract_id = await _make_contract(tenant_id, customer_id, sent_days_ago=10)  # > 7-day threshold
    fresh_contract_id = await _make_contract(tenant_id, customer_id, sent_days_ago=1)  # under threshold

    sweep_automation = await service.create_automation(
        tenant_id, name="Daily Contract Pending Sweep", description=None, trigger_type=TriggerType.SCHEDULE,
        trigger_config={"frequency": "DAILY", "time": "08:00"}, condition=None,
        steps=[{"action": "contracts.detect_pending", "params": {}}], created_by=None,
    )
    await service.publish(tenant_id, sweep_automation.id)

    notify_automation = await service.create_automation(
        tenant_id, name="Notify Owner of Pending Contract", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": EventType.CONTRACT_EXPIRED}, condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "Contract still pending", "body": "A sent contract has not been signed."}}],
        created_by=None,
    )
    await service.publish(tenant_id, notify_automation.id)

    dispatched = await service.check_and_dispatch_scheduled(tenant_id, now_utc=datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc))
    assert len(dispatched) == 1

    sweep_steps = await service.list_execution_steps(tenant_id, dispatched[0])
    assert sweep_steps[0].status == "SUCCEEDED"
    assert str(stale_contract_id) in sweep_steps[0].result["expired_contract_ids"]
    assert str(fresh_contract_id) not in sweep_steps[0].result["expired_contract_ids"]

    async with async_session_maker() as session:
        stale = await session.get(Contract, stale_contract_id)
        fresh = await session.get(Contract, fresh_contract_id)
    assert stale.status == ContractStatus.EXPIRED
    assert fresh.status == ContractStatus.SENT  # untouched — still within threshold

    stats = await event_bus.process_pending(EventType.CONTRACT_EXPIRED)
    assert stats.succeeded >= 1

    async with async_session_maker() as session:
        notify_executions = (
            await session.execute(select(AutomationExecution).where(AutomationExecution.automation_id == notify_automation.id))
        ).scalars().all()
        notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id, Notification.title == "Contract still pending"))
        ).scalars().all()
    assert len(notify_executions) == 1
    assert len(notifications) == 1


async def test_contract_pending_sweep_is_idempotent_no_repeat_notify(event_bus, tool_registry) -> None:
    """Step 12: duplicate scheduler ticks must not duplicate the action —
    the second sweep finds no SENT/VIEWED contracts left to expire (the
    first sweep already transitioned it), so it publishes nothing new."""
    tenant_id = uuid.uuid4()
    service = _service(tool_registry, event_bus)
    customer_id = await _make_customer(tenant_id)
    await _make_contract(tenant_id, customer_id, sent_days_ago=10)

    from app.tools.factory import build_tool_registry

    registry = build_tool_registry(async_session_maker, event_bus)
    from app.services.contract_service import ContractService

    contract_service = ContractService(async_session_maker, event_bus)
    first = await contract_service.detect_pending(tenant_id)
    assert len(first) == 1
    second = await contract_service.detect_pending(tenant_id)
    assert second == []  # nothing left to expire — no duplicate event


async def test_signed_contract_produces_no_pending_alert(event_bus, tool_registry) -> None:
    """Step 13: if a contract becomes SIGNED before the sweep runs, it must
    never be flagged pending."""
    from app.services.contract_service import ContractService

    tenant_id = uuid.uuid4()
    customer_id = await _make_customer(tenant_id)
    contract_id = await _make_contract(tenant_id, customer_id, sent_days_ago=10)

    async with async_session_maker() as session:
        contract = await session.get(Contract, contract_id)
        contract.status = ContractStatus.SIGNED
        await session.commit()

    contract_service = ContractService(async_session_maker, event_bus)
    ids = await contract_service.detect_pending(tenant_id)
    assert ids == []

    async with async_session_maker() as session:
        contract = await session.get(Contract, contract_id)
    assert contract.status == ContractStatus.SIGNED  # unchanged


# =====================================================================
# C. REFERRAL OPPORTUNITY
# =====================================================================

async def test_referral_eligible_opportunity_triggers_owner_notification(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry, event_bus)
    customer_id = await _make_customer(tenant_id, name="Happy Customer")

    automation = await service.create_automation(
        tenant_id, name="Referral Opportunity Notify", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": EventType.RETENTION_OPPORTUNITY_CREATED},
        condition={"field": "retention_opportunity.type", "op": "eq", "value": "REFERRAL_ELIGIBLE"},
        steps=[{"action": "notifications.create_notification", "params": {"title": "Referral opportunity", "body": "{{retention_opportunity.reason}}"}}],
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)

    retention_service = RetentionService(async_session_maker, event_bus, ExceptionService(async_session_maker, event_bus))
    await retention_service.create_referral_eligibility_opportunity(
        tenant_id, customer_id, reason="Customer left 5-star feedback — great referral candidate."
    )

    stats = await event_bus.process_pending(EventType.RETENTION_OPPORTUNITY_CREATED)
    assert stats.succeeded >= 1

    async with async_session_maker() as session:
        executions = (
            await session.execute(select(AutomationExecution).where(AutomationExecution.automation_id == automation.id))
        ).scalars().all()
        notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id, Notification.title == "Referral opportunity"))
        ).scalars().all()
    assert len(executions) == 1
    assert len(notifications) == 1
    assert "great referral candidate" in notifications[0].body


async def test_other_opportunity_types_do_not_trigger_referral_automation(event_bus, tool_registry) -> None:
    """The condition must genuinely filter — POST_JOB_FOLLOWUP or
    REVIEW_ELIGIBLE opportunities must never fire the referral automation."""
    tenant_id = uuid.uuid4()
    service = _service(tool_registry, event_bus)
    customer_id = await _make_customer(tenant_id)

    automation = await service.create_automation(
        tenant_id, name="Referral Opportunity Notify Filter Check", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": EventType.RETENTION_OPPORTUNITY_CREATED},
        condition={"field": "retention_opportunity.type", "op": "eq", "value": "REFERRAL_ELIGIBLE"},
        steps=[{"action": "notifications.create_notification", "params": {"title": "Referral opportunity", "body": "x"}}],
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)

    job_id = await _make_job(tenant_id, customer_id)
    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        retention_service = RetentionService(async_session_maker, event_bus, ExceptionService(async_session_maker, event_bus))
        await retention_service._create_post_job_followup_opportunity(tenant_id, job=job)

    await event_bus.process_pending(EventType.RETENTION_OPPORTUNITY_CREATED)

    # The execution row exists (the automation IS triggered by the event
    # type) but the condition filters out this non-REFERRAL_ELIGIBLE
    # opportunity, so no step ever runs and no notification is created.
    async with async_session_maker() as session:
        notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_id))
        ).scalars().all()
    assert notifications == []


# =====================================================================
# Tenant isolation (Step 9)
# =====================================================================

async def test_qa_failure_tenant_isolation(event_bus, tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    service = _service(tool_registry, event_bus)
    customer_a = await _make_customer(tenant_a)
    job_a = await _make_job(tenant_a, customer_a)

    automation_b = await service.create_automation(
        tenant_b, name="Tenant B QA Escalation", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": EventType.JOB_QA_FAILED}, condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "QA failed", "body": "x"}}],
        created_by=None,
    )
    await service.publish(tenant_b, automation_b.id)

    qa_service = QAService(async_session_maker, event_bus)
    await qa_service.fail_qa(tenant_a, job_a, reason="Tenant A only failure")
    await event_bus.process_pending(EventType.JOB_QA_FAILED)

    async with async_session_maker() as session:
        tenant_b_executions = (
            await session.execute(select(AutomationExecution).where(AutomationExecution.automation_id == automation_b.id))
        ).scalars().all()
        tenant_b_notifications = (
            await session.execute(select(Notification).where(Notification.tenant_id == tenant_b))
        ).scalars().all()
    assert tenant_b_executions == []
    assert tenant_b_notifications == []


# =====================================================================
# Failure handling (Step 14)
# =====================================================================

async def test_contract_sweep_missing_contracts_is_a_safe_noop(tool_registry, event_bus) -> None:
    from app.services.contract_service import ContractService

    tenant_id = uuid.uuid4()
    contract_service = ContractService(async_session_maker, event_bus)
    ids = await contract_service.detect_pending(tenant_id)
    assert ids == []


async def test_qa_failure_automation_disabled_does_not_execute(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = _service(tool_registry, event_bus)
    customer_id = await _make_customer(tenant_id)
    job_id = await _make_job(tenant_id, customer_id)

    automation = await service.create_automation(
        tenant_id, name="QA Failure Escalation Disabled", description=None, trigger_type=TriggerType.EVENT,
        trigger_config={"event_type": EventType.JOB_QA_FAILED}, condition=None,
        steps=[{"action": "notifications.create_notification", "params": {"title": "QA failed", "body": "x"}}],
        created_by=None,
    )
    await service.publish(tenant_id, automation.id)
    await service.set_enabled(tenant_id, automation.id, enabled=False)

    qa_service = QAService(async_session_maker, event_bus)
    await qa_service.fail_qa(tenant_id, job_id, reason="Should not trigger")
    await event_bus.process_pending(EventType.JOB_QA_FAILED)

    async with async_session_maker() as session:
        executions = (
            await session.execute(select(AutomationExecution).where(AutomationExecution.automation_id == automation.id))
        ).scalars().all()
    assert executions == []


# =====================================================================
# Real HTTP walkthrough (Step 10/19): both an EVENT automation and the
# SCHEDULE half, driven entirely through the real FastAPI app — real
# register, real automation create/publish over HTTP, real domain
# mutation (QA failure) through the real domain service (no HTTP endpoint
# exists to fail QA directly outside the job lifecycle, so this mirrors
# Phase 23/11's own "seed the domain event, drive the rest over HTTP"
# convention), real POST /events/process to run the dispatcher, real GET
# /notifications to observe the result.
# =====================================================================

async def test_real_http_qa_failure_and_contract_sweep_walkthrough() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.api.tool_deps import get_tool_registry, get_wired_event_bus
    from app.events.automation_handlers import register_automation_handlers
    from app.events.bus import EventBus
    from app.events.handlers import register_default_handlers
    from app.events.operations_handlers import register_operations_handlers
    from app.events.transport import InMemoryTransport
    from app.main import app
    from app.tools.factory import build_tool_registry

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    register_default_handlers(bus)
    register_operations_handlers(bus, async_session_maker)
    registry = build_tool_registry(async_session_maker, bus)
    register_automation_handlers(bus, async_session_maker, AIExecutionService(registry))
    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_wired_event_bus] = lambda: bus

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            reg = await client.post(
                "/api/v1/auth/register",
                json={"organization_name": "Phase24 HTTP Co", "full_name": "Owner", "email": "phase24-http@example.com", "password": "supersecret1"},
            )
            assert reg.status_code == 201, reg.text
            token = reg.json()["tokens"]["access_token"]
            tenant_id = uuid.UUID(reg.json()["user"]["tenant_id"])
            headers = {"Authorization": f"Bearer {token}"}

            customer_id = await _make_customer(tenant_id)
            job_id = await _make_job(tenant_id, customer_id)
            contract_id = await _make_contract(tenant_id, customer_id, sent_days_ago=10)

            # --- QA failure escalation, via real HTTP automation CRUD ---
            qa_automation = await client.post(
                "/api/v1/automations",
                json={
                    "name": "QA Failure Escalation (HTTP)", "trigger_type": "EVENT",
                    "trigger_config": {"event_type": "job.qa_failed"}, "condition": None,
                    "steps": [{"action": "notifications.create_notification", "params": {"title": "QA failed", "body": "{{job.reason}}"}}],
                },
                headers=headers,
            )
            assert qa_automation.status_code == 201, qa_automation.text
            qa_automation_id = qa_automation.json()["id"]
            publish_resp = await client.post(f"/api/v1/automations/{qa_automation_id}/publish", headers=headers)
            assert publish_resp.status_code == 200, publish_resp.text

            qa_service = QAService(async_session_maker, bus)
            await qa_service.fail_qa(tenant_id, job_id, reason="HTTP walkthrough failure")

            process_resp = await client.post("/api/v1/events/process/job.qa_failed", headers=headers)
            assert process_resp.status_code == 200, process_resp.text

            notif_resp = await client.get("/api/v1/notifications", headers=headers)
            assert notif_resp.status_code == 200, notif_resp.text
            titles = [n["title"] for n in notif_resp.json()["notifications"]]
            assert "QA failed" in titles

            # --- Contract-pending sweep + notify, via real HTTP scheduled-tick ---
            sweep_automation = await client.post(
                "/api/v1/automations",
                json={
                    "name": "Contract Pending Sweep (HTTP)", "trigger_type": "SCHEDULE",
                    "trigger_config": {"frequency": "DAILY", "time": "00:00"}, "condition": None,
                    "steps": [{"action": "contracts.detect_pending", "params": {}}],
                },
                headers=headers,
            )
            assert sweep_automation.status_code == 201, sweep_automation.text
            await client.post(f"/api/v1/automations/{sweep_automation.json()['id']}/publish", headers=headers)

            notify_automation = await client.post(
                "/api/v1/automations",
                json={
                    "name": "Contract Pending Notify (HTTP)", "trigger_type": "EVENT",
                    "trigger_config": {"event_type": "contract.expired"}, "condition": None,
                    "steps": [{"action": "notifications.create_notification", "params": {"title": "Contract still pending", "body": "x"}}],
                },
                headers=headers,
            )
            assert notify_automation.status_code == 201, notify_automation.text
            await client.post(f"/api/v1/automations/{notify_automation.json()['id']}/publish", headers=headers)

            tick_resp = await client.post("/api/v1/automations/scheduled/dispatch-tick", headers=headers)
            assert tick_resp.status_code == 200, tick_resp.text
            assert len(tick_resp.json()["dispatched_execution_ids"]) == 1

            async with async_session_maker() as session:
                contract = await session.get(Contract, contract_id)
            assert contract.status == ContractStatus.EXPIRED

            # Phase 25 fixed the dual-EventBus artifact this comment used to
            # document (get_automation_service now resolves its ToolRegistry
            # via Depends(get_tool_registry) instead of a bare function call,
            # so it correctly picks up this test's overridden registry/bus
            # the same way every other endpoint does) — the real HTTP
            # events-process endpoint below is now sufficient, no manual
            # bus access needed. See test_phase25_eventbus_boundary.py for
            # the dedicated regression proving this.
            process_resp2 = await client.post("/api/v1/events/process/contract.expired", headers=headers)
            assert process_resp2.status_code == 200, process_resp2.text

            notif_resp2 = await client.get("/api/v1/notifications", headers=headers)
            titles2 = [n["title"] for n in notif_resp2.json()["notifications"]]
            assert "Contract still pending" in titles2
    finally:
        app.dependency_overrides.pop(get_tool_registry, None)
        app.dependency_overrides.pop(get_wired_event_bus, None)
