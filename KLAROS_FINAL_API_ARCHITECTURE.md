# Klaros AI — Final API Evolution Strategy

Covers item Q. All 65+ existing `include_router` registrations (`backend/app/api/v1/router.py`) are preserved unchanged. New surface is additive route groups only. Idempotency-key convention (existing, used on multi-step write endpoints) is carried forward for all new multi-step write endpoints below.

## Surface categories

Public / Authenticated / Tenant / Agent-runtime / Internal-service / Webhooks / OAuth-callbacks / Admin — mapped onto the existing verified surface: Public = `public_leads`, `public_quotes`, `public_contracts`, `public_invites`, and new `public_appointment-requests`; Authenticated+Tenant = the ~60 existing JWT-gated tenant routers, extended by the groups below; Agent-runtime = new, invoked only by the `AgentExecutionService` itself (never directly client-callable — see below); Internal-service = Temporal worker/event-worker code paths, not HTTP-exposed; Webhooks = `webhooks`, `marketplace_webhooks`; OAuth-callbacks = existing QuickBooks/Google Calendar callback routes, extended by any new OAuth provider's callback; Admin = new `MANAGE_INTEGRATIONS_CATALOG`-gated catalog-curation routes.

## New endpoint groups

### `/api/v1/business-discovery`
| Method/Path | Purpose | Auth | Tenant scope | Input | Output | Side effects | Idempotent | Audit | Errors |
|---|---|---|---|---|---|---|---|---|---|
| `POST /business-discovery/sessions` | Start a discovery session from free-text business description | JWT | tenant | `{description}` | `DiscoverySession` | creates `DiscoverySession` | No (new session each call) | Yes (tool call) | 422 invalid input |
| `POST /business-discovery/sessions/{id}/answer` | Submit answer to current adaptive question | JWT | tenant | `{answer}` | next question or `COMPLETED` | writes `DiscoveryTurn`, may write `BlueprintClaim` PROPOSED rows | Yes (idempotency key on turn) | Yes | 404 session not found, 409 session already completed |
| `GET /business-discovery/sessions/{id}` | Read session state | JWT | tenant | — | `DiscoverySession` + turns | none | Yes | No (read) | 404 |

### `/api/v1/business-blueprint`
| Method/Path | Purpose | Auth | Tenant scope | Input | Output | Side effects | Idempotent | Audit | Errors |
|---|---|---|---|---|---|---|---|---|---|
| `GET /business-blueprint` | Get active blueprint | JWT | tenant | — | `BusinessBlueprint` + sections | none | Yes | No | 404 none active yet |
| `GET /business-blueprint/versions/{v}` | Get a specific historical version | JWT | tenant | — | `BusinessBlueprint` (read-only) | none | Yes | No | 404 |
| `PUT /business-blueprint/sections/{key}` | Human-edit a section (creates new blueprint version) | JWT, `MANAGE_BLUEPRINT` | tenant | section JSONB payload | new `BusinessBlueprint` version | new version row, audit write | No (mutates state) | Yes | 422 schema validation, 403 no permission |
| `POST /business-blueprint/claims/{id}/confirm` | Confirm a proposed claim | JWT, `MANAGE_BLUEPRINT` | tenant | — | updated `BlueprintClaim` | status→CONFIRMED, mirrors into `CompanyMemory` | Yes (safe to repeat) | Yes | 404, 409 already rejected |
| `POST /business-blueprint/claims/{id}/reject` | Reject a proposed claim | JWT, `MANAGE_BLUEPRINT` | tenant | `{reason?}` | updated `BlueprintClaim` | status→REJECTED | Yes | Yes | 404, 409 already confirmed |

**Endpoint-shape decision** (resolves KLAROS_ARCHITECTURE_RECONCILIATION.md #1): explicit `confirm`/`reject` action endpoints, not a generic `PATCH`, matching the existing codebase convention (`/automations/{id}/publish`, `/automations/{id}/enabled`).

### `/api/v1/recommendations`
| Method/Path | Purpose | Auth | Tenant scope | Notes |
|---|---|---|---|---|
| `GET /recommendations` | List recommendations for the org, filterable by target type/status | JWT | tenant | Read |
| `POST /recommendations/{id}/accept` | Accept a recommendation | JWT, role-appropriate for target type | tenant | Side effect varies by target (e.g. creates a `Recommendation`-linked draft `IntegrationConnection` or draft `Agent`) — always audited |
| `POST /recommendations/{id}/dismiss` | Dismiss | JWT | tenant | Idempotent |

### `/api/v1/integrations/catalog` (sub-route of existing `/integrations`, not a new top-level router)
| Method/Path | Purpose | Auth | Tenant scope |
|---|---|---|---|
| `GET /integrations/catalog` | List catalog with derived per-tenant status | JWT | tenant (read merges tenant `IntegrationConnection` state) |
| `PUT /integrations/catalog/{provider_key}` | Admin-edit catalog entry | JWT, `MANAGE_INTEGRATIONS_CATALOG` | platform-admin (no tenant scope — reference data) |

### `/api/v1/agents`
| Method/Path | Purpose | Auth | Notes |
|---|---|---|---|
| `POST /agents` | Create agent (DRAFT) | JWT, `MANAGE_AGENTS` | |
| `PUT /agents/{id}` | Edit DRAFT agent | JWT, `MANAGE_AGENTS` | 409 if agent has a published version already referenced by executions — must create new version instead |
| `POST /agents/{id}/versions/{v}/publish` | Publish a version, agent→ACTIVE | JWT, `MANAGE_AGENTS` | Audit |
| `POST /agents/{id}/execute` | Manually trigger a run | JWT, `EXECUTE_AGENT` | Enqueues `AgentExecution`; goes through the full governance chain (KLAROS_FINAL_AGENT_MODEL.md) — this route itself does not bypass anything, it is just one of several trigger sources (manual/scheduled/event) |
| `GET /agents/{id}/executions` | List execution history | JWT | Read |
| `POST /agents/{id}/pause` | Pause | JWT, `MANAGE_AGENTS` | |

**Agent-runtime internal surface**: `AgentExecutionService`'s internal calls into `AIExecutionService`/`ToolRegistry` are **not** separately HTTP-exposed — they are in-process calls, exactly matching how `AIExecutionService.request_tool_execution()` is called today by `automation_service.py` etc. (in-process, not HTTP). No new "agent-runtime API" surface is introduced at the network level; this avoids inventing a second API layer for what is fundamentally the same execution boundary.

### `/api/v1/websites`
Standard CRUD + `/websites/{id}/preview`, `/websites/{id}/publish-requests` (creates a `PublishRequest`, reuses existing `ApprovalRequest` approve/reject routes rather than duplicating them).

### `/api/v1/public/appointment-requests` (new public endpoint)
Narrow, allowlisted target for Website Builder-generated forms — matches the existing `public_leads`/`public_quotes` pattern exactly (rate-limited, no auth, writes a constrained record type only).

### Domain-vertical routers (conditionally mounted per enabled `VerticalExtension`)
`/providers`, `/providers/{id}/procedures` (Medical Tourism); `/products`, `/products/{id}/skus`, `/suppliers`, `/orders` (Dropshipping) — mounted only when the organization has the corresponding vertical enabled, matching the "core never imports a vertical by name" rule: the router registration itself is conditional on data (`OrganizationVerticalExtension`), not on a compiled-in business-type switch in the route-handler code.

## Duplication check

Every new group above was checked against the existing 65+ routers for overlap — none found. The closest adjacency is `/integrations/catalog` as a sub-route of the existing `/integrations` router (deliberately nested, not a sibling top-level router, to keep the tenant-connection and catalog-reference concerns visibly related without merging their permission models).
