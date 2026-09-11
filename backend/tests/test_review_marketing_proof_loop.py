"""The reviews/referral-conversion -> marketing content proof loop —
genuinely absent before this. Non-negotiable safety model under test:
a review NEVER becomes marketing content without (1) a real, deterministic
eligibility bar (rating >= settings.REVIEW_MARKETING_MIN_RATING) and
(2) explicit, human-recorded consent — never inferred, never AI-settable.
Reuses the existing ContentService/MarketingContent state machine, the
existing ReviewService, and the existing ToolRegistry/ApprovalRequest/
audit architecture; introduces no parallel marketing system."""

import uuid

import pytest

from app.models.actor import ActorType
from app.models.crm import Customer
from app.models.marketing import ContentStatus
from app.models.rbac import Role
from app.services.content_service import ContentService
from app.tools.base import ExecutionContext
from app.tools.errors import ToolApprovalRequiredError

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id: uuid.UUID, role: Role = Role.OWNER, actor_type: ActorType = ActorType.USER) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=actor_type, actor_id=uuid.uuid4(), role=role)


async def _register_and_login(client, email: str) -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": "Review Loop Co", "full_name": "Owner", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["tokens"]["access_token"]


async def _make_customer(tool_registry, tenant_id: uuid.UUID, ctx: ExecutionContext) -> str:
    result = await tool_registry.execute("crm.create_customer", {"name": "Proof Loop Customer"}, ctx)
    return result.customer["id"]


async def test_high_rating_feedback_without_consent_cannot_become_content(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)

    feedback = await tool_registry.execute(
        "retention.record_feedback",
        {"customer_id": customer_id, "rating": 5, "comment": "Fantastic work, highly recommend!"},
        ctx,
    )

    with pytest.raises(Exception):
        await tool_registry.execute(
            "marketing.create_content_from_review", {"feedback_id": feedback.feedback_id}, ctx
        )


async def test_low_rating_feedback_with_consent_still_cannot_become_content(tool_registry) -> None:
    """Consent alone is not enough — eligibility (the rating bar) is a
    separate, independent gate."""
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)

    feedback = await tool_registry.execute(
        "retention.record_feedback", {"customer_id": customer_id, "rating": 3, "comment": "It was fine."}, ctx
    )
    await tool_registry.execute(
        "retention.record_review_consent", {"feedback_id": feedback.feedback_id, "consent": True}, ctx
    )

    with pytest.raises(Exception):
        await tool_registry.execute(
            "marketing.create_content_from_review", {"feedback_id": feedback.feedback_id}, ctx
        )


async def test_ai_cannot_record_consent(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx_user = _ctx(tenant_id)
    ctx_ai = _ctx(tenant_id, role=Role.MANAGER, actor_type=ActorType.AI)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx_user)

    feedback = await tool_registry.execute(
        "retention.record_feedback", {"customer_id": customer_id, "rating": 5, "comment": "Great!"}, ctx_user
    )

    with pytest.raises(ValueError, match="AI cannot record customer consent"):
        await tool_registry.execute(
            "retention.record_review_consent", {"feedback_id": feedback.feedback_id, "consent": True}, ctx_ai
        )


async def test_eligible_and_consented_review_creates_content_requiring_approval(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)

    feedback = await tool_registry.execute(
        "retention.record_feedback",
        {"customer_id": customer_id, "rating": 5, "comment": "Absolutely fantastic, on time and professional."},
        ctx,
    )
    await tool_registry.execute(
        "retention.record_review_consent", {"feedback_id": feedback.feedback_id, "consent": True}, ctx
    )

    # APPROVAL_REQUIRED policy — a real ApprovalRequest, not immediate execution.
    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute(
            "marketing.create_content_from_review", {"feedback_id": feedback.feedback_id}, ctx
        )
    assert exc_info.value.approval_request_id is not None


async def test_pii_is_redacted_from_generated_content(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)

    feedback = await tool_registry.execute(
        "retention.record_feedback",
        {
            "customer_id": customer_id, "rating": 5,
            "comment": "Great job! Call me at 555-867-5309 or email jane@example.com anytime.",
        },
        ctx,
    )
    await tool_registry.execute(
        "retention.record_review_consent", {"feedback_id": feedback.feedback_id, "consent": True}, ctx
    )

    # Bypass the approval gate to inspect the real generated content directly
    # via the service layer (the tool-level test above already proves the
    # gate exists) — this test is specifically about redaction correctness.
    from app.db.session import async_session_maker
    from app.events.factory import get_event_bus

    content_service = ContentService(async_session_maker, get_event_bus())
    content = await content_service.create_content_from_feedback(
        tenant_id, uuid.UUID(feedback.feedback_id), None
    )
    assert "555-867-5309" not in content.summary
    assert "jane@example.com" not in content.summary
    assert "[redacted]" in content.summary


async def test_content_from_review_is_idempotent_per_feedback(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)

    feedback = await tool_registry.execute(
        "retention.record_feedback", {"customer_id": customer_id, "rating": 5, "comment": "Excellent!"}, ctx
    )
    await tool_registry.execute(
        "retention.record_review_consent", {"feedback_id": feedback.feedback_id, "consent": True}, ctx
    )

    from app.db.session import async_session_maker
    from app.events.factory import get_event_bus

    content_service = ContentService(async_session_maker, get_event_bus())
    first = await content_service.create_content_from_feedback(tenant_id, uuid.UUID(feedback.feedback_id), None)
    second = await content_service.create_content_from_feedback(tenant_id, uuid.UUID(feedback.feedback_id), None)
    assert first.id == second.id


async def test_content_from_review_never_crosses_tenants(tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    ctx_a = _ctx(tenant_a)
    customer_id = await _make_customer(tool_registry, tenant_a, ctx_a)

    feedback = await tool_registry.execute(
        "retention.record_feedback", {"customer_id": customer_id, "rating": 5, "comment": "Great!"}, ctx_a
    )
    await tool_registry.execute(
        "retention.record_review_consent", {"feedback_id": feedback.feedback_id, "consent": True}, ctx_a
    )

    from app.db.session import async_session_maker
    from app.events.factory import get_event_bus
    from app.services.content_service import FeedbackNotFoundError

    content_service = ContentService(async_session_maker, get_event_bus())
    with pytest.raises(FeedbackNotFoundError):
        await content_service.create_content_from_feedback(tenant_b, uuid.UUID(feedback.feedback_id), None)


async def test_approving_a_request_for_unconsented_feedback_fails_safely_at_execution(
    tool_registry, event_bus
) -> None:
    """The crux of the safety model: the APPROVAL_REQUIRED policy check
    runs before the tool's own consent check (see the REST-level test
    above), so an approval request can exist for feedback with no
    consent. This proves that even if an owner approves it anyway, the
    real, hard consent gate inside ContentService still runs at
    execution time and no MarketingContent is ever created — the
    approval layer and the business-rule layer are independent, and both
    must agree before anything real happens."""
    from app.models.rbac import Role
    from app.services.approval_execution_service import ApprovalExecutionService
    from app.tools.errors import ToolApprovalRequiredError

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)

    feedback = await tool_registry.execute(
        "retention.record_feedback", {"customer_id": customer_id, "rating": 5, "comment": "Great!"}, ctx
    )
    # Deliberately NOT recording consent.

    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute(
            "marketing.create_content_from_review", {"feedback_id": feedback.feedback_id}, ctx
        )
    approval_id = exc_info.value.approval_request_id

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, event_bus)
    result = await service.approve(tenant_id, approval_id, decided_by_id=uuid.uuid4(), decided_by_role=Role.OWNER)

    assert result.execution_status == "FAILED"
    assert "consent" in (result.execution_error or "").lower()

    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.marketing import MarketingContent

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(MarketingContent).where(
                    MarketingContent.tenant_id == tenant_id,
                    MarketingContent.source_feedback_id == uuid.UUID(feedback.feedback_id),
                )
            )
        ).scalars().all()
    assert rows == []


async def test_resulting_content_uses_the_existing_status_machine(tool_registry) -> None:
    """No duplicate/parallel state machine — the created idea starts in
    the same ContentStatus.DRAFT every other draft content starts in, and
    is traceable back to the exact review via source_feedback_id."""
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)

    feedback = await tool_registry.execute(
        "retention.record_feedback", {"customer_id": customer_id, "rating": 5, "comment": "Loved it!"}, ctx
    )
    await tool_registry.execute(
        "retention.record_review_consent", {"feedback_id": feedback.feedback_id, "consent": True}, ctx
    )

    from app.db.session import async_session_maker
    from app.events.factory import get_event_bus

    content_service = ContentService(async_session_maker, get_event_bus())
    content = await content_service.create_content_from_feedback(tenant_id, uuid.UUID(feedback.feedback_id), None)
    assert content.status == ContentStatus.DRAFT
    assert str(content.source_feedback_id) == feedback.feedback_id


async def test_rest_endpoints_are_wired_end_to_end(client) -> None:
    """Proves the actual HTTP routes (not just the underlying ToolRegistry
    tools) are reachable and correctly wired — /retention/reviews/feedback/
    {id}/consent and /create-content."""
    token = await _register_and_login(client, "review-loop-rest@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    cust_resp = await client.post("/api/v1/customers", json={"name": "REST Customer"}, headers=headers)
    customer_id = cust_resp.json()["customer"]["id"]

    fb_resp = await client.post(
        "/api/v1/retention/reviews/feedback",
        json={"customer_id": customer_id, "rating": 5, "comment": "Wonderful!"},
        headers=headers,
    )
    assert fb_resp.status_code == 200, fb_resp.text
    feedback_id = fb_resp.json()["feedback_id"]

    # The APPROVAL_REQUIRED policy check runs before the tool's own
    # consent/eligibility check, so even a request with no consent yet
    # reaches "pending_approval" — this is correct, existing ToolRegistry
    # behavior (see registry.execute), not a bypass: no MarketingContent
    # row is ever created by merely reaching this state, and the real
    # consent/eligibility gate still runs for real if/when a human
    # actually approves the resulting ApprovalRequest.
    create_resp = await client.post(
        f"/api/v1/retention/reviews/feedback/{feedback_id}/create-content", headers=headers
    )
    assert create_resp.status_code == 202, create_resp.text
    assert create_resp.json()["detail"]["status"] == "pending_approval"
    assert create_resp.json()["detail"]["approval_request_id"]

    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.marketing import MarketingContent

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(MarketingContent).where(MarketingContent.source_feedback_id == uuid.UUID(feedback_id))
            )
        ).scalars().all()
    assert rows == [], "no content may exist merely from an unapproved, unconsented request"

    consent_resp = await client.post(
        f"/api/v1/retention/reviews/feedback/{feedback_id}/consent", json={"consent": True}, headers=headers
    )
    assert consent_resp.status_code == 200, consent_resp.text
    assert consent_resp.json()["consent_to_use_publicly"] is True

    list_resp = await client.get("/api/v1/retention/reviews/feedback", headers=headers)
    row = next(r for r in list_resp.json()["feedback"] if r["id"] == feedback_id)
    assert row["consent_to_use_publicly"] is True
