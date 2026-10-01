"""Phase 17B-2R: real-PostgreSQL behavioral proof that ApprovalExecutionService's
own, independently-opened sessions (7 sites) now stamp `SET LOCAL
app.tenant_id`. Given special care per the coordinator's instruction
(security-sensitive Agent approval flow, treated like payment_service.py):
this test never weakens the CAS-based approve/reject state transitions,
never introduces a new tenant-resolution mechanism, and only adds context
using the already-trusted `tenant_id` parameter (sourced from
CurrentUser/authenticated caller in production, exactly as `_load`'s own
tenant-ownership check already relies on). Same spy methodology as
`test_tenant_context_automation_service_phase17b2r.py`.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.approval import ApprovalStatus
from app.models.rbac import Role
from app.services.approval_execution_service import ApprovalExecutionService, ApprovalNotFoundError
from app.tools.base import ExecutionContext
from app.tools.errors import ToolApprovalRequiredError
from app.tools.policy import ActionPolicy, DEFAULT_TOOL_POLICIES

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


class _ContextSpy:
    def __init__(self):
        self.calls: list[tuple[uuid.UUID | None, str | None]] = []

    async def __call__(self, session, tenant_id):
        await set_tenant_context(session, tenant_id)
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            readback = await session.scalar(text("select current_setting('app.tenant_id', true)"))
        else:
            readback = None
        self.calls.append((tenant_id, readback))


@pytest.fixture
def spy():
    return _ContextSpy()


@pytest.fixture
def approval_required_notification():
    DEFAULT_TOOL_POLICIES["notifications.create_notification"] = ActionPolicy.APPROVAL_REQUIRED
    yield


def _ctx(tenant_id: uuid.UUID, *, actor_id=None) -> ExecutionContext:
    return ExecutionContext(
        tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=actor_id or uuid.uuid4(), role=Role.OWNER,
    )


@requires_real_postgres
async def test_approve_sets_tenant_context_on_every_session(
    monkeypatch, spy, tool_registry, event_bus, approval_required_notification
) -> None:
    import app.services.approval_execution_service as aes_module

    monkeypatch.setattr(aes_module, "set_tenant_context", spy)

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
    assert len(spy.calls) >= 3  # approve's own CAS session + execute_approved's + _record_execution_outcome's
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_approve_tenant_bs_approval_request(
    tool_registry, event_bus, approval_required_notification
) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    ctx_a = _ctx(tenant_a)

    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx_a)
    approval_id = exc_info.value.approval_request_id

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, event_bus)

    with pytest.raises(ApprovalNotFoundError):
        await service.approve(tenant_b, approval_id, decided_by_id=uuid.uuid4(), decided_by_role=Role.OWNER)


@requires_real_postgres
async def test_reject_sets_tenant_context(monkeypatch, spy, tool_registry, event_bus, approval_required_notification) -> None:
    import app.services.approval_execution_service as aes_module

    monkeypatch.setattr(aes_module, "set_tenant_context", spy)

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    with pytest.raises(ToolApprovalRequiredError) as exc_info:
        await tool_registry.execute("notifications.create_notification", {"title": "x", "body": "y"}, ctx)
    approval_id = exc_info.value.approval_request_id

    service = ApprovalExecutionService(tool_registry._session_factory, tool_registry, event_bus)
    result = await service.reject(tenant_id, approval_id, decided_by_id=uuid.uuid4())

    assert result.status == ApprovalStatus.REJECTED
    assert len(spy.calls) >= 1
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)
