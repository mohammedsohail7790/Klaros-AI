"""Xero contact import and rotated-credential persistence: idempotency, matching, tenant isolation, consent gate, secrecy. httpx mocked; real DB session."""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.integrations import connectors_xero as cx
from app.integrations import xero_client as xc
from app.integrations.credential_store import decrypt_credential
from app.models.crm import Customer
from app.models.organization import Organization
from app.services.integration_connection_service import IntegrationConnectionService
from app.services.xero_import_service import XeroImportService, XeroNotConnectedError

pytestmark = pytest.mark.asyncio

XT = "xero-tenant-A"
_REAL = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _platform(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "XERO_CLIENT_ID", "xero-client-id")
    monkeypatch.setattr(s, "XERO_CLIENT_SECRET", "xero-client-secret")

    async def _nosleep(_):
        return None

    monkeypatch.setattr(xc.asyncio, "sleep", _nosleep)


@pytest.fixture
def conn_service():
    service = IntegrationConnectionService(async_session_maker)
    service.register_verifier("xero", cx._verify)
    return service


def mock_http(monkeypatch, handler):
    monkeypatch.setattr(xc.httpx, "AsyncClient", lambda *a, **k: _REAL(*a, **{**k, "transport": httpx.MockTransport(handler)}))


async def _org() -> uuid.UUID:
    tid = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(Organization(id=tid, name=f"org-{tid}", slug=f"org-{tid}"))
        await session.commit()
    return tid


def _iso(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


async def _connect(service, tid, *, expires_in_minutes=20, access="AT-old", refresh="RT-old"):
    cred = {"access_token": access, "refresh_token": refresh, "expires_at": _iso(expires_in_minutes), "xero_tenant_id": XT, "tenant_name": "Demo"}

    def handler(request):
        return httpx.Response(200, json={"Organisations": [{"Name": "Demo"}]})

    return cred, handler


async def _stored(service, tid) -> dict:
    conn = await service.get_connection(tid, "xero")
    return decrypt_credential(conn.encrypted_credential)


CONTACTS = [
    {"ContactID": "x1", "Name": "Ada Lovelace", "EmailAddress": "Ada@Example.com", "ContactStatus": "ACTIVE", "IsCustomer": True, "Phones": [{"PhoneType": "DEFAULT", "PhoneNumber": "5550101", "PhoneAreaCode": "415", "PhoneCountryCode": "1"}]},
    {"ContactID": "x2", "Name": "Bob Builder", "EmailAddress": "bob@example.com", "ContactStatus": "ACTIVE"},
    {"ContactID": "x3", "Name": "Supplier Only Ltd", "ContactStatus": "ACTIVE", "IsSupplier": True},
    {"ContactID": "x4", "Name": "", "ContactStatus": "ACTIVE"},
]


def api_handler(calls=None, *, contacts=CONTACTS, token_body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if request.url.host == "identity.xero.com":
            return httpx.Response(200, json=token_body or {"access_token": "AT-new", "refresh_token": "RT-new", "expires_in": 1800})
        if request.url.path.endswith("/Contacts"):
            assert request.headers["xero-tenant-id"] == XT
            page = int(request.url.params["page"])
            return httpx.Response(200, json={"Contacts": contacts if page == 1 else []})
        return httpx.Response(200, json={"Organisations": [{"Name": "Demo"}]})

    return handler


async def _connect_ok(service, tid, monkeypatch, **kw):
    cred, _ = await _connect(service, tid, **kw)
    mock_http(monkeypatch, api_handler())
    conn = await service.connect(tid, "xero", cred, created_by=None, external_account_id=XT, scopes=xc.SCOPES)
    assert str(conn.status).lower() == "connected"


async def _customers(tid) -> list[Customer]:
    async with async_session_maker() as session:
        await set_tenant_context(session, tid)
        return list((await session.execute(select(Customer).where(Customer.tenant_id == tid))).scalars())


async def test_import_creates_matches_skips_and_is_idempotent(conn_service, monkeypatch) -> None:
    tid = await _org()
    await _connect_ok(conn_service, tid, monkeypatch)
    async with async_session_maker() as session:
        await set_tenant_context(session, tid)
        session.add(Customer(tenant_id=tid, name="Robert B", email="bob@example.com"))
        await session.commit()
    mock_http(monkeypatch, api_handler())
    svc = XeroImportService(async_session_maker, conn_service)

    first = await svc.import_contacts(tid)
    assert (first.contacts_seen, first.created, first.matched, first.already_linked, first.skipped) == (4, 1, 1, 0, 2)
    rows = {c.external_id or c.name: c for c in await _customers(tid)}
    assert len(rows) == 2 and rows["x1"].email == "ada@example.com" and rows["x1"].phone == "+14155550101" and rows["x1"].phone_normalized == "4155550101"
    assert rows["x2"].name == "Robert B"  # the existing customer was linked, not duplicated or renamed

    second = await svc.import_contacts(tid)
    assert (second.created, second.matched, second.already_linked, second.skipped) == (0, 0, 2, 2)
    assert len(await _customers(tid)) == 2


async def test_import_is_tenant_isolated(conn_service, monkeypatch) -> None:
    a, b = await _org(), await _org()
    await _connect_ok(conn_service, a, monkeypatch)
    mock_http(monkeypatch, api_handler())
    svc = XeroImportService(async_session_maker, conn_service)
    await svc.import_contacts(a)
    assert len(await _customers(a)) == 2 and await _customers(b) == []
    with pytest.raises(XeroNotConnectedError):  # tenant B has no connection and cannot use A's
        await svc.import_contacts(b)
    # the same Xero ContactID in another tenant is a separate customer
    await _connect_ok(conn_service, b, monkeypatch)
    mock_http(monkeypatch, api_handler())
    await svc.import_contacts(b)
    assert len(await _customers(b)) == 2 and len(await _customers(a)) == 2


async def test_import_refreshes_and_persists_rotated_tokens_before_listing(conn_service, monkeypatch) -> None:
    tid = await _org()
    await _connect_ok(conn_service, tid, monkeypatch)
    stored = await _stored(conn_service, tid)
    assert stored["refresh_token"] == "RT-old"
    stored["expires_at"] = _iso(-10)
    await conn_service.update_credential(tid, "xero", stored)
    calls: list[httpx.Request] = []
    order: list[str] = []
    real_update = conn_service.update_credential

    async def spy_update(*a, **k):
        order.append("persist")
        return await real_update(*a, **k)

    monkeypatch.setattr(conn_service, "update_credential", spy_update)

    def handler(request):
        if request.url.path.endswith("/Contacts"):
            order.append("list")
        return api_handler(calls)(request)

    mock_http(monkeypatch, handler)
    svc = XeroImportService(async_session_maker, conn_service)
    await svc.import_contacts(tid)
    assert order[:2] == ["persist", "list"]  # the rotated refresh token is stored before any other Xero call
    stored = await _stored(conn_service, tid)
    assert stored["refresh_token"] == "RT-new" and stored["access_token"] == "AT-new" and not xc.needs_refresh(stored["expires_at"])
    assert stored["xero_tenant_id"] == XT and stored["tenant_name"] == "Demo"
    refreshes = [r for r in calls if r.url.host == "identity.xero.com"]
    assert len(refreshes) == 1 and b"RT-old" in refreshes[0].content
    await svc.import_contacts(tid)  # token now fresh: no second refresh
    assert len([r for r in calls if r.url.host == "identity.xero.com"]) == 1


async def test_verify_via_connection_service_persists_rotation(conn_service, monkeypatch) -> None:
    tid = await _org()
    await _connect_ok(conn_service, tid, monkeypatch, expires_in_minutes=-10)  # connect() itself verified and rotated
    stored = await _stored(conn_service, tid)
    assert stored["refresh_token"] == "RT-new"
    # a second verify with a fresh token must not rotate again
    calls: list[httpx.Request] = []
    mock_http(monkeypatch, api_handler(calls))
    conn = await conn_service.verify(tid, "xero")
    assert str(conn.status).lower() == "connected" and not any(r.url.host == "identity.xero.com" for r in calls)
    assert (await _stored(conn_service, tid))["refresh_token"] == "RT-new"


async def test_rotation_is_persisted_even_when_the_following_call_fails(conn_service, monkeypatch) -> None:
    tid = await _org()
    await _connect_ok(conn_service, tid, monkeypatch)
    # make the stored token look expired by re-saving it, then fail the Organisation call after a successful refresh
    stored = await _stored(conn_service, tid)
    stored["expires_at"] = _iso(-10)
    await conn_service.update_credential(tid, "xero", stored)

    def handler(request):
        if request.url.host == "identity.xero.com":
            return httpx.Response(200, json={"access_token": "AT-new", "refresh_token": "RT-new", "expires_in": 1800})
        return httpx.Response(500, json={"Detail": "boom"})

    mock_http(monkeypatch, handler)
    conn = await conn_service.verify(tid, "xero")
    assert str(conn.status).lower() == "error"
    assert (await _stored(conn_service, tid))["refresh_token"] == "RT-new"  # the spent old token is not what remains


async def test_import_revoked_login_is_honest(conn_service, monkeypatch) -> None:
    tid = await _org()
    await _connect_ok(conn_service, tid, monkeypatch, expires_in_minutes=-10)
    stored = await _stored(conn_service, tid)
    stored["expires_at"] = _iso(-10)
    await conn_service.update_credential(tid, "xero", stored)
    mock_http(monkeypatch, lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(XeroNotConnectedError) as exc:
        await XeroImportService(async_session_maker, conn_service).import_contacts(tid)
    assert "RT-" not in str(exc.value) and "reconnect" in str(exc.value).lower()


async def test_import_requires_connection(conn_service) -> None:
    tid = await _org()
    with pytest.raises(XeroNotConnectedError):
        await XeroImportService(async_session_maker, conn_service).import_contacts(tid)


async def test_consent_gated_tenant_cannot_import_new_customers(conn_service, monkeypatch) -> None:
    from app.services import consent_gate

    tid = await _org()
    await _connect_ok(conn_service, tid, monkeypatch)
    mock_http(monkeypatch, api_handler())

    async def gated(session_factory, tenant_id):
        return True

    monkeypatch.setattr(consent_gate, "tenant_requires_consent", gated)
    with pytest.raises(consent_gate.ConsentRequiredError):
        await XeroImportService(async_session_maker, conn_service).import_contacts(tid)
    assert await _customers(tid) == []


async def test_tool_policy_and_registration() -> None:
    from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy

    assert DEFAULT_TOOL_POLICIES["finance.import_xero_contacts"] == ActionPolicy.APPROVAL_REQUIRED
