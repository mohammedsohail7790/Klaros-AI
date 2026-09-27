# Phase 7 (Agent Runtime Reliability II) — Implementation Log

## 1. Baseline (captured before any Phase 7 code edit)

- Repo: `/Users/mohammedsohail/Desktop/Klaros AI`, branch `main`, nothing from any prior phase committed (all local, uncommitted — preserved exactly; only new/modified files this phase touched are additional diffs on top).
- Postgres: a `pgserver`-provisioned PostgreSQL 16 instance was already running on this host at `/private/tmp/klaros_pg5` (unix-socket only, database `klaros`, role `postgres`) — reused directly (Docker unavailable in this sandbox, consistent with every prior phase). `DATABASE_URL=postgresql+asyncpg://postgres@/klaros?host=/private/tmp/klaros_pg5`.
- `alembic current` against that instance: **`0047 (head)`** — confirmed by actually running the command, not assumed. 48 revision files present, `0047_agent_runtime_reliability.py` is the tip (Phase 6's own migration).
- **Full backend suite (real Postgres), single pytest process (no concurrent pytest processes were run against the shared `pgserver` instance at any point in this session):**

  ```
  1 failed, 1660 passed, 12 skipped, 21 warnings in 732.36s (0:12:12)
  ```

  The single failure is `tests/test_openai_realtime_voice_service.py::test_voice_stream_route_dispatches_to_realtime_engine_when_configured` — the same pre-existing voice/event-loop flake documented in Phase 3/4/5's own logs. (Note: 1660 passed here vs. Phase 6's documented 1619 — the working tree already contained more tests/files than Phase 6's own log described, per this repo's stated "everything is uncommitted local changes" starting condition; the number was verified by actually running the suite, not assumed from any prior log.)

## 2. Architecture reviewed

Source (forensically inspected directly, not assumed from any log): `app/models/agent.py`, `app/services/agent_service.py`, `app/services/agent_execution_service.py`, `app/services/agent_reasoning_service.py`, `app/services/agent_recovery_service.py`, `app/services/agent_trigger_service.py`, `app/events/agent_trigger_handlers.py`, `app/tools/base.py`, `app/tools/registry.py`, `app/tools/policy.py`, `app/tools/redact.py`, `app/services/approval_execution_service.py`, `app/models/actor.py`, `app/models/audit_log.py`, `app/events/worker.py`, `app/integrations/stripe_client.py`, `app/integrations/quickbooks_client.py`, `app/tools/builtin/stripe_tools.py`.

Docs: `KLAROS_ARCHITECTURE_RECONCILIATION.md`, `KLAROS_FINAL_AGENT_MODEL.md`, `KLAROS_DO_NOT_BUILD_YET.md`, `PHASE_6_IMPLEMENTATION_LOG.md` (primary — Phase 7's scope is defined almost entirely as "close Phase 6's own two documented gaps"), plus the existing test files `test_agent_recovery_service.py`, `test_agent_recovery_security.py`, `test_postgres_agent_recovery.py`, `test_agent_execution_service.py`, `test_agent_no_hardcoding_guard.py`, `test_cross_vertical_agent_validation.py` (these encode the actual, already-battle-tested reconciled architecture more precisely than the prose docs for this specific gap, so they were treated as authoritative for exact behavioral contracts — e.g. the "version immutability under recovery means a clean rejection, not a silent continuation" semantics in `test_agent_recovery_security.py`).

Key finding: Phase 6's own module docstring in `agent_recovery_service.py` already named exactly the two gaps this phase was asked to close, in its own words: SINGLE_ACTION executions "NEVER resumed... only ever safely halted" (no step-level durability boundary existed), and tool side effects remain "honestly at-least-once" with no idempotency identity contract. This made the reconciliation unusually unambiguous — the target state was already specified by the code Phase 7 needed to change, not something this phase had to invent from scratch.

## 3. Reconciliation decisions (before writing implementation code)

1. **SINGLE_ACTION durability**: `AgentExecutionService.run_action` now writes an `AgentExecutionStep` row (`step_number=1`, `TOOL_CALL`, `completed_at=NULL`) *before* calling `ToolRegistry.execute()` — the identical "row exists before the external call" invariant `AgentReasoningService._attempt_tool_call` already established for REASONING's first step. A SINGLE_ACTION execution is, by construction, a REASONING execution bounded to exactly one step.
2. **`AgentExecutionStep` reuse**: reused unmodified — no new column, no new table. Forensic inspection confirmed the existing columns (`execution_id`, `step_number`, `step_type`, `status`, `tool_name`, `input_summary`, `output_summary`, `decision_summary`, `error_code`, `started_at`, `completed_at`) already represent everything a SINGLE_ACTION step needs. `AgentExecutionStepStatus` (`EXECUTED`/`APPROVAL_REQUIRED`/`REJECTED`/`FAILED`/`BLOCKED`/`DENIED`) was reused as-is; "still in flight" is represented the same way REASONING already represents it — `completed_at IS NULL` — not a new status value.
3. **One step per attempt**: yes — SINGLE_ACTION always writes exactly `step_number=1`; a retry-through-recovery reuses that same row (updates it), it never creates step 2.
4. **Tool idempotency contract**: `Tool.supports_idempotency: bool = False` (new, optional, defaults False — every existing tool remains valid unmodified) + `ExecutionContext.idempotency_key: str | None = None` (new, optional). A tool that sets `supports_idempotency = True` is asserting a *verified* claim (documented per-tool, see §10); the runtime never infers it.
5. **Identity generation**: `f"agent-exec:{execution_id}:{step_number}"` — `AgentExecutionService._idempotency_identity` (a `staticmethod`), also reused by `AgentReasoningService._attempt_tool_call` for the identical formula. Deterministic, server-generated only, never a fresh `uuid4()` per attempt.
6. **Where persisted**: *not* a new column. The identity is fully reconstructible from already-durable state (`AgentExecutionStep.execution_id` + `.step_number`, both persisted before the tool call), so no additional column was needed to "store" it — computing it is itself the durable operation, exactly like a natural key. This was a deliberate reconciliation choice to avoid an unnecessary migration (see §4's "prefer reuse over new columns" instruction).
7. **Where propagated**: `ExecutionContext.idempotency_key`, set once by `AgentExecutionService`/`AgentReasoningService` when constructing the context, read only inside a `Tool.execute()` implementation that opts in. `ToolRegistry.execute()` passes `context` through unchanged (it already did) — no new parameter, no bypass path.
8. **How recovery decides whether a tool already ran**: `AgentExecutionService.resume_recovered`'s decision table (see §8) — inspects the durable step's existence and `completed_at`, never guesses, never infers from elapsed time or execution status alone.
9. **Tool doesn't support idempotency**: safe-halt (`AgentExecutionStatus.HALTED`, `AgentExecutionTerminationReason.AMBIGUOUS_TOOL_OUTCOME_SAFE_HALT`) — never retried automatically, ever. A human inspects and manually re-issues if appropriate.
10. **Approval interaction**: `ApprovalExecutionService._record_execution_outcome`'s SINGLE_ACTION branch now also finalizes the step row (same transaction, same commit) before finalizing the `AgentExecution` row — so a crash between "tool ran via `ToolRegistry.execute(..., skip_approval_gate=True)`" and "outcome recorded" still leaves a terminal step a subsequent recovery attempt would find already-complete (boundary F), never re-invoking the tool. `ApprovalExecutionService` itself is otherwise byte-for-byte unchanged (no second approval mechanism).
11. **Postgres concurrency strategy**: unchanged from Phase 6 — the existing CAS lease claim (`UPDATE ... WHERE status='RUNNING' AND lease stale ... ` checked via `rowcount`) is the sole serialization mechanism for "which recovery worker gets to act on this execution." No new lock, no advisory lock, no `SELECT ... FOR UPDATE`. Tool-level idempotency concurrency (multiple recovery attempts retrying the *same* step for an idempotent tool) is a *different* axis, deliberately tested independently (without the execution-level lease serializing them) to prove the tool's own idempotency mechanism holds even without relying on the execution-level lock (see §13, tests 3 & 4).

## 4. SINGLE_ACTION durability — exact lifecycle

```
run_action:
  load agent+version -> idempotency pre-check -> rate/concurrency check
  -> INSERT AgentExecution (PENDING)
  -> UPDATE AgentExecution SET status=RUNNING, execution_owner_id=self, lease_expires_at=now+5m   [bug fix, see §14]
  -> INSERT AgentExecutionStep (step_number=1, TOOL_CALL, completed_at=NULL)      <-- durable boundary, BEFORE the tool call
  -> ToolRegistry.execute(tool_name, tool_input, context[idempotency_key=agent-exec:<id>:1])
  -> UPDATE AgentExecutionStep SET status=<outcome>, completed_at=now   <-- terminal, BEFORE marking the execution terminal
  -> UPDATE AgentExecution SET status=<terminal>, ...
```

`resume_recovered` (called only by `AgentRecoveryService`, after it already won the lease CAS):

| Step state found                                         | Action                                                                 |
|------------------------------------------------------------|-------------------------------------------------------------------------|
| No step row (boundary A/B — nothing ever attempted)         | Create the step now, run the tool once through the normal path.        |
| Step `completed_at` set, status `EXECUTED` (boundary F)     | Finish the execution from `step.output_summary` — tool never re-called. |
| Step `completed_at` set, status `APPROVAL_REQUIRED`         | No-op — already durably parked on `ApprovalExecutionService`'s own resume path. |
| Step `completed_at` set, any other terminal status          | Finish the execution as `FAILED`, reflecting the step's own error.      |
| Step `completed_at` NULL (boundaries C/D/E — mid-flight)     | `tool.supports_idempotency == True` → retry with the SAME identity. Else → safe-halt (`AMBIGUOUS_TOOL_OUTCOME_SAFE_HALT`), tool never re-called. |

## 5. `AgentExecutionStep` reuse

No new model, no new table, no new column. `AgentExecutionStep` already modeled everything needed (see §3.2). The model's docstring in `app/models/agent.py` was updated (comment only) to document this dual use explicitly.

## 6. Tool idempotency contract (exact)

- `Tool.supports_idempotency: bool = False` (class attribute, `app/tools/base.py`). Default False for every tool that doesn't explicitly opt in.
- `ExecutionContext.idempotency_key: str | None = None` (dataclass field, `app/tools/base.py`). Server-generated only; never read from tool output; never settable by the LLM (the LLM only ever produces `tool_name`/`arguments` — see `AgentDecision`; `arguments` never populates `ExecutionContext` fields, proven by `test_malicious_tool_input_cannot_influence_idempotency_identity_or_tenant`).
- A tool that sets `supports_idempotency = True` MUST have a verified, tested mechanism making a retried call with the same key resolve to at most one logical side effect. Only one tool in the current 233-tool inventory makes this claim (see §10).

## 7. Identity generation (exact formula)

```python
def _idempotency_identity(execution_id: uuid.UUID, step_number: int) -> str:
    return f"agent-exec:{execution_id}:{step_number}"
```

Defined once (`AgentExecutionService._idempotency_identity`, a `staticmethod`), imported/reused by `AgentReasoningService._attempt_tool_call` for the identical formula — one source of truth, not two independent implementations. Always the same string for the same `(execution_id, step_number)` pair, across retries, recovery, process restarts. Never derived from wall-clock time, randomness, or model output.

## 8. Recovery semantics (exact)

See §4's table. Implemented as `AgentExecutionService.resume_recovered`, called from `AgentRecoveryService._recover_one`'s `SINGLE_ACTION` branch (previously: always safe-halt with `RECOVERY_UNSAFE_SINGLE_ACTION`; now: delegates to the same decision table REASONING already uses the equivalent of). The old `RECOVERY_UNSAFE_SINGLE_ACTION` reason is preserved (not deleted) as the defensive fallback if `resume_recovered` itself raises before reaching any terminal state (should be unreachable in normal operation — covered by the existing broad `except Exception` in `_recover_one`).

## 9. Ambiguous external outcomes — exact policy

Per the mandatory "Unknown External Outcome" rule: a step left mid-flight (`completed_at IS NULL`) by a crash is *never* assumed to have succeeded or failed.

- **Case 1 (idempotent)**: `tool.supports_idempotency is True` → retry through `ToolRegistry.execute()` with the identical deterministic `idempotency_key`. The tool's own mechanism (a provider idempotency-key header, a DB unique constraint + upsert, or genuine read-only/naturally-idempotent semantics) is what actually makes this safe — the runtime does not itself dedupe, it only guarantees the *same key* is reused.
- **Case 2 (not verified idempotent)**: default. `AgentExecutionStatus.HALTED`, `AgentExecutionTerminationReason.AMBIGUOUS_TOOL_OUTCOME_SAFE_HALT`, tool never called again, human-actionable `error_message`. Proven never to call the tool under a 5-way concurrent race (`test_ambiguous_non_idempotent_tool_never_reexecuted_under_concurrency`).

## 10. Provider-specific behavior (exact, per tool category)

Inventory: 233 `Tool` subclasses across 56 files in `app/tools/builtin/`.

- **External side effect, provider-verified idempotent (1 tool)**: `finance.create_stripe_checkout_session` (`app/tools/builtin/stripe_tools.py`). Already used Stripe's own native `Idempotency-Key` header (`app/integrations/stripe_client.py`, pre-existing since Phase 12F) with a *business-derived* key (`invoice_id + amount_due`) — deliberately not switched to the agent-step-derived key, since the business key is the *stronger* guarantee (it also dedupes a human's manual retry through a different `AgentExecution` for the same invoice, not only a same-step crash-recovery retry). Marked `supports_idempotency = True`. Verified by the pre-existing `tests/test_stripe_client.py` plus this phase's own `IdempotentCounterTool`-based Postgres concurrency proof (a distinct fake tool, standing in for "a tool with a verified provider mechanism" without making any real network call).
- **External side effect, NOT verified idempotent (5 tools)**: `app/tools/builtin/quickbooks_tools.py`. `app/integrations/quickbooks_client.py` has no native idempotency-key mechanism (confirmed by direct inspection — no `idempotency` reference anywhere in that file). Left at the default `supports_idempotency = False`. Honestly "at-least-once" — never falsely upgraded to exactly-once, and a crash mid-QuickBooks-call now safe-halts instead of the old Phase 6 behavior of unconditionally halting *any* SINGLE_ACTION execution regardless of whether it was safe to retry.
- **Internal DB writes / read-only (227 tools)**: unmodified. Not audited individually tool-by-tool in this phase (explicitly out of the time budget for "audit all 233 tools" — flagged as a limitation in §16) — every one defaults to `supports_idempotency = False`, so an ambiguous crash mid-call safe-halts rather than guessing, which is always the conservative-safe default even for a tool that might, on individual inspection, turn out to be naturally idempotent (e.g. a `get_`/`list_`/`search_` tool). No tool was ever made to *falsely* claim idempotency.

## 11. Security (per scenario)

- **Tenant isolation**: `resume_recovered` raises `AgentExecutionError` on any tenant/execution mismatch (`test_resume_recovered_tenant_mismatch_denied`); every step read/write is additionally tenant-scoped via `AgentExecutionStep.tenant_id`.
- **Version immutability**: `resume_recovered` always reads `execution.agent_version_id` (the version pinned at execution start) and passes it unchanged into `ExecutionContext` — never `agent.current_version_id`. `ToolRegistry`'s own pre-existing "only the agent's current version may execute" check means a stale-version execution is *cleanly rejected* on recovery (not silently upgraded, and not silently allowed to keep running under the stale version) — `execution.agent_version_id` itself is proven never rewritten (`test_recovery_never_upgrades_to_a_newer_agent_version`, mirroring the identical REASONING-mode proof in `test_agent_recovery_security.py`).
- **Permission snapshot**: unchanged — `ToolRegistry._check_agent_tool_permission` already consults `AgentVersion.tool_permissions_snapshot` (the immutable one), never the live table, for every call this phase makes (no new permission-check code was added).
- **Autonomy / kill switch**: no special-case code — `resume_recovered` calls the exact same `ToolRegistry.execute()` every other path calls, which re-checks `Organization.ai_paused` and the autonomy ceiling fresh on every call (`test_kill_switch_blocks_recovered_execution_tool_call`).
- **Approval**: unchanged mechanism (`ApprovalExecutionService`'s own CAS), only extended to also finalize the durable step (§3.10). Repeated approval callback tests (`test_repeated_approval_callback_does_not_duplicate_side_effect`) pass unmodified.
- **Prompt injection / malicious tool output**: `test_malicious_tool_input_cannot_influence_idempotency_identity_or_tenant` proves tool *input* (attacker-controlled, if the LLM were compromised) cannot influence `idempotency_key`/`tenant_id`. Tool *output* was already never trusted for authority (`ToolRegistry` never reads tool output to set context fields) — Phase 7 adds no new output-trust surface.
- **RBAC**: unchanged — every governed call still passes through the same `role_has_permission` check.

## 12. Database

**No migration was required.** Zero new tables, zero new columns, zero new indexes, zero new constraints. `alembic current` remains `0047 (head)` before and after this phase's changes (verified). The only additions were: two new `StrEnum` values (`AgentExecutionTerminationReason.AMBIGUOUS_TOOL_OUTCOME_SAFE_HALT`, string-valued, no DB constraint references it — `termination_reason` is a plain `String(40)` column with no `CheckConstraint`), and two new plain Python-level fields (`Tool.supports_idempotency`, `ExecutionContext.idempotency_key`) that are not database columns at all — they live on the in-process `Tool`/`ExecutionContext` objects, never persisted directly (the idempotency identity itself is *computed*, not stored — see §3.6).

**RLS**: no new tenant-owned table — existing `AgentExecution`/`AgentExecutionStep` tenant controls (already RLS-audited in prior phases) remain authoritative, unchanged.

## 13. Concurrency validation (real Postgres evidence)

All four tests below in `tests/test_postgres_agent_single_action_reliability.py` ran against the real `pgserver` Postgres 16 instance and passed (`4 passed in 1.50s`):

1. **`test_five_way_recovery_race_exactly_one_worker_wins`** — 5 concurrent `AgentRecoveryService` instances (distinct `worker_id`s) call `sweep_once` simultaneously against the SAME stale SINGLE_ACTION execution. Exactly 1 of 5 claims it (`recovery_attempt_count == 1`, not 5); the execution completes.
2. **`test_five_way_duplicate_execution_request_one_logical_execution`** — 5 concurrent `run_action` calls with the identical `idempotency_key` for the same tenant+agent. Exactly 1 of 5 creates the row; the other 4 receive `DuplicateExecutionRequestError` pointing at the same id (exercising the exact `IntegrityError`-catch path from Phase 6's own bug fix, under a genuine 5-way race rather than the 2-way race that was proven insufficient in Phase 6).
3. **`test_five_way_concurrent_idempotent_tool_calls_one_logical_side_effect`** — 5 concurrent `resume_recovered` calls (no execution-level lease serialization — deliberately isolating the tool-level mechanism) against the same mid-flight step for a tool with `supports_idempotency=True`. The fake provider sees 5 real invocations (`call_count == 5`) but exactly 1 distinct logical side effect (`logical_operations == 1`) — proving the deterministic-identity + provider-idempotency mechanism holds under real concurrency, not just sequentially.
4. **`test_ambiguous_non_idempotent_tool_never_reexecuted_under_concurrency`** — negative control: the identical 5-way race against a tool with `supports_idempotency=False`. `call_count == 0` — the tool is never invoked by any of the 5 concurrent recovery attempts; all 5 safe-halt.

No raw `IntegrityError` observed or leaked in any of these paths (the pre-existing Phase 6 catch-and-convert pattern in `_create_execution_row` was exercised directly by test 2 above).

## 14. Bugs found

**Bug #1 (real, found during forensic inspection of `AgentExecutionService._mark_running`, before writing any new code)**: `_mark_running` never set `execution_owner_id`/`lease_expires_at`/`heartbeat_at` for a SINGLE_ACTION execution — meaning a SINGLE_ACTION execution was, for its entire RUNNING window (normally sub-second, but not bounded), already a "stale" candidate by `AgentRecoveryService._find_stale_candidates`'s own `lease_expires_at IS NULL` test. Under the old (Phase 6) code this was masked by "SINGLE_ACTION is always safe-halted anyway regardless of genuine staleness," so it never manifested as an incorrect resume — but it was a real latent gap that a race between a live SINGLE_ACTION call and a concurrent recovery sweep could have exposed once Phase 7 made SINGLE_ACTION resumable. Fixed by having `_mark_running` claim the same lease `AgentReasoningService.start`/`resume_after_approval` already claim for themselves (`AgentExecutionService.__init__` now takes an optional `worker_id`, mirroring `AgentReasoningService`'s existing pattern).

No other latent bugs were found in the reused Phase 4/5/6 code paths touched by this phase.

## 15. Regression

| | passed | failed | skipped | total |
|---|---|---|---|---|
| **Baseline** (before any Phase 7 edit, real Postgres, single pytest process) | 1660 | 1 | 12 | 1673 |
| **Phase 7 focused** (`test_agent_single_action_durability.py`, `test_agent_execution_service.py`, `test_agent_recovery_service.py`, `test_agent_recovery_security.py`) | 41 | 0 | 0 | 41 |
| **Broader agent/stripe/approval/cross-vertical slice** (`-k "agent or stripe or approval or cross_vertical"`) | 291 | 0 | 1 | 292 |
| **Postgres concurrency** (`test_postgres_agent_single_action_reliability.py`) | 4 | 0 | 0 | 4 |
| **Full suite — 1st run** (real Postgres, single pytest process) | 1674 | 3 | 12 | 1689 |
| **Full suite — 2nd run** (real Postgres, single pytest process, immediately after) | 1676 | 1 | 12 | 1689 |

New tests added this phase: **16** (10 in `test_agent_single_action_durability.py`, 4 in `test_postgres_agent_single_action_reliability.py`, net +2 in `test_agent_recovery_service.py` — 1 obsolete test replaced by 3 new ones). `1689 - 1673 = 16` — reconciles exactly.

**On the 1st full-suite run's 2 extra failures** (`test_automation_event_dispatch.py::test_event_trigger_starts_a_real_execution_on_real_dispatch`, `::test_event_dispatch_is_tenant_isolated`): investigated immediately, not silently reclassified as pre-existing.
  - Ran in isolation (`pytest tests/test_automation_event_dispatch.py`): both passed.
  - Ran as part of a 33-file alphabetical prefix subset (`test_agent_api.py` through `test_automation_event_dispatch.py`, including every Phase 7 test file) in a single process: all 306 tests passed, including these 2.
  - Ran the COMPLETE full suite a second time, single process, no code changes in between: **1 failed** (only the known pre-existing voice flake) — the 2 `test_automation_event_dispatch.py` failures did not reproduce.
  - Conclusion: a one-off, order/timing-sensitive flake in the full 1689-test run, not caused by any Phase 7 file — neither test touches `AgentExecution`/`AgentExecutionStep`/`ToolRegistry.idempotency_key`/anything this phase modified; they exercise `AutomationService`'s own event-trigger dispatch. This is the same category of finding Phase 4's own log documented (a first full run showing a spurious failure burst, root-caused to test-run conditions rather than a code defect, confirmed clean on a second single-process run). Given the 2nd run's clean result and the isolated/subset reruns both passing, this is recorded honestly as a pre-existing flake newly observed this session, not silently hidden and not falsely attributed to Phase 7 without evidence.

**Authoritative regression comparison** (2nd full run vs. baseline): passed 1660 → 1676 (+16, exactly the new tests), failed 1 → 1 (same pre-existing voice flake, unchanged), skipped 12 → 12 (unchanged). Zero net-new failures, zero new skips.

## 16. Known limitations (no overclaiming)

- **Only 1 of 233 tools has a verified idempotency mechanism wired.** Every other external-side-effect tool (QuickBooks, and any not yet reviewed) remains honestly at-least-once; an ambiguous crash mid-call safe-halts rather than silently retrying, but does NOT retry automatically the way the Stripe checkout tool does. This is the conservative, safe default — never a false exactly-once claim — but it does mean most tools still require a human to manually re-issue after a genuinely ambiguous crash, exactly as Phase 6 already did for ALL SINGLE_ACTION tools.
- **The 227 "internal DB write / read-only" tools were not individually audited** for whether they could safely be marked `supports_idempotency = True` (e.g. some `create_*` tools may already have their own unique-constraint-based dedup that would make them safe to mark). This is a real, scoped-down piece of follow-up work, not a correctness gap — the default (False) is always safe, just possibly more conservative than necessary for some tools.
- **SINGLE_ACTION recovery, like REASONING recovery before it, only covers executions that reached `RUNNING`** (crash boundary A in the truest sense — before `_mark_running` even commits — leaves the execution `PENDING` forever, un-swept). This is an existing, unchanged Phase 4/6 limitation, not something this phase introduced or was asked to close.
- **Tool-level idempotency concurrency (test 3/4 in §13) deliberately bypasses the execution-level lease** to isolate the tool mechanism — in production, the execution-level lease CAS (test 1) means only one recovery worker would actually reach that retry in the first place. Both layers were proven independently since they are independent safety properties, but their *combination* under adversarial timing (a worker's lease expires mid-retry, a second worker claims and retries concurrently) was not separately stress-tested beyond what test 1 already covers structurally.

## 17. Deferred to Phase 8+ (explicit)

MCP, Website Builder/generation/domain publishing, vertical domain models, marketplace UI/OAuth installation, autonomous business creation, Agent swarms/Agent-to-Agent orchestration, recursive Agents, a new workflow engine/scheduler/event bus, Temporal replacement, arbitrary code/HTTP/shell execution, unrestricted retries, dynamic self-modifying Agents, and a full per-tool idempotency audit of the remaining 227 tools (see §16). None of these were started.
