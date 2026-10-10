"""section 11: CRM tools, registered on the same ToolRegistry as everything
else in Klaros — no CRM code path bypasses permission/tenant/schema/policy/
audit enforcement.
"""

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.crm import Customer, CustomerNote, CustomerStatus, Lead, LeadSource, LeadStatus, QualificationStatus
from app.models.rbac import Permission
from app.services import consent_gate
from app.services.customer_matching import normalize_email, normalize_phone
from app.services.lead_service import CreateLeadInput, LeadService
from app.services.qualification_service import LeadQualificationService
from app.tools.base import ExecutionContext, Tool


def _lead_to_dict(lead: Lead) -> dict[str, Any]:
    return {
        "id": str(lead.id),
        "customer_id": str(lead.customer_id) if lead.customer_id else None,
        "name": lead.name,
        "phone": lead.phone,
        "email": lead.email,
        "source": lead.source,
        "service_requested": lead.service_requested,
        "location": lead.location,
        "urgency": lead.urgency,
        "estimated_value": float(lead.estimated_value) if lead.estimated_value is not None else None,
        "status": lead.status,
        "lead_score": lead.lead_score,
        "qualification_status": lead.qualification_status,
        "score_reason": lead.score_reason,
        "assigned_user_id": str(lead.assigned_user_id) if lead.assigned_user_id else None,
        "created_at": lead.created_at.isoformat(),
    }


def _customer_to_dict(c: Customer) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "name": c.name,
        "company_name": c.company_name,
        "email": c.email,
        "phone": c.phone,
        "address": c.address,
        "city": c.city,
        "state": c.state,
        "postal_code": c.postal_code,
        "status": c.status,
        "created_at": c.created_at.isoformat(),
    }


class CreateLeadToolInput(BaseModel):
    name: str
    source: str
    phone: str | None = None
    email: str | None = None
    source_detail: str | None = None
    service_requested: str | None = None
    description: str | None = None
    location: str | None = None
    urgency: str = "MEDIUM"
    estimated_value: float | None = None
    idempotency_key: str | None = None
    # {"scopes": [...], "wording_version": "..."} — what the person agreed to. Only a signed-in person's claim counts; read by consent-gated tenants only.
    consent: dict[str, Any] | None = None


class LeadOutput(BaseModel):
    lead: dict[str, Any]
    deduplicated: bool = False


class CreateLead(Tool):
    name = "crm.create_lead"
    description = "Create a new lead, deduplicating on idempotency_key and matching to an existing customer by email/phone."
    input_schema = CreateLeadToolInput
    output_schema = LeadOutput
    required_permission = Permission.CREATE_LEAD
    # Redacted from the audit record for consent-gated tenants (the record would otherwise keep what consent has not been given for).
    pii_input_fields = ("name", "phone", "email", "description", "service_requested", "location")

    def __init__(self, lead_service: LeadService) -> None:
        self._lead_service = lead_service

    async def execute(self, input: CreateLeadToolInput, context: ExecutionContext) -> LeadOutput:
        lead, deduplicated = await self._lead_service.create_lead(
            context.tenant_id,
            CreateLeadInput(
                name=input.name,
                source=input.source,
                phone=input.phone,
                email=input.email,
                source_detail=input.source_detail,
                service_requested=input.service_requested,
                description=input.description,
                location=input.location,
                urgency=input.urgency,
                estimated_value=input.estimated_value,
                idempotency_key=input.idempotency_key,
                consent=consent_gate.claim_from_tool(input.consent, context.actor_type, context.actor_id),
            ),
        )
        return LeadOutput(lead=_lead_to_dict(lead), deduplicated=deduplicated)


class BulkImportLeadRow(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None
    service_requested: str | None = None
    description: str | None = None
    location: str | None = None
    estimated_value: float | None = None


class BulkImportLeadsInput(BaseModel):
    leads: list[BulkImportLeadRow] = Field(min_length=1, max_length=2000)


class BulkImportLeadsOutput(BaseModel):
    created_count: int
    matched_existing_customer_count: int
    lead_ids: list[str]


class BulkImportLeads(Tool):
    """The bulk counterpart to CreateLead, for a tenant migrating an
    existing prospect pipeline (e.g. a spreadsheet of open inquiries) into
    Klaros. Reuses LeadService.create_lead's own matching logic row by
    row — a row whose email/phone matches an existing customer gets
    linked to it exactly as a normal single lead creation would; nothing
    here duplicates that logic. source is always LeadSource.OTHER: none
    of the real channels (PHONE/WEB/REFERRAL/...) honestly describe a
    bulk-imported row."""

    name = "crm.bulk_import_leads"
    description = "Create many leads at once from an existing pipeline being migrated into Klaros."
    input_schema = BulkImportLeadsInput
    output_schema = BulkImportLeadsOutput
    required_permission = Permission.CREATE_LEAD
    pii_input_fields = ("leads",)

    def __init__(self, lead_service: LeadService, session_factory: async_sessionmaker | None = None) -> None:
        self._lead_service = lead_service
        self._session_factory = session_factory

    async def execute(self, input: BulkImportLeadsInput, context: ExecutionContext) -> BulkImportLeadsOutput:
        if self._session_factory is not None and await consent_gate.tenant_requires_consent(self._session_factory, context.tenant_id):
            # A spreadsheet carries no per-person consent evidence, so for a consent-gated tenant the whole import is refused.
            raise consent_gate.ConsentRequiredError([consent_gate.STORE_PERSONAL], reason="bulk_import_disabled_without_per_person_consent")
        lead_ids: list[str] = []
        matched_existing = 0
        for row in input.leads:
            lead, _deduplicated = await self._lead_service.create_lead(
                context.tenant_id,
                CreateLeadInput(
                    name=row.name,
                    source=LeadSource.OTHER,
                    phone=row.phone,
                    email=row.email,
                    service_requested=row.service_requested,
                    description=row.description,
                    location=row.location,
                    estimated_value=row.estimated_value,
                ),
            )
            lead_ids.append(str(lead.id))
            if lead.customer_id is not None:
                matched_existing += 1
        return BulkImportLeadsOutput(
            created_count=len(lead_ids), matched_existing_customer_count=matched_existing, lead_ids=lead_ids
        )


class GetLeadInput(BaseModel):
    lead_id: uuid.UUID


class GetLead(Tool):
    name = "crm.get_lead"
    description = "Fetch a lead by id, scoped to the caller's tenant."
    input_schema = GetLeadInput
    output_schema = LeadOutput
    required_permission = Permission.READ_LEADS

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: GetLeadInput, context: ExecutionContext) -> LeadOutput:
        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            lead = await session.get(Lead, input.lead_id)
            if lead is None or lead.tenant_id != context.tenant_id:
                raise ValueError("Lead not found")
            return LeadOutput(lead=_lead_to_dict(lead))


class UpdateLeadInput(BaseModel):
    lead_id: uuid.UUID
    status: str | None = None
    assigned_user_id: uuid.UUID | None = None
    description: str | None = None


class UpdateLead(Tool):
    name = "crm.update_lead"
    description = "Update mutable fields on a lead."
    input_schema = UpdateLeadInput
    output_schema = LeadOutput
    required_permission = Permission.UPDATE_LEAD
    pii_input_fields = ("description",)

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: UpdateLeadInput, context: ExecutionContext) -> LeadOutput:
        if input.status is not None:
            try:
                LeadStatus(input.status)
            except ValueError:
                raise ValueError(f"Invalid lead status: {input.status}") from None

        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            lead = await session.get(Lead, input.lead_id)
            if lead is None or lead.tenant_id != context.tenant_id:
                raise ValueError("Lead not found")
            if input.status is not None:
                if input.status == LeadStatus.QUALIFIED and lead.qualification_status == QualificationStatus.REQUIRES_HUMAN and context.actor_type != ActorType.USER:
                    # Handed to a person (safety category / needs human review): only a signed-in person may mark it qualified.
                    raise ValueError("This lead requires human review before it can be marked qualified")
                lead.status = input.status
            if input.assigned_user_id is not None:
                lead.assigned_user_id = input.assigned_user_id
            if input.description is not None:
                if await consent_gate.tenant_requires_consent(self._session_factory, context.tenant_id):
                    from app.services import halla_consent

                    state = await halla_consent.state_for(session, context.tenant_id, halla_lead_id=lead.external_id, lead_id=lead.id)
                    if not state.allows(consent_gate.STORE_MEDICAL):
                        raise consent_gate.ConsentRequiredError([consent_gate.STORE_MEDICAL])
                lead.description = input.description
            await session.commit()
            await session.refresh(lead)
            out = LeadOutput(lead=_lead_to_dict(lead))
        if input.description:
            from app.services.mt_intake_safety import screen_lead

            await screen_lead(self._session_factory, context.tenant_id, input.lead_id, input.description, source="lead_update")
        return out


class SearchLeadsInput(BaseModel):
    status: str | None = None
    source: str | None = None
    q: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class SearchLeadsOutput(BaseModel):
    leads: list[dict[str, Any]]
    total: int


class SearchLeads(Tool):
    name = "crm.search_leads"
    description = "Search/filter/paginate leads for this tenant."
    input_schema = SearchLeadsInput
    output_schema = SearchLeadsOutput
    required_permission = Permission.READ_LEADS

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: SearchLeadsInput, context: ExecutionContext) -> SearchLeadsOutput:
        from sqlalchemy import func

        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            query = select(Lead).where(Lead.tenant_id == context.tenant_id)
            count_query = select(func.count(Lead.id)).where(Lead.tenant_id == context.tenant_id)

            if input.status:
                query = query.where(Lead.status == input.status)
                count_query = count_query.where(Lead.status == input.status)
            if input.source:
                query = query.where(Lead.source == input.source)
                count_query = count_query.where(Lead.source == input.source)
            if input.q:
                like = f"%{input.q}%"
                cond = or_(Lead.name.ilike(like), Lead.email.ilike(like), Lead.service_requested.ilike(like))
                query = query.where(cond)
                count_query = count_query.where(cond)

            total = (await session.execute(count_query)).scalar_one()
            query = query.order_by(Lead.created_at.desc()).limit(input.limit).offset(input.offset)
            leads = (await session.execute(query)).scalars().all()
            return SearchLeadsOutput(leads=[_lead_to_dict(l) for l in leads], total=total)


class QualifyLeadInput(BaseModel):
    lead_id: uuid.UUID


class QualifyLeadOutput(BaseModel):
    lead_id: str
    qualification_status: str
    score: int
    reason: str


class QualifyLead(Tool):
    name = "crm.qualify_lead"
    description = "Run deterministic qualification scoring on a lead and persist the result."
    input_schema = QualifyLeadInput
    output_schema = QualifyLeadOutput
    required_permission = Permission.QUALIFY_LEAD

    def __init__(self, qualification_service: LeadQualificationService) -> None:
        self._qualification_service = qualification_service

    async def execute(self, input: QualifyLeadInput, context: ExecutionContext) -> QualifyLeadOutput:
        outcome = await self._qualification_service.qualify(context.tenant_id, input.lead_id, by_person=context.actor_type == ActorType.USER)
        return QualifyLeadOutput(**outcome.__dict__)


class AIQualifyLeadAdvisoryInput(BaseModel):
    lead_id: uuid.UUID


class AIQualifyLeadAdvisoryOutput(BaseModel):
    available: bool
    qualification_score: int | None = None
    intent: str | None = None
    urgency: str | None = None
    buying_signal: str | None = None
    summary: str | None = None
    recommended_next_action: str | None = None
    unavailable_reason: str | None = None


class AIQualifyLeadAdvisory(Tool):
    """Phase 12E: ADVISORY ONLY — never writes to the Lead record. Produces
    a real-LLM-generated qualification recommendation for a human (or a
    future approval-gated apply step) to review; applying it still
    requires calling the existing, policy-gated crm.qualify_lead or a
    human editing the lead directly. Read-only, so AUTO policy is correct
    here for the same reason as finance.send_invoice/create_stripe_checkout_
    session — nothing is mutated by this call itself."""

    name = "crm.ai_qualify_lead_advisory"
    description = "Generate an AI-assisted qualification recommendation for a lead (advisory only, does not persist)."
    input_schema = AIQualifyLeadAdvisoryInput
    output_schema = AIQualifyLeadAdvisoryOutput
    required_permission = Permission.QUALIFY_LEAD

    def __init__(self, ai_qualification_service) -> None:
        self._service = ai_qualification_service

    async def execute(
        self, input: AIQualifyLeadAdvisoryInput, context: ExecutionContext
    ) -> AIQualifyLeadAdvisoryOutput:
        from app.services.ai_qualification_service import LeadNotFoundError

        try:
            result = await self._service.generate_recommendation(
                context.tenant_id,
                input.lead_id,
                actor_type=context.actor_type,
                actor_id=context.actor_id,
                correlation_id=context.correlation_id,
            )
        except LeadNotFoundError as exc:
            raise ValueError(str(exc)) from exc

        if not result.available:
            return AIQualifyLeadAdvisoryOutput(available=False, unavailable_reason=result.error_detail)

        rec = result.recommendation
        return AIQualifyLeadAdvisoryOutput(
            available=True,
            qualification_score=rec.qualification_score,
            intent=rec.intent,
            urgency=rec.urgency,
            buying_signal=rec.buying_signal,
            summary=rec.summary,
            recommended_next_action=rec.recommended_next_action,
        )


class CreateCustomerInput(BaseModel):
    name: str
    company_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None


class CustomerOutput(BaseModel):
    customer: dict[str, Any]


class CreateCustomer(Tool):
    name = "crm.create_customer"
    pii_input_fields = ("name", "company_name", "email", "phone", "address", "city", "state", "postal_code", "notes")
    description = "Create a customer record directly (not via lead conversion)."
    input_schema = CreateCustomerInput
    output_schema = CustomerOutput
    required_permission = Permission.CREATE_CUSTOMER

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: CreateCustomerInput, context: ExecutionContext) -> CustomerOutput:
        await consent_gate.ensure_not_gated(self._session_factory, context.tenant_id, "direct_customer_creation_disabled_for_consent_gated_tenant")
        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            customer = Customer(
                tenant_id=context.tenant_id,
                name=input.name,
                company_name=input.company_name,
                email=normalize_email(input.email),
                phone=input.phone,
                phone_normalized=normalize_phone(input.phone),
                address=input.address,
                city=input.city,
                state=input.state,
                postal_code=input.postal_code,
            )
            session.add(customer)
            await session.commit()
            await session.refresh(customer)
            return CustomerOutput(customer=_customer_to_dict(customer))


class BulkImportCustomersInput(BaseModel):
    customers: list[CreateCustomerInput] = Field(min_length=1, max_length=2000)


class BulkImportCustomersOutput(BaseModel):
    created_count: int
    skipped_duplicate_count: int
    customer_ids: list[str]


class BulkImportCustomers(Tool):
    """Real, transactional bulk creation — the counterpart to CreateCustomer
    for a business migrating an existing customer list into Klaros (e.g.
    from a CSV export of a spreadsheet or another CRM). A row is skipped as
    a duplicate only when its normalized email already matches an existing
    customer for this tenant (or another row earlier in the same batch) —
    a row with no email is never treated as a duplicate of anything, since
    there's nothing reliable to match on."""

    name = "crm.bulk_import_customers"
    pii_input_fields = ("customers",)
    description = "Create many customer records at once, skipping rows whose email already exists for this tenant."
    input_schema = BulkImportCustomersInput
    output_schema = BulkImportCustomersOutput
    required_permission = Permission.CREATE_CUSTOMER

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: BulkImportCustomersInput, context: ExecutionContext) -> BulkImportCustomersOutput:
        await consent_gate.ensure_not_gated(self._session_factory, context.tenant_id, "bulk_import_disabled_without_per_person_consent")
        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            existing_emails = set(
                (
                    await session.execute(
                        select(Customer.email).where(
                            Customer.tenant_id == context.tenant_id, Customer.email.is_not(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
            seen_in_batch: set[str] = set()
            created_ids: list[str] = []
            skipped = 0

            for row in input.customers:
                normalized = normalize_email(row.email)
                if normalized and (normalized in existing_emails or normalized in seen_in_batch):
                    skipped += 1
                    continue
                if normalized:
                    seen_in_batch.add(normalized)
                customer = Customer(
                    tenant_id=context.tenant_id,
                    name=row.name,
                    company_name=row.company_name,
                    email=normalized,
                    phone=row.phone,
                    phone_normalized=normalize_phone(row.phone),
                    address=row.address,
                    city=row.city,
                    state=row.state,
                    postal_code=row.postal_code,
                )
                session.add(customer)
                await session.flush()
                created_ids.append(str(customer.id))

            await session.commit()
            return BulkImportCustomersOutput(
                created_count=len(created_ids), skipped_duplicate_count=skipped, customer_ids=created_ids
            )


class GetCustomerInput(BaseModel):
    customer_id: uuid.UUID


class GetCustomer(Tool):
    name = "crm.get_customer"
    description = "Fetch a customer by id, scoped to the caller's tenant."
    input_schema = GetCustomerInput
    output_schema = CustomerOutput
    required_permission = Permission.READ_CUSTOMERS

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: GetCustomerInput, context: ExecutionContext) -> CustomerOutput:
        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            customer = await session.get(Customer, input.customer_id)
            if customer is None or customer.tenant_id != context.tenant_id:
                raise ValueError("Customer not found")
            return CustomerOutput(customer=_customer_to_dict(customer))


class UpdateCustomerInput(BaseModel):
    customer_id: uuid.UUID
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    status: str | None = None
    notes: str | None = None


class UpdateCustomer(Tool):
    name = "crm.update_customer"
    pii_input_fields = ("name", "email", "phone", "address", "notes")
    description = "Update mutable fields on a customer."
    input_schema = UpdateCustomerInput
    output_schema = CustomerOutput
    required_permission = Permission.UPDATE_CUSTOMER

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def _require_consent_for_changes(self, input: UpdateCustomerInput, customer: Customer, context: ExecutionContext) -> None:
        """Consent gate for editing an EXISTING customer of a consent-gated tenant (Medical Tourism). Adding or changing personal data (name, e-mail,
        phone, address) needs `store_personal_data`; a note (free text that may hold health information) needs `store_medical_information`, exactly
        like `crm.create_note`. Both are checked against EVERY lead behind the customer, so a customer with no lead behind it (no evidence) cannot be
        edited. Clearing a field is never refused (erasure must stay possible), and re-sending an unchanged value is not a change. `status` is not
        personal data. Non-gated tenants are untouched."""

        def adds(new: str | None, old: str | None) -> bool:
            return new is not None and new.strip() != "" and new != old

        personal = (
            adds(input.name, customer.name)
            or adds(input.email, customer.email) and normalize_email(input.email) != customer.email
            or adds(input.phone, customer.phone)
            or adds(input.address, customer.address)
        )
        medical = adds(input.notes, customer.notes)
        if not (personal or medical):
            return
        if not await consent_gate.tenant_requires_consent(self._session_factory, context.tenant_id):
            return
        missing = [
            scope
            for scope, needed in ((consent_gate.STORE_PERSONAL, personal), (consent_gate.STORE_MEDICAL, medical))
            if needed and not await consent_gate.customer_leads_allow(self._session_factory, context.tenant_id, customer.id, scope)
        ]
        if missing:
            raise consent_gate.ConsentRequiredError(missing)

    async def execute(self, input: UpdateCustomerInput, context: ExecutionContext) -> CustomerOutput:
        if input.status is not None:
            try:
                CustomerStatus(input.status)
            except ValueError:
                raise ValueError(f"Invalid customer status: {input.status}") from None

        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            customer = await session.get(Customer, input.customer_id)
            if customer is None or customer.tenant_id != context.tenant_id:
                raise ValueError("Customer not found")
            await self._require_consent_for_changes(input, customer, context)
            if input.name is not None:
                customer.name = input.name
            if input.email is not None:
                customer.email = normalize_email(input.email)
            if input.phone is not None:
                customer.phone = input.phone
                customer.phone_normalized = normalize_phone(input.phone)
            if input.address is not None:
                customer.address = input.address
            if input.status is not None:
                customer.status = input.status
            if input.notes is not None:
                customer.notes = input.notes
            await session.commit()
            await session.refresh(customer)
            return CustomerOutput(customer=_customer_to_dict(customer))


class SearchCustomersInput(BaseModel):
    q: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class SearchCustomersOutput(BaseModel):
    customers: list[dict[str, Any]]
    total: int


class SearchCustomers(Tool):
    name = "crm.search_customers"
    description = "Search/paginate customers for this tenant."
    input_schema = SearchCustomersInput
    output_schema = SearchCustomersOutput
    required_permission = Permission.READ_CUSTOMERS

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: SearchCustomersInput, context: ExecutionContext) -> SearchCustomersOutput:
        from sqlalchemy import func

        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            query = select(Customer).where(Customer.tenant_id == context.tenant_id)
            count_query = select(func.count(Customer.id)).where(Customer.tenant_id == context.tenant_id)
            if input.q:
                like = f"%{input.q}%"
                cond = or_(Customer.name.ilike(like), Customer.email.ilike(like))
                query = query.where(cond)
                count_query = count_query.where(cond)
            total = (await session.execute(count_query)).scalar_one()
            query = query.order_by(Customer.created_at.desc()).limit(input.limit).offset(input.offset)
            customers = (await session.execute(query)).scalars().all()
            return SearchCustomersOutput(customers=[_customer_to_dict(c) for c in customers], total=total)


class GetCustomerTimelineInput(BaseModel):
    customer_id: uuid.UUID


class TimelineEntry(BaseModel):
    type: str
    timestamp: str
    summary: str


class GetCustomerTimelineOutput(BaseModel):
    customer_id: str
    entries: list[TimelineEntry]


class GetCustomerTimeline(Tool):
    """section 18: builds the timeline from real rows (leads, appointments,
    notes, audit log) — never fabricated."""

    name = "crm.get_customer_timeline"
    description = "Build a chronological timeline of everything on record for a customer."
    input_schema = GetCustomerTimelineInput
    output_schema = GetCustomerTimelineOutput
    required_permission = Permission.READ_CUSTOMERS

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(
        self, input: GetCustomerTimelineInput, context: ExecutionContext
    ) -> GetCustomerTimelineOutput:
        from app.models.audit_log import AuditLog
        from app.models.crm import Appointment

        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            customer = await session.get(Customer, input.customer_id)
            if customer is None or customer.tenant_id != context.tenant_id:
                raise ValueError("Customer not found")

            entries: list[TimelineEntry] = []

            leads = (
                await session.execute(
                    select(Lead).where(
                        Lead.tenant_id == context.tenant_id, Lead.customer_id == input.customer_id
                    )
                )
            ).scalars().all()
            for lead in leads:
                entries.append(
                    TimelineEntry(
                        type="lead",
                        timestamp=lead.created_at.isoformat(),
                        summary=f"Lead created via {lead.source}: {lead.service_requested or 'unspecified service'}",
                    )
                )

            appointments = (
                await session.execute(
                    select(Appointment).where(
                        Appointment.tenant_id == context.tenant_id,
                        Appointment.customer_id == input.customer_id,
                    )
                )
            ).scalars().all()
            for appt in appointments:
                entries.append(
                    TimelineEntry(
                        type="appointment",
                        timestamp=appt.created_at.isoformat(),
                        summary=f"Appointment '{appt.title}' ({appt.status}) at {appt.start_time.isoformat()}",
                    )
                )

            notes = (
                await session.execute(
                    select(CustomerNote).where(
                        CustomerNote.tenant_id == context.tenant_id,
                        CustomerNote.customer_id == input.customer_id,
                    )
                )
            ).scalars().all()
            for note in notes:
                entries.append(
                    TimelineEntry(type="note", timestamp=note.created_at.isoformat(), summary=note.body)
                )

            audit_rows = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.tenant_id == context.tenant_id,
                        AuditLog.entity_type == "customer",
                        AuditLog.entity_id == input.customer_id,
                    )
                )
            ).scalars().all()
            for row in audit_rows:
                entries.append(
                    TimelineEntry(type="audit", timestamp=row.created_at.isoformat(), summary=row.action)
                )

            entries.sort(key=lambda e: e.timestamp)
            return GetCustomerTimelineOutput(customer_id=str(input.customer_id), entries=entries)


class CreateNoteInput(BaseModel):
    customer_id: uuid.UUID
    body: str


class CreateNoteOutput(BaseModel):
    note_id: str


class CreateNote(Tool):
    name = "crm.create_note"
    pii_input_fields = ("body",)
    description = "Attach a freeform note to a customer."
    input_schema = CreateNoteInput
    output_schema = CreateNoteOutput
    required_permission = Permission.UPDATE_CUSTOMER

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: CreateNoteInput, context: ExecutionContext) -> CreateNoteOutput:
        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            customer = await session.get(Customer, input.customer_id)
            if customer is None or customer.tenant_id != context.tenant_id:
                raise ValueError("Customer not found")
            if await consent_gate.tenant_requires_consent(self._session_factory, context.tenant_id) and not await consent_gate.customer_leads_allow(
                self._session_factory, context.tenant_id, input.customer_id, consent_gate.STORE_MEDICAL
            ):
                # A free-text note may contain health information: it needs store_medical_information on every lead behind this customer.
                raise consent_gate.ConsentRequiredError([consent_gate.STORE_MEDICAL])
            note = CustomerNote(
                tenant_id=context.tenant_id,
                customer_id=input.customer_id,
                author_type=context.actor_type,
                author_id=context.actor_id,
                body=input.body,
            )
            session.add(note)
            await session.commit()
            await session.refresh(note)
            note_id = str(note.id)
        from app.services.mt_intake_safety import screen_customer

        await screen_customer(self._session_factory, context.tenant_id, input.customer_id, input.body, source="customer_note")
        return CreateNoteOutput(note_id=note_id)


class GenerateCustomerSummaryInput(BaseModel):
    customer_id: uuid.UUID


class GenerateCustomerSummaryOutput(BaseModel):
    customer_id: str
    summary: str


class GenerateCustomerSummary(Tool):
    """section 19: built entirely from real rows — leads, appointments, open
    (non-cancelled/non-completed) appointments. There is no invoice/payment
    data yet (Phase 5), so the summary says so rather than inventing it.
    """

    name = "crm.generate_customer_summary"
    description = "Summarize a customer's real history. Says so honestly when data is insufficient."
    input_schema = GenerateCustomerSummaryInput
    output_schema = GenerateCustomerSummaryOutput
    required_permission = Permission.READ_CUSTOMERS

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(
        self, input: GenerateCustomerSummaryInput, context: ExecutionContext
    ) -> GenerateCustomerSummaryOutput:
        from app.models.crm import Appointment, AppointmentStatus

        async with self._session_factory() as session:
            await set_tenant_context(session, context.tenant_id)
            customer = await session.get(Customer, input.customer_id)
            if customer is None or customer.tenant_id != context.tenant_id:
                raise ValueError("Customer not found")

            leads = (
                await session.execute(
                    select(Lead).where(
                        Lead.tenant_id == context.tenant_id, Lead.customer_id == input.customer_id
                    )
                )
            ).scalars().all()
            appointments = (
                await session.execute(
                    select(Appointment).where(
                        Appointment.tenant_id == context.tenant_id,
                        Appointment.customer_id == input.customer_id,
                    )
                )
            ).scalars().all()

        if not leads and not appointments:
            return GenerateCustomerSummaryOutput(
                customer_id=str(input.customer_id),
                summary=f"Customer since {customer.created_at.strftime('%B %Y')}. No leads or appointments "
                "on record yet — insufficient data for a fuller summary.",
            )

        completed = [a for a in appointments if a.status == AppointmentStatus.COMPLETED]
        upcoming = [
            a for a in appointments if a.status in (AppointmentStatus.TENTATIVE, AppointmentStatus.CONFIRMED)
        ]

        lines = [f"Customer since {customer.created_at.strftime('%B %Y')}."]
        lines.append(f"{len(leads)} lead(s) on record.")
        if completed:
            lines.append(f"{len(completed)} completed appointment(s).")
        if upcoming:
            next_appt = min(upcoming, key=lambda a: a.start_time)
            lines.append(
                f"{len(upcoming)} upcoming appointment(s); next is '{next_appt.title}' "
                f"on {next_appt.start_time.strftime('%Y-%m-%d %H:%M')} UTC."
            )
        lines.append(
            "Finance module not connected yet — no invoice/payment history available."
        )

        if upcoming:
            action = f"Confirm the upcoming appointment on {min(upcoming, key=lambda a: a.start_time).start_time.strftime('%Y-%m-%d')}."
        elif any(l.qualification_status == "PENDING" for l in leads):
            action = "Qualify the pending lead."
        else:
            action = "No open items — no immediate action recommended."
        lines.append(f"Recommended next action: {action}")

        return GenerateCustomerSummaryOutput(customer_id=str(input.customer_id), summary=" ".join(lines))
