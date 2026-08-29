import uuid

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.review_service import ReviewService
from app.tools.base import ExecutionContext, Tool


class RecordFeedbackInput(BaseModel):
    customer_id: uuid.UUID
    job_id: uuid.UUID | None = None
    rating: int | None = None
    comment: str | None = None
    source: str = "manual"


class FeedbackOutput(BaseModel):
    feedback_id: str
    sentiment: str | None


class RecordFeedback(Tool):
    """Sentiment is always deterministic from `rating` — never an AI guess.
    Negative feedback (rating <= 2) never triggers a public review request
    — it opens a real SERVICE_RECOVERY_REQUIRED/NEGATIVE_FEEDBACK exception
    instead. See `app/services/review_service.py`."""

    name = "retention.record_feedback"
    description = "Record real customer feedback; rating <= 2 opens a service-recovery exception instead of a public review request."
    input_schema = RecordFeedbackInput
    output_schema = FeedbackOutput
    required_permission = Permission.MANAGE_REVIEWS

    def __init__(self, review_service: ReviewService) -> None:
        self._review_service = review_service

    async def execute(self, input: RecordFeedbackInput, context: ExecutionContext) -> FeedbackOutput:
        feedback = await self._review_service.record_feedback(
            context.tenant_id, customer_id=input.customer_id, job_id=input.job_id, rating=input.rating,
            comment=input.comment, source=input.source,
        )
        return FeedbackOutput(feedback_id=str(feedback.id), sentiment=feedback.sentiment)
