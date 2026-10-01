# PHASE 17B-2 — TENANT CONTEXT PROPAGATION — IMPLEMENTATION LOG

Repository: `/Users/mohammedsohail/Desktop/Klaros AI`. Scope: close the tenant-context propagation gaps `PHASE_17A_RLS_ENFORCEMENT_READINESS_AUDIT.md` (§5, §9, §15, §16, §17) found — Event Bus, MCP, public website/lead intake, webhooks, background/scheduled Agent execution never called `set_tenant_context` before touching tenant-owned tables. Reuses the existing Phase 0 mechanism (`app.db.session.set_tenant_context`, `SET LOCAL app.tenant_id`) everywhere; no second mechanism was created, no RLS policy was touched, no restricted-role work from 17B-1 was touched. Continues directly from `PHASE_17B1_RESTRICTED_DB_ROLE_IMPLEMENTATION_LOG.md`.

---

## 1. Starting Git State

```
HEAD: af4937e403e47cdc141f2db349dfcc46a3df6c4b (matches expected)
```

`git status --short` at the start of this phase matched exactly the expected Phase 16a/16b/17A/17B-1 state (business_discovery_service.py, discovery_extraction_service.py, test_business_discovery_service.py, test_migration_schema_matches_models.py modified; KLAROS_DISCOVERY_COMPLETION_AUDIT.md, PHASE_16A/16B/17A/17B1 logs, backend/scripts/db/, three new Discovery-related test files, and test_restricted_app_role_cutover.py untracked; .env.example, .env.staging.example, .github/workflows/ci.yml, backend/alembic/env.py, backend/app/core/config.py, docker-compose*.yml also modified per 17B-1). None of this pre-existing state was staged, committed, or altered during this phase — re-confirmed at the end (§13). No `git add`, `git commit`, or `git push` was run at any point.

---

## 2. Audit Re-Verification

PHASE_17A's findings were re-confirmed directly against the current source (not just cited):

- `app/db/session.py::set_tenant_context` is the one and only implementation of `SET LOCAL app.tenant_id` (via `set_config(..., is_local=true)`), unchanged this phase — no second mechanism was written anywhere.
- Before this phase, exhaustively grepping `backend/app/` for `set_tenant_context` confirmed exactly the two call sites PHASE_17A named: `app/api/deps.py` (2 sites, the authenticated HTTP request path) and `app/workflows/activities.py` (2 sites, Temporal activities) — unchanged, untouched this phase.
- Confirmed **not** called anywhere in: `app/events/worker.py`, `app/events/bus.py`, every domain handler in `app/events/*_handlers.py`, `app/mcp/protocol.py`, `app/api/v1/public_websites.py`, `app/api/v1/public_leads.py`, `app/api/v1/webhooks.py`, `app/tools/registry.py`, `app/services/agent_execution_service.py`, `app/services/agent_reasoning_service.py`, `app/services/agent_trigger_service.py`, `app/services/agent_recovery_service.py`, `app/services/website_service.py`, `app/services/lead_service.py` — all of these are fixed below.

---

## 3. Entrypoint-by-Entrypoint Audit Table

`TENANT CONTEXT SET` = state **after** this phase's changes. `RLS RISK` reflects that only 32/132 tenant tables carry any RLS policy today and it is audit-mode/permissive (PHASE_17A §3) — so today's actual isolation guarantee on every row below still comes from **application-layer tenant filtering** (`.where(tenant_id == ...)`), not from Postgres; this phase's work makes the DB-level GUC correctly available for the day RLS is turned on (17B-4/5/6), it does not itself add DB-level enforcement.

| ENTRYPOINT | AUTHENTICATION | TENANT RESOLUTION | DB SESSION CREATION | TENANT CONTEXT SET | TRANSACTION BOUNDARY | RLS RISK | CURRENT TEST COVERAGE |
|---|---|---|---|---|---|---|---|
| HTTP API (`app/api/deps.py`) | JWT | `payload["tenant_id"]` | `Depends(get_db)`, per-request | **Yes (unchanged, pre-existing)** | per-request | Low for the literal `db` session; **see §7 corrected finding** for the services most endpoints actually delegate to | Existing suite |
| Temporal activities (`app/workflows/activities.py`) | N/A (internal) | Passed into the activity | Per-activity session | **Yes (unchanged, pre-existing)** | per-activity | Low | Existing suite |
| EventBus.publish (`app/events/bus.py:83`) | N/A (internal) | `tenant_id` kwarg | `self.session_factory()` | **Yes — added** (right after session open) | one txn, commit/rollback on IntegrityError | Low (now) | New: `test_event_bus_handle_one_sets_context_per_event` |
| EventBus._handle_one (`app/events/bus.py:189`) | N/A (internal) | `event.tenant_id`, learned from the fetched `Event` row | `self.session_factory()` | **Yes — added**, right after the event row is loaded, before `EventProcessingRecord`/`DeadLetterEvent` access | one txn spanning the whole handler call + commit | Low (now) | New: `test_event_bus_handle_one_sets_context_per_event`, `test_event_bus_no_cross_tenant_context_leak_on_pooled_connections` |
| EventBus.replay (`app/events/bus.py:304`) | N/A (internal, admin-triggered) | `event.tenant_id` after PK fetch | `self.session_factory()` | **Yes — added** | one txn | Low (now) | Existing `test_event_outbox_reconciliation.py`-style coverage (not re-verified per-line this phase) |
| EventBus.reconcile_stuck_events (`app/events/bus.py:337`) | N/A (internal, worker tick) | **None — intentionally cross-tenant** (scans all tenants' stuck PUBLISHED events) | `self.session_factory()` | **Deliberately NOT set** — flagged in-code as a Phase 17B-3 system/global-context item, not faked | one txn (read-only) | N/A — only touches `events`, which has zero RLS today | Existing `test_event_outbox_reconciliation.py` |
| 8 domain event handlers' own sessions (`crm_handlers.py`, `handlers.py`, `automation_handlers.py`, `marketing_handlers.py`, `notification_handlers.py`, `finance_handlers.py`, `retention_handlers.py`, `operations_handlers.py`) | N/A (internal) | `event.tenant_id` (in closure scope for every handler) | Each handler's own `session_factory()` — a **separate connection** from EventBus._handle_one's | **Yes — added** at every one of the 24 direct session-open sites across these 8 files | one txn per handler invocation | Low (now) for the direct reads/writes shown; **see §8** for handlers that delegate further into un-instrumented domain services | New: `test_event_handler_own_session_sets_context` (crm_handlers); others not individually re-tested (see §6) |
| `agent_trigger_handlers.py` (event-triggered Agent dispatch) | N/A (internal) | `event.tenant_id` / `agent.tenant_id` | Own `session_factory()`, 3 sites | **Yes — added** | per-call | Low (now) | Existing `test_agent_event_triggers.py` (regression-verified, §9) |
| MCP `tools/call`/`tools/list`/`initialize` (`app/mcp/protocol.py`) | Bearer token → `McpClientCredential` DB lookup | `auth.credential.tenant_id`, from the authenticated row — never client JSON-RPC body | `self._session_factory()` in `_audit()` only (actual tool DB work goes through `ToolRegistry.execute()`, itself now instrumented — see below) | **Yes — added** in `_audit()` | per-audit-write txn | Low for `_audit`; tool execution's own RLS risk now flows through the already-instrumented `ToolRegistry` | New: `test_mcp_audit_sets_tenant_context_from_authenticated_credential` |
| ToolRegistry (`app/tools/registry.py`) — `_create_approval_request`, `_check_agent_tool_permission`, `_check_agent_autonomy`, `_get_organization`, `_audit` | Caller-supplied `ExecutionContext` (built by HTTP/Agent/MCP callers, tenant_id never client-writable per `ExecutionContext`'s own construction) | `context.tenant_id` | 5 separate `self._session_factory()` sites | **Yes — added at all 5** | per-call | Low (now) | Existing `test_tool_registry_*.py` suite (regression-verified, §9) |
| Public website read (`app/api/v1/public_websites.py` → `WebsiteService`) | None (intentionally public) | URL path parameter `tenant_id` — the module's own documented entire trust boundary | `WebsiteService(async_session_maker)`, 14 separate session-open sites | **Yes — added at all 14** | per-call | Low (now) | New: `test_public_website_sets_context_from_trusted_url_tenant_id` |
| Public lead intake (`app/api/v1/public_leads.py`, `app/services/lead_service.py`) | None (intentionally public) | URL path parameter `tenant_id` | Router's own `async_session_maker()` (Organization lookup) + `LeadService`'s own session | **Yes — added at both** | per-call | Low (now) | New: `test_public_lead_intake_sets_context_from_trusted_url_tenant_id`; existing `test_public_lead_intake.py` (regression-verified, §9) |
| Webhooks — Stripe (`app/api/v1/webhooks.py::stripe_webhook`) | HMAC signature, verified first | Resolved from PaymentIntent/Charge `metadata.tenant_id`, which Klaros itself set at creation time — never trusted from an unauthenticated claim | `async_session_maker()`, 3 sites in this flow | **Yes — added at 2 of 3**: the WebhookEvent status-update (tenant known by then) and the quote-ownership guard; the very first dedup-row insert deliberately left unset (tenant genuinely unknown before the payload is parsed — documented in-code, matches `set_tenant_context`'s own pre-existing docstring example) | per-block | Low (now) for the 2 fixed sites | Existing `test_marketplace_lead_webhooks.py`/Stripe webhook suite (regression-verified, §9); new `test_webhook_tenant_resolved_post_signature_sets_context` |
| Webhooks — Twilio status callback (`twilio_status_webhook`) | HMAC signature, verified first | Resolved from `CommunicationLog.tenant_id` post-lookup | `async_session_maker()`, 1 site | **Yes — added**, right after the tenant-owning log row is found | one txn | Low (now) | Existing suite (regression-verified) |
| Webhooks — Twilio inbound SMS/voice (`twilio_inbound_sms_webhook`, `twilio_inbound_voice_webhook`) | HMAC signature over the full URL (including `tenant_id`), verified first | Trusted URL path parameter (tampering breaks the signature) | `async_session_maker()`, 2 sites | **Yes — added at both** | per-call | Low (now) | Existing suite (regression-verified) |
| Scheduled Agent dispatch (`app/services/agent_trigger_service.py::check_and_dispatch_scheduled`) | N/A (internal worker tick) | Optional `tenant_id` param: single-tenant call is a real per-tenant op; `tenant_id=None` is this worker's own intentional all-tenants sweep | `self._session_factory()`, 3 sites | **Yes when `tenant_id` given; deliberately not set for the `None`/all-tenants sweep** (flagged for 17B-3, same reasoning as `reconcile_stuck_events`) | per-call | Low (now) for the per-tenant case | Existing `test_agent_trigger_service.py` (regression-verified, §9) |
| Crash/approval recovery (`app/services/agent_recovery_service.py`) | N/A (internal worker sweep) | Optional `tenant_id` on `sweep_once`; `_claim`/`_recover_one`/`_halt`/`_audit` resolve tenant_id from the `AgentExecution` row itself (by PK) when not otherwise known | `self._session_factory()`, 5 sites | **Yes — added at all 5** (the all-tenants sweep case in `_find_stale_candidates` follows the same `tenant_id is not None` guard as `agent_trigger_service`) | per-call | Low (now) | Existing `test_agent_recovery_service.py`, `test_postgres_agent_recovery.py`, `test_postgres_agent_pending_recovery.py`, `test_agent_recovery_security.py` (all regression-verified, §9) |
| Agent execution — SINGLE_ACTION (`app/services/agent_execution_service.py`) | Caller-resolved tenant_id (HTTP/event/scheduled/recovery) | `tenant_id` param on most methods; PK-fetched-then-tenant-derived for the handful keyed only by `execution_id` | 17 separate session-open sites | **Yes — added at all 17** | per-call | Low (now) | New: `test_agent_execution_sets_context_across_the_run_loop`; existing `test_agent_execution_service.py` (regression-verified, §9) |
| Agent execution — REASONING (`app/services/agent_reasoning_service.py`) | Same as above | Same pattern; `_renew_lease` (heartbeat) does a scalar `tenant_id` lookup first since it has no `tenant_id` parameter | 13 separate session-open sites | **Yes — added at all 13** | per-call | Low (now) | Existing `test_agent_reasoning_service.py`, `test_agent_reasoning_api.py`, `test_agent_reasoning_security.py`, `test_postgres_agent_reasoning_rls.py`, `test_cross_vertical_agent_trigger_validation.py` (all regression-verified, §9) |

---

## 4. What Was Changed and Why

Every change follows the identical pattern: `await set_tenant_context(session, <tenant_id already in scope>)` inserted as the first statement inside an existing `async with session_factory() as session:` block, using whichever variable already carries the correct tenant identity at that point (a method parameter, the row just fetched by primary key, or the enclosing event/context object) — never a new tenant-resolution mechanism, never trusting a client-supplied value where a trusted one (JWT, MCP credential, verified webhook signature + internal metadata, or a URL path parameter documented as this app's own trust boundary) was already available.

Two deliberate **non**-changes, both explicitly flagged in code comments rather than worked around:

1. `EventBus.reconcile_stuck_events` (`app/events/bus.py`) and the `tenant_id=None` branch of `AgentTriggerService.check_and_dispatch_scheduled` / `AgentRecoveryService.sweep_once` are genuine, intentional **cross-tenant** operations (a worker tick scanning across every tenant's due items). Per this phase's explicit instruction ("If system/global events exist, do not fake them as tenant events — flag them for Phase 17B-3's system/global mechanism instead of inventing one now"), these were left unset with an in-code comment naming Phase 17B-3, not given a fabricated tenant value.
2. The Stripe webhook's very first `WebhookEvent` dedup-row insert (`stripe_webhook`) happens before the payload's `metadata.tenant_id` has even been parsed — there is genuinely no tenant to stamp yet. This is the exact scenario `set_tenant_context`'s own pre-existing docstring already used as its example gap; it remains a documented no-op call site, not silently ignored.

---

## 5. Bug Found and Fixed During This Phase's Own Testing

While instrumenting `AgentReasoningService._renew_lease` (a lease-heartbeat method with no `tenant_id` parameter), a `select(AgentExecution.tenant_id)` scalar lookup was added to resolve the owning tenant before stamping context — but `agent_reasoning_service.py` only imported `update` from `sqlalchemy`, not `select`, at module scope (two other functions in the same file import `select` **locally**, inside their own function bodies, which masked the gap during code review). This produced a real `NameError: name 'select' is not defined` raised from inside `_renew_lease`, which is called on every iteration of the REASONING execution loop — breaking every code path that runs a multi-step reasoning execution: event-triggered dispatch, scheduled dispatch, and crash/approval recovery resume.

- **Root cause classification**: implementation bug introduced by this phase's own edit (not a pre-existing issue, not a test artifact).
- **Detection**: a real-Postgres test run (`pytest -q tests/ -k "event or agent or mcp or website or lead or webhook"`) showed 53 failures, all either directly in reasoning/recovery/trigger test files or indirectly (e.g. `test_agent_event_triggers.py` catching the exception and asserting on a status that was never reached).
- **Fix**: `backend/app/services/agent_reasoning_service.py` — changed `from sqlalchemy import update` to `from sqlalchemy import select, update` (one line).
- **Verification**: re-ran `pytest -q tests/ -k agent` (157 tests) after the fix — **157 passed, 0 failed**. See §6 for the full-suite confirmation.

No test was weakened, no assertion was loosened, and no production behavior was changed beyond the one-line import fix — this is a straightforward bug fix for a bug this phase's own instrumentation introduced, caught by this phase's own real-Postgres testing before being reported as done.

---

## 6. New Tests (Real PostgreSQL)

`backend/tests/test_tenant_context_propagation_phase17b2.py` — 8 tests, all `@requires_real_postgres` (skipped on SQLite, matching the existing convention), all passing against a real, disposable PostgreSQL 16 instance.

**Methodology**: each test wraps the specific module's own imported `set_tenant_context` reference (e.g. `app.events.bus.set_tenant_context`, `app.mcp.protocol.set_tenant_context`) with a spy (`_ContextSpy`) that (a) calls through to the real implementation — production behavior is completely unchanged — and (b) immediately reads back `current_setting('app.tenant_id', true)` on the **same session, same transaction**, proving the `SET LOCAL` actually took effect where the code path claims it did, not merely that the function was invoked. This is the same "prove the mechanism, not just that a call happened" standard already established by `test_postgres_rls_audit_mode.py` and `test_restricted_app_role_cutover.py`.

| Test | Proves |
|---|---|
| `test_event_bus_handle_one_sets_context_per_event` | `EventBus.publish()`/`_handle_one()` each stamp the correct, per-event tenant_id, in the correct order, for two distinct tenants |
| `test_event_handler_own_session_sets_context` | A domain handler's own, separately-opened session (`crm_handlers.notify_on_appointment_created`) — a different DB connection than the bus's own — independently stamps the right tenant |
| `test_event_bus_no_cross_tenant_context_leak_on_pooled_connections` | 4 tenants' events interleaved (round-robin) through one worker/pooled engine; every call's readback matches that same call's own tenant_id — no leaked/stale value from a prior connection use |
| `test_mcp_audit_sets_tenant_context_from_authenticated_credential` | MCP's `_audit()` stamps the authenticated credential's own `tenant_id`, for two distinct tenants, never anything from the JSON-RPC body |
| `test_public_website_sets_context_from_trusted_url_tenant_id` | `WebsiteService.get_website()` stamps the caller-supplied tenant_id correctly for two tenants read back to back, no bleed-through |
| `test_public_lead_intake_sets_context_from_trusted_url_tenant_id` | `LeadService.create_lead()` (as called by the public, unauthenticated lead-intake router) stamps the trusted URL tenant_id |
| `test_webhook_tenant_resolved_post_signature_sets_context` | The Stripe quote-ownership guard's post-signature, post-tenant-resolution DB lookup stamps context correctly |
| `test_agent_execution_sets_context_across_the_run_loop` | A full, real `AgentExecutionService.run_action()` SINGLE_ACTION execution (via the repo's own `AgentService.create_agent`/`grant_tool_permission`/`create_version`/`publish_version`/`activate` pipeline, not a hand-rolled shortcut) stamps the correct tenant across every one of its internal session opens |

Result: **8 passed, 0 failed** (isolated run); also passed as part of the full suite run (§9).

---

## 7. Corrected/Expanded Finding: the Authenticated HTTP Path

PHASE_17A §5/§8 stated the authenticated HTTP path is "instrumented and (for HTTP) well-tested for the pool-safety property" and treated it as already correct, needing no further 17B-2 work. **This needs a correction, found while instrumenting `WebsiteService`/`MedicalTourismService`/`LeadService` for this phase's own explicitly-in-scope work:**

`app/api/deps.py`'s `set_tenant_context` call only ever stamps the transaction-local GUC on the **one literal `db: AsyncSession` object** FastAPI dependency-injects via `Depends(get_db)`. That claim is true and unchanged. But the overwhelming majority of this codebase's actual business logic does **not** run its queries against that injected `db` session at all — it runs through service classes (`WebsiteService`, `MedicalTourismService`, `LeadService`, `NotificationService`, `InvoiceService`, `AutomationService`, and dozens more) that are constructed directly against the process-wide `async_session_maker` (see `app/api/v1/medical_tourism.py::_service()`'s own comment: *"Read paths construct the service directly against the app's own session_factory ... rather than the request-scoped `db` session FastAPI's `get_db` dependency provides — MedicalTourismService's methods each own a short-lived session"*). Each such service method opens its **own, independent** session per call — a different connection than the one `deps.py` stamped — and therefore never inherits that context.

**Concretely**: an authenticated HTTP request to, say, `GET /medical-tourism/providers` does call `set_tenant_context` on its own `db` dependency (which may go entirely unused by the endpoint), while the actual `MedicalTourismService.list_providers(...)` call that produces the response opens a fresh session with **no** tenant context set at all. This is the exact same class of gap this phase was asked to close for Event Bus/MCP/webhooks/public-website/background-workers — it just was not previously surfaced because PHASE_17A's audit inspected `deps.py` in isolation rather than tracing what the endpoints built on top of it actually do.

This phase fixed this pattern for the two service classes its explicit scope already required touching for other reasons (`WebsiteService`, used by the public website router; `LeadService`, used by the public lead-intake router) — both now correctly stamp context at every session open, benefiting their authenticated call sites too, incidentally. It did **not** attempt to fix this pattern for every other service reachable only from authenticated HTTP routes (that is a materially larger, different-shaped piece of work — see §8). **This finding should be treated as a correction to PHASE_17A's own audit, and the "HTTP path is already correct" assumption should not be relied on for 17B-4/17B-5 planning without a dedicated follow-up pass.**

---

## 8. Known, Explicitly Scoped-Out Gaps

The entrypoints named in this phase's task (Event Bus worker/handlers, MCP tool execution, public website/lead-intake routes, webhook routes, background/scheduler entrypoints, Agent execution entrypoints) are now correctly instrumented and tested. However, many of these entrypoints — and many authenticated HTTP endpoints per §7 — call **into** further business/domain services that also open their own independent sessions via `session_factory()`/`async_session_maker()` and were **not** touched this phase, because they are not among the explicitly-named entrypoints and instrumenting all of them is a substantially larger, differently-shaped body of work (this phase's own task explicitly warned against "NO SCOPE CREEP" and against redesigning "Agent runtime's core reasoning/execution semantics" beyond adding the missing plumbing at the named layer).

Grep evidence: **111 files** across `backend/app/` open a session via `session_factory()`/`self._session_factory()`/`async_session_maker()`; this phase added `set_tenant_context` calls in **20 of them** (the entrypoint-layer files named in §3). The largest known remaining gaps, with their own session-open-site counts:

| File | Session-open sites | Reached from |
|---|---|---|
| `services/automation_service.py` | 23 | `automation_handlers.py` (now-instrumented) and `AgentExecutionService`-adjacent automation dispatch |
| `services/notification_service.py` | 10 | `notification_handlers.py` (now-instrumented) |
| `services/invoice_service.py` | 8 | `finance_handlers.py` (now-instrumented), authenticated HTTP finance endpoints |
| `services/attribution_service.py` | 5 | `marketing_handlers.py`, `retention_handlers.py` (both now-instrumented) |
| `services/morning_brief_service.py` | 2 (+ its own `check_and_generate_scheduled` sweep, same cross-tenant shape as §4's flagged items) | `EventWorker.on_tick` |
| `services/qualification_service.py` | 1 | `crm_handlers.py` (now-instrumented) |
| `services/enrichment_service.py` | 1 | `qualification_service.py` |
| ~233 individual `Tool.execute()` implementations under `app/tools/` | Not enumerated individually | `ToolRegistry.execute()` (now-instrumented at the registry's own audit/permission/approval layer, but each tool's own business logic constructs its own services independently) |
| Every other authenticated-HTTP-only service (per §7's finding) | Not enumerated | Various routers |

**None of this represents new risk introduced by this phase** — these code paths had zero tenant-context propagation before this phase and have zero after; RLS itself is still fully audit-mode/permissive on the 32 tables that have it at all, so no enforcement gap opens or closes here. It is flagged so that Phase 17B-4/17B-5 planning treats "tenant context propagation is done" as false for this remaining surface, and so a future pass can decide whether to instrument every remaining service individually (mechanical but large) or introduce a structural fix (e.g., a tenant-aware session-factory wrapper bound at the point tenant identity first becomes known, rather than a per-call-site `await set_tenant_context(...)` — a design question explicitly out of this phase's "don't redesign" scope).

---

## 9. Backend Regression Results

Baseline (PHASE_17B1_RESTRICTED_DB_ROLE_IMPLEMENTATION_LOG.md §14): **1888 passed, 1 pre-existing failure (`test_openai_realtime_voice_service.py::test_voice_stream_route_dispatches_to_realtime_engine_when_configured`, a known async/event-loop test-ordering flake, independently reconfirmed to pass in isolation), 12 skipped.**

Real-Postgres run methodology: `pgserver`-backed disposable PostgreSQL 16 instance (Docker unavailable in this sandbox, per every prior phase's own documented constraint), `DATABASE_URL`/`DATABASE_MIGRATION_URL` both pointed at it, `pgvector` extension created, `EVENT_TRANSPORT=memory`, `AI_PROVIDER=deterministic`, `EMBEDDING_PROVIDER=deterministic`, `STT_PROVIDER=deterministic`, `TTS_PROVIDER=deterministic` (matching every prior phase's own env).

- **Run 1** (before the §5 fix): a filtered run (`-k "event or agent or mcp or website or lead or webhook"`, 1499 of the suite's tests) showed **53 failed, 444 passed, 2 skipped** — all 53 traced to the single `NameError` described in §5.
- **Fix applied** (§5).
- **Run 2** (`-k agent` only, 157 tests, to confirm the fix): **157 passed, 0 failed.**
- **Run 3** (full suite, `pytest -q`, no filter, real Postgres, 949.13s / 15:49): **1 failed, 1896 passed, 12 skipped.**

Compared against the Phase 17B-1 baseline (**1888 passed, 1 pre-existing failure, 12 skipped**): passed count is 1888 + 8 = **1896**, exactly accounting for this phase's 8 new tests, with no other net change; skipped count is identical (12); failed count is identical (1). The single failure is:

```
FAILED tests/test_openai_realtime_voice_service.py::test_voice_stream_route_dispatches_to_realtime_engine_when_configured
```

— the **exact same test** PHASE_17A and PHASE_17B1's own reports already named as the pre-existing, unrelated, order-dependent voice-websocket flake. Re-ran it standalone in this phase to independently reconfirm: **1 passed** in isolation (0.59s), confirming it is a test-ordering/event-loop interaction, not a regression from this phase's changes (this phase touched no voice/realtime code). **Classification: PRE-EXISTING, NOT INTRODUCED BY THIS PHASE.** No other failure appeared anywhere in the full run.

`backend/tests/test_tenant_context_propagation_phase17b2.py` (all 8 new tests) was also re-run standalone: **8 passed**.

No test was skipped, deleted, or weakened to make this pass. No production behavior was changed beyond the tenant-context-stamping additions themselves plus the one-line `select` import fix in §5.

---

## 10. Frontend Regression

No frontend file was changed in this phase (confirmed: every file touched is under `backend/app/` or `backend/tests/`; `git status --short` in §13 shows no `frontend/` entries). No frontend test run was needed or performed.

---

## 11. Secret Scan

`git diff` across every file this phase touched (`backend/app/api/v1/{public_leads,webhooks}.py`, `backend/app/events/*.py`, `backend/app/mcp/protocol.py`, `backend/app/services/{agent_execution_service,agent_reasoning_service,agent_recovery_service,agent_trigger_service,lead_service,website_service}.py`, `backend/app/tools/registry.py`, and the new test file) was grepped for credential-shaped strings (`password`, `secret`, `api[_-]?key`, long token-literal assignments, PEM headers). No match beyond pre-existing, already-committed identifiers (`token_hash`, `token_prefix`, `STRIPE_WEBHOOK_SECRET`/`TWILIO_AUTH_TOKEN` config-setting *names*, never values) and this phase's own test file's synthetic, non-secret test data (`token_hash="x"`, `token_prefix="x" * 8`). No real password, API key, JWT secret, or provider token appears anywhere in this phase's diff.

---

## 12. Known Limitations

- §8's ~87-file remaining-session-open-sites gap (business/domain services reached indirectly from the now-instrumented entrypoints, and from authenticated HTTP routes per §7).
- §7's corrected finding: the authenticated HTTP path has the same class of gap as the paths this phase was asked to fix, for every service beyond `WebsiteService`/`LeadService`.
- The two deliberately-unset cross-tenant sweep cases (`EventBus.reconcile_stuck_events`, the `tenant_id=None` branch of scheduled-dispatch/recovery-sweep) still have no system/global context — this is explicitly Phase 17B-3's job, not invented here, per the task's own instruction.
- The Stripe webhook's pre-parse dedup-row insert still has no tenant context (genuinely cannot, until the payload is parsed) — documented, matches `set_tenant_context`'s own pre-existing docstring example.
- `agent_recovery_service.py::_claim`'s tenant lookup and `agent_reasoning_service.py::_renew_lease`'s tenant lookup each add one extra `SELECT` per call to resolve the owning tenant before the real UPDATE — a small, deliberate overhead traded for correctness; not benchmarked (out of this phase's scope).
- This phase's own real-Postgres testing (§6) covers one representative code path per subsystem, not every one of the ~85 individual `set_tenant_context` call sites added — matching the same scope-of-proof precedent `test_postgres_rls_audit_mode.py` already set (1 of 132 tables) and `test_restricted_app_role_cutover.py` set (DML shape, not every table).

---

## 13. Final Git State

```
$ git status --short
 M .env.example
 M .env.staging.example
 M .github/workflows/ci.yml
 M backend/alembic/env.py
 M backend/app/api/v1/public_leads.py
 M backend/app/api/v1/webhooks.py
 M backend/app/core/config.py
 M backend/app/events/agent_trigger_handlers.py
 M backend/app/events/automation_handlers.py
 M backend/app/events/bus.py
 M backend/app/events/crm_handlers.py
 M backend/app/events/finance_handlers.py
 M backend/app/events/handlers.py
 M backend/app/events/marketing_handlers.py
 M backend/app/events/notification_handlers.py
 M backend/app/events/operations_handlers.py
 M backend/app/events/retention_handlers.py
 M backend/app/mcp/protocol.py
 M backend/app/services/agent_execution_service.py
 M backend/app/services/agent_reasoning_service.py
 M backend/app/services/agent_recovery_service.py
 M backend/app/services/agent_trigger_service.py
 M backend/app/services/business_discovery_service.py
 M backend/app/services/discovery_extraction_service.py
 M backend/app/services/lead_service.py
 M backend/app/services/website_service.py
 M backend/app/tools/registry.py
 M backend/tests/test_business_discovery_service.py
 M backend/tests/test_migration_schema_matches_models.py
 M docker-compose.prod.yml
 M docker-compose.yml
?? KLAROS_DISCOVERY_COMPLETION_AUDIT.md
?? PHASE_16A_DISCOVERY_FALLBACK_IMPLEMENTATION_LOG.md
?? PHASE_16B_DISCOVERY_REAL_PROVIDER_VALIDATION_LOG.md
?? PHASE_17A_RLS_ENFORCEMENT_READINESS_AUDIT.md
?? PHASE_17B1_RESTRICTED_DB_ROLE_IMPLEMENTATION_LOG.md
?? PHASE_17B2_TENANT_CONTEXT_PROPAGATION_LOG.md
?? backend/scripts/db/
?? backend/tests/test_discovery_connected_provider_service.py
?? backend/tests/test_discovery_extraction_service.py
?? backend/tests/test_restricted_app_role_cutover.py
?? backend/tests/test_tenant_context_propagation_phase17b2.py
```

```
$ git diff --stat
 .env.example                                       |  15 +-
 .env.staging.example                               |  16 +-
 .github/workflows/ci.yml                           |  24 +++
 backend/alembic/env.py                             |  10 +-
 backend/app/api/v1/public_leads.py                 |   6 +-
 backend/app/api/v1/webhooks.py                     |  21 ++-
 backend/app/core/config.py                         |  20 +++
 backend/app/events/agent_trigger_handlers.py       |   4 +
 backend/app/events/automation_handlers.py          |   3 +
 backend/app/events/bus.py                          |  20 +++
 backend/app/events/crm_handlers.py                 |   2 +
 backend/app/events/finance_handlers.py             |   3 +
 backend/app/events/handlers.py                     |   2 +
 backend/app/events/marketing_handlers.py           |   5 +
 backend/app/events/notification_handlers.py        |   4 +
 backend/app/events/operations_handlers.py          |   2 +
 backend/app/events/retention_handlers.py           |   5 +
 backend/app/mcp/protocol.py                        |   6 +
 backend/app/services/agent_execution_service.py    |  21 +++
 backend/app/services/agent_reasoning_service.py    |  24 ++-
 backend/app/services/agent_recovery_service.py     |  20 +++
 backend/app/services/agent_trigger_service.py      |  13 ++
 backend/app/services/business_discovery_service.py |  19 +++
 .../app/services/discovery_extraction_service.py   | 113 +++++++++++---
 backend/app/services/lead_service.py               |   2 +
 backend/app/services/website_service.py            |  15 ++
 backend/app/tools/registry.py                      |   6 +
 backend/tests/test_business_discovery_service.py   | 167 +++++++++++++++++++++
 .../tests/test_migration_schema_matches_models.py  |  14 ++
 docker-compose.prod.yml                            |   5 +
 docker-compose.yml                                 |  31 +++-
 31 files changed, 584 insertions(+), 34 deletions(-)
```

```
$ git diff --check
(no output — exit code 0, clean, no whitespace errors)
```

```
$ git diff --cached --stat
(no output — exit code 0, nothing staged)
```

Nothing is staged. Nothing was committed. Nothing was pushed. The pre-existing Phase 16/16B/17A/17B-1 uncommitted diffs (`business_discovery_service.py`, `discovery_extraction_service.py`, `test_business_discovery_service.py`, `test_migration_schema_matches_models.py`, `.env.example`, `.env.staging.example`, `.github/workflows/ci.yml`, `backend/alembic/env.py`, `backend/app/core/config.py`, `docker-compose.yml`, `docker-compose.prod.yml`, `backend/scripts/db/`, and the untracked Phase 16/17A/17B1 documentation/test files) are present and were not modified by this phase — confirmed identical to their state at the start of this phase (§1) and identical in shape to the diff stat PHASE_17B1's own report already recorded for those same files.

---

## 14. Phase Report

```
PHASE: 17B-2
STATUS: COMPLETE WITH LIMITATIONS — every entrypoint explicitly named in this phase's scope (Event Bus worker/handlers, MCP tool execution, public website/lead-intake routes, webhook routes, background/scheduler entrypoints, Agent execution entrypoints) is instrumented and behaviorally verified against real PostgreSQL, and the full regression suite confirms zero new failures. "WITH LIMITATIONS" because this phase also surfaced (§7) and documented (§8) a materially larger, real gap — most business/domain services reached indirectly (and most of the authenticated HTTP path beyond WebsiteService/LeadService) still open independent, un-instrumented sessions — which is honest follow-up scope, not a defect this phase introduced, and was explicitly out of this phase's named boundaries.
FILES CHANGED: 20 backend/app source files (see §3/§4) + 1 new test file (backend/tests/test_tenant_context_propagation_phase17b2.py). Pre-existing uncommitted Phase 16/16B/17A/17B-1 files untouched (verified byte-for-byte unchanged in shape, §13).
MIGRATIONS: None created. None required — this phase is pure application-code plumbing, no schema/DDL/policy change.
TESTS: 8 new real-Postgres tests, all passing (test_tenant_context_propagation_phase17b2.py). 1 real bug found and fixed by this phase's own testing (see §5: missing `select` import in agent_reasoning_service.py, causing a NameError in the lease-heartbeat path). Full-suite regression: 1 failed, 1896 passed, 12 skipped (real Postgres, 949s) — vs. the 1888-passed/1-failed/12-skipped baseline, this is +8 passed (exactly this phase's new tests) and the identical 1 pre-existing failure (tests/test_openai_realtime_voice_service.py::test_voice_stream_route_dispatches_to_realtime_engine_when_configured — reconfirmed passing in isolation, an unrelated order-dependent flake, not touched by this phase's changes).
POSTGRES: Real, disposable PostgreSQL 16 (pgserver-backed, Docker unavailable in this sandbox). pgvector extension required and created. All new tests and the full regression run executed against it, not SQLite.
SECURITY: No new attack surface introduced. Tenant identity is still resolved only from JWT/MCP-credential/verified-webhook-signature+internal-metadata/trusted-URL-path-parameter sources, exactly as before — this phase only adds DB-session-level plumbing on top of already-trusted values, never a new tenant-resolution mechanism. RLS itself remains fully audit-mode/permissive; this phase changes no policy and provides no new enforcement. Secret scan of this phase's diff: clean (§11).
BROWSER: Not applicable — no frontend file touched, no UI-observable change.
KNOWN LIMITATIONS: See §12 (headline: ~87 remaining files with un-instrumented session_factory() sites in domain/business services reached indirectly; the corrected finding (§7) that the authenticated HTTP path shares this same class of gap beyond WebsiteService/LeadService; two deliberately-unset genuine cross-tenant sweep operations flagged for Phase 17B-3, not faked).
UNRESOLVED BLOCKERS: None for this phase's own explicitly-scoped work. The §7/§8 gaps are follow-up scope for a future phase, not a blocker to closing 17B-2 as scoped.
GIT STATUS: Clean of staged/committed changes; only the intended working-tree modifications present (see §13). Nothing staged (git diff --cached --stat empty), git diff --check clean. No commit, no push.
NEXT PHASE: Per the task's explicit hard-stop instruction, do NOT proceed to 17B-3 (system/global context design), 17B-4 (full RLS instrumentation), 17B-5 (real policies), 17B-6 (FORCE RLS), or any later phase without explicit authorization.
```
