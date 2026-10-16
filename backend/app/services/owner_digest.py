"""A morning e-mail to the business's owners listing what needs them, so they do not have to open the app to find out.

It is built from the same attention queue the dashboard shows, goes only to active OWNER users of this business, is sent at most once a day, and is skipped (not sent empty) when nothing needs them.
It never contains patient names or anything a patient wrote: only the item titles the attention queue already shows an owner, with a link back into the app.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.organization import Organization
from app.models.rbac import Role
from app.models.user import User
from app.services.owner_attention_service import OwnerAttentionService

SENT_ACTION = "owner.digest_sent"
MAX_ITEMS = 12


def compose(business: str, items: list[Any], base_url: str) -> tuple[str, str]:
    """(subject, body). The title and reason of each attention item, most urgent first, each with its link."""
    n = len(items)
    subject = f"{business}: {n} thing{'s' if n != 1 else ''} need{'' if n != 1 else 's'} you today"
    lines = [f"Good morning,\n\nHere is what needs you at {business} today.\n"]
    for i in items[:MAX_ITEMS]:
        lines.append(f"- [{i.priority}] {i.title}\n  {i.reason}\n  {base_url.rstrip('/')}{i.link}")
    if n > MAX_ITEMS:
        lines.append(f"\n...and {n - MAX_ITEMS} more in the app.")
    lines.append("\nNothing here was sent to anyone outside your team.")
    return subject, "\n".join(lines)


async def send_digest(session_factory, tenant_id: uuid.UUID, *, actor_type=ActorType.SYSTEM, actor_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Send today's digest to the owners unless one already went today or nothing needs them. Returns {'sent': int, 'reason': str | None}."""
    from app.communications.base import MessageTemplate
    from app.communications.email_config import email_configured
    from app.communications.factory import get_internal_channel

    start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        org = await session.get(Organization, tenant_id)
        owners = [u.email for u in (await session.execute(select(User).where(User.tenant_id == tenant_id, User.role == Role.OWNER, User.is_active.is_(True)))).scalars() if u.email]
        done = (await session.execute(select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == SENT_ACTION,
                                                                                         AuditLog.created_at >= start_of_day))).scalar_one()
    if done:
        return {"sent": 0, "reason": "already_sent_today"}
    if not owners:
        return {"sent": 0, "reason": "no_owner_email"}
    if not email_configured():
        return {"sent": 0, "reason": "email_not_configured"}
    items = await OwnerAttentionService(session_factory).get_attention_queue(tenant_id)
    if not items:
        return {"sent": 0, "reason": "nothing_needs_you"}
    subject, body = compose(org.name if org is not None else "Your business", items, get_settings().FRONTEND_BASE_URL)
    channel = get_internal_channel(session_factory)
    sent = 0
    for to in owners:
        if await channel.send_email(tenant_id, to=to, subject=subject, body=body, template=MessageTemplate.OWNER_DIGEST):
            sent += 1
    if sent:
        async with session_factory() as session:
            await set_tenant_context(session, tenant_id)
            session.add(AuditLog(tenant_id=tenant_id, actor_type=actor_type, actor_id=actor_id, action=SENT_ACTION, entity_type="organization", entity_id=tenant_id,
                                 input_summary={"items": len(items), "recipients": sent}, result="success"))
            await session.commit()
    return {"sent": sent, "reason": None if sent else "not_delivered"}
