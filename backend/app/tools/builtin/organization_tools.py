"""The AI kill switch — a real, org-wide emergency control. Enforcement
lives in app/tools/registry.py::ToolRegistry.execute() (checked before
any other gate, for every non-USER actor); these two tools are just the
read/write surface an owner uses to see and flip it. Deliberately
OWNER-only (checked directly via context.role, stricter than
Permission.MANAGE_USERS which ADMIN also carries) — this is the one
nuclear-option control in the app.
"""

from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.organization import Organization
from app.models.rbac import Role
from app.tools.base import ExecutionContext, Tool
from app.tools.errors import ToolPermissionError


class KillSwitchStatus(BaseModel):
    ai_paused: bool
    ai_paused_at: str | None
    ai_paused_by: str | None


class EmptyInput(BaseModel):
    pass


class GetKillSwitchStatus(Tool):
    name = "organization.get_kill_switch_status"
    description = "Read whether this tenant's AI kill switch is on."
    input_schema = EmptyInput
    output_schema = KillSwitchStatus

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> KillSwitchStatus:
        async with self._session_factory() as session:
            org = await session.get(Organization, context.tenant_id)
        if org is None:
            return KillSwitchStatus(ai_paused=False, ai_paused_at=None, ai_paused_by=None)
        return KillSwitchStatus(
            ai_paused=org.ai_paused,
            ai_paused_at=org.ai_paused_at.isoformat() if org.ai_paused_at else None,
            ai_paused_by=str(org.ai_paused_by) if org.ai_paused_by else None,
        )


class SetKillSwitchInput(BaseModel):
    active: bool


class SetKillSwitch(Tool):
    name = "organization.set_kill_switch"
    description = "Turn the AI kill switch on or off — pauses/resumes every AI and automation action tenant-wide."
    input_schema = SetKillSwitchInput
    output_schema = KillSwitchStatus

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: SetKillSwitchInput, context: ExecutionContext) -> KillSwitchStatus:
        if context.role != Role.OWNER:
            raise ToolPermissionError("Only an owner can change the AI kill switch")

        async with self._session_factory() as session:
            org = await session.get(Organization, context.tenant_id)
            if org is None:
                raise ToolPermissionError("Organization not found")
            org.ai_paused = input.active
            org.ai_paused_at = datetime.now(UTC) if input.active else None
            org.ai_paused_by = context.actor_id if input.active else None
            await session.commit()
            await session.refresh(org)
        return KillSwitchStatus(
            ai_paused=org.ai_paused,
            ai_paused_at=org.ai_paused_at.isoformat() if org.ai_paused_at else None,
            ai_paused_by=str(org.ai_paused_by) if org.ai_paused_by else None,
        )
