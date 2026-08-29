"""Phase 12 production hardening: closes the P2-3 finding from the Phase 11
audit — attachments could be uploaded and listed as metadata since Phase 4,
but there was no route to ever retrieve the actual file bytes back."""

import base64
import uuid

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id, role=Role.OWNER):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def _register(client, email="owner@attachdl.com", org="Attach DL Co"):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org, "full_name": "Owner", "email": email, "password": "supersecret1"},
    )
    return resp.json()


async def test_download_returns_the_real_uploaded_bytes(client, tool_registry) -> None:
    reg = await _register(client)
    tenant_id = uuid.UUID(reg["user"]["tenant_id"])
    token = reg["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Attach Co"}, ctx)
    job = await tool_registry.execute(
        "operations.create_job",
        {"title": "Attachment Job", "customer_id": customer.customer["id"], "estimated_revenue": 100.0},
        ctx,
    )
    job_id = job.job["id"]

    content = b"\xff\xd8\xff fake jpeg bytes for download test"
    result = await tool_registry.execute(
        "operations.add_job_photo",
        {
            "job_id": job_id,
            "filename": "before.jpg",
            "content_type": "image/jpeg",
            "content_base64": base64.b64encode(content).decode(),
        },
        ctx,
    )
    attachment_id = result.attachment["id"]

    resp = await client.get(f"/api/v1/jobs/{job_id}/attachments/{attachment_id}/download", headers=headers)
    assert resp.status_code == 200
    assert resp.content == content
    assert resp.headers["content-type"] == "image/jpeg"


async def test_download_is_tenant_isolated(client, tool_registry) -> None:
    reg_a = await _register(client, "ownera@attachdl2.com", "Attach DL Tenant A")
    reg_b = await _register(client, "ownerb@attachdl2.com", "Attach DL Tenant B")
    tenant_a = uuid.UUID(reg_a["user"]["tenant_id"])
    token_b = reg_b["tokens"]["access_token"]
    ctx_a = _ctx(tenant_a)

    customer = await tool_registry.execute("crm.create_customer", {"name": "A Co"}, ctx_a)
    job = await tool_registry.execute(
        "operations.create_job",
        {"title": "A Job", "customer_id": customer.customer["id"], "estimated_revenue": 50.0},
        ctx_a,
    )
    job_id = job.job["id"]
    result = await tool_registry.execute(
        "operations.add_job_photo",
        {"job_id": job_id, "filename": "a.jpg", "content_type": "image/jpeg", "content_base64": base64.b64encode(b"secret").decode()},
        ctx_a,
    )
    attachment_id = result.attachment["id"]

    resp = await client.get(
        f"/api/v1/jobs/{job_id}/attachments/{attachment_id}/download",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 404


async def test_download_nonexistent_attachment_404s(client, tool_registry) -> None:
    reg = await _register(client, "owner3@attachdl3.com", "Attach DL Co 3")
    tenant_id = uuid.UUID(reg["user"]["tenant_id"])
    token = reg["tokens"]["access_token"]
    ctx = _ctx(tenant_id)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Co 3"}, ctx)
    job = await tool_registry.execute(
        "operations.create_job",
        {"title": "Job 3", "customer_id": customer.customer["id"], "estimated_revenue": 50.0},
        ctx,
    )
    job_id = job.job["id"]

    resp = await client.get(
        f"/api/v1/jobs/{job_id}/attachments/{uuid.uuid4()}/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
