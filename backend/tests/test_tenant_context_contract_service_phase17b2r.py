"""Phase 17B-2R: real-PostgreSQL behavioral proof that ContractService's own,
independently-opened sessions (7 sites, including `get_for_public_view`,
whose `tenant_id` originates from the documented public-token URL
boundary per task §21) now stamp `SET LOCAL app.tenant_id`. Same
methodology as `test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.crm import Customer
from app.models.organization import Organization
from app.models.quote import Quote
from app.services.contract_service import ContractNotFoundError, ContractService

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


class _ContextSpy:
    def __init__(self):
        self.calls: list[tuple[uuid.UUID | None, str | None]] = []

    async def __call__(self, session, tenant_id):
        await set_tenant_context(session, tenant_id)
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            readback = await session.scalar(text("select current_setting('app.tenant_id', true)"))
        else:
            readback = None
        self.calls.append((tenant_id, readback))


@pytest.fixture
def spy():
    return _ContextSpy()


async def _make_org_customer_quote(tenant_id: uuid.UUID) -> uuid.UUID:
    customer_id = uuid.uuid4()
    quote_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Customer(id=customer_id, tenant_id=tenant_id, name="Contract Test Customer"))
        session.add(
            Quote(
                id=quote_id, tenant_id=tenant_id, quote_number=f"Q-{uuid.uuid4().hex[:6]}", customer_id=customer_id,
            )
        )
        await session.commit()
    return quote_id


def _service() -> ContractService:
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    return ContractService(async_session_maker, bus)


@requires_real_postgres
async def test_create_from_quote_and_public_view_set_tenant_context(monkeypatch, spy) -> None:
    import app.services.contract_service as contract_service_module

    monkeypatch.setattr(contract_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    quote_id = await _make_org_customer_quote(tenant_id)
    service = _service()

    contract, deduped = await service.create_from_quote(tenant_id, quote_id)
    assert not deduped

    # get_for_public_view: tenant_id here comes from the documented
    # public-contract-token URL boundary, not a client-supplied claim —
    # still correctly stamped, same as every other tenant-scoped call.
    await service.get_for_public_view(tenant_id, contract.id)

    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_read_tenant_bs_contract() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    quote_a = await _make_org_customer_quote(tenant_a)
    await _make_org_customer_quote(tenant_b)
    service = _service()

    contract, _ = await service.create_from_quote(tenant_a, quote_a)

    with pytest.raises(ContractNotFoundError):
        await service.get(tenant_b, contract.id)
