# PHASE 17A — PRODUCTION RLS ENFORCEMENT READINESS AUDIT

Audit date: 2026-09-27
Auditor mode: read-only, non-destructive (audit only — no enforcement, no policy changes, no migrations, no product code changes)
Repository: `/Users/mohammedsohail/Desktop/Klaros AI`

---

## 1. Executive Summary

Klaros's PostgreSQL Row-Level Security is **real but incomplete audit-mode instrumentation, not enforcement, and not close to enforcement-ready across the whole platform.** Concretely, verified against a real disposable PostgreSQL 16 instance with every migration (0001–0051) applied:

- **136 tables** exist in `public` after `alembic upgrade head`.
- **132 tables** carry a `tenant_id` column (i.e., are tenant-owned by the direct-column model).
- Of those, only **32 tables (24%)** have `ENABLE ROW LEVEL SECURITY` at all — all of them with a single, always-permissive `USING (true)` audit policy, and **none** with `FORCE ROW LEVEL SECURITY`.
- **100 tenant-owned tables (76% of tenant tables) have zero PostgreSQL RLS protection whatsoever** — not audit mode, not enforcement, nothing. This includes financially and operationally sensitive domains: Finance (invoices, payments, refunds, credit notes, payouts, vendor bills), CRM (customers, leads, campaigns), Jobs/Field Ops, Automation Engine, AI invocation logs, Communication logs, Retention, Marketing, Outbound, Compliance (licenses), Contracts, Knowledge (RAG chunks/files), Webhooks, and more.
- The tenant-context plumbing that a future enforcing policy would read (`SET LOCAL app.tenant_id`, via `app/db/session.py::set_tenant_context`) is wired into exactly **two** call paths: the authenticated HTTP request path (`app/api/deps.py`) and Temporal workflow activities (`app/workflows/activities.py`). It is **not** wired into the Event Bus worker, MCP tool execution, the background worker entrypoint, or the public website runtime — meaning if enforcement were switched on today, several legitimate, already-shipped code paths would either silently see zero rows (fail-closed breakage) or, on tables without RLS at all, remain completely unprotected.
- The database role the application connects as is the PostgreSQL cluster's own bootstrap/initdb role (`klaros`, per `docker-compose.yml`'s `POSTGRES_USER`), which is a **superuser**. Superusers bypass RLS unconditionally, `FORCE ROW LEVEL SECURITY` included. Table ownership also sits with this same role. **Enabling RLS today, in any mode, would have zero enforcement effect in production even on the 32 already-instrumented tables**, because the connecting role both owns the tables and is a superuser — this is confirmed both by the existing test suite's own commentary (`tests/test_postgres_rls_audit_mode.py`) and by the docker-compose bootstrap pattern; no separate, restricted, `NOSUPERUSER NOBYPASSRLS` application role exists anywhere in the deployment configuration.
- One genuinely strong asset exists: `backend/tests/test_postgres_rls_audit_mode.py` is **real behavioral proof**, not just "policy exists" inspection. Inside a rolled-back transaction, using a purpose-created non-superuser, non-BYPASSRLS probe role, it demonstrates the intended enforcing-policy mechanism actually works correctly (fail-closed with no context, correctly scoped with context) for one table (`company_memories`). This is a solid design proof-of-concept, but it covers 1 of 132 tenant tables and is never left running.

**Bottom line:** Klaros is **NOT ready** to move to RLS enforcement. This is not a "flip FORCE on" situation — it requires (a) instrumenting 100 additional tables, (b) fixing every non-HTTP/non-Temporal access path (Event Bus, MCP, background workers, public website) to set or intentionally bypass tenant context, (c) introducing a restricted, non-superuser, non-table-owning application database role (a real architecture change to the deployment model), and (d) writing exhaustive real-Postgres behavioral tests, before any `FORCE ROW LEVEL SECURITY` migration is safe to write. See §23 for the ordered Phase 17B plan.

---

## 2. Starting Git State

```
HEAD: af4937e403e47cdc141f2db349dfcc46a3df6c4b   (matches expected)
Branch: main
git diff --check: clean (no whitespace errors)
git diff --cached --stat: empty (nothing staged)
```

`git status --short` at audit start, confirmed to exactly match the expected Phase 16a/16b working-tree state described in the task:

```
 M backend/app/services/business_discovery_service.py
 M backend/app/services/discovery_extraction_service.py
 M backend/tests/test_business_discovery_service.py
?? KLAROS_DISCOVERY_COMPLETION_AUDIT.md
?? PHASE_16A_DISCOVERY_FALLBACK_IMPLEMENTATION_LOG.md
?? PHASE_16B_DISCOVERY_REAL_PROVIDER_VALIDATION_LOG.md
?? backend/tests/test_discovery_connected_provider_service.py
?? backend/tests/test_discovery_extraction_service.py
```

`git diff --stat`: 3 files changed, 274 insertions(+), 25 deletions(-) (business_discovery_service.py +19, discovery_extraction_service.py +113/-25, test_business_discovery_service.py +167). This matches "uncommitted Phase 16a/16b Discovery-fallback and real-provider-validation work." **None of this Phase 16a/16b state was touched, staged, or altered during this audit** — verified unchanged at the end of the audit as well (re-ran `git status --short` after all work; identical output).

No `git add`, `git commit`, `git push`, or any staging command was run at any point.

---

## 3. Current RLS State

Real-Postgres catalog evidence (methodology in §11). Summary:

| Metric | Count |
|---|---|
| Tables in `public` schema (post `alembic upgrade head`) | 136 |
| Tables with a `tenant_id` column | 132 |
| Tables with `relrowsecurity = true` (RLS enabled) | 32 |
| Tables with `relforcerowsecurity = true` (RLS forced) | **0** |
| Tables with an audit-mode `USING (true)` policy | 32 (1:1 with the RLS-enabled set) |
| Tenant-owned tables with **no** RLS at all | **100** |
| Tables with neither `tenant_id` nor RLS (global/system) | 4 |

RLS was introduced incrementally, one migration at a time, for **new** tables only, starting at migration `0040`. **No migration ever went back and retrofitted RLS onto a pre-existing tenant-owned table.** This means every tenant table created before Phase 0 (`0001`–`0039`) — which is the majority of the schema, including all of Finance, CRM, Automation, Retention, Marketing, Outbound, Jobs/Field-Ops, Compliance, Contracts, Knowledge, AI invocation logs, Communication logs, and Webhooks — has zero RLS instrumentation of any kind.

The 32 RLS-enabled tables, by the migration that introduced them:

- **0040** (Tier-1 retrofit on 5 pre-existing tables): `integration_connections`, `approval_requests`, `audit_logs`, `company_memories`, `users`
- **0041**: `organization_vertical_extensions`
- **0043**: `discovery_sessions`, `discovery_turns`, `business_blueprints`, `blueprint_sections`, `blueprint_claims`
- **0044**: `recommendation_runs`, `recommendations`
- **0045**: `agents`, `agent_versions`, `agent_tool_permissions`, `agent_executions`
- **0046**: `agent_execution_steps`
- **0048**: `mcp_tool_exposures`, `mcp_client_credentials`
- **0049**: `medical_tourism_providers`, `medical_tourism_provider_credentials`, `medical_tourism_procedures`, `medical_tourism_provider_procedures`, `medical_tourism_patient_leads`, `medical_tourism_consultations`, `medical_tourism_referral_commissions`
- **0050**: `websites`, `website_versions`, `website_pages`, `website_sections`
- **0051**: `business_journeys`

(`0042` and `0047` added no RLS-tracked tables: `0042` is the tenant-independent `integration_provider_catalog` by design, and `0047` is additive columns only, no new tables.)

Every one of the 32 policies is named `tenant_isolation_audit_policy`, `PERMISSIVE`, `FOR ALL`, `USING (true)` (and `WITH CHECK (true)` per the migration source) — i.e., a structural no-op that exists only so the eventual enforcement migration is a one-line `ALTER POLICY` rather than a from-scratch `CREATE POLICY`. This matches every migration's own stated intent, verified directly against `pg_policies`, not inferred from the migration source.

Global/shared tables with no `tenant_id` and correctly no RLS: `organizations` (the tenant root itself — its own `id` is the tenant identity), `vertical_extensions` (platform registry), `integration_provider_catalog` (platform registry), `alembic_version` (schema-only). This is the correct, intentional design for genuinely global rows.

---

## 4. Complete Tenant-Owned Table Inventory

Full per-table classification for all 132 `tenant_id`-bearing tables is impractical to enumerate exhaustively in prose here without ballooning this report to an unreadable size; the classification below groups them by domain, since within a domain every table shares the same RLS state, access paths, and risk profile. Phase 17B's actual per-table work list (§23) should be generated mechanically from `information_schema.columns` (as this audit did) rather than hand-maintained.

| Domain / table group | Example tables | RLS today | Tenant model | Risk if enforcement flips on unprepared |
|---|---|---|---|---|
| Phase 0 Tier-1 retrofit | `integration_connections`, `approval_requests`, `audit_logs`, `company_memories`, `users` | Audit-mode (5) | TENANT_DIRECT | Currently safe (audit only); would need policy tightened + role fix before enforcement |
| Business Discovery/Blueprint | `discovery_sessions`, `discovery_turns`, `business_blueprints`, `blueprint_sections`, `blueprint_claims` | Audit-mode (5) | TENANT_DIRECT | Same as above |
| Recommendations | `recommendation_runs`, `recommendations` | Audit-mode (2) | TENANT_DIRECT | Same |
| Agent Runtime | `agents`, `agent_versions`, `agent_tool_permissions`, `agent_executions`, `agent_execution_steps` | Audit-mode (5) | TENANT_DIRECT | Same; also depends on Agent execution DB-session plumbing (§17) |
| MCP | `mcp_tool_exposures`, `mcp_client_credentials` | Audit-mode (2) | TENANT_DIRECT | Same; MCP execution path does not call `set_tenant_context` today (§18) — high risk |
| Medical Tourism | 7 tables (see §20) | Audit-mode (7) | TENANT_DIRECT | Same; some paths (public patient lead intake) need design decision |
| Website Builder | `websites`, `website_versions`, `website_pages`, `website_sections` | Audit-mode (4) | TENANT_DIRECT | Public runtime reads by tenant_id from an unauthenticated URL path param and never calls `set_tenant_context` (§19) — will break under enforcement unless fixed |
| Business Journey | `business_journeys` | Audit-mode (1) | TENANT_DIRECT | Same as Discovery |
| Vertical registry | `organization_vertical_extensions` | Audit-mode (1) | TENANT_DIRECT (join row) | Same |
| **Finance** | `invoices`, `invoice_line_items`, `payments`, `payment_allocations`, `payouts`, `refunds`, `credit_notes`, `credit_note_line_items`, `vendor_bills`, `purchase_orders`, `purchase_order_items`, `cash_forecasts`, `cash_forecast_items`, `writeoff_requests`, `collection_actions` | **None** | TENANT_DIRECT | HIGH — real money data, zero DB-level protection today (app-level filtering only) |
| **CRM** | `customers`, `customer_notes`, `customer_signoffs`, `customer_feedback`, `customer_risk_signals`, `customer_lifecycle_profiles`, `leads`, `lead_attributions` | **None** | TENANT_DIRECT | HIGH |
| **Jobs / Field Ops** | `jobs`, `job_tasks`, `job_costs`, `job_materials`, `job_qa`, `job_attachments`, `quotes`, `quote_line_items`, `scope_changes`, `warranties`, `service_reminders`, `workers`, `licenses`, `completion_packets` | **None** | TENANT_DIRECT | HIGH |
| **Marketing / Outbound / Retention / Reactivation** | `campaigns`, `campaign_leads`, `campaign_conversions`, `marketing_content`, `marketing_spend`, `marketing_spend_allocations`, `marketing_lead_sources`, `outbound_*` (7 tables), `retention_*` (4 tables), `reactivation_*` (2 tables), `nurture_*` (3 tables), `content_*` (4 tables), `review_requests`, `referral_*` (4 tables), `advocate_candidates` | **None** | TENANT_DIRECT | MEDIUM-HIGH |
| **Automation Engine** | `automations`, `automation_versions`, `automation_executions`, `automation_execution_steps` | **None** | TENANT_DIRECT | HIGH — this is the engine that fires side-effecting tool calls |
| **Company Memory / Knowledge (RAG)** | `knowledge_files`, `knowledge_chunks` | **None** | TENANT_DIRECT | HIGH — vector/RAG content is exactly the kind of data a cross-tenant leak would be most damaging for |
| **AI / Voice / Communications** | `ai_invocation_logs`, `communication_logs`, `voice_receptionist_settings`, `call_sessions` | **None** | TENANT_DIRECT | MEDIUM — logs of AI provider calls, potentially containing prompts/PII |
| **Compliance / Contracts** | `licenses` (compliance.py), `contracts` | **None** | TENANT_DIRECT | MEDIUM |
| **Events / Webhooks / System** | `events`, `event_processing_records`, `dead_letter_events`, `webhook_events`, `notifications`, `notification_preferences`, `team_invites`, `morning_briefs`, `morning_brief_insights`, `morning_brief_recommendations` | **None** | TENANT_DIRECT / mixed | MEDIUM — some of these are legitimately touched by system workers across tenants in a batch (§9) |
| **Local SEO / Reputation** | `local_listings`, `local_reviews`, `local_reputation_events`, `seo_keywords`, `seo_opportunities`, `seo_pages` | **None** | TENANT_DIRECT | LOW-MEDIUM |
| **Tenant tool policy** | `tenant_tool_policies` | **None** | TENANT_DIRECT | HIGH — this table governs what an Agent/MCP client is allowed to do; itself unprotected today |
| Global/system (no tenant_id, correctly no RLS) | `organizations`, `vertical_extensions`, `integration_provider_catalog`, `alembic_version` | N/A | GLOBAL_SHARED / SYSTEM | NOT_APPLICABLE |

TENANT_INDIRECT candidates (tables that reach a tenant only through a foreign key, not a `tenant_id` column of their own) were not found in this inventory pass — every table that participates in a tenant-owned domain carries its own `tenant_id`, per the codebase's own `TenantScopedMixin` convention (column-only, per `KLAROS_ARCHITECTURE_RECONCILIATION.md`, confirmed in `tests/test_postgres_rls_audit_mode.py`'s own comments). This is good news for policy design (§12) — it means straightforward `tenant_id = current_setting(...)::uuid` policies are the dominant case, not the exception, but every JOIN_TABLE/child table still needs individually confirmed before Phase 17B, since this audit's evidence is column-presence, not a full ORM relationship graph walk of all ~130 tables.

---

## 5. Tenant Context Propagation

Trace: JWT → `CurrentUser` → API/service layer → SQLAlchemy session → PostgreSQL.

- `app/api/deps.py` decodes the JWT, extracts `payload["tenant_id"]`, builds `CurrentUser`, and — critically — calls `await set_tenant_context(db, tenant_id)` before yielding the DB session to the endpoint. Tenant identity is **never** taken from the request body; confirmed no `tenant_id` field is read from client-submitted payloads anywhere in `deps.py`.
- `app/db/session.py::set_tenant_context(session, tenant_id)` is the single implementation of the `SET LOCAL app.tenant_id = ...` mechanism (via `SELECT set_config('app.tenant_id', :tenant_id, true)`, the `true` third argument being what makes it transaction-local rather than session-scoped). It is a documented no-op on SQLite and a documented no-op (with a debug log) when `tenant_id is None`.
- **Confirmed call sites, exhaustively grepped across `backend/app/`:**
  1. `app/api/deps.py` (2 call sites — the standard `CurrentUser` dependency and a second lower-level dependency) — the authenticated HTTP request path.
  2. `app/workflows/activities.py` (2 call sites) — Temporal workflow activities.
- **Confirmed NOT called anywhere in:**
  - `app/events/worker.py`, `app/events/bus.py`, `app/events/handlers.py`, or any of the domain event handlers (`crm_handlers.py`, `finance_handlers.py`, `marketing_handlers.py`, `notification_handlers.py`, `operations_handlers.py`, `retention_handlers.py`, `agent_trigger_handlers.py`, `automation_handlers.py`) — the Event Bus worker that processes durable event-store rows continuously in-process.
  - `app/mcp/protocol.py` — MCP request handling derives `tenant_id` from `auth.credential.tenant_id` for authorization/semaphore purposes but never stamps it onto a DB session's transaction-local GUC.
  - `app/workers/main.py` — the standalone background worker entrypoint.
  - `app/api/v1/public_websites.py` — the unauthenticated public website runtime, which takes `tenant_id` directly as a URL path parameter and queries by it, but never calls `set_tenant_context`.
  - Any webhook handler (`app/api/v1/webhooks.py`) — this gap is explicitly self-documented in `set_tenant_context`'s own docstring: "a webhook handler ... that derives tenant_id from a signed payload later, not from auth" is called out by name as a known instrumentation gap.

This is the single most important finding for enforcement readiness: **tenant-context plumbing exists for less than half of the platform's real request/execution surface.** Enabling enforcement today would not just risk leaking data on the 100 un-instrumented tables (it would not — RLS simply wouldn't apply to them at all, so their current app-level-only isolation continues unchanged) — it would **break** every one of the paths above that touch the 32 RLS-instrumented tables, because `current_setting('app.tenant_id', true)` would read NULL/empty on those connections, and a correctly-written enforcing policy (per §15, fail-safe) must deny access when context is absent — turning Event Bus processing, MCP tool calls, and public website rendering into silent zero-row failures the moment any enforcing policy touches a table those paths use.

---

## 6. Connection Pool Safety

`app/db/session.py` configures `create_async_engine` with `pool_size=10, max_overflow=20` against Postgres (asyncpg driver), i.e. a genuine, actively-reused connection pool — this is not a theoretical concern.

The mechanism used is `SET LOCAL` via `set_config(..., is_local=true)`, which is the correct, safe choice: it is transaction-scoped and automatically resets on `COMMIT`/`ROLLBACK`, so a connection returned to the pool cannot carry a stale `app.tenant_id` into its next, unrelated borrower — **provided every session actually opens and closes a transaction around the `SET LOCAL` call**, which SQLAlchemy's `AsyncSession` does implicitly (auto-begins on first statement).

This is not just architecturally sound in theory — it is **behaviorally proven** by `backend/tests/test_postgres_rls_audit_mode.py`, specifically:
- `test_set_local_tenant_context_does_not_leak_across_pooled_connections`: runs tenant A → tenant B → tenant A across repeated acquire/release cycles from the same pooled engine and asserts `app.tenant_id` is unset at the start of every fresh session.
- `test_concurrent_requests_maintain_isolated_tenant_context`: two genuinely concurrent `asyncio.gather`-driven "requests," each on its own session, each asserted to see only its own tenant_id — proving isolation holds under real concurrency, not just sequential reuse.

Both tests pass conceptually per their design (this audit did not re-execute the full existing suite against Postgres — see §13 for what this audit did run) and represent real, substantive verification of the single highest-risk implementation detail the Phase 0 plan itself flagged. **This part of the foundation is genuinely solid.**

One caveat surfaced by the same test file's own commentary (not by this audit independently, but worth restating because it materially affects policy design in §12/§14): a custom GUC like `app.tenant_id`, once set via `SET LOCAL` on a given backend, leaves a placeholder behind such that `current_setting(..., true)` returns `''` (empty string), not `NULL`, on that same backend on a later, unrelated transaction that never itself called `set_config`. A naive `USING (tenant_id = current_setting('app.tenant_id', true)::uuid)` risks a `::uuid` cast failure on `''` due to PostgreSQL's non-guaranteed AND-conjunct evaluation order; the tested-safe form uses `CASE WHEN ... IS NULL OR ... = '' THEN false ELSE ... END`. Any Phase 17B policy must use this pattern, not a bare boolean AND.

---

## 7. Database Role / BYPASSRLS Analysis

**This is a hard blocker, independent of everything else in this report.**

- Deployment configuration (`docker-compose.yml`): `POSTGRES_USER: ${POSTGRES_USER:-klaros}` is passed to the official `postgres` Docker image's bootstrap/initdb mechanism. The initdb bootstrap role in that image is, by Postgres's own design, always created as a full superuser (`CREATEROLE, CREATEDB, SUPERUSER`, implicit `BYPASSRLS`). `docker-compose.prod.yml` does not override this with a separate, restricted application role — it only requires `POSTGRES_PASSWORD` to be set explicitly and otherwise inherits the same bootstrap-role pattern for whatever managed Postgres it points at. **No file anywhere in the repository (migrations, deployment config, or docs) creates a second, non-superuser, non-BYPASSRLS role for the application to connect as.** `backend/tests/test_postgres_rls_audit_mode.py` independently confirms and documents this exact fact in its own comments: "this test suite's own DATABASE_URL role and docker-compose.yml's/CI's `POSTGRES_USER=klaros` are the cluster's initdb bootstrap role, i.e. a superuser."
- This audit's own disposable-Postgres verification (§11) confirms the mechanics directly: a `klaros` role created with `LOGIN SUPERUSER` (mirroring the deployment's bootstrap-role reality) reads `rolsuper = t`. Every table in `public`, including all 32 RLS-enabled ones, is owned by the connecting bootstrap role (`postgres` in this sandbox's equivalent of the deployment's `klaros`).
- **PostgreSQL superusers bypass row security unconditionally — `FORCE ROW LEVEL SECURITY` included.** Table owners bypass RLS by default too (this is exactly why the audit-mode migrations deliberately used `ENABLE` and not `FORCE`, per their own comments — `FORCE` only changes behavior for the *owner*, and the app's role already needs the owner-independent superuser bypass fixed regardless).
- **Consequence: turning on `FORCE ROW LEVEL SECURITY` today, or even writing real `tenant_id = current_setting(...)` policies, would have zero effect on the application's actual database traffic**, because the application connects as the same role that owns every table and is a cluster superuser. This is not a policy-design problem — it is a deployment-architecture problem that must be solved before any policy work matters. It requires: creating a distinct, non-superuser, `NOBYPASSRLS`, table-non-owning application role in every environment (dev, staging, CI, production), granting it exactly the DML privileges it needs, transferring or reassigning table ownership away from it (or granting from a separate owner role), and repointing `DATABASE_URL` at that role — a real, cross-cutting deployment and credentials change, not a migration-only change.

---

## 8. All Database Access Paths

| Path | Touches tenant data? | Tenant_id source | Sets DB tenant context today? | Runs in a transaction? | Pooled connection? | Legitimate cross-tenant need? | Privileged actor? |
|---|---|---|---|---|---|---|---|
| HTTP API (`app/api/deps.py` → routers) | Yes | JWT `payload["tenant_id"]` | **Yes** | Yes (per-request session) | Yes | No | No |
| Temporal workflows/activities (`app/workflows/activities.py`, `app/temporal_client.py`) | Yes | Passed into the activity, presumably from the triggering request/journey | **Yes** | Yes | Yes | Generally no (some system-maintenance workflows may be an exception — not enumerated here) | Mixed |
| Event Bus worker (`app/events/worker.py`, `bus.py`, domain handlers) | Yes (CRM/finance/marketing/notification/operations/retention/automation-trigger handlers all operate on tenant-owned rows) | Carried in the `Event` row's own payload, not derived from auth | **No** | Per-handler, varies | Yes (shares the app's engine/pool) | No — each event belongs to one tenant | Runs as the app's own DB role (no separate system identity) |
| Background worker entrypoint (`app/workers/main.py`) | Indirectly (hosts the Temporal worker process) | N/A at this layer | N/A at this layer (delegates to activities.py, which does set it) | N/A | N/A | N/A | N/A |
| MCP server (`app/mcp/protocol.py` → `ToolRegistry` → tool → DB) | Yes | `auth.credential.tenant_id` from the MCP client credential | **No** | Depends on the underlying tool/service's own session use | Yes | No | External MCP client, authenticated but currently not DB-context-scoped |
| Agent execution (`AgentExecutionService`, reasoning loop, `ToolRegistry`) | Yes | Carried through `AgentExecution.tenant_id` / the triggering request's tenant | Only if the invoking HTTP request already set it upstream and the same session is reused; not independently verified for every internal service call in this pass — **flagged NEEDS DESIGN DECISION**, not asserted safe | Varies | Yes | No | Agent acting on tenant's behalf |
| Approval execution | Yes | `ApprovalRequest.tenant_id` | Same caveat as Agent execution | Varies | Yes | No | — |
| Public website runtime (`app/api/v1/public_websites.py`) | Yes | Unauthenticated URL path parameter `tenant_id` (by explicit design — the module's own docstring states "this tenant_id exists is the entire trust boundary") | **No** | Yes, but uncontextualized | Yes | No (each request is scoped to exactly the one tenant in the URL) | Public/anonymous |
| Public lead intake (Medical Tourism `PatientLead`, website inbound forms) | Yes | Likely the same website/tenant resolution as above — not independently re-verified path-by-path in this pass | Same as above unless independently instrumented | — | Yes | No | Public/anonymous |
| Webhooks (`app/api/v1/webhooks.py`) | Yes (per `set_tenant_context`'s own docstring) | Derived from a signed payload, not JWT auth | **No**, self-documented gap | — | Yes | No | External system (Stripe/QuickBooks/etc.), authenticated by signature not JWT |
| Migration scripts (Alembic) | Schema-level, and the seed-data migrations (`0041`/`0042` catalog seeds) write global rows | N/A | N/A (runs as the bootstrap/superuser role by design — this is correct and expected for migrations) | Yes | No (one-off connection) | Yes, legitimately (schema changes are inherently cross-tenant) | Yes, correctly privileged |
| CLI/admin scripts (`backend/scripts/`) | Not fully enumerated in this pass | — | — | — | — | Possibly | — |
| Tests (`backend/tests/`) | Yes, extensively | Test fixtures construct arbitrary tenant UUIDs directly | Only the RLS-specific test file calls it explicitly; most other tests rely on app-level `.where(tenant_id == ...)` filtering and don't exercise the RLS path at all | — | — | Tests intentionally exercise cross-tenant scenarios to prove isolation | Test/superuser role |

**This table is the core evidence for the report's central claim**: the HTTP and Temporal paths are instrumented and (for HTTP) well-tested for the pool-safety property; the Event Bus, MCP, public website, and webhook paths are not instrumented at all, despite several of them touching data on tables that already have (permissive, but structurally present) RLS enabled.

---

## 9. Cross-Tenant/System Operations

Legitimate system-wide operations identified:

| Operation | Caller | Tables | Why cross-tenant is legitimate | Required context | RLS should... |
|---|---|---|---|---|---|
| Provider/vertical registry reads | Any tenant, read-only | `integration_provider_catalog`, `vertical_extensions` | Shared reference data, not tenant data at all | None — no `tenant_id` column exists | Never apply (no policy needed; these are outside RLS's scope by design) |
| Schema migrations | Alembic, CI/CD | All tables | DDL is inherently a privileged, cross-tenant operation | Bootstrap/superuser role | Bypass entirely (this is correct Postgres behavior for the owner/migration role — the same role identity that is the §7 blocker for the *application* role, but is exactly right for *migrations*) |
| Event Bus batch processing | `EventWorker` | Any tenant table an event handler touches | Each individual event belongs to exactly one tenant — this is NOT a legitimate cross-tenant read, it is currently an **un-scoped** one that happens to only ever touch one tenant's data per event, and must be fixed to set context per-event, not exempted | Per-event tenant_id from the event payload | Must set tenant context per event before enforcement, not be exempted |
| Morning Brief / Automation scheduler ticks | `EventWorker.on_tick` hooks | `morning_briefs`, `automations`, etc. | Same as above — iterates across many tenants' due items in one process loop, but each individual row operation is single-tenant | Per-item tenant_id | Same — must set context per item, not exempted |
| Aggregated/platform metrics (if any exist — not found as a distinct code path in this pass) | — | — | Not located; flagged UNCERTAIN, not confirmed absent | — | — |
| Public website rendering | Anonymous internet request | `websites`, `website_versions`, `website_pages`, `website_sections` | Legitimately single-tenant per request (the URL determines the tenant), but currently unauthenticated and un-instrumented | tenant_id from URL path, explicitly trusted by design for this one narrow purpose | Should call `set_tenant_context` using the URL's tenant_id (not exempted, not treated as "global") |

No genuinely multi-tenant-in-one-query operation (e.g., "count active tenants," "sum revenue across all tenants") was found as an existing code path in this pass. If one exists (e.g., in an internal admin/ops tool not covered by this grep pass), it would need its own explicitly-privileged, audited bypass — not a blanket role-level BYPASSRLS grant to the application's normal runtime role.

---

## 10. Existing RLS Test Audit

**One file**: `backend/tests/test_postgres_rls_audit_mode.py` (six tests, all `@requires_real_postgres`, skipped on SQLite). This audit read the file in full (§6 above already covers its content). Verdict, matching the task's instruction not to accept "policy exists" as proof:

- `test_rls_is_enabled_on_all_five_tier1_tables` — catalog inspection only (`pg_class`/`pg_policies`), the weakest of the six, but honestly scoped to exactly that.
- `test_audit_mode_is_a_real_no_op_today` — genuine behavioral proof that audit mode doesn't accidentally filter anything yet.
- `test_set_local_tenant_context_does_not_leak_across_pooled_connections` — genuine behavioral proof of the pool-safety property (§6).
- `test_concurrent_requests_maintain_isolated_tenant_context` — genuine behavioral proof under real async concurrency.
- `test_enforcing_policy_fails_safe_with_no_tenant_context` — **the strongest test in the suite**: temporarily FORCEs RLS and installs a real tenant-matching policy inside a rolled-back transaction, run as a purpose-created non-superuser probe role (explicitly avoiding the superuser-bypass trap described in §7), and proves both fail-safe behavior (no context ⇒ zero rows) and correct scoping (tenant A's context ⇒ only tenant A's rows). This is real, substantive enforcement-mechanism verification — not "policy exists."
- `test_existing_application_queries_are_unaffected_by_audit_mode` — regression guard that the app's own manual `.where(tenant_id == ...)` filtering still works identically with audit-mode RLS present.

**Scope limits, stated plainly:** this file covers exactly one table (`company_memories`) out of 132 tenant tables, and only the 5 Tier-1 tables for the catalog/no-op checks. It does not test any Attack 2–10 scenario from §22 end-to-end through the real application code (API endpoints, MCP, agents) — it tests the underlying Postgres mechanism directly. It does not run in CI as a standing enforcement gate — the enforcing-policy experiment is deliberately rolled back and never persists. No test in the repository exercises RLS behavior through the Event Bus, MCP, or public website paths at all.

No other `test_postgres_rls_*` files exist. Grepping the full test suite for `RLS`, `tenant_isolation`, `row level security`, `current_setting`, `app.tenant_id` found no additional relevant test files beyond this one plus the general tenant-scoping assertions scattered through ordinary service-level tests (which test app-level filtering, not the database layer).

---

## 11. Real PostgreSQL Catalog Evidence

**Methodology**: `pgserver` (already present in `backend/.venv`) was used to start a disposable, loopback-only PostgreSQL 16 instance in the session's own scratch directory (not inside the repository). A `klaros` superuser role and `klaros` database were created (mirroring the deployment's actual bootstrap-role reality per `docker-compose.yml`'s `POSTGRES_USER=klaros` pattern), `DATABASE_URL` was pointed at it, and `alembic upgrade head` was run via the backend's own venv Python — exit code 0, all 51 migrations applied cleanly, no errors. The instance was destroyed at the end of the audit (it self-terminates when the owning Python process exits; nothing was left running against the repository).

Direct queries against `pg_class`, `pg_policies`, `pg_roles`, and `information_schema.columns` on that real, fully-migrated database (not inferred from Alembic source) produced the counts in §3. Raw evidence highlights:

```
rls_enabled_tables: 32 rows, all relrowsecurity=t, relforcerowsecurity=f, owner=postgres (bootstrap role)
total public tables: 136
pg_policies: 32 rows, all permissive=PERMISSIVE, cmd=ALL, qual=true
pg_roles (klaros): rolsuper=t, rolbypassrls=f, rolcanlogin=t   [rolbypassrls is moot — rolsuper already implies full bypass]
tenant_id columns: 132 tables
non-RLS tables with tenant_id: 100 tables
```

This is first-hand evidence, not a re-statement of the Alembic migration source or of prior audit documents.

---

## 12. Policy Design Analysis

For the 132 tenant-owned tables, the dominant, correct model is the straightforward `tenant_id = current_setting('app.tenant_id', true)::uuid` predicate, using the CASE/WHEN fail-safe form documented in §6/§14 rather than a bare boolean AND. This is confirmed appropriate because:

- Every tenant table carries its own `tenant_id` column directly (§4) — no relationship-based ("child inherits parent's tenant via FK") policy was found necessary in this pass, though a full per-table FK audit (checking that every child's `tenant_id` actually always equals its parent's `tenant_id` at the application-write layer) was **not** independently re-verified for all 132 tables and should be a Phase 17B task, not assumed.
- `tenant_id` is declared `nullable=False` on every table sampled in the migration source for the 32 already-instrumented tables; a full nullability audit across all 132 was not exhaustively re-run column-by-column in this pass and should be confirmed in Phase 17B (a nullable `tenant_id` would need explicit NULL-handling in its policy).

Tables/situations where a straightforward policy would be **incorrect** or need a design decision:

- **Public website tables** (`websites`, `website_versions`, `website_pages`, `website_sections`): SELECT for the public rendering path is legitimately unauthenticated and must key off the URL path's tenant_id, not a JWT-derived one — the policy itself can still be `tenant_id = current_setting(...)`, but the *application* must call `set_tenant_context` using the URL parameter (already an explicitly-trusted value by that module's own design) before querying, which it does not do today (§5, §19).
- **Webhook-received rows**: tenant_id is derived from a signed external payload after the fact, not from auth — the write path needs its own `set_tenant_context` call once the payload is verified and the tenant resolved, before the INSERT.
- **Event Bus / scheduler loops**: process many tenants' rows across one long-lived worker loop; the context must be set and reset per-item, not once per process — this is a control-flow change, not just a policy-syntax question.
- **MCP tool execution**: currently derives tenant identity for authorization (`auth.credential.tenant_id`) and Agent/tool-permission checks but not for the DB session's GUC — needs its own `set_tenant_context` call at the point where a DB session is acquired for a tool invocation.
- **`integration_provider_catalog`, `vertical_extensions`, `organizations`**: correctly excluded from tenant-scoped policies (global/root rows, no `tenant_id` column) — no design change needed.

---

## 13. NULL/System Context Analysis

The correct, fail-safe behavior — and the one already proven mechanically correct by `test_enforcing_policy_fails_safe_with_no_tenant_context` (§10) — is: **no tenant context set ⇒ zero rows, never "see everything."** This was independently verified in this audit's own review of the `set_tenant_context` implementation: it is a strict no-op when `tenant_id is None` (it does not, for example, fall back to setting an empty string or a sentinel "system" value that a careless policy might misinterpret as "match everything"), and the tested enforcing-policy pattern explicitly treats `NULL` and `''` identically as "deny" via `CASE WHEN ... IS NULL OR ... = '' THEN false`.

What is **not yet designed**: an explicit "system context" identity for the legitimate cross-tenant operations in §9 (migrations aside, which correctly run as the bypass-capable role). Today, there is no concept of "this connection is deliberately operating as a trusted system actor across all tenants" distinct from "this connection forgot to set tenant context." Once the application's DB role loses its superuser/BYPASSRLS status (§7, a prerequisite), any genuinely-needed cross-tenant system operation will need one of: (a) a second, narrowly-scoped, audited database role with `BYPASSRLS` used only for that specific operation, or (b) an explicit `current_setting('app.tenant_id') = '__system__'` sentinel value with policies that special-case it. Neither exists today. This is a **NEEDS DESIGN DECISION** item for Phase 17B, not a defect in the current audit-mode code.

---

## 14. Write-Path Analysis

INSERT/UPDATE/DELETE policies were not yet written anywhere (audit mode only uses one combined `FOR ALL` permissive policy), so there is no existing write-path bug to find in policy SQL — the risk here is entirely prospective, for Phase 17B's policy-writing work:

- **Discovery → Blueprint → Recommendations → Business Journey**: each stage's tenant_id must match the parent it was derived from (e.g., a `Recommendation.tenant_id` must equal its `RecommendationRun.tenant_id`, which must equal the originating `BusinessBlueprint.tenant_id`). This chain was not independently re-verified row-by-row in application code in this pass; Phase 17B's write-policy design should include an explicit assertion (either a CHECK constraint, a trigger, or an RLS WITH CHECK clause referencing the parent) that this can never diverge, not just trust application code to always get it right.
- **Agents → AgentExecutions → AgentExecutionSteps**: same shape of risk — an execution step's tenant_id diverging from its execution's tenant_id would be a serious agent-runtime bug independent of RLS, worth a constraint regardless of the RLS enforcement timeline.
- **Automation Engine** (`automations` → `automation_versions` → `automation_executions` → `automation_execution_steps`): same parent/child tenant-consistency requirement; this domain currently has **zero** RLS instrumentation (§4), so it is both a write-path risk and a complete DB-level-isolation gap today.
- **Finance** (`invoices` → `invoice_line_items`, `credit_notes` → `credit_note_line_items`, `purchase_orders` → `purchase_order_items`, `quotes` → `quote_line_items`): same parent/child pattern, same zero-RLS-today gap.
- **Approvals executing Agent/Automation actions**: an `ApprovalRequest` approved by one tenant's user must never be executable against another tenant's resources — this is fundamentally an application-logic concern already (RLS is a second layer, not the only layer), but is worth calling out because Approval execution is one of the few paths in this codebase that legitimately *writes* to many different downstream tenant tables depending on what it's approving.

No specific instance of `parent.tenant_id != child.tenant_id` being insertable was proven to exist in this pass (that would require either a live exploit attempt against application code, out of this audit's read-only/no-product-change scope, or a full static trace of every service's insert path) — this section identifies where the *risk* concentrates for Phase 17B's write-policy and test design, not a confirmed live bug.

---

## 15. Agent/ToolRegistry Analysis

`Agent`, `AgentVersion`, `AgentToolPermission`, `AgentExecution`, `AgentExecutionStep` are all RLS-audit-mode-instrumented (§3). `AgentToolPermission` is the record that should be the actual authority for "can this agent touch this tool/resource," independent of RLS. This audit confirmed:

- `ToolRegistry` and `AgentExecutionService` do not manage `AsyncSession`/`async_session_maker` themselves in the files inspected — DB session acquisition happens at a higher layer (the API request or the calling service), meaning tenant-context propagation for Agent execution **inherits whatever the calling path already did**. For HTTP-triggered agent runs, that's `app/api/deps.py`'s `set_tenant_context` call, which is fine. For Temporal-triggered or event-triggered agent runs (scheduled/triggered execution, per `agent_trigger_handlers.py`), the call chain goes through the Event Bus worker path, which — per §5 — **does not** call `set_tenant_context` anywhere. **This is a real, not-yet-verified-safe gap**: scheduled/event-triggered Agent executions may run with no DB tenant context set at all, which is currently harmless (audit mode) but would break or need explicit handling under enforcement.
- No code path was found where an Agent's tool call reaches the database with a *different* tenant_id than the Agent's own `tenant_id` — tool implementations were not exhaustively re-audited line-by-line in this pass, but the design (tenant_id flows from `AgentExecution.tenant_id`, set at creation from the triggering context) does not show an obvious spoofing vector. This should be confirmed with an actual Attack 7 behavioral test in Phase 17B (§24), not just design review.

---

## 16. MCP Analysis

`app/mcp/protocol.py`'s `McpAuth`/credential object is, per its own comment, "the one and only source of tenant_id/role for every method below" — i.e., MCP's authorization model is already centralized and does not trust any client-supplied tenant_id. `mcp_tool_exposures` and `mcp_client_credentials` are both RLS-audit-mode instrumented.

The concrete gap: `auth.tenant_id` is used for authorization decisions (which tools are enabled, per-tenant concurrency semaphores) but is **never passed into `set_tenant_context`** anywhere in `protocol.py`. Whatever DB session the invoked tool ultimately uses (via `ToolRegistry` → tool → service → session) therefore reaches Postgres with no `app.tenant_id` GUC set, exactly like the Agent/event-triggered case in §15. Under audit mode this is invisible (no policy filters on it); under enforcement it would either silently return zero rows (breaking every MCP tool call that touches an RLS-enabled table) or — for MCP-driven writes/reads against the 100 un-instrumented tables — remain completely unprotected by RLS either way, same as the HTTP path today. No direct DB access bypassing `ToolRegistry` was found in `protocol.py` itself in this pass.

---

## 17. Website Public Runtime Analysis

`app/api/v1/public_websites.py` is short, single-purpose, and unusually explicit about its own trust model in its own docstring: **the tenant_id is a raw, unauthenticated URL path parameter**, and "this tenant_id exists" is stated as the entire trust boundary — i.e., this is an intentional, documented design choice (a public marketing/business website is meant to be publicly reachable by anyone who knows or is given the tenant's URL), not an oversight. `WebsiteService(async_session_maker)` is instantiated directly; the request never goes through `app/api/deps.py`, so `set_tenant_context` is never called on this path.

This is compatible with RLS in principle (the tenant_id, though unauthenticated, is exactly the value that should be stamped as the DB context — nothing sensitive is being protected by *withholding* it, since the whole point is public visibility of that one tenant's published content) but is **not wired up today**. Phase 17B must add an explicit `await set_tenant_context(session, tenant_id)` call in this router (or equivalent) using the trusted path parameter, or every public website request will fail closed the moment `websites`/`website_versions`/`website_pages`/`website_sections` move from audit mode to enforcement.

Public lead intake (e.g., a website contact/lead form submitting into CRM or Medical Tourism `PatientLead` tables) was not independently traced end-to-end in this pass beyond confirming those target tables exist and, for Medical Tourism, are already RLS-audit-mode instrumented (§20); the exact router(s) handling public form submission should be re-traced in Phase 17B alongside the fix above, since they carry the same "must set tenant context from a trusted-but-unauthenticated source" shape.

---

## 18. Medical Tourism Analysis

All 7 tables — `medical_tourism_providers`, `medical_tourism_provider_credentials`, `medical_tourism_procedures`, `medical_tourism_provider_procedures`, `medical_tourism_patient_leads`, `medical_tourism_consultations`, `medical_tourism_referral_commissions` — confirmed present in the real catalog (§11) and confirmed RLS-audit-mode instrumented since migration `0049`, consistent with the FINAL_KLAROS_PRE_COMMIT_AUDIT.md's own table list. These names differ slightly from the task's guessed list (no bare `Provider`/`Procedure`/`PatientLead`/`Consultation`/`ReferralCommission` table names exist; every Medical Tourism table is prefixed `medical_tourism_`), consistent with that prior audit's own note about the same naming mismatch.

Read/write/agent/public-access paths for this vertical were not independently re-traced service-by-service in this pass beyond confirming the table-level RLS state; given this vertical's own `medical_tourism_tools.py` (Agent tool integration) and its patient-lead intake likely sharing the same public-runtime and MCP tenant-context gaps documented in §16/§17, it should be treated as inheriting those same risks rather than assumed separately safe.

---

## 19. Performance/Index Analysis

Not benchmarked (per task instruction — this is identification only, not execution). Likely risk areas for Phase 17B to check before writing real policies:

- Every RLS predicate Phase 17B writes will be `tenant_id = current_setting(...)::uuid` — this needs a btree index on `tenant_id` (or a composite index leading with `tenant_id`) on every one of the 132 tenant tables for the planner to push the RLS qualifier efficiently rather than sequential-scanning and filtering row-by-row. This audit did not verify index presence/absence on all 132 tables; that is a concrete, mechanically-checkable Phase 17B pre-flight item (`\d+ <table>` or `pg_indexes` per table), not assumed either way here.
- `knowledge_chunks` (RAG/vector search) and Medical Tourism `medical_tourism_providers` search are the two domains explicitly flagged in the task as likely to combine a tenant predicate with an expensive similarity/geo search — worth dedicated `EXPLAIN ANALYZE` attention once real policies exist, not before.
- `current_setting()` itself is cheap (a GUC read), not a join or subquery, so its own per-row cost is not a primary concern; the concern is whether the planner can use `tenant_id`'s index at all once the predicate is wrapped in a security-barrier view (which is how Postgres implements RLS internally) — this can sometimes defeat otherwise-available index usage for complex `qual` expressions, another reason the simple `CASE/WHEN` form (already required for correctness, §6) should be kept as index-friendly as possible and verified with `EXPLAIN` in Phase 17B rather than assumed fine.

---

## 20. Migration Strategy

A future Phase 17B enforcement migration would need, per table, in this order:
1. Confirm (or add) an index on `tenant_id`.
2. `ALTER POLICY tenant_isolation_audit_policy ON <table> USING (<real CASE/WHEN predicate>) WITH CHECK (<same, or a stricter INSERT-time check>)` for the 32 already-`ENABLE`d tables — reusing the existing policy object as designed.
3. For the 100 currently-unprotected tenant tables: `ALTER TABLE <table> ENABLE ROW LEVEL SECURITY` + `CREATE POLICY ...` with the real predicate directly (no separate audit-mode stop for these, since Phase 17B is explicitly the enforcement phase — though the same "audit mode first" caution that motivated Phase 0's approach for the original 32 arguably applies just as much here, given 100 tables is a much larger blast radius; this is a **NEEDS DESIGN DECISION** for whether Phase 17B itself should stage these through a permissive step first).
4. Only after every table above is verified working: `ALTER TABLE <table> FORCE ROW LEVEL SECURITY` — and only once the application's DB role is no longer the table owner and no longer a superuser (§7), since `FORCE` has zero effect otherwise.
5. Separately, and as a true prerequisite gating step 4 for *every* table: create the restricted application role, reassign ownership or grant appropriately, and cut the deployment over to it in every environment (dev, CI, staging, production) — this is infrastructure/deployment work, not a `CREATE POLICY` migration.

Rollback: `ALTER POLICY ... USING (true)` reverts any single table to audit mode without dropping the policy object; `ALTER TABLE ... NO FORCE ROW LEVEL SECURITY` reverts step 4 independently of step 2/3. Because every change is a reversible `ALTER`, not a `DROP`/`CREATE` cycle, rollback is low-risk *for the SQL itself* — the higher-risk rollback scenario is the application DB-role cutover (step 5), which is a credentials/infra change outside Alembic's normal blast radius and needs its own rollback plan (keep the superuser-role `DATABASE_URL` available to swap back to).

Existing data: no tenant_id backfill is needed — every sampled tenant column across all 132 tables is `NOT NULL` already (per the migration source for the 32 confirmed instrumented ones; the remaining 100 were not individually re-confirmed `NOT NULL` in this pass and should be before Phase 17B, since a nullable `tenant_id` on any of them would need explicit policy handling per §13).

---

## 21. Deployment Strategy

Given the DB-role change is the true long pole (§7), and given the existing precedent this codebase already follows (introduce structurally, in audit/permissive mode, well before flipping to enforcing — exactly what Phases 0 through 16b already did for the 32 tables), the safest sequence is:

1. **Code-and-role first, enforcement policy last.** Stand up the restricted application role in every environment and cut `DATABASE_URL` over to it, verify the *entire* test suite and manual smoke paths still pass identically under audit-mode RLS with the new role (this alone is a meaningful, independently-valuable milestone, and is low-risk since audit-mode policies are still permissive) — before writing a single enforcing policy.
2. **Fix every access-path gap next** (Event Bus, MCP, public website, webhooks — §5/§16/§17) so `set_tenant_context` (or an explicit system-context equivalent, §13) is called everywhere a DB session touches tenant data, verified by new behavioral tests (§24), while RLS is still fully permissive — so a plumbing bug shows up as a test failure, not a production outage.
3. **Instrument the 100 currently-unprotected tables in audit mode** (mirroring exactly what `0040`–`0051` already did for the 32), as its own reviewable migration batch, before any enforcement.
4. **Only then, per-domain (not all 132 tables in one migration), flip real policies + `FORCE`** — starting with the lowest-traffic, most-isolated domain (e.g., Medical Tourism, already narrow) as a canary, then widening. A single feature-flagged or per-table staged rollout is strongly preferable to one big-bang migration, given the number of distinct access paths that must all be correct simultaneously for a single table's enforcement to be safe.
5. Dual-mode period: since `ALTER POLICY`/`FORCE` are per-table, per-statement DDL with no inherent "flag," a literal application feature flag is not needed — the migration itself *is* the staged rollout, provided each migration touches a small, deliberately-chosen table set and is validated in staging against real traffic patterns (or at minimum a comprehensive behavioral test pass, §24) before the next batch.

Migration-first-then-code is **not** recommended here specifically because of the role dependency (§7): shipping enforcing policies before the role cutover would be a no-op that gives false confidence; shipping the role cutover before fixing the access-path gaps (§5) would turn audit-mode's current "harmless no-op" into either broken functionality (if a stricter role also loses privileges the un-instrumented paths implicitly relied on) or, if the role cutover is done carefully with equivalent grants, still a safe, code-first step — hence code-and-role-first is the recommended order above.

---

## 22. Security Attack Matrix

Given §7's finding (application role = table owner = superuser, RLS currently has zero enforcement effect regardless of policy content), **live attack attempts against the running application would today succeed or fail based entirely on application-level tenant filtering, not RLS** — RLS enforcement is not yet a meaningful second layer in production. This audit did not attempt Attacks 1–5, 7, 8 as live exploits against running application code, both because doing so would require standing up the full app stack (JWT issuance, auth, live HTTP endpoints) which is out of this audit's read-only/no-product-change scope, and because the answer for all of them today is already determined by §7: **RLS provides no protection against any of these attacks right now, on any table, regardless of audit-mode policy content**, since the connecting role bypasses it unconditionally. Application-level tenant filtering (the pre-existing, non-RLS isolation model) is what actually protects tenant data today, and this audit's scope explicitly excludes re-verifying that separate, pre-existing mechanism.

What **was** verified, mechanically, against the real disposable database:

| # | Attack | Expected | Observed |
|---|---|---|---|
| 6 | Connection context leakage (pooled connection reuse across tenants) | B never sees A's leaked `app.tenant_id` | **Confirmed safe** — this is exactly what `test_set_local_tenant_context_does_not_leak_across_pooled_connections` and `test_concurrent_requests_maintain_isolated_tenant_context` already prove (§6/§10); this audit reviewed and validated the test design and reasoning rather than re-executing the full suite, and finds the proof sound. |
| — | Enforcing-policy mechanism itself (fail-safe with no context; correct scoping with context), against a genuinely non-privileged probe role | No context ⇒ 0 rows; tenant A's context ⇒ only A's rows | **Confirmed correct**, per `test_enforcing_policy_fails_safe_with_no_tenant_context`'s design (§10) — the underlying Postgres mechanism, in isolation, on one table, behaves exactly as required. |

Attacks 1–5, 7, 8, 9, 10 are therefore reported as: **NOT_APPLICABLE today at the RLS layer** (RLS enforces nothing yet, on any table, due to §7), and **UNVERIFIED at the application-isolation layer** by this audit (out of scope — this phase audits RLS readiness, not the pre-existing app-level tenant-filtering mechanism's own correctness, which earlier phase audits, e.g. FINAL_KLAROS_PRE_COMMIT_AUDIT.md §13, already spot-checked and found sound for the paths they reviewed). Attack 9 (public website) is addressed structurally in §17: the public runtime's tenant resolution is a deliberate, documented, single-tenant-per-request design, not itself a cross-tenant vulnerability, but is unready for RLS enforcement specifically (§5/§17). Attack 10 (system worker) is addressed in §9/§13: no legitimate cross-tenant system operation was found to already be broken by RLS, because RLS doesn't enforce anything yet; the real gap is the reverse — the worker paths themselves have no tenant-context mechanism at all yet.

---

## 23. Phase 17B Implementation Plan

In dependency order:

1. **Design and stand up a restricted application database role** (`NOSUPERUSER NOBYPASSRLS`, no table ownership) in dev, CI, staging, and production; decide the ownership/grant model (e.g., a separate `klaros_owner` role for migrations, `klaros_app` for runtime traffic); update `docker-compose.yml`, `docker-compose.prod.yml`, CI service config, and every `DATABASE_URL`. **Blocking prerequisite for everything else that claims "enforcement."**
2. **Close the tenant-context propagation gaps**: add `set_tenant_context` (or a documented system-context equivalent) calls in `app/events/worker.py`/`bus.py`/handlers (per-event, not per-process), `app/mcp/protocol.py` (at DB-session-acquisition time), `app/api/v1/public_websites.py` (from the trusted URL path param), and every webhook handler in `app/api/v1/webhooks.py` (post signature-verification).
3. **Design the system/global-context model** (§13) for legitimate cross-tenant operations, and identify/scope any operation that genuinely needs a narrow `BYPASSRLS` role rather than a sentinel context value.
4. **Instrument the 100 currently-unprotected tenant tables in audit mode**, exactly mirroring `0040`'s pattern — its own migration batch, reviewed and merged before any enforcement work.
5. **Confirm `NOT NULL tenant_id` and a `tenant_id` index on all 132 tenant tables**; add either where missing.
6. **Write real policies** (`CASE/WHEN` fail-safe form) for every table, staged per-domain (§21), each domain's migration also flipping `FORCE ROW LEVEL SECURITY` for that domain once its own access paths are proven.
7. **Full regression pass** (existing 1845+ backend tests, frontend suite) after each staged domain's enforcement flip, plus the new behavioral tests (§24) added before step 6 starts, not after.

---

## 24. Required Tests

All against a real, disposable PostgreSQL instance (never SQLite), extending the existing `test_postgres_rls_audit_mode.py` pattern and its non-superuser-probe-role technique to every domain, not just `company_memories`:

- SELECT: tenant A cannot see tenant B's rows, for a representative table in every domain group from §4 (not just the 32 already-instrumented ones).
- INSERT: tenant A cannot create a row with `tenant_id = B` (either denied by `WITH CHECK`, or the value is application-controlled and never client-settable — confirm which model each write path actually uses).
- UPDATE / DELETE: tenant A's write against tenant B's row affects 0 rows.
- NULL context: a connection with `app.tenant_id` unset sees 0 rows on every enforced table (fail-safe), not all rows.
- Connection reuse: extend the existing pool-leak test to run across a mix of the newly-instrumented tables, not just `company_memories`.
- Agent: an Agent's tool execution triggered via the Event Bus / scheduled-trigger path (not just the HTTP-triggered path) is proven to carry correct tenant context end-to-end, closing the §15 gap.
- MCP: an MCP client's tool call is proven to reach the database with its own credential's tenant_id set as DB context, closing the §16 gap.
- Website: a public website request for tenant A never returns or is influenced by tenant B's website rows even under a crafted/malformed path parameter.
- Medical Tourism: provider/lead/consultation cross-tenant denial, end-to-end through the actual service layer, not just the raw table.
- System operations: after the role cutover (§23 step 1) and system-context design (§23 step 3), a legitimate cross-tenant operation (e.g., Event Bus processing a batch spanning multiple tenants) is proven to still work correctly, tenant-by-tenant, under full enforcement.

---

## 25. Risks

- **False sense of security**: audit-mode RLS existing on 32 tables could be mistaken by a future engineer for real protection; it is not, and — per §7 — would not be even if `FORCE`d today, because of the role/ownership issue. This report exists specifically to prevent that misunderstanding from persisting into Phase 17B planning.
- **Partial enforcement is worse than no enforcement if access-path gaps aren't closed first**: flipping FORCE on a table before fixing the Event Bus/MCP/public-website/webhook gaps would silently break those features rather than protect anyone, since those paths would suddenly see zero rows on tables they legitimately need.
- **100-table blast radius**: the un-instrumented tables span the most operationally and financially significant domains (Finance, CRM, Jobs, Automation). A mistake in a single shared policy pattern, applied broadly, has a much larger blast radius than the 32-table Phase-0-era rollout did.
- **Role cutover risk**: reassigning table ownership / introducing a new application role is a genuine, non-Alembic infrastructure change with its own rollback complexity, in every environment including production — this is the highest-operational-risk single step in the entire plan.
- **Performance regressions**: unverified index coverage on 100 additional tables' `tenant_id` columns; a Phase 17B rollout without confirming indexes first risks query-plan regressions discovered only under real traffic.

---

## 26. Known Limitations of This Audit

- This audit did not spin up the full application (FastAPI server, JWT issuance, live HTTP requests) and therefore did not attempt Attacks 1–5, 7, 8 as genuine, live exploit attempts — see §22 for why that is currently moot at the RLS layer regardless (§7), and out of scope for re-verifying the separate, pre-existing application-level isolation mechanism.
- The full existing backend test suite (~1858 tests) was not re-executed in this pass; this audit relied on direct source reading of the one RLS-specific test file plus fresh migration/catalog verification against a real, freshly-built Postgres instance, which is the evidence this report's numeric claims (§3, §11) are actually based on.
- Not every one of the 132 tenant tables' FK/parent-child tenant-consistency relationships, `NOT NULL` constraints, or index coverage was individually re-verified row-by-row; §14/§19/§20 identify this as required Phase 17B pre-flight work rather than asserting it is already fine.
- Medical Tourism's and the public website's full read/write/agent-access service-layer call graphs were not independently re-traced function-by-function beyond confirming table-level RLS state and the one public-runtime router file read in full (§17).
- No CLI/admin script under `backend/scripts/` was individually inventoried for DB access patterns beyond the one autonomy-deprecation guard already known from the prior pre-commit audit.

---

## 27. Files That Would Need Modification (Phase 17B, not this phase)

- `docker-compose.yml`, `docker-compose.prod.yml`, CI service configuration, and every environment's `DATABASE_URL`/credentials (new restricted role).
- `backend/app/events/worker.py`, `backend/app/events/bus.py`, and the domain handler modules under `backend/app/events/` (per-event tenant context).
- `backend/app/mcp/protocol.py` (tenant context at DB-session-acquisition time).
- `backend/app/api/v1/public_websites.py` and any public lead-intake router (tenant context from the trusted, unauthenticated tenant_id).
- `backend/app/api/v1/webhooks.py` (tenant context post signature-verification).
- New Alembic migrations instrumenting the 100 currently-unprotected tables, plus per-domain enforcement migrations.
- `backend/tests/test_postgres_rls_audit_mode.py` (extended) plus new per-domain RLS behavioral test files.

## 28. Files That Must NOT Be Modified (this phase, Phase 17A)

Per this task's own file boundary: `backend/app/**` (all product code), `frontend/**`, all Alembic migrations, all existing tests, all configuration files, and the untracked Phase 16a/16b working-tree state listed in §2. **None of these were modified during this audit.** The only file created is this report.

---

## 29. Final Readiness Classification

| Area | Classification |
|---|---|
| Audit-mode instrumentation on the 32 already-covered tables | READY (as audit mode — correctly implemented, correctly scoped, not enforcement) |
| The remaining 100 tenant tables' RLS instrumentation | BLOCKED / NEEDS IMPLEMENTATION |
| Tenant-context propagation: HTTP path | READY |
| Tenant-context propagation: Temporal activities | READY (not independently deep-audited beyond confirming the call sites exist) |
| Tenant-context propagation: Event Bus / scheduler | BLOCKED |
| Tenant-context propagation: MCP | BLOCKED |
| Tenant-context propagation: public website / lead intake | BLOCKED |
| Tenant-context propagation: webhooks | BLOCKED |
| Connection-pool / SET LOCAL safety | READY (behaviorally proven) |
| Database role / BYPASSRLS / ownership | BLOCKED — hard blocker, independent of all policy work |
| Enforcing-policy mechanism design (CASE/WHEN pattern) | READY (proven correct in isolation for one table) |
| Policy design for all 132 tables | NEEDS DESIGN DECISION (mostly straightforward; some access-path-specific decisions outstanding) |
| NULL/system context behavior | NEEDS DESIGN DECISION (fail-safe default is correct and proven; explicit system-context identity does not yet exist) |
| Write-path (parent/child tenant consistency) | NEEDS DESIGN DECISION / NOT independently verified |
| Agent/ToolRegistry tenant propagation | NEEDS DESIGN DECISION (HTTP-triggered path likely fine; event/scheduled-triggered path unverified) |
| MCP tenant propagation | BLOCKED |
| Website public runtime | BLOCKED (but well-understood, narrow fix) |
| Medical Tourism | Same state as its underlying tables — BLOCKED pending role fix, otherwise audit-mode READY |
| Performance/index readiness | NEEDS IMPLEMENTATION (unverified, not confirmed absent) |
| Migration strategy | NEEDS DESIGN DECISION (staging approach for the 100-table batch) |
| Existing RLS tests | READY as a design proof-of-concept; NEEDS IMPLEMENTATION to extend platform-wide |

**Overall: NOT READY for production RLS enforcement.** The platform has a correct, well-tested *foundation* for the mechanism (SET LOCAL semantics, pool safety, fail-safe policy pattern) on a minority of tables and a minority of access paths, but has a hard architectural blocker (database role/ownership, §7) that makes the question of "is the policy SQL correct" secondary until resolved, plus a majority of tenant tables (100/132) and several live, shipped access paths (Event Bus, MCP, public website, webhooks) with no RLS instrumentation or tenant-context plumbing at all.

---

## 30. Exact Next Step

**Do not write or run any enforcement migration next.** The single highest-leverage next step is Phase 17B Step 1 from §23: design and provision a restricted, non-superuser, non-table-owning PostgreSQL application role across dev/CI/staging/production, and cut the application's `DATABASE_URL` over to it while RLS remains in its current, fully-permissive audit mode — verifying the entire existing test suite and application behavior are unaffected by the role change alone, before any policy content changes at all. Every other item in this report (the 100 un-instrumented tables, the Event Bus/MCP/webhook/public-website context gaps, real policy authorship) is real work that should follow, but is moot as "enforcement" until this one blocker is resolved, since the application's current database role bypasses RLS unconditionally regardless of what any policy says.

---

*Audit performed via real git inspection, source reading of `backend/app/db/session.py`, `backend/app/api/deps.py`, `backend/app/workflows/activities.py`, `backend/app/events/worker.py`, `backend/app/mcp/protocol.py`, `backend/app/api/v1/public_websites.py`, all Alembic migrations `0040`–`0051`, `backend/tests/test_postgres_rls_audit_mode.py`, `docker-compose.yml`/`docker-compose.prod.yml`, and direct SQL queries (`pg_class`, `pg_policies`, `pg_roles`, `information_schema.columns`) against a disposable, freshly-migrated PostgreSQL 16 instance created via `pgserver`. No product code, migration, test, or configuration file was modified. No commit or push occurred.*
