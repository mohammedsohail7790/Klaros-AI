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

Medical Tourism vertical integration (KLAROS_MEDICAL_TOURISM_COMPLETION
task, Confirmed Gap #2): after the generic `Lead` row is created, this
endpoint checks the vertical registry (`VerticalExtensionService.
is_enabled_for_organization`, app/services/vertical_extension_service.py —
the documented lookup mechanism, not a hardcoded tenant/business-type
check) for whether the submitting tenant has `medical_tourism` enabled. If
so, it additionally creates a `PatientLead` 1:1 extension row via
`MedicalTourismService.create_patient_lead`, carrying the handful of
medical-specific intake fields a Medical-Tourism-configured public form may
optionally submit. A non-Medical-Tourism tenant's submission never touches
`MedicalTourismService` at all — this branch is glue code in this one
public API handler, not something injected into the generic `LeadService`
core path, matching the extensibility rule that core services never
branch on a vertical name.
"""

import uuid
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.tool_deps import get_wired_event_bus
from app.core.rate_limit import rate_limit, tenant_and_ip_key
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.models.organization import Organization
from app.services.lead_service import CreateLeadInput, LeadService
from app.services.medical_tourism_service import InvalidRelationshipError, MedicalTourismService, NotFoundError
from app.services.vertical_extension_service import VerticalExtensionService

router = APIRouter(prefix="/public/leads", tags=["public-leads"])

MEDICAL_TOURISM_VERTICAL_KEY = "medical_tourism"

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
    # Medical Tourism vertical fields — all optional, ignored entirely for
    # a tenant that doesn't have the medical_tourism vertical enabled (see
    # module docstring). A public Medical-Tourism-configured website form
    # may submit any subset of these.
    procedure_id: uuid.UUID | None = None
    preferred_destination_country: str | None = Field(default=None, max_length=2)
    medical_history_summary: str | None = Field(default=None, max_length=2000)
    travel_start_date: date | None = None
    travel_end_date: date | None = None
    has_insurance: bool | None = None
    insurance_notes: str | None = Field(default=None, max_length=2000)


class PublicLeadResponse(BaseModel):
    received: bool
    lead_id: str | None = None
    patient_lead_id: str | None = None


def _lead_service(bus: EventBus) -> LeadService:
    return LeadService(async_session_maker, bus)


@router.post("/{tenant_id}", status_code=status.HTTP_201_CREATED, dependencies=[Depends(_rate_limit_dependency)])
async def submit_public_lead(
    tenant_id: uuid.UUID, body: PublicLeadRequest, bus: EventBus = Depends(get_wired_event_bus)
) -> PublicLeadResponse:
    async with async_session_maker() as session:
        # tenant_id here is the trusted URL path parameter (this endpoint's
        # entire trust boundary, per its own module docstring) — the same
        # pattern app/api/v1/public_websites.py uses.
        await set_tenant_context(session, tenant_id)
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
    lead, deduplicated = await service.create_lead(
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

    patient_lead_id: str | None = None
    vertical_service = VerticalExtensionService(async_session_maker)
    try:
        medical_tourism_enabled = await vertical_service.is_enabled_for_organization(
            tenant_id, MEDICAL_TOURISM_VERTICAL_KEY
        )
    except Exception:
        # The registry lookup itself (VerticalExtension.get_by_key raising
        # VerticalExtensionNotFoundError when the platform-wide catalog row
        # doesn't exist at all) must never break generic public lead
        # intake -- a non-Medical-Tourism tenant's submission is completely
        # unaffected either way.
        medical_tourism_enabled = False

    if medical_tourism_enabled and not deduplicated:
        # Only attempt the extension on a genuinely new Lead -- a
        # deduplicated resubmission already has (or doesn't need) its own
        # PatientLead row, and create_patient_lead would otherwise raise
        # InvalidRelationshipError on the pre-existing extension.
        mt_service = MedicalTourismService(async_session_maker)
        try:
            patient_lead = await mt_service.create_patient_lead(
                tenant_id,
                lead.id,
                procedure_id=body.procedure_id,
                preferred_destination_country=body.preferred_destination_country,
                medical_history_summary=body.medical_history_summary,
                travel_start_date=body.travel_start_date,
                travel_end_date=body.travel_end_date,
                has_insurance=body.has_insurance,
                insurance_notes=body.insurance_notes,
            )
            patient_lead_id = str(patient_lead.id)
        except NotFoundError:
            # e.g. an invalid/unknown procedure_id was submitted -- the
            # generic Lead is still a valid, useful record; don't fail the
            # whole public submission over an optional enrichment field.
            patient_lead_id = None
        except InvalidRelationshipError:
            patient_lead_id = None

    return PublicLeadResponse(received=True, lead_id=str(lead.id), patient_lead_id=patient_lead_id)
