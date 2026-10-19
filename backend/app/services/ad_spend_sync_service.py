"""Ad spend sync: reads the connected ad platform's daily campaign cost and records it as Campaign and MarketingSpend rows, so real spend sits beside leads and revenue.

Read from the provider only; nothing is ever changed on the ad platform. Idempotent: a campaign is matched on (external_provider, external_campaign_id) and a day's spend on
(source, external_reference = "<provider>:<campaign_id>:<date>"), so a re-run updates an amount the provider has since revised (today's spend keeps growing) and never
adds a second row. Money is Decimal in the account's currency (the repo stores `Numeric(14, 2)`, never cents or floats).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select

from app.db.session import set_tenant_context
from app.integrations import google_ads_client as gac
from app.integrations import meta_client as mc
from app.integrations.credential_store import decrypt_credential
from app.models.integration import ConnectionStatus
from app.models.marketing import Campaign, CampaignChannel, CampaignObjective, CampaignStatus, MarketingSpend, MarketingSpendAllocation
from app.services.integration_connection_service import IntegrationConnectionService

logger = structlog.get_logger(__name__)

GOOGLE_ADS = "google_ads"
META_ADS = "meta_ads"
PROVIDERS = (GOOGLE_ADS, META_ADS)
_CHANNEL = {GOOGLE_ADS: CampaignChannel.GOOGLE_ADS, META_ADS: CampaignChannel.META_ADS}
_GOOGLE_STATUS = {"ENABLED": CampaignStatus.ACTIVE, "PAUSED": CampaignStatus.PAUSED, "REMOVED": CampaignStatus.ARCHIVED}
DEFAULT_DAYS = 7
MAX_DAYS = 90


def spend_reference(provider: str, campaign_id: str, day: date) -> str:
    return f"{provider}:{campaign_id}:{day.isoformat()}"


class AdSpendSyncService:
    def __init__(self, session_factory, connection_service: IntegrationConnectionService) -> None:
        self._session_factory = session_factory
        self._connections = connection_service

    async def sync(self, tenant_id: uuid.UUID, *, days: int = DEFAULT_DAYS) -> dict[str, Any]:
        days = max(1, min(int(days), MAX_DAYS))
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days - 1)
        results: list[dict[str, Any]] = []
        for provider in PROVIDERS:
            connection = await self._connections.get_connection(tenant_id, provider)
            if connection is None or not connection.encrypted_credential or connection.status in (ConnectionStatus.DISCONNECTED, ConnectionStatus.NOT_CONNECTED):
                continue
            if connection.status not in (ConnectionStatus.CONNECTED, ConnectionStatus.UNVERIFIED):
                results.append({"provider": provider, "status": "connection_error", "error": connection.last_error or "The connection is in an error state; reconnect it."})
                continue
            credential = decrypt_credential(connection.encrypted_credential)
            try:
                rows = await (self._google_rows if provider == GOOGLE_ADS else self._meta_rows)(tenant_id, connection, credential, start, end)
            except (gac.GoogleAdsAPIError, mc.MetaAPIError) as exc:
                logger.warning("ad_spend_sync_provider_failed", provider=provider, tenant_id=str(tenant_id))
                results.append({"provider": provider, "status": "error", "error": str(exc)})
                continue
            summary = await self._upsert(tenant_id, provider, rows)
            results.append({"provider": provider, "status": "synced", "window": [start.isoformat(), end.isoformat()], **summary})
        if not results:
            return {"status": "not_connected", "detail": "No Google Ads or Meta Ads account is connected for this business.", "providers": []}
        ok = [r for r in results if r["status"] == "synced"]
        status = "synced" if len(ok) == len(results) else ("partial" if ok else "error")
        return {"status": status, "providers": results}

    async def _google_rows(self, tenant_id, connection, credential, start: date, end: date) -> list[tuple[str, str, str | None, date, Decimal]]:
        refresh, customer = credential.get("refresh_token"), credential.get("customer_id")
        if not refresh or not customer:
            raise gac.GoogleAdsAPIError("The stored Google Ads credential is incomplete; reconnect Google Ads.", error_type=gac.GoogleAdsErrorType.AUTHENTICATION)
        client = gac.GoogleAdsClient()
        tokens = await client.refresh_access_token(refresh_token=refresh)  # access tokens last about an hour; always start from the refresh token
        costs = await client.daily_campaign_costs(
            access_token=tokens.access_token, customer_id=customer, start=start, end=end, login_customer_id=credential.get("login_customer_id"),
        )
        return [(c.campaign_id, c.campaign_name, _GOOGLE_STATUS.get(c.status), c.day, c.cost) for c in costs]

    async def _meta_rows(self, tenant_id, connection, credential, start: date, end: date) -> list[tuple[str, str, str | None, date, Decimal]]:
        token, account = credential.get("access_token"), credential.get("ad_account_id")
        if not token or not account:
            raise mc.MetaAPIError("The stored Meta credential is incomplete; reconnect Meta Ads.", error_type=mc.MetaErrorType.AUTHENTICATION)
        state = mc.token_expiry_state(credential.get("expires_at"))
        if state == "expired":
            raise mc.MetaAPIError("The Meta access token has expired; reconnect Meta Ads.", error_type=mc.MetaErrorType.AUTHENTICATION)
        client = mc.MetaClient()
        if state == "expiring":
            token = await self._extend_meta_token(tenant_id, connection, credential, client)
        costs = await client.daily_campaign_costs(access_token=token, ad_account_id=account, start=start, end=end)
        return [(c.campaign_id, c.campaign_name, None, c.day, c.cost) for c in costs]

    async def _extend_meta_token(self, tenant_id, connection, credential: dict, client: mc.MetaClient) -> str:
        """Push the long-lived token's expiry out while it is still valid. A failure here is not fatal: the current token still works until it expires."""
        try:
            fresh = await client.exchange_for_long_lived(access_token=credential["access_token"])
        except mc.MetaAPIError:
            logger.warning("meta_token_extend_failed", tenant_id=str(tenant_id))
            return credential["access_token"]
        updated = {**credential, "access_token": fresh.access_token, "expires_at": fresh.expires_at.isoformat() if fresh.expires_at else credential.get("expires_at")}
        await self._connections.connect(
            tenant_id, META_ADS, updated, created_by=connection.created_by, external_account_id=connection.external_account_id, scopes=connection.scopes,
        )
        return fresh.access_token

    async def _upsert(self, tenant_id: uuid.UUID, provider: str, rows: list[tuple[str, str, str | None, date, Decimal]]) -> dict[str, Any]:
        created = updated = unchanged = 0
        campaigns_created = 0
        total = Decimal("0")
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            existing_campaigns = {
                c.external_campaign_id: c for c in (await session.execute(select(Campaign).where(
                    Campaign.tenant_id == tenant_id, Campaign.external_provider == provider))).scalars()
            }
            existing_spend = {
                s.external_reference: s for s in (await session.execute(select(MarketingSpend).where(
                    MarketingSpend.tenant_id == tenant_id, MarketingSpend.source == provider))).scalars()
            }
            for campaign_id, name, status, day, cost in sorted(rows, key=lambda r: (r[0], r[3])):
                campaign = existing_campaigns.get(campaign_id)
                if campaign is None:
                    if cost == 0:
                        continue  # nothing was spent: no reason to create a record for it yet
                    campaign = Campaign(
                        tenant_id=tenant_id, name=name[:255], channel=_CHANNEL[provider], objective=CampaignObjective.LEAD_GEN,
                        status=status or CampaignStatus.ACTIVE, external_provider=provider, external_campaign_id=campaign_id,
                    )
                    session.add(campaign)
                    await session.flush()
                    existing_campaigns[campaign_id] = campaign
                    campaigns_created += 1
                else:
                    if campaign.name != name[:255]:
                        campaign.name = name[:255]
                    if status and campaign.status != status:
                        campaign.status = status
                ref = spend_reference(provider, campaign_id, day)
                spend = existing_spend.get(ref)
                total += cost
                if spend is None:
                    if cost == 0:
                        continue
                    spend = MarketingSpend(tenant_id=tenant_id, channel=_CHANNEL[provider], amount=cost, spend_date=day, source=provider, external_reference=ref)
                    session.add(spend)
                    await session.flush()
                    session.add(MarketingSpendAllocation(tenant_id=tenant_id, spend_id=spend.id, campaign_id=campaign.id, amount=cost))
                    existing_spend[ref] = spend
                    created += 1
                elif Decimal(spend.amount) != cost:
                    spend.amount = cost
                    allocation = (await session.execute(select(MarketingSpendAllocation).where(
                        MarketingSpendAllocation.tenant_id == tenant_id, MarketingSpendAllocation.spend_id == spend.id))).scalars().first()
                    if allocation is not None:
                        allocation.amount = cost
                    updated += 1
                else:
                    unchanged += 1
            await session.commit()
        return {
            "campaigns_created": campaigns_created, "spend_rows_created": created, "spend_rows_updated": updated, "spend_rows_unchanged": unchanged,
            "total_spend_in_window": str(total),
        }
