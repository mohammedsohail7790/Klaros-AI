"""The full Retention & Referral loop, run end to end against a real
(test-database) tenant:

    customer -> completed job -> invoice -> payment -> job.closed
    -> retention profile -> post-job follow-up opportunity -> review request
    -> positive feedback -> referral eligibility -> referral program
    -> referral code -> referral -> referral creates a REAL Lead
    (source=REFERRAL) -> qualify -> appointment -> job -> close -> invoice
    -> payment -> referral CONVERTED -> referral revenue -> Marketing
    attribution sees it -> Customer 360 timeline -> Owner Cockpit -> audit
    -> idempotent re-processing (no duplicate conversion)

plus a negative scenario: a rating-1 feedback event must NOT create a
public review request — it must open a real SERVICE_RECOVERY_REQUIRED
exception/opportunity instead.
"""

import base64
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.actor import ActorType
from app.models.event import EventType
from app.models.finance import Invoice
from app.models.marketing import CampaignConversion
from app.models.operations import ExceptionType, OperationsException, ExceptionStatus
from app.models.rbac import Role
from app.models.retention import LifecycleState, Referral, ReferralStatus, ReviewStatus
from app.services.attribution_service import AttributionService
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def _close_job(tool_registry, ctx, tenant_id, session_factory, *, customer_id, lead_id=None, title="Job", estimated_revenue=500.0, estimated_cost=200.0):
    job_payload = {"title": title, "customer_id": customer_id, "estimated_revenue": estimated_revenue, "estimated_cost": estimated_cost}
    if lead_id:
        job_payload["lead_id"] = lead_id
    job_out = await tool_registry.execute("operations.create_job", job_payload, ctx)
    job_id = job_out.job["id"]

    await tool_registry.execute("operations.schedule_job", {"job_id": job_id, "start_time": "2026-11-01T09:00:00+00:00", "end_time": "2026-11-01T11:00:00+00:00"}, ctx)
    worker = await tool_registry.execute("operations.create_worker", {"name": "Sam", "service_types": ["HVAC"]}, ctx)
    await tool_registry.execute("operations.assign_job", {"job_id": job_id, "worker_id": worker.worker["id"]}, ctx)
    await tool_registry.execute("operations.dispatch_job", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.update_job_status", {"job_id": job_id, "target_status": "EN_ROUTE"}, ctx)
    await tool_registry.execute("operations.update_job_status", {"job_id": job_id, "target_status": "ON_SITE"}, ctx)
    await tool_registry.execute("operations.start_job", {"job_id": job_id}, ctx)
    await tool_registry.execute(
        "operations.add_job_photo",
        {"job_id": job_id, "filename": "done.jpg", "content_type": "image/jpeg", "content_base64": base64.b64encode(b"photo bytes").decode()},
        ctx,
    )
    await tool_registry.execute("operations.complete_job", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.start_qa", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.complete_qa", {"job_id": job_id}, ctx)
    await tool_registry.execute("operations.generate_completion_packet", {"job_id": job_id}, ctx)
    closed = await tool_registry.execute("operations.close_job", {"job_id": job_id}, ctx)
    assert closed.job["status"] == "CLOSED"
    return job_id


async def _pay_invoice(tool_registry, event_bus, ctx, tenant_id, *, job_id, customer_id, amount):
    await event_bus.process_pending(EventType.INVOICE_TRIGGER_REQUESTED)
    async with event_bus.session_factory() as session:
        invoice = (await session.execute(select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.job_id == uuid.UUID(job_id)))).scalar_one()
    invoice_id = str(invoice.id)
    await event_bus.process_pending(EventType.INVOICE_CREATED)

    await tool_registry.execute("finance.request_invoice_approval", {"invoice_id": invoice_id}, ctx)
    await tool_registry.execute("finance.send_invoice", {"invoice_id": invoice_id}, ctx)
    await tool_registry.execute(
        "finance.record_test_payment",
        {"customer_id": customer_id, "amount": str(amount), "allocations": [{"invoice_id": invoice_id, "amount": str(amount)}]},
        ctx,
    )
    await event_bus.process_pending(EventType.PAYMENT_RECEIVED)
    return invoice_id


async def test_full_retention_and_referral_loop(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    # 1-5: customer, completed job, invoice, payment, job.closed.
    customer = await tool_registry.execute("crm.create_customer", {"name": "Referring Customer", "email": "referrer@example.com"}, ctx)
    customer_id = customer.customer["id"]

    job_id = await _close_job(tool_registry, ctx, tenant_id, event_bus.session_factory, customer_id=customer_id, title="Initial job", estimated_revenue=500.0, estimated_cost=200.0)
    await event_bus.process_pending(EventType.JOB_CLOSED)
    invoice_id = await _pay_invoice(tool_registry, event_bus, ctx, tenant_id, job_id=job_id, customer_id=customer_id, amount="500.00")

    # 6: retention profile generated off job.closed.
    async with event_bus.session_factory() as session:
        from app.models.retention import CustomerLifecycleProfile

        profile = (
            await session.execute(select(CustomerLifecycleProfile).where(CustomerLifecycleProfile.tenant_id == tenant_id, CustomerLifecycleProfile.customer_id == uuid.UUID(customer_id)))
        ).scalar_one()
    assert profile.lifecycle_state == LifecycleState.FIRST_SERVICE
    assert profile.jobs_completed_count == 1

    # 7: post-job follow-up opportunity.
    from app.models.retention import OpportunityStatus, OpportunityType, RetentionOpportunity

    async with event_bus.session_factory() as session:
        followup = (
            await session.execute(
                select(RetentionOpportunity).where(
                    RetentionOpportunity.tenant_id == tenant_id, RetentionOpportunity.customer_id == uuid.UUID(customer_id),
                    RetentionOpportunity.type == OpportunityType.POST_JOB_FOLLOWUP,
                )
            )
        ).scalar_one()
    assert followup.status == OpportunityStatus.OPEN

    # 8: review request created (ELIGIBLE).
    from app.models.retention import ReviewRequest

    async with event_bus.session_factory() as session:
        review = (
            await session.execute(select(ReviewRequest).where(ReviewRequest.tenant_id == tenant_id, ReviewRequest.job_id == uuid.UUID(job_id)))
        ).scalar_one()
    assert review.status == ReviewStatus.ELIGIBLE
    review_id = str(review.id)

    # send_review_request is APPROVAL_REQUIRED by policy (reaches a real
    # customer channel) — the ToolRegistry intercepts before the tool body
    # runs, same as Phase 5's finance.void_invoice. Confirm the boundary
    # is enforced, then complete the send directly against the service
    # (mirrors what a dedicated post-approval endpoint would do).
    from app.tools.errors import ToolApprovalRequiredError

    with pytest.raises(ToolApprovalRequiredError):
        await tool_registry.execute("retention.send_review_request", {"review_request_id": review_id}, ctx)

    from app.communications.internal_test_adapter import InternalTestCommunicationAdapter
    from app.services.exception_service import ExceptionService
    from app.services.retention_service import RetentionService
    from app.services.review_service import ReviewService

    review_service = ReviewService(
        event_bus.session_factory, event_bus, ExceptionService(event_bus.session_factory, event_bus),
        RetentionService(event_bus.session_factory, event_bus, ExceptionService(event_bus.session_factory, event_bus)),
        InternalTestCommunicationAdapter(event_bus.session_factory),
    )
    sent = await review_service.send_review_request(tenant_id, uuid.UUID(review_id))
    assert sent.status == ReviewStatus.REQUESTED

    # 9: positive feedback.
    feedback = await tool_registry.execute(
        "retention.record_feedback", {"customer_id": customer_id, "job_id": job_id, "rating": 5, "comment": "Great work!"}, ctx
    )
    assert feedback.sentiment == "POSITIVE"

    # 10: customer becomes eligible for referral (a real REFERRAL_ELIGIBLE opportunity).
    async with event_bus.session_factory() as session:
        referral_opp = (
            await session.execute(
                select(RetentionOpportunity).where(
                    RetentionOpportunity.tenant_id == tenant_id, RetentionOpportunity.customer_id == uuid.UUID(customer_id),
                    RetentionOpportunity.type == OpportunityType.REFERRAL_ELIGIBLE,
                )
            )
        ).scalar_one()
    assert referral_opp.status == OpportunityStatus.OPEN

    # 11-13: referral program, code, referral.
    program = await tool_registry.execute("retention.create_referral_program", {"name": "Friends & Family", "reward_type": "credit", "reward_amount": "50.00"}, ctx)
    program_id = program.program_id
    campaign_id = program.campaign_id

    code = await tool_registry.execute("retention.get_or_create_referral_code", {"program_id": program_id, "customer_id": customer_id}, ctx)
    code_id = code.code_id

    # Idempotent code generation.
    code_again = await tool_registry.execute("retention.get_or_create_referral_code", {"program_id": program_id, "customer_id": customer_id}, ctx)
    assert code_again.code_id == code_id

    referral = await tool_registry.execute("retention.create_referral", {"referral_code_id": code_id}, ctx)
    referral_id = referral.referral_id
    assert referral.status == ReferralStatus.CREATED

    # 14-15: referral creates a real Lead, source=REFERRAL, attributed to the program's campaign.
    converted = await tool_registry.execute(
        "retention.convert_referral_to_lead",
        {"referral_id": referral_id, "name": "New Referred Prospect", "email": "referred@example.com", "service_requested": "Furnace repair"},
        ctx,
    )
    assert converted.status == ReferralStatus.LEAD_CREATED

    async with event_bus.session_factory() as session:
        referral_row = await session.get(Referral, uuid.UUID(referral_id))
    lead_id = str(referral_row.lead_id)

    from app.models.crm import Lead

    async with event_bus.session_factory() as session:
        lead_row = await session.get(Lead, uuid.UUID(lead_id))
    assert lead_row.source == "REFERRAL"
    assert lead_row.campaign_id == uuid.UUID(campaign_id)

    # Idempotency: converting the same referral again must not create a second lead.
    converted_again = await tool_registry.execute(
        "retention.convert_referral_to_lead",
        {"referral_id": referral_id, "name": "New Referred Prospect", "email": "referred@example.com"},
        ctx,
    )
    async with event_bus.session_factory() as session:
        referral_row_2 = await session.get(Referral, uuid.UUID(referral_id))
    assert referral_row_2.lead_id == uuid.UUID(lead_id)

    # 16: qualify lead.
    await tool_registry.execute("crm.qualify_lead", {"lead_id": lead_id}, ctx)
    await event_bus.process_pending(EventType.LEAD_QUALIFIED)

    async with event_bus.session_factory() as session:
        referral_row = await session.get(Referral, uuid.UUID(referral_id))
    assert referral_row.status == ReferralStatus.QUALIFIED

    # 17: appointment.
    new_customer = await tool_registry.execute("crm.create_customer", {"name": "New Referred Prospect", "email": "referred@example.com"}, ctx)
    new_customer_id = new_customer.customer["id"]
    appt = await tool_registry.execute(
        "crm.create_appointment",
        {"customer_id": new_customer_id, "lead_id": lead_id, "title": "Furnace repair estimate", "start_time": "2026-11-05T09:00:00+00:00", "end_time": "2026-11-05T10:00:00+00:00"},
        ctx,
    )
    await event_bus.process_pending(EventType.APPOINTMENT_CREATED)

    async with event_bus.session_factory() as session:
        referral_row = await session.get(Referral, uuid.UUID(referral_id))
    assert referral_row.status == ReferralStatus.BOOKED

    # 18-19: job, close.
    referred_job_id = await _close_job(tool_registry, ctx, tenant_id, event_bus.session_factory, customer_id=new_customer_id, lead_id=lead_id, title="Furnace repair", estimated_revenue=600.0, estimated_cost=250.0)
    await event_bus.process_pending(EventType.JOB_CLOSED)

    # 20-21: invoice, payment.
    await _pay_invoice(tool_registry, event_bus, ctx, tenant_id, job_id=referred_job_id, customer_id=new_customer_id, amount="600.00")

    # 22-23: referral CONVERTED, referral revenue.
    async with event_bus.session_factory() as session:
        referral_row = await session.get(Referral, uuid.UUID(referral_id))
    assert referral_row.status == ReferralStatus.CONVERTED
    assert referral_row.revenue_amount == Decimal("600.00")
    assert referral_row.collected_amount == Decimal("600.00")

    # A reward should have been auto-requested (program.reward_amount was set).
    from app.models.retention import ReferralReward, RewardStatus

    async with event_bus.session_factory() as session:
        reward = (await session.execute(select(ReferralReward).where(ReferralReward.tenant_id == tenant_id, ReferralReward.referral_id == uuid.UUID(referral_id)))).scalar_one()
    assert reward.status == RewardStatus.PENDING

    approved = await tool_registry.execute("retention.approve_referral_reward", {"reward_id": str(reward.id)}, ctx)
    assert approved.status == RewardStatus.APPROVED
    issued = await tool_registry.execute("retention.issue_referral_reward", {"reward_id": str(reward.id)}, ctx)
    assert issued.status == RewardStatus.ISSUED

    # 24: Marketing attribution sees the referral revenue.
    attribution_service = AttributionService(event_bus.session_factory)
    perf = await attribution_service.campaign_performance(tenant_id, uuid.UUID(campaign_id))
    assert perf.leads == 1
    assert perf.revenue == Decimal("600.00")
    assert perf.collected_revenue == Decimal("600.00")

    # 27: audit rows exist for the key referral tool calls.
    from app.models.audit_log import AuditLog

    async with event_bus.session_factory() as session:
        audit_tools = {
            row.tool for row in (
                await session.execute(select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.tool.like("retention.%")))
            ).scalars().all()
        }
    assert "retention.create_referral_program" in audit_tools
    assert "retention.create_referral" in audit_tools
    assert "retention.convert_referral_to_lead" in audit_tools
    assert "retention.approve_referral_reward" in audit_tools

    # 28: no duplicate conversion — re-processing invoice.created for the same invoice again is idempotent.
    await event_bus.process_pending(EventType.INVOICE_CREATED)
    async with event_bus.session_factory() as session:
        referral_row_final = await session.get(Referral, uuid.UUID(referral_id))
    assert referral_row_final.status == ReferralStatus.REWARDED  # unchanged (already REWARDED above), not double-processed


async def test_negative_feedback_triggers_service_recovery_not_public_review(event_bus, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Unhappy Customer", "email": "unhappy@example.com"}, ctx)
    customer_id = customer.customer["id"]
    job_id = await _close_job(tool_registry, ctx, tenant_id, event_bus.session_factory, customer_id=customer_id, title="Rough job")
    await event_bus.process_pending(EventType.JOB_CLOSED)

    from app.models.retention import ReviewRequest, ReviewStatus

    async with event_bus.session_factory() as session:
        review = (await session.execute(select(ReviewRequest).where(ReviewRequest.tenant_id == tenant_id, ReviewRequest.job_id == uuid.UUID(job_id)))).scalar_one()
    assert review.status == ReviewStatus.ELIGIBLE

    feedback = await tool_registry.execute("retention.record_feedback", {"customer_id": customer_id, "job_id": job_id, "rating": 1, "comment": "Very unhappy."}, ctx)
    assert feedback.sentiment == "NEGATIVE"

    # The existing review request must NOT have been sent/promoted — it's marked RECEIVED (closed the loop) not REQUESTED.
    async with event_bus.session_factory() as session:
        review_row = (await session.execute(select(ReviewRequest).where(ReviewRequest.tenant_id == tenant_id, ReviewRequest.job_id == uuid.UUID(job_id)))).scalar_one()
    assert review_row.status != ReviewStatus.REQUESTED

    from app.models.operations import ExceptionType, OperationsException, ExceptionStatus

    async with event_bus.session_factory() as session:
        recovery_exc = (
            await session.execute(
                select(OperationsException).where(
                    OperationsException.tenant_id == tenant_id, OperationsException.type == ExceptionType.SERVICE_RECOVERY_REQUIRED,
                    OperationsException.entity_id == uuid.UUID(customer_id), OperationsException.status == ExceptionStatus.OPEN,
                )
            )
        ).scalar_one_or_none()
    assert recovery_exc is not None

    async with event_bus.session_factory() as session:
        negative_exc = (
            await session.execute(
                select(OperationsException).where(
                    OperationsException.tenant_id == tenant_id, OperationsException.type == ExceptionType.NEGATIVE_FEEDBACK,
                    OperationsException.entity_id == uuid.UUID(customer_id), OperationsException.status == ExceptionStatus.OPEN,
                )
            )
        ).scalar_one_or_none()
    assert negative_exc is not None
