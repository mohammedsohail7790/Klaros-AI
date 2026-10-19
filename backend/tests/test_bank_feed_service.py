"""Bank feed sync and reconcile: cursor, idempotency, modified/removed, pagination, PRODUCT_NOT_READY, and conservative invoice matching. Plaid HTTP is mocked."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.api.tool_deps_integrations import get_integration_connection_service
from app.db.session import async_session_maker, set_tenant_context
from app.models.bank_feed import BankAccount, BankFeedState, BankTransaction
from app.models.crm import Customer
from app.models.finance import Invoice
from app.services.bank_feed_service import BankFeedService
from tests.test_connector_plaid import ACCESS_TOKEN, FakePlaid, _err, plaid  # noqa: F401
from tests.test_halla_integration import _register, _tenant_id

pytestmark = pytest.mark.asyncio

ACCT = {"account_id": "acc-1", "name": "Business Checking", "mask": "0000", "type": "depository", "subtype": "checking", "balances": {"current": 1234.56, "iso_currency_code": "USD"}}
TODAY = date.today()


def svc() -> BankFeedService:
    return BankFeedService(async_session_maker, get_integration_connection_service())


def tx(txid, amount, name="DEPOSIT", days_ago=1, **over):
    return {"transaction_id": txid, "account_id": "acc-1", "amount": amount, "iso_currency_code": "USD", "date": (TODAY - timedelta(days=days_ago)).isoformat(), "name": name, "pending": False, **over}


def page(added=(), modified=(), removed=(), cursor="c1", more=False):
    return {"added": list(added), "modified": list(modified), "removed": [{"transaction_id": r} for r in removed], "accounts": [ACCT], "next_cursor": cursor, "has_more": more}


async def tenant(client, name="Feed Co"):
    token = await _register(client, name, f"{name.lower().replace(' ', '')}@example.com")
    tid = await _tenant_id(client, token)
    await get_integration_connection_service().connect(tid, "plaid", {"access_token": ACCESS_TOKEN, "item_id": "item-1"}, created_by=None, external_account_id="item-1", verify=False)
    return token, tid


async def rows(tid) -> dict[str, BankTransaction]:
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return {t.external_transaction_id: t for t in (await s.execute(select(BankTransaction).where(BankTransaction.tenant_id == tid))).scalars().all()}


async def invoice(tid, number, amount, *, customer="Acme Plumbing", issued_days_ago=10, due_in=20, status="SENT", currency="USD"):
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        c = Customer(tenant_id=tid, name=customer)
        s.add(c)
        await s.flush()
        inv = Invoice(tenant_id=tid, invoice_number=number, customer_id=c.id, status=status, issue_date=TODAY - timedelta(days=issued_days_ago), due_date=TODAY + timedelta(days=due_in),
                      currency=currency, subtotal=Decimal(amount), total=Decimal(amount), amount_due=Decimal(amount))
        s.add(inv)
        await s.commit()
        return inv.id


async def test_not_connected_is_reported_cleanly_and_touches_nothing(client) -> None:
    token = await _register(client, "No Bank Co", "nobank@example.com")
    tid = await _tenant_id(client, token)
    out = await svc().sync_and_reconcile(tid)
    assert out["status"] == "not_connected" and out["added"] == 0 and "reconcile" not in out
    assert await rows(tid) == {}


async def test_first_sync_stores_accounts_and_normalises_the_sign_and_saves_the_cursor(client, plaid) -> None:  # noqa: F811
    _, tid = await tenant(client)
    plaid.routes["/transactions/sync"] = page([tx("t-out", 42.5, "COFFEE SHOP"), tx("t-in", -1500.0, "ACH ACME PLUMBING", merchant_name="Acme")])
    out = await svc().sync(tid)
    assert out["status"] == "ok" and out["added"] == 2
    r = await rows(tid)
    assert (r["t-out"].direction, r["t-out"].amount) == ("OUT", Decimal("42.50"))  # Plaid positive = money out
    assert (r["t-in"].direction, r["t-in"].amount, r["t-in"].merchant) == ("IN", Decimal("1500.00"), "Acme")  # Plaid negative = deposit, stored as a positive magnitude
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        acct = (await s.execute(select(BankAccount).where(BankAccount.tenant_id == tid))).scalar_one()
        state = (await s.execute(select(BankFeedState).where(BankFeedState.tenant_id == tid))).scalar_one()
    assert (acct.name, acct.mask, acct.current_balance, acct.currency) == ("Business Checking", "0000", Decimal("1234.56"), "USD")
    assert state.cursor == "c1" and state.last_status == "ok"
    assert "cursor" not in plaid.bodies("/transactions/sync")[0]  # first pull has no cursor


async def test_rerunning_a_sync_is_idempotent_and_sends_the_stored_cursor(client, plaid) -> None:  # noqa: F811
    _, tid = await tenant(client)
    same = page([tx("t1", -100.0), tx("t2", 9.99)])
    plaid.routes["/transactions/sync"] = same
    assert (await svc().sync(tid))["added"] == 2
    again = await svc().sync(tid)  # Plaid replays the same rows (e.g. a retried page): nothing may duplicate
    assert again["status"] == "ok" and again["added"] == 0 and again["modified"] == 0
    assert len(await rows(tid)) == 2
    assert plaid.bodies("/transactions/sync")[1]["cursor"] == "c1"


async def test_modified_and_removed_transactions_are_applied(client, plaid) -> None:  # noqa: F811
    _, tid = await tenant(client)
    plaid.routes["/transactions/sync"] = page([tx("t1", 10.0, "PENDING CAFE", pending=True), tx("t2", 20.0), tx("t3", 30.0)])
    await svc().sync(tid)
    plaid.routes["/transactions/sync"] = page(modified=[tx("t1", 12.34, "CAFE", pending=False)], removed=["t3"], cursor="c2")
    out = await svc().sync(tid)
    assert (out["added"], out["modified"], out["removed"]) == (0, 1, 1)
    r = await rows(tid)
    assert set(r) == {"t1", "t2"} and r["t1"].amount == Decimal("12.34") and r["t1"].pending is False and r["t1"].description == "CAFE"


async def test_pagination_collects_every_page_and_saves_only_the_final_cursor(client, plaid) -> None:  # noqa: F811
    _, tid = await tenant(client)
    plaid.routes["/transactions/sync"] = [page([tx("a", 1.0)], cursor="p1", more=True), page([tx("b", 2.0)], cursor="p2", more=True), page([tx("c", 3.0)], cursor="p3")]
    out = await svc().sync(tid)
    assert out["added"] == 3 and [b.get("cursor") for b in plaid.bodies("/transactions/sync")] == [None, "p1", "p2"]
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert (await s.execute(select(BankFeedState.cursor).where(BankFeedState.tenant_id == tid))).scalar_one() == "p3"


async def test_a_mutation_during_pagination_restarts_from_the_original_cursor(client, plaid) -> None:  # noqa: F811
    _, tid = await tenant(client)
    plaid.routes["/transactions/sync"] = [page([tx("a", 1.0)], cursor="p1", more=True), (400, _err("TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION", "TRANSACTIONS_ERROR")),
                                          page([tx("a", 1.0), tx("b", 2.0)], cursor="p9")]
    out = await svc().sync(tid)
    assert out["status"] == "ok" and out["added"] == 2 and len(await rows(tid)) == 2


async def test_product_not_ready_is_reported_honestly_and_keeps_the_cursor(client, plaid) -> None:  # noqa: F811
    _, tid = await tenant(client)
    plaid.routes["/transactions/sync"] = (400, _err("PRODUCT_NOT_READY", "ITEM_ERROR", "the transactions product is not yet ready"))
    out = await svc().sync_and_reconcile(tid)
    assert out["status"] == "product_not_ready" and out["added"] == 0 and "reconcile" not in out and "first transaction pull" in out["message"]
    assert await rows(tid) == {}
    overview = await svc().overview(tid)
    assert overview["last_sync_status"] == "product_not_ready" and overview["connected"] is True
    plaid.routes["/transactions/sync"] = page([tx("t1", -5.0)])
    assert (await svc().sync(tid))["added"] == 1  # the next run recovers


async def test_login_required_and_other_errors_are_reported_not_hidden(client, plaid) -> None:  # noqa: F811
    _, tid = await tenant(client)
    plaid.routes["/transactions/sync"] = (400, _err("ITEM_LOGIN_REQUIRED"))
    out = await svc().sync(tid)
    assert out["status"] == "login_required" and "sign in again" in out["message"]
    plaid.routes["/transactions/sync"] = (400, _err("INVALID_FIELD", "INVALID_REQUEST"))
    out = await svc().sync(tid)
    assert out["status"] == "error" and "INVALID_FIELD" in out["message"] and ACCESS_TOKEN not in out["message"]


async def test_a_posted_transaction_inherits_the_decision_made_on_its_pending_version(client, plaid) -> None:  # noqa: F811
    _, tid = await tenant(client)
    plaid.routes["/transactions/sync"] = page([tx("pend", -75.0, "ACME DEPOSIT", pending=True)])
    await svc().sync(tid)
    pending_id = (await rows(tid))["pend"].id
    await svc().ignore(tid, pending_id)
    plaid.routes["/transactions/sync"] = page([tx("post", -75.0, "ACME DEPOSIT", pending_transaction_id="pend")], removed=["pend"], cursor="c2")
    await svc().sync(tid)
    r = await rows(tid)
    assert set(r) == {"post"} and r["post"].match_status == "IGNORED"


async def test_reconcile_auto_matches_only_when_exactly_one_open_invoice_has_that_amount(client, plaid) -> None:  # noqa: F811
    _, tid = await tenant(client)
    inv = await invoice(tid, "INV-1", "1500.00")
    await invoice(tid, "INV-2", "980.00", customer="Other Co")
    plaid.routes["/transactions/sync"] = page([tx("dep", -1500.0, "ACH CREDIT ACME PLUMBING", days_ago=2)])
    out = await svc().sync_and_reconcile(tid)
    assert out["reconcile"] == {"auto_matched": 1, "suggested": 0, "cleared": 0}
    t = (await rows(tid))["dep"]
    assert (t.match_status, t.matched_entity_type, t.matched_entity_id, t.match_method) == ("MATCHED", "invoice", inv, "auto")
    async with async_session_maker() as s:  # the bank feed only records the match: the invoice is untouched and no payment exists
        await set_tenant_context(s, tid)
        i = (await s.execute(select(Invoice).where(Invoice.id == inv))).scalar_one()
        from app.models.finance import Payment
        assert i.status == "SENT" and i.amount_due == Decimal("1500.00") and (await s.execute(select(func.count()).select_from(Payment))).scalar_one() == 0
    assert (await svc().reconcile(tid)) == {"auto_matched": 0, "suggested": 0, "cleared": 0}  # idempotent: a matched row is not revisited


async def test_reconcile_only_suggests_when_two_invoices_share_the_amount(client, plaid) -> None:  # noqa: F811
    _, tid = await tenant(client)
    a = await invoice(tid, "INV-A", "500.00", customer="Acme Plumbing")
    b = await invoice(tid, "INV-B", "500.00", customer="Birch Roofing")
    plaid.routes["/transactions/sync"] = page([tx("dep", -500.0, "WIRE FROM ACME PLUMBING LLC")])
    await svc().sync_and_reconcile(tid)
    t = (await rows(tid))["dep"]
    assert t.match_status == "SUGGESTED" and t.matched_entity_id == a and t.match_method is None  # the customer name ranks Acme first, but a person decides
    assert [c["invoice_id"] for c in t.match_candidates][:2] == [str(a), str(b)] and t.match_candidates[0]["score"] > t.match_candidates[1]["score"]


async def test_a_unique_amount_outside_the_window_without_a_name_is_not_even_suggested(client, plaid) -> None:  # noqa: F811
    _, tid = await tenant(client)
    await invoice(tid, "INV-OLD", "700.00", issued_days_ago=90, due_in=-60)  # due 60 days ago: far outside the window
    plaid.routes["/transactions/sync"] = page([tx("dep", -700.0, "MYSTERY CREDIT")])
    out = await svc().sync_and_reconcile(tid)
    assert out["reconcile"]["auto_matched"] == 0 and out["reconcile"]["suggested"] == 0
    assert (await rows(tid))["dep"].match_status == "UNMATCHED"


async def test_spend_pending_other_currency_and_non_open_invoices_are_never_matched(client, plaid) -> None:  # noqa: F811
    _, tid = await tenant(client)
    await invoice(tid, "INV-EUR", "100.00", currency="EUR")
    await invoice(tid, "INV-PAID", "200.00", status="PAID")
    await invoice(tid, "INV-OPEN", "300.00")
    plaid.routes["/transactions/sync"] = page([tx("eur", -100.0), tx("paid", -200.0), tx("spend", 300.0, "ACME PLUMBING"), tx("pending", -300.0, "ACME PLUMBING", pending=True)])
    await svc().sync_and_reconcile(tid)
    assert {k: v.match_status for k, v in (await rows(tid)).items()} == {"eur": "UNMATCHED", "paid": "UNMATCHED", "spend": "UNMATCHED", "pending": "UNMATCHED"}


async def test_two_deposits_cannot_claim_the_same_invoice(client, plaid) -> None:  # noqa: F811
    _, tid = await tenant(client)
    await invoice(tid, "INV-ONE", "250.00")
    plaid.routes["/transactions/sync"] = page([tx("d1", -250.0, days_ago=3), tx("d2", -250.0, days_ago=1)])
    await svc().sync_and_reconcile(tid)
    states = sorted(t.match_status for t in (await rows(tid)).values())
    assert states == ["MATCHED", "UNMATCHED"]


async def test_a_changed_amount_resets_an_auto_match(client, plaid) -> None:  # noqa: F811
    _, tid = await tenant(client)
    await invoice(tid, "INV-X", "640.00")
    plaid.routes["/transactions/sync"] = page([tx("dep", -640.0, "ACME PLUMBING")])
    await svc().sync_and_reconcile(tid)
    assert (await rows(tid))["dep"].match_status == "MATCHED"
    plaid.routes["/transactions/sync"] = page(modified=[tx("dep", -600.0, "ACME PLUMBING")], cursor="c2")
    await svc().sync(tid)
    t = (await rows(tid))["dep"]
    assert t.match_status == "UNMATCHED" and t.matched_entity_id is None and t.amount == Decimal("600.00")


async def test_another_business_never_sees_or_touches_these_rows(client, plaid) -> None:  # noqa: F811
    _, a = await tenant(client, "Alpha Co")
    _, b = await tenant(client, "Beta Co")
    plaid.routes["/transactions/sync"] = page([tx("only-a", -10.0)])
    await svc().sync(a)
    assert await rows(b) == {} and (await svc().overview(b))["accounts"] == []
    assert await svc().list_transactions(b) == [] and len(await svc().list_transactions(a)) == 1
    from app.services.bank_feed_service import BankFeedError
    a_id = (await rows(a))["only-a"].id
    with pytest.raises(BankFeedError) as caught:
        await svc().ignore(b, a_id)
    assert caught.value.status_code == 404
    inv_b = await invoice(b, "INV-B", "10.00")
    with pytest.raises(BankFeedError):
        await svc().match(b, a_id, entity_type="invoice", entity_id=inv_b)  # B cannot even see A's transaction
    plaid.routes["/transactions/sync"] = page([tx("dep-a", -10.0, "SOMEONE")], cursor="c2")
    await svc().sync(a)
    dep_a = (await rows(a))["dep-a"].id
    with pytest.raises(BankFeedError) as caught:  # A's deposit cannot be matched to B's invoice
        await svc().match(a, dep_a, entity_type="invoice", entity_id=inv_b)
    assert caught.value.status_code == 404


async def test_a_sync_never_needs_a_network_call_when_no_bank_is_connected(client, plaid) -> None:  # noqa: F811
    token = await _register(client, "Quiet Co", "quiet@example.com")
    await svc().sync(await _tenant_id(client, token))
    assert plaid.requests == []
