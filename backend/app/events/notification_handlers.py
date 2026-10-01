"""Phase 10B: event -> owner notification. Every handler here just calls
`NotificationService.notify()` (app/services/notification_service.py) — the
one place a `Notification` row is ever created for these event types — with
a `dedupe_key` built from the event's own entity_id, so the exact same
underlying event delivered more than once (retry, replay, a duplicate
publish) can never fan out into more than one notification. This is the
existing EventWorker's poll loop doing the delivery; no second worker, no
manual trigger required for a notification to appear.
"""

import uuid

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.events.bus import EventBus
from app.models.approval import ApprovalRequest
from app.models.event import Event, EventType
from app.models.notification import NotificationPriority, NotificationType
from app.models.crm import Lead
from app.models.operations import OperationsException
from app.services.notification_service import NotificationService

logger = structlog.get_logger(__name__)

_EXCEPTION_SEVERITY_PRIORITY = {
    "CRITICAL": NotificationPriority.HIGH,
    "HIGH": NotificationPriority.HIGH,
    "MEDIUM": NotificationPriority.MEDIUM,
    "LOW": NotificationPriority.LOW,
    "INFO": NotificationPriority.LOW,
}


def register_notification_handlers(bus: EventBus, session_factory: async_sessionmaker) -> None:
    notifications = NotificationService(session_factory)

    async def on_approval_requested(event: Event) -> None:
        approval_id = event.entity_id
        async with session_factory() as session:
            await set_tenant_context(session, event.tenant_id)
            request = await session.get(ApprovalRequest, approval_id)
        if request is None:
            return
        await notifications.notify(
            event.tenant_id,
            NotificationType.APPROVAL_REQUIRED,
            title="Approval required",
            body=f"'{request.tool_name}' requires your approval: {request.reason}",
            priority=NotificationPriority.HIGH,
            entity_type="approval_request",
            entity_id=approval_id,
            dedupe_key=f"approval.requested:{approval_id}",
        )

    async def on_approval_approved(event: Event) -> None:
        await notifications.notify(
            event.tenant_id,
            NotificationType.APPROVAL_APPROVED,
            title="Approval decided",
            body="An approval request was approved.",
            priority=NotificationPriority.LOW,
            entity_type="approval_request",
            entity_id=event.entity_id,
            dedupe_key=f"approval.approved:{event.entity_id}",
        )

    async def on_approval_rejected(event: Event) -> None:
        await notifications.notify(
            event.tenant_id,
            NotificationType.APPROVAL_REJECTED,
            title="Approval rejected",
            body="An approval request was rejected.",
            priority=NotificationPriority.LOW,
            entity_type="approval_request",
            entity_id=event.entity_id,
            dedupe_key=f"approval.rejected:{event.entity_id}",
        )

    async def on_execution_completed(event: Event) -> None:
        tool_name = event.payload.get("tool_name", "action")
        await notifications.notify(
            event.tenant_id,
            NotificationType.ACTION_EXECUTED,
            title="Action completed",
            body=f"'{tool_name}' executed successfully after approval.",
            priority=NotificationPriority.MEDIUM,
            entity_type="approval_request",
            entity_id=event.entity_id,
            dedupe_key=f"approval.execution.completed:{event.entity_id}",
        )

    async def on_execution_failed(event: Event) -> None:
        tool_name = event.payload.get("tool_name", "action")
        error = event.payload.get("error") or "unknown error"
        await notifications.notify(
            event.tenant_id,
            NotificationType.ACTION_FAILED,
            title="Action failed",
            body=f"'{tool_name}' failed after approval: {error}",
            priority=NotificationPriority.HIGH,
            entity_type="approval_request",
            entity_id=event.entity_id,
            dedupe_key=f"approval.execution.failed:{event.entity_id}",
        )

    async def on_exception_created(event: Event) -> None:
        # `event.entity_id` is the AFFECTED entity (e.g. the customer), not
        # the OperationsException's own id — exception_service.create_exception
        # publishes it that way so other handlers can find "exceptions
        # about this job/customer"; the exception's own id only ever
        # appears in the payload.
        exception_id = event.payload.get("exception_id")
        if exception_id is None:
            return
        async with session_factory() as session:
            await set_tenant_context(session, event.tenant_id)
            exc = await session.get(OperationsException, uuid.UUID(exception_id))
        if exc is None:
            return
        severity = event.payload.get("severity", "MEDIUM")
        exc_type = event.payload.get("type", "")
        priority = _EXCEPTION_SEVERITY_PRIORITY.get(severity, NotificationPriority.MEDIUM)
        # A subset of exception types map to their own, more specific
        # notification type (spec's INVOICE_OVERDUE / NEGATIVE_FEEDBACK /
        # JOB_DELAYED) — everything else, and anything HIGH/CRITICAL
        # regardless of type, is a HIGH_PRIORITY_EXCEPTION.
        type_map = {
            "INVOICE_OVERDUE": NotificationType.INVOICE_OVERDUE,
            "SERVICE_RECOVERY_REQUIRED": NotificationType.NEGATIVE_FEEDBACK,
            "JOB_DELAYED": NotificationType.JOB_DELAYED,
        }
        notification_type = type_map.get(exc_type)
        if notification_type is None:
            if priority != NotificationPriority.HIGH:
                return  # low/medium generic exceptions don't need a push notification
            notification_type = NotificationType.HIGH_PRIORITY_EXCEPTION
        await notifications.notify(
            event.tenant_id,
            notification_type,
            title=f"{exc_type.replace('_', ' ').title()}",
            body=exc.description,
            priority=priority,
            entity_type=exc.entity_type,
            entity_id=exc.entity_id,
            dedupe_key=f"exception.created:{exc.id}",
        )

    async def on_payment_received(event: Event) -> None:
        amount = event.payload.get("amount")
        await notifications.notify(
            event.tenant_id,
            NotificationType.PAYMENT_RECEIVED,
            title="Payment received",
            body=f"A payment of ${amount} was received." if amount else "A payment was received.",
            priority=NotificationPriority.LOW,
            entity_type="payment",
            entity_id=event.entity_id,
            dedupe_key=f"payment.received:{event.entity_id}",
        )

    async def on_lead_created(event: Event) -> None:
        async with session_factory() as session:
            await set_tenant_context(session, event.tenant_id)
            lead = await session.get(Lead, event.entity_id)
        name = lead.name if lead is not None else "a new lead"
        await notifications.notify(
            event.tenant_id,
            NotificationType.NEW_LEAD,
            title="New lead",
            body=f"New lead: {name}",
            priority=NotificationPriority.LOW,
            entity_type="lead",
            entity_id=event.entity_id,
            dedupe_key=f"lead.created:{event.entity_id}",
        )

    async def on_morning_brief_generated(event: Event) -> None:
        rec_count = event.payload.get("recommendation_count", 0)
        high_count = event.payload.get("high_priority_insight_count", 0)
        if rec_count == 0 and high_count == 0:
            return
        parts = []
        if high_count:
            parts.append(f"{high_count} high priority item(s)")
        if rec_count:
            parts.append(f"{rec_count} recommended action(s)")
        await notifications.notify(
            event.tenant_id,
            NotificationType.MORNING_BRIEF_READY,
            title="Morning Brief ready",
            body=f"Klaros found {' and '.join(parts)}.",
            priority=NotificationPriority.MEDIUM if high_count else NotificationPriority.LOW,
            entity_type="morning_brief",
            entity_id=event.entity_id,
            dedupe_key=f"morning_brief.generated:{event.entity_id}",
        )

    bus.subscribe(EventType.APPROVAL_REQUESTED, "notification_approval_requested_handler", on_approval_requested)
    bus.subscribe(EventType.APPROVAL_APPROVED, "notification_approval_approved_handler", on_approval_approved)
    bus.subscribe(EventType.APPROVAL_REJECTED, "notification_approval_rejected_handler", on_approval_rejected)
    bus.subscribe(
        EventType.APPROVAL_EXECUTION_COMPLETED, "notification_execution_completed_handler", on_execution_completed
    )
    bus.subscribe(
        EventType.APPROVAL_EXECUTION_FAILED, "notification_execution_failed_handler", on_execution_failed
    )
    bus.subscribe(EventType.EXCEPTION_CREATED, "notification_exception_handler", on_exception_created)
    bus.subscribe(EventType.PAYMENT_RECEIVED, "notification_payment_handler", on_payment_received)
    bus.subscribe(EventType.LEAD_CREATED, "notification_lead_handler", on_lead_created)
    bus.subscribe(
        EventType.MORNING_BRIEF_GENERATED, "notification_morning_brief_handler", on_morning_brief_generated
    )
