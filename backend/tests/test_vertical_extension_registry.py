"""Phase 1.1 (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.1): the
VerticalExtension / DomainDefinition registry. Covers creation, uniqueness,
retrieval, status transitions, invalid-data rejection, and tenant isolation
of the OrganizationVerticalExtension join table — the "registry CRUD
tests" the plan calls for.

`tests/conftest.py::_reset_database` rebuilds the schema straight from
`Base.metadata` (not via Alembic), so migration 0041's seed data is never
present here — these tests seed rows directly via the service, using the
exact same `app/data/vertical_extension_seed.py` source-of-truth the
migration itself reads from, so the two can never silently diverge.
"""

import uuid

import pytest

from app.data.vertical_extension_seed import EXPECTED_SEED_KEYS, SEED_VERTICALS
from app.models.vertical_extension import VerticalExtensionStatus
from app.services.vertical_extension_service import (
    VerticalExtensionAlreadyExistsError,
    VerticalExtensionNotFoundError,
    VerticalExtensionService,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def service(tool_registry) -> VerticalExtensionService:
    # `tool_registry` fixture (conftest.py) is only depended on to force
    # the same session_factory/engine wiring every other test uses; this
    # service doesn't call any tool.
    from app.db.session import async_session_maker

    return VerticalExtensionService(async_session_maker)


async def _seed_verticals(service: VerticalExtensionService) -> None:
    for v in SEED_VERTICALS:
        await service.create_vertical(
            key=v["key"],
            name=v["name"],
            description=v["description"],
            version=v["version"],
            status=str(v["status"]),
            capabilities=v["capabilities"],
            configuration_schema=v["configuration_schema"],
            extra_metadata=v["extra_metadata"],
        )


async def test_create_and_get_vertical(service: VerticalExtensionService) -> None:
    created = await service.create_vertical(key="field_service", name="Field Service")
    assert created.status == VerticalExtensionStatus.BETA
    fetched = await service.get_by_key("field_service")
    assert fetched.id == created.id
    assert fetched.name == "Field Service"


async def test_key_uniqueness_enforced(service: VerticalExtensionService) -> None:
    await service.create_vertical(key="dup_vertical", name="First")
    with pytest.raises(VerticalExtensionAlreadyExistsError):
        await service.create_vertical(key="dup_vertical", name="Second")


async def test_get_unknown_key_raises_not_found(service: VerticalExtensionService) -> None:
    with pytest.raises(VerticalExtensionNotFoundError):
        await service.get_by_key("no_such_vertical")


async def test_seed_data_matches_expected_reality(service: VerticalExtensionService) -> None:
    """The regression guard named in KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md
    §1.1 — proves the seed list actually contains exactly the two verticals
    the reconciled plan names. Originally both were asserted BETA (neither
    table family had shipped yet); Phase 10 ships medical_tourism's table
    family (app/models/medical_tourism.py) and promotes exactly that one
    row to ACTIVE in app/data/vertical_extension_seed.py — dropshipping's
    table family has not shipped and stays BETA, so this now asserts each
    seed row's status against the single source of truth (SEED_VERTICALS)
    rather than a blanket BETA that would no longer be true."""
    await _seed_verticals(service)
    verticals = await service.list_verticals()
    assert {v.key for v in verticals} == EXPECTED_SEED_KEYS
    expected_status_by_key = {v["key"]: v["status"] for v in SEED_VERTICALS}
    for v in verticals:
        assert v.status == expected_status_by_key[v.key]


async def test_list_filters_by_status(service: VerticalExtensionService) -> None:
    await service.create_vertical(key="active_one", name="Active One", status=VerticalExtensionStatus.ACTIVE)
    await service.create_vertical(key="beta_one", name="Beta One", status=VerticalExtensionStatus.BETA)
    active_only = await service.list_verticals(status=VerticalExtensionStatus.ACTIVE)
    assert [v.key for v in active_only] == ["active_one"]


async def test_set_status_transitions(service: VerticalExtensionService) -> None:
    await service.create_vertical(key="transition_test", name="Transition Test")
    updated = await service.set_status("transition_test", VerticalExtensionStatus.ACTIVE)
    assert updated.status == VerticalExtensionStatus.ACTIVE
    disabled = await service.set_status("transition_test", VerticalExtensionStatus.DISABLED)
    assert disabled.status == VerticalExtensionStatus.DISABLED


async def test_set_status_unknown_key_raises(service: VerticalExtensionService) -> None:
    with pytest.raises(VerticalExtensionNotFoundError):
        await service.set_status("does_not_exist", VerticalExtensionStatus.ACTIVE)


async def test_enable_for_organization_and_lookup(service: VerticalExtensionService) -> None:
    await service.create_vertical(key="medical_tourism", name="Medical Tourism", status=VerticalExtensionStatus.BETA)
    tenant_id = uuid.uuid4()
    link = await service.enable_for_organization(tenant_id, "medical_tourism")
    assert link.tenant_id == tenant_id
    assert await service.is_enabled_for_organization(tenant_id, "medical_tourism") is True

    other_tenant = uuid.uuid4()
    assert await service.is_enabled_for_organization(other_tenant, "medical_tourism") is False


async def test_enable_for_organization_unique_per_tenant_vertical(service: VerticalExtensionService) -> None:
    await service.create_vertical(key="dropshipping", name="Dropshipping")
    tenant_id = uuid.uuid4()
    await service.enable_for_organization(tenant_id, "dropshipping")
    with pytest.raises(VerticalExtensionAlreadyExistsError):
        await service.enable_for_organization(tenant_id, "dropshipping")


async def test_organization_vertical_extension_isolated_per_tenant(service: VerticalExtensionService) -> None:
    """The tenant-isolation property required for the one genuinely
    tenant-scoped Phase 1 table (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md's
    dependency: 'Phase 0 complete (RLS-on-day-one applies to
    OrganizationVerticalExtension, since it's tenant-scoped')."""
    await service.create_vertical(key="dropshipping", name="Dropshipping")
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await service.enable_for_organization(tenant_a, "dropshipping")

    enabled_a = await service.list_enabled_for_organization(tenant_a)
    enabled_b = await service.list_enabled_for_organization(tenant_b)
    assert len(enabled_a) == 1
    assert enabled_b == []


async def test_enable_for_unknown_vertical_raises(service: VerticalExtensionService) -> None:
    with pytest.raises(VerticalExtensionNotFoundError):
        await service.enable_for_organization(uuid.uuid4(), "no_such_vertical")
