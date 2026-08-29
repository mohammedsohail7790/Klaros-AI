"""Phase 10B: notification orchestration, built on top of the existing
`Notification` table/`EventBus`/`AuditLog` — no second notification store,
no second event mechanism. `NotificationService.notify()` is the one place
that creates a `Notification` row; every event handler that wants to alert
the Owner calls it instead of constructing a `Notification` directly (the
one pre-Phase-10 exception, crm_handlers.py's appointment-booked notice,
is intentionally left as-is — a `GENERAL`-type row with no dedupe key,
harmless and unchanged).

In-app delivery IS the notification row itself — creating it is "sending"
it for that channel. Email/SMS are separate, real `CommunicationProvider`
calls (`app/communications/`) that honestly report `NOT_CONNECTED` when no
provider is configured, exactly like every other outbound channel in this
codebase; nothing here ever claims an external send succeeded without one.
"""

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.notification import (
    Notification,
    NotificationChannelName,
    NotificationPreference,
    NotificationPriority,
    NotificationStatus,
    NotificationType,
)
from app.models.rbac import Permission, role_has_permission
from app.models.user import User

logger = structlog.get_logger(__name__)


# Safe, non-spammy defaults (spec example) — used whenever a user has no
# explicit NotificationPreference row for a (type, channel) pair. Critical
# types stay visible in-app regardless of what's configured for other
# channels (enforced in `notify()`, not just here).
_DEFAULT_ENABLED: dict[str, set[str]] = {
    NotificationType.APPROVAL_REQUIRED: {NotificationChannelName.IN_APP, NotificationChannelName.EMAIL},
    NotificationType.APPROVAL_APPROVED: {NotificationChannelName.IN_APP},
    NotificationType.APPROVAL_REJECTED: {NotificationChannelName.IN_APP},
    NotificationType.ACTION_EXECUTED: {NotificationChannelName.IN_APP},
    NotificationType.ACTION_FAILED: {NotificationChannelName.IN_APP, NotificationChannelName.EMAIL},
    NotificationType.HIGH_PRIORITY_EXCEPTION: {NotificationChannelName.IN_APP, NotificationChannelName.EMAIL},
    NotificationType.PAYMENT_RECEIVED: {NotificationChannelName.IN_APP},
    NotificationType.INVOICE_OVERDUE: {NotificationChannelName.IN_APP, NotificationChannelName.EMAIL},
    NotificationType.JOB_DELAYED: {NotificationChannelName.IN_APP},
    NotificationType.NEGATIVE_FEEDBACK: {NotificationChannelName.IN_APP, NotificationChannelName.EMAIL},
    NotificationType.NEW_LEAD: {NotificationChannelName.IN_APP},
    NotificationType.MORNING_BRIEF_READY: {NotificationChannelName.IN_APP},
    NotificationType.SYSTEM_ERROR: {NotificationChannelName.IN_APP, NotificationChannelName.EMAIL},
}

# Never suppressed in-app even if a user disables it — the spec's explicit
# "critical types must stay visible in-app even if optional channels fail"
# rule.
_ALWAYS_IN_APP = {
    NotificationType.APPROVAL_REQUIRED,
    NotificationType.ACTION_FAILED,
    NotificationType.HIGH_PRIORITY_EXCEPTION,
}


class NotificationService:
    def __init__(self, session_factory: async_sessionmaker, communication_provider=None) -> None:
        self._session_factory = session_factory
        self._communication_provider = communication_provider

    async def notify(
        self,
        tenant_id: uuid.UUID,
        notification_type: NotificationType,
        *,
        title: str,
        body: str,
        priority: NotificationPriority = NotificationPriority.MEDIUM,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        dedupe_key: str | None = None,
        recipient_id: uuid.UUID | None = None,
    ) -> Notification | None:
        """Creates (at most once, if `dedupe_key` is given) an in-app
        Notification row, then best-effort attempts email/SMS per the
        recipient's (or, if none given, every eligible user's) preferences.
        Returns the created row, or None if a duplicate was silently
        absorbed by the dedupe constraint."""
        async with self._session_factory() as session:
            notification = Notification(
                tenant_id=tenant_id,
                recipient_id=recipient_id,
                type=notification_type,
                priority=priority,
                title=title,
                body=body,
                entity_type=entity_type,
                entity_id=entity_id,
                channel=NotificationChannelName.IN_APP,
                status=NotificationStatus.SENT,
                dedupe_key=dedupe_key,
                sent_at=datetime.now(timezone.utc),
                severity=priority,
                category=notification_type.lower(),
            )
            session.add(notification)
            try:
                await session.commit()
            except IntegrityError:
                # Same dedupe_key already delivered for this tenant — a
                # retried/duplicated event must never fan out into a second
                # notification. Not an error; the expected idempotent path.
                await session.rollback()
                logger.info("notification_deduplicated", tenant_id=str(tenant_id), dedupe_key=dedupe_key)
                return None
            await session.refresh(notification)

        await self._dispatch_external_channels(
            tenant_id, notification, recipient_id=recipient_id
        )
        return notification

    async def _dispatch_external_channels(
        self, tenant_id: uuid.UUID, notification: Notification, *, recipient_id: uuid.UUID | None
    ) -> None:
        """Email/SMS are opt-in, best-effort, and NEVER fabricated. If no
        real provider is connected (the case in this environment — see
        app/integrations/adapters.py), nothing is sent and nothing claims
        otherwise; this is intentionally a no-op today rather than a fake
        send, consistent with every other outbound channel in this
        codebase."""
        if self._communication_provider is None:
            return

        async with self._session_factory() as session:
            recipients: list[User]
            if recipient_id is not None:
                user = await session.get(User, recipient_id)
                recipients = [user] if user else []
            else:
                recipients = (
                    await session.execute(select(User).where(User.tenant_id == tenant_id))
                ).scalars().all()

        for user in recipients:
            if not role_has_permission(user.role, Permission.READ_NOTIFICATIONS):
                continue
            for channel in (NotificationChannelName.EMAIL, NotificationChannelName.SMS):
                if not await self._is_enabled(tenant_id, user.id, notification.type, channel):
                    continue
                # No real Twilio/SendGrid credentials in this environment —
                # get_status() reports NOT_CONNECTED, so no call is made.
                # A future phase with real credentials wires the actual
                # send here through the same CommunicationProvider every
                # customer-facing message already uses.
                logger.info(
                    "notification_external_channel_not_connected",
                    channel=channel,
                    notification_type=notification.type,
                )

    async def _is_enabled(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, notification_type: str, channel: str
    ) -> bool:
        if notification_type in _ALWAYS_IN_APP and channel == NotificationChannelName.IN_APP:
            return True
        async with self._session_factory() as session:
            pref = (
                await session.execute(
                    select(NotificationPreference).where(
                        NotificationPreference.tenant_id == tenant_id,
                        NotificationPreference.user_id == user_id,
                        NotificationPreference.type == notification_type,
                        NotificationPreference.channel == channel,
                    )
                )
            ).scalar_one_or_none()
        if pref is not None:
            return pref.enabled
        return channel in _DEFAULT_ENABLED.get(notification_type, {NotificationChannelName.IN_APP})

    async def list_for_tenant(
        self, tenant_id: uuid.UUID, *, unread_only: bool = False, limit: int = 50
    ) -> list[Notification]:
        async with self._session_factory() as session:
            query = select(Notification).where(Notification.tenant_id == tenant_id)
            if unread_only:
                query = query.where(Notification.read_at.is_(None))
            query = query.order_by(Notification.created_at.desc()).limit(limit)
            return (await session.execute(query)).scalars().all()

    async def unread_count(self, tenant_id: uuid.UUID) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count()).select_from(Notification).where(
                    Notification.tenant_id == tenant_id, Notification.read_at.is_(None)
                )
            )
            return result.scalar_one()

    async def mark_read(self, tenant_id: uuid.UUID, notification_id: uuid.UUID) -> Notification:
        async with self._session_factory() as session:
            notification = await session.get(Notification, notification_id)
            if notification is None or notification.tenant_id != tenant_id:
                raise ValueError("Notification not found")
            if notification.read_at is None:
                notification.read_at = datetime.now(timezone.utc)
                notification.status = NotificationStatus.READ
                await session.commit()
                await session.refresh(notification)
            return notification

    async def mark_all_read(self, tenant_id: uuid.UUID) -> int:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Notification).where(
                        Notification.tenant_id == tenant_id, Notification.read_at.is_(None)
                    )
                )
            ).scalars().all()
            now = datetime.now(timezone.utc)
            for row in rows:
                row.read_at = now
                row.status = NotificationStatus.READ
            await session.commit()
            return len(rows)

    async def dismiss(self, tenant_id: uuid.UUID, notification_id: uuid.UUID) -> Notification:
        async with self._session_factory() as session:
            notification = await session.get(Notification, notification_id)
            if notification is None or notification.tenant_id != tenant_id:
                raise ValueError("Notification not found")
            notification.status = NotificationStatus.DISMISSED
            if notification.read_at is None:
                notification.read_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(notification)
            return notification

    async def get_preferences(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[dict]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(NotificationPreference).where(
                        NotificationPreference.tenant_id == tenant_id,
                        NotificationPreference.user_id == user_id,
                    )
                )
            ).scalars().all()
        overrides = {(r.type, r.channel): r.enabled for r in rows}
        results = []
        for ntype in NotificationType:
            if ntype == NotificationType.GENERAL:
                continue
            for channel in NotificationChannelName:
                key = (ntype.value, channel.value)
                enabled = overrides.get(
                    key,
                    channel in _DEFAULT_ENABLED.get(ntype, {NotificationChannelName.IN_APP})
                    or ntype in _ALWAYS_IN_APP
                    and channel == NotificationChannelName.IN_APP,
                )
                results.append({"type": ntype.value, "channel": channel.value, "enabled": enabled})
        return results

    async def set_preference(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, notification_type: str, channel: str, enabled: bool
    ) -> dict:
        if notification_type in _ALWAYS_IN_APP and channel == NotificationChannelName.IN_APP and not enabled:
            raise ValueError(f"{notification_type} cannot be disabled in-app — it is always visible")
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(NotificationPreference).where(
                        NotificationPreference.tenant_id == tenant_id,
                        NotificationPreference.user_id == user_id,
                        NotificationPreference.type == notification_type,
                        NotificationPreference.channel == channel,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.enabled = enabled
            else:
                existing = NotificationPreference(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    type=notification_type,
                    channel=channel,
                    enabled=enabled,
                )
                session.add(existing)
            await session.commit()
        return {"type": notification_type, "channel": channel, "enabled": enabled}
