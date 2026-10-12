"""One-form setup for a Medical Tourism business.

The owner describes the business ONCE (name, patient markets, destinations, procedures, business model). Klaros then:
  * switches the Medical Tourism module on for the organisation (the existing, idempotent module-enable);
  * creates the procedure catalogue from the list (idempotent: running it again never duplicates a procedure);
  * saves the profile (one row per organisation; running it again updates it);
  * returns a LAUNCH CHECKLIST computed from what is really recorded (providers, verified providers, offerings, the Halla connection, approved consent wording) plus a
    few owner confirmations only the owner can give (signed clinic agreements, escalation contact, reviewed claims, domain), and a set of DRAFTS (intake questions,
    qualification rule, pipeline, consent settings, marketing topics, commercial notes) that are clearly marked as drafts needing the owner's approval.

What it never does: invent a clinic, a partnership, a price, a commission or a medical claim; publish anything; contact anyone. Drafts name no provider. Anything that
needs real information or approval is listed as such; nothing is reported done that Klaros cannot see.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.integration import ConnectionStatus, IntegrationConnection
from app.models.medical_tourism import Procedure, ProcedureStatus, Provider, ProviderProcedure, ProviderProcedureStatus
from app.models.medical_tourism_referral import MedicalTourismSetup
from app.services.medical_tourism_service import CreateProcedureInput, MedicalTourismService
from app.services.mt_intake import country_code, country_name
from app.services.mt_referrals import ReferralError, provider_is_referable
from app.services.vertical_extension_service import VerticalExtensionAlreadyExistsError, VerticalExtensionService

MODELS = {"referral": "Provider referral partnerships", "coordination": "Patient coordination services", "both": "Referral and coordination"}
CONFIRMATIONS = {
    "agreements_signed": "I hold signed referral agreements with the clinics I work with.",
    "consent_wording_approved": "The consent wording shown to patients has been approved.",
    "escalation_contact_verified": "A human escalation contact for urgent or clinical questions is in place and verified.",
    "claims_reviewed": "Every factual and medical claim on the website and in marketing has been reviewed.",
    "domain_owned": "I own the domain the website will be published on.",
}
_NAME_RE = re.compile(r"[\s]+")


def _clean(value: Any, lo: int, hi: int, code: str) -> str:
    text = _NAME_RE.sub(" ", str(value or "")).strip()
    if not lo <= len(text) <= hi:
        raise ReferralError(code, status=422)
    return text


def _countries(values: Any, code: str) -> list[str]:
    if not isinstance(values, list) or not 1 <= len(values) <= 20:
        raise ReferralError(code, "Add at least one country (up to 20).", status=422)
    out: list[str] = []
    for v in values:
        iso = country_code(str(v))
        if iso is None:
            raise ReferralError(code, f"'{str(v)[:40]}' is not a country Klaros recognises; use a name or a two-letter code.", status=422)
        if iso not in out:
            out.append(iso)
    return out


def validate(body: dict[str, Any]) -> dict[str, Any]:
    name = _clean(body.get("business_name"), 2, 120, "business_name_required")
    model = str(body.get("business_model") or "")
    if model not in MODELS:
        raise ReferralError("business_model_required", "Choose referral, coordination or both.", status=422)
    procs = body.get("procedures")
    if not isinstance(procs, list) or not 1 <= len(procs) <= 20:
        raise ReferralError("procedures_required", "Add at least one procedure (up to 20).", status=422)
    seen: dict[str, str] = {}
    for p in procs:
        text = _clean(p, 2, 80, "procedure_invalid")
        seen.setdefault(text.lower(), text)
    return {"business_name": name, "business_model": model, "patient_markets": _countries(body.get("patient_markets"), "patient_markets_required"),
            "destinations": _countries(body.get("destinations"), "destinations_required"), "procedures": list(seen.values())}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


MODULE_KEY = "medical_tourism"


async def apply_setup(session_factory, tenant_id: uuid.UUID, user_id: uuid.UUID, body: dict[str, Any]) -> dict[str, Any]:
    data = validate(body)
    verticals = VerticalExtensionService(session_factory)
    try:
        await verticals.enable_for_organization(tenant_id, "medical_tourism", enabled_by=user_id)
    except VerticalExtensionAlreadyExistsError:
        pass  # idempotent
    service = MedicalTourismService(session_factory)
    for name in data["procedures"]:
        await service.create_procedure(tenant_id, CreateProcedureInput(name=name, category="General", idempotency_key=f"setup-{_slug(name)}"))
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        row = (await session.execute(select(MedicalTourismSetup).where(MedicalTourismSetup.tenant_id == tenant_id))).scalar_one_or_none()
        if row is None:
            row = MedicalTourismSetup(tenant_id=tenant_id, created_by=user_id, confirmations={}, **{k: v for k, v in data.items() if k != "procedures"})
            session.add(row)
        else:
            for k, v in data.items():
                if k != "procedures":
                    setattr(row, k, v)
        session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=user_id, action="mt.setup.applied", entity_type="medical_tourism_setup", entity_id=row.id,
                             input_summary={"procedures": len(data["procedures"]), "markets": data["patient_markets"], "destinations": data["destinations"], "model": data["business_model"]}, result="success"))
        await session.commit()
    await _seed_blueprint(session_factory, tenant_id, user_id, data)
    return await get_setup(session_factory, tenant_id)


def _join(names: list[str]) -> str:
    return ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1] if names else ""


async def _seed_blueprint(session_factory, tenant_id: uuid.UUID, user_id: uuid.UUID, data: dict[str, Any]) -> None:
    """Record the owner's setup answers as their Business Blueprint, using ONLY what they stated, and activate it, so the website builder, recommendations and
    the business map work for a business that used the one-form setup. An existing Blueprint (one the owner built or edited) is never touched."""
    from app.data.vertical_extension_seed import SEED_VERTICALS
    from app.services.business_blueprint_service import BlueprintActivationError, BusinessBlueprintService
    from app.models.business_blueprint import BlueprintSectionKey, BlueprintStatus

    blueprints = BusinessBlueprintService(session_factory)
    if await blueprints.get_active(tenant_id) is not None:
        return
    draft = await blueprints.get_or_create_draft(tenant_id, created_by=user_id)
    if draft.status != BlueprintStatus.DRAFT:
        return
    # A draft someone has already started filling in (the owner, or a Discovery conversation) is theirs: never overwrite it.
    if any(sec.status != "EMPTY" for sec in await blueprints.list_sections(tenant_id, draft.id)):
        return
    markets = _join([country_name(c) for c in data["patient_markets"]])
    destinations = _join([country_name(c) for c in data["destinations"]])
    module = {v["key"]: v for v in SEED_VERTICALS}.get(MODULE_KEY, {})  # looked up by key, never branched on
    capabilities = list(module.get("capabilities", []))
    sections = {
        BlueprintSectionKey.IDENTITY: {"business_name": data["business_name"], "description": f"{data['business_name']} arranges treatment abroad for patients in {markets}, in {destinations}."},
        BlueprintSectionKey.INDUSTRY: {"industry": "Medical tourism"},
        BlueprintSectionKey.BUSINESS_MODEL: {"model": data["business_model"]},
        BlueprintSectionKey.REQUIRED_CAPABILITIES: {"capabilities": capabilities},
        BlueprintSectionKey.CUSTOMERS: {"target_customer": f"Patients in {markets} who want treatment in {destinations}."},
        BlueprintSectionKey.PRODUCTS_SERVICES: {"services": list(data["procedures"])},
        # Places the owner named, as short statements the website's trust bar can show as they are.
        BlueprintSectionKey.GEOGRAPHY: {"highlights": [f"Patients from {markets}", f"Treatment in {destinations}"]},
        # How the enquiry is handled. These describe what Klaros itself enforces (consent before anything is kept, nothing shared with a clinic without
        # the patient's approval, the clinic confirms the consultation), so the website can state them without promising prices, times or outcomes.
        BlueprintSectionKey.CUSTOMER_JOURNEY: {"steps": [
            {"title": "Tell us what you need", "description": "Send an enquiry through this website. Your details are only kept once you agree to how they will be used."},
            {"title": "We read it and reply", "description": "A coordinator looks at your enquiry and answers you directly."},
            {"title": "You decide about a clinic", "description": "Nothing about you is shared with a clinic until you say yes."},
            {"title": "The clinic confirms with you", "description": "A consultation is only confirmed once the clinic has agreed it with you."},
        ]},
    }
    for key, section in sections.items():
        await blueprints.update_section(tenant_id, draft.id, section_key=key.value, data=section, updated_by=user_id)
    try:
        await blueprints.activate(tenant_id, draft.id, activated_by=user_id)
    except BlueprintActivationError:
        pass  # leave it as a complete draft: the owner can still confirm it in the Blueprint view


async def confirm(session_factory, tenant_id: uuid.UUID, user_id: uuid.UUID, key: str, confirmed: bool) -> dict[str, Any]:
    if key not in CONFIRMATIONS:
        raise ReferralError("unknown_confirmation", status=422)
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        row = (await session.execute(select(MedicalTourismSetup).where(MedicalTourismSetup.tenant_id == tenant_id))).scalar_one_or_none()
        if row is None:
            raise ReferralError("not_set_up", "Set up the business first.", status=409)
        current = dict(row.confirmations or {})
        if confirmed:
            current[key] = {"by": str(user_id), "at": datetime.now(timezone.utc).isoformat()}
        else:
            current.pop(key, None)
        row.confirmations = current
        session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=user_id, action="mt.setup.confirmation", entity_type="medical_tourism_setup", entity_id=row.id,
                             input_summary={"key": key, "confirmed": confirmed}, result="success"))
        await session.commit()
    return await get_setup(session_factory, tenant_id)


def _item(key: str, label: str, status: str, why: str, route: str | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "status": status, "why": why, "route": route}


async def get_setup(session_factory, tenant_id: uuid.UUID) -> dict[str, Any]:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        row = (await session.execute(select(MedicalTourismSetup).where(MedicalTourismSetup.tenant_id == tenant_id))).scalar_one_or_none()
        if row is None:
            return {"configured": False}
        procedures = (await session.execute(select(Procedure).where(Procedure.tenant_id == tenant_id, Procedure.status == ProcedureStatus.ACTIVE))).scalars().all()
        providers = (await session.execute(select(Provider).where(Provider.tenant_id == tenant_id))).scalars().all()
        referable = [p for p in providers if await provider_is_referable(session, tenant_id, p)]
        offerings = (await session.execute(select(func.count()).select_from(ProviderProcedure).where(ProviderProcedure.tenant_id == tenant_id, ProviderProcedure.status == ProviderProcedureStatus.ACTIVE))).scalar() or 0
        halla = (await session.execute(select(IntegrationConnection).where(IntegrationConnection.tenant_id == tenant_id, IntegrationConnection.provider == "halla"))).scalar_one_or_none()
        confirmed = set((row.confirmations or {}).keys())
        names = [p.name for p in procedures]
    from app.services.consent_gate import approved_wording_versions

    wording = await approved_wording_versions(session_factory, tenant_id)
    items = [
        _item("profile", "Business profile", "DONE", "Saved from your setup answers."),
        _item("procedures", "Procedure catalogue", "DONE" if procedures else "TODO", f"{len(procedures)} procedure(s) in the catalogue." if procedures else "Add the procedures you offer.", "/medical-tourism/procedures"),
        _item("providers", "Clinics you work with", "DONE" if providers else "TODO", f"{len(providers)} clinic(s) added." if providers else "Add the clinics you have real partnerships with. Klaros never invents one.", "/medical-tourism/providers"),
        _item("verified_provider", "At least one verified clinic", "DONE" if referable else "NEEDS_INFORMATION", f"{len(referable)} clinic(s) verified." if referable else "Record a clinic's credentials and mark it verified. Only a verified clinic can be matched or referred to.", "/medical-tourism/providers"),
        _item("offerings", "What each clinic offers", "DONE" if offerings else "TODO", f"{offerings} offering(s) recorded." if offerings else "Record which procedures each clinic really offers (and any price it has approved).", "/medical-tourism/procedures"),
        _item("halla", "AI receptionist connected", "DONE" if halla is not None and halla.status == ConnectionStatus.CONNECTED else "TODO",
              "Connected." if halla is not None and halla.status == ConnectionStatus.CONNECTED else "Connect Halla AI so calls become leads.", "/workforce"),
        _item("consent_wording", "Consent wording approved for the website form", "DONE" if wording else "NEEDS_APPROVAL", "Approved wording is on file." if wording else "Until wording is approved the public enquiry form stays closed. Write it below, then approve it.", "/setup#consent-wording"),
    ]
    for key, text in CONFIRMATIONS.items():
        items.append(_item(key, text, "DONE" if key in confirmed else "NEEDS_APPROVAL", "Confirmed by the owner." if key in confirmed else "Only you can confirm this; Klaros cannot see it.", None))
    open_items = [i for i in items if i["status"] != "DONE"]
    return {
        "configured": True,
        "profile": {"business_name": row.business_name, "patient_markets": row.patient_markets, "destinations": row.destinations, "business_model": row.business_model,
                    "business_model_label": MODELS.get(row.business_model), "procedures": names},
        "checklist": items,
        "ready_to_launch": not open_items,
        "open_items": len(open_items),
        "drafts": drafts(row, names),
        "note": "Everything under 'drafts' is a draft for your approval. Klaros does not publish, contact patients or claim partnerships on its own.",
    }


def drafts(row: MedicalTourismSetup, procedures: list[str]) -> dict[str, Any]:
    destinations = list(row.destinations or [])
    d = {"status": "DRAFT", "needs": "owner approval"}
    return {
        "intake_questions": {**d, "items": ["What treatment are you interested in?", "Which country would you like to travel to?", "When are you thinking of travelling?",
                                              "Which language would you prefer?", "When is the best time to contact you?"],
                              "note": "Administrative questions only. The assistant never asks about medical history, symptoms or diagnoses."},
        "qualification_rule": {**d, "text": "An enquiry is complete when the patient's consent, name, a way to reach them and the treatment of interest are on record. Anything else is held for a person. Klaros never decides medical suitability."},
        "pipeline": {**d, "stages": ["New enquiry", "Consent", "Qualification", "Provider options", "Patient approval", "Referred", "Clinic response", "Outcome"]},
        "consent_settings": {**d, "scopes": ["contact", "store_personal_data", "store_medical_information", "share_with_selected_provider"],
                             "note": "Sharing with a clinic needs the patient's own approval for that clinic, recorded by a person."},
        "marketing_topics": {**d, "items": [f"{p}: what to ask a clinic before you travel ({dest})" for p in procedures[:6] for dest in destinations[:3]],
                              "note": "Topic ideas only. No claim about any clinic, price or result is made; every piece needs review before it is published."},
        "commercial_model": {**d, "text": MODELS.get(row.business_model, ""), "note": "Commission rates and terms must come from signed agreements with each clinic; none are assumed here."},
    }
