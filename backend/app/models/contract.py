"""The sales-side contract/agreement between quote acceptance and payment
— genuinely absent before this: `CustomerSignoff` (app/models/operations.py)
represents POST-COMPLETION job signoff, a different business moment. This
represents the customer's binding agreement to the accepted quote's terms,
distinct from both QUOTE_ACCEPTED (the customer's initial decision) and
PAYMENT_RECEIVED (the money actually moving).

No external e-signature provider is integrated (no credentials, no
established provider boundary for this yet) — this is a real, internal
signing workflow: the customer views the frozen contract content through a
signed, tenant-bound token (mirrors `create_quote_view_token`), and typing
their name to sign records a genuine `signed_at` timestamp, `signer_name`/
`signer_email`, and a SHA-256 hash of the exact content they agreed to
(`content_hash`) — proof of exactly what was signed, immutable once
`SIGNED`. This is never represented or logged as if an external
DocuSign-style provider verified the signature; it is an internal
attestation only, matching this project's standing rule to never fabricate
a live-provider claim.

One Contract per Quote (idempotency_key `contract-for-quote-{quote_id}`),
same pattern as `Invoice.idempotency_key` for `invoice-for-job-{job_id}`."""

import hashlib
import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin


class ContractStatus(StrEnum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    VIEWED = "VIEWED"
    SIGNED = "SIGNED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class Contract(TenantScopedMixin, Base):
    __tablename__ = "contracts"

    contract_number: Mapped[str] = mapped_column(String(50), nullable=False)
    quote_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ContractStatus.DRAFT, index=True)

    # The exact agreement text the customer is being asked to sign — frozen
    # at creation time from the quote's own terms/line items, never
    # recomputed afterward (the quote itself could theoretically change
    # status further, but the contract's own content must not silently
    # drift out from under an already-sent or already-signed agreement).
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 of `content`, computed once at creation and never
    # recomputed — the immutable record of exactly what was presented,
    # independent of whatever `content` might (should never, but might)
    # later contain if a bug ever mutated it post-signature.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    signer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decline_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "contract_number", name="uq_contracts_tenant_number"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_contracts_tenant_idempotency_key"),
    )


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
