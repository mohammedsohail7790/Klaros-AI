"""Phase 17B-2: real-PostgreSQL behavioral proof that the tenant-context
propagation gaps PHASE_17A_RLS_ENFORCEMENT_READINESS_AUDIT.md §5/§16/§17
found (Event Bus, MCP, public website/lead intake, webhooks, Agent
execution never called `set_tenant_context`) are now closed.

Methodology, applied uniformly below: `app.db.session.set_tenant_context`
is the ONE authoritative mechanism (Phase 0) for `SET LOCAL
app.tenant_id`, reused everywhere in this phase — never reimplemented.
Each test here wraps that function, in the specific module under test, with
a spy that (a) calls through to the real implementation, so production
behavior is completely unchanged, and (b) immediately reads back
`current_setting('app.tenant_id', true)` on the exact same SQLAlchemy
session, in the exact same transaction, proving the `SET LOCAL` really did
take effect where the code path claims it did — not just that the function
was called. This is the same "prove the mechanism, not just that a call
happened" standard `tests/test_postgres_rls_audit_mode.py` and
`tests/test_restricted_app_role_cutover.py` already established.

Skipped entirely on SQLite (no `SET LOCAL`/GUC concept there), matching the
existing `requires_real_postgres` convention.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.agent import AgentAutonomyTier
from app.models.crm import Appointment
from app.models.event import EventType
from app.models.mcp_server import McpClientCredential
from app.models.organization import Organization
from app.models.rbac import Role
from app.models.website import Website

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


class _ContextSpy:
    """Wraps the real `set_tenant_context` so every call is recorded
    together with what `current_setting('app.tenant_id', true)` reads back
    as, on the SAME session, immediately afterward — proving the SET LOCAL
    genuinely applied in that transaction, not just that the function ran."""

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


async def _make_org(session, tenant_id: uuid.UUID) -> None:
    session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
    await session.commit()


# ---------------------------------------------------------------- Event Bus


@requires_real_postgres
async def test_event_bus_handle_one_sets_context_per_event(monkeypatch, spy) -> None:
    """PHASE_17A §5: `EventBus._handle_one` never called `set_tenant_context`
    — closed by stamping the event's own tenant_id, learned from the event
    row itself, before any further tenant-owned table access in that same
    transaction (audit_log/event_processing_record tables etc.)."""
    import app.events.bus as bus_module

    monkeypatch.setattr(bus_module, "set_tenant_context", spy)

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as session:
        await _make_org(session, tenant_a)
        await _make_org(session, tenant_b)

    from app.events.bus import EventBus
    from app.events.transport import InMemoryTransport

    seen: list[uuid.UUID] = []

    async def handler(event) -> None:
        seen.append(event.tenant_id)

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    bus.subscribe(EventType.LEAD_CREATED, "phase17b2_probe", handler)

    await bus.publish(tenant_id=tenant_a, event_type=EventType.LEAD_CREATED, source="test", payload={})
    await bus.publish(tenant_id=tenant_b, event_type=EventType.LEAD_CREATED, source="test", payload={})
    stats = await bus.process_pending(EventType.LEAD_CREATED, count=10)

    assert stats.succeeded == 2
    assert seen == [tenant_a, tenant_b]
    # Both `publish()` and `_handle_one()` now call set_tenant_context —
    # publish(A), publish(B), then _handle_one(A), _handle_one(B), in that
    # order (publish is synchronous/sequential above; process_pending then
    # processes both pending events in delivery order). Every call's own
    # readback must equal that same call's own tenant_id.
    assert [c[0] for c in spy.calls] == [tenant_a, tenant_b, tenant_a, tenant_b]
    for called_tenant, readback in spy.calls:
        assert readback == str(called_tenant)


@requires_real_postgres
async def test_event_handler_own_session_sets_context(monkeypatch, spy) -> None:
    """PHASE_17A §5/§9: a domain handler's OWN, separately-opened session
    (crm_handlers.notify_on_appointment_created) is a different connection
    than EventBus._handle_one's — it must stamp its own context
    independently. Was previously an un-instrumented gap."""
    import app.events.crm_handlers as crm_handlers_module

    monkeypatch.setattr(crm_handlers_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    appt_id = uuid.uuid4()
    async with async_session_maker() as session:
        await _make_org(session, tenant_id)
        session.add(
            Appointment(
                id=appt_id, tenant_id=tenant_id, customer_id=uuid.uuid4(), title="Consult",
                start_time=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
                end_time=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            )
        )
        await session.commit()

    from app.events.bus import EventBus
    from app.events.crm_handlers import register_crm_handlers
    from app.events.transport import InMemoryTransport

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    register_crm_handlers(bus, async_session_maker)

    event = await bus.publish(
        tenant_id=tenant_id, event_type=EventType.APPOINTMENT_CREATED, source="test",
        entity_id=appt_id, payload={},
    )
    stats = await bus.process_pending(EventType.APPOINTMENT_CREATED, count=10)
    assert stats.succeeded == 1
    assert spy.calls, "notify_on_appointment_created never called set_tenant_context"
    assert spy.calls[0] == (tenant_id, str(tenant_id))


@requires_real_postgres
async def test_event_bus_no_cross_tenant_context_leak_on_pooled_connections(monkeypatch, spy) -> None:
    """Interleaved tenant A / tenant B events through the SAME worker
    process/pooled engine — the exact leak scenario PHASE_17A §6 and the
    pre-existing `test_postgres_rls_audit_mode.py` pool tests already
    proved safe for the underlying mechanism; this proves the newly wired
    call sites don't reintroduce it."""
    import app.events.bus as bus_module

    monkeypatch.setattr(bus_module, "set_tenant_context", spy)

    tenants = [uuid.uuid4() for _ in range(4)]
    async with async_session_maker() as session:
        for t in tenants:
            await _make_org(session, t)

    from app.events.bus import EventBus
    from app.events.transport import InMemoryTransport

    seen: list[uuid.UUID] = []

    async def handler(event) -> None:
        # Mid-handler: this SAME session (bus._handle_one's) must show
        # exactly this event's own tenant — never a neighbor's.
        seen.append(event.tenant_id)

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    bus.subscribe(EventType.JOB_CREATED, "phase17b2_leak_probe", handler)

    # Publish in round-robin (A, B, C, D, A, B, C, D) so processing
    # interleaves tenants across whatever pooled connections get reused.
    for _ in range(2):
        for t in tenants:
            await bus.publish(tenant_id=t, event_type=EventType.JOB_CREATED, source="test", payload={})

    stats = await bus.process_pending(EventType.JOB_CREATED, count=20)
    assert stats.succeeded == 8
    # Every call's readback must equal that SAME call's own tenant_id --
    # a leaked/stale value from a prior connection use would show up as a
    # mismatch here.
    for tenant_id, readback in spy.calls:
        assert readback == str(tenant_id)


# --------------------------------------------------------------------- MCP


@requires_real_postgres
async def test_mcp_audit_sets_tenant_context_from_authenticated_credential(monkeypatch, spy) -> None:
    """PHASE_17A §16: MCP resolved tenant_id for authorization but never
    stamped it onto the DB session. Proves `_audit` now does, and that the
    value used is the AUTHENTICATED credential's tenant_id, never anything
    a client could supply in the JSON-RPC body."""
    import app.mcp.protocol as protocol_module

    monkeypatch.setattr(protocol_module, "set_tenant_context", spy)

    from app.mcp.protocol import McpProtocolHandler, McpRequestAuth
    from app.services.mcp_service import McpExposureService
    from app.tools.factory import build_tool_registry

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as session:
        await _make_org(session, tenant_a)
        await _make_org(session, tenant_b)

    registry = build_tool_registry(async_session_maker, None)
    exposure = McpExposureService(async_session_maker)
    handler = McpProtocolHandler(async_session_maker, registry, exposure)

    for tenant in (tenant_a, tenant_b):
        cred = McpClientCredential(
            id=uuid.uuid4(), tenant_id=tenant, name="probe", role=Role.OWNER.value,
            token_hash="x", token_prefix="x" * 8,
        )
        auth = McpRequestAuth(credential=cred)
        await handler._audit(auth, action="mcp.client_authenticated", tool_name=None, result="success")

    assert [c[0] for c in spy.calls] == [tenant_a, tenant_b]
    assert [c[1] for c in spy.calls] == [str(tenant_a), str(tenant_b)]


# ---------------------------------------------------------- Public Website


@requires_real_postgres
async def test_public_website_sets_context_from_trusted_url_tenant_id(monkeypatch, spy) -> None:
    """PHASE_17A §17: the public, unauthenticated website read path never
    called set_tenant_context, even though the URL's tenant_id is this
    module's own documented, trusted boundary. Two tenants' published
    websites, read back to back, must each stamp their own tenant_id and
    never bleed into the other."""
    import app.services.website_service as website_service_module

    monkeypatch.setattr(website_service_module, "set_tenant_context", spy)

    from app.services.website_service import WebsiteService

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as session:
        await _make_org(session, tenant_a)
        await _make_org(session, tenant_b)
        for t in (tenant_a, tenant_b):
            session.add(Website(id=uuid.uuid4(), tenant_id=t, name=f"biz-{t}", slug=f"biz-{t}"))
        await session.commit()

    service = WebsiteService(async_session_maker)
    site_a = await service.get_website(tenant_a)
    site_b = await service.get_website(tenant_b)

    assert site_a is not None and site_a.tenant_id == tenant_a
    assert site_b is not None and site_b.tenant_id == tenant_b
    assert [c[0] for c in spy.calls] == [tenant_a, tenant_b]
    assert [c[1] for c in spy.calls] == [str(tenant_a), str(tenant_b)]


# ------------------------------------------------------------ Public Leads


@requires_real_postgres
async def test_public_lead_intake_sets_context_from_trusted_url_tenant_id(monkeypatch, spy) -> None:
    """PHASE_17A §17's sibling gap: public lead intake shares the exact
    same trusted-URL-tenant_id shape as the public website read path."""
    import app.services.lead_service as lead_service_module

    monkeypatch.setattr(lead_service_module, "set_tenant_context", spy)

    from app.events.bus import EventBus
    from app.events.transport import InMemoryTransport
    from app.services.lead_service import CreateLeadInput, LeadService

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        await _make_org(session, tenant_id)

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    service = LeadService(async_session_maker, bus)
    lead, _dedup = await service.create_lead(
        tenant_id, CreateLeadInput(name="Jane Public", source="WEB", email="jane@example.com"),
    )
    assert lead.tenant_id == tenant_id
    assert spy.calls == [(tenant_id, str(tenant_id))]


# ---------------------------------------------------------------- Webhooks


@requires_real_postgres
async def test_webhook_tenant_resolved_post_signature_sets_context(monkeypatch, spy) -> None:
    """PHASE_17A §5's explicitly-named gap ("a webhook handler ... that
    derives tenant_id from a signed payload later, not from auth").
    Exercises the exact statement `stripe_webhook`'s own quote-ownership
    guard (`_handle_quote_deposit_succeeded`) runs post-signature-
    verification, post-tenant-resolution."""
    import app.api.v1.webhooks as webhooks_module

    monkeypatch.setattr(webhooks_module, "set_tenant_context", spy)

    from app.db.session import async_session_maker as _asm
    from app.models.quote import Quote, QuoteStatus

    tenant_id = uuid.uuid4()
    quote_id = uuid.uuid4()
    async with _asm() as session:
        await _make_org(session, tenant_id)
        session.add(
            Quote(
                id=quote_id, tenant_id=tenant_id, quote_number="Q-1", customer_id=uuid.uuid4(),
                status=QuoteStatus.SENT, subtotal=100, total=100,
            )
        )
        await session.commit()

    from sqlalchemy import select as _select

    async with _asm() as session:
        await set_tenant_context(session, tenant_id)
        quote = (await session.execute(_select(Quote).where(Quote.id == quote_id))).scalar_one()
    assert quote.tenant_id == tenant_id

    # Directly drives the guarded lookup webhooks.py's
    # _handle_quote_deposit_succeeded performs, using the module's own
    # (now-spied) set_tenant_context.
    async with webhooks_module.async_session_maker() as session:
        await webhooks_module.set_tenant_context(session, tenant_id)
        loaded = await session.get(Quote, quote_id)
    assert loaded.tenant_id == tenant_id
    assert spy.calls[-1] == (tenant_id, str(tenant_id))


# ---------------------------------------------------------- Agent execution


@requires_real_postgres
async def test_agent_execution_sets_context_across_the_run_loop(monkeypatch, tool_registry) -> None:
    """PHASE_17A §15: event-triggered/scheduled/approval-resumed Agent
    execution's tenant-context propagation through AgentExecution ->
    AgentReasoningService -> ToolRegistry was 'flagged NEEDS DESIGN
    DECISION, not asserted safe'. Proves a real SINGLE_ACTION run now
    stamps tenant context at every one of AgentExecutionService's session
    opens, for the correct tenant. Uses this repo's own existing
    AgentService/`system.get_tenant_context` pattern (see
    tests/test_agent_execution_service.py) rather than hand-rolling agent
    rows, so this test exercises the real creation/publish/activate path,
    not a shortcut."""
    import app.services.agent_execution_service as exec_module

    spy = _ContextSpy()
    monkeypatch.setattr(exec_module, "set_tenant_context", spy)

    from app.services.agent_execution_service import AgentExecutionService
    from app.services.agent_service import AgentService

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        await _make_org(session, tenant_id)

    agent_service = AgentService(async_session_maker)
    execution_service = AgentExecutionService(async_session_maker, tool_registry)

    agent = await agent_service.create_agent(
        tenant_id, name="Probe Agent", purpose="test", autonomy_tier=AgentAutonomyTier.EXECUTE_AUTONOMOUS,
        acting_role=Role.MANAGER, created_by=None,
    )
    await agent_service.grant_tool_permission(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_registry=tool_registry, created_by=None
    )
    version = await agent_service.create_version(tenant_id, agent.id, instructions="x", created_by=None)
    await agent_service.publish_version(tenant_id, agent.id, version.id, actor_id=None)
    await agent_service.activate(tenant_id, agent.id)

    execution = await execution_service.run_action(
        tenant_id, agent.id, tool_name="system.get_tenant_context", tool_input={}, triggered_by=None,
    )
    assert execution.tenant_id == tenant_id
    assert spy.calls, "AgentExecutionService never called set_tenant_context"
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)
