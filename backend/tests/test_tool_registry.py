import uuid

import pytest
from pydantic import BaseModel

from app.models.actor import ActorType
from app.models.rbac import Permission, Role
from app.tools.base import ExecutionContext, Tool
from app.tools.errors import (
    ToolApprovalRequiredError,
    ToolBlockedError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolValidationError,
)
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio


class EchoInput(BaseModel):
    message: str


class EchoOutput(BaseModel):
    echoed: str


class EchoTool(Tool):
    name = "test.echo"
    description = "Echoes input back."
    input_schema = EchoInput
    output_schema = EchoOutput
    required_permission = Permission.DELETE_CUSTOMER  # OWNER-only in the default matrix

    async def execute(self, input: EchoInput, context: ExecutionContext) -> EchoOutput:
        return EchoOutput(echoed=input.message)


def _context(tenant_id=None, role=Role.OWNER) -> ExecutionContext:
    return ExecutionContext(
        tenant_id=tenant_id or uuid.uuid4(),
        actor_type=ActorType.USER,
        actor_id=uuid.uuid4(),
        role=role,
    )


async def test_tool_executes_when_authorized(tool_registry) -> None:
    tool_registry.register(EchoTool())
    from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy

    DEFAULT_TOOL_POLICIES["test.echo"] = ActionPolicy.AUTO
    output = await tool_registry.execute("test.echo", {"message": "hi"}, _context())
    assert output.echoed == "hi"


async def test_unknown_tool_raises(tool_registry) -> None:
    with pytest.raises(ToolNotFoundError):
        await tool_registry.execute("does.not.exist", {}, _context())


async def test_permission_denied_for_wrong_role(tool_registry) -> None:
    tool_registry.register(EchoTool())
    from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy

    DEFAULT_TOOL_POLICIES["test.echo"] = ActionPolicy.AUTO
    with pytest.raises(ToolPermissionError):
        await tool_registry.execute("test.echo", {"message": "hi"}, _context(role=Role.STAFF))


async def test_invalid_input_raises_validation_error(tool_registry) -> None:
    tool_registry.register(EchoTool())
    from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy

    DEFAULT_TOOL_POLICIES["test.echo"] = ActionPolicy.AUTO
    with pytest.raises(ToolValidationError):
        await tool_registry.execute("test.echo", {"wrong_field": 1}, _context())


async def test_blocked_tool_never_executes(tool_registry) -> None:
    tool_registry.register(EchoTool())
    from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy

    DEFAULT_TOOL_POLICIES["test.echo"] = ActionPolicy.BLOCKED
    with pytest.raises(ToolBlockedError):
        await tool_registry.execute("test.echo", {"message": "hi"}, _context())
    DEFAULT_TOOL_POLICIES.pop("test.echo", None)


async def test_approval_required_creates_request_and_does_not_execute(tool_registry) -> None:
    tool_registry.register(EchoTool())
    from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy

    DEFAULT_TOOL_POLICIES["test.echo"] = ActionPolicy.APPROVAL_REQUIRED
    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute("test.echo", {"message": "hi"}, _context())
    assert exc_info.value.approval_request_id is not None
    DEFAULT_TOOL_POLICIES.pop("test.echo", None)


async def test_list_available_filters_by_permission(tool_registry) -> None:
    tool_registry.register(EchoTool())
    owner_ctx = _context(role=Role.OWNER)
    technician_ctx = _context(role=Role.TECHNICIAN)

    owner_tools = {t.name for t in tool_registry.list_available(owner_ctx)}
    technician_tools = {t.name for t in tool_registry.list_available(technician_ctx)}

    assert "test.echo" in owner_tools  # OWNER has READ_CUSTOMERS
    assert "system.get_current_time" in technician_tools  # no permission required
