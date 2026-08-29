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
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import CommunicationProvider, MessageTemplate
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
        action_type = _action_for_days_overdue(days_overdue)
        async with self._session_factory() as session:
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
            await session.commit()
            await session.refresh(action)
        return action

    async def execute_due_actions(self, tenant_id: uuid.UUID) -> list[uuid.UUID]:
        """Deterministic, on-demand execution — no Temporal sleep. Sends a
        reminder via the existing CommunicationProvider for due, PENDING
        actions and marks them EXECUTED."""
        executed: list[uuid.UUID] = []
        now = datetime.now(timezone.utc)

        async with self._session_factory() as session:
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
