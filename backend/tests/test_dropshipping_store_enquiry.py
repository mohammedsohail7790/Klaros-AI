"""The store's "write to us" form: an enquiry becomes an ordinary lead in the store business's CRM, works while the store is closed, and gives a bot nothing."""

import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.crm import Lead
from tests.test_business_journey_api import _register
from tests.test_dropshipping_api import _tenant_id

ENQUIRY = "/api/v1/public/store/enquiry"


@pytest.fixture(autouse=True)
def _generous_limit(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "RATE_LIMIT_PUBLIC_STORE_ENQUIRY_PER_MINUTE", 100)


async def _store_tenant(client, monkeypatch, org: str, email: str) -> uuid.UUID:
    owner = await _register(client, org, email)
    tenant_id = await _tenant_id(client, owner)
    monkeypatch.setattr(get_settings(), "DROPSHIPPING_STORE_TENANT_ID", str(tenant_id))
    return tenant_id


async def _leads(tenant_id: uuid.UUID) -> list[Lead]:
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant_id)
        return list((await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalars())


async def test_an_enquiry_becomes_a_lead_even_while_the_store_is_closed(client, monkeypatch) -> None:
    tenant = await _store_tenant(client, monkeypatch, "Enquiry Shop", "enq1@example.com")
    r = await client.post(ENQUIRY, json={"name": "Sam Shopper", "email": "sam@example.com", "message": "Do you ship to Ohio?", "order_number": "CR-1001"})
    assert r.status_code == 201 and r.json() == {"received": True}                      # nothing else comes back: no lead id
    leads = await _leads(tenant)
    assert len(leads) == 1
    assert leads[0].name == "Sam Shopper" and leads[0].email == "sam@example.com"
    assert "Do you ship to Ohio?" in (leads[0].description or "") and "CR-1001" in (leads[0].description or "")
    assert leads[0].service_requested == "Store enquiry"


async def test_a_bot_that_fills_the_hidden_field_is_told_all_is_well_and_nothing_is_stored(client, monkeypatch) -> None:
    tenant = await _store_tenant(client, monkeypatch, "Bot Shop", "enq2@example.com")
    r = await client.post(ENQUIRY, json={"name": "Bot", "email": "bot@example.com", "message": "buy pills", "website": "http://spam.example"})
    assert r.status_code == 201 and r.json() == {"received": True}
    assert await _leads(tenant) == []


@pytest.mark.parametrize("bad", [
    {"name": "", "email": "a@b.co", "message": "hi"},
    {"name": "A", "email": "not-an-email", "message": "hi"},
    {"name": "A", "email": "a@b.co", "message": ""},
    {"name": "A", "email": "a@b.co", "message": "x" * 1801},
    {"name": "A", "email": "a@b.co", "message": "hi", "tenant_id": str(uuid.uuid4())},   # the caller can never pick the business
    {"name": "A", "email": "a@b.co", "message": "hi", "source": "CHAT"},                  # nor the lead source
])
async def test_bad_enquiries_are_refused_and_store_nothing(client, monkeypatch, bad) -> None:
    tenant = await _store_tenant(client, monkeypatch, "Bad Shop", f"enq3{uuid.uuid4().hex[:6]}@example.com")
    r = await client.post(ENQUIRY, json=bad)
    assert r.status_code == 422
    assert await _leads(tenant) == []


async def test_no_store_configured_means_the_form_does_not_exist(client, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "DROPSHIPPING_STORE_TENANT_ID", None)
    r = await client.post(ENQUIRY, json={"name": "A", "email": "a@b.co", "message": "hi"})
    assert r.status_code == 404


async def test_the_form_is_rate_limited(client, monkeypatch) -> None:
    await _store_tenant(client, monkeypatch, "Limit Shop", "enq4@example.com")
    monkeypatch.setattr(get_settings(), "RATE_LIMIT_PUBLIC_STORE_ENQUIRY_PER_MINUTE", 2)
    codes = [(await client.post(ENQUIRY, json={"name": "A", "email": "a@b.co", "message": f"hi {i}"})).status_code for i in range(4)]
    assert codes[:2] == [201, 201] and 429 in codes[2:]
