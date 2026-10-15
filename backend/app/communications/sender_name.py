"""The name a recipient sees on an e-mail a business sends through Klaros.

Klaros is one product for many businesses, so a patient or a clinic must see the BUSINESS's name (for example "MedLuxe Travel"), not the platform's. The address still comes from the deployment's
configured sender until the business has its own authenticated domain. Falls back to "Klaros AI" when the business has no usable name; control characters and angle brackets never reach a header.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.organization import Organization

PLATFORM_NAME = "Klaros AI"


def clean_display_name(raw: str | None) -> str:
    name = re.sub(r"[\x00-\x1f\x7f<>\"\\]", " ", raw or "")
    name = re.sub(r"\s+", " ", name).strip()[:60]
    return name or PLATFORM_NAME


async def sender_name_for(session_factory: async_sessionmaker, tenant_id: uuid.UUID) -> str:
    try:
        async with session_factory() as session:
            await set_tenant_context(session, tenant_id)
            org = await session.get(Organization, tenant_id)
        return clean_display_name(org.name if org else None)
    except Exception:  # noqa: BLE001 - a naming problem must never stop an e-mail
        return PLATFORM_NAME
