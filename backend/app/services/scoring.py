"""section 9: deterministic lead scoring.

This is real, rule-based scoring — not an LLM call. No service-area or
service-catalog configuration exists yet (Phase 4+), so "service fit" and
"location fit" degrade to a simpler presence check for now; that
simplification is called out in the returned reason and in
PROJECT_STATUS.md, not hidden.
"""

from dataclasses import dataclass

from app.models.crm import Lead, LeadSource, Urgency
from app.services.enrichment_service import EnrichmentResult

SCORE_VERSION = "v1-deterministic"

_URGENCY_POINTS = {
    Urgency.EMERGENCY: 25,
    Urgency.HIGH: 15,
    Urgency.MEDIUM: 5,
    Urgency.LOW: 0,
}

_SOURCE_QUALITY_POINTS = {
    LeadSource.REFERRAL: 15,
    LeadSource.PHONE: 10,
    LeadSource.WALK_IN: 10,
    LeadSource.WEB: 5,
    LeadSource.CHAT: 5,
    LeadSource.TEXT: 5,
    LeadSource.DM: 0,
    LeadSource.MARKETPLACE: 0,
}

QUALIFIED_THRESHOLD = 70
UNQUALIFIED_THRESHOLD = 30


@dataclass
class ScoreResult:
    score: int
    version: str
    reason: str
    qualification_status: str


def score_lead(lead: Lead, enrichment: EnrichmentResult) -> ScoreResult:
    from app.models.crm import QualificationStatus

    points = 50
    reasons: list[str] = []

    urgency_pts = _URGENCY_POINTS.get(lead.urgency, 0)
    points += urgency_pts
    if urgency_pts:
        reasons.append(f"urgency={lead.urgency} (+{urgency_pts})")

    if lead.service_requested:
        points += 10
        reasons.append("requested a specific service (+10)")

    if lead.location:
        points += 5
        reasons.append("location provided (+5)")

    if enrichment.previous_customer:
        points += 15
        reasons.append("returning customer (+15)")

    if lead.estimated_value:
        value = float(lead.estimated_value)
        if value >= 5000:
            points += 15
            reasons.append(f"high estimated value ${value:,.0f} (+15)")
        elif value >= 1000:
            points += 10
            reasons.append(f"estimated value ${value:,.0f} (+10)")

    source_pts = _SOURCE_QUALITY_POINTS.get(lead.source, 0)
    points += source_pts
    if source_pts:
        reasons.append(f"source={lead.source} (+{source_pts})")

    points = max(0, min(100, points))

    if points >= QUALIFIED_THRESHOLD:
        status = QualificationStatus.QUALIFIED
        action = "Contact within 15 minutes."
    elif points < UNQUALIFIED_THRESHOLD:
        status = QualificationStatus.UNQUALIFIED
        action = "No action — below qualification threshold."
    else:
        status = QualificationStatus.REQUIRES_HUMAN
        action = "Borderline score — route to a human for review."

    reason = (
        f"Score {points}/100 ({', '.join(reasons) if reasons else 'no scoring factors matched'}). "
        f"Recommended action: {action}"
    )

    return ScoreResult(score=points, version=SCORE_VERSION, reason=reason, qualification_status=status)
