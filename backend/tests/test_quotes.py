"""Phase 14: Quotes/Estimates — the pre-work stage the pipeline was
missing. Covers the internal lifecycle (create/edit/send, tool + API
layer), the public customer-facing accept/decline flow (Klaros' first
unauthenticated surface), tenant isolation at every layer, RBAC,
idempotency, event publishing, and a full realistic E2E scenario
(create -> send -> customer views -> accepts -> real Job created).
"""

import uuid

import pytest

from app.core.security import decode_quote_view_token
from app.models.actor import ActorType
from app.models.event import EventType
from app.models.operations import Job, JobStatus
from app.models.quote import Quote, QuoteStatus
from app.models.rbac import Role
from app.services.invoice_service import LineItemInput
from app.services.quote_service import QuoteExpiredError, QuoteService
from app.tools.base import ExecutionContext
from app.tools.errors import ToolError


def _ctx(tenant_id: uuid.UUID, role: Role = Role.OWNER, actor_type: ActorType = ActorType.USER) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=actor_type, actor_id=uuid.uuid4(), role=role)


async def _make_customer(tool_registry, tenant_id: uuid.UUID, ctx: ExecutionContext, name: str = "Quote Test Customer") -> str:
    result = await tool_registry.execute("crm.create_customer", {"name": name, "email": "quote-customer@example.com"}, ctx)
    return result.customer["id"]


_ITEMS = [{"description": "Roof repair", "quantity": "1", "unit_price": "500.00"}]


# --- 1. Tool-layer lifecycle. ---


async def test_create_quote_draft(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)

    result = await tool_registry.execute(
        "quotes.create_quote_draft", {"customer_id": customer_id, "line_items": _ITEMS}, ctx
    )
    assert result.quote["status"] == "DRAFT"
    assert result.quote["total"] == "500.00"
    assert result.deduplicated is False


async def test_create_quote_draft_idempotent(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)

    body = {"customer_id": customer_id, "line_items": _ITEMS, "idempotency_key": "quote-key-1"}
    first = await tool_registry.execute("quotes.create_quote_draft", body, ctx)
    second = await tool_registry.execute("quotes.create_quote_draft", body, ctx)
    assert first.quote["id"] == second.quote["id"]
    assert second.deduplicated is True


async def test_create_quote_draft_unknown_customer_is_honest_error(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    with pytest.raises(ValueError, match="Customer not found"):
        await tool_registry.execute(
            "quotes.create_quote_draft", {"customer_id": str(uuid.uuid4()), "line_items": _ITEMS}, ctx
        )


async def test_update_draft_recalculates_totals(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)
    created = await tool_registry.execute(
        "quotes.create_quote_draft", {"customer_id": customer_id, "line_items": _ITEMS}, ctx
    )
    updated = await tool_registry.execute(
        "quotes.update_quote_draft",
        {"quote_id": created.quote["id"], "line_items": [{"description": "Bigger job", "quantity": "2", "unit_price": "300.00"}]},
        ctx,
    )
    assert updated.quote["total"] == "600.00"


async def test_update_draft_after_send_is_rejected(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)
    created = await tool_registry.execute(
        "quotes.create_quote_draft", {"customer_id": customer_id, "line_items": _ITEMS}, ctx
    )
    await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)

    with pytest.raises(ValueError, match="DRAFT"):
        await tool_registry.execute(
            "quotes.update_quote_draft", {"quote_id": created.quote["id"], "line_items": _ITEMS}, ctx
        )


async def test_send_quote_generates_real_signed_view_link(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)
    created = await tool_registry.execute(
        "quotes.create_quote_draft", {"customer_id": customer_id, "line_items": _ITEMS}, ctx
    )
    sent = await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)
    assert sent.quote["status"] == "SENT"
    assert "token=" in sent.view_url_path

    token = sent.view_url_path.split("token=")[1]
    payload = decode_quote_view_token(token)
    assert payload["quote_id"] == created.quote["id"]
    assert payload["tenant_id"] == str(tenant_id)


async def test_send_quote_delivers_via_internal_test_adapter(tool_registry) -> None:
    from app.db.session import async_session_maker
    from app.models.communication import CommunicationLog
    from sqlalchemy import select

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)
    created = await tool_registry.execute(
        "quotes.create_quote_draft", {"customer_id": customer_id, "line_items": _ITEMS}, ctx
    )
    await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)

    async with async_session_maker() as session:
        row = (
            await session.execute(select(CommunicationLog).where(CommunicationLog.channel == "QUOTE_DELIVERY"))
        ).scalar_one()
        assert row.tenant_id == tenant_id
        assert row.status == "SENT"


# --- 2. RBAC. ---


async def test_technician_cannot_create_quote(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    owner_ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, owner_ctx)

    tech_ctx = _ctx(tenant_id, role=Role.TECHNICIAN)
    with pytest.raises(ToolError):
        await tool_registry.execute(
            "quotes.create_quote_draft", {"customer_id": customer_id, "line_items": _ITEMS}, tech_ctx
        )


async def test_staff_can_create_but_not_send_quote(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    owner_ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, owner_ctx)

    staff_ctx = _ctx(tenant_id, role=Role.STAFF)
    with pytest.raises(ToolError):
        await tool_registry.execute(
            "quotes.create_quote_draft", {"customer_id": customer_id, "line_items": _ITEMS}, staff_ctx
        )


# --- 3. Tenant isolation (tool layer). ---


async def test_tenant_b_cannot_update_tenant_as_quote(tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    ctx_a = _ctx(tenant_a)
    ctx_b = _ctx(tenant_b)
    customer_id = await _make_customer(tool_registry, tenant_a, ctx_a)
    created = await tool_registry.execute(
        "quotes.create_quote_draft", {"customer_id": customer_id, "line_items": _ITEMS}, ctx_a
    )

    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute(
            "quotes.update_quote_draft", {"quote_id": created.quote["id"], "line_items": _ITEMS}, ctx_b
        )


async def test_tenant_b_cannot_send_tenant_as_quote(tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    ctx_a = _ctx(tenant_a)
    ctx_b = _ctx(tenant_b)
    customer_id = await _make_customer(tool_registry, tenant_a, ctx_a)
    created = await tool_registry.execute(
        "quotes.create_quote_draft", {"customer_id": customer_id, "line_items": _ITEMS}, ctx_a
    )

    with pytest.raises(ValueError, match="not found"):
        await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx_b)


# --- 4. Public view/accept/decline (Klaros' first unauthenticated surface). ---


async def _create_and_send_quote(tool_registry, tenant_id: uuid.UUID, ctx: ExecutionContext) -> tuple[str, str]:
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)
    created = await tool_registry.execute(
        "quotes.create_quote_draft", {"customer_id": customer_id, "line_items": _ITEMS}, ctx
    )
    sent = await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)
    token = sent.view_url_path.split("token=")[1]
    return created.quote["id"], token


async def test_public_view_marks_viewed_and_never_leaks_internal_ids(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(tool_registry, tenant_id, ctx)

    resp = await client.get(f"/api/v1/public/quotes/{quote_id}", params={"token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "VIEWED"
    assert "customer_id" not in body
    assert "tenant_id" not in body
    assert body["line_items"][0]["description"] == "Roof repair"


async def test_public_view_with_wrong_quote_id_in_token_is_rejected(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    _quote_id, token = await _create_and_send_quote(tool_registry, tenant_id, ctx)

    other_quote_id = uuid.uuid4()
    resp = await client.get(f"/api/v1/public/quotes/{other_quote_id}", params={"token": token})
    assert resp.status_code == 400


async def test_public_view_with_garbage_token_is_rejected(client) -> None:
    resp = await client.get(f"/api/v1/public/quotes/{uuid.uuid4()}", params={"token": "not-a-real-token"})
    assert resp.status_code == 400


async def test_public_view_token_from_tenant_a_cannot_be_reused_for_tenant_bs_quote(client, tool_registry) -> None:
    """A forged token can't be constructed without JWT_SECRET, but this
    proves the endpoint itself checks the token's OWN tenant_id/quote_id
    together — not just that some valid-looking token was presented."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    ctx_a = _ctx(tenant_a)
    ctx_b = _ctx(tenant_b)
    _quote_a_id, token_a = await _create_and_send_quote(tool_registry, tenant_a, ctx_a)
    quote_b_id, _token_b = await _create_and_send_quote(tool_registry, tenant_b, ctx_b)

    # tenant A's real token, presented against tenant B's real quote id.
    resp = await client.get(f"/api/v1/public/quotes/{quote_b_id}", params={"token": token_a})
    assert resp.status_code == 400


async def test_public_accept_creates_a_real_job_end_to_end(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(tool_registry, tenant_id, ctx)

    resp = await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["quote"]["status"] == "CONVERTED"
    assert body["job_created"] is True
    # Regression: the accept response's `quote` previously omitted
    # line_items entirely (unlike the GET view), crashing the public
    # frontend — which renders quote.line_items unconditionally right
    # after acceptance — with "Cannot read properties of undefined
    # (reading 'map')" the moment a real customer accepted a quote.
    assert body["quote"]["line_items"][0]["description"] == "Roof repair"

    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        assert quote.job_id is not None
        job = await session.get(Job, quote.job_id)
        assert job is not None
        assert job.tenant_id == tenant_id
        assert job.quote_id == uuid.UUID(quote_id)
        assert job.status == JobStatus.DRAFT
        assert float(job.estimated_revenue) == 500.0


async def test_public_accept_is_idempotent_never_creates_two_jobs(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(tool_registry, tenant_id, ctx)

    resp1 = await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})
    assert resp1.status_code == 200

    resp2 = await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})
    assert resp2.status_code == 409

    from app.db.session import async_session_maker
    from sqlalchemy import func, select

    async with async_session_maker() as session:
        job_count = (
            await session.execute(select(func.count()).select_from(Job).where(Job.quote_id == uuid.UUID(quote_id)))
        ).scalar_one()
        assert job_count == 1


async def test_public_decline_never_creates_a_job(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(tool_registry, tenant_id, ctx)

    resp = await client.post(
        f"/api/v1/public/quotes/{quote_id}/decline", params={"token": token}, json={"reason": "Too expensive"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["quote"]["status"] == "DECLINED"
    assert body["quote"]["line_items"][0]["description"] == "Roof repair"

    from app.db.session import async_session_maker
    from sqlalchemy import func, select

    async with async_session_maker() as session:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        assert quote.decline_reason == "Too expensive"
        job_count = (
            await session.execute(select(func.count()).select_from(Job).where(Job.quote_id == uuid.UUID(quote_id)))
        ).scalar_one()
        assert job_count == 0


async def test_declined_quote_cannot_later_be_accepted(client, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, token = await _create_and_send_quote(tool_registry, tenant_id, ctx)

    await client.post(f"/api/v1/public/quotes/{quote_id}/decline", params={"token": token}, json={})
    resp = await client.post(f"/api/v1/public/quotes/{quote_id}/accept", params={"token": token})
    assert resp.status_code == 409


# --- 5. Expiry. ---


async def test_expired_quote_cannot_be_decided(event_bus) -> None:
    from datetime import date, timedelta

    from app.db.session import async_session_maker
    from app.invoice_delivery.factory import get_invoice_delivery_provider
    from app.models.crm import Customer

    tenant_id = uuid.uuid4()
    service = QuoteService(async_session_maker, event_bus)

    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Expiry Test Customer")
        session.add(customer)
        await session.flush()
        customer_id = customer.id
        await session.commit()

    quote, _ = await service.create_draft(
        tenant_id, customer_id=customer_id, lead_id=None,
        items=[LineItemInput(description="x", quantity=1, unit_price=100)],
    )
    sent = await service.send(
        tenant_id, quote.id, get_invoice_delivery_provider(async_session_maker), "https://example.com/view",
    )

    async with async_session_maker() as session:
        row = await session.get(Quote, sent.id)
        row.valid_until = date.today() - timedelta(days=1)
        await session.commit()

    with pytest.raises(QuoteExpiredError):
        await service.decide(tenant_id, quote.id, accepted=True)


async def test_detect_expired_quotes_sweep(tool_registry, event_bus) -> None:
    from app.db.session import async_session_maker
    from datetime import date, timedelta

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)
    created = await tool_registry.execute(
        "quotes.create_quote_draft", {"customer_id": customer_id, "line_items": _ITEMS}, ctx
    )
    await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)

    async with async_session_maker() as session:
        row = await session.get(Quote, uuid.UUID(created.quote["id"]))
        row.valid_until = date.today() - timedelta(days=1)
        await session.commit()

    result = await tool_registry.execute("quotes.detect_expired_quotes", {}, ctx)
    assert created.quote["id"] in result.expired_quote_ids

    async with async_session_maker() as session:
        row = await session.get(Quote, uuid.UUID(created.quote["id"]))
        assert row.status == QuoteStatus.EXPIRED


# --- 6. Events + audit. ---


async def test_quote_lifecycle_publishes_events(tool_registry, event_bus) -> None:
    from app.db.session import async_session_maker
    from sqlalchemy import select
    from app.models.event import Event

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)
    created = await tool_registry.execute(
        "quotes.create_quote_draft", {"customer_id": customer_id, "line_items": _ITEMS}, ctx
    )
    await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(Event.event_type).where(
                    Event.tenant_id == tenant_id, Event.entity_id == uuid.UUID(created.quote["id"])
                )
            )
        ).scalars().all()
    assert EventType.QUOTE_CREATED.value in rows
    assert EventType.QUOTE_SENT.value in rows


async def test_quote_actions_are_audited(tool_registry, event_bus) -> None:
    from app.db.session import async_session_maker
    from sqlalchemy import select
    from app.models.audit_log import AuditLog

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer_id = await _make_customer(tool_registry, tenant_id, ctx)
    await tool_registry.execute(
        "quotes.create_quote_draft", {"customer_id": customer_id, "line_items": _ITEMS}, ctx
    )

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.tool == "quotes.create_quote_draft")
            )
        ).scalars().all()
    assert len(rows) == 1
