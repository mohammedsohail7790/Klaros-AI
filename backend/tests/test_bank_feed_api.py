"""`/api/v1/bank-feed`: contract, permissions and tenant isolation, plus the governed tool and the crew member. Plaid HTTP is mocked."""

import pytest

from app.main import app
from app.api.v1 import bank_feed as bank_feed_api
from app.services import operating_crew as oc
from app.tools.policy import ActionPolicy, DEFAULT_TOOL_POLICIES
from tests.test_bank_feed_service import ACCT, invoice, page, rows, tenant, tx  # noqa: F401
from tests.test_connector_plaid import plaid  # noqa: F401
from tests.test_halla_integration import _h, _register

pytestmark = pytest.mark.asyncio

if not any(getattr(r, "path", "").startswith("/api/v1/bank-feed") for r in app.routes):  # until the lead registers the router in router.py
    app.include_router(bank_feed_api.router, prefix="/api/v1")


async def test_accounts_shows_not_connected_before_a_bank_is_connected(client) -> None:
    token = await _register(client, "Api Empty Co", "apiempty@example.com")
    r = await client.get("/api/v1/bank-feed/accounts", headers=_h(token))
    assert r.status_code == 200
    assert r.json() == {"connected": False, "connection_status": "NOT_CONNECTED", "connection_error": None, "last_sync_at": None, "last_sync_status": None, "last_sync_error": None, "accounts": []}
    assert (await client.get("/api/v1/bank-feed/transactions", headers=_h(token))).json() == []
    sync = await client.post("/api/v1/bank-feed/sync", headers=_h(token))
    assert sync.status_code == 200 and sync.json()["status"] == "not_connected"


async def test_requires_login(client) -> None:
    assert (await client.get("/api/v1/bank-feed/accounts")).status_code in (401, 403)
    assert (await client.post("/api/v1/bank-feed/sync")).status_code in (401, 403)


async def test_sync_then_list_then_confirm_and_ignore_through_the_api(client, plaid) -> None:  # noqa: F811
    token, tid = await tenant(client, "Api Co")
    a = await invoice(tid, "INV-A", "500.00", customer="Acme Plumbing")
    await invoice(tid, "INV-B", "500.00", customer="Birch Roofing")
    plaid.routes["/transactions/sync"] = page([tx("dep", -500.0, "WIRE ACME PLUMBING"), tx("spend", 12.0, "COFFEE")])
    r = await client.post("/api/v1/bank-feed/sync", headers=_h(token))
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ok" and body["added"] == 2 and body["reconcile"] == {"auto_matched": 0, "suggested": 1, "cleared": 0}

    acc = (await client.get("/api/v1/bank-feed/accounts", headers=_h(token))).json()
    assert acc["connected"] is True and acc["last_sync_status"] == "ok" and acc["accounts"][0]["name"] == "Business Checking" and acc["accounts"][0]["current_balance"] == "1234.56"
    assert "access_token" not in str(acc) and "item" not in {k for k in acc}

    listed = (await client.get("/api/v1/bank-feed/transactions", headers=_h(token))).json()
    assert {t["match_status"] for t in listed} == {"SUGGESTED", "UNMATCHED"}
    dep = next(t for t in listed if t["direction"] == "IN")
    assert dep["amount"] == "500.00" and dep["matched_entity_id"] == str(a) and dep["match_candidates"][0]["invoice_number"] == "INV-A"
    only = (await client.get("/api/v1/bank-feed/transactions?status=suggested", headers=_h(token))).json()
    assert [t["id"] for t in only] == [dep["id"]]
    assert (await client.get("/api/v1/bank-feed/transactions?status=bogus", headers=_h(token))).status_code == 422

    m = await client.post(f"/api/v1/bank-feed/transactions/{dep['id']}/match", json={"entity_type": "invoice", "entity_id": str(a)}, headers=_h(token))
    assert m.status_code == 200 and m.json()["match_status"] == "MATCHED" and m.json()["match_method"] == "manual" and m.json()["match_candidates"] is None
    spend = next(t for t in listed if t["direction"] == "OUT")
    bad = await client.post(f"/api/v1/bank-feed/transactions/{spend['id']}/match", json={"entity_id": str(a)}, headers=_h(token))
    assert bad.status_code == 409  # a spend is not a payment
    ig = await client.post(f"/api/v1/bank-feed/transactions/{spend['id']}/ignore", headers=_h(token))
    assert ig.status_code == 200 and ig.json()["match_status"] == "IGNORED"


async def test_another_business_gets_empty_lists_and_404s(client, plaid) -> None:  # noqa: F811
    ta, tid_a = await tenant(client, "Api Alpha Co")
    tb = await _register(client, "Api Beta Co", "apibeta@example.com")
    plaid.routes["/transactions/sync"] = page([tx("dep", -5.0)])
    await client.post("/api/v1/bank-feed/sync", headers=_h(ta))
    dep_id = (await client.get("/api/v1/bank-feed/transactions", headers=_h(ta))).json()[0]["id"]
    assert (await client.get("/api/v1/bank-feed/transactions", headers=_h(tb))).json() == []
    assert (await client.get("/api/v1/bank-feed/accounts", headers=_h(tb))).json()["accounts"] == []
    assert (await client.post(f"/api/v1/bank-feed/transactions/{dep_id}/ignore", headers=_h(tb))).status_code == 404
    inv_a = await invoice(tid_a, "INV-A", "5.00")
    assert (await client.post(f"/api/v1/bank-feed/transactions/{dep_id}/match", json={"entity_id": str(inv_a)}, headers=_h(tb))).status_code == 404


async def test_the_governed_tool_and_crew_member_exist_and_are_deterministic(client, plaid, tool_registry) -> None:  # noqa: F811
    assert DEFAULT_TOOL_POLICIES["finance.sync_bank_feed"] is ActionPolicy.AUTO
    tool_registry.get("finance.sync_bank_feed")
    member = next(m for m in oc.crew_for([]) if m.key == "bank-feed-sync")  # in the generic crew, so every business has it
    assert member.name == "Bank Feed Sync" and member.schedule_action == ("finance.sync_bank_feed", {}) and member.tools == ("finance.sync_bank_feed",)
    assert member.event_type is None and member.schedule_time


async def test_the_tool_runs_through_the_registry_and_reports_not_connected(client, tool_registry) -> None:
    from app.tools.builtin.bank_feed_tools import SyncBankFeed
    from app.api.tool_deps_integrations import get_integration_connection_service
    from app.db.session import async_session_maker
    from app.services.bank_feed_service import BankFeedService
    import uuid
    tool = SyncBankFeed(BankFeedService(async_session_maker, get_integration_connection_service()))
    out = await tool.execute(tool.input_schema(), type("Ctx", (), {"tenant_id": uuid.uuid4()})())
    assert out.status == "not_connected" and out.reconcile is None
