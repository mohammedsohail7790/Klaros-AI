# Klaros AI — Phase 1 Implementation Plan

Covers item X. Scope derived from the actual dependency graph (KLAROS_IMPLEMENTATION_DEPENDENCY_GRAPH.md), not assumed: Phase 1 is the smallest set of reference-data foundations that every later user-facing phase (Discovery, Blueprint, Recommendation, Marketplace, Agents, Website, domain verticals) needs to exist first, and nothing more.

## 1.1 — `VerticalExtension` / `DomainDefinition` registry

- **Why now:** every later domain-plugin mechanism (Recommendation Engine plugins, conditional router mounting, Blueprint vertical sub-schemas) reads this table; it must exist before any of them can be built without a hardcoded stand-in.
- **Current state:** no domain/vertical registry exists; nothing plugs in today.
- **Target state:** `DomainDefinition` table (id, name, capability-contribution list, status) + `OrganizationVerticalExtension` join table, seeded with `medical_tourism` and `dropshipping` rows (status=BETA, since their own table families ship later in Phases 9-10) so the registry mechanism itself can be exercised and tested well before the vertical's actual data model exists.
- **Files/subsystems affected:** new models/migration only; no existing code changes.
- **Dependencies:** Phase 0 complete (RLS-on-day-one applies to `OrganizationVerticalExtension`, since it's tenant-scoped; `DomainDefinition` itself is reference data, no RLS).
- **Migration:** 2 new tables.
- **Tests:** registry CRUD tests; a static-analysis CI check (extends 0.1's pipeline) asserting no application code branches on a vertical name string outside this registry's own lookup path.
- **Risks:** low — pure reference data with no consumers yet in Phase 1 itself.
- **Rollback:** drop tables; nothing depends on them yet.
- **Done when:** the registry exists, is seeded, and the static-analysis guard is live in CI.

## 1.2 — `IntegrationProviderCatalog`

- **Why now:** the Integration Marketplace UI work (later phase) and the Recommendation Engine both need a tenant-independent source of truth for provider status; building either without it first would mean hardcoding provider lists inline.
- **Current state:** no catalog table; `frontend/app/settings/integrations/page.tsx` hardcodes its provider groupings client-side today.
- **Target state:** `IntegrationProviderCatalog` table, seeded with the verified-real/stub/webhook-normalizer status for every currently-integrated provider (Stripe/QuickBooks/Google Calendar=REAL; Xero/Google Ads/Meta Ads/GBP/ServiceTitan/Jobber=STUB; Angi/Thumbtack/Nextdoor=WEBHOOK_NORMALIZER), read-only from the existing `/settings/integrations` page (no UI redesign required in Phase 1 — that's a later-phase task; Phase 1 only stands up the data source).
- **Files/subsystems affected:** new model/migration/seed data; new `MANAGE_INTEGRATIONS_CATALOG` permission (RBAC addition).
- **Dependencies:** Phase 0 RBAC groundwork (permission enum extension pattern already exists; adding one is low-risk).
- **Migration:** 1 new table + seed data migration.
- **Tests:** seed-data-matches-verified-reality test (a regression guard: if a stub provider's adapter is later actually implemented, this test should be updated deliberately, not silently drift).
- **Risks:** seed data going stale if an adapter's real/stub status changes without updating the catalog — mitigated by the regression test above.
- **Rollback:** drop table; the existing hardcoded frontend grouping continues to work unaffected (Phase 1 does not modify the frontend).
- **Done when:** the catalog exists, is seeded correctly, and the regression test is green.

## 1.3 — Tool/capability catalog exposure (read-only API over the existing `ToolRegistry`)

- **Why now:** the Agent Runtime (Phase 7) needs a way to enumerate available tools for `AgentToolPermission` configuration UI, and the Recommendation Engine needs to know what capabilities exist to recommend — both need this before they can be built, and it requires zero changes to `ToolRegistry` itself, only a new read endpoint over its existing tool metadata.
- **Current state:** the 233 registered tools are enumerable in-process (`ToolRegistry` holds them) but there is no API exposing this list.
- **Target state:** `GET /api/v1/tools/catalog` (new, thin, read-only) — returns each tool's name, description, required_permission, tenant_scoped flag, counts_toward_ai_usage flag, sourced directly from the live `ToolRegistry`, not a duplicated/denormalized table (avoiding a second source of truth for something that already has one).
- **Files/subsystems affected:** one new thin router file; zero changes to `ToolRegistry`/`base.py`/`factory.py`.
- **Dependencies:** none beyond Phase 0's CI to test it.
- **Migration:** none — this is a read projection over existing in-memory registry state, not a new table.
- **Tests:** endpoint returns exactly the 233 currently-registered tools' metadata, cross-checked against direct `ToolRegistry` introspection.
- **Risks:** none — additive, read-only, no new table.
- **Rollback:** remove the router; zero impact elsewhere.
- **Done when:** the endpoint is live and its test passes.

## 1.4 — Credential/connection abstraction confirmation (no new code, verification-only)

- **Why now:** later phases (Integration Marketplace UI, Recommendation-driven integration connects) will build on top of the existing `IntegrationConnection`/`credential_store.py` — Phase 1 confirms this abstraction is sufficient as-is rather than assuming it, closing the loop on item K's "verify existing integrations" requirement before Phase 5 builds on it.
- **Current state:** verified real and correctly shaped (Fernet encryption, boot-time key-sanity guard) in this review's own backend research pass.
- **Target state:** unchanged — this Phase 1 item is a formal sign-off, not a code change, recording that no new credential/connection abstraction is needed.
- **Files/subsystems affected:** none.
- **Dependencies:** none.
- **Migration:** none.
- **Tests:** none new — existing credential-store tests (if any exist in the 183-file suite) are the coverage; confirming their existence is itself the Phase 1 task if not already known (**UNKNOWN — REQUIRES VERIFICATION**, to be closed during Phase 0's CI stand-up when the full test suite is actually run and enumerated).
- **Risks:** none.
- **Rollback:** not applicable.
- **Done when:** explicitly recorded (this document constitutes that record) that `IntegrationConnection`/`credential_store.py` require no changes to support Phase 5+.

## Phase 1 exit criteria

1.1-1.3 shipped and tested; 1.4 signed off. No Discovery/Blueprint/Recommendation/Agent/Website *feature* code ships in Phase 1 — only the reference-data and read-projection foundations those features will build on, keeping Phase 1 genuinely small and independently shippable rather than a re-badged "everything foundational" phase.
