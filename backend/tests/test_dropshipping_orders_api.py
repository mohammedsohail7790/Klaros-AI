"""Dropshipping store, branch 3: the staff order API over HTTP (permissions, the whole staff flow, status codes, isolation, audit rows)."""

import uuid

from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.audit_log import AuditLog
from app.models.rbac import Role
from tests.test_business_journey_api import _register
from tests.test_dropshipping_api import _h, _tenant_id, _user_token

BASE = "/api/v1/dropshipping"
SHIP = {"name": "Pat Shopper", "line1": "12 Main St", "city": "Austin", "state": "TX", "postal_code": "78701"}
WHO = {"name": "Pat Shopper", "email": "pat@example.com", "phone": "512-555-0147"}


async def _owner_with_store(client, org: str, email: str) -> str:
    """An owner whose store has Tea (Acme Supply) and Rice (Beta Foods), both published and in stock, via the real catalog API."""
    owner = await _register(client, org, email)
    h = _h(owner)
    for name in ("Acme Supply", "Beta Foods"):
        assert (await client.post(f"{BASE}/suppliers", json={"name": name}, headers=h)).status_code == 200
    for name, code, supplier in (("Green Tea", "TEA-1", "Acme Supply"), ("Brown Rice", "RICE-1", "Beta Foods")):
        product = (await client.post(f"{BASE}/products", json={"name": name, "is_non_perishable": True, "skus": [{"sku_code": code, "price": "10.00", "supplier": supplier, "supplier_sku": code + "-S", "availability": "AVAILABLE"}]}, headers=h)).json()["product"]
        assert (await client.post(f"{BASE}/products/{product['id']}/publish", headers=h)).status_code == 200
    return owner


def _body(key: str | None = None, **over) -> dict:
    return {"lines": [{"sku": "TEA-1", "quantity": 2}, {"sku": "RICE-1", "quantity": 1}], "contact": WHO, "ship_to": SHIP, "idempotency_key": key or "idem-" + uuid.uuid4().hex, **over}


async def _create(client, token: str, **over) -> dict:
    r = await client.post(f"{BASE}/orders", json=_body(**over), headers=_h(token))
    assert r.status_code == 200, r.text
    return r.json()["order"]


async def test_every_order_route_needs_a_signed_in_user(client) -> None:
    oid, sid = uuid.uuid4(), uuid.uuid4()
    for method, path in (("get", "/orders"), ("get", f"/orders/{oid}"), ("post", "/orders"), ("post", f"/orders/{oid}/mark-paid"), ("post", f"/orders/{oid}/cancel"),
                         ("post", f"/orders/{oid}/request-refund"), ("post", f"/orders/{oid}/erase-personal-data"), ("get", "/supplier-orders"),
                         ("post", f"/supplier-orders/{sid}/placed"), ("post", f"/supplier-orders/{sid}/shipments"), ("post", f"/shipments/{sid}/delivered")):
        resp = await getattr(client, method)(f"{BASE}{path}", **({"json": {}} if method == "post" else {}))
        assert resp.status_code == 401, (method, path)


async def test_permissions_split_reading_from_working_orders_from_money(client) -> None:
    owner = await _owner_with_store(client, "Perm Orders Co", "perm-orders@example.com")
    tokens = {role: await _user_token(client, owner, f"{role.value.lower()}@orders.example.com", role)
              for role in (Role.MANAGER, Role.STAFF, Role.READ_ONLY, Role.TECHNICIAN, Role.ACCOUNTANT)}
    order = await _create(client, owner)
    oid = order["id"]

    for role in (Role.MANAGER, Role.STAFF, Role.READ_ONLY):
        assert (await client.get(f"{BASE}/orders", headers=_h(tokens[role]))).status_code == 200, role
        assert (await client.get(f"{BASE}/orders/{oid}", headers=_h(tokens[role]))).status_code == 200, role
        assert (await client.get(f"{BASE}/supplier-orders", headers=_h(tokens[role]))).status_code == 200, role
    for role in (Role.TECHNICIAN, Role.ACCOUNTANT):
        assert (await client.get(f"{BASE}/orders", headers=_h(tokens[role]))).status_code == 403, role
        assert (await client.get(f"{BASE}/supplier-orders", headers=_h(tokens[role]))).status_code == 403, role

    # working orders: staff and manager may; read-only, technician and accountant may not
    for role in (Role.READ_ONLY, Role.TECHNICIAN, Role.ACCOUNTANT):
        h = _h(tokens[role])
        assert (await client.post(f"{BASE}/orders", json=_body(), headers=h)).status_code == 403, role
        assert (await client.post(f"{BASE}/orders/{oid}/cancel", json={"reason": "x"}, headers=h)).status_code == 403, role
        assert (await client.post(f"{BASE}/orders/{oid}/request-refund", json={"reason": "x"}, headers=h)).status_code == 403, role
        assert (await client.post(f"{BASE}/supplier-orders/{uuid.uuid4()}/placed", json={"supplier_reference": "R"}, headers=h)).status_code == 403, role
        assert (await client.post(f"{BASE}/supplier-orders/{uuid.uuid4()}/shipments", json={"tracking_number": "ABC123"}, headers=h)).status_code == 403, role
        assert (await client.post(f"{BASE}/shipments/{uuid.uuid4()}/delivered", headers=h)).status_code == 403, role
    for role in (Role.STAFF, Role.MANAGER):
        assert (await client.post(f"{BASE}/orders", json=_body(), headers=_h(tokens[role]))).status_code == 200, role

    # money and personal data: manage only. Staff (who can coordinate) cannot record a payment or erase.
    for role in (Role.STAFF, Role.READ_ONLY, Role.TECHNICIAN, Role.ACCOUNTANT):
        h = _h(tokens[role])
        assert (await client.post(f"{BASE}/orders/{oid}/mark-paid", json={"payment_reference": "BANK-1"}, headers=h)).status_code == 403, role
        assert (await client.post(f"{BASE}/orders/{oid}/erase-personal-data", headers=h)).status_code == 403, role
    paid = await client.post(f"{BASE}/orders/{oid}/mark-paid", json={"payment_reference": "BANK-1"}, headers=_h(tokens[Role.MANAGER]))
    assert paid.status_code == 200 and paid.json()["order"]["status"] == "PAID"


async def test_the_whole_staff_flow_over_http(client) -> None:
    owner = await _owner_with_store(client, "Flow Orders Co", "flow-orders@example.com")
    staff = await _user_token(client, owner, "staff@flow-orders.example.com", Role.STAFF)
    order = await _create(client, staff, shipping="4.99")  # staff take a phone order
    oid = order["id"]
    assert order["status"] == "PENDING_PAYMENT" and order["total"] == "34.99" and order["tax"] == "0.00"
    assert (await client.get(f"{BASE}/orders?status=PAID", headers=_h(staff))).json()["orders"] == []
    assert [o["id"] for o in (await client.get(f"{BASE}/orders?q={order['order_number'][-4:]}", headers=_h(staff))).json()["orders"]] == [oid]

    paid = (await client.post(f"{BASE}/orders/{oid}/mark-paid", json={"payment_reference": "BANK-42"}, headers=_h(owner))).json()["order"]  # only manage can
    assert paid["status"] == "PAID" and paid["payment_source"] == "MANUAL" and len(paid["supplier_orders"]) == 2

    queue = (await client.get(f"{BASE}/supplier-orders?status=TO_PLACE", headers=_h(staff))).json()["supplier_orders"]
    assert {q["supplier"] for q in queue} == {"Acme Supply", "Beta Foods"} and all(q["ship_to"]["city"] == "Austin" for q in queue)
    assert {i["supplier_sku"] for q in queue for i in q["items"]} == {"TEA-1-S", "RICE-1-S"}
    for so in queue:
        placed = await client.post(f"{BASE}/supplier-orders/{so['id']}/placed", json={"supplier_reference": "REF-" + so["supplier"][:4]}, headers=_h(staff))
        assert placed.status_code == 200, placed.text
    assert placed.json()["order"]["status"] == "SUPPLIER_ORDER_PLACED"

    for so in queue:
        shipped = await client.post(f"{BASE}/supplier-orders/{so['id']}/shipments", json={"carrier": "UPS", "tracking_number": "1Z" + so["id"][:12], "tracking_url": "https://track.example/x"}, headers=_h(staff))
        assert shipped.status_code == 200, shipped.text
    order_now = shipped.json()["order"]
    assert order_now["status"] == "SHIPPED"
    for so in order_now["supplier_orders"]:
        for sh in so["shipments"]:
            delivered = await client.post(f"{BASE}/shipments/{sh['id']}/delivered", headers=_h(staff))
            assert delivered.status_code == 200
    assert delivered.json()["order"]["status"] == "DELIVERED"

    erased = await client.post(f"{BASE}/orders/{oid}/erase-personal-data", headers=_h(owner))
    assert erased.status_code == 200 and erased.json()["order"]["personal_data_erased"] is True and erased.json()["order"]["ship_to"] is None
    assert (await client.get(f"{BASE}/orders/{oid}", headers=_h(staff))).json()["order"]["contact_email"] is None


async def test_status_codes_for_bad_input_missing_things_and_illegal_moves(client) -> None:
    owner = await _owner_with_store(client, "Codes Co", "codes@example.com")
    h = _h(owner)
    order = await _create(client, owner)
    oid = order["id"]
    assert (await client.get(f"{BASE}/orders/{uuid.uuid4()}", headers=h)).status_code == 404
    assert (await client.post(f"{BASE}/orders/{uuid.uuid4()}/cancel", json={"reason": "x"}, headers=h)).status_code == 404
    assert (await client.get(f"{BASE}/orders?status=BOGUS", headers=h)).status_code == 422
    assert (await client.get(f"{BASE}/supplier-orders?status=BOGUS", headers=h)).status_code == 422
    assert (await client.post(f"{BASE}/supplier-orders/{uuid.uuid4()}/teleported", headers=h)).status_code == 404  # an unknown action
    assert (await client.post(f"{BASE}/shipments/{uuid.uuid4()}/teleported", headers=h)).status_code == 404
    assert (await client.post(f"{BASE}/supplier-orders/{uuid.uuid4()}/placed", json={"supplier_reference": "R"}, headers=h)).status_code == 404

    bad = await client.post(f"{BASE}/orders", json=_body(ship_to={**SHIP, "state": "AK"}), headers=h)
    assert bad.status_code == 422 and any("contiguous" in p for p in bad.json()["detail"]["problems"])
    assert (await client.post(f"{BASE}/orders", json=_body(lines=[{"sku": "NOPE", "quantity": 1}]), headers=h)).status_code == 422
    assert (await client.post(f"{BASE}/orders", json={"lines": []}, headers=h)).status_code == 422
    assert (await client.post(f"{BASE}/orders/{oid}/mark-paid", json={}, headers=h)).status_code == 422  # a reference is mandatory
    assert (await client.post(f"{BASE}/orders/{oid}/cancel", json={}, headers=h)).status_code == 422  # and so is a reason

    key = "idem-fixed-key-1"
    first = await client.post(f"{BASE}/orders", json=_body(key), headers=h)
    again = await client.post(f"{BASE}/orders", json=_body(key), headers=h)
    assert first.json()["created"] is True and again.json()["created"] is False and again.json()["order"]["id"] == first.json()["order"]["id"]
    different = await client.post(f"{BASE}/orders", json=_body(key, shipping="9.00"), headers=h)
    assert different.status_code == 409

    assert (await client.post(f"{BASE}/orders/{oid}/mark-paid", json={"payment_reference": "BANK-1"}, headers=h)).status_code == 200
    assert (await client.post(f"{BASE}/orders/{oid}/mark-paid", json={"payment_reference": "BANK-1"}, headers=h)).status_code == 200  # same reference: idempotent
    assert (await client.post(f"{BASE}/orders/{oid}/mark-paid", json={"payment_reference": "BANK-2"}, headers=h)).status_code == 409
    assert (await client.post(f"{BASE}/orders/{oid}/erase-personal-data", headers=h)).status_code == 409  # still in flight

    so = (await client.get(f"{BASE}/orders/{oid}", headers=h)).json()["order"]["supplier_orders"][0]
    assert (await client.post(f"{BASE}/supplier-orders/{so['id']}/shipments", json={"tracking_number": "ABC123"}, headers=h)).status_code == 409  # not placed yet
    assert (await client.post(f"{BASE}/supplier-orders/{so['id']}/placed", json={}, headers=h)).status_code == 422  # supplier reference required
    assert (await client.post(f"{BASE}/supplier-orders/{so['id']}/placed", json={"supplier_reference": "R-1"}, headers=h)).status_code == 200
    assert (await client.post(f"{BASE}/orders/{oid}/cancel", json={"reason": "too late"}, headers=h)).status_code == 409  # a supplier order is placed
    assert (await client.post(f"{BASE}/orders/{oid}/request-refund", json={"reason": "Damaged"}, headers=h)).status_code == 200


async def test_one_business_never_sees_or_changes_anothers_orders(client) -> None:
    owner_a = await _owner_with_store(client, "Iso Orders A", "iso-orders-a@example.com")
    owner_b = await _owner_with_store(client, "Iso Orders B", "iso-orders-b@example.com")
    order = await _create(client, owner_a)
    paid = (await client.post(f"{BASE}/orders/{order['id']}/mark-paid", json={"payment_reference": "A-1"}, headers=_h(owner_a))).json()["order"]
    hb = _h(owner_b)
    assert (await client.get(f"{BASE}/orders", headers=hb)).json()["orders"] == []
    assert (await client.get(f"{BASE}/supplier-orders", headers=hb)).json()["supplier_orders"] == []
    assert (await client.get(f"{BASE}/orders/{order['id']}", headers=hb)).status_code == 404
    for path, body in ((f"/orders/{order['id']}/cancel", {"reason": "x"}), (f"/orders/{order['id']}/request-refund", {"reason": "x"}), (f"/orders/{order['id']}/mark-paid", {"payment_reference": "B-1"}),
                       (f"/orders/{order['id']}/erase-personal-data", {}), (f"/supplier-orders/{paid['supplier_orders'][0]['id']}/placed", {"supplier_reference": "R"}),
                       (f"/supplier-orders/{paid['supplier_orders'][0]['id']}/shipments", {"tracking_number": "ABC123"})):
        assert (await client.post(f"{BASE}{path}", json=body, headers=hb)).status_code == 404, path
    assert (await client.get(f"{BASE}/orders/{order['id']}", headers=_h(owner_a))).json()["order"]["status"] == "PAID"  # untouched
    other = await _create(client, owner_b)  # B can order the same SKU codes from its own catalog
    assert other["id"] != order["id"]


async def test_every_status_change_by_a_person_is_audited(client) -> None:
    owner = await _owner_with_store(client, "Audit Orders Co", "audit-orders@example.com")
    h = _h(owner)
    tenant = await _tenant_id(client, owner)
    order = await _create(client, owner)
    oid = order["id"]
    await client.post(f"{BASE}/orders/{oid}/mark-paid", json={"payment_reference": "BANK-9"}, headers=h)
    so = (await client.get(f"{BASE}/orders/{oid}", headers=h)).json()["order"]["supplier_orders"]
    for s in so:
        await client.post(f"{BASE}/supplier-orders/{s['id']}/placed", json={"supplier_reference": "R-" + s["id"][:4]}, headers=h)
    await client.post(f"{BASE}/orders/{oid}/request-refund", json={"reason": "Test"}, headers=h)
    async with async_session_maker() as session:
        rows = list((await session.execute(select(AuditLog).where(AuditLog.tenant_id == tenant, AuditLog.action.like("dropshipping.order.%")))).scalars())
    actions = {r.action for r in rows}
    assert {"dropshipping.order.created", "dropshipping.order.paid", "dropshipping.order.supplier_orders_placed", "dropshipping.order.refund_requested"} <= actions
    moves = [r.input_summary for r in rows if r.action == "dropshipping.order.paid"]
    assert moves and moves[0]["from"] == "PENDING_PAYMENT" and moves[0]["to"] == "PAID" and moves[0]["source"] == "MANUAL"
    assert all(r.actor_id is not None for r in rows)  # a person is recorded on each
    summary = " ".join(str(r.input_summary) for r in rows)
    assert "pat@example.com" not in summary and "512-555-0147" not in summary and "12 Main St" not in summary  # the audit trail holds no personal data
