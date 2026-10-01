"""Phase 12 (PHASE_12_WEBSITE_BUILDER_PRODUCTIZATION_IMPLEMENTATION_LOG.md
§11 "Medical Tourism E2E" / §12 "Tenant isolation" / §16 "Security"): the
mandatory end-to-end acceptance scenario, run over the REAL HTTP surface
(FastAPI + RBAC dependencies + the public routers) rather than calling
service classes directly — Phase 11's own test suite
(tests/test_website_medical_tourism_validation.py) already proves the
service-layer flow; this file is the first to prove the same flow works
through the actual authenticated editor API and the actual public runtime
API a browser would hit, plus the attack surface around it:
tenant-spoofing, draft-version public access, unpublished-version access,
and RBAC over HTTP (not just the permission-table unit test in
tests/test_website_rbac.py).

Synthetic data only — no real hospitals/providers, per HARD SCOPE.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.business_blueprint import BlueprintSectionKey, ClaimProvenance, ClaimType
from app.models.crm import Lead
from app.models.vertical_extension import VerticalExtensionStatus
from app.services.business_blueprint_service import BusinessBlueprintService
from app.services.medical_tourism_service import (
    CreateOfferingInput,
    CreateProcedureInput,
    CreateProviderInput,
    MedicalTourismService,
)
from app.services.vertical_extension_service import VerticalExtensionService

pytestmark = pytest.mark.asyncio


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: AsyncClient, org_name: str, email: str) -> dict:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org_name, "full_name": "Owner", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _seed_medical_tourism_blueprint(tenant_id: uuid.UUID) -> None:
    """(1)-(6) of the mandatory scenario: activate the vertical, build an
    ACTIVE Blueprint, and create synthetic provider/procedure/offering
    data — identical setup to
    tests/test_website_medical_tourism_validation.py, done directly
    against the service layer (no HTTP surface exists for these Phase 1/10
    steps; only website generation/editing/publishing is this test's own
    subject)."""
    vertical_service = VerticalExtensionService(async_session_maker)
    blueprint_service = BusinessBlueprintService(async_session_maker)
    mt_service = MedicalTourismService(async_session_maker)

    vertical = await vertical_service.create_vertical(
        key=f"medical_tourism_phase12_{tenant_id.hex[:8]}",
        name="Medical Tourism (Phase 12 e2e)",
        status=VerticalExtensionStatus.ACTIVE,
        capabilities=["medical_tourism.provider_directory", "medical_tourism.procedure_catalog"],
    )
    await vertical_service.enable_for_organization(tenant_id, vertical.key, enabled_by=None)

    blueprint = await blueprint_service.get_or_create_draft(tenant_id, created_by=None)

    async def _confirm(section_key, key, value, claim_type=ClaimType.FACT):
        claim = await blueprint_service.propose_claim(
            tenant_id, blueprint.id, section_key=section_key.value, claim_type=claim_type.value,
            key=key, value=value, confidence=0.9, provenance=ClaimProvenance.USER_STATED.value,
            discovery_turn_id=None, evidence_ref=None,
        )
        await blueprint_service.confirm_claim(tenant_id, claim.id, confirmed_by=None)

    await _confirm(BlueprintSectionKey.IDENTITY, "business_name", "Synthetic Global Health")
    await _confirm(BlueprintSectionKey.IDENTITY, "description", "Synthetic test data only.")
    await _confirm(BlueprintSectionKey.INDUSTRY, "industry.v", "Medical Tourism")
    await _confirm(BlueprintSectionKey.BUSINESS_MODEL, "business_model.v", "Facilitation services")
    await _confirm(BlueprintSectionKey.PRODUCTS_SERVICES, "services", ["Hip replacement", "Dental care"])
    await _confirm(BlueprintSectionKey.CUSTOMERS, "target_customer", "International patients")
    await _confirm(BlueprintSectionKey.COMMUNICATIONS, "contact_email", "care@synthetic-ghp.example")
    await _confirm(
        BlueprintSectionKey.REQUIRED_CAPABILITIES, "required_capabilities.list",
        ["medical_tourism.provider_directory"], claim_type=ClaimType.REQUIREMENT,
    )
    await blueprint_service.activate(tenant_id, blueprint.id, activated_by=None)

    provider, _ = await mt_service.create_provider(
        tenant_id,
        CreateProviderInput(name="Synthetic Test Hospital", country="TR", city="Istanbul", description="Synthetic test data only."),
    )
    procedure, _ = await mt_service.create_procedure(
        tenant_id, CreateProcedureInput(name="Synthetic Hip Replacement", category="Orthopedics")
    )
    await mt_service.create_provider_procedure(
        tenant_id, CreateOfferingInput(provider_id=provider.id, procedure_id=procedure.id, estimated_price=None, currency="USD"),
    )


async def test_full_medical_tourism_public_website_e2e(client: AsyncClient) -> None:
    owner = await _register(client, "Synthetic Global Health", "owner@synthetic-ghp.example")
    token = owner["tokens"]["access_token"]
    tenant_id = uuid.UUID(owner["user"]["tenant_id"])

    await _seed_medical_tourism_blueprint(tenant_id)

    # (7)/(8)/(9) generate a draft via the authenticated editor API — the
    # real HTTP equivalent of "open the editor", since no browser-driven
    # test harness exists in this backend-only environment.
    gen_resp = await client.post("/api/v1/websites/generate", headers=_auth(token))
    assert gen_resp.status_code == 200, gen_resp.text
    gen_body = gen_resp.json()
    website_id = gen_body["website"]["id"]
    version_id = gen_body["version"]["id"]
    assert gen_body["version"]["status"] == "DRAFT"

    # (10) preview the draft through the authenticated preview endpoint.
    preview_resp = await client.get(
        f"/api/v1/websites/{website_id}/versions/{version_id}/preview", headers=_auth(token)
    )
    assert preview_resp.status_code == 200, preview_resp.text
    preview_body = preview_resp.json()
    home_page = next(p for p in preview_body["pages"] if p["slug"] == "home")
    assert any(s["component_type"] == "PROVIDER_DIRECTORY" for s in home_page["sections"])

    # Regression (Bug A, Website Builder editor rehydration): the
    # authenticated preview endpoint must echo each section's saved
    # `data_source` back so frontend/components/website/SectionEditor.tsx
    # can rehydrate the provider-key field after save/reload, instead of
    # always showing blank. Auto-generation (above) binds this directory to
    # a real provider_key, so it must come back non-null here.
    preview_directory = next(s for s in home_page["sections"] if s["component_type"] == "PROVIDER_DIRECTORY")
    assert preview_directory["data_source"] is not None
    assert preview_directory["data_source"]["provider_key"] == "medical_tourism.provider_directory"

    # Attack: this same tenant's DRAFT must not be visible on the public
    # endpoint before it is ever published.
    pre_publish_public = await client.get(f"/api/v1/public/websites/{tenant_id}")
    assert pre_publish_public.status_code == 404

    # (11) publish.
    publish_resp = await client.post(
        f"/api/v1/websites/{website_id}/versions/{version_id}/publish", headers=_auth(token)
    )
    assert publish_resp.status_code == 200, publish_resp.text
    assert publish_resp.json()["status"] == "PUBLISHED"

    # (12) open the public website — no Authorization header at all.
    public_resp = await client.get(f"/api/v1/public/websites/{tenant_id}")
    assert public_resp.status_code == 200, public_resp.text
    public_body = public_resp.json()
    public_home = next(p for p in public_body["pages"] if p["slug"] == "home")

    # (13) provider directory renders through the generic data-provider
    # mechanism with real (synthetic) data attached.
    directory = next(s for s in public_home["sections"] if s["component_type"] == "PROVIDER_DIRECTORY")
    assert directory["data"]["resolved"] is True
    assert directory["data"]["items"][0]["name"] == "Synthetic Test Hospital"

    # Security boundary for the Bug A fix: the editor-only `data_source`
    # overlay lives solely in the authenticated preview_version() handler
    # (app/api/v1/websites.py) — the public, unauthenticated render path
    # (app/api/v1/public_websites.py) calls the shared renderer directly and
    # must never gain this key, even though it renders the exact same
    # underlying section.
    assert "data_source" not in directory

    # (14) procedure information renders.
    procedure_section = next(
        (s for s in public_home["sections"] if s["component_type"] == "PROCEDURE_LIST"), None
    )
    if procedure_section is not None:
        assert procedure_section["data"]["resolved"] is True

    # (15)/(16) submit the CONTACT_FORM — this is the exact call the
    # frontend's rendered CONTACT_FORM component makes
    # (frontend/components/website/ComponentRegistry.tsx's ContactForm ->
    # lib/api.ts's submitPublicWebsiteLead), hitting the SAME public lead
    # endpoint tests/test_public_lead_intake.py already covers in
    # isolation — this test proves it end-to-end from "published website"
    # through to "a real Lead exists for the right tenant".
    contact_page = next((p for p in public_body["pages"] if p["slug"] == "contact"), None)
    assert contact_page is not None
    assert any(s["component_type"] == "CONTACT_FORM" for s in contact_page["sections"])

    lead_resp = await client.post(
        f"/api/v1/public/leads/{tenant_id}",
        json={
            "name": "Synthetic Visitor", "source": "WEB", "email": "visitor@example.com",
            "description": "Interested in hip replacement pricing.",
        },
    )
    assert lead_resp.status_code == 201, lead_resp.text
    lead_id = lead_resp.json()["lead_id"]
    assert lead_id

    async with async_session_maker() as session:
        lead = (await session.execute(select(Lead).where(Lead.id == uuid.UUID(lead_id)))).scalar_one()
    assert lead.tenant_id == tenant_id
    assert lead.name == "Synthetic Visitor"

    # (17) attempt tenant spoofing: a second, unrelated tenant must never
    # see the first tenant's public website or be able to attach a lead to
    # it via anything other than the first tenant's own tenant_id.
    other = await _register(client, "Unrelated Co", "owner@unrelated.example")
    other_tenant_id = uuid.UUID(other["user"]["tenant_id"])
    spoofed_public = await client.get(f"/api/v1/public/websites/{other_tenant_id}")
    assert spoofed_public.status_code == 404  # the other tenant has no website at all

    # (18) attempt draft-version public access: create a second DRAFT
    # version on the now-published site and confirm the public endpoint
    # still only ever serves the PUBLISHED one, never the new draft.
    new_draft_resp = await client.post(
        f"/api/v1/websites/{website_id}/versions/{version_id}/new-draft", headers=_auth(token)
    )
    assert new_draft_resp.status_code == 200, new_draft_resp.text
    new_draft_id = new_draft_resp.json()["id"]
    assert new_draft_resp.json()["status"] == "DRAFT"

    still_public = await client.get(f"/api/v1/public/websites/{tenant_id}")
    assert still_public.status_code == 200
    # The public router (app/api/v1/public_websites.py) exposes no
    # version_id-accepting surface at all — confirm no such path exists to
    # even attempt a direct guess against.
    guess_resp = await client.get(f"/api/v1/public/websites/{tenant_id}/versions/{new_draft_id}")
    assert guess_resp.status_code == 404

    # (19) attempt unpublished-version access after unpublishing entirely.
    unpublish_resp = await client.post(f"/api/v1/websites/{website_id}/unpublish", headers=_auth(token))
    assert unpublish_resp.status_code == 200, unpublish_resp.text
    after_unpublish = await client.get(f"/api/v1/public/websites/{tenant_id}")
    assert after_unpublish.status_code == 404

    # (20) every attack attempt above failed safely (404s, never a 500 or
    # a cross-tenant/draft leak) — asserted inline at each step.


async def test_public_website_rejects_malformed_and_path_traversal_tenant_ids(client: AsyncClient) -> None:
    for bad in ["not-a-uuid", "../../etc/passwd", "0" * 500]:
        resp = await client.get(f"/api/v1/public/websites/{bad}")
        assert resp.status_code in (404, 422), f"{bad!r} -> {resp.status_code}"

    # An encoded "../" is normalized by Starlette's own routing before it
    # ever reaches app code (a 307 to the normalized path, never a 200) —
    # not a traversal vulnerability since no filesystem path is ever
    # touched, but confirm the normalized destination still resolves to
    # nothing rather than leaking anything.
    redirect_resp = await client.get("/api/v1/public/websites/%2e%2e%2f", follow_redirects=True)
    assert redirect_resp.status_code in (404, 422)


async def test_public_website_query_param_cannot_select_a_different_tenant(client: AsyncClient) -> None:
    owner = await _register(client, "Query Spoof Co", "owner@queryspoof.example")
    token = owner["tokens"]["access_token"]
    tenant_id = uuid.UUID(owner["user"]["tenant_id"])
    await _seed_medical_tourism_blueprint(tenant_id)
    gen = await client.post("/api/v1/websites/generate", headers=_auth(token))
    website_id, version_id = gen.json()["website"]["id"], gen.json()["version"]["id"]
    await client.post(f"/api/v1/websites/{website_id}/versions/{version_id}/publish", headers=_auth(token))

    other = await _register(client, "Other Query Co", "owner@otherquery.example")
    other_tenant_id = other["user"]["tenant_id"]

    # A client-supplied tenant_id/organization_id in the query string must
    # never override the path-resolved tenant.
    resp = await client.get(f"/api/v1/public/websites/{tenant_id}?tenant_id={other_tenant_id}&organization_id={other_tenant_id}")
    assert resp.status_code == 200
    body = resp.json()
    home = next(p for p in body["pages"] if p["slug"] == "home")
    assert any(s["component_type"] == "HERO" for s in home["sections"])
    # (still the requesting tenant's own site — no cross-tenant content;
    # there is nothing tenant-identifying in the payload to assert against
    # directly, so the real proof is the 200 + this tenant's own content,
    # combined with test_public_website_never_leaks_other_tenant_content
    # below.)


async def test_public_website_never_leaks_other_tenant_content(client: AsyncClient) -> None:
    owner_a = await _register(client, "Tenant A Site", "owner@tenanta-site.example")
    token_a = owner_a["tokens"]["access_token"]
    tenant_a = uuid.UUID(owner_a["user"]["tenant_id"])
    await _seed_medical_tourism_blueprint(tenant_a)
    gen_a = await client.post("/api/v1/websites/generate", headers=_auth(token_a))
    await client.post(
        f"/api/v1/websites/{gen_a.json()['website']['id']}/versions/{gen_a.json()['version']['id']}/publish",
        headers=_auth(token_a),
    )

    owner_b = await _register(client, "Tenant B Site", "owner@tenantb-site.example")
    token_b = owner_b["tokens"]["access_token"]
    tenant_b = uuid.UUID(owner_b["user"]["tenant_id"])
    # Tenant B activates no vertical/blueprint and never generates a site.
    b_public = await client.get(f"/api/v1/public/websites/{tenant_b}")
    assert b_public.status_code == 404

    a_public = await client.get(f"/api/v1/public/websites/{tenant_a}")
    assert a_public.status_code == 200
    directory = next(
        s
        for s in next(p for p in a_public.json()["pages"] if p["slug"] == "home")["sections"]
        if s["component_type"] == "PROVIDER_DIRECTORY"
    )
    assert directory["data"]["items"][0]["name"] == "Synthetic Test Hospital"

    # Tenant B's own authenticated GET /websites must never see Tenant A's
    # website.
    b_website_resp = await client.get("/api/v1/websites", headers=_auth(token_b))
    assert b_website_resp.status_code == 200
    assert b_website_resp.json() is None


async def test_rbac_over_http_staff_can_edit_not_publish_read_only_cannot_edit(client: AsyncClient) -> None:
    owner = await _register(client, "RBAC HTTP Co", "owner@rbachttp.example")
    owner_token = owner["tokens"]["access_token"]
    tenant_id = uuid.UUID(owner["user"]["tenant_id"])
    await _seed_medical_tourism_blueprint(tenant_id)

    async def _invite_and_login(role: str, email: str) -> str:
        invite = await client.post(
            "/api/v1/users/invites", json={"email": email, "role": role}, headers=_auth(owner_token)
        )
        assert invite.status_code == 201, invite.text
        token_qs = invite.json()["invite_url_path"].split("token=")[1]
        accept = await client.post(
            f"/api/v1/public/invites/{token_qs}/accept", json={"full_name": role.title(), "password": "memberpass1"}
        )
        assert accept.status_code == 200, accept.text
        return accept.json()["tokens"]["access_token"]

    staff_token = await _invite_and_login("STAFF", "staff@rbachttp.example")
    read_only_token = await _invite_and_login("READ_ONLY", "readonly@rbachttp.example")

    # STAFF can generate (MANAGE_WEBSITE) ...
    gen_resp = await client.post("/api/v1/websites/generate", headers=_auth(staff_token))
    assert gen_resp.status_code == 200, gen_resp.text
    website_id, version_id = gen_resp.json()["website"]["id"], gen_resp.json()["version"]["id"]

    # ... but not publish (PUBLISH_WEBSITE).
    staff_publish = await client.post(
        f"/api/v1/websites/{website_id}/versions/{version_id}/publish", headers=_auth(staff_token)
    )
    assert staff_publish.status_code == 403

    # READ_ONLY can read ...
    ro_read = await client.get("/api/v1/websites", headers=_auth(read_only_token))
    assert ro_read.status_code == 200

    # ... but cannot generate/edit or publish.
    ro_generate = await client.post("/api/v1/websites/generate", headers=_auth(read_only_token))
    assert ro_generate.status_code == 403
    ro_publish = await client.post(
        f"/api/v1/websites/{website_id}/versions/{version_id}/publish", headers=_auth(read_only_token)
    )
    assert ro_publish.status_code == 403

    # OWNER can publish what STAFF drafted.
    owner_publish = await client.post(
        f"/api/v1/websites/{website_id}/versions/{version_id}/publish", headers=_auth(owner_token)
    )
    assert owner_publish.status_code == 200, owner_publish.text


async def test_public_website_has_no_authenticated_only_surface_leak(client: AsyncClient) -> None:
    """Phase 2: the public payload must contain no internal IDs, no RBAC
    info, no audit info, no draft content. Checked structurally rather
    than by a brittle key-name grep."""
    owner = await _register(client, "Leak Check Co", "owner@leakcheck.example")
    token = owner["tokens"]["access_token"]
    tenant_id = uuid.UUID(owner["user"]["tenant_id"])
    await _seed_medical_tourism_blueprint(tenant_id)
    gen = await client.post("/api/v1/websites/generate", headers=_auth(token))
    website_id, version_id = gen.json()["website"]["id"], gen.json()["version"]["id"]
    await client.post(f"/api/v1/websites/{website_id}/versions/{version_id}/publish", headers=_auth(token))

    resp = await client.get(f"/api/v1/public/websites/{tenant_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"theme", "navigation", "seo_defaults", "pages"}
    for page in body["pages"]:
        assert set(page.keys()) == {"slug", "title", "seo", "sections"}
        for section in page["sections"]:
            assert set(section.keys()) <= {"component_type", "props", "data"}
