"""Live Temporal workflow tests using temporalio's ephemeral local test server.

Unlike the rest of the suite, these do NOT run against sqlite — the
temporalio test environment downloads and runs a real, standalone Temporal
server binary (via network, no Docker needed) and a real worker polls it.
If that download/binary can't run in this sandbox, these tests are skipped
rather than faked — see the skip reason for exactly what failed.
"""

import uuid

import pytest

try:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
except ImportError:  # pragma: no cover
    WorkflowEnvironment = None  # type: ignore

from app.workflows.activities import ACTIVITIES
from app.workflows.definitions import (
    EventProcessingInput,
    EventProcessingWorkflow,
    InvoiceOverdueInput,
    InvoiceOverdueWorkflow,
)

pytestmark = pytest.mark.asyncio

TASK_QUEUE = "klaros-test-queue"


@pytest.fixture(scope="module")
async def temporal_env():
    if WorkflowEnvironment is None:
        pytest.skip("temporalio not installed")
    try:
        async with await WorkflowEnvironment.start_local() as env:
            yield env
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Could not start local Temporal test server: {exc}")


async def test_event_processing_workflow_executes_tool(temporal_env) -> None:
    async with Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[EventProcessingWorkflow],
        activities=ACTIVITIES,
    ):
        tenant_id = str(uuid.uuid4())
        result = await temporal_env.client.execute_workflow(
            EventProcessingWorkflow.run,
            EventProcessingInput(
                event_id=str(uuid.uuid4()),
                tool_name="system.get_current_time",
                tool_input={},
                tenant_id=tenant_id,
                role=None,
                correlation_id=None,
            ),
            id=f"event-processing-{uuid.uuid4()}",
            task_queue=TASK_QUEUE,
        )
        assert "utc_now" in result


async def test_invoice_overdue_workflow_escalates_when_unpaid(temporal_env) -> None:
    async with Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[InvoiceOverdueWorkflow],
        activities=ACTIVITIES,
    ):
        tenant_id = str(uuid.uuid4())
        result = await temporal_env.client.execute_workflow(
            InvoiceOverdueWorkflow.run,
            InvoiceOverdueInput(
                invoice_id="inv_test_1",
                tenant_id=tenant_id,
                reminder_wait_seconds=1,
                payment_check_wait_seconds=1,
            ),
            id=f"invoice-overdue-{uuid.uuid4()}",
            task_queue=TASK_QUEUE,
        )
        assert result["paid"] is False
        assert result["escalated"] is True
