import uuid
from typing import Any

from pydantic import BaseModel

from app.models.marketing import SEOKeyword, SEOOpportunity, SEOPage
from app.models.rbac import Permission
from app.services.local_service import ListingNotFoundError, LocalService
from app.services.seo_service import SEOPageNotFoundError, SEOService
from app.tools.base import ExecutionContext, Tool


def _page_to_dict(p: SEOPage) -> dict[str, Any]:
    return {
        "id": str(p.id), "service": p.service, "location": p.location, "url_slug": p.url_slug, "title": p.title,
        "status": p.status, "ai_generated": p.ai_generated,
    }


class GeneratePageDraftInput(BaseModel):
    service: str
    location: str


class PageOutput(BaseModel):
    page: dict[str, Any]


class GenerateSEOPageDraft(Tool):
    name = "marketing.generate_seo_page_draft"
    description = "Generate a service/location SEO page draft (DRAFT / AI GENERATED)."
    input_schema = GeneratePageDraftInput
    output_schema = PageOutput
    required_permission = Permission.MANAGE_MARKETING_CONTENT

    def __init__(self, seo_service: SEOService) -> None:
        self._seo_service = seo_service

    async def execute(self, input: GeneratePageDraftInput, context: ExecutionContext) -> PageOutput:
        page = await self._seo_service.generate_page_draft(
            context.tenant_id, service=input.service, location=input.location,
            actor_type=context.actor_type, actor_id=context.actor_id, correlation_id=context.correlation_id,
        )
        return PageOutput(page=_page_to_dict(page))


class PublishSEOPageInput(BaseModel):
    page_id: uuid.UUID


class PublishSEOPage(Tool):
    """APPROVAL_REQUIRED by policy — publishing a page is public-visible."""

    name = "marketing.publish_seo_page"
    description = "Publish a DRAFT SEO page."
    input_schema = PublishSEOPageInput
    output_schema = PageOutput
    required_permission = Permission.MANAGE_MARKETING_CONTENT

    def __init__(self, seo_service: SEOService) -> None:
        self._seo_service = seo_service

    async def execute(self, input: PublishSEOPageInput, context: ExecutionContext) -> PageOutput:
        try:
            page = await self._seo_service.publish_page(context.tenant_id, input.page_id)
        except SEOPageNotFoundError as e:
            raise ValueError(str(e)) from e
        return PageOutput(page=_page_to_dict(page))


class RecordKeywordInput(BaseModel):
    keyword: str
    target_location: str | None = None
    page_id: uuid.UUID | None = None
    search_volume: int | None = None
    current_ranking: int | None = None


class KeywordOutput(BaseModel):
    keyword_id: str
    keyword: str


class RecordSEOKeyword(Tool):
    name = "marketing.record_seo_keyword"
    description = "Record a keyword and, if known, a real observed ranking (never fabricated)."
    input_schema = RecordKeywordInput
    output_schema = KeywordOutput
    required_permission = Permission.MANAGE_MARKETING_CONTENT

    def __init__(self, seo_service: SEOService) -> None:
        self._seo_service = seo_service

    async def execute(self, input: RecordKeywordInput, context: ExecutionContext) -> KeywordOutput:
        row = await self._seo_service.record_keyword(
            context.tenant_id, keyword=input.keyword, target_location=input.target_location, page_id=input.page_id,
            search_volume=input.search_volume, current_ranking=input.current_ranking,
        )
        return KeywordOutput(keyword_id=str(row.id), keyword=row.keyword)


class CreateSEOOpportunityInput(BaseModel):
    service: str
    location: str
    rationale: str | None = None
    priority: str = "MEDIUM"


class OpportunityOutput(BaseModel):
    opportunity_id: str


class CreateSEOOpportunity(Tool):
    name = "marketing.create_seo_opportunity"
    description = "Record a service/location SEO opportunity."
    input_schema = CreateSEOOpportunityInput
    output_schema = OpportunityOutput
    required_permission = Permission.MANAGE_MARKETING_CONTENT

    def __init__(self, seo_service: SEOService) -> None:
        self._seo_service = seo_service

    async def execute(self, input: CreateSEOOpportunityInput, context: ExecutionContext) -> OpportunityOutput:
        row = await self._seo_service.create_opportunity(
            context.tenant_id, service=input.service, location=input.location, rationale=input.rationale,
            priority=input.priority,
        )
        return OpportunityOutput(opportunity_id=str(row.id))


class CreateListingInput(BaseModel):
    business_name: str
    address: str | None = None
    city: str | None = None
    state: str | None = None


class ListingOutput(BaseModel):
    listing_id: str


class CreateLocalListing(Tool):
    name = "marketing.create_local_listing"
    description = "Create an internal local business listing record."
    input_schema = CreateListingInput
    output_schema = ListingOutput
    required_permission = Permission.MANAGE_MARKETING_CONTENT

    def __init__(self, local_service: LocalService) -> None:
        self._local_service = local_service

    async def execute(self, input: CreateListingInput, context: ExecutionContext) -> ListingOutput:
        row = await self._local_service.create_listing(
            context.tenant_id, business_name=input.business_name, address=input.address, city=input.city, state=input.state
        )
        return ListingOutput(listing_id=str(row.id))


class RecordReviewInput(BaseModel):
    listing_id: uuid.UUID
    rating: int
    author: str | None = None
    body: str | None = None
    source: str = "manual"


class ReviewOutput(BaseModel):
    review_id: str


class RecordLocalReview(Tool):
    name = "marketing.record_local_review"
    description = "Record a real review for a local listing."
    input_schema = RecordReviewInput
    output_schema = ReviewOutput
    required_permission = Permission.MANAGE_MARKETING_CONTENT

    def __init__(self, local_service: LocalService) -> None:
        self._local_service = local_service

    async def execute(self, input: RecordReviewInput, context: ExecutionContext) -> ReviewOutput:
        try:
            row = await self._local_service.record_review(
                context.tenant_id, input.listing_id, rating=input.rating, author=input.author, body=input.body,
                source=input.source,
            )
        except ListingNotFoundError as e:
            raise ValueError(str(e)) from e
        return ReviewOutput(review_id=str(row.id))


class RespondToReviewInput(BaseModel):
    review_id: uuid.UUID
    response_text: str


class RespondToReview(Tool):
    """`APPROVAL_REQUIRED` by policy — a review response is public-visible."""

    name = "marketing.respond_to_review"
    description = "Respond to a local review."
    input_schema = RespondToReviewInput
    output_schema = ReviewOutput
    required_permission = Permission.MANAGE_MARKETING_CONTENT

    def __init__(self, local_service: LocalService) -> None:
        self._local_service = local_service

    async def execute(self, input: RespondToReviewInput, context: ExecutionContext) -> ReviewOutput:
        try:
            row = await self._local_service.respond_to_review(context.tenant_id, input.review_id, input.response_text)
        except ListingNotFoundError as e:
            raise ValueError(str(e)) from e
        return ReviewOutput(review_id=str(row.id))
