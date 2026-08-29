import uuid
from typing import Any

from pydantic import BaseModel

from app.models.marketing import ContentPublication, ContentVariant, MarketingContent
from app.models.rbac import Permission
from app.services.content_service import (
    ContentNotFoundError,
    ContentService,
    InvalidContentTransitionError,
    JobNotFoundError,
)
from app.tools.base import ExecutionContext, Tool


def _content_to_dict(c: MarketingContent) -> dict[str, Any]:
    return {
        "id": str(c.id), "source_job_id": str(c.source_job_id) if c.source_job_id else None, "title": c.title,
        "summary": c.summary, "status": c.status, "ai_generated": c.ai_generated,
    }


def _variant_to_dict(v: ContentVariant) -> dict[str, Any]:
    return {"id": str(v.id), "content_id": str(v.content_id), "channel": v.channel, "body_text": v.body_text, "status": v.status}


class CreateContentIdeaInput(BaseModel):
    title: str
    summary: str | None = None


class ContentOutput(BaseModel):
    content: dict[str, Any]


class CreateContentIdea(Tool):
    name = "marketing.create_content_idea"
    description = "Create a marketing content idea."
    input_schema = CreateContentIdeaInput
    output_schema = ContentOutput
    required_permission = Permission.MANAGE_MARKETING_CONTENT

    def __init__(self, content_service: ContentService) -> None:
        self._content_service = content_service

    async def execute(self, input: CreateContentIdeaInput, context: ExecutionContext) -> ContentOutput:
        content = await self._content_service.create_idea(
            context.tenant_id, title=input.title, summary=input.summary, created_by=context.actor_id
        )
        return ContentOutput(content=_content_to_dict(content))


class GenerateDraftFromJobInput(BaseModel):
    job_id: uuid.UUID


class GenerateDraftFromJob(Tool):
    """Grounds the draft in real job/attachment data — see
    app/services/ai_content_service.py's docstring on why this is a
    deterministic template, not a fabricated LLM call, in this environment."""

    name = "marketing.generate_content_draft_from_job"
    description = "Generate a content draft from a completed job's real data (service, notes, photos)."
    input_schema = GenerateDraftFromJobInput
    output_schema = ContentOutput
    required_permission = Permission.MANAGE_MARKETING_CONTENT

    def __init__(self, content_service: ContentService) -> None:
        self._content_service = content_service

    async def execute(self, input: GenerateDraftFromJobInput, context: ExecutionContext) -> ContentOutput:
        try:
            content = await self._content_service.generate_draft_from_job(context.tenant_id, input.job_id, context.actor_id)
        except JobNotFoundError as e:
            raise ValueError(str(e)) from e
        return ContentOutput(content=_content_to_dict(content))


class AddVariantInput(BaseModel):
    content_id: uuid.UUID
    channel: str
    body_text: str | None = None


class VariantOutput(BaseModel):
    variant: dict[str, Any]


class AddContentVariant(Tool):
    name = "marketing.add_content_variant"
    description = "Add a channel-specific variant (Instagram/LinkedIn/TikTok/Blog/YouTube/Facebook) to a content item."
    input_schema = AddVariantInput
    output_schema = VariantOutput
    required_permission = Permission.MANAGE_MARKETING_CONTENT

    def __init__(self, content_service: ContentService) -> None:
        self._content_service = content_service

    async def execute(self, input: AddVariantInput, context: ExecutionContext) -> VariantOutput:
        try:
            variant = await self._content_service.add_variant(
                context.tenant_id, input.content_id, channel=input.channel, body_text=input.body_text
            )
        except ContentNotFoundError as e:
            raise ValueError(str(e)) from e
        return VariantOutput(variant=_variant_to_dict(variant))


class RequestContentApprovalInput(BaseModel):
    content_id: uuid.UUID


class RequestContentApproval(Tool):
    name = "marketing.request_content_approval"
    description = "Submit content for approval before it can be published."
    input_schema = RequestContentApprovalInput
    output_schema = ContentOutput
    required_permission = Permission.MANAGE_MARKETING_CONTENT

    def __init__(self, content_service: ContentService) -> None:
        self._content_service = content_service

    async def execute(self, input: RequestContentApprovalInput, context: ExecutionContext) -> ContentOutput:
        try:
            content = await self._content_service.request_approval(context.tenant_id, input.content_id, context.actor_id)
        except (ContentNotFoundError, InvalidContentTransitionError) as e:
            raise ValueError(str(e)) from e
        return ContentOutput(content=_content_to_dict(content))


class DecideContentApprovalInput(BaseModel):
    content_id: uuid.UUID


class ApproveContent(Tool):
    """Gated by APPROVE_MARKETING_CONTENT — a human-only permission the AI
    execution boundary's default role does not hold. AI can request
    approval; AI cannot approve its own request."""

    name = "marketing.approve_content"
    description = "Approve content pending approval."
    input_schema = DecideContentApprovalInput
    output_schema = ContentOutput
    required_permission = Permission.APPROVE_MARKETING_CONTENT

    def __init__(self, content_service: ContentService) -> None:
        self._content_service = content_service

    async def execute(self, input: DecideContentApprovalInput, context: ExecutionContext) -> ContentOutput:
        try:
            content = await self._content_service.decide_approval(
                context.tenant_id, input.content_id, approved=True, decided_by=context.actor_id
            )
        except (ContentNotFoundError, InvalidContentTransitionError) as e:
            raise ValueError(str(e)) from e
        return ContentOutput(content=_content_to_dict(content))


class RejectContent(Tool):
    name = "marketing.reject_content"
    description = "Reject content pending approval, returning it to DRAFT."
    input_schema = DecideContentApprovalInput
    output_schema = ContentOutput
    required_permission = Permission.APPROVE_MARKETING_CONTENT

    def __init__(self, content_service: ContentService) -> None:
        self._content_service = content_service

    async def execute(self, input: DecideContentApprovalInput, context: ExecutionContext) -> ContentOutput:
        try:
            content = await self._content_service.decide_approval(
                context.tenant_id, input.content_id, approved=False, decided_by=context.actor_id
            )
        except (ContentNotFoundError, InvalidContentTransitionError) as e:
            raise ValueError(str(e)) from e
        return ContentOutput(content=_content_to_dict(content))


class PublishVariantInput(BaseModel):
    content_variant_id: uuid.UUID


class PublicationOutput(BaseModel):
    publication_id: str
    status: str
    provider: str


class PublishContentVariant(Tool):
    """APPROVAL_REQUIRED by policy — publishing to a real external channel
    is customer/public-visible, so the ToolRegistry intercepts before this
    body runs, same shape as Phase 5's invoice void."""

    name = "marketing.publish_content_variant"
    description = "Publish an APPROVED content variant (internal test publication adapter)."
    input_schema = PublishVariantInput
    output_schema = PublicationOutput
    required_permission = Permission.MANAGE_MARKETING_CONTENT

    def __init__(self, content_service: ContentService) -> None:
        self._content_service = content_service

    async def execute(self, input: PublishVariantInput, context: ExecutionContext) -> PublicationOutput:
        try:
            publication = await self._content_service.publish_variant(context.tenant_id, input.content_variant_id)
        except (ContentNotFoundError, InvalidContentTransitionError) as e:
            raise ValueError(str(e)) from e
        return PublicationOutput(publication_id=str(publication.id), status=publication.status, provider=publication.provider)
