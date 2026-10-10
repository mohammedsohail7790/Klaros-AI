"""Klaros Medical Tourism completion task, round 2: real-Postgres API-level
tests for the endpoints this round added to `app/api/v1/medical_tourism.py`
(PatientLead/Consultation/ReferralCommission list/get/create/update — see
KLAROS_MEDICAL_TOURISM_COMPLETION_PROGRESS.md's Round 1 "CONFIRMED GAP #1")
and the public-lead -> PatientLead vertical integration fix in
`app/api/v1/public_leads.py` ("CONFIRMED GAP #2").

Driven through the real HTTP client (register -> real JWT -> real
endpoint), mirroring tests/test_tenant_context_jobs_api_phase17b2r.py's
established pattern for this codebase's authenticated-HTTP-path tests.
Skipped entirely unless DATABASE_URL points at a real PostgreSQL instance
-- these tests exist specifically to exercise RBAC/tenant-isolation
behavior over the real HTTP+DB stack, not sqlite.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.models.actor import ActorType
from app.models.rbac import Role
from app.services.vertical_extension_service import VerticalExtensionService
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _ctx(tenant_id: uuid.UUID) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


async def _register(client, org_name: str, email: str) -> dict:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org_name, "full_name": "Owner Test", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_provider(client, token: str, name: str = "Test Hospital") -> str:
    resp = await client.post(
        "/api/v1/medical-tourism/providers",
        json={"name": name, "country": "TR"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["provider"]["id"]


async def _create_lead(tool_registry, tenant_id: uuid.UUID, name: str = "Patient One") -> str:
    output = await tool_registry.execute(
        "crm.create_lead", {"name": name, "source": "WEB", "phone": "+15550001111"}, _ctx(tenant_id)
    )
    return output.lead["id"]


async def _create_appointment(tool_registry, tenant_id: uuid.UUID) -> str:
    customer = await tool_registry.execute("crm.create_customer", {"name": "Consult Customer"}, _ctx(tenant_id))
    start = datetime.now(timezone.utc) + timedelta(days=1)
    end = start + timedelta(hours=1)
    output = await tool_registry.execute(
        "crm.create_appointment",
        {
            "customer_id": customer.customer["id"],
            "title": "Consultation",
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
        },
        _ctx(tenant_id),
    )
    return output.appointment["id"]


async def _create_referral(tool_registry, tenant_id: uuid.UUID) -> str:
    customer = await tool_registry.execute("crm.create_customer", {"name": "Referrer"}, _ctx(tenant_id))
    program = await tool_registry.execute(
        "retention.create_referral_program", {"name": "MT Referral Program"}, _ctx(tenant_id)
    )
    code = await tool_registry.execute(
        "retention.get_or_create_referral_code",
        {"program_id": program.program_id, "customer_id": customer.customer["id"]},
        _ctx(tenant_id),
    )
    referral = await tool_registry.execute(
        "retention.create_referral", {"referral_code_id": code.code_id}, _ctx(tenant_id)
    )
    return referral.referral_id


# --- PatientLead -------------------------------------------------------------


@requires_real_postgres
async def test_create_and_get_patient_lead_over_http(client, tool_registry) -> None:
    reg = await _register(client, "PL Co", f"owner-pl-{uuid.uuid4().hex[:8]}@example.com")
    token = reg["tokens"]["access_token"]
    tenant_id = uuid.UUID(reg["user"]["tenant_id"])
    lead_id = await _create_lead(tool_registry, tenant_id)

    resp = await client.post(
        "/api/v1/medical-tourism/patient-leads",
        json={"lead_id": lead_id, "preferred_destination_country": "tr", "has_insurance": True},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    patient_lead = resp.json()["patient_lead"]
    assert patient_lead["lead_id"] == lead_id
    assert patient_lead["preferred_destination_country"] == "TR"

    get_resp = await client.get(
        f"/api/v1/medical-tourism/patient-leads/{patient_lead['id']}", headers=_auth(token)
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["patient_lead"]["id"] == patient_lead["id"]

    list_resp = await client.get("/api/v1/medical-tourism/patient-leads", headers=_auth(token))
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 1


@requires_real_postgres
async def test_tenant_a_cannot_read_tenant_bs_patient_lead_over_http(client, tool_registry) -> None:
    reg_a = await _register(client, "PL A Co", f"owner-pla-{uuid.uuid4().hex[:8]}@example.com")
    reg_b = await _register(client, "PL B Co", f"owner-plb-{uuid.uuid4().hex[:8]}@example.com")
    token_a, token_b = reg_a["tokens"]["access_token"], reg_b["tokens"]["access_token"]
    tenant_a = uuid.UUID(reg_a["user"]["tenant_id"])
    lead_id = await _create_lead(tool_registry, tenant_a)

    create_resp = await client.post(
        "/api/v1/medical-tourism/patient-leads", json={"lead_id": lead_id}, headers=_auth(token_a)
    )
    assert create_resp.status_code == 200, create_resp.text
    patient_lead_id = create_resp.json()["patient_lead"]["id"]

    cross_resp = await client.get(
        f"/api/v1/medical-tourism/patient-leads/{patient_lead_id}", headers=_auth(token_b)
    )
    assert cross_resp.status_code == 404

    list_resp = await client.get("/api/v1/medical-tourism/patient-leads", headers=_auth(token_b))
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 0


# --- Consultations -------------------------------------------------------------


@requires_real_postgres
async def test_create_update_and_isolate_consultation_over_http(client, tool_registry) -> None:
    reg_a = await _register(client, "Consult A Co", f"owner-ca-{uuid.uuid4().hex[:8]}@example.com")
    reg_b = await _register(client, "Consult B Co", f"owner-cb-{uuid.uuid4().hex[:8]}@example.com")
    token_a, token_b = reg_a["tokens"]["access_token"], reg_b["tokens"]["access_token"]
    tenant_a = uuid.UUID(reg_a["user"]["tenant_id"])

    provider_id = await _create_provider(client, token_a)
    appointment_id = await _create_appointment(tool_registry, tenant_a)

    create_resp = await client.post(
        "/api/v1/medical-tourism/consultations",
        json={"appointment_id": appointment_id, "provider_id": provider_id, "notes": "Initial consult"},
        headers=_auth(token_a),
    )
    assert create_resp.status_code == 200, create_resp.text
    consultation = create_resp.json()["consultation"]
    assert consultation["status"] == "SCHEDULED"

    patch_resp = await client.patch(
        f"/api/v1/medical-tourism/consultations/{consultation['id']}",
        json={"status": "COMPLETED"},
        headers=_auth(token_a),
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["consultation"]["status"] == "COMPLETED"

    # Tenant B: cannot read, and mutating tenant A's row 404s rather than
    # silently affecting it.
    cross_get = await client.get(
        f"/api/v1/medical-tourism/consultations/{consultation['id']}", headers=_auth(token_b)
    )
    assert cross_get.status_code == 404
    cross_patch = await client.patch(
        f"/api/v1/medical-tourism/consultations/{consultation['id']}",
        json={"status": "CANCELLED"},
        headers=_auth(token_b),
    )
    assert cross_patch.status_code == 404


# --- ReferralCommission -------------------------------------------------------


@requires_real_postgres
async def test_create_update_and_isolate_referral_commission_over_http(client, tool_registry) -> None:
    reg_a = await _register(client, "Comm A Co", f"owner-coma-{uuid.uuid4().hex[:8]}@example.com")
    reg_b = await _register(client, "Comm B Co", f"owner-comb-{uuid.uuid4().hex[:8]}@example.com")
    token_a, token_b = reg_a["tokens"]["access_token"], reg_b["tokens"]["access_token"]
    tenant_a = uuid.UUID(reg_a["user"]["tenant_id"])

    referral_id = await _create_referral(tool_registry, tenant_a)

    create_resp = await client.post(
        "/api/v1/medical-tourism/referral-commissions",
        json={
            "referral_id": referral_id,
            "currency": "usd",
            "basis": "FLAT",
            "flat_amount": "150.00",
        },
        headers=_auth(token_a),
    )
    assert create_resp.status_code == 200, create_resp.text
    commission = create_resp.json()["referral_commission"]
    assert commission["currency"] == "USD"
    assert commission["status"] == "PENDING"

    status_resp = await client.patch(
        f"/api/v1/medical-tourism/referral-commissions/{commission['id']}/status",
        json={"status": "CONFIRMED"},
        headers=_auth(token_a),
    )
    assert status_resp.status_code == 200, status_resp.text
    assert status_resp.json()["referral_commission"]["status"] == "CONFIRMED"

    cross_get = await client.get(
        f"/api/v1/medical-tourism/referral-commissions/{commission['id']}", headers=_auth(token_b)
    )
    assert cross_get.status_code == 404
    cross_status = await client.patch(
        f"/api/v1/medical-tourism/referral-commissions/{commission['id']}/status",
        json={"status": "CANCELLED"},
        headers=_auth(token_b),
    )
    assert cross_status.status_code == 404


# --- Public lead intake -> PatientLead vertical integration -----------------


@requires_real_postgres
async def test_public_lead_creates_patient_lead_for_medical_tourism_tenant(client) -> None:
    reg = await _register(client, "MT Public Co", f"owner-mtpub-{uuid.uuid4().hex[:8]}@example.com")
    tenant_id = uuid.UUID(reg["user"]["tenant_id"])

    vx_service = VerticalExtensionService(__import__("app.db.session", fromlist=["async_session_maker"]).async_session_maker)
    try:
        await vx_service.get_by_key("medical_tourism")
    except Exception:
        await vx_service.create_vertical(key="medical_tourism", name="Medical Tourism")
    await vx_service.enable_for_organization(tenant_id, "medical_tourism")

    resp = await client.post(
        f"/api/v1/public/leads/{tenant_id}",
        json={
            "name": "Public Patient",
            "source": "WEB",
            "email": "patient@example.com",
            "preferred_destination_country": "tr",
            "has_insurance": False,
        },
    )
    # Consent gate (tests/test_consent_gate_intake.py): a Medical Tourism tenant whose owner has approved no consent wording keeps its public form
    # closed, so nothing is stored. The approved-wording happy path (lead + patient lead + evidence) is covered there.
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"] == "public_intake_closed"


@requires_real_postgres
async def test_public_lead_stays_generic_for_non_medical_tourism_tenant(client) -> None:
    reg = await _register(client, "Generic Public Co", f"owner-genpub-{uuid.uuid4().hex[:8]}@example.com")
    tenant_id = uuid.UUID(reg["user"]["tenant_id"])
    # Deliberately never enabled the medical_tourism vertical for this tenant.

    resp = await client.post(
        f"/api/v1/public/leads/{tenant_id}",
        json={"name": "Generic Visitor", "source": "WEB", "email": "visitor@example.com"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["received"] is True
    assert body["lead_id"] is not None
    assert body["patient_lead_id"] is None

    from app.services.medical_tourism_service import MedicalTourismService, NotFoundError
    from app.db.session import async_session_maker

    mt_service = MedicalTourismService(async_session_maker)
    with pytest.raises(NotFoundError):
        await mt_service.get_patient_lead_by_lead_id(tenant_id, uuid.UUID(body["lead_id"]))
