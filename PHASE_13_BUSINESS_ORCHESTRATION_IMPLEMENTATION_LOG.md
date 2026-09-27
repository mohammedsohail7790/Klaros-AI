# PHASE 13 — BUSINESS ORCHESTRATION FOUNDATION — IMPLEMENTATION LOG

## 1. Baseline

Phases 0–12 were already implemented locally, uncommitted/unpushed, at repo
root. `KLAROS_POST_PHASE_12_END_TO_END_AUDIT.md` (§14, "Business
Orchestration Audit") concluded Discovery, Blueprint, Recommendations, and
Agent configuration are individually shipped (Phases 2/3/4) but never
connected into one coherent, resumable product journey, and none of them
have a dedicated frontend route.

Before any edit, the following were read in full: the audit doc (§6, §7,
§14, §17, §21), `PHASE_2_IMPLEMENTATION_LOG.md`, `PHASE_3_IMPLEMENTATION_LOG.md`,
and the actual source of `app/models/business_discovery.py`,
`app/models/business_blueprint.py`, `app/models/recommendation.py`,
`app/services/business_discovery_service.py`,
`app/services/business_blueprint_service.py`,
`app/services/recommendation_service.py`, `app/api/v1/business_discovery.py`,
`app/api/v1/business_blueprint.py`, `app/api/v1/recommendations.py`,
`app/api/deps.py`, `app/models/rbac.py`, `app/db/base.py`,
`app/models/__init__.py`, `app/api/v1/router.py`,
`alembic/versions/0044_recommendation_engine.py` and `0050_website_builder.py`
(for exact RLS-audit-mode / partial-unique-index migration style), and the
`tests/test_postgres_*` methodology (pgserver-based, `pytest.mark.skipif`
gated on `DATABASE_URL`).

Pre-existing alembic head at the start of this phase: **0050**
(`0050_website_builder.py`). A `pgserver`-provisioned disposable PostgreSQL
16 instance was started fresh for this phase at
`/private/tmp/klaros_pg_phase13` (database `klaros`), since the
`.venv/lib/python3.12/site-packages/pgserver` package was already vendored
in `backend/.venv` from a prior phase's setup — no new venv was created.

Focused pre-existing-test baseline (the two closest analogues,
`test_postgres_business_discovery_blueprint_rls_audit_mode.py` and
`test_postgres_recommendation_rls_audit_mode.py`) passed unmodified against
this fresh Postgres instance before any Phase 13 code was added, confirming
the instance and existing schema were sound to build on.

## 2. Exact scope

Implemented exactly the scope in §A–H of the task brief: a
`BusinessJourney` persistence model, a deterministic state machine, a
single coordinating `BusinessJourneyService`, a minimal authenticated API
surface, explicit human-confirmation checkpoints, audit logging via the
existing `AuditLog` table, tenant isolation, and real-Postgres-validated
tests (creation, lifecycle, transitions, duplicate start, tenant isolation,
resume, interruption/retry, invalid transitions, human-confirmation
boundaries, and concurrency).

Nothing outside this scope was touched: no frontend file was created or
modified, no Medical Tourism file was touched, no Dropshipping/OAuth/MCP
client/Website Builder file was touched, no Temporal workflow was added, no
second workflow/execution engine was introduced.

## 3. Architecture

`BusinessJourney` is a thin coordinator sitting entirely above three
already-shipped, already-authoritative subsystems:

```
BusinessJourneyService
    ├── BusinessDiscoveryService   (Phase 2 — unmodified)
    ├── BusinessBlueprintService   (Phase 2 — unmodified)
    └── RecommendationService      (Phase 3 — unmodified)
```

No file in Phase 2 or Phase 3 (`app/models/business_discovery.py`,
`app/models/business_blueprint.py`, `app/models/recommendation.py`,
`app/services/business_discovery_service.py`,
`app/services/business_blueprint_service.py`,
`app/services/recommendation_service.py`,
`app/api/v1/business_discovery.py`, `app/api/v1/business_blueprint.py`,
`app/api/v1/recommendations.py`) was modified by this phase. Every journey
transition either (a) reads an existing subsystem row's real status, or (b)
calls exactly one existing subsystem *write* method
(`BusinessDiscoveryService.start_session`,
`BusinessBlueprintService.activate`,
`RecommendationService.generate_recommendations`) — the journey layer never
re-implements Discovery's completion criteria, Blueprint's minimum-bar
check, or the Recommendation Engine's matching pipeline.

## 4. Model

`app/models/business_journey.py` — `BusinessJourney(TenantScopedMixin, Base)`,
table `business_journeys`:

| Field | Type | Notes |
|---|---|---|
| `id`, `tenant_id`, `created_at`, `updated_at` | (from `TenantScopedMixin`) | |
| `status` | `String(30)` | `BusinessJourneyStatus` |
| `discovery_session_id` | `Uuid`, FK `discovery_sessions.id`, nullable | |
| `blueprint_id` | `Uuid`, FK `business_blueprints.id`, nullable | set only once Discovery reports COMPLETED |
| `recommendation_run_id` | `Uuid`, FK `recommendation_runs.id`, nullable | |
| `created_by` | `Uuid`, nullable | |
| `completed_at` / `abandoned_at` | `DateTime`, nullable | |
| `last_error` | `Text`, nullable | informational only, never gates a retry |

`BusinessJourneyStatus` (`StrEnum`): `DISCOVERY_ACTIVE`, `BLUEPRINT_REVIEW`,
`BLUEPRINT_ACTIVE`, `RECOMMENDATIONS_READY`, `COMPLETED`, `ABANDONED`. No
separate "NEW" state — `start_journey` creates the row directly in
`DISCOVERY_ACTIVE`, since starting a journey *is* starting a
`DiscoverySession`.

One-active-journey-per-tenant is enforced with a **real partial unique
index**, `uq_business_journeys_one_active_per_tenant`, on `tenant_id WHERE
status NOT IN ('COMPLETED', 'ABANDONED')` — never only an
application-level check, mirroring `business_blueprints`'
`uq_business_blueprints_one_active_per_tenant` (0043) and Website's
one-PUBLISHED-version-per-website pattern (0050) exactly.

Registered in `app/models/__init__.py`.

## 5. State machine

```
DISCOVERY_ACTIVE  --complete-discovery-->  BLUEPRINT_REVIEW
BLUEPRINT_REVIEW  --confirm-blueprint-->   BLUEPRINT_ACTIVE
BLUEPRINT_ACTIVE  --generate-recommendations--> RECOMMENDATIONS_READY
RECOMMENDATIONS_READY --complete-->        COMPLETED
(any non-terminal state) --abandon-->      ABANDONED
```

Every transition is server-decided (`BusinessJourneyService`), never
client-supplied. Guard conditions are re-derived from the *real* subsystem
state on every call, never cached:

- `complete-discovery`: requires `DiscoverySession.status == COMPLETED`
  (Phase 2's own real completion criterion — minimum-bar sections filled or
  question cap reached — never re-implemented here).
- `confirm-blueprint`: requires the linked `BusinessBlueprint` to be either
  already `ACTIVE` (idempotent resume path) or successfully activatable via
  `BusinessBlueprintService.activate()` (Phase 2's own minimum-bar check,
  unmodified).
- `generate-recommendations`: requires the journey to be `BLUEPRINT_ACTIVE`;
  reuses an existing `RecommendationRun` for the current blueprint version
  if one exists, otherwise calls `RecommendationService.generate_recommendations()`.
- `complete`: requires `RECOMMENDATIONS_READY`.
- `abandon`: legal from any non-terminal state; illegal from `COMPLETED`.

Calling a forward action when the journey is already past that checkpoint
is a no-op returning the current state (HTTP 200), never an error — this is
what makes retries and resumed clients safe. Calling one out of order (skip
ahead) is HTTP 409.

## 6. APIs

All under `/api/v1/business-journey`, all tenant-scoped via `CurrentUser`
(never a request-body tenant field):

| Method | Path | Permission |
|---|---|---|
| POST | `/business-journey` | `MANAGE_BUSINESS_JOURNEY` |
| GET | `/business-journey` | `READ_BUSINESS_JOURNEY` |
| GET | `/business-journey/history` | `READ_BUSINESS_JOURNEY` |
| GET | `/business-journey/{id}` | `READ_BUSINESS_JOURNEY` |
| POST | `/business-journey/{id}/complete-discovery` | `MANAGE_BUSINESS_JOURNEY` |
| POST | `/business-journey/{id}/confirm-blueprint` | `MANAGE_BUSINESS_JOURNEY` |
| POST | `/business-journey/{id}/generate-recommendations` | `MANAGE_BUSINESS_JOURNEY` |
| POST | `/business-journey/{id}/complete` | `MANAGE_BUSINESS_JOURNEY` |
| POST | `/business-journey/{id}/abandon` | `MANAGE_BUSINESS_JOURNEY` |

Every forward transition is an explicit named action, never a generic
`POST /transition?from=X&to=Y`. `GET /business-journey` (current active) is
also the resume entry point: a client re-fetches it and calls whichever
named action corresponds to `status`.

Registered in `app/api/v1/router.py`; dependency wiring in
`app/api/tool_deps_business_journey.py` (same `@lru_cache` singleton
pattern as the other tool_deps modules).

## 7. Service boundaries

`app/services/business_journey_service.py` — `BusinessJourneyService`
takes the existing `BusinessDiscoveryService`, `BusinessBlueprintService`,
and `RecommendationService` instances as constructor dependencies (wired in
`tool_deps_business_journey.py`) and never constructs its own competing
logic for any of their domains. It owns only:

- `start_journey`, `get_current`, `get_by_id`, `list_journeys`
- `complete_discovery`, `confirm_blueprint`, `generate_recommendations`
- `complete`, `abandon`

## 8. Human checkpoints

Three explicit, never-silently-crossed checkpoints, exactly as specified:

1. **Discovery complete → ready for Blueprint generation**: gated by
   `complete-discovery`, which itself gates on `DiscoverySession.status ==
   COMPLETED` (a real Phase 2 status, not invented here).
2. **Blueprint ready for confirmation**: gated by `confirm-blueprint`,
   which delegates the actual activation decision entirely to
   `BusinessBlueprintService.activate()` — the same minimum-bar check the
   standalone Blueprint API already enforces. The journey never
   auto-activates a Blueprint; a human must call this action.
3. **Recommendations ready for human decision**: `generate-recommendations`
   only ever creates `Recommendation` rows in `PROPOSED` status (via the
   unmodified Recommendation Engine) — nothing in this phase accepts,
   executes, connects a provider, creates an Agent, or publishes a Website.

No new `JourneyApproval` table was created — Blueprint's own
activation-as-confirmation boundary is reused directly, per the task's
explicit "reuse rather than invent a parallel approval system" guidance.

## 9. Idempotency

- **Duplicate start**: `start_journey` first checks for an existing active
  journey and returns it unchanged. Concurrency is closed at two layers:
  (a) a Postgres advisory lock (`pg_advisory_xact_lock(hashtext(tenant_id))`,
  no-op on SQLite) serializes concurrent starts for the same tenant *before*
  ever calling into Discovery, and (b) the `uq_business_journeys_one_active_per_tenant`
  partial unique index is the DB-level backstop — a losing `INSERT` raises
  `IntegrityError`, caught, and the winner's row is returned instead of
  propagating an error (mirrors `AutomationService`'s established
  `except IntegrityError` dedup pattern).
- **Duplicate discovery completion**: repeating `complete-discovery` once
  the journey is past `DISCOVERY_ACTIVE` is a no-op — no second Blueprint
  reference is ever attached.
- **Duplicate blueprint activation**: `confirm-blueprint` checks the
  Blueprint's actual status before calling `activate()`; if already
  `ACTIVE` (activated directly via the Blueprint API, or a resumed retry
  after `activate()` already succeeded), it skips straight to advancing the
  journey.
- **Duplicate recommendation generation**: `generate-recommendations`
  acquires a Postgres row lock (`SELECT ... FOR UPDATE`) on the journey's
  own row before checking for an existing `RecommendationRun` for the
  current blueprint version; concurrent callers queue on this lock and the
  losers reuse the winner's run rather than generating a second one. This
  was necessary because `RecommendationRun` has no DB-level uniqueness on
  `(tenant_id, blueprint_id, blueprint_version)` in Phase 3 — the journey
  closes this gap at its own layer instead of modifying Phase 3's schema
  (see §19 Limitations).
- **Retry after timeout**: every forward action re-derives its guard
  condition from real, freshly-read subsystem/journey state — a repeated
  client call after a lost response always converges on the same resulting
  state, never re-performs the underlying action.

## 10. Failure / resume behavior

Crash between Discovery-completion and Blueprint-generation: nothing to
generate — `complete-discovery` only *attaches* the already-existing DRAFT
blueprint reference; resume re-reads `DiscoverySession.status` and
proceeds safely.

Crash between Blueprint-activation and Recommendation-generation: resume
calls `confirm-blueprint` again → sees Blueprint already `ACTIVE` → skips
re-activation → advances the journey. Then `generate-recommendations` →
finds the existing `RecommendationRun` for that blueprint version → reuses
it instead of generating a duplicate.

No Temporal workflow, no second execution/workflow engine — every
transition is a single, short, committed unit of work with the real
subsystem state as ground truth, per the task's explicit constraint.

## 11. Tenant isolation

Every `BusinessJourneyService` method takes an explicit `tenant_id` from
`CurrentUser` (never a request body field) and filters every query by it,
mirroring `BusinessBlueprintService`/`RecommendationService`'s exact
convention. Verified by dedicated tests: tenant B gets 404 reading,
advancing, or abandoning tenant A's journey, and tenant B's own "current
journey" view never surfaces tenant A's row.

## 12. RBAC

Two new permissions, `READ_BUSINESS_JOURNEY` / `MANAGE_BUSINESS_JOURNEY`,
added to `app/models/rbac.py` following the exact same read/manage split
every other domain in that file already uses. Granted to
OWNER/ADMIN (via `_ALL_PERMISSIONS`), MANAGER (both), STAFF/READ_ONLY
(read only) — TECHNICIAN and ACCOUNTANT get neither, matching their
existing exclusion from `READ_BLUEPRINT`/`READ_RECOMMENDATIONS`. A new pair
was justified (rather than reusing `MANAGE_BUSINESS_DISCOVERY`) because a
single journey action can trigger Blueprint activation and Recommendation
generation too — no single existing subsystem permission covers the whole
surface, and the journey layer never uses its own permission to bypass a
subsystem's own tenant-scoped checks (it still calls
`BusinessBlueprintService`/`RecommendationService` directly).

## 13. Audit logging

Every mutating journey action writes one `AuditLog` row via the existing
table (no new audit mechanism): `business_journey.start`,
`.complete_discovery`, `.confirm_blueprint`, `.generate_recommendations`,
`.complete`, `.abandon` — tenant-scoped, actor-aware, with a small
non-sensitive `input_summary` (resource ids only, never raw business
content or credentials).

## 14. Migration

`alembic/versions/0051_business_journey.py` (`0050 -> 0051`): creates
`business_journeys` with FKs to `discovery_sessions`, `business_blueprints`,
`recommendation_runs`; five plain indexes; the partial unique index
described in §4/§9; RLS enabled with the existing audit-mode
(`USING(true) WITH CHECK(true)`) policy, PostgreSQL-only, matching every
migration since 0040. `downgrade()` reverses all of it, tested clean (see
§16).

## 15. Tests

`tests/test_business_journey_api.py` — 15 focused, SQLite-backed API tests:
authentication required, RBAC (TECHNICIAN blocked from both read and
manage), start creates a linked DiscoverySession, duplicate start returns
the same journey, get-current / get-by-id / history, 404 with no active
journey, three illegal-transition-returns-409 cases (skip-ahead at each
checkpoint), the full happy path through to `RECOMMENDATIONS_READY` and
`COMPLETED` (plus a same-tenant restart afterward), full idempotent-repeat
coverage of all three forward actions, abandon (+ idempotent repeat, +
blocked-after-completion, + restart-after-abandon), and two tenant-isolation
tests (cross-tenant read/advance/abandon all 404; a tenant never sees
another tenant's "current" journey).

`tests/test_postgres_business_journey_concurrency.py` — 4 real-Postgres-only
tests (skipped on SQLite): RLS audit-mode instrumentation verification,
partial-unique-index existence check, 5-concurrent-`start_journey`-calls
resolving to exactly one journey row, and 5-concurrent-
`generate-recommendations`-calls resolving to exactly one `RecommendationRun`.

Total new tests: **19** (15 + 4), all passing on both SQLite and real
Postgres (Postgres-only tests skip cleanly on SQLite).

## 16. Real Postgres validation

Instance: `pgserver`-provisioned PostgreSQL 16, database `klaros`, unix
socket `/private/tmp/klaros_pg_phase13` (loopback-only, disposable, never
touching any host/production Postgres). `alembic upgrade head` from a bare
database (0001→0051) succeeded end-to-end. Two full
`downgrade(0050)`/`upgrade(head)` cycles run for `0051` specifically — both
clean, `alembic current` ends at `0051 (head)` both times.

A real bug was caught only against Postgres and fixed before landing: the
first version of `start_journey` called `BusinessDiscoveryService.start_session`
→ `BusinessBlueprintService.get_or_create_draft` for brand-new tenants
without any lock; 5 concurrent journey-starts for one new tenant raced on
`business_blueprints`' own `(tenant_id, version)` unique constraint (a
pre-existing Phase 2 gap this phase's own concurrency test newly exercised
— see §19). Fixed at the journey layer with a Postgres advisory lock (§9),
without modifying Phase 2 code. All 19 new tests plus the two closest
pre-existing analogue Postgres suites
(`test_postgres_recommendation_rls_audit_mode.py`,
`test_postgres_business_discovery_blueprint_rls_audit_mode.py`) pass
against this instance, re-run 3× for the concurrency tests specifically to
rule out flakiness — stable every time.

## 17. Regression

Full backend suite baseline and final-state comparison, plus the
`test_agent_no_hardcoding_guard.py` / `test_vertical_extension_no_hardcoding_guard.py`
/ `test_cross_vertical_recommendation_validation.py` genericity guards, are
recorded in the companion regression note captured during this session
(see the session's final report for the exact pass/fail/skip counts and
baseline arithmetic reconciliation). No test outside `test_business_journey_*`
was modified.

## 18. Security audit

New/modified files (`app/models/business_journey.py`,
`app/services/business_journey_service.py`,
`app/api/v1/business_journey.py`, `app/api/tool_deps_business_journey.py`,
`alembic/versions/0051_business_journey.py`, `app/models/rbac.py`,
`app/models/__init__.py`, `app/api/v1/router.py`) were inspected for:
tenant spoofing (none — `tenant_id` always sourced from `CurrentUser`, no
request model anywhere accepts `tenant_id`/`organization_id`/`role`/
`actor_type`), unsafe dynamic SQL (only parameterized `text()` calls for
the Postgres advisory lock / row lock, both with bound parameters, never
string-interpolated), `eval`/`exec`/`subprocess`/shell execution (none),
secret logging (none — `AuditLog.input_summary` carries only ids/status
strings), cross-tenant FK attachment (every linked-resource read goes
through the owning service's own tenant-scoped `get_by_id`/`get_session`
before the id is ever stored on the journey), arbitrary state transitions
(the transition graph is entirely server-coded, no client-supplied target
state), privilege escalation (RBAC checked via the standard
`require_permission` dependency, no bypass), duplicate execution (§9).
Security-focused test coverage: `test_technician_cannot_manage_journey`,
the two tenant-isolation tests, and the concurrency tests double as
duplicate-execution security tests.

## 19. Limitations

- `RecommendationRun` (Phase 3, unmodified) has no DB-level uniqueness on
  `(tenant_id, blueprint_id, blueprint_version)`. This phase closes that
  gap only at the journey layer (a Postgres row lock on the journey's own
  row around the check-then-generate sequence — §9), which is sufficient
  for every path that goes through `BusinessJourneyService`, but a caller
  invoking `RecommendationService.generate_recommendations()` directly
  and concurrently (bypassing the journey) is not protected by this
  phase's fix — that was true before this phase too, and is out of this
  phase's authorized scope to fix in Phase 3's own file.
- `BusinessBlueprintService.get_or_create_draft` (Phase 2, unmodified) has
  its own pre-check-then-insert race on `(tenant_id, version=1)` for a
  brand-new tenant's very first blueprint. This phase's advisory lock in
  `start_journey` closes the race for every path that goes through the
  journey (the only intended entry point per the product flow), but a
  caller hitting `POST /business-discovery/sessions` directly, concurrently,
  for a tenant with no journey yet, remains exposed — pre-existing, not
  introduced or worsened by this phase, out of scope to fix in Phase 2's
  own file.
- No dedicated resume/repair endpoint beyond `GET /business-journey`
  (current) plus the self-repairing forward actions — sufficient per the
  task's own guidance ("thin persisted coordinator", "resume behavior"
  demonstrated via idempotent re-calls), but there is no single "resume"
  verb a frontend could call generically; it must know which action
  corresponds to the returned `status`.
- `last_error` is set only on `abandon` (as the reason) — forward-action
  failures surface as HTTP 409 with a message but do not persist onto the
  journey row itself, since every guard is re-derived fresh rather than
  cached.

## 20. Deferred work

- Any frontend route for Discovery/Blueprint/Recommendations/the journey
  itself (explicitly out of scope for this phase).
- Automatic execution of accepted recommendations, automatic integration
  connection, automatic Agent creation, automatic Website
  generation/publication from a completed journey — all explicitly
  deferred to a future phase per the task's Website/Agent Boundary section.
- A DB-level uniqueness constraint on `RecommendationRun` (§19) — would
  belong to a future Phase 3 hardening pass, not this phase.
- RLS enforcement (FORCE ROW LEVEL SECURITY) — audit-mode only, as
  instructed.

## 21. Explicit out-of-scope items (confirmed untouched)

Discovery/Blueprint/Recommendation/Agent frontend, Website Builder,
Medical Tourism, Dropshipping, OAuth, MCP client, Halla, RLS enforcement,
Temporal workflows for this journey, a second execution/workflow engine, a
generic orchestration framework, `Organization.autonomy_level`.

## 22. Final verdict

**Phase 13: COMPLETE.** See the session's final report for the exact
regression pass/fail/skip counts against the measured pre-edit baseline.
