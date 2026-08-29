"""Phase 12D: tenant-scoped integration connection API — real HTTP-level
tenant isolation (distinct from the service-layer tests in
test_integration_connection_service.py, which don't go through auth/API
routing at all)."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _register_and_get_token(client: AsyncClient, org_name: str, email: str) -> str:
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
    return resp.json()["tokens"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_connect_to_unimplemented_provider_is_honest_error_not_fake_connected(client: AsyncClient) -> None:
    token = await _register_and_get_token(client, "Conn API Co", "owner@connapi.com")
    resp = await client.post(
        "/api/v1/integrations/connections/quickbooks/connect",
        json={"credential": {"client_id": "fake", "client_secret": "fake"}},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ERROR"
    assert "no real verifier" in body["last_error"].lower()
    # Never echo the submitted credential back.
    assert "client_secret" not in body
    assert "credential" not in body


async def test_list_connections_only_shows_own_tenant(client: AsyncClient) -> None:
    token_a = await _register_and_get_token(client, "Tenant A Co", "owner@tenanta.com")
    token_b = await _register_and_get_token(client, "Tenant B Co", "owner@tenantb.com")

    await client.post(
        "/api/v1/integrations/connections/quickbooks/connect",
        json={"credential": {"client_id": "tenant-a-secret"}},
        headers=_auth(token_a),
    )

    resp_b = await client.get("/api/v1/integrations/connections", headers=_auth(token_b))
    assert resp_b.status_code == 200
    assert resp_b.json() == []

    resp_a = await client.get("/api/v1/integrations/connections", headers=_auth(token_a))
    assert resp_a.status_code == 200
    assert len(resp_a.json()) == 1


async def test_tenant_b_cannot_verify_tenant_as_connection_via_api(client: AsyncClient) -> None:
    token_a = await _register_and_get_token(client, "Verify A Co", "owner@verifya.com")
    token_b = await _register_and_get_token(client, "Verify B Co", "owner@verifyb.com")

    await client.post(
        "/api/v1/integrations/connections/quickbooks/connect",
        json={"credential": {"client_id": "a-secret"}},
        headers=_auth(token_a),
    )

    resp = await client.post(
        "/api/v1/integrations/connections/quickbooks/verify", headers=_auth(token_b)
    )
    assert resp.status_code == 404


async def test_tenant_b_cannot_disconnect_tenant_as_connection_via_api(client: AsyncClient) -> None:
    token_a = await _register_and_get_token(client, "Disc A Co", "owner@disca.com")
    token_b = await _register_and_get_token(client, "Disc B Co", "owner@discb.com")

    await client.post(
        "/api/v1/integrations/connections/quickbooks/connect",
        json={"credential": {"client_id": "a-secret"}},
        headers=_auth(token_a),
    )

    resp = await client.post(
        "/api/v1/integrations/connections/quickbooks/disconnect", headers=_auth(token_b)
    )
    assert resp.status_code == 404

    # Tenant A's connection must be unaffected.
    resp_a = await client.get("/api/v1/integrations/connections", headers=_auth(token_a))
    assert resp_a.json()[0]["status"] == "ERROR"  # unchanged from the connect attempt, not disconnected


async def test_unauthenticated_request_is_rejected(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/integrations/connections")
    assert resp.status_code == 401


async def test_disconnect_clears_status_for_owning_tenant(client: AsyncClient) -> None:
    token = await _register_and_get_token(client, "Disc Own Co", "owner@discown.com")
    await client.post(
        "/api/v1/integrations/connections/quickbooks/connect",
        json={"credential": {"client_id": "fake"}},
        headers=_auth(token),
    )
    resp = await client.post(
        "/api/v1/integrations/connections/quickbooks/disconnect", headers=_auth(token)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "DISCONNECTED"
