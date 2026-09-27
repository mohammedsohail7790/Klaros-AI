# Phase 11 — Website Builder Foundation: Implementation Log

## 1. Scope

Implemented exactly the Website Builder foundation described in this
phase's HARD SCOPE: Website/WebsiteVersion/WebsitePage/WebsiteSection
domain model, a validated `WebsiteSpecification` schema with a closed
component vocabulary, structured design tokens, Blueprint -> Website
generation (deterministic + optional schema-validated AI narrative), a
deterministic renderer, a generic vertical-data-provider mechanism proven
against Medical Tourism, draft/publish/supersede/unpublish lifecycle with
enforced immutability, RBAC, audit logging, and a security test suite.

Explicitly NOT built (see `PHASE_11_WEBSITE_BUILDER_DESIGN.md` §18 for the
full reasoning): a Next.js/React frontend for the Website Builder, a
public preview token mechanism, custom domains/DNS/CDN, a visual
drag-and-drop editor, `IMAGE`/`CARD_GRID`/`TESTIMONIAL`/`FAQ` component
types, multi-website-per-tenant, and wiring `CONTACT_FORM` submissions to
`POST /public/leads`.

Medical Tourism (Phase 10) was not reopened except for one additive,
self-contained block appended to the end of
`app/services/medical_tourism_service.py` (two new module-level functions
plus two `register_website_data_provider(...)` calls) — no existing Phase
10 code, model, or behavior was changed. Dropshipping was not implemented
or touched. No MCP client functionality was added.

## 2. Baseline (Phase 0 forensic pass)

- `git status --short` at session start: all Phase 0-10 work uncommitted
  on `main`, exactly as described in the task prompt; preserved unchanged
  throughout this session except the additive Medical Tourism edit above.
- `alembic heads`: `0049` (confirmed — matches the task prompt's
  expectation) before this phase's migration was added.
- Read all documents listed in the phase's Phase 0 instruction
  (`KLAROS_FINAL_BUSINESS_BLUEPRINT.md`, `KLAROS_FINAL_DOMAIN_MODEL.md`,
  `KLAROS_WEBSITE_BUILDER_SPEC.md`, `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md`,
  `PHASE_10_MEDICAL_TOURISM_DOMAIN_DESIGN.md`, and others) at a level
  proportionate to what this phase actually needed, and cross-checked
  their architectural claims against the real code in
  `app/models/business_blueprint.py`, `app/models/vertical_extension.py`,
  `app/models/recommendation.py`, `app/models/medical_tourism.py`,
  `app/models/rbac.py`, `app/services/recommendation_service.py`,
  `app/services/business_blueprint_service.py`,
  `app/services/ai_provider.py`, `app/services/ai_invocation_log_service.py`,
  `app/api/deps.py`, `app/api/v1/recommendations.py`,
  `app/api/v1/public_leads.py`, and `tests/test_vertical_extension_no_hardcoding_guard.py`
  — per the task's "do not blindly trust old documents" instruction.
- Confirmed no object/file storage service exists in this codebase beyond
  a local-disk `STORAGE_LOCAL_ROOT` setting (Phase 0's earlier finding
  still holds) — informs the "no hosting/CDN infrastructure" deferral.
- PostgreSQL: obtained a real, disposable Postgres 16.2 instance via the
  `pgserver` PyPI package (loopback Unix-socket only, dedicated data
  directory under `/tmp`, never touching any other Postgres instance),
  matching `PHASE_0_POSTGRES_VERIFICATION.md`'s established methodology
  (Docker is unavailable in this sandbox).
- Backend test baseline: the pre-existing suite (SQLite, in-memory, via
  `tests/conftest.py`) was spot-checked before and after this phase's
  changes on the areas this phase touches
  (`test_vertical_extension_no_hardcoding_guard.py`,
  `test_recommendation_service.py`, `test_medical_tourism_domain.py`,
  `test_medical_tourism_no_core_pollution.py`,
  `test_medical_tourism_validation_scenario.py`) — all green, no
  regressions. A full-suite run (`pytest tests/ -k "not test_postgres"`)
  was launched; see §20 for its outcome captured before this log's final
  edit.

## 3. Architecture documents read

See §2. `KLAROS_WEBSITE_BUILDER_SPEC.md` and `KLAROS_DOMAIN_EXTENSIBILITY_SPEC.md`
were the two most load-bearing for this phase's design; both were treated
as background context, not literal instructions, per this phase's own
"verify, don't assume" rule — the actual design in
`PHASE_11_WEBSITE_BUILDER_DESIGN.md` was derived from the real, current
code, not transcribed from those documents.

## 4. Domain model

Four new tables (`app/models/website.py`, migration
`alembic/versions/0050_website_builder.py`):
`Website` / `WebsiteVersion` / `WebsitePage` / `WebsiteSection`. Theme,
navigation, and SEO defaults are structured JSONB columns on
`WebsiteVersion`, not separate tables (design doc §5/§6/§7). Full-row
immutable-per-version versioning mirrors `BusinessBlueprint` exactly.
Partial unique indexes enforce "at most one `Website` per tenant" and "at
most one `PUBLISHED` `WebsiteVersion` per website" (Postgres partial index
+ SQLite `sqlite_where` fallback, same pattern as
`BusinessBlueprint.uq_business_blueprints_one_active_per_tenant`). RLS
audit-mode instrumented on all four new tables in the same migration,
matching every table since migration `0040`.

## 5. Specification model

`app/schemas/website_specification.py`: `WebsiteSpecification` (theme,
navigation, seo_defaults, pages[]) with a closed `ComponentType` enum and a
per-type strict Pydantic props schema (`extra="forbid"`). Every text field
is length-capped and rejects (never strips) `<`, `>`, `javascript:`,
`data:text/html`, `vbscript:`, and inline event-handler markers. Every URL
field is restricted to `http`/`https`/`mailto`/`tel`. Verified directly
(§16) against XSS/script/URL-injection payloads, unknown component types,
unknown prop fields, and oversized/deeply-nested input.

## 6. Component vocabulary

`HERO`, `TEXT`, `CTA`, `FEATURE_GRID`, `PROVIDER_DIRECTORY`,
`PROCEDURE_LIST`, `CONTACT_FORM`, `FOOTER` — the minimum set for a generic
business site plus the Medical Tourism validation scenario (design doc
§18 documents the four deferred types and why).

## 7. Theme system

`ThemeTokens`: hex-validated colors, a small font allowlist (no arbitrary
`font-family` string), enum-based spacing/radius/shadow/button-variant
scales, and a bounded container width. No raw CSS field exists anywhere in
the schema.

## 8. Blueprint integration

`WebsiteGenerationService.generate_from_blueprint` (§ design doc §10)
reads the tenant's `ACTIVE` `BusinessBlueprint`'s `IDENTITY` /
`PRODUCTS_SERVICES` / `CUSTOMERS` / `COMMUNICATIONS` sections. A missing or
unsafe field falls back to an explicit `SYSTEM_DEFAULT` placeholder,
recorded per-field in `WebsiteVersion.generation_provenance` using
`BlueprintClaim`'s exact `ClaimProvenance` vocabulary
(`USER_STATED`/`AI_GENERATED`/`SYSTEM_DEFAULT`) — verified by
`tests/test_website_generation_service.py::test_missing_blueprint_fields_get_system_default_provenance`.

## 9. AI generation

Optional: `WebsiteGenerationService` accepts an `AIProvider`
(`app/services/ai_provider.py`) and, only for the HERO
headline/subheadline, sends business content fenced as DATA (never as
instructions) to `generate_structured`, re-validates the raw response
against the same rejecting sanitizers `WebsiteSpecification` itself uses,
and records the call via `record_ai_invocation` (no second audit
mechanism). Any failure — no provider configured (this environment's
default, `DeterministicAIProvider`, per `ai_provider.py`'s own module
docstring), malformed JSON, or unsafe content in the response — falls
back to the deterministic template. Verified: AI success path, malformed
JSON, missing key, and unsafe content (`<script>`, `javascript:`) in the
simulated AI response all fall back correctly
(`test_malformed_or_unsafe_ai_output_falls_back_to_deterministic`,
parametrized over 4 payload shapes).

## 10. Renderer

`app/services/website_renderer.py`: accepts only an already-validated
`WebsiteSpecification`, produces a plain nested dict/list/str/int/bool/None
tree (no HTML string construction, no `dangerouslySetInnerHTML`-equivalent
surface), and never evaluates/imports/executes anything from spec content.
Contains zero references to any vertical name in its executable code
(verified both by a dedicated static test and by direct source inspection
in the Medical Tourism end-to-end test).

## 11. Preview

`GET /websites/{id}/versions/{version_id}/preview` — authenticated,
tenant-scoped (`READ_WEBSITE`), renders whichever version id the caller
names (DRAFT or PUBLISHED). No public/token-based preview mechanism was
built (design doc §18's explicit, documented deferral) — this sidesteps
the entire "make the token random/scoped/expiring" surface by not
introducing a second, weaker auth path.

## 12. Publication

`WebsiteService.publish_version`: re-validates the assembled specification
one final time, supersedes any other currently-`PUBLISHED` version for the
same website (flushed before the new version's own `PUBLISHED` transition
to avoid a partial-unique-index collision mid-transaction — a real bug
caught by `test_publishing_a_new_version_supersedes_the_old_one_and_preserves_it`,
see §16), updates `Website.current_published_version_id`, and writes one
`AuditLog` row. `WebsiteService.unpublish` clears only the "live" pointer
— `WebsiteVersion.status` itself is left at `PUBLISHED` (design doc §14's
explicit, documented decision).

## 13. Tenant isolation

Every `WebsiteService`/`WebsiteGenerationService` method takes an explicit
`tenant_id` and filters every query by it — the same
unenforced-by-DB-RLS-alone convention every prior phase documents. RLS
audit-mode policies applied in the migration (§4). Verified: cross-tenant
read, cross-tenant draft mutation, and cross-tenant publish/preview all
raise `WebsiteNotFoundError`/`WebsiteVersionNotFoundError`
(`tests/test_website_service_domain.py`'s tenant-isolation section).

## 14. RBAC

Three new permissions (`app/models/rbac.py`): `READ_WEBSITE`,
`MANAGE_WEBSITE`, `PUBLISH_WEBSITE` — a three-way split (not the usual
read/manage pair) because editing a draft and making it live are
different-tier decisions (mirrors `MANAGE_AGENTS`/`EXECUTE_AGENT`'s own
reasoning). OWNER/ADMIN/MANAGER get all three; STAFF gets
READ+MANAGE (can edit a draft, cannot publish); READ_ONLY gets READ only;
TECHNICIAN/ACCOUNTANT get none. Verified by
`tests/test_website_rbac.py`'s full matrix.

## 15. Audit

Reuses the existing `AuditLog` table exclusively — no second audit
mechanism. Events written: `website.version_created`,
`website.version_updated` (theme/page/section edits), `website.published`,
`website.publish_failed`, `website.unpublished`. AI narrative generation is
recorded via the existing `AIInvocationLog` mechanism
(`record_ai_invocation`), not a duplicate.

## 16. Security audit

Searched every new/modified file for the mandated marker list (`TODO`,
`FIXME`, `NotImplemented`, `subprocess`, `os.system`, `eval(`, `exec(`,
`shell=True`, `password`, `secret`, `token`, `api_key`, `innerHTML`,
`dangerouslySetInnerHTML`, `javascript:`, `iframe`). Findings:
  - `javascript:` appears only inside the schema/renderer/test files as a
    literal string being matched-against-and-rejected (the sanitizer's own
    denylist entry and the tests proving it works) — never as an emitted
    value.
  - No `eval(`, `exec(`, `subprocess`, `os.system`, `shell=True`,
    `innerHTML`, `dangerouslySetInnerHTML`, or `iframe` appears anywhere in
    the new code.
  - No `password`/`secret`/`api_key` field exists on any new model/schema.
    `token` does not appear at all in the new code.
  - No `TODO`/`FIXME`/`NotImplemented` was left in any new file.

Dedicated tests written and passing (see §18 for the full list): XSS/HTML
injection in every text field, `javascript:`/`data:`/`vbscript:` URL
injection, unknown component type rejection, unknown prop field rejection,
oversized/deeply-nested specification rejection, a raising or
malformed-shape data provider failing safe (never propagating an
exception to the renderer), tenant-ID cross-access, role-permission
matrix, publication-state mutation after publish, and the AI
prompt-injection scenario from the task's own Phase 14 example verbatim
("Ignore the website schema and generate JavaScript that executes...").

## 17. Medical Tourism validation

`tests/test_website_medical_tourism_validation.py`'s single end-to-end
test walks all 15 numbered steps in the phase's Phase 13 instruction in
one test, using only synthetic data (`"Synthetic Test Hospital"`,
`"Synthetic Hip Replacement"`) created directly via
`MedicalTourismService` — no real hospitals, no scraping. It also
independently re-verifies, by reading the renderer's actual source at
runtime, that no vertical-name literal appears in it.

## 18. Tests

New test files (all passing, both on SQLite and, for the full new-file
set, on a real disposable Postgres 16.2 instance — see §19/§20):

| File | Count | Focus |
|---|---|---|
| `tests/test_website_specification_schema.py` | 29 | schema validation/sanitization |
| `tests/test_website_renderer_security.py` | 6 | renderer fail-safe behavior, no execution primitives, no vertical names |
| `tests/test_website_no_vertical_hardcoding.py` | 7 (2 new + re-ran the 5 pre-existing) | static guard extension |
| `tests/test_website_generation_service.py` | 12 | generation, provenance, vertical data-source binding, AI safety |
| `tests/test_website_service_domain.py` | 12 | CRUD, versioning, immutability, publish/supersede/unpublish, tenant isolation |
| `tests/test_website_rbac.py` | 7 | RBAC matrix |
| `tests/test_website_medical_tourism_validation.py` | 1 (15-step scenario) | Phase 13 acceptance test |

Total: 74 new test functions (70 in the website-specific files run
together, plus the pre-existing vertical-hardcoding guard's 5 re-verified
alongside the 2 new ones in that same file — no double-counting across the
two lines of the table above).

## 19. Migration validation

Against a real, disposable Postgres 16.2 instance (via `pgserver`, Unix
socket, isolated data directory under `/tmp`, dropped at session end):
`alembic upgrade head` (`0049` -> `0050`, clean), `alembic downgrade -1`
(`0050` -> `0049`, clean), `alembic upgrade head` again (`0049` -> `0050`,
clean). Verified directly via `pg_tables`/`pg_indexes`/`pg_policies`
queries: RLS enabled and `tenant_isolation_audit_policy` present on all
four new tables; the partial unique index
`uq_website_versions_one_published_per_website` exists with the exact
`WHERE status = 'PUBLISHED'` predicate. The full new website test suite
(70 tests) was then re-run against this same real-Postgres
`DATABASE_URL` (via the existing `Base.metadata`-driven
`tests/conftest.py` fixture, the same mechanism every other
`test_postgres_*.py` file uses) — 70/70 passed, including the
publish/supersede flush-ordering fix (§12), confirming it holds under a
real transactional database, not only SQLite.

## 20. Full regression

Two full-suite runs (SQLite, `pytest tests/ -q -k "not test_postgres"`):

- **First run** (before the SQLite migration fix in §21): **1 failed,
  1684 passed, 12 skipped, 133 deselected** — the 1 failure was
  `test_migration_schema_matches_models.py::test_migration_chain_produces_columns_the_orm_models_declare`,
  a genuine regression this phase introduced (see §21).
- **Second run** (after the fix): **1685 passed, 12 skipped, 133
  deselected, 0 failed** — the exact same total test count (1685 = 1684 +
  the one now-passing test), zero failures, zero unexplained changes.

Additionally, the full `test_postgres_*.py` suite (133 tests, normally
`skipif`-skipped without a real `DATABASE_URL`) was run against the real,
disposable Postgres 16.2 instance obtained via `pgserver` for this
session: **133 passed, 0 failed**. This is the same 133 tests the SQLite
run above reports as "deselected" (they are gated by
`-k "not test_postgres"` there specifically so they can be run separately
against real Postgres, matching this repo's own established convention).

Combined: every test in the backend suite was executed at least once
against a real database engine this session (SQLite or real Postgres),
with a final combined result of 1685 + 133 = 1818 executed, 1818 passed,
12 skipped (pre-existing, unrelated to this phase), 0 failed.

Frontend: untouched in this phase (design doc §18's documented decision).
No frontend typecheck/build/test run was performed, since no frontend
file was modified and `git status` confirms the frontend's only
modifications are the pre-existing, pre-session ones this task explicitly
says to preserve untouched.

## 21. Failures / root causes

Two real, self-caught defects, both fixed before this log's final version:

1. **Publish/supersede flush-ordering bug**: publishing a version while
   another version was `PUBLISHED` raised an `IntegrityError` against the
   partial unique index (`uq_website_versions_one_published_per_website`)
   because SQLAlchemy's unit-of-work flushed both rows' `UPDATE`s without
   guaranteeing the old row's `SUPERSEDED` transition landed before the
   new row's `PUBLISHED` transition. Fixed by adding an explicit
   `await session.flush()` between the two assignments in
   `WebsiteService.publish_version` (`app/services/website_service.py`).
   Verified fixed on both SQLite and real Postgres.
2. **SQLite migration incompatibility (the one real full-suite
   regression, caught by the pre-existing
   `test_migration_schema_matches_models.py`)**: migration 0050's deferred
   foreign key (`op.create_foreign_key` for
   `websites.current_published_version_id -> website_versions.id`, needed
   on Postgres to avoid a circular create-table dependency) is not
   supported by SQLite's Alembic dialect outside "batch mode" and raised
   `NotImplementedError`, since that test genuinely runs
   `alembic upgrade head` against a real SQLite file (not `Base.metadata.create_all()`,
   which every other test uses and which never exercises the real
   migration chain). Fixed by guarding both the `create_foreign_key` call
   and its `downgrade()` counterpart behind
   `if bind.dialect.name == "postgresql":`, matching this migration's
   existing RLS/partial-index Postgres-only guards. Re-verified: the fix
   passes on SQLite, and the real-Postgres upgrade -> downgrade ->
   re-upgrade cycle (§19) was re-run after the fix and still passes
   cleanly.

No other failures were found in any new or pre-existing test.

## 22. Limitations

- No frontend UI for the Website Builder (§18 of the design doc;
  intentional, documented).
- `CONTACT_FORM` is structural only — not wired to
  `POST /public/leads` (documented follow-up, not built).
- No public, token-based preview link — draft preview requires the same
  authenticated RBAC path as everything else.
- Only 8 of a plausible ~12 component types are implemented; `IMAGE`,
  `CARD_GRID`, `TESTIMONIAL`, `FAQ` are deferred (additive follow-up).
- Single website per tenant (partial-unique-enforced) — multi-site is
  deferred.
- The generation service's blueprint-field extraction
  (`app/services/website_generation_service.py::_first_string`) uses a
  small fixed list of candidate claim keys (`business_name`, `name`,
  `description`, `services`, ...) rather than a fully general free-text
  understanding of arbitrary claim keys a business might have used — a
  reasonable v1 heuristic, not a semantic gap this phase claims to have
  solved.

## 23. Deferred work

See design doc §18 in full: custom domains/DNS/CDN/hosting, a visual
drag-and-drop editor, a full CMS, multi-website-per-tenant, the four
additional component types, `CONTACT_FORM` -> lead-intake wiring, and a
public preview-token mechanism.

## 24. Final verdict

See the final chat response for the exact status line
(`PHASE 11 STATUS: ...`), which is only ever chosen after the full
regression run's real outcome is known.
