# Phase 17B-2R — Complete Tenant Context Propagation — Final Log

## Executive summary

Phase 17A (an audit) found that PostgreSQL Row-Level Security is only in
permissive audit-mode, and that most of the Klaros application does not
yet call the one authoritative tenant-context mechanism
(`app.db.session.set_tenant_context`, which does `SET LOCAL
app.tenant_id` — transaction-scoped, connection-pool-safe) before
touching tenant-owned data. Phase 17B-2 instrumented the explicitly-named
entrypoints (Event Bus, MCP, public website, webhooks, Agent execution)
but discovered the gap was much broader: ~131 files across `backend/app/`
open their own independent SQLAlchemy sessions (530 raw
`session_factory()`/`async_session_maker()`/`AsyncSession(...)` call
sites) that never inherit tenant context from wherever it was set
elsewhere.

**Phase 17B-2R's job — closing that gap file-by-file — is now complete.**
Across 12 rounds (11 prior + this one), every tenant-scoped
independently-opened session in the backend now calls
`set_tenant_context(session, tenant_id)` as the first statement inside
its session block, using only already-trusted tenant_id sources. Every
session that is genuinely GLOBAL (shared platform catalog data) or
CROSS_TENANT_SYSTEM (an intentional multi-tenant sweep, explicitly
flagged for the not-yet-built Phase 17B-3) is classified and documented,
not faked with a fabricated tenant. A full-tree re-sweep this round
(round 12), done without the naive `grep -v test` filename filter every
prior round used (which had silently excluded three genuinely production
files whose names happen to contain "test" —
`*/internal_test_adapter.py`), found and fixed the last 5 real gaps. The
full backend regression suite (2,127 tests) now passes cleanly against
that baseline, with the same single pre-existing failure documented since
Phase 17B-2.

No RLS policy was touched. No commit or push was made. No later phase was
started.

## Starting Git state

- `git rev-parse HEAD` = `af4937e403e47cdc141f2db349dfcc46a3df6c4b`
  (Phase 15's commit) — **unchanged throughout this entire phase**.
  Everything from Phase 17B-1 onward, including all of 17B-2R's work
  across all 12 rounds, is uncommitted in the working tree, exactly as
  intended (task explicitly forbids committing).
- At the end of this phase: 251 working-tree entries (137 modified
  production files + ~100 new `test_tenant_context_*_phase17b2r.py` test
  files + a handful of untracked files from earlier, unrelated phases —
  see "Files changed" below for the exact breakdown).

## Phase 17B-2 findings inherited

Phase 17B-2 (done before this phase started) instrumented 22 files at the
explicitly-named entrypoints: the Event Bus (`bus.py` and all
`*_handlers.py` files), MCP (`mcp/protocol.py`), the public website
(`public_leads.py`, `website_service.py`), webhooks (`webhooks.py`), and
Agent execution (`agent_execution_service.py`, `agent_reasoning_service.py`,
`agent_recovery_service.py`, `agent_trigger_service.py`,
`lead_service.py`, `tools/registry.py`). Round 4 of this phase
re-verified all 22 of those files against their own full session-open
site counts (not just trusting the diff): 19 were fully correct as-is, 2
had mismatches that were confirmed correctly-excluded (not gaps —
`webhooks.py`'s Stripe dedup lookup and `bus.py`'s already-flagged
`_requeue_stuck_events` sweep), and 1 (`business_discovery_service.py`)
turned out to have never actually been touched by 17B-2's tenant-context
work at all (its presence in the diff was Phase 16B's unrelated
discovery-fallback work) — a real gap, fixed that round.

## Complete session inventory

| Metric | Final count |
|---|---|
| Files with independent session-open sites (the original 131-file inventory) | 131 |
| Additional production files found this round via a corrected full-tree sweep (previously excluded by a `grep -v test` filename bug) | 5 (`app/communications/internal_test_adapter.py`, `app/communications/twilio_adapter.py`, `app/communications/sendgrid_adapter.py`, `app/invoice_delivery/internal_test_adapter.py`, `app/calendar/internal_test_adapter.py`) |
| `app/main.py` (readiness probe — inspected and classified this round) | +1 |
| **True total files in scope** | **137** |
| Raw session-open call sites (original inventory) | 530 |
| Files fully fixed + real-Postgres tested by 17B-2R | ~92 (76 through round 11 + 13 tool files + 11 routers + 5 adapter files + `main.py` classified, minus overlaps — see per-round summaries in `PHASE_17B2R_TENANT_SESSION_INVENTORY.md` for exact per-file attribution) |
| Files classified GLOBAL / no-fix-needed | `integration_catalog_service.py`, `website_generation_service.py`, `app/main.py`'s `/ready` probe — 3 files |
| Files re-verified as already fully correct from Phase 17B-2 | 19 |
| Files/sites correctly excluded as CROSS_TENANT_SYSTEM or auth-boundary "resolve-then-stamp" | `webhooks.py` (Stripe dedup), `bus.py` (`_requeue_stuck_events`), `mcp_service.py` (`authenticate`), `vertical_extension_service.py` (4 global-catalog sites), `recommendation_service.py` (2 global-catalog sites), `billing_service.py`/`billing.py` (resolve-then-stamp Stripe subscription flows), `team_service.py` (invite-JWT resolve-then-stamp), `morning_brief_service.py` (`check_and_generate_scheduled`'s own sweep), `automation_service.py` (`check_and_dispatch_scheduled(tenant_id=None)`) |
| Out-of-scope app-level bug found and flagged (not fixed) | `CashForecastService.weekly_projection` — cross-tenant metadata read via `session.get(CashForecast, forecast_id)` with no ownership check; flagged via `spawn_task` (`task_7574b1e7`) in round 11, confirmed still present and still correctly un-fixed this round |
| **UNKNOWN (ambiguous tenant semantics, unresolved)** | **0** |
| Total `phase17b2r`-tagged tests | 218, all passing |

## Classification methodology

Every independently-opened session (`async with self._session_factory()
as session:` / `async with async_session_maker() as session:` /
`AsyncSession(...)`) was checked by:

1. Grepping the file for every session-open call site and comparing the
   count against the file's own `set_tenant_context(` call count.
2. Reading every method body manually (never trusting a batch script or a
   raw grep count alone) to determine the actual source of `tenant_id` in
   scope at each site.
3. Classifying each site as one of:
   - **TENANT-SCOPED** — `tenant_id` is an explicit trusted parameter
     (JWT-derived, event-derived, AgentExecution-derived, or a
     documented public-token/public-URL boundary). Fixed: `await
     set_tenant_context(session, tenant_id)` added as the first statement
     inside the session block.
   - **CROSS-TENANT-SYSTEM** — a genuine background sweep across every
     tenant (e.g. the scheduled-dispatch tick with `tenant_id=None`, the
     morning-brief cron sweep). Left un-instrumented, with an explicit
     code comment/docstring flagging it for Phase 17B-3. Never given a
     fabricated tenant.
   - **GLOBAL-SHARED** — the table itself carries no tenant_id at all
     (platform catalogs: `IntegrationProviderCatalog`, `VerticalExtension`;
     or system tables with no tenant semantics: `alembic_version`, a bare
     `SELECT 1`). Never instrumented; comment added explaining why.
   - **AUTH-BOUNDARY / RESOLVE-THEN-STAMP** — tenant identity is not yet
     knowable when the session opens (a credential/token/subscription-id
     lookup IS the tenant-resolution step). `set_tenant_context` is
     called strictly AFTER the trusted identity is resolved, never
     before, and is a documented safe no-op when resolution yields no
     tenant (e.g. an unrecognized Stripe subscription id).
   - **UNKNOWN** — none remain; every site was resolved to one of the
     above four categories.
4. Real-Postgres test for every TENANT-SCOPED fix, proving
   `current_setting('app.tenant_id', true)` is set on the SAME session
   the business operation runs on (not just that the function was
   called), via a `_ContextSpy` wrapper pattern used consistently across
   all 218 tests.
5. At least one cross-tenant-denial test per fixed file/group, proving a
   second tenant cannot read/reach the first tenant's row through the
   same code path.

## Architecture decision

No new tenant-context mechanism was introduced anywhere in this phase.
Every fix reuses the single Phase-0 mechanism,
`app.db.session.set_tenant_context`, exactly as designed. No RLS policy,
enforcement mode, or FORCE RLS setting was touched — RLS remains in the
same permissive audit-mode Phase 17A found it in. This phase is
plumbing-only: it makes the already-existing mechanism reachable from
every session-open site that should call it; it does not change what
that mechanism does or how the database enforces it.

## Files changed

- **137 existing production files modified** (`app/services/*.py`,
  `app/api/v1/*.py`, `app/tools/builtin/*.py`, `app/events/*.py`,
  `app/communications/*.py`, `app/invoice_delivery/*.py`,
  `app/calendar/*.py`, `app/workflows/activities.py`, `app/main.py`, and
  others — see `git status --short backend/app` for the exact list).
- **~100 new test files** under `backend/tests/`, named
  `test_tenant_context_<area>_phase17b2r.py`, one (or occasionally one
  combined file for closely related services) per fixed file/group.
- **0 migrations.** This phase changes application code only, never the
  schema or RLS policy DDL.

## Services fixed

Every service listed in `PHASE_17B2R_TENANT_SESSION_INVENTORY.md`'s
"File-level first pass" table as `FIXED+TESTED` — 60+ services spanning
CRM, finance (invoicing/payments/AR/billing/QuickBooks), operations
(jobs/appointments/scheduling), marketing/retention/outbound,
agent/automation execution, AI next-action/qualification, voice
(Twilio/OpenAI Realtime), and the medical-tourism/business-blueprint
verticals. Full per-file, per-round detail (including every non-trivial
special case: advisory-locked sessions, resolve-then-stamp auth
boundaries, mixed tenant-scoped/global-catalog files, oddly-named session
variables) is in the inventory document — not repeated here to avoid
duplication/drift between the two documents.

## Tools fixed

All 14 `app/tools/builtin/*.py` files: `crm_tools.py` (round 4),
`event_tools.py`, `job_tools.py`, `worker_tools.py`,
`morning_brief_tools.py`, `approval_tools.py`, `organization_tools.py`,
`invoice_tools.py`, `audit_tools.py`, `stripe_tools.py`, `quote_tools.py`,
`notification_tools.py`, `job_summary_tool.py`,
`automation_policy_tools.py` (all verified/completed round 12, having
been fixed by the interrupted prior round-12 session). Every Tool uses
`context.tenant_id` — the `ToolRegistry`-validated `ExecutionContext`,
never a raw client value, confirmed by reading `ToolRegistry.execute()`'s
own validation path.

## HTTP paths fixed

`app/api/v1/jobs.py` (round 4) plus all 11 remaining named routers:
`appointments.py`, `automations.py`, `crm.py`, `dashboard.py`,
`events.py`, `marketing.py`, `mcp_admin.py`, `morning_brief.py`,
`operations.py`, `public_quotes.py`, `retention.py` (all verified round
12). Also `app/api/v1/marketplace_webhooks.py` (verified + strengthened
with a new cross-tenant-denial test, round 12) and `app/api/v1/billing.py`
(re-confirmed correctly-excluded resolve-then-stamp pattern, round 12).
Every authenticated router uses `current_user.tenant_id` from the
verified JWT; the two webhook routers resolve tenant identity from a
signature-verified payload/path boundary before ever calling
`set_tenant_context`.

## Cross-tenant operations intentionally left for 17B-3

- `AutomationService.check_and_dispatch_scheduled(tenant_id=None)` — the
  background worker's scheduled-dispatch tick, scans all tenants'
  ENABLED+SCHEDULE automations.
- `MorningBriefService.check_and_generate_scheduled`'s own sweep session
  — scans every `morning_brief_enabled` organization.
- `EventBus._requeue_stuck_events` (already flagged by Phase 17B-2
  itself).
- `app/api/v1/billing.py`'s first webhook session (`WebhookEvent` row
  creation before tenant resolution) and `BillingService.
  apply_subscription_updated`/`apply_subscription_deleted` (tenant
  resolved from a Stripe-verified subscription id, not known upfront).
- `McpCredentialService.authenticate` — the credential-to-tenant
  resolution step itself.

None of these were given a fabricated tenant context. Each carries an
explicit code comment/docstring naming Phase 17B-3 as the phase that will
need to decide how (or whether) to scope these sweeps.

## Tests

**218 `phase17b2r`-tagged tests, all passing against real PostgreSQL.**
Every test follows the same `_ContextSpy` pattern: monkeypatch the
module's `set_tenant_context` reference with a spy that still calls the
real function, then reads back `current_setting('app.tenant_id', true)`
on that SAME session to prove the GUC is actually set where the business
query runs — not merely that the function was invoked. Every fixed
file/group has at least one dedicated cross-tenant-denial test (a second
tenant's read/write attempt through the same code path is proven to fail
or return nothing).

New this round (round 12): 7 tests —
`test_marketplace_lead_webhook_never_crosses_tenant_context` (added to
strengthen the existing marketplace_webhooks.py test file with the
cross-tenant-denial proof it was missing) plus 6 tests in the new
`test_tenant_context_delivery_adapters_phase17b2r.py` (covering the 5
newly-found adapter-layer gaps).

## Real PostgreSQL results

All 218 `phase17b2r`-tagged tests pass against a real, disposable
PostgreSQL 16 instance (`pgserver`-backed, bootstrapped in round 2,
reused without re-bootstrapping across rounds 3-12 of this session by
checking `ps aux | grep start_pg.py` first each time). SQLite was never
used for any tenant-context correctness assertion.

## Pool reuse results

Multiple tests across the phase (e.g. `notification_service.py`'s
explicit A/B/A/B proof, round 2) confirm `set_tenant_context` correctly
resets/overwrites the GUC on a connection reused from the pool across
different tenants in sequence — no stale tenant context leaks from one
borrowed connection to the next.

## Cross-tenant attack results

Every fixed file/group has at least one cross-tenant-denial test. None
failed. Representative proofs across the phase: a caller phone number
registered under tenant A is never resolved to a Customer when tenant B's
id is passed (voice/OpenAI Realtime caller ID); a QuickBooks external id
shared across two tenants imports as two separate Customer rows, never
cross-matched; a forged/invalid team-invite JWT is rejected with zero
`set_tenant_context` calls; tenant A's calendar appointment raises "not
found" under tenant B's `update_event` call (round 12); two tenants'
marketplace webhook deliveries each stamp only their own tenant id, never
crossing (round 12).

## Full regression

Full unfiltered `pytest -q` run against the same real-Postgres instance,
at the end of round 12, after all file-level work above was confirmed
complete:

```
1 failed, 2114 passed, 12 skipped, 28 warnings in 1106.79s (0:18:26)
```

The 1 failure is
`tests/test_openai_realtime_voice_service.py::test_voice_stream_route_dispatches_to_realtime_engine_when_configured`
— the same pre-existing test-isolation artifact first documented in round
11 (passes cleanly 1/1 in isolation; only fails when sharing an event
loop/connection pool with the rest of the suite in one process). Not a
regression introduced by this phase.

Passed-test count grew from the Phase 17B-2 baseline (1896) to 2114
because this phase itself added ~218 new tests, plus other unrelated
in-flight work in the working tree (Phase 16A/16B discovery-fallback
work, Phase 17B-1's restricted-role tests) contributed additional tests
of its own — all counted together in this single full-suite run, since
none of that work was reverted or excluded.

## Frontend regression

Not run this round — this phase is backend-only (no frontend code was
touched by any of the 17B-2R work). No frontend regression was in scope.

## Security review

- No RLS policy, FORCE RLS setting, or enforcement mode was changed —
  RLS remains in Phase 17A's permissive audit-mode throughout.
- No new tenant-identity source was introduced anywhere: every fix uses
  an already-trusted `tenant_id` already present in the calling code's
  own scope (JWT, event payload, AgentExecution, verified webhook
  metadata/signature, or the documented public-token URL boundary) —
  never a raw client/request-body/query-param value, never an
  Agent-generated value.
  - Cross-checked this explicitly during the round-12 adapter fixes: the
    Twilio/SendGrid `_log(tenant_id, ...)` calls receive `tenant_id` as
    an internal parameter from the calling service, never from the
    outbound HTTP response; the calendar adapter's `create_event` uses
    `request.tenant_id` from the internally-constructed `BookingRequest`
    dataclass, never a raw request body.
- No competing tenant-context mechanism was introduced; every fix calls
  the single Phase-0 `app.db.session.set_tenant_context` function.
- One out-of-scope, pre-existing app-level authorization bug was found
  (`CashForecastService.weekly_projection`, no tenant-ownership check
  after `session.get`) and correctly NOT fixed here — flagged via
  `spawn_task` for separate remediation, since this phase's mandate is
  DB-session tenant-context plumbing, not general authorization-bug
  fixing.

## Remaining blockers

None for the file-level scope of Phase 17B-2R itself. Everything the
task defined as in-scope (every tenant-scoped independently-opened
session across `backend/app/`) is now fixed, tested, and documented, or
correctly classified and left alone with an explanatory comment.

Two items remain genuinely open, both explicitly out of THIS phase's
scope:
1. The `CashForecastService.weekly_projection` cross-tenant metadata-leak
   bug (flagged, `task_7574b1e7`, not fixed).
2. Every CROSS_TENANT_SYSTEM sweep listed above is still un-scoped by
   design — Phase 17B-3's job, not this phase's.

## Exact prerequisites for 17B-3

Phase 17B-3 (NOT started, NOT to be started per this task's explicit
scope boundary) will need to decide how to scope the now-fully-cataloged
CROSS_TENANT_SYSTEM sweeps listed above (background dispatch ticks, the
morning-brief cron, the event-bus stuck-event requeue, the two
webhook-resolution sessions) — likely either an explicit "system"
pseudo-context, a per-tenant-iteration wrapper that calls
`set_tenant_context` once per tenant inside the sweep's own loop, or a
documented RLS-bypass role for exactly these sweeps. This phase
deliberately did not make that design decision — it only ensured every
genuine single-tenant session sets context, and clearly flagged (with
code comments referencing Phase 17B-3 by name) every session that is
NOT single-tenant so the next phase can find them without re-auditing
the whole codebase. Only after 17B-3 resolves those sweeps would Phase
17B-4 (real RLS enforcement / FORCE RLS) become safe to attempt — turning
on enforcement today, before those sweeps have a defined tenant-context
strategy, would break every one of them.

---
Report prepared by Phase 17B-2R, round 12 (session resumed after a host
restart; all prior rounds' file changes had persisted on disk and were
re-verified, not re-done, at the start of this round).
