"""Phase 3: API-level tests for /recommendations — authentication, RBAC
(wrong role rejected), tenant isolation over HTTP, and a full happy-path
walk from an activated blueprint through generate -> list -> accept/reject,
exercised entirely through the HTTP API (mirrors
tests/test_business_discovery_blueprint_api.py's structure).
"""

import uuid as _uuid

import pytest

from app.models.business_blueprint import MINIMUM_BAR_SECTIONS, BlueprintSectionKey, ClaimProvenance, ClaimType
from app.services.business_blueprint_service import BusinessBlueprintService

pytestmark = pytest.mark.asyncio


async def _register(client, org_name: str, email: str) -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org_name, "full_name": "Owner Test", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["tokens"]["access_token"]


async def _tenant_id(client, token: str) -> _uuid.UUID:
    resp = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    return _uuid.UUID(resp.json()["tenant_id"])


async def _readonly_token(client, owner_token: str, email: str, tenant_id: _uuid.UUID) -> str:
    from app.core.security import create_access_token, hash_password
    from app.db.session import async_session_maker
    from app.models.rbac import Role
    from app.models.user import User

    async with async_session_maker() as session:
        user = User(
            tenant_id=tenant_id, email=email, full_name="Read Only User",
            hashed_password=hash_password("supersecret1"), role=Role.READ_ONLY,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return create_access_token(user.id, tenant_id, Role.READ_ONLY)


async def _activate_blueprint(tenant_id: _uuid.UUID, capability_keys: list[str]):
    from app.db.session import async_session_maker

    service = BusinessBlueprintService(async_session_maker)
    blueprint = await service.get_or_create_draft(tenant_id, created_by=None)
    for key in MINIMUM_BAR_SECTIONS:
        if key == BlueprintSectionKey.REQUIRED_CAPABILITIES:
            continue
        claim = await service.propose_claim(
            tenant_id, blueprint.id, section_key=key.value, claim_type=ClaimType.FACT.value,
            key=f"{key.value.lower()}.v", value="ok", confidence=None,
            provenance=ClaimProvenance.USER_STATED.value, discovery_turn_id=None, evidence_ref=None,
        )
        await service.confirm_claim(tenant_id, claim.id, confirmed_by=None)
    cap_claim = await service.propose_claim(
        tenant_id, blueprint.id, section_key=BlueprintSectionKey.REQUIRED_CAPABILITIES.value,
        claim_type=ClaimType.REQUIREMENT.value, key="required_capabilities.list", value=capability_keys,
        confidence=0.9, provenance=ClaimProvenance.AI_INFERRED.value, discovery_turn_id=None, evidence_ref=None,
    )
    await service.confirm_claim(tenant_id, cap_claim.id, confirmed_by=None)
    return await service.activate(tenant_id, blueprint.id, activated_by=None)


async def test_recommendations_require_authentication(client) -> None:
    resp = await client.get("/api/v1/recommendations")
    assert resp.status_code in (401, 403)

    resp = await client.post("/api/v1/recommendations/generate")
    assert resp.status_code in (401, 403)


async def test_read_only_role_cannot_generate_but_can_list(client) -> None:
    owner_token = await _register(client, "RO Blocked Co", "owner@roblockedco.com")
    tenant_id = await _tenant_id(client, owner_token)
    ro_token = await _readonly_token(client, owner_token, "ro@roblockedco.com", tenant_id)

    resp = await client.get("/api/v1/recommendations", headers={"Authorization": f"Bearer {ro_token}"})
    assert resp.status_code == 200

    resp = await client.post("/api/v1/recommendations/generate", headers={"Authorization": f"Bearer {ro_token}"})
    assert resp.status_code == 403


async def test_generate_requires_active_blueprint_returns_409(client) -> None:
    owner_token = await _register(client, "No Blueprint Co", "owner@noblueprintco.com")
    resp = await client.post(
        "/api/v1/recommendations/generate", headers={"Authorization": f"Bearer {owner_token}"}
    )
    assert resp.status_code == 409


async def test_full_generate_list_accept_reject_flow(client) -> None:
    owner_token = await _register(client, "Flow Reco Co", "owner@flowrecoco.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    tenant_id = await _tenant_id(client, owner_token)

    await _activate_blueprint(tenant_id, ["accounting", "payment_processing"])

    gen = await client.post("/api/v1/recommendations/generate", headers=headers)
    assert gen.status_code == 200, gen.text
    body = gen.json()
    assert body["run"]["blueprint_version"] == 1
    assert len(body["recommendations"]) >= 2

    listing = await client.get("/api/v1/recommendations", headers=headers)
    assert listing.status_code == 200
    recs = listing.json()
    assert len(recs) == len(body["recommendations"])
    for r in recs:
        assert r["status"] == "PROPOSED"
        assert "why" in r and "what" in r and "confidence" in r and "source" in r
        # Never leaks internal implementation details.
        assert "prompt" not in r
        assert "raw_model_output" not in r

    run_id = body["run"]["id"]
    run_resp = await client.get(f"/api/v1/recommendations/runs/{run_id}", headers=headers)
    assert run_resp.status_code == 200
    assert run_resp.json()["id"] == run_id

    target = recs[0]["id"]
    accept_resp = await client.post(f"/api/v1/recommendations/{target}/accept", headers=headers)
    assert accept_resp.status_code == 200
    assert accept_resp.json()["status"] == "ACCEPTED"

    other = recs[1]["id"]
    reject_resp = await client.post(
        f"/api/v1/recommendations/{other}/reject", json={"reason": "not applicable"}, headers=headers
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "REJECTED"

    # Explicit action endpoints, never a generic PATCH: re-rejecting an
    # ACCEPTED recommendation is a 409, not silently accepted.
    conflict = await client.post(f"/api/v1/recommendations/{target}/reject", headers=headers)
    assert conflict.status_code == 409


async def test_reject_unknown_recommendation_returns_404(client) -> None:
    owner_token = await _register(client, "404 Reco Co", "owner@404recoco.com")
    resp = await client.post(
        f"/api/v1/recommendations/{_uuid.uuid4()}/accept", headers={"Authorization": f"Bearer {owner_token}"}
    )
    assert resp.status_code == 404


async def test_tenant_isolation_over_http(client) -> None:
    owner_a = await _register(client, "Tenant A Reco Co", "owner@tenantarecoco.com")
    owner_b = await _register(client, "Tenant B Reco Co", "owner@tenantbrecoco.com")
    tenant_a = await _tenant_id(client, owner_a)

    await _activate_blueprint(tenant_a, ["accounting"])
    gen = await client.post("/api/v1/recommendations/generate", headers={"Authorization": f"Bearer {owner_a}"})
    assert gen.status_code == 200
    rec_id = gen.json()["recommendations"][0]["id"]

    # Tenant B cannot see it in their list, and cannot accept it.
    listing_b = await client.get("/api/v1/recommendations", headers={"Authorization": f"Bearer {owner_b}"})
    assert listing_b.status_code == 200
    assert all(r["id"] != rec_id for r in listing_b.json())

    accept_b = await client.post(
        f"/api/v1/recommendations/{rec_id}/accept", headers={"Authorization": f"Bearer {owner_b}"}
    )
    assert accept_b.status_code == 404
