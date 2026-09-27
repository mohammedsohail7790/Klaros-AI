# Klaros AI — Current vs. Target Architecture

Definitive current-state vs. target-state matrix. "Current" is drawn from direct code verification (see KLAROS_ARCHITECTURE_REVIEW.md); "Target" is drawn from the reconciled blueprint documents.

## Capability Matrix

| Capability | Current | Target | Reuse | Extend | Refactor | Replace | New |
|---|---|---|---|---|---|---|---|
| Web framework (FastAPI) | Real, 65+ routers | Same, +~9 new route groups | Yes | — | — | — | — |
| Frontend framework (Next.js App Router) | Real, 57+ pages | Same, + ~7 new route groups | Yes | — | — | — | — |
| Tool execution (`ToolRegistry`) | Real, 233 tools, full governance pipeline | Same pipeline, +2 new pre-checks (agent identity, agent autonomy tier) | Yes | Yes (new checks inserted) | — | — | — |
| AI provider abstraction (`ai_provider.py`) | Real, raw `httpx`, 6 providers, deterministic fallback | Same abstraction, add task-routing + cost/latency tracking | Yes | Yes | — | — | — |
| Agent runtime / `class Agent` | **Absent** | `Agent`/`AgentVersion`/`AgentExecution`/`AgentToolPermission` built on top of `AIExecutionService` | — | — | — | — | New |
| Organization autonomy (`autonomy_level`) | Inert/decorative, never read | Deprecated as enforcement; kept as UX-hint compat field | — | — | Yes (repurpose) | — | — |
| AI kill-switch (`ai_paused`) | Real, enforced in `ToolRegistry.execute()` | Unchanged, composed with new Agent autonomy tiers | Yes | — | — | — | — |
| Approval system (`ApprovalRequest`) | Real | Reused for Agent approvals and Website `PublishRequest` | Yes | Yes | — | — | — |
| Audit (`AuditLog`, `AIInvocationLog`) | Real | Reused, + `AIInvocationLog.agent_execution_id` FK | Yes | Yes (additive column) | — | — | — |
| Temporal workflows | Real, genuine worker | Same worker, + new workflow defs (`AgentExecutionWorkflow`, `BusinessLaunchWorkflow`) | Yes | Yes | — | — | — |
| Deterministic Automation Engine | Real, separate from Temporal | Unchanged; Workflow Generation (item N) compiles into it or Temporal per workflow shape, never a third engine | Yes | Yes | — | — | — |
| Event bus | Real, Postgres-durable, Redis Streams transport | Unchanged | Yes | — | — | — | — |
| Multi-tenancy | App-layer only, manual `.where(tenant_id==...)` | + Postgres RLS, phased rollout | — | Yes (add DB layer) | — | — | — |
| RBAC | Real, code-level StrEnum, ~90 permissions | + `MANAGE_BLUEPRINT`, `MANAGE_AGENTS`, `EXECUTE_AGENT`, `MANAGE_WEBSITE`, `PUBLISH_WEBSITE`, `MANAGE_INTEGRATIONS_CATALOG` | Yes | Yes | — | — | — |
| Credential encryption | Real, Fernet | Unchanged | Yes | — | — | — | — |
| Integrations framework (`app/integrations/`) | Real adapters (3 real, 6 stub, 3 webhook-normalizer) | Same adapters + new tenant-independent `IntegrationProviderCatalog` table | Yes | Yes | — | — | New (catalog only) |
| Knowledge/RAG (pgvector) | Real | Unchanged; consumed by Discovery/Blueprint as source evidence | Yes | Yes | — | — | — |
| Company Memory | Real, propose/confirm gate | Unchanged; confirmed Blueprint claims mirror into it | Yes | Yes | — | — | — |
| Voice (Realtime API, Twilio) | Real | Unchanged | Yes | — | — | — | — |
| CRM core (`Lead`, `Customer`, `Appointment`) | Real | Unchanged core; vertical extensions FK-reference these, never fork them | Yes | Yes (via FK extension tables) | — | — | — |
| Finance core (`Invoice`, `Payment`, `Vendor`/`VendorBill`) | Real | Unchanged; Dropshipping's `Supplier`/`Order` domain is genuinely new (Vendor/VendorBill insufficient) | Yes | Yes | — | — | New (dropshipping order domain) |
| Business Discovery | **Absent** | New adaptive-questioning service over `DiscoverySession` | — | — | — | — | New |
| Business Blueprint | **Absent** | New `BusinessBlueprint`/`BlueprintSection`/`BlueprintClaim` | — | — | — | — | New |
| Recommendation Engine | **Absent** | New, plugin-based (no hardcoded per-vertical rules) | — | — | — | — | New |
| Integration Marketplace UI/status taxonomy | Partial (settings/integrations page exists, no catalog/recommended/coming-soon taxonomy) | Full taxonomy (CONNECTED/RECOMMENDED/REQUIRED/OPTIONAL/AVAILABLE/NOT_CONNECTED/COMING_SOON/STUB/CUSTOM) | — | Yes | — | — | New (taxonomy + catalog) |
| Website Builder | **Absent** | Fixed-component-registry pipeline, human-approved publish | — | — | — | — | New |
| Domain extensibility (Medical Tourism, Dropshipping) | **Absent** | `VerticalExtension` registry + additive tables per vertical | — | — | — | — | New |
| CI/CD | **Absent, confirmed** | 8-stage pipeline (lint/typecheck/test/security/build/migration-check/staging/prod-approval) | — | — | — | — | New |
| Frontend tests | **Absent, confirmed** | Vitest + RTL + Playwright (decision made in KLAROS_FINAL_TESTING_ARCHITECTURE.md) | — | — | — | — | New |
| Staging environment | **Absent** | New deploy target, mirrors prod topology | — | — | — | — | New |
| Postgres RLS | **Absent** | Phased rollout, all new tables RLS-on-day-one | — | Yes (existing tables) | — | — | New (new tables) |
| MCP | **Absent, and deliberately not adopted** | Remains not adopted | — | — | — | — | — |

## Narrative

The dominant pattern across every row is **Reuse/Extend, not Replace**. Of ~30 major subsystems evaluated, zero are marked Replace. This is a direct, verified consequence of Klaros's current backend being more architecturally mature than a typical pre-Phase-0 codebase — the tool-execution, approval, audit, event, and workflow primitives already exist and are correctly shaped to carry the new AI-agent and business-launch capabilities without being torn out.

The "New" rows cluster into exactly the areas the 8 forensic-audit documents independently confirmed as absent: Discovery, Blueprint, Recommendation, Website Builder, domain-vertical tables, CI, frontend tests, staging, and RLS. This convergence — audit-confirmed-absent capabilities mapping 1:1 onto blueprint-proposed-new capabilities, with zero unexplained overlap — is itself evidence the 20 blueprint documents were grounded in the actual codebase rather than written speculatively.

The one row that is simultaneously Extend and Refactor is `Organization.autonomy_level`: it is not deleted (no data/migration risk), not left as-is (it would mislead future engineers into thinking it's enforced), but repurposed as a non-authoritative compatibility field while the real autonomy model moves to the Agent layer. See KLAROS_ARCHITECTURE_REVIEW.md §5 for the full re-verification and KLAROS_FINAL_SECURITY_MODEL.md for the enforcement model that replaces it functionally.
