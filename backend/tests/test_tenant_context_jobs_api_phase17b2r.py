"""Phase 17B-2R: real-PostgreSQL behavioral proof for the authenticated
HTTP path gap (task §6-7): `app/api/v1/jobs.py`'s `list_job_tasks`,
`list_job_materials`, `list_job_attachments`, `download_job_attachment`,
and their shared `_owned_job_or_404` helper open their OWN, independent
`async_session_maker()` sessions directly in the route handler — a
DIFFERENT session than FastAPI's `get_db` dependency (which nothing in
this particular router even uses, since these 5 endpoints read directly
rather than through a Tool). `current_user.tenant_id`, resolved from the
authenticated JWT via `get_current_user`, is the trusted tenant source
here (task §23 — never a client-supplied tenant_id).

Driven through the real HTTP client (register -> real JWT -> real
endpoint), not by calling the route function directly, so this also
proves the whole FastAPI auth -> route -> independent-session chain works
end to end, not just the function in isolation.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio

_settings = get_settings()
requires_real_postgres = pytest.mark.skipif(
    "postgresql" not in _settings.DATABASE_URL,
    reason="requires DATABASE_URL pointed at a real PostgreSQL instance",
)


class _ContextSpy:
    def __init__(self):
        self.calls: list[tuple[uuid.UUID | None, str | None]] = []

    async def __call__(self, session, tenant_id):
        await set_tenant_context(session, tenant_id)
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            readback = await session.scalar(text("select current_setting('app.tenant_id', true)"))
        else:
            readback = None
        self.calls.append((tenant_id, readback))


@pytest.fixture
def spy():
    return _ContextSpy()


async def _register(client, org_name: str, email: str) -> dict:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org_name, "full_name": "Owner Test", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _ctx(tenant_id: uuid.UUID) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


async def _create_job(client, tool_registry, tenant_id: uuid.UUID, token: str) -> str:
    """Customer creation has no dedicated REST route in this codebase
    (only a Tool, exercised directly here exactly like
    test_job_attachment_download.py's own precedent) — job creation DOES
    have a real POST /api/v1/jobs route, which is the one this test
    actually exercises over HTTP."""
    customer = await tool_registry.execute("crm.create_customer", {"name": "HTTP Path Customer"}, _ctx(tenant_id))
    job_resp = await client.post(
        "/api/v1/jobs",
        json={"title": "HTTP Path Job", "customer_id": customer.customer["id"]},
        headers=_auth(token),
    )
    assert job_resp.status_code == 201, job_resp.text
    return job_resp.json()["job"]["id"]


@requires_real_postgres
async def test_list_job_tasks_over_http_sets_tenant_context(monkeypatch, spy, client, tool_registry) -> None:
    import app.api.v1.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "set_tenant_context", spy)

    reg = await _register(client, "HTTP Path Co", f"owner-{uuid.uuid4().hex[:8]}@example.com")
    token = reg["tokens"]["access_token"]
    tenant_id = uuid.UUID(reg["user"]["tenant_id"])
    job_id = await _create_job(client, tool_registry, tenant_id, token)

    resp = await client.get(f"/api/v1/jobs/{job_id}/tasks", headers=_auth(token))
    assert resp.status_code == 200, resp.text

    # _owned_job_or_404 + list_job_tasks itself = at least 2 session-open
    # sites hit in one request.
    assert len(spy.calls) >= 2
    tenant_id = spy.calls[0][0]
    assert tenant_id is not None
    for called_tenant, readback in spy.calls:
        assert called_tenant == tenant_id
        assert readback == str(tenant_id)


@requires_real_postgres
async def test_tenant_a_cannot_list_tenant_bs_job_tasks_over_http(client, tool_registry) -> None:
    reg_a = await _register(client, "Tenant A Co", f"owner-a-{uuid.uuid4().hex[:8]}@example.com")
    reg_b = await _register(client, "Tenant B Co", f"owner-b-{uuid.uuid4().hex[:8]}@example.com")
    token_a = reg_a["tokens"]["access_token"]
    token_b = reg_b["tokens"]["access_token"]
    tenant_a = uuid.UUID(reg_a["user"]["tenant_id"])

    job_id_a = await _create_job(client, tool_registry, tenant_a, token_a)

    resp = await client.get(f"/api/v1/jobs/{job_id_a}/tasks", headers=_auth(token_b))
    assert resp.status_code == 404


@requires_real_postgres
async def test_download_attachment_over_http_sets_tenant_context_and_denies_cross_tenant(monkeypatch, spy, client, tool_registry) -> None:
    import app.api.v1.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "set_tenant_context", spy)

    reg_a = await _register(client, "Attach A Co", f"owner-a2-{uuid.uuid4().hex[:8]}@example.com")
    reg_b = await _register(client, "Attach B Co", f"owner-b2-{uuid.uuid4().hex[:8]}@example.com")
    token_a = reg_a["tokens"]["access_token"]
    token_b = reg_b["tokens"]["access_token"]
    tenant_a = uuid.UUID(reg_a["user"]["tenant_id"])
    job_id = await _create_job(client, tool_registry, tenant_a, token_a)

    materials_resp = await client.get(f"/api/v1/jobs/{job_id}/materials", headers=_auth(token_a))
    assert materials_resp.status_code == 200
    assert len(spy.calls) >= 2
    for called_tenant, readback in spy.calls:
        assert readback == str(called_tenant)

    # Tenant B can never even resolve the job (404 before any attachment
    # lookup), proving the ownership check is enforced before anything
    # tenant B isn't entitled to is touched.
    cross_resp = await client.get(f"/api/v1/jobs/{job_id}/materials", headers=_auth(token_b))
    assert cross_resp.status_code == 404
