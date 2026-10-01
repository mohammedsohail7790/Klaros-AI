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

                if self._comms is not None and customer and customer.email:
                    await self._comms.send_email(
                        tenant_id,
                        to=customer.email,
                        subject=f"Payment reminder: Invoice {invoice.invoice_number}",
                        body=(
                            f"Invoice {invoice.invoice_number} for ${invoice.amount_due} is overdue "
                            f"({action.action_type})."
                        ),
                        template=MessageTemplate.COLLECTION_REMINDER,
                    )

                action.status = CollectionActionStatus.EXECUTED
                executed.append(action.id)

            await session.commit()
        return executed
