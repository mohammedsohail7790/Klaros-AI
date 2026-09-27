# Phase 1 Implementation Log — Registry Foundation Layer

Covers `KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md` items 1.1–1.4: the
`VerticalExtension` registry, the `IntegrationProviderCatalog`, and a
read-only Tool Catalog API layered over the existing `ToolRegistry`. No
Phase 2+ feature (Discovery/Blueprint/Recommendation/Agent/Website/domain
verticals/MCP) was started.

## Baseline

- Branch: `main`. Alembic head at session start: `0040` (Phase 0's RLS
  audit-mode migration), confirmed via `alembic current`.
- `git status --short` at session start showed only pre-existing,
  already-uncommitted Phase-0-and-earlier changes (see the conversation's
  opening `gitStatus` snapshot) — nothing Phase-1-related was staged yet.
- No Docker available in this sandbox (`docker --version` → command not
  found), consistent with all three prior Phase 0 passes. Real PostgreSQL
  was provisioned the same documented way: the `pgserver` PyPI package
  (already cached; installed via `pip install pgserver`), a disposable,
  loopback-only, genuine **PostgreSQL 16.2** instance with the `vector`
  extension available (confirmed `CREATE EXTENSION vector` succeeds).
  Kept alive for the session via a small keepalive script
  (`backend/_pg_keepalive.py`, not part of the deliverable diff) so
  Alembic and pytest could both connect to the same instance across
  multiple tool calls; `DATABASE_URL` pointed at its Unix socket
  (`postgresql+asyncpg://postgres@/klaros?host=/private/tmp/klaros_pg1`).
- Baseline full-suite run (before writing any Phase 1 test, but after the
  Phase 1 model/migration/service/API code existed — no Phase-1-specific
  tests are collected until they're added, so this run is a true
  zero-new-tests regression check) against real Postgres:
  **1 failed, 1415 passed, 12 skipped** — the failure is
  `tests/test_openai_realtime_voice_service.py::test_voice_stream_route_dispatches_to_realtime_engine_when_configured`,
  the exact pre-existing async event-loop test-infrastructure issue
  documented in `PHASE_0_CI_FINAL_VALIDATION.md` and named explicitly in
  this task's own instructions. Confirmed NOT a Phase 1 regression: it
  reproduces identically with only Phase 1's non-test code present, and
  is unrelated to any Phase 1 table/route (a WebSocket/Realtime voice
  path).

## Architecture references consulted

Read in full before implementing: `KLAROS_FINAL_DOMAIN_MODEL.md`,
`KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md`, `KLAROS_FINAL_API_ARCHITECTURE.md`,
`KLAROS_FINAL_INTEGRATION_MODEL.md`, `KLAROS_FINAL_SECURITY_MODEL.md`,
`KLAROS_FINAL_DATABASE_ARCHITECTURE.md`, `KLAROS_FINAL_TESTING_ARCHITECTURE.md`,
`KLAROS_ARCHITECTURE_RECONCILIATION.md` (specifically items #2/#3/#8 —
`MANAGE_BLUEPRINT` naming, `MANAGE_INTEGRATIONS` vs.
`MANAGE_INTEGRATIONS_CATALOG` kept separate, the two distinct
`ConnectionStatus`-shaped enums kept separate). Plus direct code reads of
`app/integrations/adapters.py` (to verify real/stub/webhook-normalizer
status first-hand rather than trusting the docs alone), `app/tools/registry.py`,
`app/tools/base.py`, `app/api/tool_deps.py`, `app/api/v1/integrations.py`,
`app/api/v1/tools.py`, `app/models/rbac.py`, `app/db/base.py`,
`alembic/versions/0040_rls_audit_mode_tier1.py`, `tests/test_postgres_rls_audit_mode.py`.

Where the task prompt's illustrative field names/route shapes diverged
from the actual docs (e.g. the prompt suggested an
`AVAILABLE/COMING_SOON/STUB/CUSTOM` catalog status enum), the actual docs
were followed instead: `IntegrationProviderCatalog.implementation_status`
is `REAL/STUB/WEBHOOK_NORMALIZER` per `KLAROS_FINAL_INTEGRATION_MODEL.md`,
with the tenant-derived rendered status (`NOT_CONNECTED/CONNECTED/ERROR/...
/STUB/WEBHOOK_NORMALIZER`) computed separately by
`derive_tenant_status()`.

## Models added

- `app/models/vertical_extension.py`
  - `VerticalExtension` (global reference data, no `tenant_id`): `id`,
    `key` (unique), `name`, `description`, `version`, `status`
    (`ACTIVE`/`BETA`/`DISABLED`), `capabilities` (JSON list),
    `configuration_schema` (JSON, nullable), `extra_metadata` (JSON,
    nullable), `created_at`/`updated_at`.
  - `OrganizationVerticalExtension` (tenant-scoped, `TenantScopedMixin`):
    `tenant_id`, `vertical_extension_id` (FK), `enabled_at`, `enabled_by`.
    Unique `(tenant_id, vertical_extension_id)`.
- `app/models/integration_catalog.py`
  - `IntegrationProviderCatalog` (global reference data, no `tenant_id`):
    `provider_key` (unique), `display_name`, `category`,
    `implementation_status` (`REAL`/`STUB`/`WEBHOOK_NORMALIZER`),
    `auth_shape` (`OAUTH2`/`API_KEY`/`WEBHOOK`), `description`,
    `recommended_for_verticals` (JSON array of `VerticalExtension.key`
    strings), `health_check_strategy_ref`.
- Both registered in `app/models/__init__.py` (so Alembic's `env.py`
  autoloads them via `from app.models import *`).
- RBAC: `app/models/rbac.py` — added `Permission.MANAGE_INTEGRATIONS_CATALOG`.
  Automatically granted to `OWNER`/`ADMIN` (both derive from
  `_ALL_PERMISSIONS`); no other role's explicit permission set was
  touched. See "Known limitations" — there is no cross-tenant
  platform-admin role distinct from a tenant's own OWNER/ADMIN in this
  codebase, so this is the closest existing approximation, not a true
  platform-wide boundary.

## Migrations

- `alembic/versions/0041_vertical_extension_registry.py` (`0040 -> 0041`):
  creates `vertical_extensions` (no RLS — global) and
  `organization_vertical_extensions` (RLS **audit-mode**: `ENABLE ROW
  LEVEL SECURITY` + permissive `USING (true)` policy, same maturity level
  as Phase 0's `0040` migration — not `FORCE`/enforced, since the rest of
  the system's RLS rollout hasn't reached enforcement yet either). Seeds
  `medical_tourism` and `dropshipping` (`status=BETA`) from
  `app/data/vertical_extension_seed.py`.
- `alembic/versions/0042_integration_provider_catalog.py` (`0041 -> 0042`):
  creates `integration_provider_catalog` (no RLS — global). Seeds 12
  providers from `app/data/integration_provider_catalog_seed.py`.
- Both migrations are guarded to no-op their RLS DDL on non-PostgreSQL
  dialects (SQLite dev/test fallback), matching `0040`'s existing pattern.
- Verified against real Postgres: `alembic upgrade head` (0040→0042)
  clean; `alembic downgrade -1` twice then `alembic upgrade head` clean
  (reversibility); `alembic current` reports `0042 (head)`.
- Indexes/constraints: `uq_vertical_extensions_key` (unique `key`),
  `uq_org_vertical_extension_tenant_vertical` (unique
  `(tenant_id, vertical_extension_id)`),
  `uq_integration_provider_catalog_provider_key` (unique `provider_key`),
  plus non-unique indexes on `status`, `tenant_id`,
  `vertical_extension_id`, `category`, `implementation_status`.

## Services

- `app/services/vertical_extension_service.py` (`VerticalExtensionService`):
  create/get-by-key/list(optionally filtered by status)/set_status for
  `VerticalExtension`; enable-for-organization/list-enabled-for-organization/
  is-enabled-for-organization for `OrganizationVerticalExtension`. No HTTP
  API sits over this in Phase 1 (see API section) — the plan's own
  §1.1 scope is "new models/migration only."
- `app/services/integration_catalog_service.py`
  (`IntegrationCatalogService`): create/get-by-provider-key/list(optional
  category filter)/update for `IntegrationProviderCatalog`. Also exports
  `derive_tenant_status(implementation_status, connection_status)`, the
  pure function enforcing `KLAROS_FINAL_INTEGRATION_MODEL.md`'s hard
  constraint (STUB/WEBHOOK_NORMALIZER can never render CONNECTED).
- `app/services/tool_catalog_service.py` (`list_tool_catalog`): a
  stateless function, not a class with its own persistence — reads
  `ToolRegistry._tools` directly (see "Tool Registry Integration" below)
  and returns `ToolCatalogEntry` dataclasses. No DB access, no side
  effects.
- `app/data/vertical_extension_seed.py` / `app/data/integration_provider_catalog_seed.py`:
  the single source of truth for seed data, imported by both the
  Alembic migration and the test suite's seed-data-matches-reality
  guards, so the two can never silently drift from each other.

## APIs

| Method | Path | Auth | Scope | Purpose |
|---|---|---|---|---|
| GET | `/api/v1/tools/catalog` | JWT (any authenticated user) | platform-wide, read-only | Full, unfiltered metadata for every registered `Tool` (name, description, required_permission, tenant_scoped, counts_toward_ai_usage) |
| GET | `/api/v1/integrations/catalog` | JWT | platform-wide catalog, merged with the caller's own tenant connection state | List `IntegrationProviderCatalog` entries with a derived, per-caller-tenant `tenant_status` |
| GET | `/api/v1/integrations/catalog/{provider_key}` | JWT | same as above | Single catalog entry |
| PUT | `/api/v1/integrations/catalog/{provider_key}` | JWT + `MANAGE_INTEGRATIONS_CATALOG` | platform-wide (writes the global catalog row, not tenant-scoped) | Create-or-update a catalog entry (admin curation) |

No API was added for `VerticalExtension`/`OrganizationVerticalExtension`
— `KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md` §1.1 explicitly scopes 1.1 to
"new models/migration only; no existing code changes," and no route for
it appears in `KLAROS_FINAL_API_ARCHITECTURE.md` either. The service
layer exists and is fully tested so a later phase's Recommendation
Engine / conditional router mounting can consume it without any Phase 1
rework.

`GET /tools/catalog` is additive alongside the pre-existing `GET /tools`
(role-filtered `list_available()`, missing `tenant_scoped`/
`counts_toward_ai_usage`) — deliberately not replacing it, since it
serves a different purpose ("what can I invoke" vs. "what exists").

`GET/PUT /integrations/catalog...` are sub-routes of the existing
`/integrations` router (`app/api/v1/integrations.py`), not a new
top-level router, matching `KLAROS_FINAL_API_ARCHITECTURE.md`'s
"Duplication check" decision. No `app/api/v1/router.py` changes were
needed — both `tools.router` and `integrations.router` were already
registered.

## Authorization

- Tool catalog: any authenticated user (JWT only) — it is platform
  metadata ("what tools exist"), not tenant data.
- Integration catalog reads: any authenticated user; the response merges
  in only the CALLING tenant's own `IntegrationConnection` state (proven
  by `tests/test_integration_provider_catalog.py::test_catalog_read_never_depends_on_another_tenants_connection`).
- Integration catalog writes (`PUT`): `MANAGE_INTEGRATIONS_CATALOG`,
  proven both for rejection (a `TECHNICIAN`-role user, granted no such
  permission, gets 403) and success (an `OWNER`, which has it via
  `_ALL_PERMISSIONS`).
- Tool execution governance is completely untouched — the tool catalog
  route never calls `ToolRegistry.execute()`, so the existing
  9-step-plus-policy pipeline (`registry.py`) sees zero new code in its
  path.

## Tool Registry Integration — sync mechanism, drift handling

**Derived live, read-through — no persisted/synced copy.**
`list_tool_catalog()` re-reads `ToolRegistry`'s own in-memory `_tools`
dict at request time; there is no table, no cache, no background sync job,
and therefore no drift to detect, because the catalog and the registry
are the same data by construction. This was chosen over a synced table
specifically because `KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md` §1.3 states
the files-affected constraint as "one new thin router file; zero changes
to `ToolRegistry`/`base.py`/`factory.py`" — a live read achieves that with
no additional moving parts. `registry._tools` (a "private" attribute) is
read directly rather than adding a new public accessor method to
`ToolRegistry`, specifically to honor that zero-changes constraint;
`list_available()` (the one existing public accessor) is role-filtered,
which is the wrong shape for a catalog that must enumerate every
registered tool regardless of the caller's role.
`tests/test_tool_catalog_api.py::test_catalog_matches_registry_introspection_exactly`
cross-checks the HTTP response against direct registry introspection
field-by-field.

## Integration Architecture — relation to `IntegrationConnection` and adapters

`IntegrationProviderCatalog` is tenant-independent reference data
describing what a provider IS (its `implementation_status`, `auth_shape`,
category) — it holds no credentials and no per-tenant state whatsoever.
`IntegrationConnection` (existing, unchanged) remains the sole runtime
source of a tenant's own connection/credential state. The two are joined
only by the shared string `provider_key`/`provider`, never merged into
one table or one status enum. `derive_tenant_status()` is the one place
that reads both: it takes `implementation_status` (catalog) and a
tenant's own `connection_status` (or `None`) and returns the rendered
status, enforcing that a `STUB`/`WEBHOOK_NORMALIZER` provider can never
render `CONNECTED` regardless of what any `IntegrationConnection.status`
says — proven directly in
`tests/test_integration_provider_catalog.py::test_derive_tenant_status_stub_never_renders_connected`
and `..._webhook_normalizer_never_renders_connected`. Seed data's
real/stub/webhook-normalizer classification was cross-checked directly
against `app/integrations/adapters.py`'s adapter implementations (not
copied blindly from the planning docs) — see
`app/data/integration_provider_catalog_seed.py`'s docstring for the
per-provider citation.

## Vertical registry design

Tenant-independent `VerticalExtension` rows are the only thing core code
may query to discover vertical capability — never a compiled-in switch
statement (`app/models/vertical_extension.py`'s module docstring states
this explicitly). `VerticalExtensionService.is_enabled_for_organization()`
is the documented, registry-backed lookup shape any future caller
(Recommendation Engine, conditional router mounting) must use. No caller
exists yet in Phase 1 — by design, since no consumer ships until a later
phase. A grep-based static-analysis CI guard
(`tests/test_vertical_extension_no_hardcoding_guard.py`) asserts no file
under `app/` (excluding the registry's own model/service/seed files)
branches on the literal strings `"medical_tourism"`/`"dropshipping"` in an
`if`/`elif` condition — this is the "static-analysis CI check... asserting
no application code branches on a vertical name string outside this
registry's own lookup path" item named in
`KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md` §1.1, wired into CI automatically
since it's an ordinary pytest test picked up by the existing
`python -m pytest -q` CI step (no `.github/workflows/ci.yml` change
needed).

## Seed/catalog data

- `vertical_extensions`: `medical_tourism`, `dropshipping`, both
  `status=BETA`, `version="0.0.0-registry-only"` — exactly as
  `KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md` §1.1 specifies, so the
  registry mechanism is exercisable before either vertical's real table
  family ships.
- `integration_provider_catalog`: 12 providers, exactly the set
  `KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md` §1.2 names — `stripe`,
  `quickbooks`, `google_calendar` (`REAL`); `xero`, `google_ads`,
  `meta_ads`, `google_business_profile`, `servicetitan`, `jobber`
  (`STUB`); `angi`, `thumbtack`, `nextdoor` (`WEBHOOK_NORMALIZER`).
  Deliberately excludes `twilio`/`sendgrid`/`anthropic`/`openai`/other
  AI-provider adapters — those are platform-level, single-shared-
  credential providers (configured once via Settings/env), not providers
  a tenant discovers/connects via this marketplace catalog; including
  them was judged out of the reconciled plan's named scope.

## Tests

New files (all pass against real PostgreSQL 16.2 via `pgserver`):
- `backend/tests/test_vertical_extension_registry.py` — 13 tests:
  create/get, key uniqueness, not-found, seed-matches-expected, status
  filter, status transitions, enable-for-org + lookup, per-tenant
  uniqueness, tenant isolation of `OrganizationVerticalExtension`, enable
  for unknown vertical rejected.
- `backend/tests/test_postgres_vertical_extension_rls_audit_mode.py` — 3
  tests (real-Postgres-only): RLS enabled (not forced) on
  `organization_vertical_extensions`, audit mode is a real no-op today,
  and the two global tables (`vertical_extensions`,
  `integration_provider_catalog`) correctly carry **no** RLS policy at
  all.
- `backend/tests/test_vertical_extension_no_hardcoding_guard.py` — 3
  tests: the grep-based static-analysis guard itself, plus two
  self-tests proving the guard's regex actually detects a hardcoded
  branch (so the guard isn't vacuously passing).
- `backend/tests/test_integration_provider_catalog.py` — 15 tests:
  create/get, uniqueness, not-found (get + update), seed-matches-
  verified-reality, category filter, the three `derive_tenant_status`
  cases, unauthenticated rejection, authenticated read returns seeded
  entries with correct derived `tenant_status`, write requires
  `MANAGE_INTEGRATIONS_CATALOG` (403 for `TECHNICIAN`), write succeeds
  for `OWNER` and is visible platform-wide to a second, unrelated
  tenant, and the "catalog read never depends on another tenant's
  connection" property.
- `backend/tests/test_tool_catalog_api.py` — 5 tests: unauthenticated
  rejection, catalog matches direct `ToolRegistry` introspection exactly
  (name/description/required_permission/tenant_scoped/
  counts_toward_ai_usage for every entry), catalog is unfiltered by
  caller role (unlike `GET /tools`), and two explicit proofs that
  reading the catalog never executes a tool or writes an `AuditLog` row
  (one over HTTP, one calling the service function directly).

**Total new tests: 37. All pass.** Combined with the full existing suite, see Regression below.

## Regression

Final full-suite run, real PostgreSQL, with all Phase 1 code and tests in
place: **1 failed, 1452 passed, 12 skipped, 16 warnings in 580.76s**
(1452 = the original 1415 passing tests + 37 new Phase 1 tests; skipped
count unchanged at 12; failed count unchanged at 1).

- **PRE-EXISTING** (not NEW, not caused by Phase 1):
  `tests/test_openai_realtime_voice_service.py::test_voice_stream_route_dispatches_to_realtime_engine_when_configured`
  — an async event-loop/cross-loop `asyncpg` teardown error in the voice
  Realtime WebSocket bridge test, reproduced identically both before any
  Phase 1 test existed and after, and matching the exact failure
  signature and location documented in prior Phase 0 verification passes
  (`PHASE_0_CI_FINAL_VALIDATION.md`). Zero relationship to any Phase 1
  table, model, service, or route.
- **NEW**: none.
- **ENVIRONMENT**: none beyond the already-declared Docker unavailability
  (not a test failure — no Docker-dependent test exists in this suite).
- **FLAKY**: none observed (the suite was run three times in this
  session — once as the true baseline, once isolated to the 5 new Phase
  1 test files, once as the final full run — with fully consistent
  results each time).

## CI

No `.github/workflows/ci.yml` changes were needed:
- New migrations are picked up automatically by the existing "Run Alembic
  migrations against a real Postgres" and "Alembic downgrade/re-upgrade
  smoke test" steps.
- New tests are picked up automatically by the existing
  `python -m pytest -q` step (no per-file wiring required).
- `pip-audit` step already exists (non-blocking) — re-run locally (see
  Security below).
- No frontend files were touched, so `frontend-checks` needed no changes
  either (confirmed via `git status --short -- frontend`, which shows
  only pre-existing, already-uncommitted changes from before this
  session began).

## PostgreSQL validation

- Real PostgreSQL 16.2 (`pgserver`, with `pgvector` available) — not
  SQLite, not a mock.
- `alembic upgrade head`: 0040 → 0041 → 0042, clean.
- `alembic downgrade -1` ×2 → `alembic upgrade head`: clean
  (reversibility of both new migrations, forward and back).
- `alembic current`: `0042 (head)`.
- Full backend suite against this real Postgres instance:
  **1415 passed, 1 failed (pre-existing), 12 skipped** before adding
  Phase 1 tests; **37 new Phase 1 tests, all passing**, run both in
  isolation and (see Regression) as part of the full suite.

## Docker validation

**BLOCKED — Docker unavailable in this sandbox** (`docker --version` →
command not found; `docker info` → command not found). Consistent with
all three prior Phase 0 passes. No Docker-build verification was
performed for either backend or frontend images; this is an environment
limitation, not a Phase 1 code issue — nothing in this diff touches
`Dockerfile`/`docker-compose.yml`.

## Security validation

- RBAC: verified via HTTP — a `TECHNICIAN` user (no
  `MANAGE_INTEGRATIONS_CATALOG`) gets 403 on the catalog write route; an
  `OWNER` succeeds.
- Tenant scope: verified `OrganizationVerticalExtension` isolation
  (Tenant A's enabled-vertical row invisible to Tenant B) and that a
  catalog read never depends on/exposes another tenant's
  `IntegrationConnection` state.
- Global scope: verified the two intentionally-global tables carry no
  RLS policy (there is no `tenant_id` to filter on) while the one
  genuinely tenant-scoped new table does, in audit mode, consistent with
  the rest of the codebase's current RLS maturity level.
- Secrets: no new table stores credentials; `IntegrationProviderCatalog`
  never has been given a credential-shaped column, and the catalog write
  endpoint's request/response schemas were checked to confirm neither
  exposes secret material of any kind.
- Tool governance: the tool catalog route never calls
  `ToolRegistry.execute()` — proven both over HTTP and via a direct
  service-function call that reading the catalog creates zero new
  `AuditLog` rows, meaning it cannot have entered the execute() pipeline
  at all, let alone bypassed its policy semantics.
- `pip-audit -r requirements.txt`: 2 findings, both the same pre-existing
  `ecdsa` (`PYSEC-2026-1325`) transitive-dependency advisory already
  present before this session (not introduced by Phase 1, and already
  non-blocking in CI per `ci.yml`'s own comment).
- Credential-isolation fix (pytest never loading `backend/.env`):
  unaffected — no changes to `app/core/config.py`'s `env_file=None`
  under-pytest behavior; confirmed the full suite still ran with only
  the CI-style env vars set explicitly (no `backend/.env` present/read).

## Files changed

New:
- `backend/app/models/vertical_extension.py`
- `backend/app/models/integration_catalog.py`
- `backend/app/data/__init__.py`
- `backend/app/data/vertical_extension_seed.py`
- `backend/app/data/integration_provider_catalog_seed.py`
- `backend/app/services/vertical_extension_service.py`
- `backend/app/services/integration_catalog_service.py`
- `backend/app/services/tool_catalog_service.py`
- `backend/alembic/versions/0041_vertical_extension_registry.py`
- `backend/alembic/versions/0042_integration_provider_catalog.py`
- `backend/tests/test_vertical_extension_registry.py`
- `backend/tests/test_postgres_vertical_extension_rls_audit_mode.py`
- `backend/tests/test_vertical_extension_no_hardcoding_guard.py`
- `backend/tests/test_integration_provider_catalog.py`
- `backend/tests/test_tool_catalog_api.py`
- `PHASE_1_IMPLEMENTATION_LOG.md` (this file)

Modified:
- `backend/app/models/__init__.py` (register the two new model modules)
- `backend/app/models/rbac.py` (add `MANAGE_INTEGRATIONS_CATALOG`)
- `backend/app/api/v1/integrations.py` (add the `/catalog` sub-routes)
- `backend/app/api/v1/tools.py` (add `GET /tools/catalog`)
- `backend/app/api/tool_deps_integrations.py` (add
  `get_integration_catalog_service`)

No other existing file was modified. `backend/app/api/v1/router.py` did
NOT need changes (both routers were already registered).

Not part of the deliverable diff (local-only scratch files used to run
real Postgres in this sandbox, removed before finishing):
`backend/_pg_keepalive.py`, `backend/_run_tests.sh`.

## Known limitations

1. **`MANAGE_INTEGRATIONS_CATALOG` is not a true platform-wide admin
   permission.** This codebase has no cross-tenant "platform admin" role
   distinct from a tenant's own OWNER/ADMIN. The permission is granted to
   OWNER/ADMIN within the existing per-organization RBAC model — the
   closest existing approximation, not a genuine platform-wide boundary.
   Any tenant's OWNER can currently write to the GLOBAL catalog. A real
   platform-admin concept, separate from any single tenant's role grants,
   is out of Phase 1's scope and is deferred.
2. RLS on `organization_vertical_extensions` is audit-mode only (matching
   the rest of the codebase's current RLS maturity), not enforced —
   consistent with Phase 0's own explicit staging, not a Phase 1
   shortfall.
3. The `pip-audit` `ecdsa` finding is pre-existing and unaddressed (also
   non-blocking in CI already); not a Phase 1 regression, not fixed here
   since dependency upgrades are out of this phase's scope.
4. No Docker build verification was possible in this sandbox.
5. `app/data/vertical_extension_seed.py` / `integration_provider_catalog_seed.py`
   are the single source of truth for seed data, but nothing currently
   enforces that a future manual edit to the Alembic migration's inline
   values (if someone bypassed the shared import) would be caught beyond
   the existing regression tests reading the same shared module — this is
   an acceptable, standard "shared constant" risk, not a gap unique to
   Phase 1.

## Deferred Phase 2+ work (explicitly NOT implemented)

Business Discovery, Business Blueprint, Requirements Engine,
Recommendation Engine, Agent Runtime (Agent/AgentVersion/AgentExecution/
AgentToolPermission), Website Builder, Medical Tourism functionality,
Dropshipping functionality, MCP, any new workflow/automation/event-bus/
integration-execution engine, any public marketplace UI, any
recommendation-consumption UI, any AI-generated business setup, any
frontend UI for verticals/agents/blueprint/integration-marketplace. No
`VerticalExtension`/`IntegrationProviderCatalog` consumer (Recommendation
Engine plugin, conditional router mounting) was built — only the registry
layer those future consumers will read from.

## Phase 2 readiness

Phase 1's stated exit criteria (1.1–1.3 shipped and tested; 1.4 signed
off) are satisfied:
- 1.1 (`VerticalExtension` registry): shipped, seeded, tested, static-
  analysis guard live (runs in CI via the existing pytest step).
- 1.2 (`IntegrationProviderCatalog`): shipped, seeded, tested, RBAC-gated
  write route live.
- 1.3 (Tool catalog): shipped, tested, proven read-only and drift-free by
  construction.
- 1.4 (credential/connection abstraction sign-off): confirmed — no
  changes were made or needed to `IntegrationConnection`/
  `credential_store.py`; existing credential-store test coverage was
  exercised unchanged as part of the full-suite regression run.

This is a statement that Phase 1's own foundations are in place — it is
NOT authorization to begin Phase 2, which was explicitly withheld for
this task.
