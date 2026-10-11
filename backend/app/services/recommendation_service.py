"""Phase 3 (Recommendation Engine): consumes the canonical, ACTIVE Business
Blueprint (Phase 2) — never raw Discovery messages — plus Phase 1's
VerticalExtension / IntegrationProviderCatalog / Tool Catalog registries to
produce structured, explainable `Recommendation` rows.

Architectural invariant preserved: Discovery -> Requirements/Claims ->
Business Blueprint -> Recommendations. This service only ever reads a
blueprint's already-CONFIRMED claims and its already-COMPLETE
REQUIRED_CAPABILITIES section — it never re-derives business facts from
DiscoveryTurn rows itself.

Pipeline (all deterministic — see "Why no AI" below):

  1. Resolve the tenant's ACTIVE BusinessBlueprint (never DRAFT/SUPERSEDED
     — an unconfirmed or historical blueprint is not a valid basis for a
     live recommendation).
  2. Collect capability requirements from exactly two GENERIC sources,
     merged by capability_key (case-insensitive, deduplicated):
       a. The blueprint's own CONFIRMED claims under the
          REQUIRED_CAPABILITIES section (BlueprintSectionKey.
          REQUIRED_CAPABILITIES) — capability keys the business itself
          stated it needs (source=BASELINE_RULE, required=True,
          evidence=the claim's id). Supports both valid structured-claim
          shapes the extraction schema can produce for this section: a
          value-shaped claim (list/string of capability keys in
          `claim.value`) and a key-shaped claim (`claim.key` IS the
          capability key, `claim.value is True`) — see
          `_capability_keys_from_claim`'s own docstring for the exact,
          deliberately conservative rules.
       b. Every VerticalExtension the organization has enabled
          (OrganizationVerticalExtension, looked up via
          VerticalExtensionService — the exact "plugin function per
          VerticalExtension, looked up by id" mechanism
          app/models/vertical_extension.py's docstring calls for) — each
          vertical's own `capabilities` registry list contributes
          candidate (not mandatory) capability keys
          (source=VERTICAL_EXTENSION_RULE, required=False, evidence=the
          vertical's key).
     There is deliberately no third, vertical-name-branching source —
     see tests/test_cross_vertical_blueprint_validation.py-style proof in
     tests/test_cross_vertical_recommendation_validation.py, and this
     module's own static self-check
     (test_no_vertical_branch_in_recommendation_service_source).
  3. For each capability_key, emit one CAPABILITY recommendation.
  4. Match each capability_key against the live IntegrationProviderCatalog
     (by its additive `capabilities` tag list — see
     app/models/integration_catalog.py) and emit one INTEGRATION
     recommendation per matching provider, preserving
     `implementation_status` verbatim.
  5. Match each capability_key against the live Tool Catalog (read-only,
     via app/services/tool_catalog_service.py::list_tool_catalog — never
     ToolRegistry.execute()/get() with mutating intent) using simple
     deterministic keyword overlap, and emit one TOOL recommendation per
     matching tool.
  6. Persist a RecommendationRun envelope + every Recommendation row,
     superseding any still-PROPOSED recommendations left over from a
     prior blueprint version for this tenant (see "Blueprint version
     binding" below), and write one AuditLog row for the run.

Why no AI: KLAROS_MASTER_IMPLEMENTATION_ROADMAP.md's Phase 4 section allows
AI only for the `why`/`what` NARRATIVE text, never for the structural
"what to recommend" decision. Every structural step above (which
capabilities are required, which providers/tools match) is already fully
answered by structured registry data the business itself confirmed or the
platform already curated — there is no genuine semantic-reasoning gap for
an LLM to fill, so this implementation generates `why`/`what` text with a
deterministic template (mirroring DiscoveryExtractionService's own
"real provider if connected, else an honest deterministic path" shape,
except here the deterministic path is the ONLY path, since no step needs
semantic reasoning). This keeps every recommendation reproducible byte-for-
byte from the same blueprint + registry state, with zero hallucination
surface — see PHASE_3_IMPLEMENTATION_LOG.md's "AI architecture" section.

Blueprint version binding: a Recommendation snapshots the exact
`blueprint_id`/`blueprint_version` it was generated against
(TenantScopedMixin gives every row `created_at`, but the *business*
version is this explicit column, since a new BusinessBlueprint version is
a brand-new row per Phase 2's full-row versioning model). Generating a new
run for a tenant supersedes (status -> SUPERSEDED) every still-PROPOSED
recommendation left over from an older blueprint_id for that tenant, so a
stale recommendation is never silently presented as current — ACCEPTED/
REJECTED rows are left untouched (a human decision, once made, is not
retroactively erased by a later blueprint edit).

Tenant isolation: every method takes an explicit `tenant_id` and filters
every query by it, mirroring BusinessBlueprintService's own convention
exactly (this codebase's unenforced-by-DB-RLS convention — RLS audit-mode
is applied in the migration but is not itself the enforcement boundary
yet).

Read-only guarantee: nothing in this module calls
`ToolRegistry.execute()`/`.get()`, `IntegrationConnectionService.connect()`
/`.verify()`, or any communication/agent-creation code path. It only reads
`list_tool_catalog()` (itself read-only, see its own docstring) and the
IntegrationProviderCatalog table, and writes `Recommendation`/
`RecommendationRun`/`AuditLog` rows — see
tests/test_recommendation_service.py's explicit proof of this.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.business_blueprint import (
    BlueprintClaim,
    BlueprintSection,
    BlueprintSectionKey,
    BlueprintStatus,
    BusinessBlueprint,
    ClaimStatus,
)
from app.models.integration_catalog import IntegrationProviderCatalog
from app.models.recommendation import (
    Recommendation,
    RecommendationRun,
    RecommendationSource,
    RecommendationStatus,
    RecommendationType,
)
from app.models.vertical_extension import VerticalExtension
from app.services.capability_vocabulary import canonical_key, split_capability_text
from app.services.tool_catalog_service import ToolCatalogEntry, list_tool_catalog
from app.services.vertical_extension_service import VerticalExtensionService
from app.tools.registry import ToolRegistry

# Generic English stopwords/connector tokens excluded from capability-key
# <-> tool keyword matching — kept intentionally tiny and domain-agnostic
# (no vertical- or capability-specific word appears here).
_STOPWORD_TOKENS = {"and", "or", "the", "a", "an", "for", "to", "of", "management", "processing"}

_MAX_TOOL_MATCHES_PER_CAPABILITY = 3


class NoActiveBlueprintError(Exception):
    pass


class RecommendationNotFoundError(Exception):
    pass


class InvalidRecommendationTransitionError(Exception):
    pass


@dataclass(frozen=True)
class _CapabilityRequirement:
    capability_key: str
    required: bool
    why: str
    based_on: list
    confidence: float
    source: str
    source_vertical_key: str | None


def _tokenize(text: str) -> set[str]:
    return {
        tok
        for tok in text.lower().replace("-", "_").replace("/", "_").split("_")
        if len(tok) >= 4 and tok not in _STOPWORD_TOKENS
    }


def _tokenize_words(text: str) -> set[str]:
    return {tok for tok in "".join(c if c.isalnum() else " " for c in text.lower()).split() if len(tok) >= 4}


def _capability_keys_from_claim(claim: BlueprintClaim) -> list[str]:
    """Extract candidate capability keys from one CONFIRMED
    REQUIRED_CAPABILITIES `BlueprintClaim`, supporting both valid
    structured-claim representations the extraction schema
    (`DiscoveryExtractionService.ExtractedClaim`, which always carries both
    a `key` and a `value` field) can legitimately produce for this section:

      - value-shaped (pre-existing): the capability name(s) live in
        `claim.value` — either a single non-empty string, or a list of
        non-empty strings — and `claim.key` is just a free-form/generic
        label for the claim itself (e.g. "required_capabilities.summary",
        as emitted by the deterministic fallback). This is unchanged.

      - key-shaped: `claim.key` itself IS the capability name (e.g.
        "telemedicine"), and `claim.value` is the literal boolean `True`
        — a per-capability Requirement claim, one row per stated
        capability, rather than one row bundling a list. This shape was
        previously silently dropped (`claim.value` is neither a list nor
        a string), which is the bug this function fixes.

    Deliberately conservative to satisfy "never fabricate a capability
    from an arbitrary claim key": the key-shaped interpretation only ever
    fires when `claim.value is True` exactly (never a merely-truthy value
    such as a non-empty dict/number/string — those are not this schema's
    boolean-flag shape and are treated as malformed/unrelated instead).
    `False`, `None`, numbers, dicts, and empty/blank strings never yield a
    capability either way. The REQUIRED_CAPABILITIES section_key filter is
    applied by the caller's query, not here — this function trusts that
    scoping and does not re-check it.
    """
    value = claim.value
    if isinstance(value, list):
        return [v.strip().lower() for v in value if isinstance(v, str) and v.strip()]
    if isinstance(value, str):
        # A free-text statement ("bookings, payments and inventory") is
        # split into individual capabilities and canonicalised through the
        # shared vocabulary (unknown phrases fall back to a stable
        # snake_case key); a single phrase is never fragmented. Structured
        # list / key-shaped claims keep their exact keys.
        keys: list[str] = []
        for item in split_capability_text(value) if value.strip() else []:
            key = canonical_key(item)
            if key and key not in keys:
                keys.append(key)
        return keys
    if value is True and isinstance(claim.key, str) and claim.key.strip():
        # A key that names several capabilities at once ("inventory and pricing") is
        # split; a single key (incl. snake_case) is kept exactly as stated.
        parts = split_capability_text(claim.key)
        if len(parts) > 1:
            keys = []
            for part in parts:
                k = canonical_key(part)
                if k and k not in keys:
                    keys.append(k)
            return keys
        return [claim.key.strip().lower()]
    return []


_SECTION_CAPABILITY_FIELDS = ("capabilities", "required_capabilities", "capability_list")


def _capability_keys_from_section_data(data: dict | None) -> list[str]:
    """Capabilities the business lists directly in its (human-editable)
    REQUIRED_CAPABILITIES section payload — the correction path on the
    Blueprint review screen. Only the explicitly capability-named fields are
    read (never arbitrary section values), as a list of strings or a
    free-text string, with the same normalisation as free-text claims."""
    if not isinstance(data, dict):
        return []
    keys: list[str] = []
    for field_name in _SECTION_CAPABILITY_FIELDS:
        value = data.get(field_name)
        items: list[str] = []
        if isinstance(value, list):
            items = [v.strip() for v in value if isinstance(v, str) and v.strip()]
        elif isinstance(value, str) and value.strip():
            items = split_capability_text(value)
        for item in items:
            key = canonical_key(item)
            if key and key not in keys:
                keys.append(key)
    return keys


def _provider_serves(provider_tags: list, capability_key: str) -> bool:
    """A provider serves a capability when it is tagged with that exact key
    or with a tag the shared vocabulary canonicalises to it (so a free-text
    requirement like "payments" matches a provider tagged
    `payment_processing`)."""
    wanted = {capability_key, canonical_key(capability_key)}
    return any(isinstance(t, str) and (t in wanted or canonical_key(t) in wanted) for t in provider_tags)


class RecommendationService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory
        self._verticals = VerticalExtensionService(session_factory)

    # --- generation ------------------------------------------------------

    async def generate_recommendations(
        self,
        tenant_id: uuid.UUID,
        *,
        tool_registry: ToolRegistry,
        triggered_by: uuid.UUID | None,
    ) -> RecommendationRun:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            blueprint = (
                await session.execute(
                    select(BusinessBlueprint).where(
                        BusinessBlueprint.tenant_id == tenant_id,
                        BusinessBlueprint.status == BlueprintStatus.ACTIVE,
                    )
                )
            ).scalar_one_or_none()
        if blueprint is None:
            raise NoActiveBlueprintError(
                "No ACTIVE Business Blueprint for this tenant — recommendations require an activated blueprint"
            )

        requirements, verticals_considered = await self._collect_capability_requirements(tenant_id, blueprint)
        providers = await self._load_providers()
        tool_catalog = list_tool_catalog(tool_registry)

        run_id = uuid.uuid4()
        rows: list[Recommendation] = []
        seen: set[tuple] = set()

        def _add(row_kwargs: dict) -> None:
            dedup_key = (
                row_kwargs["type"],
                row_kwargs["capability_key"],
                row_kwargs.get("provider_key"),
                row_kwargs.get("tool_name"),
            )
            if dedup_key in seen:
                return
            seen.add(dedup_key)
            rows.append(
                Recommendation(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    blueprint_id=blueprint.id,
                    blueprint_version=blueprint.version,
                    status=RecommendationStatus.PROPOSED,
                    **row_kwargs,
                )
            )

        for req in requirements:
            _add(
                {
                    "type": RecommendationType.CAPABILITY,
                    "capability_key": req.capability_key,
                    "provider_key": None,
                    "provider_implementation_status": None,
                    "tool_name": None,
                    "what": f"This business needs the '{req.capability_key}' capability.",
                    "why": req.why,
                    "based_on": req.based_on,
                    "dependencies": [],
                    "cost_estimate": None,
                    "required": req.required,
                    "alternatives": [],
                    "confidence": req.confidence,
                    "source": req.source,
                    "source_vertical_key": req.source_vertical_key,
                }
            )

            matching_providers = [p for p in providers if _provider_serves(p.capabilities or [], req.capability_key)]
            matching_provider_keys = [p.provider_key for p in matching_providers]
            for provider in matching_providers:
                alternatives = [k for k in matching_provider_keys if k != provider.provider_key]
                _add(
                    {
                        "type": RecommendationType.INTEGRATION,
                        "capability_key": req.capability_key,
                        "provider_key": provider.provider_key,
                        "provider_implementation_status": provider.implementation_status,
                        "tool_name": None,
                        "what": f"Consider {provider.display_name} to satisfy '{req.capability_key}'.",
                        "why": (
                            f"{provider.display_name} is catalogued (Phase 1 IntegrationProviderCatalog) "
                            f"as supporting the '{req.capability_key}' capability this business needs. "
                            f"Catalog implementation_status={provider.implementation_status}."
                        ),
                        "based_on": req.based_on + [{"kind": "integration_provider_catalog", "provider_key": provider.provider_key}],
                        "dependencies": [req.capability_key],
                        "cost_estimate": None,
                        "required": False,
                        "alternatives": alternatives,
                        "confidence": 0.8,
                        "source": req.source,
                        "source_vertical_key": req.source_vertical_key,
                    }
                )

            matching_tools = self._match_tools(req.capability_key, tool_catalog)
            matching_tool_names = [t.name for t in matching_tools]
            for tool in matching_tools:
                alternatives = [n for n in matching_tool_names if n != tool.name]
                _add(
                    {
                        "type": RecommendationType.TOOL,
                        "capability_key": req.capability_key,
                        "provider_key": None,
                        "provider_implementation_status": None,
                        "tool_name": tool.name,
                        "what": f"Consider the '{tool.name}' tool to help satisfy '{req.capability_key}'.",
                        "why": (
                            f"Tool '{tool.name}' ({tool.description}) shares keyword overlap with the "
                            f"'{req.capability_key}' capability this business needs, per the read-only Tool Catalog."
                        ),
                        "based_on": req.based_on + [{"kind": "tool_catalog", "tool_name": tool.name}],
                        "dependencies": [req.capability_key],
                        "cost_estimate": None,
                        "required": False,
                        "alternatives": alternatives,
                        "confidence": 0.6,
                        "source": req.source,
                        "source_vertical_key": req.source_vertical_key,
                    }
                )

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            # Blueprint version binding: supersede prior still-PROPOSED
            # recommendations left over from an older blueprint for this
            # tenant. ACCEPTED/REJECTED rows are a human decision and are
            # never retroactively touched.
            stale = (
                await session.execute(
                    select(Recommendation).where(
                        Recommendation.tenant_id == tenant_id,
                        Recommendation.blueprint_id != blueprint.id,
                        Recommendation.status == RecommendationStatus.PROPOSED,
                    )
                )
            ).scalars().all()
            for row in stale:
                row.status = RecommendationStatus.SUPERSEDED

            run = RecommendationRun(
                id=run_id,
                tenant_id=tenant_id,
                blueprint_id=blueprint.id,
                blueprint_version=blueprint.version,
                triggered_by=triggered_by,
                verticals_considered=verticals_considered,
                recommendation_count=len(rows),
                completed_at=datetime.now(timezone.utc),
            )
            session.add(run)
            # Explicit flush before the dependent Recommendation rows: the
            # FK to recommendation_runs.id has no ORM `relationship()`
            # configured (deliberately — these rows are addressed by
            # explicit tenant-scoped queries, not lazy-loaded graphs), so
            # the unit-of-work's automatic insert-ordering can't be relied
            # on to sequence these two inserts correctly against a real
            # FK-enforcing database (masked entirely on SQLite, which does
            # not enforce foreign keys by default — caught only by this
            # phase's real-Postgres test).
            await session.flush()
            for row in rows:
                session.add(row)

            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.SYSTEM if triggered_by is None else ActorType.USER,
                    actor_id=triggered_by,
                    action="recommendation.generate_run",
                    tool=None,
                    entity_type="recommendation_run",
                    entity_id=run.id,
                    input_summary={
                        "blueprint_id": str(blueprint.id),
                        "blueprint_version": blueprint.version,
                        "recommendation_count": len(rows),
                        "superseded_count": len(stale),
                    },
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(run)
            return run

    async def collect_capability_requirements(
        self, tenant_id: uuid.UUID, blueprint: BusinessBlueprint
    ) -> tuple[list[_CapabilityRequirement], list[str]]:
        """Public, read-only view of the exact requirement set the engine
        would act on for `blueprint` (confirmed REQUIRED_CAPABILITIES claims
        plus enabled verticals' registry capabilities). The Business Builder's
        Requirements stage derives from this so there is one source of truth."""
        return await self._collect_capability_requirements(tenant_id, blueprint)

    async def _collect_capability_requirements(
        self, tenant_id: uuid.UUID, blueprint: BusinessBlueprint
    ) -> tuple[list[_CapabilityRequirement], list[str]]:
        merged: dict[str, _CapabilityRequirement] = {}

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            claims = (
                await session.execute(
                    select(BlueprintClaim).where(
                        BlueprintClaim.tenant_id == tenant_id,
                        BlueprintClaim.blueprint_id == blueprint.id,
                        BlueprintClaim.section_key == BlueprintSectionKey.REQUIRED_CAPABILITIES.value,
                        BlueprintClaim.status == ClaimStatus.CONFIRMED,
                    )
                )
            ).scalars().all()

        for claim in claims:
            for key in _capability_keys_from_claim(claim):
                evidence = {
                    "kind": "blueprint_claim",
                    "claim_id": str(claim.id),
                    "section_key": claim.section_key,
                }
                existing = merged.get(key)
                if existing is None:
                    merged[key] = _CapabilityRequirement(
                        capability_key=key,
                        required=True,
                        why=(
                            f"The business's own confirmed Business Blueprint claim "
                            f"(REQUIRED_CAPABILITIES, claim {claim.id}) states this capability is required."
                        ),
                        based_on=[evidence],
                        confidence=(claim.confidence if claim.confidence is not None else 0.95),
                        source=RecommendationSource.BASELINE_RULE,
                        source_vertical_key=None,
                    )
                else:
                    merged[key] = _CapabilityRequirement(
                        capability_key=key,
                        required=True,
                        why=existing.why,
                        based_on=existing.based_on + [evidence],
                        confidence=max(existing.confidence, claim.confidence or 0.95),
                        source=existing.source,
                        source_vertical_key=existing.source_vertical_key,
                    )

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            section = (
                await session.execute(
                    select(BlueprintSection).where(
                        BlueprintSection.tenant_id == tenant_id,
                        BlueprintSection.blueprint_id == blueprint.id,
                        BlueprintSection.section_key == BlueprintSectionKey.REQUIRED_CAPABILITIES.value,
                    )
                )
            ).scalar_one_or_none()
        for key in _capability_keys_from_section_data(section.data if section is not None else None):
            evidence = {"kind": "blueprint_section", "section_key": BlueprintSectionKey.REQUIRED_CAPABILITIES.value}
            existing = merged.get(key)
            if existing is None:
                merged[key] = _CapabilityRequirement(
                    capability_key=key,
                    required=True,
                    why=(
                        "The business's own Business Blueprint lists this capability under "
                        "REQUIRED_CAPABILITIES."
                    ),
                    based_on=[evidence],
                    confidence=0.95,
                    source=RecommendationSource.BASELINE_RULE,
                    source_vertical_key=None,
                )
            else:
                merged[key] = _CapabilityRequirement(
                    capability_key=key,
                    required=True,
                    why=existing.why,
                    based_on=existing.based_on + [evidence],
                    confidence=max(existing.confidence, 0.95),
                    source=existing.source,
                    source_vertical_key=existing.source_vertical_key,
                )

        # Registry-lookup plugin mechanism (never a hardcoded vertical
        # branch): every VerticalExtension the org has enabled contributes
        # its own `capabilities` list as candidate (non-mandatory)
        # capabilities.
        enabled_links = await self._verticals.list_enabled_for_organization(tenant_id)
        verticals_considered: list[str] = []
        if enabled_links:
            # Phase 17B-2R classification: this session only reads
            # `VerticalExtension` — the GLOBAL platform catalog table (see
            # vertical_extension_service.py's own docstring), never the
            # tenant-scoped `OrganizationVerticalExtension` join (already
            # queried above via `self._verticals.list_enabled_for_organization`,
            # which does set tenant context on its own session). Correctly
            # excluded here, not a gap.
            async with self._session_factory() as session:
                vertical_ids = [link.vertical_extension_id for link in enabled_links]
                verticals = (
                    await session.execute(select(VerticalExtension).where(VerticalExtension.id.in_(vertical_ids)))
                ).scalars().all()
            for vertical in verticals:
                verticals_considered.append(vertical.key)
                for raw in vertical.capabilities or []:
                    if not isinstance(raw, str) or not raw.strip():
                        continue
                    key = raw.strip().lower()
                    evidence = {"kind": "vertical_extension", "vertical_key": vertical.key}
                    if key not in merged:
                        merged[key] = _CapabilityRequirement(
                            capability_key=key,
                            required=False,
                            why=(
                                f"The '{vertical.key}' vertical (enabled for this organization) registers "
                                f"'{key}' as one of its capabilities."
                            ),
                            based_on=[evidence],
                            confidence=0.6,
                            source=RecommendationSource.VERTICAL_EXTENSION_RULE,
                            source_vertical_key=vertical.key,
                        )
                    else:
                        existing = merged[key]
                        merged[key] = _CapabilityRequirement(
                            capability_key=existing.capability_key,
                            required=existing.required,
                            why=existing.why,
                            based_on=existing.based_on + [evidence],
                            confidence=existing.confidence,
                            source=existing.source,
                            source_vertical_key=existing.source_vertical_key,
                        )

        return list(merged.values()), verticals_considered

    async def _load_providers(self) -> list[IntegrationProviderCatalog]:
        # Phase 17B-2R classification: `IntegrationProviderCatalog` is
        # deliberately NOT tenant-scoped (see app/models/integration_catalog.py's
        # own docstring — "tenant-independent reference data"). GLOBAL/SHARED,
        # correctly excluded, not a gap.
        async with self._session_factory() as session:
            return list((await session.execute(select(IntegrationProviderCatalog))).scalars().all())

    def _match_tools(self, capability_key: str, tool_catalog: list[ToolCatalogEntry]) -> list[ToolCatalogEntry]:
        cap_tokens = _tokenize(capability_key)
        if not cap_tokens:
            return []
        matches = []
        for tool in tool_catalog:
            haystack = _tokenize_words(f"{tool.name} {tool.description}")
            if cap_tokens & haystack:
                matches.append(tool)
        return matches[:_MAX_TOOL_MATCHES_PER_CAPABILITY]

    # --- read ------------------------------------------------------------

    async def list_recommendations(
        self,
        tenant_id: uuid.UUID,
        *,
        blueprint_id: uuid.UUID | None = None,
        status: str | None = None,
        type_: str | None = None,
    ) -> list[Recommendation]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            stmt = select(Recommendation).where(Recommendation.tenant_id == tenant_id)
            if blueprint_id is not None:
                stmt = stmt.where(Recommendation.blueprint_id == blueprint_id)
            if status is not None:
                stmt = stmt.where(Recommendation.status == status)
            if type_ is not None:
                stmt = stmt.where(Recommendation.type == type_)
            stmt = stmt.order_by(Recommendation.created_at.asc())
            return list((await session.execute(stmt)).scalars().all())

    async def get_run(self, tenant_id: uuid.UUID, run_id: uuid.UUID) -> RecommendationRun:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            run = await session.get(RecommendationRun, run_id)
            if run is None or run.tenant_id != tenant_id:
                raise RecommendationNotFoundError(f"RecommendationRun {run_id} not found")
            return run

    # --- lifecycle ---------------------------------------------------------

    async def accept(
        self, tenant_id: uuid.UUID, recommendation_id: uuid.UUID, *, decided_by: uuid.UUID | None
    ) -> Recommendation:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rec = await session.get(Recommendation, recommendation_id)
            if rec is None or rec.tenant_id != tenant_id:
                raise RecommendationNotFoundError(f"Recommendation {recommendation_id} not found")
            if rec.status == RecommendationStatus.REJECTED:
                raise InvalidRecommendationTransitionError("Recommendation already rejected — cannot accept")
            if rec.status == RecommendationStatus.SUPERSEDED:
                raise InvalidRecommendationTransitionError(
                    "Recommendation has been superseded by a newer blueprint version — cannot accept"
                )
            if rec.status == RecommendationStatus.ACCEPTED:
                return rec  # idempotent

            rec.status = RecommendationStatus.ACCEPTED
            rec.decided_by = decided_by
            rec.decided_at = datetime.now(timezone.utc)
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=decided_by,
                    action="recommendation.accept",
                    tool=None,
                    entity_type="recommendation",
                    entity_id=rec.id,
                    input_summary={"type": rec.type, "capability_key": rec.capability_key},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(rec)
            return rec

    async def reject(
        self,
        tenant_id: uuid.UUID,
        recommendation_id: uuid.UUID,
        *,
        decided_by: uuid.UUID | None,
        reason: str | None,
    ) -> Recommendation:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rec = await session.get(Recommendation, recommendation_id)
            if rec is None or rec.tenant_id != tenant_id:
                raise RecommendationNotFoundError(f"Recommendation {recommendation_id} not found")
            if rec.status == RecommendationStatus.ACCEPTED:
                raise InvalidRecommendationTransitionError("Recommendation already accepted — cannot reject")
            if rec.status == RecommendationStatus.REJECTED:
                return rec  # idempotent

            rec.status = RecommendationStatus.REJECTED
            rec.decided_by = decided_by
            rec.decided_at = datetime.now(timezone.utc)
            rec.rejection_reason = reason
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=decided_by,
                    action="recommendation.reject",
                    tool=None,
                    entity_type="recommendation",
                    entity_id=rec.id,
                    input_summary={"type": rec.type, "capability_key": rec.capability_key, "reason": reason},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(rec)
            return rec
