"""Dropshipping store, branch 2: the staff catalog API over HTTP (permissions, status codes, the publish and import flows, tenant isolation, audit rows)."""

import uuid

from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.audit_log import AuditLog
from app.models.rbac import Role
from tests.test_business_journey_api import _register
from tests.test_dropshipping_catalog import HEADER, _csv, _row  # noqa: F401

BASE = "/api/v1/dropshipping"
SUPPLIER = "Acme Supply"


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _tenant_id(client, owner_token: str) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/users/me", headers=_h(owner_token))).json()["tenant_id"])


async def _user_token(client, owner_token: str, email: str, role: Role) -> str:
    from app.core.security import create_access_token, hash_password
    from app.models.user import User

    tenant_id = await _tenant_id(client, owner_token)
    async with async_session_maker() as session:
        user = User(tenant_id=tenant_id, email=email, full_name=f"{role.value} User", hashed_password=hash_password("supersecret1"), role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return create_access_token(user.id, tenant_id, role)


async def _owner_with_catalog(client, org: str, email: str) -> tuple[str, dict]:
    owner = await _register(client, org, email)
    assert (await client.post(f"{BASE}/suppliers", json={"name": SUPPLIER}, headers=_h(owner))).status_code == 200
    r = await client.post(
        f"{BASE}/products",
        json={"name": "Demo Tea", "category": "Beverages", "skus": [{"sku_code": "T-1", "price": "10.00", "supplier": SUPPLIER, "supplier_cost": "6.00"}]},
        headers=_h(owner),
    )
    assert r.status_code == 200, r.text
    return owner, r.json()["product"]


async def test_every_route_needs_a_signed_in_user(client) -> None:
    for method, path in (("get", "/products"), ("get", "/suppliers"), ("post", "/products"), ("post", "/suppliers"), ("post", f"/products/{uuid.uuid4()}/publish"),
                         ("post", "/catalog/import/preview"), ("post", "/catalog/import/commit")):
        resp = await getattr(client, method)(f"{BASE}{path}", **({"json": {}} if method == "post" else {}))
        assert resp.status_code == 401, (method, path)


async def test_permissions_follow_the_read_manage_split(client) -> None:
    owner, product = await _owner_with_catalog(client, "Perm Co", "perm-owner@example.com")
    tokens = {
        role: await _user_token(client, owner, f"{role.value.lower()}@perm.example.com", role)
        for role in (Role.MANAGER, Role.STAFF, Role.READ_ONLY, Role.TECHNICIAN, Role.ACCOUNTANT)
    }
    pid = product["id"]
    # reading: manager, staff, read-only (and owner) may; technician and accountant may not
    for role in (Role.MANAGER, Role.STAFF, Role.READ_ONLY):
        assert (await client.get(f"{BASE}/products", headers=_h(tokens[role]))).status_code == 200, role
        assert (await client.get(f"{BASE}/suppliers", headers=_h(tokens[role]))).status_code == 200, role
        assert (await client.get(f"{BASE}/products/{pid}", headers=_h(tokens[role]))).status_code == 200, role
    for role in (Role.TECHNICIAN, Role.ACCOUNTANT):
        assert (await client.get(f"{BASE}/products", headers=_h(tokens[role]))).status_code == 403, role
    # writing: only manager (and owner/admin)
    new_supplier = {"name": "Second Supply"}
    for role in (Role.STAFF, Role.READ_ONLY, Role.TECHNICIAN, Role.ACCOUNTANT):
        h = _h(tokens[role])
        assert (await client.post(f"{BASE}/suppliers", json=new_supplier, headers=h)).status_code == 403, role
        assert (await client.post(f"{BASE}/products", json={"name": "Nope", "skus": [{"sku_code": "N-1", "price": "1.00", "supplier": SUPPLIER}]}, headers=h)).status_code == 403, role
        assert (await client.post(f"{BASE}/products/{pid}/publish", headers=h)).status_code == 403, role
        assert (await client.post(f"{BASE}/products/{pid}/archive", headers=h)).status_code == 403, role
        assert (await client.patch(f"{BASE}/skus/{product['skus'][0]['id']}/availability", json={"availability": "AVAILABLE"}, headers=h)).status_code == 403, role
        assert (await client.post(f"{BASE}/catalog/import/preview", json={"csv_text": _csv(_row("P-1"))}, headers=h)).status_code == 403, role
        assert (await client.post(f"{BASE}/catalog/import/commit", json={"csv_text": _csv(_row("P-1")), "token": "x"}, headers=h)).status_code == 403, role
    assert (await client.post(f"{BASE}/suppliers", json=new_supplier, headers=_h(tokens[Role.MANAGER]))).status_code == 200
    assert (await client.post(f"{BASE}/catalog/import/preview", json={"csv_text": _csv(_row("P-1"))}, headers=_h(tokens[Role.MANAGER]))).status_code == 200


async def test_the_staff_flow_over_http_with_costs_and_the_publish_gate(client) -> None:
    owner, product = await _owner_with_catalog(client, "Flow Co", "flow@example.com")
    h = _h(owner)
    assert product["status"] == "DRAFT" and product["publish_problems"]
    sku = product["skus"][0]
    assert sku["supplier_cost"] == "6.00" and sku["margin"] == "4.00" and sku["availability"] == "UNKNOWN"  # staff see cost and margin

    listing = (await client.get(f"{BASE}/products?q=tea", headers=h)).json()["products"]
    assert [p["id"] for p in listing] == [product["id"]]
    assert (await client.get(f"{BASE}/products?status=ACTIVE", headers=h)).json()["products"] == []
    assert (await client.get(f"{BASE}/products?status=WHATEVER", headers=h)).status_code == 422
    assert (await client.get(f"{BASE}/products/{uuid.uuid4()}", headers=h)).status_code == 404

    avail = await client.patch(f"{BASE}/skus/{sku['id']}/availability", json={"availability": "AVAILABLE", "quantity": 12}, headers=h)
    assert avail.status_code == 200 and avail.json()["sku"]["availability"] == "AVAILABLE"
    assert (await client.patch(f"{BASE}/skus/{sku['id']}/availability", json={"availability": "AVAILABLE", "quantity": 0}, headers=h)).status_code == 422
    assert (await client.patch(f"{BASE}/skus/{uuid.uuid4()}/availability", json={"availability": "AVAILABLE"}, headers=h)).status_code == 404

    blocked = await client.post(f"{BASE}/products/{product['id']}/publish", headers=h)  # still marked perishable
    assert blocked.status_code == 422 and any("non-perishable" in p for p in blocked.json()["detail"]["problems"])
    assert (await client.get(f"{BASE}/products/{product['id']}", headers=h)).json()["product"]["status"] == "DRAFT"

    fixed = await client.post(
        f"{BASE}/products",
        json={"name": "Demo Tea", "is_non_perishable": True, "skus": [{"sku_code": "T-1", "price": "10.00", "supplier": SUPPLIER, "supplier_cost": "6.00", "availability": "AVAILABLE"}]},
        headers=h,
    )
    assert fixed.status_code == 200 and fixed.json()["product"]["id"] == product["id"]
    live = await client.post(f"{BASE}/products/{product['id']}/publish", headers=h)
    assert live.status_code == 200 and live.json()["product"]["status"] == "ACTIVE"
    assert (await client.post(f"{BASE}/products/{product['id']}/archive", headers=h)).json()["product"]["status"] == "ARCHIVED"
    assert (await client.post(f"{BASE}/products/{product['id']}/publish", headers=h)).status_code == 422  # an archived product cannot go back on sale


async def test_bad_input_gets_a_422_with_reasons_not_a_404(client) -> None:
    owner = await _register(client, "Input Co", "input@example.com")
    h = _h(owner)
    await client.post(f"{BASE}/suppliers", json={"name": SUPPLIER}, headers=h)
    bad = await client.post(f"{BASE}/products", json={"name": "X Item", "skus": [{"sku_code": "X-1", "price": "1.00", "supplier": "Ghost"}]}, headers=h)
    assert bad.status_code == 422 and "unknown supplier" in bad.text
    assert (await client.post(f"{BASE}/products", json={"name": "X Item", "skus": [{"sku_code": "X-1", "price": "free", "supplier": SUPPLIER}]}, headers=h)).status_code == 422
    assert (await client.post(f"{BASE}/products", json={"name": "X Item", "skus": []}, headers=h)).status_code == 422
    assert (await client.post(f"{BASE}/suppliers", json={"name": "x"}, headers=h)).status_code == 422
    dup = await client.post(f"{BASE}/suppliers", json={"name": SUPPLIER.upper()}, headers=h)
    assert dup.status_code == 200 and dup.json()["deduplicated"] is True


async def test_the_import_flow_preview_then_commit_over_http(client) -> None:
    owner = await _register(client, "Import Co", "import@example.com")
    h = _h(owner)
    await client.post(f"{BASE}/suppliers", json={"name": SUPPLIER}, headers=h)
    text = _csv(_row("I-1", "Import One"), _row("I-2", "Import Two", price="5.50"))

    preview = (await client.post(f"{BASE}/catalog/import/preview", json={"csv_text": text}, headers=h)).json()
    assert preview["ok"] is True and preview["committed"] is False and preview["token"]
    assert preview["summary"]["skus_created"] == 2 and [r["action"] for r in preview["rows"]] == ["create", "create"]
    assert (await client.get(f"{BASE}/products", headers=h)).json()["products"] == []  # a preview writes nothing

    wrong = await client.post(f"{BASE}/catalog/import/commit", json={"csv_text": text, "token": "not-the-token"}, headers=h)
    assert wrong.status_code == 409
    edited = await client.post(f"{BASE}/catalog/import/commit", json={"csv_text": text + "\n", "token": preview["token"]}, headers=h)
    assert edited.status_code == 409
    assert (await client.post(f"{BASE}/catalog/import/commit", json={"csv_text": text}, headers=h)).status_code == 409  # no token at all

    done = await client.post(f"{BASE}/catalog/import/commit", json={"csv_text": text, "token": preview["token"]}, headers=h)
    assert done.status_code == 200 and done.json()["committed"] is True
    products = (await client.get(f"{BASE}/products", headers=h)).json()["products"]
    assert {p["status"] for p in products} == {"DRAFT"} and len(products) == 2
    again = (await client.post(f"{BASE}/catalog/import/commit", json={"csv_text": text, "token": preview["token"]}, headers=h)).json()
    assert again["summary"]["skus_unchanged"] == 2  # re-importing the same file is harmless


async def test_a_file_with_one_bad_row_is_rejected_whole_over_http(client) -> None:
    owner = await _register(client, "Reject Co", "reject@example.com")
    h = _h(owner)
    await client.post(f"{BASE}/suppliers", json={"name": SUPPLIER}, headers=h)
    text = _csv(_row("OK-1", "Fine One"), _row("BAD-1", "Bad Price", price="free"), _row("OK-2", "Fine Two"))
    preview = (await client.post(f"{BASE}/catalog/import/preview", json={"csv_text": text}, headers=h)).json()
    assert preview["ok"] is False and preview["summary"]["rejected"] == 1
    bad_row = next(r for r in preview["rows"] if r["sku_code"] == "BAD-1")
    assert bad_row["action"] == "rejected" and "plain number" in " ".join(bad_row["problems"])
    commit = await client.post(f"{BASE}/catalog/import/commit", json={"csv_text": text, "token": preview["token"]}, headers=h)
    assert commit.status_code == 422 and commit.json()["detail"]["report"]["summary"]["rejected"] == 1
    assert (await client.get(f"{BASE}/products", headers=h)).json()["products"] == []  # the two good rows were not written either
    empty = (await client.post(f"{BASE}/catalog/import/preview", json={"csv_text": ""}, headers=h)).json()
    assert empty["ok"] is False and empty["token"] is None and empty["file_problems"]


async def test_one_business_never_sees_or_changes_anothers_catalog(client) -> None:
    owner_a, product = await _owner_with_catalog(client, "Isolation A", "iso-a@example.com")
    owner_b = await _register(client, "Isolation B", "iso-b@example.com")
    hb = _h(owner_b)
    assert (await client.get(f"{BASE}/products", headers=hb)).json()["products"] == []
    assert (await client.get(f"{BASE}/suppliers", headers=hb)).json()["suppliers"] == []
    assert (await client.get(f"{BASE}/products/{product['id']}", headers=hb)).status_code == 404
    assert (await client.post(f"{BASE}/products/{product['id']}/publish", headers=hb)).status_code == 404
    assert (await client.post(f"{BASE}/products/{product['id']}/archive", headers=hb)).status_code == 404
    assert (await client.patch(f"{BASE}/skus/{product['skus'][0]['id']}/availability", json={"availability": "AVAILABLE"}, headers=hb)).status_code == 404
    uses_a_supplier = await client.post(f"{BASE}/products", json={"name": "B Tea", "skus": [{"sku_code": "B-1", "price": "1.00", "supplier": SUPPLIER}]}, headers=hb)
    assert uses_a_supplier.status_code == 422  # B has no supplier of that name; A's is not reachable
    mine = (await client.get(f"{BASE}/products/{product['id']}", headers=_h(owner_a))).json()["product"]
    assert mine["status"] == "DRAFT" and mine["skus"][0]["availability"] == "UNKNOWN"  # and nothing of A's changed


async def test_the_actions_a_person_takes_are_audited_in_the_same_transaction(client) -> None:
    owner, product = await _owner_with_catalog(client, "Audit Co", "audit@example.com")
    h = _h(owner)
    tenant = await _tenant_id(client, owner)
    await client.patch(f"{BASE}/skus/{product['skus'][0]['id']}/availability", json={"availability": "AVAILABLE"}, headers=h)
    await client.post(f"{BASE}/products", json={"name": "Demo Tea", "is_non_perishable": True, "skus": [{"sku_code": "T-1", "price": "10.00", "supplier": SUPPLIER}]}, headers=h)
    await client.post(f"{BASE}/products/{product['id']}/publish", headers=h)
    text = _csv(_row("AU-1", "Audited Import"))
    token = (await client.post(f"{BASE}/catalog/import/preview", json={"csv_text": text}, headers=h)).json()["token"]
    await client.post(f"{BASE}/catalog/import/commit", json={"csv_text": text, "token": token}, headers=h)
    await client.post(f"{BASE}/products/{product['id']}/archive", headers=h)
    async with async_session_maker() as session:
        actions = [a for (a,) in (await session.execute(select(AuditLog.action).where(AuditLog.tenant_id == tenant))).all()]
    for expected in ("dropshipping.sku.availability_set", "dropshipping.product.published", "dropshipping.catalog.imported", "dropshipping.product.archived"):
        assert expected in actions, expected
    assert "tool.execute:dropshipping.upsert_product" in actions and "tool.execute:dropshipping.create_supplier" in actions  # the registry audited its own
