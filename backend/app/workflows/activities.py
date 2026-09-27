"""Temporal activities (section 3).

`execute_tool_activity` is the one real, general-purpose activity: it goes
through the same ToolRegistry as everything else, so a workflow step is
authorized, validated, and audited exactly like a direct API call.

`send_reminder_activity` and `check_payment_status_activity` back the demo
`InvoiceOverdueWorkflow` below. They are explicitly internal test actions —
there is no Finance module or Communication provider wired up yet (Phase 5),
so they do not call QuickBooks, Stripe, Twilio, or SendGrid. They log and
return a fixed, clearly-labeled result so the workflow's control flow (wait /
remind / check / escalate) can be exercised for real without pretending an
integration exists.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from temporalio import activity

from app.ai.execution_service import AIExecutionService
from app.core.logging import configure_logging
from app.db.session import async_session_maker, set_tenant_context
from app.events.factory import get_event_bus
from app.models.actor import ActorType
from app.models.automation import AutomationVersion
from app.models.rbac import Role
from app.tools.base import ExecutionContext
from app.tools.errors import ToolApprovalRequiredError
from app.tools.factory import build_tool_registry
from app.services.enrichment_service import LeadEnrichmentService
from app.services.qualification_service import LeadQualificationService

logger = structlog.get_logger(__name__)


@activity.defn
async def execute_tool_activity(
    tool_name: str,
    tool_input: dict[str, Any],
    tenant_id: str,
    role: str | None,
    correlation_id: str | None,
) -> dict[str, Any]:
    configure_logging()
    registry = build_tool_registry(async_session_maker, get_event_bus())
    context = ExecutionContext(
        tenant_id=uuid.UUID(tenant_id),
        actor_type=ActorType.WORKFLOW,
        actor_id=None,
        role=Role(role) if role else None,
        correlation_id=uuid.UUID(correlation_id) if correlation_id else None,
    )
    try:
        output = await registry.execute(tool_name, tool_input, context)
        return output.model_dump(mode="json")
    except ToolApprovalRequiredError as exc:
        return {"pending_approval": True, "approval_request_id": str(exc.approval_request_id)}


@activity.defn
async def resume_automation_execution_activity(execution_id: str, tenant_id: str) -> dict[str, Any]:
    """The Automation Engine's durable-wait resume point (Rule 7/16 — see
    app/services/automation_service.py's docstring). Re-fetches the
    execution's real, CURRENT context and condition from PostgreSQL (never
    trusts anything cached in the workflow itself) and runs the remaining
    steps through the exact same `AutomationService.run_steps` a
    synchronous (no-wait) automation uses — the only difference is that a
    real Temporal timer, not this activity, is what made the wait itself
    durable."""
    configure_logging()
    from app.models.automation import AutomationExecution, ExecutionStatus
    from app.services.automation_condition import evaluate_condition
    from app.services.automation_service import AutomationService

    registry = build_tool_registry(async_session_maker, get_event_bus())
    ai_execution = AIExecutionService(registry)
    service = AutomationService(async_session_maker, ai_execution)
    tenant_uuid = uuid.UUID(tenant_id)
    execution_uuid = uuid.UUID(execution_id)

    # Phase 0 §0.2: this activity runs outside any HTTP request, so there is
    # no get_current_user() to establish tenant context — the Temporal
    # workflow input (tenant_id, already required by this activity's own
    # signature) is the only trustworthy source here, matching the "worker
    # context" tenant-identity trace required by the Phase 0 plan.
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_uuid)
        execution = await session.get(AutomationExecution, execution_uuid)
        if execution is None or execution.tenant_id != tenant_uuid:
            return {"status": "not_found"}
        version = await session.get(AutomationVersion, execution.automation_version_id)

    if not evaluate_condition(version.condition, execution.context):
        async with async_session_maker() as session:
            await set_tenant_context(session, tenant_uuid)
            row = await session.get(AutomationExecution, execution_uuid)
            row.status = ExecutionStatus.COMPLETED
            row.completed_at = datetime.now(timezone.utc)
            await session.commit()
        return {"status": "condition_not_met"}

    await service.run_steps(tenant_uuid, execution_uuid, version.steps, start_index=1)
    return {"status": "resumed"}


@activity.defn
async def send_reminder_activity(invoice_id: str, tenant_id: str) -> dict[str, Any]:
    del tenant_id
    logger.info(
        "invoice_reminder_sent_INTERNAL_TEST_ACTION",
        invoice_id=invoice_id,
        note="not connected to a real email/SMS provider — Phase 5 Finance module required",
    )
    return {"sent": True, "channel": "internal-test-action", "sent_at": datetime.now(timezone.utc).isoformat()}


@activity.defn
async def check_payment_status_activity(invoice_id: str, tenant_id: str) -> dict[str, Any]:
    del tenant_id
    logger.info(
        "invoice_payment_check_INTERNAL_TEST_ACTION",
        invoice_id=invoice_id,
        note="always reports unpaid — no real Finance module/Stripe adapter connected yet",
    )
    return {"paid": False}


@activity.defn
async def qualify_lead_activity(lead_id: str, tenant_id: str) -> dict[str, Any]:
    """Backs LeadQualificationWorkflow (section 24). Calls the same
    LeadQualificationService the event-bus handler uses (app/events/crm_handlers.py) —
    one qualification implementation, two ways to trigger it.
    """
    configure_logging()
    bus = get_event_bus()
    enrichment = LeadEnrichmentService(async_session_maker)
    service = LeadQualificationService(async_session_maker, bus, enrichment)
    outcome = await service.qualify(uuid.UUID(tenant_id), uuid.UUID(lead_id))
    return outcome.__dict__


ACTIVITIES = [
    execute_tool_activity,
    send_reminder_activity,
    check_payment_status_activity,
    qualify_lead_activity,
    resume_automation_execution_activity,
]
