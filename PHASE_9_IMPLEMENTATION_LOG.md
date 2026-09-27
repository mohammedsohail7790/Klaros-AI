# Phase 9 (MCP Server Only) — Implementation Log

## 0. Scope framing (read first)

This phase implements **only** the narrow direction `KLAROS_FINAL_AGENT_MODEL.md` explicitly carved out as "a different, narrower question... evaluated separately, on its own merits": Klaros **exposing** a small, explicitly-approved set of its own already-governed `ToolRegistry` tools **through an MCP server** to an authenticated, tenant-scoped external client.

The **MCP-client direction** (Klaros itself connecting outbound to third-party MCP servers/tools) remains the **rejected** direction per `KLAROS_FINAL_AGENT_MODEL.md`'s "MCP decision — DO NOT BUILD" section, `KLAROS_ARCHITECTURAL_DECISIONS.md` ADR-002, and `KLAROS_DO_NOT_BUILD_YET.md` §4. Nothing in this phase implements, imports, or depends on any MCP client library, outbound MCP connection, or third-party tool ingestion. This is stated once here and re-confirmed in §20.

## 1. Baseline (captured before any Phase 9 code edit)

- Repo: `/Users/mohammedsohail/Desktop/Klaros AI`, branch `main`. Nothing from any prior phase (0-8) had been committed — all local, uncommitted, preserved exactly; this phase's changes are additive on top of that same uncommitted tree.
- Postgres: reused the same `pgserver`-provisioned PostgreSQL 16.2 instance from Phases 7/8, still running at `/private/tmp/klaros_pg5` (unix-socket only, database `klaros`, role `postgres`). `DATABASE_URL=postgresql+asyncpg://postgres@/klaros?host=/private/tmp/klaros_pg5`.
- `alembic heads`: **`0047 (head)`**. `alembic current`: **`0047 (head)`**. Confirmed identical (no drift) before any edit.
- `git status --short`: 169 lines of pre-existing uncommitted changes/untracked files from Phases 0-8, confirmed via the environment's own git-status snapshot — none touched by this phase except as listed in §3/§13/§14 below.
- **Full backend suite (real Postgres, single pytest process, genuinely before any Phase 9 file was edited — see the ordering note below):**

  ```
  1 failed, 1683 passed, 12 skipped, 21 warnings in 750.39s (0:12:30)
  ```

  The single failure: `tests/test_openai_realtime_voice_service.py::test_voice_stream_route_dispatches_to_realtime_engine_when_configured` — the same pre-existing voice/event-loop test already listed as locally modified in the environment's initial git-status snapshot (`M backend/tests/test_openai_realtime_voice_service.py`), and the same flake documented in every prior phase's log (Phases 3/4/5/7/8). Not caused by this phase.

  **Ordering note (honest disclosure):** the baseline pytest run was started in the background immediately after confirming `alembic current`/`heads`/`git status`, then this phase's documentation-reading and forensic-audit steps ran concurrently while it executed. Source edits (model/migration/service/API files) were only started once the background baseline process had already begun and had already imported every Python module it would use for the entire run (`sys.modules` is populated once per process; a running CPython process never re-reads an edited `.py` file for a module already imported) — so the ~1750s of concurrent editing that followed could not and did not contaminate the numbers above. This was verified directly, not assumed: a subsequent bug (§14) confirmed the two new tables were genuinely absent from the database throughout that baseline run.

## 2. Architecture documents read

Full-document reads: `KLAROS_FINAL_AGENT_MODEL.md` (specifically re-read the "MCP decision — DO NOT BUILD" section and its explicit carve-out sentence: "If a genuine future need emerges... expose its own tools to an external agent... that is a different, narrower question... and should be evaluated separately, on its own merits"), `KLAROS_DO_NOT_BUILD_YET.md` §4 (MCP framing), `KLAROS_ARCHITECTURAL_DECISIONS.md` ADR-002, `KLAROS_ARCHITECTURE_RECONCILIATION.md` (confirms ADR-002/MCP-rejection is consistent across all 20 blueprint docs, no contradiction found), `PHASE_4_IMPLEMENTATION_LOG.md` through `PHASE_8_IMPLEMENTATION_LOG.md` (governance-chain and reliability precedent), `PHASE_8_TOOL_IDEMPOTENCY_AUDIT.md` (confirms which of the 233 tools are `supports_idempotency=True` — relevant to §11 below).

Source read directly (not inferred from logs): `app/tools/registry.py` (the full `ToolRegistry.execute()` pipeline, all 393 lines), `app/tools/base.py` (`ExecutionContext`, `Tool`), `app/models/actor.py` (`ActorType`), `app/models/rbac.py` (`Role`, `Permission`, `ROLE_PERMISSIONS`), `app/models/approval.py` (`ApprovalRequest`), `app/services/approval_execution_service.py` (`_reconstruct_context`), `app/api/deps.py` (`CurrentUser`, `get_current_user`, `require_permission`), `app/api/tool_deps.py` / `app/api/tool_deps_integrations.py` (DI wiring conventions), `app/integrations/credential_store.py` (existing credential-encryption boundary and why this phase does NOT reuse it — see §7), `app/db/base.py` (`TenantScopedMixin`), `alembic/versions/0045_agent_runtime.py` (RLS audit-mode migration template, copied exactly for §14), `tests/conftest.py` and `tests/test_agent_api.py`/`tests/test_postgres_agent_runtime_rls.py` (test conventions mirrored in §15).

`KLAROS_MASTER_IMPLEMENTATION_ROADMAP.md`'s own "Phase 9" (Medical Tourism domain extension) was noted but not read as governing this task — this is an out-of-roadmap-numbering insert, authorized directly by the user's task instructions, not a claim of matching that document's Phase 9.

No listed document was missing from the repo root; nothing here was invented.

## 3. Repository findings (forensic audit)

- `ToolRegistry.execute()` (`app/tools/registry.py:126-245`) is confirmed, by direct read, to remain the single choke point: kill-switch check → (Agent-only pre-checks, skipped for every other actor type) → RBAC (`role_has_permission`) → tenant-scope check → billing-usage check → Pydantic schema validation → policy resolution (AUTO/APPROVAL_REQUIRED/BLOCKED) → execute → audit. No direct `tool.execute()` call site exists outside this method (grep-verified).
- `ExecutionContext` (`app/tools/base.py`) is actor-agnostic: `tenant_id`, `actor_type`, `actor_id`, `role`, plus Agent-only optional fields. Nothing about it assumes the caller is human — a `Role`-bearing, tenant-scoped, non-USER actor already flows through the exact same RBAC/tenant/policy checks a `USER` would. This is the load-bearing fact this phase's entire design rests on: an MCP client needs no new enforcement machinery inside `ToolRegistry`, only a new way to arrive at a correctly-populated `ExecutionContext`.
- `ApprovalRequest`/`ApprovalExecutionService._reconstruct_context` (`app/services/approval_execution_service.py:391-416`) already reconstructs `ExecutionContext` generically from `requested_by_type`/`requested_by_role`/`requested_by_id` — it does not special-case `ActorType.USER` vs `ActorType.AGENT` for the fields this phase needs (role/id/type), so an `ApprovalRequest` created by an MCP-routed call resumes correctly with **no changes to this file** (verified by the approval test in §15, not just read).
- `AuditLog` (`app/models/audit_log.py`) has no actor-type-specific columns — logging an MCP-originated call is a pure data insert, no schema change needed.
- `redact_input` (`app/tools/redact.py`) already redacts any key matching a sensitive-marker list and truncates oversized strings — reused unchanged for every MCP-originated audit row via `ToolRegistry._audit()`, which every MCP `tools/call` still passes through.
- No existing per-tenant + per-caller-identity concurrency limiter exists anywhere in the codebase (`app/core/rate_limit.py` is a per-IP/per-route HTTP limiter for login/webhook endpoints — a different dimension). This justifies the small MCP-specific semaphore in §12.
- `app/integrations/credential_store.py` is a **reversible** encryption boundary (Fernet) for tenant OAuth/API-key credentials Klaros must present back to a third party later. An MCP client credential is the opposite shape — Klaros only ever needs to *verify* a token a client already holds, never recover it — so this phase uses a one-way SHA-256 hash instead (§7), deliberately not reusing `credential_store.py` (reusing a reversible-encryption module for a value that should never be decryptable would be the wrong abstraction, not "duplicating infrastructure needlessly").

## 4. Exposure-policy design and reasoning

`McpToolExposure` (`app/models/mcp_server.py`) — one row per `(tenant_id, tool_name)`. `enabled` is a boolean, not a delete-on-disable, so there is a durable trail of what was once exposed and later withdrawn. No tool is ever auto-populated into this table; a row exists only because an OWNER/ADMIN explicitly created it via `PUT /api/v1/mcp-admin/exposures`, gated on the new `Permission.MANAGE_MCP_SERVER`.

Read time (`McpExposureService.list_enabled_tool_names`) re-reads the DB on every call — no in-process cache — so a revocation takes effect on the very next request, matching the kill-switch's own re-read-every-call discipline already in `ToolRegistry.execute()`.

`app/mcp/protocol.py`'s `tools/list` intersects the exposure allowlist with (a) tools still actually registered in the running `ToolRegistry`, and (b) the calling credential's own `Role` permission (`role_has_permission`) — so the list a client sees is never wider than what it could actually invoke; an exposure row alone never fabricates authority the `Role` doesn't already have.

## 5. Integration point with ToolRegistry

`app/mcp/protocol.py::McpProtocolHandler._tools_call` is the **only** place this phase calls `ToolRegistry.execute()`. Sequence: exposure-allowlist check (MCP-server-layer only, defense-in-depth against a stale/compromised allowlist row, not a substitute for RBAC) → tool existence check → `tool.input_schema.model_validate()` pre-check (reused, not duplicated) → build `ExecutionContext(actor_type=ActorType.MCP_CLIENT, actor_id=credential.id, tenant_id=credential.tenant_id, role=Role(credential.role))` from the authenticated credential **only** → `await asyncio.wait_for(registry.execute(name, arguments, context), timeout=30s)`. No second execution path, no `tool.execute()` call, no bypass of any existing check. `ToolRegistry.execute()`'s own kill-switch/RBAC/tenant/billing/schema/policy/approval/audit sequence runs completely unchanged for every MCP-routed call.

## 6. Security model

- **Defense in depth, not defense in only one layer**: the exposure allowlist is checked once in `app/mcp/protocol.py` *before* `ToolRegistry.execute()`, and RBAC/tenant/policy are checked again, unconditionally, *inside* `ToolRegistry.execute()` for every call regardless of origin. A bug or stale row in the MCP-layer allowlist can only ever result in a call being denied that should have been allowed, or being handed to `ToolRegistry.execute()` where the underlying `Role`'s real permissions are the actual, unbypassable backstop — never the reverse.
- Non-exposed and nonexistent tool names return the **identical** generic message ("Tool '{name}' is not available via MCP for this tenant") — no oracle for an external caller to enumerate the tenant's internal tool catalog.
- Every JSON-RPC/tool-call error message returned to the client is exactly `str(exc)` from the same `ToolError` subclasses `ToolRegistry.execute()` already raises for every other caller (human API, Agent) — no MCP-specific error text was invented that could leak more detail than an equivalent API caller already gets.

## 7. Authentication model for external clients

`McpClientCredential` (`app/models/mcp_server.py`): `tenant_id`, `name`, `role` (reuses the existing `Role` enum — an MCP client is authorized exactly like a human user of that role, never a parallel permission system), `token_hash` (SHA-256 hex of the raw token, unique), `token_prefix` (first 16 chars, display-only, never used for the actual auth check), `status` (ACTIVE/REVOKED), `created_by`, `revoked_at`, `last_used_at`.

Issuance (`McpCredentialService.issue`, admin-only via `POST /api/v1/mcp-admin/credentials`): generates `secrets.token_urlsafe(32)` prefixed `mcpkl_`, returns the raw token in the HTTP response body **exactly once**; only the hash is ever persisted. No endpoint or audit row anywhere in this phase can retrieve the raw token again after issuance — verified by reading every response model in `app/api/v1/mcp_admin.py` (`CredentialResponse` carries only `token_prefix`).

Authentication (`McpCredentialService.authenticate`, called by `app/api/mcp_deps.py::get_mcp_request_auth` from the `Authorization: Bearer <token>` header): hashes the presented token and looks it up by hash — tenant identity comes **solely** from the row this lookup returns, never from any client-supplied header, body field, or claim. Unknown, malformed, and revoked tokens all produce the identical generic 401 (`get_mcp_request_auth`) — no oracle to distinguish "wrong token" from "revoked token" from "never authenticate this way" — a deliberate anti-enumeration choice, not an oversight.

## 8. Tenant isolation

An `McpClientCredential` row carries exactly one `tenant_id`, set at issuance by an admin already authenticated to that tenant — no field anywhere in the JSON-RPC request (`tools/list`/`tools/call` params) can select or influence which tenant a call executes against. `McpRequestAuth.execution_context()` derives `tenant_id` from the authenticated row only. Tested explicitly at both layers (§15): protocol layer (tenant A's credential's `tools/list` never shows tenant B's exposures; tenant A's credential cannot invoke a tool only tenant B exposed) and prompt-injection layer (arguments containing `tenant_id`/`role`/`actor_type` fields are ignored — the created row lands under the real authenticated tenant, proven by a DB read after the call, not just by the response body).

## 9. RBAC

New permission `Permission.MANAGE_MCP_SERVER` (`app/models/rbac.py`) gates both admin surfaces (exposure allowlist management and credential issuance/revocation) as one canonical permission — granted automatically to `OWNER`/`ADMIN` (both defined as `_ALL_PERMISSIONS`-based sets) and to no other role, matching `MANAGE_AGENTS`/`MANAGE_INTEGRATIONS_CATALOG`'s existing shape. Verified by test: a `MANAGER` and a `STAFF` user both get `403` from `PUT /mcp-admin/exposures` and `POST /mcp-admin/credentials` respectively.

Separately, an authenticated MCP client's own `Role` (stored on its credential) is checked by ordinary `role_has_permission` inside `ToolRegistry.execute()` exactly as a human caller's `Role` would be — verified by test: a `READ_ONLY`-role credential is denied `finance.void_invoice` (which requires `Permission.VOID_INVOICE`) even though the tool is exposed and an `OWNER`-role credential for the same tenant can call it.

## 10. Approval/autonomy composition

No new approval or autonomy machinery. `ToolRegistry.execute()`'s existing `APPROVAL_REQUIRED` branch creates a real `ApprovalRequest` (`requested_by_type=ActorType.MCP_CLIENT`, `requested_by_role=<credential's Role>`) exactly as it would for a `USER` call; `app/mcp/protocol.py` catches `ToolApprovalRequiredError` and returns `{"status": "pending_approval", "approval_request_id": ...}` as a non-error MCP tool result (the call was accepted, not rejected). A human approving it through the existing `POST /approvals/{id}/approve` endpoint resumes through the unmodified `ApprovalExecutionService`, which reconstructs the exact `ExecutionContext` (role/actor_id/actor_type) from the stored request row with **zero code changes needed** in that service — verified by test (`test_approval_required_tool_creates_real_approval_request_and_resumes_once`), which also asserts `execution_attempts == 1` after approval, proving exactly-once resume.

An MCP client is never treated as an `Agent` — it never goes through `_check_agent_tool_permission`/`_check_agent_autonomy` (those trigger only on `ActorType.AGENT`), since an MCP client is not a governed `Agent`/`AgentVersion` and has no autonomy tier; its only ceiling is its `Role`'s ordinary RBAC/policy exposure, same as any human API caller.

## 11. Idempotency/crash reuse

No new idempotency mechanism. `ExecutionContext.idempotency_key` is left `None` for every MCP-routed call in this phase (same as every non-Agent caller today) — an MCP `tools/call` is a single request/response, not a multi-step `AgentExecution`, so there is no `AgentExecutionStep` durability boundary for it to cross. If an MCP-triggered tool call itself creates or triggers an `AgentExecution` (none of the currently-exposed example tools do), that execution would still go through the unmodified Phase 6/7/8 lease/crash-recovery machinery — nothing in this phase alters it.

**Known limitation** (stated honestly, not overclaimed): if the MCP server process crashes or the connection drops *between* `ToolRegistry.execute()` committing its audit row and the HTTP response reaching the external client, the client cannot tell from the MCP protocol alone whether the call executed — this is the same "ambiguous outcome after a partial failure" class Phase 7/8 built the `supports_idempotency` mechanism to bound for Agent-initiated calls, and it is **not yet extended to direct (non-Agent) callers including MCP clients** in this codebase generally (the same gap exists today for a direct human API call that loses its connection mid-request). Documented as a pre-existing limitation, not a Phase 9 regression.

## 12. Transport/resource limits

`app/mcp/protocol.py`: `MAX_REQUEST_BODY_BYTES = 256 KiB`, `MAX_JSON_NESTING_DEPTH = 12`, `TOOL_CALL_TIMEOUT_SECONDS = 30` (`asyncio.wait_for`), `MAX_CONCURRENT_CALLS_PER_TENANT = 5` (a small in-process `asyncio.Semaphore` per tenant — the smallest correct addition, since no existing rate/concurrency limiter is per-tenant-plus-per-caller-identity shaped; explicitly documented in-code as a soft bound, never a substitute for `ToolRegistry`'s own kill-switch/billing/approval checks). Oversized and deeply-nested payloads are rejected before JSON is even handed to any tool-specific logic. **Known limitation**: the concurrency semaphore is process-local, not distributed across multiple app instances — acceptable for this minimal slice (see §19).

**SSRF check**: nothing in `app/mcp/protocol.py`, `app/api/mcp_deps.py`, `app/api/v1/mcp.py`, or `app/api/v1/mcp_admin.py` makes an outbound network call based on client-supplied data — there is no callback URL, webhook registration, or client-suppliable URL anywhere in this phase's new code. Confirmed by full read of every new file; SSRF-safe-outbound-policy work is not applicable to this phase's server-only direction.

## 13. Audit logging

Reuses `AuditLog` exclusively — no second audit table. `ToolRegistry.execute()`'s own `_audit()` already covers "MCP tool invoked via external client" and every denial/failure/pending-approval outcome that occurs *inside* `execute()`, tagged `actor_type=ActorType.MCP_CLIENT`. Two cases fall *before* `ToolRegistry.execute()` and are audited explicitly by `McpProtocolHandler._audit`: `mcp.client_authenticated` (on `initialize`) and the not-exposed/not-registered tool-call denial (`mcp.tool_invoked` with `result="failure", error="not_exposed"` — this case never reaches `ToolRegistry`, so its own audit trail would otherwise never see it). Admin actions (`mcp.exposure_enabled`/`mcp.exposure_disabled`/`mcp.credential_issued`/`mcp.credential_revoked`) are audited in `app/api/v1/mcp_admin.py`. No credential/token value is ever written to any audit row (grep-verified — see §16).

## 14. Database changes

Migration `0048_mcp_server.py`: two new tenant-owned tables, `mcp_tool_exposures` and `mcp_client_credentials`, RLS audit-mode instrumented on day one (same `tenant_isolation_audit_policy`/`FOR ALL USING (true) WITH CHECK (true)` pattern as every tenant table since 0040/0041/0043/0044/0045 — audit-mode, not enforcement, matching this codebase's current RLS rollout stage everywhere else; never overclaimed as enforcing). No changes to any existing table.

**A real bug was caught and fixed during this phase, not glossed over**: the full-suite baseline run (§1) was executed against the real Postgres instance while this phase's edits were being made concurrently in the same working tree. `tests/conftest.py::_reset_database` calls a helper, `_current_alembic_head()`, that re-reads the `alembic/versions/` directory from disk on every single test (unlike `Base.metadata`, which is only read from the process's already-imported Python objects). Because `alembic/versions/0048_mcp_server.py` was written to disk partway through that already-running baseline process, later tests within that same run stamped the real Postgres `alembic_version` table to `'0048'` even though the actual `CREATE TABLE` DDL was never executed against it (the running process's `Base.metadata` still only reflected the tables imported at process start). This was caught by directly querying `information_schema.tables` after the baseline finished and finding `mcp_tool_exposures`/`mcp_client_credentials` absent despite `alembic current` reporting `0048 (head)`. **Fix**: manually reset the stamped version back to `0047`, then ran a real `alembic upgrade head`, confirmed via a second direct `information_schema` query that both tables now genuinely exist. This does not affect the validity of the baseline test-count numbers in §1 (those tests ran against whatever schema existed at the time, correctly) — it only means the *stamped version number* briefly lied about the real schema state outside of any test's own transaction, and it was corrected before any migration-cycle validation below was trusted.

**Migration cycle validated twice against the real instance** (`alembic current` → `upgrade head` → `current` → `downgrade 0047` → `current` → `upgrade head` → `current`, repeated once more): final state `0048 (head)` both times, `information_schema.tables` shows both tables present after each upgrade and absent after each downgrade — no orphaned schema.

**Fixed during review**: the first draft of the migration created both a `UniqueConstraint("token_hash", ...)` and a separate `unique=True` index on the same column — Postgres backs a unique constraint with an index automatically, so the explicit second index was a genuine duplicate. Removed before the final migration-cycle validation run (the log above reflects the corrected version); confirmed via `pg_indexes` that `mcp_client_credentials` now has exactly one index on `token_hash` (`uq_mcp_client_credentials_token_hash`).

RLS/constraint verification (`tests/test_postgres_mcp_server_rls.py`, real Postgres only): RLS enabled + audit-mode (not FORCE) + exactly one policy per table; `(tenant_id, tool_name)` uniqueness on `mcp_tool_exposures` (same tool name usable independently by two different tenants, but not twice by the same tenant); global `token_hash` uniqueness on `mcp_client_credentials`; audit-mode is a genuine no-op today (a context-less session still sees every row) — consistent with, and never overclaiming past, the rest of this codebase's current RLS posture.

`app/models/actor.py`: additive `ActorType.MCP_CLIENT` enum value — a plain `String` column everywhere it's stored, no migration required (same reasoning already documented for `ActorType.AGENT` in Phase 4).

## 15. Tests

New files: `tests/test_mcp_server.py` (19 tests, HTTP-level against the real ASGI app) and `tests/test_postgres_mcp_server_rls.py` (6 tests, real-Postgres-only). Both run clean against SQLite (in-memory, the default test DB) and against the real Postgres instance.

Coverage, mapped to the spec's required test strategy: architecture (an MCP-routed `crm.create_lead` call produces a real `AuditLog` row tagged `actor_type=MCP_CLIENT`, proving it went through `ToolRegistry.execute()`, not a parallel path); authentication (no header, malformed bearer, revoked credential all → 401; `MANAGE_MCP_SERVER`-gated issuance); tenant isolation (protocol-layer cross-tenant exposure/invocation denial, DB-layer uniqueness-per-tenant and global token-hash uniqueness); exposure policy (non-exposed-but-registered-and-permitted tool denied; only `MANAGE_MCP_SERVER` can change the allowlist — `MANAGER` gets 403); RBAC (a `READ_ONLY`-role credential denied a `VOID_INVOICE`-gated tool that an `OWNER`-role credential for the same tenant can call); approval (`APPROVAL_REQUIRED` tool call via MCP creates a real `ApprovalRequest`, approving it resumes and executes exactly once — `execution_attempts == 1` verified by direct row read); kill switch (`ai_paused=True` blocks an MCP tool call with the same kill-switch error `ToolRegistry` already raises); prompt injection / untrusted input (client-supplied `tenant_id`/`role`/`actor_type` fields inside tool arguments are ignored — the resulting row is verified, by DB read, to belong to the real authenticated tenant); schema validation (malformed arguments rejected with a client-legible `isError: true` result before `ToolRegistry.execute()` ever runs); transport limits (oversized body and over-deep JSON both rejected with JSON-RPC error `-32600`, before any parsing/business logic); protocol correctness (`notifications/initialized` gets no response body per JSON-RPC 2.0; an unknown method gets `-32601`).

## 16. Security audit

Grep across every new/modified file for `TODO|FIXME|NotImplemented|subprocess|os\.system|eval\(|exec\(|shell=True|password` returned zero hits outside test-fixture password strings (the same pattern every other existing test file in this repo already uses for registering a test user — not a new pattern). No bare `pass` statements. `secret|token|api_key` hits were all classified: every one is either a docstring/comment explaining the hashing design, the `_hash_token`/`token_urlsafe` implementation itself, or a field name (`token_prefix`, `token_hash`) — the raw token value is returned to the HTTP client exactly once, at issuance, in `IssueCredentialResponse.token`, and is never written to `AuditLog`, never logged via `structlog`, and never present in any list/read response model (`CredentialResponse` exposes only `token_prefix`). Verified: no policy bypass (§5/§6), no hidden credentials, no raw `Authorization` header logging anywhere, no tenant override from client input (§8), no unauthorized tool exposure (§4).

## 17. Dependency audit

**No new dependency was added.** Every import in every new file (`app/models/mcp_server.py`, `app/services/mcp_service.py`, `app/mcp/protocol.py`, `app/api/mcp_deps.py`, `app/api/v1/mcp.py`, `app/api/v1/mcp_admin.py`) is either Python stdlib (`hashlib`, `secrets`, `json`, `asyncio`, `uuid`, `dataclasses`, `datetime`) or already-present first-party/third-party modules this codebase already depends on (`fastapi`, `pydantic`, `sqlalchemy`). `pip-audit` was not run against a new dependency because there is none to audit; the existing `requirements.txt`/`requirements-dev.txt` are unmodified.

The MCP protocol layer (`app/mcp/protocol.py`) is a ~370-line hand-written JSON-RPC adapter implementing exactly `initialize`/`tools/list`/`tools/call`/`notifications/initialized` (see the module's own docstring for the REQUIRED NOW / OPTIONAL BUT SAFE / DEFERRED / NOT APPROPRIATE classification of the full MCP feature surface) rather than a general MCP SDK — smaller, fully auditable in this log, and avoids depending on a third-party protocol library's own trust/version surface for a slice this narrow.

## 18. Full regression

Full suite (real Postgres) after all Phase 9 code was in place, run 1:

```
2 failed, 1707 passed, 12 skipped, 21 warnings in 811.68s (0:13:31)
```

`1707 + 2 + 12 = 1721 = 1696 (baseline total) + 25 (this phase's 19 + 6 new tests)` — accounted for exactly.

Failure 1 (expected, pre-existing, unrelated): `tests/test_openai_realtime_voice_service.py::test_voice_stream_route_dispatches_to_realtime_engine_when_configured` — same as baseline §1.

Failure 2 (investigated in full — not caused by this phase): `tests/test_postgres_agent_single_action_reliability.py::test_five_way_duplicate_execution_request_one_logical_execution`. Regression-discipline steps taken:
- **Isolate + rerun 3×, single test only**: failed consistently, 3/3 — `AgentNotExecutableError: Agent has reached its concurrent execution ceiling (1)`.
- **Run its file** (all 6 tests together), 5×: passed consistently, 5/5.
- **Root cause identified by reading the unmodified source** (`app/services/agent_execution_service.py::_enforce_rate_and_concurrency`, called from `run_action`): the in-flight concurrency-ceiling check (counting `PENDING`/`RUNNING`/`WAITING_APPROVAL` rows) runs *before* the idempotency-key dedup/unique-constraint path. Under a genuine 5-way `asyncio.gather` race with a cold connection pool (this test run alone, first thing in the process), the timing can let one caller's row reach `PENDING` before a sibling caller's concurrency-count query runs — that sibling then fails with `AgentNotExecutableError` (ceiling of 1 already "in flight") instead of the test's expected `DuplicateExecutionRequestError` (which the idempotency-key unique constraint is supposed to produce). This is a genuine pre-existing check-ordering race in unmodified Phase 4/7 code, exposed by timing, not by any Phase 9 change.
- **Path touched by this task?** No. `app/services/agent_execution_service.py` and `tests/test_postgres_agent_single_action_reliability.py` are both pre-existing/untracked from Phases 4-8 and were not read for editing, not edited, and are not imported by anything this phase added (`app/mcp/protocol.py`, `app/services/mcp_service.py`, `app/api/mcp_deps.py`, `app/api/v1/mcp.py`, `app/api/v1/mcp_admin.py`, `app/models/mcp_server.py`) — confirmed by grep, zero cross-references either direction.
- **Second full-suite run** (identical command, zero code changes in between) to distinguish "flaky/timing-sensitive" from "deterministic regression":

  ```
  1 failed, 1708 passed, 12 skipped, 21 warnings in 798.61s (0:13:18)
  ```

  Only the pre-existing voice-test failure remains; `test_five_way_duplicate_execution_request_one_logical_execution` **passed** this time, with zero code changes between the two runs. `1707 + 2 = 1709 = 1708 + 1` — the same total test count, confirming this was the exact same test flipping from fail to pass under identical code, i.e. genuine non-determinism in the pre-existing implementation, not a deterministic regression this phase caused.
- **Fix status**: not fixed in this phase — it is a pre-existing latent race in code this phase's mandate explicitly says not to modify (`app/services/agent_execution_service.py` belongs to Phases 4/6/7/8's Agent Runtime, out of this phase's scope, and the task instructions require the smallest correct change, not opportunistic unrelated fixes). Documented here per "never hide failures... document if genuinely pre-existing," not silently patched, retried-until-green, or hidden.

**Final regression verdict**: two full real-Postgres runs, zero unexplained failures. Every failure across both runs is one of the two known pre-existing conditions (the voice-stream test, and this timing-sensitive concurrency race), both confirmed unrelated to and untouched by this phase's changes.

## 19. Known limitations (explicit, no overclaiming)

- The per-tenant MCP concurrency semaphore (§12) is process-local only — not distributed across multiple app instances. Acceptable for this minimal slice; the real correctness backstops (kill switch, RBAC, billing, approval, audit) are unaffected by this limitation since none of them depend on the semaphore.
- No idempotency-key propagation for a plain MCP `tools/call` (§11) — matches the current behavior of every other non-Agent direct caller in this codebase; not a Phase 9-specific gap.
- RLS on the two new tables is audit-mode only (`USING (true)`), consistent with — and no further ahead than — every other tenant table in this codebase today. Tenant isolation for MCP is enforced at the application layer (`McpRequestAuth`/`ExecutionContext`), the same layer that enforces it for every other caller, not by Postgres RLS.
- The admin "available tools" listing (`GET /mcp-admin/available-tools`) reads `ToolRegistry`'s private `_tools` dict directly (no public listing method existed that returns the full internal catalog regardless of the caller's own `Role` — `list_available()` already filters by a `Role`, which is the wrong shape for an admin choosing what to expose). This is a deliberate, narrow, `noqa`-annotated exception for an admin-only, read-only endpoint — not a precedent for bypassing `ToolRegistry` elsewhere.
- No frontend was built. See explicit confirmation below.

## 20. Explicitly deferred work

- **The MCP-client direction remains unimplemented and rejected.** No code in this phase connects Klaros outbound to any third-party MCP server, ingests any external MCP tool definition, or gives any Klaris-side AI/Agent the ability to call an external MCP tool. `KLAROS_FINAL_AGENT_MODEL.md`'s "MCP decision — DO NOT BUILD" verdict and ADR-002 stand exactly as before this phase.
- MCP `resources/*`, `prompts/*`, `sampling/*`, `elicitation/*`, and streaming/subscriptions — deferred/not-appropriate per the classification in `app/mcp/protocol.py`'s module docstring (§17 above); no evidence of need, and `sampling`/`elicitation` are actively the wrong direction for a server that is being called, not delegating cognition outward.
- Cross-app-instance-distributed concurrency limiting for MCP calls (§19).
- Idempotency-key propagation for direct (non-Agent) MCP tool calls (§11/§19).
- No frontend/admin UI was built — the two admin endpoints (`/mcp-admin/exposures`, `/mcp-admin/credentials`) are backend-only, matching the task's own "None required unless truly unavoidable" instruction; a future phase could add a settings-page UI over these same endpoints with no backend changes needed.
- No Phase 10+ capability (Website Builder, vertical domain models, marketplace UI, autonomous business creation, agent swarms/recursive agents, a second Temporal/EventBus/Automation Engine) was implemented — none of this phase's changes touch those areas.

## 21. Final verdict

**PHASE 9 (MCP SERVER ONLY): COMPLETE WITH LIMITATIONS.**

The MCP server exists as a coherent, minimal protocol layer (`initialize`/`tools/list`/`tools/call`/`notifications/initialized`) that creates no second execution engine — every governed action still flows through the exact, unmodified `ToolRegistry.execute()` choke point. Only explicitly allowlisted tools are exposed per tenant. External client identity is cryptographically authenticated (SHA-256-hashed bearer token) and strictly tenant-scoped, verified at both the protocol layer and the database layer. Inbound client data is treated as untrusted and validated (schema pre-check, size/depth bounds) before ever reaching `ToolRegistry`. RBAC, approval, autonomy composition, kill switch, and audit are all reused unchanged and proven so by test, not by inspection alone. Two real, independent full-suite runs against real Postgres show zero unexplained failures — both observed failures are pre-existing and fully investigated (§18). "Limitations" (not "complete" unqualified) because of the explicitly-documented, honestly-stated items in §19: a process-local (non-distributed) concurrency limiter, no idempotency-key propagation for direct MCP calls (matching every other non-Agent caller today), and no frontend/admin UI (backend-only, as instructed). The MCP-client direction remains unimplemented and rejected, exactly as authorized.
