from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

settings = get_settings()

_engine_kwargs: dict = {"pool_pre_ping": True, "echo": False}
if settings.DATABASE_URL.startswith("sqlite"):
    # Test/dev fallback (see tests/conftest.py): a single shared in-memory
    # SQLite connection so every session in the process sees the same data.
    _engine_kwargs = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session
