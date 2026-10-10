import asyncio
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from app.services.consent_gate import ConsentRequiredError
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.error_monitoring import capture_exception, init_error_monitoring
from app.core.logging import configure_logging, get_logger

settings = get_settings()
configure_logging()
logger = get_logger(__name__)


_INSECURE_DEFAULT_JWT_SECRET = "change-me-in-production"


_REACHABLE_ENVIRONMENTS = ("production", "staging")


def _assert_production_secrets_are_real() -> None:
    """Refuse to boot with the publicly-visible default JWT secret in any
    environment that is actually network-reachable by someone other than
    the developer running it locally — every valid token is signed with
    it, so anyone reading this open-source codebase could forge a token
    for any tenant/user/role against a deployment that forgot to override
    it. This check has to live in code, not just documentation, since a
    missed .env value is exactly the kind of mistake documentation
    doesn't catch.

    Phase 0 (KLAROS_PHASE_0_IMPLEMENTATION_PLAN.md §0.5 — staging
    foundation): extended from "production only" to also cover
    ENV=staging. A staging environment that reuses the insecure default
    secret is exactly as exploitable as production reusing it — staging
    just didn't exist as a concept in this codebase before this Phase 0
    pass. This does NOT change docs_url/redoc_url exposure below, which
    stays intentionally unchanged for staging (an existing, deliberate
    choice predating this change — see its own comment)."""
    if settings.ENV in _REACHABLE_ENVIRONMENTS and settings.JWT_SECRET == _INSECURE_DEFAULT_JWT_SECRET:
        raise RuntimeError(
            f"Refusing to start: ENV={settings.ENV} but JWT_SECRET is still the insecure default "
            "('change-me-in-production'). Set a real, random JWT_SECRET before deploying."
        )
    # Phase 12D: same reasoning as JWT_SECRET above, for tenant-owned
    # integration credentials (OAuth tokens, per-tenant API keys) — an
    # unset key falls back to a publicly-known default in
    # app/integrations/credential_store.py, which would let anyone reading
    # this codebase decrypt any tenant's stored credential in a deployment
    # that forgot to set a real one.
    if settings.ENV in _REACHABLE_ENVIRONMENTS and not settings.INTEGRATION_CREDENTIAL_ENCRYPTION_KEY:
        raise RuntimeError(
            f"Refusing to start: ENV={settings.ENV} but INTEGRATION_CREDENTIAL_ENCRYPTION_KEY is unset "
            "(would fall back to a publicly-known default key). Set a real, random value before deploying."
        )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    _assert_production_secrets_are_real()
    init_error_monitoring()
    logger.info("klaros_api_startup", env=settings.ENV)

    # Wire event-bus subscriptions eagerly at startup rather than lazily on
    # first request to /api/v1/events/* (the previous behavior — the bus
    # singleton had zero subscribers, and therefore silently dropped every
    # published event, until something happened to hit that one dependency
    # first). See PROJECT_STATUS.md Phase 8 for how this was found.
    from app.api.tool_deps import get_tool_registry, get_wired_event_bus

    bus = get_wired_event_bus()
    tool_registry = get_tool_registry()

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
        from app.api.tool_deps import get_automation_service, get_morning_brief_service
        from app.api.tool_deps_agents import get_agent_recovery_service, get_agent_trigger_service
        from app.events.worker import EventWorker, combine_on_tick

        morning_brief_service = get_morning_brief_service(tool_registry, bus)
        automation_service = get_automation_service(tool_registry)
        # Phase 6 (Agent Runtime Reliability): same piggyback-on-this-tick
        # mechanism as the Automation Engine/Morning Brief above — see
        # agent_recovery_service.py/agent_trigger_service.py.
        agent_recovery_service = get_agent_recovery_service(tool_registry)
        agent_trigger_service = get_agent_trigger_service(tool_registry)
        worker = EventWorker(
            bus,
            poll_interval_seconds=settings.EVENT_WORKER_POLL_SECONDS,
            on_tick=combine_on_tick(
                morning_brief_service.check_and_generate_scheduled,
                automation_service.check_and_dispatch_scheduled,
                agent_trigger_service.check_and_dispatch_scheduled,
                agent_recovery_service.sweep_once,
            ),
        )
        worker_task = asyncio.create_task(worker.run_forever(shutdown_event))
        logger.info("klaros_in_process_event_worker_started")

    try:
        yield
    finally:
        if worker_task is not None:
            shutdown_event.set()
            await worker_task


# First-customer deployment gate: FastAPI's interactive docs
# (/docs, /redoc) and the raw OpenAPI schema (/openapi.json) are public,
# unauthenticated by default — fine for development, unnecessary surface
# area for a real production deployment (it doesn't expose data, but it
# does hand an unauthenticated caller the exact shape of every endpoint).
# Disabled only when ENV=="production"; every other environment
# (development, staging, test) keeps them, unchanged.
_docs_enabled = settings.ENV != "production"

app = FastAPI(
    title="Klaros AI API",
    version="0.1.0",
    description="AI operating system for one-person companies and small service businesses.",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _request_id_middleware(request: Request, call_next):
    """Production observability: every request gets a real correlation
    id — reused from an inbound `X-Request-Id` header when a caller (or
    a reverse proxy/load balancer) already set one, otherwise generated
    here. Bound into structlog's contextvars (already wired into every
    log line via `merge_contextvars` in app/core/logging.py) for the
    life of this request, so every log line this request produces —
    including from deep inside a tool/service call — carries the same
    id without every function needing to thread it through explicitly.
    Never a second correlation-id concept: EventBus/AIInvocationLog
    already have their own `correlation_id`, unrelated to and untouched
    by this HTTP-layer one."""
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    # Also stashed on `request.state` (not contextvar-based) — the
    # canonical, unambiguously-scoped-to-this-request way to read it back
    # in the exception handler below, since Starlette's BaseHTTPMiddleware
    # (what `@app.middleware("http")` uses) can run `call_next` in a way
    # that doesn't reliably preserve contextvars across that boundary.
    request.state.request_id = request_id
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.unbind_contextvars("request_id")
    response.headers["X-Request-Id"] = request_id
    return response


@app.exception_handler(ConsentRequiredError)
async def _consent_required_handler(request: Request, exc: ConsentRequiredError) -> JSONResponse:
    """Any path that refuses to keep personal data for want of consent answers 422 with the reason and the scopes, never a generic 500."""
    return JSONResponse(status_code=422, content={"detail": {"error": exc.reason, "missing_scopes": exc.missing}})


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Production observability: before this handler existed, an
    unhandled exception anywhere in a route was caught only by FastAPI's
    own default handler — a generic 500 with no structured log line, no
    tenant/request context, and no error-monitoring report. Every other
    failure surface in this codebase (webhooks, AI calls, automation
    executions) already logs and now reports through
    app.core.error_monitoring.capture_exception; this is the one
    remaining gap it closes for the HTTP layer itself. Never leaks the
    real exception message to the client — only the safe, generic body
    below; the real detail goes to structlog + Sentry (if configured),
    tagged with the same request_id the client already has via the
    `X-Request-Id` response header."""
    request_id = getattr(request.state, "request_id", None)
    capture_exception(
        exc, component="http",
        context={"path": request.url.path, "method": request.method, "request_id": request_id},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred.", "request_id": request_id},
        # Set directly here, not left to the request-id middleware's own
        # post-call_next line — an exception that reaches this handler
        # propagated PAST that middleware's `call_next()` call (it never
        # returned normally), so that line never runs for this response.
        headers={"X-Request-Id": request_id} if request_id else None,
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
    from pathlib import Path

    from sqlalchemy import text

    from app.db.session import async_session_maker

    checks: dict[str, str] = {}
    CHECK_TIMEOUT_SECONDS = 3.0

    # Phase 17B-2R classification: both sessions below (database + migration
    # head checks) are GLOBAL/system-level — they touch no tenant-owned row
    # (`SELECT 1`, `alembic_version`), so no tenant context is meaningful or
    # set here.
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

    async def _check_migration_head() -> str:
        """Phase 27: a connectable database is not the same as a USABLE
        one — `SELECT 1` above succeeds against a completely empty schema
        (confirmed by direct reproduction: pointing at a fresh, unmigrated
        Postgres schema, this endpoint previously reported `database: ok`
        with a 200, even though every real table this app depends on was
        missing). Compares the DB's own `alembic_version` row against the
        code's expected head (from the Alembic script directory) so a
        forgotten/failed `alembic upgrade` shows up here as `not_ready`
        instead of silently passing traffic to an instance that will fail
        on its first real query. The test suite's own DB (SQLite or real
        Postgres) is built directly from `Base.metadata` rather than real
        Alembic migrations, but `tests/conftest.py`'s `_reset_database`
        fixture stamps `alembic_version` at the current head itself — that
        schema genuinely does reflect the current code's expected head, so
        no test-only special case is needed here."""
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
        config = Config(str(alembic_ini))
        expected_head = ScriptDirectory.from_config(config).get_current_head()

        async with async_session_maker() as session:
            result = await session.execute(text("SELECT version_num FROM alembic_version"))
            row = result.first()
        current = row[0] if row else None
        if current != expected_head:
            return f"schema out of date: db is at {current!r}, code expects {expected_head!r}"
        return "ok"

    try:
        migration_status = await asyncio.wait_for(_check_migration_head(), timeout=CHECK_TIMEOUT_SECONDS)
        checks["migration"] = migration_status
    except TimeoutError:
        checks["migration"] = f"unreachable: no response within {CHECK_TIMEOUT_SECONDS}s"
    except Exception as exc:  # noqa: BLE001 — reported as a check result, not an unhandled error
        checks["migration"] = f"check failed: {exc}"

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

    # First-customer production gate: `credential_store.is_using_insecure_
    # default_key()` has existed since Phase 12D but was never actually
    # consulted anywhere — a real deployment that forgot to set
    # INTEGRATION_CREDENTIAL_ENCRYPTION_KEY would silently encrypt every
    # tenant's OAuth tokens/API keys (Stripe, QuickBooks, Google Calendar)
    # with a hardcoded, publicly-visible-in-source default key, with zero
    # warning anywhere. Wired in here — the same place a forgotten
    # migration already surfaces — so this is caught before traffic is
    # routed to an insecure instance, never silently.
    from app.integrations.credential_store import is_using_insecure_default_key

    checks["credential_encryption"] = (
        "insecure_default_key_in_use — set INTEGRATION_CREDENTIAL_ENCRYPTION_KEY"
        if is_using_insecure_default_key() else "ok"
    )

    all_ok = all(v == "ok" or v.startswith("not_applicable") for v in checks.values())
    response.status_code = 200 if all_ok else 503
    return {"status": "ready" if all_ok else "not_ready", "checks": checks}
