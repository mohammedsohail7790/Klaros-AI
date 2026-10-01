"""section 6: the lead creation flow.

    input -> validate (Pydantic, at the API layer) -> normalize -> find
    possible existing customer -> create/update lead -> persist -> publish
    lead.created

Deliberately does NOT run qualification synchronously — that happens via the
lead.created event (see app/events/crm_handlers.py), so the API call that
creates a lead returns immediately regardless of how long qualification
takes.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.events.bus import EventBus
from app.models.crm import Lead, LeadStatus, QualificationStatus
from app.models.event import EventType
from app.services.customer_matching import find_matching_customer, normalize_email, normalize_phone


class DuplicateLeadError(Exception):
    def __init__(self, existing_lead: Lead) -> None:
        super().__init__("Lead with this idempotency key already exists")
        self.existing_lead = existing_lead


@dataclass
class CreateLeadInput:
    name: str
    source: str
    phone: str | None = None
    email: str | None = None
    source_detail: str | None = None
    campaign_id: uuid.UUID | None = None
    service_requested: str | None = None
    description: str | None = None
    location: str | None = None
    urgency: str = "MEDIUM"
    estimated_value: float | None = None
    idempotency_key: str | None = None
    assigned_user_id: uuid.UUID | None = None


class LeadService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus) -> None:
        self._session_factory = session_factory
        self._bus = bus

    async def create_lead(self, tenant_id: uuid.UUID, data: CreateLeadInput) -> tuple[Lead, bool]:
        """Returns (lead, was_deduplicated).

        Phase 30 fix: the `idempotency_key` check-then-insert below is
        backed by a real `uq_leads_tenant_idempotency_key` unique
        constraint, but — like `ContractService.create_from_quote` and
        `RetentionService._create_opportunity` (same audit, same fix) —
        never caught the `IntegrityError` a genuine concurrent duplicate
        submission would raise. Public lead intake (`POST /public/leads`)
        and marketplace webhooks are exactly the kind of caller that
        realistically retries the same idempotency key concurrently (a
        sender's timeout-triggered retry racing the original in-flight
        request). Fixed with the same try/except/rollback/re-fetch CAS
        pattern already proven in Phase 29's `CollectionService.
        schedule_next_action` fix."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            if data.idempotency_key:
                existing = (
                    await session.execute(
                        select(Lead).where(
                            Lead.tenant_id == tenant_id, Lead.idempotency_key == data.idempotency_key
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return existing, True

            matched_customer = await find_matching_customer(
                session, tenant_id=tenant_id, email=data.email, phone=data.phone
            )

            lead = Lead(
                tenant_id=tenant_id,
                customer_id=matched_customer.id if matched_customer else None,
                name=data.name,
                phone=data.phone,
                phone_normalized=normalize_phone(data.phone),
                email=normalize_email(data.email),
                source=data.source,
                source_detail=data.source_detail,
                campaign_id=data.campaign_id,
                service_requested=data.service_requested,
                description=data.description,
                location=data.location,
                urgency=data.urgency,
                estimated_value=data.estimated_value,
                status=LeadStatus.NEW,
                qualification_status=QualificationStatus.PENDING,
                assigned_user_id=data.assigned_user_id,
                idempotency_key=data.idempotency_key,
            )
            session.add(lead)
            if data.idempotency_key:
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    existing = (
                        await session.execute(
                            select(Lead).where(
                                Lead.tenant_id == tenant_id, Lead.idempotency_key == data.idempotency_key
                            )
                        )
                    ).scalar_one()
                    return existing, True
            else:
                await session.commit()
            await session.refresh(lead)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.LEAD_CREATED,
            source="crm",
            entity_type="lead",
            entity_id=lead.id,
            payload={
                "lead_id": str(lead.id),
                "matched_existing_customer": matched_customer is not None,
            },
            idempotency_key=f"lead-created-{lead.id}",
        )

        return lead, False
