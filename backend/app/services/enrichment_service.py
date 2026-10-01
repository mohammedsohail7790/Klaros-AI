"""section 7: lead enrichment.

Only internal enrichment (data already in this tenant's own database) is
implemented for real. There is no external enrichment provider connected —
`external` always reports NOT_CONNECTED rather than fabricating a result.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import set_tenant_context
from app.models.crm import Appointment, Customer, Lead
from app.services.customer_matching import normalize_email, normalize_phone


@dataclass
class EnrichmentResult:
    previous_customer: bool
    previous_lead_count: int
    previous_appointment_count: int
    matched_customer_id: str | None
    external_enrichment: dict = field(default_factory=lambda: {"status": "NOT_CONNECTED"})


class LeadEnrichmentService:
    """Business/location/contact enrichment stubs live here as the natural
    extension points for Phase 9 provider adapters (e.g. a business data
    provider, a geocoding provider) — none are wired up yet, so those fields
    are omitted rather than faked. Only internal-data enrichment runs today.
    """

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def enrich(self, tenant_id: uuid.UUID, lead: Lead) -> EnrichmentResult:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            matched_customer = await self._find_customer(session, tenant_id, lead)
            previous_lead_count = 0
            previous_appointment_count = 0

            if matched_customer is not None:
                previous_lead_count = await self._count_previous_leads(
                    session, tenant_id, matched_customer.id, exclude_lead_id=lead.id
                )
                previous_appointment_count = await self._count_appointments(
                    session, tenant_id, matched_customer.id
                )

            return EnrichmentResult(
                previous_customer=matched_customer is not None,
                previous_lead_count=previous_lead_count,
                previous_appointment_count=previous_appointment_count,
                matched_customer_id=str(matched_customer.id) if matched_customer else None,
            )

    async def _find_customer(self, session: AsyncSession, tenant_id, lead: Lead) -> Customer | None:
        if lead.customer_id:
            return await session.get(Customer, lead.customer_id)

        norm_email = normalize_email(lead.email)
        norm_phone = normalize_phone(lead.phone)
        if not norm_email and not norm_phone:
            return None

        from sqlalchemy import or_

        conditions = []
        if norm_email:
            conditions.append(Customer.email == norm_email)
        if norm_phone:
            conditions.append(Customer.phone_normalized == norm_phone)

        result = await session.execute(
            select(Customer).where(Customer.tenant_id == tenant_id, or_(*conditions))
        )
        return result.scalars().first()

    async def _count_previous_leads(
        self, session: AsyncSession, tenant_id: uuid.UUID, customer_id: uuid.UUID, exclude_lead_id: uuid.UUID
    ) -> int:
        from sqlalchemy import func

        result = await session.execute(
            select(func.count(Lead.id)).where(
                Lead.tenant_id == tenant_id,
                Lead.customer_id == customer_id,
                Lead.id != exclude_lead_id,
            )
        )
        return result.scalar_one()

    async def _count_appointments(
        self, session: AsyncSession, tenant_id: uuid.UUID, customer_id: uuid.UUID
    ) -> int:
        from sqlalchemy import func

        result = await session.execute(
            select(func.count(Appointment.id)).where(
                Appointment.tenant_id == tenant_id, Appointment.customer_id == customer_id
            )
        )
        return result.scalar_one()
