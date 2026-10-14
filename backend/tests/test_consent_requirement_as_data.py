"""A business module can declare in its own registry data that it needs consent; the hard-coded keys remain a floor, so nothing gets looser."""

import pytest

from app.db.session import async_session_maker
from app.services import consent_gate
from app.services.vertical_extension_service import VerticalExtensionService
from tests.test_consent_gate_intake import _enable_vertical, _plain
from tests.test_halla_integration import _tenant_id, halla  # noqa: F401


async def _tid(client, name, email):
    token, _ = await _plain(client, name, email)
    return await _tenant_id(client, token)


@pytest.mark.asyncio
async def test_a_module_that_declares_requires_consent_gates_its_tenants_and_others_stay_free(client, halla) -> None:  # noqa: F811
    vx = VerticalExtensionService(async_session_maker)
    await vx.create_vertical(key="data_gated_demo", name="Gated Demo", extra_metadata={"requires_consent": True})
    await vx.create_vertical(key="data_open_demo", name="Open Demo", extra_metadata={"note": "no consent flag"})
    gated, open_, none = await _tid(client, "Dg Co", "dg@example.com"), await _tid(client, "Do Co", "do@example.com"), await _tid(client, "Dn Co", "dn@example.com")
    await vx.enable_for_organization(gated, "data_gated_demo")
    await vx.enable_for_organization(open_, "data_open_demo")
    assert await consent_gate.tenant_requires_consent(async_session_maker, gated) is True
    assert await consent_gate.tenant_requires_consent(async_session_maker, open_) is False
    assert await consent_gate.tenant_requires_consent(async_session_maker, none) is False


@pytest.mark.asyncio
async def test_medical_tourism_is_still_gated_without_any_metadata_flag(client, halla) -> None:  # noqa: F811
    tid = await _tid(client, "Mt Floor Co", "mtfloor@example.com")
    await _enable_vertical(tid)  # the registry row here carries no requires_consent flag: the hard-coded key is the floor
    assert await consent_gate.tenant_requires_consent(async_session_maker, tid) is True
