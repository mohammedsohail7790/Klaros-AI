"""Public, UNAUTHENTICATED lead intake — closes a real gap: `POST /leads`
(app/api/v1/leads.py) requires a logged-in tenant user, so an actual
website visitor or embedded chat widget had no way to submit a lead at
all. This is Klaros' first customer-facing surface with no signed token
(unlike public_quotes/public_contracts, there is no pre-existing entity to
bind a token to) — the trust boundary is simply "this tenant_id exists",
the same boundary the Twilio inbound webhooks accept for their own path
parameter (app/api/v1/webhooks.py).

Deliberately narrow: only `source in {WEB, CHAT}` may be self-reported by
an anonymous caller, and only the handful of fields a real contact-form
or chat widget would ever collect are accepted — no `assigned_user_id`,
`campaign_id`, or `estimated_value` (those stay owner/internal-only, set
via the authenticated `POST /leads` or by AI qualification later).

No rate limiting is applied here — this repository has no rate-limiting
layer anywhere yet (see ARCHITECTURE_TRACEABILITY.md); that is a real,
tracked gap, not something this endpoint should pretend to solve on its
own.
"""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.tool_deps import get_wired_event_bus
from app.core.rate_limit import rate_limit, tenant_and_ip_key
from app.db.session import async_session_maker
from app.events.bus import EventBus
from app.models.organization import Organization
from app.services.lead_service import CreateLeadInput, LeadService

router = APIRouter(prefix="/public/leads", tags=["public-leads"])

_rate_limit_dependency = rate_limit(
    "public_lead", limit_setting="RATE_LIMIT_PUBLIC_LEAD_PER_MINUTE", window_seconds=60, key_func=tenant_and_ip_key
)


class PublicLeadRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    source: Literal["WEB", "CHAT"]
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=255)
    service_requested: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    location: str | None = Field(default=None, max_length=255)
    idempotency_key: str | None = Field(default=None, max_length=255)
    # Honeypot: a real visitor never sees or fills this field (hidden via
    # CSS in the embedding form/widget). Any non-empty value here is a
    # near-certain bot — accept the request but silently drop it rather
    # than creating a lead, so the bot gets no signal that it was caught.
    website: str | None = Field(default=None, max_length=255)


class PublicLeadResponse(BaseModel):
    received: bool
    lead_id: str | None = None


def _lead_service(bus: EventBus) -> LeadService:
    return LeadService(async_session_maker, bus)


@router.post("/{tenant_id}", status_code=status.HTTP_201_CREATED, dependencies=[Depends(_rate_limit_dependency)])
async def submit_public_lead(
    tenant_id: uuid.UUID, body: PublicLeadRequest, bus: EventBus = Depends(get_wired_event_bus)
) -> PublicLeadResponse:
    async with async_session_maker() as session:
        org = (await session.execute(select(Organization).where(Organization.id == tenant_id))).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown tenant")

    if body.website:
        return PublicLeadResponse(received=True, lead_id=None)

    if not body.phone and not body.email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="phone or email is required"
        )

    service = _lead_service(bus)
    lead, _deduplicated = await service.create_lead(
        tenant_id,
        CreateLeadInput(
            name=body.name,
            source=body.source,
            phone=body.phone,
            email=body.email,
            source_detail=f"public_{body.source.lower()}_form",
            service_requested=body.service_requested,
            description=body.description,
            location=body.location,
            idempotency_key=body.idempotency_key,
        ),
    )
    return PublicLeadResponse(received=True, lead_id=str(lead.id))
