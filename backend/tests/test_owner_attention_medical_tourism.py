"""The owner's attention queue also lists what only a person can fix for a Medical Tourism business: no verified clinic, patient tasks waiting, a referral with no commission terms."""

import uuid

import pytest

from app.db.session import async_session_maker
from app.models.medical_tourism import Provider
from app.services.owner_attention_service import AttentionCategory, OwnerAttentionService
from tests.test_mt_referrals import BASE, _approved, _gated, _h, _patient, _provider, _send, halla  # noqa: F401

pytestmark = pytest.mark.asyncio


async def _items(tid):
    return await OwnerAttentionService(async_session_maker).get_attention_queue(tid)


async def test_a_business_with_no_clinics_or_tasks_gets_no_medical_tourism_items() -> None:
    assert [i for i in await _items(uuid.uuid4()) if i.category.startswith("mt_")] == []


async def test_unverified_clinics_and_a_referral_without_terms_are_raised_and_clear_when_fixed(client, halla) -> None:  # noqa: F811
    token, tid_str = await _gated(client, "Attention Co", "attention@example.com")
    me = (await client.get("/api/v1/auth/me", headers=_h(token))).json()
    tid = uuid.UUID(str(me.get("tenant_id") or me.get("organization_id") or tid_str))
    await _provider(client, token, name="Unverified Clinic", verified=False)
    cats = {i.category: i for i in await _items(tid)}
    assert cats[AttentionCategory.MT_NO_VERIFIED_CLINIC].priority == "HIGH" and cats[AttentionCategory.MT_NO_VERIFIED_CLINIC].link == "/medical-tourism/agreements"

    verified = await _provider(client, token, name="Verified Clinic", verified=True)
    ref = await _approved(client, token, await _patient(client, token), verified["id"])
    assert (await _send(client, token, ref)).status_code == 200
    cats = {i.category: i for i in await _items(tid)}
    assert AttentionCategory.MT_NO_VERIFIED_CLINIC not in cats
    assert "Verified Clinic has 1 referral but no commission terms" in cats[AttentionCategory.MT_REFERRAL_NO_TERMS].title

    r = await client.put(f"{BASE}/providers/{verified['id']}/terms", json={"commission_basis": "FLAT", "flat_amount": "300", "currency": "usd"}, headers=_h(token))
    assert r.status_code == 200, r.text
    assert AttentionCategory.MT_REFERRAL_NO_TERMS not in {i.category for i in await _items(tid)}
    other = await _items(uuid.uuid4())
    assert not [i for i in other if i.category.startswith("mt_")]  # another business sees none of it
