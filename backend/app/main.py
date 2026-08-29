import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

settings = get_settings()
configure_logging()
logger = get_logger(__name__)


_INSECURE_DEFAULT_JWT_SECRET = "change-me-in-production"


def _assert_production_secrets_are_real() -> None:
    """Refuse to boot in production with the publicly-visible default JWT
    secret — every valid token is signed with it, so anyone reading this
    open-source codebase could forge a token for any tenant/user/role
    against a deployment that forgot to override it. This check has to
    live in code, not just documentation, since a missed .env value is
    exactly the kind of mistake documentation doesn't catch."""
    if settings.ENV == "production" and settings.JWT_SECRET == _INSECURE_DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "Refusing to start: ENV=production but JWT_SECRET is still the insecure default "
            "('change-me-in-production'). Set a real, random JWT_SECRET before deploying."
        )
    # Phase 12D: same reasoning as JWT_SECRET above, for tenant-owned
    # integration credentials (OAuth tokens, per-tenant API keys) — an
    # unset key falls back to a publicly-known default in
    # app/integrations/credential_store.py, which would let anyone reading
    # this codebase decrypt any tenant's stored credential in a deployment
    # that forgot to set a real one.
    if settings.ENV == "production" and not settings.INTEGRATION_CREDENTIAL_ENCRYPTION_KEY:
        raise RuntimeError(
            "Refusing to start: ENV=production but INTEGRATION_CREDENTIAL_ENCRYPTION_KEY is unset "
            "(would fall back to a publicly-known default key). Set a real, random value before deploying."
        )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    _assert_production_secrets_are_real()
    logger.info("klaros_api_startup", env=settings.ENV)

    # Wire event-bus subscriptions eagerly at startup rather than lazily on
    # first request to /api/v1/events/* (the previous behavior — the bus
    # singleton had zero subscribers, and therefore silently dropped every
    # published event, until something happened to hit that one dependency
    # first). See PROJECT_STATUS.md Phase 8 for how this was found.
    from app.api.tool_deps import get_wired_event_bus

    bus = get_wired_event_bus()

    worker_task: asyncio.Task | None = None
    shutdown_event = asyncio.Event()
    if settings.EVENT_TRANSPORT == "memory":
        # Dev/test fallback: InMemoryTransport's state lives only inside this
        # process's memory, so the continuous worker has to run co-located
        # with the API here — the same reason InMemoryTransport itself
        # exists (see app/events/transport.py). In production
        # (EVENT_TRANSPORT=redis) this task is NOT started; a separate
        # `event-worker` Docker Compose service consumes the same Redis
        # Streams the API publishes to, genuinely out-of-process.
        from app.api.tool_deps import get_morning_brief_service
        from app.events.worker import EventWorker

        morning_brief_service = get_morning_brief_service()
        worker = EventWorker(
            bus,
            poll_interval_seconds=settings.EVENT_WORKER_POLL_SECONDS,
            on_tick=morning_brief_service.check_and_generate_scheduled,
        )
        worker_task = asyncio.create_task(worker.run_forever(shutdown_event))
        logger.info("klaros_in_process_event_worker_started")

    try:
        yield
    finally:
        if worker_task is not None:
            shutdown_event.set()
            await worker_task


app = FastAPI(
    title="Klaros AI API",
    version="0.1.0",
    description="AI operating system for one-person companies and small service businesses.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness only: this process is up and able to handle a request. Says
    nothing about whether its dependencies (Postgres, Redis) are reachable
    — that's /ready below. An orchestrator should restart the process on a
    failing /health, but only stop routing traffic to it (not restart) on a
    failing /ready — those are different failure responses, so this must
    stay a genuinely separate, dependency-free check."""
    return {"status": "ok", "env": settings.ENV}


@app.get("/ready")
async def ready(response: Response) -> dict[str, object]:
    """Readiness (Phase 12B): actually checks the two real dependencies this
    app cannot function without. Found missing during real-infrastructure
    verification — a previous /health-only setup would have reported
    healthy even with Postgres or Redis completely unreachable, which is
    exactly the state an orchestrator needs to know about to stop routing
    traffic here. Never raises — a failed dependency check is a normal,
    expected outcome reported via `checks` + a 503 status, not a 500. Every
    check is bounded by an explicit outer asyncio.wait_for: a TCP connect
    can fail fast, but a peer that accepts the connection (kernel backlog)
    and then never replies — a stopped-but-not-dead process, a stuck
    proxy — leaves an in-flight command with no libary-level timeout of its
    own. A readiness probe that can hang forever is worse than one that
    reports unreachable; verified by SIGSTOPing a real redis-server and
    observing this endpoint hang indefinitely before this fix was added."""
    from sqlalchemy import text

    from app.db.session import async_session_maker

    checks: dict[str, str] = {}
    CHECK_TIMEOUT_SECONDS = 3.0

    async def _check_database() -> None:
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(_check_database(), timeout=CHECK_TIMEOUT_SECONDS)
        checks["database"] = "ok"
    except TimeoutError:
        checks["database"] = f"unreachable: no response within {CHECK_TIMEOUT_SECONDS}s"
    except Exception as exc:  # noqa: BLE001 — reported as a check result, not an unhandled error
        checks["database"] = f"unreachable: {exc}"

    if settings.EVENT_TRANSPORT == "memory":
        # The in-process dev/test fallback has no external Redis to check —
        # honestly report what's actually being used, never claim a Redis
        # check that didn't happen.
        checks["redis"] = "not_applicable (EVENT_TRANSPORT=memory)"
    else:

        async def _check_redis() -> None:
            import redis.asyncio as redis

            client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=3)
            try:
                await client.ping()
            finally:
                await client.aclose()

        try:
            await asyncio.wait_for(_check_redis(), timeout=CHECK_TIMEOUT_SECONDS)
            checks["redis"] = "ok"
        except TimeoutError:
            checks["redis"] = f"unreachable: no response within {CHECK_TIMEOUT_SECONDS}s"
        except Exception as exc:  # noqa: BLE001 — reported as a check result, not an unhandled error
            checks["redis"] = f"unreachable: {exc}"

    all_ok = all(v == "ok" or v.startswith("not_applicable") for v in checks.values())
    response.status_code = 200 if all_ok else 503
    return {"status": "ready" if all_ok else "not_ready", "checks": checks}
