"""section 15: collections, built on a deterministic 'next action due'
record rather than a Temporal `workflow.sleep()` — see
`CollectionAction.scheduled_for`. Nothing here sleeps; `execute_due_actions`
is meant to be called on demand (API trigger or a scheduled job), mirroring
Phase 4's `DelayDetectionService` pattern.

The days-overdue -> action policy is a static table (the same
simplification Phase 3's scoring and Phase 4's `OVERDUE_TOLERANCE_MINUTES`
use) — per-tenant configurability is a named limitation, not implemented.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import CommunicationProvider, MessageTemplate
from app.db.session import set_tenant_context
from app.models.crm import Customer
from app.models.finance import (
    CollectionAction,
    CollectionActionStatus,
    CollectionActionType,
    Invoice,
)

# (days_overdue threshold, action type) — first match wins, evaluated ascending.
COLLECTION_POLICY: list[tuple[int, CollectionActionType]] = [
    (0, CollectionActionType.FRIENDLY_REMINDER),
    (15, CollectionActionType.SECOND_REMINDER),
    (30, CollectionActionType.ESCALATION),
    (45, CollectionActionType.OWNER_REVIEW),
    (60, CollectionActionType.COLLECTION_REFERRAL),
]


def _action_for_days_overdue(days_overdue: int) -> CollectionActionType:
    chosen = COLLECTION_POLICY[0][1]
    for threshold, action_type in COLLECTION_POLICY:
        if days_overdue >= threshold:
            chosen = action_type
    return chosen


def reminder_body(customer_name: str | None, invoice: Invoice, action_type: CollectionActionType) -> str:
    """A plain reminder in the invoice's own currency (never an assumed dollar sign), with the number, amount and due date, and what to do if it was already paid."""
    first = action_type == CollectionActionType.FRIENDLY_REMINDER
    opening = "A quick reminder that" if first else "Following our earlier reminder, we have not yet received payment for the invoice below. As a reminder,"
    return (
        f"Hello {customer_name or 'there'},\n\n{opening} invoice {invoice.invoice_number} for {invoice.currency} {invoice.amount_due:,.2f} was due on "
        f"{invoice.due_date.isoformat()} and is still open.\n\nIf you have already paid it, thank you, and please reply with the payment date so we can match it. "
        "If anything about the invoice is unclear, reply to this email and we will sort it out.\n"
    )


class CollectionService:
    def __init__(self, session_factory: async_sessionmaker, comms: CommunicationProvider | None = None) -> None:
        self._session_factory = session_factory
        self._comms = comms

    async def schedule_next_action(
        self, tenant_id: uuid.UUID, invoice_id: uuid.UUID, *, days_overdue: int
    ) -> CollectionAction:
        """Phase 29 fix: the pre-existing check-then-insert (SELECT for an
        existing row, INSERT if none found) is exactly the same
        check-then-act shape `ExceptionService.create_exception` already
        handles safely — but this method was missing the second half of
        that pattern. A real PostgreSQL concurrency test
        (`ARService.detect_overdue()` called 10x concurrently for the
        same overdue invoice) proved two concurrent callers can both pass
        the "no existing action" check before either commits; the second
        one's INSERT then hits the real, pre-existing
        `uq_collection_actions_tenant_invoice_type` unique constraint and
        raised an *unhandled* `IntegrityError`, crashing the sweep's
        per-invoice downstream loop (and, since that loop has no
        try/except, aborting processing of every subsequent invoice in
        the same batch). The constraint was always correct and already
        does the real work — this method just never caught the race it
        makes possible. Fixed with the identical try/except/rollback/
        re-fetch shape `create_exception` and `CompanyMemoryService`'s
        supersession logic already use: the loser's INSERT fails, it
        rolls back its own (otherwise-poisoned) transaction, and returns
        the winner's real row instead of raising."""
        action_type = _action_for_days_overdue(days_overdue)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            existing = (
                await session.execute(
                    select(CollectionAction).where(
                        CollectionAction.tenant_id == tenant_id,
                        CollectionAction.invoice_id == invoice_id,
                        CollectionAction.action_type == action_type,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

            action = CollectionAction(
                tenant_id=tenant_id,
                invoice_id=invoice_id,
                action_type=action_type,
                scheduled_for=datetime.now(timezone.utc),
                status=CollectionActionStatus.PENDING,
            )
            session.add(action)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = (
                    await session.execute(
                        select(CollectionAction).where(
                            CollectionAction.tenant_id == tenant_id,
                            CollectionAction.invoice_id == invoice_id,
                            CollectionAction.action_type == action_type,
                        )
                    )
                ).scalar_one()
                return existing
            await session.refresh(action)
        return action

    async def execute_due_actions(self, tenant_id: uuid.UUID) -> list[uuid.UUID]:
        """Deterministic, on-demand execution — no Temporal sleep. Sends a
        reminder via the existing CommunicationProvider for due, PENDING
        actions and marks them EXECUTED."""
        executed: list[uuid.UUID] = []
        now = datetime.now(timezone.utc)

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            due = (
                await session.execute(
                    select(CollectionAction).where(
                        CollectionAction.tenant_id == tenant_id,
                        CollectionAction.status == CollectionActionStatus.PENDING,
                        CollectionAction.scheduled_for <= now,
                    )
                )
            ).scalars().all()

            for action in due:
                invoice = await session.get(Invoice, action.invoice_id)
                if invoice is None:
                    continue
                customer = await session.get(Customer, invoice.customer_id)

                if customer and customer.email and customer.external_provider == "mt_provider":
                    # A partner clinic is a business, not a patient: its reminders use the clinic channel, not the patient consent guard. A failed send stays PENDING to be retried.
                    from app.communications.factory import get_clinic_channel

                    sent = await get_clinic_channel(self._session_factory).send_email(
                        tenant_id, to=customer.email, subject=f"Payment reminder: Invoice {invoice.invoice_number}",
                        body=reminder_body(customer.name, invoice, action.action_type), template=MessageTemplate.COLLECTION_REMINDER,
                    )
                    if not sent:
                        continue
                elif self._comms is not None and customer and customer.email:
                    result = await self._comms.deliver_email(
                        tenant_id,
                        to=customer.email,
                        customer_id=customer.id,
                        subject=f"Payment reminder: Invoice {invoice.invoice_number}",
                        body=reminder_body(customer.name, invoice, action.action_type),
                        template=MessageTemplate.COLLECTION_REMINDER,
                    )
                    if result.blocked:
                        action.status = CollectionActionStatus.BLOCKED_CONSENT
                        continue

                action.status = CollectionActionStatus.EXECUTED
                executed.append(action.id)

            await session.commit()
        return executed
