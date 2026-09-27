import uuid
from collections.abc import AsyncGenerator

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

settings = get_settings()
logger = structlog.get_logger(__name__)

_engine_kwargs: dict = {"pool_pre_ping": True, "echo": False}
if settings.DATABASE_URL.startswith("sqlite"):
    if ":memory:" in settings.DATABASE_URL:
        # Test fallback (see tests/conftest.py): `:memory:` SQLite only exists
        # for the connection that created it, so every session in the process
        # must share the one physical connection via StaticPool, or they'd
        # each see an empty, unrelated database.
        _engine_kwargs = {
            "poolclass": StaticPool,
            "connect_args": {"check_same_thread": False},
        }
    else:
        # File-based SQLite dev fallback (dev.db): unlike `:memory:`, a real
        # file is visible across separate connections on its own — no shared
        # StaticPool connection needed. This matters as of Phase 8: the
        # in-process Klaros Event Worker (see app/main.py's lifespan) now
        # polls continuously in the same process as request handling: two
        # SQLAlchemy AsyncSessions issuing genuinely concurrent transactions
        # against one *shared* StaticPool connection corrupt each other's
        # transaction state (a real bug hit and root-caused during Phase 8's
        # live verification — see PROJECT_STATUS.md). Giving each session
        # its own connection (still to the same dev.db file) lets SQLite's
        # own file-level locking serialize concurrent writers safely instead.
        # `timeout` (seconds) is SQLite's busy-timeout: a second writer waits
        # for the first to finish instead of immediately raising "database is
        # locked", since separate connections now really do serialize at the
        # SQLite file level rather than never overlapping at all.
        _engine_kwargs = {"connect_args": {"check_same_thread": False, "timeout": 15}}
else:
    # Default SQLAlchemy pool (size 5, overflow 10 = 15 max connections)
    # was too small for this app's own request shape: the owner dashboard
    # alone fans out to ~17 endpoints in parallel on a single page load,
    # each needing its own connection, which on its own was enough to
    # exhaust the pool and make every waiting request time out after 30s
    # — visible in prod logs as repeated "QueuePool limit of size 5
    # overflow 10 reached, connection timed out". Because that exception
    # is raised before the app's CORS middleware can attach headers to
    # the response, the browser reported it to users as a misleading
    # CORS failure rather than the real timeout underneath. Raised to
    # comfortably clear one dashboard load's own concurrency with
    # headroom for other simultaneous requests, while staying well under
    # typical free-tier Postgres connection ceilings for a single app
    # instance (this service runs numInstances: 1).
    _engine_kwargs["pool_size"] = 10
    _engine_kwargs["max_overflow"] = 20

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


# Phase 0 (KLAROS_PHASE_0_IMPLEMENTATION_PLAN.md §0.2 — RLS instrumentation,
# audit/permissive mode): the plumbing a future enforcing RLS policy will
# read via `current_setting('app.tenant_id', true)`. Using `SET LOCAL` (via
# `set_config(..., is_local=true)`, the third positional arg below) rather
# than a bare `SET` is deliberate and load-bearing: `SET` is *session*-scoped
# and would leak across pooled-connection reuse (the single highest-risk
# detail flagged in the Phase 0 plan) since this app's AsyncSession/engine
# uses a real connection pool (pool_size=10, max_overflow=20) against
# Postgres. `SET LOCAL`/`set_config(..., true)` resets automatically at the
# end of the current transaction, so a connection handed back to the pool
# never carries a stale tenant_id into its next, unrelated borrower. It is a
# genuine no-op today: no RLS policy in this migration set is enforcing yet
# (audit mode, per 0.2 — enforcement is 0.3, not in this Phase 0 slice), and
# on SQLite (the test suite's engine) this function does nothing at all, so
# it is always safe to call. Postgres requires this to run *inside* an open
# transaction for `is_local=true` to have any effect — callers must invoke
# it against a session that already has a transaction started (every
# AsyncSession call auto-begins one on first use, which is the case at both
# call sites below).
async def set_tenant_context(session: AsyncSession, tenant_id: uuid.UUID | None) -> None:
    """Best-effort, audit-mode tenant-context plumbing. No-op on SQLite and
    when `tenant_id` is None (never silently sets a bogus/empty value that a
    future enforcing policy could misread as "no tenant" == "see everything"
    — see the RLS section of PHASE_0_IMPLEMENTATION_LOG.md for the fail-safe
    design this must uphold once 0.3 flips these policies to enforcing)."""
    if tenant_id is None:
        # Observability (Phase 0 §Step 9): on Postgres, a call site that
        # has a live DB session but genuinely no tenant identity to stamp
        # is exactly the kind of gap the RLS rollout matrix needs to know
        # about before 0.3 turns policies enforcing — e.g. a webhook
        # handler (app/api/v1/webhooks.py) that derives tenant_id from a
        # signed payload later, not from auth. Logged, not raised: this
        # function's contract is "best-effort," never a new failure mode.
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            logger.debug("tenant_context_not_set", reason="tenant_id_is_none")
        return
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
