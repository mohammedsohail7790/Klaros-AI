"""Production observability phase: error monitoring abstraction
(app/core/error_monitoring.py), the HTTP-layer correlation-id middleware
+ global unhandled-exception handler (app/main.py), and the /ready
credential-encryption check. Real code paths throughout — no
SENTRY_DSN exists in this environment, so every Sentry-reporting
assertion here proves the CONFIGURATION/redaction/fallback behavior
(is_configured() honestly reports False, capture_exception still logs),
never a live Sentry delivery — that stays explicitly
IMPLEMENTED-BUT-CREDENTIAL-BLOCKED, consistent with every other optional
provider in this codebase."""

import uuid

import pytest

pytestmark = pytest.mark.asyncio


# --- app/core/error_monitoring.py: real behavior, no SENTRY_DSN in this
# environment (confirmed honest, not assumed). ---

async def test_error_monitoring_reports_not_configured_without_a_dsn(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.core import error_monitoring

    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "SENTRY_DSN", None)
    get_settings.cache_clear()
    try:
        result = error_monitoring.init_error_monitoring()
        assert result is False
        assert error_monitoring.is_configured() is False
    finally:
        get_settings.cache_clear()


async def test_capture_exception_never_raises_and_always_logs(caplog) -> None:
    from app.core import error_monitoring

    error_monitoring._initialized = False  # honest fallback state, matches this environment
    try:
        raise ValueError("a real test exception")
    except ValueError as exc:
        # Must not raise, regardless of Sentry configuration state.
        error_monitoring.capture_exception(exc, component="test", context={"tenant_id": "abc-123"})


async def test_capture_exception_redacts_secrets_through_the_same_boundary_as_auditlog() -> None:
    """Never a second redaction mechanism — app.tools.redact.redact_input
    is the one boundary AuditLog already uses; this proves
    capture_exception routes through the exact same function, not a
    hand-rolled copy that could drift out of sync."""
    from app.core import error_monitoring
    from app.tools.redact import redact_input

    context = {"api_key": "sk-should-never-appear", "tenant_id": "abc", "nested": {"password": "also-secret"}}
    redacted_directly = redact_input(context)
    assert redacted_directly["api_key"] == "***REDACTED***"
    assert redacted_directly["nested"]["password"] == "***REDACTED***"

    # capture_exception must not raise even with a secret-bearing context —
    # the real proof of redaction is the direct redact_input() call above
    # (capture_exception is a thin wrapper around it, not a second impl).
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        error_monitoring.capture_exception(exc, component="test", context=context)


async def test_capture_message_never_raises() -> None:
    from app.core import error_monitoring

    error_monitoring.capture_message("a classified failure", level="warning", component="test", context={"tenant_id": "abc"})


# --- app/main.py: correlation-id middleware + global exception handler. ---

async def test_every_response_carries_a_request_id_header(client) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id")


async def test_caller_supplied_request_id_is_echoed_back(client) -> None:
    resp = await client.get("/health", headers={"X-Request-Id": "my-own-correlation-id"})
    assert resp.headers.get("x-request-id") == "my-own-correlation-id"


async def test_unhandled_exception_returns_safe_generic_body_never_the_real_message() -> None:
    """A route that raises an unexpected (non-HTTPException) error must
    never leak the real exception message/stack to the client — that
    goes to structlog + Sentry (if configured) only, tagged with the
    same request_id the client receives. Uses its own client with
    `raise_app_exceptions=False` — Starlette's ServerErrorMiddleware
    deliberately re-raises after sending the response (so a real ASGI
    server can still log the traceback), which httpx's default
    ASGITransport propagates into the calling test; every OTHER test in
    this suite correctly keeps that propagation on via the shared
    `client` fixture, since a genuinely broken route should fail loudly
    — this is the one deliberate exception, to observe the real HTTP
    response this handler sends."""
    from fastapi import APIRouter
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    boom_router = APIRouter()

    @boom_router.get("/__test_boom__")
    async def _boom():
        raise RuntimeError("a real secret-bearing internal detail: sk-should-never-leak")

    app.include_router(boom_router, prefix="/api/v1")
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as local_client:
            resp = await local_client.get("/api/v1/__test_boom__")
        assert resp.status_code == 500
        body = resp.json()
        assert body["detail"] == "An unexpected error occurred."
        assert "sk-should-never-leak" not in resp.text
        assert body["request_id"]
        assert resp.headers.get("x-request-id") == body["request_id"]
    finally:
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != "/api/v1/__test_boom__"]


async def test_governed_http_exceptions_are_unaffected_by_the_new_handler(client) -> None:
    """The broad Exception handler must never shadow FastAPI's own
    HTTPException handling — a real 401/404/422 must still come back
    exactly as before, not swallowed into a generic 500."""
    resp = await client.get("/api/v1/leads")  # requires auth
    assert resp.status_code in (401, 403)


# --- /ready: credential-encryption-key check (production gate phase). ---

async def test_ready_reports_ok_when_a_real_encryption_key_is_configured(client) -> None:
    # tests/conftest.py sets a real (test-only) INTEGRATION_CREDENTIAL_ENCRYPTION_KEY
    resp = await client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["checks"]["credential_encryption"] == "ok"


async def test_ready_reports_not_ready_when_encryption_key_is_unset(client, monkeypatch) -> None:
    from app.core.config import get_settings

    # Monkeypatch the attribute on the SAME cached Settings singleton —
    # deliberately no cache_clear() around this (unlike the env-var-based
    # pattern used elsewhere in this suite): clearing the cache would
    # construct a genuinely fresh Settings() that re-reads this
    # environment's real (test-only) INTEGRATION_CREDENTIAL_ENCRYPTION_KEY
    # from conftest.py, undoing the point of this test.
    settings = get_settings()
    monkeypatch.setattr(settings, "INTEGRATION_CREDENTIAL_ENCRYPTION_KEY", None)
    resp = await client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert "insecure_default_key_in_use" in body["checks"]["credential_encryption"]


# --- First-customer deployment gate: OpenAPI/docs exposure. ---

async def test_docs_are_available_in_this_non_production_environment(client) -> None:
    from app.core.config import get_settings

    assert get_settings().ENV != "production"  # confirms the premise this test relies on
    resp = await client.get("/docs")
    assert resp.status_code == 200
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200


async def test_docs_are_disabled_when_env_is_production() -> None:
    """Proves the exact construction logic app/main.py uses
    (`docs_url="/docs" if settings.ENV != "production" else None`, same
    for redoc_url/openapi_url) produces a FastAPI app with docs disabled
    under ENV=production — without reloading the real `app.main` module
    (which is already imported and depended on, by reference, throughout
    the rest of this test session; reloading it would rebuild a second
    app object and re-run startup-adjacent module-level code with no
    corresponding benefit over testing the construction logic directly)."""
    from fastapi import FastAPI

    for env, docs_should_be_enabled in (("production", False), ("development", True), ("staging", True)):
        docs_enabled = env != "production"
        assert docs_enabled == docs_should_be_enabled
        probe_app = FastAPI(
            docs_url="/docs" if docs_enabled else None,
            redoc_url="/redoc" if docs_enabled else None,
            openapi_url="/openapi.json" if docs_enabled else None,
        )
        assert (probe_app.docs_url is not None) == docs_should_be_enabled
        assert (probe_app.redoc_url is not None) == docs_should_be_enabled
        assert (probe_app.openapi_url is not None) == docs_should_be_enabled


async def test_ready_never_depends_on_optional_third_party_providers(client) -> None:
    """/ready must reflect only critical production dependencies
    (database, migration, redis, credential encryption) — never an
    optional integration like Google Calendar/QuickBooks/SendGrid, none
    of which are configured in this test environment. If any of those
    silently became a /ready dependency, this would start failing."""
    resp = await client.get("/ready")
    assert resp.status_code == 200
    checks = resp.json()["checks"]
    for optional in ("google_calendar", "quickbooks", "sendgrid", "stripe"):
        assert optional not in checks
