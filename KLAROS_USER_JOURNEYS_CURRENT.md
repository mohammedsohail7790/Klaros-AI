# Klaros User Journeys — Current State

Methodology: journeys are traced by following route existence (`frontend/app/**/page.tsx`) and the corresponding real backend calls in `frontend/lib/api.ts` / `backend/app/api/v1/*.py`. Page-internal UX correctness (e.g. exact loading/empty-state rendering per page) was spot-checked, not exhaustively verified for all 57 pages — see the main audit §5 for that caveat.

## Full frontend route list (57 pages, exhaustive — `frontend/app/**/page.tsx`)

`/` (marketing home), `/pricing`, `/login`, `/register`, `/accept-invite`, `/onboarding`, `/dashboard`, `/leads`, `/leads/[id]`, `/customers`, `/customers/[id]`, `/calendar`, `/jobs`, `/jobs/[id]`, `/operations`, `/operations/workers`, `/exceptions`, `/quotes`, `/quotes/[id]`, `/quotes/view/[id]`, `/contracts`, `/contracts/[id]`, `/contracts/view/[id]`, `/finance`, `/finance/ar`, `/finance/cash`, `/finance/invoices`, `/finance/invoices/[id]`, `/finance/profitability`, `/vendors`, `/marketing`, `/marketing/campaigns`, `/marketing/campaigns/[id]`, `/marketing/content`, `/marketing/content/[id]`, `/marketing/nurture`, `/marketing/outbound`, `/marketing/reactivation`, `/marketing/seo`, `/retention`, `/retention/campaigns`, `/retention/opportunities`, `/retention/referrals`, `/retention/reminders`, `/retention/reviews`, `/retention/risk-signals`, `/retention/warranties`, `/morning-brief`, `/automations`, `/approvals`, `/ai-activity`, `/events`, `/settings/automation`, `/settings/billing`, `/settings/compliance`, `/settings/integrations`, `/settings/knowledge`, `/settings/memory`, `/settings/team`, `/settings/voice`.

## Journey (a): Landing → Signup → Onboarding → Dashboard

- `/` → `/register` → real `register()` call (`lib/api.ts:162-170`, hits `POST /api/v1/auth/register`) → returns org slug + user + tokens → `/onboarding` → `/dashboard`.
- Real, server-backed at the auth boundary. `/onboarding` page content was **not read in this pass** — whether it is a genuine multi-step business-setup flow or a thin form is **UNKNOWN**; there is no backend "business discovery" endpoint to back an AI-driven onboarding wizard (confirmed absent, main audit §16), so at most it can be a conventional setup form (org name, first user, maybe initial settings), not an AI discovery flow.
- Dashboard (`/dashboard`) fans out to ~16 concurrent backend calls per an explicit code comment in `lib/api.ts:20-25` describing the retry logic built specifically for this page's load pattern — real, not static/mock.
- **Status: REAL for signup/login/dashboard; UNKNOWN for onboarding depth.**

## Journey (b): Business description → Questions → Requirements → Recommendations

- **Does not exist.** No route, no backend endpoint, no service implements this. There is no page prompting a free-text business description that feeds any downstream AI questioning/requirements/recommendation logic.
- **Status: MISSING.**

## Journey (c): Recommendation → Connect app → OAuth/API → Credentials → Test → Active

- The back half is real: `/settings/integrations` presumably lists providers; QuickBooks and Google Calendar have real OAuth2 connect/callback flows (`quickbooks_oauth.py`, `google_calendar_oauth.py`) with CSRF-bound state tokens; credentials are stored encrypted (`credential_store.py`); `IntegrationConnection.ConnectionStatus` presumably reflects connected/error/not-connected state ("Test" step = `verify_connection`-style calls exist for Stripe at least, `stripe_client.py:185`).
- The front half — a system recommending *which* integration to connect based on business context — **does not exist**; the user manually chooses from a fixed settings list.
- **Status: PARTIAL** — OAuth/credential/test/active mechanics are real; the "recommendation" trigger is missing.

## Journey (d): Create agent → Configure → Knowledge → Tools → Deploy

- Knowledge exists and is real (`/settings/knowledge`, pgvector-backed chunking/embedding/retrieval). Tools exist and are real (57 built-in tools behind the `ToolRegistry`). There is **no UI or API to compose a new agent** from these building blocks — no agent-creation form, no agent model, no "deploy" concept for a user-defined agent.
- The one thing that plays the role of a deployed "agent" — the AI Voice Receptionist — is hard-coded in Python, configured only via `VoiceReceptionistSettings` (a settings row, not an agent definition), not user-composable from arbitrary tools/knowledge.
- **Status: MISSING** as a self-serve journey; the underlying primitives (knowledge, tools) are real but not exposed as composable agent-building blocks.

## Journey (e): Trigger → AI → Tool → Integration → Result

- Real for two concrete cases: (1) the Automation Engine — a SCHEDULE or EVENT trigger evaluates a condition and executes an action, which can be a tool call, with durable waits backed by real Temporal workflows (`automation_workflow.py`); (2) the AI Voice Receptionist — a caller's speech triggers the model, which can invoke an allowlisted tool (e.g. `crm.create_appointment`) through the governed `AIExecutionService` boundary, which can call an integration (e.g. Google Calendar) and persist a result with an audit trail.
- Not real as a *general* case: there is no arbitrary "trigger → AI decides which of 57 tools to call → any integration" path outside these two specific, hard-coded features.
- **Status: PARTIAL/REAL within named features, MISSING as a generic capability.**

## Journey (f): Business idea → Website → Integrations → Agents → Workflows → Launch

- **Does not exist end-to-end.** Website creation is missing entirely (§18 of main audit). Integrations connect but aren't recommendation-driven (journey c). Agents aren't user-composable (journey d). Workflows (automations) are real but must be hand-configured, not generated from a "business idea." No "Launch" concept ties these together anywhere in the code.
- **Status: MISSING.**

## Other notable, verified-real flows not explicitly in the phase list

- **Quote → Contract → Deposit → Job commercial pipeline**: explicitly aggregated on `/dashboard` via a real backend endpoint (`getCommercialPipeline`, `lib/api.ts:1564-1579`, `GET /api/v1/finance/commercial-pipeline`) — the code comment states this file "never computes these numbers itself," i.e. it's a real backend aggregation, not client-side mock math.
- **Public, no-login customer flows**: quote view/accept/decline + deposit checkout (`/quotes/view/[id]`), contract view/sign/decline (`/contracts/view/[id]`) — both use a signed `token` query parameter as the sole auth mechanism, explicitly never sending an `Authorization` header (`lib/api.ts:1370-1374, 1496-1498, 1580-1587`). This is Klaros's only customer-facing (non-staff) surface and it is real, deliberately designed, and consistent between quotes and contracts.
- **Lead → Customer → Appointment → Job conversion**: one idempotent backend call (`convertLeadAndBook`, `lib/api.ts:372-388`, `POST /api/v1/jobs/convert-lead`) chains what would otherwise be four separate actions — real, atomic-by-design.
