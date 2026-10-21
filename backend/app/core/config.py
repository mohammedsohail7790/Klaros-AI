import os
import sys
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Credential-isolation guard (see the backend/.env incident documented in
# PHASE_0_POSTGRES_VERIFICATION.md §2): pydantic-settings' `env_file` is a
# fallback source used only for fields not already present in the real
# process environment — so under a normal `pytest` run it silently filled
# in any provider credential (OPENAI_API_KEY, STRIPE_SECRET_KEY,
# TWILIO_*, SENDGRID_API_KEY, ...) a developer's own gitignored
# `backend/.env` happened to contain, even though tests/conftest.py never
# asked for those values. That once caused real outbound calls to
# api.stripe.com / api.twilio.com during a test run using someone's local
# credentials. Detecting "are we running under pytest" this way (rather
# than requiring `ENV=test` to be set everywhere, which would also change
# app/main.py's production/staging-only guards) is reliable at both
# collection time and test-run time: pytest imports itself as a top-level
# module before it ever imports conftest.py or a test module, and (pytest
# 8+, this repo pins 9.0.3) also sets `PYTEST_VERSION` for the whole
# process as soon as it starts, so either check alone is sufficient and
# together they don't depend on pytest-version-specific behavior.
_RUNNING_UNDER_PYTEST = "pytest" in sys.modules or os.environ.get("PYTEST_VERSION") is not None


class Settings(BaseSettings):
    # Never auto-load the developer's real `backend/.env` while running
    # under pytest — tests must be fully deterministic and provider-
    # credential-free regardless of what a developer's own local `.env`
    # contains, and this must not require deleting or renaming that file.
    # `tests/conftest.py` is solely responsible for seeding every
    # test-safe env var pytest needs (see its `os.environ.setdefault`
    # calls); real deployments (dev/staging/production) are unaffected
    # and continue to load `.env` exactly as before.
    model_config = SettingsConfigDict(env_file=None if _RUNNING_UNDER_PYTEST else ".env", extra="ignore")

    ENV: str = "development"
    # The application's normal runtime connection. As of Phase 17B-1, this
    # is intended to point at a RESTRICTED application role (NOSUPERUSER,
    # NOBYPASSRLS, non-table-owning — see
    # backend/scripts/db/provision_app_role.py) rather than the schema-
    # owning bootstrap role, so that PostgreSQL RLS policies (currently
    # audit-mode/permissive — see PHASE_17A_RLS_ENFORCEMENT_READINESS_AUDIT.md)
    # have any real enforcement effect once a future phase makes them
    # enforcing. The default below still matches the pre-Phase-17B-1
    # bootstrap-role shape for backward compatibility with any environment
    # that hasn't been cut over yet.
    DATABASE_URL: str = "postgresql+asyncpg://klaros:klaros@localhost:5432/klaros"
    # The schema-owning / migration connection. Alembic (backend/alembic/
    # env.py) and backend/scripts/db/provision_app_role.py use this URL,
    # never DATABASE_URL, so that "run a migration" and "run the app" are
    # explicitly different privilege levels rather than the application
    # silently being able to fall back to owner/superuser access. When
    # unset (the pre-Phase-17B-1 default), every owner/migration operation
    # falls back to DATABASE_URL — i.e. today's existing single-role
    # behavior, unchanged, for any environment that hasn't provisioned a
    # separate restricted role yet.
    DATABASE_MIGRATION_URL: str | None = None
    # Phase 17B-4 (§37d/§39): the ONE narrow, read-only connection intended
    # to point at the restricted `klaros_discovery` role (see
    # backend/scripts/db/provision_discovery_role.py) — a role with
    # column-level SELECT on exactly the handful of tables the platform's
    # small set of legitimate cross-tenant discovery/resolution reads need
    # (§10/§11c), and ZERO DML grants anywhere, ever. As of this setting's
    # introduction, used by exactly one real call site:
    # `McpCredentialService._resolve_tenant_id_via_discovery` (MCP
    # credential authentication's token-hash-to-tenant resolution step,
    # which — like login/webhook signature verification — must run BEFORE
    # any tenant is known, so no ordinary tenant-scoped `klaros_app`
    # session can perform it at all under real RLS). Unset by default: an
    # environment that hasn't provisioned `klaros_discovery` yet (or
    # doesn't use the MCP surface) doesn't need this configured, and the
    # one call site that needs it fails closed (logs, returns "no
    # credential found") rather than raising, exactly like every other
    # unknown/malformed-token outcome in that same function.
    DISCOVERY_DATABASE_URL: str | None = None
    REDIS_URL: str = "redis://localhost:6379/0"
    # "redis" (default, production) or "memory" (dev/test fallback when no Redis is
    # reachable — a real in-process transport, not a mock; see app/events/transport.py)
    EVENT_TRANSPORT: str = "redis"
    # How often the Klaros Event Worker (app/events/worker.py) polls for
    # pending events — distinct from Temporal, which has its own scheduling.
    EVENT_WORKER_POLL_SECONDS: float = 1.0
    # Crash recovery (see app/events/bus.py). A handler run unfinished for this long is presumed dead and may be taken over by another delivery.
    EVENT_PROCESSING_LEASE_SECONDS: float = 300.0
    # A stream message delivered to a worker and not acked for this long belongs to a crashed worker and is reclaimed (Redis XAUTOCLAIM).
    EVENT_PENDING_RECLAIM_IDLE_MS: int = 60000
    EVENT_WORKER_CONSUMER_NAME: str = "worker-1"
    # Run the event worker inside the API process even though the transport is Redis. Only for a deployment that cannot afford a second service
    # (free-tier staging). Redis durability, pending-message takeover and the Postgres outbox relay all still apply; what is lost is process isolation:
    # the worker sleeps and restarts with the API. Production keeps a separate `python -m app.events.worker` service.
    EVENT_WORKER_IN_PROCESS: bool = False
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
    RATE_LIMIT_INBOUND_EMAIL_PER_MINUTE: int = 120  # the email provider's callbacks for replies, per source address
    RATE_LIMIT_PUBLIC_QUOTE_PER_MINUTE: int = 30
    RATE_LIMIT_PUBLIC_CONTRACT_PER_MINUTE: int = 30
    RATE_LIMIT_PUBLIC_INVITE_PER_MINUTE: int = 20
    # The dropshipping storefront (one business): browsing, order-status lookups, and the much stricter checkout/pay-again routes.
    RATE_LIMIT_PUBLIC_STORE_BROWSE_PER_MINUTE: int = 60
    RATE_LIMIT_PUBLIC_STORE_STATUS_PER_MINUTE: int = 30
    RATE_LIMIT_PUBLIC_STORE_CHECKOUT_PER_MINUTE: int = 10
    RATE_LIMIT_AUTH_PER_MINUTE: int = 10
    RATE_LIMIT_CONTACT_PER_MINUTE: int = 3
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
    # How a provider referral is transferred. Only "sandbox" exists: it records what would be sent and contacts no clinic. Anything else fails closed.
    MT_REFERRAL_TRANSPORT: str = "sandbox"
    # Phase 13: where the backend redirects the browser back to after a
    # provider OAuth callback completes (QuickBooks, ...) — distinct from
    # CORS_ORIGINS (which is about which origins may call the API, not
    # where to send a browser redirect).
    FRONTEND_BASE_URL: str = "http://localhost:3000"
    # The ONE business tenant whose dropshipping storefront is served publicly under /api/v1/public/store. Unset = the public store does not exist (404).
    # No other tenant can be served: the public routes take no tenant from the caller.
    DROPSHIPPING_STORE_TENANT_ID: str | None = None
    # Where the store sends a shopper back to after Stripe (the store's own address, for example https://www.yourcleanroom.com). Unset = FRONTEND_BASE_URL.
    # Kept separate because FRONTEND_BASE_URL is shared by every other feature of this backend (quote links, OAuth returns) and must not change for the store.
    DROPSHIPPING_STORE_URL: str | None = None
    # The store's owners get one e-mail a day (orders, revenue, what is waiting, what is sold out) from this hour (UTC) on; 13 is 9 am in New York in summer.
    DROPSHIPPING_DIGEST_HOUR_UTC: int = 13
    # The store's name in the e-mails it sends to shoppers (the storefront reads its own NEXT_PUBLIC_STORE_NAME).
    DROPSHIPPING_STORE_NAME: str = "Clean Room"
    # Stops the checkout route from creating unlimited unpaid orders; checked per store.
    DROPSHIPPING_MAX_UNPAID_ORDERS: int = 200
    # Where messages from the public contact form are emailed (they are always stored too). Unset = stored only.
    SUPPORT_INBOX_EMAIL: str | None = None

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
    # Klaros's OWN Stripe account for its SaaS subscription billing — a
    # deliberately separate credential from STRIPE_SECRET_KEY above, which
    # is only ever a per-tenant key for collecting THAT tenant's own
    # customer payments. Never mix the two.
    STRIPE_PLATFORM_SECRET_KEY: str | None = None
    STRIPE_PLATFORM_WEBHOOK_SECRET: str | None = None
    STRIPE_PRICE_SOLO: str | None = None
    STRIPE_PRICE_GROWTH: str | None = None
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
    # Facebook Login for Business configuration id (carries the ads_read permission). Unset = classic login with a scope parameter.
    META_ADS_LOGIN_CONFIG_ID: str | None = None
    # Bank feed (Plaid). ENV is sandbox, development or production; Klaros's own Plaid account, one for every tenant.
    PLAID_CLIENT_ID: str | None = None
    PLAID_SECRET: str | None = None
    PLAID_ENV: str = "sandbox"
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
    # Web search for Medical Tourism clinic sourcing (Tavily). Unset = clinic sourcing reports `not_configured` and does nothing.
    TAVILY_API_KEY: str | None = None
    # Verified SendGrid sender identity — SendGrid rejects sends from an
    # unverified address, so this must be a real, verified sender.
    SENDGRID_FROM_EMAIL: str | None = None
    # Any SMTP account can send Klaros's e-mail instead of SendGrid (Brevo has a free forever plan, Gmail and Zoho work too). All of host, username, password and a from
    # address are needed; unset = not configured. SendGrid wins over Brevo, and Brevo over SMTP. Some hosts (Render's free plan) block outbound SMTP: use the Brevo API there. Port 587 uses STARTTLS, port 465 uses SSL.
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_EMAIL: str | None = None
    # Brevo's web API (HTTPS, port 443). Prefer this to SMTP on a host that blocks outbound SMTP, as Render's free plan does. The sender defaults to SMTP_FROM_EMAIL, so only the API key is new.
    BREVO_API_KEY: str | None = None
    BREVO_FROM_EMAIL: str | None = None
    OPENAI_API_KEY: str | None = None
    # Which AI-workforce adapter this deployment wires in. "pending" (default) reports
    # honestly that no workforce is connected. "dev" enables the development simulator
    # (never reports CONNECTED; lets Halla-shaped events be simulated in dev/test only).
    WORKFORCE_ADAPTER: str = "pending"
    # --- Halla (real AI-workforce platform, a SEPARATE service). Used when WORKFORCE_ADAPTER=halla.
    # The base URL is deployment configuration, never tenant input: it must be https in production.
    HALLA_API_BASE_URL: str | None = None
    # Name of the HTTP header that carries the tenant-scoped Halla API credential. Deliberately has no
    # default: the header is defined by Halla's contract, and Klaros will not guess it.
    HALLA_API_KEY_HEADER: str | None = None
    # Optional prefix for the header value, e.g. "Bearer". Empty = send the key as-is.
    HALLA_API_KEY_SCHEME: str | None = None
    # Comma-separated hosts the base URL may point at. Defaults to the base URL's own host.
    HALLA_ALLOWED_HOSTS: str | None = None
    HALLA_REQUEST_TIMEOUT_SECONDS: float = 8.0
    # Health is allowed longer than a normal request: a hosted gateway that has been idle can take well over 10s to answer
    # its first request (measured live: 12s, then 0.6s, then 3s). Too tight a limit makes a healthy Halla flap to ERROR.
    HALLA_HEALTH_TIMEOUT_SECONDS: float = 20.0
    HALLA_MAX_RETRIES: int = 2
    # A stored CONNECTED state older than this is re-proven with a live health request.
    HALLA_STATUS_TTL_SECONDS: int = 300
    HALLA_WEBHOOK_TOLERANCE_SECONDS: int = 300
    # Platform secret that lets THIS Klaros deployment ask Halla to create a business's AI receptionist, so the owner never has to open a Halla account.
    # Unset = automatic setup is off (owners can still paste credentials). It is a service credential, never shown to an owner and never logged.
    HALLA_PROVISIONING_SECRET: str | None = None
    # How long a RECEIVED (in-flight) Halla event is owned by the worker that claimed it. After this a crashed worker's event may be reclaimed by a
    # redelivery; before it, a concurrent delivery of the same event is a duplicate and does nothing.
    HALLA_WEBHOOK_LEASE_SECONDS: int = 120
    # Public base URL of THIS Klaros API, used only to show the tenant the webhook URL to register in Halla.
    KLAROS_PUBLIC_API_URL: str | None = None
    # --- App Builder (app/builder): per-task AI budgets and run limits. The AI_* / *_MAX_OUTPUT_TOKENS defaults above are sized for one-line
    # briefs; planning a multi-page project needs far more output and a longer wait.
    BUILDER_PLAN_MAX_OUTPUT_TOKENS: int = 9000
    BUILDER_EDIT_MAX_OUTPUT_TOKENS: int = 6000
    BUILDER_LLM_TIMEOUT_SECONDS: float = 120.0
    BUILDER_PLAN_MODEL: str | None = None  # None = the provider's configured model
    BUILDER_MAX_REPAIR_ATTEMPTS: int = 2
    BUILDER_RUN_TIMEOUT_SECONDS: float = 420.0
    BUILDER_MAX_LLM_CALLS_PER_RUN: int = 6
    BUILDER_MAX_ACTIVE_RUNS_PER_TENANT: int = 1
    BUILDER_MAX_RUNS_PER_TENANT_PER_HOUR: int = 20
    BUILDER_MAX_PROJECTS_PER_TENANT: int = 25
    BUILDER_VERSIONS_KEPT: int = 30
    BUILDER_RUN_STALE_AFTER_SECONDS: int = 120
    BUILDER_PREVIEW_TOKEN_TTL_SECONDS: int = 3600
    # Custom domains: when set, a domain a business adds is registered with the frontend host (Vercel) automatically. Without them the owner is told to add it there by hand.
    # Replies to the emails a business sends: the subdomain whose mail is handed to Klaros by the email provider (for example reply.example.com), and the shared secret in the webhook address.
    # Both unset: replies are off and outgoing mail is unchanged.
    INBOUND_REPLY_DOMAIN: str | None = None
    INBOUND_EMAIL_WEBHOOK_SECRET: str | None = None
    VERCEL_API_TOKEN: str | None = None
    VERCEL_PROJECT_ID: str | None = None
    VERCEL_TEAM_ID: str | None = None
    BUILDER_PUBLIC_BASE_URL: str | None = None  # where published projects are served from (defaults to the API host)

    ANTHROPIC_API_KEY: str | None = None
    # "auto" (default) picks a real provider only if its key is set, preferring
    # anthropic > openai > groq > deepseek > nvidia > google, else falls back
    # to deterministic. "deterministic" forces the deterministic path even if
    # keys are present (useful for ops/testing). Any of "anthropic" / "openai"
    # / "groq" / "deepseek" / "nvidia" / "google" forces that provider —
    # get_ai_provider() still falls back to deterministic if the matching key
    # is missing, it never fabricates a connection.
    AI_PROVIDER: str = "auto"
    # Consent-gated tenants (Medical Tourism): comma-separated names of EXTERNAL AI / embedding providers that may receive their content. Empty
    # (the default) means NONE: such a tenant's data is never sent out and every AI call degrades to its deterministic path. Adding a name here is a
    # legal / data-processing decision (see docs/MEDICAL_TOURISM_POLICY_DECISIONS.md), not a technical switch. Other tenants are not affected.
    AI_EXTERNAL_PROCESSING_ALLOWED_PROVIDERS: str = ""
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
