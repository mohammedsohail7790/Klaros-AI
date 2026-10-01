"""section 3: Content Engine. Publishing is gated by the *existing*
`ApprovalRequest` model — content only leaves `PENDING_APPROVAL` via a
real, permission-gated `approve_content` call, never an AI self-approval.
`InternalTestContentPublicationAdapter`-equivalent behavior lives directly
here (records a real `ContentPublication` row) since there is no real
social/blog publishing API connected — same "internal but real" pattern
as Phase 5's invoice delivery.
"""

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.events.bus import EventBus
from app.models.event import EventType
from app.models.marketing import (
    ContentAsset,
    ContentPublication,
    ContentStatus,
    ContentVariant,
    MarketingContent,
)
from app.models.actor import ActorType
from app.models.operations import Job, JobAttachment
from app.models.retention import CustomerFeedback, FeedbackSentiment
from app.services.ai_content_service import (
    JobContentInput,
    generate_job_caption,
    generate_job_caption_via_ai,
    is_llm_connected,
)
from app.services.ai_invocation_log_service import record_ai_invocation
from app.services.ai_provider import AIProvider
from app.services.approval_helper import create_approval_request
from app.services.company_memory_service import CompanyMemoryService, format_context_as_text


class JobNotFoundError(Exception):
    pass


class ContentNotFoundError(Exception):
    pass


class InvalidContentTransitionError(Exception):
    pass


class FeedbackNotFoundError(Exception):
    pass


class FeedbackNotEligibleError(Exception):
    """Raised when a CustomerFeedback row does not meet the deterministic
    eligibility bar (rating >= settings.REVIEW_MARKETING_MIN_RATING) —
    never overridden by AI judgment of "how positive" a comment sounds."""


class ConsentRequiredError(Exception):
    """Raised whenever consent_to_use_publicly is not exactly True — this
    is the one hard, non-negotiable gate: no review becomes marketing
    content, however positive, without the customer's explicit consent."""


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(\+?\d[\d\-. ()]{7,}\d)")


def _redact_pii(text: str) -> str:
    """Best-effort removal of an email/phone number a customer's free-text
    review might contain — defense in depth on top of the consent gate,
    never a substitute for it. Never claims to catch every possible PII
    pattern; only the two most common, unambiguous ones."""
    text = _EMAIL_RE.sub("[redacted]", text)
    text = _PHONE_RE.sub("[redacted]", text)
    return text


class ContentService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus, ai_provider: AIProvider | None = None) -> None:
        self._session_factory = session_factory
        self._bus = bus
        # Phase 15: real Company Memory integration + the real LLM caption
        # path (see generate_draft_from_job below). `ai_provider` is
        # optional/keyword so every existing 2-arg call site (factory.py,
        # tests) is unaffected; defaults to the same real
        # get_ai_provider() factory Morning Brief/Qualification use.
        if ai_provider is None:
            from app.services.ai_provider import get_ai_provider

            ai_provider = get_ai_provider()
        self._ai_provider = ai_provider
        self._memory = CompanyMemoryService(session_factory)

    async def create_idea(self, tenant_id: uuid.UUID, *, title: str, summary: str | None, created_by: uuid.UUID | None) -> MarketingContent:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            content = MarketingContent(
                tenant_id=tenant_id, title=title, summary=summary, status=ContentStatus.IDEA, created_by=created_by,
            )
            session.add(content)
            await session.commit()
            await session.refresh(content)

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.MARKETING_CONTENT_CREATED, source="marketing",
            entity_type="marketing_content", entity_id=content.id, payload={"content_id": str(content.id)},
        )
        return content

    async def create_content_from_feedback(
        self, tenant_id: uuid.UUID, feedback_id: uuid.UUID, created_by: uuid.UUID | None
    ) -> MarketingContent:
        """The reviews -> marketing proof loop, gated hard on two
        deterministic, server-verified conditions — never trusted from the
        caller: (1) rating >= settings.REVIEW_MARKETING_MIN_RATING, (2)
        consent_to_use_publicly is exactly True. Idempotent per feedback
        row (one content idea per review, mirroring generate_draft_from_job's
        one-per-job rule). The generated content is the customer's own
        words, PII-redacted, never rewritten or embellished — this method
        does not call an LLM."""
        settings = get_settings()
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            feedback = await session.get(CustomerFeedback, feedback_id)
            if feedback is None or feedback.tenant_id != tenant_id:
                raise FeedbackNotFoundError("Feedback not found")

            existing = (
                await session.execute(
                    select(MarketingContent).where(
                        MarketingContent.tenant_id == tenant_id,
                        MarketingContent.source_feedback_id == feedback_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

            if feedback.consent_to_use_publicly is not True:
                raise ConsentRequiredError(
                    "This review has no recorded customer consent to be used publicly — "
                    "record consent first (retention.record_review_consent)."
                )
            if (
                feedback.sentiment != FeedbackSentiment.POSITIVE
                or feedback.rating is None
                or feedback.rating < settings.REVIEW_MARKETING_MIN_RATING
            ):
                raise FeedbackNotEligibleError(
                    f"Feedback rating {feedback.rating} does not meet the "
                    f"eligibility threshold ({settings.REVIEW_MARKETING_MIN_RATING})."
                )

            comment = _redact_pii(feedback.comment) if feedback.comment else None
            content = MarketingContent(
                tenant_id=tenant_id,
                source_feedback_id=feedback_id,
                title=f"Customer testimonial ({feedback.rating}/5)",
                summary=comment,
                status=ContentStatus.DRAFT,
                created_by=created_by,
                ai_generated=False,
            )
            session.add(content)
            await session.commit()
            await session.refresh(content)

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.MARKETING_CONTENT_CREATED, source="marketing",
            entity_type="marketing_content", entity_id=content.id, payload={"content_id": str(content.id)},
        )
        return content

    async def generate_draft_from_job(
        self, tenant_id: uuid.UUID, job_id: uuid.UUID, created_by: uuid.UUID | None,
        *, actor_type: ActorType = ActorType.USER, correlation_id: uuid.UUID | None = None,
    ) -> MarketingContent:
        """Grounds the draft in real job data — real attachments, real
        notes, real service type/location. Never invents a result, photo,
        or testimonial. Idempotent per job (one content idea per job).

        Phase 15: when `is_llm_connected()`, calls the real
        `generate_job_caption_via_ai` (governed `AIProvider.
        generate_structured()` boundary, real `AIInvocationLog` row,
        Company Memory context fenced as DATA) and falls back to the
        deterministic template only if that call fails — never a silent
        downgrade presented as the real thing. When no provider is
        configured (the only case in this sandbox), behavior is
        byte-for-byte unchanged from before this phase."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id:
                raise JobNotFoundError("Job not found")

            existing = (
                await session.execute(
                    select(MarketingContent).where(
                        MarketingContent.tenant_id == tenant_id, MarketingContent.source_job_id == job_id
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

            attachments = (
                await session.execute(
                    select(JobAttachment).where(
                        JobAttachment.tenant_id == tenant_id, JobAttachment.job_id == job_id,
                        JobAttachment.kind == "PHOTO",
                    )
                )
            ).scalars().all()

            job_title = job.title
            attachment_ids = [a.id for a in attachments]
            job_input = JobContentInput(
                service_type=job.service_type, city=job.location, job_title=job_title,
                notes=job.internal_notes, photo_count=len(attachments),
            )

        caption = None
        if is_llm_connected():
            company_memory = format_context_as_text(await self._memory.get_context(tenant_id))
            caption, outcome = await generate_job_caption_via_ai(
                job_input, self._ai_provider, company_memory=company_memory,
            )
            await record_ai_invocation(
                self._session_factory, tenant_id=tenant_id, actor_type=actor_type, actor_id=created_by,
                operation="marketing_caption_generation", outcome=outcome, correlation_id=correlation_id,
                input_metadata={"job_id": str(job_id), "company_memory_used": company_memory is not None},
            )
        if caption is None:
            # Either no provider configured, or the real call failed —
            # both fall back to the deterministic template honestly
            # (never silently presented as an LLM result).
            caption = generate_job_caption(job_input)

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            content = MarketingContent(
                tenant_id=tenant_id, source_job_id=job_id, title=job_title, summary=caption.text,
                status=ContentStatus.DRAFT, created_by=created_by, ai_generated=True,
            )
            session.add(content)
            await session.flush()

            for attachment_id in attachment_ids:
                session.add(
                    ContentAsset(tenant_id=tenant_id, content_id=content.id, job_attachment_id=attachment_id)
                )

            await session.commit()
            await session.refresh(content)

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.MARKETING_CONTENT_CREATED, source="marketing",
            entity_type="marketing_content", entity_id=content.id,
            payload={
                "content_id": str(content.id), "job_id": str(job_id), "llm_connected": is_llm_connected(),
                "caption_source": caption.source,
            },
        )
        return content

    async def add_variant(self, tenant_id: uuid.UUID, content_id: uuid.UUID, *, channel: str, body_text: str | None) -> ContentVariant:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            content = await session.get(MarketingContent, content_id)
            if content is None or content.tenant_id != tenant_id:
                raise ContentNotFoundError("Content not found")
            variant = ContentVariant(tenant_id=tenant_id, content_id=content_id, channel=channel, body_text=body_text)
            session.add(variant)
            await session.commit()
            await session.refresh(variant)
        return variant

    async def request_approval(self, tenant_id: uuid.UUID, content_id: uuid.UUID, requested_by: uuid.UUID | None) -> MarketingContent:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            content = await session.get(MarketingContent, content_id)
            if content is None or content.tenant_id != tenant_id:
                raise ContentNotFoundError("Content not found")
            if content.status not in (ContentStatus.IDEA, ContentStatus.DRAFT):
                raise InvalidContentTransitionError(f"Cannot request approval from status {content.status}")

            content.status = ContentStatus.PENDING_APPROVAL
            await create_approval_request(
                session, tenant_id=tenant_id, requested_by_type="USER", requested_by_id=requested_by,
                tool_name="marketing.approve_content", action_type="content_approval",
                reason=f"Content '{content.title}' requested for publication approval",
                tool_input={"content_id": str(content_id)},
            )
            await session.commit()
            await session.refresh(content)
        return content

    async def decide_approval(self, tenant_id: uuid.UUID, content_id: uuid.UUID, *, approved: bool, decided_by: uuid.UUID | None) -> MarketingContent:
        from app.models.approval import ApprovalRequest, ApprovalStatus

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            content = await session.get(MarketingContent, content_id)
            if content is None or content.tenant_id != tenant_id:
                raise ContentNotFoundError("Content not found")
            if content.status != ContentStatus.PENDING_APPROVAL:
                raise InvalidContentTransitionError("Content is not pending approval")

            pending = (
                await session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.tenant_id == tenant_id,
                        ApprovalRequest.tool_name == "marketing.approve_content",
                        ApprovalRequest.status == ApprovalStatus.PENDING,
                    )
                )
            ).scalars().all()
            for req in pending:
                if req.tool_input.get("content_id") == str(content_id):
                    req.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
                    req.decided_by = decided_by

            content.status = ContentStatus.APPROVED if approved else ContentStatus.DRAFT
            await session.commit()
            await session.refresh(content)

        if approved:
            await self._bus.publish(
                tenant_id=tenant_id, event_type=EventType.MARKETING_CONTENT_APPROVED, source="marketing",
                entity_type="marketing_content", entity_id=content_id, payload={"content_id": str(content_id)},
            )
        return content

    async def publish_variant(self, tenant_id: uuid.UUID, content_variant_id: uuid.UUID) -> ContentPublication:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            variant = await session.get(ContentVariant, content_variant_id)
            if variant is None or variant.tenant_id != tenant_id:
                raise ContentNotFoundError("Content variant not found")
            content = await session.get(MarketingContent, variant.content_id)
            if content is None or content.status != ContentStatus.APPROVED:
                raise InvalidContentTransitionError("Content must be APPROVED before publishing")

            variant.status = ContentStatus.PUBLISHED
            publication = ContentPublication(
                tenant_id=tenant_id, content_variant_id=content_variant_id,
                published_at=datetime.now(timezone.utc), provider="internal_test_publication",
                external_reference=f"internal-pub-{uuid.uuid4().hex[:12]}", status=ContentStatus.PUBLISHED,
            )
            session.add(publication)
            content.status = ContentStatus.PUBLISHED
            await session.commit()
            await session.refresh(publication)

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.MARKETING_CONTENT_PUBLISHED, source="marketing",
            entity_type="content_publication", entity_id=publication.id,
            payload={"content_variant_id": str(content_variant_id)},
        )
        return publication
