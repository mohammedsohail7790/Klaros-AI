"""Clinic quotes for one patient's enquiry, side by side, with the commission disclosed.

A quote is what a clinic has put in writing for this patient. Klaros keeps the figures and what they include, and says where the written quote is kept. The comparison is ordered by PRICE, lowest first
within each currency, never by commission: a facilitator paid by clinics must not steer a patient to whoever pays most, and must say so. Every quote therefore carries a plain disclosure
("we receive a commission from this clinic" / "we have no commission agreement with this clinic") that is safe to show the patient; the amounts stay with managers.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from app.db.session import set_tenant_context
from app.models.crm import Lead
from app.models.medical_tourism import Procedure, Provider
from app.models.mt_quotes import ClinicQuote, QuoteStatus
from app.models.mt_revenue import ProviderTerms
from app.services.mt_referrals import ReferralError

PRICE_ORDER_NOTE = "Clinics are listed by price, lowest first. They are not ordered by commission."
GENERAL_DISCLOSURE = "We are paid a commission by the clinics we refer patients to. " + PRICE_ORDER_NOTE


def _money(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(Decimal("0.01")), "f")


def _amount(body: dict) -> Decimal:
    try:
        v = Decimal(str(body.get("amount")))
    except (InvalidOperation, TypeError):
        raise ReferralError("invalid_amount", "Enter the quoted price as a number.", status=422) from None
    if not v.is_finite() or v <= 0 or v >= Decimal("1000000000"):
        raise ReferralError("invalid_amount", "The quoted price must be more than zero.", status=422)
    return v


def _currency(body: dict) -> str:
    raw = body.get("currency")
    if not isinstance(raw, str) or len(raw.strip()) != 3 or not raw.strip().isalpha():
        raise ReferralError("currency_required", "Use a 3-letter currency code such as USD.", status=422)
    return raw.strip().upper()


def _text(body: dict, key: str, limit: int) -> str | None:
    raw = body.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ReferralError(f"invalid_{key}", status=422)
    clean = raw.strip()[:limit]
    return clean or None


def _date(body: dict, key: str) -> date | None:
    raw = body.get(key)
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        raise ReferralError(f"invalid_{key}", "Use a date such as 2026-12-31.", status=422) from None


def _uuid_or_none(body: dict, key: str) -> uuid.UUID | None:
    raw = body.get(key)
    if raw in (None, ""):
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        raise ReferralError(f"invalid_{key}", status=422) from None


async def record_quote(session_factory, tenant_id: uuid.UUID, lead_id: uuid.UUID, actor_id: uuid.UUID | None, body: dict) -> ClinicQuote:
    """Record a clinic's quote. A newer quote from the same clinic for the same procedure replaces the earlier one, which is kept as SUPERSEDED."""
    provider_id = _uuid_or_none(body, "provider_id")
    if provider_id is None:
        raise ReferralError("provider_required", "Choose the clinic that gave the quote.", status=422)
    procedure_id = _uuid_or_none(body, "procedure_id")
    amount, currency = _amount(body), _currency(body)
    includes = _text(body, "includes", 2000)
    if not includes:
        raise ReferralError("includes_required", "Say what the price includes, so quotes can be compared like for like.", status=422)
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        lead = (await session.execute(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))).scalar_one_or_none()
        if lead is None:
            raise ReferralError("lead_not_found", status=404)
        provider = (await session.execute(select(Provider).where(Provider.id == provider_id, Provider.tenant_id == tenant_id))).scalar_one_or_none()
        if provider is None:
            raise ReferralError("provider_not_found", status=404)
        if procedure_id is not None:
            found = (await session.execute(select(Procedure.id).where(Procedure.id == procedure_id, Procedure.tenant_id == tenant_id))).scalar_one_or_none()
            if found is None:
                raise ReferralError("procedure_not_found", status=404)
        earlier = (
            await session.execute(
                select(ClinicQuote).where(
                    ClinicQuote.tenant_id == tenant_id, ClinicQuote.lead_id == lead_id, ClinicQuote.provider_id == provider_id,
                    ClinicQuote.procedure_id == procedure_id, ClinicQuote.status == QuoteStatus.ACTIVE,
                )
            )
        ).scalars().all()
        for q in earlier:
            q.status = QuoteStatus.SUPERSEDED
        row = ClinicQuote(
            tenant_id=tenant_id, lead_id=lead_id, provider_id=provider_id, procedure_id=procedure_id, amount=amount, currency=currency, includes=includes,
            excludes=_text(body, "excludes", 2000), valid_until=_date(body, "valid_until"), reference=_text(body, "reference", 255), status=QuoteStatus.ACTIVE, recorded_by=actor_id,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def withdraw_quote(session_factory, tenant_id: uuid.UUID, quote_id: uuid.UUID) -> ClinicQuote:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        row = (await session.execute(select(ClinicQuote).where(ClinicQuote.id == quote_id, ClinicQuote.tenant_id == tenant_id))).scalar_one_or_none()
        if row is None:
            raise ReferralError("quote_not_found", status=404)
        row.status = QuoteStatus.WITHDRAWN
        await session.commit()
        await session.refresh(row)
        return row


def _commission(terms: ProviderTerms | None, amount: Decimal, currency: str) -> dict[str, Any]:
    """What this clinic would pay us on this quote. Estimated only when the figures can be compared (a percentage, or a flat fee in the same currency)."""
    if terms is None or not terms.commission_basis:
        return {"agreed": False, "basis": None, "percentage": None, "flat_amount": None, "currency": None, "estimated": None, "estimated_currency": None}
    estimated: Decimal | None = None
    if terms.commission_basis == "PERCENTAGE" and terms.commission_percentage is not None:
        estimated = (amount * terms.commission_percentage / Decimal("100"))
    elif terms.commission_basis == "FLAT" and terms.flat_amount is not None and terms.currency == currency:
        estimated = terms.flat_amount
    return {
        "agreed": True, "basis": terms.commission_basis, "percentage": _money(terms.commission_percentage), "flat_amount": _money(terms.flat_amount), "currency": terms.currency,
        "estimated": _money(estimated), "estimated_currency": currency if estimated is not None else None,
    }


async def quotes_for_lead(session_factory, tenant_id: uuid.UUID, lead_id: uuid.UUID, *, show_money: bool, today: date | None = None) -> dict[str, Any]:
    """The active quotes for one enquiry, ordered by price within each currency, each with its clinic, verification and commission disclosure.

    `show_money` is True for managers only: it adds the commission figures. The yes/no disclosure and the ordering note are for everyone."""
    today = today or date.today()
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        lead = (await session.execute(select(Lead.id).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))).scalar_one_or_none()
        if lead is None:
            raise ReferralError("lead_not_found", status=404)
        quotes = (
            await session.execute(select(ClinicQuote).where(ClinicQuote.tenant_id == tenant_id, ClinicQuote.lead_id == lead_id, ClinicQuote.status == QuoteStatus.ACTIVE))
        ).scalars().all()
        provider_ids = {q.provider_id for q in quotes}
        providers = {p.id: p for p in (await session.execute(select(Provider).where(Provider.tenant_id == tenant_id, Provider.id.in_(provider_ids)))).scalars()} if provider_ids else {}
        terms = {t.provider_id: t for t in (await session.execute(select(ProviderTerms).where(ProviderTerms.tenant_id == tenant_id, ProviderTerms.provider_id.in_(provider_ids)))).scalars()} if provider_ids else {}
        procedure_ids = {q.procedure_id for q in quotes if q.procedure_id}
        procedures = {p.id: p.name for p in (await session.execute(select(Procedure).where(Procedure.tenant_id == tenant_id, Procedure.id.in_(procedure_ids)))).scalars()} if procedure_ids else {}

    ordered = sorted(quotes, key=lambda q: (q.currency, q.amount, q.created_at))
    lowest: dict[str, uuid.UUID] = {}
    for q in ordered:
        lowest.setdefault(q.currency, q.id)
    rows = []
    for q in ordered:
        p = providers.get(q.provider_id)
        t = terms.get(q.provider_id)
        has_commission = bool(t and t.commission_basis)
        row: dict[str, Any] = {
            "id": str(q.id), "provider_id": str(q.provider_id), "provider_name": p.name if p else "Unknown clinic", "country": (p.country if p else None),
            "verified": bool(p and getattr(p, "verification_status", None) == "VERIFIED"), "procedure": procedures.get(q.procedure_id) if q.procedure_id else None,
            "amount": _money(q.amount), "currency": q.currency, "includes": q.includes, "excludes": q.excludes,
            "valid_until": q.valid_until.isoformat() if q.valid_until else None, "expired": bool(q.valid_until and q.valid_until < today),
            "reference": q.reference, "recorded_at": q.created_at.isoformat() if q.created_at else None, "lowest_in_currency": lowest.get(q.currency) == q.id,
            "disclosure": "We receive a commission from this clinic." if has_commission else "We have no commission agreement with this clinic.",
        }
        if show_money:
            row["commission"] = _commission(t, q.amount, q.currency)
        rows.append(row)
    return {"lead_id": str(lead_id), "quotes": rows, "order_note": PRICE_ORDER_NOTE, "general_disclosure": GENERAL_DISCLOSURE, "currencies": sorted({q.currency for q in quotes})}
