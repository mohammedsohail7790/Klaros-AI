"""Phase 12E: the one place `AIInvocationLog` rows are written — mirrors
`AuditLog`'s "one write path" convention. Deliberately does NOT compute
`estimated_cost_usd` from a hardcoded price table: neither the OpenAI nor
Anthropic API returns a real-time price, and embedding a static
per-token-price table here would silently go stale as providers change
pricing, producing a number that LOOKS real but isn't verified against
anything current. `estimated_cost_usd` stays NULL — real usage (token
counts) is recorded, cost is honestly reported as not computed, never
fabricated.
"""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.error_monitoring import capture_message
from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.ai_invocation import AIInvocationLog
from app.services.ai_provider import AICallOutcome


async def record_ai_invocation(
    session_factory: async_sessionmaker,
    *,
    tenant_id: uuid.UUID,
    actor_type: ActorType,
    actor_id: uuid.UUID | None,
    operation: str,
    outcome: AICallOutcome,
    correlation_id: uuid.UUID | None = None,
    input_metadata: dict | None = None,
    output_metadata: dict | None = None,
) -> AIInvocationLog:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        row = AIInvocationLog(
            tenant_id=tenant_id,
            actor_type=actor_type.value if hasattr(actor_type, "value") else str(actor_type),
            actor_id=actor_id,
            provider=outcome.provider,
            model=outcome.model,
            operation=operation,
            correlation_id=correlation_id,
            success=outcome.success,
            error_type=outcome.error_type.value if outcome.error_type else None,
            latency_ms=outcome.latency_ms,
            retry_count=outcome.retry_count,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            estimated_cost_usd=None,
            input_metadata=input_metadata,
            output_metadata=output_metadata,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

        # Production observability: this is the ONE write path every AI
        # call in the codebase goes through (Morning Brief, AI Next
        # Action, Qualification, Marketing, Voice) — a real provider
        # failure reported here covers all of them without touching each
        # individual service. Never the raw prompt/response (this table
        # has no such columns at all — see Phase 31), only the same
        # safe, already-audited fields already persisted to the row
        # itself.
        if not outcome.success:
            capture_message(
                f"AI call failed: {operation}", level="warning", component="ai",
                context={
                    "tenant_id": str(tenant_id), "operation": operation, "provider": outcome.provider,
                    "model": outcome.model, "error_type": row.error_type,
                    "correlation_id": str(correlation_id) if correlation_id else None,
                },
            )

        return row
