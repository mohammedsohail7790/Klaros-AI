"""Phase 13: QuickBooks Online OAuth2 connect flow + invoice sync — fully
self-contained (no real Intuit credentials needed). Mocks `httpx` at the
transport level for the client's own retry/error-classification behavior
(matching `test_stripe_client.py`'s pattern), and mocks
`QuickBooksClient` methods directly for the higher-level OAuth
router/sync-service tests (matching `test_stripe_tenant_connection.py`'s
pattern).
"""

import uuid
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.core.config import get_settings
from app.core.security import TokenError, create_oauth_state_token, decode_oauth_state_token
from app.integrations.quickbooks_client import (
    QuickBooksAPIError,
    QuickBooksClient,
    QuickBooksErrorType,
    get_authorization_url,
)
from app.models.actor import ActorType
from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceLineItem, InvoiceStatus
from app.models.rbac import Role
from app.services.integration_connection_service import IntegrationConnectionService
from app.services.quickbooks_sync_service import (
    InvoiceNotSyncableError,
    QuickBooksNotConnectedError,
    QuickBooksSyncService,
)
from app.tools.base import ExecutionContext


def _ctx(tenant_id: uuid.UUID) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


# --- 1. Authorization URL construction (deterministic, no network). ---


def test_authorization_url_contains_client_id_redirect_and_state() -> None:
    url = get_authorization_url(client_id="fake_client_id", redirect_uri="https://api.example.com/cb", state="the-state-token")
    assert "client_id=fake_client_id" in url
    assert "state=the-state-token" in url
    assert "appcenter.intuit.com" in url
    assert "response_type=code" in url


# --- 2. OAuth state token: signed, tenant-bound, provider-bound, expiring. ---


def test_oauth_state_token_round_trips_tenant_and_provider() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    token = create_oauth_state_token(tenant_id, "quickbooks", user_id)
    payload = decode_oauth_state_token(token, expected_provider="quickbooks")
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["sub"] == str(user_id)


def test_oauth_state_token_rejects_wrong_provider() -> None:
    token = create_oauth_state_token(uuid.uuid4(), "quickbooks", uuid.uuid4())
    with pytest.raises(TokenError):
        decode_oauth_state_token(token, expected_provider="stripe")


def test_oauth_state_token_rejects_garbage() -> None:
    with pytest.raises(TokenError):
        decode_oauth_state_token("not-a-real-jwt", expected_provider="quickbooks")


def test_oauth_state_token_rejects_expired() -> None:
    """The 10-minute expiry is enforced by the same `python-jose`
    `jwt.decode` mechanism already relied on for access/refresh tokens
    (`app/core/security.py`) — this constructs an already-expired state
    token directly (bypassing `create_oauth_state_token`'s real 10-minute
    window) to prove expiry is genuinely enforced, not merely intended."""
    from datetime import datetime, timedelta, timezone

    from jose import jwt as jose_jwt

    settings = get_settings()
    payload = {
        "tenant_id": str(uuid.uuid4()), "provider": "quickbooks", "sub": str(uuid.uuid4()),
        "type": "oauth_state", "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
    }
    expired_token = jose_jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    with pytest.raises(TokenError):
        decode_oauth_state_token(expired_token, expected_provider="quickbooks")


# --- 3. QuickBooksClient retry/backoff/error-classification (real httpx
# MockTransport, mirroring test_stripe_client.py). ---


def _make_transport(responses: list[httpx.Response]) -> httpx.MockTransport:
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        idx = min(state["calls"], len(responses) - 1)
        state["calls"] += 1
        return responses[idx]

    transport = httpx.MockTransport(handler)
    transport.call_count = lambda: state["calls"]  # type: ignore[attr-defined]
    return transport


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    import app.integrations.quickbooks_client as mod

    async def _noop_sleep(_seconds):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _noop_sleep)
    yield


@pytest.fixture(autouse=True)
def _configure_quickbooks_app(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "QUICKBOOKS_CLIENT_ID", "fake_platform_client_id")
    monkeypatch.setattr(settings, "QUICKBOOKS_CLIENT_SECRET", "fake_platform_client_secret")
    monkeypatch.setattr(settings, "QUICKBOOKS_REDIRECT_URI", "https://api.example.com/api/v1/integrations/quickbooks/callback")
    yield


async def _patched_client(monkeypatch, transport: httpx.MockTransport, *, max_retries: int = 3) -> QuickBooksClient:
    import app.integrations.quickbooks_client as mod

    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(mod.httpx, "AsyncClient", _patched)
    settings = get_settings()
    monkeypatch.setattr(settings, "QUICKBOOKS_MAX_RETRIES", max_retries)
    return QuickBooksClient()


async def test_token_exchange_success(monkeypatch) -> None:
    request = httpx.Request("POST", "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer")
    responses = [httpx.Response(200, request=request, json={
        "access_token": "at_123", "refresh_token": "rt_456", "token_type": "bearer", "expires_in": 3600,
    })]
    client = await _patched_client(monkeypatch, _make_transport(responses))
    result = await client.exchange_code_for_tokens(code="the-code", redirect_uri="https://x/cb")
    assert result.access_token == "at_123"
    assert result.refresh_token == "rt_456"


async def test_token_exchange_without_platform_app_configured_raises_cleanly(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "QUICKBOOKS_CLIENT_ID", None)
    client = QuickBooksClient()
    with pytest.raises(QuickBooksAPIError, match="not configured"):
        await client.exchange_code_for_tokens(code="x", redirect_uri="https://x/cb")


async def test_401_on_companyinfo_is_classified_authentication_and_never_retried(monkeypatch) -> None:
    request = httpx.Request("GET", "https://sandbox-quickbooks.api.intuit.com/v3/company/123/companyinfo/123")
    responses = [httpx.Response(401, request=request, json={"Fault": {"Error": [{"Message": "Token expired"}]}})]
    transport = _make_transport(responses)
    client = await _patched_client(monkeypatch, transport)
    with pytest.raises(QuickBooksAPIError) as exc_info:
        await client.get_company_info(access_token="expired", realm_id="123")
    assert exc_info.value.error_type == QuickBooksErrorType.AUTHENTICATION
    assert transport.call_count() == 1


async def test_500_is_retried_and_eventually_succeeds(monkeypatch) -> None:
    request = httpx.Request("GET", "https://sandbox-quickbooks.api.intuit.com/v3/company/123/companyinfo/123")
    responses = [
        httpx.Response(500, request=request, json={"Fault": {"Error": [{"Message": "server error"}]}}),
        httpx.Response(200, request=request, json={"CompanyInfo": {"CompanyName": "Acme Co", "Id": "123"}}),
    ]
    transport = _make_transport(responses)
    client = await _patched_client(monkeypatch, transport, max_retries=3)
    info = await client.get_company_info(access_token="ok", realm_id="123")
    assert info.CompanyName == "Acme Co"
    assert transport.call_count() == 2


async def test_key_never_appears_in_client_secret_error_message(monkeypatch) -> None:
    """The client only ever puts the platform client_secret into the
    Basic-auth header, base64-encoded, never into a raised exception's
    message — proven the same way as Stripe's equivalent test."""
    request = httpx.Request("POST", "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer")
    responses = [httpx.Response(400, request=request, json={"error": "invalid_grant", "error_description": "code expired"})]
    transport = _make_transport(responses)
    client = await _patched_client(monkeypatch, transport)
    with pytest.raises(QuickBooksAPIError) as exc_info:
        await client.exchange_code_for_tokens(code="expired-code", redirect_uri="https://x/cb")
    assert "fake_platform_client_secret" not in str(exc_info.value)


# --- 4. OAuth callback endpoint (full HTTP-level flow). ---


async def _register_and_get_token(client, org_name: str, email: str) -> tuple[str, uuid.UUID]:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org_name, "full_name": "Owner Test", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["tokens"]["access_token"], uuid.UUID(body["user"]["tenant_id"])


async def test_authorize_endpoint_requires_platform_app_configured(client, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "QUICKBOOKS_CLIENT_ID", None)
    token, _tenant_id = await _register_and_get_token(client, "QB Authorize Co", "owner@qbauth.com")
    resp = await client.get("/api/v1/integrations/quickbooks/authorize", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 503


async def test_authorize_endpoint_returns_real_intuit_url_with_signed_state(client) -> None:
    token, _tenant_id = await _register_and_get_token(client, "QB Authorize Co 2", "owner@qbauth2.com")
    resp = await client.get("/api/v1/integrations/quickbooks/authorize", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    url = resp.json()["authorization_url"]
    assert "appcenter.intuit.com" in url
    assert "state=" in url


async def test_callback_with_intuit_error_param_redirects_with_error(client) -> None:
    resp = await client.get(
        "/api/v1/integrations/quickbooks/callback", params={"error": "access_denied"}, follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "quickbooks=error" in resp.headers["location"]


async def test_callback_missing_required_params_is_rejected(client) -> None:
    resp = await client.get("/api/v1/integrations/quickbooks/callback", params={"code": "x"})
    assert resp.status_code == 400


async def test_callback_with_invalid_state_is_rejected(client) -> None:
    resp = await client.get(
        "/api/v1/integrations/quickbooks/callback",
        params={"code": "x", "state": "not-a-real-state-token", "realmId": "123"},
    )
    assert resp.status_code == 400


async def test_callback_with_state_for_wrong_provider_is_rejected(client) -> None:
    forged_state = create_oauth_state_token(uuid.uuid4(), "stripe", uuid.uuid4())
    resp = await client.get(
        "/api/v1/integrations/quickbooks/callback",
        params={"code": "x", "state": forged_state, "realmId": "123"},
    )
    assert resp.status_code == 400


async def test_callback_success_stores_a_real_connection_for_the_correct_tenant(client, monkeypatch) -> None:
    token, tenant_id = await _register_and_get_token(client, "QB Callback Co", "owner@qbcallback.com")

    # A real HTTP call would go out for the token exchange — mock the
    # client method directly (same technique as Stripe's tenant-connection
    # tests), not the transport, since this test is about the callback's
    # own orchestration, not the client's HTTP behavior (covered above).
    async def _fake_exchange(self, *, code, redirect_uri):
        from app.integrations.quickbooks_schemas import QuickBooksTokenResponse

        return QuickBooksTokenResponse(access_token="at_real", refresh_token="rt_real", expires_in=3600)

    monkeypatch.setattr(QuickBooksClient, "exchange_code_for_tokens", _fake_exchange)

    user_id_resp = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    user_id = uuid.UUID(user_id_resp.json()["id"])

    state = create_oauth_state_token(tenant_id, "quickbooks", user_id)
    resp = await client.get(
        "/api/v1/integrations/quickbooks/callback",
        params={"code": "real-code", "state": state, "realmId": "realm-999"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "quickbooks=connected" in resp.headers["location"]

    conns = await client.get(
        "/api/v1/integrations/connections", headers={"Authorization": f"Bearer {token}"}
    )
    quickbooks_conn = next(c for c in conns.json() if c["provider"] == "quickbooks")
    assert quickbooks_conn["external_account_id"] == "realm-999"
    # Never echoes the token itself back.
    assert "at_real" not in str(conns.json())
    assert "access_token" not in quickbooks_conn


async def test_callback_token_exchange_failure_redirects_with_error(client, monkeypatch) -> None:
    token, tenant_id = await _register_and_get_token(client, "QB Callback Fail Co", "owner@qbcallbackfail.com")

    async def _fake_exchange_fails(self, *, code, redirect_uri):
        raise QuickBooksAPIError("invalid_grant: code expired", error_type=QuickBooksErrorType.INVALID_REQUEST)

    monkeypatch.setattr(QuickBooksClient, "exchange_code_for_tokens", _fake_exchange_fails)

    user_id_resp = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    user_id = uuid.UUID(user_id_resp.json()["id"])
    state = create_oauth_state_token(tenant_id, "quickbooks", user_id)

    resp = await client.get(
        "/api/v1/integrations/quickbooks/callback",
        params={"code": "expired-code", "state": state, "realmId": "realm-1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "quickbooks=error" in resp.headers["location"]


# --- 5. QuickBooks verifier (registered in app/api/tool_deps_integrations.py). ---


async def test_quickbooks_verifier_succeeds_with_real_company_info_call(monkeypatch) -> None:
    from app.api.tool_deps_integrations import _quickbooks_verifier
    from app.integrations.quickbooks_schemas import QuickBooksCompanyInfo

    async def _fake_get_company_info(self, *, access_token, realm_id):
        return QuickBooksCompanyInfo(CompanyName="Acme Co", Id=realm_id)

    monkeypatch.setattr(QuickBooksClient, "get_company_info", _fake_get_company_info)
    ok, detail = await _quickbooks_verifier({"access_token": "at_1", "realm_id": "realm-1"})
    assert ok is True
    assert "Acme Co" in detail


async def test_quickbooks_verifier_fails_honestly_with_missing_fields() -> None:
    from app.api.tool_deps_integrations import _quickbooks_verifier

    ok, detail = await _quickbooks_verifier({"access_token": "at_1"})  # no realm_id
    assert ok is False
    assert "realm_id" in detail


async def test_quickbooks_verifier_fails_on_real_rejection(monkeypatch) -> None:
    from app.api.tool_deps_integrations import _quickbooks_verifier

    async def _fake_rejects(self, *, access_token, realm_id):
        raise QuickBooksAPIError("Token expired", error_type=QuickBooksErrorType.AUTHENTICATION)

    monkeypatch.setattr(QuickBooksClient, "get_company_info", _fake_rejects)
    ok, detail = await _quickbooks_verifier({"access_token": "bad", "realm_id": "realm-1"})
    assert ok is False
    assert "rejected" in detail.lower()


# --- 6. QuickBooksSyncService: connection state, invoice gating,
# idempotency, customer reuse, token-refresh-on-401, tenant isolation. ---


@pytest.fixture
def connection_service() -> IntegrationConnectionService:
    from app.api.tool_deps_integrations import get_integration_connection_service

    return get_integration_connection_service()


async def _make_customer_and_invoice(
    tenant_id: uuid.UUID, *, status: str = InvoiceStatus.APPROVED, already_synced: bool = False,
) -> tuple[Customer, Invoice]:
    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        customer = Customer(tenant_id=tenant_id, name="QB Sync Test Customer", email="c@example.com")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, customer_id=customer.id, invoice_number=f"QB-{uuid.uuid4().hex[:8]}",
            status=status, issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=Decimal("150.00"), total=Decimal("150.00"), amount_due=Decimal("150.00"),
            external_provider="quickbooks" if already_synced else None,
            external_id="already-synced-qb-id" if already_synced else None,
        )
        session.add(invoice)
        await session.flush()
        session.add(InvoiceLineItem(
            tenant_id=tenant_id, invoice_id=invoice.id, description="Service call",
            quantity=Decimal("1"), unit_price=Decimal("150.00"), line_total=Decimal("150.00"),
        ))
        await session.commit()
        await session.refresh(customer)
        await session.refresh(invoice)
    return customer, invoice


async def _connect_quickbooks(connection_service, tenant_id: uuid.UUID, monkeypatch) -> None:
    from app.integrations.quickbooks_schemas import QuickBooksCompanyInfo

    async def _fake_get_company_info(self, *, access_token, realm_id):
        return QuickBooksCompanyInfo(CompanyName="Acme Co", Id=realm_id)

    monkeypatch.setattr(QuickBooksClient, "get_company_info", _fake_get_company_info)
    await connection_service.connect(
        tenant_id, "quickbooks", {"access_token": "at_valid", "refresh_token": "rt_valid", "realm_id": "realm-1"},
        created_by=None, external_account_id="realm-1", scopes="com.intuit.quickbooks.accounting",
    )


def _sync_service(connection_service) -> QuickBooksSyncService:
    from app.db.session import async_session_maker

    return QuickBooksSyncService(async_session_maker, connection_service)


async def test_sync_without_connection_raises_not_connected(connection_service) -> None:
    tenant_id = uuid.uuid4()
    _customer, invoice = await _make_customer_and_invoice(tenant_id)
    service = _sync_service(connection_service)
    with pytest.raises(QuickBooksNotConnectedError):
        await service.sync_invoice(tenant_id, invoice.id)


async def test_sync_draft_invoice_is_rejected(connection_service, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    _customer, invoice = await _make_customer_and_invoice(tenant_id, status=InvoiceStatus.DRAFT)
    service = _sync_service(connection_service)
    with pytest.raises(InvoiceNotSyncableError, match="DRAFT"):
        await service.sync_invoice(tenant_id, invoice.id)


async def test_already_synced_invoice_is_a_safe_noop_no_api_call(connection_service, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    _customer, invoice = await _make_customer_and_invoice(tenant_id, already_synced=True)

    async def _fail_if_called(self, **kwargs):
        raise AssertionError("must not call create_invoice for an already-synced invoice")

    monkeypatch.setattr(QuickBooksClient, "create_invoice", _fail_if_called)
    monkeypatch.setattr(QuickBooksClient, "create_customer", _fail_if_called)

    service = _sync_service(connection_service)
    result = await service.sync_invoice(tenant_id, invoice.id)
    assert result.already_synced is True
    assert result.quickbooks_invoice_id == "already-synced-qb-id"


async def test_full_sync_creates_customer_and_invoice_and_persists_external_ids(connection_service, monkeypatch) -> None:
    from app.integrations.quickbooks_schemas import QuickBooksCustomerResponse, QuickBooksInvoiceResponse

    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    _customer, invoice = await _make_customer_and_invoice(tenant_id)

    async def _fake_create_customer(self, *, access_token, realm_id, display_name, email, phone):
        return QuickBooksCustomerResponse(Id="qb-cust-1", DisplayName=display_name)

    async def _fake_create_invoice(self, *, access_token, realm_id, customer_id, doc_number, lines):
        assert customer_id == "qb-cust-1"
        return QuickBooksInvoiceResponse(Id="qb-inv-1", DocNumber=doc_number)

    monkeypatch.setattr(QuickBooksClient, "create_customer", _fake_create_customer)
    monkeypatch.setattr(QuickBooksClient, "create_invoice", _fake_create_invoice)

    service = _sync_service(connection_service)
    result = await service.sync_invoice(tenant_id, invoice.id)
    assert result.already_synced is False
    assert result.quickbooks_invoice_id == "qb-inv-1"
    assert result.quickbooks_customer_id == "qb-cust-1"

    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        refreshed_invoice = await session.get(Invoice, invoice.id)
        refreshed_customer = await session.get(Customer, _customer.id)
        assert refreshed_invoice.external_provider == "quickbooks"
        assert refreshed_invoice.external_id == "qb-inv-1"
        assert refreshed_customer.external_provider == "quickbooks"
        assert refreshed_customer.external_id == "qb-cust-1"


async def test_second_invoice_for_same_customer_reuses_qb_customer(connection_service, monkeypatch) -> None:
    """A customer is created in QuickBooks at most once — a second
    invoice for the same Klaros customer must reuse the stored mapping,
    never call create_customer again."""
    from app.integrations.quickbooks_schemas import QuickBooksCustomerResponse, QuickBooksInvoiceResponse

    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    customer, invoice_1 = await _make_customer_and_invoice(tenant_id)

    from app.db.session import async_session_maker
    async with async_session_maker() as session:
        invoice_2 = Invoice(
            tenant_id=tenant_id, customer_id=customer.id, invoice_number=f"QB-{uuid.uuid4().hex[:8]}",
            status=InvoiceStatus.APPROVED, issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
            subtotal=Decimal("50.00"), total=Decimal("50.00"), amount_due=Decimal("50.00"),
        )
        session.add(invoice_2)
        await session.commit()
        await session.refresh(invoice_2)

    create_customer_calls = {"n": 0}

    async def _fake_create_customer(self, *, access_token, realm_id, display_name, email, phone):
        create_customer_calls["n"] += 1
        return QuickBooksCustomerResponse(Id="qb-cust-shared", DisplayName=display_name)

    async def _fake_create_invoice(self, *, access_token, realm_id, customer_id, doc_number, lines):
        return QuickBooksInvoiceResponse(Id=f"qb-inv-{doc_number}", DocNumber=doc_number)

    monkeypatch.setattr(QuickBooksClient, "create_customer", _fake_create_customer)
    monkeypatch.setattr(QuickBooksClient, "create_invoice", _fake_create_invoice)

    service = _sync_service(connection_service)
    await service.sync_invoice(tenant_id, invoice_1.id)
    await service.sync_invoice(tenant_id, invoice_2.id)

    assert create_customer_calls["n"] == 1


async def test_401_during_create_invoice_refreshes_token_once_and_retries(connection_service, monkeypatch) -> None:
    from app.integrations.quickbooks_schemas import (
        QuickBooksCustomerResponse,
        QuickBooksInvoiceResponse,
        QuickBooksTokenResponse,
    )

    tenant_id = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)
    _customer, invoice = await _make_customer_and_invoice(tenant_id)

    async def _fake_create_customer(self, *, access_token, realm_id, display_name, email, phone):
        return QuickBooksCustomerResponse(Id="qb-cust-1", DisplayName=display_name)

    monkeypatch.setattr(QuickBooksClient, "create_customer", _fake_create_customer)

    calls = {"n": 0}

    async def _fake_create_invoice(self, *, access_token, realm_id, customer_id, doc_number, lines):
        calls["n"] += 1
        if calls["n"] == 1:
            raise QuickBooksAPIError("Token expired", error_type=QuickBooksErrorType.AUTHENTICATION)
        assert access_token == "at_refreshed"
        return QuickBooksInvoiceResponse(Id="qb-inv-refreshed", DocNumber=doc_number)

    async def _fake_refresh(self, *, refresh_token):
        return QuickBooksTokenResponse(access_token="at_refreshed", refresh_token="rt_refreshed", expires_in=3600)

    monkeypatch.setattr(QuickBooksClient, "create_invoice", _fake_create_invoice)
    monkeypatch.setattr(QuickBooksClient, "refresh_access_token", _fake_refresh)

    service = _sync_service(connection_service)
    result = await service.sync_invoice(tenant_id, invoice.id)
    assert result.quickbooks_invoice_id == "qb-inv-refreshed"
    assert calls["n"] == 2


async def test_tenant_b_cannot_sync_tenant_as_invoice(connection_service, monkeypatch) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_b, monkeypatch)
    _customer, invoice_a = await _make_customer_and_invoice(tenant_a)

    service = _sync_service(connection_service)
    # Tenant B has a real QuickBooks connection but tenant A's invoice —
    # must fail as "not found", never sync tenant A's data using tenant
    # B's QuickBooks company.
    with pytest.raises(InvoiceNotSyncableError, match="not found"):
        await service.sync_invoice(tenant_b, invoice_a.id)


async def test_tenant_bs_connection_is_never_used_for_tenant_a(connection_service, monkeypatch) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await _connect_quickbooks(connection_service, tenant_b, monkeypatch)
    # Tenant A has no connection of its own.
    _customer, invoice_a = await _make_customer_and_invoice(tenant_a)

    service = _sync_service(connection_service)
    with pytest.raises(QuickBooksNotConnectedError):
        await service.sync_invoice(tenant_a, invoice_a.id)


# --- 7. Tool-layer wiring (ToolRegistry -> permission -> execution). ---


async def test_sync_tool_is_registered_and_reachable_through_tool_registry(tool_registry, monkeypatch, event_bus) -> None:
    from app.integrations.quickbooks_schemas import QuickBooksCustomerResponse, QuickBooksInvoiceResponse

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)

    from app.api.tool_deps_integrations import get_integration_connection_service

    connection_service = get_integration_connection_service()
    await _connect_quickbooks(connection_service, tenant_id, monkeypatch)

    customer = await tool_registry.execute("crm.create_customer", {"name": "Tool Registry QB Customer"}, ctx)
    invoice = await tool_registry.execute(
        "finance.create_invoice_draft",
        {"customer_id": customer.customer["id"], "line_items": [{"description": "Work", "quantity": "1", "unit_price": "100.00"}]},
        ctx,
    )
    invoice_id = invoice.invoice["id"]
    await tool_registry.execute("finance.request_invoice_approval", {"invoice_id": invoice_id}, ctx)  # auto-approves under threshold

    async def _fake_create_customer(self, *, access_token, realm_id, display_name, email, phone):
        return QuickBooksCustomerResponse(Id="qb-cust-tool", DisplayName=display_name)

    async def _fake_create_invoice(self, *, access_token, realm_id, customer_id, doc_number, lines):
        return QuickBooksInvoiceResponse(Id="qb-inv-tool", DocNumber=doc_number)

    monkeypatch.setattr(QuickBooksClient, "create_customer", _fake_create_customer)
    monkeypatch.setattr(QuickBooksClient, "create_invoice", _fake_create_invoice)

    output = await tool_registry.execute("finance.sync_invoice_to_quickbooks", {"invoice_id": invoice_id}, ctx)
    assert output.quickbooks_invoice_id == "qb-inv-tool"
    assert output.already_synced is False


async def test_sync_tool_honest_error_when_not_connected(tool_registry) -> None:
    from app.tools.errors import ToolError

    tenant_id = uuid.uuid4()
    ctx = _ctx(tenant_id)
    customer = await tool_registry.execute("crm.create_customer", {"name": "No QB Connection Customer"}, ctx)
    invoice = await tool_registry.execute(
        "finance.create_invoice_draft",
        {"customer_id": customer.customer["id"], "line_items": [{"description": "Work", "quantity": "1", "unit_price": "100.00"}]},
        ctx,
    )
    invoice_id = invoice.invoice["id"]
    await tool_registry.execute("finance.request_invoice_approval", {"invoice_id": invoice_id}, ctx)  # auto-approves under threshold

    with pytest.raises(ToolError, match="not connected"):
        await tool_registry.execute("finance.sync_invoice_to_quickbooks", {"invoice_id": invoice_id}, ctx)
