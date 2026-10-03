# Klaros Live Halla Integration Report

Date: 2026-10-03. Scope: validate the Klaros side of the Klaros ↔ Halla integration against real infrastructure wherever it exists.
Labels used throughout: **REAL**, **MOCKED**, **CODE-REVIEW ONLY**, **ENVIRONMENT-BLOCKED**, **SKIPPED**.

## 1. Executive Summary

* **The authenticated live handshake with Halla did not happen — ENVIRONMENT-BLOCKED.** There are no Halla credentials, no `HALLA_*`
  configuration and no signing secret anywhere in the Klaros environment, and the Halla-side implementation is not present in the Halla
  repository on GitHub (last push 2026-09-22) nor in the only local checkout (`~/Desktop/halla-ai-site`, clean, identical to `origin/main`,
  no `integrations/klaros` code). Nothing was fabricated to fill that gap.
* **What was genuinely REAL:** TLS + reachability + default-deny of the deployed Halla gateway; Klaros' client and connection flow against
  that real gateway on the *failure* path (real 401 → `ERROR`, never `CONNECTED`, from a real browser session); the Klaros receiver, lead
  sync, appointment mirror, escalation, idempotency and concurrency on **real PostgreSQL under the restricted `klaros_app` role (real RLS)**
  — 36/36 checks, three consecutive runs; the event bus on **real Redis 8.10.1**; migration 0064 on real PostgreSQL (upgrade/downgrade/upgrade).
* **Two real Klaros defects were found by that live testing and fixed** (§19): (1) Klaros' own automatic lead qualification could
  silently overwrite the outcome Halla reported; (2) an escalation reported twice by Halla (inside `call.completed` and as `lead.escalated`)
  could publish two escalations and run the workflow twice. A third operational issue — a 5 s health timeout that made a healthy but
  slow-to-wake Halla gateway flap to `ERROR` — was also fixed. One further hardening: a late/out-of-order `lead.qualified` can no longer
  override a newer `call.completed` outcome.
* **Verdict: READY WITH KNOWN LIMITATIONS** (§27). Not `READY FOR LIVE INTEGRATION — REAL SUPABASE SMOKE TEST PENDING`, because the
  pending part is not only the Supabase smoke test: every authenticated Halla call is unverified.

## 2. Repository Baseline

| | |
|---|---|
| HEAD | `21d9fc5b683e5a6e9bab706cd581f3f822c8bdcf` (unchanged; no commit, no push, nothing staged) |
| Migration head | `0064` (leads external reference). Fresh PostgreSQL upgrade → downgrade → upgrade: REAL, PASS |
| Initial working tree | 84 changed paths: 12 pre-existing modified files + 3 untracked `KLAROS_*.md` (not mine, untouched) + the previous tasks' uncommitted work |
| Files newly modified by THIS validation (not dirty before) | `backend/app/events/crm_handlers.py`, `backend/app/services/qualification_service.py`, `backend/app/services/integration_connection_service.py` |
| Files further edited by this validation (already dirty) | `integrations/workforce/halla_webhook.py`, `halla_adapter.py`, `events.py`; `services/halla_event_processor.py`; `api/tool_deps_integrations.py`; `core/config.py`; `tests/test_halla_integration.py`; `tests/test_workforce_boundary.py`; `.env.example` (names only) |
| Created by this validation | this report |

## 3. Environment

| Dependency | Status |
|---|---|
| Halla gateway (public, `gateway.hallaai.com`) | **REAL — reachable, unauthenticated surface only.** TLS 1.3, certificate verified; `/health` → `{"status":"ok","service":"halla-ai-gateway"}`; `/ready` → 200 with database + redis ok; every `/api/v1/*` route (existing or not) → 401 (default-deny). Latency varies widely (measured 0.6 s, 3.3 s, 12.2 s for `/health`; the first request after idle returned no answer within 20 s) |
| Halla authenticated API (health/workforce/agents/leads/outbound) | **ENVIRONMENT-BLOCKED** — no API key, header name, Halla tenant id or signing secret available |
| Halla-side source of the contract | **ENVIRONMENT-BLOCKED** — not in the GitHub repo or local checkout |
| Klaros | REAL |
| PostgreSQL | **REAL** — local PostgreSQL via `pgserver`; restricted role `klaros_app` (RLS enforced) for the live API runs; owner role for the pytest runs (RLS bypassed — labelled) |
| Redis | **REAL** — local Redis 8.10.1 (streams). Halla validated against Redis 7.4.11; not reproduced here |
| Supabase | **ENVIRONMENT-BLOCKED** — no Supabase credentials |
| Twilio | **ENVIRONMENT-BLOCKED** — no Halla-side Twilio; no real call placed |
| Browser | REAL — desktop 1440, tablet 768, mobile 375 |

Configuration presence (names only): `WORKFORCE_ADAPTER` ABSENT · `HALLA_API_BASE_URL` ABSENT · `HALLA_API_KEY_HEADER` ABSENT ·
`HALLA_API_KEY_SCHEME` ABSENT (NOT REQUIRED) · `HALLA_ALLOWED_HOSTS` ABSENT (NOT REQUIRED) · `KLAROS_PUBLIC_API_URL` ABSENT ·
`INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` ABSENT (the insecure development default is in use locally) · `SUPABASE_*` ABSENT ·
`REDIS_URL` ABSENT in the shell (set only for the Redis runs). No secret values were read or printed.

## 4. Contract Verification

Source of truth: the contract in the task brief plus what the Klaros adapter implements. Nothing was inferred from a live Halla.

| # | Operation | Klaros implementation (CODE-REVIEW + MOCKED tests) | Live status |
|---|---|---|---|
| 1 | `GET /api/v1/integrations/klaros/health` | GET, base URL from deployment config only, credential header from `HALLA_API_KEY_HEADER`, 5xx/429/timeouts retried ≤ `HALLA_MAX_RETRIES` (health itself uses 0 retries and `HALLA_HEALTH_TIMEOUT_SECONDS`=20), redirects not followed, TLS verification on (httpx default), error text carries status only | **REAL for the failure path** (unauthenticated/invalid key → real 401 → "Halla rejected the credential"); success path ENVIRONMENT-BLOCKED |
| 2 | `GET …/klaros/workforce` | as above | ENVIRONMENT-BLOCKED |
| 3 | `PUT …/klaros/workforce` | PUT (retried: idempotent); body = camelCase business configuration (`build_halla_workforce_config`) with no secret | ENVIRONMENT-BLOCKED; **body shape UNKNOWN** |
| 4 | `GET …/klaros/agents` | tolerant parse of `agents`/`data`; only `id,name,role,status,available` are ever passed on; works without `systemPrompt`/`transferNumber` and drops them if present (tested) | ENVIRONMENT-BLOCKED |
| 5 | `POST /api/v1/leads` | POST, **never retried**, always carries `klarosLeadId`; returned id stored in `leads.external_id` | ENVIRONMENT-BLOCKED |
| 6 | `PUT /api/v1/leads/{id}` | PUT with the stored Halla id (path-segment guarded), `klarosLeadId` kept | ENVIRONMENT-BLOCKED |
| 7 | `POST /api/v1/calls/outbound` | POST, never retried; `toNumber, reason, openingContext?, klarosLeadId` | ENVIRONMENT-BLOCKED |

**UNKNOWN / ENVIRONMENT-BLOCKED contract elements** (not guessed): the credential header name (no default by design); the exact request body
of `PUT workforce`; response shapes of health/agents/leads/outbound (parsed tolerantly: `data` wrapper, `id|lead_id|leadId`,
`callSid|callId`); the webhook digest encoding (hex, optionally `sha256=`-prefixed, is accepted) and timestamp format (epoch s/ms or
ISO-8601 accepted; the HMAC always covers the timestamp header's exact text); the field names inside each event's `data` (snake and camel
case are both read: `klarosLeadId|klaros_lead_id`, `leadId|lead_id`, `callId|call_id|callSid`, `qualificationStatus|qualification|…`).

## 5. Workforce Integration
* **REAL (failure path):** browser → Klaros (live PostgreSQL, real adapter enabled, `HALLA_API_BASE_URL=https://gateway.hallaai.com`) →
  real Halla gateway over TLS: connect with an obviously invalid key → real 401 → UI "Halla rejected the credential", status "Connection
  error", form cleared, no credential in the page, none in the backend log, ciphertext in PostgreSQL contains no trace of it.
* **MOCKED:** success path, workforce GET/PUT, agents (62 tests in `test_halla_integration.py`).
* **ENVIRONMENT-BLOCKED:** real workforce configuration, agent discovery.
* Klaros sends the business context only (name, industry, description, customers, services, markets, qualification fields, escalation
  triggers, booking rules, `source:"klaros"`); no secret and no credential is in the body (tested).

## 6. Lead Create/Update — MOCKED (live ENVIRONMENT-BLOCKED)
`klarosLeadId` always sent; Halla's id stored as `external_provider="halla"`, `external_id`; Klaros' id remains the identity; second sync is
a PUT; a failed POST changes nothing; POST is never retried.

## 7. klarosLeadId Correlation
Inbound events are resolved by `klarosLeadId` first (tenant-scoped under RLS), then by the stored Halla lead id. **REAL (live PostgreSQL,
restricted role):** an event naming another tenant's lead is acknowledged and changes nothing; a Halla id already linked to one Klaros
lead is never reassigned (second event answered 200, not 500); `lead.created` with no Klaros id creates exactly one lead (idempotent on
the Halla lead id), never merged by phone alone. IDs are never inferred when ambiguous (unresolvable → acknowledged, nothing created).

## 8. Webhook Registration — ENVIRONMENT-BLOCKED
No Halla registration endpoint was available or documented to Klaros. Klaros shows the tenant the address to register
(`<KLAROS_PUBLIC_API_URL>/api/v1/webhooks/halla/{klaros_tenant_id}`) and stores the signing secret only inside the encrypted credential
(never returned by any read — REAL, verified in responses and PostgreSQL).

## 9. Webhook Signature Validation — REAL receiver, test-signed payloads
`HMAC-SHA256(timestamp + "." + raw_body)` over the exact received bytes, constant-time (`hmac.compare_digest`), freshness window
`HALLA_WEBHOOK_TOLERANCE_SECONDS` (300). Negative cases against the live receiver on PostgreSQL (restricted role), all PASS:

| Case | Result |
|---|---|
| missing signature / malformed / invalid / wrong secret | 401 |
| stale timestamp (±) / missing / malformed timestamp | 401 |
| modified body (valid signature of the original) | 401 |
| unknown Klaros tenant, no connection, disconnected | 404 (indistinguishable on purpose) |
| authentic signature, envelope `tenant_id` ≠ stored Halla tenant | 403 |
| A's secret on B's URL | 401 |
| oversized body (declared or actual) | 413 (refused before reading) |
| unsupported type incl. `appointment.requested` | 400 |
| duplicate event id | 200 `duplicate_ignored`, no second effect |

These payloads are signed by the test with the contract's algorithm: they prove Klaros verifies the contract correctly, **not** that Halla
signs it that way (that needs Halla).

## 10. Webhook Event Contract
All nine types handled (`call.started`, `call.completed`, `lead.created`, `lead.updated`, `lead.qualified`, `lead.escalated`,
`appointment.confirmed`, `appointment.rescheduled`, `appointment.cancelled`); unknown types rejected. Verified REAL on live PostgreSQL with
test-signed events; real Halla payload casing/field names UNVERIFIED (both casings read).

## 11. Qualification / call.completed Ordering
* **Live Halla call lifecycle: ENVIRONMENT-BLOCKED.** Halla's own fix (qualification persisted and `lead.qualified` emitted before
  `call.completed`) cannot be observed from Klaros without Halla.
* **REAL (live PostgreSQL, restricted role) — Klaros' behaviour for every ordering Halla could deliver:**
  * correct order (`lead.qualified` then `call.completed`) → final outcome kept;
  * **late stale `lead.qualified` after a newer `call.completed`** (a retry can still reorder deliveries): acknowledged (so Halla stops
    retrying) but ignored — ordering uses Halla's own envelope timestamps, never arrival time — and not recorded as a qualification;
  * qualification `unknown`/missing from `call.completed` (qualification-service failure on Halla's side) never demotes a prior outcome;
  * duplicate/retried delivery → no second effect; 10 concurrent deliveries of one event id → exactly 1 applied, 9 duplicates, 1 workflow run.
* **Defect found and fixed (Klaros-side):** Klaros' own automatic qualification (`lead.created` handler) runs asynchronously and used to
  overwrite `qualification_status`/`status` unconditionally — including a decision Halla (or a person) had already recorded. Reproduced
  on a freshly started backend (`REQUIRES_HUMAN` from Halla became the auto score). Fix: the automatic path (`preserve_decided=True`)
  still records the score but never overrides a decided lead; the explicit "qualify" action still always applies. Tested (SQLite + PostgreSQL)
  and re-validated live 3×.

## 12. Escalation
* Klaros reads the flat flags and the embedded `call.completed.escalation` object (explicit false inside it means no).
* **Contract concern, handled on the Klaros side without changing either side:** `lead.escalated` is a separate realtime event and can
  arrive before or after `call.completed`. Klaros now treats them as **one escalation per call** (idempotency key on lead+call), and a
  completed call never un-escalates the lead state. REAL on live PostgreSQL: both orders → exactly one `halla.lead.escalated`, state stays
  `ESCALATED`, one workflow run; duplicate delivery → one run (10 concurrent → one).
* The `call.completed.escalation` object is sufficient as final state **if Halla includes it**; whether it is deterministic for every
  transfer is a Halla question (UNKNOWN). Transfer-target persistence is Halla-side; Klaros does not store it.

## 13. Appointment Synchronization — REAL (live PostgreSQL), test-signed
Halla is the booking authority; Klaros mirrors one `Appointment` per Halla appointment id (`external_provider="halla"`), reschedule updates
it, cancel cancels it; two deliveries → one row; a booking without a time is not invented; **no core `appointment.created` is ever
emitted** (0 rows on live PostgreSQL) and nothing is sent back to Halla (tested).

## 14. Outbound Calls — MOCKED; OUTBOUND LIVE TEST BLOCKED
Request contract (`toNumber, reason, openingContext, klarosLeadId`) validated against a mock transport; leads without a phone number are
refused (422); the UI requires an explicit confirmation before asking Halla to call. No call was placed (no Halla, no Twilio, no permission).

## 15. Redis Retry/Reclaim/DLQ — REAL Redis 8.10.1
`tests/test_halla_redis_events.py` (skipped unless `REDIS_URL`/`EVENT_TRANSPORT` point at a real Redis ≥ 5): a verified Halla escalation travels
through a real Redis stream to the existing automation dispatcher and runs the workflow once; a redelivered webhook never re-publishes; a
consumer that always fails is retried a bounded number of times and then dead-lettered while the lead update is never lost. 2/2 PASS.
Not covered here: reclaim of an idle pending message by a second worker (Halla's own Redis path was validated by Halla, on 7.4.11).

## 16. Database Persistence — REAL (live PostgreSQL)
`leads.external_provider/external_id` unique per tenant (verified: a Halla id links to exactly one lead); `appointments` mirror rows;
`webhook_events` rows `PROCESSED` (160+ across runs), **0 `FAILED`**; `integration_connections` ciphertext contains no key/secret; Halla
tenant id stored in `external_account_id`.

## 17. RLS / Tenant Isolation — REAL (restricted role `klaros_app`)
Mandatory checks, all PASS: tenant B cannot read A's connection status, lead interaction, workflow runs, leads board; B's Halla tenant
id/secret naming A's lead changes nothing; A's secret on B's URL rejected; Halla tenant mismatch 403; AI counts/workflows of B unaffected;
connect without auth rejected; connect requires `MANAGE_INTEGRATIONS` (403 otherwise).
RLS bug found earlier in the integration work (and fixed): `webhook_events` is RLS-enforced and the receiver must set the verified tenant
context — invisible on SQLite, caught only on PostgreSQL as `klaros_app`.

## 18. Frontend — REAL browser, 375 / 768 / 1440
Workforce, lead detail, Business Home, Integrations: no overflow, no console errors, no null/undefined, no secret in the DOM, secret fields
cleared after every attempt, no Halla URL in the page, status honest (`Connection error`, never `Connected`), Halla actions absent while not
connected. 268 frontend tests (MOCKED APIs) pass; they are not evidence of live integration.

## 19. Security Audit
CODE-REVIEW + REAL where noted. Passed: no logging of key/secret/auth header (log scan + real backend log inspected: 0 occurrences); ciphertext
only at rest (REAL); credential never returned (REAL); base URL is deployment config, https enforced in production, no userinfo/query,
host allow-list, redirects not followed, TLS verification default on; raw-body HMAC, constant-time compare, freshness window, replay
protection (event-id idempotency, tenant-scoped); tenant from the stored connection, never from payload/`x-tenant-id`; external ids cannot
be reassigned; POST never retried; per-tenant credentials only.
**Defects found and fixed in this validation (Klaros-side):** (a) automatic qualification overwrote Halla's decision; (b) double
escalation → double workflow; (c) late stale qualification could win; (d) health timeout (5 s) flapped a healthy slow gateway to ERROR —
now `HALLA_HEALTH_TIMEOUT_SECONDS` (20 s) with a per-provider verifier timeout in `IntegrationConnectionService`.
**Residual (not Klaros-fixable):** two Klaros tenants claiming the same Halla tenant id with a real key for it can only be prevented by Halla.

## 20. Automated Tests

| Run | Label | Result |
|---|---|---|
| Full backend suite (SQLite; Redis tests skipped by default), `--ignore=tests/test_postgres_agent_recovery.py` | MOCKED/SQLite | **1895 passed, 392 skipped, 0 failed** (previous release baseline: 1885 passed, 392 skipped) |
| `tests/test_halla_integration.py` | MOCKED Halla HTTP + real Klaros code (SQLite) | 62 tests, all pass |
| Halla + workforce + operations + Medical Tourism + guard/migration tests on PostgreSQL (owner role) **with real Redis** | REAL PG (RLS bypassed) + REAL Redis | **104 passed**, 0 failed, 0 skipped |
| Live API as `klaros_app` (`pg_halla.py`, 36 checks) | **REAL PG with RLS**, Halla not contacted (unreachable host) / real gateway | **36/36**, run 3× consecutively after the fixes |
| Redis tests without `REDIS_URL` | SKIPPED | 2 skipped, as designed |
| Frontend (vitest) | MOCKED | **268 passed** / 34 files |
| `tsc --noEmit` | — | PASS |
| `next build` | — | PASS |


## 21. Build / Typecheck / Lint
Typecheck PASS · build PASS · lint: **PRE-EXISTING, not runnable** — `npm run lint` is `next lint`, which Next 16 removed (the project's own
`vitest.config.ts` notes it) and there is no ESLint config; not caused by this work. No backend linter is configured.

## 22. Migration Validation
Klaros head `0064` (adds `leads.external_provider/external_id` + unique constraint, batch-safe for SQLite; fresh PostgreSQL upgrade →
downgrade → upgrade REAL PASS; also applied to the existing database). **Halla's migration 070: UNKNOWN / ENVIRONMENT-BLOCKED** — its source
is not available, so ordering/compatibility with Klaros cannot be verified. Klaros needs no Halla migration; Halla needs none from Klaros
(no shared database).

## 23. npm Audit
`npm audit`: 10 vulnerabilities — 2 critical (`next`, `vitest`), 6 high (`braces`, `chokidar`, `fast-glob`, `micromatch`, `tailwindcss`,
`vite`), 2 moderate (`esbuild`, `vite-node`). **PRE-EXISTING**: `package.json`/`package-lock.json` are unchanged by this work (no dependency was
added or upgraded); fixes are available but were not applied automatically.

## 24. Real Supabase Smoke Test — ENVIRONMENT-BLOCKED
Missing: Halla-side Supabase/service credentials, a test-tenant API key with its scopes, the header name the key travels in, the Halla tenant
id, and the webhook signing secret from the allowed creation flow. None of the 14 smoke-test steps was performed.

## 25. Final Live E2E — ENVIRONMENT-BLOCKED
Not executed (needs a live Halla tenant and call). Every Klaros-side stage after "Halla event received" was exercised REAL on PostgreSQL with
test-signed events; every Klaros → Halla stage was exercised against a MOCKED transport only.

## 26. Known Limitations

| Limitation | Class |
|---|---|
| No authenticated live Halla validation (health/workforce/agents/lead create/update/outbound/webhook registration) | ENVIRONMENT-BLOCKED |
| Credential header, `PUT workforce` body, response/event field names, digest/timestamp encodings unverified against Halla | ENVIRONMENT-BLOCKED |
| Halla migration 070 compatibility; Supabase smoke test; real call lifecycle ordering; real outbound call | ENVIRONMENT-BLOCKED |
| Public gateway latency varies 0.6–12 s (Render idle wake-up): a passive status re-check on a stale `CONNECTED` can block a page load up to 20 s once per `HALLA_STATUS_TTL_SECONDS`, and an idle gateway can still answer nothing within the first request | NON-BLOCKING (operational; honest ERROR, recovers on the next check) |
| Two Klaros tenants can claim the same Halla tenant id if they hold a real key for it — Halla must prevent it | NON-BLOCKING (Halla-side) |
| Redis reclaim by a second worker not re-tested on Klaros' side; Halla's own Redis path validated by Halla on 7.4.11, here 8.10.1 | NON-BLOCKING |
| `npm run lint` not runnable (Next 16 removed `next lint`); 10 npm advisories incl. 2 critical | PRE-EXISTING |
| Halla call summaries are stored in the Klaros event table (tenant-scoped); transcripts are not | NON-BLOCKING (by design) |

No BLOCKING limitation is attributable to Klaros: the no-go list (§29 of the brief) was not triggered by any observation.

## 27. Final Verdict

**READY WITH KNOWN LIMITATIONS**

## 28. Exact Next Action

Give Klaros staging a Halla **test tenant**'s API key, the header name that key travels in, the Halla tenant id and the webhook signing
secret (the creation-time value), set `WORKFORCE_ADAPTER=halla`, `HALLA_API_BASE_URL=https://gateway.hallaai.com`,
`HALLA_API_KEY_HEADER=<that header>` and `KLAROS_PUBLIC_API_URL`, connect the test tenant from the Workforce page, and re-run this validation
(§§4–14, 24, 25) — it will then convert every ENVIRONMENT-BLOCKED row above to REAL PASS or a concrete Halla-side defect.
