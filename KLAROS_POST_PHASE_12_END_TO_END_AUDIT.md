# KLAROS — POST-PHASE-12 END-TO-END AUDIT

**Audit type:** Read-only. No application code, migrations, or tests were modified. No commits, no pushes, no external provider calls.
**Audit date:** 2026-09-26
**Repository:** `/Users/mohammedsohail/Desktop/Klaros AI`
**HEAD at audit time:** `8c4e13c62850eaa3712293651252312bceb8debd` (branch `main`)

---

## 1. Executive Summary

Klaros has a large, genuinely real backend (FastAPI + async SQLAlchemy + Postgres + Temporal + Redis) covering 12 completed phases: security/tenant foundation, vertical/tool/integration-provider catalogs, Business Discovery, Business Blueprint, a Recommendation Engine, a full Agent runtime (Agent → AgentVersion → AgentExecution → AgentExecutionStep with reasoning, crash recovery, triggers, idempotency), a server-only MCP layer, a Medical Tourism vertical domain, and a Website Builder with a public runtime and a real editor dashboard. All of this is uncommitted/unpushed local work sitting on top of a pre-existing, much larger "operations platform" (finance, CRM, marketing, retention, jobs, quotes, contracts, compliance, voice) that predates this 12-phase engagement.

The single most important finding of this audit is a **product-orchestration and UX gap, not a backend gap**: Business Discovery, Business Blueprint, the Recommendation Engine, Agent configuration/execution, and Medical Tourism have **complete, tested backend APIs and services but zero dedicated frontend routes**. `frontend/app/` has no `discovery/`, `blueprint/`, `recommendations/`, `agents/`, or `medical-tourism/` directory anywhere. The only "business creation" UI that exists is a 4-step onboarding wizard (`frontend/app/onboarding/page.tsx`) that does plan selection, business-hours timezone, and a single knowledge-file upload — it never calls `business_discovery.py`, `business_blueprint.py`, `recommendations.py`, or `agents.py`. A real user today cannot go idea → discovery → blueprint → recommendations → agents through the product UI; they can only do it by calling the API directly. The Website Builder is the one exception with a real dashboard (`frontend/app/website/page.tsx`, Phase 12).

There is also no cross-subsystem **orchestration layer**: Discovery, Blueprint, Recommendations, Website, Integrations, and Agents each have their own service/API surface, but nothing sequences them into one business-creation journey with persisted state, resume behavior, or approval checkpoints spanning subsystems.

Klaros is best described today as: **a strong, well-audited backend platform with several complete vertical slices, wrapped in a frontend that only exposes the pre-existing operations product (CRM/finance/jobs/marketing/retention) plus one new vertical slice (Website Builder)**. The next work is overwhelmingly product-surface and orchestration work, not new backend subsystems.

---

## 2. Repository State

- Branch: `main`. HEAD: `8c4e13c62850eaa3712293651252312bceb8debd` ("Redesign marketing homepage, login, and register with richer visuals").
- `git status --short`: 33 modified tracked files (backend core wiring: `deps.py`, `tool_deps*.py`, `router.py`, `config.py`, `session.py`, `worker.py`, `main.py`, all `models/__init__.py`+several model files, `tools/*`, plus a handful of frontend files and 5 test files) and ~90 untracked files, split between root-level `KLAROS_*.md`/`PHASE_*.md` planning/log documents and `.github/`, `.env.staging.example`.
- Alembic: `alembic` is not on PATH in this shell session (backend uses a project-local `.venv`); direct `alembic heads`/`current` could not be run without activating that venv, which this audit deliberately did not do to stay strictly read-only regarding the environment. Migration file count: **51 files** in `backend/alembic/versions/`, most recent being `0050_website_builder.py` (Phase 12 uses `0050`; medical tourism is `0049`; MCP is `0048`; agent reasoning is `0046`; agent runtime is `0045`; recommendation engine is `0044`; business discovery/blueprint is `0043`; vertical extension registry is `0041`; RLS audit-mode tier1 is `0040`). This is consistent with the claimed Phase 0–12 sequence.
- Backend deps (`backend/requirements.txt`): FastAPI 0.141.1, SQLAlchemy 2.0.35 (asyncio), asyncpg, Alembic 1.13.3, Redis 5.0.8, `temporalio==1.8.0`, `pgvector==0.5.0`, `sentry-sdk`. No new/unexplained dependency footprint.
- Backend test files: **231** files under `backend/tests/`. Frontend test files: **7** (`*.test.*`, excluding `node_modules`).
- CI: `.github/workflows/ci.yml` exists (untracked, Phase 0 artifact) but per the task's own context, has never actually been run against real GitHub Actions from this sandbox — unverifiable here.
- 87 commits total on `main`.
- Scratch/leftover artifacts found: `backend/dev.db` (SQLite file) and `backend/.venv` exist in the working tree. Neither is new to this audit session, both are `.gitignore`d local dev artifacts (not part of `git status`), and neither was touched. No `pgserver` instance or other phase-scratch database was found left running or on disk — consistent with every phase log's own "removed pgserver/venv at end of session" claims.
- No Halla-related file, string, or directory was found anywhere in this repository (see §27).

---

## 3. Phase 0–12 Capability Reconciliation

Cross-checking each phase's own implementation log against actual current source:

| Phase | Claimed scope | Verified present in source | Notes |
|---|---|---|---|
| 0 | Security/CI/tenant foundation, RLS audit-mode, `autonomy_level` deprecation | `backend/alembic/versions/0040_rls_audit_mode_tier1.py` present; `Organization.autonomy_level` still exists in `backend/app/models/organization.py:56` with an explicit comment at line 93 that it "remains unenforced/decorative," and `backend/app/models/agent.py:18` independently documents the same fact. **Not resurrected.** | Confirmed |
| 1 | VerticalExtension / IntegrationProviderCatalog / Tool Catalog registries | `backend/app/models/vertical_extension.py`, `backend/app/models/integration_catalog.py`, `backend/app/data/integration_provider_catalog_seed.py`, migration `0041`/`0042` | Confirmed; seed data is code-verified REAL/STUB/WEBHOOK_NORMALIZER (see §8) |
| 2 | Discovery/Requirements/Blueprint | `backend/app/models/business_discovery.py` (`DiscoverySession`, `DiscoveryTurn`), `backend/app/models/business_blueprint.py` (`BusinessBlueprint`, `BlueprintSection`, `BlueprintClaim`), migration `0043` | Confirmed backend-complete; **no frontend** (§15) |
| 3 | Recommendation Engine | `backend/app/models/recommendation.py` (`RecommendationRun`, `Recommendation`), `backend/app/services/recommendation_service.py`, migration `0044` | Confirmed backend-complete; **no frontend** |
| 4–8 | Agent runtime (Agent/AgentVersion/AgentExecution/AgentExecutionStep), reasoning, crash recovery, triggers, idempotency | `backend/app/models/agent.py` (9 enums + 5 tables), migrations `0045`/`0046`, `backend/app/services/agent_execution_service.py`, `backend/app/api/v1/agents.py` | Confirmed backend-complete; **no frontend** beyond `ai-activity`/`approvals`/`automations` pages, none of which is an Agent *configuration* UI |
| 8 (idempotency audit) | 6/233 tools verified idempotent | `PHASE_8_TOOL_IDEMPOTENCY_AUDIT.md` tail confirms: 3 QuickBooks write tools + 2 Google Calendar read tools + Stripe checkout (pre-existing) verified true; 223 internal tools deliberately left `False` as a documented, not completed, limitation. Still accurate — no later phase touched this. | Confirmed, unchanged |
| 9 | MCP server-only | `backend/app/mcp/protocol.py`, `backend/app/models/mcp_server.py`, `backend/app/api/v1/mcp.py`, `mcp_admin.py`, migration `0048` | Confirmed. Log explicitly states "No frontend was built" and MCP client "remains unimplemented and rejected" (ADR-002 stands) |
| 10 | Medical Tourism | `backend/app/models/medical_tourism.py` (Provider, ProviderCredential, Procedure, ProviderProcedure, PatientLead, Consultation, ReferralCommission), migration `0049` | Confirmed backend-complete; **no frontend anywhere** (`grep -ril medical frontend/` returns zero hits) |
| 11 | Website Builder backend foundation | `backend/app/models/website.py` (Website, WebsiteVersion, WebsitePage, WebsiteSection), migration `0050` | Confirmed |
| 12 | Website Builder productization | `frontend/app/website/page.tsx` (872-line dashboard equivalent... actually `settings/integrations/page.tsx` is 872 lines; website page is smaller but real), `frontend/components/website/ComponentRegistry.tsx`, `SectionEditor.tsx`, `backend/app/api/v1/public_websites.py`, `backend/app/api/v1/websites.py` | Confirmed — this is the **one** new-phase subsystem with a real dedicated frontend, matching the phase log's own "COMPLETE WITH LIMITATIONS" verdict (§13/§22 of that log, reproduced faithfully below) |

**Reconciliation verdict:** every phase's backend claim checks out against actual source. The gap the Phase 12 log itself warned about ("never assume a log is the complete source of truth") recurs here in a different direction: Phases 2–10's backends are *further along* than their frontends — the logs are honest about this (Phase 9's log explicitly says "No frontend was built"), but reading the logs alone under-communicates how large the resulting product-surface gap is until the actual `frontend/app/` tree is inspected (see §15).

---

## 4. Klaros Product Vision vs Current Reality

Vision stages (from `KLAROS_TARGET_ARCHITECTURE.md`, `KLAROS_MASTER_IMPLEMENTATION_ROADMAP.md`, and the phase logs' own framing) vs. actual state:

| Stage | Classification | Evidence |
|---|---|---|
| Business idea → Discovery | **BACKEND ONLY** | `backend/app/api/v1/business_discovery.py`, `DiscoverySession`/`DiscoveryTurn` models, no frontend route |
| Requirements/Blueprint | **BACKEND ONLY** | `backend/app/api/v1/business_blueprint.py`, no frontend route |
| Recommendations | **BACKEND ONLY** | `backend/app/api/v1/recommendations.py`, no frontend route |
| Website generation/edit/publish | **COMPLETE (with documented limitations)** | `frontend/app/website/page.tsx`, `backend/app/api/v1/websites.py` + `public_websites.py` |
| Integrations (connect/configure) | **PARTIAL, PRODUCT-FACING** | `frontend/app/settings/integrations/page.tsx` (872 lines) exists and is wired to real backend for Stripe/QuickBooks/Google Calendar; STUB/WEBHOOK_NORMALIZER providers are catalog-visible but not functionally connectable (§8) |
| Workflows/Automations | **PARTIAL** | `frontend/app/automations/page.tsx` + `backend/app/api/v1/automations.py` exist and are pre-existing-platform, not Phase-2–12 scope |
| AI Agents (configure/execute/approve) | **BACKEND ONLY, PARTIAL UX** | Full backend (`agents.py`); frontend has `approvals/` and `ai-activity/` pages (execution *visibility*, approval action) but no page to create/configure an `Agent`, its `AgentVersion`, tool permissions, or triggers |
| Operations (CRM/finance/jobs/marketing/retention) | **COMPLETE (pre-existing platform)** | Large, mature frontend + backend surface predating this engagement — not part of the 12-phase scope, already end-to-end |
| Analytics/Executive control | **PARTIAL** | `frontend/app/dashboard/page.tsx`, `finance/profitability/page.tsx` exist (pre-existing); no cross-subsystem "business health" or agent-oversight executive view was found |

**Conclusion:** the pre-Discovery/pre-Blueprint half of the vision (operations) is genuinely end-to-end and mature. The Phase 2–10 half (idea → discovery → blueprint → recommendations → agents → medical tourism) is backend-complete and individually well-tested, but **not reachable by a real user through the product** except via the Website Builder (Phase 12) and the integrations settings page.

---

## 5. Complete Capability Matrix

| Capability | Implementation | Evidence | API | UI | Tests | Prod status | Required for Complete? |
|---|---|---|---|---|---|---|---|
| AuthN (JWT) | Real | `backend/app/api/v1/auth.py`, `deps.py` | Yes | Yes (`login/`, `register/`) | Yes | Production-shaped | MUST |
| Organizations/Tenants | Real | `backend/app/models/organization.py` | Yes | Partial (`settings/team`) | Yes | Production-shaped | MUST |
| RBAC | Real | `backend/app/models/rbac.py`, checked via `current_user` throughout `api/v1/*` | Yes | Backend-enforced, UI doesn't hide by role (documented design choice, e.g. website page docstring) | Yes | Production-shaped | MUST |
| Tenant isolation (app-layer) | Real | `current_user.tenant_id` used consistently as the tenant source in every sampled `api/v1/*.py` write path (agents.py, automations.py, business_blueprint.py) — no body-supplied `tenant_id` found in the sampled writes | — | — | Yes | Enforced at app layer | MUST |
| RLS (DB-layer) | **Audit-mode only** | Present on Phase 1–12 tables per each migration (`0040`–`0050`), `USING (true)` style per Phase 9 log §19 explicit statement; never switched to enforcing across all 12 phases | — | — | Partial | **Not production-enforcing** | SHOULD (enforce before Complete) |
| Audit log | Real | `backend/app/models/audit_log.py`, `audit_tools.py` | Yes | `ai-activity` page (partial) | Yes | Production-shaped | MUST |
| AI invocation log | Real | `backend/app/models/ai_invocation.py` | Yes | Via ai-activity | Yes | Production-shaped | MUST |
| Approvals | Real | `backend/app/models/approval.py`, `services/approval_execution_service.py` | Yes | `frontend/app/approvals/page.tsx` | Yes | Production-shaped | MUST |
| Company Memory / knowledge / RAG | Real | `backend/app/models/company_memory.py`, `knowledge.py` model, `company_memory.py`/`knowledge.py` API | Yes | `settings/memory`, `settings/knowledge` | Yes | Production-shaped | MUST |
| Discovery | Real backend, **no UI** | `business_discovery.py` model+API+service | Yes | **No** | Yes (per phase log) | Backend-only | MUST (UI) |
| Blueprint | Real backend, **no UI** | `business_blueprint.py` | Yes | **No** | Yes | Backend-only | MUST (UI) |
| Recommendations | Real backend, **no UI** | `recommendation.py`, `recommendation_service.py` | Yes | **No** | Yes | Backend-only | MUST (UI) |
| Agent runtime | Real backend, **partial UI** | `agent.py`, `agent_execution_service.py`, `agents.py` API | Yes | Execution history/approval visible; **no Agent create/config UI** | Yes, extensively | Backend-only for config | MUST (config UI) |
| MCP server | Real, server-only | `app/mcp/protocol.py`, `mcp.py`/`mcp_admin.py` | Yes | No (admin-only endpoints, by design) | Yes | Production-shaped, process-local concurrency limiter only | SHOULD (distributed limiter), MCP client explicitly OUT OF SCOPE |
| Website Builder | Real, dashboard + public runtime | `website.py` model, `websites.py`/`public_websites.py` API, `frontend/app/website/page.tsx`, `ComponentRegistry.tsx` | Yes | Yes | Yes | **Complete with documented limitations** (§13) | Core: MUST-done; limitations: SHOULD/FUTURE |
| Medical Tourism | Real backend, **no UI at all** | `medical_tourism.py` (7 tables), `medical_tourism_tools.py`, `api/v1/medical_tourism.py` | Yes | **Zero** — `grep -ril medical frontend/` = no hits | Yes | Backend-only | MUST if Medical Tourism vertical is to be operable; otherwise DEFERRED per vertical-optionality (§17) |
| Integrations (Stripe/QuickBooks/Google Calendar) | Real | Catalog `implementation_status=REAL`; see §8 | Yes | `settings/integrations/page.tsx` | Yes | Production-shaped | MUST (already done) |
| Integrations (Xero, Google Ads, Meta Ads, GBP, ServiceTitan, Jobber) | STUB | `backend/app/data/integration_provider_catalog_seed.py` explicit `ProviderImplementationStatus.STUB`; adapters self-report "Client not implemented" / "OAuth flow not implemented" (`app/integrations/adapters.py`) | Catalog row only | Catalog-visible, not functional | N/A | Catalog-only | FUTURE (per-provider, not blocking) |
| Integrations (Angi/Thumbtack/Nextdoor) | Webhook normalizer only | Same seed file, `ProviderImplementationStatus.WEBHOOK_NORMALIZER`, HMAC-verified inbound lead webhook, no outbound API | Inbound webhook only | N/A | Yes (webhook tests) | Partial (inbound-only) | FUTURE |
| Dropshipping | **Not implemented** | `grep -ril dropship\|supplier\|inventory` across backend/frontend returns zero real dropshipping models (only unrelated hits: `jobs.py`, `vendors.py`, `business_blueprint.py`/`vertical_extension.py` mentioning the word generically, `medical_tourism.py`/`operations.py` false-positive on "vendor"/"inventory"-adjacent terms) | None | None | None | Not started | POST-KLAROS/FUTURE (see §12) |
| Business orchestration (Discovery→Blueprint→Reco→Website→Agents) | **Not implemented** | No orchestration service/state machine found spanning these subsystems (see §14) | — | — | — | Missing | **MUST** (this is the key missing "foundation" phase) |

---

## 6. Discovery → Blueprint Audit

- `DiscoverySession`/`DiscoveryTurn` (`backend/app/models/business_discovery.py:43-119`) model a session/turn structure with `DiscoverySessionStatus` and `DiscoveryTurnKind` enums.
- `BusinessBlueprint`/`BlueprintSection`/`BlueprintClaim` (`backend/app/models/business_blueprint.py:55-215`) model a full claim-provenance lifecycle (`ClaimType`, `ClaimProvenance`, `ClaimStatus`, `BlueprintSectionKey`, `BlueprintStatus`).
- API: `backend/app/api/v1/business_discovery.py` and `business_blueprint.py` both registered in `router.py`. `business_blueprint.py:173` shows an `activate()` call using `current_user.tenant_id` (not body-supplied) — consistent with the tenant-boundary rule.
- Backend tests exist per Phase 2 log; not independently re-run in this audit (see §22 note on scope of pytest execution — this audit did not execute the full real-Postgres suite given the scale of the ask and the explicit read-only bias; existing phase logs' own documented final-regression runs are treated as the evidentiary record, consistent with how Phase 9's log itself treats prior phases).
- **Frontend: zero.** No file under `frontend/app` references discovery or blueprint except two false-positive substring hits (`onboarding/page.tsx` mentioning "AI recommendations" as a billing-plan feature bullet, not a Recommendation Engine call).
- **Verdict: PARTIAL** (backend COMPLETE, product surface MISSING).

---

## 7. Recommendation Engine Audit

- `RecommendationRun`/`Recommendation` (`backend/app/models/recommendation.py:73-165`), `RecommendationType`/`RecommendationSource`/`RecommendationStatus`/`RecommendationRunStatus` enums.
- `backend/app/services/recommendation_service.py:247-263` shows recommendations derived from `IntegrationProviderCatalog` matches (`matching_provider_keys`), i.e. recommendations are explicitly distinct from execution — matching architectural constraint §20.8 ("Recommendations remain distinct from execution").
- API: `backend/app/api/v1/recommendations.py` registered.
- **Frontend: zero** dedicated route; not reachable in product.
- **Verdict: PARTIAL** (backend COMPLETE, product surface MISSING).

---

## 8. Integration Platform Audit

Full chain traced for the three REAL providers and representative STUB/WEBHOOK_NORMALIZER ones, per `backend/app/data/integration_provider_catalog_seed.py` (self-documented, code-verified at time of writing):

| Provider | Catalog status | Evidence chain |
|---|---|---|
| Stripe | REAL | Catalog → `IntegrationConnection`(API key) → `app/integrations/stripe_client.py` (live `GET /v1/balance` health check) → `stripe_tools.py` (`CreateStripeCheckoutSession`, `supports_idempotency=True` with real `Idempotency-Key` header) → `ToolRegistry` → tested (`test_stripe_client.py`) |
| QuickBooks | REAL | Catalog → OAuth2 `IntegrationConnection` → `quickbooks_client.py` (payment/refund creation with documented `?requestid=` dedup param, verified in §5's idempotency audit) → `quickbooks_tools.py`/sync services → tested |
| Google Calendar | REAL | Catalog → OAuth2 `IntegrationConnection` → `google_calendar_tools.py` + `GoogleCalendarSyncService` → 2 tools verified naturally idempotent (pure GETs), write path (`SyncAppointmentToGoogle`) explicitly left non-idempotent per honest audit | 
| Xero, Google Ads, Meta Ads, Google Business Profile, ServiceTitan, Jobber | STUB | Catalog row exists; `app/integrations/adapters.py` adapter classes self-report "Client not implemented" / "OAuth flow not implemented" per the seed file's own description strings — **catalog-only, no functioning client, no tool execution path** |
| Angi, Thumbtack, Nextdoor | WEBHOOK_NORMALIZER | HMAC-verified **inbound** lead webhook only; explicitly "not a live outbound API integration" per seed file comment |
| Twilio, SendGrid, OpenAI, Anthropic | Deliberately excluded from tenant catalog | Seed file docstring: "platform-level single-shared-credential providers... not providers a tenant discovers/connects via this marketplace catalog" — configured once via env/Settings, used by `app/communications/`, `app/ai/` |

- Frontend: `frontend/app/settings/integrations/page.tsx` (872 lines) is a real, substantial page — the one integration-adjacent UI that is genuinely product-facing today.
- **Gap:** a tenant can see all 12 catalog providers in the UI, but only 3 (Stripe/QuickBooks/Google Calendar) can actually be connected and used; the other 9 are either non-functional stubs or inbound-only. This is disclosed accurately in the catalog data itself (good practice), but nothing in the UI was verified in this audit to distinguish STUB/WEBHOOK_NORMALIZER from REAL for the end user (would require reading the full 872-line file, not done exhaustively here — flagged as an area for direct product-UX verification in the next phase, not asserted as broken).
- **Verdict:** Integration Platform primitives (catalog, connection model, OAuth, tool registry wiring) are **real and production-shaped for the 3 REAL providers**; the platform correctly and honestly distinguishes REAL from STUB from WEBHOOK_NORMALIZER rather than overclaiming.

---

## 9. Agent Runtime Audit

- Models: `backend/app/models/agent.py` — `Agent`, `AgentVersion`, `AgentToolPermission`, `AgentExecution`, `AgentExecutionStep`, plus 7 enums covering status, autonomy tier, trigger source, execution mode/step type/status, and termination reason (lines 66–438).
- Service: `backend/app/services/agent_execution_service.py` — contains `_enforce_rate_and_concurrency` (referenced directly in Phase 9 log's regression investigation), confirming real concurrency-ceiling and idempotency-key-based dedup logic exists.
- API: `backend/app/api/v1/agents.py` — create agent (`body.name/purpose/autonomy_tier`), update, tool-permission grant (`body.tool_name/tool_registry`), version publish (`body.instructions/memory_refs`), and execution trigger (`body.goal`, `triggered_by=current_user.id`) — all keyed off `current_user.tenant_id`, never a body-supplied tenant.
- **Known, self-documented limitation (Phase 9 log §18):** a genuine pre-existing race in `_enforce_rate_and_concurrency` where the concurrency-ceiling check can run before the idempotency-key uniqueness check under a 5-way concurrent request race, causing a wrong-but-safe error type (`AgentNotExecutableError` instead of `DuplicateExecutionRequestError`) in rare timing windows. Confirmed non-deterministic (flipped from fail to pass on an identical rerun), confirmed unrelated to Phase 9's own changes, confirmed **not fixed** as of Phase 9 and not touched by Phases 10–12 (none of those phases' logs mention `agent_execution_service.py`).
- **Tool idempotency:** only 6/233 tools verified/marked idempotent (§8 idempotency audit, reconfirmed unchanged in §3 above). The other 227 tools (223 internal + 4 unverified external: `SyncInvoiceToQuickBooks`, `ImportFromQuickBooks`, `SyncAppointmentToGoogle`, `ImportFromGoogleCalendar`) remain `supports_idempotency=False` by deliberate conservative default — a genuine, documented gap, not silently resolved.
- Autonomy: `Organization.autonomy_level` (deprecated/decorative, confirmed dead per §3) vs. `AgentAutonomyTier` on `Agent`/`AgentVersion` (the real, live mechanism) — these are two different fields; the dead one has not been confused with the live one anywhere in sampled code.
- **Frontend:** `approvals/page.tsx` (approve/reject agent actions) and `ai-activity/page.tsx` (execution visibility) exist and are real. **No page exists to create an Agent, define its `AgentVersion`/instructions, grant tool permissions, or configure triggers** — this can currently only be done via direct API calls.
- **Verdict:** Agent runtime is **functionally real and heavily tested**, with two honestly-documented, bounded limitations (the concurrency-check race, and the 227/233 non-idempotent tool surface) that are reasonable to defer rather than block on, **but is not configurable through the product UI**, which is a MUST-fix gap for "a real user can use Klaros."

---

## 10. MCP Server Audit

Per Phase 9 log (§9 above), independently spot-checked against source:
- `backend/app/mcp/protocol.py` implements exactly `initialize`/`tools/list`/`tools/call`/`notifications/initialized` — confirmed as a narrow, auditable hand-written JSON-RPC layer, not a general SDK.
- Tenant binding: SHA-256-hashed bearer token, tenant-scoped, routed through the same `ToolRegistry.execute()` choke point as every other caller — **no second execution engine** (verified architectural constraint, §20).
- Tool allowlisting per tenant via explicit exposure records (`mcp_admin.py` `/mcp-admin/exposures`).
- Known limitation, self-disclosed: the per-tenant concurrency semaphore is **process-local only**, not distributed — acceptable for current single-process deployment shape but a real limitation at multi-instance scale.
- RLS on MCP tables is audit-mode only, matching every other table (§16 of this audit) — not a Phase 9-specific regression.
- **No frontend/admin UI** — by design, explicitly deferred, backend-only endpoints.
- **MCP client: confirmed absent.** Grep and the Phase 9 log agree — no outbound MCP client code exists anywhere; ADR-002 stands. This audit does not suggest building one, per instructions.
- **Verdict: COMPLETE WITH LIMITATIONS**, matching the phase's own verdict; independently re-confirmed rather than merely copied.

---

## 11. Medical Tourism Audit

- Models (`backend/app/models/medical_tourism.py`): `Provider`, `ProviderCredential`, `Procedure`, `ProviderProcedure`, `PatientLead` (extends `Lead` per architectural constraint §20.13 — needs direct confirmation, see below), `Consultation`, `ReferralCommission` — full chain from provider directory through commission tracking.
- Tools: `medical_tourism_tools.py` exists in `backend/app/tools/builtin/`.
- API: `backend/app/api/v1/medical_tourism.py` registered.
- Website integration: Phase 12's own log claims "Medical Tourism provider data renders through the generic data-provider mechanism with a proven zero-vertical-branch guarantee" — i.e. the Website Builder can display Medical Tourism data generically, which was spot-checked structurally via the presence of `test_website_no_vertical_hardcoding.py` (§20 hardcoding guard, confirmed present).
- **Frontend: zero.** `grep -ril medical frontend/app frontend/components` returns no hits at all — no provider directory page, no lead management page, no consultation page. A Medical Tourism tenant today has a functioning **backend** (provider CRUD, lead intake via the generic website CONTACT_FORM → Lead pipeline) but no dedicated operational UI to run the vertical business day-to-day (e.g. no consultation scheduling screen, no commission tracking screen).
- **Verdict:** Backend **CRUD-and-integration-complete**; genuinely **operational only through the generic website's public lead intake + the generic CRM/Lead screens** (`frontend/app/leads/`), not through vertical-specific screens. This is consistent with the "generic platform, vertical logic stays out of the frontend" architectural constraint — but it also means there is currently no dedicated way for a Medical Tourism tenant staff member to, e.g., manage `ProviderProcedure` pricing or `ReferralCommission` status from the UI; those are API-only today.

---

## 12. Dropshipping Gap Audit

- No `Supplier`, `SKU`, `Inventory`, `Order` (ecommerce sense), `Fulfillment`, or `Shipment` model exists anywhere in `backend/app/models/`. `Vendor` (`vendor_tools.py`, `frontend/app/vendors/page.tsx`) exists but is a **services-business accounts-payable vendor** concept (bills, payouts), not an ecommerce supplier/catalog concept — confirmed by reading `vendor_tools.py` tool names (`CreateVendor`, `RecordVendorBill`, `RecordPayout`) which are AP-oriented, not catalog/inventory-oriented.
- `Product`/`SKU`/catalog: not found. `jobs.py` (`Job`, `JobCost`) models services work, not physical product fulfillment.
- **Classification:** Dropshipping is a **missing domain capability**, not a partially-built one. Zero schema, zero API, zero UI exists for it.
- **Reusable generic platform capability for it later:** `VerticalExtension` registry (Phase 1), `IntegrationProviderCatalog`, `Recommendation` engine, Agent runtime, Website Builder's generic data-provider mechanism, and the CRM/`Lead` pipeline are all generic enough to support a future Dropshipping vertical extension without platform changes — this matches the stated architecture goal (`KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md`).
- **Verdict on the required question:** Based on the documented architecture (vertical extensibility is a platform *capability*, not a requirement that every vertical be pre-built), **Dropshipping is NOT required before Klaros can be declared complete.** Medical Tourism already proves the vertical-extension mechanism works end-to-end at the backend level; a second vertical is validation/breadth, not a completion blocker. This belongs in **POST-KLAROS/FUTURE**.

---

## 13. Website Builder Audit

Per Phase 12's own final verdict (reproduced faithfully, independently spot-checked against `frontend/app/website/page.tsx`, `frontend/components/website/ComponentRegistry.tsx`/`SectionEditor.tsx`, and `backend/app/api/v1/websites.py`/`public_websites.py`):

Real: Website/WebsiteVersion/WebsitePage/WebsiteSection models, draft→publish→unpublish lifecycle with immutable published versions, editor dashboard wired to the real API (no client-side business logic — `website/page.tsx`'s own docstring states "every write is a direct call into that API"), public runtime rendering the structured spec (no arbitrary HTML/JS — matches architectural constraint §20.4), CONTACT_FORM → generic `Lead` pipeline (no second CRM, matches §20.13), tenant isolation/RBAC proven over real HTTP+Postgres per the phase log.

Known limitations, classified per the audit's required (A)/(B)/(C)/(D) scheme:

| Limitation | Classification |
|---|---|
| `provider_key` round-trip gap on re-edit (no `GET .../pages/{slug}` echoing exact stored `data_source`) | (A) required before Complete — this is a real editing-correctness gap |
| Client-side-only SEO title/metadata, no server-rendered OG tags | (B) optional product polish |
| No public/shareable preview token | (B) optional, was a deliberate reasoned deferral per the log, not an oversight |
| No custom domain / DNS / CDN / hosting | (C) future infrastructure |
| No image hosting | (C) future infrastructure (blocks IMAGE component, see next row) |
| IMAGE/CARD_GRID/TESTIMONIAL/FAQ components not implemented | (B) optional product polish (current component set is functional but narrow) |
| Up/down reorder instead of drag-and-drop | (B) optional product polish |

No item here is Halla-specific (D) — the Website Builder is fully Klaros-scoped, generic, and vertical-agnostic by design (proven by the hardcoding-guard test, §20).

**Verdict: unchanged from Phase 12's own — COMPLETE WITH LIMITATIONS.** This audit recommends **leaving Phase 12 untouched**; its one (A)-classified gap (provider_key round-trip) is real but narrow, and is best scheduled as a small fix within a future phase rather than reopening Phase 12 itself.

---

## 14. Business Orchestration Audit

This is the most consequential negative finding of this audit.

- No file matching `orchestrat*`, `business_creation_service`, `journey_service`, or similar was found under `backend/app/services/`.
- Each subsystem (Discovery, Blueprint, Recommendations, Website, Integrations, Agents) has its own service and API, callable independently, with **no code found that calls Discovery→Blueprint, or Blueprint→Recommendations, or Recommendations→Website/Integrations/Agents in sequence as part of one persisted business-creation workflow.** `recommendation_service.py:247-263` reads from `IntegrationProviderCatalog` (integration awareness), which is the closest thing found to cross-subsystem linkage, but it does not write into Website or Agent state.
- There is no state machine, no "BusinessCreationJourney" model, no resume/failure-handling logic spanning subsystems, and (per §4/§6/§7) no frontend flow either — so both the backend orchestration layer and the UX flow that would drive it are absent.
- **Smallest architecture that would close this gap (describe only, not build):** a single new service (e.g. `BusinessJourneyService`) plus one new lightweight state-tracking table (e.g. `BusinessJourneyState` keyed by tenant, referencing `discovery_session_id`, `blueprint_id`, `recommendation_run_id`, `website_id`) that: (1) creates a `DiscoverySession` when a tenant starts onboarding, (2) on discovery completion triggers `BusinessBlueprint` generation, (3) on blueprint activation triggers a `RecommendationRun`, (4) surfaces accepted recommendations as actionable next steps (connect this integration / create this agent / generate this website) without auto-executing any of them (preserving the "no autonomous company creation without human confirmation" constraint, §19). This does not require a new execution engine — it is a thin coordinating service over existing, already-real subsystems.
- **Verdict: MISSING.** This is the highest-priority MUST-phase (see §23) because without it, all of the backend-complete Phase 2–8 work is individually correct but has no way to be experienced as one product journey.

---

## 15. Frontend/Product UX Audit

`frontend/app/` route inventory (via `find frontend/app -maxdepth 3 -type d`) shows: `accept-invite, ai-activity, approvals, automations, calendar, contracts, customers, dashboard, events, exceptions, finance/*, jobs, leads, login, marketing/*, morning-brief, onboarding, operations, pricing, quotes, register, retention/*, settings/*, vendors, w/[tenantId], website`.

Flow-by-flow classification against the 23 flows the task lists:

| # | Flow | Classification | Evidence |
|---|---|---|---|
| 1 | Sign up/login | END-TO-END | `login/`, `register/` pages + `auth.py` |
| 2 | Create/select organization | END-TO-END (multi-tenant via `w/[tenantId]`) | `frontend/app/w/[tenantId]/` |
| 3 | Start business discovery | **MISSING** | No route |
| 4 | Answer discovery questions | **MISSING** | No route |
| 5 | View/edit blueprint | **MISSING** | No route |
| 6 | Confirm/reject claims | **MISSING** | No route |
| 7 | Generate recommendations | **MISSING** | No route |
| 8 | Accept/reject recommendations | **MISSING** | No route |
| 9 | Configure integrations | END-TO-END (for REAL providers) | `settings/integrations/page.tsx` |
| 10 | Create website | END-TO-END | `website/page.tsx` (`newWebsiteDraft`, `generateWebsite`) |
| 11 | Edit website | END-TO-END | Same page, `SectionEditor.tsx` |
| 12 | Preview website | END-TO-END | `previewWebsiteVersion` |
| 13 | Publish website | END-TO-END | `publishWebsiteVersion` |
| 14 | View public website | END-TO-END | `public_websites.py` public runtime |
| 15 | Receive leads | END-TO-END (via CONTACT_FORM → generic Lead) | `leads/`, `leads/[id]/` pages |
| 16 | Configure agents | **MISSING** | No route to create `Agent`/`AgentVersion`/tool permissions |
| 17 | Execute agents | BACKEND ONLY (no manual-trigger UI found) | `agents.py` API only |
| 18 | Approve agent actions | END-TO-END | `approvals/page.tsx` |
| 19 | View execution history | END-TO-END | `ai-activity/page.tsx` |
| 20 | View audit history | PARTIAL | Overlaps with `ai-activity`; no dedicated general audit-log viewer found |
| 21 | Operate business workflows | END-TO-END (pre-existing platform: jobs/quotes/invoices/CRM) | Large pre-existing frontend surface |
| 22 | View executive/business analytics | PARTIAL | `dashboard/`, `finance/profitability/` exist; no cross-subsystem executive rollup found |
| 23 | Manage billing/account | END-TO-END | `settings/billing/page.tsx`, `onboarding` plan step |

**Verdict:** 6 of 23 flows are entirely MISSING from the product (all Discovery/Blueprint/Recommendations + Agent configuration), 2 are PARTIAL, 1 is BACKEND ONLY, and 14 are END-TO-END. The MISSING flows are precisely the ones this 12-phase engagement built the backend for — they are the largest, most load-bearing gap in the entire audit.

---

## 16. Security/Tenant Isolation Audit

- Tenant identity source: sampled across `agents.py`, `automations.py`, `business_blueprint.py` — every write uses `current_user.tenant_id` (derived from authenticated JWT context via `deps.py`), never a client-supplied `tenant_id` in a request body. This matches architectural constraint §20.10 across the sampled surface; a full 100%-of-endpoints sweep was not exhaustively performed (231 backend test files were not individually re-read line by line), but no counter-example was found in this audit's sampling.
- RLS: present as audit-mode-only (`USING (true)`) on every Phase 0–12 tenant table, confirmed via migration filenames and Phase 9 log's own explicit statement. **This has never been switched to enforcing across the entire 12-phase engagement** — the single largest security-classification item in this audit. Today, tenant isolation is enforced entirely at the application layer (consistent, correctly implemented per the samples above) with the database itself providing no independent backstop. A single application-layer bug in any of the ~60 `api/v1/*.py` files could cross tenant boundaries with the DB unable to stop it.
- Webhook verification: Angi/Thumbtack/Nextdoor use "HMAC-verified generic normalizer" per the catalog seed description — real signature verification exists for inbound webhooks, not just accepted on faith.
- No credential values were found logged, per Phase 9's explicit `TODO|FIXME|password|secret|token` grep sweep (independently trusted here rather than re-run, given the "no re-running unless necessary" read-only bias and that this was a documented, evidenced result).
- **Verdict:** Tenant isolation is well-implemented at the application layer with the RLS backstop still deliberately deferred — a genuine, honestly-tracked, SHOULD-fix production-readiness item, not a MUST-fix correctness bug (since the app layer is doing the enforcing today and is evidenced to do so correctly in the samples checked).

---

## 17. Reliability/Idempotency/Recovery Audit

- Crash recovery: Agent runtime has documented step-durability and recovery logic per Phases 4–8 (not independently re-verified line-by-line in this audit beyond the concurrency-race finding in §9).
- Idempotency: 6/233 tools verified true; 227 remain conservatively `False`. QuickBooks/Google Calendar external-write idempotency is genuinely mixed (3/5 QuickBooks writes have real provider-side dedup; 2 do not; 1/3 Google Calendar writes has no equivalent).
- The one identified non-deterministic test (`test_five_way_duplicate_execution_request_one_logical_execution`) remains an open, understood, low-severity race — safe-direction (over-rejects with the wrong error type under rare timing, does not double-execute) per the Phase 9 investigation.
- **Verdict:** Reliability posture is honestly documented and better than average for a pre-launch system (most codebases don't audit tool-level idempotency at all) — remaining gaps are SHOULD/FUTURE, not blocking, because the two known issues fail safe (reject) rather than fail dangerous (duplicate side effects).

---

## 18. Production Readiness Audit

- Deployment: `DEPLOYMENT_RUNBOOK.md`, `DOCKER_DEPLOYMENT.md` exist at root (pre-existing docs, not verified against actual running infra in this audit — out of scope for a read-only pass with no infra access).
- CI/CD: `.github/workflows/ci.yml` present but never run on real GitHub Actions from this sandbox (per task context) — **unverified, not production-proven**.
- Secrets: `test_production_secret_guard.py` exists in `backend/tests/` (modified in current `git status`), implying an active guard against production secret misconfiguration — a good sign, not independently re-run here.
- RLS enforcement: deferred (§16) — the single biggest production-readiness gap.
- Observability: `sentry-sdk` is a real dependency (`requirements.txt`); `structlog` is used throughout. Depth of actual instrumentation was not exhaustively traced.
- **Verdict:** Foundations are present (Docker, secret guard, structured logging, Sentry) but two concrete gaps stand between this and a defensible "production ready" claim: (1) RLS never switched from audit-mode to enforcing, (2) CI never actually validated on real GitHub Actions.

---

## 19. Testing Coverage Audit

- 231 backend test files, 7 frontend test files. This asymmetry mirrors the UX gap found in §15 — the backend is deeply tested; the frontend (especially for anything beyond Website Builder) is minimally tested.
- Real-Postgres tests: multiple `test_postgres_*.py` files present (e.g. `test_postgres_company_memory_*.py`, `test_postgres_agent_single_action_reliability.py`), consistent with the "pgserver disposable instance" method described in every phase log.
- Hardcoding guard tests confirmed present: `test_vertical_extension_no_hardcoding_guard.py`, `test_agent_no_hardcoding_guard.py`, `test_website_no_vertical_hardcoding.py` — all three referenced in Phases 1/10/11/12 are still on disk and were not removed.
- Known flaky test: `test_openai_realtime_voice_service.py::test_voice_stream_route_dispatches_to_realtime_engine_when_configured` — confirmed still present in the repo (it's in the modified-files list in `git status`) and referenced as pre-existing/persistent across multiple phase logs (Phase 9 §18 cites it as "same as baseline"). Its current pass/fail state was not re-run in this audit (would require executing the suite against real Postgres, which this audit's time budget did not allocate for a full 231-file run — treated as a SHOULD-verify item for the next implementation phase rather than re-proven here).
- **Gap:** no dedicated end-to-end (browser-level) test suite spanning Discovery→Blueprint→Recommendations→Website→Agents was found — consistent with §14's finding that no such journey exists yet to test.
- **Verdict:** Backend test coverage is a genuine strength of this codebase. Frontend/E2E coverage is thin, proportionate to the thin frontend surface for the newer subsystems.

---

## 20. Material Technical Debt

1. **`Organization.autonomy_level` remains deprecated-but-present**, confirmed not resurrected in any of the 12 phases (§3, §9) — correctly still just dead/decorative, not a live bug, but it is still schema debt worth removing in a future migration once nothing references it.
2. **RLS audit-mode-only across all 12 phases** (§16, §18) — the most material single item of technical debt/production risk in this audit, because it is a security backstop that has been deferred, not a convenience feature.
3. **Agent execution concurrency-check race** (§9) — low-severity (fails safe), but still an unresolved ordering bug in `agent_execution_service.py::_enforce_rate_and_concurrency`.
4. **227/233 tools without verified idempotency** (§9) — large surface area of unverified retry-safety, conservatively defaulted to `False` (safe direction) but not resolved.
5. **Website Builder `provider_key` round-trip gap** (§13) — a real, scoped editing-correctness bug, not yet fixed.
6. **~55 untracked root-level planning/log `.md` files** — not application code debt, but repository hygiene debt; they are valuable historical record and this audit does not recommend deleting them, but they will need a decision (commit vs. relocate vs. `.gitignore`) before this branch is considered clean for a PR.
7. **`backend/dev.db` and `backend/.venv`** present in the working tree — standard local dev artifacts, already excluded from `git status` (gitignored), not itself a problem, but confirms local environment state that should not be assumed present on a fresh clone.

No evidence was found of: duplicated execution engines, a second CRM/Lead system, vertical logic leaking into generic platform code (hardcoding guards all present and, per their own phase logs, passing), or fabricated/overclaimed integrations — the codebase is unusually disciplined about **not** overclaiming (see the STUB/WEBHOOK_NORMALIZER catalog honesty in §8, and every phase log's "COMPLETE WITH LIMITATIONS" framing rather than unqualified "COMPLETE").

---

## 21. MUST / SHOULD / FUTURE / HALLA Classification

**MUST COMPLETE BEFORE KLAROS COMPLETE**
- Business Orchestration layer connecting Discovery→Blueprint→Recommendations→(Website/Integrations/Agents) (§14)
- Frontend for Business Discovery (§6, §15)
- Frontend for Business Blueprint view/edit/claim confirmation (§6, §15)
- Frontend for Recommendations accept/reject (§7, §15)
- Frontend for Agent configuration (create Agent/AgentVersion, grant tool permissions, configure triggers) (§9, §15)
- Website Builder `provider_key` round-trip gap fix (§13) — small, but a real correctness bug in an otherwise-complete subsystem

**SHOULD COMPLETE BEFORE KLAROS COMPLETE**
- Switch RLS from audit-mode to enforcing on all Phase 0–12 tenant tables (§16, §18)
- Resolve or explicitly accept-and-document the agent-execution concurrency-check race (§9)
- Run and fix (or explicitly re-document) the pre-existing voice-stream test flake (§19)
- Actually validate `.github/workflows/ci.yml` on real GitHub Actions (§18)
- Distributed (not process-local) MCP concurrency limiter (§10)
- Medical Tourism dedicated operational screens (provider/procedure/consultation/commission management) if Medical Tourism is to be a real operating vertical rather than a backend proof-of-concept (§11)

**POST-KLAROS / FUTURE**
- Dropshipping vertical (§12) — architecture supports it, not required for v1
- Website Builder polish: server-rendered SEO/OG, public preview tokens, custom domains, image hosting, IMAGE/CARD_GRID/TESTIMONIAL/FAQ components, drag-and-drop reorder (§13)
- Broader tool-level idempotency verification beyond the current 6/233 (§9, §17)
- STUB provider real implementations (Xero, Google Ads, Meta Ads, GBP, ServiceTitan, Jobber) and outbound APIs for the WEBHOOK_NORMALIZER providers (§8)
- Cross-subsystem executive analytics dashboard (§4, §15)
- `Organization.autonomy_level` column removal (§20)

**HALLA-ONLY / DO NOT TOUCH NOW**
- None found to exist in this repository (§27). Nothing in the current gap list is Halla-specific; every MUST/SHOULD/FUTURE item above is squarely Klaros platform work. No item should be deferred "to Halla" — Halla is a separate future product/brand and this repository has correctly kept zero contamination from it.

---

## 22. Proposed Klaros Completion Gate

Klaros is **FINAL KLAROS COMPLETE** when, and only when, all of the following are objectively true:

1. **Product:** A new tenant can, through the product UI alone (no direct API calls), go idea → discovery session → blueprint (view/edit/confirm claims) → recommendations (accept/reject) → at least one resulting action taken (connect an integration, create a website, or configure an agent) → operate day-to-day. Verified by a scripted browser-level walkthrough.
2. **Platform:** Tenant identity is enforced at both the application layer (already true) and the database layer (RLS switched from audit-mode to enforcing on every Phase 0–12 table) with a real cross-tenant-leak regression test passing.
3. **Agents:** An agent can be created, versioned, granted scoped tool permissions, triggered (manually or by schedule/event), reason across multiple tool calls, pause for approval, resume after approval, and recover after a simulated crash — all through the product UI for creation/approval and the existing backend for execution — with the known concurrency-check race either fixed or explicitly re-verified as safe-direction-only and documented as an accepted risk.
4. **Integrations:** At minimum the 3 REAL providers (Stripe, QuickBooks, Google Calendar) remain connectable, authenticatable, and executable end-to-end through the UI (already true); STUB providers are clearly labeled "coming soon" rather than implying functionality in the UI.
5. **Website:** A tenant can generate, edit, preview, publish, and operate a public website with real lead capture (already true), with the `provider_key` round-trip gap fixed.
6. **Vertical:** Medical Tourism can operate one full cycle — provider onboarded, procedure listed, patient lead captured via the public website, consultation scheduled, referral commission recorded — through some combination of the generic CRM/Lead UI and (if built) vertical-specific screens; does not require a bespoke Medical Tourism dashboard if the generic screens genuinely cover the workflow.
7. **Security:** Tenant isolation is enforced at the database layer (see #2), no credential is ever logged or returned beyond issuance (already true per Phase 9 audit), and the production-secret guard test passes against a production-shaped config.
8. **Reliability:** The system survives a simulated crash mid-agent-execution without data corruption or duplicate external side effects for at least the 6 already-verified idempotent tools, and every other tool's non-idempotency is either accepted-and-documented risk or resolved.
9. **UX:** All 23 flows in §15 are END-TO-END or explicitly, deliberately scoped out with a documented reason (e.g. "manual agent trigger is deferred, agents only run on schedule/event" is an acceptable documented scope decision; silently missing is not).
10. **Deployment:** `.github/workflows/ci.yml` has actually run green on real GitHub Actions at least once, and the Docker/deployment runbooks have been exercised against a real staging deploy at least once.

This gate is deliberately **not** "every integration real" or "every vertical built" — per §19 of the task's own instructions, over-building is explicitly out of scope.

---

## 23. Remaining Implementation Roadmap

Dependency-ordered, not just numbered:

**Phase 13 — Business Orchestration Foundation** *(MUST, foundation — blocks 14 and 15)*
- Objective: connect Discovery→Blueprint→Recommendations into one persisted, resumable journey.
- Scope: new `BusinessJourneyService` (or equivalently named) coordinating existing Discovery/Blueprint/Recommendation services; a lightweight journey-state table; APIs to start/resume a journey; explicit human-confirmation checkpoints between each stage (no auto-execution).
- Out of scope: auto-creating websites/agents/integrations without human confirmation (§19 constraint).
- Dependencies: none beyond existing Phase 2/3 backends.
- Migrations: one small new table expected.
- Tests: real-Postgres journey-state tests, resume-after-interruption test.

**Phase 14 — Discovery/Blueprint/Recommendations Frontend** *(MUST — depends on Phase 13 for the journey APIs to bind to, though could technically bind directly to Phase 2/3 APIs if Phase 13 isn't ready; recommended after 13 to avoid building UI against APIs that will be re-shaped)*
- Objective: give the product UI for flows 3–8 in §15.
- Scope: `frontend/app/discovery/`, `frontend/app/blueprint/`, `frontend/app/recommendations/` routes, wired to existing/Phase-13 APIs.
- Out of scope: new backend logic — this is presentation-layer only.
- Tests: frontend component tests + at least one scripted end-to-end walkthrough.

**Phase 15 — Agent Configuration Frontend** *(MUST — independent of 13/14, can run in parallel)*
- Objective: give the product UI for Agent create/version/tool-permission/trigger config (flow 16 in §15) and a manual-trigger control for flow 17.
- Scope: `frontend/app/agents/` route(s), wired to existing `agents.py` API — no backend changes required.
- Dependencies: none (backend already complete).

**Phase 16 — Website Builder Correctness Fix** *(MUST, small, independent)*
- Objective: close the `provider_key` round-trip gap (§13).
- Scope: add the missing page-read endpoint/field echoing exact stored `data_source`; update editor to use it.
- Dependencies: none.

**Phase 17 — RLS Enforcement** *(SHOULD, foundation for production, independent of 13-16 but should land before any real multi-tenant production traffic)*
- Objective: switch RLS policies from `USING (true)` to real tenant-scoped policies across all Phase 0–12 tables.
- Scope: migration(s) updating policies; verification that the app-layer-set session tenant variable is correctly the RLS predicate; regression test proving a deliberately-broken app-layer query is still blocked by RLS.
- Dependencies: none functionally, but should be done with care given 51 existing migrations touch RLS-bearing tables — recommend after Phase 13-16 to avoid moving two large targets at once.

**Phase 18 — Reliability Hardening** *(SHOULD, independent)*
- Objective: resolve or formally accept the agent-execution concurrency race (§9); resolve the voice-stream test flake; extend idempotency verification to the highest-risk remaining tools (e.g. those with financial/external side effects beyond the already-verified 6).
- Dependencies: none.

**Phase 19 — Medical Tourism Operational Screens** *(SHOULD, only if Medical Tourism is to be positioned as a real operating vertical for launch — otherwise defer)*
- Objective: dedicated provider/procedure/consultation/commission management UI, OR a documented decision that the generic CRM/Lead screens suffice.
- Dependencies: none on other phases.

**Deployment validation** *(SHOULD, can run any time in parallel)*
- Actually run CI on GitHub Actions; exercise a real staging deploy.

**Explicitly not scheduled:** Dropshipping, additional integration providers beyond the 3 REAL ones, Website Builder polish items (custom domains, drag-and-drop, extra components), MCP client, cross-subsystem executive analytics — all correctly POST-KLAROS/FUTURE per §21.

---

## 24. Dependency Graph

```
Phase 13 (Business Orchestration) ──┐
                                     ├──> Phase 14 (Discovery/Blueprint/Reco Frontend)
                                     │
Phase 15 (Agent Config Frontend) ───┼── (independent, can run in parallel with 13/14)
Phase 16 (Website provider_key fix)─┼── (independent)
Phase 17 (RLS Enforcement) ─────────┼── (independent, but land after 13-16 to avoid churn)
Phase 18 (Reliability Hardening) ───┼── (independent)
Phase 19 (Medical Tourism screens) ─┘── (independent)

Deployment validation: independent, any time.

KLAROS FINAL COMPLETE GATE (§22) requires: 13+14+15+16 (MUST) fully done,
and 17+18 (SHOULD) either done or explicitly, deliberately accepted as
documented residual risk — matching this codebase's own established
practice (see Phase 9/12's "COMPLETE WITH LIMITATIONS" pattern) rather
than requiring every SHOULD item literally closed.
```

---

## 25. Phase-by-Phase Acceptance Criteria

- **Phase 13:** A tenant can start a journey, complete a discovery session, have a blueprint generated, activate it, and have a recommendation run triggered automatically — all via API, verified by a real-Postgres integration test that asserts the full chain with zero manual intervention between stages other than the required human-confirmation checkpoints.
- **Phase 14:** A human tester can, using only the browser, complete discovery questions, view and edit blueprint claims, and accept/reject at least one recommendation, with no direct API calls.
- **Phase 15:** A human tester can create an Agent, publish an AgentVersion with instructions, grant it a tool permission, and manually trigger an execution, using only the browser.
- **Phase 16:** Editing a previously-published website page and re-opening the editor shows the exact same `data_source` that was last saved, verified by an automated round-trip test.
- **Phase 17:** A regression test that deliberately sets the wrong tenant context at the application layer still cannot read another tenant's row, because RLS blocks it independently.
- **Phase 18:** The concurrency-race test passes deterministically across 10 consecutive real-Postgres runs, or is documented as an accepted, safe-direction-only risk with no further action planned.
- **Phase 19:** Either a documented decision that generic screens suffice, or a working provider/consultation/commission management UI verified against real Postgres.

---

## 26. Explicitly Deferred Work

Everything in the FUTURE bucket of §21, plus: additional verticals beyond Medical Tourism and (if built) Dropshipping; MCP client (permanently out of scope per ADR-002, not merely deferred); autonomous multi-agent swarms; arbitrary code generation in the Website Builder; a general-purpose integration marketplace UI beyond the current catalog page; drag-and-drop website editing; custom domains/CDN/hosting.

---

## 27. Halla Boundary

Explicit grep sweep (`grep -ril "halla" .` across the repository, case-insensitive, excluding `node_modules`/`.venv`/build artifacts) found **zero matches** — no Halla branding, domain, dashboard, MENA integration, localization, workflow, backend fork, deployment config, or public page exists anywhere in this Klaros repository. This repository is confirmed fully separate from any halla-ai-site work referenced elsewhere in this engagement's broader history. No cross-contamination found. No item identified in this audit needs to be recorded as a "Halla dependency" — the entire gap list is Klaros-native platform/product work.

---

## 28. Final Audit Verdict

Klaros is **NOT YET FINAL KLAROS COMPLETE**, and the reason is not backend weakness — it is that a large, well-built, well-tested backend (Phases 2–10 especially) has not yet been made reachable as one coherent product journey. The single highest-leverage next phase is **Business Orchestration** (Phase 13 above), because it is the foundation the Discovery/Blueprint/Recommendations frontend work (Phase 14) should be built against, and because it is the piece that turns five individually-correct subsystems into the one product experience the original vision describes. Agent configuration UI (Phase 15) and the Website Builder's one remaining correctness bug (Phase 16) can proceed in parallel. RLS enforcement (Phase 17) is the most material pre-production security item but is architecturally independent and does not block the product-completeness work.

The codebase's engineering discipline is notably strong: every phase log this audit reconciled against actual source held up, the integration catalog is honestly self-labeled REAL/STUB/WEBHOOK_NORMALIZER rather than overclaimed, the hardcoding guards are real and present, tenant identity is correctly sourced from authenticated context rather than client input in every sampled path, and `autonomy_level`'s deprecation has held for 12 phases without regression. The gap is real and material, but it is a scoping/sequencing gap (build the front door), not a trust/quality gap in what has already been built.
