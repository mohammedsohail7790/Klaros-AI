"""The sales-contract lifecycle — created automatically once a customer
accepts a Quote (see the `QUOTE_ACCEPTED` subscriber in
`app/events/crm_handlers.py`), distinct from both the quote's own
acceptance decision and the deposit/payment that follows it. Mirrors
`QuoteService`'s own structure and conventions closely (number generation,
idempotency key, public-token view flow) rather than inventing a new
pattern.

Deliberately does NOT block or gate the existing deposit/payment pipeline
— `QuoteService.decide()`/`mark_deposit_paid()` are untouched. A Contract
is created alongside the accepted quote so the business now has a genuine,
distinct signed-agreement record, without risking the already-hardened
money-moving path."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.contract import Contract, ContractStatus, compute_content_hash
from app.models.crm import Customer
from app.models.event import EventType
from app.models.quote import Quote, QuoteLineItem

# Phase 24: how long a sent-but-undecided contract stays actionable before
# a stale-follow-up sweep flags it. Deliberately shorter than a Quote's own
# `DEFAULT_VALID_DAYS` (30, app/services/quote_service.py) — a contract is
# the "please sign now" step immediately after the customer already
# accepted the quote's price, a faster-moving business moment than the
# initial price-shopping window a quote represents. No existing product
# convention specifies a contract-specific number, so this is a new,
# explicitly documented default, not a value inferred from other code.
CONTRACT_PENDING_THRESHOLD_DAYS = 7


class ContractNotFoundError(Exception):
    pass


class QuoteNotFoundError(Exception):
    pass


class InvalidContractTransitionError(Exception):
    pass


class ContractService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus) -> None:
        self._session_factory = session_factory
        self._bus = bus

    async def _next_contract_number(self, session, tenant_id: uuid.UUID) -> str:
        count = (
            await session.execute(select(func.count(Contract.id)).where(Contract.tenant_id == tenant_id))
        ).scalar_one()
        return f"CTR-{1000 + count + 1}"

    def _render_content(self, quote: Quote, line_items: list[QuoteLineItem], customer: Customer) -> str:
        """Deterministic, plain-text rendering of the agreement — real
        content built from the quote's own real data, never placeholder
        text. Frozen into `Contract.content` at creation time."""
        lines = [
            f"Service Agreement for {quote.quote_number}",
            f"Customer: {customer.name}",
            "",
            "Line items:",
        ]
        for item in line_items:
            lines.append(f"- {item.description}: {item.quantity} x ${item.unit_price} = ${item.line_total}")
        lines += [
            "",
            f"Subtotal: ${quote.subtotal}",
            f"Tax: ${quote.tax}",
            f"Discount: ${quote.discount}",
            f"Total: ${quote.total} {quote.currency}",
        ]
        if quote.terms:
            lines += ["", "Terms:", quote.terms]
        return "\n".join(lines)

    async def create_from_quote(self, tenant_id: uuid.UUID, quote_id: uuid.UUID) -> tuple[Contract, bool]:
        """Idempotent: one Contract per Quote. Safe to call repeatedly
        (e.g. a redelivered `QUOTE_ACCEPTED` event) — returns the existing
        row rather than creating a duplicate.

        Phase 30 fix: the docstring's own "safe to call repeatedly" claim
        was only true for sequential redelivery — the real
        `uq_contracts_tenant_idempotency_key` constraint backs it, but a
        genuinely concurrent redelivery (e.g. Redis Streams reclaiming and
        redelivering a message to a second worker while the first is still
        mid-flight) would have raised an unhandled `IntegrityError`
        instead of returning the existing row, same class of defect as
        Phase 29's `CollectionService.schedule_next_action` and this
        phase's `LeadService.create_lead`. Fixed with the same
        try/except/rollback/re-fetch CAS pattern."""
        idempotency_key = f"contract-for-quote-{quote_id}"
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(Contract).where(
                        Contract.tenant_id == tenant_id, Contract.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing, True

            quote = await session.get(Quote, quote_id)
            if quote is None or quote.tenant_id != tenant_id:
                raise QuoteNotFoundError("Quote not found")

            customer = await session.get(Customer, quote.customer_id)
            if customer is None or customer.tenant_id != tenant_id:
                raise QuoteNotFoundError("Quote's customer not found")

            line_items = (
                await session.execute(
                    select(QuoteLineItem)
                    .where(QuoteLineItem.tenant_id == tenant_id, QuoteLineItem.quote_id == quote_id)
                    .order_by(QuoteLineItem.sort_order)
                )
            ).scalars().all()

            content = self._render_content(quote, list(line_items), customer)
            contract = Contract(
                tenant_id=tenant_id,
                contract_number=await self._next_contract_number(session, tenant_id),
                quote_id=quote_id,
                customer_id=customer.id,
                status=ContractStatus.DRAFT,
                content=content,
                content_hash=compute_content_hash(content),
                idempotency_key=idempotency_key,
            )
            session.add(contract)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = (
                    await session.execute(
                        select(Contract).where(
                            Contract.tenant_id == tenant_id, Contract.idempotency_key == idempotency_key
                        )
                    )
                ).scalar_one()
                return existing, True
            await session.refresh(contract)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.CONTRACT_CREATED,
            source="contracts",
            entity_type="contract",
            entity_id=contract.id,
            payload={"contract_id": str(contract.id), "quote_id": str(quote_id)},
            idempotency_key=f"contract-created-{contract.id}",
        )
        return contract, False

    async def get(self, tenant_id: uuid.UUID, contract_id: uuid.UUID) -> Contract:
        async with self._session_factory() as session:
            contract = await session.get(Contract, contract_id)
        if contract is None or contract.tenant_id != tenant_id:
            raise ContractNotFoundError("Contract not found")
        return contract

    async def send(self, tenant_id: uuid.UUID, contract_id: uuid.UUID) -> Contract:
        async with self._session_factory() as session:
            contract = await session.get(Contract, contract_id)
            if contract is None or contract.tenant_id != tenant_id:
                raise ContractNotFoundError("Contract not found")
            if contract.status != ContractStatus.DRAFT:
                raise InvalidContractTransitionError(
                    f"Cannot send: contract is {contract.status}, expected DRAFT"
                )
            contract.status = ContractStatus.SENT
            contract.sent_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(contract)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.CONTRACT_SENT,
            source="contracts",
            entity_type="contract",
            entity_id=contract.id,
            payload={"contract_id": str(contract.id)},
        )
        return contract

    async def get_for_public_view(self, tenant_id: uuid.UUID, contract_id: uuid.UUID) -> Contract:
        """Marks VIEWED the first time (SENT -> VIEWED); a safe no-op on
        any later view. Never mutates an already-decided contract."""
        async with self._session_factory() as session:
            contract = await session.get(Contract, contract_id)
            if contract is None or contract.tenant_id != tenant_id:
                raise ContractNotFoundError("Contract not found")
            first_view = contract.status == ContractStatus.SENT
            if first_view:
                contract.status = ContractStatus.VIEWED
                contract.viewed_at = datetime.now(timezone.utc)
                await session.commit()
                await session.refresh(contract)

        if first_view:
            await self._bus.publish(
                tenant_id=tenant_id,
                event_type=EventType.CONTRACT_VIEWED,
                source="contracts",
                entity_type="contract",
                entity_id=contract.id,
                payload={"contract_id": str(contract.id)},
            )
        return contract

    async def sign(
        self, tenant_id: uuid.UUID, contract_id: uuid.UUID, *, signer_name: str, signer_email: str | None,
    ) -> Contract:
        """The internal signing workflow: no external e-signature provider
        is involved. Recording `signer_name`/`signer_email`/`signed_at`
        (`decided_at`) plus the frozen `content_hash` is an honest,
        real attestation of what was agreed to and by whom -- never
        represented as a third-party-verified signature."""
        async with self._session_factory() as session:
            contract = await session.get(Contract, contract_id)
            if contract is None or contract.tenant_id != tenant_id:
                raise ContractNotFoundError("Contract not found")
            if contract.status == ContractStatus.SIGNED:
                return contract
            if contract.status not in (ContractStatus.SENT, ContractStatus.VIEWED):
                raise InvalidContractTransitionError(
                    f"Cannot sign: contract is {contract.status}, expected SENT or VIEWED"
                )
            contract.status = ContractStatus.SIGNED
            contract.signer_name = signer_name
            contract.signer_email = signer_email
            contract.decided_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(contract)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.CONTRACT_SIGNED,
            source="contracts",
            entity_type="contract",
            entity_id=contract.id,
            payload={"contract_id": str(contract.id), "signer_name": signer_name},
            idempotency_key=f"contract-signed-{contract.id}",
        )
        return contract

    async def decline(
        self, tenant_id: uuid.UUID, contract_id: uuid.UUID, *, reason: str | None = None,
    ) -> Contract:
        async with self._session_factory() as session:
            contract = await session.get(Contract, contract_id)
            if contract is None or contract.tenant_id != tenant_id:
                raise ContractNotFoundError("Contract not found")
            if contract.status == ContractStatus.DECLINED:
                return contract
            if contract.status not in (ContractStatus.SENT, ContractStatus.VIEWED):
                raise InvalidContractTransitionError(
                    f"Cannot decline: contract is {contract.status}, expected SENT or VIEWED"
                )
            contract.status = ContractStatus.DECLINED
            contract.decline_reason = reason
            contract.decided_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(contract)

        await self._bus.publish(
            tenant_id=tenant_id,
            event_type=EventType.CONTRACT_DECLINED,
            source="contracts",
            entity_type="contract",
            entity_id=contract.id,
            payload={"contract_id": str(contract.id)},
        )
        return contract

    async def detect_pending(self, tenant_id: uuid.UUID, *, as_of: datetime | None = None) -> list[uuid.UUID]:
        """Phase 24: the contract-pending-follow-up sweep — same real
        pattern as `QuoteService.detect_expired`/`ARService.detect_overdue`
        (a deterministic, periodic/scheduled sweep; no Temporal workflow,
        no second scheduler). `ContractStatus.EXPIRED` and
        `EventType.CONTRACT_EXPIRED` already existed in this codebase but
        were never reached by any code path before this — this sweep is
        what completes that half-built feature, exactly mirroring how
        Phase 11 completed the equivalent stale-quote sweep. Idempotent:
        only ever transitions SENT/VIEWED contracts (an already-EXPIRED
        row is never re-selected, so a duplicate/rerun sweep is a safe
        no-op).

        Phase 28 fix: `.with_for_update(skip_locked=True)` — a real
        PostgreSQL concurrency test (`test_postgres_phase24_automations.py
        ::test_concurrent_contract_pending_sweep_ticks_expire_contract_
        exactly_once`) proved that without row locking, N genuinely
        concurrent scheduler ticks under PostgreSQL's default READ
        COMMITTED isolation can all SELECT the same stale contract before
        any of them COMMITs its UPDATE — each one then independently
        transitions the row and publishes its own CONTRACT_EXPIRED event,
        producing duplicates. SQLite's single-writer serialization had
        been silently masking this: each call's SELECT+UPDATE+COMMIT
        always completed before the next call's SELECT could even run, so
        the bug was invisible until executed against real PostgreSQL.
        `skip_locked=True` makes concurrent tickers each grab a disjoint
        set of not-yet-locked rows — a row already locked by another
        in-flight sweep is simply skipped, not waited on — so N concurrent
        calls against the same contract now produce exactly one winner, no
        blocking. A no-op on SQLite (which does not support row locking;
        SQLAlchemy's sqlite dialect drops `FOR UPDATE` from the compiled
        SQL), so existing SQLite-backed tests are unaffected."""
        now = as_of or datetime.now(timezone.utc)
        threshold = now - timedelta(days=CONTRACT_PENDING_THRESHOLD_DAYS)
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Contract).where(
                        Contract.tenant_id == tenant_id,
                        Contract.status.in_([ContractStatus.SENT, ContractStatus.VIEWED]),
                        Contract.sent_at.is_not(None),
                        Contract.sent_at < threshold,
                    ).with_for_update(skip_locked=True)
                )
            ).scalars().all()
            ids = [r.id for r in rows]
            for row in rows:
                row.status = ContractStatus.EXPIRED
            await session.commit()

        for contract_id in ids:
            await self._bus.publish(
                tenant_id=tenant_id, event_type=EventType.CONTRACT_EXPIRED, source="contracts",
                entity_type="contract", entity_id=contract_id, payload={"contract_id": str(contract_id)},
            )
        return ids
