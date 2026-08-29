"""Phase 12: the Company-OS Knowledge Layer — real, DB-backed, tenant-scoped
markdown documents an Owner (and, where wired in, the AI) can read/edit."""

import uuid

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role
from app.services.knowledge_service import DEFAULT_FILES, KnowledgeService
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def test_set_and_get_a_file(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    await tool_registry.execute(
        "knowledge.set_file", {"path": "office/pricing-rules.md", "content": "# Pricing\n\n$150 base rate."}, ctx
    )
    result = await tool_registry.execute("knowledge.get_file", {"path": "office/pricing-rules.md"}, ctx)
    assert result.content == "# Pricing\n\n$150 base rate."


async def test_set_is_idempotent_update_not_duplicate(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    await tool_registry.execute("knowledge.set_file", {"path": "brand/voice-guide.md", "content": "v1"}, ctx)
    await tool_registry.execute("knowledge.set_file", {"path": "brand/voice-guide.md", "content": "v2"}, ctx)

    files = await tool_registry.execute("knowledge.list_files", {"prefix": "brand/"}, ctx)
    matching = [f for f in files.files if f.path == "brand/voice-guide.md"]
    assert len(matching) == 1
    assert matching[0].content == "v2"


async def test_list_files_filters_by_prefix(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    await tool_registry.execute("knowledge.set_file", {"path": "office/a.md", "content": "x"}, ctx)
    await tool_registry.execute("knowledge.set_file", {"path": "finance/b.md", "content": "y"}, ctx)

    result = await tool_registry.execute("knowledge.list_files", {"prefix": "office/"}, ctx)
    assert all(f.path.startswith("office/") for f in result.files)
    assert any(f.path == "office/a.md" for f in result.files)


async def test_tenant_isolation(tool_registry) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    ctx_a, ctx_b = _ctx(tenant_a), _ctx(tenant_b)

    await tool_registry.execute("knowledge.set_file", {"path": "office/secret.md", "content": "A's secret"}, ctx_a)

    with pytest.raises(ValueError):
        await tool_registry.execute("knowledge.get_file", {"path": "office/secret.md"}, ctx_b)

    files_b = await tool_registry.execute("knowledge.list_files", {}, ctx_b)
    assert not any(f.path == "office/secret.md" for f in files_b.files)


async def test_delete_file(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    await tool_registry.execute("knowledge.set_file", {"path": "office/temp.md", "content": "x"}, ctx)

    result = await tool_registry.execute("knowledge.delete_file", {"path": "office/temp.md"}, ctx)
    assert result.deleted is True

    with pytest.raises(ValueError):
        await tool_registry.execute("knowledge.get_file", {"path": "office/temp.md"}, ctx)


async def test_read_only_role_cannot_write(tool_registry) -> None:
    from app.tools.errors import ToolPermissionError

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id, role=Role.READ_ONLY)

    with pytest.raises(ToolPermissionError):
        await tool_registry.execute("knowledge.set_file", {"path": "office/x.md", "content": "y"}, ctx)

    # Read-only can still read.
    result = await tool_registry.execute("knowledge.list_files", {}, ctx)
    assert result.files == []


async def test_registration_seeds_default_knowledge_files(client) -> None:
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Knowledge Seed Co",
            "full_name": "Owner",
            "email": "owner@knowledgeseed.com",
            "password": "supersecret1",
        },
    )
    token = reg.json()["tokens"]["access_token"]
    resp = await client.get("/api/v1/knowledge/files", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    paths = {f["path"] for f in resp.json()["files"]}
    assert set(DEFAULT_FILES.keys()).issubset(paths)


async def test_api_set_and_get_round_trip(client, tool_registry) -> None:
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Knowledge API Co",
            "full_name": "Owner",
            "email": "owner@knowledgeapi.com",
            "password": "supersecret1",
        },
    )
    token = reg.json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.put(
        "/api/v1/knowledge/files/office/pricing-rules.md",
        json={"content": "Updated pricing content."},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == "Updated pricing content."

    get_resp = await client.get("/api/v1/knowledge/files/office/pricing-rules.md", headers=headers)
    assert get_resp.json()["content"] == "Updated pricing content."


async def test_get_content_helper_returns_none_when_unset(tool_registry) -> None:
    service = KnowledgeService(tool_registry._session_factory)
    tenant_id = uuid.uuid4()
    content = await service.get_content(tenant_id, "brand/voice-guide.md")
    assert content is None
