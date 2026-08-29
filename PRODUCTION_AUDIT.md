# Klaros AI — Production Audit

Performed as a senior/principal-engineer-level end-to-end audit of the Phase 1–10 system, after
those phases were already built and their own test suites passing. The mandate for this audit was
explicit: do not trust prior "complete" claims — inspect, reproduce, and prove.

This document is the working audit record: architecture as actually found, trust boundaries as
actually enforced, and every finding with evidence. `PRODUCTION_READINESS.md` is the resulting
checklist; the final chat report is the executive summary.

## Methodology

- Read the real code for every trust-boundary-crossing path (auth, tenant scoping, ToolRegistry,
  PolicyService, ApprovalExecutionService, AI provider, file storage, event bus) rather than relying
  on prior phases' documentation of themselves.
- Ran the complete backend test suite, including `tests/test_temporal_workflows.py` — **for the
  first time in this project's history**, since every prior phase's own report excluded it with
  `--ignore=tests/test_temporal_workflows.py` due to an unresolved hang.
- Actually ran `alembic upgrade head` against a brand-new, empty SQLite database file — not the
  project's long-lived `dev.db`, which every phase since 5 patched directly with raw `sqlite3 ALTER
  TABLE` statements, never through Alembic. This was the first real end-to-end migration test this
  project has ever had.
- Grepped systematically for secrets, raw SQL, tenant_id trust violations, and broad exception
  swallowing across the entire backend.
- Traced the AI execution boundary and prompt-construction code by hand for injection/authorization
  bypass potential.
- No Docker, Postgres, or Redis is available in this sandbox (consistent with every prior phase) —
  those verifications are explicitly marked NOT VERIFIED below and in `PRODUCTION_READINESS.md`,
  never assumed to pass.

## Architecture (as actually found, not as documented)

```
Owner (browser) ──JWT──> FastAPI ──> CurrentUser (tenant_id/role from verified token only)
                                        │
                                        ▼
                                  ToolRegistry.execute()
                              permission → tenant → schema
                                        │
                                        ▼
                              PolicyService.resolve(tenant_id, tool)
                        SYSTEM_BLOCKED → tenant override → system default
                                        │
                         ┌──────────────┼───────────────┐
                         ▼              ▼                ▼
                       AUTO      APPROVAL_REQUIRED     BLOCKED
                         │              │                │
                         ▼              ▼                ▼
                  domain Tool   ApprovalRequest row   ToolBlockedError
                         │       + event published
                         ▼              │
                    domain Service      ▼
                         │       (human approves via
                         ▼        /approvals, second
                    Postgres/         actor required)
                    SQLite               │
                         │               ▼
                         ▼      ApprovalExecutionService
                    EventBus         .execute_approved()
                    .publish()    → re-resolves policy →
                         │           ToolRegistry.execute(
                         ▼             skip_approval_gate=True)
                  EventWorker
                (continuous poll,
                 same process in
                 dev, separate
                 Docker service
                 in prod)
                         │
             ┌───────────┼──────────────┐
             ▼           ▼              ▼
        domain        NotificationService     AuditLog
        handlers      .notify()               (every tool call,
        (retention,   → Notification row       every policy change,
         finance,     (dedupe_key unique       every approval decision)
         marketing)    per tenant)
```

**AI execution boundary**: `AIExecutionService.request_tool_execution()` is the *only* method that
exists on that class — it takes a `ToolRequest(tool_name, input)` and hands it to
`ToolRegistry.execute()` with `actor_type=AI`. There is no method, anywhere in the AI-facing surface,
that accepts SQL, a shell command, an HTTP URL, or a Python callable. An AI-authored recommendation
becomes a real action only by going through the exact same permission/tenant/schema/policy/audit
pipeline every human API call goes through — confirmed by reading `app/ai/execution_service.py` in
full (34 lines, no branch, no bypass) and by two explicit `actor_type == ActorType.AI` guards inside
`ApproveAction`/`RejectAction`/`SetPolicy`/`ResetPolicy` that exist *specifically* because
role-based permission alone would otherwise let an AI actor through (Role.MANAGER, which
`AIExecutionService` always assigns, legitimately holds `MANAGE_AUTOMATION_POLICIES` for its human
members).

**Tenant identity**: never accepted from a route parameter, query string, or request body anywhere
in the API layer — verified by grepping every file in `app/api/v1/` for a `tenant_id` parameter
accepted from the client; none exists. Every handler derives `tenant_id` from
`current_user.tenant_id`, itself only ever set from a JWT payload that was cryptographically verified
(`python-jose`, explicit `algorithms=[settings.JWT_ALGORITHM]` pin — no "alg: none" or algorithm-confusion
surface) by `app/api/deps.py::get_current_user`.

## Findings

### P0 — FIXED

**P0-1: No startup guard against the insecure default JWT secret in production.**
- **Evidence**: `app/core/config.py` — `JWT_SECRET: str = "change-me-in-production"`. This string is
  publicly visible in this open-source codebase. `app/main.py` had no check preventing the app from
  starting with this value regardless of `ENV`.
- **Why it matters**: if an operator deploys with `ENV=production` and forgets to set `JWT_SECRET`
  (a single missed environment variable), every JWT the app issues — and every JWT an attacker forges
  using the well-known default secret — is accepted. This is a complete authentication bypass: an
  attacker can mint a token for any `tenant_id`/`user_id`/`role`, including `OWNER`, for any tenant,
  without ever authenticating.
- **Reproduction**: set `ENV=production`, leave `JWT_SECRET` unset, start the app, forge a token with
  `jose.jwt.encode({"sub": <any-uuid>, "tenant_id": <any-uuid>, "role": "OWNER", "type": "access",
  "exp": <future>}, "change-me-in-production", algorithm="HS256")`, use it against any endpoint.
- **Fix**: `app/main.py` now calls `_assert_production_secrets_are_real()` in the lifespan startup,
  which raises `RuntimeError` and refuses to boot if `ENV == "production"` and `JWT_SECRET` is still
  the default. Development/test environments are unaffected (`ENV=development` is the `.env.example`
  default).
- **Test**: `tests/test_production_secret_guard.py` (3 tests: refuses in production with default,
  starts in production with a real secret, unaffected in development) — all pass.
- **Status**: FIXED, tested.

### P1 — FIXED

**P1-1: `InvoiceOverdueWorkflow` did not "hang" — it called a nonexistent API and Temporal silently
retried the failing workflow task forever, which looked identical to a hang from outside.**
- **Evidence**: `app/workflows/definitions.py` called `await workflow.sleep(...)`. The installed
  `temporalio==1.8.0` SDK has no `workflow.sleep` attribute — `AttributeError: module
  'temporalio.workflow' has no attribute 'sleep'`, confirmed by running the workflow directly and
  capturing the worker's own failure log. Temporal's default behavior on a workflow task failure is
  to retry the task indefinitely (by design, for genuinely-transient failures) — from the calling
  test's perspective, this manifests as `execute_workflow()` never returning, which is exactly what
  every prior phase's report called "hangs, not re-investigated."
- **Why it matters**: this is the single workflow in the project that used Temporal's own durable
  timer — the entire justification, across five separate phase reports, for not building any other
  sleep-based Temporal workflow ("every workflow that has used `workflow.sleep()` has hung"). The
  actual root cause was a five-character wrong API call, not a platform limitation, and it had never
  been diagnosed because every phase's test run explicitly excluded this file
  (`--ignore=tests/test_temporal_workflows.py`) rather than let it fail visibly.
- **Reproduction**: `PYTHONPATH=. .venv/bin/pytest tests/test_temporal_workflows.py::test_invoice_overdue_workflow_escalates_when_unpaid`
  — hung past 120s before the fix (confirmed directly, twice).
  A standalone diagnostic script (`asyncio.wait_for(..., timeout=25)`) surfaced the underlying
  `AttributeError` from the worker's own log within 2 seconds.
- **Fix**: `await workflow.sleep(timedelta(seconds=n))` → `await asyncio.sleep(n)`. Temporal's SDK
  patches `asyncio.sleep` inside the workflow sandbox to route through its own durable timer
  mechanism (confirmed in `temporalio/worker/_workflow_instance.py`) — this is the SDK's actual,
  current API for a durable wait, not a workaround.
- **Test**: `tests/test_temporal_workflows.py` (already existed, unmodified) — now passes in 2.78s
  for the whole file, including the workflow that had never once completed before.
- **Status**: FIXED, verified. This resolves the single oldest open item in the entire project's
  known-limitations history (present in every phase's status doc since Phase 5).

**P1-2: Migration 0011 has never actually run against a real database — it is not SQLite-compatible,
and no migration in this project had ever been tested end-to-end from an empty database.**
- **Evidence**: running `alembic upgrade head` against a brand-new, empty SQLite file failed at
  0010→0011 with `NotImplementedError: No support for ALTER of constraints in SQLite dialect` — a
  bare `op.create_unique_constraint()` call on an existing table, which SQLite's `ALTER TABLE` cannot
  express directly.
- **Why it matters**: this project's `dev.db` was never actually migrated by Alembic past a certain
  point — every phase since Phase 5 (documented explicitly in each phase's own status report) instead
  ran raw `sqlite3` `ALTER TABLE`/`CREATE INDEX` statements by hand to keep the dev database in sync,
  which is precisely the kind of manual step that lets a broken migration go unnoticed indefinitely.
  Whether the equivalent Postgres migration would have worked was never actually known — Postgres
  supports direct `ALTER TABLE ADD CONSTRAINT`, so this specific SQL might have been fine there, but
  the *practice* of never once testing the full migration chain from zero is itself the finding, and
  it had already produced one real, concrete failure.
- **Reproduction**: `rm -f test.db && DATABASE_URL="sqlite+aiosqlite:///test.db" alembic upgrade head`
  from a clean checkout — failed before the fix, confirmed.
- **Fix**: rewrote migration `0011`'s `notifications` table alteration to use
  `op.batch_alter_table("notifications")`, Alembic's portable pattern that emits direct `ALTER`
  statements on Postgres and its copy-and-move strategy on SQLite. Re-ran the same from-empty
  migration: all 11 migrations now apply cleanly, in order, producing the expected 89-table schema
  with the `uq_notification_dedupe` constraint correctly present. Then booted the full FastAPI app
  against that freshly-migrated (not `create_all`'d) database and exercised a real
  register → login → create-customer → list-notifications → list-automation-policies round trip
  successfully over HTTP.
- **Test**: no automated test previously existed for "migrations apply to an empty database" — this
  is exactly the kind of check that only a manual/CI step catches, since the test suite's own fixtures
  use `Base.metadata.create_all`, which papers over migration bugs by construction. Documented as a
  now-required manual/CI step in `PRODUCTION_READINESS.md` and the operational runbook in
  `PROJECT_STATUS.md`, since a proper automated "migrate from empty, assert schema" CI job is the
  correct long-term fix and wasn't added as a test file in this pass (see Remaining Limitations).
- **Status**: FIXED (migration itself) and manually verified end-to-end; the CI regression test for
  "migrations apply cleanly from empty" is a named remaining gap, not silently claimed as covered.

### P2 — Identified, not fixed this pass (documented, not blocking)

**P2-1: No token revocation / session invalidation.** JWTs are stateless with a 60-minute default
access-token lifetime and no server-side blacklist or session table. A deactivated/fired user's
existing access token remains valid until it naturally expires. Standard tradeoff for a stateless-JWT
system, but worth stating plainly: there is currently no way to immediately revoke a compromised or
no-longer-authorized token short of rotating `JWT_SECRET` (which invalidates every session tenant-wide).

**P2-2: `/api/v1/auth/refresh` and `/api/v1/auth/logout` do not exist.** `create_refresh_token()` is
called at register/login and the frontend stores the refresh token in `sessionStorage`, but no backend
route ever consumes it — it is dead data today. When the 60-minute access token expires, the frontend's
`useAuth` hook catches the resulting 401 and forces a full re-login rather than silently refreshing.
Not a security issue (the unused refresh token can't be exploited for anything it doesn't already grant
via re-login), but a real, previously unnoticed incomplete feature — the refresh token has been minted
and shipped to the client every single phase without ever being usable.

**P2-3: No file-download endpoint exists for uploaded job attachments/photos/voice notes.** Files can be
uploaded (`operations.add_job_photo` etc., base64-in-tool-payload, real validation, real tenant-scoped
storage — see `app/storage/local_adapter.py`, which is genuinely well-built: filename sanitization,
`..`/`/` rejection in `_resolve()`, MIME allowlist, 25MB cap) and listed as metadata
(`GET /jobs/{id}/attachments`), but there is no route that returns the actual file bytes back to a
browser. A photo taken in the field can be recorded but never viewed again through the product.

**P2-4 (FIXED, Phase 12A — the "no internet access" premise was wrong):** Phase 11 assumed no internet
access and left dependency CVEs unverified. It turned out internet access is available in this sandbox
(confirmed by successfully installing `pip-audit` and `uvx` tooling from PyPI). A real scan found 23
backend CVEs across `starlette` (via `fastapi`), `python-jose`, `python-multipart`, `pyasn1`, and `pytest`,
plus 2 frontend CVEs in `next`. All fixed — see `PRODUCTION_READINESS.md` row 25 for the full detail,
exact version changes, and verification method (full test suite + fresh-venv install + live smoke tests,
both backend and frontend, before and after each change). One remaining, deliberately accepted: `ecdsa`
0.19.2's Minerva timing-attack advisory has no fix from its maintainers, but this app's JWT usage is
HS256-only and never invokes the vulnerable ECDSA code path.

## Not Verified (explicitly, not silently assumed)

The following require infrastructure genuinely unavailable in this sandbox and were **not** exercised
this pass, consistent with the honest-reporting requirement of every phase of this project:

- **PostgreSQL** (all testing, including the migration fix above, ran against SQLite — no `docker`
  binary is present in this environment).
- **Redis** (`EVENT_TRANSPORT=redis`, the production event transport, was never exercised — all testing
  used `EVENT_TRANSPORT=memory`).
- **Docker / `docker compose up`** (no Docker daemon available).
- **A second, physically separate `event-worker` process** talking to the API process only via Redis
  Streams (only the in-process fallback worker has ever run).
- **Real load/concurrency at scale** (concurrency tests in this project use small numbers of genuinely
  concurrent `asyncio` coroutines against SQLite, which is sufficient to prove CAS/locking logic is
  correct but says nothing about throughput or lock contention under real production load).
- **Real AI provider calls** (Anthropic/OpenAI) — no API key configured; the HTTP call bodies are real
  and unit-tested against a mocked network seam only.
- **Real email/SMS delivery** — no SendGrid/Twilio credentials configured.

## Phase 12B — Real PostgreSQL + Redis + Docker Production Verification

Closes several of the gaps listed above. Docker itself remains unavailable in this
sandbox (confirmed again: no `docker`, `podman`, `colima`, `lima`, or `brew`) — that gap
is NOT closed and is reported honestly below, not faked. Instead, real PostgreSQL 16.2
and real Redis were obtained as actual compiled binaries via the `pgserver`/`redislite`
Python packages (no Docker required) and used to run this project's entire real
application and test suite against genuine engines, not substitutes.

**What is now verified that previously was not:**

- **PostgreSQL**: all 14 migrations run cleanly from a genuinely empty, freshly
  initialized real Postgres database. The full 290-test backend suite passes running
  against it (was SQLite-only before). A real, previously-invisible bug was found this
  way: SQLite does not enforce `VARCHAR(N)` length at all; Postgres does.
  `communication_logs.status` was `VARCHAR(20)` but a real code path
  (`internal_test_adapter.py`) writes a 21-character value — this had been silently
  wrong since Phase 5 and was invisible to every prior test run. Fixed (migration 0014,
  widened to 40) and covered by a dedicated regression test that runs directly against
  real Postgres.
- **Redis**: the full test suite now also runs with `EVENT_TRANSPORT=redis` against a
  real `redis-server` process. Separately, `RedisStreamTransport` (previously never
  exercised at all — the test suite's `event_bus` fixture always constructed
  `InMemoryTransport` regardless of `EVENT_TRANSPORT`, a fact the class's own docstring
  incorrectly obscured until this phase) is now directly tested against real Redis:
  `XADD`/`XREADGROUP`/`XACK`/`XPENDING`, consumer-group message-splitting across two
  consumers, and a full `EventBus.publish → real Redis → process_pending → handler →
  ack` round trip including idempotency-key dedup.
- **A real regression from this same session's prior work**: the immediately-preceding
  dependency-CVE-remediation pass (pytest-asyncio 0.24.0→1.4.0) silently broke
  cross-event-loop asyncpg connection handling — invisible against SQLite/aiosqlite,
  but produced ~175 cascading errors the moment the suite ran against real Postgres.
  Root-caused to pytest-asyncio 1.x removing support for overriding the built-in
  `event_loop` fixture, which this project's `conftest.py` relied on. Fixed via
  `pytest.ini`'s `asyncio_default_fixture_loop_scope = session` /
  `asyncio_default_test_loop_scope = session`, and the now-dead custom fixture removed.
- **Health vs. readiness**: `GET /ready` did not exist at all before this phase — only
  a static, dependency-free `/health`, which would report `ok` even with Postgres and
  Redis both completely unreachable. Added a real `/ready` (checks a live `SELECT 1`
  and a live Redis `ping()`, returns 503 with a `checks` breakdown on failure),
  live-verified across all 4 up/down combinations of Postgres/Redis against the real
  infrastructure above, including recovery once each dependency comes back. Found and
  fixed a genuine hang bug in the process: a connection that's already open to a
  dependency that stops responding (simulated via `SIGSTOP` on the real `redis-server`
  and `postgres` processes) has no bounded timeout by default — the endpoint would hang
  indefinitely rather than reporting unreachable. Fixed with an explicit
  `asyncio.wait_for` (3s) around each check.
- **Temporal**: re-verified all 4 existing workflow tests (including
  `InvoiceOverdueWorkflow`, fixed in Phase 11) against a real ephemeral Temporal test
  server, and added 2 new tests: duplicate workflow-ID rejection (a real
  `WorkflowAlreadyStartedError` from the real server, not application-level dedup) and
  worker-restart resilience (a workflow started under one `Worker`, that worker fully
  shut down entirely, a brand-new `Worker` instance brought up against the same task
  queue, and the workflow resumes and completes with no lost work).
- **Real PostgreSQL transaction semantics**: dedicated tests directly against real
  Postgres prove a failed `INSERT` inside a multi-row transaction rolls back the entire
  transaction (not just the offending row), that `session.flush()` is not a partial
  commit, and that a `(tenant_id, idempotency_key)` unique constraint is correctly
  tenant-scoped (the same key succeeds for two different tenants).
- **Tenant isolation on real Postgres**: all 17 existing dedicated tenant-isolation
  tests (auth, CRM, operations, tool execution) now also pass running against real
  Postgres, not just SQLite.
- **Postgres persistence**: a marker row was written, the real `postgres` process
  cleanly stopped via `pg_ctl stop -m fast` (not killed), then restarted pointed at the
  same data directory, and the row confirmed to have survived — proves the data
  directory itself is durable across a clean stop/restart. This is NOT a backup/restore
  or point-in-time-recovery test; no such procedure exists yet.
- **A full live critical-business-flow E2E run** directly against the real
  Postgres+Redis-backed running application over real HTTP: register → login → create
  lead → qualify → convert-lead-to-job (creates customer + appointment + job in one
  step) → schedule → dispatch → en_route → on_site → start → complete → QA-pass →
  close → trigger invoice from job → attempt send (correctly rejected: "Only APPROVED
  invoices can be sent" — the real business rule, not a bug) → record payment →
  customer balance → audit timeline. Every step either succeeded or correctly enforced
  an existing business rule; nothing was mocked.
- **Frontend against the real-Postgres-backed backend**: driven through an actual
  browser (not a static read) — registered a new organization, landed on a real
  dashboard showing live zeros for a fresh tenant, created a lead through the UI form,
  and confirmed it round-tripped through the real API and displayed correctly. Network
  tab confirmed every request hit the real-Postgres-backed backend, not a mock.
  `tsc --noEmit` and `next build` both clean.
- **Performance spot-check**: re-read the dashboard aggregation hot paths (`GET
  /crm/metrics`, `GET /customers` search) directly — both use single aggregate
  `func.count()`/paginated queries with no per-row nested queries and no lazy-relationship
  access during serialization. No N+1 found. Not a systematic load test.
- **Environment separation / secrets**: `Settings` (Pydantic `BaseSettings`) reads every
  environment-specific value (`DATABASE_URL`, `REDIS_URL`, `TEMPORAL_NAMESPACE`,
  `STORAGE_LOCAL_ROOT`, `OBJECT_STORAGE_BUCKET`) from the environment with no hardcoded
  cross-environment sharing found. Re-grepped the full source tree for hardcoded secret
  patterns (API-key-shaped strings, AWS keys, PEM private keys, Slack tokens) — none
  found. Frontend bundle only exposes `NEXT_PUBLIC_API_URL` — no secret-shaped
  `NEXT_PUBLIC_*` variable exists anywhere in the frontend source.

**What is still explicitly NOT verified after this phase:**

- **Docker itself** — no daemon available. `docker compose up`, image builds, and
  container-to-container networking under a real Docker daemon have never been run. A
  static re-read of `docker-compose.yml` and both Dockerfiles this phase found real,
  previously-undocumented gaps (documented in full in `DOCKER_DEPLOYMENT.md`): neither
  Dockerfile drops to a non-root user; `frontend/Dockerfile` has no production build
  stage (dev-mode `npm run dev` only); no `restart:` policy on any compose service.
- **Real backup/restore or point-in-time recovery** — no procedure exists; only clean
  stop/restart persistence was verified (see above).
- **Real production-scale concurrent load** — the concurrency tests (CAS logic under
  `asyncio.gather()`) prove correctness, not throughput, and were not repeated at scale
  against real Postgres this phase.
- **A real staging/production deployment target** — still does not exist.
- Everything else listed as not-verified in the original section above this one, that
  Phase 12B did not touch (real AI provider calls, real email/SMS delivery, real
  external integrations) remains unchanged and still not verified.

## Phase 12C — Real External Integrations

Full architecture, per-provider detail, and exact testing procedures live in `INTEGRATIONS.md`
(new this phase). This section is the audit-style summary.

**Real, implemented, architecturally sound — but genuinely unverified against any live provider
API**, because no real credentials were available in this environment at any point during this
phase (checked `backend/.env` repeatedly; every provider's key remained empty throughout):

- **Stripe**: real `StripeClient` (`app/integrations/stripe_client.py`) — direct httpx calls,
  real retry-with-exponential-backoff on 429/5xx, real webhook signature verification (HMAC-
  SHA256 per Stripe's documented scheme, with a replay-attack timestamp tolerance check). Wired
  through the EXISTING `PaymentService`/`ToolRegistry`/approval pipeline, not a parallel path:
  `finance.create_stripe_checkout_session` (new tool, `AUTO` policy — generates a payment link
  only, no money moves) creates a real Checkout Session with tenant/invoice/customer stamped
  into PaymentIntent metadata; `POST /api/v1/webhooks/stripe` verifies the signature, dedupes
  via the new `webhook_events` table, and calls the SAME `PaymentService.record_payment()` the
  internal-test payment tool already used — proven idempotent by a dedicated test that asserts
  a duplicate webhook delivery never double-applies a payment. Refunds: `decide_refund()` now
  makes a real Stripe refund call (with an idempotency key) BEFORE marking the DB refund
  COMPLETED — if the real call fails, no DB state changes, never claims a refund happened when
  it didn't. Refunds remain permission-gated/approval-required exactly as before; AI still
  cannot approve its own refund.
- **Twilio**: real `TwilioSMSAdapter` (`app/communications/twilio_adapter.py`) — direct httpx
  calls, real webhook signature verification implementing Twilio's documented algorithm
  directly (HMAC-SHA1 over the full request URL + sorted params). Wired through the EXISTING
  `CommunicationProvider` abstraction via a new `get_communication_provider()` factory
  (mirroring `get_object_storage()`'s and `get_ai_provider()`'s established "real if
  configured, else honest internal-test fallback" pattern) — replaced 5 previously-hardcoded
  `InternalTestCommunicationAdapter(...)` call sites (`app/tools/factory.py` x2,
  `app/api/v1/ar.py`, `app/events/operations_handlers.py`, `app/events/finance_handlers.py`),
  so every existing SMS-sending flow (collections, retention reminders, appointment/job
  notifications) automatically uses real Twilio once configured, with zero changes to those
  services themselves. A new status-callback webhook
  (`POST /api/v1/webhooks/twilio/status`) updates the matching `CommunicationLog` row (new
  `external_id` column, migration `0016`) as Twilio reports delivery progress.
- **SendGrid**: real `SendGridEmailAdapter`, same factory/wiring pattern as Twilio, direct
  httpx calls to SendGrid's v3 Mail Send API.
- **OpenAI/Anthropic**: real since Phase 9 (`app/services/ai_provider.py`, unchanged this
  phase); Phase 12C adds both to the `GET /api/v1/integrations` status list for the first time
  (previously entirely absent from it), with a real minimal-call `check_status()`.

**A found-and-fixed self-inflicted mistake, caught before it shipped**: an early draft of the
Twilio signature test asserted against a specific "Twilio's own documented example" signature
value that was, on inspection, fabricated rather than sourced — Twilio's real docs describe the
algorithm and give an example URL/param set but deliberately never publish a real Auth Token
(publishing one would mean publishing a real secret), so there is no independently-verifiable
reference triple to test against. Caught this before merging by actually fetching Twilio's real
docs page and confirming no real signature value exists there to test against; the test suite
was rewritten to self-consistently round-trip (sign then verify) rather than falsely claim
parity with an external fixture that doesn't exist. Recorded here because it's exactly the kind
of "fabricate a plausible-looking value" mistake this audit's own rules forbid, and it's more
useful to document than to quietly bury.

**Webhook security review**: every webhook endpoint (Stripe, Twilio) verifies the provider's
signature BEFORE parsing/trusting the payload; a rejected signature is never persisted to the
idempotency ledger (its event id is unverified, so trusting it as a dedup key would let a forged
payload poison a future real one); `tenant_id` is only ever taken from server-side metadata this
application itself stamped when creating the upstream object (Stripe PaymentIntent metadata),
never from unauthenticated request data directly. Duplicate delivery is DB-enforced via a real
unique constraint, proven by test for both providers.

**Secret handling re-checked**: re-grepped the full tree for hardcoded API-key/AWS-key/private-
key/Slack-token patterns (none found, including in test files, where fake-looking test
credentials were deliberately used); confirmed `.env` is gitignored AND was never committed to
git history (not just currently untracked); confirmed no `NEXT_PUBLIC_*` frontend env var
carries any credential; confirmed webhook raw payloads (retained for audit, per spec) never
contain a provider credential — Stripe/Twilio webhook bodies are authenticated by signature,
not by embedding a secret in the body, so this is inherent to the providers' own design, not
something this application had to guard against separately.

**Dependency audit re-run**: `pip-audit` and `npm audit` both re-run this phase. Result:
unchanged from Phase 12A/12B (`ecdsa` 0.19.2's known, maintainer-declined, unreachable-in-this-
app timing-attack advisory remains the only finding; frontend: 0 vulnerabilities). No new
third-party dependencies were added this phase — every new integration (Stripe/Twilio/
SendGrid/OpenAI/Anthropic) uses direct httpx calls, matching the existing `ai_provider.py`
pattern, deliberately avoiding new SDK dependencies.

**Not implemented this phase (real gaps, not further stubbed)**: S3-compatible object storage
(local-disk adapter remains the only real, working backend); QuickBooks, Google Calendar,
Gmail, Google Ads, Meta Ads, Google Business, ServiceTitan, Jobber, and outbound-enrichment
(Apollo/Clay/Instantly) providers — all remain shallow, honest `NOT_CONNECTED` stubs; a
tenant-scoped OAuth connection model for per-tenant credentials (deliberately deferred — every
provider actually built this phase uses a single platform-level API key, and building
speculative OAuth-token storage before the first OAuth-based provider has real credentials to
verify against would be untestable infrastructure, not real progress).

**What remains explicitly NOT verified after this phase**: every claim above about "real API
client," "real webhook verification," and "real refund/payment recording" has been proven at
the unit/integration-test level against realistic, correctly-shaped, self-signed fixtures — NOT
against any actual Stripe/Twilio/SendGrid/OpenAI/Anthropic account, because no credentials were
ever configured in this environment during this phase. `check_status()`'s real API calls,
`finance.create_stripe_checkout_session`'s real Checkout Session creation, an actual SMS/email
delivery, and a live webhook fired by a real provider are all implemented but unexercised. See
`INTEGRATIONS.md` for the exact remaining verification step per provider once credentials
exist — in every case, it is "set the env var(s), restart, and run the documented testing
procedure," not further code changes.

## Phase 12D — Production Integration Completion

**Credential state re-audited at the start of this phase**: identical to the end of Phase 12C
— every integration env var in `backend/.env` remained empty. Real-provider verification
(spec Steps 2-6, 16) was therefore skipped entirely again, consistent with the spec's own rule
16 ("if blocked by credentials, continue with self-contained architecture/tests rather than
pretending"). Work concentrated on Steps 7-9: the tenant-scoped connection architecture.

**Built and real**: `integration_connections` (migration `0017`) — the tenant-scoped model the
Phase 12C audit explicitly deferred, for providers where each tenant owns their own external
account (QuickBooks/Google Calendar/Gmail/Google Ads/Meta Ads), separate from the Phase 12C
platform-level providers (Stripe/Twilio/SendGrid/OpenAI/Anthropic, unchanged this phase).
Credentials are encrypted at rest via Fernet (`app/integrations/credential_store.py`) — no new
third-party dependency (`cryptography` was already transitive via `python-jose[cryptography]`).
A new `INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` setting, with the identical production-boot-
refusal pattern as `JWT_SECRET`: the app now refuses to start with `ENV=production` if this key
is unset, rather than silently falling back to a publicly-known default that anyone reading
this open-source codebase could use to decrypt a real tenant's stored credential.

`IntegrationConnectionService` is the single place `NOT_CONNECTED → CONNECTING → CONNECTED →
ERROR → DISCONNECTED` lifecycle logic lives — a real state machine, not per-router duplication.
Providers register a real `verify()` callback (decrypted credential in, a real API call,
`(ok, detail)` out); **none is registered for any provider this phase** — no real OAuth client
exists yet for QuickBooks/Google Calendar/Gmail/Google Ads/Meta Ads, so attempting to connect
any of them produces an honest `ERROR: "No real verifier implemented for provider 'X'"`. This
was a deliberate structural choice: it is architecturally impossible for this service to report
a fabricated `CONNECTED` for an unimplemented provider, because `CONNECTED` is only ever set in
the branch where a registered verifier's real call actually returned `ok=True`.

**Tenant isolation — the specific requirement this phase emphasized**: enforced and tested at
three independent layers. Query layer: every `IntegrationConnectionService` method takes
`tenant_id` and filters every query by it (12 tests: connect/verify/disconnect/list all proven
to only ever see/touch the calling tenant's own row, including two tenants independently
connecting the identical provider name without collision). API layer: `current_user.tenant_id`
from the authenticated JWT only, never a client-supplied value; a cross-tenant verify/disconnect
attempt returns a real HTTP 404 (not another tenant's data, not a 403 that would confirm the
row's existence) — 6 dedicated tests, including one that asserts tenant A's connection is
provably UNCHANGED after tenant B's failed cross-tenant disconnect attempt. Database layer: a
real `UniqueConstraint("tenant_id", "provider")` and an indexed `tenant_id` column.

**A real bug found and fixed while building this**: `IntegrationConnectionService.verify()`
initially awaited a registered verifier's real API call with no bounded timeout — a hung
provider call (network partition, stalled endpoint) would have hung the request indefinitely.
This is the identical class of bug found and fixed in Phase 12B's `/ready` endpoint (an
already-open connection to a dependency that stops responding has no timeout by default).
Fixed with an explicit `asyncio.wait_for` (10s); proven by a dedicated test registering a
verifier that `asyncio.sleep(999)`s and asserting the overall call still completes with status
`ERROR` and a "timed out" detail within the test's own 5-second outer bound.

**A second, smaller bug found and fixed**: adding the new production-boot-refusal check broke
an existing, previously-passing test (`test_starts_in_production_with_a_real_secret`, which
simulates a fully-valid production boot by setting a real `JWT_SECRET` but — reasonably, since
the check didn't exist yet when that test was written in Phase 11 — never set the new
`INTEGRATION_CREDENTIAL_ENCRYPTION_KEY`). Fixed by updating the test to set both, and added a
new dedicated test for the refusal case itself, rather than weakening or removing the new
check to make the old test pass.

**Security re-checked**: fresh secrets grep across the full tree (API-key/AWS-key/private-key/
Slack-token patterns) — none found, including the deliberately fake-looking test credentials in
`test_stripe_webhook_signature.py`/`test_stripe_webhook_endpoint.py` (unchanged, pre-existing,
already reviewed in Phase 12C). Confirmed no logging call anywhere in the new connection
service or API references the raw credential dict — only `provider`/`tenant_id`/`operation`/
`success`/`latency_ms`. `pip-audit`/`npm audit` re-run: unchanged from Phase 12B/12C (0 new
vulnerabilities; no new third-party dependency was added this phase).

**Not done this phase**: any real OAuth provider (spec Step 10) — building OAuth authorization-
URL/state-validation/callback/token-exchange code with no real Google/Intuit developer app to
test it against would produce untestable, unverifiable code, not real progress. The connection
model, encryption, lifecycle, and tenant isolation are ready to receive the first real OAuth
provider the moment credentials exist.

## Phase 12E — Real OpenAI Provider Activation + Production AI Verification

**Credential state re-audited at the start and end of this phase**: `OPENAI_API_KEY` (and
`ANTHROPIC_API_KEY`) remained unset in `backend/.env` throughout. Real-provider verification
(spec Steps 12, 13, and the real-provider halves of 16/18/19) is `BLOCKED BY CREDENTIAL` — not
run, and never claimed as passing. Work concentrated on hardening the existing Phase 9 AI
provider architecture and building the first real, tested consumer of it beyond the Morning
Brief.

**Architecture confirmed before any change (spec Step 2)**: `AIExecutionService`
(`app/ai/execution_service.py`) is the sole path by which AI-originated code reaches
`ToolRegistry.execute()` — confirmed by grepping every call site of `registry.execute(`/
`.execute(...)` across the tree; every caller is a human FastAPI route, a Temporal activity, the
post-approval resume path, or this one AI service. OpenAI/Anthropic were confirmed to be wired
as platform-level, single shared `Settings`-based credentials (not tenant-scoped via the Phase
12D `IntegrationConnectionService` — no verifier is registered for either) — this phase
preserved that established design rather than inventing a conflicting tenant-scoped model for
these two providers, per the spec's explicit instruction to do so.

**Hardened, real, and tested** (`app/services/ai_provider.py`, `app/core/config.py`):
- Configurable timeout/retries/max-output-tokens (`OPENAI_TIMEOUT_SECONDS`/`OPENAI_MAX_RETRIES`/
  `OPENAI_MAX_OUTPUT_TOKENS`, symmetric `ANTHROPIC_*`) — previously hardcoded (20s timeout, 0
  retries, 1024 max tokens for Anthropic only; OpenAI had no output cap at all).
- Real retry-with-exponential-backoff (`0.5 * 2^attempt`) — proven by test to retry on 429/5xx/
  timeout/network errors, and proven by test to NEVER retry on 401/403 (authentication) or 400
  (bad request), since retrying an invalid credential wastes time and quota rather than
  eventually succeeding.
- Real error classification (`AIErrorType`) replacing the previous "every failure collapses to
  `None`" behavior — `authentication`/`rate_limit`/`timeout`/`provider_error`/
  `malformed_response`/`network_error`, each proven by a dedicated test using a real
  `httpx.HTTPStatusError`/`httpx.TimeoutException`/`httpx.ConnectError`.
- Real token-usage extraction from both providers' actual response `usage` fields — previously
  discarded entirely. **Cost is deliberately never computed**: neither provider's API returns a
  real-time price, and embedding a static per-token price table would silently go stale as
  providers change pricing, producing a number that looks real but isn't verified against
  anything current — `estimated_cost_usd` stays `NULL`, proven by a dedicated test asserting it
  is never populated.
- Defense-in-depth key redaction (`_redact_key`): verified directly (via a real
  `httpx.HTTPStatusError` constructed with an `Authorization` header) that `httpx`'s own
  exception `str()`/`repr()` never include request headers — then added a scrub of the literal
  API key from every error string anyway, proven by a worst-case test that raises an exception
  whose message directly embeds the key and asserts it never survives to `error_detail`.

**A real, deliberate backward-compatibility decision**: `_call_api`'s return contract changed
from a bare `str` to a `_ProviderResponse` (carrying token counts) — this broke 2 pre-existing
tests that mocked the old contract (`test_successful_response_is_validated_and_returned`,
`test_ai_response_referencing_unknown_entity_id_is_caught_by_the_service_layer`). Fixed by
updating both mocks to the new, legitimately-changed contract — not by reverting the contract
change or weakening either test's assertions.

**New — the first real consumer of `generate_structured()` beyond the Morning Brief**
(`app/services/ai_qualification_service.py`, `crm.ai_qualify_lead_advisory` tool, `POST
/api/v1/leads/{lead_id}/ai-qualify-advisory`): an AI-assisted lead-qualification recommendation.
**Structurally advisory-only** — the service never opens a session to write, and the tool never
calls another tool; `app/services/scoring.py::score_lead` (deterministic, unchanged since Phase
3) remains the only code path that ever sets `Lead.lead_score`/`qualification_status`. Verified
by test that:
- **Tenant isolation** — a lead is resolved via `session.get(Lead, lead_id)` then checked
  against the caller's `tenant_id` BEFORE the AI provider is ever invoked; a dedicated test
  asserts the fake provider's `last_prompt` is `None` after a cross-tenant attempt, proving the
  provider genuinely was never called, not just that the wrong answer was discarded afterward.
  A second test proves the tool's Pydantic input schema has no `tenant_id` field at all — a
  caller (AI or human) supplying one in the raw request dict has it silently dropped by Pydantic
  validation, structurally, not by a runtime check that could be bypassed.
- **Prompt/context isolation** — the constructed prompt is proven (by test, inspecting the
  actual prompt string) to never contain the lead's name, phone, or email, and to fence the
  lead's free-text `description` field strictly inside a `BEGIN LEAD DATA`/`END LEAD DATA`
  marker pair, even when that field itself contains an injection attempt ("Ignore prior
  instructions...") — proven by asserting the injection text's string index falls after the
  fence marker's index, not merely that both strings are present somewhere in the prompt.
- **Audit/usage persistence** — every real invocation, success or failure, is written to
  `ai_invocation_logs` (migration `0018`) with real `provider`/`model`/`operation`/
  `correlation_id`/`latency_ms`/`retry_count`/`input_tokens`/`output_tokens`/`error_type`, never
  the raw prompt, raw response, or API key; proven tenant-scoped by a test confirming tenant B's
  query for tenant A's invocation returns empty.
- **Safe fallback** — with no AI provider configured (this environment's actual state), the
  service returns an honest `available: false` with a real reason string, proven through the
  full stack: service layer, the real `ToolRegistry` (not a test-only construction — the same
  `build_tool_registry()` the running app uses), and a live HTTP call against the actual
  running app backed by real PostgreSQL.
- **Approval boundary** — this tool's policy is `AUTO`, correct and verified because the tool
  is read-only; it has no code path to call `ToolRegistry.execute()` for a different tool, so it
  structurally cannot bypass another tool's `APPROVAL_REQUIRED`/`BLOCKED` policy — there is
  simply no mechanism by which it could.

**Security re-checked**: fresh secrets grep across the full tree (API-key-shaped patterns) —
none found. Confirmed by direct code read that no logging call anywhere in
`ai_provider.py`/`ai_qualification_service.py`/`ai_invocation_log_service.py` references
`self._api_key`, `raw_text`, or any prompt/response content — only `provider`/`tenant_id`/
`operation`/`success`/`latency_ms`. `pip-audit`/`npm audit` re-run: unchanged from Phase 12B/
12C/12D (0 new vulnerabilities; no new third-party dependency was added this phase — no `openai`
or `anthropic` SDK, matching the project's established direct-httpx pattern).

**Live verification performed** (Step 19): registered a fresh tenant, logged in through the
real browser against the real-Postgres-backed backend, confirmed the Owner Cockpit, Leads page,
and Integrations page all render with zero console errors beyond the expected dev-server HMR
websocket noise; confirmed via the browser's own network panel that every API call hit the real
backend on its real port, not a mock; made a direct HTTP call to the new AI-qualification
endpoint against the live, real-Postgres-backed app and confirmed the exact same honest
`available: false` response the unit tests predicted.

**What remains explicitly NOT verified after this phase**: the real OpenAI/Anthropic API call
itself (`check_status()`'s real network round-trip, `generate_structured()`'s real response
parsing, real token-usage numbers from an actual response, a real AI-generated qualification
recommendation) — all implemented and tested against realistic fixtures, none exercised against
a real provider, because no credential exists in this environment. See `INTEGRATIONS.md` for
the exact remaining step.

## Phase 12F — Real Stripe Activation + Production Payment Integration

**Credential state re-audited at the start and end of this phase**: `STRIPE_SECRET_KEY` and
`STRIPE_WEBHOOK_SECRET` remained unset in `backend/.env` throughout. Real-provider verification
(spec Step 16) is `BLOCKED BY CREDENTIAL`. Work extended the substantial Phase 12C Stripe
foundation (real client, checkout sessions, webhook, refund wiring — all confirmed present
before writing any code, per spec Step 2) rather than duplicating it.

**A real, exploitable P0 vulnerability found and fixed this phase — the most significant
finding of Phase 12F**: while hardening the Stripe refund approval path, discovered that
`finance.approve_refund`/`reject_refund` relied SOLELY on role-based permission gating
(`required_permission = Permission.APPROVE_REFUND`), with NO explicit actor-type check — unlike
the generic `approval.approve_action`/`reject_action` tools
(`app/tools/builtin/approval_tools.py`), which have always had an explicit
`if context.actor_type == ActorType.AI: raise ValueError(...)` guard. The tool's own docstring
claimed "a human-only permission the AI boundary's role never grants" — this claim was checked
directly and found FALSE: `Role.MANAGER`, the only role `AIExecutionService` has ever actually
been invoked with anywhere in this codebase (`app/services/morning_brief_service.py`, the sole
real caller), genuinely holds `APPROVE_REFUND` in `ROLE_PERMISSIONS`
(`role_has_permission(Role.MANAGER, Permission.APPROVE_REFUND)` → `True`, verified directly).

**A captured proof-of-concept, not a hypothetical**: built a real invoice, real internal-test
payment, real refund request through the actual `ToolRegistry`, then constructed an
`ExecutionContext(actor_type=ActorType.AI, role=Role.MANAGER)` and called
`finance.approve_refund` directly. **Result: the AI actor successfully approved and completed
its own refund** (`status = COMPLETED`), with no human ever involved, no approval bypass error,
nothing in the pipeline to stop it. This is exactly what the "AI must never approve its own
action" invariant — stated repeatedly across this project's history — exists to prevent, and it
was silently unenforced for the entire finance-approval domain (not just refunds).

**Same gap found, by direct code inspection, in three sibling tool pairs**: `finance.approve_invoice`/
`reject_invoice` (docstring made the identical false claim: "the AI execution boundary's role
never grants it"), `finance.approve_credit_note`/`reject_credit_note`, `finance.approve_writeoff`/
`reject_writeoff` — all four pairs share the exact same permission-only gating pattern, all four
of `APPROVE_INVOICE`/`APPROVE_CREDIT_NOTE`/`APPROVE_WRITEOFF` are also held by `Role.MANAGER`
(verified directly, all `True`).

**Fixed**: added the identical explicit `ActorType.AI` guard (raising `ValueError` with a clear
message) to all eight tools — `finance.approve_refund`, `reject_refund`, `approve_invoice`,
`reject_invoice`, `approve_credit_note`, `reject_credit_note`, `approve_writeoff`,
`reject_writeoff`. Re-ran the exact proof-of-concept script after the fix: the same AI-actor
call now raises `ValueError("AI cannot approve a refund — approval requires a human actor")`
instead of succeeding. Added 8 permanent regression tests
(`tests/test_finance_approval_ai_guard.py`) — one per guard, plus a test proving the legitimate
human path still works unchanged, plus a test that directly documents
`role_has_permission(Role.MANAGER, ...)` returning `True` for all four permissions (so this
exact gap can never silently regress without a visible, named test failure). The fix required no
functional behavior change for any human caller — every existing test (398 total at phase close) continued
passing unmodified, confirming no legitimate flow depended on the AI-reachable path.

**Real, hardened, and tested this phase** (`app/integrations/stripe_client.py`,
`app/core/config.py`): `STRIPE_TIMEOUT_SECONDS`/`STRIPE_MAX_RETRIES` moved from hardcoded
constants to `Settings`. Real error classification (`StripeErrorType`) — 401/403 now correctly
never retried (previously fell through the generic `>=400` non-retry branch by accident, not by
explicit design — now explicit and tested). A real idempotency key on `create_checkout_session`
(`klaros-checkout-{invoice_id}-{amount_due}`) — previously only `create_refund` had one.

**New — the first provider to actually use the Phase 12D `IntegrationConnection` model**: a
`_stripe_verifier` registered in `app/api/tool_deps_integrations.py`, making a real
`GET /v1/balance` call against a tenant's own submitted key. `finance.create_stripe_checkout_session`
now resolves credentials tenant-first, platform-fallback — proven by test that tenant B's key is
never used for tenant A even when both exist, and that a disconnected tenant connection falls
back to the platform key rather than erroring. Live-verified end-to-end in a real browser
against the real-Postgres-backed app: connecting a deliberately-fake test key (`sk_test_fake_
for_browser_verification`) made a real network call to Stripe's live API — confirmed via the
resulting `ERROR: "secret_key rejected by Stripe's API"` message, which only a real rejected
API call could produce, not a code-level short-circuit.

**New — webhook event coverage extended**: `payment_intent.payment_failed` (publishes the
existing `PAYMENT_FAILED` event with the real decline reason, no `Payment` row created since
nothing succeeded) and `charge.refunded` (reconciles a refund issued OUTSIDE Klaros — Stripe
Dashboard, a dispute — via a new `PaymentService.reconcile_external_refund()`). The
reconciliation method uses Stripe's own CUMULATIVE `amount_refunded` field, not a delta,
mirroring `record_payment()`'s established "recompute from the real source of truth" idempotency
pattern — proven by a dedicated test that delivers the identical `charge.refunded` webhook twice
and confirms exactly one `Refund` row and no double-adjustment of the invoice.

**Marketing attribution loop verified against a real Stripe payment (spec Step 15, called out as
critical)**: built a real `Campaign` + `MarketingSpendAllocation` + attributed `Lead` + `Job` +
`Invoice`, then sent a real (self-signed, correctly-shaped) `payment_intent.succeeded` webhook
through the actual `/api/v1/webhooks/stripe` endpoint and confirmed `CampaignConversion.collected_amount`
and `campaign_performance()`'s real ROAS calculation update correctly — proving Stripe payments
flow into the SAME attribution pipeline as every other payment source, with no Stripe-specific
code path. **A genuine architectural discovery made while writing this test, not a bug**: the
first attempt asserted attribution updated synchronously after the webhook call and failed —
`EventBus.publish()` (the durable outbox pattern used throughout this project) persists an event
but does not synchronously invoke subscribers; dispatch only happens when something calls
`process_pending()`, which in production is the real `EventWorker`'s continuous poll loop, not
the webhook request itself. Fixed the test (not the architecture, which is correct and
consistent with the project's established at-least-once delivery design) by explicitly calling
`process_pending()` after each webhook POST, matching what the real worker does continuously.
A second test proved a redelivered webhook (duplicate) never double-counts collected revenue —
the duplicate is caught by `webhook_events`'s unique constraint before `record_payment` (and
therefore `PAYMENT_RECEIVED`, and therefore attribution) is ever reached a second time.

**Security re-checked**: fresh secrets grep across the full tree — no Stripe-key-shaped strings
(`sk_live_`/`sk_test_[20+chars]`/`whsec_[20+chars]`) found anywhere outside deliberately-fake
test fixtures (unchanged from Phase 12C, already reviewed). Confirmed by direct code read that
no logging call anywhere in `stripe_client.py`/`stripe_tools.py`/`tool_deps_integrations.py`/
`payment_service.py`/`webhooks.py` references secret/key/credential/auth material — every log
line carries only `attempt`/`error_type`/`status_code`/`retryable`. Confirmed no request-body-
logging middleware exists anywhere in the app (so an incoming `connect` request's raw credential
JSON is never incidentally logged). `pip-audit`/`npm audit` re-run: unchanged from Phase 12B-E
(0 new vulnerabilities; no new third-party dependency — no `stripe` SDK, matching the project's
established direct-httpx pattern).

**Security-boundary gap tests added at phase close** (`tests/test_stripe_phase12f_gaps.py`, 7
tests, all passing on SQLite and in the full real-Postgres+Redis run): (1) **no tool can ever
record a `stripe`-sourced payment** — `finance.record_test_payment`, the only payment-recording
tool, is pinned to the `internal_test_payment` provider, so the `stripe` provider value is
reserved exclusively for the server-side signed-webhook path; (2) **a cross-tenant signed
webhook cannot credit another tenant's invoice** — a correctly-signed `payment_intent.succeeded`
whose metadata points `invoice_id` at another tenant's invoice is recorded FAILED, rolls back
cleanly (zero `Payment` rows), and leaves the target tenant's invoice untouched, proving the
tenant check inside `PaymentService.record_payment` is a real security boundary, not a formality;
(3) `finance.create_stripe_checkout_session` refuses cleanly with no tenant connection and no
platform key (asserting no Stripe HTTP call is ever attempted), and (4) surfaces a real Stripe
401 as a `ToolError`; (5-7) the payment tool honors the tenant automation policy end-to-end —
APPROVAL_REQUIRED records the approval and records no payment, BLOCKED never executes, and a
genuinely APPROVED request resumes through `ApprovalExecutionService.execute_approved` and
executes.

**Live verification performed**: registered a fresh tenant, logged in through the real browser
against the real-Postgres-backed backend; confirmed Finance/Leads/Owner-Cockpit/Integrations
pages all render with real (honestly zero, for a fresh tenant) data and zero console errors
beyond expected dev-server HMR noise; used the new Stripe connect UI to submit a fake key and
confirmed, via both the UI and a direct API call, that a real Stripe API rejection was correctly
surfaced and persisted, with the credential itself never appearing in any API response.

**What remains explicitly NOT verified after this phase**: an actual successful Stripe test-mode
payment, an actual successful tenant Stripe connection (both require a real key, which does not
exist in this environment), the real webhook round-trip from an actual Stripe-fired event
(requires a public endpoint or `stripe listen`, neither available here). See `INTEGRATIONS.md`
for the exact remaining step per capability.
