"""Phase 17B-2R: real-PostgreSQL behavioral proof that
LeadQualificationService.qualify (1 session-open site) and
LeadEnrichmentService.enrich (1 session-open site, called from inside
qualify()) both stamp `SET LOCAL app.tenant_id` on their own,
independently-opened sessions. Same methodology as
`test_tenant_context_automation_service_phase17b2r.py`.

Note the two services open TWO SEPARATE sessions for one `qualify()` call
(qualify's own session, plus enrich's own session nested inside it) — both
must independently set context; a fix on only one of the two would leave a
real gap.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.events.bus import EventBus
from app.events.transport import InMemoryTransport
from app.models.crm import Lead
from app.models.organization import Organization
from app.services.enrichment_service import LeadEnrichmentService
from app.services.qualification_service import LeadNotFoundError, LeadQualificationService

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


async def _make_org_and_lead(tenant_id: uuid.UUID) -> uuid.UUID:
    lead_id = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tenant_id, name=f"org-{tenant_id}", slug=f"org-{tenant_id}"))
        session.add(Lead(id=lead_id, tenant_id=tenant_id, name="Test Lead", source="web", phone="+15551234567"))
        await session.commit()
    return lead_id


def _services():
    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    enrichment = LeadEnrichmentService(async_session_maker)
    qualification = LeadQualificationService(async_session_maker, bus, enrichment)
    return qualification, enrichment


@requires_real_postgres
async def test_qualify_sets_tenant_context_on_both_its_own_and_enrichments_session(monkeypatch, spy) -> None:
    import app.services.enrichment_service as enrichment_service_module
    import app.services.qualification_service as qualification_service_module

    monkeypatch.setattr(qualification_service_module, "set_tenant_context", spy)
    monkeypatch.setattr(enrichment_service_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    lead_id = await _make_org_and_lead(tenant_id)
    qualification, _ = _services()

    outcome = await qualification.qualify(tenant_id, lead_id)
    assert outcome.lead_id == str(lead_id)

    # Two independent session-open sites: qualify()'s own + enrich()'s own.
    assert len(spy.calls) == 2
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_qualify_tenant_bs_lead() -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    lead_a = await _make_org_and_lead(tenant_a)
    await _make_org_and_lead(tenant_b)
    qualification, _ = _services()

    with pytest.raises(LeadNotFoundError):
        await qualification.qualify(tenant_b, lead_a)


@requires_real_postgres
async def test_enrich_scopes_customer_match_to_tenant() -> None:
    """LeadEnrichmentService._find_customer queries Customer filtered by
    tenant_id — proves a tenant B lead's enrichment can never match a
    tenant A customer even when email/phone happen to coincide."""
    from app.models.crm import Customer

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    lead_b = await _make_org_and_lead(tenant_b)

    shared_phone = "+15559998888"
    async with async_session_maker() as session:
        session.add(Customer(id=uuid.uuid4(), tenant_id=tenant_a, name="A Customer", phone_normalized=shared_phone))
        await session.commit()

    async with async_session_maker() as session:
        lead = await session.get(Lead, lead_b)
        lead.phone_normalized = shared_phone
        await session.commit()

    _, enrichment = _services()
    async with async_session_maker() as session:
        lead = await session.get(Lead, lead_b)

    result = await enrichment.enrich(tenant_b, lead)
    assert result.previous_customer is False
    assert result.matched_customer_id is None
