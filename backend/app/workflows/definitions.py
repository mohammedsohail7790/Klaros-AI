"""Temporal workflow definitions (section 3).

EventProcessingWorkflow is the generic shape from the spec:

    Event received -> start workflow -> execute action (typed tool)
    -> verify result -> record audit -> complete

InvoiceOverdueWorkflow is the demo long-running workflow:

    invoice.overdue -> wait -> send reminder -> wait -> check payment
    -> if unpaid -> escalate (notification)

Both call into `execute_tool_activity`, so every side effect they cause goes
through the same permission/tenant/schema/policy pipeline as any other tool
call — a workflow has no back door.
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import (
        check_payment_status_activity,
        execute_tool_activity,
        qualify_lead_activity,
        send_reminder_activity,
    )

DEFAULT_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_attempts=3,
)


@dataclass
class EventProcessingInput:
    event_id: str
    tool_name: str
    tool_input: dict[str, Any]
    tenant_id: str
    role: str | None
    correlation_id: str | None


@workflow.defn
class EventProcessingWorkflow:
    @workflow.run
    async def run(self, input: EventProcessingInput) -> dict[str, Any]:
        result = await workflow.execute_activity(
            execute_tool_activity,
            args=[input.tool_name, input.tool_input, input.tenant_id, input.role, input.correlation_id],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY_POLICY,
        )
        return result


@dataclass
class InvoiceOverdueInput:
    invoice_id: str
    tenant_id: str
    reminder_wait_seconds: int = 1
    payment_check_wait_seconds: int = 1


@workflow.defn
class InvoiceOverdueWorkflow:
    @workflow.run
    async def run(self, input: InvoiceOverdueInput) -> dict[str, Any]:
        await asyncio.sleep(input.reminder_wait_seconds)

        await workflow.execute_activity(
            send_reminder_activity,
            args=[input.invoice_id, input.tenant_id],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=DEFAULT_RETRY_POLICY,
        )

        await asyncio.sleep(input.payment_check_wait_seconds)

        status = await workflow.execute_activity(
            check_payment_status_activity,
            args=[input.invoice_id, input.tenant_id],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=DEFAULT_RETRY_POLICY,
        )

        escalated = False
        if not status["paid"]:
            await workflow.execute_activity(
                execute_tool_activity,
                args=[
                    "notifications.create_notification",
                    {
                        "title": "Invoice still overdue",
                        "body": f"Invoice {input.invoice_id} is unpaid after a reminder was sent.",
                        "severity": "HIGH",
                        "category": "finance",
                    },
                    input.tenant_id,
                    None,
                    None,
                ],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=DEFAULT_RETRY_POLICY,
            )
            escalated = True

        return {"paid": status["paid"], "escalated": escalated}


@dataclass
class LeadQualificationInput:
    lead_id: str
    tenant_id: str


@workflow.defn
class LeadQualificationWorkflow:
    """section 24:

        lead.created -> load lead -> enrich -> qualify -> persist result
        -> emit lead.qualified / lead.unqualified

    `qualify_lead_activity` does the load/enrich/qualify/persist/emit in one
    call (LeadQualificationService is the single implementation shared with
    the event-bus trigger path in app/events/crm_handlers.py) — this
    workflow's job is Temporal's retry/timeout envelope around that call, not
    reimplementing the logic.
    """

    @workflow.run
    async def run(self, input: LeadQualificationInput) -> dict[str, Any]:
        return await workflow.execute_activity(
            qualify_lead_activity,
            args=[input.lead_id, input.tenant_id],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY_POLICY,
        )


@dataclass
class JobLifecycleInput:
    job_id: str
    tenant_id: str
    role: str | None = None
    correlation_id: str | None = None


@workflow.defn
class JobLifecycleWorkflow:
    """section 32: durably, idempotently validates a newly created job.

    Deliberately narrow. Real job progression (schedule -> assign ->
    dispatch -> monitor -> QA -> completion -> close-out) is driven by
    explicit operator/API actions through the same ToolRegistry this
    workflow itself uses — not blind, automatic Temporal orchestration
    ("do not create a workflow that blindly performs every step without
    checking state"). A signal-driven version of this workflow that
    durably watches a job through its whole lifecycle is a natural
    extension; this one does the one step that's genuinely useful without
    a timer — idempotently validating the job is well-formed — via the
    same `execute_tool_activity` every other workflow in this project
    uses, so it inherits the same tested authorization/audit path.
    """

    @workflow.run
    async def run(self, input: JobLifecycleInput) -> dict[str, Any]:
        result = await workflow.execute_activity(
            execute_tool_activity,
            args=[
                "operations.get_job",
                {"job_id": input.job_id},
                input.tenant_id,
                input.role,
                input.correlation_id,
            ],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY_POLICY,
        )
        job = result.get("job", {})
        issues: list[str] = []
        if not job.get("customer_id"):
            issues.append("missing customer_id")
        if not job.get("title"):
            issues.append("missing title")

        return {"job_id": input.job_id, "valid": not issues, "issues": issues}
