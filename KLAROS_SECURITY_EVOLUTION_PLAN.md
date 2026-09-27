# Klaros — Security Evolution Plan

Status: proposal only. No security/tenancy code, RLS policy, or credential-handling code was changed. This is the P0/P1-weighted document referenced throughout the rest of this analysis — most other specs defer their security detail here.

## 1. Current state (verified)

- Multi-tenancy is app-layer only: `TenantScopedMixin` on ~190/208 model classes, `CurrentUser.tenant_id` extracted from JWT (`backend/app/api/deps.py:20,54`), no Postgres RLS anywhere (confirmed absent by grep across all 40 migrations and all of `backend/app`, both audit passes).
- Every tool call — human or AI — passes through the identical `ToolRegistry.execute` pipeline (`registry.py:126-224`, verified): kill-switch → RBAC permission → tenant-scope check → billing cap → schema validation → policy resolution → execute/approve → audit. This is a real, consistently enforced tool-level control.
- Credentials: `IntegrationConnection.encrypted_credential` (Fernet-based, `credential_store.py`), boot-time refusal if `INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` unset (`main.py:35-47`).
- Object storage: local-disk adapter is real, tenant-namespaced (`{tenant_id}/{uuid}_{filename}`), path-traversal-guarded, 25MB cap, content-type allowlist (verified, `local_adapter.py`). S3 path genuinely unimplemented (`NotConnectedObjectStorageAdapter` raises unconditionally; no S3 SDK dependency exists at all).
- `Organization.autonomy_level` is stored but unenforced (verified, `organization.py:60-67`) — see `KLAROS_AI_AGENT_ARCHITECTURE.md` §1 for the full implication.
- No tool-call loop/depth/rate protection beyond one billing cap (verified).

## 2. Postgres RLS — target and migration strategy

**Target**: RLS as a *backstop*, not a replacement, for the existing app-layer `tenant_id` filtering. A missed `WHERE tenant_id=...` in a service method becomes a caught-at-the-database-level no-op/error instead of a cross-tenant leak.

**Design**: for each tenant-scoped table, a policy of the form `USING (tenant_id = current_setting('app.current_tenant_id')::uuid)`, with the application setting `app.current_tenant_id` via `SET LOCAL` at the start of each request-scoped DB session (in the same `deps.py` dependency that already extracts `CurrentUser.tenant_id` from the JWT — one additional line per request, not a new mechanism). Superuser/migration connections bypass RLS (`BYPASSRLS` role) as normal.

**Rollout strategy without breaking the live application** (this is the #1 structural risk item, per the original audit's P1 finding, and must be done carefully):

1. **Phase A — instrumentation only**: add the `SET LOCAL app.current_tenant_id` call to every request, but do not enable RLS on any table yet. Verify via logging that it's set correctly on 100% of tenant-scoped requests for a full deploy cycle (catches any code path — e.g. a background job, a Temporal activity — that doesn't go through the normal request dependency chain and would need its own tenant-context-setting equivalent).
2. **Phase B — enable RLS in `PERMISSIVE` mode with a bypass flag**: turn on RLS per table, but include a temporary `OR current_setting('app.rls_bypass', true) = 'true'` clause defaulting to allow, so a misconfigured session degrades to today's app-layer-only behavior (logged loudly) rather than a hard outage, while the team confirms no legitimate code path was missed.
3. **Phase C — remove the bypass clause**, table by table, starting with the smallest-blast-radius tables (new Phase 0–1 tables: `BusinessBlueprint`, `Agent*` — these have zero existing production traffic to break) and only *later* extending to the 208 existing tables, in an order prioritized by data sensitivity (financial/PHI-adjacent tables — `Invoice`, `Payment`, `PatientLead` once it exists — before low-sensitivity tables like `MarketingContent`).
4. **New tables from this plan onward ship with RLS enabled from day one** (no bypass period needed, since there's no existing traffic pattern to risk).

**Why not RLS everywhere immediately**: the existing 208-table, ~70-service surface has never been tested against RLS; a naive blanket enablement risks breaking working production code paths that set `tenant_id` implicitly in ways RLS's session-variable model doesn't anticipate (e.g. a background job with no per-request context). The phased approach isolates that risk to new, low-traffic surfaces first.

## 3. Other isolation layers (target state per brief's checklist)

| Layer | Current | Target |
|---|---|---|
| Application-level checks | Real, `TenantScopedMixin` + `CurrentUser.tenant_id` | Unchanged, remains the primary layer; RLS is additive |
| Tenant-scoped repositories | Implicit (service methods filter manually) | No change required — RLS backstops this pattern rather than replacing it |
| Service-level checks | Real, per the audit's sampling | Unchanged |
| API dependency checks | Real (`deps.py`) | + `SET LOCAL app.current_tenant_id` (§2) |
| Agent-level tenant binding | N/A (no agents exist) | `Agent.tenant_id` immutable at creation, never derived from model output — `AgentExecutionService` always sources `tenant_id` from the authenticated session/scheduled trigger's own tenant context, exactly like `AIExecutionService.request_tool_execution` does today (`tenant_id` is a caller-supplied parameter, never something the model emits) |
| Tool-level tenant enforcement | Real, unchanged (`registry.py:164-167`) | Unchanged |
| Integration credential isolation | Real (Fernet, per-tenant row) | Unchanged |
| Object storage isolation | Real for local disk; N/A for S3 (unimplemented) | New S3 adapter must preserve the exact tenant-prefixed-key convention (`{tenant_id}/...`) the local adapter already uses, plus per-tenant bucket-policy or prefix-scoped IAM credentials if the chosen provider supports it (provider choice **UNKNOWN — REQUIRES DECISION**) |
| Website isolation | N/A (doesn't exist) | Separate `website-runtime` origin (`KLAROS_WEBSITE_BUILDER_SPEC.md` §8, `KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md` §2) |
| Knowledge isolation | Real, tenant-scoped pre-filter before similarity scoring (`knowledge_retrieval_service.py:11-13`, verified) | Unchanged |
| Vector-search isolation | Real, same as above | Unchanged; add RLS to `KnowledgeChunk` in Phase C rollout given its sensitivity |
| Cache isolation | Redis shared infra; tenant separation by key-namespacing convention, not independently key-by-key verified | Audit key-namespacing convention explicitly (**UNKNOWN — REQUIRES VERIFICATION**, flagged but not resolved by either audit pass); document the convention once confirmed |
| Event isolation | `Event`/`EventProcessingRecord` presumably tenant-scoped like other tables | Confirm `TenantScopedMixin` coverage explicitly during Phase 0 |
| Temporal workflow isolation | Workflow IDs/queries not independently audited for tenant-id leakage into logs/IDs | Adopt a convention: workflow IDs may include `tenant_id` for operability but must never be guessable/enumerable in a way that leaks which tenants exist — **UNKNOWN — REQUIRES VERIFICATION** of current workflow-ID scheme |

## 4. Object storage evolution (S3)

Replace `NotConnectedObjectStorageAdapter` with a real adapter implementing the same `ObjectStorageProvider` ABC (`put`/`get`/`delete`, verified interface in `backend/app/storage/base.py`) — no interface change, so `factory.py`'s `get_object_storage()` selection logic needs only its stub branch replaced, not a rewrite. Tenant isolation: preserve the `{tenant_id}/{uuid}_{filename}` key convention; add server-side encryption at rest (provider-native, e.g. SSE-S3/SSE-KMS) as a new-vs-local-disk improvement, since local disk today has no at-rest encryption noted in either audit pass (**UNKNOWN — REQUIRES VERIFICATION** whether the host filesystem is itself encrypted at the infra level, outside this codebase's control).

## 5. AI-specific security (cross-referenced, detailed in `KLAROS_AI_AGENT_ARCHITECTURE.md`)

Prompt-injection mitigation (reuse the existing fenced-DATA-block pattern from `ai_provider.py`), tool allowlisting (`AgentToolPermission`), plan validation before execution, financial/communication/integration hard limits, `CompanyMemory`'s existing `PENDING→ACTIVE` human-confirmation gate extended to Blueprint facts. See `KLAROS_AI_AGENT_ARCHITECTURE.md` §4–§5 for full detail; not duplicated here.

## 6. RBAC additions

New `Permission` enum values (code-level, per existing pattern — no schema change, since `Role`/`Permission` are StrEnums, not DB tables per verified `KLAROS_DATABASE_CURRENT.md` finding): `MANAGE_BLUEPRINT`, `MANAGE_AGENTS`, `EXECUTE_AGENT`, `MANAGE_WEBSITE`, `PUBLISH_WEBSITE`, `MANAGE_INTEGRATIONS_CATALOG` (distinct from existing `MANAGE_INTEGRATIONS` which governs connection, not catalog visibility — **UNKNOWN — REQUIRES DECISION** whether these should actually be the same permission; default recommendation is to keep catalog-viewing open to any authenticated role and gate only connection actions, matching today's implicit pattern where `/settings/integrations` is presumably viewable broadly).

## 7. Security testing gate before autonomous AI execution

Per the brief's explicit requirement, before any `Agent` may run at `Execute-approved` or `Execute-autonomous` tier in production: (a) the RLS Phase C rollout (§2) must cover all tables that agent's tool allowlist can touch; (b) the tenant-isolation regression suite (`KLAROS_TESTING_STRATEGY.md`) must pass for every new table; (c) the hard limits in `KLAROS_AI_AGENT_ARCHITECTURE.md` §5 must be implemented and tested, not just designed; (d) a staging environment (`KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md` §4) must exist and have exercised at least one full agent execution end to end.

## Cross-references

`KLAROS_GAP_ANALYSIS.md` §1.2, `KLAROS_AI_AGENT_ARCHITECTURE.md`, `KLAROS_TESTING_STRATEGY.md`, `KLAROS_ARCHITECTURAL_DECISIONS.md` ADR-004, `KLAROS_DEPLOYMENT_EVOLUTION_PLAN.md` §2.
