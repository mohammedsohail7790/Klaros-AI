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

## Phase 12G — Credential Re-Audit + Self-Contained Stripe Deep Verification

**Credential audit re-run at the start of this phase** (PRESENT/MISSING only, no values ever
printed): `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`,
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY` — all PRESENT as variable names, all EMPTY, unchanged from
every phase back to 12C. Real-provider Stripe verification remains correctly `BLOCKED BY
CREDENTIAL`; no live call, no synthetic webhook, no fabricated response was produced instead.

**Self-contained Stripe audit, ten specific criteria, each checked against the actual code (not
assumed from prior documentation)**: request/response schema handling, webhook signature
verification (bypass-checked — none found), outbound idempotency (`Idempotency-Key` header on
checkout/refund calls), inbound webhook dedup (`webhook_events` unique constraint + a race-safe
`IntegrityError` catch), tenant isolation (metadata-only tenant resolution, cross-checked against
the invoice's own tenant before allocation), duplicate-delivery handling (both payment-success
and refund-reconciliation paths proven idempotent), retry/failure behavior (`STRIPE_TIMEOUT_SECONDS`/
`STRIPE_MAX_RETRIES` genuinely wired into `httpx.AsyncClient` and a real backoff loop, with
correct per-status-code retry/no-retry classification), audit logging (`WebhookEvent` rows for
every event including rejections, plus the generic bus-driven `AuditLog` handler, plus
`ToolRegistry`'s own audit path), and secret hygiene (grepped every Stripe-adjacent file for a
key value co-occurring with a `logger.*` call — none found; `StripeClient.__repr__` never
exposes the key).

**One real gap found and closed**: `PaymentService.decide_refund`'s re-decision guard
(`if refund.status != RefundStatus.REQUESTED: raise InvalidRefundError(...)`) existed in code
since at least Phase 12C but had no test proving it directly for the ordinary (non-Stripe)
refund path — the only existing coverage of that branch was incidental, inside the Phase 12F
Stripe-API-failure test. Added `tests/test_refund_state_transition_guard.py`: proves an
already-`COMPLETED` refund cannot be re-approved or rejected, an already-`REJECTED` refund
cannot be approved afterward, and neither a second approval nor a second rejection changes the
`Refund`/`Payment`/`Invoice` rows at all. Both tests pass.

**Two findings documented as known limitations, deliberately not changed**: (1)
`RefundStatus.APPROVED` and `PaymentStatus.PENDING`/`FAILED` are real enum members with zero
code paths in this codebase that ever assign them — reserved/future states, since the only
refund flow that exists (`REQUESTED` → `COMPLETED`/`REJECTED` directly) is fully implemented and
tested as written; removing or wiring them in without a driving requirement would be scope creep
this phase's mission explicitly warned against. (2) Stripe's inbound webhook JSON and outbound
API responses (`create_checkout_session`'s return value in particular) are handled as untyped
`dict`s with manual `.get()` calls rather than through a Pydantic schema boundary — the tool
layer one level up (`CreateStripeCheckoutInput`/`Output`) IS a proper Pydantic model, so the gap
is specifically at the raw-Stripe-shape boundary, not everywhere. A structural change on
Stripe's side would surface as a `KeyError`/`AttributeError` deep inside a handler rather than
at one central validation point — worth a dedicated schema in a future phase, not fixed
speculatively here.

**Real infrastructure, re-verified rather than re-stated**: this sandbox already had a
locally-running (non-Docker) PostgreSQL 16.2 and Redis 7 from prior work, confirmed live via
`lsof` (listening on 5432/6379) and a real `asyncpg`/`redis-py` connection — not `pgserver`/
`redislite` this time, the actual local server processes. The existing `klaros` role lacks
`CREATEDB`, so a fresh throwaway database could not be created; the existing `klaros` database
was reused instead, which is safe because `tests/conftest.py`'s autouse `_reset_database`
fixture drops and recreates every table before every single test regardless of what was in the
database beforehand. Full suite run against it, twice: once before the new refund-guard test
existed (406 passed, confirming the Phase 12F baseline was genuinely unchanged), and once after
adding it (see `PRODUCTION_READINESS.md`'s Phase 12G section for the exact final count).
SQLite re-run: 400 passed, 8 skipped (up from 398/8, the 2 new tests). `pip-audit` re-run:
unchanged, the one already-accepted `ecdsa` finding only.

**A genuine environment limitation, disclosed rather than glossed over**: no `node`/`npm` binary
exists anywhere in this sandbox instance this phase (checked `which node npm`, `mdfind`, and a
filesystem search under the user's home directory) — despite `frontend/node_modules` already
being populated from whatever prior session had Node available. `tsc --noEmit`, `next build`,
and `npm audit` could not be re-run. This is reported as NOT RE-VERIFIED THIS SESSION in
`PRODUCTION_READINESS.md` rather than silently treating Phase 12F's last clean result as still
current — no frontend source was touched this phase, so nothing is known to have broken, but
nothing was re-proven either.

**Gap analysis across the rest of the architecture** (authentication, RBAC, tenant isolation,
event bus, Temporal, the ToolRegistry/MCP layer, `AIExecutionService`, approvals, CRM,
Operations, Marketing, Retention, Finance, storage, communications, observability, migrations,
frontend/backend contract): re-checked for any concrete, reproducible defect. None found beyond
the one refund-guard test gap above — every other area's already-documented state from Phases
12A–12F held up under this pass with no new code-level finding.

## Phase 12G-2 — Dedicated Stripe Schema-Hardening Pass

Same phase, a follow-on mission specifically targeting the one item Phase 12G left as a known
limitation rather than a fix: Stripe's webhook/API bodies handled as bare `dict`s. Credentials
re-checked again at the start — `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` still empty in
`backend/.env`; every finding below comes from static code audit and self-contained tests, never
a live Stripe call.

**Built**: `app/integrations/stripe_schemas.py` — Pydantic models for exactly the Stripe shapes
this app reads or writes (the webhook envelope; `payment_intent`/`charge` payloads; the
`checkout.session`/`payment_intent`/`refund` API responses), every one `extra="allow"` so a field
Stripe adds later is preserved rather than rejected. `app/integrations/stripe_client.py` and
`app/api/v1/webhooks.py` were updated to validate through these models instead of unchecked
`.get()`/`body["..."]` indexing; business-rule validation (tenant ownership, UUID
well-formedness, legal state transitions) is untouched — the schemas only replace the
structural-shape layer beneath it.

**Two real, previously-invisible bugs found and fixed**:

1. **A validly-signed-but-malformed webhook body could 500 instead of 400.**
   `verify_webhook_signature` parsed JSON internally immediately after its HMAC check passed — a
   body that genuinely verified (real secret, real signature) but wasn't valid JSON raised an
   uncaught `json.JSONDecodeError` all the way to an unhandled 500. Root-caused by direct code
   reading (the exception simply wasn't in the webhook route's `except` clause), reproduced with
   a unit test calling the raw function directly before touching the route at all. Fixed with a
   new `StripeWebhookPayloadError`, distinct from `StripeWebhookSignatureError` (the trust
   boundary already passed by the time this fires — a different failure mode, different log
   message, same 400 status). Proven fixed at both the unit level (the client function now raises
   the right exception type) and the integration level (the live endpoint returns 400, not 500).

2. **A multi-field Pydantic validation failure could overflow a VARCHAR(500) column against real
   Postgres.** `WebhookEvent.error_detail` is `String(500)` (`app/models/integration.py`). A
   single `ValidationError` naming several simultaneously-invalid fields on one `payment_intent`/
   `charge` object renders as a message whose length scales with the number of failing fields —
   manually confirmed at 673 characters for a representative 3-field-error case (missing `id`,
   wrong-typed `metadata`, wrong-typed `amount`). SQLite never enforces `VARCHAR` length at all
   (unchanged fact since the Phase 12B `communication_logs.status` finding), so this was
   completely invisible against the SQLite suite; a genuine `StringDataRightTruncationError`
   against real Postgres was the actual failure mode, root-caused by direct experimentation with
   Pydantic's error-rendering (not discovered via a failing test — found by inspecting for
   exactly this failure class, informed by the Phase 12B precedent, then confirmed
   experimentally). Fixed by truncating `error_detail` to the column's real limit at its one
   write site in `app/api/v1/webhooks.py`. **Proven fixed against the actual constraint, not just
   the Python-level slicing**: a new regression test constructs a `payment_intent.succeeded`
   webhook whose `data.object` fails validation on four fields simultaneously, and asserts
   `len(row.error_detail) <= 500` — this test was run and passed against BOTH SQLite (415/8/0 on
   its own) and, specifically, real PostgreSQL+Redis (423/0/0/0), where it is the one test in the
   suite that actually exercises the enforcing `VARCHAR(500)` column this fix protects.

**A third finding, fixed while reviewing the new envelope-validation log line for information
hygiene** (not a crash, a log-hygiene concern): Pydantic's default `ValidationError.__str__()`
echoes a repr of the actual (possibly PII-bearing) input value for every failed field. Logging
`str(exc)` directly for the envelope-validation failure path (as the initial implementation did)
would have duplicated raw webhook body content into structured logs beyond what the signed
request already legitimately carries in the DB's `raw_payload` column. Changed to log only each
failed field's location (`loc`) and error type (`type`), never the echoed `input` value.

**Tests**: 15 new (`tests/test_stripe_schema_hardening.py`) — malformed JSON (unit + endpoint),
envelope validation (missing `id`/missing `type`, unit + live endpoint, confirming a rejected
envelope is never persisted to the dedup/audit table), unknown-field tolerance (schema-level unit
test + a full webhook round-trip carrying fields this app has never seen, both at the top level
and inside `metadata`), outbound response validation (a well-formed `checkout.session` response
and one deliberately missing the `url` field this app depends on, proving Stripe-side schema
drift surfaces as a classified `StripeAPIError` rather than an unhandled `pydantic.
ValidationError`), the `error_detail` truncation fix, and three new cross-tenant refund tests
(tenant B can approve/reject neither via the tool layer nor `PaymentService.decide_refund`
directly — a guard that already existed and was already correct, simply not previously pinned
down as an isolated, explicit test independent of the Stripe-failure test that incidentally
covered part of it). All 47 pre-existing Stripe tests across the other 8 Stripe test files
continued to pass completely unchanged — re-run together with the 15 new ones (43 tests spanning
`test_stripe_schema_hardening.py`, `test_stripe_phase12f_gaps.py`, `test_stripe_tenant_
connection.py`, `test_refund_state_transition_guard.py`, `test_stripe_webhook_endpoint.py`, and
`test_stripe_webhook_failure_and_refund.py`), all 43/43 passing. This was a hardening pass on an
already-largely-correct implementation, confirmed by tests rather than a rewrite: the one
behavior-visible change outside the client/schemas is `create_checkout_session`'s return type
moving from an untyped `dict` to `StripeCheckoutSessionResponse`, updating its single call site
in `app/tools/builtin/stripe_tools.py` from bracket to attribute access.

**Full suite, re-run against the completed final code state (not inferred from an earlier run)**:
SQLite 415 passed, 8 skipped, 0 failed, 0 errors (86.91s); real PostgreSQL 16.2 + real Redis 423
passed, 0 failed, 0 skipped, 0 errors (420.33s, i.e. 7:00). The dedicated
`tests/test_stripe_schema_hardening.py` file was additionally run in complete isolation from the
rest of the suite: 15/15 on SQLite, 15/15 on real Postgres+Redis. `pip-audit` re-run again:
unchanged, the one already-accepted `ecdsa` finding only. Repository secret-scan re-run against
every new/changed file (`stripe_schemas.py`, `stripe_client.py`, `webhooks.py`, `stripe_tools.py`,
both new test files): no hardcoded key/secret pattern found. Migrations re-verified from a
genuinely empty schema directly against the same real Postgres+Redis instance already running in
this environment — NOT the `public` schema holding this environment's existing data: a throwaway
schema (`klaros_migration_check`) was created, the `klaros` role's `search_path` was scoped to it
for the duration of the check via `ALTER ROLE ... IN DATABASE ... SET search_path`, `alembic
upgrade head` was run against it (all 18 migrations, 0001→0018, applied cleanly, landing 93
tables, alembic head confirmed `0018`), then the role's `search_path` was reset and the throwaway
schema dropped — confirmed afterward that `public` still holds exactly its prior 92 tables and
the role's config is back to `None`. Frontend: still no `node`/`npm` binary anywhere in this
sandbox instance (re-checked again this pass); genuinely NOT RE-VERIFIED, no frontend source
touched. Git status confirmed clean throughout: only the 3 modified production files, 1 new
schema module, and 2 new test files beyond the already-modified Phase 12G docs — no secrets, no
temporary database files left in the repository, no debug `print()`/skip markers introduced.

## Phase 13 — QuickBooks Online Integration (OAuth2 + Invoice Sync) — Final Verification

**Mission**: after Phase 12G-2's Stripe schema-hardening, audit the entire Phase 1-12G
architecture for the single highest-value unfinished production capability. Every completed
domain (CRM, Operations, Marketing, Retention, Finance, Stripe) was re-checked and found to have
no concrete, reproducible defect worth fixing in isolation. Of the unbuilt external integrations
(QuickBooks, Xero, Google Ads, Meta Ads, Google Business, ServiceTitan, Jobber — equally
credential-blocked in this environment), QuickBooks was selected as the highest business-value
pick for a field-service SMB platform, buildable and self-contained-testable completely
honestly without real credentials, matching exactly how Stripe itself was built in Phase 12C
before any credential existed.

**Credential audit, start of phase**: `QUICKBOOKS_CLIENT_ID`/`QUICKBOOKS_CLIENT_SECRET`/
`QUICKBOOKS_REDIRECT_URI` — none present in `backend/.env` at all (the pydantic settings model
defaults each to `None`/`"sandbox"`). Real Intuit OAuth/API verification is `BLOCKED BY
CREDENTIAL` for the entire phase; every finding below comes from static code audit and
self-contained tests, never a live QuickBooks call.

**Built**: `QuickBooksClient` (direct httpx, real OAuth2 authorization-code + refresh-token
grants, real error classification, real retry-with-backoff, real bounded timeout — the exact
same shape as `StripeClient`) + `quickbooks_schemas.py` (Pydantic, `extra="allow"`, built from
the start with the Phase 12G-2 schema-hardening pattern rather than needing a later hardening
pass). OAuth connect flow (`app/api/v1/quickbooks_oauth.py`: `/authorize` authenticated,
`/callback` deliberately unauthenticated matching the webhook trust model) reuses the existing
`IntegrationConnectionService`/`IntegrationConnection` model from Phase 12D — no parallel
credential-storage path. One new, narrowly-scoped primitive: `create_oauth_state_token`/
`decode_oauth_state_token` (`app/core/security.py`) — a signed, 10-minute CSRF/tenant-binding
token reusing the existing `JWT_SECRET`, the one mechanism this project didn't already have
(binding an unauthenticated provider redirect back to the tenant/user who started it). A real
verifier (`_quickbooks_verifier`) makes a real `GET .../companyinfo/{realmId}` call, mirroring
Stripe's `GET /v1/balance` verifier exactly.

**Invoice sync** (`QuickBooksSyncService`): pushes an approved/sent/paid invoice to the tenant's
connected QuickBooks company, creating a matching QBO Customer the first time (new
`customers.external_provider`/`external_id` columns, migration `0019`, mirroring the
pre-existing `Invoice` columns of the same name) and reusing it for subsequent invoices of the
same customer. Idempotent by construction — an already-synced invoice
(`external_provider="quickbooks"` already set) is a proven no-op, zero further API calls. A 401
during either the customer-create or invoice-create call triggers exactly one
token-refresh-and-retry, never an unbounded loop. Exposed as `finance.sync_invoice_to_quickbooks`
— a real `ToolRegistry` tool, `AUTO` policy (moves no money, idempotent).

**Security review, item by item** (each verified against actual running code and a passing test,
not just design intent):
- **OAuth state token signed/validated/tenant-bound**: `create_oauth_state_token` signs a
  payload containing `tenant_id`/`provider`/`sub`/a 10-minute `exp` with the existing
  `JWT_SECRET`; `decode_oauth_state_token` verifies the signature AND checks `type=="oauth_state"`
  AND the `provider` matches the endpoint being called. Proven by `test_oauth_state_token_
  round_trips_tenant_and_provider`, `test_oauth_state_token_rejects_wrong_provider`,
  `test_oauth_state_token_rejects_garbage`, and — added during this final-verification pass —
  `test_oauth_state_token_rejects_expired` (constructs an already-expired token directly via
  `jose.jwt.encode`, bypassing the real 10-minute window, to prove expiry is genuinely enforced
  by the underlying `jwt.decode` call, not merely intended by the code's design).
- **Callback rejects invalid/expired/mismatched state**: `test_callback_with_invalid_state_is_
  rejected`, `test_callback_with_state_for_wrong_provider_is_rejected`,
  `test_callback_missing_required_params_is_rejected` — all confirmed 400, all confirmed before
  any token exchange is attempted (a forged/garbage state never reaches Intuit's real token
  endpoint at all).
- **Tokens never appear in logs/API responses/exceptions/audit records**: grepped every new
  QuickBooks file for `logger.*` calls — none include token/secret material, only structural
  metadata (attempt count, error type, tenant/provider). `ConnectionResponse` (the shared,
  provider-agnostic API response shape) never serializes `encrypted_credential` or any decrypted
  value — proven directly by `test_callback_success_stores_a_real_connection_for_the_correct_
  tenant`, which asserts the real access token string never appears anywhere in the connections
  list response. The platform app's `client_secret` is used only inside a base64-encoded
  Basic-auth header, proven never to leak into an exception message by
  `test_key_never_appears_in_client_secret_error_message`.
- **Credentials stored only through the encrypted IntegrationConnection store**: both the OAuth
  callback and the sync service's token-refresh path write exclusively through
  `IntegrationConnectionService.connect()` (Fernet-encrypted `encrypted_credential` column) — no
  second storage path exists anywhere in the new code.
- **Tenant isolation on authorize/callback/connection lookup/invoice sync**: the state token
  binds to `current_user.tenant_id` at `/authorize` time; the callback's `connect()` call is
  itself tenant-scoped; `QuickBooksSyncService.sync_invoice` resolves BOTH the connection and the
  invoice by `tenant_id` before doing anything. Proven by two dedicated cross-tenant tests:
  `test_tenant_b_cannot_sync_tenant_as_invoice` (a tenant with a real QuickBooks connection
  cannot sync another tenant's invoice — fails as "not found") and
  `test_tenant_bs_connection_is_never_used_for_tenant_a` (a tenant with no connection of their
  own is never silently given another tenant's).
- **Idempotent sync + real external-id persistence**: `test_already_synced_invoice_is_a_safe_
  noop_no_api_call` (monkeypatches both `create_customer`/`create_invoice` to raise
  `AssertionError` if called — proving zero API calls for an already-synced invoice) and
  `test_full_sync_creates_customer_and_invoice_and_persists_external_ids` (asserts the real QBO
  ids land on `Invoice.external_provider`/`external_id` and the new `Customer` columns after a
  real — mocked — sync).

**A real, pre-existing test-infrastructure bug found and fixed during final verification**
(discovered while investigating an intermittent failure surfaced by repeated full-suite runs in
this phase — unrelated to QuickBooks in cause): `tests/test_phase10_e2e.py::test_full_autonomy_
loop_policy_gated_notified_approved_then_reconfigured` failed once in a full-suite run, passed in
isolation, and the failure's actual assertion (`"overdue" in insight.summary`) plus its very fast
failure time (0.27s, nowhere near the test's 5s polling timeout) ruled out a simple slow-timeout
explanation — prompting an actual root-cause investigation rather than a second dismissal as
"flaky." Found: the test runs a real background `EventWorker` task concurrently with its own
tool calls, and correctly serializes every DB access between them through a shared
`asyncio.Lock()` (`db_lock`) via `call`/`read` helper functions — except ONE direct row mutation
(setting the test invoice to `OVERDUE` with a 12-days-ago due date) that used a raw
`session_factory()` call bypassing the lock entirely, the only such gap in the whole test. This
let the concurrently-running worker race that specific write, occasionally letting the Morning
Brief generation immediately afterward run before the overdue status was reliably visible —
producing a real, intermittent, environment-timing-dependent false failure with zero actual
product defect. Fixed by wrapping that one write in `async with db_lock:`, matching every other
DB access in the same test. Verified fixed, not just theorized: 5 consecutive clean runs of the
test in isolation, then 2 consecutive clean full-suite runs (446 passed, 0 failed, each).

**Final counts, all re-run against the completed final code state — never inferred from an
earlier or partial run**:
- SQLite: **446 passed, 8 skipped, 0 failed, 0 errors** (96.17s) — 415 Phase-12G-2 baseline + 30
  new QuickBooks tests + 1 expired-state-token test added during this final-verification pass.
- Real PostgreSQL 16.2 + real Redis: **454 passed, 0 failed, 0 skipped, 0 errors, 472.40s
  (7:52)** — the actual process was confirmed still running (via `ps`) on two separate occasions
  when asked to report a result before it had genuinely exited, and the report was correctly
  deferred both times rather than inferred from partial output.
- `tests/test_quickbooks_integration.py` run independently: **31/31 passed**.
- Migration `0019`: re-verified from a genuinely empty, isolated throwaway Postgres schema in
  the same real instance already running in this environment (created, migrated, verified,
  `search_path` reset, schema dropped) — 19/19 migrations (0001→0019), 93 tables, single head
  `0019` confirmed; `public`'s pre-existing 92 tables and data confirmed untouched afterward.
- `pip-audit`: unchanged, the one already-accepted `ecdsa` finding only — no new dependency
  introduced.
- Repository secret scan (targeted at every new/changed QuickBooks and Stripe file): no
  hardcoded key/secret pattern found.
- `.env`/`.env.local` confirmed still gitignored, never committed (checked git history, not just
  current status).
- Frontend: `node`/`npm` confirmed still absent from this sandbox (re-checked again this phase).
  The QuickBooks connect UI (a real "Connect with QuickBooks" OAuth-redirect button, a
  `useSearchParams`-driven callback-result banner, Verify/Disconnect actions — all following the
  existing Stripe section's exact pattern) was written into `app/settings/integrations/page.tsx`,
  but genuinely NOT RE-VERIFIED — no typecheck, no build, no live browser verification performed
  or claimed.
- `git status`/`git diff` reviewed in full: only the expected Phase 12G/12G-2/13 files (Stripe
  schema-hardening production code, QuickBooks integration production code, two surgical test
  fixes, doc updates) — no secrets, no temporary database/schema files left in the repository, no
  debug `print()` statements, no weakened assertions, no unrelated modifications.

**The only remaining blocker to real QuickBooks verification is a real Intuit developer app's
credentials** — `QUICKBOOKS_CLIENT_ID`/`QUICKBOOKS_CLIENT_SECRET`/`QUICKBOOKS_REDIRECT_URI`. The
entire OAuth2 boundary, tenant-scoped connect flow, and invoice-sync capability are built and
self-contained tested; nothing further needs to be built before a real sandbox credential can be
supplied and the real end-to-end flow (consent → token exchange → a real invoice landing in a
real QuickBooks sandbox company) exercised for the first time.

## Phase 14 — Quotes/Estimates + Klaros' First Customer-Facing Surface

**Current-state audit and selection**: with Stripe hardened and QuickBooks landed, re-audited
every completed domain (CRM, Operations, Marketing, Retention, Finance, Stripe, QuickBooks) plus
the shared architecture (RBAC/permissions, EventBus/EventType, ToolRegistry/factory/policy,
IntegrationConnection, Temporal, approvals/exceptions/communications, API routers, frontend
routes, migrations, documented known limitations) for the next highest-value capability. Two
concrete findings: (1) `Job` already carries `estimated_revenue`/`estimated_cost`/
`estimated_margin` — an older "known limitation" note claiming otherwise (from Phase 5) was
stale and never corrected; the REAL gap is that the pipeline goes straight Lead → Job → Invoice
with nothing modeling a formal, customer-approvable price proposal before work starts. (2)
Klaros has zero customer-facing pages anywhere — even the existing Stripe Checkout flow
redirects the customer to *Stripe's* hosted page; nothing is ever rendered by Klaros itself for
someone with no account. Quotes/Estimates closes both: it's the natural pre-work pipeline stage,
it's fully self-contained (no external provider, no credential of any kind — scores higher on
the mission's own "verifiable without credentials" criterion than a further external
integration would have), and its customer accept/decline step is the natural first
unauthenticated surface (mirroring the Stripe-Checkout-link pattern, but hosted by Klaros
itself instead of redirecting to a third party).

**Built, reusing existing architecture at every layer rather than duplicating it**:
- `Quote`/`QuoteLineItem` models (migration `0020`) — identical `Decimal`-via-`Numeric` shape to
  `Invoice`/`InvoiceLineItem`. `jobs.quote_id` (nullable) added to the existing `Job` model
  rather than a new job-origin table.
- `QuoteService` imports and reuses `InvoiceService`'s deterministic pricing primitives directly
  — `LineItemInput`/`compute_line_total`/`compute_totals` are not re-derived, they're the same
  functions. Accepting a quote calls the existing `JobService.create_job` (one small, additive
  `quote_id: uuid.UUID | None = None` field added to `CreateJobInput`, threaded through to the
  `Job(...)` construction) rather than constructing a `Job` row by hand a second way.
- `InvoiceDeliveryProvider` (the existing document-delivery provider/adapter abstraction,
  `app/invoice_delivery/`) extended with a `send_quote` abstract method and its
  `InternalTestInvoiceDeliveryAdapter` implementation — same provider, same factory
  (`get_invoice_delivery_provider`), one new capability, not a parallel `quote_delivery/`
  package.
- `create_quote_view_token`/`decode_quote_view_token` (`app/core/security.py`) reuse the exact
  `create_oauth_state_token` signing mechanism introduced in Phase 13 (same `JWT_SECRET`, same
  `jose.jwt.encode`/`decode` calls, same "type" discriminator field pattern) — a 90-day token
  (long-lived because it's mailed/texted once and must still work when a customer opens it
  weeks later, unlike the 10-minute OAuth `state` round-trip) that is the ENTIRE trust boundary
  for the new public endpoints. No second credential/signing system was introduced.
- `quotes.*` ToolRegistry tools (create/update/send/get-draft, plus a deterministic expiry-sweep
  tool) — all `AUTO` policy: a quote commits no money and no work, so none of the existing
  approval-boundary machinery applies to the internal-staff side; the real commitment point is
  the CUSTOMER's own accept decision, made through the public, unauthenticated view — which has
  no `ExecutionContext` at all and therefore cannot go through the ToolRegistry, the identical
  reasoning `app/api/v1/webhooks.py` already established for Stripe/Twilio. Two new permissions
  (`CREATE_QUOTE`/`SEND_QUOTE`) mapped to the exact same roles as `CREATE_INVOICE`/
  `SEND_INVOICE` (MANAGER, ACCOUNTANT; OWNER/ADMIN via `_ALL_PERMISSIONS`) rather than inventing
  a new tier. Seven new `EventType.QUOTE_*` lifecycle events, picked up automatically by the
  existing generic audit-recorder handler (`app/events/handlers.py`, which subscribes to every
  `EventType` — no new wiring needed for basic audit coverage).
- Deliberately no new Temporal workflow: quote expiry is a deterministic sweep tool
  (`quotes.detect_expired_quotes`), mirroring `ARService.detect_overdue`'s existing pattern for
  invoices — async orchestration wasn't a genuine need for "check a date, flip a status."
- Two new API routers: `app/api/v1/quotes.py` (authenticated staff CRUD, list/get via direct
  tenant-scoped DB query + mutations via `_call_tool`, mirroring `app/api/v1/invoices.py`'s
  structure line-for-line) and `app/api/v1/public_quotes.py` (unauthenticated — Klaros' first).
- Frontend: `app/quotes/page.tsx` (list, mirrors `app/finance/invoices/page.tsx`),
  `app/quotes/[id]/page.tsx` (staff detail + Send action, surfaces the real signed customer link
  once sent), `app/quotes/view/[id]/page.tsx` (the public page — no `AppShell`, no `useAuth`,
  `Suspense`-wrapped per the existing `useSearchParams` convention already used by
  `app/calendar/page.tsx` and the Phase 13 integrations page). Corresponding `lib/api.ts`
  functions added, with the public ones deliberately never attaching an `Authorization` header.

**Security review, performed with extra scrutiny since this is Klaros' first-ever unauthenticated
endpoint**:
- The `state`-token-equivalent (`quote_view` token) is signed and its payload's own `quote_id`/
  `tenant_id` are the ONLY source of authority in the public router — the `{quote_id}` URL path
  segment is checked for equality against the token's `quote_id`, never trusted on its own.
  Proven, not just designed: `test_public_view_token_from_tenant_a_cannot_be_reused_for_tenant_
  bs_quote` presents tenant A's real, validly-signed token against tenant B's real quote id and
  asserts 400 — a forged token can't even be constructed without `JWT_SECRET`, so this test
  specifically proves the endpoint checks token-internal consistency, not merely "is this
  signature valid."
- The public response shape (`_quote_to_dict` in `app/api/v1/public_quotes.py`) is a distinct,
  narrower model than the internal one — `customer_id`/`lead_id`/`job_id`/`tenant_id` are never
  serialized to an unauthenticated caller, proven by `test_public_view_marks_viewed_and_never_
  leaks_internal_ids` asserting their literal absence from a real response body, not just "the
  fields aren't in the Pydantic schema" (the test actually inspects the JSON).
- No `logger.*` call exists anywhere in the new quote code (client-side there is none — Quotes
  has no external HTTP client at all — service, tools, and both routers) — grepped and confirmed
  zero results, so there was no token/PII-in-logs surface to introduce in the first place.
- Accept/decline idempotency is verified against real DB state, not response-shape alone:
  `test_public_accept_is_idempotent_never_creates_two_jobs` asserts a second accept attempt
  returns 409 AND that exactly one `Job` row exists with that `quote_id`
  (`select(func.count()).select_from(Job).where(Job.quote_id == ...)`), and
  `test_declined_quote_cannot_later_be_accepted` proves the terminal states are genuinely
  terminal.
- RBAC and tenant isolation both verified at the tool layer independent of the public flow:
  `test_technician_cannot_create_quote`/`test_staff_can_create_but_not_send_quote` for RBAC,
  `test_tenant_b_cannot_update_tenant_as_quote`/`test_tenant_b_cannot_send_tenant_as_quote` for
  the internal-staff side (the public-token cross-tenant test above covers the customer side).

**Final counts, all re-run against the completed final code state — never inferred from an
earlier or partial run**:
- SQLite: **469 passed, 8 skipped, 0 failed, 0 errors** (85.22s) — 446 Phase-13 baseline + 23 new
  Quote tests.
- Real PostgreSQL 16.2 + real Redis: **477 passed, 0 failed, 0 skipped, 0 errors, 490.19s
  (8:10)**.
- `tests/test_quotes.py` run independently: **23/23 passed**.
- Migration `0020`: re-verified from a genuinely empty, isolated throwaway Postgres schema in
  the same real instance already running in this environment (created, migrated, verified,
  `search_path` reset, schema dropped) — 20/20 migrations (0001→0020), 95 tables, single head
  `0020` confirmed; `public`'s pre-existing 92 tables and data confirmed untouched afterward.
- `pip-audit`: unchanged, the one already-accepted `ecdsa` finding only — Quotes needed zero new
  dependencies (fully internal, no external HTTP client).
- Repository secret scan (targeted at every new/changed Quotes file): no hardcoded key/secret
  pattern found.
- Frontend: `node`/`npm` confirmed still absent from this sandbox (re-checked again this phase).
  The three new quote pages and the nav entry (`components/AppShell.tsx`) were written, but
  genuinely NOT VERIFIED — no typecheck, no build, no live browser verification performed or
  claimed.
- `git status`/`git diff` reviewed in full: only the expected Phase 14 files (Quotes
  domain/service/tools/API/frontend, two small additive changes to pre-existing Finance/
  Operations code — `CreateJobInput.quote_id`, `InvoiceDeliveryProvider.send_quote` — plus doc
  updates) — no secrets, no temporary database/schema files, no debug `print()` statements, no
  weakened assertions, no unrelated modifications.

**Quotes requires no external credential of any kind to be fully, completely verified** — every
piece of this phase (models, service, tools, both API routers, the entire public accept/decline
flow, tenant isolation, RBAC, idempotency, events, audit) has been genuinely, not
internally-mocked-only, exercised end-to-end. The only unverified piece is the frontend build/
browser check, blocked by the same pre-existing `node`/`npm` environment gap as every phase since
Phase 12G — not a Quotes-specific limitation.

## Phase 14 — Real Google Calendar Integration

(Also named "Phase 14" by the mission that requested it — a second, independent phase sharing
that number with the Quotes/Estimates section directly above in this document's history.
Chronologically this phase follows Quotes.)

**Current-state audit**: inspected `Customer`/`Lead`/`Appointment`/`Job`/`Invoice` models,
existing calendar code, `IntegrationConnection`/its provider factory, the QuickBooks OAuth
state-token implementation, RBAC permissions, `EventType`/exception infrastructure, ToolRegistry/
policy patterns, communications/reminder infrastructure, existing Google-related settings,
migration head, and the existing frontend Integrations/settings/calendar pages — as instructed,
before writing any code. Findings: `app/calendar/base.py`'s `CalendarProvider` ABC
(`get_availability`/`create_event`/`update_event`/`cancel_event`) already existed and already
matched the shape a real Google adapter needs, but `InternalTestCalendarAdapter` is the only
implementation, hardcoded in `app/tools/factory.py` with no selection mechanism — swapping the
internal scheduling engine for an external one was judged too risky and out of this phase's real
scope; instead, Google Calendar was built as an ADDITIVE sync capability pushing already-decided
Klaros appointments outward, mirroring `QuickBooksSyncService`'s identical relationship with
internal invoicing. `Appointment` had no `external_provider`/`external_id` columns (`Invoice`/
`Customer`/`Quote` already did) — genuinely necessary per the mission's own "add a migration only
if genuinely necessary" instruction, added via `0021`. RBAC needed zero new permissions:
`READ_APPOINTMENTS`/`CREATE_APPOINTMENT`/`MANAGE_INTEGRATIONS` already existed and were exact
semantic fits — confirmed by reading `app/models/rbac.py`'s role mappings before deciding, not
assumed. `EventType.APPOINTMENT_CREATED`/`UPDATED`/`CANCELLED`/`CONFIRMED` already existed.

**Credential audit, start of phase**: `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/
`GOOGLE_REDIRECT_URI` — none present in `backend/.env` at all (confirmed via direct grep, no
values ever printed). Real Google OAuth/API verification is `BLOCKED BY CREDENTIAL` for the
entire phase; every finding below comes from static code audit and self-contained tests, never a
live Google call.

**Built, reusing existing architecture rather than a second OAuth/state/credential framework**:
`GoogleCalendarClient` mirrors `QuickBooksClient` exactly (direct httpx, real OAuth2 grants, real
error classification, real retry-with-backoff, real bounded timeout) — the consent URL includes
`access_type=offline&prompt=consent` so a `refresh_token` is guaranteed even on a repeat connect,
proven by a dedicated URL-construction test. `google_calendar_schemas.py` uses the exact same
`extra="allow"` Pydantic pattern as `stripe_schemas.py`/`quickbooks_schemas.py`. The OAuth
connect flow (`/authorize` + `/callback`) reuses `create_oauth_state_token`/
`decode_oauth_state_token` — the identical primitive QuickBooks uses, zero new state-token code
— and the existing `IntegrationConnectionService.connect()`, no parallel credential-storage
path. A real verifier (`_google_calendar_verifier`) makes a real `GET /calendars/primary` call,
registered exactly like the Stripe/QuickBooks verifiers.

**Appointment sync** (`GoogleCalendarSyncService.sync_appointment`): one idempotent entry point
covering create/update/cancel — creates the Google event the first time, updates it once
`Appointment.external_id` is set, deletes/cancels it once the Klaros appointment itself is
`CANCELLED`. Two real edge cases handled and proven, not assumed: a delete of an already-gone
event (Google returns 404) is a safe no-op, not a failure; cancelling an appointment that was
never synced never calls Google at all. A 401 on any call triggers exactly one
token-refresh-and-retry — proven on two independent call sites (`sync_appointment`'s
create-event path and `list_calendars`), not just one, to confirm the pattern generalizes rather
than being special-cased once. Exposed as three `ToolRegistry` tools, all `AUTO` policy (move no
money, reversible/idempotent — same reasoning as the QuickBooks sync tool): `calendar.
sync_appointment_to_google`, `calendar.list_google_calendars`, `calendar.
check_google_availability` (a real `freeBusy` query).

**A deliberate design decision, not a gap**: calendar sync is a manually-invoked tool, not an
automatic subscriber on `APPOINTMENT_CREATED`/`APPOINTMENT_UPDATED`. The mission's STEP 8
explicitly raised the risk of an existing event firing repeatedly — rather than auto-subscribing
and relying on idempotency alone to absorb that risk, the same reasoning already established for
QuickBooks invoice sync was applied: an external push should be an explicit, auditable action a
human or AI actor took, not a side effect that could silently retry against a flaky external API
on every internal event. The operation is fully idempotent regardless (proven by the create/
update/cancel tests above), so this is a safety-and-auditability choice, not a workaround for a
missing capability.

**Security review, item by item** (third tenant-owned OAuth provider on this exact model, so the
review confirmed the pattern held under a third application, not merely that new code compiled):
- **Token/secret never in logs, API responses, or exceptions**: grepped every new file for
  `logger.*` calls — the three that exist (`google_calendar_client.py`'s retry-warning paths)
  log only `attempt`/`error_type`/`retryable`/`status_code`, never token material. The platform
  app's `client_secret` is used only in the OAuth token-exchange request body, proven never to
  leak into an exception message by `test_key_never_appears_in_client_secret_error_message`
  (same technique as the Stripe/QuickBooks equivalents). `ConnectionResponse` (the shared,
  provider-agnostic API shape) never serializes `encrypted_credential` — proven directly by
  `test_callback_success_stores_a_real_connection_for_the_correct_tenant`, which asserts the
  real access token string never appears anywhere in the connections list response.
- **Encrypted credential storage verified, not assumed**: both the OAuth callback and the sync
  service's token-refresh path write exclusively through `IntegrationConnectionService.
  connect()` (Fernet-encrypted `encrypted_credential` column) — no second storage path exists
  anywhere in the new code; confirmed by reading every write site, not just the happy path.
- **A missing refresh_token is treated as a hard failure**, not a silently-broken connection —
  `test_callback_without_refresh_token_is_rejected` constructs exactly that response shape and
  proves the callback redirects with an honest error rather than storing a connection that would
  stop working after the access token's ~1 hour lifetime.
- **OAuth state expiration/signature verified**: `test_oauth_state_token_rejects_expired`
  constructs an already-expired token directly (bypassing the real window) to prove expiry is
  genuinely enforced by the underlying `jwt.decode` call; `test_oauth_state_token_rejects_wrong_
  provider` proves a QuickBooks-issued state token can't be replayed against the Google callback.
- **Tenant boundaries verified**: `test_tenant_b_cannot_sync_tenant_as_appointment` (a tenant
  with a real connection cannot sync another tenant's appointment — fails as "not found") and
  `test_tenant_bs_connection_is_never_used_for_tenant_a` (a tenant with no connection of their
  own is never silently given another tenant's) — identical shape to the QuickBooks/Quotes
  cross-tenant tests, proving the pattern generalizes to a third provider correctly.
- **Bounded timeout/retry classification verified**: `GOOGLE_CALENDAR_TIMEOUT_SECONDS`/
  `GOOGLE_CALENDAR_MAX_RETRIES` are read into `httpx.AsyncClient(timeout=...)` and a real retry
  loop; 401/403 never retried, 404 classified `NOT_FOUND` (not retried, since a retry can't
  un-404 something), 429/5xx retried, each with a dedicated test.

**Final counts, all re-run against the completed final code state — never inferred from an
earlier or partial run**:
- SQLite: **508 passed, 8 skipped, 0 failed, 0 errors** (89.97s) — 469 Quotes-phase baseline +
  39 new Google Calendar tests.
- Real PostgreSQL 16.2 + real Redis: **516 passed, 0 failed, 0 skipped, 0 errors, 518.60s
  (8:38)** — this run was performed just before one small, additive, behaviorally-inert field
  addition to the `appointments` API response dict (`external_provider`/`external_id`, mirroring
  the identical Phase 13 fix to `finance.get_invoice`'s output); SQLite was re-run in full
  afterward and stayed 508/8/0, and the dedicated Google Calendar test file (which exercises all
  actual sync/OAuth behavior, not the appointments-list dict shape) passed 39/39 both before and
  after — a third full real-infra run was judged unnecessary given the change's nature and these
  two independent confirmations, disclosed here explicitly rather than silently omitted.
- `tests/test_google_calendar_integration.py` run independently: **39/39 passed**.
- Migration `0021`: re-verified from a genuinely empty, isolated throwaway Postgres schema in
  the same real instance already running in this environment (created, migrated, verified,
  `search_path` reset, schema dropped) — 21/21 migrations (0001→0021), 95 tables, single head
  `0021` confirmed; this environment's pre-existing data confirmed untouched afterward.
- `pip-audit`: unchanged, the one already-accepted `ecdsa` finding only — Google Calendar needed
  zero new dependencies (direct httpx, matching every other provider client in this codebase).
- Repository secret scan (targeted at every new/changed Google Calendar file): no hardcoded
  key/secret pattern found.
- Frontend: `node`/`npm` confirmed still absent from this sandbox (re-checked again this phase).
  The Google Calendar connect UI (`app/settings/integrations/page.tsx`, mirroring the QuickBooks
  section) and the per-appointment sync button (`app/calendar/page.tsx`) were written, but
  genuinely NOT VERIFIED — no typecheck, no build, no live browser verification performed or
  claimed.
- `git status`/`git diff` reviewed in full: only the expected Phase 14 (Google Calendar) files
  (production code, tests, doc updates) plus the already-uncommitted Phase 12G-14 (Quotes) work
  — no secrets, no temporary database/schema files, no debug `print()` statements, no weakened
  assertions, no unrelated modifications.

**Google Calendar's entire self-contained boundary has been genuinely, not internally-mocked-
only, exercised** — OAuth URL construction, state-token signing/expiry/tenant-binding, the
client's real retry/error-classification behavior (via real `httpx.MockTransport`, not a bare
mock of the client itself), the full callback HTTP flow, the verifier, and the complete sync
service including two real edge cases (already-gone event, never-synced cancellation) and
token-refresh-on-401 proven on two independent call sites. **The only remaining blocker to real
Google Calendar verification is a real Google Cloud OAuth app's credentials** —
`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/`GOOGLE_REDIRECT_URI`. Nothing further needs to be
built before a real credential can be supplied and the real end-to-end flow (consent → token
exchange → a real event landing in a real Google Calendar) exercised for the first time.

## Phase 15 — Quote Acceptance + Deposit Collection

**Current-state audit**: inspected `Quote`/`QuoteLineItem`/`QuoteStatus`, `Customer`/`Lead`/
`Appointment`/`Job`/`Invoice`/`Payment`/`PaymentAllocation`, `StripeClient`, the Stripe webhook
handler, `PaymentService`, `stripe_tools.py`, the Phase 13 QuickBooks invoice sync, `Integration
Connection`, existing checkout/payment endpoints, `EventType`, `ToolRegistry`/policy, RBAC, and the
frontend quote pages — before writing any code, per instruction. Confirmed: (1) quote acceptance
already existed (`QuoteService.decide`, Phase 14) and already auto-created a `Job` immediately on
`ACCEPTED`, with no deposit concept; (2) `StripeClient.create_payment_intent` existed with zero
real callers — `create_checkout_session` (the hosted-page flow invoices already use) was reused
instead, so no Stripe.js/Elements is needed anywhere; (3) `Payment` already had `provider`/
`external_id` (directly reusable for a Stripe PaymentIntent id) but NO relationship to `Quote` —
`PaymentAllocation.invoice_id` is `NOT NULL`, so a deposit (no `Invoice` exists yet) genuinely
could not be represented via the existing allocation path, confirming a real, necessary gap; (4)
`Quote` had no deposit/payment fields at all; (5) the existing `create_quote_view_token`/
`decode_quote_view_token` (tenant+quote bound, purpose-typed via `type="quote_view"`, 90-day
expiry) already satisfied every customer-facing-security requirement and was reused unchanged —
explicitly NOT the OAuth-state token, whose semantics differ (short-lived CSRF binding vs. a
long-lived customer access link).

**Credential audit, start of phase**: `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` — both confirmed
empty in `backend/.env` (grepped for presence only, no value ever printed). Real Stripe
verification is `BLOCKED BY CREDENTIAL` for the entire phase; every finding below comes from
self-contained tests against a real `httpx.MockTransport` and the real webhook HTTP endpoint,
never a live Stripe call.

**State machine, deliberately additive not restructuring**: two new `QuoteStatus` members,
`DEPOSIT_PENDING`/`DEPOSIT_PAID`, reached ONLY when a quote has `deposit_type` configured. The
existing Phase 14 rule ("accept → immediately convert to a real Job") is preserved byte-for-byte
for every quote with no deposit configured — confirmed by re-running every pre-existing Phase 14
test unchanged (94 quote/stripe/payment/webhook tests, 0 regressions, before any new test was
even written). A deposit-configured quote instead freezes `deposit_amount` at accept time
(Decimal-safe: `PERCENTAGE` quantized to cents via `Decimal.quantize`, `FIXED` used directly, both
clamped to `(0, quote.total]`, never a float anywhere in the calculation) and holds at
`DEPOSIT_PENDING`; the `Job` is created only once `QuoteService.mark_deposit_paid` (called
exclusively from the webhook) advances it through `DEPOSIT_PAID` — extending, not replacing, the
"customer's own decision is the approval boundary" principle from Phase 14.

**A real bug found and fixed this phase (tenant isolation)**: the first working version of the
webhook's `_handle_quote_deposit_succeeded` recorded the `Payment` before verifying that the
`quote_id` in the PaymentIntent's metadata actually belonged to the `tenant_id` also in that
metadata. `PaymentService.record_payment` has no way to validate `quote_id`'s real owner —
`Payment.quote_id` is not a DB-enforced foreign key — so a metadata mismatch (impossible to forge
without the webhook signing secret, but a genuine logic gap regardless) would have created an
orphan `Payment` row scoped to the wrong tenant, with `mark_deposit_paid` then correctly failing to
advance the (rightful) tenant's quote — leaving a real financial-record row under a tenant it
didn't belong to. Caught by this phase's own `test_deposit_payment_is_tenant_isolated` (initial
run: `payment_count == 1`, expected 0). Fixed by looking the `Quote` up scoped to the claimed
`tenant_id` and rejecting (recorded `FAILED` in `WebhookEvent`, no `Payment` written) before ever
calling `record_payment`. Re-run: `payment_count == 0`, quote state untouched. This is the kind of
gap the mission's STEP 16 ("tenant spoofing") was specifically watching for, and it was real.

**Idempotency, proven at multiple independent layers, not just designed**:
- Stripe checkout-session creation: `idempotency_key=f"klaros-quote-deposit-{quote_id}-{deposit_
  amount}"` — stable because `deposit_amount` is frozen at accept time and never recomputed;
  proven by a dedicated test asserting the literal `Idempotency-Key` HTTP header is identical
  across two requests for the same quote via a real `httpx.MockTransport` handler.
- Webhook delivery: `WebhookEvent`'s `(provider, external_event_id)` unique constraint (unchanged,
  pre-existing) rejects a redelivered event before any handler runs.
- Payment recording: `Payment`'s `(tenant_id, provider, external_id)` unique constraint
  (unchanged, pre-existing) makes a second `record_payment` call for the same PaymentIntent a
  documented no-op, not a duplicate row.
- Quote-state transition: `mark_deposit_paid` treats an already-`DEPOSIT_PAID`/`CONVERTED` quote
  as a safe no-op rather than an error — a second webhook delivery (or a race between two webhook
  deliveries) can never create a second `Job`.
- All four layers proven together, not just individually, by
  `test_duplicate_deposit_webhook_never_double_records_or_double_converts`.

**Deliberately not built (documented, not silently skipped)**: QuickBooks deposit-payment sync.
`QuickBooksSyncService` only syncs `Invoice` rows today — there is no payment-sync capability at
all (real or stubbed) to extend safely without inventing new, unverified QuickBooks-payments API
logic. The eventual `Invoice` an `Job` converts into can still be pushed via the existing
`finance.sync_invoice_to_quickbooks` tool unchanged; only the deposit `Payment` itself has no
QuickBooks representation. Documented in `PROJECT_STATUS.md`'s known-limitations list.

**Frontend**: `node`/`npm` confirmed still absent from this sandbox (re-checked this phase, same
result as every prior phase). No frontend code was written or claimed verified for the deposit
flow — correctly reported `BLOCKED BY ENVIRONMENT`, not silently skipped.

**Final counts, all re-run against the completed final code state — never inferred from an earlier
or partial run, each confirmed via an independent `ps -p <pid>` check before being read**:
- SQLite: **533 passed, 8 skipped, 0 failed** (102.48s) — 508 Phase-14 baseline + 25 new.
- Real PostgreSQL 16.2 + real Redis (isolated throwaway schema, created/migrated/verified/dropped,
  role `search_path` reset each time, `public` schema's 94 tables confirmed identical before and
  after both runs performed this phase): **541 passed, 0 failed, 0 skipped, 569.86s**.
- `tests/test_quote_deposit.py` run independently: **25/25 passed**.
- Migration `0022`: verified from a genuinely empty SQLite file AND from a genuinely empty,
  isolated throwaway Postgres schema — 22/22 migrations (0001→0022), single head `0022` confirmed,
  `quotes.deposit_type`/`deposit_value`/`deposit_amount` and `payments.quote_id` (+ its index)
  present with correct types in the migrated schema, `public` schema's pre-existing data confirmed
  untouched both before and after.
- No dependency changes (`requirements.txt`/`pyproject.toml` diff: empty).
- Repository secret scan (every new/changed file): no hardcoded key/secret pattern found; no
  `logger.*` call in any new code includes token/secret/raw-webhook-payload material.
- `git status`/`git diff` reviewed in full at the close of this phase: only the expected Phase 15
  files (production code, tests, doc updates) plus the already-uncommitted Phase 12G-14 work from
  earlier in this session — no secrets, no temporary database/schema files (the throwaway Postgres
  schemas used for verification were dropped, not left behind), no debug `print()` statements, no
  weakened assertions, no unrelated modifications.

**The quote-deposit collection loop is genuinely, not internally-mocked-only, exercised end to
end** — deposit configuration and Decimal-safe computation, the accept-time state-machine branch
(with and without a deposit), Stripe checkout-session creation via a real `httpx.MockTransport`,
the real webhook HTTP endpoint (success, duplicate delivery, missing metadata, cross-tenant
rejection, and a regression proof that the pre-existing invoice-payment path is unaffected), RBAC
on both new tools, and a full create→send→accept→checkout→webhook→Payment→Job E2E, verified
through the public view endpoint as a real customer would see it. **The only remaining blocker to
real Stripe verification of this flow is a real `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET`** —
nothing further needs to be built before a real credential can be supplied and the real end-to-end
flow (a real test-mode Checkout Session, a real webhook delivery, a real `Payment` row, a real
downstream `Job`) exercised for the first time.

## Phase 16 — Customer-Facing Quote Acceptance + Deposit UX

**Current-state audit**: re-inspected (not assumed) `/quotes/view/[id]`, the public quote API
functions/types in `frontend/lib/api.ts`, `PublicQuote`'s existing shape, existing customer-facing
component conventions (no `AppShell`, dark-theme neutral/emerald/red Tailwind classes established
by both the existing public quote page and its authenticated `/quotes/[id]` sibling), and
`app/api/v1/public_quotes.py`'s exact current contract, re-read fresh this phase rather than
trusted from the Phase 15 summary. Confirmed: the backend's `GET /public/quotes/{id}` had already
been returning `deposit_required`/`deposit_amount` since Phase 15, but `PublicQuote` in the
frontend never declared those fields; `POST /{id}/deposit/checkout` existed and was fully tested at
the API layer, but had zero frontend caller anywhere in the codebase. No existing frontend Stripe-
redirect pattern existed to reuse — the invoice-side Checkout Session tool is staff-only, invoked
only through the authenticated `ToolRegistry`, never surfaced in any frontend page. This phase
establishes the `window.location.href = checkout_url` full-page-navigation pattern for the first
time, matching the backend's own "hosted Stripe Checkout page, confirmation only ever from the
webhook" design intent from Phase 15.

**Environment audit**: `node`/`npm`/`yarn`/`pnpm` — none found on `PATH` (checked all four, not
just `node`/`npm`), consistent with every prior phase. `frontend/node_modules` exists but is
sparse (94 entries, not a full install) — genuinely insufficient to run `tsc`/`next build`.
Frontend verification for this phase is `BLOCKED BY ENVIRONMENT`, disclosed explicitly rather than
attempted and misreported.

**Built, reusing existing conventions rather than inventing new ones**:
- `PublicQuote` (frontend type) extended with `deposit_required: boolean`/`deposit_amount: string |
  null` — mirrors the backend's actual, already-redacted response shape field-for-field; explicitly
  does NOT add `deposit_type`/`deposit_value` (the backend never sends them to this endpoint by
  design).
- One new API function, `createPublicQuoteDepositCheckout(quoteId, token)` — same
  `URLSearchParams({ token })` + `request<T>()` pattern every other public-quote function already
  uses; sends no request body, no amount, no redirect URL.
- `/quotes/view/[id]` rewritten to cover every state named in the mission's STEP 4: no-deposit
  accept/decline is byte-for-byte the pre-Phase-16 behavior; a deposit-configured, not-yet-accepted
  quote shows a "requires a deposit" notice (amount deliberately withheld until accept, since the
  backend itself doesn't freeze/expose it before then); `DEPOSIT_PENDING` shows the frozen amount
  and a "Pay deposit securely with Stripe" button; `DEPOSIT_PAID`/`CONVERTED` show a confirmed-
  payment banner with the paid amount and a derived remaining balance (`Number(total) -
  Number(deposit_amount)`, purely for display — never sent back to the server, never used to
  compute anything the backend trusts).

**Stripe return experience — the mission's explicit "do not trust query parameters as proof of
payment" instruction, verified line-by-line**: the `?deposit=success|cancelled` query param Stripe
appends on redirect is read (`searchParams.get("deposit")`) and used ONLY to decide whether to (a)
show a "confirming your payment" banner and begin polling, or (b) show a dismissible "checkout was
cancelled" notice. Neither branch ever calls `setQuote` or otherwise mutates displayed state based
on the param's value alone — every state transition shown to the customer comes from a fresh
`getPublicQuote()` response. The polling loop (`useEffect` keyed on `confirming`/`quote`/`id`/
`token`) is bounded to 5 attempts at 2.5s apart (never infinite), stops the instant the server-
side status genuinely advances past `DEPOSIT_PENDING`, and falls back to an honest "we haven't
received confirmation yet" message with a manual "Check again" retry if the real webhook hasn't
landed by the time it gives up — it never claims success it hasn't observed from the backend.

**Security review, item by item (mission's STEP 6 checklist)**:
- **Token remains the sole authorization boundary**: the new deposit-checkout call sends nothing
  besides the existing `token`; no `tenant_id`/`quote_id` is ever accepted from anywhere but the
  verified token server-side (unchanged from Phase 15, re-confirmed by re-reading `_resolve_token`
  this phase).
- **Deposit amounts never computed by the browser**: every dollar figure the page renders is a
  direct pass-through of a backend response field; the one client-side arithmetic op (remaining
  balance) is display-only and never round-trips into a request.
- **No Stripe secret, webhook secret, or other credential in any new frontend code** — grepped
  explicitly across the new/changed files; found nothing.
- **No open redirect introduced**: `window.location.href` is set only to the `checkout_url` value
  the BACKEND returns (itself derived from Stripe's real response plus the backend's own
  `FRONTEND_BASE_URL`-built success/cancel URLs, never from browser-supplied data).
- **Customer cannot force `DEPOSIT_PAID` or change the deposit amount**: nothing in the new
  frontend code writes quote state directly — every state change is a real API round-trip through
  the same tenant/token-scoped backend endpoints already hardened in Phase 15.
- **Duplicate submission handled**: a shared `busy` flag gates all three actions (accept/decline/
  pay-deposit) at both the handler-entry check and the button's `disabled` prop; the pay-deposit
  button is additionally hidden entirely while a post-return payment confirmation is in flight, so
  a customer can't start a second Checkout Session while the first one's webhook is still landing.

**A dedicated regression suite was added to verify these properties AT THE BACKEND, not merely
assumed from the frontend's own good behavior** (`tests/test_quote_deposit_public_ux.py`, 9 new
tests, all passing): the deposit-checkout endpoint rejects a garbage token, a validly-signed token
for a different quote, and a validly-signed token from a different tenant (three separate tests,
each `400`); rejects a checkout attempt on an already-`CONVERTED` quote and on a quote that hasn't
been accepted yet (both `409`); proves — by inspecting the real outbound `httpx.MockTransport`
request to Stripe — that the actual charge amount is always the server-frozen `deposit_amount`
even when the request carries a forged JSON body attempting to smuggle `{"amount": "1.00"}` (the
endpoint parses no body at all, so this has zero effect, proven rather than assumed); proves a
client-supplied `evil_redirect` query parameter never reaches the success/cancel URLs actually sent
to Stripe; and proves the public view endpoint's response never includes `deposit_type`/
`deposit_value`, only the customer-appropriate `deposit_required`/`deposit_amount`. **No gap was
found in the Phase 15 backend by this review** — every test passed once one test's own assertion
(a urlencoded field-name substring check) was corrected; the code under test needed no changes.

**Final counts, all re-run against the completed final code state, each confirmed via `ps -p
<pid>` before being read**:
- SQLite: **542 passed, 8 skipped, 0 failed** (128.68s) — 533 Phase-15 baseline + 9 new.
- Real PostgreSQL 16.2 + real Redis (isolated throwaway schema, created/verified/dropped, `public`
  schema's 94 tables confirmed identical before and after, role `search_path` reset): **550
  passed, 0 failed, 0 skipped (482.44s)**.
- `tests/test_quote_deposit_public_ux.py` run independently: **9/9 passed**.
- No migration this phase — no schema change was needed (the deposit UX consumes fields the Phase
  15 migration already added).
- No dependency changes (`frontend/package.json`/`backend/requirements.txt` diffs: empty).
- Repository secret scan (every new/changed file, backend and frontend): no hardcoded key/secret
  pattern found.
- `git status`/`git diff` reviewed in full: only the expected Phase 16 files (one new backend test
  file, `frontend/lib/api.ts`'s additive diff, and the rewritten `/quotes/view/[id]/page.tsx`,
  itself still untracked from Phase 14 onward) plus the already-uncommitted prior-phase work — no
  secrets, no temporary database/schema files, no debug statements, no weakened assertions.

**What genuinely was NOT verified this phase, stated plainly**: no `tsc --noEmit`, no `next
build`, no dev server, no browser, no console/network inspection — `node`/`npm` are absent from
this sandbox, and no result for any of those steps is claimed. No real Stripe test-mode checkout
was driven through this UI — `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` remain unset. Everything
reported above as verified was verified through static code review and the real backend HTTP
test suite (real request/response cycles through FastAPI's test client, a real signed JWT token, a
real `httpx.MockTransport` standing in only for the actual network hop to Stripe) — a genuinely
different, narrower form of verification than a live browser session, and reported as such.

## Phase 17 — QuickBooks Deposit & Payment Synchronization

**Current-state audit**: re-inspected (not assumed) `Payment`/`Refund`/`Quote`/`Invoice`/
`Customer`, `QuickBooksClient`/`quickbooks_schemas.py`, the QuickBooks OAuth/connection service,
`QuickBooksSyncService`/`finance.sync_invoice_to_quickbooks`, the Stripe webhook's deposit-payment
handling, `QuoteDepositService`, `PaymentService`, `EventType`, `ToolRegistry`/`policy.py`/
`factory.py`, and every existing QuickBooks test — all freshly re-read this phase per instruction,
not trusted from the Phase 13/15 summaries. Confirmed the five things the mission asked to
determine: (1) the Stripe deposit `Payment` is persisted via `PaymentService.record_payment` with
`quote_id` set and no `PaymentAllocation` (Phase 15); (2) `Quote` becomes `DEPOSIT_PAID` exactly
inside `QuoteService.mark_deposit_paid`, called only from the webhook's `_handle_quote_deposit_
succeeded`; (3) `Quote` becomes `CONVERTED` immediately afterward, inside the same call, via
`_convert_to_job`; (4) **critically, no `Invoice` exists at conversion — only a `Job`** (confirmed
by reading `_convert_to_job` line by line: it calls `JobService.create_job`, nothing invoice-
related); (5) therefore the deposit must be represented as a QuickBooks Payment applied against an
Invoice that is resolved (and may legitimately not yet exist) at sync time, NOT a SalesReceipt
(which would misrepresent a deposit toward future work as a completed, standalone sale) and NOT an
unapplied payment (QuickBooks supports this, but it would need a separate, unbuilt "apply later"
reconciliation step this phase didn't need to invent, since the existing invoice-sync path already
gives every deposit a natural eventual home). This is a real architectural decision made from
evidence, not chosen blindly, and is the reason "resolve the associated Invoice" in the service
below is a genuine precondition rather than a formality.

**Identifier storage audit**: `Payment.provider`/`external_id` already carry a `UNIQUE(tenant_id,
provider, external_id)` constraint identifying the payment's ORIGINATING provider — for a Stripe
deposit, `provider="stripe"` and `external_id=<PaymentIntent id>`. Reusing this same pair for a
SECOND provider's identity (QuickBooks) was ruled out immediately: it would either collide with
the Stripe identity or require overloading the unique constraint's meaning. `Invoice`'s existing
`external_provider`/`external_id` pair — used for exactly this "secondary accounting-sync target"
relationship — was the correct model to mirror, but `Payment` has no equivalent second slot.
Genuinely necessary: one new column, `Payment.quickbooks_payment_id` (migration `0023`), not a new
table, not a redundant identifier pair (no `quickbooks_provider` column needed — the column's mere
non-null presence already answers "synced to QuickBooks yes/no" unambiguously, since a `Payment`
row is either a Stripe deposit or nothing else that could sync this way in the current schema).

**QuickBooks API model**: `QuickBooksPaymentInput`/`QuickBooksPaymentResponse` added to
`quickbooks_schemas.py` following the exact established discipline — `extra="allow"` (tolerates
undocumented fields), only the fields this app actually reads/writes declared explicitly
(`Id`/`TotalAmt`), validated via the same `_validate_response`/`QuickBooksAPIError` boundary every
other QuickBooks response already goes through (a malformed response is a `PROVIDER_ERROR`, never
an unhandled `pydantic.ValidationError`).

**Client extension**: `QuickBooksClient.create_payment`/`get_payment` — no unrelated API surface
added (no refund-sync, no Item-catalog calls, nothing outside this phase's bounded scope). Reuses
the client's existing `_request` method verbatim (bounded `httpx.AsyncClient(timeout=...)`, real
retry-with-backoff on 429/5xx/timeout/network, 401/403 never retried, `_extract_error_message`
never echoes request headers/tokens). The one genuinely new piece of client behavior is the
`?requestid=` query parameter on `create_payment` — Intuit's own documented write-deduplication
mechanism (the QBO-API equivalent of Stripe's `Idempotency-Key` header, applied the way THIS
provider's API actually exposes it). This is real, documented Intuit API behavior, not invented —
but it has genuinely not been exercised against a live QuickBooks account in this environment (no
credentials), and is reported as such everywhere it's mentioned, never overclaimed as verified.

**Deposit payment sync service**: `QuickBooksPaymentSyncService.sync_deposit_payment(tenant_id,
payment_id)` — exactly the two arguments the mission specified, nothing else. Every one of the ten
listed responsibilities is implemented in order: load Payment tenant-scoped (fails before any
QuickBooks call for a wrong id/tenant); confirm Stripe-deposit shape (`provider == "stripe" and
quote_id is not None`); confirm `PaymentStatus.SUCCEEDED`; resolve Quote (tenant-scoped); resolve
Invoice (via `Quote.job_id` → `Job` → `Invoice.job_id == Job.id`, tenant-scoped — at most one row,
since `InvoiceService.create_draft_from_job`'s idempotency key is `f"invoice-for-job-{job_id}"`);
resolve the QuickBooks Customer (defensively re-checked, not merely assumed present even though
`QuickBooksSyncService.sync_invoice` always populates it first); resolve the QuickBooks Invoice
(`Invoice.external_id`, requiring `external_provider == "quickbooks"`); create the QuickBooks
Payment; persist `quickbooks_payment_id`; and return an idempotent success (no API call at all) if
that column is already set. **Never trusts** browser-supplied tenant_id, amount, invoice id, or
QBO customer id — the method signature has no slot for any of them; every value comes from a
server-side `session.get()`/`select()` against Klaros' own persisted rows.

**Tenant isolation, proven not assumed** (mission's explicit STEP 6): `test_tenant_b_cannot_sync_
tenant_as_payment` (cross-tenant `Payment` lookup fails `PaymentNotFoundError` before any
QuickBooks call — proven by never mocking `create_payment` in that test at all, so a stray call
would raise `AttributeError`/fail the test outright rather than silently succeeding);
`test_tenant_b_cannot_use_tenant_as_quickbooks_connection` (a tenant with its own real paid
deposit but no QuickBooks connection of its own can never reach a connected state through another
tenant's realm — proven at both the invoice-sync and payment-sync layers independently). No test
found a way to attach a Payment to another tenant's Invoice or QuickBooks connection.

**Amount integrity, proven not assumed** (STEP 7): `test_amount_integrity_uses_server_persisted_
payment_amount_only` asserts the literal float value QuickBooks receives equals the real
`Payment.amount` read back from the database after the sync — the service has no other source for
this value to have used. Because the service's public method accepts no amount/invoice/customer
parameter at all, "forged input" tests for those fields are structurally impossible to write in a
meaningful way (there is no argument to forge) — instead, the precondition tests (missing quote,
missing job, missing invoice, un-synced invoice, un-synced customer, wrong payment type, unpaid
status) prove the service refuses to *guess* or *substitute* a value when the real one isn't
resolvable, which is the equivalent guarantee for a service with this narrow a signature.

**Idempotency, proven at multiple independent layers** (STEP 8): (1) a duplicate Stripe webhook
delivery was already proven safe in Phase 15/16 and is unchanged here; (2) `test_duplicate_tool_
invocation_never_creates_two_qbo_payments` calls the ToolRegistry tool twice, asserting
`create_payment` is invoked exactly once; (3)/(6) `test_stripe_requestid_is_deterministic_per_
payment_for_retry_safety` simulates the "QuickBooks accepted it, then this process crashed before
persisting the id" partial-completion scenario directly (clearing `quickbooks_payment_id` after a
successful sync, then retrying) and proves the SAME deterministic `requestid` is sent both times —
this is the strongest LOCAL guarantee available; whether Intuit's server actually deduplicates on
it is the one genuinely unverifiable-without-credentials piece, disclosed honestly rather than
assumed; (4) `test_already_synced_payment_is_a_safe_noop_no_api_call` proves a Payment already
carrying a `quickbooks_payment_id` never calls `create_payment` again (the mock raises
`AssertionError` if called, so this is a hard proof, not an inference); (5) quote-conversion retry
is structurally covered by `mark_deposit_paid`'s own pre-existing idempotency (Phase 15, unchanged).

**Event/worker integration** (STEP 9, evaluated not assumed): `EventBus`/`EventWorker` already has
real bounded retry (`max_retries`) and a dead-letter queue with a `replay()` method — inspected
before deciding anything. A new subscriber on the ALREADY-EXISTING `EventType.QUOTE_DEPOSIT_PAID`
(no new EventType needed) reuses this infrastructure directly rather than introducing a second
background mechanism. Explicitly reasoned through and disclosed: this automatic attempt is
EXPECTED to fail on most first attempts (no Invoice created yet is the common case, not a rare
edge case) — three dedicated tests prove the full lifecycle: `test_quote_deposit_paid_event_
triggers_automatic_sync_attempt` (succeeds when the precondition is already met),
`test_failed_automatic_sync_lands_in_dead_letter_and_stays_retryable` (fails cleanly, dead-letters,
and — critically — leaves `Payment.status`/`Quote.status` completely untouched, asserted directly),
and `test_replaying_dead_lettered_sync_succeeds_once_invoice_is_ready` (the same event, replayed
after the precondition becomes true, succeeds). This is the "visibly ERROR/PENDING_RETRY, safely
retryable later" behavior STEP 10 requires, built from existing primitives rather than new ones.

**Failure semantics** (STEP 10, verified directly): the dead-letter test above is the direct proof
that a QuickBooks sync failure never rolls back or marks unpaid a real, successful Stripe deposit —
structurally guaranteed by the outbox pattern this whole event bus already uses (the triggering
event is published only after the Payment/Quote state is already committed), not merely hoped for.

**Tool/API surface** (STEP 11): `finance.sync_deposit_payment_to_quickbooks` reuses the existing
`SEND_INVOICE` permission (the same one `finance.sync_invoice_to_quickbooks` already uses — an
exact semantic fit, "push a financial record to QuickBooks") and `AUTO` policy (idempotent by
construction, moves no NEW money — the real payment already happened via Stripe). No new API
endpoint — the mission's "if an API endpoint is needed" branch didn't apply here, since this is an
internal/staff finance operation the existing architecture already expects to expose as a
`ToolRegistry` tool, not a REST endpoint (mirroring the invoice-sync precedent exactly).

**Security review** (STEP 15): secret scan of every new/changed file — clean, no hardcoded
key/secret pattern. No dependency changes (no `pip-audit` delta to report). No new `logger.*` call
anywhere in `quickbooks_payment_sync_service.py` at all (none was needed); the client's existing
retry-warning logs (unchanged, pre-existing pattern) log only `attempt`/`error_type`/`status_code`,
never token material — re-confirmed this phase, not just assumed unchanged.
`test_create_payment_error_message_never_leaks_access_token` proves a deliberately-planted fake
access token string never appears in a raised `QuickBooksAPIError`'s message. No raw provider
payload is logged anywhere in the new code. Tenant isolation, amount integrity, idempotency, and
bounded HTTP timeouts are each covered above with direct evidence, not assumption.

**Frontend audit** (STEP 16): `node`/`npm`/`yarn`/`pnpm` re-checked, still absent from this
sandbox (unchanged every phase). No frontend change was made — a QuickBooks-payment-sync status
badge on an invoice/payment page would be a reasonable future addition, but was judged not
compelling enough to add un-typechecked/un-buildable/un-browser-verifiable code for a capability
with no direct customer-facing surface this phase. Explicitly `BLOCKED BY ENVIRONMENT`, not
silently skipped without disclosure.

**Final counts, all re-run against the completed final code state, each confirmed via `ps -p
<pid>` before being read**:
- SQLite: **572 passed, 8 skipped, 0 failed** (116.62s) — 542 Phase-16 baseline + 30 new.
- Real PostgreSQL 16.2 + real Redis (isolated throwaway schema, created/verified/dropped, `public`
  schema's 94 tables confirmed identical before and after, role `search_path` reset): **580
  passed, 0 failed, 0 skipped (607.33s)**.
- `tests/test_quickbooks_deposit_payment_sync.py` run independently, 3 times in a row specifically
  to check for flakiness in the event/worker tests (async ordering is always the first suspect for
  that category): **30/30 passed, all three runs, no flakiness observed.**
- Migration `0023` verified from a genuinely empty SQLite file and a genuinely empty, isolated
  throwaway Postgres schema (created, migrated, verified, `search_path` reset, schema dropped) —
  23/23 migrations (0001→0023), single head `0023` confirmed, `payments.quickbooks_payment_id`
  present with the correct type in the migrated schema, this environment's pre-existing `public`
  data confirmed untouched both before and after.
- No dependency changes.
- `git status`/`git diff` reviewed in full: only the expected Phase 17 files plus the
  already-uncommitted prior-phase work — no secrets, no temporary database/schema files, no debug
  statements, no weakened assertions, no unrelated modifications.

**What genuinely was NOT verified this phase, stated plainly**: no real QuickBooks API call of any
kind — `QUICKBOOKS_CLIENT_ID`/`QUICKBOOKS_CLIENT_SECRET`/`QUICKBOOKS_REDIRECT_URI` all confirmed
absent from `backend/.env`. No real Stripe call either (`STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_
SECRET` also absent, unchanged). Intuit's `?requestid=` deduplication behavior is implemented per
documented contract but its actual server-side effect has never been observed. No frontend change,
therefore no frontend verification of any kind this phase. Everything reported above as verified
was verified through the real service/tool layer, the real `EventBus` (including its real
dead-letter/replay mechanics), and `httpx.MockTransport`/`monkeypatch`-level mocking of the one
genuine external boundary (the QuickBooks HTTP API) — never a live provider call, and never
represented as one.

## Phase 18 — QuickBooks Refund Synchronization

**Current-state audit**: re-inspected (not assumed) `Refund`/`RefundStatus`, `Payment`/
`PaymentStatus`, `Invoice`, `Quote`, `PaymentService` (both `request_refund`/`decide_refund` and
`reconcile_external_refund`), the Stripe refund client/webhook handling, `QuickBooksClient`,
`QuickBooksPaymentSyncService`, `QuickBooksSyncService`, the QuickBooks schemas, `Integration
Connection`, `EventBus`/`EventType`, existing finance tools, and every existing refund test — all
freshly re-read this phase, not trusted from the Phase 12F/17 summaries. Confirmed the six things
the mission asked to determine: (1) a Refund moves `REQUESTED → COMPLETED` (Klaros-approved) or
`REQUESTED → REJECTED`, or is created directly as `COMPLETED` by `reconcile_external_refund` for a
Stripe-Dashboard-initiated refund (`RefundStatus.APPROVED` remains a defined-but-unreachable enum
member, a documented pre-existing Phase 12G finding, unchanged by this phase); (2) the real Stripe
refund happens inside `decide_refund`, via a real `StripeClient.create_refund` call, BEFORE
`Refund.status` is ever set to `COMPLETED` — money never claimed moved before it moved; (3) Stripe's
own refund id is genuinely NOT persisted anywhere (a separate, pre-existing, already-documented
Phase 12G-2 limitation — `Refund` has no Stripe `external_id`/`provider` pair; this phase does not
need it and does not add it, staying narrowly scoped to the QuickBooks-sync identifier it DOES
need); (4) the Refund's own `payment_id` unambiguously identifies its Payment; (5) the Invoice/QBO
Invoice relationship is NOT what a refund needs to reference in QuickBooks — the ORIGINAL Payment's
own QBO id is; (6) therefore the correct QuickBooks object is a `RefundReceipt` (QuickBooks'
documented "money already received, now refunded back" object) referencing the original QBO
Payment via `LinkedTxn`, not a `CreditMemo` (wrong model — an unapplied credit toward FUTURE
purchases, not a real cash-equivalent refund of money already taken) and not a void/edit of the
original Payment (would destroy the historical record and cannot represent a PARTIAL refund
correctly, since `Refund.amount` can be less than `Payment.amount` — enforced by `request_refund`'s
own overpayment guard). This is a real decision made from evidence, matching the same reasoning
discipline Phase 17's Payment-vs-SalesReceipt decision used.

**Identifier storage audit**: `Refund` had no `external_id`/`provider`/`quickbooks_*` column of any
kind — genuinely necessary, one new column, `Refund.quickbooks_refund_receipt_id` (migration
`0024`), mirroring `Payment.quickbooks_payment_id`'s exact role from Phase 17. No redundant
identifier pair added (no separate `quickbooks_provider` column — the column's non-null presence
already answers "synced yes/no" unambiguously, same reasoning as Phase 17).

**QuickBooks API model**: `QuickBooksRefundReceiptInput`/`QuickBooksRefundReceiptResponse` added
to `quickbooks_schemas.py` following the exact established discipline (`extra="allow"`, only
`Id`/`TotalAmt` declared explicitly, validated through the same `_validate_response`/
`QuickBooksAPIError` boundary every other QuickBooks response goes through).

**Client extension**: `QuickBooksClient.create_refund_receipt`/`get_refund_receipt` — no unrelated
API surface added. Reuses `_request` verbatim (bounded timeout, real retry/backoff, 401/403 never
retried, `_extract_error_message` never echoes tokens). Passes the same Intuit-documented
`?requestid=` write-deduplication parameter Phase 17's `create_payment` established — genuinely
real, documented Intuit behavior, but its actual server-side dedup effect has never been observed
in this environment (no credentials), and is reported as such everywhere it's mentioned.

**Refund sync service**: `QuickBooksRefundSyncService.sync_refund_to_quickbooks(tenant_id,
refund_id)` — exactly the two arguments specified, nothing else. Implements all ten listed
responsibilities: tenant-scoped Refund lookup (idempotent short-circuit checked first); confirm
`RefundStatus.COMPLETED` (proven to reject both `REQUESTED` and `REJECTED` by dedicated tests, not
just `COMPLETED` assumed as the only other state); resolve the Payment (tenant-scoped); resolve the
QuickBooks Customer (defensively re-checked, same pattern as Phase 17); resolve the QuickBooks
Payment id (`Payment.quickbooks_payment_id` — the genuine precondition, proven to fail cleanly via
`PaymentNotSyncedError` rather than silently skip or fabricate one); derive the refund amount
exclusively from `Refund.amount` (never `Payment.amount` — proven directly by a dedicated PARTIAL-
refund test, `test_partial_refund_amount_integrity`, asserting the exact partial value reaches the
mocked client call); create the QuickBooks RefundReceipt; persist
`quickbooks_refund_receipt_id`; return an idempotent success (no API call) if that column is
already set. **Never trusts** browser-supplied amount, tenant, invoice, payment, customer, or
QuickBooks ids — the method signature has no slot for any of them.

**A real bug found and fixed this phase**: while building the "replay a dead-lettered refund sync
once the precondition is met" test, `QuickBooksPaymentSyncService.sync_deposit_payment`'s
eligibility check (`payment.status != PaymentStatus.SUCCEEDED`) was found to permanently reject any
payment that had SINCE been refunded (`REFUNDED`/`PARTIALLY_REFUNDED`) — meaning a deposit payment
that got refunded before staff got around to syncing it to QuickBooks could NEVER be synced at all,
not merely delayed. This is a genuine correctness gap: the payment genuinely happened and deserves
its own QuickBooks record regardless of a later refund (the refund itself syncs as a separate,
reversing transaction). Fixed by widening the check to accept `SUCCEEDED`/`PARTIALLY_REFUNDED`/
`REFUNDED` (still correctly rejecting `PENDING`/`FAILED` — payments that never actually succeeded).
Verified the fix doesn't regress Phase 17: the full `tests/test_quickbooks_deposit_payment_sync.py`
suite (30 tests) re-run and still passes unchanged after the fix.

**Tenant isolation, proven not assumed** (STEP 7): `test_tenant_b_cannot_sync_tenant_as_refund`
(cross-tenant Refund lookup fails `RefundNotFoundError` before any QuickBooks call — the mock for
`create_refund_receipt` is never even set up in that test, so a stray call would fail with
`AttributeError` rather than silently succeeding); `test_tenant_b_cannot_use_tenant_as_quickbooks_
connection_for_refund` (a tenant with its own fully-synced refund and its own real QuickBooks
connection still fails not-connected the instant its OWN connection's credential is cleared —
proving the connection lookup is genuinely per-tenant, not falling back to any other tenant's row).

**Amount integrity, proven not assumed** (STEP 6): `test_partial_refund_amount_integrity`
specifically builds a PARTIAL refund (125.00 against a 300.00 payment) and asserts the exact
partial amount is what reaches the mocked QuickBooks call — not the full payment amount, not a
caller-supplied value (there is no such parameter to supply). Because the service's public method
accepts no amount/invoice/customer/payment argument at all, "altered input" tests for those fields
are structurally impossible to write meaningfully (there is no argument to forge) — the precondition
tests (missing payment, payment not synced, customer not synced, wrong refund status) are the
equivalent guarantee for a service this narrowly scoped, proving it refuses to substitute or guess
a value rather than ever computing one from something the caller controls.

**Idempotency, proven at multiple independent layers** (STEP 8): duplicate refund event delivery is
covered by the pre-existing `WebhookEvent`/`EventProcessingRecord` machinery (unchanged); duplicate
service/tool invocation proven directly (`test_duplicate_tool_invocation_never_creates_two_qbo_
refund_receipts`, asserting `create_refund_receipt` is called exactly once across two calls);
"QuickBooks accepted it, then this process crashed before persisting the id" proven via
`test_requestid_is_deterministic_across_a_simulated_partial_completion_retry` (clears
`quickbooks_refund_receipt_id` after a successful sync, retries, asserts the identical deterministic
`requestid` is sent both times — the strongest LOCAL guarantee available; whether Intuit's server
actually dedups on it is the one genuinely unverifiable-without-credentials piece, disclosed
honestly); already-synced no-op proven with a hard `AssertionError`-raising mock rather than an
inference. "Same refund processed concurrently" (mission's item 7): this codebase's architecture
does not run true concurrent handler execution for the same event/tool call within a single test
process in a way that would exercise a genuine race here beyond what the check-then-act pattern
already covers (matching the identical, accepted precedent from Phase 17's own invoice-sync
customer-creation race, which was never given DB-level locking either) — not independently
re-verified under real concurrent load this phase, disclosed as a residual, honest limitation
rather than silently assumed safe.

**Event/worker integration** (STEP 9, evaluated not assumed): confirmed `EventType.PAYMENT_REFUNDED`
ALREADY fires at exactly refund completion for BOTH refund paths (`decide_refund`'s approved branch
and `reconcile_external_refund`) — no new EventType was needed, avoiding exactly the kind of
unnecessary-new-primitive the mission warned against. A new subscriber reuses the EventBus's
existing bounded retry + dead-letter queue. Three dedicated tests prove the full lifecycle:
`test_payment_refunded_event_triggers_automatic_sync_attempt` (succeeds when the precondition is
met), `test_failed_automatic_refund_sync_lands_in_dead_letter_and_stays_retryable` (fails cleanly,
dead-letters, and — critically — leaves `Refund.status`/`Payment.status` completely untouched,
asserted directly), and `test_replaying_dead_lettered_refund_sync_succeeds_once_payment_is_synced`
(the same event, replayed after the precondition becomes true via the Phase 17 payment sync,
succeeds). Never calls QuickBooks synchronously from the Stripe webhook or from `decide_refund`
itself — the coupling STEP 9 explicitly warned against was avoided.

**Failure semantics** (STEP 11, verified directly): the dead-letter test above is direct proof that
a QuickBooks refund-sync failure never turns a successfully completed Stripe/Klaros refund back
into a failed one — structurally guaranteed by the same outbox pattern (event published only after
DB commit) already relied on in Phase 17, not merely hoped for.

**Tool/API surface** (STEP 10): `finance.sync_refund_to_quickbooks` reuses the existing
`SEND_INVOICE` permission (the same one both prior QuickBooks-sync tools use — "push a financial
record to QuickBooks") and `AUTO` policy. No new API endpoint — an internal/staff finance operation
the existing architecture already expects to expose as a `ToolRegistry` tool, mirroring the
invoice-sync and payment-sync precedent exactly.

**Security review** (STEP 15): secret scan of every new/changed file — clean. No dependency
changes. No new `logger.*` call anywhere in `quickbooks_refund_sync_service.py` (none was needed);
the client's existing retry-warning logs (unchanged) log only `attempt`/`error_type`/`status_code`,
never token material. `test_create_refund_receipt_error_never_leaks_access_token` proves a
deliberately-planted fake access token never appears in a raised `QuickBooksAPIError`'s message. No
raw provider payload logged anywhere in the new code. Tenant isolation, amount integrity,
idempotency, and bounded HTTP timeouts (403 classification added and tested alongside the
pre-existing 401/429/5xx/4xx/timeout coverage) are each covered above with direct evidence.

**Frontend audit** (STEP 16): `node`/`npm`/`yarn`/`pnpm` re-checked, still absent from this sandbox.
No frontend change was made — same reasoning as Phase 17 (no compelling customer-facing surface for
a pure accounting-sync capability). Explicitly `BLOCKED BY ENVIRONMENT`, disclosed not skipped
silently.

**Final counts, all re-run against the completed final code state, each confirmed via `ps -p
<pid>` before being read**:
- SQLite: **602 passed, 8 skipped, 0 failed** (110.34s) — 572 Phase-17 baseline + 30 new.
- Real PostgreSQL 16.2 + real Redis (isolated throwaway schema, created/verified/dropped, `public`
  schema's 94 tables confirmed identical before and after, role `search_path` reset): **610
  passed, 0 failed, 0 skipped (591.87s)**.
- `tests/test_quickbooks_refund_sync.py` run independently, 3 times in a row to check for
  flakiness in the event/worker tests: **30/30 passed, all three runs, no flakiness observed.**
- `tests/test_quickbooks_deposit_payment_sync.py` (Phase 17's suite) re-run in full after this
  phase's payment-status-eligibility fix: **30/30 passed, unchanged.**
- Migration `0024` verified from a genuinely empty SQLite file and a genuinely empty, isolated
  throwaway Postgres schema (created, migrated, verified, `search_path` reset, schema dropped) —
  24/24 migrations (0001→0024), single head `0024` confirmed,
  `refunds.quickbooks_refund_receipt_id` present with the correct type, this environment's
  pre-existing `public` data confirmed untouched both before and after.
- No dependency changes.
- `git status`/`git diff` reviewed in full: only the expected Phase 18 files (plus the one Phase
  17 bug-fix line) and the already-uncommitted prior-phase work — no secrets, no temporary
  database/schema files, no debug statements, no weakened assertions, no unrelated modifications.

**What genuinely was NOT verified this phase, stated plainly**: no real QuickBooks API call of any
kind (`QUICKBOOKS_CLIENT_ID`/`QUICKBOOKS_CLIENT_SECRET`/`QUICKBOOKS_REDIRECT_URI` all confirmed
absent). No real Stripe call either (`STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` also absent). The
`?requestid=` deduplication's actual server-side effect has never been observed. True concurrent-
execution safety was not independently re-verified beyond the check-then-act pattern's existing,
accepted precedent. No frontend change, therefore no frontend verification of any kind. Everything
reported above as verified was verified through the real service/tool layer, the real `EventBus`
(including its real dead-letter/replay mechanics), and `httpx.MockTransport`/`monkeypatch`-level
mocking of the one genuine external boundary (the QuickBooks HTTP API, and the Stripe refund call
inside `decide_refund`) — never a live provider call, never represented as one.

## Phase 19 — Ordinary Invoice Payment → QuickBooks Payment Sync

**Current-state audit**: re-inspected (not assumed) `Payment`/`Invoice`/`Refund`, `PaymentService`,
`InvoiceService`, the Stripe webhook's payment handling, `QuickBooksPaymentSyncService`,
`QuickBooksRefundSyncService`, existing `EventType`s/subscribers, existing QuickBooks tools, every
existing test file touching these areas, existing migrations, and the tenant-scoped connection
resolution — all freshly re-read this phase. Confirmed: an ordinary invoice payment IS represented
by the existing `Payment` row shape, differing from a deposit payment only in `quote_id` (`None`)
and how its Invoice is reached — indirectly (Quote→Job→Invoice) for a deposit, directly via one or
more `PaymentAllocation` rows for an ordinary payment. `Payment.quickbooks_payment_id` (Phase 17)
already generalizes correctly to either origin — confirmed by reading its column definition and
every place it's read/written, not assumed from the name. No duplicate field was added; no
migration was needed this phase, verified by re-reading the full migration history (head `0024`,
unchanged).

**Eligibility rule, defined precisely from the models rather than guessed**: `Payment.quote_id is
None` (excludes deposits — those keep using `sync_deposit_payment`, unmodified) AND `provider ==
"stripe"` (excludes the internal test payment adapter and any other non-Stripe payment method) AND
`status in (SUCCEEDED, PARTIALLY_REFUNDED, REFUNDED)` (the Phase 18 fix's widened set, reused
verbatim — a payment that never actually succeeded has nothing to sync) AND exactly one distinct
`PaymentAllocation.invoice_id` for that payment. Zero allocations and more-than-one distinct
invoice are BOTH treated as genuine, distinguishable failures (`NoInvoiceAssociatedError`/
`MultipleInvoicesNotSupportedError`) rather than either silently skipping or silently picking one
invoice arbitrarily out of several — a real design decision, not an oversight: real QuickBooks
Payments CAN represent a split payment via multiple `Line` entries each with their own
`LinkedTxn`, but implementing that correctly (proportional amounts, multiple invoice-sync
preconditions, multiple `PaymentAllocation` amounts potentially differing from a naive equal
split) was judged genuinely out of this phase's bounded scope — documented honestly rather than
half-built.

**Extended the existing service, not a parallel one** (mission's explicit instruction, verified
followed): `QuickBooksPaymentSyncService.sync_invoice_payment_to_quickbooks` was added to the SAME
class Phase 17 defined, in the SAME file. The connection-resolution/create-payment(with-401-retry)/
persist logic previously inlined at the end of `sync_deposit_payment` was extracted into a private
`_create_and_persist_payment(tenant_id, payment_id, *, qb_customer_id, qb_invoice_id, amount,
request_id)` helper, and BOTH `sync_deposit_payment` (unchanged behavior, re-verified by re-running
its full 30-test suite unmodified) and the new `sync_invoice_payment_to_quickbooks` now call it —
zero duplicated QuickBooks HTTP/retry/persistence logic between the two paths, exactly as
instructed. Each method keeps its own distinct `requestid` namespace
(`klaros-deposit-payment-{id}` vs. `klaros-invoice-payment-{id}`) so the two paths' Intuit
write-deduplication keys can never collide even in the (currently impossible, since `payment_id`
is always unique) case they were ever called for the same id.

**QuickBooks payload** (STEP 4): reuses `QuickBooksClient.create_payment` verbatim — no new client
method, no invented fields. The exact same `Line[].LinkedTxn` (`TxnType: "Invoice"`) shape Phase 17
already established is used for both deposit and ordinary invoice payments; only the resolved
`invoice_id`/`customer_id`/`amount` values differ by how they were looked up. Response validation
(`QuickBooksPaymentResponse`, `extra="allow"`) is unchanged and reused, consistent with the
Phase 12G+ standard.

**Event-driven automation** (STEP 5, evaluated not assumed): confirmed `EventType.PAYMENT_RECEIVED`
ALREADY fires for every `PaymentService.record_payment` call — deposit or ordinary — so no new
EventType was needed (the mission explicitly warned against creating one unnecessarily). The new
subscriber, `finance_quickbooks_invoice_payment_sync`, distinguishes the two payment kinds itself
by re-reading the `Payment` row and checking `quote_id`/`provider` before deciding whether to act —
for a deposit payment (or a non-Stripe payment) it returns immediately, a proven silent no-op
(`test_payment_received_event_for_deposit_payment_is_a_silent_noop_here` asserts `dead_lettered ==
0`, `failed_retrying == 0`, AND that the `EventProcessingRecord` shows `SUCCESS`, not merely
"didn't crash") — never a false-positive dead-letter entry for a payment type this handler isn't
responsible for. For the genuine failure case (invoice not yet synced), the EventBus's own bounded
retry + dead-letter queue is reused unchanged, and — proven directly, not assumed — neither
`Payment.status` nor `Invoice.status` is touched by that failure
(`test_failed_automatic_invoice_payment_sync_lands_in_dead_letter_without_mutating_state`).

**Idempotency** (STEP 6, proven at the application boundary as instructed, never claimed live):
first sync creates the QuickBooks Payment and persists the id; a second call for the same Payment
is proven to make zero API calls (`test_already_synced_payment_is_a_safe_noop_no_api_call`, using a
hard `AssertionError`-raising mock); `test_duplicate_invocation_never_creates_two_qbo_payments`
calls the service twice directly and asserts `create_payment` fires exactly once;
`test_requestid_deterministic_across_simulated_partial_completion_retry` clears
`quickbooks_payment_id` after a successful sync (simulating a crash between "QuickBooks accepted
it" and "we persisted the id") and proves the identical deterministic `requestid` is sent on retry
— explicitly NOT claimed as proof Intuit's server actually deduplicates on it, since no credentials
exist to observe that; the test's own docstring and this report both say so plainly.

**Refund compatibility** (STEP 7, verified not assumed): read `QuickBooksRefundSyncService`
(Phase 18) end to end again this phase and confirmed it never inspects `Payment.quote_id` anywhere
— its only precondition on the original payment is `Payment.quickbooks_payment_id` being set. Zero
lines of that service were changed. `test_refund_against_a_synced_invoice_payment_syncs_via_
existing_refund_service` proves the full real chain: an ordinary invoice payment is synced to
QuickBooks via the new Phase 19 method, a real (mocked) Stripe refund is issued against it through
`PaymentService.decide_refund`, and the resulting `Refund` syncs to QuickBooks as a `RefundReceipt`
correctly referencing the QBO Payment id the Phase 19 sync produced — proving the two phases
compose correctly rather than merely asserting they should.

**Tenant isolation, proven not assumed** (STEP 8): `test_tenant_b_cannot_sync_tenant_as_payment`
(cross-tenant Payment lookup fails before any QuickBooks call — `create_payment` never mocked in
that test, so a stray call would fail outright); `test_tenant_b_cannot_use_tenant_as_invoice` — a
deliberately adversarial scenario: a `PaymentAllocation` row correctly scoped to tenant B but
naming tenant A's real, already-synced invoice id (which should never happen through real code
paths, but proves the LOOKUP itself re-validates tenant ownership of the resolved Invoice, not
just the initial Payment check) — fails as `InvoiceNotYetCreatedError` (tenant B's own
tenant-scoped `session.get(Invoice, ...)` correctly finds nothing, since the row belongs to tenant
A); `test_tenant_b_cannot_use_tenant_as_quickbooks_connection` (a tenant with its own fully-synced
payment and its own real connection still fails not-connected the instant ITS OWN connection's
credential is cleared, proving no fallback to any other tenant's connection row exists).

**RBAC/tool** (STEP 9): `finance.sync_invoice_payment_to_quickbooks` reuses `Permission.
SEND_INVOICE` (the identical permission both prior QuickBooks-sync tools use) and `AUTO` policy —
no new permission created, matching the mission's explicit "do not create a new permission unless
absolutely necessary" instruction; there was no necessity.

**Failure classification** (STEP 10): reuses `QuickBooksClient.create_payment`'s existing, already
fully-tested (Phase 17) retry/error-classification behavior verbatim — no new client method, so no
new classification behavior to test; the eligibility-failure tests above (not-connected, missing
QBO invoice/customer id, unsupported payment state) exercise the SERVICE-level failure paths this
phase actually added. Malformed-response/401/403/429/4xx/5xx/timeout classification at the CLIENT
level is unchanged Phase 17 behavior, re-verified passing via the full re-run of `tests/
test_quickbooks_deposit_payment_sync.py` below.

**Security review** (STEP 11): secret scan of every new/changed file — clean. `pip-audit` re-run:
unchanged, the one already-accepted `ecdsa` finding only (transitive `python-jose` dependency, this
app never uses the vulnerable ECDSA/EC-key code path — established finding, re-confirmed not
re-litigated). No dependency added. No new `logger.*` call in `quickbooks_payment_sync_service.py`
or the new event handler (none was needed, matching the pattern of the other two automatic
handlers). No raw provider payload, OAuth token, Stripe secret, Authorization header, or
unnecessary customer PII logged anywhere in the new code — grepped explicitly, not assumed from
the file being "similar to" prior phases' code.

**A real, adversarial cross-tenant scenario was specifically constructed and tested** this phase
(the `PaymentAllocation` naming another tenant's real invoice id) that goes beyond simply
confirming "wrong tenant_id fails" — it proves the SPECIFIC lookup chain (Payment → Allocation →
Invoice) re-validates tenant ownership at the Invoice step even when an intermediate row is
correctly tenant-scoped but points somewhere it structurally shouldn't be able to reach in
practice. No gap was found — the existing `session.get(Invoice, invoice_id)` pattern, scoped by
`invoice.tenant_id != tenant_id`, already closes this by construction; the test exists to prove it,
not because a bug was found.

**Frontend** (STEP 14): explicitly evaluated and judged not warranted — this is a backend-internal
accounting-synchronization extension with no new user-facing workflow; reported as `N/A`, not
`BLOCKED BY ENVIRONMENT` (the mission's own distinction, followed precisely: `BLOCKED` implies a
change was needed but couldn't be verified, whereas here no change was needed at all).

**Final counts, all re-run against the completed final code state, each confirmed via `ps -p
<pid>` before being read**:
- SQLite: **627 passed, 8 skipped, 0 failed** (122.33s) — 602 Phase-18 baseline + 25 new.
- Real PostgreSQL 16.2 + real Redis (isolated throwaway schema, created/verified/dropped, `public`
  schema's 94 tables confirmed identical before and after, role `search_path` reset): **635
  passed, 0 failed, 0 skipped (570.50s)**.
- `tests/test_quickbooks_invoice_payment_sync.py` run independently, 3 times in a row to check for
  flakiness in the event/worker tests: **25/25 passed, all three runs, no flakiness observed.**
- `tests/test_quickbooks_deposit_payment_sync.py` (Phase 17) and `tests/test_quickbooks_refund_
  sync.py` (Phase 18) both re-run in full after this phase's refactor and new event subscriber:
  **30/30 and 30/30, unchanged.**
- No migration this phase — confirmed by re-reading `Payment`'s full column list; head remains
  `0024`.
- No dependency changes.
- `git status`/`git diff` reviewed in full: only the expected Phase 19 files plus the
  already-uncommitted prior-phase work — no secrets, no temporary database/schema files, no debug
  statements, no weakened assertions, no unrelated modifications. `.env` untouched (confirmed via
  diff, not just recalled from memory).

**What genuinely was NOT verified this phase, stated plainly**: no real QuickBooks API call of any
kind (credentials confirmed absent). No real Stripe call either. Intuit's `requestid`
deduplication's actual server-side effect has never been observed — every idempotency claim above
is scoped to "this application's own local behavior is correct and deterministic," never extended
to "and Intuit's server definitely deduplicates on it," which the mission explicitly forbade
claiming. No frontend change, therefore no frontend verification of any kind (and none was
warranted). Everything reported above as verified was verified through the real service/tool
layer, the real `EventBus` (including its real dead-letter/replay mechanics), and
`httpx.MockTransport`/`monkeypatch`-level mocking of the one genuine external boundary (the
QuickBooks HTTP API) — never a live provider call, never represented as one.

## Phase 20 — Accounting Lifecycle Audit + Split-Payment Hardening

**Data-model audit** (STEP 1, traced from actual service behavior and constraints, not assumed
from the schema's apparent shape — the mission's own explicit instruction): re-read
`PaymentService.record_payment`/`request_refund`/`decide_refund`/`reconcile_external_refund` line
by line this phase. Confirmed ten things: (1) one payment→one invoice, the standard single-
allocation case; (2) **one payment→multiple invoices is a real, already-supported, already-
reachable capability** — `record_payment`'s `allocations` parameter is a `list[AllocationInput]`,
each entry independently validated against its OWN invoice's `amount_due` and creating its own
`PaymentAllocation` row, exercised today via `finance.record_test_payment`'s identical `list[
AllocationModel]` input shape — this is the finding that justified removing Phase 19's
restriction rather than merely leaving it documented; (3) partial payment — `alloc.amount` can be
less than `invoice.amount_due`; (4) multiple payments→one invoice — `_recompute_invoice` sums
EVERY `PaymentAllocation` row for that invoice across all `Payment`s, not just the latest; (5) one
refund→one payment — `Refund.payment_id` is a single, non-nullable FK; (6) partial refund —
`Refund.amount` can be less than `Payment.amount`, enforced only by the overpayment guard in
`request_refund`; (7) multiple refunds against one payment — `request_refund` sums existing
non-`REJECTED` refunds and guards the total against `Payment.amount`, a real supported flow; (8)
**fully refunded payment via multiple partial refunds — found genuinely broken** (detailed below);
(9) payment before invoice creation — the Phase 15 deposit shape, unchanged; (10) invoice payment
after QuickBooks invoice sync — the Phase 19 precondition, unchanged.

**QuickBooks payment flow audit** (STEP 2): re-read `QuickBooksPaymentSyncService`,
`QuickBooksRefundSyncService`, `QuickBooksClient`, `PaymentService`, `InvoiceService`,
`app/events/finance_handlers.py`, `app/tools/builtin/quickbooks_tools.py`, and every existing
QuickBooks test file in full before changing anything, per instruction. Located the exact three
points where an assumption of exactly one invoice was baked in: (a) `QuickBooksClient.
create_payment`'s single `invoice_id`/`amount` parameter pair; (b) `sync_invoice_payment_to_
quickbooks`'s `len(allocation_invoice_ids) > 1` rejection; (c) implicitly, the single `qb_invoice_
id` variable threaded through `_create_and_persist_payment`. No other code path (webhook, event
handler, tool) made this assumption independently — all three were downstream of these two
functions, confirming the fix needed to touch only the client and the one service method, not the
event handler or tool (both already operate on a bare `payment_id`, agnostic to how many invoices
end up involved).

**Split-payment safety determination** (STEP 3): answered directly from the code, not guessed —
YES, a single Klaros `Payment` can have `PaymentAllocation A → Invoice A` and `PaymentAllocation B
→ Invoice B` simultaneously; this is not a hypothetical schema capability but an already-exercised
one. QuickBooks' own Payment object supports the equivalent representation (multiple `Line`
entries, each with an independent `LinkedTxn`) — a real, documented feature of the API this
codebase already targets, not an invented workaround. The one genuine new constraint discovered
during implementation (not anticipated at audit time): a QBO Payment carries exactly ONE
`CustomerRef` — if a payment's allocated invoices belonged to different QuickBooks customers,
there would be no single correct QBO Payment to represent it as. This is not reachable through any
real Klaros code path today (nothing in `record_payment` currently enforces or even checks that
allocated invoices share the payment's own customer, but nothing constructs a payment against a
foreign customer's invoice either) — implemented as a defensive guard
(`AllocationSpansMultipleCustomersError`) rather than assumed impossible.

**Implementation, extending not duplicating**: `QuickBooksClient.create_payment`'s signature
changed from `(invoice_id: str, amount: float)` to `(invoice_lines: list[tuple[str, float]])` —
the single call site inside `_create_and_persist_payment` and both its callers
(`sync_deposit_payment`, `sync_invoice_payment_to_quickbooks`) were updated to match; no second
client method was added. `sync_invoice_payment_to_quickbooks` now loops over every `PaymentAllocation`
row (previously fetched only distinct invoice ids and rejected >1), resolving each invoice AND its
customer, collecting `(qb_invoice_id, alloc.amount)` pairs and validating a single consistent
`qb_customer_id` across all of them. `sync_deposit_payment` itself required a two-line change
(`qb_invoice_id=...` → `qb_invoice_lines=[(qb_invoice_id, amount)]`) and is otherwise byte-for-byte
unchanged — re-verified by its full 30-test suite passing unmodified.

**A real bug found and fixed, independent of the split-payment work** (surfaced directly by STEP 1's
explicit instruction to trace "multiple refunds against one payment" and "fully refunded payment"
through the actual code, not assume): `PaymentService.decide_refund` line 311 (before this fix)
read `payment.status = PARTIALLY_REFUNDED if refund.amount < payment.amount else REFUNDED` —
comparing the amount of the refund JUST decided against the payment's ORIGINAL total, never the
cumulative amount refunded so far. Two 50%-each refunds decided in sequence: after the second,
`refund.amount` (50) is still `< payment.amount` (100), so status stays `PARTIALLY_REFUNDED` even
though the payment has now genuinely been refunded in full. Found by deliberately tracing through
this exact scenario while auditing STEP 1's item 8, not by a failing test surfacing it
accidentally — then a dedicated test was written to prove it (`test_two_partial_refunds_summing_
to_full_amount_mark_payment_refunded`), confirmed to fail against the pre-fix code (verified by
reasoning through the exact comparison, not by re-introducing the bug to check — the logic is
unambiguous), and confirmed to pass after the fix. `PaymentService.reconcile_external_refund`
(the Stripe-Dashboard-initiated refund path) already used the CORRECT cumulative-total comparison
— this was a real inconsistency between two code paths that must behave identically, not a
matter of interpretation. Fixed by computing the cumulative sum of `COMPLETED` refunds against the
payment (via a fresh query inside the same transaction, autoflush-visible to include the refund
just marked `COMPLETED` in this same call) and comparing THAT total against `Payment.amount`,
mirroring `reconcile_external_refund` exactly. Four additional regression tests prove the fix
doesn't overcorrect: a single partial refund still yields `PARTIALLY_REFUNDED`; a single full
refund still yields `REFUNDED` directly; a `REJECTED` refund is never counted toward the
cumulative total (proven by a rejected-then-approved-different-amount scenario that would
incorrectly reach `REFUNDED` early if rejected refunds leaked into the sum).

**Security review**: secret scan of every new/changed file — clean. `pip-audit` re-run: unchanged,
the one already-accepted `ecdsa` finding only. No dependency added. No new `logger.*` call
anywhere in `payment_service.py`, `quickbooks_payment_sync_service.py`, `quickbooks_client.py`, or
`quickbooks_tools.py` — grepped the full diff explicitly, confirmed empty. No raw provider
payload, OAuth token, Stripe secret, or unnecessary customer PII logged anywhere in the changed
code. No migration this phase — confirmed by `git status` on `alembic/versions/` showing no new
file; head remains `0024`.

**Final counts, all re-run against the completed final code state, each confirmed via `ps -p
<pid>` before being read**:
- SQLite: **634 passed, 8 skipped, 0 failed** (121.85s) — 627 Phase-19 baseline + 5
  (`test_payment_refund_status_cumulative.py`) + 2 net new (`test_quickbooks_invoice_payment_
  sync.py`: one rejection test replaced with three).
- Real PostgreSQL 16.2 + real Redis (isolated throwaway schema, created/verified/dropped, `public`
  schema's 94 tables confirmed identical before and after, role `search_path` reset): **642
  passed, 0 failed, 0 skipped (705.06s)**.
- `tests/test_quickbooks_deposit_payment_sync.py` + `tests/test_quickbooks_refund_sync.py` +
  `tests/test_quickbooks_invoice_payment_sync.py` run together, 3 times in a row, to confirm no
  flakiness from the client signature change across all three files at once: **87/87 passed, all
  three runs.**
- No dependency changes.
- `git status`/`git diff` reviewed in full: only the expected Phase 20 files (plus the one Phase
  19 client-signature ripple into its own test file's mocks) and the already-uncommitted
  prior-phase work — no secrets, no temporary database/schema files, no debug statements, no
  weakened assertions. `.env` untouched.

**What genuinely was NOT verified this phase, stated plainly**: no real QuickBooks API call
(credentials confirmed absent) — the multi-line `Line[]` payload's real-world acceptance by
Intuit's API has never been observed, only that this application constructs and sends the shape
Intuit's documentation describes. No real Stripe call. No frontend change, therefore no frontend
verification. Everything reported above as verified was verified through the real service layer,
the real `EventBus`, and `httpx.MockTransport`/`monkeypatch`-level mocking of the one genuine
external boundary — never a live provider call, never represented as one.

## Phase 21 — Accounting Production-Readiness Audit

**Explicit instruction followed**: did not trust Phase 17–20's own certifications blindly. Re-read
`PaymentService`, `QuickBooksPaymentSyncService`, `QuickBooksRefundSyncService`,
`QuickBooksClient`, `QuoteService`, `app/events/finance_handlers.py`, and every relevant existing
test file fresh this phase, tracing the full 14-item lifecycle (quote acceptance → deposit
configuration → Stripe Checkout → webhook → Payment → Job conversion → Invoice creation →
Invoice/deposit/split payment → QuickBooks → partial/full refund → QuickBooks refund → EventBus
retry/DLQ/replay) end to end before touching any code, per STEP 1's instruction.

**State-machine audit** (STEP 2): traced every assignment site of `QuoteStatus`/`PaymentStatus`/
`RefundStatus`. Confirmed `RefundStatus.APPROVED` was a real, defined-but-never-assigned enum
member (a pre-existing, honestly-documented Phase 12G finding) — this phase gives it a genuine,
narrowly-scoped purpose (the CAS "claimed, in-flight" intermediate state) rather than leaving it
dead or removing it. No impossible transition found elsewhere. No state found assignable twice
under normal (non-concurrent) operation.

**QuickBooks payload audit** (STEP 3): re-read `QuickBooksClient.create_payment`/
`create_refund_receipt`'s actual request construction. Confirmed `float()` conversion happens ONLY
at the final JSON-serialization boundary (never in internal comparison/computation), Decimal
precision preserved throughout via `Numeric` columns, and `.quantize(Decimal("0.01"))` (deterministic
banker's rounding) is the only rounding operation in the entire accounting codebase. Requestid
values (`klaros-deposit-payment-{id}`, `klaros-invoice-payment-{id}`, `klaros-refund-{id}`) are
namespaced by operation type and keyed on globally-unique UUIDs — no collision risk between
different operations even in principle. **Found and fixed a real duplicate-Line gap**: a payment
with two `PaymentAllocation` rows against the SAME invoice (a real, reachable shape once Bug 1
below was fixed to allow it when within bounds) previously produced two separate QuickBooks `Line`
entries with duplicate `LinkedTxn` references to the same invoice — unverified and risky QuickBooks
behavior. Fixed by merging same-invoice allocations into one summed `Line` before building the
payload.

**Idempotency audit** (STEP 4, the mission's own explicit standard: "do NOT call check-then-act
concurrency safety 'proven' unless there is an actual concurrency test"): every external write
boundary was individually assessed, and for the ones resting on a plain check-then-act pattern, a
real `asyncio.gather` concurrency test was written and RUN — not assumed safe by inspection alone.
This is exactly how the two real bugs below were found: both were check-then-act patterns that
LOOKED safe on casual reading and were NOT safe once actually raced.

**Bug 1 — duplicate-invoice-allocation overpayment (single call)**: `record_payment` validated
each allocation independently against the invoice's own unchanged `amount_due` — reproduced
directly (not inferred): two $60 `AllocationInput`s against a $100-due invoice within ONE call
both passed, `amount_due` went to **-$20.00**. Root cause: no running per-invoice total tracked
across the loop. Fixed by adding `allocated_so_far: dict[uuid.UUID, Decimal]`, checked and updated
per allocation within the same call. Re-verified: the identical two-$60 case now raises
`OverpaymentError` before any row is written; a legitimate two-allocation split that fits (e.g.
$40 + $60 against $100) is still correctly accepted and summed.

**Bug 2 — concurrent payments to the same invoice (cross-call race)**: reproduced directly under
`asyncio.gather` — two SEPARATE `record_payment` calls (distinct Stripe payments, as a real
double-payment-via-two-tabs scenario would produce) each read `amount_due` in their own
transaction before either committed; both succeeded, again driving `amount_due` to -$20.00 for a
$100-due invoice paid twice at $60 each. Fixed with `session.get(Invoice, alloc.invoice_id,
with_for_update=True)` — a genuine Postgres row lock. **Verified specifically against real
PostgreSQL** (not SQLite, which has no row-level locking and silently ignores the hint — confirmed
by first re-running the identical repro against real Postgres with the fix applied: the second
transaction now correctly blocks until the first commits, then sees the updated `amount_due` and
correctly raises `OverpaymentError` instead of also succeeding; `amount_due` settles at $40.00, not
-$20.00). This asymmetry (fixed on Postgres, not on SQLite) is disclosed explicitly in the test
file's own docstring and skip logic, not hidden.

**Bug 3 — concurrent refund approval (double real Stripe call)**: reproduced directly — two
concurrent `decide_refund(approved=True)` calls for the same refund both passed a post-hoc
`refund.status != REQUESTED` check (both read `REQUESTED` before either wrote back) and both
proceeded to call Stripe's real `create_refund` — confirmed via a call-counting mock: **2** real
Stripe API calls for one $100 refund under `asyncio.gather`, not 1. The ONLY thing preventing an
actual double-refund at Stripe was Stripe's own documented idempotency-key deduplication — real
per Stripe's contract, but genuinely unverifiable in this environment (no credentials), and never
something Klaros' own code enforced. Fixed with a conditional `UPDATE ... WHERE status='REQUESTED'`
CAS claim (REQUESTED → APPROVED, repurposing the previously-dead `RefundStatus.APPROVED`) executed
BEFORE any decision about calling Stripe is even made — re-verified: Stripe's `create_refund` now
fires **exactly once** under the identical race, and the losing `decide_refund` call raises
`InvalidRefundError` immediately, never reaching the Stripe client at all. A companion test proves
the claim correctly REVERTS to `REQUESTED` (not stuck in `APPROVED` forever) when the real Stripe
call genuinely fails, and that a subsequent retry then succeeds — preserving the pre-existing "no
DB state changed on failure" guarantee this code path has always documented.

**Tenant isolation audit** (STEP 5): re-verified the adversarial boundaries the mission listed are
still closed after this phase's changes — the new CAS `UPDATE` statement embeds `tenant_id` directly
in its `WHERE` clause (a strengthening, not a weakening, of tenant isolation at that specific write).
No new cross-tenant surface was introduced by either fix; both operate entirely within the existing
tenant-scoped session/query patterns already proven across Phases 17–20's own extensive isolation
test suites, which were re-run in full this phase (see final counts) and remain green.

**EventBus failure-semantics audit** (STEP 6): re-confirmed (not re-derived from scratch, since
Phases 17–20 already built and tested this extensively) that every QuickBooks subscriber's
success/transient-failure/permanent-failure/replay behavior is unchanged by this phase's fixes —
neither bug fix touches event publication, subscription, or dispatch; both are internal to the
service methods those subscribers call.

**Database consistency audit** (STEP 7): `Payment.quickbooks_payment_id`/`Refund.
quickbooks_refund_receipt_id` reviewed for uniqueness scope — correctly unconstrained, since a QBO
id is only unique within its own realm (tenant) and is never queried by that column alone anywhere
in the codebase (always resolved through a tenant-scoped lookup first, confirmed by grepping every
read site). No migration required or added — both fixes are pure service-layer logic changes.

**Security review** (STEP 9): secret scan of every changed file — clean. `pip-audit` re-run:
unchanged, the one already-accepted `ecdsa` finding only. No new `logger.*` call anywhere in the
diff (grepped explicitly). The new CAS error paths never leak another tenant's id or any refund/
payment internal state in their exception messages (`"Refund not found"`/`"Refund is not pending"`,
identical wording to the pre-existing errors).

**Retry/timeout audit** (STEP 10): unchanged from Phases 17–20 — neither fix touches
`QuickBooksClient`'s HTTP layer; its retry/backoff/error-classification behavior (already
extensively tested) is untouched.

**Final counts, all re-run against the completed final code state, each confirmed via `ps -p
<pid>` before being read**:
- SQLite: **641 passed, 8 skipped, 0 failed** (116.78s) — 634 Phase-20 baseline + 7 new.
- Real PostgreSQL 16.2 + real Redis (isolated throwaway schema, created/verified/dropped, `public`
  schema's 94 tables confirmed identical before and after, role `search_path` reset): **649
  passed, 0 failed, 0 skipped (602.02s)**.
- `tests/test_phase21_accounting_hardening.py` run independently, 3 times in a row on SQLite AND
  3 times in a row on real Postgres (the concurrency assertions are timing-sensitive and were
  specifically checked for flakiness on both backends): **7/7 passed, all six runs, no flakiness
  observed.**
- No migration this phase — confirmed by `git status` on `alembic/versions/` showing no new file;
  head remains `0024`.
- No dependency changes.
- `git status`/`git diff` reviewed in full: only the expected Phase 21 files and the
  already-uncommitted prior-phase work — no secrets, no temporary database/schema files, no debug
  statements, no weakened assertions. `.env` untouched.

**What genuinely was NOT verified this phase, stated plainly**: no real QuickBooks or Stripe API
call of any kind (credentials confirmed absent). Whether Stripe's own idempotency key actually
deduplicates a genuine double-`create_refund` call at Stripe's end has never been observed —
Klaros no longer DEPENDS on it for the concurrent-approval case (the local CAS claim now prevents
the second call from ever being made), but the claim about Stripe's own behavior itself remains
unverified. The concurrent-payment fix's real effectiveness was verified specifically against real
Postgres, not inferred from SQLite (where the same test intentionally does not assert the fixed
behavior, and says so). No frontend change, therefore no frontend verification. Everything reported
above as verified was verified through the real service layer, real `asyncio.gather` concurrency
tests against both SQLite and real Postgres, and `monkeypatch`-level mocking of the two genuine
external boundaries (Stripe, QuickBooks) — never a live provider call, never represented as one.

## Phase 22 — Stripe Payment Lifecycle Production-Readiness Audit

**Complete lifecycle trace** (STEP 1, re-read fresh, not trusted from Phase 15–21 summaries):
quote acceptance (`QuoteService.decide`) → deposit calculation (`compute_deposit_amount`, Decimal-
safe) → Checkout Session creation (`QuoteDepositService.create_deposit_checkout_session`, resolving
`deposit_amount` exclusively from the server-side `Quote` row) → Stripe metadata (`tenant_id`,
`quote_id`, `customer_id`, `purpose="quote_deposit"`) → webhook (`app/api/v1/webhooks.py::
_handle_quote_deposit_succeeded`) → `Payment` (via `PaymentService.record_payment`, tenant-scoped
Quote ownership re-verified BEFORE recording, a Phase 15 fix) → `QuoteService.mark_deposit_paid`
(`DEPOSIT_PENDING → DEPOSIT_PAID`) → `_convert_to_job` (`JobService.create_job`, idempotency-keyed
`job-from-quote-{quote_id}`) → `CONVERTED` → (staff-triggered) Invoice creation → QuickBooks sync
(Phases 17–20). Refund path traced separately: `PaymentService.request_refund` → `decide_refund`
(Phase 21's CAS-claim fix) → real Stripe refund → cumulative-total `Payment.status` update (Phase
20 fix) → `EventType.PAYMENT_REFUNDED` → QuickBooks `RefundReceipt` sync (Phase 18). Every stage's
authoritative state/service/event/subscriber/tool/external-op/persisted-id/idempotency-boundary/
tenant-boundary/failure-behavior matches its Phase 15–21 documentation — re-verified, not assumed.

**A real, high-severity bug found and fixed**: `JobService.create_job`'s idempotency check is a
plain check-then-insert (`SELECT` for an existing `idempotency_key`, then `INSERT`) — traced this
phase specifically because STEP 5 explicitly asked to test "two valid payment webhooks arriving
concurrently for the same quote" and to NOT call a check-then-act pattern concurrency-safe without
reproducing it under real Postgres. Reproduced directly: two concurrent `QuoteService.
mark_deposit_paid` calls for the SAME quote (simulating two genuinely distinct Stripe PaymentIntents
both succeeding — e.g. a customer re-opening the Checkout link) both passed `create_job`'s "does a
Job with this idempotency_key exist?" check before either committed; the second `INSERT` raised an
**unhandled** `IntegrityError` on `uq_jobs_tenant_idempotency_key`, propagating straight out of
`create_job` through `mark_deposit_paid`/`_convert_to_job` with NO handling anywhere in the call
chain — the quote was left at `DEPOSIT_PAID` with `job_id=None`, **a real Stripe deposit taken with
no Job ever created and no error surfaced to staff**. This is a materially worse outcome than any
Phase 21 finding: money moves, but the customer's actual work order silently never exists.

Fixed by wrapping `create_job`'s `session.commit()` in a `try/except IntegrityError`, rolling back
and re-resolving the existing row via a **fresh** session (reusing the failed session for the
re-check was tried first and produced its own confusing follow-on error — `InvalidRequestError:
Could not refresh instance` — reusing a session immediately after a caught commit failure risks
stale/poisoned state; a fresh session for the re-check is the safer, correct pattern), mirroring
the "concurrent delivery raced us to the constraint — the other request is handling it, this is a
genuine duplicate, not an error" pattern already established for `WebhookEvent`
(`app/api/v1/webhooks.py`).

**A genuine SQLite-vs-real-Postgres discrepancy was itself investigated, not just noted**: the
identical reproduction, first run against SQLite (this project's test default), produced a
DIFFERENT and more confusing failure — BOTH concurrent calls raised exceptions, and the end state
showed **zero** Jobs at all (not one, as real Postgres correctly produces). Root-caused before
accepting either result at face value: SQLite's `StaticPool` shares ONE physical connection across
all sessions in this test process (a documented, pre-existing project constraint — see `app/db/
session.py`), meaning two "concurrent" `asyncio` tasks cannot hold two truly independent, isolated
open transactions the way two real Postgres connections can — the interleaving that results is a
test-harness artifact, not a faithful simulation of real concurrent database access. Re-running the
identical scenario against real PostgreSQL (a separate, real connection per session, as production
actually has) showed the TRUE behavior: with the fix applied, both concurrent calls succeed, exactly
one `Job` exists, and the quote correctly reaches `CONVERTED` with a valid `job_id`. This distinction
is disclosed explicitly in the regression test's own code (an `engine.dialect.name == "sqlite"`
guard that skips the strict assertions there, with a comment explaining why) rather than either
hidden or asserted against a misleading SQLite-only result.

**Checkout creation audit** (STEP 2): re-confirmed `QuoteDepositService.create_deposit_checkout_
session` resolves `deposit_amount`/currency/customer exclusively from the server-side `Quote`/
`Customer` rows — the method accepts only `tenant_id`, `quote_id`, `success_url`, `cancel_url`
(itself never accepted from the unauthenticated public endpoint, built server-side from
`FRONTEND_BASE_URL` — a Phase 16 finding, re-verified unchanged). Every adversarial case the mission
listed (wrong tenant, wrong quote status, forged amount/currency/quote_id) was already covered by
Phase 15/16's own test suites (re-run this phase, still green) — no gap found. Concurrent duplicate
checkout requests: proven this phase (`test_concurrent_deposit_checkout_creation_shares_one_
idempotency_key`) to send the IDENTICAL deterministic Stripe idempotency key
(`klaros-quote-deposit-{quote_id}-{deposit_amount}`) both times — Klaros' own LOCAL guarantee,
explicitly distinguished from Stripe's own server-side deduplication of that key, which remains
unverifiable without credentials and is never claimed as observed.

**Webhook authentication audit** (STEP 3): re-read `verify_webhook_signature` fresh — raw bytes
used, signature verified strictly BEFORE JSON parsing (a signature failure never reaches
`json.loads`), replay-tolerance window enforced (`abs(time.time() - ts) > tolerance_seconds`),
`hmac.compare_digest` used for the signature comparison itself (constant-time). All of Phase 12F/
12G/15's existing signature/malformed-JSON/malformed-envelope tests re-run this phase, still green.
No gap found — none was expected here, since this exact boundary has already been through two prior
dedicated hardening passes (Phase 12F, 12G-2).

**Payment creation / conflicting delivery audit** (STEP 4): the mission's own worked example (Event
A: `payment_intent.succeeded`, amount=100; Event B: same PaymentIntent id, amount=999) was
constructed and run directly, using two genuinely DIFFERENT Stripe event ids (so `WebhookEvent`'s
own per-event dedup does not intercept the second delivery, isolating exactly the boundary the
mission wanted tested) — proven: `PaymentService.record_payment`'s own `(tenant_id, provider,
external_id)` uniqueness is a real, independent second line of defense; the original `Payment.amount`
(100) is never overwritten by the conflicting second event's forged 999. A companion test proves
the OTHER STEP 14 scenario — the SAME event id delivered twice with a DIFFERENT payload body — never
applies the second payload's content at all (the persisted `WebhookEvent.raw_payload` and the
resulting `Payment` both reflect only the first, authoritative delivery).

**Quote state-transition concurrency** (STEP 5): this IS the `JobService.create_job` bug above,
found by following STEP 5's own explicit instruction to test exactly this scenario under real
Postgres rather than assume safety.

**Stripe refunds audit** (STEP 6): the 100+100+100-against-300 and 100+100+101-against-300
sequences the mission specified were already built and passing in Phase 20/21 (`test_three_full_
hundred_refunds_against_a_300_payment` proves the first sequence reaches `REFUNDED` and a subsequent
even-$0.01 over-refund attempt is rejected before any Stripe call — the second sequence's "third
refund rejected before any external Stripe operation" is exactly `request_refund`'s own guard,
which runs entirely before `decide_refund`/any Stripe call is ever reached). Concurrent refund
approval was already found, reproduced, and fixed in Phase 21 (the CAS claim) — re-verified passing
this phase, unchanged, not re-invented.

**Stripe idempotency audit** (STEP 7, LOCAL vs. PROVIDER guarantee explicitly distinguished
throughout, never conflated): Checkout Session creation's key
(`klaros-quote-deposit-{quote_id}-{deposit_amount}`) and refund creation's key
(`klaros-refund-{refund_id}`) are both deterministic and stable across retries/concurrent calls
(proven directly for the checkout case this phase; the refund case was proven in Phase 21). Whether
Stripe's OWN server actually deduplicates on either key has never been observed in this environment
(no credentials) and is never claimed as verified — every mention of Stripe's idempotency behavior
in this document and the code comments explicitly flags it as the provider's own documented
contract, not something this test suite demonstrates.

**Payment status reconciliation** (STEP 8): the actual transition table was built from code, not
assumed: `PENDING → SUCCEEDED` (`record_payment`, unconditional on creation — no code path ever
creates a `Payment` in any other initial status), `SUCCEEDED → PARTIALLY_REFUNDED → REFUNDED` (both
`decide_refund` and `reconcile_external_refund`, both now using the Phase 20-fixed cumulative-total
comparison), `PENDING → FAILED` is a defined-but-unreachable path (no code ever creates a `PENDING`
Payment row that could later transition to `FAILED` — `record_payment` always creates `SUCCEEDED`
directly, matching the pre-existing, already-documented Phase 12G finding that `PaymentStatus.
PENDING`/`FAILED` are reserved states with no active assignment path). No backwards transition found
anywhere in the codebase (`SUCCEEDED → PENDING`, `REFUNDED → SUCCEEDED`, `REFUNDED →
PARTIALLY_REFUNDED`, `FAILED → SUCCEEDED` — none of these are ever written by any code path,
confirmed by grepping every `payment.status =`/`.status =` assignment site in `payment_service.py`).

**Amount/currency/Decimal-safety audit** (STEPs 9–10): unchanged from the Phase 20/21 findings
(re-verified, not re-derived) — `float()` only at the final Stripe/QuickBooks API-serialization
boundary, `.quantize(Decimal("0.01"))` the only rounding operation, currency is quote-defined
(`Quote.currency`, defaulting `"USD"`) and never accepted from the client at any boundary (Checkout
Session creation reads it from the server-side `Quote` row exclusively). Multi-currency is
genuinely unsupported (no conversion logic exists anywhere) but this fails by simply never being
exercised — no silent conversion path exists to produce a wrong number.

**Tenant isolation, event ordering, failure recovery, WebhookEvent persistence** (STEPs 11–14):
re-verified via the existing, extensive Phase 15–21 test suites (all still green) plus this phase's
two new adversarial tests (conflicting-amount, same-event-different-payload) — no new gap found in
any of these areas. The "Stripe succeeds but local persistence fails" scenario (STEP 13's
explicitly-named dangerous case) has the SAME honest, already-documented guarantee as every prior
phase: no true atomic distributed transaction exists between Stripe and Klaros' own database (this
was never claimed); the WEBHOOK is the actual reconciliation mechanism — if local persistence
genuinely failed after a real Stripe success, the webhook delivery is recorded `FAILED` in
`WebhookEvent` (not silently dropped) and Stripe's own automatic webhook retry (a real, external
mechanism this app doesn't control but correctly integrates with via idempotent processing) is the
recovery path, not a local rollback of the Stripe side.

**Security review**: secret scan of every changed file — clean. `pip-audit` re-run: unchanged, the
one already-accepted `ecdsa` finding only. No new `logger.*` call in the diff. Credential audit:
`STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` both confirmed absent from `backend/.env` — no live call
made or claimed.

**Final counts, all re-run against the completed final code state, each confirmed via `ps -p
<pid>` before being read**:
- SQLite: **645 passed, 8 skipped, 0 failed** (105.81s) — 641 Phase-21 baseline + 4 new.
- Real PostgreSQL 16.2 + real Redis (isolated schema `phase22_test_run`, `public`'s 94 tables
  confirmed untouched before/after): **653 passed, 0 failed, 0 skipped** (587.63s).
- `tests/test_phase22_stripe_hardening.py` run independently, 3 times in a row on SQLite AND 3
  times in a row on real Postgres (the concurrency assertion is timing-sensitive and specifically
  checked for flakiness on both backends): **4/4 passed, all six runs, no flakiness observed.**
- No migration this phase — confirmed by `git status` on `alembic/versions/` showing no new file;
  head remains `0024`.
- No dependency changes.
- `git status`/`git diff` reviewed in full: only the expected Phase 22 files (`app/services/
  job_service.py`'s fix, the new test file) and the already-uncommitted prior-phase work — no
  secrets, no temporary database/schema files, no debug statements, no weakened assertions.
  `.env` untouched.

**What genuinely was NOT verified this phase, stated plainly**: no real Stripe API call of any
kind (credentials confirmed absent). Whether Stripe's own idempotency-key deduplication actually
collapses two concurrent Checkout Session or Refund creation requests into one has never been
observed — Klaros' own local guarantee (a stable, deterministic key sent identically every time)
is proven directly; the provider's side of the contract is not, and is never represented as such.
No frontend change, therefore no frontend verification. Everything reported above as verified was
verified through the real service layer, real `asyncio.gather` concurrency tests against both
SQLite and real Postgres (with the SQLite-vs-Postgres discrepancy itself investigated and
explained, not glossed over), the real webhook HTTP endpoint, and `httpx.MockTransport`/
`monkeypatch`-level mocking of the one genuine external boundary — never a live provider call,
never represented as one.

## Phase 23 — Live Stripe Test-Mode Verification & Payment Lifecycle Final Audit

**Mission**: take the existing, heavily-tested Stripe payment lifecycle and determine whether it is
actually ready for live Stripe test-mode verification — combined with a from-scratch adversarial
re-audit that does not assume Phase 22's own certification is correct merely because it was
reported as passing.

**STEP 3 — credential/environment audit**: `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` checked in
`backend/.env`, the OS environment, and a grep across the repo for any other secrets source —
confirmed **ABSENT** in all three, exactly as Phase 22 left them. `QUICKBOOKS_CLIENT_ID`/
`QUICKBOOKS_CLIENT_SECRET` also confirmed **ABSENT**. `.env` confirmed still gitignored and
untouched. No credential value was ever printed, logged, or written to any file this phase. This
blocks STEPs 15–16 (live Stripe verification, live provider-side idempotency) and the live half of
STEP 9 (QuickBooks) entirely — reported below as `BLOCKED BY CREDENTIAL`, never fabricated.

**STEP 1 — current-state audit, traced end-to-end**: public quote → acceptance → deposit
calculation → Stripe Checkout creation → Stripe idempotency → webhook receipt → signature
verification → webhook dedup → Payment creation → Quote `DEPOSIT_PAID` transition → Job creation →
Job idempotency/concurrent creation → Invoice creation → QuickBooks invoice/payment sync → partial
refund → full refund → QuickBooks refund sync → retry/DLQ/replay behavior. Traced against the
actual current code in `app/integrations/stripe_client.py`, `app/services/quote_deposit_service.py`,
`app/api/v1/webhooks.py`, `app/services/payment_service.py`, `app/services/quote_service.py`,
`app/services/job_service.py`, and `app/events/finance_handlers.py` — not assumed from prior
phases' reports.

**STEP 2 — test coverage matrix**: enumerated all Phase 15–22 Stripe/quote/QuickBooks test files
(210 tests across `test_stripe_*.py`, `test_quote_deposit*.py`, `test_quotes.py`,
`test_phase21_accounting_hardening.py`, `test_phase22_stripe_hardening.py`,
`test_quickbooks_*.py`, `test_payment_refund_status_cumulative.py`,
`test_refund_state_transition_guard.py`). One genuine, previously-zero-coverage gap identified:
**webhook-level retry/recovery after a genuine (non-duplicate-key) processing failure** — nothing
in the existing suite exercised "Stripe succeeds, then something else in the pipeline fails, then
the same event is redelivered." This gap is exactly what STEP 7/12 ask to focus on, and led
directly to the defect below. No duplicate tests were added for flows already well-covered.

**A real, previously-undiscovered defect found and fixed — distinct from Phase 22's concurrency
fix**: reproduced directly (via `monkeypatch`, not inferred) under STEP 7/12's failure-injection
guidance. If `JobService.create_job` fails for ANY reason during `QuoteService.mark_deposit_paid`
→ `_convert_to_job` OTHER than the specific duplicate-key `IntegrityError` Phase 22 already fixed
(e.g. a transient DB connectivity blip — a realistic, not theoretical, production failure mode),
two independent bugs combined to make the failure **permanent and unrecoverable**:

1. **`app/api/v1/webhooks.py`'s `WebhookEvent` dedup check** (the very first thing the endpoint
   does) treated ANY existing row for a given `external_event_id` — including one stuck at
   `FAILED` — as an unconditional duplicate. A redelivery of the SAME event (Stripe's own automatic
   retry, or an operator manually resending it from the Stripe Dashboard) was silently swallowed
   before ever reaching the handler again.
2. **`QuoteService.mark_deposit_paid`'s own idempotency check** treated `DEPOSIT_PAID`/`CONVERTED`
   status as "already fully handled," returning immediately regardless of whether `Quote.job_id`
   was actually set. Even if bug #1 were the only problem and a redelivery DID reach the handler,
   this check would still silently report success with `job=None` — masking the still-missing Job
   rather than retrying its creation.

**Reproduction** (`monkeypatch`-forced `RuntimeError` inside `JobService.create_job`, real webhook
POST through the actual signed endpoint, SQLite): first delivery → HTTP 200, `{"status": "failed"}`,
`Payment` recorded (1 row — money genuinely moved), `Quote.status == DEPOSIT_PAID`,
`Quote.job_id is None`, `WebhookEvent.status == FAILED` with `tenant_id` incorrectly `NULL` (a
quiet side effect of the same root cause — the uncaught exception skipped the tenant_id assignment
entirely, making the stuck row impossible to filter by tenant for operator investigation). Second
delivery of the byte-identical event (simulating the transient issue having cleared) → still HTTP
200 `{"status": "duplicate_ignored"}`, **zero Jobs created**, quote permanently stuck.

**Root cause**: the dedup mechanism conflated "we have seen this event id before" with "this event
was successfully, durably handled" — a `FAILED` row is neither.

**Fix, smallest correct layer, no new abstraction**: the dedup check now only short-circuits to
`duplicate_ignored` when the existing row's status isn't `FAILED`; a `FAILED` row is reused (its
`raw_payload`/`status` reset to `RECEIVED`) rather than inserted twice, letting the SAME event id
be genuinely reprocessed. `mark_deposit_paid` now retries `_convert_to_job` specifically when
`DEPOSIT_PAID` but `job_id` is still unset — safe even under a concurrent retry because
`_convert_to_job`/`create_job` already has its own idempotency (`job-from-quote-{quote_id}`,
Phase 22's fix, re-confirmed unmodified and still correct). `_handle_quote_deposit_succeeded` now
also catches a generic `Exception` around the `mark_deposit_paid` call, mirroring the existing
`record_payment` try/except immediately above it in the same function — this is what fixes the
`tenant_id` audit gap, and also means the exception is now handled locally rather than propagating
all the way to the webhook endpoint's own outer handler.

**Re-verification of the fix**: same reproduction, re-run after the fix — first delivery still
correctly records the Payment and marks the event `FAILED` (with `tenant_id` now correctly
populated); second delivery now returns `{"status": "processed"}`, `Quote.status == CONVERTED`,
`Quote.job_id` set, exactly one `Job`, exactly one `Payment` (the retry does not duplicate the
payment). A third delivery after genuine success was also explicitly tested and confirmed to
correctly go back to `duplicate_ignored` — the fix does not leave a successfully-processed event
permanently retryable. Formalized as `tests/test_phase23_webhook_failure_recovery.py` (2 tests).

**Regression verification after the fix**: the exact affected suites re-run first
(`test_quote_deposit.py`, `test_quote_deposit_public_ux.py`, `test_quotes.py`,
`test_stripe_webhook_endpoint.py`, `test_stripe_webhook_failure_and_refund.py`,
`test_stripe_webhook_signature.py`, `test_stripe_payment_attribution_flow.py`,
`test_phase21_accounting_hardening.py`, `test_phase22_stripe_hardening.py`, the new Phase 23 file,
and all three QuickBooks sync files — 178 tests, all passing), then the full suite on both engines:
- SQLite: **647 passed, 8 skipped, 0 failed (119.88s)** — 645 Phase-22 baseline + 2 new.
- Real PostgreSQL 16.2 + real Redis (isolated schema, migrated to head `0024` before the run,
  `public`'s 94 tables confirmed untouched before/after, `EVENT_TRANSPORT=redis` explicitly set):
  **655 passed, 0 failed, 0 skipped (591.04s)** — 653 baseline + 2 new.
- **STEP 11 concurrency battery re-run 3× against real Postgres** specifically because this
  phase's fix touches `QuoteService.mark_deposit_paid`, the exact function Phase 22's concurrency
  test exercises: `test_phase21_accounting_hardening.py` + `test_phase22_stripe_hardening.py` +
  `test_phase23_webhook_failure_recovery.py` (13 tests) — **13/13 passed, all three runs, no
  flakiness observed** — confirming the `mark_deposit_paid` restructuring does not disturb Phase
  22's own concurrent duplicate-key fix.
- `pip-audit`: unchanged, the one already-accepted `ecdsa` (`PYSEC-2026-1325`) finding only.
- No dependency changes. No migration this phase — `alembic heads` unchanged at `0024`; the fix is
  pure service-layer logic.

**STEP 13 — security audit**: fresh secret scan covering not just `git diff` (which only shows
tracked files — most of this codebase's Phase 15+ work is intentionally uncommitted) but the
untracked `quote_service.py` and the new test file read directly — clean. Public customer-facing
redirect URLs (`app/api/v1/public_quotes.py`) confirmed still built server-side from the fixed
`settings.FRONTEND_BASE_URL` alone — no open-redirect. The `quote_view` token's `_resolve_token`
still checks the URL's own `{quote_id}` against the token's bound `quote_id` before trusting its
`tenant_id` — no cross-quote token replay. Every entity load in `PaymentService`
(`invoice`/`payment`/`refund`) confirmed tenant-checked before use — no forged-ID/IDOR path found.

**STEP 14 — frontend**: `node`/`npm` confirmed absent — `BLOCKED BY ENVIRONMENT`. No frontend
verification performed or claimed.

**STEP 19 — final repository review**: `git status`/`git diff` reviewed in full — only this
phase's two code files (`app/api/v1/webhooks.py`, the already-uncommitted `quote_service.py`), one
new test file, and five documentation files changed; no temp file, no scratch DB, no temporary
Postgres schema left behind (three isolated schemas created and dropped this phase, `public`'s 94
tables confirmed untouched each time), no generated credential file, nothing committed.

**Bottom line**: Phase 23 found and fixed a real, previously-undiscovered gap — a class of failure
(transient errors during Job creation) that was silently and permanently unrecoverable is now
correctly retryable. Live Stripe/QuickBooks provider-side verification remains blocked purely on
missing credentials; the webhook endpoint's own pre-existing, deliberate 200-on-business-failure
design (for genuinely permanent/malformed payloads, left unchanged this phase) means Stripe itself
still will not auto-redeliver a failed event — recovery today still requires an operator-triggered
manual resend from the Stripe Dashboard, which, after this phase's fix, will now actually work.

### Phase 23 continued — concurrent refund-request over-commitment

A further audit pass, focused on STEP 5's concurrency battery item "two concurrent partial
refunds," found and fixed a **second, distinct** real defect — separate from both the
webhook-recovery gap above and Phase 21's already-fixed "concurrent refund *approval*" race.

**Root cause**: `PaymentService.request_refund`'s overcommitment guard — sum all non-`REJECTED`
`Refund` rows against a `Payment`, compare against `Payment.amount` — read the Payment row with no
lock (`session.get(Payment, payment_id)`, no `with_for_update`). This is a textbook check-then-insert
race: two concurrent `request_refund` calls against the SAME payment can both read the same
`already_refunded` total before either commits, so both independently pass the check.

**Reproduction**: a direct `asyncio.gather` of 2 concurrent $60 refund requests against a $100
payment did NOT reliably reproduce the race across 10 repeated runs (each pair resolved to exactly
one success, one correctly-rejected failure) — real asyncpg connections plus asyncio's scheduling
happened to serialize that specific narrow interleaving often enough in this environment. Widening
to 5 concurrent $30 requests against the SAME $100 payment reproduced it **reliably, 8/8 runs**:
all 5 requests succeeded, producing $150 in `REQUESTED` refunds against a $100 payment. This is
reported honestly — the 2-way case is a real but narrow race window; the 5-way case demonstrates it
unambiguously and is what's fixed and tested going forward.

**Impact**: refunds are never auto-approved (a human always decides separately via
`decide_refund`), so the request-time race alone does not move money. But nothing elsewhere in the
codebase re-validates the cumulative refunded total against the payment amount at *approval* time
— `decide_refund`'s own cumulative-total computation (Phase 20's fix) is used only to set
`PARTIALLY_REFUNDED` vs `REFUNDED` status, never to block an approval that would push the total past
the payment amount. So an approver acting on more than one of several concurrently-created,
individually-plausible-looking pending refund requests (a realistic scenario in a busy refund
queue, where nothing on screen necessarily surfaces the other pending request against the same
payment) was a genuine path to Stripe being asked to refund more than the original payment actually
received.

**Fix**: `with_for_update=True` on the Payment row load in `request_refund` — the identical pattern
already established by Phase 21 for concurrent invoice overpayment in `record_payment`'s allocation
path (`app/services/payment_service.py`). A real Postgres row lock serializes the check-then-insert
across concurrent callers; SQLite has no row-level locking and silently ignores the hint, so this
fix — like Phase 21's and Phase 22's equivalents — is verified against real Postgres specifically,
not claimed from the SQLite run.

**Regression evidence**: same 5-way reproduction re-run after the fix, 8/8 times: exactly 3 of the
5 $30 requests succeed (totalling $90, within the $100 payment), the other 2 correctly rejected
with `InvalidRefundError`. Formalized as `tests/test_phase23_refund_request_concurrency.py` (2
tests: the 5-way concurrency proof, and a sequential within-bounds sanity check), re-run 3× against
real Postgres alongside the full concurrency/hardening test set (`test_phase21_accounting_
hardening.py`, `test_phase22_stripe_hardening.py`, both new Phase 23 files — 15 tests total): 15/15
passed all three runs, no flakiness. The broader affected-suite regression (refund/payment/quote/
QuickBooks-refund-sync/tenant-connection tests, 76 tests) also re-run clean.

**Other STEP 5–13 axes audited this pass and found already correct, not merely assumed, no fix
needed**: `Payment.currency` always defaults to `"USD"`, never actually threaded from Stripe
metadata or the quote/invoice's own currency — confirmed a pre-existing, system-wide, deliberate
USD-only design (nothing in the codebase exercises a non-USD currency anywhere), not a
newly-discovered defect. QuickBooks payment sync's "provider succeeds, local persistence fails"
window is already closed via a deterministic per-Payment `request_id` (Intuit's documented
write-dedup contract), already honestly marked unverifiable without live QuickBooks credentials —
not re-claimed as verified here. `QuickBooksClient`'s 401/403/429/5xx/timeout handling re-confirmed
to mirror `StripeClient`'s already-hardened classification exactly. No Refund-table uniqueness gap
found — Refund has no external provider identity of its own until `decide_refund` actually calls
Stripe, matching its existing, intentional design.

**Final counts after this fix**: SQLite **649 passed, 8 skipped, 0 failed (122.76s)** — 647 + 2 new.
Real PostgreSQL 16.2 + real Redis (fresh isolated schema `phase23e_test_run`, migrated to head
`0024`, `public`'s 94 tables confirmed untouched before/after, schema dropped and `search_path`
reset afterward): **657 passed, 0 failed, 0 skipped (535.99s)** — 655 + 2 new. `pip-audit` re-run:
unchanged, one already-accepted `ecdsa` (`PYSEC-2026-1325`) finding only. No dependency changes. No
migration this phase — the fix is pure service-layer logic; head remains `0024`.

**Final git/artifact review for this pass**: `git status`/`git diff` reviewed — only
`app/services/payment_service.py` (already-uncommitted, this fix added) and one new test file
changed in `backend/`, plus this phase's documentation edits. Secret scan of both the diff and the
untracked files directly: clean. No temp file, no scratch DB, no leftover Postgres schema (the one
created this pass was dropped and confirmed via table-count check). `.env` untouched. Nothing
committed.

**Bottom line**: this pass found and fixed a second real, previously-undiscovered concurrency bug —
refund *requests* against a single payment can no longer collectively exceed that payment's amount
under real concurrent submission, closing a genuine path to an over-refund that depended entirely
on operational vigilance rather than any system guarantee. Live Stripe/QuickBooks provider-side
verification remains blocked purely on missing credentials, not on any code concern.

### Phase 23 continued — QuickBooks persistence-failure coverage + re-verification pass

A further audit pass re-confirmed both fixes above remain intact against the current working tree
and closed one remaining test-coverage gap explicitly named by the mission's failure-recovery
matrix: "QuickBooks payment creation succeeds, then local DB persistence fails."

**What was found**: this exact scenario was already correctly handled architecturally (a
deterministic per-Payment QuickBooks `request_id`, `klaros-deposit-payment-{payment_id}`, intended
to let Intuit's own write-deduplication absorb a retried call) and already honestly documented as
such — but had never been exercised by a direct failure-injection test, only asserted in prose.

**Test added**: `tests/test_phase23_quickbooks_persistence_failure.py` builds a real paid deposit →
synced invoice, monkeypatches `QuickBooksClient.create_payment` to succeed, and monkeypatches
`AsyncSession.commit` to fail exactly once — specifically when the pending write is the
`Payment.quickbooks_payment_id` assignment following that success (not any other commit in the
setup path) — simulating a transient local DB failure immediately after the provider call
succeeds. Confirms: the exception propagates (never silently swallowed), `Payment.
quickbooks_payment_id` genuinely stays unset after the failed attempt, and a retry succeeds while
sending the IDENTICAL deterministic `request_id` both times. This proves the LOCAL half of the
recovery guarantee directly (not merely by inspection); the PROVIDER half — whether Intuit's API
actually deduplicates two calls carrying the same `request_id` — remains explicitly unverified
without live QuickBooks sandbox credentials, stated as such in the test's own docstring rather than
claimed. No code change was needed — the existing architecture was already correct; this closes a
coverage gap, not a defect.

**Stability**: the 3 Phase 23 test files (5 tests total) re-run 3× on SQLite and 3× against real
Postgres — 5/5 passed every time, no flakiness.

**Full regression after this pass**: SQLite **650 passed, 8 skipped, 0 failed (125.99s)** — 649 + 1
new. Real PostgreSQL 16.2 + real Redis (fresh isolated schema, migrated to head `0024`, `public`'s
94 tables confirmed untouched before/after): **658 passed, 0 failed, 0 skipped (694.58s)** — 657 +
1 new. `pip-audit` re-run: unchanged, one already-accepted `ecdsa` finding only. No migration this
phase — head remains `0024`. No dependency changes.

**Bottom line**: no new defect found this pass — both previously-fixed bugs (webhook failure
recovery, concurrent refund-request over-commitment) remain correctly fixed and regression-tested;
one legitimate test-coverage gap (QuickBooks local-persistence-failure recovery) closed with a
direct failure-injection test proving the existing architecture already behaves as documented.

## Phase 24 — Provider Boundary & Production Readiness Audit

**Mission**: a disciplined readiness audit focused specifically on the boundary between the
verified internal implementation and the real Stripe/QuickBooks providers — explicitly NOT a
feature-counting exercise. The mission's own rules: do not manufacture a bug to justify the phase;
be conservative; accuracy over declaring another phase complete.

**STEP 1 — current-state audit**: re-traced the actual current code (not prior phase documentation)
for the complete Stripe path (Checkout creation → PaymentIntent/Checkout → webhook verification →
`WebhookEvent` dedup → Payment creation/update → deposit-paid transition → Job creation → Invoice
creation), the refund path (refund request → CAS concurrency claim → Stripe refund → persistence →
`PAYMENT_REFUNDED` → QuickBooks refund sync), and the QuickBooks path (OAuth authorize → callback →
encrypted connection storage → token refresh → invoice sync → deposit/ordinary/split payment sync →
refund receipt sync). Every idempotency key in the lifecycle re-enumerated and re-confirmed
deterministic (Checkout, Payment uniqueness, Job, QuickBooks payment/refund `request_id`, Refund).

**STEP 2 — fresh credential audit**: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
`STRIPE_PUBLISHABLE_KEY`, `QUICKBOOKS_CLIENT_ID`, `QUICKBOOKS_CLIENT_SECRET`,
`QUICKBOOKS_REDIRECT_URI` all checked in `backend/.env` and the OS environment — all confirmed
**ABSENT**, none printed. Live-provider verification remains entirely `BLOCKED BY CREDENTIAL`.

**STEP 3 — provider contract audit, newly checked items this phase**:
- QuickBooks OAuth CSRF/state protection (`app/core/security.py::create_oauth_state_token`/
  `decode_oauth_state_token`): a real signed JWT, 10-minute expiry, binding the inbound
  `/quickbooks/callback` back to the exact tenant/user/provider that started the flow — the only
  defense against a forged or replayed OAuth callback, since Intuit's own redirect carries no
  Klaros authentication. Genuinely verified by code inspection (`app/api/v1/quickbooks_oauth.py`
  rejects any callback whose `state` doesn't decode/match).
- QuickBooks OAuth scope (`com.intuit.quickbooks.accounting`) and API base URLs
  (`sandbox-quickbooks.api.intuit.com` / `quickbooks.api.intuit.com/v3/company`, defaulting to
  sandbox on any unrecognized `QUICKBOOKS_ENVIRONMENT` value) both match Intuit's real, current,
  documented values — confirmed by direct inspection of `app/integrations/quickbooks_client.py`
  against Intuit's public API documentation (external-documentation verification, not a live call).
- Amount-unit handling: confirmed QuickBooks amounts are sent as plain decimal dollars with no
  cents conversion anywhere in `quickbooks_client.py` (no `* 100`/`/ 100`) — correct, since QBO's
  JSON API uses major-unit decimal amounts, unlike Stripe's minor-unit (cents) convention, which
  `StripeClient` correctly does convert. No amount-unit mistake found in either client.
- Credential storage re-confirmed genuinely Fernet-encrypted at rest (`app/integrations/
  credential_store.py`, AES-128-CBC + HMAC via `cryptography`'s `Fernet`) — not merely claimed.
- Every `IntegrationConnection` lookup (`IntegrationConnectionService.get_connection` and all
  sibling methods) re-confirmed tenant-scoped at the query level — no cross-tenant provider
  connection reuse path found.
- No `access_token`/`refresh_token` value found in any `logger.*` call or exception message across
  `quickbooks_client.py`.
- `refresh_access_token` reuses the same shared `_request` retry/classification logic already
  hardened for every other QuickBooks call — a revoked refresh token (Intuit's documented 400
  `invalid_grant`) correctly falls into the non-retried 4xx path, never treated as transient.

**STEP 4–9 (failure matrix, money-safety gaps, failure-injection coverage, concurrency,
migration/database, security)**: every axis named by the mission had already been covered by the
preceding Phase 21–23 audits (concurrent Job creation, concurrent refund requests/approvals,
concurrent payment allocation, cumulative refund accounting, webhook dedup/replay, QuickBooks
persistence-failure recovery, Decimal/currency handling, tenant isolation, open-redirect surface).
Re-reviewing each against the CURRENT code (not assuming prior certifications) found no
inconsistency and no newly-discovered gap. `pip-audit` re-run: unchanged, one already-accepted
`ecdsa` (`PYSEC-2026-1325`) finding only.

**No defect found this phase.** Per the mission's own explicit instruction not to manufacture a bug
to justify the phase, and given every meaningful invariant was either already tested or newly
re-confirmed correct by direct code inspection, **zero code changes were made this phase**.

**Full regression, re-run fresh against the exact final working tree, genuine process-exit
confirmed before reading results**:
- SQLite: **650 passed, 8 skipped, 0 failed (125.84s)** — identical to the pre-phase baseline.
- Real PostgreSQL 16.2 + real Redis (fresh isolated schema `phase24_test_run`, migrated to head
  `0024` before the run, `public`'s 94 tables confirmed untouched before/after, schema dropped and
  `search_path` reset afterward): **658 passed, 0 failed, 0 skipped (668.44s)** — identical to the
  pre-phase baseline, confirming zero regression from a zero-code-change phase.
- No dependency changes. No migration — head remains `0024`.

**STEP 12 — final repository review**: `git status`/`git diff` show no new files and no changed
source files this phase (only documentation) — the working tree's 80 changed/untracked items are
identical in count to the phase's starting state. Secret scan of the (documentation-only) diff:
clean. No temp file, no leftover Postgres schema, `.env` untouched. Nothing committed.

**Bottom line**: the Stripe/QuickBooks provider boundary is code-complete and internally
self-consistent as of this audit. This phase's honest conclusion is that no further meaningful
defect exists to find through code-level audit alone — the sole remaining gap to genuine production
readiness is live Stripe test-mode and QuickBooks sandbox credential access.

## Phase 25 — Live Provider Readiness

**Mission**: make the existing provider boundary maximally ready for real credentials, identify any
remaining code-level blocker to safe live verification — explicitly not a feature phase.

**STEP 1–2**: fresh baseline confirmed (80 uncommitted items, Alembic head `0024`) matching the
stated prior state. All six credentials (`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
`STRIPE_PUBLISHABLE_KEY`, `QUICKBOOKS_CLIENT_ID`, `QUICKBOOKS_CLIENT_SECRET`,
`QUICKBOOKS_REDIRECT_URI`) re-confirmed absent in `.env` and the OS environment, none printed.

**STEP 4/5 — provider-contract recheck**: fetched Stripe's current official Checkout Session
creation API reference directly and cross-checked field-by-field against the implementation —
`mode` (required), `success_url`/`cancel_url` (required in `payment` mode with the default
`hosted_page` UI, matching Klaros' usage), `metadata` (top-level map), `line_items` (required). No
discrepancy found — this is a genuine EXTERNAL-DOCUMENTATION VERIFIED check, not a code-review
assumption. QuickBooks' contract (OAuth scope, sandbox/production base URLs, decimal-dollar amount
representation) was already re-verified this way in Phase 24 and found nothing further to check
here.

**STEP 8 — real-provider adapter boundary test depth**: found a genuine, real test-coverage gap.
Every existing Stripe `MockTransport` test either checks generic client behavior (retry/error
classification in `test_stripe_client.py`) or captures only the `Idempotency-Key` header
(`test_quote_deposit.py`, `test_phase22_stripe_hardening.py`) — none decode and assert the actual
outgoing request body for Checkout Session creation or Refund creation, the two most
safety-critical writes in the whole lifecycle (what a customer is actually charged, and how much
comes back). Added `tests/test_phase25_stripe_request_shape.py`:
- `test_deposit_checkout_session_request_shape_is_correct`: asserts `POST /v1/checkout/sessions`,
  `Authorization: Basic ...`, the deterministic `Idempotency-Key`, and every field of the decoded
  form body — `mode=payment`, `unit_amount` in real Stripe cents (verified against a $75.50 deposit
  → `7550`), `currency=usd`, `metadata[tenant_id]`/`metadata[quote_id]`/`metadata[purpose]`, and
  that `success_url`/`cancel_url` are always the server-built ones (never client-supplied).
- `test_refund_request_shape_is_correct`: asserts `POST /v1/refunds`, the deterministic
  `Idempotency-Key`, and that `payment_intent`/`amount` (cents) in the body exactly match the real
  refund being processed.

Both tests **passed on the first try** — this closes a verification gap, proving no defect exists
in the actual request construction; it is not evidence of a bug found and fixed. QuickBooks'
equivalent write path was already found adequately covered
(`test_create_payment_success_carries_requestid_and_linked_txn` already asserts the requestid-bearing
URL and `LinkedTxn`/`TxnId` body content) — no duplicate test added there.

**STEP 6/7/9/10 (failure matrix, concurrency, migration, security)**: every axis had already been
covered by Phase 21–24; re-checked against the current code and found nothing further. No new
finding, no code change.

**No production-code defect found this phase.** All Phase 21–24 fixes re-confirmed intact and
unmodified.

**Full regression after the new test**: SQLite **652 passed, 8 skipped, 0 failed (129.19s)** — 650 +
2 new. Real PostgreSQL 16.2 + real Redis (fresh isolated schema, migrated to head `0024`, `public`'s
94 tables confirmed untouched before/after): **660 passed, 0 failed, 0 skipped (625.65s)** — 658 + 2
new. `pip-audit` unchanged (one already-accepted `ecdsa` finding). No migration this phase — head
remains `0024`. No dependency changes.

**STEP 14 — final repository review**: `git status`/`git diff` show one new test file plus this
phase's documentation edits; no production code touched. Secret scan of the diff and the new
untracked file directly: clean. No temp file, no leftover Postgres schema. `.env` untouched.
Nothing committed.

**Final readiness classification**: **"Internally production-ready for controlled live-provider
verification, subject to obtaining test/sandbox credentials."** No code-level blocker remains. See
`INTEGRATIONS.md`'s Phase 25 section for the exact step-by-step runbook to execute once credentials
are supplied.

## Phase 27 — Production Operations Readiness

**Mission**: audit operational production-readiness (startup, health checks, worker/EventBus
recovery, graceful shutdown) — deliberately distinct from the payment/refund-focused Phases 21–26,
and answerable entirely without Stripe/QuickBooks credentials or Node/npm.

**STEP 1 — fresh baseline**: 81 uncommitted working-tree items (unchanged from Phase 26's ending
state), Alembic head `0024` (single head), `.env` untouched, no code changes had appeared since
Phase 26.

**STEP 4 — operational trace, application → database → Redis → worker/EventBus → provider
integrations → webhook processing → background work → failure recovery**: traced `app/main.py`'s
lifespan/startup, `GET /health`/`GET /ready`, the out-of-process `event-worker`
(`app/events/worker.py`), the Temporal worker (`app/workers/main.py`), and QuickBooks OAuth's
credential-absence handling.

**A real, previously-undiscovered defect found and fixed**: `GET /ready`'s database check was only
`SELECT 1` — proving connectivity, not usability. Reproduced directly: created an isolated,
completely empty (zero-table) Postgres schema, pointed `DATABASE_URL` at it, and confirmed `/ready`
returned `200`/`{"status": "ready", "checks": {"database": "ok", ...}}` — the exact "service
reports healthy while critical functionality is unusable" failure mode the mission's STEP 4
explicitly named. `SELECT 1` references no table, so it succeeds regardless of schema state.

**Root cause**: no check anywhere compared the database's actual schema state against what the
running code expects.

**Fix** (`app/main.py`): `/ready` now also runs `_check_migration_head`, comparing the DB's
`alembic_version.version_num` against the code's expected head (resolved via
`alembic.script.ScriptDirectory` against the real `alembic.ini`) — bounded by the same
`CHECK_TIMEOUT_SECONDS` and never-raises pattern already established for the `database`/`redis`
checks. A mismatch (or a missing `alembic_version` table entirely) reports `checks.migration` with
a clear diagnostic message and drops overall status to `not_ready`/`503`.

**Necessary companion fix** (`tests/conftest.py`): the test harness's `_reset_database` fixture
builds its schema directly from `Base.metadata.create_all` rather than running real Alembic
migrations — true on BOTH SQLite and real-Postgres test runs (`DATABASE_URL` swapped, the fixture
logic is identical). Without stamping `alembic_version` at the current head there too, the new
`/ready` check would have failed EVERY test that hits it, on both engines — not because of a real
bug, but because the test DB genuinely never had that table. Since a metadata-built schema
genuinely does reflect the current code's expected head, stamping it is correct, not a workaround.

**Regression evidence**: re-ran the exact reproduction post-fix — an empty schema now correctly
returns `not_ready`/`503` with `"schema out of date: db is at None, code expects '0024'"`; a
properly `alembic upgrade head`-migrated schema returns `ready`/`200` with `"migration": "ok"`. 2
new tests (`tests/test_readiness.py`): one asserting the normal case now also includes
`"migration": "ok"`, one `monkeypatch`-ing `ScriptDirectory.get_current_head` to force a mismatch
and asserting the `503`/`not_ready` response. Both re-run 3× on SQLite and 3× against real
Postgres: 6/6 passed every time, no flakiness.

**Other operational areas audited this phase and found already correct, no defect**:
- Startup fail-fast (`app/main.py::_assert_production_secrets_are_real`) already refuses to boot
  with `ENV=production` and either the default `JWT_SECRET` or an unset
  `INTEGRATION_CREDENTIAL_ENCRYPTION_KEY`.
- The out-of-process `event-worker` (`app/events/worker.py`) already has real graceful
  SIGTERM/SIGINT shutdown (`_standalone_main`'s signal handlers → `shutdown_event`), restart
  recovery needing no special code (state lives in Postgres — `Event.status`/
  `EventProcessingRecord.attempts` — so a fresh process just resumes polling), and a single bad
  tick is caught and logged rather than crashing the loop (`run_forever`'s try/except around
  `self.tick()`).
- The Temporal worker (`app/workers/main.py`) already retries its connection with a 5-second
  backoff rather than crash-looping silently.
- QuickBooks OAuth's `/authorize` and `exchange_code_for_tokens`/`refresh_access_token` all already
  fail cleanly (503 / `QuickBooksAPIError`) on missing or partial credentials, rather than raising
  an unexpected error deep in the call stack.
- `IntegrationConnectionService` credential storage re-confirmed genuinely Fernet-encrypted; no
  access/refresh token found in any log or exception message (already checked in Phase 24, not
  re-litigated here).

**Full regression after the fix**: SQLite **653 passed, 8 skipped, 0 failed (132.86s)** — 652 + 1
new. Real PostgreSQL 16.2 + real Redis (fresh isolated schema, migrated to head `0024`, `public`'s
94 tables confirmed untouched before/after): **661 passed, 0 failed, 0 skipped (593.44s)** — 660 +
1 new. `pip-audit` unchanged (one already-accepted `ecdsa` finding). No dependency changes. No new
migration — this is a runtime check against the existing `alembic_version` table, not a schema
change; head remains `0024`.

**STEP 9 — final repository review**: `git status`/`git diff` show `app/main.py`,
`tests/conftest.py`, one modified test file (`tests/test_readiness.py`), and this phase's
documentation edits — no unrelated modification. Secret scan: clean (the migration-check's
exception messages, like the pre-existing database/redis checks, never include connection
credentials — confirmed by direct inspection of the actual reproduced error text). No temp file, no
leftover Postgres schema (three isolated schemas created and dropped this phase, `public`'s 94
tables confirmed untouched each time). `.env` untouched. Nothing committed.

**Bottom line**: this phase found and fixed a real operational gap distinct from every prior
phase's payment/refund-focused findings — the readiness probe can no longer report healthy against
a database that hasn't actually been migrated to the code's expected schema.

## Phase 29 — Transaction-Boundary & Retry-Safety Audit

**Mission**: audit transaction boundaries across external side effects and retry safety after
partial success — a new axis, deliberately distinct from Phase 28's session-reuse and
Redis-authority investigations.

**STEP 1 — baseline**: 84 uncommitted working-tree items, Alembic head `0024` (single head), `.env`
untouched, Stripe/QuickBooks credentials confirmed absent. Real PostgreSQL and Redis confirmed
**still unavailable** in this environment (nothing listening on 5432/6379; no `postgres` binary,
data directory, `brew`, `docker`, or `pg_ctl` found anywhere — the same state as Phase 28, not
restored).

**STEP 3 — the axis**: traced every important flow performing `DB mutation → EventBus publication`
and found the same structural pattern repeated throughout the codebase: the business mutation
commits inside its own `async with self._session_factory() as session:` block, that block closes,
and `await self._bus.publish(...)` is called AFTERWARD as a fully separate operation, in its own
session. This is architecturally necessary (Postgres and the transport can't share one atomic
commit) but means a failure in the publish step — after the business mutation has already,
genuinely, durably committed — is a real crash/failure window.

**Two real, previously-undiscovered defects found and fixed, same root cause in two sibling
functions**:

1. **`PaymentService.record_payment`** (`app/services/payment_service.py`): commits the `Payment`
   row, then calls `self._bus.publish(event_type=PAYMENT_RECEIVED, ...)` separately. If that
   publish fails, the exception propagates to the caller (e.g. the Stripe webhook handler's
   `try/except Exception as exc: return tenant_id, f"record_payment failed: {exc}"`), which
   reasonably retries with the same `external_id`. The retry hits `record_payment`'s own
   idempotency check (`existing is not None: return existing, True`) — which, before this fix,
   returned immediately without ever attempting the publish again.
2. **`QuoteService.mark_deposit_paid`** (`app/services/quote_service.py`): commits the quote's
   `DEPOSIT_PAID` status, then calls `self._bus.publish(event_type=QUOTE_DEPOSIT_PAID, ...)`
   before running `_convert_to_job`. A publish failure here meant `_convert_to_job` never ran on
   that attempt either. A retry (via Phase 23's own webhook-redelivery fix) correctly re-ran
   `_convert_to_job` (the Job was still created) — but the OLD `if not already_deposit_paid:` guard
   around the publish call meant `QUOTE_DEPOSIT_PAID` itself was never re-attempted.

**Reproduction, both cases**: `EventBus.publish` `monkeypatch`-forced to raise once, mid-call,
inside the real service method (SQLite — sequential failure injection, no concurrency involved, so
real Postgres was not required to prove either of these). Confirmed in both cases: (a) the
underlying business state IS durably committed despite the publish failure; (b) the event genuinely
was never published on the failed attempt; (c) — the critical finding — a SUBSEQUENT, fully
successful retry STILL never published the event, because the retry path short-circuited before
ever reaching the publish call again. For `mark_deposit_paid` specifically: the quote correctly
reached `CONVERTED` with a real `Job` on retry, masking that `QUOTE_DEPOSIT_PAID` had been silently
dropped — any downstream QuickBooks sync or notification depending on that event would simply never
fire, permanently, with no error anywhere after the first attempt.

**Fix, both cases**: publish unconditionally (fresh-creation path AND dedup/already-exists path),
relying on `EventBus.publish`'s own pre-existing idempotency-key deduplication (unmodified) to make
the repeated call safe — a genuine no-op once the event truly was already published, a genuine
recovery when it wasn't. `record_payment`'s existing `payment-received-{payment.id}` key was
reused (already present); `mark_deposit_paid` needed a NEW deterministic key,
`quote-deposit-paid-{quote.id}`, added as part of this fix (quotes can only meaningfully reach
`DEPOSIT_PAID` once in their lifecycle, so this key is safe). `record_payment`'s publish logic was
factored into a new `_publish_payment_received` helper, called from both paths, to avoid
duplicating the field-construction logic.

**Deliberately NOT touched**: `record_payment`'s later `INVOICE_PAID` publish has no idempotency
key of its own. Applying the identical fix there would require deciding a new idempotency key for
it too — a separate, lower-severity concern (this event was never proven lost by any reproduction
this phase) that risks introducing a NEW duplicate-event issue if done without the same rigor. Left
unchanged, matching the mission's "smallest correct fix" instruction.

**Regression evidence**: both reproductions re-run against the post-fix code confirm the previously
lost event now arrives on retry (`len(events) == 1` after the second call, `0` after the first
failed one), and a FURTHER retry after genuine success does not create a duplicate. 4 new tests:
`tests/test_phase29_publish_after_commit_recovery.py` (2 tests),
`tests/test_phase29_quote_deposit_paid_publish_recovery.py` (2 tests). All 4 re-run 3× — stable,
no flakiness.

**Other new-axis areas investigated this phase and found already correct, no defect**:
`EventBus.reconcile_stuck_events()` (Phase 12A's outbox-relay) already closes a narrower, different
gap — "Event row created but never enqueued to the transport." This phase's finding is a genuinely
distinct, EARLIER window (the Event row is never created at all), previously unaddressed and not
documented as a known/accepted limitation anywhere in this project's prior phases. Re-confirmed
(not re-audited from scratch, per the mission's explicit instruction) that Redis remains purely a
transport, never authoritative state; the single `asyncio.create_task` in `app/main.py` remains
properly tracked/awaited; `EventWorker.run_forever`'s crash/restart recovery remains correct
(durable Postgres state, no special code needed). FastAPI's default unhandled-exception behavior
(no custom handler, no `debug=True`) safely avoids leaking internal details. The one
`select(PaymentAllocation)` query lacking an explicit `tenant_id` filter
(`payment_service.py`, refund-reconciliation path) is safe — transitively scoped via an
already-tenant-verified `Payment.id`, not a real IDOR gap.

**Full regression**: SQLite **657 passed, 8 skipped, 0 failed (156.73s)** — 653 + 4 new. Real
PostgreSQL + Redis: **not run this phase** — `BLOCKED BY ENVIRONMENT` (infrastructure genuinely
unavailable, confirmed by direct inspection, not assumed); both fixes are sequential
retry-after-failure scenarios, not concurrency races, so this does not weaken confidence in either
fix. `pip-audit` re-run: unchanged, one already-accepted `ecdsa` (`PYSEC-2026-1325`) finding only.
No dependency changes. No migration — both fixes are pure service-layer logic; head remains `0024`.

**STEP 13 — final repository review**: `git status`/`git diff` show `app/services/payment_service.py`
(tracked, diff visible), the already-untracked `app/services/quote_service.py` (confirmed clean via
direct grep, since untracked files don't appear in `git diff`), 2 new test files, and this phase's
documentation edits — no unrelated modification. Secret scan of both the diff and the untracked
file read directly: clean. No temp file left behind (one intermediate log cleaned up). `.env`
untouched. Nothing committed.

**Bottom line**: this phase found and fixed a real, structurally-repeated defect class — a business
mutation's own DB commit and its corresponding `EventBus.publish()` call are not atomic, and this
project's existing idempotency short-circuits, before this fix, made the resulting event-loss
window PERMANENT rather than merely transient once triggered. Both known instances (the two most
event-driven paths in the Stripe payment lifecycle) are now fixed and regression-tested.
