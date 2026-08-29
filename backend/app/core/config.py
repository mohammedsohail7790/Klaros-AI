from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENV: str = "development"
    DATABASE_URL: str = "postgresql+asyncpg://klaros:klaros@localhost:5432/klaros"
    REDIS_URL: str = "redis://localhost:6379/0"
    # "redis" (default, production) or "memory" (dev/test fallback when no Redis is
    # reachable — a real in-process transport, not a mock; see app/events/transport.py)
    EVENT_TRANSPORT: str = "redis"
    # How often the Klaros Event Worker (app/events/worker.py) polls for
    # pending events — distinct from Temporal, which has its own scheduling.
    EVENT_WORKER_POLL_SECONDS: float = 1.0

    JWT_SECRET: str = "change-me-in-production"
    # Phase 12D: encrypts tenant-owned integration credentials (OAuth
    # tokens, per-tenant API keys) at rest — see
    # app/integrations/credential_store.py. Empty by default in dev; the
    # store falls back to an insecure, publicly-known key when unset, so
    # this MUST be set to a real random value before any real tenant
    # credential is ever stored in production.
    INTEGRATION_CREDENTIAL_ENCRYPTION_KEY: str | None = None
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    OBJECT_STORAGE_ENDPOINT: str | None = None
    OBJECT_STORAGE_BUCKET: str = "klaros-documents"
    OBJECT_STORAGE_ACCESS_KEY: str | None = None
    OBJECT_STORAGE_SECRET_KEY: str | None = None
    # Used only when OBJECT_STORAGE_ENDPOINT is unset — see app/storage/local_adapter.py
    STORAGE_LOCAL_ROOT: str = "./storage_data"

    TEMPORAL_HOST: str = "localhost:7233"
    TEMPORAL_NAMESPACE: str = "default"
    TEMPORAL_TASK_QUEUE: str = "klaros-tasks"

    # Third-party integration credentials (section 11). Blank by default —
    # each adapter's get_status() reports NOT_CONNECTED until these are set.
    QUICKBOOKS_CLIENT_ID: str | None = None
    QUICKBOOKS_CLIENT_SECRET: str | None = None
    XERO_CLIENT_ID: str | None = None
    XERO_CLIENT_SECRET: str | None = None
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None
    # Phase 12F: previously hardcoded in app/integrations/stripe_client.py.
    STRIPE_TIMEOUT_SECONDS: float = 20.0
    STRIPE_MAX_RETRIES: int = 3
    GOOGLE_ADS_CLIENT_ID: str | None = None
    GOOGLE_ADS_CLIENT_SECRET: str | None = None
    GOOGLE_ADS_DEVELOPER_TOKEN: str | None = None
    META_ADS_APP_ID: str | None = None
    META_ADS_APP_SECRET: str | None = None
    GOOGLE_BUSINESS_CLIENT_ID: str | None = None
    GOOGLE_BUSINESS_CLIENT_SECRET: str | None = None
    SERVICETITAN_CLIENT_ID: str | None = None
    SERVICETITAN_CLIENT_SECRET: str | None = None
    JOBBER_CLIENT_ID: str | None = None
    JOBBER_CLIENT_SECRET: str | None = None
    TWILIO_ACCOUNT_SID: str | None = None
    TWILIO_AUTH_TOKEN: str | None = None
    # Real Twilio sender number (E.164, e.g. "+15551234567") — required to
    # actually send, distinct from the account credentials above.
    TWILIO_FROM_NUMBER: str | None = None
    SENDGRID_API_KEY: str | None = None
    # Verified SendGrid sender identity — SendGrid rejects sends from an
    # unverified address, so this must be a real, verified sender.
    SENDGRID_FROM_EMAIL: str | None = None
    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    # "auto" (default) picks a real provider only if its key is set, preferring
    # Anthropic, else falls back to deterministic. "deterministic" forces the
    # deterministic path even if keys are present (useful for ops/testing).
    # "anthropic" / "openai" force that provider — get_ai_provider() still
    # falls back to deterministic if the matching key is missing, it never
    # fabricates a connection.
    AI_PROVIDER: str = "auto"
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"
    OPENAI_MODEL: str = "gpt-4o-mini"
    # Phase 12E: previously hardcoded in app/services/ai_provider.py —
    # made configurable so timeout/retry/output-size behavior is
    # observable and testable per environment, not a silent constant.
    OPENAI_TIMEOUT_SECONDS: float = 20.0
    OPENAI_MAX_RETRIES: int = 2
    OPENAI_MAX_OUTPUT_TOKENS: int = 1024
    ANTHROPIC_TIMEOUT_SECONDS: float = 20.0
    ANTHROPIC_MAX_RETRIES: int = 2
    ANTHROPIC_MAX_OUTPUT_TOKENS: int = 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
