"""Live Temporal test for the automation engine's one durable-wait step
(app/workflows/automation_workflow.py). Uses the same ephemeral local
Temporal test server pattern as tests/test_temporal_workflows.py — a real
server, a real Worker, a real `asyncio.sleep` inside a `@workflow.defn`
durably patched into a timer by the Temporal Python SDK. Proves a
WAITING execution actually resumes and runs its remaining governed
step after the real timer fires — not a fragile in-process timer.
"""

import uuid

import pytest

try:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
except ImportError:  # pragma: no cover
    WorkflowEnvironment = None  # type: ignore

from sqlalchemy import select

from app.ai.execution_service import AIExecutionService
from app.db.session import async_session_maker
from app.models.automation import AutomationExecution, ExecutionStatus, StepStatus, TriggerType
from app.models.notification import Notification
from app.services.automation_service import AutomationService
from app.workflows.activities import ACTIVITIES
from app.workflows.automation_workflow import AutomationWaitInput, AutomationWaitWorkflow

pytestmark = pytest.mark.asyncio

TASK_QUEUE = "klaros-automation-test-queue"


@pytest.fixture(scope="module")
async def temporal_env():
    if WorkflowEnvironment is None:
        pytest.skip("temporalio not installed")
    try:
        async with await WorkflowEnvironment.start_local() as env:
            yield env
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Could not start local Temporal test server: {exc}")


async def test_wait_workflow_resumes_execution_and_runs_remaining_steps(temporal_env, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = AutomationService(async_session_maker, AIExecutionService(tool_registry))

    automation = await service.create_automation(
        tenant_id, name="Wait Then Notify", description=None, trigger_type=TriggerType.MANUAL, trigger_config={},
        condition=None,
        steps=[
            {"action": "wait", "params": {"seconds": 1}},
            {"action": "notifications.create_notification", "params": {"title": "Resumed", "body": "after wait"}},
        ],
        created_by=None,
    )
    published = await service.publish(tenant_id, automation.id)
    version = (await service.list_versions(tenant_id, automation.id))[0]

    # Construct the WAITING execution row exactly like
    # AutomationService._start_durable_wait would, but without going
    # through start_execution's own (differently-configured) Temporal
    # client — the workflow itself, driven by the real test server, is
    # what's under test here.
    async with async_session_maker() as session:
        from datetime import datetime, timezone

        execution = AutomationExecution(
            tenant_id=tenant_id, automation_id=published.id, automation_version_id=version.id,
            trigger_type=TriggerType.MANUAL, status=ExecutionStatus.WAITING, context={},
            started_at=datetime.now(timezone.utc),
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)

    async with Worker(
        temporal_env.client, task_queue=TASK_QUEUE, workflows=[AutomationWaitWorkflow], activities=ACTIVITIES,
    ):
        result = await temporal_env.client.execute_workflow(
            AutomationWaitWorkflow.run,
            AutomationWaitInput(execution_id=str(execution.id), tenant_id=str(tenant_id), wait_seconds=1),
            id=f"automation-wait-{execution.id}",
            task_queue=TASK_QUEUE,
        )
    assert result["status"] == "resumed"

    async with async_session_maker() as session:
        refreshed = await session.get(AutomationExecution, execution.id)
        assert refreshed.status == ExecutionStatus.COMPLETED

        steps = (
            await session.execute(
                select(AutomationExecution).where(AutomationExecution.id == execution.id)
            )
        ).scalar_one()
        assert steps.completed_at is not None

    async with async_session_maker() as session:
        from app.models.automation import AutomationExecutionStep

        step_rows = (
            await session.execute(
                select(AutomationExecutionStep).where(AutomationExecutionStep.execution_id == execution.id)
            )
        ).scalars().all()
    assert len(step_rows) == 1
    assert step_rows[0].status == StepStatus.SUCCEEDED

    async with async_session_maker() as session:
        notifications = (
            await session.execute(
                select(Notification).where(Notification.tenant_id == tenant_id, Notification.title == "Resumed")
            )
        ).scalars().all()
    assert len(notifications) == 1


async def test_wait_workflow_condition_not_met_completes_without_running_the_step(temporal_env, tool_registry) -> None:
    tenant_id = uuid.uuid4()
    service = AutomationService(async_session_maker, AIExecutionService(tool_registry))

    automation = await service.create_automation(
        tenant_id, name="Wait Then Gate", description=None, trigger_type=TriggerType.MANUAL, trigger_config={},
        condition={"field": "lead.score", "op": "gte", "value": 999999},
        steps=[
            {"action": "wait", "params": {"seconds": 1}},
            {"action": "notifications.create_notification", "params": {"title": "Should Not Fire", "body": "x"}},
        ],
        created_by=None,
    )
    published = await service.publish(tenant_id, automation.id)
    version = (await service.list_versions(tenant_id, automation.id))[0]

    async with async_session_maker() as session:
        from datetime import datetime, timezone

        execution = AutomationExecution(
            tenant_id=tenant_id, automation_id=published.id, automation_version_id=version.id,
            trigger_type=TriggerType.MANUAL, status=ExecutionStatus.WAITING, context={"lead": {"score": 1}},
            started_at=datetime.now(timezone.utc),
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)

    async with Worker(
        temporal_env.client, task_queue=TASK_QUEUE, workflows=[AutomationWaitWorkflow], activities=ACTIVITIES,
    ):
        result = await temporal_env.client.execute_workflow(
            AutomationWaitWorkflow.run,
            AutomationWaitInput(execution_id=str(execution.id), tenant_id=str(tenant_id), wait_seconds=1),
            id=f"automation-wait-gated-{execution.id}",
            task_queue=TASK_QUEUE,
        )
    assert result["status"] == "condition_not_met"

    async with async_session_maker() as session:
        refreshed = await session.get(AutomationExecution, execution.id)
        assert refreshed.status == ExecutionStatus.COMPLETED

    async with async_session_maker() as session:
        notifications = (
            await session.execute(
                select(Notification).where(Notification.tenant_id == tenant_id, Notification.title == "Should Not Fire")
            )
        ).scalars().all()
    assert notifications == []
