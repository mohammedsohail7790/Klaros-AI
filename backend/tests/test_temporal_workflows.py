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
    JobLifecycleInput,
    JobLifecycleWorkflow,
    LeadQualificationInput,
    LeadQualificationWorkflow,
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


async def test_lead_qualification_workflow_scores_a_real_lead(temporal_env) -> None:
    from app.db.session import async_session_maker
    from app.models.crm import Lead, LeadStatus, QualificationStatus

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        lead = Lead(
            tenant_id=tenant_id,
            name="Temporal Test Lead",
            source="REFERRAL",
            service_requested="Emergency repair",
            urgency="EMERGENCY",
            estimated_value=5000,
            status=LeadStatus.NEW,
            qualification_status=QualificationStatus.PENDING,
        )
        session.add(lead)
        await session.commit()
        await session.refresh(lead)

    async with Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[LeadQualificationWorkflow],
        activities=ACTIVITIES,
    ):
        result = await temporal_env.client.execute_workflow(
            LeadQualificationWorkflow.run,
            LeadQualificationInput(lead_id=str(lead.id), tenant_id=str(tenant_id)),
            id=f"lead-qualification-{uuid.uuid4()}",
            task_queue=TASK_QUEUE,
        )
        assert result["qualification_status"] == "QUALIFIED"

    async with async_session_maker() as session:
        refreshed = await session.get(Lead, lead.id)
        assert refreshed.qualification_status == QualificationStatus.QUALIFIED


async def test_job_lifecycle_workflow_validates_a_real_job(temporal_env) -> None:
    """section 32/50: JobLifecycleWorkflow does not use workflow.sleep() —
    verified independently, per the Phase 4 instruction not to introduce
    another unverified timer-based workflow."""
    from app.db.session import async_session_maker
    from app.models.crm import Customer
    from app.models.operations import Job, JobStatus

    tenant_id = uuid.uuid4()
    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="Temporal Job Customer")
        session.add(customer)
        await session.flush()
        job = Job(
            tenant_id=tenant_id,
            customer_id=customer.id,
            job_number="JOB-9001",
            title="Temporal validated job",
            status=JobStatus.DRAFT,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

    async with Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[JobLifecycleWorkflow],
        activities=ACTIVITIES,
    ):
        result = await temporal_env.client.execute_workflow(
            JobLifecycleWorkflow.run,
            JobLifecycleInput(job_id=str(job.id), tenant_id=str(tenant_id), role="OWNER"),
            id=f"job-lifecycle-{uuid.uuid4()}",
            task_queue=TASK_QUEUE,
        )
        assert result["valid"] is True
        assert result["issues"] == []


async def test_duplicate_workflow_id_is_rejected_by_the_real_temporal_server(temporal_env) -> None:
    """Phase 12B: starting a second workflow execution with a workflow ID
    that's already running must be rejected by Temporal itself (real
    server-side dedup), not merely by application code — proves at-most-
    once-per-ID semantics hold against the real server, not just a mock."""
    from temporalio.exceptions import WorkflowAlreadyStartedError

    async with Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[InvoiceOverdueWorkflow],
        activities=ACTIVITIES,
    ):
        tenant_id = str(uuid.uuid4())
        workflow_id = f"invoice-overdue-dup-{uuid.uuid4()}"
        workflow_input = InvoiceOverdueInput(
            invoice_id="inv_dup_test",
            tenant_id=tenant_id,
            reminder_wait_seconds=5,
            payment_check_wait_seconds=5,
        )

        handle = await temporal_env.client.start_workflow(
            InvoiceOverdueWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )

        with pytest.raises(WorkflowAlreadyStartedError):
            await temporal_env.client.start_workflow(
                InvoiceOverdueWorkflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=TASK_QUEUE,
            )

        # Let the first execution actually finish so the worker/test server
        # tear down cleanly rather than leaving an orphaned run.
        await handle.result()


async def test_worker_restart_resumes_a_workflow_started_by_the_previous_worker(temporal_env) -> None:
    """Phase 12B (Step 14): starts a workflow under one Worker, shuts that
    worker down entirely (simulating a process crash/restart), then brings
    up a brand-new Worker instance against the SAME task queue and confirms
    the in-flight workflow resumes and completes — proving no work is lost
    across a worker restart, verified against the real Temporal server
    (which persists workflow/task state independently of any single
    worker process)."""
    tenant_id = str(uuid.uuid4())
    workflow_id = f"invoice-overdue-restart-{uuid.uuid4()}"
    workflow_input = InvoiceOverdueInput(
        invoice_id="inv_restart_test",
        tenant_id=tenant_id,
        reminder_wait_seconds=1,
        payment_check_wait_seconds=1,
    )

    first_worker = Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[InvoiceOverdueWorkflow],
        activities=ACTIVITIES,
    )
    async with first_worker:
        handle = await temporal_env.client.start_workflow(
            InvoiceOverdueWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )
    # first_worker is now fully shut down (process "crashed"/restarted) —
    # the workflow is mid-flight (inside its sleep) with no worker polling
    # its task queue at all.

    second_worker = Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[InvoiceOverdueWorkflow],
        activities=ACTIVITIES,
    )
    async with second_worker:
        result = await handle.result()

    assert result["paid"] is False
    assert result["escalated"] is True
