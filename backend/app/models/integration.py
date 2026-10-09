"""Phase 12C: real external-provider integration state.

`WebhookEvent` is the idempotency + audit ledger for every inbound webhook
(Stripe, Twilio, ...) — the `(provider, external_event_id)` unique
constraint is what makes "duplicate webhook delivery must never double-
apply a payment" a real DB-enforced guarantee, not just an application
convention. Raw payload is retained (Phase 12C spec: "raw payload retention
where appropriate") for replay/debugging, but never contains the provider
credential itself (that lives only in Settings/env, never in a webhook
body).
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import UniqueConstraint

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class WebhookProcessingStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    REJECTED_SIGNATURE = "REJECTED_SIGNATURE"


class WebhookEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Deliberately NOT `TenantScopedMixin` — tenant is resolved FROM the
    payload (e.g. Stripe PaymentIntent metadata) only after signature
    verification succeeds, so at insert time the tenant isn't always known
    yet (a rejected-signature row never resolves one). `tenant_id` is
    nullable and populated when resolution succeeds."""

    __tablename__ = "webhook_events"

    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=WebhookProcessingStatus.RECEIVED)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "external_event_id", name="uq_webhook_events_provider_external_id"),
    )


class ConnectionStatus(StrEnum):
    NOT_CONNECTED = "NOT_CONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"
    DISCONNECTED = "DISCONNECTED"
    # Credential stored, but NO successful verification call has been made (the operator chose to skip it, or it has not run yet).
    # Deliberately never reported as CONNECTED. Inbound signed webhooks and outbound calls still work (only DISCONNECTED blocks them).
    UNVERIFIED = "UNVERIFIED"


class IntegrationConnection(TenantScopedMixin, Base):
    """Phase 12D: a tenant's OWN connection to a provider — distinct from
    the platform-level, single-shared-credential providers wired up in
    Phase 12C (Stripe/Twilio/SendGrid/OpenAI/Anthropic, configured once via
    Settings/env vars for the whole platform). This model exists for
    providers where each tenant has their OWN external account (QuickBooks,
    Google Calendar, Gmail, Google Ads, Meta Ads, ...) — a real OAuth-based
    multi-tenant integration must never share one global credential across
    tenants.

    `encrypted_credential` is opaque ciphertext (see
    app/integrations/credential_store.py) — this model, and every service
    that touches it, must never expose plaintext credential material
    outside the encrypt/decrypt boundary. `(tenant_id, provider)` is unique:
    one connection per provider per tenant."""

    __tablename__ = "integration_connections"

    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ConnectionStatus.NOT_CONNECTED)
    # Opaque, Fernet-encrypted JSON blob — api_key / access_token /
    # refresh_token / client_id / whatever the provider needs. Never
    # queried on, never logged, never returned by any API response.
    encrypted_credential: Mapped[str | None] = mapped_column(String, nullable=True)
    external_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scopes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    connection_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", name="uq_integration_connections_tenant_provider"),
    )
