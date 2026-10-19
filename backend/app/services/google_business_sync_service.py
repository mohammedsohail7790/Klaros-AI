"""Google Business Profile sync: reads the connected account's locations and reviews into Klaros's own LocalListing / LocalReview records. Read-only on Google: nothing is
ever posted or changed there.

Idempotent without a new table: a listing is matched on (provider, external_id = "accounts/A/locations/L") and a review's primary key is a deterministic uuid5 of
(tenant, Google review resource name), so a second run updates nothing it need not and never adds a duplicate. A review the customer edited on Google (new updateTime or
different stars/text) is updated in place. A reply is copied from Google only when Klaros holds no reply of its own; a reply the owner drafted internally is never overwritten.

Alerting: a NEW review of 3 stars or fewer, posted in the last RECENT_DAYS days, records a LocalReputationEvent ("NEW_LOW_RATING_REVIEW") on its listing. That table is the only
reputation alert mechanism in the repo and nothing consumes it yet (no notification is sent from it), so the owner sees it in Local & Organic, not as a push alert. Older
reviews found on the first sync are imported silently so a new connection does not flood the owner with stale alerts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import select

from app.db.session import set_tenant_context
from app.integrations import google_business_client as gbc
from app.integrations.credential_store import decrypt_credential
from app.models.integration import ConnectionStatus
from app.models.marketing import LocalListing, LocalReputationEvent, LocalReview
from app.services.integration_connection_service import IntegrationConnectionService

logger = structlog.get_logger(__name__)

PROVIDER = "google_business_profile"
SOURCE = "google_business_profile"
LOW_RATING_EVENT = "NEW_LOW_RATING_REVIEW"
LOW_RATING_MAX = 3
RECENT_DAYS = 14
_NAMESPACE = uuid.UUID("6f1c2f0e-8f43-4d6e-9a41-0b8e5b7c9a10")


def review_pk(tenant_id: uuid.UUID, listing_external_id: str, review_id: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{tenant_id}:{listing_external_id}/reviews/{review_id}")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class GoogleBusinessSyncService:
    def __init__(self, session_factory, connection_service: IntegrationConnectionService) -> None:
        self._session_factory = session_factory
        self._connections = connection_service

    async def sync(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        connection = await self._connections.get_connection(tenant_id, PROVIDER)
        if connection is None or not connection.encrypted_credential or connection.status in (ConnectionStatus.DISCONNECTED, ConnectionStatus.NOT_CONNECTED):
            return {"status": "not_connected", "detail": "Google Business Profile is not connected for this business."}
        if connection.status not in (ConnectionStatus.CONNECTED, ConnectionStatus.UNVERIFIED):
            return {"status": "error", "detail": connection.last_error or "The connection is in an error state; reconnect Google Business Profile."}
        credential = decrypt_credential(connection.encrypted_credential)
        refresh, account = credential.get("refresh_token"), credential.get("account_name")
        if not refresh or not account:
            return {"status": "error", "detail": "The stored Google Business Profile credential is incomplete; reconnect it."}
        client = gbc.GoogleBusinessClient()
        try:
            tokens = await client.refresh_access_token(refresh_token=refresh)  # access tokens last about an hour; always start from the refresh token
            locations = await client.list_locations(access_token=tokens.access_token, account_name=account)
        except gbc.GoogleBusinessAPIError as exc:
            logger.warning("google_business_sync_failed", tenant_id=str(tenant_id), error_type=str(exc.error_type))
            return {"status": "error", "detail": str(exc)}

        totals = {"listings_created": 0, "listings_updated": 0, "reviews_created": 0, "reviews_updated": 0, "reviews_unchanged": 0, "low_rating_events": 0}
        failures: list[str] = []
        for loc in locations:
            external_id = gbc.location_path(account, loc.name)
            try:
                reviews = await client.list_reviews(access_token=tokens.access_token, account_name=account, location_name=loc.name)
            except gbc.GoogleBusinessAPIError as exc:
                logger.warning("google_business_reviews_failed", tenant_id=str(tenant_id), error_type=str(exc.error_type))
                failures.append(f"{loc.title}: {exc}")
                reviews = None
            summary = await self._upsert_location(tenant_id, external_id, loc, reviews)
            for key, value in summary.items():
                totals[key] += value
        out: dict[str, Any] = {"status": "synced", "locations": len(locations), **totals}
        if failures:
            out["status"] = "partial" if len(failures) < len(locations) else "error"
            out["errors"] = failures
            out["detail"] = "; ".join(failures)[:500]
        elif not locations:
            out["detail"] = "The connected Business Profile account has no locations."
        return out

    async def _upsert_location(self, tenant_id: uuid.UUID, external_id: str, loc: gbc.GbpLocation, reviews: list[gbc.GbpReview] | None) -> dict[str, int]:
        c = {"listings_created": 0, "listings_updated": 0, "reviews_created": 0, "reviews_updated": 0, "reviews_unchanged": 0, "low_rating_events": 0}
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            listing = (await session.execute(select(LocalListing).where(
                LocalListing.tenant_id == tenant_id, LocalListing.provider == PROVIDER, LocalListing.external_id == external_id))).scalars().first()
            if listing is None:
                listing = LocalListing(
                    tenant_id=tenant_id, business_name=loc.title[:255], address=loc.address, city=(loc.city or None) and loc.city[:120],
                    state=(loc.state or None) and loc.state[:120], provider=PROVIDER, external_id=external_id,
                )
                session.add(listing)
                await session.flush()
                c["listings_created"] += 1
            else:
                new = {"business_name": loc.title[:255], "address": loc.address, "city": loc.city, "state": loc.state}
                changed = False
                for attr, value in new.items():
                    if value is not None and getattr(listing, attr) != value:
                        setattr(listing, attr, value)
                        changed = True
                c["listings_updated"] += int(changed)
            if reviews:
                ids = {r.review_id: review_pk(tenant_id, external_id, r.review_id) for r in reviews}
                existing = {
                    r.id: r for r in (await session.execute(select(LocalReview).where(
                        LocalReview.tenant_id == tenant_id, LocalReview.id.in_(list(ids.values()))))).scalars()
                }
                for r in reviews:
                    if r.rating is None:
                        continue  # no recognised star rating: nothing honest to record
                    pk = ids[r.review_id]
                    row = existing.get(pk)
                    created_at = _parse_time(r.create_time) or now
                    if row is None:
                        session.add(LocalReview(
                            id=pk, tenant_id=tenant_id, listing_id=listing.id, rating=r.rating, author=(r.reviewer or None) and r.reviewer[:255], body=r.comment,
                            source=SOURCE, responded=bool(r.reply_comment), response_text=r.reply_comment or None, occurred_at=created_at,
                        ))
                        c["reviews_created"] += 1
                        if r.rating <= LOW_RATING_MAX and created_at >= now - timedelta(days=RECENT_DAYS):
                            session.add(LocalReputationEvent(
                                tenant_id=tenant_id, listing_id=listing.id, event_type=LOW_RATING_EVENT, occurred_at=now,
                                description=f"New {r.rating}-star Google review from {r.reviewer or 'a customer'}" + (f": {r.comment[:300]}" if r.comment else "."),
                            ))
                            c["low_rating_events"] += 1
                        continue
                    changed = False
                    if row.rating != r.rating:
                        row.rating, changed = r.rating, True
                    if (row.body or None) != (r.comment or None):
                        row.body, changed = r.comment, True
                    if r.reviewer and row.author != r.reviewer[:255]:
                        row.author, changed = r.reviewer[:255], True
                    if r.reply_comment and not row.response_text:  # never overwrite a reply (or draft) Klaros already holds
                        row.response_text, row.responded, changed = r.reply_comment, True, True
                    c["reviews_updated" if changed else "reviews_unchanged"] += 1
            await session.commit()
        return c
