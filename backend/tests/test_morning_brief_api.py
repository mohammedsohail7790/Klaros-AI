import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _register_and_login(client: AsyncClient, org: str, email: str) -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": org,
            "full_name": "Owner",
            "email": email,
            "password": "supersecret1",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["tokens"]["access_token"]


async def test_generate_and_fetch_morning_brief_over_api(client: AsyncClient) -> None:
    token = await _register_and_login(client, "Morning Brief Co", "owner@morningbriefco.com")
    headers = {"Authorization": f"Bearer {token}"}

    generated = await client.post("/api/v1/morning-brief/generate", headers=headers)
    assert generated.status_code == 200, generated.text
    assert generated.json()["headline"] == "No significant activity."

    latest = await client.get("/api/v1/morning-brief/latest", headers=headers)
    assert latest.status_code == 200
    assert latest.json()["headline"] == "No significant activity."


async def test_morning_brief_settings_roundtrip(client: AsyncClient) -> None:
    token = await _register_and_login(client, "Brief Settings Co", "owner@briefsettingsco.com")
    headers = {"Authorization": f"Bearer {token}"}

    default = await client.get("/api/v1/morning-brief/settings", headers=headers)
    assert default.status_code == 200
    assert default.json()["enabled"] is False

    updated = await client.put(
        "/api/v1/morning-brief/settings",
        json={"enabled": True, "local_time": "08:30", "timezone": "America/New_York"},
        headers=headers,
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["enabled"] is True
    assert body["local_time"] == "08:30"
    assert body["timezone"] == "America/New_York"
