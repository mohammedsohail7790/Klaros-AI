"""READ-ONLY evidence collector for the synthetic Medical Tourism end-to-end test.

    DATABASE_URL=<pilot DB, read-only role preferred> python -m scripts.mt_e2e_evidence --tenant <klaros tenant uuid> \
        [--halla-lead-id <id>] [--other-tenant <uuid>]

Prints ONE JSON document of ids, counts, scopes and timestamps. It never prints a name, phone, email, summary, secret or connection string, and it
issues only SELECTs (the PostgreSQL transaction is set READ ONLY). Use its output as the evidence the end-to-end plan asks for; see
docs/MEDICAL_TOURISM_PILOT_READINESS.md section E.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any


async def gather(tenant: uuid.UUID, *, halla_lead_id: str | None = None, other_tenant: uuid.UUID | None = None) -> dict[str, Any]:
    from sqlalchemy import func, select, text

    from app.db.session import async_session_maker, engine, set_tenant_context
    from app.models.crm import Lead
    from app.models.halla_consent import HallaConsentEvidence
    from app.models.integration import WebhookEvent
    from app.services import halla_consent

    out: dict[str, Any] = {"tenant": str(tenant), "halla_lead_id": halla_lead_id}
    async with async_session_maker() as s:
        if engine.dialect.name == "postgresql":
            await s.execute(text("SET TRANSACTION READ ONLY"))
        try:
            out["alembic_revision"] = (await s.execute(text("select version_num from alembic_version"))).scalar()
        except Exception:  # noqa: BLE001
            out["alembic_revision"] = None
        await set_tenant_context(s, tenant)
        leads = (await s.execute(select(Lead).where(Lead.tenant_id == tenant))).scalars().all()
        out["leads_total"] = len(leads)
        linked = [l for l in leads if halla_lead_id and l.external_id == halla_lead_id]
        out["leads_linked_to_halla_lead"] = [
            {"id": str(l.id), "source_detail": l.source_detail, "status": l.status, "qualification_status": l.qualification_status,
             "created_at": l.created_at.isoformat(), "personal_data_erased": (l.name or "").startswith("Erased")} for l in linked
        ]
        out["duplicate_leads_for_halla_lead"] = max(0, len(linked) - 1)
        rows = (await s.execute(select(HallaConsentEvidence).where(HallaConsentEvidence.tenant_id == tenant).order_by(HallaConsentEvidence.recorded_at))).scalars().all()
        out["consent_evidence"] = [
            {"row": str(r.id)[:8], "source": r.source, "event_type": r.event_type, "source_event_id": r.halla_event_id, "granted": r.granted, "scopes": r.scopes,
             "method": r.method, "wording_version": r.wording_version, "recorded_at": r.recorded_at.isoformat(), "lead_linked": r.lead_id is not None}
            for r in rows if not halla_lead_id or r.halla_lead_id in (halla_lead_id, None)
        ]
        out["duplicate_evidence_event_ids"] = int((await s.execute(
            select(func.count()).select_from(select(HallaConsentEvidence.halla_event_id).where(HallaConsentEvidence.tenant_id == tenant).group_by(HallaConsentEvidence.halla_event_id).having(func.count() > 1).subquery())
        )).scalar() or 0)
        if halla_lead_id or linked:
            state = await halla_consent.state_for(s, tenant, halla_lead_id=halla_lead_id, lead_id=linked[0].id if linked else None)
            out["derived_consent_now"] = {"evidence_recorded": state.has_evidence, "granted_scopes": sorted(state.granted)}
        events = (await s.execute(select(WebhookEvent).where(WebhookEvent.tenant_id == tenant).order_by(WebhookEvent.created_at))).scalars().all()
        out["webhook_events"] = [{"id": e.external_event_id.split(":")[-1], "type": e.event_type, "status": str(e.status)} for e in events if e.provider == "halla"]
        out["webhook_status_counts"] = {k: sum(1 for e in events if str(e.status) == k) for k in {str(e.status) for e in events}}
    if other_tenant is not None:
        async with async_session_maker() as s:
            await set_tenant_context(s, other_tenant)
            other_leads = (await s.execute(select(func.count()).select_from(Lead).where(Lead.tenant_id == other_tenant, Lead.external_id == halla_lead_id))).scalar() if halla_lead_id else None
            other_ev = (await s.execute(select(func.count()).select_from(HallaConsentEvidence).where(HallaConsentEvidence.tenant_id == other_tenant, HallaConsentEvidence.halla_lead_id == halla_lead_id))).scalar() if halla_lead_id else None
        out["isolation"] = {"other_tenant": str(other_tenant), "leads_with_same_halla_lead_id": other_leads, "evidence_with_same_halla_lead_id": other_ev, "expected": 0}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant", required=True, type=uuid.UUID)
    ap.add_argument("--halla-lead-id")
    ap.add_argument("--other-tenant", type=uuid.UUID)
    a = ap.parse_args(argv)
    result = asyncio.run(gather(a.tenant, halla_lead_id=a.halla_lead_id, other_tenant=a.other_tenant))
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
