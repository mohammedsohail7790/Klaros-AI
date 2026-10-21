"""Clinic outreach data model: one row per clinic per campaign, a stage that starts at QUEUED, a campaign that starts in SHADOW, and one suppression entry per tenant value."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.session import async_session_maker
from app.models.clinic_outreach import ClinicOutreach, ClinicOutreachCampaign, OutreachMode, OutreachStage, OutreachSuppression
from app.models.marketing import Campaign, CampaignChannel, CampaignObjective, CampaignStatus
from app.models.medical_tourism import Provider, ProviderStatus

pytestmark = pytest.mark.asyncio


# The outreach tables point at real campaigns and clinics (foreign keys, which real Postgres enforces): the rows they refer to must exist.
async def _campaign(db, tenant) -> uuid.UUID:
    row = Campaign(tenant_id=tenant, name="Clinic outreach", channel=CampaignChannel.OUTBOUND, objective=CampaignObjective.LEAD_GEN, status=CampaignStatus.DRAFT)
    db.add(row)
    await db.flush()
    return row.id


async def _clinic(db, tenant, name: str) -> uuid.UUID:
    row = Provider(tenant_id=tenant, name=name, country="TR", city="Istanbul", status=ProviderStatus.ACTIVE, verification_status="UNVERIFIED")
    db.add(row)
    await db.flush()
    return row.id


async def test_a_clinic_can_appear_once_per_campaign() -> None:
    tenant = uuid.uuid4()
    async with async_session_maker() as db:
        campaign, provider, other = await _campaign(db, tenant), await _clinic(db, tenant, "Clinic A"), await _clinic(db, tenant, "Clinic B")
        await db.commit()
        db.add(ClinicOutreach(tenant_id=tenant, campaign_id=campaign, provider_id=provider))
        await db.commit()
        db.add(ClinicOutreach(tenant_id=tenant, campaign_id=campaign, provider_id=provider))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
        db.add(ClinicOutreach(tenant_id=tenant, campaign_id=campaign, provider_id=other))
        await db.commit()  # a different clinic in the same campaign is fine


async def test_stage_defaults_to_queued_and_mode_to_shadow() -> None:
    tenant = uuid.uuid4()
    async with async_session_maker() as db:
        campaign, provider = await _campaign(db, tenant), await _clinic(db, tenant, "Clinic A")
        camp = ClinicOutreachCampaign(tenant_id=tenant, campaign_id=campaign)
        row = ClinicOutreach(tenant_id=tenant, campaign_id=campaign, provider_id=provider)
        db.add_all([camp, row])
        await db.commit()
        await db.refresh(camp)
        await db.refresh(row)
        assert camp.mode == OutreachMode.SHADOW
        assert row.stage == OutreachStage.QUEUED
        assert row.emails_sent == 0 and row.contact_checked_at is None and row.contact_source is None
    assert len(OutreachStage) == 14


async def test_suppression_is_unique_per_tenant() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as db:
        db.add(OutreachSuppression(tenant_id=a, kind="address", value="x@clinic.example"))
        await db.commit()
        db.add(OutreachSuppression(tenant_id=b, kind="address", value="x@clinic.example"))
        await db.commit()  # another tenant may hold the same value
        db.add(OutreachSuppression(tenant_id=a, kind="address", value="x@clinic.example"))
        with pytest.raises(IntegrityError):
            await db.commit()
