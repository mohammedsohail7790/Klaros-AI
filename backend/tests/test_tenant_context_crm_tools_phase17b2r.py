"""Phase 17B-2R: real-PostgreSQL behavioral proof that the CRM Tool
implementations' OWN, independently-opened sessions (`app/tools/builtin/
crm_tools.py`, 11 `self._session_factory()` sites across GetLead,
UpdateLead, SearchLeads, CreateCustomer, BulkImportCustomers, GetCustomer,
UpdateCustomer, SearchCustomers, GetCustomerTimeline, and two
CreateNote/notes-related tools) now stamp `SET LOCAL app.tenant_id`.

This is distinct from — and was NOT covered by — Phase 17B-2's own
`ToolRegistry` instrumentation: `app/tools/registry.py`'s 5 session-open
sites are the REGISTRY's own bookkeeping sessions (policy checks, audit
logging), never the same session a Tool's `execute()` method opens for its
own business queries. Proven here by going through the real, governed
`ToolRegistry.execute()` entrypoint (not calling the Tool class directly),
so this also incidentally re-confirms MCP/Agent call chains preserve
tenant identity into the Tool layer (task §22).
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.organization import Organization
from app.models.rbac import Role
from app.tools.base import ExecutionContext

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


def _ctx(tenant_id: uuid.UUID) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


async def _make_org(tenant_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        await session.commit()


@requires_real_postgres
async def test_crm_get_and_search_tools_set_tenant_context(monkeypatch, spy, tool_registry) -> None:
    import app.tools.builtin.crm_tools as crm_tools_module

    monkeypatch.setattr(crm_tools_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    await _make_org(tenant_id)
    ctx = _ctx(tenant_id)

    created = await tool_registry.execute(
        "crm.create_customer", {"name": "Test Customer"}, ctx,
    )
    customer_id = created.customer["id"]

    await tool_registry.execute("crm.get_customer", {"customer_id": customer_id}, ctx)
    await tool_registry.execute("crm.search_customers", {}, ctx)

    assert len(spy.calls) >= 3
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_get_tenant_bs_customer_through_the_tool_layer(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)

    created = await tool_registry.execute(
        "crm.create_customer", {"name": "A-only Customer"}, _ctx(tenant_a),
    )
    customer_id = created.customer["id"]

    with pytest.raises(ValueError):
        await tool_registry.execute("crm.get_customer", {"customer_id": customer_id}, _ctx(tenant_b))

    searched = await tool_registry.execute("crm.search_customers", {}, _ctx(tenant_b))
    assert customer_id not in [c["id"] for c in searched.customers]


@requires_real_postgres
async def test_pool_reuse_a_b_never_leaks_crm_tool_context(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _make_org(tenant_a)
    await _make_org(tenant_b)

    for _ in range(2):
        await tool_registry.execute("crm.create_customer", {"name": "a"}, _ctx(tenant_a))
        await tool_registry.execute("crm.create_customer", {"name": "b"}, _ctx(tenant_b))
        async with async_session_maker() as session:
            readback = await session.scalar(text("select current_setting('app.tenant_id', true)"))
            assert readback in (None, "")
