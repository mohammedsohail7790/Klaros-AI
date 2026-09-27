# Phase 14 — Discovery → Blueprint → Recommendations Frontend — Implementation Log

## 1. Baseline

- Repo at `/Users/mohammedsohail/Desktop/Klaros AI`, branch `main`, working tree already carrying Phases 0–13's uncommitted changes (see git status at session start). Nothing from those phases was reverted or altered beyond what's listed in §15.
- Frontend baseline test suite (before this phase): 7 test files, 30 tests, all passing (`ComponentRegistry.test.tsx` 7, `customers/page.test.tsx` 3, `w/page.test.tsx` 2, `useAuth.test.tsx` 3, `Toast.test.tsx` 3, `StatCard.test.tsx` 5, `api.test.ts` 7).
- No E2E framework (Playwright/Cypress) present anywhere in `frontend/` — confirmed by search; only Vitest + React Testing Library (`frontend/vitest.config.ts`).

## 2. Files inspected

Backend (real source, not just logs): `app/api/v1/business_journey.py`, `app/models/business_journey.py`, `app/services/business_journey_service.py` (module docstring only, for the transition-graph contract), `app/api/v1/business_discovery.py`, `app/models/business_discovery.py`, `app/api/v1/business_blueprint.py`, `app/models/business_blueprint.py` (BlueprintSectionKey's literal 20 keys, ClaimType/ClaimProvenance/ClaimStatus), `app/api/v1/recommendations.py`, `app/models/recommendation.py` (RecommendationType/Source/Status), `app/models/rbac.py` (Permission enum — READ/MANAGE pairs for BUSINESS_JOURNEY, BUSINESS_DISCOVERY, BLUEPRINT, RECOMMENDATIONS, and which default roles get which).

Frontend: `frontend/app/` full route listing, `frontend/lib/api.ts` (4000+ lines — read the shared `request()`/`ApiError`/`withRetry`/`authHeaders`/error-formatting helpers, and the full Website Builder (Phase 12) section as the closest precedent), `frontend/lib/useAuth.ts`, `frontend/components/AppShell.tsx` (nav structure, `NAV_SECTIONS`), `frontend/app/website/page.tsx` (Phase 12 precedent, read in full — state-management style, loading/error patterns, action button disable-while-busy pattern), `frontend/components/ui/{Button,Input,Badge,Alert,EmptyState,Skeleton,PageHeader}.tsx`, `frontend/components/website/__tests__/ComponentRegistry.test.tsx` and `frontend/app/customers/__tests__/page.test.tsx` (test-mocking conventions), `frontend/vitest.config.ts`.

## 3. Existing frontend architecture (as found)

- Next.js App Router, plain `useState`/`useEffect` per page — no React Query/Redux/Zustand anywhere in the repo. Followed exactly; no new state library introduced.
- One centralized `frontend/lib/api.ts` with typed request/response interfaces and one function per endpoint, built on a shared `request<T>()` helper (adds auth headers, silent-refresh-on-401, uniform `ApiError`). All Phase 14 endpoints were added to this same file — no fragmented `businessJourneyApi.ts` etc. created.
- `useAuth()` hook reads the token from `sessionStorage` and fetches `/users/me`; every authenticated page follows the same `{ token, user }` destructuring and redirects to `/login` itself on a 401 from its own load call (Phase 14 pages replicate this exact pattern, not a new one).
- `AppShell` renders the sidebar from a static `NAV_SECTIONS` array; the active nav item is derived from `usePathname()`. No separate dashboard shell exists elsewhere — Phase 14 reuses `AppShell` verbatim.
- Design system: Tailwind utility classes plus a handful of semantic `klaros-*` classes (`klaros-card`, `klaros-input`, `klaros-label`) and shared `components/ui/*` primitives (`Button`, `Badge`, `Alert`, `EmptyState`, `Skeleton`, `PageHeader`, `Field`/`Input`/`Label`). Phase 14 pages use only these — no new component library.

## 4. Routes added

- `frontend/app/business/page.tsx` — entry point / resume point / start form / completed / abandoned states.
- `frontend/app/business/discovery/page.tsx` — Discovery conversation.
- `frontend/app/business/blueprint/page.tsx` — Blueprint review, section display/edit, claim confirm/reject, Confirm Blueprint checkpoint.
- `frontend/app/business/recommendations/page.tsx` — Recommendation cards, accept/reject, Finish setup.

Each of the three stage pages independently fetches the current journey on mount and **redirects** (`router.replace`) to `getJourneyDestination(journey.status)` if the journey is not actually at the stage that page renders — this is the direct-URL/stale-route guard, enforced identically on every page rather than trusted to a shared layout wrapper (there is no Next.js middleware/layout-level auth gate elsewhere in this app to extend, so this matches the existing per-page pattern).

## 5. Components added

- No new shared UI primitives were created — `Button`, `Badge`, `Alert`, `EmptyState`, `Skeleton`, `PageHeader`, `Field` from `components/ui/` cover every need.
- Page-local components only: `StartForm` (business.page.tsx), `SectionCard` (blueprint), `RecommendationCard` (recommendations) — kept local since none are reused across routes, matching Phase 12's own `VersionEditor`/`ThemeEditor` local-component convention in `website/page.tsx`.

## 6. API client changes (`frontend/lib/api.ts`)

Added one new section at the end of the file (after the existing public-website section), following the exact existing pattern (`export interface` + `export function ... { return request<T>(...) }`):

- Business Journey: `BusinessJourney`, `BusinessJourneyStatus`, `startBusinessJourney`, `getCurrentBusinessJourney` (translates the backend's 404-for-no-journey into a `null` return, exactly like the existing `getMyWebsite`), `getBusinessJourney`, `listBusinessJourneys`, `completeDiscoveryJourneyStep`, `confirmBlueprintJourneyStep`, `generateRecommendationsJourneyStep`, `completeBusinessJourney`, `abandonBusinessJourney`.
- Discovery: `DiscoveryTurnResponse`, `DiscoverySession`, `DiscoveryTurn`, `startDiscoverySession`, `answerDiscoveryQuestion`, `getDiscoverySession`.
- Blueprint: `BLUEPRINT_SECTION_KEYS` (the literal 20 keys, transcribed from `BlueprintSectionKey`), `BLUEPRINT_SECTION_LABELS` (presentation-only product labels, one per key — never a second schema), `BusinessBlueprint`, `BlueprintSection`, `BlueprintClaim`, `FullBlueprintResponse`, `getActiveBlueprint` (404→null), `getDraftBlueprint`, `updateBlueprintSection`, `activateBlueprint` (kept for completeness/typing symmetry but **never called from any Phase 14 page** — activation only happens via the journey's `confirm-blueprint` action, per the spec's explicit instruction not to call the raw activate endpoint from the browser), `confirmBlueprintClaim`, `rejectBlueprintClaim`.
- Recommendations: `Recommendation`, `RecommendationRun`, `listRecommendations`, `getRecommendationRun`, `acceptRecommendation`, `rejectRecommendation`. (`POST /recommendations/generate` was deliberately **not** wrapped/called — recommendation generation is only ever triggered via the journey's `generate-recommendations` action.)

No request body anywhere in these functions includes `tenant_id`, `role`, or `actor_type` — every function takes only `token` plus the domain fields the backend actually needs (business idea text, answer text, section data, claim/recommendation id, optional reason). Verified by grep across the new files (§ Security review).

## 7. Journey-state mapping (`frontend/lib/businessJourneyController.ts`)

One small, pure, presentation-only module:
- `getJourneyDestination(status)` → route (`DISCOVERY_ACTIVE→/business/discovery`, `BLUEPRINT_REVIEW`/`BLUEPRINT_ACTIVE→/business/blueprint`, `RECOMMENDATIONS_READY→/business/recommendations`, `COMPLETED`/`ABANDONED→/business`).
- `getJourneyStageLabel(status)` → truthful stage copy for display.
- `isJourneyAtStage(status, allowed[])` → the guard every stage page uses before rendering.

Every page imports from this file instead of re-deriving its own switch; it contains no transition-validity logic (that stays server-side) and calls no API itself.

## 8. Discovery implementation

`frontend/app/business/discovery/page.tsx`: loads the journey, then (if `discovery_session_id` is set) `GET /business-discovery/sessions/{id}` for session + turns, finds the latest un-answered question from the turns, and renders it. `answerDiscoveryQuestion` submits; on `session_status !== "COMPLETED"` it shows the server's `next_question` and re-fetches the session to reconcile `questions_asked` (never incremented client-side). Progress text is truthful ("N questions answered so far" from `session.questions_asked`, never a fabricated "Question N of 8"). On `session_status === "COMPLETED"`, the page calls `completeDiscoveryJourneyStep` (Phase 13's `complete-discovery` action) and only then routes on via `getJourneyDestination`. A `useRef` guard (`submitting`) prevents a double-click firing two in-flight answer submissions. A 409 from either call triggers a full reload/reconciliation rather than a stuck UI.

## 9. Blueprint implementation

`frontend/app/business/blueprint/page.tsx`: fetches `getDraftBlueprint` while `BLUEPRINT_REVIEW`, or `getActiveBlueprint` once `BLUEPRINT_ACTIVE`. Renders all returned sections (not a fixed hardcoded list — whatever the backend returns) using `BLUEPRINT_SECTION_LABELS` for display names, each section's `status` badge, and its `data` as a definition list; empty sections show "Not filled in yet." rather than blank space. Editing (`PUT /business-blueprint/sections/{key}`) is offered only while the blueprint is `DRAFT`; the edit control is a JSON textarea (the section `data` shape is fully backend-defined/arbitrary JSONB, so a generic form engine was not built — matches the instruction not to build "a giant generic form engine unless the repo already has one worth reusing," and none exists). `PROPOSED` claims per section get Confirm/Reject buttons wired to `confirmBlueprintClaim`/`rejectBlueprintClaim`, each followed by a full reload (no optimistic claim-state mutation). "Confirm Blueprint" calls `confirmBlueprintJourneyStep` (never the raw `/business-blueprint/activate` endpoint) and routes on the server's returned status; a 409 (incomplete/minimum-bar failure, or already-active) reloads the journey and surfaces the backend's own message rather than guessing.

## 10. Recommendation implementation

`frontend/app/business/recommendations/page.tsx`: only renders once the journey is `RECOMMENDATIONS_READY` (recommendation generation itself happens from the Blueprint page's "Generate recommendations" button, which calls `generateRecommendationsJourneyStep` and routes here on success). Lists `GET /recommendations?blueprint_id=...` filtered to the journey's blueprint. Each card shows only real fields: Required/Optional (from `required`, never a frontend-invented ranking), What/Why, Dependencies (or "None"), Cost (`cost_estimate` if present, else the literal string "Cost estimate unavailable" — never a fabricated number), Confidence (or "Not provided"), Source, and Alternatives **only when `alternatives` is non-null**. Accept/Reject call the backend and replace only that card's row from the server's response (no optimistic status flip). A "Finish setup" button calls the journey's `complete` action once the user is done reviewing.

## 11. Resume behavior

The authoritative journey state is never cached in `localStorage`/`sessionStorage`/URL params/React context — every page independently calls `getCurrentBusinessJourney` on mount and branches purely off the server's response. Leaving mid-Discovery and returning later re-fetches the session/turns from the backend and re-derives the current question from turn history, not from any client-persisted draft.

## 12. Error handling

Every page follows the same map: 401 → `router.push("/login")` (mirrors `useAuth`'s own existing behavior for consistency); 403/422/5xx/network → `ApiError`'s message (or a page-specific fallback string) rendered in an `Alert`; 409 on any mutating action → reload/reconcile the journey (and blueprint/recommendation data as applicable) rather than leaving the UI on stale state. No route silently swallows an error.

## 13. RBAC behavior

No role-based UI hiding was added — consistent with the Website Builder precedent's own documented approach ("buttons are not hidden by role — the backend is the only authority ... a user lacking permission simply gets a 403 surfaced as an error banner"). A `MANAGE_*`-gated action a viewer-role user lacks permission for will 403 from the backend and surface as a red `Alert`; `READ_*`-gated pages will 403 on load with the same treatment.

## 14. Accessibility

Semantic `<h1>`/`<h2>` headings via `PageHeader`/section headers; the Discovery answer textarea and Blueprint section-edit textarea both carry explicit `aria-label`s; progress/status text uses `aria-live="polite"`; all actions are real `<button>` elements (via `Button`) with disabled state while a request is in flight, never a hover-only affordance; color is never the only status signal (`Badge` always pairs a color with text).

## 15. Tests

New Phase 14 test files (all under existing Vitest + RTL infra, no new dependency):
- `frontend/lib/__tests__/businessJourneyController.test.ts` — 8 tests (status→route mapping for all 6 statuses, stage-label truthiness, `isJourneyAtStage`).
- `frontend/app/business/__tests__/page.test.tsx` — 6 tests (no active journey/start form, start sends only `business_idea` — no tenant/role/actor spoofing, existing active journey is resumed not duplicated, COMPLETED state renders inline, ABANDONED offers restart, 5xx error banner).
- `frontend/app/business/discovery/__tests__/page.test.tsx` — 5 tests (question renders from backend, answer submission + next question, completion → `complete-discovery` → route, direct-URL guard redirects off-stage, error state on submission failure).
- `frontend/app/business/blueprint/__tests__/page.test.tsx` — 8 tests (sections render from backend, empty-section handling, claim confirm, claim reject, confirm via the named journey action, 409/incomplete-blueprint reconciliation, direct-URL guard, `BLUEPRINT_ACTIVE` read-only + generate-recommendations action).
- `frontend/app/business/recommendations/__tests__/page.test.tsx` — 8 tests (cards render real fields, required/optional truthful, null-cost honest message, alternatives omitted when absent, accept reconciles status, reject reconciles status, direct-URL guard when recommendations not yet generated, loading state).

Total new tests: 35. Security/permission behavior (no tenant_id/role/actor_type in any request body) and resilience behavior (409 reconciliation, direct-route redirect, duplicate-click guard via `useRef` on Discovery's answer submit and Blueprint's confirm) are covered inside the above suites rather than as a separate file, since each is inseparable from the page it protects.

## 16. Browser E2E status

**NOT AVAILABLE** — confirmed no Playwright/Cypress/other E2E framework exists anywhere in the repo (only Vitest+RTL, per `frontend/vitest.config.ts` and the full absence of any e2e config/directory). Per the phase instructions, Playwright/Cypress was deliberately not introduced to avoid expanding phase scope. The RTL integration tests above exercise each page's full backend-contract interaction (mocked at the `lib/api.ts` boundary) including the multi-step Discovery→completion and Blueprint→confirm→(routes to)Recommendations transitions, but a true single-process browser walk of the entire journey against a live backend was not built. Manual visual verification of the four new pages in a running browser against a live backend was also not performed in this session (no backend server was started — Phase 14 is frontend-only and starting the backend was out of scope for the available session budget); this is recorded as a known limitation in §20, not silently skipped.

## 17. Backend changes

**NONE.** No file under `backend/` was modified. Every Phase 14 page/API-client function was verified to fit the existing, already-implemented Phase 2/3/13 contracts exactly as read from source in §2 — no contract gap was found that would have required a compatibility fix.

## 18. Security review

- Grepped every new/modified frontend file for `tenant_id`/`role`/`actor_type` in request-body position, `dangerouslySetInnerHTML`, `eval(`, `new Function(`, and vertical hardcoding (`medical_tourism`, `dropshipping`) — zero matches outside test-mock `UserResponse` display objects (which mirror the app-wide existing `useAuth` user shape used only for display, never sent in a request body).
- No direct AI-provider calls, no credentials/API keys, no `localStorage`/`sessionStorage` used for authoritative journey state (only `sessionStorage` for the existing app-wide access token, unchanged).
- Every mutating call in the new API client functions takes only the resource id(s) and the specific field(s) the backend endpoint expects — tenant scoping is implicit via the bearer token exactly like every other function in `lib/api.ts`.

## 19. Performance considerations

Each page fetches the journey once on mount (no polling). Discovery re-fetches the session only after an answer submission (to reconcile `questions_asked`), not on an interval. Blueprint/Recommendations re-fetch only after a mutating action succeeds or a 409 is caught. No component fetches the same resource from two places simultaneously.

## 20. Known limitations

- No live-backend manual/visual QA was performed in this session (see §16) — typecheck, unit/integration tests (mocked contract), and production build were all run and passed, but a real running-backend walkthrough was not.
- Blueprint section editing uses a raw JSON textarea rather than a per-field form, since section `data` is backend-defined arbitrary JSON with no existing generic form engine in the repo to reuse (documented in §9, matches the spec's own instruction not to build one).
- "Finish setup" (calling the journey's `complete` action) is offered on the Recommendations page as soon as the page loads, not gated on every recommendation having been decided (an `Alert` nudges the user once all are decided, but doesn't block the button) — the backend's own `complete` action is the actual authority on whether this transition is valid at any given moment; the frontend does not duplicate that rule.
- `mobile`/`tablet` viewport rendering was not interactively verified via the browser preview tool in this session (no backend session available to authenticate into these routes); the pages reuse the exact same responsive Tailwind conventions (`grid-cols-1 sm:grid-cols-2`, `klaros-card`, `AppShell`'s existing mobile nav) as the already-shipped, already-responsive Website Builder and dashboard pages.

## 21. Deferred work

Everything explicitly out of scope per the task's SCOPE — OUT list (Halla, Dropshipping, MCP client, OAuth, Agent Configuration UI, Website Builder backend/runtime changes, autonomous execution, automatic provider/agent/website creation, RLS enforcement, a new orchestration engine) — none of it was touched or begun.

## 22. Exact final test counts

- Frontend baseline: 30 passed / 0 failed / 0 skipped (7 files).
- New Phase 14 tests: 35 passed / 0 failed / 0 skipped (5 files).
- Final frontend total: 65 passed / 0 failed / 0 skipped (12 files).
- Typecheck (`npx tsc --noEmit`): PASS (no output, exit 0).
- Production build (`npm run build`): PASS — all 4 new routes (`/business`, `/business/discovery`, `/business/blueprint`, `/business/recommendations`) compiled and statically generated alongside the existing 57 routes.
- Browser E2E: NOT AVAILABLE (no framework in repo; see §16).
- Backend: untouched, no backend test run performed (no backend changes made).

## 23. Git state

- No commit was created. No push was performed. No PR was opened.
- New files: `frontend/app/business/page.tsx`, `frontend/app/business/discovery/page.tsx`, `frontend/app/business/blueprint/page.tsx`, `frontend/app/business/recommendations/page.tsx`, `frontend/lib/businessJourneyController.ts`, `frontend/lib/__tests__/businessJourneyController.test.ts`, `frontend/app/business/__tests__/page.test.tsx`, `frontend/app/business/discovery/__tests__/page.test.tsx`, `frontend/app/business/blueprint/__tests__/page.test.tsx`, `frontend/app/business/recommendations/__tests__/page.test.tsx`, this log file.
- Modified files: `frontend/lib/api.ts` (new Business Journey/Discovery/Blueprint/Recommendations section appended), `frontend/components/AppShell.tsx` (added "Build Your Business" nav item under Overview, added `Sparkles` icon import).
- All prior Phase 0–13 uncommitted changes remain exactly as they were at session start — nothing in `backend/` was touched.

## Acceptance criteria vs. actual coverage

All Journey/Discovery/Blueprint/Recommendations/Resume/Security/UX acceptance criteria in the task spec are met except the live-backend visual/E2E walkthrough, which could not be performed in this session (§16, §20) — this keeps the phase at **COMPLETE WITH LIMITATIONS**, not COMPLETE, per the instruction not to overclaim.
