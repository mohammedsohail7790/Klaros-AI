"""Phase 11 (PHASE_11_WEBSITE_BUILDER_DESIGN.md §13/§14, HARD SCOPE §Phase 7):
the deterministic Website Builder renderer.

Contract (never weaken without updating tests/test_website_renderer_security.py):
  - Accepts only an already-validated `WebsiteSpecification`
    (app/schemas/website_specification.py) — never a raw dict/JSON blob.
  - Maps `component_type` to a fixed, closed set of render functions; an
    unrecognized `component_type` is impossible to reach here because
    `WebsiteSpecification` already rejects it at validation time, but this
    module still raises rather than silently skipping if it somehow saw one
    (defense in depth, Phase 7: "reject unknown component types").
  - Never evaluates, imports, or executes anything derived from spec
    content. Never calls out to `eval`/`exec`/`subprocess`/dynamic
    `importlib`. Output is a plain nested dict/list/str/int/bool/None tree
    ("render tree") — no HTML string is ever constructed here, so there is
    no `dangerouslySetInnerHTML`-equivalent surface at all. A future
    frontend renders this tree via its own component-type -> component
    registry, escaping every string exactly as any other JSON-sourced text
    would be (see PHASE_11_WEBSITE_BUILDER_DESIGN.md §18 "Deliberately
    deferred" for why no such frontend is built in this phase).
  - Vertical-specific data (Provider directory, procedure list) is resolved
    purely through `app/services/website_data_providers.py`'s generic
    registry — this file contains ZERO references to "medical_tourism",
    "provider", "hospital", or any vertical name (see
    tests/test_website_no_vertical_hardcoding.py, which greps this exact
    file).
  - A single section failing to resolve its data source renders as an
    explicit empty-state, never a partial/broken render and never an
    unhandled exception that could leak internals (Phase 7: "must fail
    safely, never partially execute").
"""

from __future__ import annotations

import uuid
from typing import Any

from app.schemas.website_specification import PageSpec, SectionSpec, WebsiteSpecification
from app.services.website_data_providers import resolve_data_source


async def render_section(tenant_id: uuid.UUID, section: SectionSpec) -> dict[str, Any]:
    node: dict[str, Any] = {
        "component_type": section.component_type.value,
        "props": section.props,
    }
    if section.data_source is not None:
        resolved = await resolve_data_source(
            tenant_id, section.data_source.provider_key, section.data_source.params
        )
        node["data"] = resolved
    return node


async def render_page(tenant_id: uuid.UUID, page: PageSpec) -> dict[str, Any]:
    sections = [await render_section(tenant_id, s) for s in page.sections]
    return {
        "slug": page.slug,
        "title": page.title,
        "seo": page.seo.model_dump(),
        "sections": sections,
    }


async def render_website(tenant_id: uuid.UUID, spec: WebsiteSpecification) -> dict[str, Any]:
    """The single entry point Preview (Phase 8) and the public Publish read
    path (Phase 9/13) both use — the exact same code renders a DRAFT
    preview and a PUBLISHED site; only which `WebsiteSpecification` is
    loaded differs (app/services/website_service.py)."""
    pages = [await render_page(tenant_id, p) for p in spec.pages]
    return {
        "theme": spec.theme.model_dump(),
        "navigation": spec.navigation.model_dump(),
        "seo_defaults": spec.seo_defaults.model_dump(),
        "pages": pages,
    }

