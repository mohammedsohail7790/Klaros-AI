"""Applies the structured intake a Halla lead event may carry (procedure, destination, timeframe, preferred language, preferred contact time) to the lead's patient
extension, so provider matching has something to match on.

Data minimisation, enforced here as well as at the sender: nothing is stored unless the lead's newest consent evidence grants `store_personal_data`; the treatment
(`procedure`) additionally needs `store_medical_information` and is otherwise dropped. A destination that is a country is kept as its ISO code (what matching uses);
anything else is kept as short text. Values only ever fill or replace the matching column: an event with no intake changes nothing, and an empty value never erases a
stored one.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.crm import Lead
from app.models.medical_tourism import PatientLead, Procedure, ProcedureStatus

_COUNTRIES = {
    "india": "IN", "turkey": "TR", "turkiye": "TR", "türkiye": "TR", "thailand": "TH", "mexico": "MX", "malaysia": "MY", "united arab emirates": "AE", "uae": "AE", "dubai": "AE",
    "jordan": "JO", "germany": "DE", "spain": "ES", "poland": "PL", "hungary": "HU", "czech republic": "CZ", "czechia": "CZ", "south korea": "KR", "korea": "KR", "singapore": "SG",
    "vietnam": "VN", "egypt": "EG", "saudi arabia": "SA", "lebanon": "LB", "costa rica": "CR", "colombia": "CO", "brazil": "BR", "greece": "GR", "cyprus": "CY", "croatia": "HR",
    "lithuania": "LT", "latvia": "LV", "portugal": "PT", "tunisia": "TN", "morocco": "MA", "united kingdom": "GB", "uk": "GB", "united states": "US", "usa": "US", "qatar": "QA",
}


def country_code(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip().lower()
    if len(t) == 2 and t.isalpha():
        return t.upper()
    return _COUNTRIES.get(t)


async def apply_intake(session, tenant_id: uuid.UUID, lead: Lead, intake: dict[str, str], *, personal_ok: bool, medical_ok: bool) -> bool:
    """Returns True when anything was written. Runs inside the caller's session/transaction."""
    if not intake or not personal_ok:
        return False
    patient = (await session.execute(select(PatientLead).where(PatientLead.tenant_id == tenant_id, PatientLead.lead_id == lead.id))).scalar_one_or_none()
    if patient is None:
        patient = PatientLead(tenant_id=tenant_id, lead_id=lead.id)
        session.add(patient)
    changed = False
    for key in ("preferred_language", "preferred_contact_time", "timeframe"):
        if intake.get(key) and getattr(patient, key) != intake[key]:
            setattr(patient, key, intake[key])
            changed = True
    dest = intake.get("destination")
    if dest:
        code = country_code(dest)
        if code and patient.preferred_destination_country != code:
            patient.preferred_destination_country, patient.destination_text, changed = code, None, True
        elif not code and patient.destination_text != dest:
            patient.destination_text, changed = dest, True
    proc = intake.get("procedure")
    if proc and medical_ok and patient.procedure_text != proc:
        patient.procedure_text = proc
        changed = True
        match = None
        for p in (await session.execute(select(Procedure).where(Procedure.tenant_id == tenant_id, Procedure.status == ProcedureStatus.ACTIVE))).scalars():
            if p.name.strip().lower() == proc.strip().lower():
                match = p
                break
        if match is not None and patient.procedure_id is None:
            patient.procedure_id = match.id
    if changed:
        await session.flush()
    return changed
