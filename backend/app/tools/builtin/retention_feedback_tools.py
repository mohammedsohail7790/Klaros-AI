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


class RecordReviewConsentInput(BaseModel):
    feedback_id: uuid.UUID
    consent: bool


class RecordReviewConsentOutput(BaseModel):
    feedback_id: str
    consent_to_use_publicly: bool | None


class RecordReviewConsent(Tool):
    """A human-only record of a fact confirmed with the customer directly
    (a verbal yes on a call, a reply granting permission) — never inferred,
    never defaulted to True. Required before ContentService can ever turn
    this review into public marketing content, regardless of rating.
    Gated by MANAGE_REVIEWS AND an explicit actor-type check: Role.MANAGER
    (the only role AIExecutionService has ever been invoked with) holds
    MANAGE_REVIEWS, so the permission alone is not enough — matches the
    same defensive pattern already applied to finance.approve_invoice/
    approve_refund after the Phase 12F self-approval finding."""

    name = "retention.record_review_consent"
    description = "Record whether a customer explicitly consented to their review being used publicly."
    input_schema = RecordReviewConsentInput
    output_schema = RecordReviewConsentOutput
    required_permission = Permission.MANAGE_REVIEWS

    def __init__(self, review_service: ReviewService) -> None:
        self._review_service = review_service

    async def execute(self, input: RecordReviewConsentInput, context: ExecutionContext) -> RecordReviewConsentOutput:
        from app.models.actor import ActorType

        if context.actor_type == ActorType.AI:
            raise ValueError("AI cannot record customer consent — this requires a human who actually confirmed it")
        feedback = await self._review_service.record_consent(
            context.tenant_id, input.feedback_id, consent=input.consent
        )
        return RecordReviewConsentOutput(
            feedback_id=str(feedback.id), consent_to_use_publicly=feedback.consent_to_use_publicly
        )
