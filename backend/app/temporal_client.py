from temporalio.client import Client

from app.core.config import get_settings


async def get_temporal_client() -> Client:
    settings = get_settings()
    return await Client.connect(settings.TEMPORAL_HOST, namespace=settings.TEMPORAL_NAMESPACE)
