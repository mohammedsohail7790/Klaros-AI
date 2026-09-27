"""Phase 1.3 (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.3): `GET
/api/v1/tools/catalog` — a read-only projection over the existing
`ToolRegistry`. Covers: the endpoint returns exactly the currently-
registered tools' metadata (cross-checked directly against
`ToolRegistry` introspection, per the plan's own acceptance test), and
the explicit proof this can never execute a tool or have any side effect
(no AuditLog row, no tool-specific side effect table write) — the
requirement named in the Phase 1 task's Step 9.
"""

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.services.tool_catalog_service import list_tool_catalog

pytestmark = pytest.mark.asyncio


async def _register(client, org_name: str, email: str) -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": org_name, "full_name": "Owner Test",
            "email": email, "password": "supersecret1",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["tokens"]["access_token"]


async def test_catalog_requires_authentication(client) -> None:
    resp = await client.get("/api/v1/tools/catalog")
    assert resp.status_code in (401, 403)


async def test_catalog_matches_registry_introspection_exactly(client, tool_registry) -> None:
    token = await _register(client, "Tool Catalog Co", "owner@toolcatalogco.com")
    resp = await client.get("/api/v1/tools/catalog", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    direct = list_tool_catalog(tool_registry)
    assert len(body) == len(direct) > 0
    assert {e["name"] for e in body} == {e.name for e in direct}

    by_name = {e["name"]: e for e in body}
    for entry in direct:
        api_entry = by_name[entry.name]
        assert api_entry["description"] == entry.description
        assert api_entry["required_permission"] == entry.required_permission
        assert api_entry["tenant_scoped"] == entry.tenant_scoped
        assert api_entry["counts_toward_ai_usage"] == entry.counts_toward_ai_usage


async def test_catalog_is_unfiltered_by_caller_role(client, tool_registry) -> None:
    """Unlike `GET /tools` (role-filtered `list_available`), the catalog
    must return every registered tool regardless of the caller's own
    role — a READ_ONLY user must see the exact same catalog an OWNER does,
    because this is "what tools exist", not "what can I invoke"."""
    from sqlalchemy import select as sa_select

    from app.core.security import create_access_token, hash_password
    from app.db.session import async_session_maker
    from app.models.rbac import Role
    from app.models.user import User

    owner_token = await _register(client, "Catalog Role Co", "owner@catalogroleco.com")

    async with async_session_maker() as session:
        owner = (
            await session.execute(sa_select(User).where(User.email == "owner@catalogroleco.com"))
        ).scalar_one()
        tenant_id = owner.tenant_id
        reader = User(
            tenant_id=tenant_id, email="reader@catalogroleco.com", full_name="Reader",
            hashed_password=hash_password("supersecret1"), role=Role.READ_ONLY,
        )
        session.add(reader)
        await session.commit()
        await session.refresh(reader)
        reader_token = create_access_token(reader.id, tenant_id, Role.READ_ONLY)

    owner_resp = await client.get("/api/v1/tools/catalog", headers={"Authorization": f"Bearer {owner_token}"})
    reader_resp = await client.get("/api/v1/tools/catalog", headers={"Authorization": f"Bearer {reader_token}"})
    assert owner_resp.status_code == 200
    assert reader_resp.status_code == 200
    assert {e["name"] for e in owner_resp.json()} == {e["name"] for e in reader_resp.json()}


async def test_catalog_read_never_executes_a_tool_or_writes_audit_log(client, tool_registry) -> None:
    """The explicit proof required by the Phase 1 task: calling the
    catalog endpoint must not execute any tool, and therefore must never
    create an AuditLog row (every real tool execution — success or
    failure — writes one, per app/tools/registry.py's `execute()`
    pipeline)."""
    from app.db.session import async_session_maker

    token = await _register(client, "Catalog Readonly Co", "owner@catalogreadonlyco.com")

    async with async_session_maker() as session:
        before = (await session.execute(select(AuditLog))).scalars().all()
        before_count = len(before)

    resp = await client.get("/api/v1/tools/catalog", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(resp.json()) > 0

    async with async_session_maker() as session:
        after = (await session.execute(select(AuditLog))).scalars().all()
        after_count = len(after)

    assert after_count == before_count, "reading the tool catalog must never write an AuditLog row"


async def test_direct_service_call_has_no_side_effects(tool_registry) -> None:
    """Same proof, at the service layer directly (no HTTP, no auth
    plumbing) — `list_tool_catalog` only reads attributes already present
    on each registered Tool instance; it never calls `registry.execute()`
    or `registry.get()`."""
    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        before_count = len((await session.execute(select(AuditLog))).scalars().all())

    entries = list_tool_catalog(tool_registry)
    assert len(entries) > 0

    async with async_session_maker() as session:
        after_count = len((await session.execute(select(AuditLog))).scalars().all())

    assert after_count == before_count
