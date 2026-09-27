"""Phase 10 (Medical Tourism vertical extension): ToolRegistry tools for
the domain operations HARD SCOPE / KLAROS_MEDICAL_TOURISM_VALIDATION.md's
walkthrough genuinely requires — search providers, get provider detail,
search procedures, list a provider's offerings, create a provider, create
a procedure, create a provider offering. No tool is created merely
because an API endpoint exists (Phase 9 instruction) — e.g. update/verify-
credential and the PatientLead/Consultation/ReferralCommission extension
writes are reachable only via the API + service layer directly, not
exposed as agent-callable tools in this phase, since nothing in the
validated walkthrough requires an agent to perform them autonomously (the
validation doc's own table marks provider-matching as the one genuinely
agent-relevant action, which `search_providers`/`search_procedures`/
`list_provider_offerings` already serve as read-only building blocks for).

Idempotency classification (Phase 8 discipline, reapplied here — see
PHASE_10_MEDICAL_TOURISM_IMPLEMENTATION_LOG.md's per-tool table):

  - `medical_tourism.search_providers` / `get_provider` /
    `search_procedures` / `list_provider_offerings`: read-only, verified
    by direct inspection (each `execute()` below issues only SELECT
    statements via MedicalTourismService, no writes) -> `True`, a genuine
    "naturally idempotent by construction" case, not a name-based guess.
  - `medical_tourism.create_provider` / `create_procedure` /
    `create_provider_offering`: each has a real DB-level unique-constraint
    dedup mechanism (idempotency_key / provider+procedure uniqueness) with
    the same concurrency-safe try/except-IntegrityError/re-fetch pattern
    LeadService.create_lead uses. Left at the conservative default
    `False` anyway, for exact consistency with this codebase's own
    established precedent: `crm.create_lead` (app/tools/builtin/crm_tools.py)
    has the identical mechanism and was deliberately left `False` in the
    Phase 7/8 audit (PHASE_8_TOOL_IDEMPOTENCY_AUDIT.md's "223 tools remain
    False" rule) — introducing a `True` here for the same mechanism would
    create an unexplained inconsistency across the tool catalog, not a
    more accurate claim.
"""

import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.models.rbac import Permission
from app.services.medical_tourism_service import (
    CreateOfferingInput,
    CreateProcedureInput,
    CreateProviderInput,
    MedicalTourismService,
    NotFoundError,
)
from app.tools.base import ExecutionContext, Tool


def _provider_to_dict(p) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "name": p.name,
        "practitioner_name": p.practitioner_name,
        "country": p.country,
        "city": p.city,
        "contact_email": p.contact_email,
        "contact_phone": p.contact_phone,
        "status": p.status,
    }


def _procedure_to_dict(p) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "name": p.name,
        "category": p.category,
        "description": p.description,
        "status": p.status,
    }


def _offering_to_dict(o) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "provider_id": str(o.provider_id),
        "procedure_id": str(o.procedure_id),
        "estimated_price": str(o.estimated_price) if o.estimated_price is not None else None,
        "currency": o.currency,
        "status": o.status,
    }


# --- search_providers --------------------------------------------------


class SearchProvidersInput(BaseModel):
    country: str | None = None
    status: str | None = "ACTIVE"
    limit: int = Field(default=50, le=200, ge=1)
    offset: int = Field(default=0, ge=0)


class SearchProvidersOutput(BaseModel):
    providers: list[dict[str, Any]]
    total: int


class SearchProviders(Tool):
    name = "medical_tourism.search_providers"
    description = "Search the tenant's Medical Tourism provider (hospital/clinic) directory, optionally filtered by destination country and status."
    input_schema = SearchProvidersInput
    output_schema = SearchProvidersOutput
    required_permission = Permission.READ_MEDICAL_TOURISM
    supports_idempotency = True

    def __init__(self, service: MedicalTourismService) -> None:
        self._service = service

    async def execute(self, input: SearchProvidersInput, context: ExecutionContext) -> SearchProvidersOutput:
        providers, total = await self._service.list_providers(
            context.tenant_id,
            country=input.country,
            status=input.status,
            limit=input.limit,
            offset=input.offset,
        )
        return SearchProvidersOutput(providers=[_provider_to_dict(p) for p in providers], total=total)


# --- get_provider --------------------------------------------------------


class GetProviderInput(BaseModel):
    provider_id: uuid.UUID


class GetProviderOutput(BaseModel):
    provider: dict[str, Any]


class GetProvider(Tool):
    name = "medical_tourism.get_provider"
    description = "Get a single Medical Tourism provider's detail by id."
    input_schema = GetProviderInput
    output_schema = GetProviderOutput
    required_permission = Permission.READ_MEDICAL_TOURISM
    supports_idempotency = True

    def __init__(self, service: MedicalTourismService) -> None:
        self._service = service

    async def execute(self, input: GetProviderInput, context: ExecutionContext) -> GetProviderOutput:
        try:
            provider = await self._service.get_provider(context.tenant_id, input.provider_id)
        except NotFoundError as exc:
            raise ValueError(str(exc)) from exc
        return GetProviderOutput(provider=_provider_to_dict(provider))


# --- search_procedures ----------------------------------------------------


class SearchProceduresInput(BaseModel):
    category: str | None = None
    status: str | None = "ACTIVE"
    limit: int = Field(default=50, le=200, ge=1)
    offset: int = Field(default=0, ge=0)


class SearchProceduresOutput(BaseModel):
    procedures: list[dict[str, Any]]
    total: int


class SearchProcedures(Tool):
    name = "medical_tourism.search_procedures"
    description = "Search the tenant's Medical Tourism procedure catalog, optionally filtered by category and status."
    input_schema = SearchProceduresInput
    output_schema = SearchProceduresOutput
    required_permission = Permission.READ_MEDICAL_TOURISM
    supports_idempotency = True

    def __init__(self, service: MedicalTourismService) -> None:
        self._service = service

    async def execute(self, input: SearchProceduresInput, context: ExecutionContext) -> SearchProceduresOutput:
        procedures, total = await self._service.list_procedures(
            context.tenant_id,
            category=input.category,
            status=input.status,
            limit=input.limit,
            offset=input.offset,
        )
        return SearchProceduresOutput(procedures=[_procedure_to_dict(p) for p in procedures], total=total)


# --- list_provider_offerings ----------------------------------------------


class ListProviderOfferingsInput(BaseModel):
    provider_id: uuid.UUID | None = None
    procedure_id: uuid.UUID | None = None
    status: str | None = "ACTIVE"
    limit: int = Field(default=50, le=200, ge=1)
    offset: int = Field(default=0, ge=0)


class ListProviderOfferingsOutput(BaseModel):
    offerings: list[dict[str, Any]]
    total: int


class ListProviderOfferings(Tool):
    name = "medical_tourism.list_provider_offerings"
    description = "List which providers offer which procedures (and at what estimated price), optionally filtered by provider or procedure."
    input_schema = ListProviderOfferingsInput
    output_schema = ListProviderOfferingsOutput
    required_permission = Permission.READ_MEDICAL_TOURISM
    supports_idempotency = True

    def __init__(self, service: MedicalTourismService) -> None:
        self._service = service

    async def execute(
        self, input: ListProviderOfferingsInput, context: ExecutionContext
    ) -> ListProviderOfferingsOutput:
        offerings, total = await self._service.list_offerings(
            context.tenant_id,
            provider_id=input.provider_id,
            procedure_id=input.procedure_id,
            status=input.status,
            limit=input.limit,
            offset=input.offset,
        )
        return ListProviderOfferingsOutput(offerings=[_offering_to_dict(o) for o in offerings], total=total)


# --- create_provider --------------------------------------------------------


class CreateProviderToolInput(BaseModel):
    name: str
    country: str
    practitioner_name: str | None = None
    city: str | None = None
    address: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    description: str | None = None
    idempotency_key: str | None = None


class CreateProviderOutput(BaseModel):
    provider: dict[str, Any]
    deduplicated: bool = False


class CreateProvider(Tool):
    name = "medical_tourism.create_provider"
    description = "Create a new Medical Tourism provider (hospital/clinic), deduplicating on idempotency_key."
    input_schema = CreateProviderToolInput
    output_schema = CreateProviderOutput
    required_permission = Permission.MANAGE_MEDICAL_TOURISM

    def __init__(self, service: MedicalTourismService) -> None:
        self._service = service

    async def execute(self, input: CreateProviderToolInput, context: ExecutionContext) -> CreateProviderOutput:
        provider, deduplicated = await self._service.create_provider(
            context.tenant_id,
            CreateProviderInput(
                name=input.name,
                country=input.country,
                practitioner_name=input.practitioner_name,
                city=input.city,
                address=input.address,
                contact_email=input.contact_email,
                contact_phone=input.contact_phone,
                description=input.description,
                idempotency_key=input.idempotency_key,
            ),
        )
        return CreateProviderOutput(provider=_provider_to_dict(provider), deduplicated=deduplicated)


# --- create_procedure --------------------------------------------------------


class CreateProcedureToolInput(BaseModel):
    name: str
    category: str | None = None
    description: str | None = None
    typical_destination_countries: str | None = None
    idempotency_key: str | None = None


class CreateProcedureOutput(BaseModel):
    procedure: dict[str, Any]
    deduplicated: bool = False


class CreateProcedure(Tool):
    name = "medical_tourism.create_procedure"
    description = "Create a new Medical Tourism procedure catalog entry, deduplicating on idempotency_key."
    input_schema = CreateProcedureToolInput
    output_schema = CreateProcedureOutput
    required_permission = Permission.MANAGE_MEDICAL_TOURISM

    def __init__(self, service: MedicalTourismService) -> None:
        self._service = service

    async def execute(self, input: CreateProcedureToolInput, context: ExecutionContext) -> CreateProcedureOutput:
        procedure, deduplicated = await self._service.create_procedure(
            context.tenant_id,
            CreateProcedureInput(
                name=input.name,
                category=input.category,
                description=input.description,
                typical_destination_countries=input.typical_destination_countries,
                idempotency_key=input.idempotency_key,
            ),
        )
        return CreateProcedureOutput(procedure=_procedure_to_dict(procedure), deduplicated=deduplicated)


# --- create_provider_offering -------------------------------------------------


class CreateProviderOfferingInput(BaseModel):
    provider_id: uuid.UUID
    procedure_id: uuid.UUID
    estimated_price: Decimal | None = None
    currency: str | None = None


class CreateProviderOfferingOutput(BaseModel):
    offering: dict[str, Any]
    deduplicated: bool = False


class CreateProviderOffering(Tool):
    name = "medical_tourism.create_provider_offering"
    description = "Associate a provider with a procedure it offers, at an optional estimated price. Deduplicates on (provider, procedure)."
    input_schema = CreateProviderOfferingInput
    output_schema = CreateProviderOfferingOutput
    required_permission = Permission.MANAGE_MEDICAL_TOURISM

    def __init__(self, service: MedicalTourismService) -> None:
        self._service = service

    async def execute(
        self, input: CreateProviderOfferingInput, context: ExecutionContext
    ) -> CreateProviderOfferingOutput:
        try:
            offering, deduplicated = await self._service.create_provider_procedure(
                context.tenant_id,
                CreateOfferingInput(
                    provider_id=input.provider_id,
                    procedure_id=input.procedure_id,
                    estimated_price=input.estimated_price,
                    currency=input.currency,
                ),
            )
        except NotFoundError as exc:
            raise ValueError(str(exc)) from exc
        return CreateProviderOfferingOutput(offering=_offering_to_dict(offering), deduplicated=deduplicated)
