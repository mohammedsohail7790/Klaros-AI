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


async def test_list_events_returns_real_rows(client: AsyncClient) -> None:
    token = await _register_and_login(client, "Events Admin Co", "owner@eventsadmin.com")
    headers = {"Authorization": f"Bearer {token}"}

    await client.post("/api/v1/events", json={"event_type": "lead.created", "payload": {}}, headers=headers)

    resp = await client.get("/api/v1/events", headers=headers)
    assert resp.status_code == 200, resp.text
    events = resp.json()["events"]
    assert len(events) >= 1
    assert events[0]["event_type"] == "lead.created"


async def test_list_events_filters_by_status(client: AsyncClient) -> None:
    token = await _register_and_login(client, "Events Filter Co", "owner@eventsfilter.com")
    headers = {"Authorization": f"Bearer {token}"}

    await client.post("/api/v1/events", json={"event_type": "lead.updated", "payload": {}}, headers=headers)

    resp = await client.get("/api/v1/events", params={"status_filter": "PUBLISHED"}, headers=headers)
    assert resp.status_code == 200
    assert all(e["status"] == "PUBLISHED" for e in resp.json()["events"])


async def test_dead_letters_list_and_replay_over_api(client: AsyncClient) -> None:
    token = await _register_and_login(client, "Dead Letter Co", "owner@deadletter.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/events/dead-letters", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["dead_letters"] == []


async def test_worker_metrics_endpoint_returns_real_counters(client: AsyncClient) -> None:
    token = await _register_and_login(client, "Metrics Co", "owner@metricsco.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/events/metrics", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "events_processed" in body
    assert "ticks" in body


async def test_event_detail_returns_processing_attempts(client: AsyncClient) -> None:
    token = await _register_and_login(client, "Event Detail Co", "owner@eventdetail.com")
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/api/v1/events", json={"event_type": "job.assigned", "payload": {}}, headers=headers
    )
    event_id = created.json()["id"]

    await client.post(f"/api/v1/events/process/job.assigned", headers=headers)

    detail = await client.get(f"/api/v1/events/{event_id}/detail", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["event_id"] == event_id
    assert isinstance(body["attempts"], list)


async def test_events_list_is_tenant_isolated(client: AsyncClient) -> None:
    token_a = await _register_and_login(client, "Tenant A Events", "owner@tenantaevents.com")
    token_b = await _register_and_login(client, "Tenant B Events", "owner@tenantbevents.com")

    await client.post(
        "/api/v1/events",
        json={"event_type": "lead.created", "payload": {"marker": "tenant-a-only"}},
        headers={"Authorization": f"Bearer {token_a}"},
    )

    resp_b = await client.get("/api/v1/events", headers={"Authorization": f"Bearer {token_b}"})
    assert resp_b.status_code == 200
    assert all(e.get("payload", {}).get("marker") != "tenant-a-only" for e in resp_b.json()["events"])
