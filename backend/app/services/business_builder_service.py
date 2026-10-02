"""Business Builder: the read-only derivation layer that turns what Klaros
already knows about a tenant's business into the product-facing views of the
journey — Requirements, the Business Map, Next Actions and the stage tracker.

Nothing here is a second source of truth and nothing here is persisted:

  Blueprint (confirmed claims) + enabled vertical registry rows
      -> Requirements            (RecommendationService.collect_capability_requirements,
                                  merged by canonical capability key)
  Requirements + Recommendations + connection state + Website state
      -> Business Map graph      (generic nodes/edges; deterministic)
  All of the above + workforce status
      -> Next Actions / launch checklist / stage tracker

Because every view is recomputed from persisted source rows by pure
functions, the same inputs always yield the same output (tested), and
there is no cache that could drift from the Blueprint.

Genericity: no function in this module branches on a business type or
vertical name. Vertical behaviour arrives only as data — a vertical's
registry `capabilities`, the shared capability vocabulary
(`capability_vocabulary.py`), website data providers registered by vertical
modules, and the catalog's provider tags.

Honesty: every state shown to a user comes from the vocabulary
(READY / CONFIGURATION_REQUIRED / CONNECTED / NOT_CONNECTED / PLANNED).
`CONNECTED` is only ever reported for a provider whose tenant
IntegrationConnection is CONNECTED *and* whose catalog implementation is
REAL; the AI workforce is reported exactly as its adapter reports itself.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.integrations.workforce import WorkforceStatusReport, get_workforce_integration
from app.models.business_blueprint import BlueprintSectionKey, BusinessBlueprint, ClaimStatus
from app.models.crm import Lead
from app.models.integration import ConnectionStatus, IntegrationConnection
from app.models.organization import Organization
from app.models.integration_catalog import IntegrationProviderCatalog, ProviderImplementationStatus
from app.models.recommendation import RecommendationType
from app.models.vertical_extension import VerticalExtension, VerticalExtensionStatus
from app.models.website import WebsiteVersionStatus
from app.services.business_blueprint_service import BusinessBlueprintService
from app.services.capability_vocabulary import (
    GROUP_ORDER,
    CapabilityDefinition,
    canonical_key,
    describe,
)
from app.services.recommendation_service import RecommendationService, _provider_serves
from app.services.vertical_extension_service import VerticalExtensionService
from app.services.website_data_providers import resolve_data_source
from app.services.website_service import WebsiteService

READY = "READY"
CONFIGURATION_REQUIRED = "CONFIGURATION_REQUIRED"
CONNECTED = "CONNECTED"
NOT_CONNECTED = "NOT_CONNECTED"
PLANNED = "PLANNED"
NOT_READY = "NOT_READY"
AVAILABLE = "AVAILABLE"  # a real adapter exists; the tenant has not connected it
INTEGRATION_REQUIRED = "INTEGRATION_REQUIRED"  # no adapter exists yet, so there is nothing to connect

# Map lanes, left to right. Purely presentational ordering.
LANE_CUSTOMER, LANE_FRONT, LANE_CORE, LANE_OPERATIONS, LANE_GROWTH, LANE_SYSTEMS = 0, 1, 2, 3, 4, 5

_FRONT_GROUPS = {"Customer-facing"}
# Capability groups shown in the "Money & growth" lane; everything else that is
# not customer-facing is "Operations". Keyed on the vocabulary's own group labels.
_GROWTH_GROUPS = {"Finance", "Marketing", "Data & insight", "Other"}

# Display cap for system nodes per capability, so the map stays legible.
_MAX_SYSTEMS_PER_CAPABILITY = 2


# --------------------------------------------------------------------------
# Inputs (plain data — everything below `load_inputs` is pure)
# --------------------------------------------------------------------------


@dataclass
class ProviderInfo:
    provider_key: str
    display_name: str
    implementation_status: str
    capabilities: list[str]
    connection_status: str | None  # tenant's own IntegrationConnection.status


@dataclass
class VerticalInfo:
    key: str
    name: str
    description: str | None
    status: str
    capabilities: list[str]
    enabled: bool


@dataclass
class WebsiteInfo:
    exists: bool = False
    website_id: str | None = None
    published: bool = False
    has_draft: bool = False
    page_slugs: list[str] = field(default_factory=list)


@dataclass
class BuilderInputs:
    journey: dict[str, Any] | None
    blueprint: dict[str, Any] | None  # {id, version, status}
    sections: dict[str, dict]  # section_key -> data
    claims: list[dict[str, Any]]
    requirements: list[Any]  # recommendation_service._CapabilityRequirement
    recommendations: list[dict[str, Any]]
    providers: list[ProviderInfo]
    verticals: list[VerticalInfo]
    website: WebsiteInfo
    workforce: WorkforceStatusReport
    lead_count: int
    module_counts: dict[str, int]  # canonical capability key -> data item count
    organization_name: str | None = None  # the company name given at sign-up


# --------------------------------------------------------------------------
# Pure derivations
# --------------------------------------------------------------------------


def _clip(text: str, n: int = 90) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _section_text(data: dict | None) -> str | None:
    """Best-effort one-line summary of a Blueprint section's JSON payload —
    first non-empty string value."""
    if not data:
        return None
    for v in data.values():
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            return ", ".join(x for x in v if x.strip())
    return None


def _claim_text(claims: list[dict], section_key: str) -> str | None:
    for c in claims:
        if c["section_key"] == section_key and c["status"] != ClaimStatus.REJECTED.value:
            v = c.get("value")
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _section_or_claim_text(inp: BuilderInputs, key: BlueprintSectionKey) -> str | None:
    return _section_text(inp.sections.get(key.value)) or _claim_text(inp.claims, key.value)


def _claim_statement(claim: dict[str, Any] | None) -> str | None:
    """What the user (or Klaros) actually said, for the evidence quote. A
    per-capability flag claim (`value: true`) is quoted by its name."""
    if not claim:
        return None
    value = claim.get("value")
    if value is True:
        return _clip(str(claim.get("key", "")).split(".")[-1].replace("_", " ").strip(), 160) or None
    if value is None or value is False:
        return None
    return _clip(str(value), 160)


def _requirement_why(definition: CapabilityDefinition, m: dict[str, Any], vertical_names: dict[str, str]) -> str:
    """A sentence grounded in the actual evidence — the user's own words where we
    have them, otherwise the source that introduced it. Never invented."""
    quote = next((e["statement"] for e in m["evidence"] if e.get("kind") == "blueprint_claim" and e.get("statement")), None)
    if m["source"] == "blueprint":
        if quote and quote.strip().lower() != definition.label.lower():
            return f"You listed “{quote}” as something the business needs."
        return f"You listed {definition.label.lower()} as something the business needs."
    vname = vertical_names.get(m.get("source_vertical_key") or "", None)
    return (
        f"Included with the {vname} module you enabled." if vname else "Suggested by an industry module enabled for this business."
    )


def _next_step(
    definition: CapabilityDefinition, providers: list[ProviderInfo], readiness: str, detail: str
) -> dict[str, Any]:
    """What the user can actually do next — never a fake action."""
    real = [p for p in providers if p.implementation_status == ProviderImplementationStatus.REAL.value]
    if readiness == CONNECTED:
        return {"text": "Nothing to do — connected.", "route": None}
    if readiness == READY:
        return {"text": f"Open {definition.label}.", "route": definition.native_route}
    if readiness == NOT_CONNECTED and real:
        return {"text": f"Connect {', '.join(p.display_name for p in real[:2])} in Integrations.", "route": "/settings/integrations"}
    if readiness == NOT_CONNECTED:
        return {"text": "Connect an AI workforce — the integration isn't available yet.", "route": "/workforce"}
    if readiness == CONFIGURATION_REQUIRED:
        return {"text": detail, "route": definition.native_route}
    return {"text": "Integration adapter required — Klaros has no module or integration for this yet.", "route": None}


def derive_requirements(inp: BuilderInputs) -> list[dict[str, Any]]:
    """One entry per canonical capability, merged across every source that
    states it, each carrying the evidence that explains *why*."""
    claims_by_id = {c["id"]: c for c in inp.claims}
    vertical_names = {v.key: v.name for v in inp.verticals}
    merged: dict[str, dict[str, Any]] = {}

    for req in inp.requirements:
        key = canonical_key(req.capability_key) or req.capability_key
        entry = merged.get(key)
        evidence: list[dict[str, Any]] = []
        for ev in req.based_on:
            if ev.get("kind") == "blueprint_claim":
                claim = claims_by_id.get(ev.get("claim_id"))
                evidence.append(
                    {
                        "kind": "blueprint_claim",
                        "claim_id": ev.get("claim_id"),
                        "section_key": ev.get("section_key"),
                        "statement": _claim_statement(claim),
                    }
                )
            elif ev.get("kind") == "blueprint_section":
                evidence.append(
                    {
                        "kind": "blueprint_section",
                        "section_key": ev.get("section_key"),
                        "statement": "Listed under Required Capabilities in your Blueprint",
                    }
                )
            elif ev.get("kind") == "vertical_extension":
                evidence.append({"kind": "vertical_extension", "vertical_key": ev.get("vertical_key")})
        if entry is None:
            merged[key] = {
                "required": bool(req.required),
                "confidence": req.confidence,
                "evidence": evidence,
                "why": req.why,
                "source": "blueprint" if req.required else "industry_module",
                "source_vertical_key": req.source_vertical_key,
            }
        else:
            entry["required"] = entry["required"] or bool(req.required)
            entry["confidence"] = max(entry["confidence"], req.confidence)
            entry["evidence"].extend(evidence)
            if req.required and entry["source"] != "blueprint":
                entry["source"] = "blueprint"
                entry["why"] = req.why

    rec_by_cap: dict[str, list[dict]] = {}
    for rec in inp.recommendations:
        rec_by_cap.setdefault(canonical_key(rec["capability_key"]) or rec["capability_key"], []).append(rec)

    out: list[dict[str, Any]] = []
    for key, m in merged.items():
        definition = describe(key)
        providers = _providers_for(key, inp.providers)
        readiness, detail = _readiness(definition, providers, inp)
        recs = rec_by_cap.get(key, [])
        cap_rec = next((r for r in recs if r["type"] == RecommendationType.CAPABILITY.value), None)
        out.append(
            {
                "key": key,
                "label": definition.label,
                "group": definition.group,
                "description": definition.description,
                "required": m["required"],
                "why": _requirement_why(definition, m, vertical_names),
                "next_step": _next_step(definition, providers, readiness, detail),
                "technical_why": m["why"],
                "source": m["source"],
                "confidence": m["confidence"],
                "evidence": m["evidence"],
                "klaros_support": definition.klaros_support,
                "native_route": definition.native_route,
                "readiness": readiness,
                "readiness_detail": detail,
                "workforce_addressable": definition.workforce_addressable,
                "providers": [_provider_view(p) for p in providers],
                "recommendation_id": cap_rec["id"] if cap_rec else None,
                # Every recommendation row (capability / integration / tool) whose
                # raw capability key canonicalises to this requirement — so a
                # screen can group by requirement even though the engine keys
                # rows by the raw phrase each source used.
                "recommendation_ids": sorted(r["id"] for r in recs),
                "recommendation_status": cap_rec["status"] if cap_rec else None,
            }
        )

    group_rank = {g: i for i, g in enumerate(GROUP_ORDER)}
    out.sort(key=lambda r: (group_rank.get(r["group"], len(GROUP_ORDER)), not r["required"], r["label"]))
    return out


def _providers_for(key: str, providers: list[ProviderInfo]) -> list[ProviderInfo]:
    matches = [p for p in providers if _provider_serves(p.capabilities, key)]
    rank = {
        ProviderImplementationStatus.REAL.value: 0,
        ProviderImplementationStatus.WEBHOOK_NORMALIZER.value: 1,
        ProviderImplementationStatus.STUB.value: 2,
    }
    matches.sort(key=lambda p: (rank.get(p.implementation_status, 3), p.display_name))
    return matches


def integration_state(implementation_status: str, connection_status: str | None) -> str:
    """THE single derivation of an integration's state, used by Requirements, the Business Map,
    Launch Readiness and the Integration Center alike: a provider without a real adapter is
    PLANNED; only a REAL adapter with a CONNECTED tenant connection is CONNECTED; an errored
    connection needs configuration; anything else is simply not connected (the Integration
    Center labels that "available" because a real adapter exists)."""
    if implementation_status != ProviderImplementationStatus.REAL.value:
        return PLANNED
    if connection_status == ConnectionStatus.CONNECTED.value:
        return CONNECTED
    if connection_status == ConnectionStatus.ERROR.value:
        return CONFIGURATION_REQUIRED
    return NOT_CONNECTED


def _provider_state(p: ProviderInfo) -> str:
    return integration_state(p.implementation_status, p.connection_status)


def _provider_view(p: ProviderInfo) -> dict[str, Any]:
    return {
        "provider_key": p.provider_key,
        "display_name": p.display_name,
        "implementation_status": p.implementation_status,
        "state": _provider_state(p),
        "adapter_required": p.implementation_status != ProviderImplementationStatus.REAL.value,
    }


def _readiness(
    definition: CapabilityDefinition, providers: list[ProviderInfo], inp: BuilderInputs
) -> tuple[str, str]:
    if definition.key == "website":
        if inp.website.published:
            return READY, "Your website is published."
        if inp.website.exists:
            return CONFIGURATION_REQUIRED, "A website draft exists but is not published yet."
        return CONFIGURATION_REQUIRED, "No website has been generated yet."
    if definition.workforce_addressable and definition.klaros_support == "INTEGRATION" and not providers:
        # Served by the AI workforce boundary rather than a catalog provider.
        if inp.workforce.status.value == CONNECTED:
            return CONNECTED, "Handled by your connected AI workforce."
        return NOT_CONNECTED, "Needs an AI workforce or communication provider — none is connected."
    if definition.klaros_support == "NATIVE":
        return READY, "Klaros has a built-in module for this."
    if definition.klaros_support == "INTEGRATION":
        if any(_provider_state(p) == CONNECTED for p in providers):
            return CONNECTED, "A connected integration provides this."
        if any(p.implementation_status == ProviderImplementationStatus.REAL.value for p in providers):
            return NOT_CONNECTED, "Klaros can connect a provider for this — not connected yet."
        return PLANNED, "No ready-made integration is available yet."
    # PLANNED in the vocabulary — but a real provider may still exist.
    if any(_provider_state(p) == CONNECTED for p in providers):
        return CONNECTED, "A connected integration provides this."
    if any(p.implementation_status == ProviderImplementationStatus.REAL.value for p in providers):
        return NOT_CONNECTED, "Klaros can connect a provider for this — not connected yet."
    return PLANNED, "Klaros has no module or integration for this yet — it is on the roadmap, not available today."


# --- Business Map ---------------------------------------------------------


def derive_business_map(inp: BuilderInputs, requirements: list[dict[str, Any]]) -> dict[str, Any]:
    """A generic capability/system/actor graph. Nodes and edges are derived
    only from requirements, recommendations, blueprint sections and live
    state; the same inputs always produce identical output."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def node(nid: str, kind: str, label: str, lane: int, **kw: Any) -> None:
        nodes.setdefault(nid, {"id": nid, "kind": kind, "label": label, "lane": lane, "state": None, "sublabel": None, "why": None, "route": None, "planned": False, **kw})

    def edge(src: str, dst: str, kind: str) -> None:
        if src in nodes and dst in nodes and src != dst:
            edges.setdefault((src, dst, kind), {"source": src, "target": dst, "kind": kind})

    audience = _section_or_claim_text(inp, BlueprintSectionKey.CUSTOMERS)
    node(
        "actor:customers", "actor", "Your customers", LANE_CUSTOMER,
        sublabel=_clip(audience, 80) if audience else None,
        why="Who the business serves, from your Business Blueprint." if audience else "Everyone who buys from or enquires with the business.",
        state=READY,
    )
    business_name = _section_text(inp.sections.get(BlueprintSectionKey.IDENTITY.value)) or "your business"
    node(
        "core:klaros", "core", "Klaros", LANE_CORE,
        sublabel="Business brain & operating system",
        why=f"Klaros holds the record of {_clip(business_name, 60)} and coordinates everything around it.",
        state=READY,
    )
    model = _section_or_claim_text(inp, BlueprintSectionKey.BUSINESS_MODEL)
    node(
        "outcome:revenue", "outcome", "Revenue", LANE_SYSTEMS,
        sublabel=_clip(model, 80) if model else None,
        why="How the business earns money, from your Business Blueprint." if model else "Where the business earns money.",
        state=READY,
    )
    edge("core:klaros", "outcome:revenue", "earns")

    cap_ids: dict[str, str] = {}
    front_caps: list[str] = []
    for r in requirements:
        nid = f"cap:{r['key']}"
        cap_ids[r["key"]] = nid
        lane = (
            LANE_FRONT
            if r["group"] in _FRONT_GROUPS
            else LANE_GROWTH
            if r["group"] in _GROWTH_GROUPS
            else LANE_OPERATIONS
        )
        node(
            nid, "capability", r["label"], lane,
            sublabel="Required" if r["required"] else "Suggested",
            why=r["description"], state=r["readiness"], route=r["native_route"],
            planned=r["readiness"] == PLANNED, capability_key=r["key"], required=r["required"],
        )
        if lane == LANE_FRONT:
            front_caps.append(nid)
            edge("actor:customers", nid, "reaches you via")
            edge(nid, "core:klaros", "sends data")
        else:
            edge("core:klaros", nid, "routes to")

    # Capability-to-capability dependencies (only where both are required here).
    for r in requirements:
        for dep in describe(r["key"]).depends_on:
            if dep in cap_ids:
                edge(cap_ids[r["key"]], cap_ids[dep], "depends on")

    # Customers with no front door of their own still connect to the core.
    if not front_caps:
        edge("actor:customers", "core:klaros", "reaches you via")

    # Outside parties some capabilities exchange data with.
    for r in requirements:
        party = describe(r["key"]).external_party
        if party:
            pid = f"actor:{re.sub(r'[^a-z0-9]+', '_', party.lower()).strip('_')}"
            node(pid, "actor", party, LANE_SYSTEMS, why=f"An outside party behind “{r['label']}”.", state=r["readiness"], planned=r["readiness"] == PLANNED)
            edge(pid, cap_ids[r["key"]], "sends data")
            edge("core:klaros", pid, "routes to")

    # Systems: the best catalog providers per capability.
    # Only providers Klaros can genuinely connect (a REAL adapter) appear on the map.
    # Planned/stub providers are shown on the Recommendations screen, labelled as such;
    # drawing them here would make the architecture look wired up when it is not.
    for r in requirements:
        for p in [p for p in r["providers"] if not p["adapter_required"]][:_MAX_SYSTEMS_PER_CAPABILITY]:
            sid = f"sys:{p['provider_key']}"
            node(
                sid, "system", p["display_name"], LANE_SYSTEMS,
                sublabel="Integration adapter required" if p["adapter_required"] else None,
                why=f"A catalogued provider for “{r['label']}”.",
                state=AVAILABLE if p["state"] == NOT_CONNECTED else p["state"],
                route="/settings/integrations", planned=p["state"] == PLANNED,
                provider_key=p["provider_key"],
            )
            edge(cap_ids[r["key"]], sid, "fulfilled by")

    # AI workforce (shown only if some capability could be served by one).
    if any(r["workforce_addressable"] for r in requirements):
        wf_state = (
            CONNECTED
            if inp.workforce.status.value == CONNECTED
            else CONFIGURATION_REQUIRED
            if inp.workforce.status.value == CONFIGURATION_REQUIRED
            else NOT_CONNECTED
            if inp.workforce.adapter_implemented
            else INTEGRATION_REQUIRED
        )
        node(
            "workforce:ai", "workforce", "AI workforce", LANE_FRONT,
            sublabel=f"{inp.workforce.provider.capitalize()} · external platform",
            why=inp.workforce.message, state=wf_state, route="/workforce",
            planned=not inp.workforce.adapter_implemented,
        )
        edge("actor:customers", "workforce:ai", "talks to")
        edge("workforce:ai", "core:klaros", "sends data")
        edge("core:klaros", "workforce:ai", "triggers")

    ordered_nodes = sorted(nodes.values(), key=lambda n: (n["lane"], n["kind"], n["label"]))
    ordered_edges = sorted(edges.values(), key=lambda e: (e["source"], e["target"], e["kind"]))
    return {
        "nodes": ordered_nodes,
        "edges": ordered_edges,
        "lanes": ["Customers", "Front door", "Klaros", "Operations", "Money & growth", "Systems & partners"],
    }


# --- Next actions / launch -------------------------------------------------


def pending_decisions(inp: BuilderInputs, requirements: list[dict[str, Any]]) -> int:
    """Recommendations that genuinely need the user to decide: each integration
    option, and any capability that is merely *suggested*. A capability the user
    stated as required needs no accept/reject, and name-matched tool rows are
    reference material, not decisions."""
    req_by_rec: dict[str, dict[str, Any]] = {}
    for r in requirements:
        for rid in r.get("recommendation_ids", []):
            req_by_rec[rid] = r
    n = 0
    for rec in inp.recommendations:
        if rec["status"] != "PROPOSED":
            continue
        if rec["type"] == RecommendationType.INTEGRATION.value:
            n += 1
        elif rec["type"] == RecommendationType.CAPABILITY.value:
            req = req_by_rec.get(rec["id"])
            if req is not None and not req["required"]:
                n += 1
    return n


def derive_next_actions(inp: BuilderInputs, requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []

    def add(aid: str, title: str, detail: str, state: str, route: str | None = None, *, kind: str = "link", **extra: Any) -> None:
        actions.append({"id": aid, "title": title, "detail": detail, "state": state, "route": route, "kind": kind, **extra})

    # 1. Industry modules whose registry capabilities overlap the requirements.
    req_keys = {r["key"] for r in requirements}
    context_text = " ".join(
        t.lower()
        for t in (
            _section_or_claim_text(inp, BlueprintSectionKey.IDENTITY),
            _section_or_claim_text(inp, BlueprintSectionKey.INDUSTRY),
            _section_or_claim_text(inp, BlueprintSectionKey.BUSINESS_MODEL),
        )
        if t
    )
    for v in inp.verticals:
        if v.enabled or v.status == VerticalExtensionStatus.DISABLED.value:
            continue
        overlap = {canonical_key(c) for c in v.capabilities} & req_keys
        name_mentioned = bool(v.name) and v.name.lower() in context_text or v.key.replace("_", " ") in context_text
        if not overlap and not name_mentioned:
            continue
        if v.capabilities:
            add(
                f"enable-module:{v.key}",
                f"Enable the {v.name} module",
                "Adds ready-made records and screens for this kind of business, and lets your website show them.",
                READY, None, kind="enable_vertical", vertical_key=v.key,
            )
        else:
            add(
                f"module-planned:{v.key}",
                f"{v.name} module",
                "Klaros recognises this kind of business, but there is no domain module for it yet — requirements below come from your Blueprint only.",
                PLANNED, None,
            )

    # 2. Website.
    if not inp.website.exists:
        add("website:generate", "Generate your website", "Klaros builds it from your Business Blueprint — you review before anything goes live.", READY, "/website")
    elif not inp.website.published:
        add("website:publish", "Review and publish your website", "A draft exists. Publishing makes it live and opens your lead form to the public.", CONFIGURATION_REQUIRED, "/website")

    # 3. Connect real providers for required capabilities.
    for r in requirements:
        if not r["required"]:
            continue
        real = [p for p in r["providers"] if not p["adapter_required"]]
        if real and not any(p["state"] == CONNECTED for p in real):
            p = real[0]
            add(f"connect:{p['provider_key']}", f"Connect {p['display_name']}", f"Covers “{r['label']}”.", NOT_CONNECTED, "/settings/integrations")

    # 4. Empty data behind a capability that depends on you adding records.
    for key, count in sorted(inp.module_counts.items()):
        if count == 0 and key in req_keys:
            d = describe(key)
            add(f"add-data:{key}", f"Add your {d.label.lower()}", "It is empty, so your website and team have nothing to show yet.", CONFIGURATION_REQUIRED, d.native_route)

    # 5. Pending recommendation decisions.
    pending = pending_decisions(inp, requirements)
    if pending:
        add("recommendations:review", f"Review {pending} recommendation{'s' if pending != 1 else ''}", "Accept the ones you want; nothing is connected automatically.", READY, "/business/recommendations")

    # 6. AI workforce.
    if any(r["workforce_addressable"] for r in requirements):
        if inp.workforce.status.value == CONNECTED:
            add("workforce:connected", "AI workforce connected", inp.workforce.message, CONNECTED, "/workforce")
        else:
            add("workforce:connect", "Connect your AI workforce", inp.workforce.message, NOT_CONNECTED, "/workforce")

    # 7. Capabilities Klaros cannot serve yet — shown honestly, not hidden.
    for r in requirements:
        if r["required"] and r["readiness"] == PLANNED:
            add(f"planned:{r['key']}", f"{r['label']} — not available yet", r["readiness_detail"], PLANNED, None)

    # 8. Operate.
    if inp.website.published:
        add("operate:leads", "Watch incoming leads", f"{inp.lead_count} lead{'s' if inp.lead_count != 1 else ''} so far.", READY, "/leads")

    order = {CONFIGURATION_REQUIRED: 0, READY: 1, NOT_CONNECTED: 2, PLANNED: 4, CONNECTED: 5}
    actions.sort(key=lambda a: (order.get(a["state"], 3)))
    return actions


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def derive_launch(inp: BuilderInputs, requirements: list[dict[str, Any]]) -> dict[str, Any]:
    """Launch readiness: every item has a status and a reason, and nothing is READY
    unless the underlying state really is. `required` items block launch; optional
    ones are shown honestly but never block."""
    required = [r for r in requirements if r["required"]]
    items: list[dict[str, Any]] = []

    def item(key: str, label: str, status: str, detail: str, *, required_: bool = True, route: str | None = None) -> None:
        items.append(
            {"key": key, "label": label, "status": status, "detail": detail, "required": required_, "route": route,
             "done": status in (READY, CONNECTED), "optional": not required_}
        )

    bp_ok = inp.blueprint is not None and inp.blueprint["status"] == "ACTIVE"
    item("blueprint", "Business Blueprint", READY if bp_ok else NOT_READY,
         "Your Blueprint is confirmed." if bp_ok else "Confirm your Blueprint before launching.", route="/business/blueprint")
    item("requirements", "Requirements", READY if requirements else NOT_READY,
         f"{_plural(len(requirements), 'requirement')} derived from your Blueprint." if requirements else "No requirements yet — list what the business needs in the Blueprint.",
         route="/business/requirements")

    pending = pending_decisions(inp, requirements)
    if not inp.recommendations:
        item("recommendations", "Recommendations reviewed", NOT_READY, "Recommendations haven't been generated yet.", route="/business/recommendations")
    elif pending:
        item("recommendations", "Recommendations reviewed", CONFIGURATION_REQUIRED, f"{_plural(pending, 'recommendation')} still to review.", route="/business/recommendations")
    else:
        item("recommendations", "Recommendations reviewed", READY, "Every recommendation has been decided.", route="/business/recommendations")

    if inp.website.published:
        item("website", "Website", READY, "Your website is published.", route="/website")
    elif inp.website.exists:
        item("website", "Website", CONFIGURATION_REQUIRED, "A draft exists but isn't published — publish it to go live.", route="/website")
    else:
        item("website", "Website", NOT_READY, "No website has been generated yet.", route="/website")

    # Records an industry module's capability depends on (e.g. a directory that is empty).
    req_keys = {r["key"] for r in required}
    for key, count in sorted(inp.module_counts.items()):
        d = describe(key)
        if key in req_keys:
            if count > 0:
                item(f"data:{key}", d.label, READY, f"{_plural(count, 'record')} configured.", route=d.native_route)
            else:
                item(f"data:{key}", d.label, NOT_READY, "Nothing has been added yet, so your website and team have nothing to show.", route=d.native_route)

    # Real providers needed by required capabilities.
    unconnected = [r for r in required if r["klaros_support"] == "INTEGRATION" and r["readiness"] == NOT_CONNECTED and r["providers"]]
    if unconnected:
        names = ", ".join(sorted({p["display_name"] for r in unconnected for p in r["providers"] if not p["adapter_required"]}) or {r["label"] for r in unconnected})
        item("integrations", "Integrations", NOT_READY, f"Not connected yet: {names}.", route="/settings/integrations")
    elif any(r["klaros_support"] == "INTEGRATION" for r in required):
        item("integrations", "Integrations", READY, "Every integration your requirements need is connected.", route="/settings/integrations")

    # Required capabilities Klaros cannot serve yet.
    gaps = [r for r in required if r["readiness"] == PLANNED]
    if gaps:
        item("capabilities", "Required capabilities", NOT_READY,
             f"{_plural(len(gaps), 'required capability', 'required capabilities')} not available in Klaros yet: {', '.join(r['label'] for r in gaps)}.",
             route="/business/requirements")
    elif required:
        item("capabilities", "Required capabilities", READY, "Every required capability has a module or integration.", route="/business/requirements")

    # AI workforce: only relevant if some requirement could use one; never blocks the website.
    if any(r["workforce_addressable"] for r in requirements):
        if inp.workforce.status.value == CONNECTED:
            item("workforce", "AI workforce", READY, "Your AI workforce is connected.", required_=False, route="/workforce")
        else:
            why = (
                "Connect your workforce provider before launching customer communication."
                if inp.workforce.adapter_implemented
                else "Connect Halla to enable AI customer communication — the integration isn't available yet, so this can't be done today."
            )
            item("workforce", "AI workforce", INTEGRATION_REQUIRED if not inp.workforce.adapter_implemented else NOT_CONNECTED,
                 why, required_=False, route="/workforce")

    blocking = [i for i in items if i["required"] and not i["done"]]
    return {
        "items": items,
        "ready": not blocking,
        "verdict": READY if not blocking else NOT_READY,
        "blocking": [f'{i["label"]}: {i["detail"]}' for i in blocking],
        "launched": inp.website.published,
    }


def derive_stages(inp: BuilderInputs, requirements: list[dict[str, Any]], launch: dict[str, Any]) -> list[dict[str, Any]]:
    j = inp.journey["status"] if inp.journey else None
    past_discovery = j in {"BLUEPRINT_REVIEW", "BLUEPRINT_ACTIVE", "RECOMMENDATIONS_READY", "COMPLETED"}
    blueprint_done = j in {"BLUEPRINT_ACTIVE", "RECOMMENDATIONS_READY", "COMPLETED"}
    recs_done = j in {"RECOMMENDATIONS_READY", "COMPLETED"}
    flags = [
        ("idea", "Idea", inp.journey is not None, "/business"),
        ("discovery", "Discovery", past_discovery, "/business/discovery"),
        ("blueprint", "Blueprint", blueprint_done, "/business/blueprint"),
        ("requirements", "Requirements", blueprint_done and bool(requirements), "/business/requirements"),
        ("recommendations", "Recommendations", recs_done, "/business/recommendations"),
        ("map", "Business Map", recs_done and bool(requirements), "/business/map"),
        ("website", "Website", inp.website.published, "/website"),
        ("workforce", "AI workforce", inp.workforce.status.value == CONNECTED, "/workforce"),
        ("launch", "Launch", launch["launched"] and launch["ready"], "/business/home#launch"),
        ("operate", "Operate", launch["launched"] and inp.lead_count > 0, "/business/home"),
    ]
    stages = []
    current_set = False
    for key, label, done, route in flags:
        state = "done" if done else ("current" if not current_set else "todo")
        if not done:
            current_set = True
        stages.append({"key": key, "label": label, "state": state, "route": route})
    return stages


def derive_progress(inp: BuilderInputs, requirements: list[dict[str, Any]], launch: dict[str, Any]) -> list[dict[str, Any]]:
    """The compact "launch progress" ladder shown on Business Home."""
    j = inp.journey["status"] if inp.journey else None
    past_discovery = j in {"BLUEPRINT_REVIEW", "BLUEPRINT_ACTIVE", "RECOMMENDATIONS_READY", "COMPLETED"}
    blueprint_done = j in {"BLUEPRINT_ACTIVE", "RECOMMENDATIONS_READY", "COMPLETED"}
    arch_done = j in {"RECOMMENDATIONS_READY", "COMPLETED"} and bool(requirements)
    connected = sorted(
        {p["display_name"] for r in requirements for p in r["providers"] if p["state"] == CONNECTED}
    )
    wanted = any(r["workforce_addressable"] for r in requirements)

    def row(key: str, label: str, status: str, detail: str, route: str | None = None) -> dict[str, Any]:
        return {"key": key, "label": label, "status": status, "detail": detail, "route": route}

    rows = [
        row("discovery", "Discovery", READY if past_discovery else NOT_READY, "Completed." if past_discovery else "Not completed.", "/business/discovery"),
        row("blueprint", "Blueprint", READY if blueprint_done else NOT_READY, "Confirmed." if blueprint_done else "Not confirmed.", "/business/blueprint"),
        row("requirements", "Requirements", READY if requirements else NOT_READY, _plural(len(requirements), "requirement") + "." if requirements else "None yet.", "/business/requirements"),
        row("architecture", "Architecture", READY if arch_done else NOT_READY, "Business Map generated." if arch_done else "Generate recommendations to complete it.", "/business/map"),
        row(
            "website", "Website",
            READY if inp.website.published else CONFIGURATION_REQUIRED if inp.website.exists else NOT_READY,
            "Published." if inp.website.published else "Draft, not published." if inp.website.exists else "Not generated.", "/website",
        ),
    ]
    if wanted:
        wf = inp.workforce
        rows.append(row(
            "workforce", "AI workforce",
            CONNECTED if wf.status.value == CONNECTED else INTEGRATION_REQUIRED if not wf.adapter_implemented else NOT_CONNECTED,
            "Connected." if wf.status.value == CONNECTED else "Integration required — Halla adapter not available yet." if not wf.adapter_implemented else "Not connected.",
            "/workforce",
        ))
    rows.append(row("integrations", "Integrations", READY if connected else NOT_CONNECTED,
                    f"{len(connected)} connected" + (f": {', '.join(connected)}." if connected else "."), "/settings/integrations"))
    rows.append(row("launch", "Launch", READY if launch["ready"] else NOT_READY,
                    "Ready to launch." if launch["ready"] else f"{_plural(len(launch['blocking']), 'thing')} left.", "/business/home#launch"))
    return rows


def derive_overview(inp: BuilderInputs) -> dict[str, Any]:
    requirements = derive_requirements(inp) if inp.blueprint else []
    launch = derive_launch(inp, requirements)
    return {
        "journey": inp.journey,
        "blueprint": inp.blueprint,
        "business": {
            "name": inp.organization_name or _section_text(inp.sections.get(BlueprintSectionKey.IDENTITY.value)),
            "summary": _section_or_claim_text(inp, BlueprintSectionKey.IDENTITY),
            "industry": _section_or_claim_text(inp, BlueprintSectionKey.INDUSTRY),
            "business_model": _section_or_claim_text(inp, BlueprintSectionKey.BUSINESS_MODEL),
            "customers": _section_or_claim_text(inp, BlueprintSectionKey.CUSTOMERS),
        },
        "requirements": requirements,
        "business_map": derive_business_map(inp, requirements) if requirements else {"nodes": [], "edges": [], "lanes": []},
        "next_actions": derive_next_actions(inp, requirements) if inp.blueprint else [],
        "launch": launch,
        "progress": derive_progress(inp, requirements, launch) if inp.blueprint else [],
        "stages": derive_stages(inp, requirements, launch),
        "website": {
            "exists": inp.website.exists,
            "published": inp.website.published,
            "has_draft": inp.website.has_draft,
            "pages": inp.website.page_slugs,
        },
        "workforce": workforce_view(inp.workforce),
        "operations": {
            "lead_count": inp.lead_count,
            "modules": [
                {"key": k, "label": describe(k).label, "count": c, "route": describe(k).native_route}
                for k, c in sorted(inp.module_counts.items())
            ],
        },
        "modules": [
            {"key": v.key, "name": v.name, "status": v.status, "enabled": v.enabled, "has_domain_module": bool(v.capabilities)}
            for v in inp.verticals
        ],
    }


def workforce_view(report: WorkforceStatusReport) -> dict[str, Any]:
    return {
        "provider": report.provider,
        "status": report.status.value,
        "adapter_implemented": report.adapter_implemented,
        "message": report.message,
        "agent_id": report.agent_id,
        "capabilities": [{"key": c.key, "label": c.label, "description": c.description} for c in report.capabilities],
    }


# --------------------------------------------------------------------------
# Loading (the only part that touches the database)
# --------------------------------------------------------------------------


class BusinessBuilderService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        blueprints: BusinessBlueprintService,
        recommendations: RecommendationService,
        websites: WebsiteService,
        verticals: VerticalExtensionService,
    ) -> None:
        self._session_factory = session_factory
        self._blueprints = blueprints
        self._recommendations = recommendations
        self._websites = websites
        self._verticals = verticals

    async def get_overview(self, tenant_id: uuid.UUID, *, journey: dict[str, Any] | None) -> dict[str, Any]:
        return derive_overview(await self.load_inputs(tenant_id, journey=journey))

    async def get_workforce(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        return workforce_view(await get_workforce_integration().get_status(tenant_id))

    async def load_inputs(self, tenant_id: uuid.UUID, *, journey: dict[str, Any] | None) -> BuilderInputs:
        blueprint = await self._blueprints.get_active(tenant_id)
        sections: dict[str, dict] = {}
        claims: list[dict[str, Any]] = []
        requirements: list[Any] = []
        recs: list[dict[str, Any]] = []
        blueprint_view = None
        if blueprint is not None:
            blueprint_view = {"id": str(blueprint.id), "version": blueprint.version, "status": blueprint.status}
            sections = {s.section_key: (s.data or {}) for s in await self._blueprints.list_sections(tenant_id, blueprint.id)}
            claims = [
                {"id": str(c.id), "section_key": c.section_key, "key": c.key, "value": c.value, "status": c.status}
                for c in await self._blueprints.list_claims(tenant_id, blueprint.id)
            ]
            requirements, _ = await self._recommendations.collect_capability_requirements(tenant_id, blueprint)
            recs = [
                {
                    "id": str(r.id),
                    "type": r.type,
                    "capability_key": r.capability_key,
                    "status": r.status,
                    "run_id": str(r.run_id),
                }
                for r in await self._recommendations.list_recommendations(tenant_id, blueprint_id=blueprint.id)
                if r.status != "SUPERSEDED"
            ]

        providers, verticals_all = await self._load_catalog_and_connections(tenant_id)
        enabled_keys = {v.key for v in verticals_all if v.enabled}
        website = await self._load_website(tenant_id)
        lead_count = await self._count_leads(tenant_id)
        module_counts = await self._module_counts(tenant_id, [v for v in verticals_all if v.key in enabled_keys])
        workforce = await get_workforce_integration().get_status(tenant_id)
        return BuilderInputs(
            journey=journey,
            blueprint=blueprint_view,
            sections=sections,
            claims=claims,
            requirements=requirements,
            recommendations=recs,
            providers=providers,
            verticals=verticals_all,
            website=website,
            workforce=workforce,
            lead_count=lead_count,
            module_counts=module_counts,
            organization_name=await self._organization_name(tenant_id),
        )

    async def _load_catalog_and_connections(self, tenant_id: uuid.UUID) -> tuple[list[ProviderInfo], list[VerticalInfo]]:
        # GLOBAL reference tables first (no tenant context needed)…
        async with self._session_factory() as session:
            catalog = list((await session.execute(select(IntegrationProviderCatalog))).scalars().all())
            registry = list((await session.execute(select(VerticalExtension))).scalars().all())
        # …then the tenant's own rows under tenant context.
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            conns = {
                c.provider: c.status
                for c in (
                    await session.execute(select(IntegrationConnection).where(IntegrationConnection.tenant_id == tenant_id))
                ).scalars()
            }
        enabled_ids = {l.vertical_extension_id for l in await self._verticals.list_enabled_for_organization(tenant_id)}
        providers = [
            ProviderInfo(p.provider_key, p.display_name, p.implementation_status, list(p.capabilities or []), conns.get(p.provider_key))
            for p in catalog
        ]
        verticals = [
            VerticalInfo(v.key, v.name, v.description, v.status, [c for c in (v.capabilities or []) if isinstance(c, str)], v.id in enabled_ids)
            for v in registry
        ]
        verticals.sort(key=lambda v: v.key)
        return providers, verticals

    async def _load_website(self, tenant_id: uuid.UUID) -> WebsiteInfo:
        website = await self._websites.get_website(tenant_id)
        if website is None:
            return WebsiteInfo()
        versions = await self._websites.list_versions(tenant_id, website.id)
        published = any(v.status == WebsiteVersionStatus.PUBLISHED.value for v in versions) and website.current_published_version_id is not None
        has_draft = any(v.status == WebsiteVersionStatus.DRAFT.value for v in versions)
        slugs: list[str] = []
        latest = next(iter(sorted(versions, key=lambda v: v.version, reverse=True)), None)
        if latest is not None:
            try:
                spec = await self._websites.load_specification(tenant_id, latest.id)
                slugs = [p.slug for p in spec.pages]
            except Exception:  # noqa: BLE001 - a malformed spec must not break the overview
                slugs = []
        return WebsiteInfo(True, str(website.id), published, has_draft, slugs)

    async def _organization_name(self, tenant_id: uuid.UUID) -> str | None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            org = (await session.execute(select(Organization).where(Organization.id == tenant_id))).scalar_one_or_none()
            return org.name if org is not None else None

    async def _count_leads(self, tenant_id: uuid.UUID) -> int:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            return int(
                (await session.execute(select(func.count()).select_from(Lead).where(Lead.tenant_id == tenant_id))).scalar_one()
            )

    async def _module_counts(self, tenant_id: uuid.UUID, enabled: list[VerticalInfo]) -> dict[str, int]:
        """For each enabled industry module capability that has a registered
        website data provider, how many records currently exist — how the
        Business Builder knows a directory is still empty without knowing
        what kind of directory it is."""
        counts: dict[str, int] = {}
        for v in enabled:
            for raw in v.capabilities:
                result = await resolve_data_source(tenant_id, raw, {"limit": 100})
                if result.get("resolved", True) is False:
                    continue
                items = result.get("items", [])
                counts[canonical_key(raw)] = counts.get(canonical_key(raw), 0) + len(items)
        return counts
