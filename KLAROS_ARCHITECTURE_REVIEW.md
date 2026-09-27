# Klaros AI — Architecture Review

**Status:** Final review pass before Phase 0. Produced by reading all 28 existing architecture documents in full and independently re-verifying their central claims against the live repository (`/Users/mohammedsohail/Desktop/Klaros AI`) on 2026-09-23. This document does not repeat the 28 source documents; it validates them, corrects them where the repo disagrees, and answers the 20 executive questions the review was commissioned to answer.

No application code, schema, migration, dependency, config, or test file was modified to produce this review or any of the 20 documents in this set. Only new `KLAROS_*.md` files were created, in the repo root.

---

## 1. What Klaros is today (verified)

Klaros is a mature, production-shaped **field-service / home-services operations SaaS** — directly comparable to ServiceTitan or Jobber — not an AI business-discovery or website-builder platform. Verified inventory:

- **Backend:** FastAPI (`fastapi==0.141.1`), SQLAlchemy 2.0 async (`asyncpg`/`aiosqlite`), Alembic (39 migrations, `0001`–`0039`), ~65 `include_router` registrations in `backend/app/api/v1/router.py`, ~208 SQLAlchemy models.
- **Tools:** 233 `Tool` subclasses across 54 files under `backend/app/tools/builtin/`, executed through a single, coherent, first-party `ToolRegistry` (`backend/app/tools/registry.py`) — not MCP, not LangChain.
- **Frontend:** Next.js 16 App Router, React 18.3.1, TypeScript, Tailwind. 57+ pages under `frontend/app/`, one hand-written 3,835-line API client (`frontend/lib/api.ts`), no component library, no state-management library.
- **Workflow/automation:** Real Temporal (`temporalio==1.8.0`) with a genuine worker (`backend/app/workers/main.py`) registering real workflows (`EventProcessingWorkflow`, `InvoiceOverdueWorkflow`, `LeadQualificationWorkflow`, `JobLifecycleWorkflow`), **plus** a separate deterministic trigger/condition/action Automation Engine (`backend/app/services/automation_service.py`) driven off an in-process event-worker tick loop. Both coexist and both are real — this is not documentation drift.
- **Event bus:** Real and durable — `backend/app/events/bus.py` writes to Postgres before touching transport, with `(tenant_id, idempotency_key)` dedup. Redis Streams in prod, in-memory transport in dev/test.
- **Approvals:** Real (`ApprovalRequest`/`ApprovalStatus`/`ApprovalExecutionStatus`), integrated directly into `ToolRegistry.execute()`.
- **Audit:** Real, two tables — `AuditLog` (every tool execution, all outcomes) and `AIInvocationLog` (raw LLM provider call trail).
- **Integrations:** Stripe, QuickBooks, Google Calendar, Twilio, SendGrid, OpenAI, Anthropic are real, code-verified. Xero, Google Ads, Meta Ads, Google Business Profile, ServiceTitan, Jobber are honest, self-disclosed stubs (adapter classes exist and explicitly raise "not implemented"). Angi/Thumbtack/Nextdoor are a generic HMAC webhook normalizer, not live API integrations.
- **Knowledge/RAG:** Real — `pgvector`-backed `KnowledgeChunk.embedding`, HNSW index (migration `0032`), OpenAI `text-embedding-3-small` embeddings.
- **Company Memory:** Real, separate model (`backend/app/models/company_memory.py`) — structured, keyed facts with an AI-propose/human-confirm gate, deliberately **not** a vector store.
- **Voice:** Real and substantial — OpenAI Realtime API integration with actual tool-calling wired to `AIExecutionService`, Twilio telephony, STT/TTS abstraction.
- **CI/CD:** **Confirmed absent.** No `.github/`, no `.gitlab-ci.yml`, no `.circleci/`, no CI config of any kind anywhere in the repository.
- **Frontend tests:** **Confirmed absent.** No jest/vitest/playwright/testing-library in `frontend/package.json`, no `*.test.*`/`*.spec.*` files anywhere under `frontend/`.
- **Backend tests:** `backend/pytest.ini` exists; `backend/tests/` contains 183 test files (pass/fail status not verified in this pass — collection/existence only).
- **Deployment config:** No Kubernetes, no `render.yaml`, no `vercel.json`, no `fly.toml`. Only `docker-compose.yml` (8 services: postgres/pgvector, redis, temporal, temporal-ui, backend, worker, event-worker, frontend) and an unexercised `docker-compose.prod.yml` overlay.

What is confirmed **absent** as a subsystem: business discovery, requirements engine, blueprint/spec model, recommendation engine, website builder, general-purpose configurable AI agents, an agent registry, MCP, e-commerce/product-catalog, provider/supplier directory, Postgres RLS.

## 2. What Klaros is becoming

Per the 20 blueprint documents (KLAROS_TARGET_ARCHITECTURE.md and its 19 companions), the target is: an AI-assisted **business-launch and -operation platform** layered *on top of* the existing operations SaaS, where a prospective business owner describes their business in free text, is asked adaptive follow-up questions, receives a versioned, human-reviewable Business Blueprint, gets domain-appropriate integration/agent/workflow recommendations, configures governed AI Agents that operate through the existing tool-execution boundary, and can generate and publish a website from a fixed component registry (never arbitrary LLM-generated code) — all before or alongside operating the business through the existing operational modules (CRM, jobs, finance, marketing, retention).

Critically, the blueprint documents are consistent and disciplined on one point verified independently in this review: **the new Agent Runtime is designed to be built on top of the existing `AIExecutionService` → `ToolRegistry` boundary, not to replace it, and not to introduce a second tool-execution framework.** This claim is corroborated by direct code reading (see §6 below) and is judged accurate.

## 3. What should be preserved

Everything in §1 is Preserve or Extend, not Replace. Specifically:
- `ToolRegistry` and its execution pipeline (kill-switch → RBAC → tenant-scope → billing cap → schema validation → policy resolution → execute → audit) — this is the correct foundation for agent tool-calling; do not rebuild it.
- Temporal + the deterministic Automation Engine — both are real and serve different purposes (long-running/durable workflows vs. simple trigger-condition-action rules). Reuse both; do not add a third.
- The event bus, approval system, audit log, credential encryption (Fernet), knowledge/RAG stack, and Company Memory — all real, all correctly scoped, all reusable as-is.
- `Lead`, `Customer`, `Appointment`, `Vendor`/`VendorBill`, `Invoice`/`Payment`, `ApprovalRequest`, `IntegrationConnection`, `CompanyMemory` — must never be forked or duplicated per-vertical (see KLAROS_FINAL_DOMAIN_MODEL.md).
- The existing 55+ routers and 57+ frontend pages — the new work is additive route/page groups, not a rewrite.

## 4. What is missing (confirmed gaps, not aspirational)

1. **Postgres RLS** — tenancy is enforced entirely at the application layer today (manual `.where(Model.tenant_id == ...)` convention; see §6). No RLS policy exists on any table.
2. **No CI pipeline** — nothing gates merges; no automated test run, lint, typecheck, or security scan exists today.
3. **No frontend test framework** — zero frontend test coverage.
4. **No staging environment / deployable manifest** — only local `docker-compose.yml`; `docker-compose.prod.yml` is explicitly self-documented as never exercised against a real Docker daemon.
5. **No agent framework** — no `class *Agent` exists anywhere in the codebase. `AIExecutionService.request_tool_execution()` is a thin pass-through to `ToolRegistry.execute()`; its own docstring states it "does not build the autonomous agent that produces ToolRequests."
6. **`Organization.autonomy_level` is inert** — see §5. This is the single most consequential correction this review makes to the assumed current state, and it is now independently re-verified for a third time (once each by the prior audit, the blueprint documents, and this review's own backend research pass).
7. **No RBAC/loop/depth protection for autonomous execution**, no business-discovery/blueprint/recommendation/website-builder subsystems, no product/order/supplier/provider domain tables.

## 5. `Organization.autonomy_level` — re-verified from scratch

**Definition:** `backend/app/models/organization.py:29-31`
```python
autonomy_level: Mapped[str] = mapped_column(
    String(20), nullable=False, default=AutonomyLevel.LEVEL_0
)
```
`AutonomyLevel` is a `StrEnum` (`organization.py:12-17`, LEVEL_0..LEVEL_4). The column is introduced once, in `alembic/versions/0001_initial_schema.py`, and never migrated again.

**Every read site found by exhaustive grep across the whole backend (`autonomy_level` and `autonomy`):**
- `organization.py:29` — the field definition itself.
- `organization.py:66` — a comment on the *unrelated* `ai_paused` field, explicitly stating: *"Distinct from autonomy_level above, which remains unenforced/decorative."* The codebase documents its own dead field.
- `app/tools/policy.py:60`, `app/tools/builtin/automation_policy_tools.py:124` — a tool named `automation.get_autonomy_stats`; its body (lines 130-156) computes statistics purely from `AuditLog` rows and never reads `Organization.autonomy_level`.
- `app/api/v1/automation.py:24-30` — the `/automation/autonomy-stats` endpoint that calls the above tool; still no reference to the field.
- One unrelated docstring hit in `app/api/v1/dashboard.py:3`.

**Conclusion:** `Organization.autonomy_level` is written once (default only, never updated by any code path) and read by zero executing logic anywhere in the application. Nothing branches on it, gates on it, or displays it as an enforcement mechanism. It is dead/decorative, confirmed independently by (a) the prior forensic audit, (b) all three blueprint documents that address it (`KLAROS_GAP_ANALYSIS.md`, `KLAROS_AI_AGENT_ARCHITECTURE.md`, `KLAROS_EXECUTIVE_ARCHITECTURE_SUMMARY.md`), and (c) this review's independent backend research pass.

**The real, enforced AI kill-switch is a different field entirely:** `Organization.ai_paused` (boolean), enforced inside `ToolRegistry.execute()` (`registry.py:150-160`) — when true, any tool call with `actor_type != ActorType.USER` is rejected with `ToolKillSwitchError` before permission/policy checks even run. This is a binary global stop, not a graduated autonomy model.

**Decision (carried into KLAROS_FINAL_SECURITY_MODEL.md and KLAROS_FINAL_AGENT_MODEL.md):** deprecate `autonomy_level` as an enforcement concept, keep the column as a compatibility/UX-hint field only (at most a default suggestion shown in onboarding UI, never read by any policy code), and build the real autonomy model at the **Agent** level (four tiers: Observe / Recommend / Execute-with-approval / Execute-autonomous), composed with — never replacing — the existing per-tool `ActionPolicy` and the existing `ai_paused` kill-switch. No migration is required to deprecate it (no data depends on it); a migration *is* required later only if the column is ever physically dropped, which should not happen before the new Agent autonomy model has shipped and been observed in production for at least one full release cycle (see KLAROS_MIGRATION_STRATEGY.md).

## 6. Governance chain (verified against `ToolRegistry.execute()`, `registry.py:126-224`)

For every existing tool execution today, the enforcement order is:
1. Tenant isolation (via `CurrentUser.tenant_id` from the decoded JWT, propagated into `ExecutionContext`)
2. AI kill-switch (`Organization.ai_paused`, non-USER actors only)
3. RBAC permission check (`role_has_permission()`, `app/models/rbac.py`)
4. Tenant-scope check (tool-declared `tenant_scoped` flag)
5. Billing/usage-limit check
6. Pydantic input-schema validation
7. Policy resolution (AUTO / APPROVAL_REQUIRED / BLOCKED via `PolicyService`)
8. Execute (or raise `ToolApprovalRequiredError` and create an `ApprovalRequest`)
9. Audit write (`AuditLog`, on every outcome path including denial/block/pending)

This is a real, coherent, already-enforced governance chain. The Agent Runtime's job (KLAROS_FINAL_AGENT_MODEL.md) is to insert **two new checks** ahead of step 3 — agent identity/permission and agent autonomy-tier evaluation — without altering this existing sequence. No bypass path was found in this review (any code path that calls `tool.execute()` directly instead of `ToolRegistry.execute()` would be a bypass; grep found none — all 233 tools are invoked only through the registry).

## 7. Biggest risks (see full reasoning in the Final Security Model and Gap docs)

- **Architectural risk:** building agent autonomy before RLS and before the agent-level governance chain is fully specified would let a future agent-tier feature quietly outrun the tenant-isolation guarantee the platform currently only has at the application layer.
- **Security risk:** app-layer-only tenant isolation (manual `.where(tenant_id==...)` on every query, no defense-in-depth) is CRITICAL-classified in KLAROS_FINAL_SECURITY_MODEL.md — a single missed `.where()` clause in a new endpoint is a cross-tenant data leak with no second layer to catch it.
- **Data-model risk:** shipping a giant unstructured JSON blob for the Business Blueprint instead of the claim/section model would make it unauditable and unversionable; the canonical model (KLAROS_FINAL_BUSINESS_BLUEPRINT.md) exists specifically to avoid this.
- **AI/agent risk:** granting a new Agent Runtime the full 233-tool surface with only a system prompt as the safety mechanism, before AgentToolPermission/autonomy-tier scaffolding exists — this is explicitly the #1 item in `KLAROS_DO_NOT_BUILD_YET.md` and is upheld in this review.

## 8. Executive Q&A (20 questions)

**1. What is Klaros today?** A mature, real field-service/home-services operations SaaS with a genuinely coherent tool-execution, approval, audit, event, workflow, knowledge, and voice stack — not yet an AI business-launch platform.

**2. What is Klaros becoming?** An AI-assisted business discovery → blueprint → recommendation → agent/workflow configuration → website-builder → launch platform, built as an additive layer on the existing operations SaaS and its existing governance primitives.

**3. What should be preserved?** ToolRegistry, AIExecutionService boundary, Temporal, the deterministic Automation Engine, the event bus, approvals, audit log, credential encryption, knowledge/RAG, Company Memory, core CRM/finance entities, and the existing 55+ routers/57+ pages. See KLAROS_CURRENT_VS_TARGET.md for the full matrix.

**4. What is missing?** RLS, CI, frontend tests, a staging/deployable manifest, an agent framework, a real (non-decorative) autonomy model, business-discovery/blueprint/recommendation/website-builder subsystems, and product/order/supplier/provider domain tables. See §4.

**5. Biggest architectural risk?** Sequencing — building any part of the Agent Runtime's autonomous-execution tiers before Phase 0 governance/RLS foundations land. See KLAROS_IMPLEMENTATION_DEPENDENCY_GRAPH.md.

**6. Biggest security risk?** Absence of Postgres RLS as a second, DB-enforced layer of tenant isolation underneath the correct-but-single-layer application filtering. CRITICAL in KLAROS_FINAL_SECURITY_MODEL.md.

**7. Biggest data-model risk?** An unstructured Blueprint blob instead of the versioned section+claim model — avoided by design in KLAROS_FINAL_BUSINESS_BLUEPRINT.md.

**8. Biggest AI/agent risk?** Granting broad tool access to an autonomous agent loop before tool-policy, approval, and audit scaffolding is proven at the agent level (not just the tool level). Mitigated by the tier model in KLAROS_FINAL_AGENT_MODEL.md.

**9. What should Phase 0 accomplish?** RLS rollout (phased), CI pipeline, tenant-isolation test suite, staging environment, migration-safety tooling, and closing the autonomy-model gap (deprecate `autonomy_level`, introduce the `ai_paused`-composed Agent tier scaffold at the data-model level only — no autonomous execution yet). See KLAROS_PHASE_0_IMPLEMENTATION_PLAN.md.

**10. What should Phase 1 accomplish?** The smallest useful platform foundation: capability/tool catalog exposure, integration provider catalog, domain-extension registry, credential/connection abstraction reuse — no user-facing discovery/blueprint/agent features yet. See KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md.

**11. What should NOT be built yet?** The 11 items in `KLAROS_DO_NOT_BUILD_YET.md`, all reviewed and upheld in KLAROS_ARCHITECTURE_RECONCILIATION.md — notably: no third-party agent framework/MCP, no unrestricted SQL tool, no arbitrary LLM-generated production code, no per-vertical CRM/finance duplication, no `if business_type == X` branching, no chatbot-replaces-UI approach.

**12. What is the canonical Business Blueprint?** `BusinessBlueprint` (1:1 versioned child of `Organization`) + `BlueprintSection` (JSONB per fixed section key) + `BlueprintClaim` (atomic, typed, confidence-scored, evidence-linked facts). Full model in KLAROS_FINAL_BUSINESS_BLUEPRINT.md.

**13. What is the canonical Agent model?** An `Agent`/`AgentVersion` identity+configuration record with declared tools (`AgentToolPermission`), a 4-tier autonomy setting, memory/knowledge references, triggers, execution limits, and a mandatory approval/audit path — executing exclusively through the existing `ToolRegistry`. Full model in KLAROS_FINAL_AGENT_MODEL.md.

**14. How does Agent autonomy interact with tool policies?** Additively, never as a replacement: an agent's tier can only *narrow* what its tools would otherwise allow under `ActionPolicy`; it can never widen it. Financial-risk and system-blocked tools remain approval-gated regardless of tier. See KLAROS_FINAL_SECURITY_MODEL.md §Governance Order.

**15. How will tenant isolation work?** Today: manual per-query `.where(tenant_id==...)`, confirmed as convention not enforcement (`TenantScopedMixin` is column-definition-only, per `backend/app/db/base.py:25-27`). Target: phased RLS rollout (instrument → permissive-with-bypass → enforce by sensitivity tier → RLS-on-day-one for new tables) as detailed in KLAROS_FINAL_SECURITY_MODEL.md and KLAROS_FINAL_DATABASE_ARCHITECTURE.md.

**16. How will integrations work?** Existing per-tenant `IntegrationConnection` + Fernet-encrypted credential store, unchanged, joined with a new tenant-*independent* `IntegrationProviderCatalog` reference table that never overwrites real connection state and never lets a STUB provider render as CONNECTED. See KLAROS_FINAL_INTEGRATION_MODEL.md.

**17. How will domains like Medical Tourism and Dropshipping plug in?** Additive FK-referencing tables + a `VerticalExtension` registry row + optional Blueprint sub-schema + new narrowly-scoped Tools, with core services never importing a specific vertical by name — no `if business_type == X` anywhere. See KLAROS_FINAL_DOMAIN_MODEL.md and KLAROS_VALIDATION_SCENARIOS.md.

**18. How will the website builder work?** Blueprint → deterministic Website Requirements → fixed-component-registry Page/Section/Content specification (JSON, never code) → LLM fills only declared text slots → Preview (same registry as production) → human approval (reusing `ApprovalRequest`) → publish. Never arbitrary LLM-generated code. See KLAROS_FINAL_FRONTEND_ARCHITECTURE.md and the website-builder sections there.

**19. What is the dependency order?** Phase 0 (governance/RLS/CI foundation) blocks everything; Phase 1 (catalogs/registries) blocks Discovery/Blueprint/Recommendation/Marketplace/Website; the Agent Runtime is hard-gated behind Phase 0's security gate specifically. Medical Tourism and Dropshipping validations are parallelizable once the extension pattern lands. Full graph in KLAROS_IMPLEMENTATION_DEPENDENCY_GRAPH.md.

**20. What exactly should engineering build first?** See KLAROS_FIRST_IMPLEMENTATION_SLICE.md — the concrete first slice is standing up Postgres RLS instrumentation (read-only audit mode, zero behavior change) on the three highest-risk existing tables plus a minimal CI pipeline (lint + typecheck + existing pytest suite), because every other Phase 0 item depends on knowing RLS is safe to enable and having a gate that would have caught the manual-filtering mistakes already latent in the app-layer convention.

---
*Sources: all 28 existing repo-root documents; direct code reads of `backend/app/models/organization.py`, `backend/app/tools/registry.py`, `backend/app/ai/execution_service.py`, `backend/app/db/base.py`, `backend/app/api/v1/router.py`, `backend/app/api/v1/automation.py`, `backend/app/api/v1/automations.py`, `docker-compose.yml`, `frontend/package.json`, `backend/requirements.txt`, and exhaustive repo-wide greps for `autonomy_level`, CI config files, and test files, performed 2026-09-23.*
