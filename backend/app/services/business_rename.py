"""Changing the business's name, everywhere it was recorded.

The name was captured in several places at setup (the organisation, the Medical Tourism profile, the Business Blueprint, the content topics) and flows from there into emails, the website and the
AI team's wording. A rename updates each of them together so the old name does not linger in some. What the owner has edited by hand is left alone: the content-topics file is regenerated only
if it is still exactly what setup wrote, and the Blueprint is versioned (the earlier version is kept), never overwritten.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.medical_tourism_referral import MedicalTourismSetup
from app.models.organization import Organization


class RenameError(ValueError):
    pass


def clean_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", (raw or "")).strip()
    if not 2 <= len(name) <= 120 or any(ord(c) < 32 for c in name) or "<" in name or ">" in name:
        raise RenameError("Use a business name of 2 to 120 characters, with no angle brackets.")
    return name


async def rename_business(session_factory, tenant_id: uuid.UUID, user_id: uuid.UUID, raw_name: str) -> dict[str, Any]:
    from app.models.business_blueprint import BlueprintSectionKey
    from app.services.business_blueprint_service import BusinessBlueprintService
    from app.services.knowledge_service import KnowledgeService
    from app.services.mt_content_topics import TOPICS_PATH, build_topics, parse_topics

    new = clean_name(raw_name)
    changed: dict[str, Any] = {"name": new, "organization": False, "medical_tourism_profile": False, "blueprint": False, "content_topics": False}
    profile: tuple[str, str] | None = None
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        org = await session.get(Organization, tenant_id)
        if org is None:
            raise RenameError("Business not found.")
        old = org.name
        if old == new:
            return {**changed, "unchanged": True}
        org.name, changed["organization"] = new, True
        mt = (await session.execute(select(MedicalTourismSetup).where(MedicalTourismSetup.tenant_id == tenant_id))).scalar_one_or_none()
        if mt is not None:
            profile = (mt.business_name, mt.business_model)
            mt.business_name, changed["medical_tourism_profile"] = new, True
        session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=user_id, action="business.renamed", entity_type="organization", entity_id=tenant_id,
                             input_summary={"from": old, "to": new}, result="success"))
        await session.commit()
    names_to_replace = {old} | ({profile[0]} if profile else set())

    blueprints = BusinessBlueprintService(session_factory)
    bp = await blueprints.get_active(tenant_id)
    if bp is not None:
        sections = {s.section_key: s for s in await blueprints.list_sections(tenant_id, bp.id)}
        ident = sections.get(BlueprintSectionKey.IDENTITY.value)
        if ident is not None and (ident.data or {}).get("business_name"):
            data = dict(ident.data)
            data["business_name"] = new
            if isinstance(data.get("description"), str):
                for o in names_to_replace:
                    data["description"] = data["description"].replace(o, new)
            await blueprints.update_section(tenant_id, bp.id, section_key=BlueprintSectionKey.IDENTITY.value, data=data, updated_by=user_id)  # versioned: the earlier version is kept
            changed["blueprint"] = True

    if profile is not None:
        knowledge = KnowledgeService(session_factory)
        existing = await knowledge.get_content(tenant_id, TOPICS_PATH)
        async with session_factory() as session:
            await set_tenant_context(session, tenant_id)
            from app.models.medical_tourism import Procedure

            procs = [p.name for p in (await session.execute(select(Procedure).where(Procedure.tenant_id == tenant_id).order_by(Procedure.created_at))).scalars()]
        # "Unedited" means every section is exactly what setup wrote for the old name, in any order; anything the owner changed or added is theirs and is left alone.
        if existing is not None and any(dict(parse_topics(existing)) == dict(parse_topics(build_topics(o, profile[1], procs))) for o in names_to_replace):
            await knowledge.set_file(tenant_id, TOPICS_PATH, build_topics(new, profile[1], procs), actor_id=user_id)
            changed["content_topics"] = True
    return changed
