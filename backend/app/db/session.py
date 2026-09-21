from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

settings = get_settings()

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
