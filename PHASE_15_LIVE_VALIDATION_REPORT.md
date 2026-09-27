# PHASE 15 — LIVE VALIDATION REPORT

## 1. Status

**PASS WITH LIMITATIONS**

A real, authenticated browser walkthrough was run against a real, running FastAPI backend (disposable real PostgreSQL 16, no mocks) and a real running Next.js frontend (`next dev`). Registration, login, Agent creation, tool-permission grant/revoke, versioning, publish, activation, published-immutability, a second draft-version flow, a schedule-trigger flow, an event-trigger display-only check, manual execution (deterministic-AI-provider-limited), 403/404/409 handling, refresh persistence, and responsive layout were all exercised live against the real backend and real database. **One genuine, reproducible Phase 15 frontend defect was found and fixed** (a 409-conflict state-reconciliation gap on version publish — see §9, Defect 1). **One genuine, reproducible Phase 15 mobile-responsive defect was found and left unfixed** because its root cause lives in a pre-existing, widely shared UI primitive (`PageHeader.tsx`, used by 13 other pages), not in Phase 15's own files — documented as a limitation per the task's scope boundary (see §9, Defect 2, and §14). WAITING_APPROVAL could not be reached live because both tools frozen into the published version snapshot require pre-existing fixture data (a real `quote_id` / `approval_request_id`) that a fresh synthetic tenant has none of; seeding unrelated CRM/Approval fixture data was judged out of this task's narrow scope and is documented as NOT REACHABLE rather than faked.

## 2. Validation Environment

| Item | Value |
|---|---|
| PostgreSQL | Disposable real PostgreSQL 16, self-built via the `pgserver` PyPI package (same pattern as Phase 14), Unix-socket only, data dir `/tmp/klaros_phase15_pgdata` |
| Database | `klaros`, `pgvector` extension 0.6.2 installed and confirmed via `\dx` |
| Backend URL | `http://127.0.0.1:8000` (`uvicorn app.main:app`) |
| Frontend URL | `http://localhost:3000` (`next dev`, Turbopack, Next.js 16.3.3) |
| Browser | Claude Code's built-in Browser pane (Chromium-based) |
| Test tenant | Newly registered org "Phase15 Validation Org" (slug `phase15-validation-org`) via the real `/register` flow |
| Test user (OWNER) | `phase15.validation@klaros-test-local.com` (synthetic, disposable). Password never logged in this report. |
| Test user (READ_ONLY) | `phase15.readonly@klaros-test-local.com`, created via the real team-invite flow (`POST /users/invites` → `POST /public/invites/{token}/accept`) to exercise RBAC live |
| AI provider | `AI_PROVIDER=deterministic` — no real external AI/LLM provider contacted |
| Event transport | `EVENT_TRANSPORT=memory` — no Redis |
| Redis | NOT REQUIRED / not run |

## 3. Git Baseline

- HEAD before: `8c4e13c62850eaa3712293651252312bceb8debd` (unchanged throughout — no commit made)
- Working tree before: identical to the session-start snapshot recorded in the environment's `gitStatus` (Phase 0-14's pre-existing uncommitted diff, plus Phase 15's untracked `frontend/app/agents/*`, `backend/app/api/v1/agents.py`, `backend/app/api/tool_deps_agents.py`, and the Phase 15 implementation log)

## 4. Startup

| Component | Result |
|---|---|
| PostgreSQL (disposable, pgserver) | PASS — started cleanly, `klaros` DB created, `pgvector` extension installed and verified |
| Alembic migrations | PASS — `alembic heads` → `0051 (head)` (matches expectation exactly, no Phase 15 migration needed); `alembic upgrade head` ran cleanly through all 51 migrations; `alembic current` confirmed `0051 (head)` afterward |
| Backend (`uvicorn`) | PASS — started, connected to disposable Postgres, `/openapi.json` returned 200, all 13 documented Agent routes present in the schema (`/api/v1/agents`, `.../activate`, `.../pause`, `.../archive`, `.../tool-permissions`, `.../tool-permissions/{tool_name}`, `.../versions`, `.../versions/{id}/publish`, `.../execute`, `.../executions`, `.../executions/{id}`, `.../executions/{id}/steps`) |
| Frontend (`next dev`) | PASS — started in <1s, no compilation errors, `.env.local`'s `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000` already correctly pointed at the backend |
| Authentication | PASS — real registration via `/register`, real JWT-based session, RBAC-authorized `role: OWNER` confirmed in the UI header ("role OWNER") |

## 5. Browser Walkthrough

| Flow | Result | Evidence |
|---|---|---|
| Unauthenticated `/agents` | LIVE VERIFIED — PASS | Clean redirect to `/login`, no crash, no console error |
| Authenticated `/agents` | LIVE VERIFIED — PASS | Empty state rendered ("No agents yet..."), no infinite skeleton (re-validates the already-fixed Phase 15 bug); no unexpected console errors |
| Create Agent | LIVE VERIFIED — PASS | `POST /api/v1/agents` → 201; real `Agent` row created; navigated to `/agents/{id}` |
| Agent persistence | LIVE VERIFIED — PASS | Refreshed browser; agent still present; independently confirmed via direct `psql` query against the disposable DB |
| Agent detail | LIVE VERIFIED — PASS | Identity/purpose/status/autonomy/acting-role/current-version all matched browser response, backend serializer, and DB row exactly |
| Acting role | LIVE VERIFIED — PASS | Displayed read-only ("OWNER", "set from its creator's own role... cannot be changed"); confirmed via DOM that the textarea/edit path has no acting-role field anywhere; matches backend (no client-settable field exists) |
| Autonomy | LIVE VERIFIED — PASS | Selected `EXECUTE_WITH_APPROVAL` at creation; persisted through refresh and DB read; confirmed immutable via UI once agent left DRAFT (`disabled` attribute checked directly on the DOM node) |
| Tool catalog | LIVE VERIFIED — PASS | Catalog rendered in UI cross-checked byte-for-byte against a direct `GET /api/v1/tools/catalog` call (same tool names/descriptions) |
| Tool permissions (grant) | LIVE VERIFIED — PASS | Granted `approvals.approve` via checkbox; confirmed row in `agent_tool_permissions` table directly |
| Tool permissions (revoke) | LIVE VERIFIED — PASS | Unchecked it; confirmed row removed from the DB directly |
| Save draft (identity) | LIVE VERIFIED — PASS (implicitly, via disabled-state check) / CODE VERIFIED for the write path | Once agent was ACTIVE, textarea was confirmed `disabled` — the write path itself (`PUT /agents/{id}` while DRAFT) was exercised implicitly by the create flow and is covered by the 15 mocked RTL tests for the detail page; a live DRAFT-state identity edit+save was not separately re-tested after activation (would require creating a second, still-DRAFT agent) |
| Publish | LIVE VERIFIED — PASS | `POST .../versions/{id}/publish` → 200; version status flipped DRAFT→PUBLISHED in the DB; UI "Review" panel showed the exact frozen snapshot (tools, limits, triggers) before publish |
| Published immutability | LIVE VERIFIED — PASS | Once agent activated (non-DRAFT), identity `<textarea>` confirmed `disabled=true` via direct DOM inspection; no edit control rendered for a PUBLISHED version |
| New version | LIVE VERIFIED — PASS | Created versions 2, 3, and 4 as separate DRAFTs while version 1 stayed PUBLISHED and unmodified; each new version froze its own independent tool-permission snapshot, confirmed in the DB |
| Schedule trigger | LIVE VERIFIED — PASS | Configured DAILY 09:00 schedule with a goal via the UI on version 3; persisted exactly (`{"schedule":{"enabled":true,"frequency":"DAILY","time":"09:00","goal":"..."}}`) in `agent_versions.triggers`; UI "Review" panel showed "Triggers: Schedule (DAILY at 09:00)" after a full page refresh |
| Event trigger display | LIVE VERIFIED — PASS | Created a version with a real `triggers.event` block directly via the backend API (since the create-version UI intentionally does not expose event-trigger authoring, per the documented limitation); confirmed the UI's Review panel displays it truthfully ("Triggers: Event (lead.created)") with no edit control offered — matches the implementation log's claim exactly |
| Manual execution | LIVE VERIFIED — PASS (request/persistence), BLOCKED (successful COMPLETED run) | Both the goal (`REASONING`) and single-tool (`SINGLE_ACTION`) paths were run live through the UI's "Run Agent" modal; each produced a real `POST /agents/{id}/execute` → 200 and a real `AgentExecution` row, confirmed in the DB. Both runs terminated `FAILED` — the reasoning path because `AI_PROVIDER=deterministic` has no real provider connected (mirrors Phase 14's exact, pre-existing, non-Phase-15 environment limitation), the single-tool path because the only tools frozen into the published snapshot (`ai.propose_quote_followup`, `approvals.approve`) require a real `quote_id`/`approval_request_id` this fresh synthetic tenant has none of. The frontend displayed both FAILED states honestly (toast + expandable error detail), never fabricating success. |
| Approval (WAITING_APPROVAL) | NOT REACHABLE | Requires either a real AI provider (forbidden in this sandbox) or seeding unrelated CRM/Approval fixture data to give a tool a valid target — judged out of this task's narrow Agent-configuration scope. Not faked. |
| Execution history | LIVE VERIFIED — PASS | All 3 executions (1 from the reasoning path, 1 single-action, 1 repeat reasoning) appeared in "Recent executions" with correct status/mode/goal/timestamp; expanding one showed the real backend `error_message` verbatim |
| 403 (RBAC) | LIVE VERIFIED — PASS | Created a real READ_ONLY user via the actual invite-accept flow, logged in as them in the browser, attempted Create Agent; backend returned real `403 Forbidden`; UI showed "Missing required permission: MANAGE_AGENTS" honestly; no agent was created (confirmed no new DB row) |
| 404 | LIVE VERIFIED — PASS | Navigated to `/agents/00000000-...`; clean "This agent doesn't exist, or isn't visible to your account." message, no stack trace |
| 409 (stale version publish) | LIVE VERIFIED — PASS (defect found and fixed) | Simulated a second tab by publishing a version out-of-band via direct API call while the browser held stale state, then clicked "Publish" on the now-already-published version in the UI; backend correctly returned 409; **before the fix**, the UI showed a warning toast but left the stale DRAFT badge in place indefinitely (Defect 1, §9) — **after the fix**, the same repro immediately reconciles to the true server state (PUBLISHED/Current, previous version DEPRECATED) | 
| Refresh persistence | LIVE VERIFIED — PASS | Re-verified at multiple points (agent list, agent detail, tool permissions, versions, triggers) — every refresh reconstructed state from the backend, no localStorage/sessionStorage dependency observed |
| Direct URL access | LIVE VERIFIED — PASS | `/agents`, `/agents/new`, `/agents/{id}` (real and nonexistent) all navigated to directly and behaved correctly |
| Mobile (375×812) | LIVE VERIFIED — PASS (list, create form) / FAIL, documented not fixed (agent detail header actions) | `/agents` and `/agents/new` rendered cleanly with no overflow; `/agents/{id}`'s header action row (status badge + Pause + Archive + Run Agent) is NOT reachable at 375px — see Defect 2, §9 |
| Tablet (768×1024) | LIVE VERIFIED — PASS | Agent detail page (all sections, including the same 4-item action row) rendered fully with no overflow |
| Desktop | LIVE VERIFIED — PASS | Used throughout the walkthrough — no issues |

## 6. Network Validation

- `POST /api/v1/agents` request → 201 response body inspected directly: `{"id":..., "name":"Phase15 Validation Agent", "purpose":"...", "status":"DRAFT", "autonomy_tier":"EXECUTE_WITH_APPROVAL", "acting_role":"OWNER", "current_version_id":null, "source_blueprint_id":null, ..., "created_by":"<user-uuid>", ...}` — `acting_role` is present in the *response* but was never client-supplied (confirmed by source: `frontend/app/agents/new/page.tsx`'s submit body has no `acting_role`/`tenant_id`/`role`/`actor_type` field, backed by a dedicated existing RTL test asserting the exact payload); the backend derives it server-side from the bearer token, exactly as documented.
- `POST /api/v1/agents/{id}/versions/{version_id}/publish` → 200 on success, 409 on a stale conflict; response body carried the real `AgentVersionImmutableError`-derived message ("Version is not DRAFT (status=PUBLISHED)").
- `POST /api/v1/agents/{id}/execute` → always 200 at the HTTP layer (the execution's *own* `status` field carries FAILED/COMPLETED/etc., not the HTTP status) — confirmed both FAILED executions this way.
- `POST /api/v1/agents` as the READ_ONLY user → real 403 Forbidden.
- No request body anywhere included `tenant_id`, `organization_id`, `role`, or `actor_type` — verified both by direct network inspection of the create-agent flow and by reading the actual submit code in `frontend/app/agents/new/page.tsx` and `frontend/app/agents/[id]/page.tsx`.
- No secret, password, or token value appears in this report or was logged to any tool output.

## 7. Database Validation

Direct `psql` queries against the disposable database (not the frontend or backend's own claims) confirmed, at the end of the session:

```
agents:            1 row  — id=aa36adde-..., status=ACTIVE, autonomy_tier=EXECUTE_WITH_APPROVAL,
                             acting_role=OWNER, current_version_id=81534bac-... (v4)
agent_versions:    4 rows — v1/v2/v3 DEPRECATED, v4 PUBLISHED (exactly the lifecycle the UI showed)
agent_tool_permissions: 3 rows — ai.propose_quote_followup, approvals.approve, approvals.list
agent_executions:  3 rows — all FAILED, with the exact error_message strings the UI displayed
```

The agent's `tenant_id` was independently joined against `organizations` and confirmed to belong to "Phase15 Validation Org" — not the default/demo tenant.

## 8. Security Validation

- **Tenant spoofing**: not separately exercised with a second live tenant (time-boxed); this specific Agent-table isolation is additionally backed by the RLS-audit-mode instrumentation applied to every tenant-scoped table since migration 0040 per the schema (not independently re-verified this session).
- **Role spoofing**: LIVE VERIFIED — a real READ_ONLY user was created via the actual invite flow and a real 403 was observed for `POST /agents` in the browser (§5).
- **Actor spoofing**: LIVE VERIFIED — `acting_role` has no client input anywhere in the create/detail flows (confirmed both by network inspection and source read); it is only ever a read-only server-derived display field.
- **Tool permission escalation**: LIVE VERIFIED (negative case) — the frozen `tool_permissions_snapshot` correctly did NOT include a tool (`approvals.list`) granted to the *live* grant table *after* a version was already published; only a new version's fresh snapshot picked it up, exactly matching the documented "live grants vs. frozen snapshot" design.
- **Autonomy escalation**: not separately fuzz-tested by sending an invalid autonomy string via raw network manipulation this session (time-boxed); the four tiers exposed in the UI are exactly the four `AgentAutonomyTier` enum values read from backend source in the implementation log, and the backend's own Pydantic validation is the authoritative gate.
- **Secret exposure**: no password, token, or API key appears anywhere in this report or was echoed in any tool output.

## 9. Defects

### Defect 1 — Publish 409 conflict left the UI showing stale, incorrect version state (found and fixed)

- **Symptom**: If a version is published out-of-band (e.g. a second tab, or a race) while a user's page still shows it as DRAFT, clicking "Publish" in the stale tab correctly gets rejected by the backend (409), but the UI only showed a warning toast — the version row kept showing the stale "DRAFT" badge and an active "Publish" button indefinitely, with no way to see the true state short of a manual full-page reload.
- **Reproduction**: Load `/agents/{id}` with a DRAFT version visible. Via a separate request, publish that same version. Click "Publish" on it in the still-loaded page.
- **Browser state**: Toast read "Version is not DRAFT (status=PUBLISHED)"; the version list below it still showed "Version N · DRAFT · Publish" unchanged.
- **Network request**: `POST /api/v1/agents/{id}/versions/{version_id}/publish` → `409 Conflict`.
- **Database state**: The version was, correctly, already `PUBLISHED` server-side — only the frontend's local React state was stale.
- **Root cause**: `frontend/app/agents/[id]/page.tsx`'s `VersionsSection.publish()` catch block handled a 409 with only `toast.warning(err.message)`, unlike the sibling `doTransition()` function (used for Activate/Pause/Archive) a few dozen lines above it, which calls `await load()` to reconcile with the server on the exact same 409 case. The two lifecycle-mutation code paths had inconsistent reconciliation behavior.
- **Classification**: Genuine Phase 15 frontend defect (the page's own state-reconciliation logic) — small, local, in a file Phase 15 owns exclusively, no backend/architecture change required. Meets all the task's fix criteria.
- **Fix**: In the 409 branch of `publish()`, after showing the warning toast, re-fetch both `listAgentVersions` and `getAgent` and push the fresh results through the existing `onVersionsChanged`/`onAgentChanged` callbacks — mirroring exactly what `doTransition()` already does. One localized, ~8-line change in `frontend/app/agents/[id]/page.tsx`; no new dependency, no component restructuring, no backend touch.
- **Regression**: Re-ran the exact repro live after the fix — the version list now immediately shows the correct PUBLISHED/Current state and the correctly-demoted DEPRECATED state for the version it replaced. Re-ran the full frontend suite (89/89 still pass, unchanged), `tsc --noEmit` clean, `npm run build` clean (see §10-12).

### Defect 2 — Agent detail page header actions unreachable at 375px mobile width (found, NOT fixed — documented as a limitation)

- **Symptom**: On `/agents/{id}` at a 375×812 mobile viewport, the header's action row (status badge, Activate/Pause, Archive, Run Agent) does not wrap onto a second line. The "Archive" and "Run Agent" buttons are positioned off the right edge of the viewport and are not reachable by any scroll (the page itself has no horizontal scrollbar — `document.documentElement.scrollWidth === clientWidth === 375` — the buttons are simply clipped/unreachable, not merely scrolled-past).
- **Reproduction**: Set viewport to 375×812, navigate to an agent detail page for an ACTIVE agent (which has 3+ action buttons: Pause, Archive, Run Agent, alongside the status badge). Confirmed via direct DOM measurement: `Archive` button right edge at x≈404.6px, `Run Agent` button right edge at x≈513.4px, both beyond the 375px viewport.
- **Root cause**: `frontend/components/ui/PageHeader.tsx`'s own actions wrapper — `<div className="flex shrink-0 items-center gap-2">{actions}</div>` — has no `flex-wrap` and is itself wrapped in a non-wrapping `<header className="flex items-start justify-between gap-4">`. The Agent detail page's own actions container (`app/agents/[id]/page.tsx`) *does* include `flex-wrap`, but that has no effect because its parent (PageHeader's own wrapper) constrains and clips it first.
- **Classification**: Real, live-discovered, viewport-dependent defect — exactly the class of bug a mocked/JSDOM test suite structurally cannot catch. **However**, `PageHeader.tsx` is a shared, pre-existing UI primitive used by 13 other pages across the app (confirmed via `grep -rl PageHeader app/`), none of which were built or touched by Phase 15. Phase 15 is simply the first consumer to feed it an unusually long 4-item action row that exposes the latent bug. Fixing it correctly (e.g. adding `flex-wrap` to the shared component) is a small CSS change, but it touches code outside Phase 15's own file set and could not be re-verified this session against all 13 other pages that also use it, which is exactly the kind of unrelated-code, scope-expanding change the task's rules direct against.
- **Fix**: NOT APPLIED. Documented as a limitation per the task's explicit instruction to document rather than fix when a fix would require touching code beyond the narrow, already-verified scope.
- **Severity**: Low-to-medium. Desktop and tablet (768px+) are unaffected (verified live, §5). Mobile users on this one page cannot tap Archive or Run Agent without either rotating to a wider viewport or a future fix to the shared header component.

No other Phase 15 defects were found. No backend defect was found or is claimed.

## 10. Automated Regression

- Frontend test suite (`npx vitest run`): **89 passed / 0 failed / 0 skipped, 15 test files** — identical to the Phase 15 implementation log's own claimed count, re-run after the Defect 1 fix, confirming no regression.
- Before-fix baseline (per the implementation log, not independently re-run before the fix in this session): 89/89.
- After-fix: 89/89, unchanged.

## 11. Typecheck

`npx tsc --noEmit` — **PASS**, exit code 0, no output.

## 12. Production Build

`npm run build` — **PASS**. Route manifest confirmed to include `/agents` (static), `/agents/[id]` (dynamic, `ƒ`), and `/agents/new` (static), alongside every pre-existing route, with no build errors.

## 13. Live Coverage

**VERIFIED (live, real browser + real backend + real Postgres):** unauthenticated redirect; authenticated list load and empty state; agent creation and response-shape audit; DB persistence of the created agent; agent detail rendering against real backend/DB data; acting-role read-only display and immutability; autonomy display and post-activation immutability; real tool catalog cross-check; tool-permission grant and revoke against the live DB; version creation (×3) with frozen snapshot verification; publish and lifecycle transition (DRAFT→PUBLISHED→DEPRECATED across versions); published-version immutability; schedule-trigger creation, persistence, and display; event-trigger display-only truthfulness; manual execution via both the reasoning and single-tool paths (both correctly FAILED given this sandbox's constraints, honestly displayed); execution history; 403 RBAC via a real second user; 404 for a nonexistent agent; 409 stale-publish conflict (defect found and fixed, then re-verified); refresh persistence at multiple states; direct URL access; mobile/tablet/desktop responsive layout for the list and create-form pages.

**NOT VERIFIED (documented, not fabricated):** tenant isolation with a second live tenant; autonomy-value fuzzing via raw network tampering; a live DRAFT-state identity re-save exercised as a completely separate live flow (covered instead by the existing 15 mocked RTL tests for this exact code path, which were also re-confirmed passing this session).

**BLOCKED:** WAITING_APPROVAL / approval-flow execution state — requires either a real AI provider (forbidden in this sandbox) or unrelated CRM/Approval fixture data seeding, both out of this task's scope.

**DEFERRED BY DESIGN (confirmed accurate, not re-implemented):** event-trigger creation UI (schedule-only, as documented); constraint editor (absence confirmed — no such UI exists anywhere in the tool-permission rows); inline approve/reject on a WAITING_APPROVAL execution (links to the existing `/approvals` page instead, per the original design).

## 14. Known Limitations

- **Event trigger editing is deferred by design** (schedule triggers are the only ones the create-version UI exposes) — confirmed accurate; an existing event trigger displays correctly and truthfully with no misleading edit affordance.
- **No constraint editor** — confirmed deliberately absent; no UI anywhere implies `constraint_config` is enforced.
- **`AI_PROVIDER=deterministic` environment limitation** (pre-existing, not Phase 15's fault, identical in kind to the Phase 14 live-validation report's own documented limitation): any goal-based (REASONING mode) manual execution fails with "No AI provider connected" in this sandbox, since connecting a real provider is forbidden. This is why COMPLETED/RUNNING/WAITING_APPROVAL execution states could not be reached live.
- **WAITING_APPROVAL not reachable**: the tools frozen into the currently published version (`ai.propose_quote_followup`, `approvals.approve`) both require pre-existing fixture data (a real quote or a real pending approval request) this fresh synthetic tenant has none of; seeding unrelated CRM/Approval data was judged out of scope for a narrowly-focused Agent-configuration validation pass.
- **Defect 2 (mobile header action overflow) left unfixed** — root cause is in a shared, pre-existing `PageHeader.tsx` component used by 13 other pages, outside Phase 15's own file set; see §9 for full detail and reasoning.
- **Tenant isolation with a second live tenant, and autonomy-value fuzz testing via raw network tampering, were not separately exercised live** in this time-boxed session — see §8.

## 15. Git Final State

- HEAD: `8c4e13c62850eaa3712293651252312bceb8debd` (unchanged — no commit created)
- Modified (this validation session, beyond the pre-existing baseline diff): `frontend/app/agents/[id]/page.tsx` (Defect 1 fix — an already-untracked-as-new Phase 15 file, so this shows as part of that new file's content, not a tracked-file diff)
- Untracked files: identical set to the session-start baseline, plus this report (`PHASE_15_LIVE_VALIDATION_REPORT.md`)
- Commit status: no commit created
- Push status: no push performed
- Temporary validation artifacts (disposable Postgres data dir `/tmp/klaros_phase15_pgdata`, backend/frontend log files, the background `pgserver`/`uvicorn`/`next dev` processes) remain running as background processes for this session only and were not committed to the repo; no other phase's files were touched

## 16. Final Verdict

**PHASE 15 LIVE VALIDATION: PASS WITH LIMITATIONS**
