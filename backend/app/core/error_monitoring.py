"""Production error monitoring — a single, thin abstraction over Sentry,
matching this project's established "real provider if configured, honest
structlog-only fallback otherwise, never fake" pattern already used for
every other optional integration (Deepgram/ElevenLabs in
app/services/speech_provider.py, the platform-level adapters in
app/integrations/adapters.py).

`capture_exception()` is the ONE function every failure path in this
codebase should call to report an unexpected error — never a second,
parallel monitoring mechanism. It never raises, never blocks or changes
the caller's own error handling, and never sends anything beyond what
`app.tools.redact.redact_input()` already considers safe — the exact
same redaction boundary the AuditLog already uses, not a second one.
Always logs via structlog regardless of whether Sentry is configured, so
nothing is ever silently lost even in the fallback case.
"""

from __future__ import annotations

import structlog

from app.core.config import get_settings
from app.tools.redact import redact_input

logger = structlog.get_logger(__name__)

_initialized = False


def init_error_monitoring() -> bool:
    """Called once at app startup (see app/main.py's lifespan). Returns
    True if a real Sentry client was initialized, False if running in
    the honest structlog-only fallback (no SENTRY_DSN configured) — this
    return value is itself never fabricated, so a caller (or a test) can
    tell the two states apart for real."""
    global _initialized
    settings = get_settings()
    if not settings.SENTRY_DSN:
        logger.info(
            "error_monitoring_not_configured",
            detail="SENTRY_DSN unset — structlog-only fallback active, every capture_exception() call still logs",
        )
        _initialized = False
        return False
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENV,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            # This app has its own redaction boundary (redact_input) and
            # decides explicitly what context to attach per call — never
            # let the SDK itself auto-attach request bodies/headers/user
            # data that could bypass that boundary.
            send_default_pii=False,
        )
        _initialized = True
        logger.info("error_monitoring_initialized", provider="sentry", environment=settings.ENV)
        return True
    except Exception as exc:  # noqa: BLE001 — monitoring setup must never crash the app
        logger.error("error_monitoring_init_failed", error=str(exc))
        _initialized = False
        return False


def is_configured() -> bool:
    return _initialized


def capture_exception(exc: Exception, *, component: str, context: dict | None = None) -> None:
    """The one call site every failure path uses. `context` is redacted
    through the same `redact_input()` boundary the AuditLog uses before
    it ever reaches a log line or Sentry — never a raw payload, never a
    credential/token/secret (matched by key name, same markers
    AuditLog's own redaction already uses)."""
    safe_context = redact_input(context or {})
    logger.error(
        "unhandled_exception", component=component, error=str(exc), error_type=type(exc).__name__,
        **safe_context,
    )
    if not _initialized:
        return
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            scope.set_tag("component", component)
            for key, value in safe_context.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    scope.set_tag(key, str(value))
                else:
                    scope.set_context(key, {"value": str(value)})
            sentry_sdk.capture_exception(exc)
    except Exception as monitoring_exc:  # noqa: BLE001 — reporting a failure must never cause a second, unhandled failure
        logger.error("error_monitoring_capture_failed", error=str(monitoring_exc))


def capture_message(message: str, *, level: str = "error", component: str, context: dict | None = None) -> None:
    """For a known, classified failure that isn't a raised Python
    exception — a provider call that returned success=False, a webhook
    marked FAILED, an automation execution that ended FAILED. Same
    redaction boundary, same always-logs-regardless-of-Sentry-config
    honesty as `capture_exception`."""
    safe_context = redact_input(context or {})
    log_fn = getattr(logger, level, logger.error)
    log_fn("classified_failure", component=component, message=message, **safe_context)
    if not _initialized:
        return
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            scope.set_tag("component", component)
            for key, value in safe_context.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    scope.set_tag(key, str(value))
                else:
                    scope.set_context(key, {"value": str(value)})
            sentry_sdk.capture_message(message, level=level)
    except Exception as monitoring_exc:  # noqa: BLE001
        logger.error("error_monitoring_capture_failed", error=str(monitoring_exc))
