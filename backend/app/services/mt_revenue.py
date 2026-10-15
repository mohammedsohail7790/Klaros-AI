"""Turns a clinic's agreed terms into money movements without anyone retyping them.

  terms        what a clinic has agreed to pay (commission as a percentage or a flat fee). A clinic with no terms gets nothing created, and says so.
  on_referral_sent       creates the PENDING commission from those terms. Potential only: it is never revenue.
  on_commission_confirmed   when a person has confirmed the clinic reported the agreed outcome (with evidence), raises a DRAFT invoice to the clinic. A person still approves and sends it.

Every hook is best effort: it never raises into the action that triggered it (sending a referral, confirming a commission), and a repeat is a no-op.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from sqlalchemy import select

from app.db.session import set_tenant_context
from app.models.crm import Customer, CustomerStatus
from app.models.finance import Invoice
from app.models.medical_tourism import Procedure, Provider, ProviderProcedure, ReferralCommission, ReferralCommissionBasis
from app.models.medical_tourism_referral import ProviderReferral, ReferralOutcome
from app.models.mt_revenue import ProviderTerms
from app.services.mt_referrals import ReferralError, _audit

logger = structlog.get_logger(__name__)
BASES = {ReferralCommissionBasis.PERCENTAGE.value, ReferralCommissionBasis.FLAT.value}


def terms_to_dict(t: ProviderTerms | None) -> dict[str, Any]:
    if t is None:
        return {"set": False, "commission_basis": None, "commission_percentage": None, "flat_amount": None, "currency": None, "qualifying_outcome": None,
                "agreement_reference": None}
    d = lambda v: str(v) if v is not None else None  # noqa: E731
    return {"set": True, "commission_basis": t.commission_basis, "commission_percentage": d(t.commission_percentage), "flat_amount": d(t.flat_amount), "currency": t.currency,
            "qualifying_outcome": t.qualifying_outcome, "agreement_reference": t.agreement_reference}


def _dec(body: dict, key: str, *, lo: Decimal = Decimal("0"), hi: Decimal | None = None) -> Decimal | None:
    raw = body.get(key)
    if raw in (None, ""):
        return None
    try:
        v = Decimal(str(raw))
    except InvalidOperation:
        raise ReferralError("invalid_amount", f"{key.replace('_', ' ').capitalize()} must be a number.", status=422) from None
    if v < lo or (hi is not None and v > hi) or not v.is_finite():
        raise ReferralError("invalid_amount", f"{key.replace('_', ' ').capitalize()} is out of range.", status=422)
    return v


def _currency(body: dict, key: str) -> str | None:
    raw = body.get(key)
    if raw in (None, ""):
        return None
    if not isinstance(raw, str) or len(raw.strip()) != 3 or not raw.strip().isalpha():
        raise ReferralError("currency_required", "Use a 3-letter currency code such as USD.", status=422)
    return raw.strip().upper()


async def get_terms(session_factory, tenant_id: uuid.UUID, provider_id: uuid.UUID) -> ProviderTerms | None:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        return (await session.execute(select(ProviderTerms).where(ProviderTerms.tenant_id == tenant_id, ProviderTerms.provider_id == provider_id))).scalar_one_or_none()


async def set_terms(session_factory, tenant_id: uuid.UUID, provider_id: uuid.UUID, actor_id: uuid.UUID, body: dict) -> ProviderTerms:
    basis = (str(body.get("commission_basis") or "").upper()) or None
    if basis is not None and basis not in BASES:
        raise ReferralError("invalid_basis", "Commission is a percentage or a flat fee.", status=422)
    pct = _dec(body, "commission_percentage", hi=Decimal("100"))
    flat = _dec(body, "flat_amount")
    currency = _currency(body, "currency")
    if basis == "PERCENTAGE" and pct is None:
        raise ReferralError("commission_percentage_required", "Enter the commission percentage.", status=422)
    if basis == "FLAT" and flat is None:
        raise ReferralError("flat_amount_required", "Enter the flat fee.", status=422)
    if basis is not None and currency is None:
        raise ReferralError("currency_required", "Choose the currency the commission is paid in.", status=422)
    outcome = (str(body.get("qualifying_outcome") or "").upper()) or None
    if outcome is not None and outcome not in {o.value for o in ReferralOutcome}:
        raise ReferralError("unknown_outcome", status=422)
    ref = (str(body.get("agreement_reference") or "").strip()[:255]) or None
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        provider = (await session.execute(select(Provider).where(Provider.id == provider_id, Provider.tenant_id == tenant_id))).scalar_one_or_none()
        if provider is None:
            raise ReferralError("not_found", status=404)
        row = (await session.execute(select(ProviderTerms).where(ProviderTerms.tenant_id == tenant_id, ProviderTerms.provider_id == provider_id))).scalar_one_or_none()
        if row is None:
            row = ProviderTerms(tenant_id=tenant_id, provider_id=provider_id)
            session.add(row)
        row.commission_basis, row.commission_percentage, row.flat_amount, row.currency = basis, pct if basis == "PERCENTAGE" else None, flat if basis == "FLAT" else None, currency
        row.qualifying_outcome, row.agreement_reference, row.updated_by = outcome, ref, actor_id
        await session.flush()
        _audit(session, tenant_id, actor_id, "mt.terms.updated", "medical_tourism_provider_terms", row.id, {"provider_id": str(provider_id), "basis": basis, "currency": currency})
        await session.commit()
        await session.refresh(row)
        return row


async def on_referral_sent(session_factory, tenant_id: uuid.UUID, referral_id: uuid.UUID, actor_id: uuid.UUID | None) -> ReferralCommission | None:
    """The PENDING commission for a referral that has just been sent, from the clinic's terms. None when there is nothing to create (no terms, or one already exists)."""
    from app.services import mt_commission

    try:
        async with session_factory() as session:
            await set_tenant_context(session, tenant_id)
            ref = (await session.execute(select(ProviderReferral).where(ProviderReferral.id == referral_id, ProviderReferral.tenant_id == tenant_id))).scalar_one_or_none()
            if ref is None:
                return None
            terms = (await session.execute(select(ProviderTerms).where(ProviderTerms.tenant_id == tenant_id, ProviderTerms.provider_id == ref.provider_id))).scalar_one_or_none()
            if terms is None or terms.commission_basis is None or terms.currency is None:
                _audit(session, tenant_id, actor_id, "mt.commission.no_terms", "medical_tourism_provider_referral", ref.id, {"provider_id": str(ref.provider_id)}, result="skipped")
                await session.commit()
                return None
            price = None
            if ref.procedure_id is not None:
                pp = (await session.execute(select(ProviderProcedure).where(ProviderProcedure.tenant_id == tenant_id, ProviderProcedure.provider_id == ref.provider_id,
                                                                            ProviderProcedure.procedure_id == ref.procedure_id))).scalars().first()
                if pp is not None and pp.estimated_price is not None and (pp.currency or "").upper() == terms.currency:
                    price = pp.estimated_price
            basis, pct, flat, cur, outcome = terms.commission_basis, terms.commission_percentage, terms.flat_amount, terms.currency, terms.qualifying_outcome
        try:
            return await mt_commission.create_for_referral(session_factory, tenant_id, referral_id, actor_id or uuid.UUID(int=0), currency=cur, basis=basis, commission_percentage=pct,
                                                           flat_amount=flat, reference_amount=price, qualifying_outcome=outcome or ReferralOutcome.TREATMENT_COMPLETED)
        except ReferralError as exc:
            if exc.code != "commission_exists":
                logger.warning("mt_auto_commission_refused", code=exc.code)
            return None
    except Exception as exc:  # noqa: BLE001 - never break sending a referral
        logger.warning("mt_auto_commission_failed", error=type(exc).__name__)
        return None


async def _billing_customer(session, tenant_id: uuid.UUID, provider: Provider) -> Customer:
    existing = (await session.execute(select(Customer).where(Customer.tenant_id == tenant_id, Customer.external_provider == "mt_provider", Customer.external_id == str(provider.id)))).scalar_one_or_none()
    if existing is not None:
        return existing
    c = Customer(tenant_id=tenant_id, name=provider.name, company_name=provider.name, email=provider.contact_email, phone=provider.contact_phone, city=provider.city,
                 notes="Billing account for a partner clinic (commissions and fees).", status=CustomerStatus.ACTIVE, external_provider="mt_provider", external_id=str(provider.id))
    session.add(c)
    await session.flush()
    return c


async def on_commission_confirmed(session_factory, tenant_id: uuid.UUID, commission_id: uuid.UUID, actor_id: uuid.UUID | None, invoice_service) -> uuid.UUID | None:
    """A DRAFT invoice to the clinic for an earned commission. Returns the invoice id; None when it cannot or need not be raised (no amount known yet, already invoiced)."""
    from app.services.invoice_service import LineItemInput

    try:
        async with session_factory() as session:
            await set_tenant_context(session, tenant_id)
            c = (await session.execute(select(ReferralCommission).where(ReferralCommission.id == commission_id, ReferralCommission.tenant_id == tenant_id))).scalar_one_or_none()
            if c is None or c.invoice_id is not None or c.provider_id is None:
                return None
            if c.computed_amount is None or c.computed_amount <= 0:
                _audit(session, tenant_id, actor_id, "mt.commission.invoice_needs_amount", "medical_tourism_referral_commission", c.id, {}, result="skipped")
                await session.commit()
                return None
            provider = await session.get(Provider, c.provider_id)
            customer = await _billing_customer(session, tenant_id, provider)
            procedure = None
            if c.provider_referral_id is not None:
                ref = await session.get(ProviderReferral, c.provider_referral_id)
                if ref is not None and ref.procedure_id is not None:
                    procedure = await session.get(Procedure, ref.procedure_id)
            customer_id, amount, currency = customer.id, c.computed_amount, c.currency
            label = f"Referral commission{' - ' + procedure.name if procedure else ''} (ref {str(c.provider_referral_id or c.id)[:8]})"
            await session.commit()
        invoice = await invoice_service.create_manual_draft(tenant_id, customer_id=customer_id, job_id=None, items=[LineItemInput(description=label, quantity=Decimal("1"), unit_price=amount)])
        async with session_factory() as session:
            await set_tenant_context(session, tenant_id)
            inv = (await session.execute(select(Invoice).where(Invoice.id == invoice.id, Invoice.tenant_id == tenant_id))).scalar_one()
            inv.currency = currency
            c = (await session.execute(select(ReferralCommission).where(ReferralCommission.id == commission_id, ReferralCommission.tenant_id == tenant_id))).scalar_one()
            c.invoice_id = inv.id
            _audit(session, tenant_id, actor_id, "mt.commission.invoice_drafted", "medical_tourism_referral_commission", c.id, {"invoice_id": str(inv.id), "currency": currency})
            await session.commit()
            return inv.id
    except Exception as exc:  # noqa: BLE001 - never break confirming a commission
        logger.warning("mt_auto_invoice_failed", error=type(exc).__name__)
        return None


async def on_outcome_recorded(session_factory, tenant_id: uuid.UUID, referral_id: uuid.UUID, actor_id: uuid.UUID, treatment_amount: Decimal | None, currency: str | None) -> dict[str, Any]:
    """A person has recorded the clinic's reported outcome (with evidence). If the commission is a percentage and the treatment price is given, work out the amount; when the outcome is the one
    the agreement names and the amount is known, confirm the commission, which drafts the invoice to the clinic. Never raises; returns what happened so the caller can show it."""
    from app.services import mt_commission

    result: dict[str, Any] = {"commission_id": None, "status": None, "invoice_id": None, "note": None}
    try:
        async with session_factory() as session:
            await set_tenant_context(session, tenant_id)
            c = (await session.execute(select(ReferralCommission).where(ReferralCommission.tenant_id == tenant_id, ReferralCommission.provider_referral_id == referral_id))).scalar_one_or_none()
            ref = (await session.execute(select(ProviderReferral).where(ProviderReferral.id == referral_id, ProviderReferral.tenant_id == tenant_id))).scalar_one_or_none()
            if c is None or ref is None:
                result["note"] = "No commission is set up for this referral: add the clinic's terms."
                return result
            result["commission_id"] = str(c.id)
            if treatment_amount is not None:
                if c.basis != ReferralCommissionBasis.PERCENTAGE or c.commission_percentage is None:
                    result["note"] = "This clinic's commission is a flat fee, so the treatment price is not needed."
                elif currency and currency.upper() != c.currency:
                    raise ReferralError("currency_mismatch", f"The commission is in {c.currency}: give the treatment price in {c.currency}.", status=422)
                elif treatment_amount <= 0:
                    raise ReferralError("invalid_amount", "The treatment price must be more than 0.", status=422)
                elif c.status == "PENDING":
                    c.computed_amount = (treatment_amount * c.commission_percentage / Decimal("100")).quantize(Decimal("0.01"))
                    _audit(session, tenant_id, actor_id, "mt.commission.amount_set", "medical_tourism_referral_commission", c.id, {"currency": c.currency})
            await session.commit()
            ready = c.status == "PENDING" and c.computed_amount is not None and ref.outcome == (c.qualifying_outcome or ReferralOutcome.TREATMENT_COMPLETED)
            commission_id = c.id
            if c.status == "PENDING" and c.computed_amount is None and c.basis == ReferralCommissionBasis.PERCENTAGE:
                result["note"] = "Enter the treatment price to work out the commission and draft the invoice."
        if ready:
            done = await mt_commission.transition(session_factory, tenant_id, commission_id, "CONFIRMED", actor_id, "Confirmed automatically: the clinic's outcome is recorded with evidence.")
            result["status"], result["invoice_id"] = done.status, str(done.invoice_id) if done.invoice_id else None
        return result
    except ReferralError:
        raise
    except Exception as exc:  # noqa: BLE001 - never break recording an outcome
        logger.warning("mt_outcome_followup_failed", error=type(exc).__name__)
        return result


async def agreements_overview(session_factory, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Every clinic with what it has agreed to pay and how much work has gone to it: the commission side of Sales. A clinic with no terms is listed as such, never hidden."""
    from sqlalchemy import func

    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        providers = (await session.execute(select(Provider).where(Provider.tenant_id == tenant_id).order_by(Provider.name))).scalars().all()
        terms = {t.provider_id: t for t in (await session.execute(select(ProviderTerms).where(ProviderTerms.tenant_id == tenant_id))).scalars()}
        sent = dict((await session.execute(select(ProviderReferral.provider_id, func.count(ProviderReferral.id)).where(ProviderReferral.tenant_id == tenant_id,
                    ProviderReferral.sent_at.is_not(None)).group_by(ProviderReferral.provider_id))).all())
    rows = []
    for p in providers:
        t = terms.get(p.id)
        rows.append({"provider_id": str(p.id), "name": p.name, "country": p.country, "verification_status": p.verification_status, "referrals_sent": int(sent.get(p.id, 0)), "terms": terms_to_dict(t)})
    with_terms = sum(1 for r in rows if r["terms"]["set"] and r["terms"]["commission_basis"])
    return {"clinics": rows, "with_terms": with_terms, "without_terms": len(rows) - with_terms}
