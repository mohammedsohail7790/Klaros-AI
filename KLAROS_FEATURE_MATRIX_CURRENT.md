# Klaros Feature Matrix — Current State

Status legend: IMPLEMENTED (real, DB-backed, tested-in-sample), PARTIAL (real but incomplete/narrow), SCAFFOLD (boundary/structure exists, no working behavior behind it), MOCKED (fake data presented as real), MISSING (not found), UNKNOWN (not verified either way in this pass).

| Feature area | Status | Backend evidence | Frontend evidence | Notes |
|---|---|---|---|---|
| Auth (login/register/refresh/logout) | IMPLEMENTED | `api/v1/auth.py`, `core/security.py:21-90` | `lib/api.ts:155-247` | JWT, bcrypt, silent refresh |
| Team invites | IMPLEMENTED | `users.py`, `public_invites.py`, `security.py:88-107` | `lib/api.ts:198-240`, `/accept-invite` page | |
| RBAC enforcement | IMPLEMENTED | `models/rbac.py`, used across 57 route files | N/A (server-enforced) | also applied to AI actor role |
| CRM: leads | IMPLEMENTED | `api/v1/leads.py`, `models/crm.py` | `/leads`, `/leads/[id]` | incl. dedup, bulk import |
| CRM: customers | IMPLEMENTED | `customers.py` | `/customers`, `/customers/[id]` | timeline, AI summary endpoint |
| CRM: appointments/calendar | IMPLEMENTED | `appointments.py`, Google Calendar sync | `/calendar` | real external sync |
| Jobs/work orders | IMPLEMENTED | `jobs.py` (largest single route file by API surface) | `/jobs`, `/jobs/[id]` | state machine, QA, signoff, PO draft |
| Quotes | IMPLEMENTED | `quotes.py`, `public_quotes.py` | `/quotes`, `/quotes/[id]`, `/quotes/view/[id]` | public no-login customer view/accept |
| Contracts | IMPLEMENTED (internal attestation only) | `contracts.py`, `public_contracts.py` | `/contracts`, `/contracts/[id]`, `/contracts/view/[id]` | no third-party e-sign provider |
| Invoicing | IMPLEMENTED | `invoices.py` | `/finance/invoices` | incl. QuickBooks sync, Stripe checkout |
| AR / collections | IMPLEMENTED | `ar.py` | `/finance/ar` | aging, scheduled collection actions |
| Refunds/credit notes/write-offs | IMPLEMENTED | `refunds.py`,`credit_notes.py`,`writeoffs.py` | via `/finance/invoices` flows | always routed through `ApprovalRequest` |
| Job costing/profitability | IMPLEMENTED | `job_costs.py`, `profitability.py` | `/finance/profitability` | |
| Cash forecast | IMPLEMENTED | `cash.py`, `models/finance.py::CashForecast` | `/finance/cash` | |
| Vendors/subcontractors | IMPLEMENTED | `vendors.py` | `/vendors` | bills, payouts |
| Compliance (licenses) | IMPLEMENTED | `compliance.py` | `/settings/compliance` | expiry detection |
| Marketing (campaigns/content/SEO/outbound/nurture/reactivation) | IMPLEMENTED | 7 dedicated route files, 18+ models | `/marketing/*` (7 sub-pages) | deep, feature-scoped, not unified |
| Retention (opportunities/reminders/reviews/referrals/warranties/risk) | IMPLEMENTED | 6 dedicated route files, 18+ models | `/retention/*` (7 sub-pages) | |
| Billing (Klaros's own SaaS plan billing) | IMPLEMENTED | `billing.py`, `billing_service.py`, `Organization.plan/billing_status` | `/settings/billing` | real Stripe subscription lifecycle |
| Integrations settings | PARTIAL | `integrations.py`, `credential_store.py` | `/settings/integrations` | UI not verified to disclose stub vs real providers |
| AI Morning Brief | PARTIAL | `morning_brief.py`, `ai_provider.py` | `/morning-brief` | deterministic by default, AI prose optional |
| AI lead-qualification advisory | PARTIAL | `ai_qualification_service.py` | `aiQualifyLeadAdvisory` in `lib/api.ts:348-367` | advisory-only, human must apply |
| AI Voice Receptionist | IMPLEMENTED (as a governed, narrow feature) | `voice_conversation_service.py`, `openai_realtime_voice_service.py`, `voice_stream.py` | `/settings/voice` | two selectable engines, governed tool calls only |
| Automation engine | IMPLEMENTED | `models/automation.py`, `automation_service.py`, `automation_workflow.py` | `/automations`, `/settings/automation` | deterministic trigger/condition/action; Temporal-backed durable waits |
| Approvals | IMPLEMENTED | `approvals.py`, `models/approval.py` | `/approvals` | gates refunds/write-offs/credit-notes/certain sends |
| AI activity log | IMPLEMENTED (presumed) | `ai_activity.py`, `models/ai_invocation.py` | `/ai-activity` | not independently deep-verified |
| Knowledge/RAG | IMPLEMENTED | `knowledge.py`, `knowledge_retrieval_service.py`, pgvector | `/settings/knowledge` | real chunk/embed/retrieve pipeline |
| Company Memory | PARTIAL | `company_memory.py`, model+service | `/settings/memory` | not consumed by voice AI's realtime engine (self-documented gap) |
| Notifications | IMPLEMENTED (presumed) | `notifications.py`, `models/notification.py` | in-app (toast notifications per recent commit history) | not deep-verified |
| Events (audit/inspection) | IMPLEMENTED | `events.py`, `models/event.py` incl. dead-letter | `/events` | |
| Exceptions/operations dashboard | IMPLEMENTED | `exceptions.py`, `operations.py` | `/operations`, `/exceptions` | delay detection |
| Dashboard | IMPLEMENTED | `dashboard.py` | `/dashboard` | ~16 concurrent calls per `lib/api.ts` retry-logic comment |
| Business discovery | MISSING | none found | none found | |
| Recommendation engine (cross-category) | MISSING | none found (only per-insight "next action" text) | none found | |
| Website creation | MISSING | none found | none found | |
| General configurable AI agents | MISSING | `ai/execution_service.py` explicitly scaffolds the boundary, not the agent | none | |
| MCP | MISSING | none found | none found | |
| Ecommerce (product/inventory/order) | MISSING | no models found | none found | blocks dropshipping use case |
| Provider/supplier directory | MISSING | no models found | none found | blocks medical-tourism use case |
| Frontend automated tests | MISSING | N/A | `package.json` has no test script | |
| Backend automated tests | IMPLEMENTED (collection verified) | 182 files, 1,417 tests collected live | N/A | pass/fail status not verified in this audit |
| CI pipeline | MISSING | no `.github/workflows` for this project | N/A | |
| Postgres RLS | MISSING | no `CREATE POLICY` found | N/A | app-level tenant filtering only |
