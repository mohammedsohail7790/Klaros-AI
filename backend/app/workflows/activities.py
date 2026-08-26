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

from app.core.logging import configure_logging
from app.db.session import async_session_maker
from app.events.factory import get_event_bus
from app.models.actor import ActorType
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
]
