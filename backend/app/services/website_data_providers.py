"""Phase 11 (PHASE_11_WEBSITE_BUILDER_DESIGN.md §11/§12): the generic
vertical-data-binding registry. This is the ENTIRE mechanism by which a
`PROVIDER_DIRECTORY`/`PROCEDURE_LIST` website section is bound to real,
vertical-specific structured data, without the generic renderer
(`app/services/website_renderer.py`) or the generic generation service
(`app/services/website_generation_service.py`) ever importing or naming a
specific vertical.

A "provider" here is a plain async function: `(tenant_id, params) -> dict`,
registered under a namespaced string key (`"<vertical_key>.<data_key>"`,
validated by `DataSourceRef.provider_key`, app/schemas/website_specification.py).
The registry itself is vertical-agnostic — it is populated by whichever
vertical module chooses to register a function, exactly mirroring how
`IntegrationProviderCatalog.capabilities` tags are looked up by key rather
than by an `if vertical == ...` switch (app/models/recommendation.py's own
module docstring).

Every registered function's return value is itself still passed through
`app/services/website_renderer.py`'s generic escaping/sanitization before
being handed to any renderer output — a malicious or buggy data provider
cannot itself become an injection vector (Phase 12/14: "generated content is
ALWAYS treated as untrusted, even AI-generated content").
"""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

WebsiteDataProvider = Callable[[uuid.UUID, dict[str, Any]], Awaitable[dict[str, Any]]]

_REGISTRY: dict[str, WebsiteDataProvider] = {}


def register_website_data_provider(provider_key: str, fn: WebsiteDataProvider) -> None:
    """Called once, at import time, by a vertical's own service module
    (e.g. app/services/medical_tourism_service.py) — never by generic
    Website Builder code."""
    _REGISTRY[provider_key] = fn


def get_website_data_provider(provider_key: str) -> WebsiteDataProvider | None:
    return _REGISTRY.get(provider_key)


async def resolve_data_source(
    tenant_id: uuid.UUID, provider_key: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Never raises to the renderer — an unregistered or failing provider
    resolves to an explicit empty-result shape so the renderer can fail
    safely (Phase 7: "a malicious website specification must fail safely,
    never partially execute") rather than propagate an exception that
    might expose internals."""
    fn = _REGISTRY.get(provider_key)
    if fn is None:
        return {"items": [], "provider_key": provider_key, "resolved": False}
    try:
        result = await fn(tenant_id, params)
    except Exception:  # noqa: BLE001 - a data provider must never crash a render
        return {"items": [], "provider_key": provider_key, "resolved": False}
    if not isinstance(result, dict) or "items" not in result:
        return {"items": [], "provider_key": provider_key, "resolved": False}
    return {"items": result["items"], "provider_key": provider_key, "resolved": True}
