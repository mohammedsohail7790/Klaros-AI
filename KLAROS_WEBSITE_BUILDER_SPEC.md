# Klaros — Website Builder Specification

Status: design proposal. No page-generation, rendering, or publishing code exists anywhere in the current repo (confirmed absent by grep across `backend/app` and `frontend/app`, both audit passes) — this is entirely net-new.

## 1. Rejected approach and why

Prompt → LLM → arbitrary generated code (React/HTML/JS) → deployed to production is rejected outright. Reasons: (a) unreviewable per-tenant code is an XSS/SSRF/supply-chain attack surface at multi-tenant SaaS scale that cannot be safely sandboxed cheaply; (b) it cannot be validated deterministically before publish, so "preview" would mean "hope"; (c) it duplicates the exact anti-pattern `KLAROS_DO_NOT_BUILD_YET.md` §7 flags for agent-generated workflow code. See also ADR-005 in `KLAROS_ARCHITECTURAL_DECISIONS.md`.

## 2. Pipeline

```
Business Blueprint (existing, per KLAROS_BUSINESS_BLUEPRINT_SPEC.md)
  → Website Requirements (derived: page list, form list, brand-token candidates
    — computed deterministically from IDENTITY/PRODUCTS_SERVICES/CHANNELS/
    GEOGRAPHY/CUSTOMER_JOURNEY sections, not a fresh LLM call per field)
  → Site Specification (JSON: {pages: [...], nav: [...], brand_ref})
  → Page Specification (per page: {sections: [...], seo_metadata})
  → Component Specification (per section: {component_type (enum, from the
    FIXED REGISTRY below), props (schema-validated per component_type)})
  → Content Specification (LLM-generated copy, but ONLY filling the declared
    text slots of an already-chosen component — the model never chooses
    which component renders, only what text goes in it)
  → Brand Specification (colors/fonts/logo — derived from Blueprint IDENTITY +
    optional tenant logo upload via the existing object-storage path)
  → Generated Site (a fully resolved SiteVersion row — JSON data, not code)
  → Preview (renders the SiteVersion through the SAME component registry that
    serves production, on a preview-only route/subdomain, so preview and
    production are never different code paths)
  → Validation (schema validation + automated checks, §5)
  → Human Approval (PublishRequest — reuses ApprovalRequest's existing
    shape/status model, KLAROS_BUSINESS_BLUEPRINT_SPEC.md-style reuse)
  → Publish (SiteVersion.status = PUBLISHED; served by a dedicated
    website-runtime service, isolated origin — KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md §2)
```

## 3. Component registry

A fixed, versioned set of React components Klaros engineers write and review — e.g. `HeroSection`, `ServiceGrid`, `ProviderDirectoryCard` (Medical Tourism extension), `ProductGrid` (Dropshipping extension), `LeadForm`, `AppointmentBookingWidget`, `TestimonialCarousel`, `ContactBlock`, `FAQAccordion`. Each component declares: its `props` JSON schema (validated server-side before a `Component` spec row can be saved), its text slots (what Content Specification is allowed to fill), and whether it needs a live data fetch (e.g. `AppointmentBookingWidget` calls the existing `appointments.py` availability endpoint client-side) versus being pure SSG content. Vertical extensions (`KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md`) may register additional components into the same registry (e.g. Medical Tourism's `ProviderDirectoryCard`), but every component — core or vertical — goes through the same review-and-registry process; none are generated.

## 4. Entity model

`Site` (`id`, `tenant_id`, `blueprint_id`, `domain`, `status`), `SiteVersion` (`site_id`, `version`, `spec` JSONB — the fully resolved Site/Page/Component tree, immutable once created, enabling instant rollback to any prior version by flipping which version is `PUBLISHED`), `Page`, `PageVersion`, `Section`, `Component` (registry key + props, not code), `Navigation`, `Form` (`target_endpoint` — always one of a small allowlist of existing public endpoints, see §6; never a free-text URL), `SEOMetadata`, `Domain` (custom domain binding, DNS verification state), `PublishingState` (`DRAFT`/`PREVIEW`/`PUBLISHED`/`UNPUBLISHED`), `BrandSettings` (`site_id`, colors, fonts, logo `storage_key`).

## 5. Validation (must pass before `PublishRequest` can even be created)

Schema validation of the full `SiteVersion.spec` against the registry's component schemas; every `Form.target_endpoint` resolves to an allowlisted, real backend route; every internal `Navigation` link resolves to a `Page` that exists in this `SiteVersion`; brand color-contrast/accessibility check (WCAG AA minimum, computed deterministically, not LLM-judged); no `Component` references a registry key that doesn't exist (protects against a stale spec referencing a since-removed component after a registry version bump).

## 6. Forms → existing lead infrastructure (not a new pipeline)

`Form.target_endpoint` is restricted to a small allowlist: the existing `public_leads.py` endpoint (rate-limited, unauthenticated, verified real) for contact/intake forms, and — for Medical Tourism/appointment-booking use cases — a new but equally narrow `public_appointments` availability-and-request endpoint that itself creates a `Lead` first (never writes an `Appointment` directly from an unauthenticated public form; the existing qualification step must run first, matching the current `convertLeadAndBook` pattern's atomicity, audit §15). Field mapping (`Form.field_map`: which visual field → which `Lead` field, e.g. `procedure_of_interest` → `Lead.service_requested`) is per-`Form` configuration, not new backend logic — the same `Lead` model already handles arbitrary `service_requested`/`description` text. This means: `Website form → Public Lead API (existing) → Lead → Qualification (existing) → CRM → Agent (new, optional, per KLAROS_AI_AGENT_ARCHITECTURE.md) → Appointment (existing appointments.py + Google Calendar sync)` — every arrow except "Agent" is an existing, unmodified code path.

## 7. Rendering approach decision

SSG (Next.js static generation) for the default case — marketing/lead-gen pages are read-heavy, cacheable, and minimizing server-side surface area per generated site reduces the multi-tenant attack surface. A thin server-rendered path is used only for genuinely dynamic widgets (e.g. Medical Tourism's live provider-availability widget), which call existing read-only, already-tenant-scoped public endpoints client-side rather than requiring the whole page to be dynamically rendered. Full arbitrary React/Next.js generation and a pure JSON/DSL-only (no code at all) approach were both considered; the chosen middle ground — fixed component registry + JSON spec — gets the safety of a DSL with the visual quality of hand-built components, at the cost of requiring Klaros engineers (not tenants or AI) to add new component types over time. This is an explicit, accepted tradeoff (see ADR-005).

## 8. Isolation

Published sites are served by a separate `website-runtime` deploy unit on a separate origin from the main Klaros app (`KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md` §2) — a compromised or misconfigured generated site (e.g. a tenant-uploaded logo file that turns out to be malicious, or a future extension component with a bug) has no access to the Klaros app's own cookies, `sessionStorage` JWTs, or API surface beyond the same public endpoints any browser could already reach.

## 9. Migration impact

All new tables and a new deploy unit; zero changes to existing CRM/Lead/Appointment tables or endpoints beyond the one new narrow `public_appointments` endpoint (additive route file, not a modification to `public_leads.py`/`appointments.py`).

## 10. Failure modes

Generation produces an invalid spec → validation (§5) blocks `PublishRequest` creation, tenant sees which check failed, not a generic error. Publish succeeds but a form later breaks (e.g. `public_leads` rate limit hit during a traffic spike) → existing rate-limit/retry behavior applies unchanged; the website-runtime layer shows the form's own existing error-state UI (reusing `frontend/lib/api.ts`'s `ApiError` formatting pattern for the public-facing equivalent). Domain/DNS verification fails → `Domain.status=UNVERIFIED`, site remains reachable on the Klaros-assigned subdomain.

## Cross-references

`KLAROS_BUSINESS_BLUEPRINT_SPEC.md`, `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` (vertical components), `KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md` §2, `KLAROS_ARCHITECTURAL_DECISIONS.md` ADR-005, `KLAROS_DO_NOT_BUILD_YET.md` §7.
