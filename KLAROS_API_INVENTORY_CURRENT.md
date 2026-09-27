# Klaros API Inventory — Current State

Source: `backend/app/api/v1/router.py:70-134` (exhaustive — this is every router mounted into the app) plus the corresponding file in `backend/app/api/v1/`. All mounted under prefix `/api/v1`. Each router's own internal path/method list was **not individually enumerated for all 55 files** in this pass (that would require opening all 55) — this inventory gives the router-level surface (confirmed complete) and marks per-route method/path detail as sampled only where explicitly noted.

| # | Router module | Domain | Auth class (inferred from naming + spot checks) |
|---|---|---|---|
| 1 | `auth.py` | login/register/refresh/logout | public (credentials) |
| 2 | `users.py` | team members, invites | authenticated, RBAC (`MANAGE_USERS`) |
| 3 | `vendors.py` | subcontractor vendors, bills, payouts | authenticated |
| 4 | `compliance.py` | licenses/certifications | authenticated |
| 5 | `warranties.py` | warranties | authenticated |
| 6 | `events.py` | event bus inspection | authenticated (internal/admin-leaning) |
| 7 | `tools.py` | tool registry introspection/execution | authenticated, likely admin/AI-actor |
| 8 | `approvals.py` | `ApprovalRequest` approve/reject | authenticated, RBAC |
| 9 | `ai_activity.py` | AI invocation log surface | authenticated |
| 10 | `integrations.py` | generic integration connection mgmt | authenticated, RBAC (`MANAGE_INTEGRATIONS`) |
| 11 | `quickbooks_oauth.py` | QuickBooks OAuth connect/callback | authenticated start, provider callback (state-token bound) |
| 12 | `google_calendar_oauth.py` | Google Calendar OAuth | same pattern |
| 13 | `google_calendar.py` | calendar sync ops | authenticated |
| 14 | `leads.py` | CRM leads CRUD, import, qualify, AI advisory | authenticated, RBAC |
| 15 | `customers.py` | CRM customers CRUD, timeline, summary, notes | authenticated, RBAC |
| 16 | `appointments.py` | scheduling, availability | authenticated, RBAC |
| 17 | `crm.py` | CRM cockpit metrics | authenticated |
| 18 | `dashboard.py` | dashboard aggregates | authenticated |
| 19 | `billing.py` | Klaros SaaS billing (Stripe subscriptions), webhook | authenticated + webhook (signature-verified) |
| 20 | `jobs.py` | job lifecycle: create/search/schedule/assign/transition/QA/signoff/PO draft/convert-lead | authenticated, RBAC (many permissions) |
| 21 | `workers.py` | field worker roster | authenticated |
| 22 | `exceptions.py` | operations exceptions, delay detection | authenticated |
| 23 | `operations.py` | operations dashboard | authenticated |
| 24 | `organizations.py` | org settings | authenticated, RBAC (owner/admin) |
| 25 | `invoices.py` | invoicing lifecycle incl. QuickBooks sync, checkout | authenticated, RBAC |
| 26 | `quotes.py` | quotes CRUD/send/deposit checkout | authenticated, RBAC |
| 27 | `contracts.py` | internal-attestation contracts CRUD/send | authenticated, RBAC |
| 28 | `public_quotes.py` | customer-facing quote view/accept/decline/deposit | **public**, signed-token auth (no login) |
| 29 | `public_contracts.py` | customer-facing contract view/sign/decline | **public**, signed-token auth |
| 30 | `public_leads.py` | inbound public lead capture (e.g. website form) | **public**, rate-limited |
| 31 | `public_invites.py` | team-invite accept flow | **public**, signed-token auth |
| 32 | `payments.py` | payment records, test-payment | authenticated |
| 33 | `ar.py` | AR aging, collections | authenticated |
| 34 | `refunds.py` | refund requests (→ approval) | authenticated |
| 35 | `credit_notes.py` | credit note requests (→ approval) | authenticated |
| 36 | `writeoffs.py` | write-off requests (→ approval) | authenticated |
| 37 | `job_costs.py` | job cost tracking | authenticated |
| 38 | `profitability.py` | job profitability reporting | authenticated |
| 39 | `cash.py` | cash forecast | authenticated |
| 40 | `finance.py` | commercial pipeline aggregate | authenticated |
| 41 | `marketing_campaigns.py` | campaign CRUD | authenticated |
| 42 | `marketing_attribution.py` | lead attribution | authenticated |
| 43 | `marketing_content.py` | content pieces/variants/publication/performance | authenticated |
| 44 | `marketing_seo.py` | SEO pages/keywords/opportunities/local listings | authenticated |
| 45 | `marketing_outbound.py` | outbound lists/sequences/enrollments | authenticated |
| 46 | `marketing_nurture.py` | nurture sequences | authenticated |
| 47 | `marketing_reactivation.py` | reactivation campaigns | authenticated |
| 48 | `marketing.py` | marketing overview/dashboard | authenticated |
| 49 | `retention_opportunities.py` | retention opportunities | authenticated |
| 50 | `retention_reminders.py` | service reminders | authenticated |
| 51 | `retention_reviews.py` | review requests | authenticated |
| 52 | `retention_referrals.py` | referral programs/codes/rewards | authenticated |
| 53 | `retention_campaigns.py` | retention campaigns | authenticated |
| 54 | `retention.py` | retention overview | authenticated |
| 55 | `morning_brief.py` | daily brief generate/view | authenticated |
| 56 | `automation.py` | automation engine core | authenticated |
| 57 | `automations.py` | automation CRUD (separate from #56 — possibly versioned/duplicate naming, not resolved) | authenticated |
| 58 | `company_memory.py` | CompanyMemory CRUD | authenticated |
| 59 | `notifications.py` | notifications, preferences | authenticated |
| 60 | `knowledge.py` | knowledge file upload/index/search | authenticated |
| 61 | `webhooks.py` | inbound provider webhooks (Stripe etc.) | **webhook**, signature-verified |
| 62 | `marketplace_webhooks.py` | inbound Angi/Thumbtack/Nextdoor-style leads | **webhook**, per-tenant HMAC |
| 63 | `voice.py` | voice/call session mgmt, receptionist settings | authenticated |
| 64 | `voice_stream.py` | Twilio Media Stream WebSocket bridge | **websocket**, Twilio-signed |

(Router count above is 55 include_router calls per the file listing in the main audit; this table numbers each entry individually for reference, so the count column runs to 64 against the 55 `include_router` lines because several files register more than one logical resource group under one import — reconcile against `router.py:70-134` directly for the authoritative list.)

## Public/webhook surface (explicitly unauthenticated by JWT)

- `public_leads.py`, `public_quotes.py`, `public_contracts.py`, `public_invites.py` — signed single-purpose tokens (`create_quote_view_token`, `create_invite_token`, and an equivalent for public lead intake) per `backend/app/core/security.py`.
- `webhooks.py`, `marketplace_webhooks.py` — provider-signed (Stripe signature; per-tenant HMAC for marketplace leads).
- `voice_stream.py` — Twilio-authenticated WebSocket, not a JWT-bearer REST call.

All four public/rate-limited categories (`lead`, `quote`, `contract` views, `auth`, `webhook`) have distinct configured rate limits in `.env.example:24-28`.

## Not independently verified in this pass

- Exact HTTP method + path list per router (would require opening all 55 files).
- Presence/absence of a dedicated `/health` endpoint.
- Whether any router has truly dead (zero frontend consumer) endpoints — a full cross-reference of `frontend/lib/api.ts`'s ~150+ exported functions against all backend routes was not completed line-by-line; the sampled routes (leads, jobs, invoices, quotes, contracts, payments, AR, workers, vendors, compliance, warranties, exceptions, operations) all have matching frontend callers.
- Whether `automation.py` and `automations.py` are genuinely distinct resources or an unresolved naming duplication — flagged for direct follow-up.
