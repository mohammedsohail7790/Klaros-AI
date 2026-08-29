"""section 2: Local & Organic — internal, owner-entered listing/review
records. `provider`/`external_id` stay NULL until a real Google
Business/Yelp connection exists (see app/integrations/adapters.py)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.marketing import LocalListing, LocalReputationEvent, LocalReview


class ListingNotFoundError(Exception):
    pass


class LocalService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def create_listing(
        self, tenant_id: uuid.UUID, *, business_name: str, address: str | None, city: str | None, state: str | None
    ) -> LocalListing:
        async with self._session_factory() as session:
            listing = LocalListing(tenant_id=tenant_id, business_name=business_name, address=address, city=city, state=state)
            session.add(listing)
            await session.commit()
            await session.refresh(listing)
        return listing

    async def record_review(
        self, tenant_id: uuid.UUID, listing_id: uuid.UUID, *, rating: int, author: str | None, body: str | None,
        source: str = "manual",
    ) -> LocalReview:
        async with self._session_factory() as session:
            listing = await session.get(LocalListing, listing_id)
            if listing is None or listing.tenant_id != tenant_id:
                raise ListingNotFoundError("Listing not found")
            review = LocalReview(
                tenant_id=tenant_id, listing_id=listing_id, rating=rating, author=author, body=body,
                source=source, occurred_at=datetime.now(timezone.utc),
            )
            session.add(review)
            await session.commit()
            await session.refresh(review)
        return review

    async def respond_to_review(self, tenant_id: uuid.UUID, review_id: uuid.UUID, response_text: str) -> LocalReview:
        async with self._session_factory() as session:
            review = await session.get(LocalReview, review_id)
            if review is None or review.tenant_id != tenant_id:
                raise ListingNotFoundError("Review not found")
            review.responded = True
            review.response_text = response_text
            await session.commit()
            await session.refresh(review)
        return review

    async def record_reputation_event(
        self, tenant_id: uuid.UUID, listing_id: uuid.UUID, *, event_type: str, description: str | None
    ) -> LocalReputationEvent:
        async with self._session_factory() as session:
            listing = await session.get(LocalListing, listing_id)
            if listing is None or listing.tenant_id != tenant_id:
                raise ListingNotFoundError("Listing not found")
            event = LocalReputationEvent(
                tenant_id=tenant_id, listing_id=listing_id, event_type=event_type, description=description,
                occurred_at=datetime.now(timezone.utc),
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)
        return event
