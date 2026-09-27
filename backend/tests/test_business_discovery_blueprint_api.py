"""Phase 2: API-level tests for /business-discovery and /business-blueprint
— authentication, RBAC (wrong role rejected), tenant isolation (a user
cannot read/mutate another tenant's session/blueprint/claim), and a full
happy-path walk from free-text description through claim confirmation to
blueprint activation, exercised entirely through the HTTP API (mirrors
tests/test_integration_provider_catalog.py's `_register` helper pattern).
"""

import pytest

pytestmark = pytest.mark.asyncio


async def _register(client, org_name: str, email: str) -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org_name, "full_name": "Owner Test", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["tokens"]["access_token"]


async def _tech_token(client, owner_token: str, email: str) -> str:
    import uuid as _uuid

    from app.core.security import create_access_token, hash_password
    from app.db.session import async_session_maker
    from app.models.rbac import Role
    from app.models.user import User

    resp = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {owner_token}"})
    tenant_id = _uuid.UUID(resp.json()["tenant_id"])

    async with async_session_maker() as session:
        tech = User(
            tenant_id=tenant_id, email=email, full_name="Tech User",
            hashed_password=hash_password("supersecret1"), role=Role.TECHNICIAN,
        )
        session.add(tech)
        await session.commit()
        await session.refresh(tech)
        return create_access_token(tech.id, tenant_id, Role.TECHNICIAN)


async def test_discovery_requires_authentication(client) -> None:
    resp = await client.post("/api/v1/business-discovery/sessions", json={"description": "x"})
    assert resp.status_code in (401, 403)


async def test_blueprint_requires_authentication(client) -> None:
    resp = await client.get("/api/v1/business-blueprint")
    assert resp.status_code in (401, 403)


async def test_technician_cannot_manage_blueprint(client) -> None:
    """TECHNICIAN is granted no MANAGE_BLUEPRINT (see app/models/rbac.py)."""
    owner_token = await _register(client, "Tech Blocked Co", "owner@techblockedco.com")
    tech_token = await _tech_token(client, owner_token, "tech@techblockedco.com")

    resp = await client.post(
        "/api/v1/business-discovery/sessions",
        json={"description": "A field service business."},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp.status_code == 201, resp.text

    resp = await client.post(
        "/api/v1/business-blueprint/activate",
        json={"blueprint_id": "00000000-0000-0000-0000-000000000000"},
        headers={"Authorization": f"Bearer {tech_token}"},
    )
    assert resp.status_code == 403


async def test_full_discovery_to_activation_flow(client) -> None:
    owner_token = await _register(client, "Flow Co", "owner@flowco.com")
    headers = {"Authorization": f"Bearer {owner_token}"}

    start = await client.post(
        "/api/v1/business-discovery/sessions",
        json={"description": "We connect international patients with partner hospitals in Turkey and India, earning a referral commission."},
        headers=headers,
    )
    assert start.status_code == 201, start.text
    body = start.json()
    session_id = body["session_id"]
    assert body["session_status"] == "ACTIVE"

    session_resp = await client.get(f"/api/v1/business-discovery/sessions/{session_id}", headers=headers)
    assert session_resp.status_code == 200
    assert len(session_resp.json()["turns"]) == 1

    draft_resp = await client.get("/api/v1/business-blueprint/draft", headers=headers)
    assert draft_resp.status_code == 200
    draft = draft_resp.json()
    blueprint_id = draft["blueprint"]["id"]
    assert draft["blueprint"]["status"] == "DRAFT"
    proposed_claims = [c for c in draft["claims"] if c["status"] == "PROPOSED"]
    assert len(proposed_claims) >= 1

    # Confirm every proposed claim covering a minimum-bar section, then
    # manually fill the remaining minimum-bar sections via PUT (simulating
    # a human completing what the deterministic fallback didn't extract).
    for claim in proposed_claims:
        confirm_resp = await client.post(
            f"/api/v1/business-blueprint/claims/{claim['id']}/confirm", headers=headers
        )
        assert confirm_resp.status_code == 200, confirm_resp.text
        assert confirm_resp.json()["status"] == "CONFIRMED"

        # Idempotent repeat.
        again = await client.post(f"/api/v1/business-blueprint/claims/{claim['id']}/confirm", headers=headers)
        assert again.status_code == 200

    for section_key in ("IDENTITY", "INDUSTRY", "BUSINESS_MODEL", "REQUIRED_CAPABILITIES"):
        put_resp = await client.put(
            f"/api/v1/business-blueprint/sections/{section_key}",
            json={"blueprint_id": blueprint_id, "data": {"filled": True}},
            headers=headers,
        )
        assert put_resp.status_code == 200, put_resp.text

    activate_resp = await client.post(
        "/api/v1/business-blueprint/activate", json={"blueprint_id": blueprint_id}, headers=headers
    )
    assert activate_resp.status_code == 200, activate_resp.text
    assert activate_resp.json()["status"] == "ACTIVE"

    active_resp = await client.get("/api/v1/business-blueprint", headers=headers)
    assert active_resp.status_code == 200
    assert active_resp.json()["blueprint"]["id"] == blueprint_id

    version_resp = await client.get("/api/v1/business-blueprint/versions/1", headers=headers)
    assert version_resp.status_code == 200


async def test_reject_claim_endpoint(client) -> None:
    owner_token = await _register(client, "Reject Co", "owner@rejectco.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    start = await client.post(
        "/api/v1/business-discovery/sessions", json={"description": "A dropshipping storefront."}, headers=headers
    )
    draft = (await client.get("/api/v1/business-blueprint/draft", headers=headers)).json()
    claim_id = draft["claims"][0]["id"]

    resp = await client.post(
        f"/api/v1/business-blueprint/claims/{claim_id}/reject", json={"reason": "not accurate"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "REJECTED"

    confirm_after_reject = await client.post(f"/api/v1/business-blueprint/claims/{claim_id}/confirm", headers=headers)
    assert confirm_after_reject.status_code == 409


async def test_tenant_cannot_read_another_tenants_discovery_session(client) -> None:
    owner_a = await _register(client, "Tenant A Co", "owner@tenantaco.com")
    owner_b = await _register(client, "Tenant B Co", "owner@tenantbco.com")

    start = await client.post(
        "/api/v1/business-discovery/sessions",
        json={"description": "Tenant A's private business idea."},
        headers={"Authorization": f"Bearer {owner_a}"},
    )
    session_id = start.json()["session_id"]

    cross_read = await client.get(
        f"/api/v1/business-discovery/sessions/{session_id}", headers={"Authorization": f"Bearer {owner_b}"}
    )
    assert cross_read.status_code == 404


async def test_tenant_cannot_confirm_another_tenants_claim(client) -> None:
    owner_a = await _register(client, "Tenant C Co", "owner@tenantcco.com")
    owner_b = await _register(client, "Tenant D Co", "owner@tenantdco.com")

    await client.post(
        "/api/v1/business-discovery/sessions",
        json={"description": "Tenant C's private business idea."},
        headers={"Authorization": f"Bearer {owner_a}"},
    )
    draft = (
        await client.get("/api/v1/business-blueprint/draft", headers={"Authorization": f"Bearer {owner_a}"})
    ).json()
    claim_id = draft["claims"][0]["id"]

    cross_confirm = await client.post(
        f"/api/v1/business-blueprint/claims/{claim_id}/confirm", headers={"Authorization": f"Bearer {owner_b}"}
    )
    assert cross_confirm.status_code == 404


async def test_answer_unknown_session_returns_404(client) -> None:
    owner_token = await _register(client, "NotFound Co", "owner@notfoundco.com")
    resp = await client.post(
        "/api/v1/business-discovery/sessions/00000000-0000-0000-0000-000000000000/answer",
        json={"answer": "x"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp.status_code == 404


async def test_activate_without_minimum_bar_returns_409(client) -> None:
    owner_token = await _register(client, "TooEarly Co", "owner@tooearlyco.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    await client.post("/api/v1/business-discovery/sessions", json={"description": "x"}, headers=headers)
    draft = (await client.get("/api/v1/business-blueprint/draft", headers=headers)).json()
    resp = await client.post(
        "/api/v1/business-blueprint/activate", json={"blueprint_id": draft["blueprint"]["id"]}, headers=headers
    )
    assert resp.status_code == 409
