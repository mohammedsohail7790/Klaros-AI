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

    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    OBJECT_STORAGE_ENDPOINT: str | None = None
    OBJECT_STORAGE_BUCKET: str = "klaros-documents"
    OBJECT_STORAGE_ACCESS_KEY: str | None = None
    OBJECT_STORAGE_SECRET_KEY: str | None = None

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
    SENDGRID_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
