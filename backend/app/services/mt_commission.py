"""Medical Tourism commission tracking: a commission is POTENTIAL until the clinic's reported outcome is on record, and only moves through real, ordered steps.

  PENDING     potential: the referral exists, nothing has been earned (never counted as revenue)
  CONFIRMED   earned: allowed ONLY when the linked referral carries the outcome the agreement names (default TREATMENT_COMPLETED), reported by the clinic with evidence
  INVOICED    an invoice was issued to the clinic
  PAID        payment received (only after INVOICED)
  DISPUTED    the clinic or the business disputes it; can return to CONFIRMED/INVOICED when settled
  CANCELLED   will not be earned (terminal)

`revenue_summary` reports each status separately, per currency. It never presents PENDING as earned and never mixes currencies.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.db.session import set_tenant_context
from app.models.medical_tourism import Provider, ReferralCommission, ReferralCommissionBasis, ReferralCommissionStatus as S
from app.models.medical_tourism_referral import ProviderReferral, ReferralOutcome, ReferralStatus
from app.services.mt_referrals import ReferralError, _audit

TRANSITIONS: dict[str, set[str]] = {
    S.PENDING: {S.CONFIRMED, S.CANCELLED},
    S.CONFIRMED: {S.INVOICED, S.DISPUTED, S.CANCELLED},
    S.INVOICED: {S.PAID, S.DISPUTED, S.CANCELLED},
    S.DISPUTED: {S.CONFIRMED, S.INVOICED, S.CANCELLED},
    S.PAID: {S.DISPUTED},
    S.CANCELLED: set(),
}


async def create_for_referral(session_factory, tenant_id: uuid.UUID, referral_id: uuid.UUID, actor_id: uuid.UUID, *, currency: str, basis: str = ReferralCommissionBasis.PERCENTAGE,
                              commission_percentage: Decimal | None = None, flat_amount: Decimal | None = None, reference_amount: Decimal | None = None,
                              qualifying_outcome: str = ReferralOutcome.TREATMENT_COMPLETED) -> ReferralCommission:
    if not currency or len(currency) != 3:
        raise ReferralError("currency_required", "Use a 3-letter currency code.", status=422)
    if qualifying_outcome not in {o.value for o in ReferralOutcome}:
        raise ReferralError("unknown_outcome", status=422)
    if basis == ReferralCommissionBasis.PERCENTAGE and commission_percentage is None:
        raise ReferralError("commission_percentage_required", status=422)
    if basis == ReferralCommissionBasis.FLAT and flat_amount is None:
        raise ReferralError("flat_amount_required", status=422)
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        referral = (await session.execute(select(ProviderReferral).where(ProviderReferral.id == referral_id, ProviderReferral.tenant_id == tenant_id))).scalar_one_or_none()
        if referral is None:
            raise ReferralError("not_found", status=404)
        if referral.status == ReferralStatus.CANCELLED:
            raise ReferralError("referral_cancelled")
        existing = (await session.execute(select(ReferralCommission).where(ReferralCommission.tenant_id == tenant_id, ReferralCommission.provider_referral_id == referral_id))).scalar_one_or_none()
        if existing is not None:
            raise ReferralError("commission_exists", "This referral already has a commission.")
        computed = None
        if basis == ReferralCommissionBasis.FLAT:
            computed = flat_amount
        elif reference_amount is not None:
            computed = (reference_amount * commission_percentage / Decimal("100")).quantize(Decimal("0.01"))
        c = ReferralCommission(tenant_id=tenant_id, referral_id=None, provider_referral_id=referral_id, provider_id=referral.provider_id, basis=basis, commission_percentage=commission_percentage,
                               flat_amount=flat_amount, computed_amount=computed, currency=currency.upper(), status=S.PENDING, qualifying_outcome=qualifying_outcome)
        session.add(c)
        await session.flush()
        _audit(session, tenant_id, actor_id, "mt.commission.created", "medical_tourism_referral_commission", c.id, {"currency": c.currency, "basis": basis, "referral_id": str(referral_id)})
        await session.commit()
        await session.refresh(c)
        return c


async def transition(session_factory, tenant_id: uuid.UUID, commission_id: uuid.UUID, new_status: str, actor_id: uuid.UUID, note: str | None = None) -> ReferralCommission:
    if new_status not in {s.value for s in S}:
        raise ReferralError("unknown_status", status=422)
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        c = (await session.execute(select(ReferralCommission).where(ReferralCommission.id == commission_id, ReferralCommission.tenant_id == tenant_id))).scalar_one_or_none()
        if c is None:
            raise ReferralError("not_found", status=404)
        if c.status == new_status:
            return c  # idempotent
        if new_status not in TRANSITIONS.get(c.status, set()):
            raise ReferralError("invalid_transition", f"A {c.status.lower()} commission cannot become {new_status.lower()}.")
        if new_status == S.CONFIRMED and c.provider_referral_id is not None:
            ref = await session.get(ProviderReferral, c.provider_referral_id)
            wanted = c.qualifying_outcome or ReferralOutcome.TREATMENT_COMPLETED
            if ref is None or ref.outcome != wanted or not ref.outcome_evidence:
                raise ReferralError("no_outcome_evidence", f"A commission is earned only when the clinic has reported {wanted.lower().replace('_', ' ')}, with evidence.")
        now = datetime.now(timezone.utc)
        if new_status == S.INVOICED:
            c.invoiced_at = now
        elif new_status == S.PAID:
            c.paid_at = now
        c.status = new_status
        if note:
            c.status_note = note.strip()[:500]
        _audit(session, tenant_id, actor_id, f"mt.commission.{new_status.lower()}", "medical_tourism_referral_commission", c.id, {"note": (note or "")[:200]})
        await session.commit()
        await session.refresh(c)
        return c


async def revenue_summary(session_factory, tenant_id: uuid.UUID) -> dict[str, Any]:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        rows = (await session.execute(select(ReferralCommission).where(ReferralCommission.tenant_id == tenant_id))).scalars().all()
    out: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: {s.value: {"count": 0, "amount": "0.00"} for s in S})
    for c in rows:
        slot = out[c.currency][c.status]
        slot["count"] += 1
        slot["amount"] = str(Decimal(slot["amount"]) + (c.computed_amount or Decimal("0")))
    return {
        "by_currency": dict(out),
        "definitions": "Potential is not revenue. A commission counts as earned only once the clinic has reported the agreed outcome, with evidence. Currencies are never mixed.",
    }


def commission_to_dict(c: ReferralCommission) -> dict[str, Any]:
    return {
        "id": str(c.id), "referral_id": str(c.referral_id) if c.referral_id else None, "provider_referral_id": str(c.provider_referral_id) if c.provider_referral_id else None,
        "provider_id": str(c.provider_id) if c.provider_id else None, "basis": c.basis, "commission_percentage": str(c.commission_percentage) if c.commission_percentage is not None else None,
        "flat_amount": str(c.flat_amount) if c.flat_amount is not None else None, "computed_amount": str(c.computed_amount) if c.computed_amount is not None else None,
        "currency": c.currency, "status": c.status, "qualifying_outcome": c.qualifying_outcome,
        "invoiced_at": c.invoiced_at.isoformat() if c.invoiced_at else None, "paid_at": c.paid_at.isoformat() if c.paid_at else None, "status_note": c.status_note,
    }


async def commission_desk(session_factory, tenant_id: uuid.UUID) -> dict[str, Any]:
    """What the owner needs on one screen: every commission with the patient (by lead id), the clinic and the outcome it rests on, and the referrals whose clinic has
    reported an outcome but which have no commission yet. Patient names are NOT resolved here: the caller shows them only where consent allows."""
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        commissions = list((await session.execute(select(ReferralCommission).where(ReferralCommission.tenant_id == tenant_id, ReferralCommission.provider_referral_id.is_not(None)).order_by(ReferralCommission.created_at.desc()))).scalars())
        referrals = {r.id: r for r in (await session.execute(select(ProviderReferral).where(ProviderReferral.tenant_id == tenant_id))).scalars()}
        providers = {p.id: p.name for p in (await session.execute(select(Provider).where(Provider.tenant_id == tenant_id))).scalars()}
    have = {c.provider_referral_id for c in commissions}
    rows = []
    for c in commissions:
        r = referrals.get(c.provider_referral_id)
        d = commission_to_dict(c)
        d.update({"lead_id": str(r.lead_id) if r else None, "provider_name": providers.get(c.provider_id), "outcome": r.outcome if r else None, "outcome_evidence": r.outcome_evidence if r else None})
        rows.append(d)
    awaiting = [
        {"referral_id": str(r.id), "lead_id": str(r.lead_id), "provider_name": providers.get(r.provider_id), "outcome": r.outcome, "outcome_evidence": r.outcome_evidence}
        for r in referrals.values() if r.outcome and r.id not in have and r.status != ReferralStatus.CANCELLED
    ]
    return {"commissions": rows, "awaiting": awaiting}
