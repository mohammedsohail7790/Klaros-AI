"""The Automation Engine's durable-wait workflow — see
app/services/automation_service.py's `_start_durable_wait` docstring.
Mirrors `InvoiceOverdueWorkflow`'s existing `asyncio.sleep`-inside-a-
workflow pattern (app/workflows/definitions.py) exactly, which Temporal's
Python SDK durably patches into a real workflow timer — not a fragile
in-process timer that would be lost on worker restart.
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import resume_automation_execution_activity

_RETRY_POLICY = RetryPolicy(initial_interval=timedelta(seconds=1), backoff_coefficient=2.0, maximum_attempts=3)


@dataclass
class AutomationWaitInput:
    execution_id: str
    tenant_id: str
    wait_seconds: float


@workflow.defn
class AutomationWaitWorkflow:
    @workflow.run
    async def run(self, input: AutomationWaitInput) -> dict:
        await asyncio.sleep(input.wait_seconds)
        return await workflow.execute_activity(
            resume_automation_execution_activity,
            args=[input.execution_id, input.tenant_id],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_RETRY_POLICY,
        )


async def start_automation_wait_workflow(*, execution_id: str, tenant_id: str, wait_seconds: float) -> str:
    """Real Temporal client call — connects to the configured
    TEMPORAL_HOST and starts a real workflow execution. Raises if
    Temporal is unreachable; the caller (AutomationService) treats that as
    an honest execution failure, never a silent skip of the wait."""
    from app.temporal_client import get_temporal_client
    from app.core.config import get_settings

    settings = get_settings()
    client = await get_temporal_client()
    workflow_id = f"automation-wait-{execution_id}"
    await client.start_workflow(
        AutomationWaitWorkflow.run,
        AutomationWaitInput(execution_id=execution_id, tenant_id=tenant_id, wait_seconds=wait_seconds),
        id=workflow_id,
        task_queue=settings.TEMPORAL_TASK_QUEUE,
    )
    return workflow_id
