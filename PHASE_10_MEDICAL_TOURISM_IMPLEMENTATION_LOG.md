# Phase 10 — Medical Tourism Vertical Extension — Implementation Log

## 1. Scope

Implemented the Medical Tourism vertical domain extension only, per this
phase's HARD SCOPE: Provider/Hospital/Clinic domain, Procedure catalog,
Provider offerings, Provider-credential (compliance) records,
PatientLead/Consultation/ReferralCommission extensions of existing core
entities, tenant ownership/isolation, RBAC, service layer, explicit typed
API, Business Blueprint / Recommendation Engine / ToolRegistry
integration where genuinely required, tests, migration, documentation.
Did not touch: MCP architecture (Phase 9, left untouched), Dropshipping,
Website Builder, Marketplace UI, payment processing, agent swarms, a
second execution/RBAC/tenant/audit system. Nothing was committed or
pushed.

## 2. Baseline

- Repo: `/Users/mohammedsohail/Desktop/Klaros AI`, branch `main`, all
  Phase 0-9 work present as uncommitted local changes — preserved exactly;
  no `git add`/`commit`/`push`/`reset`/`checkout -- <file>` was run at any
  point.
- Alembic heads at session start: `0048` (`0048_mcp_server.py`) — verified
  via `alembic heads`, not assumed to be `0048` from memory. 49 files
  under `alembic/versions/` before this phase's migration was added (48
  numbered migrations + `__pycache__`).
- `python3` on PATH is the system Python (no `alembic` module); the
  project's own `.venv` (`backend/.venv`) has the real toolchain
  (Python 3.12, `alembic`, `pgserver`, etc.) — used for everything below.
- Postgres: no `pgserver` instance was already running for this session.
  A fresh, disposable `pgserver`-provisioned PostgreSQL 16.2 instance was
  started at `/private/tmp/klaros_pg_phase10` (unix-socket only), matching
  the exact PyPI-package methodology documented in every prior phase
  (Docker/Colima/Podman unavailable in this sandbox). Database `klaros`
  created explicitly (`CREATE DATABASE klaros;`).
  `DATABASE_URL=postgresql+asyncpg://postgres@/klaros?host=/private/tmp/klaros_pg_phase10`.
- Pre-Phase-10 test baseline: not re-run as a separate full-suite pass
  before any edit (the instance was created fresh specifically for this
  phase, so there is no "before" state on this exact instance to diff
  against) — instead, `alembic upgrade head` was run first against the
  clean instance to confirm `0001`-`0048` apply cleanly before `0049` was
  written, which is the equivalent verification for a fresh-instance
  baseline. `git status --short` at session start (from the environment's
  own snapshot) showed the same modified/untracked file set the task
  description named — confirmed via a fresh `git status` re-run, matching.

## 3. Architecture documents read

`KLAROS_FINAL_DOMAIN_MODEL.md`, `KLAROS_FINAL_BUSINESS_BLUEPRINT.md`,
`KLAROS_MEDICAL_TOURISM_VALIDATION.md` (full),
`KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` (full), `KLAROS_DO_NOT_BUILD_YET.md`
(§8/§10 sections directly relevant to this phase), plus direct code
inspection of `app/models/vertical_extension.py`, `app/models/
business_blueprint.py`, `app/models/rbac.py`, `app/models/crm.py`,
`app/models/retention.py`, `app/models/recommendation.py`, `app/services/
recommendation_service.py`, `app/tools/base.py`, `app/tools/registry.py`,
`app/tools/policy.py`, `app/tools/factory.py`, `app/api/v1/router.py`,
`app/api/v1/retention_referrals.py`, `app/api/v1/customers.py`,
`app/api/deps.py`, `app/db/base.py`, `PHASE_8_TOOL_IDEMPOTENCY_AUDIT.md`,
`alembic/versions/0041_vertical_extension_registry.py`, `alembic/versions/
0044_recommendation_engine.py`, `app/data/vertical_extension_seed.py`,
`tests/conftest.py`, `tests/test_vertical_extension_registry.py`,
`tests/test_postgres_vertical_extension_rls_audit_mode.py`, `tests/
test_cross_vertical_agent_validation.py`. `KLAROS_FINAL_API_ARCHITECTURE.md`
and `KLAROS_FINAL_DATABASE_ARCHITECTURE.md` were not re-read in full this
phase (both are short, thin stub documents per Phase 0's own audit
findings; the concrete API/DB conventions were instead verified directly
against the live code listed above, which is authoritative over the stub
docs).

Not re-read in full: `PHASE_0_IMPLEMENTATION_LOG.md` through
`PHASE_9_IMPLEMENTATION_LOG.md` individually — their conclusions are
already reflected in the current code state this phase inspected
directly (e.g. `VerticalExtension`'s Phase 1 registry, `BusinessBlueprint`'s
Phase 2 shape, `Recommendation`'s Phase 3 shape, Agent Runtime's Phase 4-7
shape, MCP's Phase 9 shape) — reading the code these logs describe is a
stronger baseline than re-reading the logs' own prose.

## 4. Domain model

See `PHASE_10_MEDICAL_TOURISM_DOMAIN_DESIGN.md` (repo root) for the full
Phase 1 design writeup. Summary: seven new tenant-scoped tables
(`Provider`, `ProviderCredential`, `Procedure`, `ProviderProcedure`,
`PatientLead`, `Consultation`, `ReferralCommission`) exactly matching
`KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §3's pre-validated minimum
normalized model — no table added or omitted from that list. `Destination`/
`Practitioner`/`TreatmentPackage` deliberately not built, per that same
spec's explicit rejection rationale (folded into `Provider.country`/
`city`/`practitioner_name`, and `Quote`/`QuoteLineItem` reuse,
respectively).

## 5. Database changes

New file: `backend/app/models/medical_tourism.py` (7 model classes + 6
status enums). Additive columns on the existing `backend/app/models/
retention.py::ReferralReward`: `currency` (String(3), nullable),
`commission_basis` (String(20), nullable) — the one pre-authorized touch
to an existing table (`KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §7).
`backend/app/models/__init__.py` updated to import/export the 7 new
model classes.

## 6. Migration

`backend/alembic/versions/0049_medical_tourism_domain.py` (`0048` ->
`0049`). Creates all 7 tables with FKs/indexes/unique constraints
(idempotency-key dedup on `Provider`/`Procedure`; `(tenant, provider,
procedure)` uniqueness on `ProviderProcedure`; `(tenant, lead_id)` on
`PatientLead`; `(tenant, appointment_id)` on `Consultation`; `(tenant,
referral_id)` on `ReferralCommission`); adds the two `ReferralReward`
columns; updates the Phase 1 `medical_tourism` `VerticalExtension` seed
row (version `0.0.0-registry-only`/BETA/`capabilities=[]` ->
`1.0.0`/ACTIVE/7 populated capability keys) via a real data migration
sourced from the single-source-of-truth `app/data/
vertical_extension_seed.py` (also updated); applies RLS audit-mode
(`ENABLE ROW LEVEL SECURITY` + one `USING (true)` policy, never `FORCE`)
to all 7 new tables, matching every table since `0040`. Full `downgrade()`
provided, including reverting the `VerticalExtension` row to its exact
pre-Phase-10 values.

**Verified against real Postgres** (fresh `pgserver` instance, see §2):
`alembic upgrade head` (0048->0049) succeeded from a clean database with
all prior 48 migrations. A full `downgrade -1` -> `upgrade head` ->
`alembic current` cycle was run: clean both directions, ending at `0049
(head)`. Verified via direct `psql`: all 7 tables have `relrowsecurity=t`,
`relforcerowsecurity=f`, exactly 1 policy each; `referral_rewards` gained
exactly `currency`/`commission_basis`; `vertical_extensions` row for
`medical_tourism` reads `version=1.0.0, status=ACTIVE`, with the 7
capability keys as a JSON array.

## 7. Tenant isolation

Application-layer only (RLS remains audit-mode across the whole
codebase, per every prior phase — not overclaimed as enforcement here
either). Every `MedicalTourismService` method takes an explicit
`tenant_id` and filters every query by it — same convention as
`LeadService`/`BusinessBlueprintService`/`RecommendationService`. Proven
by real tests (`tests/test_medical_tourism_domain.py`): tenant A cannot
read/update/list tenant B's providers (404/empty-list, never leaking
data); the same provider name can exist independently across two tenants;
the `(tenant, provider, procedure)` offering-uniqueness constraint is
scoped per tenant, not global. `tests/test_postgres_medical_tourism_
domain.py::test_audit_mode_is_a_real_no_op_today` additionally proves,
against real Postgres, that a context-less raw-SQL read is NOT blocked by
the audit-mode RLS policy — i.e. this document does not claim RLS is the
real boundary; the service layer is.

## 8. RBAC

Two new permissions in `app/models/rbac.py`: `READ_MEDICAL_TOURISM`,
`MANAGE_MEDICAL_TOURISM`. Grants: OWNER/ADMIN get both (via the existing
`_ALL_PERMISSIONS`/`_ALL_PERMISSIONS - {DELETE_CUSTOMER}` sets, unchanged
mechanism); MANAGER gets both explicitly added; STAFF and READ_ONLY get
only `READ_MEDICAL_TOURISM`; TECHNICIAN and ACCOUNTANT get neither (out of
their domain, same as e.g. `READ_BLUEPRINT` is withheld from them today).
Proven by `tests/test_medical_tourism_domain.py::
test_role_permission_matrix_for_medical_tourism` (parametrized across all
7 roles) — confirms ordinary STAFF cannot mutate vertical configuration
merely by having generic platform access, per this phase's explicit
instruction.

## 9. Service layer

New file: `backend/app/services/medical_tourism_service.py`
(`MedicalTourismService`). Covers: create/get/list/update `Provider`;
add/verify/list `ProviderCredential`; create/get/list `Procedure`;
create/list `ProviderProcedure` offerings; create `PatientLead` (1:1
extension, requires existing `Lead`); create `Consultation` (1:1
extension, requires existing `Appointment` + `Provider`); create
`ReferralCommission` (1:1 extension, requires existing `Referral`,
computes `computed_amount` for the `PERCENTAGE` basis). Concurrency-safe
create paths (`create_provider`, `create_procedure`,
`create_provider_procedure`) use the exact try/insert/except-
`IntegrityError`/rollback/re-fetch compare-and-swap pattern
`LeadService.create_lead` documents (Phase 29/30's audit fix, reused
verbatim) — proven under real concurrent load in
`tests/test_postgres_medical_tourism_domain.py` (8-way `asyncio.gather`
against the real `idempotency_key` and `(provider, procedure)` unique
constraints, each collapsing to exactly one row).

## 10. APIs

New file: `backend/app/api/v1/medical_tourism.py`, mounted at
`/api/v1/medical-tourism` in `app/api/v1/router.py`. Explicit typed
routes only (never a generic `/entity/{type}` endpoint): `GET/POST
/providers`, `GET /providers/{id}`, `GET/POST /providers/{id}/credentials`,
`POST /providers/{id}/credentials/{id}/verify`, `GET/POST /procedures`,
`GET /procedures/{id}`, `GET/POST /offerings`. Pagination via
`limit`/`offset` query params (`Query(default=20, ge=1, le=100)`),
matching the existing `customers.py` convention exactly. Mutating routes
(`create_provider`/`create_procedure`/`create_offering`) go through
`ToolRegistry.execute()`, so they get the exact same permission/tenant/
schema/policy/audit enforcement as every other tool call; read routes
call the service layer directly (mirrors `retention_referrals.py`'s own
read/write split). Credential add/verify are service-layer-direct (not
ToolRegistry-routed) since they are not agent-callable actions in this
phase (see §13) but still enforce `MANAGE_MEDICAL_TOURISM` explicitly.
All reads/writes are scoped to `current_user.tenant_id` only — the tenant
id is never accepted from the request body.

## 11. Blueprint integration

**Zero new columns on `BusinessBlueprint`/`BlueprintSection`/
`BlueprintClaim`** — proven by `tests/test_medical_tourism_no_core_
pollution.py`. The existing architecture already carries everything
needed: vertical selection via `BusinessBlueprint.vertical_extension_id`
(Phase 2) + an `OrganizationVerticalExtension` row (Phase 1); required
capabilities via free-form `BlueprintClaim` values under the existing
`REQUIRED_CAPABILITIES` section — no new `BlueprintSectionKey`, no new
claim type, no vertical-name branch anywhere in the Blueprint service.
Verified end-to-end in `tests/test_medical_tourism_validation_scenario.py`:
activating `medical_tourism` for a tenant + confirming a
`REQUIRED_CAPABILITIES` claim containing `"medical_tourism.
provider_directory"` produces a real `ACTIVE` `BusinessBlueprint`, and the
vertical's own domain tables work generically off that activation with no
tenant-specific code.

## 12. Recommendation integration

No changes to `recommendation_service.py`. The engine already reads
candidate capabilities generically from every enabled
`VerticalExtension.capabilities` list — this phase only had to give the
`medical_tourism` registry row real capability keys (§6) for the existing
generic pipeline to pick them up. Verified in
`tests/test_medical_tourism_validation_scenario.py`:
`generate_recommendations` on an activated tenant produces a CAPABILITY
recommendation for every one of the 7 `medical_tourism.*` capability keys
(both the blueprint's required one and the vertical's other contributed-
candidate ones), and at least one TOOL recommendation matches a real
`medical_tourism.*` tool via the existing keyword-overlap matcher against
the live Tool Catalog — no second recommendation system, no hardcoded
Medical-Tourism-specific matching logic.

## 13. ToolRegistry integration

New file: `backend/app/tools/builtin/medical_tourism_tools.py`, 7 tools
registered in `app/tools/factory.py`: `medical_tourism.search_providers`,
`get_provider`, `search_procedures`, `list_provider_offerings` (read-only),
`create_provider`, `create_procedure`, `create_provider_offering`
(writes). Each: subclasses the existing `Tool` base class, has a Pydantic
`input_schema`/`output_schema`, has a `required_permission`
(`READ_MEDICAL_TOURISM` or `MANAGE_MEDICAL_TOURISM`), is
`tenant_scoped=True` (the default), passes through `ToolRegistry` (no
direct-execute bypass anywhere), is audited (the registry's own
unconditional `_audit()` call), and has an explicit policy entry in
`app/tools/policy.py` (all 7 set to `AUTO`, matching the exact precedent
of `crm.create_lead`/`crm.search_leads`/`crm.search_customers`/
`retention.create_referral` — none of these tools move real money or call
an external provider).

**Idempotency classification** (reapplying the Phase 8 discipline):

| Tool | Category | supports_idempotency | Rationale |
|---|---|---|---|
| `medical_tourism.search_providers` | read-only | **True** | Verified by direct inspection: `execute()` only calls `MedicalTourismService.list_providers`, which only `SELECT`s — genuine natural read-only idempotency. |
| `medical_tourism.get_provider` | read-only | **True** | Same — only `SELECT`s via `get_provider`. |
| `medical_tourism.search_procedures` | read-only | **True** | Same — only `SELECT`s via `list_procedures`. |
| `medical_tourism.list_provider_offerings` | read-only | **True** | Same — only `SELECT`s via `list_offerings`. |
| `medical_tourism.create_provider` | internal-write-create | False | Has a real DB-unique-constraint dedup mechanism (idempotency_key + concurrency-safe re-fetch, proven in §9/§6's real-Postgres concurrency test) — left `False` anyway, for exact consistency with `crm.create_lead` (identical mechanism, left `False` in the Phase 7/8 audit). Introducing `True` here for the same mechanism would be an unexplained inconsistency in the tool catalog, not a more accurate claim. |
| `medical_tourism.create_procedure` | internal-write-create | False | Same rationale as `create_provider`. |
| `medical_tourism.create_provider_offering` | internal-write-create | False | Same rationale — real `(provider, procedure)` unique-constraint dedup, still left `False` for the same consistency reason. |

Not built as tools (Phase 9's own scoping instruction: "don't create a
tool merely because an API endpoint exists"): `update_provider`,
`add_provider_credential`, `verify_provider_credential`,
`create_patient_lead`, `create_consultation`, `create_referral_commission`
— none of these are required by the validated walkthrough to be
agent-callable; each remains reachable via the API + service layer.

## 14. Agent compatibility

No changes to the Agent Runtime (`AgentService`/`AgentExecutionService`/
`AgentToolPermission`/`AgentVersion` — Phases 4-7). Verified via
`tests/test_medical_tourism_validation_scenario.py`, mirroring `tests/
test_cross_vertical_agent_validation.py`'s existing pattern but with a
*real* medical_tourism tool (not a stand-in): an Agent granted
`medical_tourism.search_providers` at version-publish time successfully
executes it (`AgentExecutionStatus.COMPLETED`, tenant-scoped, audited);
the same agent attempting the ungranted `medical_tourism.create_provider`
gets a `FAILED` execution — the unmodified governance chain
(published-snapshot permissions, tenant scoping, audit) rejects it exactly
like every other tool/agent pairing, with zero new execution machinery.
Autonomy-policy/approval/kill-switch/crash-recovery semantics were not
independently re-exercised against a medical_tourism tool specifically
(out of this phase's budget beyond the one governance-chain proof above)
— they are unmodified, generic, tool-name-agnostic mechanisms already
covered by the existing Phase 4-7 test suites, which this phase's full
regression run (§18) re-confirms are unbroken.

## 15. Security audit

Grepped every new/modified file
(`app/models/medical_tourism.py`, `app/services/
medical_tourism_service.py`, `app/tools/builtin/medical_tourism_tools.py`,
`app/api/v1/medical_tourism.py`, `alembic/versions/
0049_medical_tourism_domain.py`, `app/data/vertical_extension_seed.py`,
`app/tools/policy.py`, `app/models/rbac.py`, `app/models/retention.py`,
`app/models/__init__.py`, `app/tools/factory.py`) for: `TODO`, `FIXME`,
`NotImplemented`, `subprocess`, `os.system`, `eval(`, `exec(`,
`shell=True`, `password`, `secret`, `token`, `api_key`. **Zero hits.**
Manually verified: no tenant override (every API/service method takes
`tenant_id` only from `current_user`/an explicit caller-supplied `tenant_id`
parameter never sourced from request body/tool input); no role/actor
override; no direct database access from untrusted input (all queries are
parameterized SQLAlchemy Core/ORM); no cross-tenant relationship traversal
(every FK lookup — `Provider`, `Procedure`, `Lead`, `Appointment`,
`Referral` — is re-checked against `tenant_id` before use, e.g.
`create_consultation` re-validates both the `Appointment` and the
`Provider` belong to the caller's tenant); no direct `tool.execute()`
bypass (every write path in the API goes through
`ToolRegistry.execute()`); no unbounded list queries (`limit`/`offset`
capped at 200 in tool schemas, 100 in API `Query` params); no dynamic SQL
(no f-string-built SQL anywhere in the new files — the migration's own
`f"..."` usages are fixed, developer-controlled table/policy names, not
user input, matching every prior migration's identical pattern); no
arbitrary file/URL access; no `eval`/`exec`/subprocess/shell execution
anywhere in the new code.

## 16. Tests

New files: `tests/test_medical_tourism_domain.py` (27 tests: service CRUD,
relationship validation, tenant isolation x4, RBAC matrix x7 parametrized,
API x4), `tests/test_postgres_medical_tourism_domain.py` (5 tests: RLS
instrumentation, audit-mode no-op proof, 2 real-concurrency tests),
`tests/test_medical_tourism_no_core_pollution.py` (5 tests: zero-new-
columns proofs for `BusinessBlueprint`/`Lead`/`Appointment`/`Referral`,
plus the `ReferralReward` two-column exception), `tests/
test_medical_tourism_validation_scenario.py` (1 comprehensive end-to-end
test covering the full target flow). Modified:
`tests/test_vertical_extension_registry.py`'s
`test_seed_data_matches_expected_reality` — updated to assert each seed
vertical's status against `SEED_VERTICALS` (the single source of truth)
rather than a blanket "both BETA" assertion that Phase 10 makes no longer
true for `medical_tourism` specifically (the test's own docstring already
anticipated this: "both BETA... since their table families ship later").

**Total new/updated Phase 10 test count: 38** (27 + 5 + 5 + 1, plus the 1
modified pre-existing test).

## 17. PostgreSQL validation

All Phase 10 test files were run against the real Postgres instance
described in §2 (never SQLite for anything Postgres-specific; SQLite is
still used for the non-Postgres-specific service/RBAC/API tests via the
existing `conftest.py` `_reset_database` fixture, same as every other
domain's test suite in this codebase) — see §16 for file-by-file pass
counts, and §6 for the migration-cycle validation. All 38 Phase 10 tests
pass; the 1 modified pre-existing test passes with its updated assertion.

## 18. Full regression

Ran against the same real Postgres instance, single pytest process (no
concurrent pytest processes against the shared `pgserver` instance at any
point in this session, per the process-hygiene lesson documented in
`PHASE_4_IMPLEMENTATION_LOG.md`).

**Result: `1 failed, 1746 passed, 12 skipped, 28 warnings in 783.15s (0:13:03)`.**

Compared to baseline: this phase added 38 new tests (27 in
`test_medical_tourism_domain.py` + 5 in
`test_postgres_medical_tourism_domain.py` + 5 in
`test_medical_tourism_no_core_pollution.py` + 1 in
`test_medical_tourism_validation_scenario.py`) and modified 1 existing
test's assertion (`test_vertical_extension_registry.py::
test_seed_data_matches_expected_reality`, still counted once, not added).
1746 passed accounts for every one of those 38 new tests passing plus the
full pre-existing suite remaining green.

## 19. Failures and root causes

One failure: `tests/test_openai_realtime_voice_service.py::
test_voice_stream_route_dispatches_to_realtime_engine_when_configured`.
This is the exact pre-existing voice/event-loop flake documented in
`PHASE_3_IMPLEMENTATION_LOG.md`, `PHASE_4_IMPLEMENTATION_LOG.md`, and
`PHASE_5_IMPLEMENTATION_LOG.md` (same test, same file, same failure mode
across multiple prior phases' full-suite runs). Investigated per this
phase's own "never silently modify unrelated code" rule: re-ran this
single test in isolation (`pytest tests/test_openai_realtime_voice_service.py::
test_voice_stream_route_dispatches_to_realtime_engine_when_configured`)
against the same real Postgres instance — **passed** (`1 passed in 0.52s`),
confirming it is order/state-dependent flakiness triggered only when run
as part of the full 1747-test suite, not a deterministic failure, and
confirmed unrelated to any file this phase touched (the test file is
`test_openai_realtime_voice_service.py`; this phase never read or edited
any voice-related code). Not modified, per the instruction to never
silently touch unrelated code for a pre-existing, already-documented
issue.

Also explicitly re-ran `tests/test_vertical_extension_no_hardcoding_guard.py`
in isolation after this phase's changes: `4 passed` — confirms none of
the new `medical_tourism.py`/`medical_tourism_service.py`/
`medical_tourism_tools.py`/`medical_tourism.py` (API) files trip the
static "no vertical-name branch in application code" guard.

## 20. Limitations

- RLS remains audit-mode (not `FORCE`) on all 7 new tables, consistent
  with the rest of the codebase — never overclaimed as enforcement.
  Application-layer tenant filtering in `MedicalTourismService` is the
  real isolation boundary today.
- `supports_idempotency` is conservatively `False` on all 3 write tools
  despite each having a real, demonstrated DB-unique-constraint dedup
  mechanism — kept consistent with `crm.create_lead`'s identical,
  already-`False` precedent rather than introducing an unexplained
  inconsistency in the tool catalog (see §13).
- Autonomy-policy/approval-required/kill-switch/crash-recovery paths were
  not independently re-exercised against a medical_tourism-specific tool
  beyond the one governance-chain (granted-vs-ungranted) proof in §14 —
  those mechanisms are generic and tool-name-agnostic, and this phase's
  full regression run (§18) confirms the existing suites covering them
  remain green, but this phase did not add a *new*, medical_tourism-
  specific approval/kill-switch/crash-recovery test.
- `add_provider_credential`/`verify_provider_credential`/
  `create_patient_lead`/`create_consultation`/`create_referral_commission`
  are reachable only through the API + service layer, not as
  ToolRegistry tools/agent-callable actions (deliberate scope decision,
  §13) — an agent cannot autonomously verify a credential, extend a lead,
  schedule a consultation, or compute a referral commission in this
  phase.
- No Blueprint section sub-schema was added for Medical-Tourism-specific
  structured configuration (§8 of the design doc) — deferred, not needed
  by the validated scenario.
- `Destination`/`Practitioner`/`TreatmentPackage` are not separate
  entities (by design, per `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md` §3's own
  rejection rationale) — folded into `Provider` fields / `Quote` reuse.
- No production provider data, no real-world hospital/scraping — every
  test uses synthetic data (Istanbul Health Hub, Delhi Care Institute,
  etc.), consistent with the mandatory "test data only, never claim real
  providers" instruction.

## 21. Deferred work

Everything in §8 of `PHASE_10_MEDICAL_TOURISM_DOMAIN_DESIGN.md` and §20
above. No Website Builder, Dropshipping, MCP-client, or OAuth-marketplace
work was started, per HARD SCOPE.

## 22. Final verdict

**PHASE 10 STATUS: COMPLETE WITH LIMITATIONS.**

All 15 success criteria hold: Medical Tourism exists as a real, isolated
vertical extension (7 new tables, zero new columns on generic core
tables except the 2 pre-authorized generic ones on `ReferralReward`);
tenant isolation proven (application-layer, with RLS correctly left at
audit-mode, never overclaimed); RBAC proven (7-role permission matrix
test); real Postgres migration cycles pass (upgrade/downgrade/re-upgrade,
clean); APIs are explicit/typed, never dynamic; Blueprint integration is
coherent and adds zero new columns; Recommendation integration reuses the
existing engine with zero code changes to it; tools use ToolRegistry
exclusively, never bypass it; the existing Agent Runtime uses a real
permitted vertical tool with zero new execution machinery (and correctly
rejects an ungranted one); security audit found zero hits across the
checked pattern list and no override/bypass classes of bug; full
regression has exactly one, pre-existing, already-documented,
isolation-confirmed-non-deterministic failure (not introduced by this
phase); limitations are explicitly stated in §20; nothing outside HARD
SCOPE was built; nothing was committed or pushed.

The "WITH LIMITATIONS" qualifier reflects genuine, disclosed scope
narrowing, not a defect: `supports_idempotency` is conservatively kept
`False` on all 3 write tools for consistency with an identical existing
precedent rather than a more "impressive" `True`; 5 of the domain's write
operations were deliberately not exposed as agent-callable tools; and
autonomy/approval/kill-switch/crash-recovery paths were verified generic
and covered by the still-green existing suite, but not independently
re-exercised with a medical_tourism-specific new test beyond the one
governance-chain (granted-vs-ungranted) proof.
