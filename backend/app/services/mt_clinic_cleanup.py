"""Tidy the clinic list: find duplicate entries and obvious junk, and suspend the ones the owner picks.

Clinic sourcing and imports leave several entries for one real clinic ("Apollo Hospital", "Apollo Hospitals") and the odd list page or test row, which makes outreach and
matching run on a dirty list. This never deletes and never decides alone: `find` previews what looks redundant and which entry would be kept; `apply` makes the entries
the owner chose INACTIVE (reversible), and refuses any entry that is not on the current suggestion list, is the entry being kept, or still has a live referral.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.medical_tourism import Provider, ProviderStatus
from app.models.medical_tourism_referral import ProviderReferral
from app.models.mt_revenue import ProviderTerms

_NOISE = {"hospital", "hospitals", "clinic", "clinics", "center", "centre", "centers", "centres", "international", "medical", "co", "ltd", "limited", "public", "company", "pcl", "the", "group"}
_JUNK = re.compile(r"(delete me|\btest\b|^top[\s-]*\d+|\bbest\b.*\b(clinics|hospitals|surgeons)\b|^\s*list of\b)", re.IGNORECASE)
_LIVE_REFERRAL = ("DRAFT", "APPROVED", "SENT", "ACCEPTED")


class CleanupError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


def normalize(name: str) -> str:
    """A key that is the same for 'Apollo Hospital', 'Apollo Hospitals' and 'Apollo Hospital (Delhi)': lowercase, no brackets, no punctuation, no generic words."""
    text = re.sub(r"\([^)]*\)", " ", (name or "").lower())
    words = [w for w in re.sub(r"[^a-z0-9\s]", " ", text).split() if w not in _NOISE]
    return " ".join(words)


def is_junk(name: str) -> bool:
    return bool(_JUNK.search(name or ""))


def _row(p: Provider, *, has_terms: bool, live_referrals: int) -> dict[str, Any]:
    return {"id": str(p.id), "name": p.name, "country": p.country, "city": p.city or None, "verified": p.verification_status == "VERIFIED", "has_terms": has_terms,
            "has_email": bool(p.contact_email), "live_referrals": live_referrals}


def _keep_score(p: Provider, has_terms: bool, live_referrals: int) -> tuple:
    """Which entry to keep: one with a live referral, then verified, then with terms, then with an email, then the shortest name, then the oldest."""
    return (live_referrals > 0, p.verification_status == "VERIFIED", has_terms, bool(p.contact_email), -len(p.name or ""), -(p.created_at.timestamp() if p.created_at else 0))


async def find(session, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Preview: groups of entries that look like one clinic (with the one to keep) and junk entries. Active entries only."""
    providers = list((await session.execute(select(Provider).where(Provider.tenant_id == tenant_id, Provider.status == ProviderStatus.ACTIVE))).scalars())
    terms = {t for (t,) in (await session.execute(select(ProviderTerms.provider_id).where(ProviderTerms.tenant_id == tenant_id))).all()}
    live: dict[uuid.UUID, int] = defaultdict(int)
    for pid, state in (await session.execute(select(ProviderReferral.provider_id, ProviderReferral.status).where(ProviderReferral.tenant_id == tenant_id))).all():
        if state in _LIVE_REFERRAL:
            live[pid] += 1
    groups: dict[tuple[str, str], list[Provider]] = defaultdict(list)
    junk: list[dict[str, Any]] = []
    for p in providers:
        if is_junk(p.name):
            junk.append(_row(p, has_terms=p.id in terms, live_referrals=live[p.id]))
            continue
        key = normalize(p.name)
        if key:
            groups[(p.country, key)].append(p)
    out_groups = []
    for members in groups.values():
        if len(members) < 2:
            continue
        ranked = sorted(members, key=lambda p: _keep_score(p, p.id in terms, live[p.id]), reverse=True)
        out_groups.append({"keep": _row(ranked[0], has_terms=ranked[0].id in terms, live_referrals=live[ranked[0].id]),
                           "redundant": [_row(p, has_terms=p.id in terms, live_referrals=live[p.id]) for p in ranked[1:]]})
    out_groups.sort(key=lambda g: g["keep"]["name"].lower())
    suggested = [r["id"] for g in out_groups for r in g["redundant"] if r["live_referrals"] == 0] + [j["id"] for j in junk if j["live_referrals"] == 0]
    return {"groups": out_groups, "junk": junk, "suggested_ids": suggested, "active_clinics": len(providers)}


async def apply(session_factory, tenant_id: uuid.UUID, user_id: uuid.UUID, provider_ids: list[uuid.UUID]) -> dict[str, Any]:
    """Make the chosen entries INACTIVE. Only entries that `find` currently suggests, with no live referral, can be suspended; anything else is refused."""
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        suggested = set((await find(session, tenant_id))["suggested_ids"])
        wanted = {str(i) for i in provider_ids}
        if not wanted:
            raise CleanupError("nothing_selected", 422)
        refused = sorted(wanted - suggested)
        if refused:
            raise CleanupError("not_suggested", 409)
        rows = list((await session.execute(select(Provider).where(Provider.tenant_id == tenant_id, Provider.id.in_([uuid.UUID(i) for i in wanted])))).scalars())
        for p in rows:
            p.status = ProviderStatus.INACTIVE
            session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=user_id, action="mt.provider.suspended_duplicate", entity_type="medical_tourism_provider",
                                 entity_id=p.id, input_summary={"name": p.name}, result="success"))
        await session.commit()
        return {"suspended": len(rows)}


async def restore(session_factory, tenant_id: uuid.UUID, user_id: uuid.UUID, provider_ids: list[uuid.UUID]) -> dict[str, Any]:
    """Turn suspended entries back on (ACTIVE). Only entries that are currently INACTIVE are touched."""
    if not provider_ids:
        raise CleanupError("nothing_selected", 422)
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        rows = list((await session.execute(select(Provider).where(Provider.tenant_id == tenant_id, Provider.id.in_(provider_ids), Provider.status == ProviderStatus.INACTIVE))).scalars())
        for p in rows:
            p.status = ProviderStatus.ACTIVE
            session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=user_id, action="mt.provider.restored", entity_type="medical_tourism_provider",
                                 entity_id=p.id, input_summary={"name": p.name}, result="success"))
        await session.commit()
        return {"restored": len(rows)}
