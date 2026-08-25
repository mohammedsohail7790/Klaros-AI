import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, org_name: str, email: str) -> dict:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": org_name,
            "full_name": "Owner Test",
            "email": email,
            "password": "supersecret1",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_register_and_login(client: AsyncClient) -> None:
    data = await _register(client, "Demo HVAC Company", "owner@demohvac.com")
    assert data["organization_slug"] == "demo-hvac-company"
    assert data["user"]["role"] == "OWNER"

    login = await client.post(
        "/api/v1/auth/login",
        json={
            "organization_slug": "demo-hvac-company",
            "email": "owner@demohvac.com",
            "password": "supersecret1",
        },
    )
    assert login.status_code == 200
    assert "access_token" in login.json()


async def test_login_wrong_password_rejected(client: AsyncClient) -> None:
    await _register(client, "Wrong Pass Co", "owner@wrongpass.com")
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "organization_slug": "wrong-pass-co",
            "email": "owner@wrongpass.com",
            "password": "not-the-password",
        },
    )
    assert login.status_code == 401


async def test_tenant_a_cannot_access_tenant_b_data(client: AsyncClient) -> None:
    """Critical scenario from section 36: Tenant A cannot access Tenant B."""
    org_a = await _register(client, "Tenant A Co", "owner@tenanta.com")
    org_b = await _register(client, "Tenant B Co", "owner@tenantb.com")

    token_a = org_a["tokens"]["access_token"]
    token_b = org_b["tokens"]["access_token"]

    me_a = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token_a}"})
    me_b = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token_b}"})

    assert me_a.status_code == 200
    assert me_b.status_code == 200
    assert me_a.json()["tenant_id"] == org_a["user"]["tenant_id"]
    assert me_b.json()["tenant_id"] == org_b["user"]["tenant_id"]
    assert me_a.json()["tenant_id"] != me_b.json()["tenant_id"]
    assert me_a.json()["id"] != me_b.json()["id"]


async def test_missing_token_rejected(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/users/me")
    assert resp.status_code == 401


async def test_invalid_token_rejected(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/v1/users/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401
