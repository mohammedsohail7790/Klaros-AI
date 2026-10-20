"""Dropshipping store, branch 2: the catalog service, the CSV import, the demo seed and the tool policies.

The API (permissions, status codes, tenant isolation over HTTP) is covered in tests/test_dropshipping_api.py.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.db.session import async_session_maker
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.dropshipping import InventorySnapshot, Product, ProductStatus, Sku, Supplier
from app.models.rbac import Role
from app.services.dropshipping_catalog_import import (
    MAX_CSV_CHARS,
    MAX_CSV_ROWS,
    CatalogValidationError,
    parse_catalog_csv,
    preview_token,
    token_matches,
)
from app.services.dropshipping_catalog_service import (
    CatalogConflictError,
    CatalogNotFoundError,
    DropshippingCatalogService,
    ImportRejectedError,
    ImportTokenError,
    PublishBlockedError,
)
from app.tools.base import ExecutionContext
from app.tools.errors import ToolApprovalRequiredError, ToolNotFoundError, ToolPermissionError, ToolValidationError
from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy

HEADER = "sku_code,product_name,description,category,price,currency,supplier,supplier_sku,supplier_cost,weight_grams,availability,non_perishable,image_url"
SUPPLIER = "Acme Supply"


@pytest.fixture
def service() -> DropshippingCatalogService:
    return DropshippingCatalogService(async_session_maker)


def _sku(code: str = "A-1", price: str = "10.00", supplier: str = SUPPLIER, **over) -> dict:
    return {"sku_code": code, "price": price, "supplier": supplier, **over}


def _product(name: str = "Demo Tea", skus: list | None = None, **over) -> dict:
    return {"name": name, "skus": skus if skus is not None else [_sku()], **over}


def _csv(*rows: str) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


def _row(code: str, name: str = "Demo Tea", price: str = "10.00", supplier: str = SUPPLIER, **over) -> str:
    cols = {"sku_code": code, "product_name": name, "description": "", "category": "", "price": price, "currency": "", "supplier": supplier,
            "supplier_sku": "", "supplier_cost": "", "weight_grams": "", "availability": "", "non_perishable": "yes", "image_url": ""}
    cols.update(over)
    return ",".join(cols[c] for c in HEADER.split(","))


async def _counts(tenant: uuid.UUID) -> tuple[int, int]:
    async with async_session_maker() as session:
        products = (await session.execute(select(func.count()).select_from(Product).where(Product.tenant_id == tenant))).scalar_one()
        skus = (await session.execute(select(func.count()).select_from(Sku).where(Sku.tenant_id == tenant))).scalar_one()
    return products, skus


async def _audit_actions(tenant: uuid.UUID) -> list[str]:
    async with async_session_maker() as session:
        return [a for (a,) in (await session.execute(select(AuditLog.action).where(AuditLog.tenant_id == tenant))).all()]


# --- suppliers -----------------------------------------------------------------------------------------------------------------------------------------


async def test_a_supplier_name_is_deduplicated_ignoring_case_and_validated(service) -> None:
    t = uuid.uuid4()
    first, dup1 = await service.create_supplier(t, "Acme Supply")
    again, dup2 = await service.create_supplier(t, "ACME supply")
    assert (dup1, dup2) == (False, True) and again.id == first.id
    assert first.integration_type == "MANUAL" and first.status == "ACTIVE"
    with pytest.raises(CatalogValidationError):
        await service.create_supplier(t, "x")
    with pytest.raises(CatalogValidationError):
        await service.create_supplier(t, "Valid Name", integration_type="TELEPATHY")
    with pytest.raises(CatalogValidationError):
        await service.create_supplier(t, "Valid Name", contact_email="not-an-address")
    other, dup3 = await service.create_supplier(uuid.uuid4(), "Acme Supply")  # another business: its own supplier
    assert dup3 is False and other.id != first.id


# --- one product -----------------------------------------------------------------------------------------------------------------------------------------


async def test_upsert_creates_a_draft_and_is_idempotent_and_updates(service) -> None:
    t = uuid.uuid4()
    await service.create_supplier(t, SUPPLIER)
    created = await service.upsert_product(t, _product(skus=[_sku(supplier_cost="6.00")]))
    assert created["status"] == "DRAFT" and created["slug"] == "demo-tea" and created["is_non_perishable"] is False
    sku = created["skus"][0]
    assert sku["availability"] == "UNKNOWN" and sku["margin"] == "4.00" and sku["currency"] == "USD"

    again = await service.upsert_product(t, _product(skus=[_sku(supplier_cost="6.00")]))
    assert again["id"] == created["id"] and again["skus"][0]["id"] == sku["id"]
    assert await _counts(t) == (1, 1)

    updated = await service.upsert_product(t, _product(skus=[_sku(price="12.50", supplier_cost="6.00")], description="Now with a description"))
    assert updated["skus"][0]["price"] == "12.50" and updated["description"] == "Now with a description" and updated["id"] == created["id"]
    assert await _counts(t) == (1, 1)


async def test_upsert_refuses_bad_input_and_lists_every_problem(service) -> None:
    t = uuid.uuid4()
    await service.create_supplier(t, SUPPLIER)
    bad_inputs = [
        (_product(skus=[_sku(price="0")]), "greater than 0"),
        (_product(skus=[_sku(price="-3.00")]), "plain number"),
        (_product(skus=[_sku(price="1,000.00")]), "thousands separator"),
        (_product(skus=[_sku(price="$10")]), "plain number"),
        (_product(skus=[_sku(currency="EUR")]), "USD"),
        (_product(skus=[_sku(weight_grams="heavy")]), "whole number"),
        (_product(skus=[_sku(availability="MAYBE")]), "availability"),
        (_product(skus=[_sku(code="bad code!")]), "sku_code"),
        (_product(image_url="http://insecure.example/x.png"), "https"),
        (_product(skus=[]), "at least one SKU"),
        (_product(skus=[_sku("DUP"), _sku("DUP")]), "appears twice"),
        (_product(skus=[_sku(supplier="No Such Supplier")]), "unknown supplier"),
    ]
    for raw, expected in bad_inputs:
        with pytest.raises(CatalogValidationError) as info:
            await service.upsert_product(t, raw)
        assert expected in " ".join(info.value.problems), (raw, info.value.problems)
    assert await _counts(t) == (0, 0)  # every refusal wrote nothing

    with pytest.raises(CatalogValidationError) as info:
        await service.upsert_product(t, _product(name="", skus=[_sku(price="0"), _sku("B-2", currency="EUR")]))
    assert len(info.value.problems) >= 3  # all the problems at once, not one at a time


async def test_a_sku_code_cannot_move_to_a_different_product(service) -> None:
    t = uuid.uuid4()
    await service.create_supplier(t, SUPPLIER)
    await service.upsert_product(t, _product("First Product", [_sku("SHARED-1")]))
    with pytest.raises(CatalogValidationError) as info:
        await service.upsert_product(t, _product("Second Product", [_sku("SHARED-1")]))
    assert "different product" in " ".join(info.value.problems)


async def test_businesses_are_isolated_from_each_other(service) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    await service.create_supplier(a, SUPPLIER)
    created = await service.upsert_product(a, _product())
    assert await service.search_products(b) == []
    with pytest.raises(CatalogNotFoundError):
        await service.get_product(b, uuid.UUID(created["id"]))
    with pytest.raises(CatalogNotFoundError):
        await service.publish_product(b, uuid.UUID(created["id"]))
    with pytest.raises(CatalogNotFoundError):
        await service.set_availability(b, uuid.UUID(created["skus"][0]["id"]), "AVAILABLE")
    with pytest.raises(CatalogValidationError) as info:  # B cannot use A's supplier, by name or by id
        await service.upsert_product(b, _product(skus=[_sku(supplier=SUPPLIER)]))
    assert "unknown supplier" in " ".join(info.value.problems)
    await service.create_supplier(b, SUPPLIER)
    await service.upsert_product(b, _product())  # the same slug and SKU code are free in another business
    assert await _counts(a) == (1, 1) and await _counts(b) == (1, 1)


# --- availability and publishing ------------------------------------------------------------------------------------------------------------------


async def test_availability_updates_the_sku_and_appends_a_snapshot_and_an_audit_row(service) -> None:
    t = uuid.uuid4()
    await service.create_supplier(t, SUPPLIER)
    product = await service.upsert_product(t, _product())
    sku_id = uuid.UUID(product["skus"][0]["id"])
    out = await service.set_availability(t, sku_id, "available", quantity=7, actor_id=uuid.uuid4())
    assert out["availability"] == "AVAILABLE"
    await service.set_availability(t, sku_id, "OUT_OF_STOCK", quantity=0)
    async with async_session_maker() as session:
        snaps = list((await session.execute(select(InventorySnapshot).where(InventorySnapshot.sku_id == sku_id).order_by(InventorySnapshot.observed_at))).scalars())
    assert [s.quantity for s in snaps] == [7, 0] and {s.source for s in snaps} == {"MANUAL"}
    assert (await _audit_actions(t)).count("dropshipping.sku.availability_set") == 2
    for args in (("AVAILABLE", 0), ("OUT_OF_STOCK", 3), ("MAYBE", None), ("AVAILABLE", -1)):
        with pytest.raises(CatalogValidationError):
            await service.set_availability(t, sku_id, args[0], quantity=args[1])


async def test_publishing_enforces_the_rules_and_is_idempotent(service) -> None:
    t = uuid.uuid4()
    await service.create_supplier(t, SUPPLIER)
    perishable = await service.upsert_product(t, _product())  # is_non_perishable defaults to False
    with pytest.raises(PublishBlockedError) as info:
        await service.publish_product(t, uuid.UUID(perishable["id"]), actor_id=uuid.uuid4())
    assert "non-perishable" in " ".join(info.value.problems)
    assert perishable["publish_problems"]  # the draft itself says what is missing
    assert (await service.get_product(t, uuid.UUID(perishable["id"])))["status"] == "DRAFT"

    ok = await service.upsert_product(t, _product(is_non_perishable=True))
    assert ok["publish_problems"] == []
    live = await service.publish_product(t, uuid.UUID(ok["id"]), actor_id=uuid.uuid4())
    assert live["status"] == "ACTIVE"
    assert (await service.publish_product(t, uuid.UUID(ok["id"])))["status"] == "ACTIVE"  # idempotent
    assert (await _audit_actions(t)).count("dropshipping.product.published") == 1
    assert [p["slug"] for p in await service.search_products(t, status="ACTIVE")] == ["demo-tea"]
    assert await service.search_products(t, status="DRAFT") == []

    archived = await service.archive_product(t, uuid.UUID(ok["id"]), actor_id=uuid.uuid4())
    assert archived["status"] == "ARCHIVED"
    with pytest.raises(PublishBlockedError):
        await service.publish_product(t, uuid.UUID(ok["id"]))


async def test_search_matches_name_slug_and_category(service) -> None:
    t = uuid.uuid4()
    await service.create_supplier(t, SUPPLIER)
    await service.upsert_product(t, _product("Green Tea", [_sku("T-1")], category="Beverages"))
    await service.upsert_product(t, _product("Oat Biscuits", [_sku("B-1")], category="Snacks"))
    assert [p["name"] for p in await service.search_products(t, q="tea")] == ["Green Tea"]
    assert [p["name"] for p in await service.search_products(t, q="snack")] == ["Oat Biscuits"]
    assert [p["slug"] for p in await service.search_products(t, q="oat-bis")] == ["oat-biscuits"]
    assert len(await service.search_products(t)) == 2


# --- the CSV import ----------------------------------------------------------------------------------------------------------------------------------------


async def test_preview_writes_nothing_and_reports_what_a_commit_would_do(service) -> None:
    t = uuid.uuid4()
    await service.create_supplier(t, SUPPLIER)
    text = _csv(_row("C-1", "Alpha"), _row("C-2", "Beta", price="5.50"))
    report = await service.preview_import(t, text)
    assert report.ok and not report.committed and report.token == preview_token(t, text)
    assert [r.action for r in report.rows] == ["create", "create"]
    assert (report.skus_created, report.products_created, report.rejected) == (2, 2, 0)
    assert await _counts(t) == (0, 0)
    assert "dropshipping.catalog.imported" not in await _audit_actions(t)


async def test_commit_needs_the_token_of_exactly_this_file_for_this_business(service) -> None:
    t, other = uuid.uuid4(), uuid.uuid4()
    await service.create_supplier(t, SUPPLIER)
    text = _csv(_row("C-1", "Alpha"))
    token = preview_token(t, text)
    for bad in ("", "nope", preview_token(other, text), preview_token(t, text + " ")):
        with pytest.raises(ImportTokenError):
            await service.commit_import(t, text, bad)
    with pytest.raises(ImportTokenError):  # the file was edited after the preview
        await service.commit_import(t, _csv(_row("C-1", "Alpha", price="1.00")), token)
    assert await _counts(t) == (0, 0)
    assert token_matches(t, text, token) and not token_matches(other, text, token)
    report = await service.commit_import(t, text, token, actor_id=uuid.uuid4())
    assert report.committed and await _counts(t) == (1, 1)


async def test_the_import_is_all_or_nothing(service) -> None:
    t = uuid.uuid4()
    await service.create_supplier(t, SUPPLIER)
    text = _csv(_row("G-1", "Good One"), _row("G-2", "Good Two"), _row("X-1", "Bad Supplier", supplier="Ghost Supply"), _row("G-3", "Good Three"))
    preview = await service.preview_import(t, text)
    assert not preview.ok and preview.rejected == 1
    assert [r.action for r in preview.rows] == ["create", "create", "rejected", "create"]
    assert "unknown supplier" in " ".join(preview.rows[2].problems)
    with pytest.raises(ImportRejectedError) as info:
        await service.commit_import(t, text, preview.token)
    assert info.value.report.rejected == 1
    assert await _counts(t) == (0, 0)  # the three good rows were NOT written either
    assert "dropshipping.catalog.imported" not in await _audit_actions(t)


async def test_a_reimport_changes_nothing_and_a_changed_row_updates(service) -> None:
    t = uuid.uuid4()
    await service.create_supplier(t, SUPPLIER)
    text = _csv(_row("R-1", "Reimport Me"), _row("R-2", "Reimport Too"))
    await service.commit_import(t, text, preview_token(t, text))
    snapshot = await _counts(t)
    again = await service.commit_import(t, text, preview_token(t, text))
    assert (again.skus_created, again.skus_updated, again.skus_unchanged) == (0, 0, 2) and [r.action for r in again.rows] == ["unchanged"] * 2
    assert await _counts(t) == snapshot == (2, 2)
    changed = _csv(_row("R-1", "Reimport Me", price="11.00"), _row("R-2", "Reimport Too"))
    third = await service.commit_import(t, changed, preview_token(t, changed))
    assert (third.skus_updated, third.skus_unchanged) == (1, 1)
    assert (await service.search_products(t, q="reimport me"))[0]["skus"][0]["price"] == "11.00"
    assert (await _audit_actions(t)).count("dropshipping.catalog.imported") == 3


async def test_rows_with_the_same_slug_are_variants_of_one_product_and_products_land_as_drafts(service) -> None:
    t = uuid.uuid4()
    await service.create_supplier(t, SUPPLIER)
    text = _csv(_row("V-S", "Rice Pack", price="4.00"), _row("V-L", "Rice Pack", price="7.00"))
    report = await service.commit_import(t, text, preview_token(t, text))
    assert (report.products_created, report.skus_created) == (1, 2)
    (product,) = await service.search_products(t)
    assert product["status"] == "DRAFT" and {s["sku_code"] for s in product["skus"]} == {"V-S", "V-L"}
    # variant rows must agree with the first row about the product: identical or empty is fine, a different value is not
    agree = _csv(_row("V-S", "Rice Pack", description="one"), _row("V-L", "Rice Pack", description="one"), _row("V-X", "Rice Pack"))
    parsed, _ = parse_catalog_csv(agree)
    assert all(not r.problems for r in parsed)
    differ = _csv(_row("V-S", "Rice Pack", description="one"), _row("V-L", "Rice Pack", description="two"))
    parsed2, _ = parse_catalog_csv(differ)
    assert not parsed2[0].problems and "differs from row 2" in " ".join(parsed2[1].problems)
    late = _csv(_row("V-S", "Rice Pack"), _row("V-L", "Rice Pack", description="added later"))  # the first row left it empty: ambiguous, so refused
    parsed3, _ = parse_catalog_csv(late)
    assert "differs from row 2" in " ".join(parsed3[1].problems)


async def test_an_import_cannot_make_a_product_on_sale_perishable_or_touch_an_archived_one(service) -> None:
    t = uuid.uuid4()
    await service.create_supplier(t, SUPPLIER)
    text = _csv(_row("L-1", "Live Item"))
    await service.commit_import(t, text, preview_token(t, text))
    (product,) = await service.search_products(t)
    await service.publish_product(t, uuid.UUID(product["id"]))
    perishable = _csv(_row("L-1", "Live Item", non_perishable="no"))
    report = await service.preview_import(t, perishable)
    assert not report.ok and "non-perishable" in " ".join(report.rows[0].problems)
    await service.archive_product(t, uuid.UUID(product["id"]))
    assert "archived" in " ".join((await service.preview_import(t, text)).rows[0].problems)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", "empty", id="empty"),
        pytest.param("   \n  ", "empty", id="blank"),
        pytest.param(_csv(_row("A-1")).replace("price", "cost", 1), "Missing required", id="missing-required-column"),
        pytest.param(_csv(_row("A-1")).replace("image_url", "photo", 1), "Unknown column", id="unknown-column"),
        pytest.param(HEADER + ",sku_code\nA-1\n", "repeats a column", id="repeated-column"),
        pytest.param("x" * (MAX_CSV_CHARS + 1), "larger than", id="over-size-cap"),
        pytest.param(HEADER + "\n" + "\n".join(_row(f"S-{i}") for i in range(MAX_CSV_ROWS + 1)), "more than", id="over-row-cap"),
    ],
)
def test_file_level_problems_reject_the_whole_file(text, expected) -> None:
    rows, problems = parse_catalog_csv(text)
    assert rows == [] and expected.lower() in " ".join(problems).lower()


def test_row_level_rules_formula_injection_wrong_cell_count_duplicates_and_a_bom() -> None:
    rows, problems = parse_catalog_csv("﻿" + _csv(
        _row("OK-1"),
        _row("INJ-1", "=HYPERLINK(\"http://evil.example\")"),
        _row("INJ-2", "Fine Name", description="@SUM(1+1)"),
        _row("INJ-3", "Plus Name", category="+cmd"),
        _row("DUP-1"),
        _row("DUP-1", "Other Name"),
        "SHORT-1,Only,Three",
    ))
    assert problems == []
    by_code = {r.sku_code: r for r in rows}
    assert not by_code["OK-1"].problems
    for code in ("INJ-1", "INJ-2", "INJ-3"):
        assert "may not start with" in " ".join(by_code[code].problems), code
    assert any("already appears on row" in p for r in rows if r.sku_code == "DUP-1" for p in r.problems)
    assert rows[-1].sku_code is None and "columns" in " ".join(rows[-1].problems)  # a row with the wrong number of cells cannot be read, so it has no code


async def test_a_file_problem_has_no_token_and_cannot_be_committed(service) -> None:
    t = uuid.uuid4()
    report = await service.preview_import(t, "")
    assert not report.ok and report.token is None and report.file_problems
    with pytest.raises(ImportTokenError):
        await service.commit_import(t, "", "")


# --- the demo seed ---------------------------------------------------------------------------------------------------------------------------------------


async def test_the_demo_seed_is_explicit_fictional_idempotent_and_never_publishes(service) -> None:
    from scripts.seed_dropshipping_demo import DEMO_SUPPLIER, load_demo_csv, seed

    t = uuid.uuid4()
    preview = await seed(t, commit=False)
    assert preview["committed"] is False and await _counts(t) == (0, 0)  # a preview writes nothing, not even the supplier
    done = await seed(t, commit=True)
    assert done["committed"] and done["summary"]["products_created"] == 12 and await _counts(t) == (12, 12)
    products = await service.search_products(t, limit=200)
    assert {p["status"] for p in products} == {"DRAFT"}  # nothing is published
    assert all("FICTIONAL" in (p["description"] or "") for p in products)
    assert all(s["supplier"] == DEMO_SUPPLIER and s["availability"] == "UNKNOWN" for p in products for s in p["skus"])
    again = await seed(t, commit=True)
    assert again["summary"]["skus_unchanged"] == 12 and await _counts(t) == (12, 12)
    assert "FICTIONAL" in load_demo_csv()
    async with async_session_maker() as session:
        suppliers = list((await session.execute(select(Supplier).where(Supplier.tenant_id == t))).scalars())
    assert [s.name for s in suppliers] == [DEMO_SUPPLIER] and "FICTIONAL" in (suppliers[0].notes or "")


# --- the tools and their policies ------------------------------------------------------------------------------------------------------------------------


def test_every_dropshipping_tool_is_registered_with_the_agreed_policy(tool_registry) -> None:
    expected = {
        "dropshipping.search_products": ActionPolicy.AUTO,
        "dropshipping.create_supplier": ActionPolicy.AUTO,
        "dropshipping.upsert_product": ActionPolicy.AUTO,
        "dropshipping.set_availability": ActionPolicy.AUTO,
        "dropshipping.publish_product": ActionPolicy.APPROVAL_REQUIRED,
        "dropshipping.import_catalog": ActionPolicy.APPROVAL_REQUIRED,
    }
    for name, policy in expected.items():
        assert DEFAULT_TOOL_POLICIES[name] == policy, name
        assert tool_registry.get(name).name == name
    assert tool_registry.get("dropshipping.search_products").supports_idempotency is True
    assert all(tool_registry.get(n).supports_idempotency is False for n in expected if n != "dropshipping.search_products")


def _ctx(tenant: uuid.UUID, role: Role = Role.OWNER) -> ExecutionContext:
    return ExecutionContext(tenant_id=tenant, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def test_publish_and_import_tools_need_approval_while_the_catalog_writes_do_not(tool_registry, service) -> None:
    t = uuid.uuid4()
    created = await tool_registry.execute("dropshipping.create_supplier", {"name": SUPPLIER}, _ctx(t))
    assert created.deduplicated is False
    upserted = await tool_registry.execute("dropshipping.upsert_product", {"name": "Tool Tea", "skus": [{"sku_code": "TT-1", "price": "9.99", "supplier": SUPPLIER}], "is_non_perishable": True}, _ctx(t))
    product_id = upserted.product["id"]
    found = await tool_registry.execute("dropshipping.search_products", {"q": "tool"}, _ctx(t))
    assert [p["id"] for p in found.products] == [product_id]
    with pytest.raises(ToolApprovalRequiredError):
        await tool_registry.execute("dropshipping.publish_product", {"product_id": product_id}, _ctx(t))
    text = _csv(_row("TI-1", "Tool Import"))
    with pytest.raises(ToolApprovalRequiredError):
        await tool_registry.execute("dropshipping.import_catalog", {"csv_text": text, "token": preview_token(t, text)}, _ctx(t))
    assert (await service.get_product(t, uuid.UUID(product_id)))["status"] == "DRAFT"  # the approval gate stopped the publish
    assert await _counts(t) == (1, 1)  # and the import


async def test_the_catalog_tools_respect_the_read_manage_split(tool_registry) -> None:
    t = uuid.uuid4()
    await tool_registry.execute("dropshipping.create_supplier", {"name": SUPPLIER}, _ctx(t, Role.MANAGER))
    for role in (Role.READ_ONLY, Role.STAFF, Role.TECHNICIAN):
        with pytest.raises(ToolPermissionError):
            await tool_registry.execute("dropshipping.create_supplier", {"name": "Another Supplier"}, _ctx(t, role))
    for role in (Role.READ_ONLY, Role.STAFF, Role.MANAGER, Role.OWNER):
        await tool_registry.execute("dropshipping.search_products", {}, _ctx(t, role))  # reading is allowed
    with pytest.raises(ToolPermissionError):
        await tool_registry.execute("dropshipping.search_products", {}, _ctx(t, Role.TECHNICIAN))


async def test_business_rule_failures_become_the_tool_errors_the_http_mapping_understands(tool_registry) -> None:
    t = uuid.uuid4()
    await tool_registry.execute("dropshipping.create_supplier", {"name": SUPPLIER}, _ctx(t))
    with pytest.raises(ToolValidationError, match="unknown supplier"):  # -> HTTP 422, not a misleading 404
        await tool_registry.execute("dropshipping.upsert_product", {"name": "X Item", "skus": [{"sku_code": "X-1", "price": "1.00", "supplier": "Ghost"}]}, _ctx(t))
    with pytest.raises(ToolNotFoundError, match="not found"):  # -> HTTP 404
        await tool_registry.execute("dropshipping.set_availability", {"sku_id": str(uuid.uuid4()), "availability": "AVAILABLE"}, _ctx(t))
    with pytest.raises(ToolValidationError):  # a malformed body is refused by the tool's own schema before any service call
        await tool_registry.execute("dropshipping.upsert_product", {"name": "X Item", "skus": []}, _ctx(t))
    assert not issubclass(CatalogConflictError, ValueError)
