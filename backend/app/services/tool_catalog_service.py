"""Phase 1 (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.3): a read-only
metadata/discovery projection over the existing `ToolRegistry` — never a
second, independently-editable copy of tool identity.

Sync strategy (documented per PHASE_1_IMPLEMENTATION_LOG.md's "Tool catalog
design" section): DERIVED LIVE, READ-THROUGH. This module holds no table,
no cached snapshot, and no persisted copy of tool metadata — every call
re-reads `ToolRegistry`'s own in-memory `_tools` dict at request time, so
there is no drift to detect: the catalog and the registry are, by
construction, the same data. The alternative (a synced table) was
rejected because Phase 1's own plan says this: "zero changes to
ToolRegistry/base.py/factory.py" — an endpoint reading the registry live
achieves that with no sync job, no staleness window, and no second source
of truth to keep consistent.

Accessing `registry._tools` (a "private" attribute) directly, rather than
adding a public accessor method to `ToolRegistry`, is a deliberate choice
to honor the explicit "zero changes to ToolRegistry" constraint in the
Phase 1 plan — `list_available()` (the one existing public accessor) is
role-filtered, which is wrong for a catalog that must enumerate every
registered tool regardless of any particular actor's role.
"""

from dataclasses import dataclass

from app.tools.registry import ToolRegistry


@dataclass(frozen=True)
class ToolCatalogEntry:
    name: str
    description: str
    required_permission: str | None
    tenant_scoped: bool
    counts_toward_ai_usage: bool


def list_tool_catalog(registry: ToolRegistry) -> list[ToolCatalogEntry]:
    """Every currently-registered tool's metadata, sourced directly from
    the live registry. Never calls `registry.execute()` or `registry.get()`
    with any mutating intent — this function only reads attributes already
    present on each registered `Tool` instance, so calling it can never
    execute a tool, write an AuditLog row, or otherwise have any side
    effect (see tests/test_tool_catalog_api.py's explicit proof of this)."""
    entries = [
        ToolCatalogEntry(
            name=tool.name,
            description=tool.description,
            required_permission=(
                tool.required_permission.value if tool.required_permission is not None else None
            ),
            tenant_scoped=tool.tenant_scoped,
            counts_toward_ai_usage=tool.counts_toward_ai_usage,
        )
        for tool in registry._tools.values()  # noqa: SLF001 — see module docstring
    ]
    return sorted(entries, key=lambda e: e.name)
