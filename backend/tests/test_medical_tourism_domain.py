"""Phase 10 (PHASE_10_MEDICAL_TOURISM_DOMAIN_DESIGN.md): the Medical
Tourism vertical extension's own test suite — service-layer CRUD/
relationship validation, tenant isolation, RBAC, and API behavior. Real-
Postgres-only concerns (RLS instrumentation, migration cycles, concurrent
uniqueness) live in tests/test_postgres_medical_tourism_domain.py, mirroring
this codebase's existing test_vertical_extension_registry.py /
test_postgres_vertical_extension_rls_audit_mode.py split.
"""

import uuid
from decimal import Decimal

import pytest

from app.models.crm import Lead, LeadSource
from app.models.medical_tourism import ProviderStatus
from app.models.rbac import Permission, Role, role_has_permission
from app.models.retention import Referral, ReferralStatus
from app.services.medical_tourism_service import (
    CreateOfferingInput,
    CreateProcedureInput,
    CreateProviderInput,
    InvalidRelationshipError,
    MedicalTourismService,
    NotFoundError,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def service(tool_registry) -> MedicalTourismService:
    # `tool_registry` fixture (conftest.py) is only depended on to force
    # the same session_factory/engine wiring every other test uses.
    from app.db.session import async_session_maker

    return MedicalTourismService(async_session_maker)


async def _make_provider(service: MedicalTourismService, tenant_id: uuid.UUID, **overrides):
    defaults = dict(name="Acme Hospital", country="tr")
    defaults.update(overrides)
    provider, _ = await service.create_provider(tenant_id, CreateProviderInput(**defaults))
    return provider


async def _make_procedure(service: MedicalTourismService, tenant_id: uuid.UUID, **overrides):
    defaults = dict(name="Hip Replacement")
    defaults.update(overrides)
    procedure, _ = await service.create_procedure(tenant_id, CreateProcedureInput(**defaults))
    return procedure


# --- Service layer: providers -----------------------------------------------


async def test_create_provider_normalizes_country_and_defaults_active(service: MedicalTourismService) -> None:
    tenant_id = uuid.uuid4()
    provider = await _make_provider(service, tenant_id, country="tr")
    assert provider.country == "TR"
    assert provider.status == ProviderStatus.ACTIVE


async def test_create_provider_deduplicates_on_idempotency_key(service: MedicalTourismService) -> None:
    tenant_id = uuid.uuid4()
    first, deduped1 = await service.create_provider(
        tenant_id, CreateProviderInput(name="A", country="TR", idempotency_key="k1")
    )
    second, deduped2 = await service.create_provider(
        tenant_id, CreateProviderInput(name="A different name", country="IN", idempotency_key="k1")
    )
    assert deduped1 is False
    assert deduped2 is True
    assert first.id == second.id
    assert second.name == "A"  # unchanged -- the second call was a no-op dedup, not an update


async def test_get_provider_not_found_raises(service: MedicalTourismService) -> None:
    with pytest.raises(NotFoundError):
        await service.get_provider(uuid.uuid4(), uuid.uuid4())


async def test_list_providers_filters_by_country_and_status(service: MedicalTourismService) -> None:
    tenant_id = uuid.uuid4()
    await _make_provider(service, tenant_id, name="TR Hospital", country="TR")
    await _make_provider(service, tenant_id, name="IN Hospital", country="IN")
    tr_only, total = await service.list_providers(tenant_id, country="TR")
    assert total == 1
    assert tr_only[0].name == "TR Hospital"


async def test_update_provider_can_deactivate(service: MedicalTourismService) -> None:
    tenant_id = uuid.uuid4()
    provider = await _make_provider(service, tenant_id)
    updated = await service.update_provider(tenant_id, provider.id, {"status": "INACTIVE"})
    assert updated.status == "INACTIVE"


# --- Service layer: procedures + offerings ----------------------------------


async def test_create_offering_requires_existing_provider_and_procedure(service: MedicalTourismService) -> None:
    tenant_id = uuid.uuid4()
    with pytest.raises(NotFoundError):
        await service.create_provider_procedure(
            tenant_id, CreateOfferingInput(provider_id=uuid.uuid4(), procedure_id=uuid.uuid4())
        )


async def test_create_offering_deduplicates_on_provider_procedure_pair(service: MedicalTourismService) -> None:
    tenant_id = uuid.uuid4()
    provider = await _make_provider(service, tenant_id)
    procedure = await _make_procedure(service, tenant_id)
    first, dup1 = await service.create_provider_procedure(
        tenant_id,
        CreateOfferingInput(provider_id=provider.id, procedure_id=procedure.id, estimated_price=Decimal("5000")),
    )
    second, dup2 = await service.create_provider_procedure(
        tenant_id,
        CreateOfferingInput(provider_id=provider.id, procedure_id=procedure.id, estimated_price=Decimal("9999")),
    )
    assert dup1 is False
    assert dup2 is True
    assert first.id == second.id
    assert second.estimated_price == Decimal("5000.00")  # unchanged


async def test_list_offerings_filters_by_provider(service: MedicalTourismService) -> None:
    tenant_id = uuid.uuid4()
    provider_a = await _make_provider(service, tenant_id, name="A")
    provider_b = await _make_provider(service, tenant_id, name="B")
    procedure = await _make_procedure(service, tenant_id)
    await service.create_provider_procedure(
        tenant_id, CreateOfferingInput(provider_id=provider_a.id, procedure_id=procedure.id)
    )
    await service.create_provider_procedure(
        tenant_id, CreateOfferingInput(provider_id=provider_b.id, procedure_id=procedure.id)
    )
    only_a, total = await service.list_offerings(tenant_id, provider_id=provider_a.id)
    assert total == 1
    assert only_a[0].provider_id == provider_a.id


# --- Service layer: PatientLead / Consultation / ReferralCommission extensions --


async def _make_lead(session_factory, tenant_id: uuid.UUID) -> uuid.UUID:
    async with session_factory() as session:
        lead = Lead(tenant_id=tenant_id, name="Jane Patient", source=LeadSource.WEB.value)
        session.add(lead)
        await session.commit()
        await session.refresh(lead)
        return lead.id


async def test_patient_lead_extends_lead_one_to_one(service: MedicalTourismService) -> None:
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    lead_id = await _make_lead(async_session_maker, tenant_id)
    procedure = await _make_procedure(service, tenant_id)

    patient_lead = await service.create_patient_lead(
        tenant_id, lead_id, procedure_id=procedure.id, preferred_destination_country="tr"
    )
    assert patient_lead.lead_id == lead_id
    assert patient_lead.preferred_destination_country == "TR"

    with pytest.raises(InvalidRelationshipError):
        await service.create_patient_lead(tenant_id, lead_id)


async def test_patient_lead_requires_existing_lead(service: MedicalTourismService) -> None:
    with pytest.raises(NotFoundError):
        await service.create_patient_lead(uuid.uuid4(), uuid.uuid4())


async def _make_referral(session_factory, tenant_id: uuid.UUID, revenue: Decimal | None = None) -> uuid.UUID:
    from datetime import datetime, timezone

    async with session_factory() as session:
        referral = Referral(
            tenant_id=tenant_id,
            program_id=uuid.uuid4(),
            referral_code_id=uuid.uuid4(),
            referrer_customer_id=uuid.uuid4(),
            status=ReferralStatus.CONVERTED.value,
            revenue_amount=revenue,
            created_at_referral=datetime.now(timezone.utc),
        )
        session.add(referral)
        await session.commit()
        await session.refresh(referral)
        return referral.id


async def test_referral_commission_extends_referral_and_computes_percentage(
    service: MedicalTourismService,
) -> None:
    from app.db.session import async_session_maker

    tenant_id = uuid.uuid4()
    referral_id = await _make_referral(async_session_maker, tenant_id, revenue=Decimal("10000"))

    commission = await service.create_referral_commission(
        tenant_id, referral_id, currency="usd", basis="PERCENTAGE", commission_percentage=Decimal("10")
    )
    assert commission.currency == "USD"
    assert commission.computed_amount == Decimal("1000.00")

    with pytest.raises(InvalidRelationshipError):
        await service.create_referral_commission(tenant_id, referral_id, currency="USD")


async def test_referral_commission_requires_existing_referral(service: MedicalTourismService) -> None:
    with pytest.raises(NotFoundError):
        await service.create_referral_commission(uuid.uuid4(), uuid.uuid4(), currency="USD")


# --- Tenant isolation (application-layer; RLS is audit-mode, not enforcing) ---


async def test_tenant_a_cannot_read_tenant_b_provider(service: MedicalTourismService) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    provider = await _make_provider(service, tenant_b, name="B's Hospital")
    with pytest.raises(NotFoundError):
        await service.get_provider(tenant_a, provider.id)


async def test_tenant_a_list_does_not_include_tenant_b_providers(service: MedicalTourismService) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_provider(service, tenant_a, name="A's Hospital")
    await _make_provider(service, tenant_b, name="B's Hospital")
    rows, total = await service.list_providers(tenant_a)
    assert total == 1
    assert rows[0].name == "A's Hospital"


async def test_tenant_a_cannot_update_tenant_b_provider(service: MedicalTourismService) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    provider = await _make_provider(service, tenant_b)
    with pytest.raises(NotFoundError):
        await service.update_provider(tenant_a, provider.id, {"status": "INACTIVE"})


async def test_same_provider_name_can_exist_independently_across_tenants(service: MedicalTourismService) -> None:
    """HARD SCOPE Phase 3 requirement: the same provider name must not be
    artificially constrained to be globally unique -- Provider is tenant-
    owned business data (each tenant's own curated directory), not global
    reference data like VerticalExtension/IntegrationProviderCatalog."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    provider_a = await _make_provider(service, tenant_a, name="Global Health Clinic")
    provider_b = await _make_provider(service, tenant_b, name="Global Health Clinic")
    assert provider_a.id != provider_b.id
    assert provider_a.name == provider_b.name


async def test_offering_uniqueness_is_scoped_per_tenant(service: MedicalTourismService) -> None:
    """The (tenant, provider, procedure) uniqueness constraint must not
    leak across tenants -- two different tenants' own provider/procedure
    pairs are independent, never colliding just because the row shape
    matches."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    provider_a = await _make_provider(service, tenant_a)
    procedure_a = await _make_procedure(service, tenant_a)
    provider_b = await _make_provider(service, tenant_b)
    procedure_b = await _make_procedure(service, tenant_b)

    offering_a, dup_a = await service.create_provider_procedure(
        tenant_a, CreateOfferingInput(provider_id=provider_a.id, procedure_id=procedure_a.id)
    )
    offering_b, dup_b = await service.create_provider_procedure(
        tenant_b, CreateOfferingInput(provider_id=provider_b.id, procedure_id=procedure_b.id)
    )
    assert dup_a is False and dup_b is False
    assert offering_a.id != offering_b.id


# --- RBAC ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,expected_read,expected_manage",
    [
        (Role.OWNER, True, True),
        (Role.ADMIN, True, True),
        (Role.MANAGER, True, True),
        (Role.STAFF, True, False),
        (Role.TECHNICIAN, False, False),
        (Role.ACCOUNTANT, False, False),
        (Role.READ_ONLY, True, False),
    ],
)
def test_role_permission_matrix_for_medical_tourism(role: Role, expected_read: bool, expected_manage: bool) -> None:
    assert role_has_permission(role, Permission.READ_MEDICAL_TOURISM) is expected_read
    assert role_has_permission(role, Permission.MANAGE_MEDICAL_TOURISM) is expected_manage


# --- API ------------------------------------------------------------------------


async def _register_and_login(client, email: str, org_name: str) -> dict:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": org_name,
            "email": email,
            "password": "supersecret123",
            "full_name": "Test Owner",
        },
    )
    assert resp.status_code in (200, 201), resp.text
    token = resp.json()["tokens"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_api_create_and_list_provider_end_to_end(client) -> None:
    headers = await _register_and_login(client, "owner@example.com", "Test Med Tourism Org 1")

    create_resp = await client.post(
        "/api/v1/medical-tourism/providers",
        json={"name": "Istanbul Health Hub", "country": "TR", "city": "Istanbul"},
        headers=headers,
    )
    assert create_resp.status_code == 200, create_resp.text
    provider = create_resp.json()["provider"]
    assert provider["country"] == "TR"

    list_resp = await client.get("/api/v1/medical-tourism/providers", headers=headers)
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body["total"] == 1
    assert body["providers"][0]["name"] == "Istanbul Health Hub"


async def test_api_requires_authentication(client) -> None:
    resp = await client.get("/api/v1/medical-tourism/providers")
    assert resp.status_code in (401, 403)


async def test_api_create_procedure_and_offering(client) -> None:
    headers = await _register_and_login(client, "owner2@example.com", "Test Med Tourism Org 2")

    provider_resp = await client.post(
        "/api/v1/medical-tourism/providers", json={"name": "Delhi Care", "country": "IN"}, headers=headers
    )
    provider_id = provider_resp.json()["provider"]["id"]

    procedure_resp = await client.post(
        "/api/v1/medical-tourism/procedures", json={"name": "Knee Replacement", "category": "Orthopedic"}, headers=headers
    )
    procedure_id = procedure_resp.json()["procedure"]["id"]

    offering_resp = await client.post(
        "/api/v1/medical-tourism/offerings",
        json={"provider_id": provider_id, "procedure_id": procedure_id, "estimated_price": "4500.00", "currency": "USD"},
        headers=headers,
    )
    assert offering_resp.status_code == 200, offering_resp.text
    offering = offering_resp.json()["offering"]
    assert offering["provider_id"] == provider_id
    assert offering["procedure_id"] == procedure_id

    list_resp = await client.get(
        f"/api/v1/medical-tourism/offerings?provider_id={provider_id}", headers=headers
    )
    assert list_resp.json()["total"] == 1


async def test_api_add_and_verify_provider_credential(client) -> None:
    headers = await _register_and_login(client, "owner3@example.com", "Test Med Tourism Org 3")

    provider_resp = await client.post(
        "/api/v1/medical-tourism/providers", json={"name": "Bangkok Wellness", "country": "TH"}, headers=headers
    )
    provider_id = provider_resp.json()["provider"]["id"]

    cred_resp = await client.post(
        f"/api/v1/medical-tourism/providers/{provider_id}/credentials",
        json={
            "credential_type": "Hospital Accreditation",
            "issuing_authority": "JCI",
            "issued_date": "2024-01-15",
            "expiry_date": "2027-01-15",
        },
        headers=headers,
    )
    assert cred_resp.status_code == 200, cred_resp.text
    credential = cred_resp.json()["credential"]
    assert credential["status"] == "PENDING"

    list_resp = await client.get(
        f"/api/v1/medical-tourism/providers/{provider_id}/credentials", headers=headers
    )
    assert len(list_resp.json()["credentials"]) == 1

    verify_resp = await client.post(
        f"/api/v1/medical-tourism/providers/{provider_id}/credentials/{credential['id']}/verify",
        headers=headers,
    )
    assert verify_resp.status_code == 200, verify_resp.text
    assert verify_resp.json()["credential"]["status"] == "VERIFIED"
