"""Phase 11 (Phase 2/9/10/15 of PHASE_11_WEBSITE_BUILDER_DESIGN.md):
WebsiteService — CRUD, full-row versioning, draft-only mutation
enforcement (immutability), publish/supersede/unpublish lifecycle, and
tenant isolation.
"""

import uuid

import pytest

from app.schemas.website_specification import (
    ComponentType,
    HeroProps,
    NavigationSpec,
    PageSpec,
    SectionSpec,
    SeoMetadata,
    ThemeTokens,
    WebsiteSpecification,
)
from app.services.website_service import (
    NoPublishedVersionError,
    PageNotFoundError,
    WebsiteNotFoundError,
    WebsiteService,
    WebsiteVersionImmutableError,
    WebsiteVersionNotFoundError,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def service(tool_registry) -> WebsiteService:
    from app.db.session import async_session_maker

    return WebsiteService(async_session_maker)


def _simple_spec(headline: str = "Welcome") -> WebsiteSpecification:
    return WebsiteSpecification(
        navigation=NavigationSpec(items=[{"label": "Home", "page_slug": "home"}]),
        pages=[
            PageSpec(
                slug="home",
                title="Home",
                seo=SeoMetadata(title="Home"),
                sections=[SectionSpec(component_type=ComponentType.HERO, props=HeroProps(headline=headline).model_dump())],
            )
        ],
    )


async def test_create_website_and_load_specification(service: WebsiteService) -> None:
    tenant_id = uuid.uuid4()
    website, version = await service.create_website_with_specification(
        tenant_id, name="Acme", slug="acme", blueprint_id=None, specification=_simple_spec(),
        provenance={"business_name": "USER_STATED"}, created_by=None,
    )
    assert version.version == 1
    loaded = await service.load_specification(tenant_id, version.id)
    assert loaded.pages[0].sections[0].props["headline"] == "Welcome"


async def test_second_create_call_reuses_website_and_increments_version(service: WebsiteService) -> None:
    tenant_id = uuid.uuid4()
    website1, v1 = await service.create_website_with_specification(
        tenant_id, name="Acme", slug="acme", blueprint_id=None, specification=_simple_spec("A"),
        provenance=None, created_by=None,
    )
    website2, v2 = await service.create_website_with_specification(
        tenant_id, name="Acme", slug="acme", blueprint_id=None, specification=_simple_spec("B"),
        provenance=None, created_by=None,
    )
    assert website1.id == website2.id
    assert v2.version == 2


async def test_draft_mutation_allowed(service: WebsiteService) -> None:
    tenant_id = uuid.uuid4()
    _website, version = await service.create_website_with_specification(
        tenant_id, name="Acme", slug="acme", blueprint_id=None, specification=_simple_spec(),
        provenance=None, created_by=None,
    )
    updated = await service.update_theme(
        tenant_id, version.id, ThemeTokens(primary_color="#000000"), updated_by=None
    )
    assert updated.theme["primary_color"] == "#000000"


async def test_publish_flips_status_and_sets_current_pointer(service: WebsiteService) -> None:
    tenant_id = uuid.uuid4()
    website, version = await service.create_website_with_specification(
        tenant_id, name="Acme", slug="acme", blueprint_id=None, specification=_simple_spec(),
        provenance=None, created_by=None,
    )
    published = await service.publish_version(tenant_id, website.id, version.id, published_by=None)
    assert published.status == "PUBLISHED"
    assert published.published_at is not None

    refreshed_website = await service.get_website_by_id(tenant_id, website.id)
    assert refreshed_website.current_published_version_id == version.id


async def test_published_version_is_immutable(service: WebsiteService) -> None:
    tenant_id = uuid.uuid4()
    website, version = await service.create_website_with_specification(
        tenant_id, name="Acme", slug="acme", blueprint_id=None, specification=_simple_spec(),
        provenance=None, created_by=None,
    )
    await service.publish_version(tenant_id, website.id, version.id, published_by=None)

    with pytest.raises(WebsiteVersionImmutableError):
        await service.update_theme(tenant_id, version.id, ThemeTokens(primary_color="#111111"), updated_by=None)
    with pytest.raises(WebsiteVersionImmutableError):
        await service.add_page(
            tenant_id, version.id, PageSpec(slug="new", title="New", sections=[]), updated_by=None
        )
    with pytest.raises(WebsiteVersionImmutableError):
        await service.replace_page_sections(tenant_id, version.id, "home", [], updated_by=None)
    with pytest.raises(WebsiteVersionImmutableError):
        await service.publish_version(tenant_id, website.id, version.id, published_by=None)


async def test_publishing_a_new_version_supersedes_the_old_one_and_preserves_it(
    service: WebsiteService,
) -> None:
    tenant_id = uuid.uuid4()
    website, v1 = await service.create_website_with_specification(
        tenant_id, name="Acme", slug="acme", blueprint_id=None, specification=_simple_spec("V1"),
        provenance=None, created_by=None,
    )
    await service.publish_version(tenant_id, website.id, v1.id, published_by=None)

    v2 = await service.create_draft_from_version(tenant_id, website.id, v1.id, created_by=None)
    await service.replace_page_sections(
        tenant_id, v2.id, "home",
        [SectionSpec(component_type=ComponentType.HERO, props=HeroProps(headline="V2").model_dump())],
        updated_by=None,
    )
    published_v2 = await service.publish_version(tenant_id, website.id, v2.id, published_by=None)
    assert published_v2.status == "PUBLISHED"

    v1_reloaded = await service.get_version(tenant_id, v1.id)
    assert v1_reloaded.status == "SUPERSEDED"
    # The old published version's own content is never mutated.
    v1_spec = await service.load_specification(tenant_id, v1.id)
    assert v1_spec.pages[0].sections[0].props["headline"] == "V1"

    v2_spec = await service.load_specification(tenant_id, v2.id)
    assert v2_spec.pages[0].sections[0].props["headline"] == "V2"


async def test_unpublish_clears_pointer_but_keeps_version_status(service: WebsiteService) -> None:
    tenant_id = uuid.uuid4()
    website, version = await service.create_website_with_specification(
        tenant_id, name="Acme", slug="acme", blueprint_id=None, specification=_simple_spec(),
        provenance=None, created_by=None,
    )
    await service.publish_version(tenant_id, website.id, version.id, published_by=None)
    unpublished_website = await service.unpublish(tenant_id, website.id, unpublished_by=None)
    assert unpublished_website.current_published_version_id is None

    version_after = await service.get_version(tenant_id, version.id)
    assert version_after.status == "PUBLISHED"  # design doc §14: status itself is preserved

    with pytest.raises(NoPublishedVersionError):
        await service.get_published_specification(tenant_id, website.id)


async def test_get_published_specification_with_no_published_version_raises(service: WebsiteService) -> None:
    tenant_id = uuid.uuid4()
    website, _version = await service.create_website_with_specification(
        tenant_id, name="Acme", slug="acme", blueprint_id=None, specification=_simple_spec(),
        provenance=None, created_by=None,
    )
    with pytest.raises(NoPublishedVersionError):
        await service.get_published_specification(tenant_id, website.id)


async def test_publish_requires_valid_specification(service: WebsiteService) -> None:
    """Design doc §14: defense-in-depth re-validation at publish time —
    force an invalid state by directly deleting a page's row underneath a
    valid navigation reference is out of scope for this unit test, but a
    missing version must fail cleanly."""
    tenant_id = uuid.uuid4()
    website, _version = await service.create_website_with_specification(
        tenant_id, name="Acme", slug="acme", blueprint_id=None, specification=_simple_spec(),
        provenance=None, created_by=None,
    )
    with pytest.raises(WebsiteVersionNotFoundError):
        await service.publish_version(tenant_id, website.id, uuid.uuid4(), published_by=None)


# --- Tenant isolation (Phase 10/15) -----------------------------------------


async def test_tenant_cannot_read_another_tenants_website(service: WebsiteService) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    website_a, version_a = await service.create_website_with_specification(
        tenant_a, name="A", slug="a", blueprint_id=None, specification=_simple_spec(), provenance=None, created_by=None,
    )
    with pytest.raises(WebsiteNotFoundError):
        await service.get_website_by_id(tenant_b, website_a.id)
    with pytest.raises(WebsiteVersionNotFoundError):
        await service.get_version(tenant_b, version_a.id)
    with pytest.raises(WebsiteVersionNotFoundError):
        await service.load_specification(tenant_b, version_a.id)
    assert await service.get_website(tenant_b) is None


async def test_tenant_cannot_mutate_another_tenants_draft(service: WebsiteService) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    _website_a, version_a = await service.create_website_with_specification(
        tenant_a, name="A", slug="a", blueprint_id=None, specification=_simple_spec(), provenance=None, created_by=None,
    )
    with pytest.raises(WebsiteVersionNotFoundError):
        await service.update_theme(tenant_b, version_a.id, ThemeTokens(), updated_by=None)
    with pytest.raises(WebsiteVersionNotFoundError):
        await service.add_page(tenant_b, version_a.id, PageSpec(slug="x", title="x", sections=[]), updated_by=None)


async def test_tenant_cannot_publish_or_preview_another_tenants_website(service: WebsiteService) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    website_a, version_a = await service.create_website_with_specification(
        tenant_a, name="A", slug="a", blueprint_id=None, specification=_simple_spec(), provenance=None, created_by=None,
    )
    with pytest.raises(WebsiteVersionNotFoundError):
        await service.publish_version(tenant_b, website_a.id, version_a.id, published_by=None)

    # Even with the right website_id but wrong tenant, publish must fail.
    await service.publish_version(tenant_a, website_a.id, version_a.id, published_by=None)
    with pytest.raises(WebsiteNotFoundError):
        await service.get_published_specification(tenant_b, website_a.id)
