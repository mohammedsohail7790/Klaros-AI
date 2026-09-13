"""Focused Retention & Referral unit/integration tests: lifecycle
transitions, repeat-customer/inactivity detection, risk signals,
tenant isolation, referral code uniqueness, reward approval boundary,
and idempotent event handling."""

import base64
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.actor import ActorType
from app.models.event import EventType
from app.models.rbac import Role
from app.models.retention import LifecycleState, ReferralCode
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def _closed_job(tool_registry, ctx, customer_id, *, title="Job", estimated_revenue=400.0, estimated_cost=150.0):
    job_out = await tool_registry.execute(
        "operations.create_job", {"title": title, "customer_id": customer_id, "estimated_revenue": estimated_revenue, "estimated_cost": estimated_cost}, ctx
    )
    job_id = job_out.job["id"]
    await tool_registry.execute("operations.schedule_job", {"job_id": job_id, "start_time": "2026-11-01T09:00:00+00:00", "end_time": "2026-11-01T11:00:00+00:00"}, ctx)
    worker = await tool_registry.execute("operations.create_worker", {"name": "Tech", "service_types": ["HVAC"]}, ctx)
    await tool_registry.execute("operations.assign_job", {"job_id": job_id, "worker_id": worker.worker["id"]}, ctx)
    await tool_registry.execute("operations.dispatch_job", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.update_job_status", {"job_id": job_id, "target_status": "EN_ROUTE"}, ctx)
    await tool_registry.execute("operations.update_job_status", {"job_id": job_id, "target_status": "ON_SITE"}, ctx)
    await tool_registry.execute("operations.start_job", {"job_id": job_id}, ctx)
    await tool_registry.execute(
        "operations.add_job_photo",
        {"job_id": job_id, "filename": "d.jpg", "content_type": "image/jpeg", "content_base64": base64.b64encode(b"x").decode()},
        ctx,
    )
    await tool_registry.execute("operations.complete_job", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.start_qa", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.complete_qa", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.generate_completion_packet", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.close_job", {"job_id": job_id}, ctx)
    return job_id


async def test_lifecycle_first_service_then_repeat_customer(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Lifecycle Customer"}, ctx)
    customer_id = customer.customer["id"]

    await _closed_job(tool_registry, ctx, customer_id, title="Job 1")
    await event_bus.process_pending(EventType.JOB_CLOSED)

    from app.models.retention import CustomerLifecycleProfile

    async with event_bus.session_factory() as session:
        profile = (
            await session.execute(select(CustomerLifecycleProfile).where(CustomerLifecycleProfile.tenant_id == tenant_id, CustomerLifecycleProfile.customer_id == uuid.UUID(customer_id)))
        ).scalar_one()
    assert profile.lifecycle_state == LifecycleState.FIRST_SERVICE
    assert profile.jobs_completed_count == 1

    await _closed_job(tool_registry, ctx, customer_id, title="Job 2")
    await event_bus.process_pending(EventType.JOB_CLOSED)

    async with event_bus.session_factory() as session:
        profile = (
            await session.execute(select(CustomerLifecycleProfile).where(CustomerLifecycleProfile.tenant_id == tenant_id, CustomerLifecycleProfile.customer_id == uuid.UUID(customer_id)))
        ).scalar_one()
    assert profile.lifecycle_state == LifecycleState.REPEAT_CUSTOMER
    assert profile.jobs_completed_count == 2


async def test_duplicate_job_closed_delivery_is_idempotent(event_bus, tool_registry) -> None:
    """job.closed delivered twice -> one retention opportunity, not two."""
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Dedup Customer"}, ctx)
    customer_id = customer.customer["id"]
    job_id = await _closed_job(tool_registry, ctx, customer_id)

    await event_bus.process_pending(EventType.JOB_CLOSED)
    await event_bus.process_pending(EventType.JOB_CLOSED)  # duplicate delivery attempt

    from app.models.retention import OpportunityType, RetentionOpportunity

    async with event_bus.session_factory() as session:
        rows = (
            await session.execute(
                select(RetentionOpportunity).where(
                    RetentionOpportunity.tenant_id == tenant_id, RetentionOpportunity.customer_id == uuid.UUID(customer_id),
                    RetentionOpportunity.type == OpportunityType.POST_JOB_FOLLOWUP,
                )
            )
        ).scalars().all()
    assert len(rows) == 1


async def test_at_risk_and_inactive_detection(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Stale Customer"}, ctx)
    customer_id = customer.customer["id"]
    await _closed_job(tool_registry, ctx, customer_id)
    await event_bus.process_pending(EventType.JOB_CLOSED)

    from app.models.retention import CustomerLifecycleProfile

    # Force last_service_at far into the past (250 days -> AT_RISK; real historical data simulated).
    async with event_bus.session_factory() as session:
        profile = (
            await session.execute(select(CustomerLifecycleProfile).where(CustomerLifecycleProfile.tenant_id == tenant_id, CustomerLifecycleProfile.customer_id == uuid.UUID(customer_id)))
        ).scalar_one()
        profile.last_service_at = datetime.now(timezone.utc) - timedelta(days=250)
        await session.commit()

    changed = await tool_registry.execute("retention.detect_at_risk_and_inactive", {}, ctx)
    assert customer_id in changed.changed_customer_ids

    async with event_bus.session_factory() as session:
        profile = (
            await session.execute(select(CustomerLifecycleProfile).where(CustomerLifecycleProfile.tenant_id == tenant_id, CustomerLifecycleProfile.customer_id == uuid.UUID(customer_id)))
        ).scalar_one()
    assert profile.lifecycle_state == LifecycleState.AT_RISK

    from app.models.operations import ExceptionType, OperationsException

    async with event_bus.session_factory() as session:
        exc = (
            await session.execute(
                select(OperationsException).where(
                    OperationsException.tenant_id == tenant_id, OperationsException.type == ExceptionType.CUSTOMER_AT_RISK,
                    OperationsException.entity_id == uuid.UUID(customer_id),
                )
            )
        ).scalar_one_or_none()
    assert exc is not None

    # Reactivation: a new closed job flips AT_RISK -> REACTIVATED.
    await _closed_job(tool_registry, ctx, customer_id, title="Win-back job")
    await event_bus.process_pending(EventType.JOB_CLOSED)

    async with event_bus.session_factory() as session:
        profile = (
            await session.execute(select(CustomerLifecycleProfile).where(CustomerLifecycleProfile.tenant_id == tenant_id, CustomerLifecycleProfile.customer_id == uuid.UUID(customer_id)))
        ).scalar_one()
    assert profile.lifecycle_state == LifecycleState.REACTIVATED


async def test_retention_tools_enforce_tenant_isolation(event_bus, tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    ctx_a = _ctx(tenant_a)
    ctx_b = _ctx(tenant_b)

    program = await tool_registry.execute("retention.create_referral_program", {"name": "Tenant A Program"}, ctx_a)
    customer = await tool_registry.execute("crm.create_customer", {"name": "Tenant A Customer"}, ctx_a)

    with pytest.raises(ValueError):
        await tool_registry.execute(
            "retention.get_or_create_referral_code", {"program_id": program.program_id, "customer_id": customer.customer["id"]}, ctx_b
        )


async def test_referral_code_is_unique_per_tenant(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    program = await tool_registry.execute("retention.create_referral_program", {"name": "Uniqueness Program"}, ctx)
    c1 = await tool_registry.execute("crm.create_customer", {"name": "Customer One"}, ctx)
    c2 = await tool_registry.execute("crm.create_customer", {"name": "Customer Two"}, ctx)

    code1 = await tool_registry.execute("retention.get_or_create_referral_code", {"program_id": program.program_id, "customer_id": c1.customer["id"]}, ctx)
    code2 = await tool_registry.execute("retention.get_or_create_referral_code", {"program_id": program.program_id, "customer_id": c2.customer["id"]}, ctx)
    assert code1.code != code2.code

    # Re-fetching the same customer's code is idempotent (same code, no new row).
    code1_again = await tool_registry.execute("retention.get_or_create_referral_code", {"program_id": program.program_id, "customer_id": c1.customer["id"]}, ctx)
    assert code1_again.code_id == code1.code_id

    async with event_bus.session_factory() as session:
        rows = (await session.execute(select(ReferralCode).where(ReferralCode.tenant_id == tenant_id))).scalars().all()
    assert len(rows) == 2


async def test_referral_reward_requires_permission_gated_approval(event_bus, tool_registry) -> None:
    """AI cannot approve its own reward request — enforced by permission,
    not just policy (READ_ONLY lacks APPROVE_REFERRAL_REWARD)."""
    tenant_id = uuid.uuid4()
    owner_ctx = _ctx(tenant_id, role=Role.OWNER)
    read_only_ctx = _ctx(tenant_id, role=Role.READ_ONLY)

    program = await tool_registry.execute("retention.create_referral_program", {"name": "Reward Test"}, owner_ctx)
    customer = await tool_registry.execute("crm.create_customer", {"name": "Referrer"}, owner_ctx)
    code = await tool_registry.execute("retention.get_or_create_referral_code", {"program_id": program.program_id, "customer_id": customer.customer["id"]}, owner_ctx)
    referral = await tool_registry.execute("retention.create_referral", {"referral_code_id": code.code_id}, owner_ctx)

    reward = await tool_registry.execute("retention.request_referral_reward", {"referral_id": referral.referral_id, "amount": "25.00"}, owner_ctx)
    assert reward.status == "PENDING"

    from app.tools.errors import ToolPermissionError

    with pytest.raises(ToolPermissionError):
        await tool_registry.execute("retention.approve_referral_reward", {"reward_id": reward.reward_id}, read_only_ctx)

    approved = await tool_registry.execute("retention.approve_referral_reward", {"reward_id": reward.reward_id}, owner_ctx)
    assert approved.status == "APPROVED"

    # Cannot issue a reward that isn't approved (already approved here, so issue succeeds).
    issued = await tool_registry.execute("retention.issue_referral_reward", {"reward_id": reward.reward_id}, owner_ctx)
    assert issued.status == "ISSUED"


async def test_advocate_candidate_identification_is_deterministic(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Loyal Customer"}, ctx)
    customer_id = customer.customer["id"]
    await _closed_job(tool_registry, ctx, customer_id, title="Job 1")
    await event_bus.process_pending(EventType.JOB_CLOSED)
    await _closed_job(tool_registry, ctx, customer_id, title="Job 2")
    await event_bus.process_pending(EventType.JOB_CLOSED)

    candidates = await tool_registry.execute("retention.identify_advocate_candidates", {}, ctx)
    assert customer_id in candidates.candidate_customer_ids

    # Idempotent: running again while still PENDING creates no duplicate.
    candidates_again = await tool_registry.execute("retention.identify_advocate_candidates", {}, ctx)
    assert customer_id not in candidates_again.candidate_customer_ids


async def test_update_reminder_status_rejects_unknown_status(event_bus, tool_registry) -> None:
    """A service reminder's status column has no DB-level enum constraint —
    only application code stands between a typo'd status string and a
    reminder that's silently orphaned forever (never matches
    mark_due_reminders' SCHEDULED filter again, with no error surfaced)."""
    from app.db.session import async_session_maker
    from app.models.retention import ServiceReminder
    from sqlalchemy import select

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Reminder Test Customer"}, ctx)
    await _closed_job(tool_registry, ctx, customer.customer["id"])
    await event_bus.process_pending(EventType.JOB_CLOSED)

    async with async_session_maker() as session:
        reminder = (
            await session.execute(select(ServiceReminder).where(ServiceReminder.tenant_id == tenant_id))
        ).scalar_one()

    with pytest.raises(ValueError):
        await tool_registry.execute(
            "retention.update_reminder_status", {"reminder_id": str(reminder.id), "status": "NOT_A_REAL_STATUS"}, ctx
        )

    updated = await tool_registry.execute(
        "retention.update_reminder_status", {"reminder_id": str(reminder.id), "status": "CANCELLED"}, ctx
    )
    assert updated.status == "CANCELLED"
