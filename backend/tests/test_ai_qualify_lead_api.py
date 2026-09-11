"""Phase 12E: POST /leads/{lead_id}/ai-qualify-advisory — real HTTP-level
check that the endpoint is wired correctly and honestly reports
unavailability with no credentials configured."""

import pytest
from httpx import AsyncClient

from app.core.config import get_settings

pytestmark = pytest.mark.asyncio

# Phase 31: this endpoint's tool is wired through the real
# build_tool_registry() -> get_ai_provider() factory (not a test double),
# so honesty means matching whichever credential state this environment
# actually has, not assuming none will ever exist.
_LIVE_PROVIDER_CONFIGURED = bool(get_settings().ANTHROPIC_API_KEY or get_settings().OPENAI_API_KEY)


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


async def test_ai_qualify_advisory_endpoint_is_honest_when_unconfigured(client: AsyncClient) -> None:
    token = await _register_and_get_token(client, "AI Qualify API Co", "owner@aiqualifyapi.com")
    headers = {"Authorization": f"Bearer {token}"}

    lead_resp = await client.post(
        "/api/v1/leads",
        json={"name": "Test Lead", "source": "WEB", "urgency": "HIGH"},
        headers=headers,
    )
    assert lead_resp.status_code == 201, lead_resp.text
    lead_id = lead_resp.json()["lead"]["id"]

    resp = await client.post(f"/api/v1/leads/{lead_id}/ai-qualify-advisory", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    if _LIVE_PROVIDER_CONFIGURED:
        assert body["available"] is True
        assert body["unavailable_reason"] is None
    else:
        assert body["available"] is False
        assert "no ai provider configured" in body["unavailable_reason"].lower()


async def test_ai_qualify_advisory_requires_auth(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/leads/00000000-0000-0000-0000-000000000000/ai-qualify-advisory")
    assert resp.status_code == 401
