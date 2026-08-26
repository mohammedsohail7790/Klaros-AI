"""Background worker entrypoint.

Runs a real Temporal worker, polling TEMPORAL_TASK_QUEUE for the workflows
and activities defined in app/workflows/. Requires a reachable Temporal
server (see docker-compose.yml's `temporal` service) — this process will
retry its connection and log the failure rather than crash-looping silently.
"""

import asyncio

from temporalio.worker import Worker

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.temporal_client import get_temporal_client
from app.workflows.activities import ACTIVITIES
from app.workflows.definitions import (
    EventProcessingWorkflow,
    InvoiceOverdueWorkflow,
    JobLifecycleWorkflow,
    LeadQualificationWorkflow,
)

configure_logging()
logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    logger.info("klaros_worker_connecting", temporal_host=settings.TEMPORAL_HOST)

    while True:
        try:
            client = await get_temporal_client()
            worker = Worker(
                client,
                task_queue=settings.TEMPORAL_TASK_QUEUE,
                workflows=[
                    EventProcessingWorkflow,
                    InvoiceOverdueWorkflow,
                    LeadQualificationWorkflow,
                    JobLifecycleWorkflow,
                ],
                activities=ACTIVITIES,
            )
            logger.info("klaros_worker_started", task_queue=settings.TEMPORAL_TASK_QUEUE)
            await worker.run()
        except Exception as exc:  # noqa: BLE001 — must never crash-loop silently
            logger.error("klaros_worker_connection_failed", error=str(exc))
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
