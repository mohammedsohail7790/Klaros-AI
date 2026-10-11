"""Minimal Medical Tourism funnel telemetry: COUNTS ONLY, derived from what Klaros actually persisted, with each stage defined strictly so that an
unconfirmed request is never reported as a confirmed booking. No names, contact details, free text, ids or categories of an individual are returned.

Stage definitions (each is a different fact):
  received                a lead exists for the tenant.
  consent_*               what the NEWEST consent evidence per lead grants (`contact`, `store_personal_data`, `store_medical_information`);
                          `no_evidence` = no evidence at all; `frozen` = evidence exists but personal-data storage is not (or no longer) granted.
  qualified               qualification_status == QUALIFIED (a decision recorded by Halla or a person; a machine never sets it on a lead held for a person).
  held_for_person         qualification_status == REQUIRES_HUMAN (a safety category, Halla's needs_human_review, or a borderline score).
  appointments            counted by their OWN status. `confirmed` counts only CONFIRMED; TENTATIVE is a request, not a booking.
  consultations           the clinic-side consultation extension by status (SCHEDULED is not a confirmation).
  escalations             `lead.safety_escalated` (Klaros screen) and `halla.lead.escalated` events: a person was ASKED to act; it does not say they did.
  delivery                Halla webhook events by processing status, including events stuck in RECEIVED past their lease, and sends the consent guard blocked.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.models.crm import Appointment, AppointmentStatus, Lead, QualificationStatus
from app.models.event import Event
from app.models.halla_consent import HallaConsentEvidence
from app.models.integration import WebhookEvent, WebhookProcessingStatus
from app.models.medical_tourism import Consultation
from app.services import halla_consent


async def build_funnel(session_factory, tenant_id: uuid.UUID) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    lease = timedelta(seconds=get_settings().HALLA_WEBHOOK_LEASE_SECONDS)
    async with session_factory() as s:
        await set_tenant_context(s, tenant_id)
        leads = list((await s.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalars())
        evidence = list((await s.execute(select(HallaConsentEvidence).where(HallaConsentEvidence.tenant_id == tenant_id))).scalars())
        by_lead: dict[uuid.UUID, list] = defaultdict(list)
        by_halla: dict[str, list] = defaultdict(list)
        for r in evidence:
            if r.lead_id is not None:
                by_lead[r.lead_id].append(r)
            if r.halla_lead_id:
                by_halla[r.halla_lead_id].append(r)

        consent = Counter()
        for lead in leads:
            rows = {r.id: r for r in by_lead.get(lead.id, [])}
            rows.update({r.id: r for r in by_halla.get(lead.external_id or "", [])})
            state = halla_consent.derive_state(rows.values())
            if not state.has_evidence:
                consent["no_evidence"] += 1
                continue
            for scope in (halla_consent.CONTACT, halla_consent.STORE_PERSONAL, halla_consent.STORE_MEDICAL):
                if state.allows(scope):
                    consent[scope] += 1
            if not state.allows(halla_consent.STORE_PERSONAL):
                consent["frozen"] += 1

        qual = Counter(str(l.qualification_status) for l in leads)
        appts = Counter(str(a) for a in (await s.execute(select(Appointment.status).where(Appointment.tenant_id == tenant_id))).scalars())
        halla_confirmed = (await s.execute(select(func.count()).select_from(Appointment).where(
            Appointment.tenant_id == tenant_id, Appointment.external_provider == "halla", Appointment.status == AppointmentStatus.CONFIRMED))).scalar() or 0
        consults = Counter(str(c) for c in (await s.execute(select(Consultation.status).where(Consultation.tenant_id == tenant_id))).scalars())
        events = Counter(e for e in (await s.execute(select(Event.event_type).where(
            Event.tenant_id == tenant_id, Event.event_type.in_(("lead.safety_escalated", "halla.lead.escalated"))))).scalars())
        hooks = list((await s.execute(select(WebhookEvent.status, WebhookEvent.updated_at).where(WebhookEvent.tenant_id == tenant_id, WebhookEvent.provider == "halla"))).all())

    def aware(ts: datetime) -> datetime:
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)

    hook_counts = Counter(str(st) for st, _ in hooks)
    stuck = sum(1 for st, ts in hooks if str(st) == str(WebhookProcessingStatus.RECEIVED) and now - aware(ts) > lease)
    blocked = await _blocked_sends(session_factory, tenant_id)
    total = len(leads)
    return {
        "received": total,
        "consent": {
            "no_evidence": consent["no_evidence"],
            "contact_granted": consent[halla_consent.CONTACT],
            "store_personal_data_granted": consent[halla_consent.STORE_PERSONAL],
            "store_medical_information_granted": consent[halla_consent.STORE_MEDICAL],
            "frozen": consent["frozen"],
        },
        "qualification": {
            "qualified": qual[str(QualificationStatus.QUALIFIED)],
            "unqualified": qual[str(QualificationStatus.UNQUALIFIED)],
            "held_for_person": qual[str(QualificationStatus.REQUIRES_HUMAN)],
            "pending": qual[str(QualificationStatus.PENDING)],
        },
        "appointments": {
            "confirmed": appts[str(AppointmentStatus.CONFIRMED)],
            "confirmed_by_halla": halla_confirmed,
            "tentative_not_a_booking": appts[str(AppointmentStatus.TENTATIVE)],
            "cancelled": appts[str(AppointmentStatus.CANCELLED)],
            "completed": appts[str(AppointmentStatus.COMPLETED)],
            "no_show": appts[str(AppointmentStatus.NO_SHOW)],
        },
        "consultations": {k.lower(): v for k, v in consults.items()},
        "escalations_requested": {"klaros_safety_screen": events["lead.safety_escalated"], "halla": events["halla.lead.escalated"]},
        "delivery": {
            "halla_events_processed": hook_counts[str(WebhookProcessingStatus.PROCESSED)],
            "halla_events_failed": hook_counts[str(WebhookProcessingStatus.FAILED)],
            "halla_events_in_flight": hook_counts[str(WebhookProcessingStatus.RECEIVED)] - stuck,
            "halla_events_stuck_past_lease": stuck,
            "outbound_blocked_by_consent": blocked,
        },
        "drop_off": {
            "no_consent_evidence": consent["no_evidence"],
            "frozen_after_withdrawal_or_decline": consent["frozen"],
            "awaiting_qualification": qual[str(QualificationStatus.PENDING)],
            "held_for_person": qual[str(QualificationStatus.REQUIRES_HUMAN)],
        },
        "definitions": "Counts only. 'confirmed' = appointment status CONFIRMED (tentative is a request). 'escalations_requested' = a person was asked to act, "
                       "not that they did. Event transport on staging is in-memory: events do not survive a restart.",
    }


async def _blocked_sends(session_factory, tenant_id: uuid.UUID) -> int:
    from app.models.finance import CollectionAction, CollectionActionStatus
    from app.models.marketing import ActivityStatus, NurtureActivity, OutboundActivity
    from app.models.retention import RetentionActivity, RetentionActivityStatus, ReviewRequest, ReviewStatus

    total = 0
    async with session_factory() as s:
        await set_tenant_context(s, tenant_id)
        for model, status in ((CollectionAction, CollectionActionStatus.BLOCKED_CONSENT), (OutboundActivity, ActivityStatus.BLOCKED_CONSENT),
                              (NurtureActivity, ActivityStatus.BLOCKED_CONSENT), (RetentionActivity, RetentionActivityStatus.BLOCKED_CONSENT),
                              (ReviewRequest, ReviewStatus.BLOCKED_CONSENT)):
            total += (await s.execute(select(func.count()).select_from(model).where(model.tenant_id == tenant_id, model.status == status))).scalar() or 0
    return total
