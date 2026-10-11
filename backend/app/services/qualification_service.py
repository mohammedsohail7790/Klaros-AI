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

    async def qualify(self, tenant_id: uuid.UUID, lead_id: uuid.UUID, *, preserve_decided: bool = False, by_person: bool = False) -> QualificationOutcome:
        """`preserve_decided=True` is for the AUTOMATIC qualification a new lead triggers: if someone or something else (a
        person, or the AI workforce reporting through Halla) has already decided this lead, the automatic score is still
        recorded but never overwrites that decision — the handler runs asynchronously, so it can land after it. An explicit
        'qualify this lead' action leaves it False and always applies."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            lead = await session.get(Lead, lead_id)
            if lead is None or lead.tenant_id != tenant_id:
                raise LeadNotFoundError(f"Lead {lead_id} not found for tenant {tenant_id}")

            enrichment = await self._enrichment.enrich(tenant_id, lead)
            result = score_lead(lead, enrichment)

            # A lead handed to a person (safety category, or Halla's needs_human_review) is never re-qualified by a machine: the score is
            # still recorded, the decision stays with a person. Only an explicit action by a signed-in person may move it on.
            held_for_person = lead.qualification_status == QualificationStatus.REQUIRES_HUMAN and not by_person
            decided = held_for_person or preserve_decided and (
                lead.qualification_status != QualificationStatus.PENDING
                or lead.status not in (LeadStatus.NEW, LeadStatus.CONTACTED)
            )
            lead.lead_score = result.score
            lead.score_version = result.version
            lead.score_reason = result.reason
            if not decided:
                lead.qualification_status = result.qualification_status
                if result.qualification_status == QualificationStatus.QUALIFIED:
                    lead.status = LeadStatus.QUALIFIED
                elif result.qualification_status == QualificationStatus.UNQUALIFIED:
                    lead.status = LeadStatus.UNQUALIFIED
            final_status = lead.qualification_status

            await session.commit()

        if decided:
            # A neutral update: announcing "qualified"/"unqualified" here would be a claim about a decision this step did not make.
            return QualificationOutcome(lead_id=str(lead_id), qualification_status=final_status, score=result.score, reason=result.reason)

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
