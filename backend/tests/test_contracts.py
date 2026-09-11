"""Contracts: the sales-side agreement between quote acceptance and
payment, genuinely absent before this — `CustomerSignoff` represents
post-completion job signoff, a different business moment. Covers: real
event-driven creation on QUOTE_ACCEPTED, idempotency, the internal signing
workflow (no external e-signature provider claimed), public-token tenant
isolation (mirrors the established quote_view pattern), and staff tool
access."""

import uuid

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.contract import Contract, ContractStatus
from app.models.event import Event
from app.models.rbac import Role
from app.services.contract_service import (
    ContractNotFoundError,
    ContractService,
    InvalidContractTransitionError,
    QuoteNotFoundError as ContractQuoteNotFoundError,
)
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio

_ITEMS = [{"description": "Consulting", "quantity": "1", "unit_price": "500.00"}]


def _ctx(tenant_id: uuid.UUID, role: Role = Role.OWNER) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def _make_customer(tool_registry, ctx: ExecutionContext) -> str:
    result = await tool_registry.execute(
        "crm.create_customer", {"name": "Contract Test Customer", "email": "contract-customer@example.com"}, ctx
    )
    return result.customer["id"]


async def _create_accepted_quote(tool_registry, tenant_id: uuid.UUID, ctx: ExecutionContext) -> str:
    """No deposit configured -> accept converts straight to a Job, exactly
    the pre-Phase-15 path; QUOTE_ACCEPTED still fires either way."""
    customer_id = await _make_customer(tool_registry, ctx)
    created = await tool_registry.execute(
        "quotes.create_quote_draft", {"customer_id": customer_id, "line_items": _ITEMS}, ctx
    )
    sent = await tool_registry.execute("quotes.send_quote", {"quote_id": created.quote["id"]}, ctx)
    token = sent.view_url_path.split("token=")[1]
    return created.quote["id"], token


async def test_accepting_a_quote_automatically_creates_a_draft_contract(tool_registry, event_bus) -> None:
    """Uses `QuoteService` directly with the test's own `event_bus`
    fixture (the established pattern in this codebase, e.g.
    `test_quickbooks_deposit_payment_sync.py`) rather than the public HTTP
    endpoint — `public_quotes.py` calls the process-wide `get_event_bus()`
    singleton directly, not via a FastAPI `Depends`, so it isn't affected
    by the test harness's `dependency_overrides` for `get_wired_event_bus`;
    exercising the real subscriber wiring means using the same bus it was
    registered on."""
    from app.services.quote_service import QuoteService

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    quote_id, _token = await _create_accepted_quote(tool_registry, tenant_id, ctx)

    quote_service = QuoteService(async_session_maker, event_bus)
    await quote_service.decide(tenant_id, uuid.UUID(quote_id), accepted=True)
    from app.models.event import EventType

    await event_bus.process_pending(EventType.QUOTE_ACCEPTED)

    async with async_session_maker() as session:
        contracts = (
            await session.execute(select(Contract).where(Contract.tenant_id == tenant_id, Contract.quote_id == uuid.UUID(quote_id)))
        ).scalars().all()
        assert len(contracts) == 1
        contract = contracts[0]
        assert contract.status == ContractStatus.DRAFT
        assert contract.content  # real rendered content, not empty/placeholder
        assert len(contract.content_hash) == 64  # a real sha256 hex digest

        events = (
            await session.execute(select(Event).where(Event.tenant_id == tenant_id, Event.event_type == "contract.created"))
        ).scalars().all()
        assert len(events) == 1


async def test_contract_creation_is_idempotent(event_bus) -> None:
    tenant_id = uuid.uuid4()
    service = ContractService(async_session_maker, event_bus)

    from datetime import date
    from decimal import Decimal

    from app.models.crm import Customer
    from app.models.quote import Quote, QuoteStatus

    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Idempotency Test Customer")
        session.add(customer)
        await session.flush()
        quote = Quote(
            tenant_id=tenant_id, quote_number="QTE-IDEMP-1", customer_id=customer.id,
            status=QuoteStatus.ACCEPTED, currency="USD", subtotal=Decimal("100.00"), total=Decimal("100.00"),
            valid_until=date(2030, 1, 1),
        )
        session.add(quote)
        await session.commit()
        await session.refresh(quote)

    contract1, deduped1 = await service.create_from_quote(tenant_id, quote.id)
    assert deduped1 is False

    contract2, deduped2 = await service.create_from_quote(tenant_id, quote.id)
    assert deduped2 is True
    assert contract2.id == contract1.id

    async with async_session_maker() as session:
        contracts = (await session.execute(select(Contract).where(Contract.quote_id == quote.id))).scalars().all()
        assert len(contracts) == 1


async def test_full_send_view_sign_lifecycle(event_bus) -> None:
    tenant_id = uuid.uuid4()
    service = ContractService(async_session_maker, event_bus)

    from datetime import date
    from decimal import Decimal

    from app.models.crm import Customer
    from app.models.quote import Quote, QuoteStatus

    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Lifecycle Test Customer")
        session.add(customer)
        await session.flush()
        quote = Quote(
            tenant_id=tenant_id, quote_number="QTE-LIFE-1", customer_id=customer.id,
            status=QuoteStatus.ACCEPTED, currency="USD", subtotal=Decimal("250.00"), total=Decimal("250.00"),
            valid_until=date(2030, 1, 1),
        )
        session.add(quote)
        await session.commit()
        await session.refresh(quote)

    contract, _ = await service.create_from_quote(tenant_id, quote.id)
    assert contract.status == ContractStatus.DRAFT

    sent = await service.send(tenant_id, contract.id)
    assert sent.status == ContractStatus.SENT
    assert sent.sent_at is not None

    viewed = await service.get_for_public_view(tenant_id, contract.id)
    assert viewed.status == ContractStatus.VIEWED
    assert viewed.viewed_at is not None

    # A second view must not re-fire the CONTRACT_VIEWED event or change viewed_at.
    viewed_again = await service.get_for_public_view(tenant_id, contract.id)
    assert viewed_again.viewed_at == viewed.viewed_at

    signed = await service.sign(tenant_id, contract.id, signer_name="Jane Customer", signer_email="jane@example.com")
    assert signed.status == ContractStatus.SIGNED
    assert signed.signer_name == "Jane Customer"
    assert signed.decided_at is not None

    async with async_session_maker() as session:
        events = (
            await session.execute(select(Event).where(Event.tenant_id == tenant_id, Event.event_type == "contract.signed"))
        ).scalars().all()
        assert len(events) == 1

    # Signing again is a safe idempotent no-op, never a second signature/event.
    signed_again = await service.sign(tenant_id, contract.id, signer_name="Someone Else", signer_email=None)
    assert signed_again.signer_name == "Jane Customer"  # unchanged -- the original signature stands

    async with async_session_maker() as session:
        events_after = (
            await session.execute(select(Event).where(Event.tenant_id == tenant_id, Event.event_type == "contract.signed"))
        ).scalars().all()
        assert len(events_after) == 1


async def test_cannot_sign_a_draft_contract(event_bus) -> None:
    tenant_id = uuid.uuid4()
    service = ContractService(async_session_maker, event_bus)

    from datetime import date
    from decimal import Decimal

    from app.models.crm import Customer
    from app.models.quote import Quote, QuoteStatus

    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Draft Guard Customer")
        session.add(customer)
        await session.flush()
        quote = Quote(
            tenant_id=tenant_id, quote_number="QTE-DRAFT-1", customer_id=customer.id,
            status=QuoteStatus.ACCEPTED, currency="USD", subtotal=Decimal("100.00"), total=Decimal("100.00"),
            valid_until=date(2030, 1, 1),
        )
        session.add(quote)
        await session.commit()
        await session.refresh(quote)

    contract, _ = await service.create_from_quote(tenant_id, quote.id)
    with pytest.raises(InvalidContractTransitionError):
        await service.sign(tenant_id, contract.id, signer_name="Too Soon", signer_email=None)


async def test_public_contract_view_rejects_cross_tenant_token(client, tool_registry, event_bus) -> None:
    from app.services.quote_service import QuoteService

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    ctx_a = _ctx(tenant_a)
    quote_id, _token_a = await _create_accepted_quote(tool_registry, tenant_a, ctx_a)

    quote_service = QuoteService(async_session_maker, event_bus)
    await quote_service.decide(tenant_a, uuid.UUID(quote_id), accepted=True)
    from app.models.event import EventType

    await event_bus.process_pending(EventType.QUOTE_ACCEPTED)

    async with async_session_maker() as session:
        contract = (
            await session.execute(select(Contract).where(Contract.tenant_id == tenant_a, Contract.quote_id == uuid.UUID(quote_id)))
        ).scalar_one()

    from app.core.security import create_contract_view_token

    forged_token = create_contract_view_token(contract.id, tenant_b)  # correct contract_id, WRONG tenant
    resp = await client.get(f"/api/v1/public/contracts/{contract.id}", params={"token": forged_token})
    assert resp.status_code == 404  # tenant_b genuinely has no such contract


async def test_staff_tool_get_and_send_contract(tool_registry, event_bus) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    from datetime import date
    from decimal import Decimal

    from app.models.crm import Customer
    from app.models.quote import Quote, QuoteStatus

    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Tool Test Customer")
        session.add(customer)
        await session.flush()
        quote = Quote(
            tenant_id=tenant_id, quote_number="QTE-TOOL-1", customer_id=customer.id,
            status=QuoteStatus.ACCEPTED, currency="USD", subtotal=Decimal("100.00"), total=Decimal("100.00"),
            valid_until=date(2030, 1, 1),
        )
        session.add(quote)
        await session.commit()
        await session.refresh(quote)

    service = ContractService(async_session_maker, event_bus)
    contract, _ = await service.create_from_quote(tenant_id, quote.id)

    got = await tool_registry.execute("contracts.get_contract", {"contract_id": str(contract.id)}, ctx)
    assert got.contract["status"] == "DRAFT"

    sent = await tool_registry.execute("contracts.send_contract", {"contract_id": str(contract.id)}, ctx)
    assert sent.contract["status"] == "SENT"
    assert "token=" in sent.view_url_path


async def _register_and_login(client, email: str) -> tuple[str, uuid.UUID]:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Contract REST Test Co", "full_name": "Owner", "email": email,
            "password": "supersecret1",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return data["tokens"]["access_token"], uuid.UUID(data["user"]["tenant_id"])


async def test_staff_rest_router_list_get_send(client, event_bus) -> None:
    """The dedicated staff REST endpoints (`app/api/v1/contracts.py`) that
    the frontend actually calls — distinct from the raw ToolRegistry tools
    exercised above, mirrors `quotes.py`'s exact list/get/send pattern."""
    from datetime import date
    from decimal import Decimal

    from app.models.crm import Customer
    from app.models.quote import Quote, QuoteStatus

    token, tenant_id = await _register_and_login(client, "contract-rest@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="REST Test Customer")
        session.add(customer)
        await session.flush()
        quote = Quote(
            tenant_id=tenant_id, quote_number="QTE-REST-1", customer_id=customer.id,
            status=QuoteStatus.ACCEPTED, currency="USD", subtotal=Decimal("100.00"), total=Decimal("100.00"),
            valid_until=date(2030, 1, 1),
        )
        session.add(quote)
        await session.commit()
        await session.refresh(quote)

    service = ContractService(async_session_maker, event_bus)
    contract, _ = await service.create_from_quote(tenant_id, quote.id)

    list_resp = await client.get("/api/v1/contracts", headers=headers)
    assert list_resp.status_code == 200
    contracts = list_resp.json()["contracts"]
    assert len(contracts) == 1
    assert contracts[0]["id"] == str(contract.id)
    assert "content_hash" not in contracts[0]  # internal verification field, never exposed to staff list view

    get_resp = await client.get(f"/api/v1/contracts/{contract.id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "DRAFT"

    send_resp = await client.post(f"/api/v1/contracts/{contract.id}/send", headers=headers)
    assert send_resp.status_code == 200
    body = send_resp.json()
    assert body["contract"]["status"] == "SENT"
    assert "token=" in body["view_url_path"]


async def test_staff_rest_router_rejects_cross_tenant_contract(client, event_bus) -> None:
    from datetime import date
    from decimal import Decimal

    from app.models.crm import Customer
    from app.models.quote import Quote, QuoteStatus

    tenant_a = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_a, name="Tenant A Customer")
        session.add(customer)
        await session.flush()
        quote = Quote(
            tenant_id=tenant_a, quote_number="QTE-XTEN-1", customer_id=customer.id,
            status=QuoteStatus.ACCEPTED, currency="USD", subtotal=Decimal("100.00"), total=Decimal("100.00"),
            valid_until=date(2030, 1, 1),
        )
        session.add(quote)
        await session.commit()
        await session.refresh(quote)

    service = ContractService(async_session_maker, event_bus)
    contract, _ = await service.create_from_quote(tenant_a, quote.id)

    # A DIFFERENT, freshly-registered tenant must never see tenant_a's contract.
    token_b, _tenant_b = await _register_and_login(client, "tenant-b-contract@example.com")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    list_resp = await client.get("/api/v1/contracts", headers=headers_b)
    assert list_resp.json()["contracts"] == []

    get_resp = await client.get(f"/api/v1/contracts/{contract.id}", headers=headers_b)
    assert get_resp.status_code == 404
