"""GET /tenant/modules: a tenant sees exactly the modules it enabled, and no other tenant's."""

import pytest

from tests.test_consent_gate_intake import _enable_vertical, _gated, _plain
from tests.test_halla_integration import _h, halla  # noqa: F401


@pytest.mark.asyncio
async def test_a_tenant_with_no_module_gets_an_empty_list(client, halla) -> None:  # noqa: F811
    token, _ = await _plain(client, "Plain Modules Co", "plainmods@example.com")
    r = await client.get("/api/v1/tenant/modules", headers=_h(token))
    assert r.status_code == 200 and r.json() == {"modules": []}


@pytest.mark.asyncio
async def test_a_tenant_sees_the_modules_it_enabled_and_only_those(client, halla) -> None:  # noqa: F811
    gated, tid = await _gated(client, "Mods Gated Co", "modsgated@example.com")
    await _enable_vertical(tid)
    plain, _ = await _plain(client, "Mods Other Co", "modsother@example.com")
    assert (await client.get("/api/v1/tenant/modules", headers=_h(gated))).json() == {"modules": ["medical_tourism"]}
    assert (await client.get("/api/v1/tenant/modules", headers=_h(plain))).json() == {"modules": []}


@pytest.mark.asyncio
async def test_modules_need_a_signed_in_user(client) -> None:
    assert (await client.get("/api/v1/tenant/modules")).status_code == 401
