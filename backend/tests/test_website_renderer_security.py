"""Phase 11 (Phase 7/12 of PHASE_11_WEBSITE_BUILDER_DESIGN.md): the
deterministic renderer's security properties — fail-safe on an
unresolvable data source, plain nested-dict output (no HTML string
construction anywhere), and no static/dynamic code-execution surface.
"""

import uuid

import pytest

from app.schemas.website_specification import (
    ComponentType,
    DataSourceRef,
    HeroProps,
    PageSpec,
    ProviderDirectoryProps,
    SectionSpec,
    WebsiteSpecification,
)
from app.services import website_renderer
from app.services.website_data_providers import _REGISTRY, register_website_data_provider


@pytest.mark.asyncio
async def test_render_website_produces_plain_dict_tree_no_html_strings() -> None:
    spec = WebsiteSpecification(
        pages=[
            PageSpec(
                slug="home",
                title="Home",
                sections=[SectionSpec(component_type=ComponentType.HERO, props=HeroProps(headline="Hi").model_dump())],
            )
        ]
    )
    tree = await website_renderer.render_website(uuid.uuid4(), spec)
    assert isinstance(tree, dict)
    assert tree["pages"][0]["sections"][0]["component_type"] == "HERO"
    # No key anywhere holds an html/script-shaped blob.
    import json

    serialized = json.dumps(tree)
    for marker in ("<script", "dangerouslySetInnerHTML", "javascript:"):
        assert marker not in serialized


@pytest.mark.asyncio
async def test_unresolved_data_source_fails_safe_not_with_exception() -> None:
    spec = WebsiteSpecification(
        pages=[
            PageSpec(
                slug="home",
                title="Home",
                sections=[
                    SectionSpec(
                        component_type=ComponentType.PROVIDER_DIRECTORY,
                        props=ProviderDirectoryProps().model_dump(),
                        data_source=DataSourceRef(provider_key="nonexistent.provider"),
                    )
                ],
            )
        ]
    )
    tree = await website_renderer.render_website(uuid.uuid4(), spec)
    section = tree["pages"][0]["sections"][0]
    assert section["data"] == {"items": [], "provider_key": "nonexistent.provider", "resolved": False}


@pytest.mark.asyncio
async def test_raising_data_provider_fails_safe() -> None:
    key = "test_website_renderer_security.raises"

    async def _boom(tenant_id, params):
        raise RuntimeError("simulated provider failure")

    register_website_data_provider(key, _boom)
    try:
        spec = WebsiteSpecification(
            pages=[
                PageSpec(
                    slug="home",
                    title="Home",
                    sections=[
                        SectionSpec(
                            component_type=ComponentType.PROVIDER_DIRECTORY,
                            props=ProviderDirectoryProps().model_dump(),
                            data_source=DataSourceRef(provider_key=key),
                        )
                    ],
                )
            ]
        )
        tree = await website_renderer.render_website(uuid.uuid4(), spec)
        assert tree["pages"][0]["sections"][0]["data"]["resolved"] is False
    finally:
        _REGISTRY.pop(key, None)


@pytest.mark.asyncio
async def test_malformed_provider_return_shape_fails_safe() -> None:
    key = "test_website_renderer_security.malformed"

    async def _bad_shape(tenant_id, params):
        return {"not_items": "evil"}

    register_website_data_provider(key, _bad_shape)
    try:
        spec = WebsiteSpecification(
            pages=[
                PageSpec(
                    slug="home",
                    title="Home",
                    sections=[
                        SectionSpec(
                            component_type=ComponentType.PROVIDER_DIRECTORY,
                            props=ProviderDirectoryProps().model_dump(),
                            data_source=DataSourceRef(provider_key=key),
                        )
                    ],
                )
            ]
        )
        tree = await website_renderer.render_website(uuid.uuid4(), spec)
        assert tree["pages"][0]["sections"][0]["data"]["items"] == []
    finally:
        _REGISTRY.pop(key, None)


def _renderer_function_source() -> str:
    """The renderer's actual executable code (function bodies only) — the
    module docstring legitimately discusses the very terms this test
    forbids in CODE, so it is deliberately excluded here."""
    import inspect

    return "\n".join(
        inspect.getsource(fn)
        for fn in (website_renderer.render_section, website_renderer.render_page, website_renderer.render_website)
    )


def test_renderer_module_source_has_no_execution_primitives() -> None:
    """Phase 7/18: static proof the renderer never evaluates/imports/
    executes anything derived from spec content."""
    source = _renderer_function_source()
    for forbidden in ("eval(", "exec(", "subprocess", "os.system", "importlib", "__import__"):
        assert forbidden not in source, f"Found forbidden primitive {forbidden!r} in website_renderer.py"


def test_renderer_module_never_names_a_vertical() -> None:
    """Phase 6/18's key extensibility proof point, scoped to this one
    file's actual code (the broader static guard lives in
    tests/test_website_no_vertical_hardcoding.py)."""
    source = _renderer_function_source().lower()
    for forbidden in ("medical_tourism", "hospital", "dropshipping", "patient"):
        assert forbidden not in source
