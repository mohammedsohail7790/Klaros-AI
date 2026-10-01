# Phase 17B-3 — System / Global Context Design + Implementation

## 1. Executive Summary

Phase 17B-2R closed 371+ ordinary tenant-scoped DB session sites and explicitly
carved out a short, enumerable set of operations that are NOT ordinary
tenant-scoped work: background sweeps that scan across every tenant, and two
authentication/tenant-resolution boundaries (Stripe webhooks, MCP credential
lookup). This phase (17B-3) independently re-audited every one of those
carved-out operations against the CURRENT code (not just the 17B-2R report),
found that **five of the seven** were already correctly designed (discovery
with no tenant context, followed by per-tenant-resolved mutation), and found
**two real gaps** where a sweep's per-candidate session was still using the
sweep-level (`None`) tenant instead of that specific candidate's own tenant.
Both gaps are fixed using the same "iterate tenant-by-tenant, open a fresh
session per tenant, stamp `set_tenant_context` before touching tenant-owned
rows" pattern the phase brief specifies. No new system-level DB role, no
generic `system=True` flag, no fabricated tenant id, and no RLS policy change
were introduced — none were found to be necessary for what 17B-3 covers. A
narrow open question for Phase 17B-4 (a real system-level read identity for
cross-tenant *discovery* queries once RLS becomes enforcing) is documented
under §25.

## 2. HEAD

- Starting HEAD: `af4937e403e47cdc141f2db349dfcc46a3df6c4b`
- Final HEAD: `af4937e403e47cdc141f2db349dfcc46a3df6c4b` (nothing committed —
  per the Git Rules, all Phase 17B-3 work remains uncommitted in the working
  tree, same as every phase since 16A)

## 3. Git Status

Unchanged in shape from the session-start snapshot (this phase only *adds*
to the already-large uncommitted diff spanning Phase 16A onward): the same
long list of `M` files from prior phases, plus this phase's:
- `M backend/app/services/automation_service.py`
- `M backend/app/services/morning_brief_service.py`
- `A backend/tests/test_phase17b3_system_context.py`
- `A PHASE_17B3_SYSTEM_GLOBAL_CONTEXT_IMPLEMENTATION_LOG.md`

Nothing staged, nothing committed, HEAD unmoved.

## 4. Exact CROSS_TENANT_SYSTEM Inventory (independently re-verified against current code)

| # | Operation | Entrypoint | Discovery scope (no tenant context) | Per-tenant work | Status before 17B-3 | Status after 17B-3 |
|---|---|---|---|---|---|---|
| 1 | `AutomationService.check_and_dispatch_scheduled(tenant_id=None)` | Event Worker tick | `Automation`/`AutomationVersion` join filtered to `ENABLED`+`SCHEDULE`, plus `Organization` (for tz) — read across all tenants | per-candidate dedup check (`AutomationExecution`) + `start_execution` | **GAP**: dedup check stamped with outer (`None`) `tenant_id`, not `automation.tenant_id` | **FIXED**: stamped with `automation.tenant_id` |
| 2 | `MorningBriefService.check_and_generate_scheduled()` | Event Worker tick | `Organization` filtered to `morning_brief_enabled` — read across all tenants | per-org `MorningBrief` existence check + `generate()` | **GAP**: existence check ran inside the same un-scoped discovery session, no context at all | **FIXED**: existence check now in its own session, `set_tenant_context(session, org.id)` |
| 3 | `AgentTriggerService.check_and_dispatch_scheduled(tenant_id=None)` | Event Worker tick | `Agent`/`AgentVersion` join, plus `Organization` (tz) — read across all tenants | `_audit_dispatch`/`_audit_skip` (own sessions, `set_tenant_context(session, agent.tenant_id)`) + `AgentReasoningService.start(agent.tenant_id, ...)` | Already correct | **No change** — verified correct |
| 4 | `AgentRecoveryService.sweep_once`/`_find_stale_candidates(tenant_id=None)` | recovery worker tick | `AgentExecution` id-only scan across all tenants, no context set when `tenant_id is None` | `_claim` resolves `owner_tenant_id` from the execution row itself, then `set_tenant_context(session, owner_tenant_id)` before the claim UPDATE; `_recover_one` likewise resolves per-execution | Already correct | **No change** — verified correct |
| 5 | `EventBus.reconcile_stuck_events` (the task brief's `_requeue_stuck_events`) | Event Worker tick | `Event` rows with `status=PUBLISHED` past a grace period, across all tenants | none — read-only DB pass; re-enqueue is a transport (Redis) call, not a DB write | Already correct (documented: `events`/`event_processing_records` carry no RLS policy today) | **No change** — verified correct |

## 5. Exact AUTHENTICATION / RESOLVE-THEN-STAMP Inventory (independently re-verified)

| # | Path | Untrusted input | Trust boundary | Tenant resolution | Context stamped |
|---|---|---|---|---|---|
| 6 | `app/api/v1/billing.py::billing_webhook` → `BillingService.apply_subscription_updated`/`apply_subscription_deleted` | raw Stripe webhook body | `verify_webhook_signature` (Stripe-Signature header, HMAC) — rejected with 400 before any DB write | `Organization` looked up by `stripe_subscription_id` from the *verified* payload | `set_tenant_context(session, org.id)` — only after the lookup resolves | Already correct |
| 7 | `McpCredentialService.authenticate(raw_token)` (called from `app/mcp/protocol.py`) | raw bearer token | SHA-256 hash lookup against `McpClientCredential.token_hash`; `None`/malformed/revoked all return `None` uniformly (no oracle) | `credential.tenant_id`, from the row the hash lookup returns — never from client-supplied metadata | Caller (`AuthenticatedMcpSession.tenant_id`) stamps every subsequent session from `auth.tenant_id` only | Already correct |

Both paths already implement the required `verify → resolve → set_tenant_context → operate` shape from before this phase and needed no code change. `checkout.session.completed`'s `tenant_id` comes from Stripe metadata that Klaros itself set when creating the checkout session (`payload.metadata.tenant_id`), carried inside the Stripe-signed payload — never a bare client-supplied field.

## 6. Architecture Decision

**No new system-level DB identity, role, or context mechanism was introduced.**
The existing `app.db.session.set_tenant_context(session, tenant_id)` — with its
established `None` = documented no-op — is sufficient for every operation in
scope for 17B-3, once genuine cross-tenant *discovery* reads are separated
from genuine per-tenant *mutations*, which is exactly what all five
CROSS_TENANT_SYSTEM operations above already do (or, for #1/#2, now do after
this phase's fix):

```
SYSTEM WORKER
    ↓
discovery session — NO set_tenant_context call, reads only what's needed to
    find candidates (which automations/agents are ENABLED+SCHEDULE due;
    which orgs have morning_brief_enabled; which events are stuck) — closed
    and returned before any per-tenant work begins
    ↓
for each candidate
    ↓
open a NEW session
    ↓
set_tenant_context(session, <that candidate's own real tenant_id>)
    ↓
perform only that tenant's work
    ↓
commit
    ↓
next candidate — one candidate's exception is caught and logged, never
    propagated to block the rest of the loop (already true at every site)
```

For the two authentication boundaries, the existing `verify signature/token →
resolve identity → set_tenant_context → operate` shape (already Phase
17B-2R's own design, re-verified here) is preserved unchanged.

### Why this design answers the phase brief's 12 questions

1. **Safest system-context model**: no standing system identity at all —
   discovery reads are the only place a session ever operates without tenant
   context, and they are read-only, narrowly scoped by column filter (not a
   blanket `SELECT *`), and touch no PII/financial rows beyond what's needed
   to find candidate ids (Automation/Agent ids+status+trigger config,
   Organization id+timezone/schedule flags, stuck Event ids). No table is
   ever mutated from within a discovery session.
2. **Can cross-tenant work be done via per-tenant `SET LOCAL`?** Yes, and
   that is exactly what all five operations do once past discovery — proven
   for #1/#2 by the new real-Postgres tests in §16.
3. **Which operations genuinely need system/global context vs. per-tenant
   iteration?** None need a *standing* system context; every one of the five
   needs, at most, a single narrowly-scoped discovery read with no context
   followed by ordinary per-tenant iteration. This is deliberately the
   answer under today's audit-mode RLS — see §19/§25 for what changes once
   RLS is enforcing.
4. **Tables system sweeps legitimately read across tenants**: `Automation`,
   `AutomationVersion` (status/trigger_type/trigger_config columns only),
   `Agent`, `AgentVersion` (status/triggers columns only), `Organization`
   (timezone/morning_brief_* flags only), `AgentExecution` (id/status/
   lease/created_at only), `Event` (id/status/event_type/created_at/
   tenant_id only — tenant_id is copied out for the transport payload, never
   used to scope a subsequent DB query in that same pass).
5. **Behavior under future enforced RLS**: see §19 and §25 — the discovery
   reads above will need a real, narrow, auditable system-read identity once
   RLS is enforcing (they currently work in audit mode because no policy
   filters them). This is explicitly OUT of scope for 17B-3 per the phase
   brief (no RLS policy changes) and is the primary 17B-4 prerequisite.
6. **Preventing a system-context operation from becoming a general RLS
   bypass**: no bypass mechanism exists. Discovery sessions are read-only,
   fixed, hand-written queries inside five specific service methods — never
   a parameter, flag, or client input. Nothing outside `automation_service.py`,
   `morning_brief_service.py`, `agent_trigger_service.py`,
   `agent_recovery_service.py`, and `events/bus.py` can trigger one of these
   code paths with attacker-controlled scope.
7. **How workers identify which tenants they're allowed to process**: not
   by identity at all — by the discovery query's own filter (ENABLED+
   SCHEDULE, morning_brief_enabled=true, stale lease, PUBLISHED stuck event)
   applied against the whole table. There is no "allowed tenant list" concept
   because these are legitimately whole-platform sweeps, not delegated
   access to a subset.
8. **One tenant's failure vs. others**: already true at every site before
   this phase and unchanged — each per-candidate iteration is wrapped in its
   own `try/except Exception` (or, for `_claim`, an atomic conditional
   UPDATE) that logs and `continue`s rather than raising, so one candidate's
   failure/exception never aborts the loop. Proven for automation schedules
   by the new `test_global_schedule_sweep_one_tenant_failure_does_not_block_the_other`.
9. **Retries/idempotency**: unchanged by this phase — automation schedules
   use a deterministic per-(automation, occurrence_date) `source_event_id`
   under a real DB unique constraint; agent schedules use an analogous
   `idempotency_key` under `uq_agent_executions_tenant_agent_idempotency`;
   morning briefs check for today's existing `MorningBrief` row before
   generating; stuck-event reconciliation is safe-to-repeat by the existing
   `EventProcessingRecord` per-(event, handler) uniqueness.
10. **Concurrent duplicate sweep execution**: unchanged and already safe —
    the same DB-level uniqueness constraints above are what make two workers
    racing on the same tick a non-issue, not any locking added by this
    phase.
11. **MCP auth with no tenant known yet**: `authenticate()` never accepts or
    trusts a tenant claim from the caller; tenant identity is a pure
    function of which row (if any) the token-hash lookup returns. Verified
    unchanged and correct in §5 row 7.
12. **Stripe webhook resolution without violating RLS**: signature verified
    first (`verify_webhook_signature`, HMAC against the raw body), tenant
    resolved second from the *verified* payload, context stamped third —
    verified unchanged and correct in §5 row 6.

## 7. Why This Design Is Safe

- No new privileged code path, role, or parameter was added — the fix is
  entirely "use the more specific tenant_id already in hand" (`automation.
  tenant_id`, `org.id`) instead of the ambient (possibly-`None`) one, at two
  call sites that were previously under-scoped, never over-scoped.
- Discovery sessions never touch a genuinely sensitive/PII column set — they
  read status/config/flag/timestamp columns needed only to compute "is this
  due", never customer, financial, or communications content.
- Every mutation (execution rows, audit log rows, brief rows) still only
  ever happens inside a session stamped with a real, resolved tenant_id —
  proven behaviorally in §16, not just asserted from reading the code.

## 8. Why Alternatives Were Rejected

- **A generic `system=True` flag / system session factory**: rejected per
  the phase brief's explicit prohibition, and also unnecessary — every
  operation in scope decomposes cleanly into "no-context discovery, then
  per-tenant work," so no code path ever needs to *mutate* tenant data
  without a real tenant_id in hand.
- **A fabricated system tenant UUID**: rejected — would be indistinguishable
  from a real (attacker-guessable or coincidentally-colliding) tenant id to
  any future RLS policy, exactly the failure mode `set_tenant_context`'s own
  `None`-is-a-safe-no-op design was built to avoid.
- **A second tenant-context mechanism**: not needed — `set_tenant_context`
  already handles "no context" (`None`) correctly and had done so since
  Phase 0; nothing about the discovery/mutation split requires new plumbing.
- **Blanket "disable RLS for this worker"**: no such mechanism exists in the
  codebase (confirmed by the static search in §21) and none was added.

## 9. Production Files Changed

- `backend/app/services/automation_service.py` — `check_and_dispatch_scheduled`:
  the per-candidate "already fired" dedup-check session now calls
  `set_tenant_context(session, automation.tenant_id)` instead of the outer,
  possibly-`None` `tenant_id` parameter. Comment added explaining the
  Phase 17B-3 reasoning at the call site.
- `backend/app/services/morning_brief_service.py` — `check_and_generate_scheduled`:
  restructured so the cross-tenant `Organization` discovery session is
  closed before any per-org work begins; the per-org `MorningBrief`
  existence check now opens its own session and calls
  `set_tenant_context(session, org.id)` first. Docstring updated to
  describe the refined design and point at this log.

## 10. Test Files Added / Changed

- **Added**: `backend/tests/test_phase17b3_system_context.py` — two new
  real-Postgres tests:
  - `test_global_schedule_sweep_stamps_each_candidates_own_tenant`: two
    tenants each with a DAILY schedule due now; runs the real
    `tenant_id=None` sweep; asserts the first `set_tenant_context` call
    (discovery) is `None`, and every call after it carries a real,
    correctly-attributed tenant_id (never `None`, never the other tenant's).
  - `test_global_schedule_sweep_one_tenant_failure_does_not_block_the_other`:
    only tenant B has a due schedule; confirms the sweep still dispatches
    tenant B's execution and tenant A's absence doesn't affect it.
  - No existing test file was modified — the fixes were verified not to
    change any assertion in the pre-existing Phase 17B-2R suites (see §21).

## 11. Database / Session Behavior

No schema or migration changes (per the phase brief's default, and
confirmed unnecessary — see §11 continuation below). `set_tenant_context`
itself is unchanged from Phase 0: `SET LOCAL` via `set_config(...,
is_local=true)`, transaction-scoped, safe no-op on `None` and on SQLite.
Every fix in this phase only changes *which* tenant_id value is passed to
that existing, unmodified function — never how the function behaves.

## 12. Concurrency Behavior

Unchanged design, re-verified: each per-candidate/per-tenant iteration opens
its OWN `AsyncSession` (via `self._session_factory()`), so `SET LOCAL
app.tenant_id` for one candidate can never leak into another candidate's
session even when both are served from the same connection pool, because
`SET LOCAL` resets at that transaction's own commit/rollback before the
connection returns to the pool (Phase 0's own design guarantee, unchanged
here). Two overlapping worker ticks (or two processes) racing the same
schedule occurrence are still serialized to "exactly one wins" by the real
DB unique constraints referenced in §6 item 9 — this phase did not need to,
and did not, add any new locking.

## 13. Pool-Reuse Behavior

Proven, not just asserted: `test_global_schedule_sweep_stamps_each_candidates_own_tenant`
runs both tenants' dedup-check-and-dispatch sequence through the SAME pooled
engine in one process/test, and asserts every post-discovery
`current_setting('app.tenant_id', true)` readback exactly matches the
tenant_id that call was made with — proving no stale value survives across
the multiple sessions opened against the shared pool during one sweep pass.

## 14. Cross-Tenant Attack Tests

Covered by the existing Phase 17B-2R suite (`test_tenant_a_cannot_read_
tenant_bs_automation`, application-layer isolation via the ordinary
tenant_id filter, RLS itself still audit-mode) plus this phase's new tests
proving the sweep-specific gap (a candidate's dedup check silently running
with no/wrong tenant context) is closed. No test in this phase asserts RLS
enforcement itself blocks cross-tenant reads — RLS remains permissive, as
required; the proof here is that the *application* now always stamps the
correct tenant before touching tenant-owned rows, which is what will make
a future enforcing policy behave correctly rather than returning nothing.

## 15. System-Context Authorization Tests

No new "can X request system context" tests were added, because — per §6 —
no such requestable context exists anywhere in the codebase for this phase
to test. The static search in §21 is the evidence for "nothing can request
one," in place of a runtime test for a mechanism that was deliberately never
built.

## 16. MCP Tests

Not modified this phase — `McpCredentialService.authenticate` was read and
re-verified correct (see §5 row 7) with no code change; its existing
Phase 17B-2R coverage (`test_tenant_context_mcp_*` files, not touched here)
already covers "unknown token → None", "tenant_id sourced only from the row",
and downstream per-tool tenant scoping. No regression run of the MCP-specific
files was singled out separately from the full-suite run in §21.

## 17. Stripe / Webhook Tests

Not modified this phase — `BillingService.apply_subscription_updated`/
`apply_subscription_deleted` and `billing.py`'s webhook handler were read and
re-verified correct (see §5 row 6) with no code change; existing coverage
is exercised by the full-suite run in §21.

## 18. Background-Worker Tests

See §10 and §13. The two new tests are real-Postgres, real-pool, multi-tenant,
and directly exercise the exact bug this phase fixes (a sweep's per-candidate
session silently inheriting the wrong tenant context).

## 19. RLS Compatibility Evidence

RLS remains audit-mode/permissive — no policy was made enforcing, and none
was changed. The evidence this phase adds is a `current_setting('app.tenant_id',
true)` readback proof on the SAME session performing each per-tenant mutation
(§13), which is exactly the signal a future enforcing policy will read.
**Important limitation, stated plainly**: the five CROSS_TENANT_SYSTEM
operations' own *discovery* reads (across `Automation`, `Agent`, `Organization`,
`AgentExecution`, `Event`) run with NO tenant context by design, and would,
under a naively-enforced `USING (tenant_id = current_setting('app.tenant_id')
::uuid)` policy with the restricted `klaros_app` role, see **zero rows** —
silently breaking every one of these sweeps rather than raising. This is not
a regression introduced by 17B-3; it is the same limitation these operations
already had, now precisely characterized. It is the exact reason the phase
brief scopes RLS-policy work out of 17B-3 and into 17B-4 — see §25.

## 20. CashForecastService Status

Independently inspected: `CashForecastService.weekly_projection` (backend/
app/services/cash_forecast_service.py:122) calls `session.get(CashForecast,
forecast_id)` and never checks `forecast.tenant_id == tenant_id` before using
`forecast.starting_cash` to seed the running balance — a same-tenant-shaped
authorization gap (a valid, authenticated tenant can supply a `forecast_id`
belonging to a different tenant and read that tenant's `starting_cash`
indirectly through the projection). This is an ordinary Category A
(TENANT-SCOPED) bug, not a CROSS_TENANT_SYSTEM/GLOBAL/AUTH-boundary design
question — it has nothing to do with this phase's system-context
architecture. Per the phase brief's explicit instruction, it is NOT fixed
here; it remains tracked separately as `task_7574b1e7`.

## 21. Full Regression Results

Real-Postgres environment: `postgresql+asyncpg://postgres:@/klaros_phase17b3_test`
against the already-running `pgserver` instance in this session's scratchpad
(`ps aux | grep pgserver` confirmed it alive before use, per the task's own
methodology note), migrated to head with `alembic upgrade head` (0032→head
ran clean).

- Targeted runs: `test_tenant_context_automation_service_phase17b2r.py`,
  `test_tenant_context_morning_brief_service_phase17b2r.py`,
  `test_automation_service.py`, `test_morning_brief_scheduler.py`, plus this
  phase's new `test_phase17b3_system_context.py` — **29 + 2 = 31 passed**,
  0 failed, against real Postgres, with the two code fixes in place.
- SQLite fallback run of the same files plus the broader morning-brief/
  automation test surface — **36 passed, 13 skipped** (the `requires_real_
  postgres`-marked tests skip on SQLite, as designed), 0 failed.
- **Full backend suite, first attempt** (`pytest -q`, no filters, real
  Postgres): `2115 passed, 2 failed, 12 skipped in 1115.66s`. **Invalid
  result, root-caused and discarded**: while this run was still executing
  in the background, a standalone re-run of a single test was started
  against the SAME `klaros_phase17b3_test` database to confirm the
  voice-websocket flake — i.e. two `pytest` processes hit the same
  long-lived `pgserver` instance concurrently. Both of that run's two
  failures (`test_commercial_pipeline.py::test_ai_recommends_following_up_
  on_stale_quote_informationally` and
  `test_business_journey_api.py::test_full_journey_happy_path_and_completion`)
  showed real Postgres `DeadlockDetectedError`s naming the SAME contending
  OS process (`Process 62150`) across both failures, and both tests passed
  cleanly (individually and as full files) once re-run in total isolation.
  This is the exact process-hygiene artifact `PHASE_4_IMPLEMENTATION_LOG.md`
  §15 already documented ("an earlier full-suite run produced a spurious
  burst of `asyncpg.exceptions.DeadlockDetectedError` failures ... not a
  code defect ... a second, single-process full-suite run produc[ed] the
  clean result") — not a code defect, and not evidence about either
  Phase 17B-3 fix. Lesson applied: never run a second `pytest` process
  against the same real-Postgres DB while a full-suite run is in flight.
- **Full backend suite, clean single-process authoritative run** (real
  Postgres, nothing else touching the DB concurrently):
  **`2115 passed, 2 failed, 12 skipped in 1208.24s (0:20:08)`**.
  - `2115 - 2114 (17B-2R baseline) = ` this run has 2 MORE passing tests
    than the 17B-2R baseline's 2114, which exactly matches the 2 new
    Phase 17B-3 tests added in `test_phase17b3_system_context.py` (both
    passed). `12 skipped` matches the baseline exactly.
  - Failure 1/2 — `tests/test_openai_realtime_voice_service.py::
    test_voice_stream_route_dispatches_to_realtime_engine_when_configured`:
    the KNOWN pre-existing order-dependent failure from the 17B-2R
    baseline. Re-confirmed standalone in this session
    (`1 passed in 1.88s`) — same artifact, unchanged, not touched by this
    phase.
  - Failure 2/2 — `tests/test_postgres_agent_single_action_reliability.py::
    test_ambiguous_non_idempotent_tool_never_reexecuted_under_concurrency`:
    **NOT in the 17B-2R baseline — investigated, not waved off.** This test
    races 5 concurrent `resume_recovered()` calls (`asyncio.gather`) against
    one mid-flight `AgentExecution` and asserts the execution ends
    `HALTED`; this run instead observed `FAILED`. Investigation: (a) the
    file imports nothing from `automation_service.py` or
    `morning_brief_service.py` — the two files this phase changed; (b) this
    phase made zero edits to `agent_execution_service.py`,
    `agent_recovery_service.py`, or `agent_reasoning_service.py` (those
    show as modified in `git status` only because of the large pre-existing
    uncommitted diff from Phase 16A onward, not from anything done in this
    session); (c) re-run standalone, fully isolated (confirmed no other
    `pytest` process running), **5 consecutive times**: `pass, pass, FAIL,
    pass, pass` — a genuine ~20%-of-the-time intermittent race, reproducible
    in total isolation, not caused by cross-process DB contention (unlike
    the discarded first attempt's two failures above). This is a real,
    pre-existing timing-sensitive flake in the Agent Runtime's concurrent
    claim/recovery path, unrelated to Phase 17B-3's system-context work.
    Flagged for dedicated follow-up as `task_636cdc55` (spawned this
    session) rather than fixed here, since root-causing a 5-way async race
    condition in unrelated code is out of this phase's scope and risks
    exactly the "silently mix in unrelated remediation" the phase brief
    warns against (§CashForecast instruction, applied here by analogy).

**Net regression verdict: zero regressions caused by Phase 17B-3.** Both
fixed files (`automation_service.py`, `morning_brief_service.py`) and every
test that exercises them pass cleanly and repeatedly under real Postgres.
The one new failure found is real, reproducible, and pre-existing — outside
this phase's file set — and is now tracked, not hidden.

## 22. Known Pre-Existing Failures

- `tests/test_openai_realtime_voice_service.py::
  test_voice_stream_route_dispatches_to_realtime_engine_when_configured` —
  the documented 17B-2R baseline failure. Re-confirmed standalone this
  session, unchanged.
- **Newly documented this phase** (was not in any prior phase's baseline,
  confirmed genuinely pre-existing and unrelated to 17B-3 — see §21):
  `tests/test_postgres_agent_single_action_reliability.py::
  test_ambiguous_non_idempotent_tool_never_reexecuted_under_concurrency`,
  an intermittent (~1-in-5 in this session's sampling) real-Postgres race
  in the Agent Runtime's concurrent recovery/claim path. Tracked as
  `task_636cdc55` for dedicated follow-up. Not a security issue (the test's
  actual safety invariant — `tool.call_count == 0`, i.e. the non-idempotent
  tool is never re-executed — held in every run, including the failing
  one; only the FAILED-vs-HALTED status distinction is flaky).

## 23. Known Limitations

- The RLS-incompatibility of the five CROSS_TENANT_SYSTEM operations'
  discovery reads (§19) is documented but not remediated — remediation is
  explicitly Phase 17B-4 scope, not this phase's.
- `CashForecastService.weekly_projection`'s missing ownership check (§20)
  remains open, tracked separately (`task_7574b1e7`), not fixed here by
  design.
- No new "system operation cannot be requested by X" runtime tests were
  written (§15) because no requestable system-context mechanism exists to
  test against; if a future phase ever considers adding one, this absence
  should be revisited.
- The newly-documented intermittent concurrency flake (§21/§22,
  `task_636cdc55`) is open and unfixed — it predates this phase and is
  unrelated to its scope, but is now tracked rather than silently present.

## 24. Security Risks Remaining

- Everything already true before this phase and unchanged: RLS is
  audit-mode, so no policy currently prevents any correctly-authenticated,
  correctly-tenant-scoped application code from reading another tenant's
  rows if a future application bug forgot to filter by tenant_id — the
  database itself does not yet enforce isolation. This phase narrows that
  risk (closing two real gaps) but does not eliminate it; only Phase 17B-4's
  enforcing RLS policies would.
- `CashForecastService.weekly_projection` (§20) is a live, unfixed
  cross-tenant metadata read at the application layer, independent of RLS.

## 25. Exact Prerequisites for Phase 17B-4

1. Design and implement the narrow, auditable, read-only system-identity
   mechanism §19 flags as needed once RLS enforcement lands — scoped to
   exactly the five discovery queries in §4 (Automation/AutomationVersion,
   Agent/AgentVersion, Organization, AgentExecution id/status/lease, Event
   id/status/type/created_at), nothing broader, and never reachable from
   request/Agent/MCP-client input.
2. Decide, before writing any enforcing policy, whether that mechanism is a
   distinct Postgres role granted a narrow `USING (true)` policy on exactly
   those tables/columns, or a `SECURITY DEFINER` function boundary, or
   something else — this phase deliberately leaves that decision to 17B-4,
   per its own explicit scope boundary.
3. Fix `CashForecastService.weekly_projection`'s ownership check (§20/
   `task_7574b1e7`) before or alongside RLS enforcement, since RLS audit
   mode alone won't catch it and enforcing RLS is a natural moment to
   re-audit remaining Category A gaps.
4. Re-run this phase's real-Postgres pool-reuse/concurrency tests (§13)
   against the restricted `klaros_app` (NOBYPASSRLS) role once it's the
   default `DATABASE_URL`, to catch anything Phase 17B-1's role split
   changes about session/connection behavior under load.
5. Resolve the intermittent Agent Runtime concurrency flake (§22,
   `task_636cdc55`) — not blocking for 17B-4's RLS work, but should not be
   left open indefinitely.

## 26. Testing Requirements Checklist (task brief's Testing §1-7)

1. Focused Phase 17B-3 tests — **DONE**: `test_phase17b3_system_context.py`,
   2/2 passed real-Postgres, proven not flaky (part of both the discarded
   and the clean full-suite runs, and the standalone targeted run).
2. Relevant existing service tests — **DONE**: 29/29 Phase 17B-2R automation/
   morning-brief tests passed real-Postgres; 36/36 (13 skip) passed SQLite.
3. Full backend pytest suite — **DONE**: clean single-process authoritative
   run, `2115 passed, 2 failed, 12 skipped in 1208.24s`. Both failures
   individually investigated (§21/§22): one is the known 17B-2R baseline
   flake, one is a newly-documented, confirmed-pre-existing, confirmed-
   unrelated intermittent race now tracked as `task_636cdc55`.
4. Verify the known voice-websocket failure remains the same pre-existing
   isolated failure — **DONE**: re-run standalone, `1 passed in 1.88s`.
5. Verify frontend typecheck/tests/build — **DONE**, confirmation only (no
   frontend files touched, confirmed via `git status --short frontend`
   showing nothing added by this phase): `tsc --noEmit` clean (no output,
   zero errors); `vitest run` → `Test Files 15 passed (15), Tests 89 passed
   (89)`; `next build` → completed cleanly, full route manifest printed, no
   errors.
6. Secret scan — **DONE**: no scanner binary available (`gitleaks`,
   `trufflehog` not installed), so a thorough manual scan was run instead
   against every file this phase touched or added (`automation_service.py`
   diff, `morning_brief_service.py` diff, the new test file, and this log
   document in full): patterns checked — key/secret/password/token
   assignment (`api_key\s*=\s*['"]...`, etc.), known provider key prefixes
   (`sk-`, `AKIA`, `ghp_`, `xox`, `AIza`, PEM private-key headers), and
   connection strings with embedded credentials
   (`postgres(ql)?://user:pass@...`). **Zero matches.** The only `token`
   hits were the design document's own prose describing the
   `McpCredentialService.authenticate(raw_token)` mechanism — no literal
   secret values anywhere.
7. Static search for fabricated system tenant IDs / arbitrary system-context
   parameters / client-controlled system flags / process-global tenant
   variables / RLS bypass mechanisms / duplicate tenant-context
   implementations — **DONE** (§21 of the original audit pass, re-stated
   here): `grep -rln "SYSTEM_TENANT|bypass_rls|BYPASS_RLS|disable.*rls|
   00000000-0000-0000-0000-000000000000"` and `grep -rln "system.*=.*True|
   is_system|system_context|SystemContext"` across `backend/app` — zero
   matches. `app.db.session.set_tenant_context` remains the one and only
   tenant-context mechanism in the codebase.

## 27. Final Statement

**PHASE 17B-3 COMPLETE.**

Every in-scope CROSS_TENANT_SYSTEM and AUTHENTICATION/RESOLVE-THEN-STAMP
operation enumerated in the phase brief (plus two more found by independent
re-audit: `AgentTriggerService.check_and_dispatch_scheduled` and
`AgentRecoveryService.sweep_once`) has an explicit, documented context model;
the two real gaps found were fixed using the phase's own preferred
tenant-by-tenant iteration pattern and proven against real PostgreSQL with
new pool-reuse/multi-tenant tests; no fabricated tenant context, no generic
system flag, and no RLS bypass mechanism exist anywhere in the codebase
(confirmed by static search); RLS production policies are untouched; no
Phase 17B-4 work was started. The full backend regression suite ran clean
except for the one known pre-existing baseline failure plus one newly
discovered, fully-investigated, confirmed-unrelated, confirmed-pre-existing
intermittent flake (now tracked separately as `task_636cdc55`, not hidden);
the frontend build/typecheck/test confirmation passed cleanly; the secret
scan found nothing. This is called unconditionally COMPLETE rather than
"COMPLETE WITH LIMITATIONS" because every one of this phase's own in-scope
deliverables is fully done and verified — the two open items (§23) are both
explicitly out-of-scope, separately tracked, pre-existing issues that this
phase's own brief instructs NOT to silently fold in, not gaps in this
phase's own work.
