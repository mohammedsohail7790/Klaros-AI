"""HTTP-route wiring tests for tools that were already implemented and
registered in the ToolRegistry but had zero routes exposing them: the
Stripe invoice checkout link, invoice/payment/refund QuickBooks push-sync,
and contract detect-pending. The underlying tool behavior (Stripe/
QuickBooks API interaction, error handling) is already covered in
test_stripe_phase12f_gaps.py / test_quickbooks_*.py — these tests only
pin down that each new route calls the right tool with the right payload.
"""

import uuid
from datetime import date

import pytest

from app.integrations.stripe_client import StripeClient

pytestmark = pytest.mark.asyncio


async def _register(client) -> tuple[str, dict]:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": f"Routing Test {uuid.uuid4().hex[:6]}",
            "full_name": "Owner Test",
            "email": f"owner-{uuid.uuid4().hex[:8]}@example.com",
            "password": "supersecret1",
        },
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["tokens"]["access_token"]
    return token, {"Authorization": f"Bearer {token}"}


class _FakeCheckoutSession:
    url = "https://checkout.stripe.com/fake-session"
    id = "cs_test_fake123"


async def test_invoice_checkout_route_calls_stripe_tool(client, monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "STRIPE_SECRET_KEY", "sk_test_fake_key")

    token, headers = await _register(client)

    customer_resp = await client.post("/api/v1/customers", json={"name": "Checkout Customer"}, headers=headers)
    assert customer_resp.status_code == 201, customer_resp.text
    customer_id = customer_resp.json()["customer"]["id"]

    import_resp = await client.post(
        "/api/v1/invoices/import",
        json=[
            {
                "customer_name": "Checkout Customer",
                "invoice_number": "INV-CHK-1",
                "issue_date": str(date.today()),
                "due_date": str(date.today()),
                "amount": "150.00",
                "amount_paid": "0.00",
            }
        ],
        headers=headers,
    )
    assert import_resp.status_code == 201, import_resp.text
    invoice_id = import_resp.json()["results"][0]["invoice_id"]

    async def _fake_create_checkout_session(self, **kwargs):
        return _FakeCheckoutSession()

    monkeypatch.setattr(StripeClient, "create_checkout_session", _fake_create_checkout_session)

    resp = await client.post(
        f"/api/v1/invoices/{invoice_id}/checkout",
        json={"success_url": "https://example.com/ok", "cancel_url": "https://example.com/cancel"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["checkout_url"] == "https://checkout.stripe.com/fake-session"
    assert body["checkout_session_id"] == "cs_test_fake123"


async def test_invoice_sync_to_quickbooks_route_surfaces_not_connected(client) -> None:
    """No QuickBooks connection exists for a fresh tenant, so this proves
    the route reaches the real tool (which raises a clean ToolError) rather
    than 404ing on a missing route."""
    token, headers = await _register(client)

    customer_resp = await client.post("/api/v1/customers", json={"name": "QB Sync Customer"}, headers=headers)
    assert customer_resp.status_code == 201, customer_resp.text

    import_resp = await client.post(
        "/api/v1/invoices/import",
        json=[
            {
                "customer_name": "QB Sync Customer",
                "invoice_number": "INV-QB-1",
                "issue_date": str(date.today()),
                "due_date": str(date.today()),
                "amount": "200.00",
                "amount_paid": "0.00",
            }
        ],
        headers=headers,
    )
    assert import_resp.status_code == 201, import_resp.text
    invoice_id = import_resp.json()["results"][0]["invoice_id"]

    resp = await client.post(f"/api/v1/invoices/{invoice_id}/sync-to-quickbooks", headers=headers)
    assert resp.status_code == 400, resp.text
    assert "not connected" in resp.text.lower()


async def test_payment_sync_to_quickbooks_route_surfaces_not_connected(client) -> None:
    token, headers = await _register(client)
    resp = await client.post(
        f"/api/v1/payments/{uuid.uuid4()}/sync-to-quickbooks", params={"is_deposit": False}, headers=headers
    )
    # Payment doesn't exist for this tenant either way, but the important
    # thing is the route exists (not a 404 route-not-found) and reaches
    # the tool pipeline.
    assert resp.status_code == 400, resp.text
    assert "not found" in resp.text.lower()


async def test_refund_sync_to_quickbooks_route_exists(client) -> None:
    token, headers = await _register(client)
    resp = await client.post(f"/api/v1/refunds/{uuid.uuid4()}/sync-to-quickbooks", headers=headers)
    assert resp.status_code == 400, resp.text
    assert "not found" in resp.text.lower()


async def test_contract_detect_pending_route(client) -> None:
    token, headers = await _register(client)
    resp = await client.post("/api/v1/contracts/detect-pending", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"expired_contract_ids": []}
