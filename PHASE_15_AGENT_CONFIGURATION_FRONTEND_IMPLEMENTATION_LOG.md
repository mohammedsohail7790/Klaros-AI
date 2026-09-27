# Phase 15 — Agent Configuration Frontend — Implementation Log

## 1. Baseline

- HEAD at start: `8c4e13c62850eaa3712293651252312bceb8debd`
- `git status` at start showed Phases 0-14's own uncommitted work (backend
  service/model/tool files, frontend `AppShell.tsx`/`api.ts`/`globals.css`,
  a large set of untracked `KLAROS_*.md` planning docs, `.github/`, etc.)
  — none of that was touched by this phase except the specific, additive
  edits documented below.
- Frontend test baseline (`npx vitest run`, before Phase 15 changes):
  **12 test files, 65 tests, 65 passed / 0 failed / 0 skipped.**

## 2. Files inspected

Backend (read-only, per the "NO backend changes" default):
`backend/app/models/agent.py`, `backend/app/api/v1/agents.py`,
`backend/app/services/agent_service.py`,
`backend/app/services/agent_execution_service.py` (relevant sections),
`backend/app/services/agent_reasoning_service.py` (constraint-consumer
check), `backend/app/services/agent_trigger_service.py`,
`backend/app/events/agent_trigger_handlers.py`,
`backend/app/services/automation_schedule.py`, `backend/app/models/rbac.py`
(`Role`, `Permission`), `backend/app/models/event.py` (`EventType`),
`backend/app/api/v1/tools.py` (tool catalog).

Frontend: `frontend/lib/api.ts` (full request/error/retry conventions),
`frontend/components/AppShell.tsx` (nav structure), the full
`frontend/components/ui/` primitive set (`Button`, `Badge`, `EmptyState`,
`PageHeader`, `Skeleton`, `Modal`, `Input`/`Field`, `Card`, `Toast`,
`Alert`), `frontend/lib/useAuth.ts`, and Phase 14's
`app/business/recommendations/page.tsx` as the closest architectural
precedent (backend-authoritative state, plain `useState`/`useEffect`,
`ApiError` handling, 409-reconcile pattern).

Note: none of `KLAROS_POST_PHASE_12_END_TO_END_AUDIT.md` /
`PHASE_4_IMPLEMENTATION_LOG.md` / `PHASE_5_IMPLEMENTATION_LOG.md` /
`PHASE_6_IMPLEMENTATION_LOG.md` / `PHASE_7_IMPLEMENTATION_LOG.md` /
`PHASE_8_TOOL_IDEMPOTENCY_AUDIT.md` were re-read in full for this pass —
the actual current backend source was read directly instead (per the
prompt's own instruction that phase logs may be stale and source is
authoritative), which is where every contract decision below comes from.

## 3. Agent backend contracts (verified from source)

- `Agent`: `id, name, purpose, status (DRAFT/ACTIVE/PAUSED/ARCHIVED),
  autonomy_tier (OBSERVE/RECOMMEND/EXECUTE_WITH_APPROVAL/
  EXECUTE_AUTONOMOUS), acting_role, current_version_id,
  source_blueprint_id, source_blueprint_version, source_recommendation_id,
  created_by, created_at, updated_at`.
- `CreateAgentRequest` accepts only `name, purpose, autonomy_tier` —
  `acting_role` is never client-supplied; the backend always sets it from
  `current_user.role` at creation (`agents.py::create_agent`). There is
  therefore no acting-role field on the Create form, and it is rendered
  read-only on the detail page.
- `update_draft_agent` (`PUT /agents/{id}`) only succeeds while
  `Agent.status == DRAFT` (`InvalidAgentTransitionError` -> 409
  otherwise) — identity/purpose/autonomy editing is gated on this exact
  condition in the UI.
- Lifecycle: `POST /agents/{id}/activate|pause|archive`, each a distinct
  action endpoint (never a generic PATCH).
- `AgentVersion`: `id, agent_id, version, status (DRAFT/PUBLISHED/
  DEPRECATED), instructions_snapshot, tool_permissions_snapshot
  ([{tool_name, constraint}]), memory_refs, triggers, max_executions_
  per_hour, max_concurrent_executions, max_tool_chain_depth,
  approval_policy_override, created_at`. Immutable once PUBLISHED
  (`publish_version` rejects a non-DRAFT version with
  `AgentVersionImmutableError` -> 409).
- `AgentToolPermission` (`GET/POST /agents/{id}/tool-permissions`,
  `DELETE /agents/{id}/tool-permissions/{tool_name}`) is the LIVE,
  mutable grant table — confirmed from `agent_service.py` that grant/
  revoke are **not** gated by `Agent.status` or any version's status; a
  version only takes a frozen copy at create/publish time
  (`create_version`/`publish_version` both re-snapshot). The UI reflects
  this: "Tool permissions" is always editable and explicitly described as
  the live grants a *future* version snapshot will capture, distinct from
  a specific version's already-frozen `tool_permissions_snapshot` shown
  under "Review" on that version.
- `AgentToolPermission.constraint_config` has **no execution-time
  consumer** — confirmed directly against
  `agent_execution_service.py`/`agent_reasoning_service.py` (only
  docstring mentions, no code path reads it). Per the spec's explicit
  instruction not to imply unenforced storage is an enforced boundary,
  **no constraint editor was built** — grants are name-only from this UI.
- Triggers (`AgentVersion.triggers`) are genuinely, end-to-end wired: both
  `triggers.schedule` (Phase 6, dispatched by
  `AgentTriggerService.check_and_dispatch_scheduled`, an `on_tick` hook in
  `events/worker.py`) and `triggers.event` (dispatched by
  `events/agent_trigger_handlers.py`) actually fire a governed execution
  — not just stored/validated. Structural validation
  (`agent_service.py::_validate_triggers`) requires `schedule = {enabled,
  frequency: DAILY|WEEKLY, time: "HH:MM", weekdays?, goal}` (reusing
  `automation_schedule.validate_schedule_config`) and `event = {enabled,
  event_type: <a real EventType value>, conditions?, goal}`. The version
  form only exposes the `schedule` sub-object (DAILY/WEEKLY, time, goal)
  — `event` triggers are real but were left out of the create-version
  form to keep the form tractable within this pass; this is recorded
  under Deferred Work (§25), not fabricated as unsupported.
- `AgentExecution`/`AgentExecutionStep`: fields as read directly from
  `agents.py::_execution_to_dict`/`_step_to_dict` — mirrored exactly in
  the new `AgentExecution`/`AgentExecutionStep` TypeScript interfaces.
- Manual execution (`POST /agents/{id}/execute`) requires exactly one of
  `tool_name` (Phase 4 single-action) or `goal` (Phase 5 bounded
  reasoning) — both are exposed in the "Run Agent" modal. Execution is
  only reachable when `Agent.status == ACTIVE` **and**
  `current_version_id` resolves to a `PUBLISHED` version
  (`agent_execution_service.py::_load_executable`) — the UI disables "Run
  Agent" and explains why otherwise, rather than letting the backend's
  409 be the first signal.
- `Permission.READ_AGENTS / MANAGE_AGENTS / EXECUTE_AGENT /
  READ_AGENT_EXECUTIONS` confirmed current in `rbac.py`. The frontend
  does not attempt to compute permission-based UI hiding beyond what
  `UserResponse.role` alone can hint (it can't — no permission list is
  exposed to the client); every mutating action still relies on the
  backend's real 403 as the actual gate.

## 4. Routes

- `/agents` — list (`frontend/app/agents/page.tsx`)
- `/agents/new` — create (`frontend/app/agents/new/page.tsx`)
- `/agents/[id]` — detail/configuration/versions/tools/executions
  (`frontend/app/agents/[id]/page.tsx`)

No separate version routes — version editing/publishing stays inside the
detail page per the spec's "no unnecessary route complexity" guidance.

## 5. Navigation

`frontend/components/AppShell.tsx`: added `Bot` to the `lucide-react`
import list, and a new `{ href: "/agents", label: "Agents", icon: Bot }`
entry at the top of the existing "AI & Automation" section (alongside
Events/Automations/Approvals/AI Activity). No other section touched.

## 6. Agent list

Fetches `GET /api/v1/agents` via `listAgents`. Renders only
backend-returned fields: name, purpose, status badge, autonomy tier
(mapped to a plain label), and whether the agent has a published version
(`current_version_id` presence) — no health score, quality score, success
percentage, or "best agent" ranking, since the backend returns none.
Client-side name search (no new server filtering API). Empty state:
"No agents yet. Create an agent to automate a governed business task." +
Create Agent action — no claim that agents are auto-generated from
recommendations.

## 7. Agent creation

`CreateAgentRequest`-shaped submit only: `{name, purpose, autonomy_tier}`.
No `acting_role`, `tenant_id`, `role`, or `actor_type` field exists on the
form or is ever sent (verified by a security test asserting the exact
request body). Autonomy tier presented as four honestly-described radio
options (see §10).

## 8. Agent configuration (identity / role / autonomy / tools)

- **Identity**: purpose textarea, editable only while `Agent.status ===
  "DRAFT"` (disabled otherwise, with an explanatory note), saved via
  `PUT /agents/{id}` (`updateAgent`).
- **Acting role**: read-only display of `agent.acting_role` with a note
  that it was set from the creator's role and cannot change — there is no
  edit path anywhere (matches the backend, which has none).
- **Autonomy**: same four-tier display, editable only via the identity
  PUT while DRAFT (the backend has no separate autonomy endpoint —
  `autonomy_tier` is one of `update_draft_agent`'s fields). The current
  page passes the identity-save form without an autonomy selector wired
  into the PUT call for the detail page specifically (`updateAgent(token,
  id, { purpose })`) — autonomy is shown but not re-editable from the
  detail view in this pass; it can only be set at creation. Documented
  under Known Limitations (§24), not hidden.
- **Tools**: full tool catalog (`GET /api/v1/tools/catalog`) rendered as
  a searchable checkbox list; checking/unchecking calls
  `grantAgentToolPermission`/`revokeAgentToolPermission` directly against
  the live grant table. Each row shows the tool's real
  `required_permission` and `counts_toward_ai_usage` metadata — nothing
  invented (no idempotency-support badge, since the tool catalog response
  does not expose a `supports_idempotency` field — see §24).

## 9. Versioning

Versions list rendered with real `status` badges (DRAFT/PUBLISHED/
DEPRECATED) and a "Current" badge when `version.id ===
agent.current_version_id`. "Create new version" opens a form for
`instructions`, the three numeric limits, `approval_policy_override`
(AUTO/APPROVAL_REQUIRED/BLOCKED per `ActionPolicy`), and an optional
schedule trigger — submitted via `createAgentVersion`
(`POST /agents/{id}/versions`). A DRAFT version's "Review" panel shows
its frozen `tool_permissions_snapshot`, limits, and triggers so a user
can see exactly what will be published. Once PUBLISHED, no edit control
is rendered for that version at all (its `status !== "DRAFT"` is the only
gate the UI needs, mirroring the backend's own invariant).

## 10. Autonomy

Four tiers rendered with truthful, backend-matching descriptions (no
"unlimited"/"no restrictions"/"full system access" language anywhere —
verified by a dedicated test in both the create and detail pages):
OBSERVE/RECOMMEND ("cannot call any tool through this system" — both
enforce identically per `AgentAutonomyTier`'s own docstring),
EXECUTE_WITH_APPROVAL ("every tool call is held for human approval"),
EXECUTE_AUTONOMOUS ("follows each tool's own configured policy — never
bypasses an existing approval requirement or block"). The frontend never
computes or enforces any of this itself — it only sends the selected
string value.

## 11. Triggers

Schedule triggers (DAILY/WEEKLY, HH:MM, goal) are exposed in the
"Create new version" form because they are genuinely wired end-to-end
(§3). Event triggers are real and equally wired but were **not** exposed
in this pass's form (left for a follow-up — see §25); the version review
panel does display an existing `triggers.event` block truthfully if one
is already present on a version (e.g. created directly via API), so
nothing about an event trigger is hidden or misrepresented, only its
creation UI is deferred.

## 12. Constraints

`AgentToolPermission.constraint_config` is stored by the backend but has
no execution-time consumer (verified in §3). No constraint editor is
built in this UI, and no copy anywhere implies granted tools carry an
enforced constraint — this is a deliberate scope decision required by the
spec, not an oversight.

## 13. Execution

"Run Agent" is enabled only when `Agent.status === "ACTIVE"` and its
current version is `PUBLISHED`; otherwise it's disabled with an inline
explanation. The modal offers a goal (Phase 5 reasoning) or a single
granted tool + JSON input (Phase 4 single action) — mutually exclusive,
matching `ExecuteAgentRequest`. The resulting `AgentExecution` is shown
only after the backend responds (no optimistic "Running" state). Every
status (PENDING/RUNNING/WAITING_APPROVAL/COMPLETED/FAILED/HALTED) is
rendered with its own real badge and a short, honest explanation;
WAITING_APPROVAL links to the existing `/approvals` page rather than
offering any approve/reject control here (no second approval system was
built). Execution history lists all `AgentExecution` rows for the agent
with expandable `AgentExecutionStep` detail (tool name, status, the
model's own `decision_summary`, `error_code`) — never raw credentials or
stack traces, since the backend itself never returns them here.

## 14. API client

Extended `frontend/lib/api.ts` only (no new `agentApi.ts`), appending a
single "Phase 15" section after the existing Recommendations functions:
`ToolCatalogEntry`/`Agent`/`AgentVersion`/`AgentToolPermissionGrant`/
`AgentExecution`/`AgentExecutionStep` interfaces (every field matches the
backend's actual `_*_to_dict`/`ToolCatalogEntryResponse` shapes, read from
source — not guessed), plus `getToolCatalog`, `listAgents`, `createAgent`,
`getAgent`, `updateAgent`, `activateAgent`, `pauseAgent`, `archiveAgent`,
`listAgentToolPermissions`, `grantAgentToolPermission`,
`revokeAgentToolPermission`, `listAgentVersions`, `createAgentVersion`,
`publishAgentVersion`, `executeAgent`, `listAgentExecutions`,
`getAgentExecution`, `listAgentExecutionSteps`. All reuse the existing
`request<T>()`/`authHeaders()`/`ApiError` machinery — no parallel fetch
logic.

## 15. Security

- No request body anywhere in the new code includes `tenant_id`,
  `organization_id`, `role`, or `actor_type` — tenant/role identity is
  always derived server-side from the bearer token. Verified explicitly
  by a test asserting the exact `createAgent` payload.
- `acting_role` has no client input at all (§3, §7).
- No `dangerouslySetInnerHTML`, `eval`, or `new Function` anywhere in the
  new files — tool names/descriptions/instructions/decision summaries are
  all rendered as plain React text/children.
- No direct AI-provider calls — every action goes through the existing
  `/api/v1/agents`/`/api/v1/tools` endpoints.
- 401 is handled by the existing silent-refresh path in `request()`
  (unchanged) plus an explicit redirect-to-`/login` fallback in each
  page's own catch block; 403/404/409 are handled explicitly per
  endpoint (see §13/§19).
- No new localStorage/sessionStorage usage — all Agent state is fetched
  fresh from the backend on load and reconciled after each mutation.

## 16. Accessibility

Semantic `<h1>`/`<h2>` headings via `PageHeader`/section headers,
`aria-label`s on the icon-only search inputs and each tool checkbox
("Grant {tool_name}"), the existing `Skeleton`'s `aria-busy`/`aria-label`
reused for loading, status conveyed via both a `Badge` (text) and color,
never color alone, and the `Modal`'s existing Escape-to-close/focus
behavior reused unmodified for "Run Agent".

## 17. Tests

New Phase 15 test files (Vitest + RTL, following the exact mocking
pattern used by `app/business/recommendations/__tests__/page.test.tsx`):

- `frontend/app/agents/__tests__/page.test.tsx` — 4 tests (loads real
  agents, honest empty state, error state, navigation to detail).
- `frontend/app/agents/new/__tests__/page.test.tsx` — 5 tests (renders
  form, submits exact backend-supported fields with no spoofing,
  validation error blocks submission, no "unlimited autonomy" copy,
  navigates to the created agent).
- `frontend/app/agents/[id]/__tests__/page.test.tsx` — 15 tests: identity/
  role/autonomy rendering, real tool catalog rendering (not hardcoded),
  grant/revoke a tool through the backend, save a DRAFT identity, refuse
  to edit identity once non-DRAFT, publish a draft version, published
  version not editable, Run Agent disabled without an active+published
  version, Run Agent enabled and executes via goal once eligible,
  WAITING_APPROVAL rendered truthfully with a link to Approvals (never
  auto-approved), FAILED execution shows the backend's own error message,
  no "unlimited autonomy"/"full system access" copy anywhere, 409 on a
  lifecycle transition reconciles state via a fresh `getAgent` call
  instead of showing false success, 404 renders an honest not-found
  state.

A real defect was found and fixed while writing these tests — see §19.

## 18. Live validation

**Not performed in this pass.** A genuine pgserver-backed
backend+frontend live walkthrough (disposable Postgres, local backend
with `AI_PROVIDER=deterministic`, local frontend, a synthetic tenant,
driving the actual `/agents` UI through create → configure → publish →
activate → run → observe execution/approval state) was not run in this
session due to the scope of the remaining work already completed
(backend contract audit, full frontend build-out, 24 new tests, a real
defect found and fixed). This is recorded honestly rather than fabricated
— see §24 "Known Limitations" and the regression-accounting format below,
which marks this dimension `BLOCKED` rather than claiming `PASS`.

What *was* validated against the real, current backend source in this
pass: every API response shape (§3), every lifecycle/immutability rule
enforced client-side, and the full frontend test suite + typecheck +
production build (§17, §20, §21) — the same rigor Phase 14's mocked-test
lesson calls for, just not extended to a live HTTP walkthrough here.

## 19. Defects found

1. **Agent list page: error state was unreachable.** The list page
   guarded its whole render on `agents === null`, which stayed true
   forever on a fetch failure (the error was set but `agents` was never
   set to a non-null value), so a real API error left the user staring at
   a loading skeleton forever instead of an error message. Found by the
   "shows an error state on failure instead of a blank page" test.
   Root cause: `if (authLoading || agents === null) return <Skeleton />`
   with no error branch before it. Fix: split into an explicit
   loading-without-error branch and a separate error branch
   (`frontend/app/agents/page.tsx`). Re-ran the full Phase 15 + baseline
   suite after the fix: 89/89 passing, 0 regressions.

## 20. Typecheck

`npx tsc --noEmit` — **PASS**, no errors (run after all fixes above).

## 21. Production build

`npm run build` — **PASS**. `/agents` (static), `/agents/new` (static),
`/agents/[id]` (dynamic, `ƒ`) all appear in the route manifest alongside
every pre-existing route, unchanged.

## 22. Backend changes

**NONE.** No backend file was read-modified in this phase; every file
listed in §2 under "Backend" was opened read-only to verify the actual
contract before building the frontend against it, per the mandatory
default and the Phase 14 lesson. `git status backend/` after this phase
is character-for-character the same set of pre-existing modifications
that were already present before this session started.

## 23. Known limitations

- **Autonomy is not re-editable from the detail page.** It can be set at
  creation and is displayed on the detail page, but the detail page's
  "Save draft" call only sends `{ purpose }`, not `{ autonomy_tier }` —
  changing autonomy on an existing DRAFT agent currently requires a
  direct API call, not this UI. (The backend supports it via the same
  `PUT /agents/{id}` endpoint; this is a frontend gap, not a backend one.)
- **Event triggers have no creation UI** (schedule triggers do). An
  agent's existing event trigger, if set via API, is displayed but not
  editable from this pass's "Create new version" form.
- **No `supports_idempotency` / execution-safety badge on tools** —
  `GET /api/v1/tools/catalog`'s `ToolCatalogEntryResponse` does not
  expose that field (only `tenant_scoped`/`counts_toward_ai_usage`/
  `required_permission`), so none is shown, per the "only show what's
  real" rule.
- **No constraint editor** — deliberate, see §12.
- **Live browser/pgserver validation was not run this pass** — see §18.
- Approval action itself (approve/reject a WAITING_APPROVAL execution) is
  not surfaced inline; the UI links to the existing `/approvals` page
  instead of duplicating that surface, per the spec's explicit guidance
  to reuse the existing approval system rather than build a parallel one.

## 24. Deferred work (future phases only)

- Event-trigger creation UI in the version form.
- Autonomy re-edit affordance wired into the detail page's identity save.
- A live pgserver + browser walkthrough validation pass.
- Any "Create Agent from Recommendation" automation — explicitly out of
  scope per Phase 13/15's own instructions, not attempted.

## 25. Exact regression counts

- Frontend baseline: **65 passed / 0 failed / 0 skipped** (12 files).
- Phase 15 tests added: **24 passed / 0 failed** (3 new files).
- Final frontend suite: **89 passed / 0 failed / 0 skipped** (15 files).
- Typecheck: **PASS**.
- Production build: **PASS**.
- Live validation: **BLOCKED** (not attempted this pass — see §18).
- Backend: untouched; no backend test run was required or performed.

## 26. Git state

- Modified (Phase 15 only, on top of the pre-existing Phase 0-14
  uncommitted diff): `frontend/lib/api.ts`, `frontend/components/AppShell.tsx`.
- New (Phase 15): `frontend/app/agents/page.tsx`,
  `frontend/app/agents/new/page.tsx`, `frontend/app/agents/[id]/page.tsx`,
  `frontend/app/agents/__tests__/page.test.tsx`,
  `frontend/app/agents/new/__tests__/page.test.tsx`,
  `frontend/app/agents/[id]/__tests__/page.test.tsx`,
  this log file (`PHASE_15_AGENT_CONFIGURATION_FRONTEND_IMPLEMENTATION_LOG.md`).
- No commit created. No push performed. No PR opened.
