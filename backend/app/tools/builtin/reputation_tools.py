"""reputation.sync_google_reviews: reads the business's connected Google Business Profile locations and reviews into Klaros's own listing and review records. It reads from
Google and writes only internal data (idempotent, never a duplicate review), so it is AUTO. It never posts a reply. With no Google connection it returns a clean `not_connected`."""

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.google_business_sync_service import GoogleBusinessSyncService
from app.tools.base import ExecutionContext, Tool


class SyncGoogleReviewsInput(BaseModel):
    pass


class SyncGoogleReviewsOutput(BaseModel):
    status: str  # synced | partial | error | not_connected
    detail: str | None = None
    locations: int = 0
    listings_created: int = 0
    reviews_created: int = 0
    reviews_updated: int = 0
    reviews_unchanged: int = 0
    low_rating_events: int = 0


class SyncGoogleReviews(Tool):
    name = "reputation.sync_google_reviews"
    description = (
        "Pull the business's Google Business Profile locations and customer reviews into listing and review records. Read-only on Google (never posts a reply); safe to repeat. "
        "Records an event for each new review of 3 stars or fewer. Returns status not_connected when Google Business Profile is not connected."
    )
    input_schema = SyncGoogleReviewsInput
    output_schema = SyncGoogleReviewsOutput
    required_permission = Permission.MANAGE_MARKETING_CONTENT

    def __init__(self, service: GoogleBusinessSyncService) -> None:
        self._service = service

    async def execute(self, input: SyncGoogleReviewsInput, context: ExecutionContext) -> SyncGoogleReviewsOutput:
        r = await self._service.sync(context.tenant_id)
        return SyncGoogleReviewsOutput(**{k: v for k, v in r.items() if k in SyncGoogleReviewsOutput.model_fields})
