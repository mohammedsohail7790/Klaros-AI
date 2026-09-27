# Phase 2 Implementation Log — Business Discovery + Requirements + Business Blueprint

## Baseline

- Branch: `main`. Nothing from any prior phase was committed — all Phase 0/1 work remained uncommitted local changes, as stated in the task brief; this phase adds to that same uncommitted working tree.
- `alembic heads` (from `backend/`) before this phase: **0042** (confirmed via `alembic.script.ScriptDirectory`). A stray `backend/dev.db` (SQLite) reported `0039` via `alembic current` — that file is stale/unrelated to the real verification path and was not used.
- A running, disposable PostgreSQL 16 instance (via the `pgserver` PyPI package, matching the approach documented for Phase 0/1) was already present on this host at `/private/tmp/klaros_pg1`, database `klaros`, at head `0042` with 110 tables. This phase's migration testing, RLS audit-mode tests, and full-pipeline Postgres test all ran against that real instance — no Docker was available (`docker --version` failed: `command not found`), matching the prior phases' documented fallback.
- Phase 1 regression: `tests/test_vertical_extension_no_hardcoding_guard.py` (4 tests) and `tests/test_integration_provider_catalog.py` were re-run before and after this phase's changes; both pass unchanged.

## Architecture references consulted

Read directly (not just via subagent summary, spot-verified with `grep`/`sed`): `KLAROS_ARCHITECTURE_RECONCILIATION.md`, `KLAROS_FINAL_DOMAIN_MODEL.md`, `KLAROS_BUSINESS_BLUEPRINT_SPEC.md`, `KLAROS_BUSINESS_DISCOVERY_SPEC.md`, `KLAROS_FINAL_API_ARCHITECTURE.md`. A research pass also read `KLAROS_FINAL_SECURITY_MODEL.md`, `KLAROS_FINAL_DATABASE_ARCHITECTURE.md`, `PHASE_1_IMPLEMENTATION_LOG.md`, `KLAROS_CURRENT_VS_TARGET.md`, plus the actual Phase 0/1 source (`app/models/rbac.py`, `app/models/vertical_extension.py`, `app/db/base.py`, `app/api/deps.py`, `app/api/v1/integrations.py`, `app/services/ai_provider.py`, `app/services/ai_qualification_service.py`, `app/services/company_memory_service.py`, `alembic/versions/0041_*.py`, `alembic/versions/0042_*.py`, `tests/test_postgres_vertical_extension_rls_audit_mode.py`, `tests/test_vertical_extension_no_hardcoding_guard.py`). Every field/enum/route name below was verified against the actual doc text or actual source, not taken on faith from the research summary alone.

## Resolved contradictions / judgment calls

The docs are internally inconsistent in a few places that mattered for implementation. Per the task's resolution order (reconciliation → final domain model → blueprint spec → actual implementation), the following calls were made and are recorded here so they're auditable:

1. **`DiscoverySession` internal shape.** `KLAROS_BUSINESS_DISCOVERY_SPEC.md` §4 describes `DiscoverySession.messages` as an inline JSONB array with no child table. `KLAROS_FINAL_DOMAIN_MODEL.md` describes a `DiscoveryTurn` child table, and `KLAROS_FINAL_API_ARCHITECTURE.md`'s endpoint table says the answer endpoint "writes `DiscoveryTurn`". **Decision: implemented `DiscoveryTurn` as a real child table** (`app/models/business_discovery.py`) — it's the more complete/operationally-consistent of the two descriptions and the reconciliation doc doesn't contradict it.
2. **`DiscoverySession.status` enum.** Discovery Spec uses `IN_PROGRESS/COMPLETE/ABANDONED`; Domain Model uses `ACTIVE/COMPLETED/PROMOTED`. **Decision: used Domain Model's `ACTIVE/COMPLETED/PROMOTED`**, plus an explicit `ABANDONED` terminal state (present in neither doc's primary enum, but both specs describe the "user abandons" failure mode, and every other lifecycle entity in this codebase has an explicit cancel state) — see `DiscoverySessionStatus`. `PROMOTED` is defined but **not yet driven by any code path in this phase** — see Known Limitations.
3. **Phase 2 vs. Phase 3 doc split.** `KLAROS_ARCHITECTURE_RECONCILIATION.md` #4 splits Discovery (docs' "Phase 2") from Blueprint (docs' "Phase 3"). This task's own prompt explicitly scopes "Phase 2" as Discovery + Requirements + Blueprint together, so both table families were implemented in one migration/pass, as instructed. This does not violate the reconciliation's actual technical content (which is about *what ships with what*, not *whether Discovery can be exercised without Blueprint tables* — here it always can, since a `DiscoverySession` always has a `blueprint_id` pointing at a lazily-created DRAFT).
4. **`BlueprintClaim` field naming.** Blueprint Spec uses `key`/`value`/`evidence`/`source` (3-value enum); Domain Model uses `content`/`evidence_ref`/`source` (a *different* 3-value enum: `Discovery/Human/Recommendation`, describing which subsystem produced a claim — not the same concept as the Discovery Spec's per-claim-type provenance table). **Decision:** used Blueprint Spec's `key`/`value` (directly useful for recomputing `BlueprintSection.data`), renamed `evidence` to `evidence_ref` (Domain Model's name, clearer), added an explicit `discovery_turn_id` FK (neither doc specifies this explicitly; both research passes independently recommended it over string-parsing a free-text evidence pointer), and used the Discovery Spec's 3-value **provenance** enum (`USER_STATED/AI_INFERRED/SYSTEM_DEFAULT`) under the field name `provenance` (not `source`, to avoid colliding with Domain Model's differently-scoped `source` concept, which was not implemented — no `Recommendation` model exists in this phase).
5. **`BlueprintClaim.status`.** Blueprint Spec: 3 values (`PROPOSED/CONFIRMED/REJECTED`). Domain Model: 4 values (adds `SUPERSEDED`). **Decision: used the 4-value superset** — needed so claims remain traceable across blueprint versions.
6. **"Imported" provenance.** The task's own prompt lists "user-provided / system-derived / AI-inferred / imported" as the provenance vocabulary to preserve. No source doc defines an import mechanism for Phase 2, and none was built. `ClaimProvenance` therefore has 3 values, not 4 — see Known Limitations.

No contradiction blocked implementation outright; none of the above met the "materially blocks" bar for a hard stop.

## Discovery architecture

`app/models/business_discovery.py`: `DiscoverySession` (tenant-scoped: `blueprint_id` nullable FK, `status`, `business_idea`, `questions_asked`, `max_questions` default 8, `created_by`) and `DiscoveryTurn` (tenant-scoped: `discovery_session_id` FK, `sequence`, `kind` [`INITIAL_DESCRIPTION`/`QUESTION_ANSWER`], `question` nullable, `answer` (always persisted, never lost even on extraction failure), `extraction_error` nullable).

`app/services/business_discovery_service.py` (`BusinessDiscoveryService`): orchestrates the pipeline from `KLAROS_BUSINESS_DISCOVERY_SPEC.md` §3 — `start_session` creates the session + an initial `DiscoveryTurn`, lazily creates (or reuses) the tenant's DRAFT/ACTIVE blueprint via `BusinessBlueprintService.get_or_create_draft`, then runs extraction and gap-checks against `MINIMUM_BAR_SECTIONS`. `submit_answer` appends a new turn, re-extracts, re-checks gaps. A session moves to `COMPLETED` when either (a) every minimum-bar section has full CONFIRMED coverage or (b) `questions_asked >= max_questions` (default 8, per spec §4's "hard cap"). A still-PROPOSED claim never closes a gap — only CONFIRMED claims count, preserving human-in-the-loop review before completion.

## Requirements architecture

There is no standalone `Requirement` table — per `KLAROS_BUSINESS_BLUEPRINT_SPEC.md` §3, "Requirement" is one of 8 `ClaimType` values on the single shared `BlueprintClaim` table (`Fact/Inference/Assumption/Requirement/Preference/Constraint/Decision/Unknown` — `app/models/business_blueprint.py::ClaimType`). Every claim carries `provenance` (`USER_STATED/AI_INFERRED/SYSTEM_DEFAULT`), `confidence` (0–1, only meaningful for AI-derived claims), `status` (`PROPOSED/CONFIRMED/REJECTED/SUPERSEDED`), and `evidence_ref`/`discovery_turn_id` back to the originating turn. An AI-derived claim is **never** silently promoted to CONFIRMED — only an explicit `POST .../claims/{id}/confirm` call (human action) can do that; `BlueprintSection.data` (the human-facing resolved view) is recomputed **only** from CONFIRMED claims.

## Blueprint architecture

`app/models/business_blueprint.py`: `BusinessBlueprint` (tenant-scoped envelope: `status` [DRAFT/ACTIVE/SUPERSEDED], `version`, `created_by`, `confirmed_at`, `vertical_extension_id` nullable FK into the Phase 1 `VerticalExtension` registry, `supersedes_id`) → `BlueprintSection` (one row per fixed `BlueprintSectionKey`, 20 literal values from the spec, JSONB `data`, `status` [EMPTY/DRAFT/COMPLETE]) → `BlueprintClaim` (atomic, evidence-linked). Domain-agnostic by construction: the only fixed vocabulary is the 20 section keys (universal business facets — identity, customers, revenue, ...), never a vertical name; a vertical's own shape lives inside a section's JSONB payload and/or the `vertical_extension_id` FK reference, looked up by id, never branched on by string (verified by both the existing Phase 1 guard test and a new Phase-2-scoped static check in `tests/test_cross_vertical_blueprint_validation.py`).

## Database

New tables (migration `0043_business_discovery_blueprint.py`, `alembic/versions/`): `business_blueprints`, `blueprint_sections`, `discovery_sessions`, `discovery_turns`, `blueprint_claims` (created in that dependency order). All five are genuinely tenant-owned business data (unlike Phase 1's two global-reference tables) and all get RLS audit-mode instrumentation in the same migration (`ENABLE ROW LEVEL SECURITY` + permissive `USING (true)` policy, never `FORCE`).

Constraints/indexes: `uq_business_blueprints_tenant_version` (unique tenant_id+version); `uq_business_blueprints_one_active_per_tenant` (partial unique index, `WHERE status='ACTIVE'` — enforced at the DB level, verified by a real-Postgres test that a second ACTIVE row for the same tenant raises an IntegrityError); `uq_blueprint_sections_blueprint_key` (unique blueprint_id+section_key); a GIN index on `blueprint_sections.data` (required switching that column to `JSONB` via `JSON().with_variant(JSONB(), "postgresql")` — plain `json` has no default GIN operator class, caught and fixed during real-Postgres migration testing); composite indexes `(tenant_id, blueprint_id, status)` and `(tenant_id, claim_type)` on `blueprint_claims`; FK indexes on every FK column.

## Migrations

`backend/alembic/versions/0043_business_discovery_blueprint.py`, `down_revision="0042"`. Tested against real PostgreSQL: `upgrade head` → verified table/RLS/index shape → `downgrade 0042` → `upgrade head` → `downgrade 0042` → `upgrade head` (two full up/down cycles) — deterministic each time, `alembic current` ends at `0043 (head)`.

## API

All under `/api/v1`, all requiring a valid JWT (`get_current_user`):

| Method | Path | Auth/Permission | Purpose |
|---|---|---|---|
| POST | `/business-discovery/sessions` | `MANAGE_BUSINESS_DISCOVERY` | start a session from free text |
| POST | `/business-discovery/sessions/{id}/answer` | `MANAGE_BUSINESS_DISCOVERY` | submit an answer; 404 unknown session, 409 already completed |
| GET | `/business-discovery/sessions/{id}` | `READ_BUSINESS_DISCOVERY` | read session state + turns |
| GET | `/business-blueprint` | `READ_BLUEPRINT` | active blueprint + sections + claims; 404 if none active |
| GET | `/business-blueprint/draft` | `READ_BLUEPRINT` | the tenant's current DRAFT/ACTIVE blueprint (own addition — not in the literal API-architecture table, needed so a UI can review PROPOSED claims pre-activation) |
| GET | `/business-blueprint/versions/{version}` | `READ_BLUEPRINT` | a specific historical version, read-only |
| PUT | `/business-blueprint/sections/{key}` | `MANAGE_BLUEPRINT` | human-edit a section; creates a new version if the blueprint is ACTIVE; 404/409 |
| POST | `/business-blueprint/activate` | `MANAGE_BLUEPRINT` | DRAFT → ACTIVE (minimum-bar check); 404/409 |
| POST | `/business-blueprint/claims/{id}/confirm` | `MANAGE_BLUEPRINT` | explicit action endpoint (never generic PATCH), idempotent |
| POST | `/business-blueprint/claims/{id}/reject` | `MANAGE_BLUEPRINT` | explicit action endpoint, idempotent; 409 if already confirmed |

## Security

RBAC: four new permissions in `app/models/rbac.py` — `READ_BUSINESS_DISCOVERY`, `MANAGE_BUSINESS_DISCOVERY`, `READ_BLUEPRINT`, `MANAGE_BLUEPRINT`. `MANAGE_BLUEPRINT` is the exact name mandated by `KLAROS_ARCHITECTURE_RECONCILIATION.md` #2, granted to OWNER/ADMIN/MANAGER (matching the doc's explicit guidance to mirror `MANAGE_MEMORY`'s grant pattern). The other three are this implementation's own read/manage split (mirrors every other domain in `rbac.py`): STAFF and READ_ONLY get read-only Discovery/Blueprint access; STAFF additionally gets `MANAGE_BUSINESS_DISCOVERY` (staff commonly run the discovery interview); TECHNICIAN/ACCOUNTANT get neither (verified by a test asserting a TECHNICIAN gets 403 on `POST /business-blueprint/activate`).

Tenant isolation: every service method takes an explicit `tenant_id` and filters every query by it (this codebase's existing unenforced-by-RLS convention). Verified by both service-level tests (`get_by_id`/`confirm_claim`/`get_session` raise not-found for a foreign tenant_id) and API-level tests (cross-tenant reads/confirms return 404, not another tenant's data).

RLS: audit-mode (`ENABLE ROW LEVEL SECURITY` + `USING (true)`) on all 5 new tables, verified against real Postgres — `relrowsecurity=true`, `relforcerowsecurity=false`, exactly 1 policy per table, and a context-less session still sees all rows (proving today's audit mode is genuinely a no-op, not accidental enforcement).

Auditability: `blueprint.confirm_claim`, `blueprint.reject_claim`, and `blueprint.new_version` actions each write an `AuditLog` row (reusing the existing `AuditLog` model/pattern from `CompanyMemoryService.create_memory`, not a new audit mechanism). A confirmed claim with a non-null value also mirrors into `CompanyMemory` (`MemoryType.COMPANY_CONTEXT`, `MemorySource.OWNER_EXPLICIT`, `source_entity_type="blueprint_claim"`) via the existing `CompanyMemoryService.create_memory` — never a second, parallel memory-write path.

## AI usage

`app/services/discovery_extraction_service.py` (`DiscoveryExtractionService`) reuses the existing `AIProvider` abstraction (`app/services/ai_provider.py::generate_structured`) directly — **not** routed through `AIExecutionService`/`ToolRegistry`, which is a narrower boundary for invoking already-governed business-action tools, not freeform text-in/structured-JSON-out extraction. This exactly mirrors the existing `AIQualificationService` pattern (`app/services/ai_qualification_service.py`). Every call is audited via the existing `record_ai_invocation` (no second audit path). The model's raw JSON output is validated against an explicit Pydantic schema (`DiscoveryExtractionResult`/`ExtractedClaim`) before anything is persisted; claims using vocabulary outside the fixed `ClaimType`/`BlueprintSectionKey`/`ClaimProvenance` enums are dropped, never persisted. No API key is configured in this environment, so `get_ai_provider()` returns `DeterministicAIProvider` (`is_connected=False`) — every extraction in this phase's tests exercises the **deterministic fallback** described in `KLAROS_BUSINESS_DISCOVERY_SPEC.md` §6 (a single `IDENTITY.description` Fact claim plus one fixed follow-up question), never a fabricated AI call. Discovery is never given tool access — it can only write `DiscoverySession`/`DiscoveryTurn`/`BlueprintClaim` rows through `BusinessBlueprintService`, never touch any operational table, call `ToolRegistry`, or reach an external system.

## Blueprint lifecycle (exact transitions)

`DRAFT → ACTIVE`: via `POST /business-blueprint/activate`, requires all 4 `MINIMUM_BAR_SECTIONS` (`IDENTITY`, `INDUSTRY`, `BUSINESS_MODEL`, `REQUIRED_CAPABILITIES`) to be `COMPLETE` (i.e., each has ≥1 CONFIRMED claim); otherwise 409. Sets `confirmed_at`. `ACTIVE → SUPERSEDED`: automatic side effect of editing a section on an ACTIVE blueprint (`PUT .../sections/{key}`) — the edit creates a **new** `BusinessBlueprint` row (`version+1`, `status=ACTIVE`) and flips the old row to `SUPERSEDED`; a `SUPERSEDED` blueprint can never be edited again (409). A `DRAFT` blueprint's sections can be edited in place with no version bump (nothing has been "believed" yet to preserve). There is no `ACTIVE → DRAFT` or any other transition.

## Versioning (exact mechanism)

Full-row, immutable-per-version (not a separate child version table — matches both source specs, which describe whole-row versioning, not a `BlueprintVersion` table). `BusinessBlueprint.version` is an integer starting at 1; `(tenant_id, version)` is uniquely constrained. Editing a section while `ACTIVE` clones every `BlueprintSection` row from the current version into a new `BusinessBlueprint` row (`version+1`), replacing only the edited section's data; the old version's rows are never mutated or deleted, so "what did we believe at time T" is answerable via `GET /business-blueprint/versions/{version}`. Verified by a test asserting the old version's unedited sections retain their pre-edit data after a new version is created, and that only one row is ever `ACTIVE` per tenant (DB-level partial unique index, not just app logic).

## Tests

New files (all under `backend/tests/`):
- `test_business_blueprint_service.py` — 15 tests: creation/idempotency, tenant isolation, propose/confirm/reject (including idempotent-confirm, cannot-confirm-rejected, cannot-reject-confirmed, cross-tenant confirm rejected), minimum-bar activation (blocked before, succeeds after, cannot double-activate), one-ACTIVE-per-tenant, versioning-on-edit (new version created, old version's data preserved, unedited sections carried forward, cannot edit SUPERSEDED).
- `test_business_discovery_service.py` — 9 tests: deterministic-AI-provider sanity check, session start + initial turn + proposed Fact claim, raw-answer-always-persisted, unknown-session error, tenant isolation, question-cap completion, cannot-answer-completed-session, claims-always-PROPOSED-never-auto-confirmed, gap-check-uses-confirmed-claims-only.
- `test_business_discovery_blueprint_api.py` — 9 tests: auth required (both routers), RBAC (TECHNICIAN 403 on activate), full happy-path flow (start session → confirm claims → PUT remaining minimum-bar sections → activate → read active/version), reject-claim endpoint + 409-after-reject, cross-tenant 404 on session read and on claim confirm, 404 on unknown session answer, 409 on premature activation.
- `test_postgres_business_discovery_blueprint_rls_audit_mode.py` — 7 tests (real Postgres only): RLS enabled/audit-mode-only/exactly-1-policy per table (parametrized ×5), audit-mode-is-a-real-no-op, and a full pipeline test (Discovery → claims → confirm → activate) plus a DB-level IntegrityError proof for the one-ACTIVE-per-tenant partial index.
- `test_cross_vertical_blueprint_validation.py` — 4 tests: medical-tourism-shaped business activates correctly (including an `Unknown` compliance claim that never gets silently confirmed), dropshipping-shaped business activates correctly, both use the identical generic schema/section-key set, and a static self-check that `business_blueprint_service.py` contains no vertical-name branch.

Total new tests: **44**, all passing (verified against both SQLite and, for the Postgres-specific file, real PostgreSQL). The existing `test_vertical_extension_no_hardcoding_guard.py` (4 tests) was re-run and still passes unchanged — this phase's code does not trip it.

## PostgreSQL validation

Real instance used: pgserver-provisioned PostgreSQL, database `klaros`, socket `/private/tmp/klaros_pg1` (pre-existing on this host from prior work, at head `0042`; Docker unavailable — `docker --version` → command not found). `alembic upgrade head` succeeded (after fixing a real bug caught only against Postgres: `blueprint_sections.data`'s GIN index needed `jsonb`, not plain `json` — SQLite's `Base.metadata.create_all()` path never surfaces this). Two full downgrade/upgrade cycles were run for determinism; each was clean. RLS audit-mode verified: `relrowsecurity=true`, `relforcerowsecurity=false`, exactly 1 `USING(true)` policy per table, on all 5 new tables. The full pipeline test (start session → propose+confirm 4 minimum-bar claims → activate → verify single ACTIVE row → attempt a second ACTIVE row and get a real DB-level `IntegrityError`) passed against this real instance.

## Regression

- New Phase 2 tests + the existing Phase 1 vertical-hardcoding guard + `test_integration_provider_catalog.py`: 55/55 passed together (SQLite).
- Full backend suite, `DATABASE_URL` pointed at the real Postgres instance (same conditions the documented Phase 1 baseline of 1452 passed / 1 failed / 12 skipped was produced under): **1497 passed, 1 failed, 12 skipped** (611.85s). 1497 = 1452 (baseline) + 45 (this phase's new tests). Skipped count is identical (12) — no new skips introduced. The one failure is the exact same pre-existing test named in the task brief, `tests/test_openai_realtime_voice_service.py::test_voice_stream_route_dispatches_to_realtime_engine_when_configured` — not touched by this phase, not fixed (out of scope), reproduces identically to the documented baseline. This is an exact, verified match: zero regressions.
- A first full run without `DATABASE_URL` set (SQLite) was also done for a fast sanity check: 1410 passed, 99 skipped, 0 failed — the higher skip count there is expected (every `requires_real_postgres`-gated test across the whole suite, not just this phase's, skips on SQLite) and is not the baseline comparison; the Postgres-backed run above is the real apples-to-apples comparison.

## Files changed

New:
- `backend/app/models/business_discovery.py`, `backend/app/models/business_blueprint.py`
- `backend/app/services/business_discovery_service.py`, `backend/app/services/business_blueprint_service.py`, `backend/app/services/discovery_extraction_service.py`
- `backend/app/api/v1/business_discovery.py`, `backend/app/api/v1/business_blueprint.py`, `backend/app/api/tool_deps_business_discovery.py`
- `backend/alembic/versions/0043_business_discovery_blueprint.py`
- `backend/tests/test_business_discovery_service.py`, `backend/tests/test_business_blueprint_service.py`, `backend/tests/test_business_discovery_blueprint_api.py`, `backend/tests/test_postgres_business_discovery_blueprint_rls_audit_mode.py`, `backend/tests/test_cross_vertical_blueprint_validation.py`
- `PHASE_2_IMPLEMENTATION_LOG.md` (this file)

Modified:
- `backend/app/models/rbac.py` (4 new permissions + role grants)
- `backend/app/models/__init__.py` (register 5 new models)
- `backend/app/api/v1/router.py` (register 2 new routers)

No file outside this list was touched. No pre-existing Phase 0/1 model, service, route, or migration was modified.

## Known limitations

- `DiscoverySessionStatus.PROMOTED` is defined (per Domain Model's lifecycle) but no code path sets it in this phase — a session currently ends at `COMPLETED`; "promotion" in this implementation is implicit (claims are written directly against the linked blueprint throughout, not batch-migrated at a separate promotion step). A literal `PROMOTED` transition, if the product wants one as a distinct observable event, is Phase 3+ work.
- `ClaimProvenance` has 3 values (`USER_STATED/AI_INFERRED/SYSTEM_DEFAULT`), not the 4 this task's own prompt lists (no `IMPORTED`) — no source doc defines an import mechanism for Phase 2, and none exists to produce that provenance value; adding the enum value with nothing that ever sets it would be dead code.
- The adaptive follow-up question, in the deterministic-AI-fallback path, is a single fixed question ("what type of business...") rather than the spec's fuller "structured choice" UX (business type / geography / revenue model as separate prompts) — implemented as one combined free-text question for this phase; a real AI provider (not configured in this environment) would produce genuinely adaptive per-gap questions via `DiscoveryExtractionService`.
- `PUT /business-blueprint/sections/{key}` accepts an arbitrary JSON `data` payload with no per-section-key Pydantic schema validation (the Blueprint Spec's "validated at the service layer via Pydantic per section_key" is not implemented per-section — only the fixed section-key *vocabulary* and claim vocabulary are validated). Per-section schema validation is a reasonable Phase 3 addition once real per-vertical section shapes exist to validate against.
- No frontend surface was built — `KLAROS_FINAL_FRONTEND_ARCHITECTURE.md` was not found to explicitly mandate Phase 2 UI, and Phase 1 was backend-only; this matches that precedent. Not verified in exhaustive detail against that doc given time constraints — worth a follow-up check if a Discovery/Blueprint UI is expected soon.
- `GET /business-blueprint/draft` is not a route literally named in the API Architecture doc's table — added because a UI needs some way to see PROPOSED claims before activation, and no other documented route serves that purpose.

## Deferred Phase 3+ work (explicitly NOT implemented)

Recommendation Engine, Agent Runtime (no `Agent`/`AgentVersion`/`AgentExecution`/`AgentToolPermission` model or autonomous agent of any kind), Website Builder, Medical Tourism domain tables (no `Hospital`/`Clinic`/`Doctor`/`Procedure`/`ReferralCommission`), Dropshipping domain tables (no `Product`/`SKU`/`Inventory`/`Supplier`/`Order`/`Fulfillment`), MCP (no server or client), integration-marketplace UI, payment workflows, a new workflow/automation/event-bus engine, arbitrary/production code generation, autonomous business creation. Confirmed by direct review of every file this phase touched (`git diff --name-only`) — none of the above appear anywhere in the diff. The cross-vertical validation test's use of the words "hospital"/"supplier"/"inventory" etc. is test *data* (JSONB values / dict literals) describing what the generic Blueprint can hold, never a table or model.
