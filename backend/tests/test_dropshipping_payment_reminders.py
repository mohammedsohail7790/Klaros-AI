"""A shopper who started an order and did not pay gets at most two plain reminders (after 1 hour and after 24 hours), and nothing in any case where it would be wrong."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import async_session_maker, set_tenant_context
from app.models.dropshipping import Order
from app.services import dropshipping_payment_reminders as reminders
from tests.test_dropshipping_api import _h
from tests.test_dropshipping_orders_api import BASE
from tests.test_dropshipping_store_api import _checkout, _live_store, _order_row, stripe  # noqa: F401  (stripe is a fixture)


class Mailbox:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.accept = True

    async def __call__(self, *, to, subject, body, from_name=None, **_):
        if self.accept:
            self.sent.append({"to": to, "subject": subject, "body": body, "from_name": from_name})
        return self.accept


@pytest.fixture
def mailbox(monkeypatch) -> Mailbox:
    box = Mailbox()
    monkeypatch.setattr(reminders, "send_system_email", box)
    return box


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _started(client, monkeypatch, org: str, email: str):
    owner, tenant = await _live_store(client, monkeypatch, org, email)
    out = await _checkout(client)
    row = await _order_row(out["order_number"])
    return owner, tenant, out, _aware(row.placed_at)


async def _count(order_number: str) -> int:
    return (await _order_row(order_number)).payment_reminders_sent


async def test_no_reminder_in_the_first_hour_then_one_then_the_last_one_a_day_in(client, monkeypatch, stripe, mailbox) -> None:
    _, tenant, out, placed = await _started(client, monkeypatch, "Remind Co", "remind-co@example.com")
    assert await reminders.send_due_reminders(async_session_maker, tenant, now=placed + timedelta(minutes=30)) == 0 and mailbox.sent == []

    assert await reminders.send_due_reminders(async_session_maker, tenant, now=placed + timedelta(minutes=70)) == 1
    first = mailbox.sent[0]
    assert "@" in first["to"] and first["from_name"] == get_settings().DROPSHIPPING_STORE_NAME
    assert out["order_number"] in first["subject"] and "waiting for payment" in first["subject"]
    assert f"/store/orders/{out['status_token']}" in first["body"] and "Total: 35.00 USD" in first["body"] and "Green Tea" in first["body"]
    assert "last reminder" not in first["body"] and "$" not in first["body"] and "discount" not in first["body"].lower()
    assert await _count(out["order_number"]) == 1

    assert await reminders.send_due_reminders(async_session_maker, tenant, now=placed + timedelta(minutes=90)) == 0     # the same hour again: nothing
    assert await reminders.send_due_reminders(async_session_maker, tenant, now=placed + timedelta(hours=25)) == 1
    assert "last reminder we will send" in mailbox.sent[1]["body"]
    assert await _count(out["order_number"]) == 2
    assert await reminders.send_due_reminders(async_session_maker, tenant, now=placed + timedelta(hours=40)) == 0       # never a third
    assert len(mailbox.sent) == 2


async def test_a_paid_or_cancelled_order_is_never_reminded(client, monkeypatch, stripe, mailbox) -> None:
    _, tenant, out, placed = await _started(client, monkeypatch, "Paid Co", "paid-co@example.com")
    async with async_session_maker() as session:
        await set_tenant_context(session, tenant)
        order = (await session.execute(select(Order).where(Order.order_number == out["order_number"]))).scalar_one()
        order.status = "PAID"
        await session.commit()
    assert await reminders.send_due_reminders(async_session_maker, tenant, now=placed + timedelta(hours=2)) == 0 and mailbox.sent == []


async def test_nothing_for_an_old_order_an_erased_order_a_closed_store_or_when_switched_off(client, monkeypatch, stripe, mailbox) -> None:
    owner, tenant, out, placed = await _started(client, monkeypatch, "Edge Co", "edge-co@example.com")
    assert await reminders.send_due_reminders(async_session_maker, tenant, now=placed + timedelta(days=4)) == 0           # older than three days

    monkeypatch.setattr(get_settings(), "DROPSHIPPING_PAYMENT_REMINDERS", False)
    assert await reminders.send_due_reminders(async_session_maker, tenant, now=placed + timedelta(hours=2)) == 0           # switched off
    monkeypatch.setattr(get_settings(), "DROPSHIPPING_PAYMENT_REMINDERS", True)

    assert (await client.put(f"{BASE}/store-settings", json={"open": False}, headers=_h(owner))).status_code == 200
    assert await reminders.send_due_reminders(async_session_maker, tenant, now=placed + timedelta(hours=2)) == 0           # closed: the pay link would not work
    assert (await client.put(f"{BASE}/store-settings", json={"open": True}, headers=_h(owner))).status_code == 200

    async with async_session_maker() as session:
        await set_tenant_context(session, tenant)
        order = (await session.execute(select(Order).where(Order.order_number == out["order_number"]))).scalar_one()
        order.contact_email, order.personal_data_erased_at = None, datetime.now(timezone.utc)
        await session.commit()
    assert await reminders.send_due_reminders(async_session_maker, tenant, now=placed + timedelta(hours=2)) == 0           # erased: no address
    assert mailbox.sent == []


async def test_a_message_the_provider_did_not_accept_is_not_counted_and_is_tried_again(client, monkeypatch, stripe, mailbox) -> None:
    _, tenant, out, placed = await _started(client, monkeypatch, "Retry Co", "retry-co@example.com")
    mailbox.accept = False
    assert await reminders.send_due_reminders(async_session_maker, tenant, now=placed + timedelta(hours=2)) == 0
    assert await _count(out["order_number"]) == 0
    mailbox.accept = True
    assert await reminders.send_due_reminders(async_session_maker, tenant, now=placed + timedelta(hours=2, minutes=15)) == 1
    assert await _count(out["order_number"]) == 1
