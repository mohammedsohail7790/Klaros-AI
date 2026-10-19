"""Google Business Profile sync: not_connected, idempotent upsert of listings and reviews, edits, internal reply protection, low-star events, tenant isolation, the governed
tool and the crew member. Google is faked at the client methods; the database is real."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.db.session import async_session_maker, set_tenant_context
from app.integrations import google_business_client as gbc
from app.models.actor import ActorType
from app.models.marketing import LocalListing, LocalReputationEvent, LocalReview
from app.models.rbac import Role
from app.services import operating_crew as oc
from app.services.google_business_sync_service import GoogleBusinessSyncService
from app.services.integration_connection_service import IntegrationConnectionService
from app.tools.base import ExecutionContext
from app.tools.builtin.reputation_tools import SyncGoogleReviews

NOW = datetime.now(timezone.utc)


def _iso(delta_days: int) -> str:
    return (NOW - timedelta(days=delta_days)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def conns() -> IntegrationConnectionService:
    svc = IntegrationConnectionService(async_session_maker)

    async def _ok(_cred):
        return True, "ok"

    svc.register_verifier("google_business_profile", _ok)
    return svc


@pytest.fixture
def fake_google(monkeypatch):
    state = {"refreshes": 0, "fail_reviews": None, "fail_locations": None, "reviews": [
        gbc.GbpReview("r5", 5, "Lovely", "Ann", _iso(30), _iso(30), None),
        gbc.GbpReview("r2", 2, "Slow service", "Bob", _iso(1), _iso(1), None),
        gbc.GbpReview("r1old", 1, "Old complaint", "Cy", _iso(100), _iso(100), "We are sorry"),
    ]}

    async def refresh(self, *, refresh_token):
        state["refreshes"] += 1
        return gbc.GoogleTokens("AT", None, 3600)

    async def locations(self, *, access_token, account_name):
        if state["fail_locations"]:
            raise state["fail_locations"]
        return [gbc.GbpLocation("locations/9", "Main Shop", "1 High St", "Leeds", "West Yorkshire")]

    async def reviews(self, *, access_token, account_name, location_name):
        if state["fail_reviews"]:
            raise state["fail_reviews"]
        return list(state["reviews"])

    monkeypatch.setattr(gbc.GoogleBusinessClient, "refresh_access_token", refresh)
    monkeypatch.setattr(gbc.GoogleBusinessClient, "list_locations", locations)
    monkeypatch.setattr(gbc.GoogleBusinessClient, "list_reviews", reviews)
    return state


async def _connect(conns, tenant_id):
    await conns.connect(tenant_id, "google_business_profile", {"refresh_token": "RT", "access_token": "x", "account_name": "accounts/1"}, created_by=None, external_account_id="accounts/1")


async def _rows(tenant_id):
    async with async_session_maker() as s:
        await set_tenant_context(s, tenant_id)
        listings = (await s.execute(select(LocalListing).where(LocalListing.tenant_id == tenant_id))).scalars().all()
        reviews = (await s.execute(select(LocalReview).where(LocalReview.tenant_id == tenant_id))).scalars().all()
        events = (await s.execute(select(LocalReputationEvent).where(LocalReputationEvent.tenant_id == tenant_id))).scalars().all()
    return listings, reviews, events


async def test_not_connected_is_clean(conns) -> None:
    out = await GoogleBusinessSyncService(async_session_maker, conns).sync(uuid.uuid4())
    assert out["status"] == "not_connected"


async def test_sync_creates_listing_reviews_and_is_idempotent(conns, fake_google) -> None:
    tid = uuid.uuid4()
    await _connect(conns, tid)
    svc = GoogleBusinessSyncService(async_session_maker, conns)
    first = await svc.sync(tid)
    assert first["status"] == "synced" and first["locations"] == 1 and first["listings_created"] == 1 and first["reviews_created"] == 3
    assert first["low_rating_events"] == 1  # r2 is new and recent; r1old is a stale backlog item imported silently
    listings, reviews, events = await _rows(tid)
    assert len(listings) == 1 and listings[0].provider == "google_business_profile" and listings[0].external_id == "accounts/1/locations/9" and listings[0].city == "Leeds"
    assert sorted(r.rating for r in reviews) == [1, 2, 5] and all(r.source == "google_business_profile" for r in reviews)
    old = next(r for r in reviews if r.rating == 1)
    assert old.responded and old.response_text == "We are sorry"
    assert len(events) == 1 and events[0].event_type == "NEW_LOW_RATING_REVIEW" and "2-star" in events[0].description

    second = await svc.sync(tid)
    assert second["status"] == "synced" and second["listings_created"] == 0 and second["reviews_created"] == 0 and second["reviews_updated"] == 0
    assert second["reviews_unchanged"] == 3 and second["low_rating_events"] == 0
    listings, reviews, events = await _rows(tid)
    assert len(listings) == 1 and len(reviews) == 3 and len(events) == 1
    assert fake_google["refreshes"] == 2


async def test_edited_review_updates_and_internal_reply_is_never_overwritten(conns, fake_google) -> None:
    tid = uuid.uuid4()
    await _connect(conns, tid)
    svc = GoogleBusinessSyncService(async_session_maker, conns)
    await svc.sync(tid)
    # The owner drafts/records a reply internally on the 2-star review.
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        row = (await s.execute(select(LocalReview).where(LocalReview.tenant_id == tid, LocalReview.rating == 2))).scalar_one()
        row.response_text, row.responded = "Our internal draft", False
        await s.commit()
    # The customer edits it on Google (stars up, new text) and Google now shows a different reply.
    fake_google["reviews"][1] = gbc.GbpReview("r2", 4, "Better after a call", "Bob", _iso(1), _iso(0), "Google-side reply")
    out = await svc.sync(tid)
    assert out["reviews_updated"] == 1 and out["reviews_created"] == 0 and out["low_rating_events"] == 0
    _, reviews, events = await _rows(tid)
    row = next(r for r in reviews if r.author == "Bob")
    assert row.rating == 4 and row.body == "Better after a call"
    assert row.response_text == "Our internal draft" and row.responded is False
    assert len(reviews) == 3 and len(events) == 1


async def test_three_star_alerts_four_star_does_not(conns, fake_google) -> None:
    tid = uuid.uuid4()
    await _connect(conns, tid)
    fake_google["reviews"] = [gbc.GbpReview("a", 3, "Meh", "A", _iso(0), _iso(0), None), gbc.GbpReview("b", 4, "Fine", "B", _iso(0), _iso(0), None)]
    out = await GoogleBusinessSyncService(async_session_maker, conns).sync(tid)
    assert out["reviews_created"] == 2 and out["low_rating_events"] == 1


async def test_tenant_isolation(conns, fake_google) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    await _connect(conns, a)
    svc = GoogleBusinessSyncService(async_session_maker, conns)
    await svc.sync(a)
    assert (await svc.sync(b))["status"] == "not_connected"
    await _connect(conns, b)
    out_b = await svc.sync(b)
    assert out_b["reviews_created"] == 3 and out_b["listings_created"] == 1  # same Google ids in another tenant never collide with tenant a's rows
    for t in (a, b):
        listings, reviews, _ = await _rows(t)
        assert len(listings) == 1 and len(reviews) == 3 and all(x.tenant_id == t for x in reviews)


async def test_api_errors_are_reported_honestly(conns, fake_google) -> None:
    tid = uuid.uuid4()
    await _connect(conns, tid)
    svc = GoogleBusinessSyncService(async_session_maker, conns)
    fake_google["fail_locations"] = gbc.GoogleBusinessAPIError(gbc.NOT_APPROVED_MESSAGE, status_code=403, error_type=gbc.GoogleBusinessErrorType.ACCESS_NOT_APPROVED)
    out = await svc.sync(tid)
    assert out["status"] == "error" and "not approved" in out["detail"]
    fake_google["fail_locations"] = None
    fake_google["fail_reviews"] = gbc.GoogleBusinessAPIError("Google Business Profile: boom", status_code=500)
    out = await svc.sync(tid)
    assert out["status"] == "error" and "boom" in out["detail"] and out["reviews_created"] == 0
    listings, reviews, _ = await _rows(tid)
    assert len(listings) == 1 and reviews == []
    await conns.disconnect(tid, "google_business_profile")
    assert (await svc.sync(tid))["status"] == "not_connected"


async def test_tool_is_governed_auto_and_returns_not_connected(conns) -> None:
    from app.api.tool_deps import get_tool_registry
    from app.tools.policy import ActionPolicy, policy_for

    assert get_tool_registry().get("reputation.sync_google_reviews").name == "reputation.sync_google_reviews"
    assert policy_for("reputation.sync_google_reviews") == ActionPolicy.AUTO
    ctx = ExecutionContext(tenant_id=uuid.uuid4(), actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)
    out = await SyncGoogleReviews(GoogleBusinessSyncService(async_session_maker, conns)).execute(SyncGoogleReviews.input_schema(), ctx)
    assert out.status == "not_connected" and out.reviews_created == 0


async def test_tool_output_carries_counts_and_no_secret(conns, fake_google) -> None:
    tid = uuid.uuid4()
    await _connect(conns, tid)
    ctx = ExecutionContext(tenant_id=tid, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)
    out = await SyncGoogleReviews(GoogleBusinessSyncService(async_session_maker, conns)).execute(SyncGoogleReviews.input_schema(), ctx)
    assert out.status == "synced" and out.reviews_created == 3 and out.low_rating_events == 1
    assert "RT" not in out.model_dump_json().replace("reviews", "")


def test_crew_member_runs_the_tool_daily_without_a_model() -> None:
    m = next(m for m in oc.crew_for([]) if m.key == "review-sync")
    t = oc._triggers(m)["schedule"]
    assert t["time"] == "06:45" and t["frequency"] == "DAILY"
    assert t["action"] == {"tool_name": "reputation.sync_google_reviews", "tool_input": {}}
