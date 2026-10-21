"""Per-tenant configuration. The platform has one default for each setting; a tenant's own row overrides it.

Keys in use:
  `crew`           {"auto_provision": bool (default True), "disabled_members": [crew member keys] (default [])}
  `ai_processing`  {"external_providers": [AI provider names the owner allows for this business's data] (default []), "acknowledged_by": user id, "acknowledged_at": iso time}
  `outreach_blocks`  {"status": "DRAFT" | "APPROVED", "version": int, "blocks": {block name: text}, "approved_by": user id, "approved_at": iso time} (clinic outreach wording; default is the platform's draft text, not approved)

Reads never fail a caller: a missing row or a lookup error returns the default. For `ai_processing` that default is the strict one (no extra provider).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.tenant_settings import TenantSetting

logger = structlog.get_logger(__name__)

from app.services.outreach_blocks import DEFAULT_BLOCKS

CREW, AI_PROCESSING, DROPSHIPPING_STORE, OUTREACH_BLOCKS = "crew", "ai_processing", "dropshipping_store", "outreach_blocks"
DEFAULTS: dict[str, dict[str, Any]] = {
    CREW: {"auto_provision": True, "disabled_members": []},
    AI_PROCESSING: {"external_providers": []},
    # closed until the owner has set a shipping rule and acknowledged that sales tax is not collected (see dropshipping_store_service)
    DROPSHIPPING_STORE: {"open": False, "shipping_flat_fee": None, "free_shipping_over": None, "sales_tax_acknowledged": False},
    OUTREACH_BLOCKS: {"status": "DRAFT", "version": 0, "blocks": DEFAULT_BLOCKS},
}


class TenantSettingsService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def get(self, tenant_id: uuid.UUID, key: str) -> dict[str, Any]:
        default = dict(DEFAULTS[key])
        try:
            async with self._session_factory() as session:
                await set_tenant_context(session, tenant_id)
                row = (await session.execute(select(TenantSetting).where(TenantSetting.tenant_id == tenant_id, TenantSetting.key == key))).scalar_one_or_none()
        except Exception:  # noqa: BLE001 - a settings hiccup must never break the caller; the platform default applies
            logger.warning("tenant_settings_read_failed", key=key)
            return default
        return {**default, **row.value} if row is not None and isinstance(row.value, dict) else default

    async def set(self, tenant_id: uuid.UUID, key: str, value: dict[str, Any], *, updated_by: uuid.UUID | None) -> dict[str, Any]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = (await session.execute(select(TenantSetting).where(TenantSetting.tenant_id == tenant_id, TenantSetting.key == key))).scalar_one_or_none()
            if row is None:
                session.add(TenantSetting(tenant_id=tenant_id, key=key, value=value, updated_by=updated_by))
            else:
                row.value, row.updated_by = value, updated_by
                row.updated_at = datetime.now(timezone.utc)
            await session.commit()
        return {**DEFAULTS[key], **value}
