"""Background worker entrypoint.

Phase 1 placeholder: the Temporal worker (event bus consumers, workflow
activities) lands in Phase 2. This process stays up and healthy so
docker-compose's worker service doesn't crash-loop while that work is
pending.
"""

import asyncio

from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


async def main() -> None:
    logger.info("klaros_worker_placeholder_started", note="Temporal workflows not yet wired up")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
