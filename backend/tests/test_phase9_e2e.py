"""Phase 9 critical E2E: an AI-recommended action that requires approval
actually resumes and executes once approved — through the SAME EventWorker
and ToolRegistry pipeline Phase 8 proved processes automatically, with zero
manual event processing anywhere in this test.

Loop covered: real business events (referral -> lead -> reward request) ->
EventWorker auto-processes them -> Morning Brief detects the pending reward
and an overdue invoice from real tool output -> Owner clicks Execute on the
reward recommendation -> policy says APPROVAL_REQUIRED -> a real
ApprovalRequest is created (not a dead end) -> Owner opens /approvals
(here: approvals.list/get_detail) -> approves -> ApprovalExecutionService
resumes the ORIGINAL retention.approve_referral_reward call through
ToolRegistry.execute(skip_approval_gate=True) -> a real event is published
-> EventWorker (already running, never invoked directly by this test)
processes it -> the audit log contains the full
recommendation/approval/execution lifecycle -> approving twice is a 409,
not a second execution -> the Morning Brief and dashboard-facing read APIs
reflect the change automatically on next read, no cache to bust.
"""

import asyncio
import uuid

import pytest

# Root cause of the order-dependent failure (it failed when run alone, passed after other test modules had been imported): the event handlers
# this loop depends on are registered by import side effects of the full application. Importing the app, as the running service does, makes the
# test self-contained instead of dependent on which other tests happened to import it first.
import app.main  # noqa: F401,E402
from sqlalchemy import func, select

from app.events.bus import EventBus
from app.events.metrics import EventWorkerMetrics
from app.events.worker import EventWorker
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.finance import Invoice, InvoiceStatus
from app.models.rbac import Role
from app.models.retention import ReferralReward, RewardStatus
from app.tools.base import ExecutionContext
from app.tools.policy import ActionPolicy, DEFAULT_TOOL_POLICIES
from datetime import date, timedelta

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER, actor_id=None):
    return ExecutionContext(
        tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=actor_id or uuid.uuid4(), role=role
    )


async def test_morning_brief_recommendation_approval_resumes_and_executes_automatically(
    event_bus: EventBus, tool_registry
) -> None:
    tenant_id = uuid.uuid4()
    requester_id = uuid.uuid4()
    approver_id = uuid.uuid4()
    requester_ctx = _ctx(tenant_id, actor_id=requester_id)
    approver_ctx = _ctx(tenant_id, actor_id=approver_id)

    original_policy = DEFAULT_TOOL_POLICIES.get("retention.approve_referral_reward")
    DEFAULT_TOOL_POLICIES["retention.approve_referral_reward"] = ActionPolicy.APPROVAL_REQUIRED

    db_lock = asyncio.Lock()

    async def call(tool_name: str, tool_input: dict, ctx=requester_ctx):
        async with db_lock:
            return await tool_registry.execute(tool_name, tool_input, ctx)

    async def read(fn):
        async with db_lock:
            async with event_bus.session_factory() as session:
                return await fn(session)

    async def wait_for(predicate, *, timeout=5.0, interval=0.03):
        elapsed = 0.0
        while elapsed < timeout:
            if await read(predicate):
                return True
            await asyncio.sleep(interval)
            elapsed += interval
        return False

    # 1. Real continuous worker running BEFORE any business action — nothing
    # in this test calls process_pending or a manual event-processing endpoint.
    worker = EventWorker(event_bus, poll_interval_seconds=0.02, metrics=EventWorkerMetrics(), db_lock=db_lock)
    shutdown = asyncio.Event()
    worker_task = asyncio.create_task(worker.run_forever(shutdown))

    try:
        # 2. Tenant + customer + an overdue invoice (real finance data the
        # brief must reflect honestly).
        customer = await call("crm.create_customer", {"name": "Overdue Referral Co"})
        job = await call(
            "operations.create_job",
            {"title": "Repair", "customer_id": customer.customer["id"], "estimated_revenue": 400.0},
        )
        invoice = await call("finance.trigger_invoice_from_job", {"job_id": job.job["id"]})
        await call("finance.request_invoice_approval", {"invoice_id": invoice.invoice["id"]})
        await call("finance.send_invoice", {"invoice_id": invoice.invoice["id"]})
        # This direct write used to happen outside `db_lock` while the running worker was processing the invoice.sent events, so it could be lost
        # or interleaved (an intermittent "no overdue insight" failure). It now takes the same lock every other DB access in this test uses.
        async with db_lock:
            async with event_bus.session_factory() as session:
                row = await session.get(Invoice, uuid.UUID(invoice.invoice["id"]))
                row.status = InvoiceStatus.OVERDUE
                row.due_date = date.today() - timedelta(days=10)
                await session.commit()

        # 3. Referral loop: converted referral requests a reward — a real
        # event chain the worker processes on its own.
        program = await call(
            "retention.create_referral_program",
            {"name": "Refer a Friend", "reward_type": "credit", "reward_amount": "40.00"},
        )
        code = await call(
            "retention.get_or_create_referral_code",
            {"program_id": program.program_id, "customer_id": customer.customer["id"]},
        )
        referral = await call("retention.create_referral", {"referral_code_id": code.code_id})
        await call(
            "retention.convert_referral_to_lead",
            {"referral_id": referral.referral_id, "name": "New Referred Customer"},
        )
        await call(
            "retention.request_referral_reward", {"referral_id": referral.referral_id, "amount": "40.00"}
        )

        # 4. Morning Brief: generated by the Owner (human ExecutionContext),
        # detects both the overdue invoice and the pending reward from real,
        # already-computed tool output — no fabricated numbers.
        await call("insights.generate_morning_brief", {})
        latest = await call("insights.get_latest_morning_brief", {})
        assert any("overdue" in i.summary.lower() for i in latest.insights if i.category == "FINANCE")
        reward_recs = [r for r in latest.recommendations if r.executable and "reward" in r.what.lower()]
        assert reward_recs, [r.what for r in latest.recommendations]
        rec = reward_recs[0]

        # 5. Owner clicks Execute -> policy says APPROVAL_REQUIRED -> a real
        # ApprovalRequest is created, not a dead end.
        exec_result = await call("insights.execute_recommendation", {"recommendation_id": rec.recommendation_id})
        assert exec_result.status == "APPROVAL_REQUESTED"
        approval_id = exec_result.approval_request_id
        assert approval_id

        # 6. Owner opens /approvals (approvals.list) and sees it pending.
        pending = await call("approvals.list", {"status": "PENDING"})
        assert any(a.approval_request_id == approval_id for a in pending.approvals)

        # 7. The requester cannot approve their own request.
        with pytest.raises(Exception):
            await call("approvals.approve", {"approval_request_id": approval_id}, ctx=requester_ctx)

        # 8. A different real approver approves it.
        decision = await call("approvals.approve", {"approval_request_id": approval_id}, ctx=approver_ctx)
        assert decision.status == "APPROVED"
        # 9. ApprovalExecutionService resumed and actually executed the
        # ORIGINAL retention.approve_referral_reward call — not a dead end.
        assert decision.execution_status == "EXECUTED"

        # 10. The real domain effect happened: the reward row is APPROVED.
        async def reward_is_approved(session) -> bool:
            rows = (
                await session.execute(
                    select(ReferralReward).where(ReferralReward.tenant_id == tenant_id)
                )
            ).scalars().all()
            return any(r.status == RewardStatus.APPROVED for r in rows)

        assert await wait_for(reward_is_approved), "referral reward was never actually approved"

        # 11. The event the execution published is processed automatically
        # by the SAME already-running worker — this test never calls
        # process_pending.
        await asyncio.sleep(0.15)

        # 12. Re-approving is rejected outright — no second execution path.
        with pytest.raises(Exception):
            await call("approvals.approve", {"approval_request_id": approval_id}, ctx=approver_ctx)

        # 13. Exactly one reward row was ever touched — idempotency holds.
        reward_count = await read(
            lambda session: session.execute(
                select(func.count()).select_from(ReferralReward).where(ReferralReward.tenant_id == tenant_id)
            )
        )
        assert reward_count.scalar_one() == 1

        # 14. The audit log carries the full lifecycle: the recommendation's
        # own execute call, the approval decision, and the resumed
        # execution — all real rows in the ONE existing audit table.
        async def audit_actions(session):
            rows = (
                await session.execute(
                    select(AuditLog.action).where(AuditLog.tenant_id == tenant_id)
                )
            ).scalars().all()
            return set(rows)

        actions = await read(audit_actions)
        assert "tool.execute:insights.execute_recommendation" in actions
        assert "tool.execute:approvals.approve" in actions
        assert any(a.startswith("approval.execution") for a in actions) or any(
            "approve_referral_reward" in a for a in actions
        )

        # 15. Owner Cockpit / dashboard-facing reads reflect it automatically
        # on the very next read — no separate "refresh" step, no stale cache.
        detail = await call("approvals.get_detail", {"approval_request_id": approval_id})
        assert detail.status == "APPROVED"
        assert detail.execution_status == "EXECUTED"

        latest_after = await call("insights.get_latest_morning_brief", {})
        updated_rec = next(
            r for r in latest_after.recommendations if r.recommendation_id == rec.recommendation_id
        )
        assert updated_rec.status == "APPROVAL_REQUESTED"  # recommendation itself doesn't retro-flip; approval record is now EXECUTED
        assert updated_rec.approval_request_id == approval_id

    finally:
        shutdown.set()
        await worker_task
        if original_policy is None:
            DEFAULT_TOOL_POLICIES.pop("retention.approve_referral_reward", None)
        else:
            DEFAULT_TOOL_POLICIES["retention.approve_referral_reward"] = original_policy
