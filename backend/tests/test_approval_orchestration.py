"""Phase 9: approval orchestration — approving a request actually resumes
and executes the original tool call, safely, idempotently, tenant-scoped,
and audited. Uses `notifications.create_notification` as the AUTO tool
flipped to APPROVAL_REQUIRED for these tests (same technique as
tests/test_approval_flow.py), and counts real `Notification` rows to prove
execution happened exactly once — never by trusting a return value alone.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.models.actor import ActorType
from app.models.approval import ApprovalExecutionStatus, ApprovalRequest, ApprovalStatus
from app.models.audit_log import AuditLog
from app.models.event import Event, EventType
from app.models.notification import Notification
from app.models.rbac import Permission, Role, role_has_permission
from app.services.approval_execution_service import (
    ApprovalExecutionService,
    ApprovalNotFoundError,
    ApprovalStateError,
    SelfApprovalError,
)
from app.tools.base import ExecutionContext
from app.tools.errors import ToolBlockedError, ToolValidationError

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER, actor_type=ActorType.USER, actor_id=None):
    return ExecutionContext(
        tenant_id=tenant_id, actor_type=actor_type, actor_id=actor_id or uuid.uuid4(), role=role
    )


@pytest.fixture
def approval_required_notification():
    from app.tools.policy import ActionPolicy, DEFAULT_TOOL_POLICIES

    DEFAULT_TOOL_POLICIES["notifications.create_notification"] = ActionPolicy.APPROVAL_REQUIRED
    yield


async def _notification_count(session_factory, tenant_id) -> int:
    async with session_factory() as session:
        return (
            await session.execute(
                select(func.count()).where(Notification.tenant_id == tenant_id)
            )
        ).scalar_one()


async def test_auto_tool_executes_immediately(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    out = await tool_registry.execute(
        "notifications.create_notification", {"title": "x", "body": "y"}, ctx
    )
    assert out.notification_id


async def test_approval_required_creates_request(tool_registry, approval_required_notification) -> None:
    from app.tools.errors import ToolApprovalRequiredError

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)

    async with tool_registry._session_factory() as session:
        request = await session.get(ApprovalRequest, exc_info.value.approval_request_id)
    assert request.status == ApprovalStatus.PENDING
    assert request.execution_status == ApprovalExecutionStatus.NOT_STARTED
    assert request.requested_by_role == Role.OWNER.value


async def test_blocked_tool_never_executes(tool_registry) -> None:
    from app.tools.policy import ActionPolicy, DEFAULT_TOOL_POLICIES

    DEFAULT_TOOL_POLICIES["notifications.create_notification"] = ActionPolicy.BLOCKED
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    with pytest.raises(ToolBlockedError):
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)
    assert await _notification_count(tool_registry._session_factory, tenant_id) == 0


async def test_approval_executes_original_tool(tool_registry, event_bus, approval_required_notification) -> None:
    from app.tools.errors import ToolApprovalRequiredError

    tenant_id = uuid.uuid4()
    requester_id = uuid.uuid4()
    approver_id = uuid.uuid4()
    ctx = _ctx(tenant_id, actor_id=requester_id)

    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)
    approval_id = exc_info.value.approval_request_id

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, event_bus)
    result = await service.approve(tenant_id, approval_id, decided_by_id=approver_id, decided_by_role=Role.OWNER)

    assert result.status == ApprovalStatus.APPROVED
    assert result.execution_status == ApprovalExecutionStatus.EXECUTED
    assert result.execution_result["notification_id"]
    assert await _notification_count(tool_registry._session_factory, tenant_id) == 1


async def test_rejection_does_not_execute(tool_registry, event_bus, approval_required_notification) -> None:
    from app.tools.errors import ToolApprovalRequiredError

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id, actor_id=uuid.uuid4())
    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)
    approval_id = exc_info.value.approval_request_id

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, event_bus)
    result = await service.reject(tenant_id, approval_id, decided_by_id=uuid.uuid4(), note="no")

    assert result.status == ApprovalStatus.REJECTED
    assert result.execution_status == ApprovalExecutionStatus.NOT_STARTED
    assert await _notification_count(tool_registry._session_factory, tenant_id) == 0


async def test_double_approval_cannot_execute_twice(tool_registry, event_bus, approval_required_notification) -> None:
    from app.tools.errors import ToolApprovalRequiredError

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id, actor_id=uuid.uuid4())
    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)
    approval_id = exc_info.value.approval_request_id

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, event_bus)
    approver_id = uuid.uuid4()
    await service.approve(tenant_id, approval_id, decided_by_id=approver_id, decided_by_role=Role.OWNER)

    with pytest.raises(ApprovalStateError):
        await service.approve(tenant_id, approval_id, decided_by_id=approver_id, decided_by_role=Role.OWNER)

    assert await _notification_count(tool_registry._session_factory, tenant_id) == 1


async def test_concurrent_execute_approved_cannot_execute_twice(tool_registry, event_bus, approval_required_notification) -> None:
    """Two concurrent calls to execute_approved (simulating a double-click or
    two workers) race for the DB-level compare-and-swap on execution_status —
    only one may run the tool."""
    from app.tools.errors import ToolApprovalRequiredError

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id, actor_id=uuid.uuid4())
    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)
    approval_id = exc_info.value.approval_request_id

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, event_bus)
    await service.approve(tenant_id, approval_id, decided_by_id=uuid.uuid4(), decided_by_role=Role.OWNER)
    # approve() already executed once; reset execution_status to simulate a
    # race at the execute_approved layer specifically.
    async with tool_registry._session_factory() as session:
        request = await session.get(ApprovalRequest, approval_id)
        request.execution_status = ApprovalExecutionStatus.NOT_STARTED
        await session.commit()

    await asyncio.gather(
        service.execute_approved(tenant_id, approval_id),
        service.execute_approved(tenant_id, approval_id),
    )
    assert await _notification_count(tool_registry._session_factory, tenant_id) == 2  # one from approve(), one from the race (only one of the two gather calls wins)


async def test_approval_tenant_isolation(tool_registry, event_bus, approval_required_notification) -> None:
    from app.tools.errors import ToolApprovalRequiredError

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    ctx = _ctx(tenant_a, actor_id=uuid.uuid4())
    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)
    approval_id = exc_info.value.approval_request_id

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, event_bus)
    with pytest.raises(ApprovalNotFoundError):
        await service.approve(tenant_b, approval_id, decided_by_id=uuid.uuid4(), decided_by_role=Role.OWNER)

    async with tool_registry._session_factory() as session:
        request = await session.get(ApprovalRequest, approval_id)
    assert request.status == ApprovalStatus.PENDING  # untouched by tenant B's attempt


async def test_requester_cannot_self_approve(tool_registry, event_bus, approval_required_notification) -> None:
    from app.tools.errors import ToolApprovalRequiredError

    tenant_id = uuid.uuid4()
    requester_id = uuid.uuid4()
    ctx = _ctx(tenant_id, actor_id=requester_id)
    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)
    approval_id = exc_info.value.approval_request_id

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, event_bus)
    with pytest.raises(SelfApprovalError):
        await service.approve(tenant_id, approval_id, decided_by_id=requester_id, decided_by_role=Role.OWNER)
    assert await _notification_count(tool_registry._session_factory, tenant_id) == 0


async def test_ai_cannot_call_approve_tool(tool_registry, approval_required_notification) -> None:
    tenant_id = uuid.uuid4()
    ai_ctx = ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.AI, actor_id=None, role=Role.OWNER)
    with pytest.raises(ValueError, match="AI cannot approve"):
        await tool_registry.execute(
            "approvals.approve", {"approval_request_id": str(uuid.uuid4())}, ai_ctx
        )


async def test_policy_blocked_after_approval_fails_execution_safely(
    tool_registry, event_bus, approval_required_notification
) -> None:
    """If policy changes to BLOCKED between request creation and approval,
    execution must refuse — not silently run a now-forbidden action."""
    from app.tools.errors import ToolApprovalRequiredError
    from app.tools.policy import ActionPolicy, DEFAULT_TOOL_POLICIES

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id, actor_id=uuid.uuid4())
    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)
    approval_id = exc_info.value.approval_request_id

    DEFAULT_TOOL_POLICIES["notifications.create_notification"] = ActionPolicy.BLOCKED

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, event_bus)
    result = await service.approve(tenant_id, approval_id, decided_by_id=uuid.uuid4(), decided_by_role=Role.OWNER)

    assert result.status == ApprovalStatus.APPROVED  # the decision itself stands
    assert result.execution_status == ApprovalExecutionStatus.FAILED  # but it never ran
    assert await _notification_count(tool_registry._session_factory, tenant_id) == 0


async def test_input_schema_is_revalidated_on_execution(tool_registry, event_bus) -> None:
    """A corrupted/incompatible stored tool_input must fail validation on
    resume, not crash the service or silently execute with bad data."""
    tenant_id = uuid.uuid4()
    async with tool_registry._session_factory() as session:
        request = ApprovalRequest(
            tenant_id=tenant_id,
            requested_by_type=ActorType.USER,
            requested_by_id=uuid.uuid4(),
            requested_by_role=Role.OWNER.value,
            tool_name="notifications.create_notification",
            action_type="notifications",
            reason="test",
            tool_input={"title": 12345, "body": None},  # wrong types
            status=ApprovalStatus.APPROVED,
        )
        session.add(request)
        await session.commit()
        await session.refresh(request)
        approval_id = request.id

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, event_bus)
    result = await service.execute_approved(tenant_id, approval_id)
    assert result.execution_status == ApprovalExecutionStatus.FAILED
    assert result.execution_error


async def test_failed_execution_is_recorded(tool_registry, event_bus) -> None:
    tenant_id = uuid.uuid4()
    async with tool_registry._session_factory() as session:
        request = ApprovalRequest(
            tenant_id=tenant_id,
            requested_by_type=ActorType.USER,
            requested_by_id=uuid.uuid4(),
            requested_by_role=Role.OWNER.value,
            tool_name="notifications.create_notification",
            action_type="notifications",
            reason="test",
            tool_input={"title": "ok", "body": "ok", "severity": "NOT_A_REAL_SEVERITY_BUT_STRING_OK"},
            status=ApprovalStatus.APPROVED,
        )
        session.add(request)
        await session.commit()
        await session.refresh(request)
        approval_id = request.id

    # Force a real failure deterministically: point tool_name at a name that
    # doesn't exist, so the registry raises ToolNotFoundError on resume.
    async with tool_registry._session_factory() as session:
        req = await session.get(ApprovalRequest, approval_id)
        req.tool_name = "notifications.does_not_exist"
        await session.commit()

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, event_bus)
    result = await service.execute_approved(tenant_id, approval_id)
    assert result.execution_status == ApprovalExecutionStatus.FAILED
    assert "does_not_exist" in result.execution_error or "Unknown tool" in result.execution_error


async def test_failed_execution_can_be_retried_and_retry_is_idempotent(
    tool_registry, event_bus, approval_required_notification
) -> None:
    from app.tools.errors import ToolApprovalRequiredError
    from app.tools.policy import ActionPolicy, DEFAULT_TOOL_POLICIES

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id, actor_id=uuid.uuid4())
    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)
    approval_id = exc_info.value.approval_request_id

    DEFAULT_TOOL_POLICIES["notifications.create_notification"] = ActionPolicy.BLOCKED
    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, event_bus)
    approved = await service.approve(tenant_id, approval_id, decided_by_id=uuid.uuid4(), decided_by_role=Role.OWNER)
    assert approved.execution_status == ApprovalExecutionStatus.FAILED

    # Fix the underlying cause and retry.
    DEFAULT_TOOL_POLICIES["notifications.create_notification"] = ActionPolicy.APPROVAL_REQUIRED
    retried_twice = await asyncio.gather(
        service.retry_failed(tenant_id, approval_id, actor_id=uuid.uuid4()),
        service.retry_failed(tenant_id, approval_id, actor_id=uuid.uuid4()),
    )
    assert any(r.execution_status == ApprovalExecutionStatus.EXECUTED for r in retried_twice)
    assert await _notification_count(tool_registry._session_factory, tenant_id) == 1


async def test_audit_records_full_lifecycle(tool_registry, event_bus, approval_required_notification) -> None:
    from app.tools.errors import ToolApprovalRequiredError

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id, actor_id=uuid.uuid4())
    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)
    approval_id = exc_info.value.approval_request_id

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, event_bus)
    await service.approve(tenant_id, approval_id, decided_by_id=uuid.uuid4(), decided_by_role=Role.OWNER)

    async with tool_registry._session_factory() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.entity_id == approval_id)
            )
        ).scalars().all()
    actions = {r.action for r in rows}
    assert "approval.approve" in actions
    assert "approval.execution.completed" in actions


async def test_approval_events_are_emitted(tool_registry, event_bus, approval_required_notification) -> None:
    from app.tools.errors import ToolApprovalRequiredError

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id, actor_id=uuid.uuid4())
    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)
    approval_id = exc_info.value.approval_request_id

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, event_bus)
    await service.approve(tenant_id, approval_id, decided_by_id=uuid.uuid4(), decided_by_role=Role.OWNER)

    async with tool_registry._session_factory() as session:
        rows = (
            await session.execute(
                select(Event).where(Event.tenant_id == tenant_id, Event.entity_id == approval_id)
            )
        ).scalars().all()
    event_types = {r.event_type for r in rows}
    assert EventType.APPROVAL_REQUESTED in event_types
    assert EventType.APPROVAL_APPROVED in event_types
    assert EventType.APPROVAL_EXECUTION_STARTED in event_types
    assert EventType.APPROVAL_EXECUTION_COMPLETED in event_types


async def test_new_permissions_are_granted_to_manager_not_technician() -> None:
    assert role_has_permission(Role.MANAGER, Permission.APPROVE_ACTIONS)
    assert role_has_permission(Role.MANAGER, Permission.READ_APPROVALS)
    assert not role_has_permission(Role.TECHNICIAN, Permission.APPROVE_ACTIONS)
    assert not role_has_permission(Role.STAFF, Permission.APPROVE_ACTIONS)
