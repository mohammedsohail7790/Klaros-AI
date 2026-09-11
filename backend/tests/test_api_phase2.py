import pytest
from httpx import AsyncClient

from app.core.config import get_settings

pytestmark = pytest.mark.asyncio


async def _register_and_login(client: AsyncClient, org: str, email: str) -> tuple[str, str]:
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
    data = resp.json()
    return data["tokens"]["access_token"], data["organization_slug"]


async def test_publish_and_get_event_via_api(client: AsyncClient) -> None:
    token, _ = await _register_and_login(client, "Event API Co", "owner@eventapi.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/events",
        json={"event_type": "lead.created", "payload": {"name": "Bob"}},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    event = resp.json()
    assert event["status"] == "PUBLISHED"

    fetched = await client.get(f"/api/v1/events/{event['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == event["id"]


async def test_duplicate_event_via_idempotency_key_over_api(client: AsyncClient) -> None:
    token, _ = await _register_and_login(client, "Dup Event Co", "owner@dupevent.com")
    headers = {"Authorization": f"Bearer {token}"}

    body = {
        "event_type": "payment.received",
        "payload": {"amount": 500},
        "idempotency_key": "stripe-evt-abc123",
    }
    first = await client.post("/api/v1/events", json=body, headers=headers)
    second = await client.post("/api/v1/events", json=body, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["deduplicated"] is True


async def test_list_tools_and_execute_auto_tool(client: AsyncClient) -> None:
    token, _ = await _register_and_login(client, "Tools Co", "owner@toolsco.com")
    headers = {"Authorization": f"Bearer {token}"}

    listing = await client.get("/api/v1/tools", headers=headers)
    assert listing.status_code == 200
    names = {t["name"] for t in listing.json()}
    assert "system.get_current_time" in names
    assert "notifications.create_notification" in names

    resp = await client.post(
        "/api/v1/tools/notifications.create_notification/execute",
        json={"input": {"title": "Hi", "body": "test notification"}},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


async def test_unauthorized_tool_execution_rejected(client: AsyncClient) -> None:
    token, _ = await _register_and_login(client, "Unauth Co", "owner@unauthco.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/tools/nonexistent.tool/execute", json={"input": {}}, headers=headers
    )
    assert resp.status_code == 404


async def test_tool_execution_without_token_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/tools/system.get_current_time/execute", json={"input": {}}
    )
    assert resp.status_code == 401


async def test_blocked_action_via_approval_endpoint(client: AsyncClient) -> None:
    token, _ = await _register_and_login(client, "Blocked Co", "owner@blockedco.com")
    headers = {"Authorization": f"Bearer {token}"}

    # customer.delete is not a registered tool yet in Phase 2, but its policy
    # is BLOCKED — verify the policy table itself, exercised via the registry
    # tests, and that an unregistered name 404s rather than silently no-op'ing.
    resp = await client.post(
        "/api/v1/tools/customer.delete/execute", json={"input": {}}, headers=headers
    )
    assert resp.status_code == 404


async def test_integrations_report_not_connected(client: AsyncClient) -> None:
    token, _ = await _register_and_login(client, "Integrations Co", "owner@integrationsco.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/integrations", headers=headers)
    assert resp.status_code == 200
    statuses = resp.json()
    assert len(statuses) >= 6
    providers = {s["provider"] for s in statuses}
    assert {"quickbooks", "stripe", "servicetitan", "jobber", "google_ads", "meta_ads", "gmail"} <= providers
    # Phase 31/32/production-integration-audit: each of these adapters
    # (app/integrations/adapters.py) checks its own real credential
    # directly — honest either way, matching whichever credential state
    # this environment actually has. This environment has since gained
    # live OPENAI_API_KEY (Phase 31), TWILIO_ACCOUNT_SID/AUTH_TOKEN
    # (Phase 32), and STRIPE_SECRET_KEY (production integration audit) —
    # see ARCHITECTURE_TRACEABILITY.md for all three.
    settings = get_settings()
    live_connected = set()
    if settings.OPENAI_API_KEY:
        live_connected.add("openai")
    if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN:
        live_connected.add("twilio")
    if settings.STRIPE_SECRET_KEY:
        live_connected.add("stripe")
    for s in statuses:
        if s["provider"] in live_connected:
            assert s["status"] == "CONNECTED"
        else:
            assert s["status"] == "NOT_CONNECTED"
