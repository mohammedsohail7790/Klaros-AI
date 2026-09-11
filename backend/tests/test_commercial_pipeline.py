"""The commercial pipeline (Quote -> Contract -> Deposit) surfaced in the
Owner Cockpit: `GET /finance/commercial-pipeline` (real aggregate query,
distinct from `/finance/summary`'s Invoice/AR-only numbers) and the
Morning Brief's AI next-action generation for contracts awaiting signature
and deposits awaiting payment (reusing the existing
`MorningBriefRecommendation` architecture — no new next-action system)."""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.contract import Contract, ContractStatus
from app.models.crm import Customer
from app.models.quote import Quote, QuoteStatus
from app.models.rbac import Role
from app.services.contract_service import ContractService
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id: uuid.UUID, role: Role = Role.OWNER) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def _make_quote_and_customer(
    tenant_id: uuid.UUID, *, status: str, deposit_amount: Decimal | None = None, total: Decimal = Decimal("100.00"),
    sent_at: datetime | None = None,
) -> tuple[Customer, Quote]:
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Pipeline Test Customer")
        session.add(customer)
        await session.flush()
        quote = Quote(
            tenant_id=tenant_id, quote_number=f"QTE-{uuid.uuid4().hex[:8]}", customer_id=customer.id,
            status=status, currency="USD", subtotal=total, total=total,
            valid_until=date(2030, 1, 1), deposit_amount=deposit_amount, sent_at=sent_at,
        )
        session.add(quote)
        await session.commit()
        await session.refresh(customer)
        await session.refresh(quote)
    return customer, quote


async def _register_and_login(client, email: str) -> tuple[str, uuid.UUID]:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Pipeline Test Co", "full_name": "Owner", "email": email,
            "password": "supersecret1",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return data["tokens"]["access_token"], uuid.UUID(data["user"]["tenant_id"])


async def test_commercial_pipeline_endpoint_reflects_real_data(client) -> None:
    token, tenant_id = await _register_and_login(client, "pipeline-owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp0 = await client.get("/api/v1/finance/commercial-pipeline", headers=headers)
    assert resp0.status_code == 200
    body0 = resp0.json()
    assert body0["contracts_awaiting_signature"] == 0
    assert body0["deposits_awaiting_payment"] == 0
    assert body0["needs_attention"] is False

    _customer, quote = await _make_quote_and_customer(
        tenant_id, status=QuoteStatus.DEPOSIT_PENDING, deposit_amount=Decimal("40.00")
    )
    async with async_session_maker() as session:
        contract = Contract(
            tenant_id=tenant_id, contract_number="CTR-TEST-1", quote_id=quote.id, customer_id=quote.customer_id,
            status=ContractStatus.SENT, content="test content", content_hash="0" * 64,
        )
        session.add(contract)
        await session.commit()

    resp1 = await client.get("/api/v1/finance/commercial-pipeline", headers=headers)
    body1 = resp1.json()
    assert body1["contracts_awaiting_signature"] == 1
    assert body1["deposits_awaiting_payment"] == 1
    assert body1["deposits_awaiting_value"] == "40.00"
    assert body1["needs_attention"] is True


async def test_ai_recommends_following_up_on_unsigned_contract(tool_registry, event_bus) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    _customer, quote = await _make_quote_and_customer(tenant_id, status=QuoteStatus.ACCEPTED)
    contract_service = ContractService(async_session_maker, event_bus)
    contract, _ = await contract_service.create_from_quote(tenant_id, quote.id)
    await contract_service.send(tenant_id, contract.id)

    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)

    contract_recs = [r for r in latest.recommendations if r.related_entity_id == str(contract.id)]
    assert contract_recs
    assert "signature" in contract_recs[0].next_action.lower() or "reminder" in contract_recs[0].next_action.lower()
    assert contract_recs[0].related_entity_type == "contract"


async def test_ai_recommends_following_up_on_unpaid_deposit(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    _customer, quote = await _make_quote_and_customer(
        tenant_id, status=QuoteStatus.DEPOSIT_PENDING, deposit_amount=Decimal("75.00")
    )

    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)

    quote_recs = [r for r in latest.recommendations if r.related_entity_id == str(quote.id)]
    assert quote_recs
    assert quote_recs[0].related_entity_type == "quote"


async def test_commercial_pipeline_snapshot_never_crosses_tenants(tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    ctx_b = _ctx(tenant_b)

    await _make_quote_and_customer(tenant_a, status=QuoteStatus.DEPOSIT_PENDING, deposit_amount=Decimal("50.00"))

    result = await tool_registry.execute("insights.get_commercial_pipeline_snapshot", {}, ctx_b)
    assert result.deposits_awaiting_payment == []
    assert result.contracts_awaiting_signature == []


async def test_stale_quote_appears_after_threshold_but_not_before(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    threshold_days = get_settings().STALE_QUOTE_FOLLOWUP_DAYS
    now = datetime.now(timezone.utc)

    _fresh_customer, fresh_quote = await _make_quote_and_customer(
        tenant_id, status=QuoteStatus.SENT, sent_at=now - timedelta(days=threshold_days - 1),
    )
    _stale_customer, stale_quote = await _make_quote_and_customer(
        tenant_id, status=QuoteStatus.VIEWED, sent_at=now - timedelta(days=threshold_days + 1),
    )
    # Not sent at all yet: must never appear regardless of age.
    await _make_quote_and_customer(tenant_id, status=QuoteStatus.DRAFT, sent_at=None)
    # Decided (accepted): must never appear even though it was sent long ago.
    await _make_quote_and_customer(
        tenant_id, status=QuoteStatus.ACCEPTED, sent_at=now - timedelta(days=threshold_days + 5),
    )

    result = await tool_registry.execute("insights.get_commercial_pipeline_snapshot", {}, ctx)
    stale_ids = {q["quote_id"] for q in result.stale_quotes_awaiting_response}

    assert str(stale_quote.id) in stale_ids
    assert str(fresh_quote.id) not in stale_ids
    assert len(result.stale_quotes_awaiting_response) == 1


async def test_stale_quote_snapshot_never_crosses_tenants(tool_registry) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    ctx_b = _ctx(tenant_b)
    now = datetime.now(timezone.utc)

    await _make_quote_and_customer(
        tenant_a, status=QuoteStatus.SENT, sent_at=now - timedelta(days=get_settings().STALE_QUOTE_FOLLOWUP_DAYS + 5)
    )

    result = await tool_registry.execute("insights.get_commercial_pipeline_snapshot", {}, ctx_b)
    assert result.stale_quotes_awaiting_response == []


async def test_ai_recommends_following_up_on_stale_quote_informationally(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    now = datetime.now(timezone.utc)

    _customer, quote = await _make_quote_and_customer(
        tenant_id, status=QuoteStatus.SENT, sent_at=now - timedelta(days=get_settings().STALE_QUOTE_FOLLOWUP_DAYS + 1)
    )

    await tool_registry.execute("insights.generate_morning_brief", {}, ctx)
    latest = await tool_registry.execute("insights.get_latest_morning_brief", {}, ctx)

    quote_recs = [r for r in latest.recommendations if r.related_entity_id == str(quote.id)]
    assert quote_recs
    assert quote_recs[0].related_entity_type == "quote"
    assert quote_recs[0].executable is False
    assert "follow up" in quote_recs[0].next_action.lower()
