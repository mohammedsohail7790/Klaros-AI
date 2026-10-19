"""Ad spend sync: idempotent upsert of campaigns and daily spend, not_connected, partial failure, Meta token extension, the governed tool and the crew member."""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.session import async_session_maker, set_tenant_context
from app.integrations import google_ads_client as gac
from app.integrations import meta_client as mc
from app.integrations.connectors import get_connector
from app.models.actor import ActorType
from app.models.marketing import Campaign, MarketingSpend, MarketingSpendAllocation
from app.models.rbac import Role
from app.services import operating_crew as oc
from app.services.ad_spend_sync_service import AdSpendSyncService, spend_reference
from app.services.integration_connection_service import IntegrationConnectionService
from app.tools.base import ExecutionContext
from app.tools.builtin.marketing_ad_spend_tools import SyncAdSpend

TODAY = datetime.now(timezone.utc).date()


@pytest.fixture
def conns() -> IntegrationConnectionService:
    svc = IntegrationConnectionService(async_session_maker)
    for key in ("google_ads", "meta_ads"):
        async def _ok(_cred):
            return True, "ok"

        svc.register_verifier(key, _ok)
    return svc


@pytest.fixture
def fake_google(monkeypatch):
    state = {"cost": Decimal("12.50"), "refreshes": 0, "fail": None}

    async def refresh(self, *, refresh_token):
        state["refreshes"] += 1
        return gac.GoogleTokens("AT", None, 3600)

    async def costs(self, *, access_token, customer_id, start, end, login_customer_id=None):
        if state["fail"]:
            raise state["fail"]
        return [
            gac.DailyCampaignCost("c1", "Brand Search", "ENABLED", TODAY, state["cost"]),
            gac.DailyCampaignCost("c1", "Brand Search", "ENABLED", TODAY - timedelta(days=1), Decimal("7.25")),
            gac.DailyCampaignCost("c2", "Idle", "PAUSED", TODAY, Decimal("0.00")),
        ]

    monkeypatch.setattr(gac.GoogleAdsClient, "refresh_access_token", refresh)
    monkeypatch.setattr(gac.GoogleAdsClient, "daily_campaign_costs", costs)
    return state


async def _connect(conns, tenant_id, provider="google_ads", credential=None):
    credential = credential or {"refresh_token": "RT", "customer_id": "123", "access_token": "x"}
    await conns.connect(tenant_id, provider, credential, created_by=None, external_account_id="123")


async def _totals(tenant_id):
    async with async_session_maker() as s:
        await set_tenant_context(s, tenant_id)
        spend = (await s.execute(select(func.count(), func.coalesce(func.sum(MarketingSpend.amount), 0)).where(MarketingSpend.tenant_id == tenant_id))).one()
        alloc = (await s.execute(select(func.coalesce(func.sum(MarketingSpendAllocation.amount), 0)).where(MarketingSpendAllocation.tenant_id == tenant_id))).scalar_one()
        camps = (await s.execute(select(func.count()).select_from(Campaign).where(Campaign.tenant_id == tenant_id))).scalar_one()
    return spend[0], Decimal(spend[1]), Decimal(alloc), camps


async def test_not_connected_is_clean(conns) -> None:
    out = await AdSpendSyncService(async_session_maker, conns).sync(uuid.uuid4())
    assert out["status"] == "not_connected" and out["providers"] == []


async def test_google_sync_is_idempotent_and_updates_revised_amounts(conns, fake_google) -> None:
    tid = uuid.uuid4()
    await _connect(conns, tid)
    svc = AdSpendSyncService(async_session_maker, conns)
    first = await svc.sync(tid, days=7)
    assert first["status"] == "synced" and first["providers"][0]["spend_rows_created"] == 2 and first["providers"][0]["campaigns_created"] == 1
    assert await _totals(tid) == (2, Decimal("19.75"), Decimal("19.75"), 1)  # the zero-cost campaign creates nothing
    second = await svc.sync(tid, days=7)
    assert second["providers"][0]["spend_rows_created"] == 0 and second["providers"][0]["spend_rows_unchanged"] == 2
    assert await _totals(tid) == (2, Decimal("19.75"), Decimal("19.75"), 1)
    fake_google["cost"] = Decimal("20.00")  # today's spend kept growing
    third = await svc.sync(tid, days=7)
    assert third["providers"][0]["spend_rows_updated"] == 1
    assert await _totals(tid) == (2, Decimal("27.25"), Decimal("27.25"), 1)
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        c = (await s.execute(select(Campaign).where(Campaign.tenant_id == tid))).scalar_one()
        refs = {r for (r,) in (await s.execute(select(MarketingSpend.external_reference).where(MarketingSpend.tenant_id == tid))).all()}
    assert (c.external_provider, c.external_campaign_id, c.channel, c.status) == ("google_ads", "c1", "GOOGLE_ADS", "ACTIVE")
    assert refs == {spend_reference("google_ads", "c1", TODAY), spend_reference("google_ads", "c1", TODAY - timedelta(days=1))}
    assert fake_google["refreshes"] == 3  # always starts from the refresh token


async def test_tenants_are_isolated(conns, fake_google) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    await _connect(conns, a)
    svc = AdSpendSyncService(async_session_maker, conns)
    await svc.sync(a)
    assert (await svc.sync(b))["status"] == "not_connected" and await _totals(b) == (0, Decimal("0"), Decimal("0"), 0)


async def test_provider_error_is_reported_not_swallowed_and_has_no_secret(conns, fake_google) -> None:
    tid = uuid.uuid4()
    await _connect(conns, tid)
    fake_google["fail"] = gac.GoogleAdsAPIError("Google Ads: developer token not approved", status_code=403, error_type=gac.GoogleAdsErrorType.AUTHENTICATION)
    out = await AdSpendSyncService(async_session_maker, conns).sync(tid)
    assert out["status"] == "error" and "developer token" in out["providers"][0]["error"] and "RT" not in str(out).replace("RT-", "")
    assert await _totals(tid) == (0, Decimal("0"), Decimal("0"), 0)


async def test_meta_sync_and_partial_when_google_fails(conns, fake_google, monkeypatch) -> None:
    tid = uuid.uuid4()
    exp = (datetime.now(timezone.utc) + timedelta(days=40)).isoformat()
    await _connect(conns, tid, "meta_ads", {"access_token": "T", "ad_account_id": "act_1", "expires_at": exp})
    await _connect(conns, tid)

    async def costs(self, *, access_token, ad_account_id, start, end):
        assert access_token == "T" and ad_account_id == "act_1"
        return [mc.DailyCampaignCost("m1", "Lookalike", TODAY, Decimal("30.10"))]

    monkeypatch.setattr(mc.MetaClient, "daily_campaign_costs", costs)
    fake_google["fail"] = gac.GoogleAdsAPIError("Google Ads: denied")
    out = await AdSpendSyncService(async_session_maker, conns).sync(tid)
    assert out["status"] == "partial" and {p["provider"]: p["status"] for p in out["providers"]} == {"google_ads": "error", "meta_ads": "synced"}
    assert await _totals(tid) == (1, Decimal("30.10"), Decimal("30.10"), 1)
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        c = (await s.execute(select(Campaign).where(Campaign.tenant_id == tid))).scalar_one()
    assert (c.external_provider, c.channel) == ("meta_ads", "META_ADS")


async def test_meta_expired_token_is_an_honest_error_and_expiring_token_is_extended(conns, monkeypatch) -> None:
    tid = uuid.uuid4()
    await _connect(conns, tid, "meta_ads", {"access_token": "OLD", "ad_account_id": "act_1", "expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()})
    out = await AdSpendSyncService(async_session_maker, conns).sync(tid)
    assert out["status"] == "error" and "expired" in out["providers"][0]["error"]

    tid2 = uuid.uuid4()
    await _connect(conns, tid2, "meta_ads", {"access_token": "OLD", "ad_account_id": "act_1", "expires_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()})
    new_exp = datetime.now(timezone.utc) + timedelta(days=60)
    used = {}

    async def extend(self, *, access_token):
        return mc.MetaToken("NEW", new_exp)

    async def costs(self, *, access_token, ad_account_id, start, end):
        used["token"] = access_token
        return []

    monkeypatch.setattr(mc.MetaClient, "exchange_for_long_lived", extend)
    monkeypatch.setattr(mc.MetaClient, "daily_campaign_costs", costs)
    out = await AdSpendSyncService(async_session_maker, conns).sync(tid2)
    assert out["status"] == "synced" and used["token"] == "NEW"
    from app.integrations.credential_store import decrypt_credential

    stored = decrypt_credential((await conns.get_connection(tid2, "meta_ads")).encrypted_credential)
    assert stored["access_token"] == "NEW" and stored["ad_account_id"] == "act_1" and stored["expires_at"] == new_exp.isoformat()


async def test_errored_connection_reports_its_error_and_disconnected_is_ignored(conns, fake_google) -> None:
    tid = uuid.uuid4()
    await conns.connect(tid, "google_ads", {"refresh_token": "RT", "customer_id": "1"}, created_by=None, verify=False)
    async def _bad(_c):
        return False, "token revoked"
    conns.register_verifier("google_ads", _bad)
    await conns.verify(tid, "google_ads")
    out = await AdSpendSyncService(async_session_maker, conns).sync(tid)
    assert out["providers"][0]["status"] == "connection_error" and "revoked" in out["providers"][0]["error"]
    await conns.disconnect(tid, "google_ads")
    assert (await AdSpendSyncService(async_session_maker, conns).sync(tid))["status"] == "not_connected"


async def test_tool_is_governed_auto_and_returns_not_connected(conns) -> None:
    from app.api.tool_deps import get_tool_registry
    from app.tools.policy import ActionPolicy, policy_for

    tool = get_tool_registry().get("marketing.sync_ad_spend")
    assert tool.name == "marketing.sync_ad_spend" and policy_for("marketing.sync_ad_spend") == ActionPolicy.AUTO
    ctx = ExecutionContext(tenant_id=uuid.uuid4(), actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)
    out = await SyncAdSpend(AdSpendSyncService(async_session_maker, conns)).execute(SyncAdSpend.input_schema(days=3), ctx)
    assert out.status == "not_connected" and out.providers == []


def test_crew_member_runs_the_tool_daily_without_a_model() -> None:
    m = next(m for m in oc.crew_for([]) if m.key == "ad-spend-sync")
    t = oc._triggers(m)["schedule"]
    assert t["time"] == "06:30" and t["frequency"] == "DAILY"
    assert t["action"] == {"tool_name": "marketing.sync_ad_spend", "tool_input": {"days": 7}}
    assert get_connector("google_ads") and get_connector("meta_ads")
