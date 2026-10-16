"""Clinic list clean-up: duplicates and junk are found, the best entry is kept, and only suggested entries can be suspended (reversibly)."""

import pytest

from app.services.mt_clinic_cleanup import is_junk, normalize
from tests.test_mt_referrals import BASE, _gated, _h, _provider, halla  # noqa: F401

pytestmark = pytest.mark.asyncio


def test_normalize_treats_plural_brackets_and_generic_words_as_the_same_clinic() -> None:
    assert normalize("Apollo Hospital") == normalize("Apollo Hospitals") == normalize("Apollo Hospital (Delhi)") == "apollo"
    assert normalize("Bangkok International Dental Center") == normalize("Bangkok International Dental Center (BIDC)") == "bangkok dental"
    assert normalize("Artemis Hospital") != normalize("Apollo Hospital")


def test_junk_names_are_recognised_but_real_ones_are_not() -> None:
    assert is_junk("TEST Clinic (delete me)") and is_junk("TOP-10 Best Plastic Surgery Clinics") and is_junk("Best dental clinics in Turkey")
    assert not is_junk("Bumrungrad International Hospital") and not is_junk("Testa Dental")


async def test_preview_keeps_the_verified_entry_and_apply_suspends_only_suggested_ones(client, halla) -> None:  # noqa: F811
    token, _tid = await _gated(client, "Cleanup Co", "cleanup@example.com")
    keep = await _provider(client, token, name="Apollo Hospitals", country="IN", verified=True)
    dup = await _provider(client, token, name="Apollo Hospital", country="IN", verified=False)
    other_country = await _provider(client, token, name="Apollo Hospital", country="TH", verified=False)
    junk = await _provider(client, token, name="TEST Clinic (delete me)", country="IN", verified=False)
    plain = await _provider(client, token, name="Artemis Hospital", country="IN", verified=False)

    preview = (await client.get(f"{BASE}/clinics/cleanup", headers=_h(token))).json()
    assert len(preview["groups"]) == 1
    group = preview["groups"][0]
    assert group["keep"]["id"] == keep["id"] and [r["id"] for r in group["redundant"]] == [dup["id"]]
    assert [j["id"] for j in preview["junk"]] == [junk["id"]]
    assert set(preview["suggested_ids"]) == {dup["id"], junk["id"]}

    # An entry that is not suggested (the one kept, another country, a plain clinic) can never be suspended through this route.
    for blocked in (keep, other_country, plain):
        r = await client.post(f"{BASE}/clinics/cleanup/apply", json={"provider_ids": [blocked["id"]]}, headers=_h(token))
        assert r.status_code == 409, r.text
    assert (await client.post(f"{BASE}/clinics/cleanup/apply", json={"provider_ids": []}, headers=_h(token))).status_code == 422

    done = await client.post(f"{BASE}/clinics/cleanup/apply", json={"provider_ids": [dup["id"], junk["id"]]}, headers=_h(token))
    assert done.status_code == 200 and done.json() == {"suspended": 2}
    again = (await client.get(f"{BASE}/clinics/cleanup", headers=_h(token))).json()
    assert again["groups"] == [] and again["junk"] == [] and again["active_clinics"] == 3
