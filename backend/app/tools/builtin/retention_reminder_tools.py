import uuid

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.retention_service import RetentionService
from app.tools.base import ExecutionContext, Tool


class EmptyInput(BaseModel):
    pass


class MarkDueRemindersOutput(BaseModel):
    due_reminder_ids: list[str]


class MarkDueReminders(Tool):
    name = "retention.mark_due_reminders"
    description = "Deterministically flip SCHEDULED reminders whose reminder_date has passed to DUE."
    input_schema = EmptyInput
    output_schema = MarkDueRemindersOutput
    required_permission = Permission.MANAGE_RETENTION

    def __init__(self, retention_service: RetentionService) -> None:
        self._retention_service = retention_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> MarkDueRemindersOutput:
        ids = await self._retention_service.mark_due_reminders(context.tenant_id)
        return MarkDueRemindersOutput(due_reminder_ids=[str(i) for i in ids])


class UpdateReminderInput(BaseModel):
    reminder_id: uuid.UUID
    status: str


class ReminderOutput(BaseModel):
    reminder_id: str
    status: str


class UpdateReminderStatus(Tool):
    name = "retention.update_reminder_status"
    description = "Update a service reminder's status (SENT/RESPONDED/BOOKED/CANCELLED)."
    input_schema = UpdateReminderInput
    output_schema = ReminderOutput
    required_permission = Permission.MANAGE_RETENTION

    def __init__(self, retention_service: RetentionService) -> None:
        self._retention_service = retention_service

    async def execute(self, input: UpdateReminderInput, context: ExecutionContext) -> ReminderOutput:
        try:
            reminder = await self._retention_service.update_reminder_status(context.tenant_id, input.reminder_id, input.status)
        except ValueError as e:
            raise ValueError(str(e)) from e
        return ReminderOutput(reminder_id=str(reminder.id), status=reminder.status)
