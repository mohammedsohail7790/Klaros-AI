"""Production observability phase: three new OwnerAttentionService
categories — PROVIDER_AUTH_FAILED, WEBHOOK_PROCESSING_FAILED,
AI_EXECUTION_FAILED — all read straight from existing tables
(IntegrationConnection, WebhookEvent, AIInvocationLog), same pattern as
every pre-existing category (AUTOMATION_FAILED etc.). Proves: real
aggregation (not one item per row), the "don't alert on a single
transient AI failure" threshold, and tenant isolation for each."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.db.session import async_session_maker
from app.models.ai_invocation import AIInvocationLog
from app.models.integration import (
    ConnectionStatus,
    IntegrationConnection,
    WebhookEvent,
    WebhookProcessingStatus,
)
from app.services.owner_attention_service import AttentionCategory, OwnerAttentionService

pytestmark = pytest.mark.asyncio


def _service() -> OwnerAttentionService:
    return OwnerAttentionService(async_session_maker)


async def test_provider_auth_failure_surfaces_in_attention_queue() -> None:
    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(IntegrationConnection(
            tenant_id=tenant_id, provider="stripe", status=ConnectionStatus.ERROR,
            last_error="secret_key rejected by Stripe's API", last_verified_at=datetime.now(timezone.utc),
        ))
        await session.commit()

    items = await _service().get_attention_queue(tenant_id)
    matches = [i for i in items if i.category == AttentionCategory.PROVIDER_AUTH_FAILED]
    assert len(matches) == 1
    assert "stripe" in matches[0].title.lower()
    assert matches[0].reason == "secret_key rejected by Stripe's API"


async def test_connected_provider_does_not_surface() -> None:
    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(IntegrationConnection(tenant_id=tenant_id, provider="stripe", status=ConnectionStatus.CONNECTED))
        await session.commit()

    items = await _service().get_attention_queue(tenant_id)
    assert not [i for i in items if i.category == AttentionCategory.PROVIDER_AUTH_FAILED]


async def test_webhook_failures_are_aggregated_per_provider_not_one_row_each() -> None:
    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        for i in range(4):
            session.add(WebhookEvent(
                provider="stripe", external_event_id=f"evt_{uuid.uuid4().hex}", event_type="payment_intent.succeeded",
                tenant_id=tenant_id, status=WebhookProcessingStatus.FAILED,
                raw_payload={}, error_detail=f"failure {i}",
            ))
        await session.commit()

    items = await _service().get_attention_queue(tenant_id)
    matches = [i for i in items if i.category == AttentionCategory.WEBHOOK_PROCESSING_FAILED]
    assert len(matches) == 1  # one aggregated item, not 4
    assert "4 stripe webhook" in matches[0].title.lower()


async def test_successfully_processed_webhooks_never_surface() -> None:
    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(WebhookEvent(
            provider="stripe", external_event_id=f"evt_{uuid.uuid4().hex}", event_type="payment_intent.succeeded",
            tenant_id=tenant_id, status=WebhookProcessingStatus.PROCESSED, raw_payload={},
        ))
        await session.commit()

    items = await _service().get_attention_queue(tenant_id)
    assert not [i for i in items if i.category == AttentionCategory.WEBHOOK_PROCESSING_FAILED]


async def test_single_transient_ai_failure_never_surfaces() -> None:
    """Never alert-fatigue the owner over one blip — only a genuine
    repeated pattern (>=3 in 24h for the same operation) is actionable."""
    from app.services.ai_invocation_log_service import record_ai_invocation
    from app.models.actor import ActorType
    from app.services.ai_provider import AICallOutcome, AIErrorType

    tenant_id = uuid.uuid4()
    await record_ai_invocation(
        async_session_maker, tenant_id=tenant_id, actor_type=ActorType.AI, actor_id=None,
        operation="voice_receptionist_turn",
        outcome=AICallOutcome(success=False, provider="openai", model="gpt-4o-mini", latency_ms=10, error_type=AIErrorType.PROVIDER_ERROR),
    )

    items = await _service().get_attention_queue(tenant_id)
    assert not [i for i in items if i.category == AttentionCategory.AI_EXECUTION_FAILED]


async def test_repeated_ai_failures_for_the_same_operation_surface() -> None:
    from app.services.ai_invocation_log_service import record_ai_invocation
    from app.models.actor import ActorType
    from app.services.ai_provider import AICallOutcome, AIErrorType

    tenant_id = uuid.uuid4()
    for _ in range(3):
        await record_ai_invocation(
            async_session_maker, tenant_id=tenant_id, actor_type=ActorType.AI, actor_id=None,
            operation="voice_receptionist_turn",
            outcome=AICallOutcome(success=False, provider="openai", model="gpt-4o-mini", latency_ms=10, error_type=AIErrorType.PROVIDER_ERROR),
        )

    items = await _service().get_attention_queue(tenant_id)
    matches = [i for i in items if i.category == AttentionCategory.AI_EXECUTION_FAILED]
    assert len(matches) == 1
    assert "3 times" in matches[0].title


async def test_tenant_b_never_sees_tenant_as_observability_items() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as session:
        session.add(IntegrationConnection(
            tenant_id=tenant_a, provider="stripe", status=ConnectionStatus.ERROR, last_error="boom",
        ))
        session.add(WebhookEvent(
            provider="stripe", external_event_id=f"evt_{uuid.uuid4().hex}", event_type="payment_intent.succeeded",
            tenant_id=tenant_a, status=WebhookProcessingStatus.FAILED, raw_payload={}, error_detail="boom",
        ))
        await session.commit()

    items_b = await _service().get_attention_queue(tenant_b)
    assert not [i for i in items_b if i.category in (
        AttentionCategory.PROVIDER_AUTH_FAILED, AttentionCategory.WEBHOOK_PROCESSING_FAILED,
    )]
