from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.notification import Notification
from app.tools.base import ExecutionContext, Tool


class CreateNotificationInput(BaseModel):
    title: str
    body: str
    severity: str = "INFO"
    category: str = "general"


class CreateNotificationOutput(BaseModel):
    notification_id: str


class CreateNotification(Tool):
    name = "notifications.create_notification"
    description = "Create an in-app notification for the tenant's owner cockpit."
    input_schema = CreateNotificationInput
    output_schema = CreateNotificationOutput

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(
        self, input: CreateNotificationInput, context: ExecutionContext
    ) -> CreateNotificationOutput:
        async with self._session_factory() as session:
            notification = Notification(
                tenant_id=context.tenant_id,
                title=input.title,
                body=input.body,
                severity=input.severity,
                category=input.category,
            )
            session.add(notification)
            await session.commit()
            await session.refresh(notification)
            return CreateNotificationOutput(notification_id=str(notification.id))
