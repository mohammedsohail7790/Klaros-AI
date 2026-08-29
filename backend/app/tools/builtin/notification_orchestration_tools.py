"""Phase 10B: tools wrapping NotificationService — the notification bell,
its dropdown, and the notification-preferences panel on /settings/automation
all go through these, same pattern as every other domain."""

import uuid

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.notification_service import NotificationService
from app.tools.base import ExecutionContext, Tool


class NotificationRow(BaseModel):
    id: str
    type: str
    priority: str
    title: str
    body: str
    entity_type: str | None
    entity_id: str | None
    status: str
    read_at: str | None
    created_at: str


def _to_row(n) -> NotificationRow:
    return NotificationRow(
        id=str(n.id),
        type=n.type,
        priority=n.priority,
        title=n.title,
        body=n.body,
        entity_type=n.entity_type,
        entity_id=str(n.entity_id) if n.entity_id else None,
        status=n.status,
        read_at=n.read_at.isoformat() if n.read_at else None,
        created_at=n.created_at.isoformat(),
    )


class ListNotificationsInput(BaseModel):
    unread_only: bool = False
    limit: int = 50


class ListNotificationsOutput(BaseModel):
    notifications: list[NotificationRow]


class ListNotifications(Tool):
    name = "notifications.list_notifications"
    description = "List notifications for the caller's tenant."
    input_schema = ListNotificationsInput
    output_schema = ListNotificationsOutput
    required_permission = Permission.READ_NOTIFICATIONS

    def __init__(self, notification_service: NotificationService) -> None:
        self._service = notification_service

    async def execute(self, input: ListNotificationsInput, context: ExecutionContext) -> ListNotificationsOutput:
        rows = await self._service.list_for_tenant(
            context.tenant_id, unread_only=input.unread_only, limit=input.limit
        )
        return ListNotificationsOutput(notifications=[_to_row(n) for n in rows])


class EmptyInput(BaseModel):
    pass


class UnreadCountOutput(BaseModel):
    unread_count: int


class GetUnreadCount(Tool):
    name = "notifications.get_unread_count"
    description = "Count unread notifications for the caller's tenant."
    input_schema = EmptyInput
    output_schema = UnreadCountOutput
    required_permission = Permission.READ_NOTIFICATIONS

    def __init__(self, notification_service: NotificationService) -> None:
        self._service = notification_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> UnreadCountOutput:
        count = await self._service.unread_count(context.tenant_id)
        return UnreadCountOutput(unread_count=count)


class MarkReadInput(BaseModel):
    notification_id: uuid.UUID


class MarkRead(Tool):
    name = "notifications.mark_read"
    description = "Mark one notification as read."
    input_schema = MarkReadInput
    output_schema = NotificationRow
    required_permission = Permission.READ_NOTIFICATIONS

    def __init__(self, notification_service: NotificationService) -> None:
        self._service = notification_service

    async def execute(self, input: MarkReadInput, context: ExecutionContext) -> NotificationRow:
        n = await self._service.mark_read(context.tenant_id, input.notification_id)
        return _to_row(n)


class MarkAllReadOutput(BaseModel):
    marked_count: int


class MarkAllRead(Tool):
    name = "notifications.mark_all_read"
    description = "Mark every unread notification as read for the caller's tenant."
    input_schema = EmptyInput
    output_schema = MarkAllReadOutput
    required_permission = Permission.READ_NOTIFICATIONS

    def __init__(self, notification_service: NotificationService) -> None:
        self._service = notification_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> MarkAllReadOutput:
        count = await self._service.mark_all_read(context.tenant_id)
        return MarkAllReadOutput(marked_count=count)


class DismissInput(BaseModel):
    notification_id: uuid.UUID


class Dismiss(Tool):
    name = "notifications.dismiss"
    description = "Dismiss one notification."
    input_schema = DismissInput
    output_schema = NotificationRow
    required_permission = Permission.READ_NOTIFICATIONS

    def __init__(self, notification_service: NotificationService) -> None:
        self._service = notification_service

    async def execute(self, input: DismissInput, context: ExecutionContext) -> NotificationRow:
        n = await self._service.dismiss(context.tenant_id, input.notification_id)
        return _to_row(n)


class PreferenceRow(BaseModel):
    type: str
    channel: str
    enabled: bool


class GetPreferencesOutput(BaseModel):
    preferences: list[PreferenceRow]


class GetPreferences(Tool):
    name = "notifications.get_preferences"
    description = "Get the caller's notification preferences (type x channel), with safe defaults filled in."
    input_schema = EmptyInput
    output_schema = GetPreferencesOutput
    required_permission = Permission.READ_NOTIFICATIONS

    def __init__(self, notification_service: NotificationService) -> None:
        self._service = notification_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> GetPreferencesOutput:
        rows = await self._service.get_preferences(context.tenant_id, context.actor_id)
        return GetPreferencesOutput(preferences=[PreferenceRow(**r) for r in rows])


class SetPreferenceInput(BaseModel):
    type: str
    channel: str
    enabled: bool


class SetPreference(Tool):
    name = "notifications.set_preference"
    description = "Set one notification preference (type x channel) for the caller."
    input_schema = SetPreferenceInput
    output_schema = PreferenceRow
    required_permission = Permission.MANAGE_NOTIFICATION_PREFERENCES

    def __init__(self, notification_service: NotificationService) -> None:
        self._service = notification_service

    async def execute(self, input: SetPreferenceInput, context: ExecutionContext) -> PreferenceRow:
        result = await self._service.set_preference(
            context.tenant_id, context.actor_id, input.type, input.channel, input.enabled
        )
        return PreferenceRow(**result)
