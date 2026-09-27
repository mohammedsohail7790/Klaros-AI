"""Phase 13 (Business Orchestration Foundation): API-level tests for
/business-journey — start/get/resume, the full Discovery -> Blueprint ->
Recommendations happy path, human-confirmation checkpoints, illegal
transitions, idempotent duplicate calls, tenant isolation, and RBAC.
Mirrors tests/test_business_discovery_blueprint_api.py's `_register` /
`_tech_token` helper pattern exactly.
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


async def _drive_journey_to_recommendations_ready(client, headers) -> dict:
    """Shared happy-path walk: start -> complete-discovery -> confirm-blueprint
    -> generate-recommendations, filling the minimum-bar sections by hand
    exactly like test_business_discovery_blueprint_api.py's flow does, via
    the journey endpoints instead of the raw Blueprint endpoints where a
    journey action exists."""
    start = await client.post(
        "/api/v1/business-journey",
        json={"business_idea": "We connect international patients with partner hospitals, earning a referral fee."},
        headers=headers,
    )
    assert start.status_code == 201, start.text
    journey = start.json()
    assert journey["status"] == "DISCOVERY_ACTIVE"
    journey_id = journey["id"]

    # complete-discovery requires the DiscoverySession itself to be COMPLETED.
    # Force it there the same way the Blueprint API tests do: confirm every
    # proposed claim then manually fill the remaining minimum-bar sections,
    # then submit answers until the session's own completion logic fires.
    draft = (await client.get("/api/v1/business-blueprint/draft", headers=headers)).json()
    blueprint_id = draft["blueprint"]["id"]
    for claim in [c for c in draft["claims"] if c["status"] == "PROPOSED"]:
        r = await client.post(f"/api/v1/business-blueprint/claims/{claim['id']}/confirm", headers=headers)
        assert r.status_code == 200, r.text
    for section_key in ("IDENTITY", "INDUSTRY", "BUSINESS_MODEL", "REQUIRED_CAPABILITIES"):
        r = await client.put(
            f"/api/v1/business-blueprint/sections/{section_key}",
            json={"blueprint_id": blueprint_id, "data": {"capabilities": ["scheduling"]} if section_key == "REQUIRED_CAPABILITIES" else {"filled": True}},
            headers=headers,
        )
        assert r.status_code == 200, r.text

    # Drive the DiscoverySession itself to COMPLETED via its own API (never
    # duplicated by the journey layer) by answering until the question cap
    # or gap-closure completes it.
    session_resp = await client.get(f"/api/v1/business-discovery/sessions/{journey['discovery_session_id']}", headers=headers)
    for _ in range(10):
        session_state = (await client.get(f"/api/v1/business-discovery/sessions/{journey['discovery_session_id']}", headers=headers)).json()
        if session_state["session"]["status"] == "COMPLETED":
            break
        ans = await client.post(
            f"/api/v1/business-discovery/sessions/{journey['discovery_session_id']}/answer",
            json={"answer": "More detail to satisfy discovery."},
            headers=headers,
        )
        assert ans.status_code == 200, ans.text
        if ans.json()["session_status"] == "COMPLETED":
            break

    complete_disc = await client.post(f"/api/v1/business-journey/{journey_id}/complete-discovery", headers=headers)
    assert complete_disc.status_code == 200, complete_disc.text
    journey = complete_disc.json()
    assert journey["status"] == "BLUEPRINT_REVIEW"

    confirm_bp = await client.post(f"/api/v1/business-journey/{journey_id}/confirm-blueprint", headers=headers)
    assert confirm_bp.status_code == 200, confirm_bp.text
    journey = confirm_bp.json()
    assert journey["status"] == "BLUEPRINT_ACTIVE"

    gen = await client.post(f"/api/v1/business-journey/{journey_id}/generate-recommendations", headers=headers)
    assert gen.status_code == 200, gen.text
    journey = gen.json()
    assert journey["status"] == "RECOMMENDATIONS_READY"
    assert journey["recommendation_run_id"] is not None
    return journey


async def test_journey_requires_authentication(client) -> None:
    resp = await client.post("/api/v1/business-journey", json={"business_idea": "x"})
    assert resp.status_code in (401, 403)


async def test_technician_cannot_manage_journey(client) -> None:
    owner_token = await _register(client, "Journey Blocked Co", "owner@journeyblockedco.com")
    tech_token = await _tech_token(client, owner_token, "tech@journeyblockedco.com")
    resp = await client.post(
        "/api/v1/business-journey", json={"business_idea": "x"}, headers={"Authorization": f"Bearer {tech_token}"}
    )
    assert resp.status_code == 403
    # STAFF-tier READ is available to owner regardless; a TECHNICIAN has no
    # READ_BUSINESS_JOURNEY either per app/models/rbac.py.
    resp = await client.get("/api/v1/business-journey", headers={"Authorization": f"Bearer {tech_token}"})
    assert resp.status_code == 403


async def test_start_journey_creates_discovery_session(client) -> None:
    owner_token = await _register(client, "Start Co", "owner@startco.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    resp = await client.post("/api/v1/business-journey", json={"business_idea": "A dropshipping storefront."}, headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "DISCOVERY_ACTIVE"
    assert body["discovery_session_id"] is not None
    # blueprint_id is attached to the JOURNEY only once Discovery reports
    # COMPLETED (checkpoint 1) — before that, the DRAFT blueprint exists
    # (Discovery's own get_or_create_draft) but is deliberately not yet
    # promoted onto the journey record.
    assert body["blueprint_id"] is None
    assert body["recommendation_run_id"] is None


async def test_duplicate_start_returns_existing_active_journey(client) -> None:
    owner_token = await _register(client, "Dup Start Co", "owner@dupstartco.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    first = await client.post("/api/v1/business-journey", json={"business_idea": "Idea A"}, headers=headers)
    second = await client.post("/api/v1/business-journey", json={"business_idea": "Idea B (ignored)"}, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


async def test_get_current_journey_and_history(client) -> None:
    owner_token = await _register(client, "Get Current Co", "owner@getcurrentco.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    started = await client.post("/api/v1/business-journey", json={"business_idea": "Idea"}, headers=headers)
    journey_id = started.json()["id"]

    current = await client.get("/api/v1/business-journey", headers=headers)
    assert current.status_code == 200
    assert current.json()["id"] == journey_id

    specific = await client.get(f"/api/v1/business-journey/{journey_id}", headers=headers)
    assert specific.status_code == 200
    assert specific.json()["id"] == journey_id

    history = await client.get("/api/v1/business-journey/history", headers=headers)
    assert history.status_code == 200
    assert len(history.json()) == 1


async def test_no_active_journey_returns_404(client) -> None:
    owner_token = await _register(client, "No Journey Co", "owner@nojourneyco.com")
    resp = await client.get("/api/v1/business-journey", headers={"Authorization": f"Bearer {owner_token}"})
    assert resp.status_code == 404


async def test_complete_discovery_before_completed_returns_409(client) -> None:
    owner_token = await _register(client, "Too Early Journey Co", "owner@tooearlyjourneyco.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    started = await client.post("/api/v1/business-journey", json={"business_idea": "x"}, headers=headers)
    journey_id = started.json()["id"]
    resp = await client.post(f"/api/v1/business-journey/{journey_id}/complete-discovery", headers=headers)
    assert resp.status_code == 409


async def test_confirm_blueprint_before_discovery_complete_returns_409(client) -> None:
    owner_token = await _register(client, "Skip Ahead Co", "owner@skipaheadco.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    started = await client.post("/api/v1/business-journey", json={"business_idea": "x"}, headers=headers)
    journey_id = started.json()["id"]
    resp = await client.post(f"/api/v1/business-journey/{journey_id}/confirm-blueprint", headers=headers)
    assert resp.status_code == 409


async def test_generate_recommendations_before_blueprint_active_returns_409(client) -> None:
    owner_token = await _register(client, "Skip Ahead Two Co", "owner@skipaheadtwoco.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    started = await client.post("/api/v1/business-journey", json={"business_idea": "x"}, headers=headers)
    journey_id = started.json()["id"]
    resp = await client.post(f"/api/v1/business-journey/{journey_id}/generate-recommendations", headers=headers)
    assert resp.status_code == 409


async def test_full_journey_happy_path_and_completion(client) -> None:
    owner_token = await _register(client, "Full Journey Co", "owner@fulljourneyco.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    journey = await _drive_journey_to_recommendations_ready(client, headers)
    journey_id = journey["id"]

    recs = await client.get("/api/v1/recommendations", headers=headers)
    assert recs.status_code == 200
    # Recommendations remain proposals only — the journey never
    # auto-accepts/executes them, regardless of how many were generated.
    for r in recs.json():
        assert r["status"] == "PROPOSED"

    run_resp = await client.get(f"/api/v1/recommendations/runs/{journey['recommendation_run_id']}", headers=headers)
    assert run_resp.status_code == 200

    complete = await client.post(f"/api/v1/business-journey/{journey_id}/complete", headers=headers)
    assert complete.status_code == 200, complete.text
    assert complete.json()["status"] == "COMPLETED"
    assert complete.json()["completed_at"] is not None

    # A completed journey no longer counts as "current" — starting again is allowed.
    current = await client.get("/api/v1/business-journey", headers=headers)
    assert current.status_code == 404

    restart = await client.post("/api/v1/business-journey", json={"business_idea": "Second business"}, headers=headers)
    assert restart.status_code == 201
    assert restart.json()["id"] != journey_id


async def test_idempotent_repeat_calls_do_not_duplicate_artifacts(client) -> None:
    owner_token = await _register(client, "Idempotent Co", "owner@idempotentco.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    journey = await _drive_journey_to_recommendations_ready(client, headers)
    journey_id = journey["id"]
    run_id = journey["recommendation_run_id"]

    # Repeat every forward action — none should error or change the linked resource.
    again_disc = await client.post(f"/api/v1/business-journey/{journey_id}/complete-discovery", headers=headers)
    assert again_disc.status_code == 200
    assert again_disc.json()["status"] == "RECOMMENDATIONS_READY"  # already past this checkpoint, no-op

    again_bp = await client.post(f"/api/v1/business-journey/{journey_id}/confirm-blueprint", headers=headers)
    assert again_bp.status_code == 200
    assert again_bp.json()["status"] == "RECOMMENDATIONS_READY"

    again_gen = await client.post(f"/api/v1/business-journey/{journey_id}/generate-recommendations", headers=headers)
    assert again_gen.status_code == 200
    assert again_gen.json()["recommendation_run_id"] == run_id  # reused, not regenerated

    recs = await client.get("/api/v1/recommendations", headers=headers)
    run_ids = {r["run_id"] for r in recs.json()}
    assert run_ids <= {run_id}  # no second run's rows ever appeared, whether or not any rows exist


async def test_abandon_journey(client) -> None:
    owner_token = await _register(client, "Abandon Co", "owner@abandonco.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    started = await client.post("/api/v1/business-journey", json={"business_idea": "x"}, headers=headers)
    journey_id = started.json()["id"]

    resp = await client.post(f"/api/v1/business-journey/{journey_id}/abandon", json={"reason": "changed my mind"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ABANDONED"
    assert resp.json()["abandoned_at"] is not None

    # Idempotent repeat.
    again = await client.post(f"/api/v1/business-journey/{journey_id}/abandon", headers=headers)
    assert again.status_code == 200
    assert again.json()["status"] == "ABANDONED"

    # No further transitions.
    blocked = await client.post(f"/api/v1/business-journey/{journey_id}/complete-discovery", headers=headers)
    assert blocked.status_code == 409

    # A new journey can now start.
    restart = await client.post("/api/v1/business-journey", json={"business_idea": "Retry"}, headers=headers)
    assert restart.status_code == 201
    assert restart.json()["id"] != journey_id


async def test_cannot_abandon_completed_journey(client) -> None:
    owner_token = await _register(client, "Abandon Completed Co", "owner@abandoncompletedco.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    journey = await _drive_journey_to_recommendations_ready(client, headers)
    journey_id = journey["id"]
    complete = await client.post(f"/api/v1/business-journey/{journey_id}/complete", headers=headers)
    assert complete.status_code == 200

    resp = await client.post(f"/api/v1/business-journey/{journey_id}/abandon", headers=headers)
    assert resp.status_code == 409


async def test_tenant_cannot_read_or_advance_another_tenants_journey(client) -> None:
    owner_a = await _register(client, "Journey Tenant A Co", "owner@journeytenantaco.com")
    owner_b = await _register(client, "Journey Tenant B Co", "owner@journeytenantbco.com")

    started = await client.post(
        "/api/v1/business-journey", json={"business_idea": "Tenant A's private idea"},
        headers={"Authorization": f"Bearer {owner_a}"},
    )
    journey_id = started.json()["id"]

    cross_read = await client.get(
        f"/api/v1/business-journey/{journey_id}", headers={"Authorization": f"Bearer {owner_b}"}
    )
    assert cross_read.status_code == 404

    cross_advance = await client.post(
        f"/api/v1/business-journey/{journey_id}/complete-discovery", headers={"Authorization": f"Bearer {owner_b}"}
    )
    assert cross_advance.status_code == 404

    cross_abandon = await client.post(
        f"/api/v1/business-journey/{journey_id}/abandon", headers={"Authorization": f"Bearer {owner_b}"}
    )
    assert cross_abandon.status_code == 404

    # Tenant B's own "current journey" view must never see tenant A's journey.
    b_current = await client.get("/api/v1/business-journey", headers={"Authorization": f"Bearer {owner_b}"})
    assert b_current.status_code == 404


async def test_unknown_journey_returns_404(client) -> None:
    owner_token = await _register(client, "Unknown Journey Co", "owner@unknownjourneyco.com")
    resp = await client.get(
        "/api/v1/business-journey/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp.status_code == 404
