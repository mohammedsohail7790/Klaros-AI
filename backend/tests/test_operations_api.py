import base64

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, org: str, email: str) -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org, "full_name": "Owner", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["tokens"]["access_token"]


async def test_job_lifecycle_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Ops HTTP Co", "owner@opshttp.com")
    headers = {"Authorization": f"Bearer {token}"}

    customer = await client.post("/api/v1/customers", json={"name": "HTTP Customer"}, headers=headers)
    customer_id = customer.json()["customer"]["id"]

    created = await client.post(
        "/api/v1/jobs", json={"title": "HTTP Job", "customer_id": customer_id}, headers=headers
    )
    assert created.status_code == 201
    job_id = created.json()["job"]["id"]

    fetched = await client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    assert fetched.status_code == 200

    scheduled = await client.post(
        f"/api/v1/jobs/{job_id}/schedule",
        json={"start_time": "2026-12-01T09:00:00+00:00", "end_time": "2026-12-01T10:00:00+00:00"},
        headers=headers,
    )
    assert scheduled.status_code == 200
    assert scheduled.json()["job"]["status"] == "SCHEDULED"

    worker = await client.post("/api/v1/workers", json={"name": "Tech"}, headers=headers)
    worker_id = worker.json()["worker"]["id"]

    assigned = await client.post(f"/api/v1/jobs/{job_id}/assign", json={"worker_id": worker_id}, headers=headers)
    assert assigned.status_code == 200

    dispatched = await client.post(f"/api/v1/jobs/{job_id}/dispatch", headers=headers)
    assert dispatched.json()["job"]["status"] == "DISPATCHED"

    invalid = await client.post(f"/api/v1/jobs/{job_id}/close", headers=headers)
    assert invalid.status_code in (404, 422)  # not COMPLETED yet — cannot close


async def test_job_document_upload_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Upload HTTP Co", "owner@uploadhttp.com")
    headers = {"Authorization": f"Bearer {token}"}

    customer = await client.post("/api/v1/customers", json={"name": "C"}, headers=headers)
    job = await client.post(
        "/api/v1/jobs", json={"title": "J", "customer_id": customer.json()["customer"]["id"]}, headers=headers
    )
    job_id = job.json()["job"]["id"]

    files = {"file": ("photo.jpg", b"fake jpeg bytes", "image/jpeg")}
    resp = await client.post(f"/api/v1/jobs/{job_id}/photos", files=files, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["attachment"]["kind"] == "PHOTO"


async def test_exceptions_listing_over_http(client: AsyncClient) -> None:
    token = await _register(client, "Exceptions HTTP Co", "owner@exceptionshttp.com")
    headers = {"Authorization": f"Bearer {token}"}

    customer = await client.post("/api/v1/customers", json={"name": "C"}, headers=headers)
    job = await client.post(
        "/api/v1/jobs", json={"title": "J", "customer_id": customer.json()["customer"]["id"]}, headers=headers
    )
    job_id = job.json()["job"]["id"]
    await client.post(
        f"/api/v1/jobs/{job_id}/schedule",
        json={"start_time": "2026-12-02T09:00:00+00:00", "end_time": "2026-12-02T10:00:00+00:00"},
        headers=headers,
    )

    detected = await client.post("/api/v1/exceptions/detect", headers=headers)
    assert detected.status_code == 200
    assert detected.json()["JOB_UNASSIGNED"] == 1

    listing = await client.get("/api/v1/exceptions", headers=headers)
    assert listing.status_code == 200
    assert len(listing.json()["exceptions"]) == 1

    exception_id = listing.json()["exceptions"][0]["id"]
    resolved = await client.post(f"/api/v1/exceptions/{exception_id}/resolve", headers=headers)
    assert resolved.status_code == 200
    assert resolved.json()["exception"]["status"] == "RESOLVED"


async def test_operations_dashboard_real_zero_for_empty_tenant(client: AsyncClient) -> None:
    token = await _register(client, "Empty Ops Co", "owner@emptyops.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/operations/dashboard", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {
        "jobs_today": 0,
        "unassigned_jobs": 0,
        "at_risk_jobs": 0,
        "blocked_jobs": 0,
        "in_progress_jobs": 0,
        "qa_pending_jobs": 0,
        "completed_today": 0,
        "open_exceptions": 0,
    }


async def test_operations_dashboard_reflects_real_jobs(client: AsyncClient) -> None:
    token = await _register(client, "Real Ops Co", "owner@realops.com")
    headers = {"Authorization": f"Bearer {token}"}

    customer = await client.post("/api/v1/customers", json={"name": "C"}, headers=headers)
    await client.post(
        "/api/v1/jobs", json={"title": "J1", "customer_id": customer.json()["customer"]["id"]}, headers=headers
    )

    resp = await client.get("/api/v1/operations/dashboard", headers=headers)
    assert resp.status_code == 200
    # Not scheduled yet, so not "today"/unassigned-scheduled — but the
    # endpoint must still return real (zero) numbers, not fabricated ones.
    assert resp.json()["unassigned_jobs"] == 0


async def test_tenant_isolation_over_http_for_jobs(client: AsyncClient) -> None:
    token_a = await _register(client, "HTTP Ops Tenant A", "owner@httpopsa.com")
    token_b = await _register(client, "HTTP Ops Tenant B", "owner@httpopsb.com")

    customer_a = await client.post(
        "/api/v1/customers", json={"name": "A"}, headers={"Authorization": f"Bearer {token_a}"}
    )
    job_a = await client.post(
        "/api/v1/jobs",
        json={"title": "A job", "customer_id": customer_a.json()["customer"]["id"]},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    job_id = job_a.json()["job"]["id"]

    cross_tenant = await client.get(f"/api/v1/jobs/{job_id}", headers={"Authorization": f"Bearer {token_b}"})
    assert cross_tenant.status_code == 404
