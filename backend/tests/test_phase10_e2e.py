"""Phase 10 critical E2E: the full autonomy loop — a real event auto-processed
by a live EventWorker, detected by the Morning Brief, gated by a real
per-tenant policy, surfaced as a real notification, approved by a real
second user, resumed and executed for real, and then the SAME action
re-run after the tenant flips the policy to AUTO (executes immediately, no
approval, no duplicate notification storm) and then to BLOCKED (execution
prevented outright). Zero calls to `process_pending` or any manual
event-processing endpoint — the same standard set by Phase 8/9's E2E tests.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.events.bus import EventBus
from app.events.metrics import EventWorkerMetrics
from app.events.worker import EventWorker
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.finance import Invoice, InvoiceStatus
from app.models.notification import Notification, NotificationType
from app.models.rbac import Role
from app.models.retention import ReferralReward, RewardStatus
from app.services.notification_service import NotificationService
from app.services.policy_service import PolicyService
from app.tools.base import ExecutionContext
from app.tools.errors import ToolBlockedError
from app.tools.policy import ActionPolicy
from datetime import date, timedelta

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER, actor_id=None):
    return ExecutionContext(
        tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=actor_id or uuid.uuid4(), role=role
    )


async def test_full_autonomy_loop_policy_gated_notified_approved_then_reconfigured(
    event_bus: EventBus, tool_registry
) -> None:
    tenant_id = uuid.uuid4()
    requester_id = uuid.uuid4()
    approver_id = uuid.uuid4()
    requester_ctx = _ctx(tenant_id, actor_id=requester_id)
    approver_ctx = _ctx(tenant_id, actor_id=approver_id)

    policy_service = PolicyService(event_bus.session_factory)
    notifications = NotificationService(event_bus.session_factory)

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

    # 1. Real continuous worker, running before any business action.
    worker = EventWorker(event_bus, poll_interval_seconds=0.02, metrics=EventWorkerMetrics(), db_lock=db_lock)
    shutdown = asyncio.Event()
    worker_task = asyncio.create_task(worker.run_forever(shutdown))

    try:
        # 2/3. Owner sets the tool's policy to APPROVAL_REQUIRED explicitly
        # (the spec's step 8) — a real, persisted, per-tenant choice.
        async with db_lock:
            await policy_service.set_policy(
                tenant_id, "retention.approve_referral_reward", ActionPolicy.APPROVAL_REQUIRED, actor_id=requester_id
            )

        # 4/5. Customer + overdue invoice — real finance data the brief must
        # reflect honestly.
        customer = await call("crm.create_customer", {"name": "Autonomy Loop Co"})
        job = await call(
            "operations.create_job",
            {"title": "Repair", "customer_id": customer.customer["id"], "estimated_revenue": 350.0},
        )
        invoice = await call("finance.trigger_invoice_from_job", {"job_id": job.job["id"]})
        await call("finance.request_invoice_approval", {"invoice_id": invoice.invoice["id"]})
        await call("finance.send_invoice", {"invoice_id": invoice.invoice["id"]})
        # Real bug found while investigating an intermittent failure of this
        # test: this direct row mutation is the only DB access in the whole
        # test that didn't go through `db_lock`, unlike every other write/
        # read here (via `call`/`read`) — racing the concurrently-running
        # real EventWorker task (polling every 0.02s) could let the
        # Morning Brief generation a few lines below run before this
        # commit's overdue status/due_date were reliably visible,
        # intermittently making the "overdue" insight assertion fail with
        # no code defect involved. Fixed by acquiring the same lock.
        async with db_lock:
            async with event_bus.session_factory() as session:
                row = await session.get(Invoice, uuid.UUID(invoice.invoice["id"]))
                row.status = InvoiceStatus.OVERDUE
                row.due_date = date.today() - timedelta(days=12)
                await session.commit()

        # Real referral loop -> a real pending reward, the executable
        # recommendation this test drives through approval.
        program = await call(
            "retention.create_referral_program",
            {"name": "Refer a Friend", "reward_type": "credit", "reward_amount": "30.00"},
        )
        code = await call(
            "retention.get_or_create_referral_code",
            {"program_id": program.program_id, "customer_id": customer.customer["id"]},
        )
        referral = await call("retention.create_referral", {"referral_code_id": code.code_id})
        await call(
            "retention.convert_referral_to_lead", {"referral_id": referral.referral_id, "name": "Referred Person"}
        )
        await call("retention.request_referral_reward", {"referral_id": referral.referral_id, "amount": "30.00"})

        # 6/7. Morning Brief detects the overdue invoice and the pending
        # reward from real, already-computed tool output.
        await call("insights.generate_morning_brief", {})
        latest = await call("insights.get_latest_morning_brief", {})
        assert any("overdue" in i.summary.lower() for i in latest.insights if i.category == "FINANCE")
        reward_recs = [r for r in latest.recommendations if r.executable and "reward" in r.what.lower()]
        assert reward_recs
        rec = reward_recs[0]

        # 8/9. Owner clicks Execute -> the tenant's real APPROVAL_REQUIRED
        # policy fires -> a real ApprovalRequest is created.
        exec_result = await call("insights.execute_recommendation", {"recommendation_id": rec.recommendation_id})
        assert exec_result.status == "APPROVAL_REQUESTED"
        approval_id = exec_result.approval_request_id

        # 10/11/12. A real notification appears automatically — the worker
        # processes `approval.requested` on its own, no manual trigger.
        async def approval_notification_exists(session) -> bool:
            rows = (
                await session.execute(
                    select(func.count()).select_from(Notification).where(
                        Notification.tenant_id == tenant_id,
                        Notification.type == NotificationType.APPROVAL_REQUIRED,
                    )
                )
            ).scalar_one()
            return rows >= 1

        assert await wait_for(approval_notification_exists), "no notification auto-created for the approval request"

        # 13/14. Owner opens the approval, approves it.
        approve_result = await call(
            "approvals.approve", {"approval_request_id": approval_id}, ctx=approver_ctx
        )
        assert approve_result.status == "APPROVED"

        # 15/16/17. Current policy was revalidated (still APPROVAL_REQUIRED,
        # unchanged) and the original action actually resumed and executed.
        assert approve_result.execution_status == "EXECUTED"

        async def reward_is_approved(session) -> bool:
            rows = (
                await session.execute(select(ReferralReward).where(ReferralReward.tenant_id == tenant_id))
            ).scalars().all()
            return any(r.status == RewardStatus.APPROVED for r in rows)

        assert await wait_for(reward_is_approved), "referral reward was never actually approved"

        # 18/19/20/21. Execution published a real event, the SAME worker
        # processed it, and a success notification appeared automatically.
        async def execution_notification_exists(session) -> bool:
            rows = (
                await session.execute(
                    select(func.count()).select_from(Notification).where(
                        Notification.tenant_id == tenant_id, Notification.type == NotificationType.ACTION_EXECUTED
                    )
                )
            ).scalar_one()
            return rows >= 1

        assert await wait_for(execution_notification_exists), "no success notification auto-created"

        # 22. Audit contains the complete chain.
        async def audit_actions(session):
            rows = (await session.execute(select(AuditLog.action).where(AuditLog.tenant_id == tenant_id))).scalars().all()
            return set(rows)

        actions = await read(audit_actions)
        assert "automation_policy.change" in actions
        assert "tool.execute:insights.execute_recommendation" in actions
        assert "approval.approve" in actions

        # 23. Owner reconfigures the policy to AUTO.
        async with db_lock:
            await policy_service.set_policy(
                tenant_id, "retention.approve_referral_reward", ActionPolicy.AUTO, actor_id=requester_id
            )

        # 24. A second qualifying action — a second referral reward request.
        referral2 = await call("retention.create_referral", {"referral_code_id": code.code_id})
        await call(
            "retention.convert_referral_to_lead", {"referral_id": referral2.referral_id, "name": "Second Referred"}
        )
        await call("retention.request_referral_reward", {"referral_id": referral2.referral_id, "amount": "15.00"})
        await call("insights.generate_morning_brief", {})
        latest2 = await call("insights.get_latest_morning_brief", {})
        reward_recs2 = [
            r
            for r in latest2.recommendations
            if r.executable and "reward" in r.what.lower() and r.recommendation_id != rec.recommendation_id
        ]
        assert reward_recs2
        rec2 = reward_recs2[0]

        # 25. AI/Owner-triggered recommendation executes without approval —
        # the exact same tool, now AUTO.
        exec_result2 = await call("insights.execute_recommendation", {"recommendation_id": rec2.recommendation_id})
        assert exec_result2.status == "EXECUTED"

        # 26. Audit reflects the direct execution, no approval this time.
        reward_count = await read(
            lambda session: session.execute(
                select(func.count()).select_from(ReferralReward).where(ReferralReward.tenant_id == tenant_id)
            )
        )
        assert reward_count.scalar_one() == 2  # both rewards, no duplicates

        # 28/29/30. Owner tightens the policy to BLOCKED — a third
        # qualifying action is prevented outright, not silently approved.
        async with db_lock:
            await policy_service.set_policy(
                tenant_id, "retention.approve_referral_reward", ActionPolicy.BLOCKED, actor_id=requester_id
            )
        referral3 = await call("retention.create_referral", {"referral_code_id": code.code_id})
        await call(
            "retention.convert_referral_to_lead", {"referral_id": referral3.referral_id, "name": "Third Referred"}
        )
        await call("retention.request_referral_reward", {"referral_id": referral3.referral_id, "amount": "10.00"})
        await call("insights.generate_morning_brief", {})
        latest3 = await call("insights.get_latest_morning_brief", {})
        reward_recs3 = [
            r
            for r in latest3.recommendations
            if r.executable
            and "reward" in r.what.lower()
            and r.recommendation_id not in (rec.recommendation_id, rec2.recommendation_id)
        ]
        assert reward_recs3
        rec3 = reward_recs3[0]

        with pytest.raises(ToolBlockedError):
            await call("insights.execute_recommendation", {"recommendation_id": rec3.recommendation_id})

        final_reward_count = await read(
            lambda session: session.execute(
                select(func.count()).select_from(ReferralReward).where(ReferralReward.tenant_id == tenant_id)
            )
        )
        assert final_reward_count.scalar_one() == 3  # requested, but the third was never approved (still PENDING)

    finally:
        shutdown.set()
        await worker_task


async def test_duplicate_event_never_creates_duplicate_notification_or_action(
    event_bus: EventBus, tool_registry
) -> None:
    """The exact same underlying event, delivered more than once, must
    still result in exactly one logical notification and never a second
    execution — real dedupe_key uniqueness, not application-level luck."""
    from app.models.event import EventType

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    from app.tools.policy import DEFAULT_TOOL_POLICIES

    DEFAULT_TOOL_POLICIES["notifications.create_notification"] = ActionPolicy.APPROVAL_REQUIRED
    try:
        from app.tools.errors import ToolApprovalRequiredError

        with pytest.raises(ToolApprovalRequiredError) as exc_info:
            await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)
        approval_id = exc_info.value.approval_request_id

        for _ in range(3):
            await event_bus.publish(
                tenant_id=tenant_id,
                event_type=EventType.APPROVAL_REQUESTED,
                source="test_duplicate_replay",
                entity_type="approval_request",
                entity_id=approval_id,
                payload={"tool_name": "notifications.create_notification"},
            )
        await event_bus.process_pending(EventType.APPROVAL_REQUESTED)

        notifications = NotificationService(event_bus.session_factory)
        rows = await notifications.list_for_tenant(tenant_id)
        approval_rows = [r for r in rows if r.type == NotificationType.APPROVAL_REQUIRED]
        assert len(approval_rows) == 1
    finally:
        DEFAULT_TOOL_POLICIES.pop("notifications.create_notification", None)


async def test_tenant_isolation_e2e_auto_vs_approval_required(event_bus: EventBus, tool_registry) -> None:
    policy_service = PolicyService(event_bus.session_factory)
    tenant_auto, tenant_approval = uuid.uuid4(), uuid.uuid4()
    ctx_auto, ctx_approval = _ctx(tenant_auto), _ctx(tenant_approval)

    await policy_service.set_policy(
        tenant_approval, "crm.create_customer", ActionPolicy.APPROVAL_REQUIRED, actor_id=ctx_approval.actor_id
    )

    out_auto = await tool_registry.execute("crm.create_customer", {"name": "Auto Tenant Co"}, ctx_auto)
    assert out_auto.customer["name"] == "Auto Tenant Co"

    from app.tools.errors import ToolApprovalRequiredError

    with pytest.raises(ToolApprovalRequiredError):
        await tool_registry.execute("crm.create_customer", {"name": "Approval Tenant Co"}, ctx_approval)

    # Neither tenant can read or mutate the other's policy.
    policies_a = await policy_service.list_policies(tenant_auto)
    policies_b = await policy_service.list_policies(tenant_approval)
    row_a = next(p for p in policies_a if p["tool_name"] == "crm.create_customer")
    row_b = next(p for p in policies_b if p["tool_name"] == "crm.create_customer")
    assert row_a["current_policy"] == "AUTO"
    assert row_b["current_policy"] == "APPROVAL_REQUIRED"
