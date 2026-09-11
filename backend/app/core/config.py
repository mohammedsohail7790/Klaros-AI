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
    # A customer review is only eligible to become marketing content at
    # this rating or above (out of 5) — configurable per tenant-agnostic
    # deployment default; the actual gate also requires explicit consent
    # regardless of rating (see ContentService.create_content_from_feedback).
    REVIEW_MARKETING_MIN_RATING: int = 4
    # A sent-but-undecided quote is flagged for owner follow-up once it has
    # been sitting this many days with no customer response — see
    # InsightService.commercial_pipeline_snapshot.
    STALE_QUOTE_FOLLOWUP_DAYS: int = 3

    # Fixed-window rate limiting for public/edge endpoints — see
    # app/core/rate_limit.py. "memory" (default) is correct only within a
    # single process; set "redis" explicitly to share limits across every
    # backend replica (never auto-detected — a misconfigured deployment
    # should fail loudly, not silently narrow its own limit).
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_BACKEND: str = "memory"
    RATE_LIMIT_PUBLIC_LEAD_PER_MINUTE: int = 10
    RATE_LIMIT_PUBLIC_QUOTE_PER_MINUTE: int = 30
    RATE_LIMIT_PUBLIC_CONTRACT_PER_MINUTE: int = 30
    RATE_LIMIT_AUTH_PER_MINUTE: int = 10
    RATE_LIMIT_WEBHOOK_PER_MINUTE: int = 120

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
    # Phase 13: where the backend redirects the browser back to after a
    # provider OAuth callback completes (QuickBooks, ...) — distinct from
    # CORS_ORIGINS (which is about which origins may call the API, not
    # where to send a browser redirect).
    FRONTEND_BASE_URL: str = "http://localhost:3000"

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
    # Phase 13: real QuickBooks Online OAuth2 + invoice-sync client
    # (app/integrations/quickbooks_client.py). REDIRECT_URI must exactly
    # match the URI registered in the Intuit developer app (no default —
    # a wrong redirect_uri is rejected by Intuit's own OAuth server, so
    # there is no safe guess to fall back to). ENVIRONMENT selects
    # Intuit's sandbox vs production API base URL.
    QUICKBOOKS_REDIRECT_URI: str | None = None
    QUICKBOOKS_ENVIRONMENT: str = "sandbox"
    QUICKBOOKS_TIMEOUT_SECONDS: float = 20.0
    QUICKBOOKS_MAX_RETRIES: int = 3
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
    # Phase 14: real Google Calendar OAuth2 + Calendar API v3 client
    # (app/integrations/google_calendar_client.py). A separate Google
    # Cloud OAuth client from GOOGLE_ADS_*/GOOGLE_BUSINESS_* above — same
    # per-integration-has-its-own-app convention already used throughout
    # this file, not a shared "Google" credential. REDIRECT_URI must
    # exactly match the URI registered in that Google Cloud OAuth client.
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GOOGLE_REDIRECT_URI: str | None = None
    GOOGLE_CALENDAR_TIMEOUT_SECONDS: float = 20.0
    GOOGLE_CALENDAR_MAX_RETRIES: int = 3
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
    # anthropic > openai > groq > deepseek > nvidia > google, else falls back
    # to deterministic. "deterministic" forces the deterministic path even if
    # keys are present (useful for ops/testing). Any of "anthropic" / "openai"
    # / "groq" / "deepseek" / "nvidia" / "google" forces that provider —
    # get_ai_provider() still falls back to deterministic if the matching key
    # is missing, it never fabricates a connection.
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

    # Additional AI providers — each exposes an OpenAI-compatible chat
    # completions endpoint, so they reuse _OpenAICompatibleProvider in
    # app/services/ai_provider.py rather than a bespoke client per
    # provider. Same "real provider if configured, honest fallback
    # otherwise" rule as every other provider in this file.
    GROQ_API_KEY: str | None = None
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    DEEPSEEK_API_KEY: str | None = None
    DEEPSEEK_MODEL: str = "deepseek-chat"
    NVIDIA_API_KEY: str | None = None
    # Confirmed against the real /v1/models catalog (Phase: multi-provider
    # AI) — "meta/llama3-70b-instruct" doesn't exist under that slug.
    NVIDIA_MODEL: str = "nvidia/llama-3.1-nemotron-70b-instruct"
    GOOGLE_API_KEY: str | None = None
    # Confirmed via a real call — "gemini-2.0-flash" is retired; Google's
    # own 404 body names the current replacement.
    GOOGLE_MODEL: str = "gemini-3.6-flash"

    # Knowledge-layer embeddings (Phase 3 RAG) — see
    # app/services/embedding_provider.py. "auto" (default) uses real
    # OpenAI embeddings only if OPENAI_API_KEY is set, else honestly
    # reports NOT_CONFIGURED (never silently substitutes a fake vector).
    # "deterministic" is a TEST-ONLY bag-of-words double — tests opt into
    # it explicitly; it is never the default for real tenant usage.
    EMBEDDING_PROVIDER: str = "auto"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    KNOWLEDGE_CHUNK_SIZE_CHARS: int = 800
    KNOWLEDGE_CHUNK_OVERLAP_CHARS: int = 150
    KNOWLEDGE_SEARCH_TOP_K: int = 5
    KNOWLEDGE_SEARCH_SCORE_THRESHOLD: float = 0.15

    # AI Voice Receptionist (Phase 4) — see app/services/speech_provider.py.
    # "auto" (default) uses the real provider only if its key is set, else
    # honestly reports NOT_CONFIGURED. "deterministic" is TEST-ONLY.
    DEEPGRAM_API_KEY: str | None = None
    ELEVENLABS_API_KEY: str | None = None
    STT_PROVIDER: str = "auto"
    TTS_PROVIDER: str = "auto"
    SPEECH_TIMEOUT_SECONDS: float = 20.0
    VOICE_MAX_TURNS_PER_CALL: int = 20
    VOICE_MEDIA_STREAM_IDLE_TIMEOUT_SECONDS: float = 30.0
    # Phase 6: real-time turn manager silence policy — counted in "silence
    # ticks" (one per ~200ms Twilio media frame with energy below
    # VOICE_SILENCE_RMS_THRESHOLD), not wall-clock seconds directly, so
    # the policy is deterministic and testable without real timers.
    VOICE_SILENCE_RMS_THRESHOLD: float = 150.0
    VOICE_SILENCE_PROMPT_AFTER_TICKS: int = 15  # ~3s of silence
    VOICE_SILENCE_HANGUP_AFTER_PROMPTS: int = 2
    # Shorter than the "are you still there?" threshold above — once the
    # caller has said something and then gone quiet for this many ticks,
    # treat it as end-of-utterance and process what they said so far.
    VOICE_UTTERANCE_END_SILENCE_TICKS: int = 8

    # Phase 32: selects which voice engine handles a call. "cascaded"
    # (default, unchanged behavior) is the existing Deepgram/ElevenLabs-
    # capable StreamingSTT/TTSProvider + VoiceConversationService.handle_turn
    # pipeline (app/api/v1/voice_stream.py, app/services/
    # voice_conversation_service.py) — untouched by this phase.
    # "openai_realtime" opts a tenant/deployment into the new
    # app/services/openai_realtime_voice_service.py bridge: OpenAI's
    # Realtime API for continuous audio + native barge-in/turn detection +
    # native function calling, with every real action still funneled
    # through the same governed ToolRegistry/ActionPolicy/AuditLog
    # pipeline. Never "auto" — this is a deliberate, explicit opt-in, not
    # a silent behavior change for existing deployments.
    VOICE_AI_ENGINE: str = "cascaded"
    # Configurable, never hardcoded — OpenAI's realtime model naming has
    # changed over time and this environment should not assume a specific
    # one is available without being told.
    OPENAI_REALTIME_MODEL: str = "gpt-4o-realtime-preview"

    # Production observability — see app/core/error_monitoring.py. "auto"
    # (default) initializes real Sentry reporting only if SENTRY_DSN is
    # set, else every capture_exception() call is a real, honest
    # structlog-only fallback (never a silent no-op, never fabricated
    # delivery). Matches this project's established "real provider if
    # configured, honest fallback otherwise, never fake" pattern used for
    # every other optional integration.
    SENTRY_DSN: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
