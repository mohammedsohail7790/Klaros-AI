"""section 3: Content Engine. Publishing is gated by the *existing*
`ApprovalRequest` model — content only leaves `PENDING_APPROVAL` via a
real, permission-gated `approve_content` call, never an AI self-approval.
`InternalTestContentPublicationAdapter`-equivalent behavior lives directly
here (records a real `ContentPublication` row) since there is no real
social/blog publishing API connected — same "internal but real" pattern
as Phase 5's invoice delivery.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.events.bus import EventBus
from app.models.event import EventType
from app.models.marketing import (
    ContentAsset,
    ContentPublication,
    ContentStatus,
    ContentVariant,
    MarketingContent,
)
from app.models.operations import Job, JobAttachment
from app.services.ai_content_service import JobContentInput, generate_job_caption, is_llm_connected
from app.services.approval_helper import create_approval_request


class JobNotFoundError(Exception):
    pass


class ContentNotFoundError(Exception):
    pass


class InvalidContentTransitionError(Exception):
    pass


class ContentService:
    def __init__(self, session_factory: async_sessionmaker, bus: EventBus) -> None:
        self._session_factory = session_factory
        self._bus = bus

    async def create_idea(self, tenant_id: uuid.UUID, *, title: str, summary: str | None, created_by: uuid.UUID | None) -> MarketingContent:
        async with self._session_factory() as session:
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

    async def generate_draft_from_job(self, tenant_id: uuid.UUID, job_id: uuid.UUID, created_by: uuid.UUID | None) -> MarketingContent:
        """Grounds the draft in real job data — real attachments, real
        notes, real service type/location. Never invents a result, photo,
        or testimonial. Idempotent per job (one content idea per job)."""
        async with self._session_factory() as session:
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

            caption = generate_job_caption(
                JobContentInput(
                    service_type=job.service_type, city=job.location, job_title=job.title,
                    notes=job.internal_notes, photo_count=len(attachments),
                )
            )

            content = MarketingContent(
                tenant_id=tenant_id, source_job_id=job_id, title=job.title, summary=caption.text,
                status=ContentStatus.DRAFT, created_by=created_by, ai_generated=True,
            )
            session.add(content)
            await session.flush()

            for attachment in attachments:
                session.add(
                    ContentAsset(tenant_id=tenant_id, content_id=content.id, job_attachment_id=attachment.id)
                )

            await session.commit()
            await session.refresh(content)

        await self._bus.publish(
            tenant_id=tenant_id, event_type=EventType.MARKETING_CONTENT_CREATED, source="marketing",
            entity_type="marketing_content", entity_id=content.id,
            payload={"content_id": str(content.id), "job_id": str(job_id), "llm_connected": is_llm_connected()},
        )
        return content

    async def add_variant(self, tenant_id: uuid.UUID, content_id: uuid.UUID, *, channel: str, body_text: str | None) -> ContentVariant:
        async with self._session_factory() as session:
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
