"""Clinic outreach service: builds a campaign over eligible clinics, finds their business email, and manages start/pause/stop.

What must hold: nothing is ever sent, only active clinics with no agreed terms and no live outreach are chosen, a no-email clinic is retried only after 14 days,
unusable or suppressed addresses are refused, and one tenant never sees or changes another tenant's campaign. SQLite; fake search and fake MX check."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.db.session import async_session_maker
from app.models.audit_log import AuditLog
from app.models.clinic_outreach import ClinicOutreach, ClinicOutreachCampaign, OutreachMode, OutreachStage
from app.models.marketing import Campaign, CampaignChannel, CampaignObjective, CampaignStatus
from app.models.medical_tourism import Provider, ProviderStatus
from app.models.mt_revenue import ProviderTerms
from app.services.clinic_outreach_service import ClinicOutreachService, OutreachError, OutreachNotFound
from app.services.mt_clinic_sourcing import SearchHit
from app.services.outreach_settings import OutreachConfig

USER = uuid.uuid4()


class FakeSearch:
    """Returns one page for each clinic it knows, carrying that clinic's address on the clinic's own domain."""

    name = "fake"

    def __init__(self, pages: dict[str, tuple[str, str]] | None = None, fail: bool = False):
        self.pages = pages or {}  # clinic name -> (url, page text)
        self.fail = fail
        self.queries: list[str] = []

    async def search(self, query, max_results=5):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("search down")
        return [SearchHit(f"{name} - Contact", url, text) for name, (url, text) in self.pages.items() if query.startswith(name + " ")]


async def _mx_ok(domain: str) -> bool:
    return True


def _svc(search=None) -> ClinicOutreachService:
    return ClinicOutreachService(async_session_maker, search_factory=lambda: search, mx_exists=_mx_ok)


def _config(countries=("TR",)) -> OutreachConfig:
    return OutreachConfig.model_validate({
        "term_sheet": {"basis": "PERCENTAGE", "min_value": "10.5", "max_value": "20", "payment_days": 30, "currency": "usd", "qualifying_outcome": "Treatment completed"},
        "sender": {"display_name": "Sam", "signature": "Sam, MedLuxe", "company_name": "MedLuxe Travel", "postal_address": "1 Main St, Dubai"},
        "countries": list(countries),
    })


async def _clinic(tid, name, *, country="TR", status=ProviderStatus.ACTIVE, email=None, city="Istanbul", verification="UNVERIFIED") -> uuid.UUID:
    async with async_session_maker() as s:
        p = Provider(tenant_id=tid, name=name, country=country, city=city, status=status, contact_email=email, verification_status=verification)
        s.add(p)
        await s.commit()
        return p.id


async def _rows(tid, campaign_id) -> dict[uuid.UUID, ClinicOutreach]:
    async with async_session_maker() as s:
        rows = (await s.execute(select(ClinicOutreach).where(ClinicOutreach.tenant_id == tid, ClinicOutreach.campaign_id == campaign_id))).scalars()
        return {r.provider_id: r for r in rows}


async def _campaign(tid, campaign_id) -> Campaign:
    async with async_session_maker() as s:
        return (await s.execute(select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tid))).scalar_one()


async def _audits(tid, action) -> list[AuditLog]:
    async with async_session_maker() as s:
        return list((await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == action))).scalars())


def _search_for(clinics: dict[str, str]) -> FakeSearch:
    """clinic name -> its own domain."""
    return FakeSearch({name: (f"https://www.{d}/contact", f"{name} welcomes international patients. Write to international@{d} for a quote.") for name, d in clinics.items()})


async def test_eligible_clinics_are_active_in_the_chosen_countries_with_no_terms() -> None:
    tid = uuid.uuid4()
    ok = await _clinic(tid, "Good Clinic", email="info@good.example")
    ok2 = await _clinic(tid, "Second Clinic")
    await _clinic(tid, "Suspended Clinic", status=ProviderStatus.INACTIVE, verification="SUSPENDED")  # the UI's "suspended" is stored as INACTIVE
    await _clinic(tid, "Inactive Clinic", status=ProviderStatus.INACTIVE)
    termed = await _clinic(tid, "Termed Clinic")
    await _clinic(tid, "Bangkok Clinic", country="TH", city="Bangkok")
    await _clinic(uuid.uuid4(), "Other Tenant Clinic")
    async with async_session_maker() as s:
        s.add(ProviderTerms(tenant_id=tid, provider_id=termed, commission_basis="FLAT", flat_amount=Decimal("100"), currency="USD"))
        await s.commit()

    out = await _svc().eligible_clinics(tid, ["tr"])
    assert {c["id"] for c in out} == {str(ok), str(ok2)}
    by_id = {c["id"]: c for c in out}
    assert by_id[str(ok)] == {"id": str(ok), "name": "Good Clinic", "country": "TR", "city": "Istanbul", "has_email": True}
    assert by_id[str(ok2)]["has_email"] is False
    assert {c["name"] for c in await _svc().eligible_clinics(tid, ["TR", "TH"])} == {"Good Clinic", "Second Clinic", "Bangkok Clinic"}


async def test_create_campaign_makes_a_draft_outbound_campaign_and_queued_rows() -> None:
    tid = uuid.uuid4()
    a, b = await _clinic(tid, "Alpha Clinic"), await _clinic(tid, "Beta Clinic")
    inactive = await _clinic(tid, "Gone Clinic", status=ProviderStatus.INACTIVE)
    svc = _svc()
    cid = await svc.create_campaign(tid, USER, name="Turkey partners", config=_config(), provider_ids=None)

    c = await _campaign(tid, cid)
    assert (c.name, c.status, c.channel, c.objective) == ("Turkey partners", CampaignStatus.DRAFT, CampaignChannel.OUTBOUND, CampaignObjective.LEAD_GEN)
    rows = await _rows(tid, cid)
    assert set(rows) == {a, b} and {r.stage for r in rows.values()} == {OutreachStage.QUEUED}
    async with async_session_maker() as s:
        cfg = (await s.execute(select(ClinicOutreachCampaign).where(ClinicOutreachCampaign.campaign_id == cid))).scalar_one()
    assert cfg.mode == OutreachMode.SHADOW and cfg.tenant_id == tid and cfg.countries == ["TR"]

    view = await svc.get(tid, cid)
    assert view["campaign"] == {"id": str(cid), "name": "Turkey partners", "status": "DRAFT"} and view["mode"] == "SHADOW"
    # Decimal values survive the JSON round trip exactly
    back = OutreachConfig.model_validate(view["config"])
    assert back.term_sheet.min_value == Decimal("10.5") and back.term_sheet.max_value == Decimal("20") and back.term_sheet.currency == "USD"
    assert back == _config()
    assert view["funnel"]["QUEUED"] == 2 and set(view["funnel"]) == {s.value for s in OutreachStage} and view["funnel"]["READY"] == 0
    assert {c["name"] for c in view["clinics"]} == {"Alpha Clinic", "Beta Clinic"}
    assert set(view["clinics"][0]) == {"provider_id", "name", "country", "city", "stage", "contact_email", "contact_source", "contact_checked_at"}
    assert len(await _audits(tid, "outreach.campaign_created")) == 1

    # a hand-picked list is intersected with the eligible clinics; unknown or ineligible ids are skipped, not errors
    tid2 = uuid.uuid4()
    x = await _clinic(tid2, "Xeno Clinic")
    await _clinic(tid2, "Yolo Clinic")
    picked = await svc.create_campaign(tid2, USER, name="Picked", config=_config(), provider_ids=[x, uuid.uuid4(), inactive])
    assert set(await _rows(tid2, picked)) == {x}
    with pytest.raises(OutreachError):
        await svc.create_campaign(tid2, USER, name="Empty", config=_config(("TH",)), provider_ids=None)


async def test_a_clinic_in_another_active_campaign_is_skipped() -> None:
    tid = uuid.uuid4()
    a = await _clinic(tid, "Alpha Clinic")
    svc = _svc()
    first = await svc.create_campaign(tid, USER, name="First", config=_config(), provider_ids=None)
    b = await _clinic(tid, "Beta Clinic")
    second = await svc.create_campaign(tid, USER, name="Second", config=_config(), provider_ids=None)
    assert set(await _rows(tid, second)) == {b}  # Alpha is still in the first (draft) campaign
    assert [c["id"] for c in await svc.eligible_clinics(tid, ["TR"])] == []
    with pytest.raises(OutreachError):
        await svc.create_campaign(tid, USER, name="Nothing left", config=_config(), provider_ids=[a])

    await svc.stop(tid, USER, first)
    assert [c["id"] for c in await svc.eligible_clinics(tid, ["TR"])] == [str(a)]
    third = await svc.create_campaign(tid, USER, name="Third", config=_config(), provider_ids=[a])
    assert set(await _rows(tid, third)) == {a}

    # a terminal stage in a live campaign also releases the clinic
    async with async_session_maker() as s:
        await s.execute(update(ClinicOutreach).where(ClinicOutreach.campaign_id == second).values(stage=OutreachStage.DECLINED))
        await s.commit()
    assert [c["id"] for c in await svc.eligible_clinics(tid, ["TR"])] == [str(b)]


async def test_find_contacts_moves_clinics_to_ready_or_no_email_and_records_the_source() -> None:
    tid = uuid.uuid4()
    found = await _clinic(tid, "Acme Dental Clinic")
    missing = await _clinic(tid, "Quiet Clinic")
    owner = await _clinic(tid, "Owner Clinic", email="Patients@Owner-Clinic.example ")
    flaky = await _clinic(tid, "Flaky Clinic")
    search = _search_for({"Acme Dental Clinic": "acme-dental.example"})
    svc = _svc(search)
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)

    original = search.search

    async def search_some_fail(query, max_results=5):
        if query.startswith("Flaky Clinic "):
            raise RuntimeError("timeout")
        return await original(query, max_results)

    search.search = search_some_fail
    r = await svc.find_contacts(tid, cid, limit=10)
    assert r == {"status": "ok", "checked": 4, "found": 2, "no_email": 1, "search_failed": 1, "search_configured": True}
    assert not any(q.startswith("Owner Clinic ") for q in search.queries)  # an owner address needs no search

    rows = await _rows(tid, cid)
    f = rows[found]
    assert (f.stage, f.contact_email, f.contact_source, f.contact_source_url) == (OutreachStage.READY, "international@acme-dental.example", "site", "https://www.acme-dental.example/contact")
    assert f.contact_found_at is not None and f.contact_checked_at is not None
    m = rows[missing]
    assert (m.stage, m.contact_email, m.contact_source) == (OutreachStage.NO_EMAIL, None, None) and m.contact_checked_at is not None
    o = rows[owner]
    assert (o.stage, o.contact_email, o.contact_source) == (OutreachStage.READY, "patients@owner-clinic.example", "owner")
    fl = rows[flaky]
    assert (fl.stage, fl.contact_checked_at) == (OutreachStage.QUEUED, None)  # left to be retried

    audits = await _audits(tid, "outreach.contact_found")
    assert len(audits) == 2 and all("@" not in str(a.input_summary) for a in audits)  # ids only, never the address


async def test_find_contacts_is_bounded_by_limit() -> None:
    tid = uuid.uuid4()
    for i in range(5):
        await _clinic(tid, f"Clinic {i}")
    search = FakeSearch()
    svc = _svc(search)
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    r = await svc.find_contacts(tid, cid, limit=2)
    assert r["checked"] == 2 and len(search.queries) == 2
    stages = [row.stage for row in (await _rows(tid, cid)).values()]
    assert stages.count(OutreachStage.QUEUED) == 3 and stages.count(OutreachStage.NO_EMAIL) == 2
    r = await svc.find_contacts(tid, cid, limit=10)
    assert r["checked"] == 3  # the fresh NO_EMAIL rows are not retried yet


async def test_find_contacts_without_a_search_provider_returns_not_configured_and_changes_nothing() -> None:
    tid = uuid.uuid4()
    a = await _clinic(tid, "Alpha Clinic")
    svc = _svc(None)
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    r = await svc.find_contacts(tid, cid)
    assert r == {"status": "not_configured", "checked": 0, "found": 0, "no_email": 0, "search_failed": 0, "search_configured": False}
    row = (await _rows(tid, cid))[a]
    assert (row.stage, row.contact_checked_at) == (OutreachStage.QUEUED, None)

    # with no search, a clinic that already has its own address is still processed; the others are left alone
    tid2 = uuid.uuid4()
    own = await _clinic(tid2, "Own Clinic", email="info@own-clinic.example")
    other = await _clinic(tid2, "Plain Clinic")
    cid2 = await svc.create_campaign(tid2, USER, name="C2", config=_config(), provider_ids=None)
    r = await svc.find_contacts(tid2, cid2)
    assert r == {"status": "ok", "checked": 1, "found": 1, "no_email": 0, "search_failed": 0, "search_configured": False}
    rows = await _rows(tid2, cid2)
    assert rows[own].stage == OutreachStage.READY and (rows[other].stage, rows[other].contact_checked_at) == (OutreachStage.QUEUED, None)


async def test_a_no_email_clinic_is_retried_only_after_14_days() -> None:
    tid = uuid.uuid4()
    old = await _clinic(tid, "Old Clinic")
    recent = await _clinic(tid, "Recent Clinic")
    search = FakeSearch()
    svc = _svc(search)
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    assert (await svc.find_contacts(tid, cid))["no_email"] == 2
    now = datetime.now(UTC)
    async with async_session_maker() as s:
        await s.execute(update(ClinicOutreach).where(ClinicOutreach.provider_id == old).values(contact_checked_at=now - timedelta(days=15)))
        await s.execute(update(ClinicOutreach).where(ClinicOutreach.provider_id == recent).values(contact_checked_at=now - timedelta(days=13)))
        await s.commit()
    search.queries.clear()
    search.pages = {"Old Clinic": ("https://www.old-clinic.example/contact", "Old Clinic welcomes you. Email international@old-clinic.example")}
    r = await svc.find_contacts(tid, cid)
    assert r == {"status": "ok", "checked": 1, "found": 1, "no_email": 0, "search_failed": 0, "search_configured": True}
    assert [q.split(" Istanbul")[0] for q in search.queries] == ["Old Clinic"]
    rows = await _rows(tid, cid)
    assert rows[old].stage == OutreachStage.READY and rows[recent].stage == OutreachStage.NO_EMAIL


async def test_set_contact_rejects_unusable_and_suppressed_addresses() -> None:
    tid = uuid.uuid4()
    a = await _clinic(tid, "Alpha Clinic")
    outsider = await _clinic(tid, "Outsider Clinic", country="TH")
    svc = _svc()
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    await svc.suppress(tid, " Blocked@Alpha.example ", "address", "asked to stop")
    await svc.suppress(tid, "BANNED.example", "domain", "complaint")
    assert await svc.is_suppressed(tid, "blocked@alpha.example") and await svc.is_suppressed(tid, " BLOCKED@ALPHA.EXAMPLE")
    assert await svc.is_suppressed(tid, "anyone@banned.example")
    assert not await svc.is_suppressed(tid, "info@alpha.example") and not await svc.is_suppressed(uuid.uuid4(), "blocked@alpha.example")
    with pytest.raises(OutreachError):
        await svc.suppress(tid, "x@y.example", "phone", "bad kind")

    for bad in ("someone@gmail.com", "noreply@alpha.example", "not-an-address", "blocked@alpha.example", "sales@banned.example"):
        with pytest.raises(OutreachError):
            await svc.set_contact(tid, cid, a, bad)
    with pytest.raises(OutreachError):
        await svc.set_contact(tid, cid, outsider, "info@outsider.example")  # not in this campaign
    assert (await _rows(tid, cid))[a].stage == OutreachStage.QUEUED

    await svc.set_contact(tid, cid, a, "  International@Alpha.example ")
    row = (await _rows(tid, cid))[a]
    assert (row.stage, row.contact_email, row.contact_source) == (OutreachStage.READY, "international@alpha.example", "owner") and row.contact_checked_at is not None


async def test_start_shadow_sets_active_and_records_shadow_started_at_and_writes_an_audit_row() -> None:
    tid = uuid.uuid4()
    await _clinic(tid, "Alpha Clinic")
    svc = _svc()
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    await svc.start_shadow(tid, USER, cid)
    assert (await _campaign(tid, cid)).status == CampaignStatus.ACTIVE
    async with async_session_maker() as s:
        cfg = (await s.execute(select(ClinicOutreachCampaign).where(ClinicOutreachCampaign.campaign_id == cid))).scalar_one()
    assert cfg.mode == OutreachMode.SHADOW and cfg.shadow_started_at is not None and cfg.live_at is None
    first_started = cfg.shadow_started_at
    (audit,) = await _audits(tid, "outreach.campaign_started")
    assert audit.entity_id == cid and audit.actor_id == USER
    with pytest.raises(OutreachError):
        await svc.start_shadow(tid, USER, cid)  # already active

    await svc.pause(tid, USER, cid)
    await svc.start_shadow(tid, USER, cid)
    async with async_session_maker() as s:
        cfg = (await s.execute(select(ClinicOutreachCampaign).where(ClinicOutreachCampaign.campaign_id == cid))).scalar_one()
    assert cfg.shadow_started_at == first_started and len(await _audits(tid, "outreach.campaign_started")) == 2

    # a broken stored configuration or an empty campaign cannot start
    tid2 = uuid.uuid4()
    await _clinic(tid2, "Beta Clinic")
    cid2 = await svc.create_campaign(tid2, USER, name="C2", config=_config(), provider_ids=None)
    async with async_session_maker() as s:
        await s.execute(update(ClinicOutreachCampaign).where(ClinicOutreachCampaign.campaign_id == cid2).values(sender={"display_name": ""}))
        await s.commit()
    with pytest.raises(OutreachError):
        await svc.start_shadow(tid2, USER, cid2)
    tid3 = uuid.uuid4()
    await _clinic(tid3, "Gamma Clinic")
    cid3 = await svc.create_campaign(tid3, USER, name="C3", config=_config(), provider_ids=None)
    async with async_session_maker() as s:
        await s.execute(ClinicOutreach.__table__.delete().where(ClinicOutreach.campaign_id == cid3))
        await s.commit()
    with pytest.raises(OutreachError):
        await svc.start_shadow(tid3, USER, cid3)
    assert (await _campaign(tid3, cid3)).status == CampaignStatus.DRAFT


async def test_nothing_is_sent_by_any_outreach_call(monkeypatch) -> None:
    from app.communications import factory

    def _boom(*a, **k):
        raise AssertionError("an outreach call tried to reach a message channel")

    monkeypatch.setattr(factory, "get_clinic_channel", _boom)
    monkeypatch.setattr(factory, "get_communication_provider", _boom)

    tid = uuid.uuid4()
    a = await _clinic(tid, "Acme Dental Clinic")
    b = await _clinic(tid, "Beta Clinic")
    svc = _svc(_search_for({"Acme Dental Clinic": "acme-dental.example"}))
    await svc.eligible_clinics(tid, ["TR"])
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    await svc.find_contacts(tid, cid)
    await svc.suppress(tid, "blocked.example", "domain", "test")
    await svc.is_suppressed(tid, "x@blocked.example")
    await svc.set_contact(tid, cid, b, "info@beta-clinic.example")
    await svc.start_shadow(tid, USER, cid)
    await svc.get(tid, cid)
    await svc.pause(tid, USER, cid)
    await svc.stop(tid, USER, cid)
    rows = await _rows(tid, cid)
    assert rows[a].emails_sent == 0 and rows[b].emails_sent == 0 and all(r.last_sent_at is None for r in rows.values())

    import inspect

    from app.services import clinic_outreach_service as mod

    src = inspect.getsource(mod)
    assert "get_clinic_channel" not in src and "get_communication_provider" not in src and "deliver_" not in src


async def test_pause_and_stop_set_status_and_never_resume_automatically() -> None:
    tid = uuid.uuid4()
    a = await _clinic(tid, "Alpha Clinic")
    svc = _svc(FakeSearch())
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    with pytest.raises(OutreachError):
        await svc.pause(tid, USER, cid)  # a draft is not running
    await svc.start_shadow(tid, USER, cid)
    await svc.pause(tid, USER, cid)
    assert (await _campaign(tid, cid)).status == CampaignStatus.PAUSED
    with pytest.raises(OutreachError):
        await svc.pause(tid, USER, cid)
    await svc.find_contacts(tid, cid)  # discovery may still run while paused, and does not resume the campaign
    assert (await _campaign(tid, cid)).status == CampaignStatus.PAUSED
    assert len(await _audits(tid, "outreach.campaign_paused")) == 1

    async with async_session_maker() as s:
        await s.execute(update(ClinicOutreach).where(ClinicOutreach.campaign_id == cid).values(next_action_at=datetime.now(UTC)))
        await s.commit()
    await svc.stop(tid, USER, cid)
    assert (await _campaign(tid, cid)).status == CampaignStatus.COMPLETED
    assert (await _rows(tid, cid))[a].next_action_at is None
    assert len(await _audits(tid, "outreach.campaign_stopped")) == 1
    for call in (svc.start_shadow, svc.pause, svc.stop):
        with pytest.raises(OutreachError):
            await call(tid, USER, cid)
    with pytest.raises(OutreachError):
        await svc.find_contacts(tid, cid)
    assert (await _campaign(tid, cid)).status == CampaignStatus.COMPLETED

    # a draft can be stopped directly
    tid2 = uuid.uuid4()
    await _clinic(tid2, "Beta Clinic")
    cid2 = await svc.create_campaign(tid2, USER, name="C2", config=_config(), provider_ids=None)
    await svc.stop(tid2, USER, cid2)
    assert (await _campaign(tid2, cid2)).status == CampaignStatus.COMPLETED


async def test_other_tenants_cannot_read_or_change_a_campaign() -> None:
    tid, other = uuid.uuid4(), uuid.uuid4()
    a = await _clinic(tid, "Alpha Clinic")
    await _clinic(other, "Other Clinic")
    svc = _svc(_search_for({"Alpha Clinic": "alpha.example"}))
    cid = await svc.create_campaign(tid, USER, name="Mine", config=_config(), provider_ids=None)

    # the other tenant cannot pick this tenant's clinic either
    with pytest.raises(OutreachError):
        await svc.create_campaign(other, USER, name="Theirs", config=_config(), provider_ids=[a])

    for call in (svc.get(other, cid), svc.find_contacts(other, cid), svc.set_contact(other, cid, a, "info@alpha.example"),
                 svc.start_shadow(other, USER, cid), svc.pause(other, USER, cid), svc.stop(other, USER, cid),
                 svc.get(tid, uuid.uuid4())):
        with pytest.raises(OutreachNotFound):
            await call
    assert issubclass(OutreachNotFound, OutreachError)

    assert (await _campaign(tid, cid)).status == CampaignStatus.DRAFT
    row = (await _rows(tid, cid))[a]
    assert (row.stage, row.contact_email, row.contact_checked_at) == (OutreachStage.QUEUED, None, None)
    await svc.suppress(other, "alpha.example", "domain", "theirs")
    assert not await svc.is_suppressed(tid, "info@alpha.example")


# --- fix round 1 -----------------------------------------------------------------------------------------------------


async def test_concurrent_creates_never_put_a_clinic_in_two_open_campaigns() -> None:
    tid = uuid.uuid4()
    ids = {await _clinic(tid, f"Clinic {i}") for i in range(4)}
    svc = _svc()
    first, second = await asyncio.gather(
        svc.create_campaign(tid, USER, name="First", config=_config(), provider_ids=None),
        svc.create_campaign(tid, USER, name="Second", config=_config(), provider_ids=None),
        return_exceptions=True)
    # the first call holds the tenant's lock and takes every clinic; the second re-checks inside the lock and finds none left
    assert isinstance(first, uuid.UUID)
    assert isinstance(second, OutreachError) and "No eligible clinics" in str(second)
    assert set(await _rows(tid, first)) == ids
    async with async_session_maker() as s:
        held = (await s.execute(select(ClinicOutreach.provider_id).where(ClinicOutreach.tenant_id == tid))).scalars().all()
        campaigns = (await s.execute(select(Campaign).where(Campaign.tenant_id == tid))).scalars().all()
    assert sorted(held) == sorted(ids) and len(campaigns) == 1  # each clinic exactly once, and no empty campaign was left behind

    # overlapping hand-picked lists: the second gets only what the first did not take
    tid2 = uuid.uuid4()
    a = await _clinic(tid2, "A Clinic")
    b = await _clinic(tid2, "B Clinic")
    c = await _clinic(tid2, "C Clinic")
    one, two = await asyncio.gather(
        svc.create_campaign(tid2, USER, name="One", config=_config(), provider_ids=[a, b]),
        svc.create_campaign(tid2, USER, name="Two", config=_config(), provider_ids=[b, c]))
    assert set(await _rows(tid2, one)) == {a, b} and set(await _rows(tid2, two)) == {c}


async def _set_stage(tid, cid, provider_id, stage) -> None:
    async with async_session_maker() as s:
        await s.execute(update(ClinicOutreach).where(ClinicOutreach.tenant_id == tid, ClinicOutreach.campaign_id == cid,
                                                     ClinicOutreach.provider_id == provider_id).values(stage=stage))
        await s.commit()


async def test_set_contact_only_for_early_stages_in_an_open_campaign() -> None:
    tid = uuid.uuid4()
    p = await _clinic(tid, "Alpha Clinic")
    svc = _svc()
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    for stage in (OutreachStage.OPTED_OUT, OutreachStage.DECLINED, OutreachStage.INTRO_SENT, OutreachStage.HANDED_TO_VERIFICATION):
        await _set_stage(tid, cid, p, stage)
        with pytest.raises(OutreachError, match="stage"):
            await svc.set_contact(tid, cid, p, "info@alpha.example")
        assert (await _rows(tid, cid))[p].stage == stage and (await _rows(tid, cid))[p].contact_email is None
    for stage in (OutreachStage.QUEUED, OutreachStage.NO_EMAIL, OutreachStage.READY):
        await _set_stage(tid, cid, p, stage)
        await svc.set_contact(tid, cid, p, f"{stage.lower()}@alpha.example")
        row = (await _rows(tid, cid))[p]
        assert (row.stage, row.contact_email) == (OutreachStage.READY, f"{stage.lower()}@alpha.example")
    audits = await _audits(tid, "outreach.contact_set")
    assert len(audits) == 3 and all("@" not in str(a.input_summary) for a in audits) and audits[0].entity_id == cid

    await _set_stage(tid, cid, p, OutreachStage.QUEUED)
    await svc.stop(tid, USER, cid)
    with pytest.raises(OutreachError, match="COMPLETED"):
        await svc.set_contact(tid, cid, p, "late@alpha.example")
    assert (await _rows(tid, cid))[p].stage == OutreachStage.QUEUED


async def test_an_unusable_or_suppressed_own_address_falls_through_to_the_site_search() -> None:
    tid = uuid.uuid4()
    gm = await _clinic(tid, "Gmail Clinic", email="gmailclinic@gmail.com")
    quiet = await _clinic(tid, "Quiet Gmail Clinic", email="quiet@gmail.com")
    sup = await _clinic(tid, "Suppressed Clinic", email="boss@suppressed-clinic.example")
    search = FakeSearch({
        "Gmail Clinic": ("https://www.gmail-clinic.example/contact", "Gmail Clinic welcomes you. Email international@gmail-clinic.example"),
        "Suppressed Clinic": ("https://www.suppressed-clinic.example/contact", "Suppressed Clinic: write to patients@suppressed-clinic.example"),
    })
    svc = _svc(search)
    await svc.suppress(tid, "boss@suppressed-clinic.example", "address", "asked to stop")
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    r = await svc.find_contacts(tid, cid)
    assert r == {"status": "ok", "checked": 3, "found": 2, "no_email": 1, "search_failed": 0, "search_configured": True}
    assert {q.split(" Istanbul")[0] for q in search.queries} == {"Gmail Clinic", "Quiet Gmail Clinic", "Suppressed Clinic"}
    rows = await _rows(tid, cid)
    assert (rows[gm].stage, rows[gm].contact_email, rows[gm].contact_source) == (OutreachStage.READY, "international@gmail-clinic.example", "site")
    assert (rows[sup].stage, rows[sup].contact_email, rows[sup].contact_source) == (OutreachStage.READY, "patients@suppressed-clinic.example", "site")
    assert (rows[quiet].stage, rows[quiet].contact_email) == (OutreachStage.NO_EMAIL, None)
    async with async_session_maker() as s:
        own = dict((await s.execute(select(Provider.id, Provider.contact_email).where(Provider.tenant_id == tid))).all())
    assert own == {gm: "gmailclinic@gmail.com", quiet: "quiet@gmail.com", sup: "boss@suppressed-clinic.example"}  # the clinic's own record is untouched

    # without a search provider an unusable own address is not something to process: nothing changes
    tid2 = uuid.uuid4()
    g2 = await _clinic(tid2, "Gmail Clinic", email="gmailclinic@gmail.com")
    none_svc = _svc(None)
    cid2 = await none_svc.create_campaign(tid2, USER, name="C2", config=_config(), provider_ids=None)
    r = await none_svc.find_contacts(tid2, cid2)
    assert r == {"status": "not_configured", "checked": 0, "found": 0, "no_email": 0, "search_failed": 0, "search_configured": False}
    assert (await _rows(tid2, cid2))[g2].stage == OutreachStage.QUEUED


async def _auto_clinic(tid, name, email) -> uuid.UUID:
    async with async_session_maker() as s:
        p = Provider(tenant_id=tid, name=name, country="TR", city="Istanbul", status=ProviderStatus.ACTIVE, contact_email=email,
                     verification_status="UNVERIFIED", idempotency_key=f"auto-{uuid.uuid4()}")
        s.add(p)
        await s.commit()
        return p.id


async def test_auto_sourced_record_address_is_not_trusted_and_goes_through_the_search() -> None:
    tid = uuid.uuid4()
    auto = await _auto_clinic(tid, "Auto Clinic", "random@elsewhere.example")
    search = _search_for({"Auto Clinic": "auto-clinic.example"})
    svc = _svc(search)
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    r = await svc.find_contacts(tid, cid)
    assert r["found"] == 1 and any(q.startswith("Auto Clinic ") for q in search.queries)
    row = (await _rows(tid, cid))[auto]
    assert (row.contact_email, row.contact_source) == ("international@auto-clinic.example", "site")


async def test_person_created_address_is_mx_checked_and_without_mx_is_not_ready() -> None:
    tid = uuid.uuid4()
    good = await _clinic(tid, "Good Clinic", email="info@good-clinic.example")
    bad = await _clinic(tid, "Nomx Clinic", email="info@nomx.example")

    async def mx(domain: str) -> bool:
        return domain != "nomx.example"

    svc = ClinicOutreachService(async_session_maker, search_factory=lambda: FakeSearch(), mx_exists=mx)
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    await svc.find_contacts(tid, cid)
    rows = await _rows(tid, cid)
    assert (rows[good].stage, rows[good].contact_source) == (OutreachStage.READY, "owner")
    assert rows[bad].stage == OutreachStage.NO_EMAIL and rows[bad].contact_email is None


@pytest.mark.parametrize("bad", ["info@clinic.example\r\nBcc: x@evil.example", "a b@clinic.example", "x@y", '"a"@clinic.example', "info@-.example",
                                 "a@b..example", "info@clinic.example,other@x.example"])
async def test_strict_address_syntax_in_usable_address_set_contact_and_suppress(bad) -> None:
    from app.services.clinic_contact_finder import usable_address
    assert not usable_address(bad)
    tid = uuid.uuid4()
    a = await _clinic(tid, "Alpha Clinic")
    svc = _svc()
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    with pytest.raises(OutreachError):
        await svc.set_contact(tid, cid, a, bad)
    with pytest.raises(OutreachError):
        await svc.suppress(tid, bad, "address", "x")
    assert (await _rows(tid, cid))[a].stage == OutreachStage.QUEUED


def test_usable_address_does_not_strip_a_trailing_newline() -> None:
    from app.services.clinic_contact_finder import usable_address
    assert usable_address("info@clinic.example") and not usable_address("info@clinic.example\n")


async def test_source_url_is_truncated_to_2000_chars() -> None:
    tid = uuid.uuid4()
    c = await _clinic(tid, "Acme Dental Clinic")
    long_url = "https://www.acme-dental.example/contact?" + "q" * 3000
    search = FakeSearch({"Acme Dental Clinic": (long_url, "Acme Dental Clinic: international@acme-dental.example")})
    svc = _svc(search)
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    await svc.find_contacts(tid, cid)
    row = (await _rows(tid, cid))[c]
    assert row.stage == OutreachStage.READY and len(row.contact_source_url) == 2000


async def test_find_contacts_default_limit_is_five() -> None:
    import inspect
    assert inspect.signature(ClinicOutreachService.find_contacts).parameters["limit"].default == 5


async def test_set_contact_audit_records_the_acting_user() -> None:
    tid = uuid.uuid4()
    a = await _clinic(tid, "Alpha Clinic")
    svc = _svc()
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    actor = uuid.uuid4()
    await svc.set_contact(tid, cid, a, "info@alpha.example", user_id=actor)
    assert [r.actor_id for r in await _audits(tid, "outreach.contact_set")] == [actor]


async def test_campaign_service_set_status_refuses_an_outreach_campaign() -> None:
    from app.services.campaign_service import CampaignService, OutreachCampaignStatusError
    tid = uuid.uuid4()
    await _clinic(tid, "Alpha Clinic")
    svc = _svc()
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    cs = CampaignService(async_session_maker, None, None)  # set_status uses neither the bus nor the exception service
    with pytest.raises(OutreachCampaignStatusError, match="outreach"):
        await cs.set_status(tid, cid, "ACTIVE")
    assert (await _campaign(tid, cid)).status == CampaignStatus.DRAFT
