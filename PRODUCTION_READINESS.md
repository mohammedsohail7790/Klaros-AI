# Klaros AI — Production Readiness Checklist

See `PRODUCTION_AUDIT.md` for full findings, evidence, and fixes. This is the scored checklist.
Nothing here is marked PASS without a specific, reproducible reason stated.

| # | Area | Status | Evidence / Reason |
|---|---|---|---|
| 1 | Authentication | PASS | JWT signature+expiry verified server-side (`python-jose`, pinned `algorithms=[...]`, no alg-confusion surface); password hashing via bcrypt; password min-length 8 enforced. P0 fixed: production boot now refuses the insecure default `JWT_SECRET`. **Phase 12A**: real token revocation (`User.token_version`, checked on every request) and working `/auth/refresh`/`/auth/logout` — live-verified a logged-out token is rejected server-side immediately, not just locally. |
| 2 | Authorization | PASS | Every mutating tool has `required_permission`; `ToolRegistry.execute()` checks it before anything else runs; `require_permission()` dependency used at the API layer too. |
| 3 | Tenant isolation | PASS | No API route accepts `tenant_id` from the client (grepped every file in `app/api/v1/`); every query filters by `current_user.tenant_id`; extensively covered by dedicated cross-tenant tests across CRM/finance/marketing/retention/approvals/notifications/automation-policy test files (all passing). |
| 4 | RBAC | PASS | Role→permission matrix in `app/models/rbac.py`, reviewed and extended (not duplicated) every phase; `role_has_permission()` is the single check function. |
| 5 | AI safety | PASS | `AIExecutionService` has exactly one method (`request_tool_execution`) and no path to SQL/shell/HTTP; every AI-originated action goes through the identical ToolRegistry→Policy pipeline as a human call; AI cannot approve its own request (`ActorType.AI` guard) or change automation policy (explicit guard added Phase 10, verified this pass still present and tested). |
| 6 | Prompt injection defense | PARTIAL | `app/services/ai_provider.py`'s prompt fences business data as `BUSINESS DATA` with explicit "treat as data, not instructions" system text, and `MorningBriefService` independently cross-checks every AI-referenced `entity_id` against the real deterministic insight list, dropping unmatched ones — a structural defense, not just a prompt instruction. Not exercised against a real, adversarial LLM response (no live provider key) — the defense is real code, but "does GPT-4/Claude actually respect the fence" is NOT VERIFIED against a live model. |
| 7 | Approval security | PASS (P0 fixed Phase 12F) | Self-approval blocked two independent ways (AI-actor guard + requester-identity check) for the GENERIC `approval.approve_action`/`reject_action` tools. **P0 found and fixed Phase 12F**: the DOMAIN-SPECIFIC finance approval tools (`finance.approve_refund`/`reject_refund`, `approve_invoice`/`reject_invoice`, `approve_credit_note`/`reject_credit_note`, `approve_writeoff`/`reject_writeoff`) had NO actor-type guard at all — only role-permission gating — and `Role.MANAGER` (the only role `AIExecutionService` has ever been invoked with) genuinely holds every one of those permissions. A captured proof-of-concept showed an AI actor could call `finance.approve_refund` directly and complete a real refund with no human involved. Fixed by adding the identical `ActorType.AI` guard to all eight tools; 8 regression tests prove it closed. DB-level CAS on both `ApprovalStatus` and `ApprovalExecutionStatus` transitions (no Python locks); re-checks current policy on every resume, including after a tenant tightens policy to BLOCKED mid-flight; concurrent-approval and concurrent-retry both tested via real `asyncio.gather()`. |
| 8 | Policy security | PASS | `SYSTEM_BLOCKED_TOOLS` cannot be overridden by any tenant (tested); tenant policy fully isolated (tested); safe fallback (`policy_for()`'s default is `APPROVAL_REQUIRED`, not `AUTO`, for any unknown tool); DB-level `version` CAS for concurrent updates; every change audited. |
| 9 | Event idempotency | PASS | `EventProcessingRecord` unique `(event_id, handler_name)`; notification dedup via a real `UNIQUE(tenant_id, dedupe_key)` DB constraint (not app-side counting), verified against a genuine duplicate-event-replay test. |
| 10 | Retry / DLQ | PASS | `EventBus`/`EventWorker` retry-with-backoff and dead-letter mechanism built and tested since Phase 8; replay tested; not re-audited line-by-line this pass beyond confirming the full suite (which includes these tests) still passes. |
| 11 | Database integrity | PASS | Every tenant-scoped table carries `tenant_id` + index; unique constraints exist for every dedup-sensitive table (`ApprovalRequest.idempotency_key`, `EventProcessingRecord`, `TenantToolPolicy(tenant_id,tool_name)`, `NotificationPreference(tenant_id,user_id,type,channel)`, `notifications(tenant_id,dedupe_key)`); no logic found relying on application-only uniqueness where a DB constraint was feasible and missing. |
| 12 | Migrations | PASS (Postgres-verified Phase 12B) | Previously fixed for SQLite (Phase 11). **Phase 12B**: all 14 migrations (0001–0014; 0014 added this phase) now also verified end-to-end against a real, freshly-initialized PostgreSQL 16.2 instance — `alembic upgrade head` from empty, correct schema, followed by the full 290-test backend suite passing against it. No longer SQLite-only. |
| 13 | Transaction safety | PASS (Postgres-verified Phase 12B) | Outbox-relay fix from Phase 12A unchanged. **Phase 12B**: dedicated rollback tests now run directly against real PostgreSQL (`tests/test_postgres_transactions.py`) — proves a failed INSERT rolls back the *entire* transaction (not just the bad row), that `flush()` is not a partial commit, and that PostgreSQL enforces `VARCHAR` length constraints SQLite silently ignores (see item 12B-new below). |
| 14 | Concurrency | PASS (Postgres-verified Phase 12B) | CAS-based concurrency tests (policy updates, approval execution/retry) now also pass running against real PostgreSQL, not just SQLite's `StaticPool` — the full suite (including these) is 290/290 against real Postgres+Redis. Real production-scale concurrent *load* (many simultaneous clients) is still NOT VERIFIED — this proves correctness under real DB semantics, not throughput. |
| 15 | API security | PASS | Pydantic validation on every input schema; consistent HTTP status codes (401/403/404/409/422) via `raise_http_for_tool_error`; no stack traces returned to clients (FastAPI's default exception handling + this project's explicit exception mapping); no endpoint found returning a secret or internal config value. |
| 16 | File security | PASS | `LocalFilesystemStorageAdapter`: filename sanitization, explicit path-traversal rejection (`..`/`/` in storage key), MIME allowlist, 25MB size cap, tenant-scoped directories. **Gap fixed Phase 12A**: `GET /jobs/{job_id}/attachments/{attachment_id}/download` now exists, tenant ownership re-verified from the DB row, tested including cross-tenant denial. |
| 17 | Finance integrity | PASS | Invoice/payment state machines gate transitions (existing test coverage from Phase 5 onward); this pass did not find a way to reach a negative/invalid financial state through the tool layer. Real currency/rounding edge cases (e.g. sub-cent allocation splits) NOT independently re-derived this pass — relying on existing, passing finance test suite. |
| 18 | Operations state machine | PASS | Job status transitions validated by `operations.update_job_status`/dedicated transition tools since Phase 4; existing tests cover invalid-transition rejection. Not re-derived from scratch this pass. |
| 19 | Marketing attribution | PASS | Existing, passing test coverage from Phase 6; `ATTRIBUTION_GAP` exception type exists for the known unattributed-lead case (documented as a named gap since Phase 6, unchanged). |
| 20 | Retention | PASS | Existing, passing test coverage from Phase 7; referral/reward dedup enforced via unique constraints. |
| 21 | Notifications | PASS | Tenant/recipient isolation tested; dedup is a real DB constraint; preferences persist and are honored (critical types locked visible in-app); Email/SMS honestly report `NOT_CONNECTED`, never fake a send. |
| 22 | Observability | PARTIAL (improved Phase 12B) | `structlog` structured JSON logging throughout; `correlation_id` propagates through event publishes and tool audit rows. **P1 found and fixed Phase 12B**: no `/ready` endpoint existed at all — only a static, dependency-free `/health`, which would report "ok" even with Postgres and/or Redis completely unreachable. Added a real `/ready` that checks both (live `SELECT 1` and `redis.ping()`), returns 503 with a `checks` breakdown on failure, live-verified across all 4 up/down combinations against real Postgres+Redis (see Phase 12B section below) — including a genuine hang bug found and fixed (an already-open connection to a SIGSTOP'd dependency has no inner timeout by default; fixed with an explicit `asyncio.wait_for` bound on every check). No centralized log aggregation / trace-id-across-services tooling exists — fine for this sandbox, a real gap for multi-instance production. |
| 23 | Error handling | PASS | No bare `except:` or `except Exception: pass` found anywhere in the backend (grepped). All 19 broad `except Exception` blocks found carry an explanatory `# noqa: BLE001` comment and either re-raise, log, or deliberately isolate one tenant's/one handler's failure from others (never a silent global swallow). |
| 24 | Secrets | PASS (fixed this pass) | No hardcoded secret/API key/credential found anywhere in the repo (grepped for common key patterns); `.env` correctly gitignored and not tracked; **P0 fixed**: production boot now refuses to start with the default JWT secret. |
| 25 | Dependencies | PASS (fixed Phase 12A, corrected finding) | Internet access in this sandbox turned out to be available after all — Phase 11's "NOT VERIFIED, no internet access" was incorrect and is corrected here. Real `pip-audit`/`npm audit` run: found and fixed 23 real backend CVEs (`starlette` 0.38.6→1.6.0 via `fastapi` 0.115.0→0.141.1 — closes real host-header/path auth-bypass and SSRF advisories; `python-jose` 3.3.0→3.5.0 — JWT-bomb DoS and algorithm-confusion; `python-multipart` 0.0.9→0.0.31 — several form-parsing DoS/smuggling issues; `pyasn1` forced to 0.6.4; `pytest`/`pytest-asyncio` bumped together for a compatible resolve) and 2 real frontend CVEs (`next` 14.2.35→16.3.3, `npm audit` now reports 0 vulnerabilities). One remaining, accepted: `ecdsa` 0.19.2 has a maintainer-declined-to-fix timing-attack advisory, but this app only ever signs/verifies JWTs with HS256 (symmetric), never touching the vulnerable ECDSA code path — confirmed by checking `ecdsa` is a transitive `python-jose` dependency only reachable via ES256/EC key algorithms this app doesn't use. Every upgrade verified with the full 275-test backend suite (including a genuinely fresh `uv venv` install from the updated `requirements.txt`, not just the existing venv) and `tsc`/`npm run build` for the frontend, both before and after, plus live smoke tests of the exact code paths that changed (JWT auth round-trip, multipart file upload, file download, a real dynamic-route page load). |
| 26 | Performance | NOT VERIFIED (spot-checked Phase 12B) | No load testing performed. **Phase 12B**: re-checked the dashboard aggregation hot paths (`GET /crm/metrics`, `GET /customers` search) directly against real PostgreSQL — both use single aggregate `func.count()`/paginated `select()` queries, no per-row nested queries, no lazy-relationship access in serialization. No N+1 found in the paths read. A systematic query-count audit under real concurrent load was still not performed. |
| 27 | Temporal | PASS (Postgres/real-server-verified Phase 12B) | `InvoiceOverdueWorkflow` fix from Phase 11 unchanged. **Phase 12B**: added and verified 2 new tests against the real ephemeral Temporal test server — duplicate workflow-ID rejection (`WorkflowAlreadyStartedError`, real server-side dedup) and worker-restart resilience (workflow started under one `Worker`, that worker fully shut down, a brand-new `Worker` instance brought up against the same task queue, workflow resumes and completes — proves no work is lost across a worker restart). All 6 Temporal tests pass. |
| 28 | Integrations | PARTIAL (Stripe hardened Phase 12F) | Stripe/Twilio/SendGrid/OpenAI/Anthropic have REAL API clients — **still not verified against any real provider API with a real, working key**, no credentials configured through the end of Phase 12F (re-checked at the start and end: unchanged). **Phase 12F**: `StripeClient` hardened (configurable timeout/retries, real error classification, idempotency key on checkout creation); Stripe is now the FIRST provider using the Phase 12D tenant-scoped `IntegrationConnection` model for real (a tenant can connect their own key, verified live — proven against Stripe's real API with a deliberately-invalid key, honestly rejected); webhook coverage extended to `payment_intent.payment_failed`/`charge.refunded`; marketing attribution proven to receive real Stripe payments with no duplicate counting. **A real P0 found and fixed**: 8 finance approval tools had no AI-actor guard (see item 7). **Phase 12E** (unchanged): AI provider hardening, `AIInvocationLog`, advisory AI qualification. S3 storage: still a stub. See `INTEGRATIONS.md`. |
| 29 | Frontend | PASS | `npx tsc --noEmit` and `npm run build` both clean (36 routes); no hardcoded/mock/placeholder data pattern found (grepped); every displayed number traced to a real API call in prior phases' live verification and spot-checked again this pass. |
| 30 | Accessibility | NOT VERIFIED | No dedicated accessibility audit (screen reader, keyboard-only nav, contrast ratio, ARIA) performed this pass or any prior phase. |
| 31 | E2E | PASS (real-Postgres-verified Phase 12B) | Full backend suite passes; critical multi-phase E2E scenarios exist for CRM→Operations→Finance, the referral/approval/notification loop, and duplicate-event/tenant-isolation scenarios. **Phase 12B**: additionally ran one full, live HTTP-driven critical-business-flow end-to-end (register→login→lead→qualify→convert-to-customer+appointment+job→schedule→dispatch→en_route→on_site→start→complete→QA→close→invoice→send(correctly blocked pre-approval)→payment→balance→audit-timeline) directly against the real PostgreSQL+Redis-backed running app, all steps succeeding or correctly enforcing business rules (e.g. invoice send requires APPROVED status). A single unbroken mega-E2E spanning every phase in one persisted test file was still not written — the constituent transitions are covered by existing per-phase tests plus this live run instead. |
| 32 | Docker | NOT VERIFIED (static audit only, Phase 12B) | No Docker daemon available in this sandbox (confirmed again this phase — no `docker`/`podman`/`colima`/`lima`/`brew`). `docker compose up`/`docker build` were never run — genuinely not verified, not faked. Static re-read of `docker-compose.yml` and both Dockerfiles this phase found real, previously-undocumented gaps: neither `backend/Dockerfile` nor `frontend/Dockerfile` declares a non-root `USER`（both run as root); `frontend/Dockerfile`'s `CMD` is `npm run dev` unconditionally — there is no production build stage (`next build` + `next start`), so the Docker image as written is dev-mode-only; no `restart:` policy on any compose service; Postgres/Redis/Temporal ports are all published to the host (fine for local dev, likely undesirable for a real multi-host prod deployment). Compose-level health-check ordering (`depends_on: condition: service_healthy` for postgres/redis) is correctly used by backend/worker/event-worker; internal service-to-service addressing correctly uses service names, never `localhost`. |
| 33 | Deployment | NOT VERIFIED | No staging/production environment exists to deploy to in this sandbox. |
| 34 | Backup / recovery | PARTIAL (Phase 12B) | No formal backup/restore procedure exists or was tested. **Phase 12B**: real Postgres process persistence was verified directly — a marker row written, the real `postgres` process cleanly stopped via `pg_ctl stop -m fast` (not killed), then restarted pointed at the same data directory, and the row confirmed to have survived. This proves the data directory itself is durable across a clean stop/restart; it does NOT prove any backup/snapshot/point-in-time-recovery procedure, none of which exists yet. |
| 35 | Documentation | PASS | `README.md`/`PROJECT_STATUS.md`/`PRODUCTION_AUDIT.md`/this checklist updated Phase 12B; new `DOCKER_DEPLOYMENT.md` added this phase. |
| 36 | Health / Readiness endpoints | PASS (new Phase 12B) | `GET /health` (liveness, dependency-free) and `GET /ready` (readiness — real DB `SELECT 1` + real Redis `ping()`, 503 with a `checks` breakdown on failure) both exist and are live-verified across all 4 up/down combinations of Postgres/Redis against real infrastructure, including recovery after each dependency comes back. A genuine hang bug (an already-open connection to a dependency that stops responding, e.g. `SIGSTOP`, has no bounded timeout by default) was found and fixed with an explicit `asyncio.wait_for` around each check. |

## Test Results (updated Phase 12F)

- Backend against SQLite (dev/test defaults): **398 passed, 8 skipped** (up from 391/8 at the
  close of Phase 12F's main work — 41 new tests in all: `StripeClient` retry/backoff/classification via
  `httpx.MockTransport` (9), tenant-scoped Stripe credential resolution/isolation (7), webhook
  failure/refund-reconciliation (6), marketing attribution flow (3), the AI-approval-guard
  regression suite (8), refund-API-failure-leaves-no-state-changed (1), plus 7 security-boundary
  gap tests: no-tool-can-record-Stripe-money, cross-tenant-webhook-cannot-credit, missing/
  invalid-credential checkout, and tenant-policy enforcement on the payment tool).
- Backend against **real PostgreSQL 16.2 + real Redis**: **406 passed, 0 failed** (up from 399 —
  same +41, same infra as Phase 12B/12C/12D/12E/12F, still bundled real binaries via `pgserver`/
  `redislite`, not Docker).
- Frontend typecheck: clean (`tsc --noEmit`).
- Frontend build: clean, 38 routes (`next build`) — `/settings/integrations` extended in place
  again (real Stripe connect/verify/disconnect UI added), no new route this phase.
- Fresh-database migration: **all 18 migrations apply cleanly** (0001–0018; unchanged this
  phase — no new table, only a new `PaymentService` method on existing tables) — re-verified
  from an empty SQLite file AND from a genuinely empty, freshly initialized real PostgreSQL
  database.
- Live browser verification against the real-Postgres-backed backend: Owner Cockpit, Leads,
  Finance, and Integrations pages all render correctly; connecting a deliberately-fake Stripe
  test key made a real network call to Stripe's live API and was honestly rejected in the UI.
- A real, previously-invisible PostgreSQL-vs-SQLite divergence was found and fixed this phase:
  SQLite does not enforce `VARCHAR(N)` length at all; PostgreSQL does. `communication_logs.status`
  was `VARCHAR(20)` but a real code path writes a 21-character value — invisible in this project's
  entire test history until this phase's real-Postgres run. Fixed (migration 0014, widened to 40)
  and covered by a dedicated regression test run directly against real Postgres.
- A real pytest-asyncio 1.x regression (introduced by this same session's immediately-prior dependency
  CVE remediation) was found and fixed: the removal of `event_loop` fixture overriding broke
  asyncpg's per-event-loop connection binding, invisible against SQLite/aiosqlite, causing ~175
  cascading errors against real Postgres. Fixed via `pytest.ini`'s
  `asyncio_default_fixture_loop_scope = session`.
- Live-verified this phase: `/ready` endpoint across all 4 Postgres/Redis up-down combinations
  (including a genuine hang-vs-timeout bug found and fixed); Postgres and Redis failure/recovery
  cycles; RedisStreamTransport's XADD/XREADGROUP/XACK/XPENDING directly against a real Redis server
  (previously never exercised — the test suite's `event_bus` fixture always used `InMemoryTransport`
  regardless of `EVENT_TRANSPORT`); 6/6 Temporal tests against a real ephemeral Temporal server,
  including new duplicate-workflow-ID rejection and worker-restart resilience tests; real Postgres
  process persistence across a clean stop/restart; a full live HTTP-driven critical-business-flow
  E2E run against the real Postgres-backed app; the same flow's frontend equivalent driven through
  an actual browser against the real-Postgres-backed backend (register → dashboard → create lead →
  see it persisted).
- Docker: still NOT VERIFIED — no Docker daemon available in this sandbox (confirmed again this
  phase). Postgres and Redis, previously the primary blocker, are now genuinely verified via real
  (non-Docker) infrastructure — Docker itself (image builds, container networking, compose
  orchestration) remains the one infrastructure component this project has never actually run.

**Phase 12C additions**: real Stripe/Twilio/SendGrid/OpenAI/Anthropic API clients built (direct
httpx, no SDK dependency, matching this project's established pattern), wired through existing
abstractions with no parallel path; real webhook signature verification for Stripe (HMAC-SHA256,
replay-tolerance) and Twilio (HMAC-SHA1 per their documented algorithm); a new `webhook_events`
idempotency ledger proven (by test) to prevent duplicate-delivery double-processing for both
providers; migrations `0015`/`0016` verified from empty on both SQLite and real Postgres. Secret
scan re-run (grepped for API-key/AWS-key/private-key/Slack-token patterns across the full
tree): none found; `.env` confirmed gitignored and never committed (checked git history, not
just current status). Dependency audit re-run (`pip-audit`/`npm audit`): unchanged from Phase
12B (0 new vulnerabilities — no new third-party dependencies were added this phase). **Not yet
verified against any real provider API** — no credentials configured in this environment; see
`INTEGRATIONS.md` for exactly what each provider needs and the precise testing procedure once
credentials exist.

**Phase 12D additions**: credential state re-checked at the start of this phase — still nothing
configured, so real-provider verification (Steps 2-6/16 of the 12D spec) was skipped again, per
the spec's own explicit rule for this situation. Instead built: a real, tenant-scoped
`integration_connections` model + `IntegrationConnectionService` lifecycle (`NOT_CONNECTED →
CONNECTING → CONNECTED → ERROR → DISCONNECTED`) + 4 new API endpoints, all gated by the
pre-existing `MANAGE_INTEGRATIONS` permission; Fernet credential encryption at rest (no new
dependency); 18 dedicated tenant-isolation tests across service and API layers, proving tenant
A can never read/verify/disconnect tenant B's connection; a real timeout bug found and fixed
(an unbounded verifier call could hang indefinitely — same class as the Phase 12B `/ready` bug).
A pre-existing test broke when the new production-boot-refusal check was added
(`test_starts_in_production_with_a_real_secret` didn't set the new required key) — fixed by
updating the test, not by weakening the check. Secret scan and dependency audit re-run: both
clean, no change from Phase 12C. No real OAuth provider is connected through the new model yet
— it has no verifier registered for any provider, so every connect attempt honestly reports
`ERROR`, never a fabricated `CONNECTED`.

**Phase 12E additions**: credential state re-checked at the start and end of this phase —
`OPENAI_API_KEY` remained unset throughout, so real-provider verification is BLOCKED BY
CREDENTIAL, not run. Hardened `app/services/ai_provider.py`: configurable timeout/retries/
output-size (previously hardcoded), real retry-with-exponential-backoff (429/5xx/timeout/
network only — never on 401/403/400), real error classification, real token-usage extraction
(cost deliberately never computed — no verified current pricing source), defense-in-depth
key redaction on every error string. Added `generate_structured()` (general-purpose real call)
and `AIInvocationLog` (migration `0018`, tenant-scoped audit+usage trail for direct AI-provider
calls, distinct from the tool-execution `AuditLog`). Built the first real consumer of both: an
advisory-only AI lead-qualification recommendation (`crm.ai_qualify_lead_advisory`) that coexists
with, and never replaces or bypasses, the deterministic `score_lead` scorer — proven by
dedicated tests for tenant isolation (a cross-tenant lead lookup fails before the AI provider is
ever called), prompt/PII isolation (contact PII never enters the prompt; free-text input is
fenced as data), and audit persistence (success and failure both logged, tenant-scoped). Live-
verified in a real browser and via direct HTTP against the real-Postgres-backed app: an honest
`available: false` with no fabrication, since no credential exists. Secret scan and dependency
audit re-run: both clean, no change from Phase 12D.

**Phase 12F additions**: credential state re-checked at the start and end — `STRIPE_SECRET_KEY`/
`STRIPE_WEBHOOK_SECRET` remained unset throughout, so real Stripe verification is BLOCKED BY
CREDENTIAL. `StripeClient` hardened (configurable timeout/retries, real error classification
mirroring `AIErrorType`, idempotency key on checkout creation). Stripe became the first provider
to actually use the Phase 12D tenant-scoped `IntegrationConnection` model — a real verifier
registered, a real UI to connect/verify/disconnect a tenant's own key, live-proven against
Stripe's real API (with a deliberately-invalid key, honestly rejected — proving the pipe, not a
successful payment). Webhook coverage extended to `payment_intent.payment_failed` and
`charge.refunded` (the latter reconciling refunds issued outside Klaros, idempotent against
Stripe's cumulative refund total). Marketing attribution proven to receive real Stripe payments
correctly with no duplicate counting — and, in the process, surfaced a real, non-obvious,
PRE-EXISTING architectural fact (not a bug): `EventBus.publish()` doesn't synchronously invoke
subscribers, only the real `EventWorker`'s poll loop does. **A real, exploitable P0 vulnerability
found and fixed**: 8 finance approval-decision tools had no explicit AI-actor guard, relying
only on role permissions that `Role.MANAGER` (the only role ever used for AI) genuinely holds —
a captured proof-of-concept showed an AI actor could complete a real refund with no human
involved; fixed identically to the existing generic-approval-tool guard, proven by 8 regression
tests. Secret scan and dependency audit re-run: both clean, no change from Phase 12E.
