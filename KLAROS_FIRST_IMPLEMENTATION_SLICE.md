# Klaros AI — First Implementation Slice

Covers item GG. Not "build Klaros AI" — the exact, small, safely-implementable-and-validatable first engineering task.

## The slice: RLS instrumentation (audit-mode) on `IntegrationConnection`, plus a minimal CI pipeline

This is Phase 0 items 0.1 and the first table of 0.2, deliberately narrowed to the smallest concrete unit that both (a) has independent value and (b) de-risks everything downstream.

### Why this, specifically, first

Every other Phase 0/1 item either depends on CI existing to test it, or depends on the RLS session-variable plumbing being proven safe before it's extended to more tables. `IntegrationConnection` is chosen as the first table (not `Organization`/`User`, not `AuditLog`) because: it holds the most sensitive per-tenant data (encrypted credentials) among tables that are simple, single-parent, and already heavily exercised by existing tests (Stripe/QuickBooks/Google Calendar connect/disconnect flows) — meaning a mistake here would be caught fast by the existing test suite, and a success here proves the pattern on the highest-value table first rather than the easiest one.

### Scope (exactly)

1. Add a minimal CI workflow running: backend `pytest` (existing suite, as-is — do not fix any newly-discovered failures as part of this slice; log them as follow-up tickets, per Phase 0's own risk note), frontend `next lint` + `tsc --noEmit`, Docker build for both images. No security-scan stage yet, no migration-validation stage yet — those are the *next* slice, not this one.
2. Add `SET LOCAL app.tenant_id = '<uuid>'` to the request-scoped `get_db()` dependency (`backend/app/db/session.py`/`app/api/deps.py`), sourced from `CurrentUser.tenant_id`.
3. Add one Alembic migration: enable RLS on `IntegrationConnection` in **audit/permissive mode only** — the policy evaluates true unconditionally (zero behavior change to any existing request), paired with an application-level shadow-check (a debug-log line, not an exception) that would flag any row a strict policy *would have* blocked.
4. Add one new test: as Tenant A, attempt to read Tenant B's `IntegrationConnection` row directly via the ORM with `SET LOCAL app.tenant_id` set to Tenant A — confirm the shadow-check correctly identifies it as a would-be violation (proving the plumbing works) while confirming the actual query still succeeds today (proving zero behavior change, since enforcement isn't turned on yet).
5. Add a connection-pool-reuse test: two sequential requests on the same pooled connection, different tenants, confirm the second request's `SET LOCAL` correctly overrides the first (not leaks) — this is the single highest-risk implementation detail named throughout this document set, and it is explicitly tested in the very first slice, not assumed safe until later.

### What is explicitly excluded from this slice

RLS enforcement (still audit-mode only — enforcement is 0.3, a later slice, only after an observation window). Any other table beyond `IntegrationConnection`. The `Organization.autonomy_level` deprecation guardrail (0.4 — independent, can run as its own tiny parallel slice, not sequenced before this one). Staging environment. Frontend test harness. Any Discovery/Blueprint/Agent/Website feature code.

### Backend / Database / Frontend / AI / Security / Tests / Deployment / Dependencies / Acceptance criteria / Rollback

- **Backend:** `backend/app/db/session.py`, `backend/app/api/deps.py` (add `SET LOCAL`), new Alembic migration file (RLS policy, audit mode).
- **Database:** `IntegrationConnection` only, one migration, no schema change — policy only.
- **Frontend:** no application changes; only the new `next lint`/`tsc` CI stages, which run against existing code as-is.
- **AI:** not applicable to this slice.
- **Security:** this slice is itself the first concrete security deliverable — closes the "no CI" gap partially (basic stages only) and begins the RLS rollout with the lowest-risk possible first step (audit mode = provably zero behavior change).
- **Tests:** the two new tests described above (shadow-check correctness, connection-pool-reuse safety), plus the existing suite now running in CI for the first time ever.
- **Deployment:** no new deploy units; the CI workflow itself deploys nothing (no staging/prod deploy stages in this first slice — those come in the next CI-pipeline slice, per KLAROS_FINAL_DEPLOYMENT_ARCHITECTURE.md's full 8-stage design).
- **Dependencies:** none — this can start immediately.
- **Acceptance criteria:** (1) every PR now runs the CI workflow and merge is blocked on its stages failing; (2) the audit-mode policy runs in the dev/test environment for `IntegrationConnection` with zero observed behavior change against the existing Stripe/QuickBooks/Google Calendar connect/disconnect test coverage; (3) the connection-pool-reuse test passes, proving `SET LOCAL`'s per-transaction scoping is correctly implemented before any table is ever moved to enforcing mode.
- **Rollback:** revert the migration (drops the audit-mode policy, table returns to its current unprotected-but-unchanged state); revert the `SET LOCAL` code change (harmless no-op without a policy present to consume it — reverting is purely a cleanliness step, not a safety requirement); disable the CI workflow file if it proves disruptive (unlikely, since it only runs existing checks against existing code, changing nothing about how the application behaves).

### Why this is the correct "first slice" and not something larger

It is genuinely small (2 code files changed, 1 migration, 2 new tests, 1 new CI workflow file), it is independently valuable even if every later phase were cancelled (CI existing at all is valuable; proving the RLS pattern is safe is valuable even if RLS were never expanded further), and it directly retires the single riskiest unverified assumption in the entire roadmap — that `SET LOCAL`-based tenant context can be safely layered under the existing connection-pooled session architecture — before a single line of any later phase's code is written against that assumption.
