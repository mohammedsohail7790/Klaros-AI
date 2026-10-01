"""section 8/9: AI qualification.

No LLM is connected in Phase 3 (ANTHROPIC_API_KEY is blank in this repo) —
qualification is real, deterministic, rule-based scoring
(app/services/scoring.py), explicitly labeled as such rather than dressed up
as an AI call that isn't really happening. The output shape (score,
qualification_status, concise reason, recommended action) matches what an
LLM-backed version would return, so swapping in a real model later is a
scoring-function change, not an API/event/workflow change.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.events.bus import EventBus
from app.models.crm import Lead, LeadStatus, QualificationStatus
from app.models.event import EventType
from app.services.enrichment_service import LeadEnrichmentService
from app.services.scoring import score_lead


@dataclass
class QualificationOutcome:
    lead_id: str
    qualification_status: str
    score: int
    reason: str


class LeadNotFoundError(Exception):
    pass


class LeadQualificationService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        bus: EventBus,
        enrichment: LeadEnrichmentService,
    ) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self._enrichment = enrichment

    async def qualify(self, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> QualificationOutcome:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            lead = await session.get(Lead, lead_id)
            if lead is None or lead.tenant_id != tenant_id:
                raise LeadNotFoundError(f"Lead {lead_id} not found for tenant {tenant_id}")

            enrichment = await self._enrichment.enrich(tenant_id, lead)
            result = score_lead(lead, enrichment)

            lead.lead_score = result.score
            lead.score_version = result.version
            lead.score_reason = result.reason
            lead.qualification_status = result.qualification_status
            if result.qualification_status == QualificationStatus.QUALIFIED:
                lead.status = LeadStatus.QUALIFIED
            elif result.qualification_status == QualificationStatus.UNQUALIFIED:
                lead.status = LeadStatus.UNQUALIFIED

            await session.commit()

        event_type = {
            QualificationStatus.QUALIFIED: EventType.LEAD_QUALIFIED,
            QualificationStatus.UNQUALIFIED: EventType.LEAD_UNQUALIFIED,
        }.get(result.qualification_status, EventType.LEAD_UPDATED)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=event_type,
            source="crm.qualification",
            entity_type="lead",
            entity_id=lead_id,
            payload={"score": result.score, "qualification_status": result.qualification_status},
        )

        return QualificationOutcome(
            lead_id=str(lead_id),
            qualification_status=result.qualification_status,
            score=result.score,
            reason=result.reason,
        )
