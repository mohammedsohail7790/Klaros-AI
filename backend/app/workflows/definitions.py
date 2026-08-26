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

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import (
        check_payment_status_activity,
        execute_tool_activity,
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
        await workflow.sleep(timedelta(seconds=input.reminder_wait_seconds))

        await workflow.execute_activity(
            send_reminder_activity,
            args=[input.invoice_id, input.tenant_id],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=DEFAULT_RETRY_POLICY,
        )

        await workflow.sleep(timedelta(seconds=input.payment_check_wait_seconds))

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
