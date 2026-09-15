"""retention.detect_at_risk_and_inactive / detect_payment_issue_risk /
identify_advocate_candidates were fully implemented, registered tools
writing real state changes / CustomerRiskSignal / AdvocateCandidate
rows, but nothing ever called them and nothing ever read the rows back
except the AI's insights.get_retention_snapshot. Covers the new routes
that run the sweeps and list their results.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.crm import Customer
from app.models.finance import Invoice, InvoiceStatus
from app.models.operations import Job, JobPriority, JobStatus
from app.models.retention import CustomerLifecycleProfile, LifecycleState

pytestmark = pytest.mark.asyncio


async def _register(client) -> tuple[dict, uuid.UUID]:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": f"Risk Detection Co {uuid.uuid4().hex[:6]}", "full_name": "Owner Test",
            "email": f"owner-{uuid.uuid4().hex[:8]}@example.com", "password": "supersecret1",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    headers = {"Authorization": f"Bearer {body['tokens']['access_token']}"}
    tenant_id = uuid.UUID(body["user"]["tenant_id"])
    return headers, tenant_id


async def test_detect_at_risk_route_flips_lifecycle_state(client, event_bus) -> None:
    headers, tenant_id = await _register(client)

    async with event_bus.session_factory() as session:
        customer = Customer(tenant_id=tenant_id, name="Long Inactive Customer")
        session.add(customer)
        await session.flush()
        profile = CustomerLifecycleProfile(
            tenant_id=tenant_id, customer_id=customer.id, lifecycle_state=LifecycleState.ACTIVE,
            last_service_at=datetime.now(timezone.utc) - timedelta(days=400),
            state_changed_at=datetime.now(timezone.utc) - timedelta(days=400),
        )
        session.add(profile)
        await session.commit()

    resp = await client.post("/api/v1/retention/detect-at-risk", headers=headers)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["changed_customer_ids"]) == 1


async def test_detect_payment_risk_route_creates_and_lists_signal(client, event_bus) -> None:
    headers, tenant_id = await _register(client)

    async with event_bus.session_factory() as session:
        customer = Customer(tenant_id=tenant_id, name="Overdue Customer")
        session.add(customer)
        await session.flush()
        invoice = Invoice(
            tenant_id=tenant_id, invoice_number=f"INV-RISK-{uuid.uuid4().hex[:6]}", customer_id=customer.id,
            status=InvoiceStatus.OVERDUE, issue_date=date.today() - timedelta(days=60),
            due_date=date.today() - timedelta(days=30),
            subtotal=Decimal("500.00"), tax=Decimal("0"), discount=Decimal("0"), total=Decimal("500.00"),
            amount_paid=Decimal("0"), amount_due=Decimal("500.00"),
        )
        session.add(invoice)
        await session.commit()

    resp = await client.post("/api/v1/retention/detect-payment-risk", headers=headers)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["flagged_customer_ids"]) == 1

    list_resp = await client.get("/api/v1/retention/risk-signals", headers=headers)
    assert list_resp.status_code == 200, list_resp.text
    signals = list_resp.json()["risk_signals"]
    assert len(signals) == 1
    assert signals[0]["signal_type"] == "PAYMENT_ISSUE"
    assert signals[0]["resolved"] is False

    # A repeat sweep must not duplicate the signal.
    resp2 = await client.post("/api/v1/retention/detect-payment-risk", headers=headers)
    assert resp2.json()["flagged_customer_ids"] == []
    list_resp2 = await client.get("/api/v1/retention/risk-signals", headers=headers)
    assert len(list_resp2.json()["risk_signals"]) == 1


async def test_detect_advocates_route_and_list(client, event_bus) -> None:
    headers, tenant_id = await _register(client)

    async with event_bus.session_factory() as session:
        customer = Customer(tenant_id=tenant_id, name="Repeat Customer")
        session.add(customer)
        await session.flush()
        for i in range(3):
            job = Job(
                tenant_id=tenant_id, customer_id=customer.id, job_number=f"JOB-ADV-{i}-{uuid.uuid4().hex[:4]}",
                title="Repeat Job", status=JobStatus.CLOSED, priority=JobPriority.NORMAL,
            )
            session.add(job)
            await session.flush()
            invoice = Invoice(
                tenant_id=tenant_id, invoice_number=f"INV-ADV-{i}-{uuid.uuid4().hex[:4]}", customer_id=customer.id,
                job_id=job.id, status=InvoiceStatus.PAID, issue_date=date.today(), due_date=date.today(),
                subtotal=Decimal("100.00"), tax=Decimal("0"), discount=Decimal("0"), total=Decimal("100.00"),
                amount_paid=Decimal("100.00"), amount_due=Decimal("0"),
            )
            session.add(invoice)
        await session.commit()

    resp = await client.post("/api/v1/retention/detect-advocates", headers=headers)
    assert resp.status_code == 200, resp.text

    list_resp = await client.get("/api/v1/retention/advocate-candidates", headers=headers)
    assert list_resp.status_code == 200, list_resp.text
