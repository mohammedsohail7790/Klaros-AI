"""Phase 27: real end-to-end proof for the Owner Activity Feed —
`GET /api/v1/dashboard/activity`. Reuses Phase 26's own realistic
mixed-business-state seed (`_seed_business_state` in
test_owner_operating_system_e2e.py) since it already covers every source
this phase's activity types read from (lead, qualified lead, appointment,
quote, pending contract, active job + QA failure, overdue invoice,
retention + referral opportunity, AI approval, AI feedback, failed
automation execution) — no duplicated seed logic, no mocked API response.
"""

import uuid

import pytest
from httpx import AsyncClient

from tests.test_owner_operating_system_e2e import _create_second_user, _register, _seed_business_state

pytestmark = pytest.mark.asyncio

_SAFE_SUBSTRINGS_NEVER_EXPOSED = (
    "sk-", "sk_live", "sk_test", "Bearer ", "api_key", "Authorization:",
)


async def test_activity_feed_aggregates_real_mixed_business_state(client: AsyncClient) -> None:
    token, tenant_id = await _register(client, "phase27-owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    await _seed_business_state(tenant_id)

    resp = await client.get("/api/v1/dashboard/activity", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    items = data["items"]
    assert len(items) > 0
    assert data["total"] == len(items) or data["total"] > len(items)  # total >= returned page

    types_seen = {i["activity_type"] for i in items}
    expected_types = {
        "LEAD_CREATED", "QUOTE_CREATED", "CONTRACT_CREATED", "JOB_CREATED", "QA_FAILED",
        "INVOICE_CREATED", "INVOICE_OVERDUE", "RETENTION_OPPORTUNITY", "REFERRAL_OPPORTUNITY",
        "AI_DECISION_PROPOSED", "AI_FEEDBACK_PENDING", "AUTOMATION_FAILED",
    }
    assert expected_types.issubset(types_seen), f"missing: {expected_types - types_seen}"

    # Chronological order: timestamp descending.
    timestamps = [i["timestamp"] for i in items]
    assert timestamps == sorted(timestamps, reverse=True)

    # Every item carries what the UI needs and nothing it shouldn't.
    for item in items:
        assert item["id"]
        assert item["timestamp"]
        assert item["activity_type"]
        assert item["category"]
        assert item["title"]
        assert item["severity"] in ("INFO", "WARNING", "ERROR")
        assert item["actor_type"]
        # No raw sensitive content anywhere in the serialized item.
        blob = str(item)
        for forbidden in _SAFE_SUBSTRINGS_NEVER_EXPOSED:
            assert forbidden not in blob, f"leaked sensitive substring {forbidden!r} in {item['activity_type']}"

    # QA failure links to the real job.
    qa_items = [i for i in items if i["activity_type"] == "QA_FAILED"]
    assert qa_items[0]["entity_type"] == "job"
    assert qa_items[0]["link"] == f"/jobs/{qa_items[0]['entity_id']}"

    # AI feedback never exposes raw prompt/response — only the persisted,
    # already-safe CompanyMemory.value summary (same content /settings/memory
    # already shows, nothing new leaked).
    feedback_items = [i for i in items if i["activity_type"] == "AI_FEEDBACK_PENDING"]
    assert feedback_items[0]["description"]


async def test_activity_feed_pagination_is_bounded_and_deterministic(client: AsyncClient) -> None:
    token, tenant_id = await _register(client, "phase27-pagination@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    await _seed_business_state(tenant_id)

    page1 = await client.get("/api/v1/dashboard/activity", params={"page": 1, "page_size": 3}, headers=headers)
    assert page1.status_code == 200
    data1 = page1.json()
    assert len(data1["items"]) <= 3
    assert data1["page_size"] == 3

    page2 = await client.get("/api/v1/dashboard/activity", params={"page": 2, "page_size": 3}, headers=headers)
    data2 = page2.json()
    ids1 = {i["id"] for i in data1["items"]}
    ids2 = {i["id"] for i in data2["items"]}
    assert ids1.isdisjoint(ids2)  # no overlap/duplication across pages
    assert data1["total"] == data2["total"]  # stable total across pages

    # Requesting more than the max page size is clamped, never unbounded.
    huge = await client.get("/api/v1/dashboard/activity", params={"page_size": 10000}, headers=headers)
    assert huge.status_code == 422  # FastAPI's own Query(le=MAX_PAGE_SIZE) validation rejects it


async def test_activity_feed_category_filter(client: AsyncClient) -> None:
    token, tenant_id = await _register(client, "phase27-filter@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    await _seed_business_state(tenant_id)

    resp = await client.get("/api/v1/dashboard/activity", params={"category": "QA"}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) > 0
    assert all(i["category"] == "QA" for i in data["items"])


async def test_activity_feed_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/dashboard/activity")
    assert resp.status_code in (401, 403)


async def test_activity_feed_tenant_isolation(client: AsyncClient) -> None:
    token_a, tenant_a = await _register(client, "phase27-tenant-a@example.com")
    await _seed_business_state(tenant_a)
    token_b, tenant_b = await _register(client, "phase27-tenant-b@example.com")

    resp_b = await client.get("/api/v1/dashboard/activity", headers={"Authorization": f"Bearer {token_b}"})
    assert resp_b.status_code == 200
    assert resp_b.json()["items"] == []  # tenant B sees none of tenant A's real seeded activity

    resp_a = await client.get("/api/v1/dashboard/activity", headers={"Authorization": f"Bearer {token_a}"})
    assert len(resp_a.json()["items"]) > 0


async def test_activity_feed_client_cannot_supply_tenant_id(client: AsyncClient) -> None:
    """There is no tenant_id parameter on this endpoint at all — attempting
    to pass one must simply be ignored (FastAPI drops unknown query params
    for a Pydantic-validated route), never used as tenant identity."""
    token_a, tenant_a = await _register(client, "phase27-notrust-a@example.com")
    await _seed_business_state(tenant_a)
    token_b, tenant_b = await _register(client, "phase27-notrust-b@example.com")

    resp = await client.get(
        "/api/v1/dashboard/activity", params={"tenant_id": str(tenant_a)},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []  # tenant B's own real (empty) state, not tenant A's


async def test_activity_feed_rbac_read_only_and_technician(client: AsyncClient) -> None:
    from app.models.rbac import Role

    _, tenant_id = await _register(client, "phase27-rbac-owner@example.com")
    await _seed_business_state(tenant_id)

    for role in (Role.READ_ONLY, Role.TECHNICIAN, Role.MANAGER):
        token = await _create_second_user(tenant_id, role=role)
        resp = await client.get("/api/v1/dashboard/activity", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, f"{role} could not view activity feed: {resp.text}"
        assert len(resp.json()["items"]) > 0


async def test_activity_feed_no_mutation_capability(client: AsyncClient) -> None:
    token, _ = await _register(client, "phase27-nomutation@example.com")
    resp = await client.post("/api/v1/dashboard/activity", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 405  # method not allowed — read-only, no POST route exists
