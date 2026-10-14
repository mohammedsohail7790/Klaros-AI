"""The owner can allow an external AI provider for their own business only, with an acknowledgement, and it is audited. Other businesses stay strict."""

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker, set_tenant_context
from app.models.audit_log import AuditLog
from app.services import ai_boundary
from tests.test_consent_gate_intake import _gated
from tests.test_halla_integration import _h, halla  # noqa: F401

URL = "/api/v1/tenant/settings/ai-processing"


class _External:
    name, model, is_connected = "fakeai", "m", True


async def _decide(tid):
    return await ai_boundary._decide(_External(), async_session_maker, tid, "qualification")


@pytest.mark.asyncio
async def test_default_is_strict_and_the_owner_can_allow_a_provider_for_their_business_only(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Allow Co", "allowco@example.com")
    _, other = await _gated(client, "Strict Co", "strictco@example.com")
    assert (await client.get(URL, headers=_h(token))).json()["external_providers"] == []
    assert (await _decide(tid))[0] is False and (await _decide(tid))[2] == "provider_not_allowlisted"

    r = await client.put(URL, json={"external_providers": ["FakeAI"], "acknowledged": True}, headers=_h(token))
    assert r.status_code == 200 and r.json()["external_providers"] == ["fakeai"]
    allowed, minimize, reason = await _decide(tid)
    assert (allowed, minimize, reason) == (True, True, "allowlisted")  # allowed, and contact details are still scrubbed
    assert (await _decide(other))[0] is False  # another business is unaffected

    audit = await _audit(tid)
    assert any(a.action == "tenant.ai_processing.update" for a in audit)


@pytest.mark.asyncio
async def test_an_acknowledgement_is_required_and_the_owner_can_take_it_back(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Ack Co", "ackco@example.com")
    assert (await client.put(URL, json={"external_providers": ["fakeai"]}, headers=_h(token))).status_code == 422
    assert (await _decide(tid))[0] is False
    await client.put(URL, json={"external_providers": ["fakeai"], "acknowledged": True}, headers=_h(token))
    assert (await _decide(tid))[0] is True
    await client.put(URL, json={"external_providers": []}, headers=_h(token))
    assert (await _decide(tid))[0] is False
    assert (await client.put(URL, json={"external_providers": ["bad name!"], "acknowledged": True}, headers=_h(token))).status_code == 422
    assert (await client.get(URL)).status_code == 401


async def _audit(tid):
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return list((await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid))).scalars())


def test_only_the_owner_role_may_change_it() -> None:
    import uuid

    from fastapi import HTTPException

    from app.api.deps import CurrentUser
    from app.api.v1.tenant_settings import _owner_only
    from app.models.rbac import Role

    _owner_only(CurrentUser(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role=Role.OWNER))  # no error
    for role in (Role.MANAGER, Role.STAFF if hasattr(Role, "STAFF") else Role.MANAGER):
        with pytest.raises(HTTPException) as err:
            _owner_only(CurrentUser(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role=role))
        assert err.value.status_code == 403
