# Klaros — API Evolution Plan

Status: proposal only. No routes were added or modified. Baseline: 55 `include_router` mount points, ~64 logical resource groups (`KLAROS_API_INVENTORY_CURRENT.md`, verified), all under `/api/v1`.

## 1. Existing endpoints — preserve as-is

All 55 existing router modules are preserved unchanged: auth, users, vendors, compliance, warranties, events, tools, approvals, ai_activity, integrations, quickbooks_oauth, google_calendar_oauth, google_calendar, leads, customers, appointments, crm, dashboard, billing, jobs, workers, exceptions, operations, organizations, invoices, quotes, contracts, public_quotes, public_contracts, public_leads, public_invites, payments, ar, refunds, credit_notes, writeoffs, job_costs, profitability, cash, finance, marketing_campaigns, marketing_attribution, marketing_content, marketing_seo, marketing_outbound, marketing_nurture, marketing_reactivation, marketing, retention_opportunities, retention_reminders, retention_reviews, retention_referrals, retention_campaigns, retention, morning_brief, automation, automations, company_memory, notifications, knowledge, webhooks, marketplace_webhooks, voice, voice_stream. None are deprecated by this plan.

**Extended (new sub-routes on existing routers, not new routers)**: `retention_referrals.py` gains commission-related endpoints once `ReferralCommission` exists (`KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §3) rather than a new router, since it's the same resource family. `integrations.py` gains a `GET /integrations/catalog` endpoint reading `IntegrationProviderCatalog` (`KLAROS_INTEGRATION_MARKETPLACE_SPEC.md`) alongside its existing connection-management endpoints.

## 2. New endpoint groups

| Group | Prefix | Key routes | Auth | Spec |
|---|---|---|---|---|
| Business Discovery | `/business-discovery` | `POST /sessions`, `POST /sessions/{id}/messages`, `GET /sessions/{id}/gaps` | authenticated, RBAC (`MANAGE_BLUEPRINT`, new permission) | `KLAROS_BUSINESS_DISCOVERY_SPEC.md` §7 |
| Business Blueprint | `/business-blueprint` | `GET /`, `PATCH /sections/{key}`, `POST /claims/{id}/confirm`, `POST /claims/{id}/reject` | authenticated, RBAC | `KLAROS_BUSINESS_BLUEPRINT_SPEC.md` §9 |
| Recommendation | `/recommendations` | `GET /`, `POST /{id}/accept`, `POST /{id}/dismiss` | authenticated, RBAC | `KLAROS_INTEGRATION_MARKETPLACE_SPEC.md` §5 |
| Integration Marketplace | `/integrations/catalog` (sub-route of existing `integrations.py`, see §1) | `GET /` | authenticated | `KLAROS_INTEGRATION_MARKETPLACE_SPEC.md` §2 |
| Agent | `/agents` | `GET/POST /`, `GET /{id}`, `POST /{id}/versions`, `POST /{id}/execute`, `GET /{id}/executions`, `GET /executions/{id}/steps`, `POST /executions/{id}/approve`, `POST /executions/{id}/reject` | authenticated, RBAC (`MANAGE_AGENTS`, `EXECUTE_AGENT`, new permissions) | `KLAROS_AI_AGENT_ARCHITECTURE.md` |
| Website | `/websites` | `GET/POST /sites`, `GET /sites/{id}/versions`, `POST /sites/{id}/versions/{v}/preview`, `POST /sites/{id}/publish-requests`, `POST /publish-requests/{id}/approve` | authenticated, RBAC | `KLAROS_WEBSITE_BUILDER_SPEC.md` |
| Public appointments (new, narrow) | `/public/appointment-requests` | `POST /` (creates a `Lead` + `ConsultationRequest`, never writes `Appointment` directly) | **public**, signed-token or rate-limited pattern matching `public_leads.py` | `KLAROS_WEBSITE_BUILDER_SPEC.md` §6 |
| Medical Tourism domain | `/providers`, `/procedures`, `/consultations` | standard CRUD + `POST /providers/match` (recommendation-style provider matching, deterministic ranking not a black-box AI call) | authenticated, RBAC | `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §3 |
| Dropshipping domain | `/products`, `/skus`, `/suppliers`, `/orders` | standard CRUD + `POST /orders/{id}/fulfill` (triggers `SupplierOrder`) | authenticated, RBAC | `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §4 |

## 3. Deprecated endpoints

**None.** No existing endpoint is removed or superseded by this plan — every new capability is additive. The one naming ambiguity the original audit flagged (`automation.py` vs `automations.py` — possibly duplicate resource groups, unresolved in the prior audit pass) should be resolved by direct code comparison **before** Phase 8 (Workflow Generation) adds AI-generated-workflow endpoints to either — flagged here as a pre-Phase-8 cleanup item, **UNKNOWN — REQUIRES VERIFICATION** which of the two is canonical.

## 4. Cross-cutting API conventions carried into new endpoints

All new authenticated routes follow the existing pattern exactly: `CurrentUser` dependency → RBAC permission check → Pydantic request validation → service call → response model. All new public routes follow the existing `public_leads.py`/`public_quotes.py` pattern exactly: signed single-purpose token or rate-limited-and-unauthenticated (never a new auth scheme). All new list endpoints support the same pagination/filtering conventions already used across the 55 existing routers (**UNKNOWN — REQUIRES VERIFICATION** of the exact existing pagination parameter names, not independently confirmed in either audit pass — new endpoints should match whatever that convention is, discovered at implementation time, not invent a second one).

## 5. Idempotency

New write endpoints that trigger multi-step side effects (`POST /agents/{id}/execute`, `POST /sites/{id}/publish-requests`, `POST /orders/{id}/fulfill`) accept an `idempotency_key` following the existing pattern already proven on `createLead`/`createJob`/`triggerInvoiceFromJob` (per audit §15) — this is a convention to carry forward, not a new mechanism to invent.

## Cross-references

`KLAROS_DATABASE_EVOLUTION_PLAN.md` (backing tables), `KLAROS_FRONTEND_EVOLUTION_PLAN.md` (consumers), `KLAROS_SECURITY_EVOLUTION_PLAN.md` (RBAC additions).
