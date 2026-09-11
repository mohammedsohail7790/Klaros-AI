"""section: Reviews & Feedback. `sentiment` is always computed
deterministically from `rating` — never an AI guess (1-2 negative, 3
neutral, 4-5 positive; no rating -> no sentiment). Negative feedback never
triggers a public review request — it opens a real
`SERVICE_RECOVERY_REQUIRED` exception via the *existing* exception engine
instead. External Google/Yelp remain NOT_CONNECTED; requests here are real,
internal records sent (when actually sent) via the *existing*
CommunicationProvider only.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import CommunicationProvider, MessageTemplate
from app.events.bus import EventBus
from app.models.crm import Customer
from app.models.event import EventType
from app.models.operations import ExceptionType
from app.models.retention import CustomerFeedback, FeedbackSentiment, ReviewRequest, ReviewStatus
from app.services.exception_service import ExceptionService
from app.services.retention_service import RetentionService


class ReviewRequestNotFoundError(Exception):
    pass


class FeedbackNotFoundError(Exception):
    pass


def _sentiment_for_rating(rating: int | None) -> str | None:
    if rating is None:
        return None
    if rating <= 2:
        return FeedbackSentiment.NEGATIVE
    if rating == 3:
        return FeedbackSentiment.NEUTRAL
    return FeedbackSentiment.POSITIVE


class ReviewService:
    def __init__(
        self, session_factory: async_sessionmaker, bus: EventBus, exception_service: ExceptionService,
        retention_service: RetentionService, comms: CommunicationProvider | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self._exception_service = exception_service
        self._retention_service = retention_service
        self._comms = comms

    async def send_review_request(self, tenant_id: uuid.UUID, review_request_id: uuid.UUID, channel: str = "EMAIL") -> ReviewRequest:
        async with self._session_factory() as session:
            review = await session.get(ReviewRequest, review_request_id)
            if review is None or review.tenant_id != tenant_id:
                raise ReviewRequestNotFoundError("Review request not found")
            if review.status != ReviewStatus.ELIGIBLE:
                raise ValueError(f"Review request is not ELIGIBLE (currently {review.status})")

            customer = await session.get(Customer, review.customer_id)
            review.status = ReviewStatus.REQUESTED
            review.channel = channel
            review.requested_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(review)

        if self._comms is not None and customer and customer.email:
            await self._comms.send_email(
                tenant_id, to=customer.email, subject="How did we do?",
                body="We'd love your feedback on the service you just received.",
                template=MessageTemplate.REVIEW_REQUEST,
            )

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.RETENTION_REVIEW_REQUEST_CREATED, source="retention",
            entity_type="review_request", entity_id=review.id, payload={"customer_id": str(review.customer_id)},
        )
        return review

    async def record_feedback(
        self, tenant_id: uuid.UUID, *, customer_id: uuid.UUID, job_id: uuid.UUID | None, rating: int | None,
        comment: str | None, source: str = "manual",
    ) -> CustomerFeedback:
        sentiment = _sentiment_for_rating(rating)
        async with self._session_factory() as session:
            feedback = CustomerFeedback(
                tenant_id=tenant_id, customer_id=customer_id, job_id=job_id, rating=rating, sentiment=sentiment,
                comment=comment, source=source, received_at=datetime.now(timezone.utc),
            )
            session.add(feedback)

            # If there's a matching ELIGIBLE/REQUESTED review request for this
            # job, mark it RECEIVED — a real response closes the loop rather
            # than leaving it dangling.
            if job_id is not None:
                review = (
                    await session.execute(
                        select(ReviewRequest).where(
                            ReviewRequest.tenant_id == tenant_id, ReviewRequest.job_id == job_id,
                            ReviewRequest.status.in_((ReviewStatus.ELIGIBLE, ReviewStatus.REQUESTED)),
                        )
                    )
                ).scalar_one_or_none()
                if review is not None:
                    review.status = ReviewStatus.RECEIVED

            await session.commit()
            await session.refresh(feedback)

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.RETENTION_FEEDBACK_RECEIVED, source="retention",
            entity_type="customer_feedback", entity_id=feedback.id,
            payload={"customer_id": str(customer_id), "sentiment": sentiment},
        )

        if sentiment == FeedbackSentiment.NEGATIVE:
            await self._exception_service.create_exception(
                tenant_id, type=ExceptionType.SERVICE_RECOVERY_REQUIRED, severity="HIGH", entity_type="customer",
                entity_id=customer_id,
                description=f"Customer left a rating of {rating}/5" + (f": \"{comment}\"" if comment else "."),
                recommended_action="Owner/staff should review and follow up directly — do not request a public review.",
            )
            await self._exception_service.create_exception(
                tenant_id, type=ExceptionType.NEGATIVE_FEEDBACK, severity="MEDIUM", entity_type="customer",
                entity_id=customer_id, description=f"Negative feedback recorded (rating {rating}/5).",
                recommended_action="Service recovery follow-up.",
            )
            await self._bus.publish(
                tenant_id=tenant_id, event_type=EventType.RETENTION_SERVICE_RECOVERY_REQUIRED, source="retention",
                entity_type="customer", entity_id=customer_id, payload={"customer_id": str(customer_id)},
            )
        elif sentiment == FeedbackSentiment.POSITIVE:
            await self._bus.publish(
                tenant_id=tenant_id, event_type=EventType.RETENTION_REVIEW_RECEIVED, source="retention",
                entity_type="customer", entity_id=customer_id, payload={"customer_id": str(customer_id)},
            )
            await self._retention_service.create_referral_eligibility_opportunity(
                tenant_id, customer_id, reason=f"Customer left positive feedback (rating {rating}/5) — good referral candidate."
            )

        return feedback

    async def record_consent(
        self, tenant_id: uuid.UUID, feedback_id: uuid.UUID, *, consent: bool
    ) -> CustomerFeedback:
        """A human recording a fact they've confirmed with the customer
        (a verbal yes on a call, a reply to an email asking permission,
        etc.) — this is the ONLY way consent_to_use_publicly is ever set;
        nothing infers or defaults it to True. Reuses the existing 'staff
        records what they know to be true' pattern (same shape as
        recording a manual test payment) rather than inventing an
        automated consent-collection flow this repository has no
        requirements for."""
        async with self._session_factory() as session:
            feedback = await session.get(CustomerFeedback, feedback_id)
            if feedback is None or feedback.tenant_id != tenant_id:
                raise FeedbackNotFoundError("Feedback not found")
            feedback.consent_to_use_publicly = consent
            await session.commit()
            await session.refresh(feedback)
        return feedback
