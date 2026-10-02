# Klaros Business Operating Platform

The Business Builder (`BUSINESS_BUILDER.md`) turns an idea into a structured business. The operating layer
described here is what the owner uses **after** that: see leads arrive, understand them, match them to the
right partner, automate the first response, and watch the numbers — on the same Business Home.

It is a **read model plus a few thin endpoints over existing infrastructure**. There is no new table and no
migration (Alembic head is unchanged).

## 1. Overview

`/business/home` is the single Business Home. It has two halves driven by one page:

- **Prepare** — Launch Readiness, progress ladder, next actions (from `GET /business-builder/overview`).
- **Operate** — leads funnel, workflows, AI workforce, integrations, activity and attention items
  (from `GET /business-builder/operations`).

Launch Readiness stays authoritative for "is the business ready"; the operating console never recomputes it.

## 2. Architecture

| Piece | Location |
|---|---|
| Operating read model | `backend/app/services/business_operations_service.py` |
| Vertical operating registry | `backend/app/services/operations_providers.py` (`register_operations_provider(vertical_key, fn)`) |
| Medical Tourism provider | `backend/app/services/medical_tourism_operations.py` |
| Integration state (single function) | `integration_state()` in `business_builder_service.py`, used by Builder *and* Operations |
| Endpoints | `api/v1/business_builder.py`, `api/v1/medical_tourism.py` |
| Frontend | `app/business/{home,workflows,data,analytics,integrations}`, `components/business/LeadOperationsPanel.tsx`, `lib/useOperations.ts`, `lib/opsLabels.ts` |

Generic modules never name a vertical (guard: `test_business_builder_modules_never_name_a_vertical`). A vertical
contributes metrics, data, breakdowns, lead stages and activity by registering an operations provider.

## 3. Leads as the operating object

The existing `Lead` model (7 statuses) is the object. The funnel on Home and the `?status=` deep links on
`/leads` come from real rows; nothing is invented. Leads created by the public website carry the tenant of the
site that received them.

## 4. Lead detail

For a lead with industry details, `LeadOperationsPanel` shows the enquiry (booleans as Yes/No, nothing as
`null`), provider matches with reasons, consultations, a timeline built from real records, and the next action.
For a lead without industry details the backend answers 404 and the panel renders nothing.

## 5. Provider matching (Medical Tourism)

`GET /medical-tourism/leads/{id}/provider-matches` and the `matching` block of `/operations`.
Deterministic and explainable: offers the procedure +3, located in the preferred country +2, verified
credential +1. Fit is STRONG or PARTIAL; every match lists its reasons. If the procedure is not stated it is
inferred from the enquiry text (and flagged as inferred). When nothing matches, the response explains why.
Suggestions only — nothing is sent, booked or contacted.

## 6. Workflows

Workflows are the **existing automation engine** (`AutomationService`, EVENT triggers, allow-listed actions).
`POST /business-builder/workflows/starter` creates, publishes and enables "New lead alert"
(`lead.created` → `notifications.create_notification`). It is idempotent and requires `MANAGE_AUTOMATIONS`.
`/business/workflows` lists real automations and their run counts.

## 7. AI workforce and the Halla boundary

Unchanged and honest: see `AI_WORKFORCE_INTEGRATION_BOUNDARY.md`. The operating layer shows the workforce as
**Integration required** (no adapter exists) and lists *agents* only from real `AgentExecution` /
`AgentToolPermission` rows. Nothing says Halla is connected, deployed or handling calls.

## 8. Integrations center

`/business/integrations` groups the real `IntegrationProviderCatalog`. States: CONNECTED (live connection),
AVAILABLE (real adapter, not connected), CONFIGURATION_REQUIRED (connection in error), PLANNED (no real
adapter), INTEGRATION_REQUIRED (the workforce — no adapter). Only Stripe, QuickBooks and Google Calendar have
real adapters.

## 9. Data center

`/business/data` shows the record sets each enabled module and the platform own (leads, website, workflows),
with counts from the database and an empty state per set.

## 10. Analytics

`/business/analytics` shows funnel, conversion and the module's breakdowns computed from real rows. With no
data it says so; it never plots placeholders.

## 11. Business Home

Combines §1's two halves. The website call to action follows real state (none / draft / published). Activity
and attention items come from audit/lead/workflow records.

## 12. API summary

| Method | Path | Permission |
|---|---|---|
| GET | `/business-builder/operations` | `READ_BUSINESS_JOURNEY` |
| POST | `/business-builder/workflows/starter` | `MANAGE_AUTOMATIONS` |
| GET | `/medical-tourism/leads/{id}/operations` | medical-tourism read |
| GET | `/medical-tourism/leads/{id}/provider-matches` | medical-tourism read |

The tenant always comes from the authenticated user; no endpoint accepts a tenant id from the client.

## 13. Security model

RLS and RBAC are untouched. Every query runs through the tenant-scoped session; the operating provider
receives `tenant_id` from the authenticated user. Cross-tenant lead ids return 404. Workflow creation uses only
allow-listed actions, so it cannot execute arbitrary tools.

## 14. Testing

```bash
cd backend && .venv/bin/python -m pytest tests/test_business_operations_api.py tests/test_medical_tourism_operations.py \
  tests/test_business_builder_api.py tests/test_capability_vocabulary.py
cd frontend && npx vitest run && npx tsc --noEmit && npm run build
```

Real PostgreSQL (RLS) runs use `DATABASE_URL` against a disposable database (see `BUSINESS_BUILDER.md` §5).
`test_business_operations_api.py::` includes a test that Builder and Operations report the same integration
state.

## 15. Known limitations

- Halla is not integrated (contract only).
- Provider matching is rule-based, not learned.
- Only the Medical Tourism module registers an operations provider today.
- The starter workflow is the only workflow template.
- Ask Klaros (natural-language operating questions) is deferred.
