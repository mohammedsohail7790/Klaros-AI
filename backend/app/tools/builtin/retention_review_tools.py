import uuid

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.review_service import ReviewRequestNotFoundError, ReviewService
from app.tools.base import ExecutionContext, Tool


class SendReviewRequestInput(BaseModel):
    review_request_id: uuid.UUID
    channel: str = "EMAIL"


class ReviewRequestOutput(BaseModel):
    review_request_id: str
    status: str
    channel: str


class SendReviewRequest(Tool):
    """`APPROVAL_REQUIRED` by policy — a review request reaches a real
    customer channel. Only ever sent for ELIGIBLE requests (job CLOSED, no
    unresolved negative feedback) — see review_service.py."""

    name = "retention.send_review_request"
    description = "Send an ELIGIBLE review request via the internal test communication provider."
    input_schema = SendReviewRequestInput
    output_schema = ReviewRequestOutput
    required_permission = Permission.MANAGE_REVIEWS

    def __init__(self, review_service: ReviewService) -> None:
        self._review_service = review_service

    async def execute(self, input: SendReviewRequestInput, context: ExecutionContext) -> ReviewRequestOutput:
        try:
            review = await self._review_service.send_review_request(context.tenant_id, input.review_request_id, input.channel)
        except (ValueError, ReviewRequestNotFoundError) as e:
            raise ValueError(str(e)) from e
        return ReviewRequestOutput(review_request_id=str(review.id), status=review.status, channel=review.channel)
