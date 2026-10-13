"""The public website's partner network lists only VERIFIED clinics.

Matching and referrals already refuse an unverified provider; the public site
must not present one as a vetted partner either. Runs without Postgres: the
service is faked.
"""

import types
import uuid

import pytest

import app.services.medical_tourism_service as mt


def _provider(name: str, verification_status: str):
    return types.SimpleNamespace(
        id=uuid.uuid4(), name=name, city="Istanbul", country="TR", description="",
        verification_status=verification_status,
    )


@pytest.mark.asyncio
async def test_unverified_providers_are_not_listed_publicly(monkeypatch) -> None:
    providers = [_provider("Verified Clinic", "VERIFIED"), _provider("Unverified Clinic", "UNVERIFIED")]

    async def fake_list_providers(self, tenant_id, **kwargs):
        return providers, len(providers)

    async def fake_list_offerings(self, tenant_id, **kwargs):
        return [], 0

    monkeypatch.setattr(mt.MedicalTourismService, "list_providers", fake_list_providers)
    monkeypatch.setattr(mt.MedicalTourismService, "list_offerings", fake_list_offerings)

    result = await mt._provide_website_provider_directory(uuid.uuid4(), {"limit": 10})
    assert [i["name"] for i in result["items"]] == ["Verified Clinic"]
