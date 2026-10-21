"""Dropshipping store, branch 2: the catalog service (suppliers, products, SKUs, availability, publishing, CSV import).

Domain logic lives here, never in route handlers or tools: both `app/api/v1/dropshipping.py` and `app/tools/builtin/dropshipping_tools.py` call this one service,
like every other domain. Input rules are in `dropshipping_catalog_import.py` (pure functions); this file adds the database rules: tenant ownership, uniqueness,
upsert by SKU code and product slug, the publish gate, and an import that is previewed first and then committed all-or-nothing in ONE transaction.

Audit: the operations a person performs directly through the staff API (publish, archive, import commit, availability) write their own `AuditLog` row inside
the same transaction as the change. The agent-callable tools go through `ToolRegistry`, which audits them itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.dropshipping import (
    InventorySnapshot,
    InventorySource,
    Product,
    ProductStatus,
    Sku,
    SkuAvailability,
    SkuStatus,
    Supplier,
    SupplierIntegration,
)
from app.services.dropshipping_catalog_import import (
    CatalogValidationError,
    ProductSpec,
    SkuSpec,
    normalize_product,
    normalize_sku,
    parse_catalog_csv,
    preview_token,
    token_matches,
)


class CatalogNotFoundError(Exception):
    pass


class CatalogConflictError(Exception):
    pass


class PublishBlockedError(CatalogValidationError):
    """A product does not meet the rules to be sold. `problems` says which."""


class ImportTokenError(Exception):
    """The preview token does not match this file for this business: preview the file again."""


class ImportRejectedError(Exception):
    def __init__(self, report: "ImportReport") -> None:
        super().__init__("The import was rejected; nothing was written.")
        self.report = report


@dataclass
class RowResult:
    row: int
    sku_code: str | None
    action: str  # create | update | unchanged | rejected | valid (checked, but another row of the file blocks the import)
    problems: list[str] = field(default_factory=list)


@dataclass
class ImportReport:
    ok: bool
    token: str | None
    file_problems: list[str] = field(default_factory=list)
    rows: list[RowResult] = field(default_factory=list)
    skus_created: int = 0
    skus_updated: int = 0
    skus_unchanged: int = 0
    products_created: int = 0
    products_updated: int = 0
    rejected: int = 0
    committed: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "committed": self.committed, "token": self.token, "file_problems": self.file_problems,
            "summary": {
                "rows": len(self.rows), "skus_created": self.skus_created, "skus_updated": self.skus_updated, "skus_unchanged": self.skus_unchanged,
                "products_created": self.products_created, "products_updated": self.products_updated, "rejected": self.rejected,
            },
            "rows": [{"row": r.row, "sku_code": r.sku_code, "action": r.action, "problems": r.problems} for r in self.rows],
        }


@dataclass
class _Context:
    suppliers_by_id: dict[uuid.UUID, Supplier]
    suppliers_by_name: dict[str, Supplier]
    products_by_slug: dict[str, Product]
    skus_by_code: dict[str, Sku]


@dataclass
class _SkuPlan:
    spec: SkuSpec
    action: str
    supplier: Supplier | None


@dataclass
class _ProductPlan:
    spec: ProductSpec
    product_action: str
    skus: list[_SkuPlan]
    problems: list[tuple[str | None, str]]  # (sku_code or None for the product itself, sentence)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def publish_problems(product: Product, skus: list[Sku]) -> list[str]:
    """The rules for selling a product, in one place. An empty list means it may be published."""
    problems: list[str] = []
    active = [s for s in skus if s.status == SkuStatus.ACTIVE]
    if not active:
        problems.append("The product needs at least one active SKU.")
    for sku in active:
        if sku.price is None or Decimal(sku.price) <= 0:
            problems.append(f"SKU {sku.sku_code} needs a price above 0.")
        if sku.supplier_id is None:
            problems.append(f"SKU {sku.sku_code} needs a supplier.")
    if not product.is_non_perishable:
        problems.append("The product must be marked non-perishable: this store sells non-perishable food only.")
    if product.image_url and not product.image_url.lower().startswith("https://"):
        problems.append("The image URL must start with https://.")
    return problems


def sku_to_dict(sku: Sku, supplier: Supplier | None = None) -> dict:
    cost = Decimal(sku.supplier_cost) if sku.supplier_cost is not None else None
    price = Decimal(sku.price)
    return {
        "id": str(sku.id), "sku_code": sku.sku_code, "supplier_id": str(sku.supplier_id), "supplier": supplier.name if supplier else None,
        "supplier_sku": sku.supplier_sku, "price": str(price), "supplier_cost": str(cost) if cost is not None else None,
        "margin": str(price - cost) if cost is not None else None, "currency": sku.currency, "weight_grams": sku.weight_grams,
        "availability": sku.availability, "status": sku.status,
    }


def product_to_dict(product: Product, skus: list[Sku], suppliers: dict[uuid.UUID, Supplier] | None = None) -> dict:
    suppliers = suppliers or {}
    return {
        "id": str(product.id), "slug": product.slug, "name": product.name, "description": product.description, "category": product.category,
        "image_url": product.image_url, "is_non_perishable": product.is_non_perishable, "status": product.status,
        "skus": [sku_to_dict(s, suppliers.get(s.supplier_id)) for s in sorted(skus, key=lambda s: s.sku_code)],
        "publish_problems": publish_problems(product, skus) if product.status != ProductStatus.ACTIVE else [],
    }


def supplier_to_dict(s: Supplier) -> dict:
    return {
        "id": str(s.id), "name": s.name, "integration_type": s.integration_type, "contact_email": s.contact_email, "contact_phone": s.contact_phone,
        "notes": s.notes, "status": s.status,
        "sync": {
            "feed_url": s.feed_url, "markup_percent": str(s.markup_percent) if s.markup_percent is not None else None, "stock_buffer": s.stock_buffer,
            "max_price_change_percent": str(s.max_price_change_percent), "sync_enabled": bool(s.sync_enabled),
            "last_sync_at": s.last_sync_at.isoformat() if s.last_sync_at else None, "last_sync_summary": s.last_sync_summary,
        },
    }


def _audit(session, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, action: str, entity_type: str, entity_id: uuid.UUID | None, summary: dict) -> None:
    session.add(AuditLog(
        tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=actor_id, action=action, entity_type=entity_type, entity_id=entity_id,
        input_summary=summary, result="success",
    ))


class DropshippingCatalogService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # --- suppliers -----------------------------------------------------------------------------------------------------------------------------

    async def create_supplier(
        self, tenant_id: uuid.UUID, name: str, *, integration_type: str = SupplierIntegration.MANUAL.value, contact_email: str | None = None,
        contact_phone: str | None = None, notes: str | None = None,
    ) -> tuple[Supplier, bool]:
        """Returns (supplier, was_deduplicated). A supplier name is unique per business, compared without regard to case."""
        name = (name or "").strip()
        problems = []
        if not 2 <= len(name) <= 200:
            problems.append("name must be 2-200 characters")
        if integration_type not in {i.value for i in SupplierIntegration}:
            problems.append("integration_type must be MANUAL, CSV or API")
        if contact_email and ("@" not in contact_email or len(contact_email) > 255):
            problems.append("contact_email is not a valid address")
        if problems:
            raise CatalogValidationError(problems)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            existing = (await session.execute(select(Supplier).where(Supplier.tenant_id == tenant_id, func.lower(Supplier.name) == name.lower()))).scalar_one_or_none()
            if existing is not None:
                return existing, True
            supplier = Supplier(
                tenant_id=tenant_id, name=name, integration_type=integration_type, contact_email=contact_email or None,
                contact_phone=(contact_phone or None), notes=notes or None,
            )
            session.add(supplier)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = (await session.execute(select(Supplier).where(Supplier.tenant_id == tenant_id, func.lower(Supplier.name) == name.lower()))).scalar_one_or_none()
                if existing is None:
                    raise
                return existing, True
            return supplier, False

    async def list_suppliers(self, tenant_id: uuid.UUID) -> list[Supplier]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            return list((await session.execute(select(Supplier).where(Supplier.tenant_id == tenant_id).order_by(Supplier.name))).scalars())

    # --- reading the catalog -------------------------------------------------------------------------------------------------------------------

    async def search_products(self, tenant_id: uuid.UUID, *, q: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            stmt = select(Product).where(Product.tenant_id == tenant_id)
            if status:
                stmt = stmt.where(Product.status == status)
            if q and q.strip():
                like = f"%{q.strip().lower()}%"
                stmt = stmt.where(or_(func.lower(Product.name).like(like), func.lower(Product.slug).like(like), func.lower(func.coalesce(Product.category, "")).like(like)))
            products = list((await session.execute(stmt.order_by(Product.name).limit(limit).offset(max(0, int(offset))))).scalars())
            return await self._with_skus(session, tenant_id, products)

    async def get_product(self, tenant_id: uuid.UUID, product_id: uuid.UUID) -> dict:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            product = (await session.execute(select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id))).scalar_one_or_none()
            if product is None:
                raise CatalogNotFoundError(f"Product {product_id} not found")
            return (await self._with_skus(session, tenant_id, [product]))[0]

    async def _with_skus(self, session, tenant_id: uuid.UUID, products: list[Product]) -> list[dict]:
        if not products:
            return []
        skus = list((await session.execute(select(Sku).where(Sku.tenant_id == tenant_id, Sku.product_id.in_([p.id for p in products])))).scalars())
        suppliers = {s.id: s for s in (await session.execute(select(Supplier).where(Supplier.tenant_id == tenant_id))).scalars()}
        by_product: dict[uuid.UUID, list[Sku]] = {}
        for sku in skus:
            by_product.setdefault(sku.product_id, []).append(sku)
        return [product_to_dict(p, by_product.get(p.id, []), suppliers) for p in products]

    # --- writing one product ---------------------------------------------------------------------------------------------------------------------

    async def upsert_product(self, tenant_id: uuid.UUID, raw: dict, *, actor_id: uuid.UUID | None = None) -> dict:
        """Creates or updates one product and its SKUs (by slug and SKU code), idempotently. Never publishes."""
        spec = normalize_product(raw)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            ctx = await self._load_context(session, tenant_id, [spec])
            plan = self._plan(spec, ctx)
            if plan.problems:
                raise CatalogValidationError([f"{code}: {text}" if code else text for code, text in plan.problems])
            try:
                product = await self._write(session, tenant_id, plan, ctx)
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise CatalogConflictError("The catalog changed at the same time; try again.") from exc
            return (await self._with_skus(session, tenant_id, [product]))[0]

    async def set_availability(
        self, tenant_id: uuid.UUID, sku_id: uuid.UUID, availability: str, *, quantity: int | None = None, actor_id: uuid.UUID | None = None
    ) -> dict:
        availability = (availability or "").upper()
        problems = []
        if availability not in {a.value for a in SkuAvailability}:
            problems.append("availability must be AVAILABLE, OUT_OF_STOCK or UNKNOWN")
        if quantity is not None and (not isinstance(quantity, int) or quantity < 0):
            problems.append("quantity must be a whole number, 0 or more")
        if availability == SkuAvailability.AVAILABLE and quantity == 0:
            problems.append("a quantity of 0 cannot be AVAILABLE")
        if availability == SkuAvailability.OUT_OF_STOCK and quantity not in (None, 0):
            problems.append("OUT_OF_STOCK cannot have a quantity above 0")
        if problems:
            raise CatalogValidationError(problems)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            sku = (await session.execute(select(Sku).where(Sku.id == sku_id, Sku.tenant_id == tenant_id))).scalar_one_or_none()
            if sku is None:
                raise CatalogNotFoundError(f"SKU {sku_id} not found")
            before = sku.availability
            sku.availability = availability
            session.add(InventorySnapshot(tenant_id=tenant_id, sku_id=sku.id, quantity=quantity, source=InventorySource.MANUAL.value, observed_at=_now()))
            _audit(session, tenant_id, actor_id, "dropshipping.sku.availability_set", "dropshipping_sku", sku.id, {"from": before, "to": availability, "quantity": quantity})
            await session.commit()
            supplier = (await session.execute(select(Supplier).where(Supplier.id == sku.supplier_id))).scalar_one_or_none()
            return sku_to_dict(sku, supplier)

    # --- publishing ------------------------------------------------------------------------------------------------------------------------------

    async def publish_product(self, tenant_id: uuid.UUID, product_id: uuid.UUID, *, actor_id: uuid.UUID | None = None) -> dict:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            product = (await session.execute(select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id))).scalar_one_or_none()
            if product is None:
                raise CatalogNotFoundError(f"Product {product_id} not found")
            if product.status == ProductStatus.ACTIVE:
                return (await self._with_skus(session, tenant_id, [product]))[0]
            if product.status == ProductStatus.ARCHIVED:
                raise PublishBlockedError(["An archived product cannot be published; create or restore it as a new product."])
            skus = list((await session.execute(select(Sku).where(Sku.tenant_id == tenant_id, Sku.product_id == product.id))).scalars())
            problems = publish_problems(product, skus)
            if problems:
                raise PublishBlockedError(problems)
            product.status = ProductStatus.ACTIVE
            _audit(session, tenant_id, actor_id, "dropshipping.product.published", "dropshipping_product", product.id, {"slug": product.slug})
            await session.commit()
            return (await self._with_skus(session, tenant_id, [product]))[0]

    async def archive_product(self, tenant_id: uuid.UUID, product_id: uuid.UUID, *, actor_id: uuid.UUID | None = None) -> dict:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            product = (await session.execute(select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id))).scalar_one_or_none()
            if product is None:
                raise CatalogNotFoundError(f"Product {product_id} not found")
            if product.status != ProductStatus.ARCHIVED:
                product.status = ProductStatus.ARCHIVED
                _audit(session, tenant_id, actor_id, "dropshipping.product.archived", "dropshipping_product", product.id, {"slug": product.slug})
                await session.commit()
            return (await self._with_skus(session, tenant_id, [product]))[0]

    # --- the CSV import --------------------------------------------------------------------------------------------------------------------------

    async def preview_import(self, tenant_id: uuid.UUID, csv_text: str) -> ImportReport:
        """Validates the whole file against this business's catalog and reports what a commit would do. Writes NOTHING."""
        return await self._import(tenant_id, csv_text, commit=False, actor_id=None)

    async def commit_import(self, tenant_id: uuid.UUID, csv_text: str, token: str, *, actor_id: uuid.UUID | None = None) -> ImportReport:
        """Commits exactly the previewed file, all or nothing. Raises ImportTokenError for a different file, ImportRejectedError if any row is rejected."""
        if not token_matches(tenant_id, csv_text, token):
            raise ImportTokenError("This file is not the one that was previewed. Preview it again.")
        report = await self._import(tenant_id, csv_text, commit=True, actor_id=actor_id)
        if not report.ok:
            raise ImportRejectedError(report)
        return report

    async def _import(self, tenant_id: uuid.UUID, csv_text: str, *, commit: bool, actor_id: uuid.UUID | None) -> ImportReport:
        parsed, file_problems = parse_catalog_csv(csv_text)
        token = preview_token(tenant_id, csv_text) if isinstance(csv_text, str) else None
        if file_problems:
            return ImportReport(ok=False, token=None, file_problems=file_problems)

        results = {p.row: RowResult(row=p.row, sku_code=p.sku_code, action="rejected" if p.problems else "valid", problems=list(p.problems)) for p in parsed}
        by_slug: dict[str, list] = {}
        for p in parsed:
            if p.slug is None and not p.problems:
                results[p.row].problems.append("product_name is required.")
            by_slug.setdefault(p.slug or f"__row{p.row}", []).append(p)

        specs: list[ProductSpec] = []
        spec_rows: dict[str, list] = {}
        for slug, rows in by_slug.items():
            for p in rows:
                if not results[p.row].problems:
                    try:
                        normalize_sku(p.sku_raw)
                    except ValueError as exc:
                        results[p.row].problems.append(str(exc))
            if any(results[p.row].problems for p in rows):
                continue
            try:
                spec = normalize_product({**rows[0].product_raw, "skus": [p.sku_raw for p in rows]})
            except CatalogValidationError as exc:
                results[rows[0].row].problems.extend(exc.problems)
                continue
            specs.append(spec)
            spec_rows[spec.slug] = rows

        report = ImportReport(ok=False, token=token)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            ctx = await self._load_context(session, tenant_id, specs)
            plans: list[_ProductPlan] = []
            for spec in specs:
                plan = self._plan(spec, ctx)
                plans.append(plan)
                rows = spec_rows[spec.slug]
                code_to_row = {r.sku_code: r for r in rows}
                for sku_plan in plan.skus:
                    results[code_to_row[sku_plan.spec.sku_code].row].action = sku_plan.action
                for code, text in plan.problems:
                    target = code_to_row.get(code, rows[0])
                    results[target.row].problems.append(text)
            for r in results.values():
                if r.problems:
                    r.action = "rejected"
            report.rows = [results[p.row] for p in parsed]
            report.rejected = sum(1 for r in report.rows if r.action == "rejected")
            report.ok = report.rejected == 0
            for plan in plans:
                report.products_created += plan.product_action == "create"
                report.products_updated += plan.product_action == "update"
                for sp in plan.skus:
                    report.skus_created += sp.action == "create"
                    report.skus_updated += sp.action == "update"
                    report.skus_unchanged += sp.action == "unchanged"
            if not (commit and report.ok):
                return report
            try:
                for plan in plans:
                    await self._write(session, tenant_id, plan, ctx)
                _audit(session, tenant_id, actor_id, "dropshipping.catalog.imported", "dropshipping_catalog", None, {
                    "rows": len(report.rows), "skus_created": report.skus_created, "skus_updated": report.skus_updated, "skus_unchanged": report.skus_unchanged,
                    "products_created": report.products_created, "products_updated": report.products_updated,
                })
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise CatalogConflictError("The catalog changed while importing; nothing was written. Preview the file again.") from exc
            report.committed = True
            return report

    # --- planning and writing (shared by upsert_product and the import) ----------------------------------------------------------------------

    async def _load_context(self, session, tenant_id: uuid.UUID, specs: list[ProductSpec]) -> _Context:
        suppliers = list((await session.execute(select(Supplier).where(Supplier.tenant_id == tenant_id))).scalars())
        slugs = [s.slug for s in specs]
        codes = [k.sku_code for s in specs for k in s.skus]
        products = list((await session.execute(select(Product).where(Product.tenant_id == tenant_id, Product.slug.in_(slugs)))).scalars()) if slugs else []
        skus = list((await session.execute(select(Sku).where(Sku.tenant_id == tenant_id, Sku.sku_code.in_(codes)))).scalars()) if codes else []
        return _Context(
            suppliers_by_id={s.id: s for s in suppliers}, suppliers_by_name={s.name.lower(): s for s in suppliers},
            products_by_slug={p.slug: p for p in products}, skus_by_code={k.sku_code: k for k in skus},
        )

    @staticmethod
    def _supplier(ctx: _Context, ref: str) -> Supplier | None:
        try:
            return ctx.suppliers_by_id.get(uuid.UUID(ref))
        except ValueError:
            return ctx.suppliers_by_name.get(ref.lower())

    def _plan(self, spec: ProductSpec, ctx: _Context) -> _ProductPlan:
        product = ctx.products_by_slug.get(spec.slug)
        problems: list[tuple[str | None, str]] = []
        if product is not None and product.status == ProductStatus.ARCHIVED:
            problems.append((None, f"The product {spec.slug} is archived."))
        if product is not None and product.status == ProductStatus.ACTIVE and not spec.is_non_perishable:
            problems.append((None, f"The product {spec.slug} is on sale and must stay marked non-perishable; archive it first if that is wrong."))
        if product is None:
            product_action = "create"
        else:
            same = (product.name, product.description, product.category, product.image_url, product.is_non_perishable) == (
                spec.name, spec.description, spec.category, spec.image_url, spec.is_non_perishable)
            product_action = "unchanged" if same else "update"
        sku_plans: list[_SkuPlan] = []
        for sku in spec.skus:
            supplier = self._supplier(ctx, sku.supplier_ref)
            if supplier is None:
                problems.append((sku.sku_code, f"unknown supplier '{sku.supplier_ref}'. Create the supplier first."))
            existing = ctx.skus_by_code.get(sku.sku_code)
            if existing is None:
                action = "create"
            elif product is None or existing.product_id != product.id:
                problems.append((sku.sku_code, "this SKU code already belongs to a different product."))
                action = "rejected"
            else:
                same_sku = supplier is not None and (
                    Decimal(existing.price), existing.supplier_id, existing.supplier_sku, existing.supplier_cost, existing.currency, existing.weight_grams, existing.availability,
                ) == (sku.price, supplier.id, sku.supplier_sku, sku.supplier_cost, sku.currency, sku.weight_grams, sku.availability)
                action = "unchanged" if same_sku else "update"
            sku_plans.append(_SkuPlan(spec=sku, action=action, supplier=supplier))
        return _ProductPlan(spec=spec, product_action=product_action, skus=sku_plans, problems=problems)

    async def _write(self, session, tenant_id: uuid.UUID, plan: _ProductPlan, ctx: _Context) -> Product:
        spec = plan.spec
        product = ctx.products_by_slug.get(spec.slug)
        if product is None:
            product = Product(tenant_id=tenant_id, slug=spec.slug, name=spec.name, description=spec.description, category=spec.category,
                              image_url=spec.image_url, is_non_perishable=spec.is_non_perishable, status=ProductStatus.DRAFT.value)
            session.add(product)
            await session.flush()  # the id is assigned on flush
            ctx.products_by_slug[spec.slug] = product
        elif plan.product_action == "update":
            product.name, product.description, product.category = spec.name, spec.description, spec.category
            product.image_url, product.is_non_perishable = spec.image_url, spec.is_non_perishable
        for sp in plan.skus:
            if sp.action == "unchanged":
                continue
            sku = ctx.skus_by_code.get(sp.spec.sku_code)
            availability_changed = sku is None and sp.spec.availability != SkuAvailability.UNKNOWN.value or (sku is not None and sku.availability != sp.spec.availability)
            if sku is None:
                sku = Sku(tenant_id=tenant_id, product_id=product.id, sku_code=sp.spec.sku_code, price=sp.spec.price, supplier_id=sp.supplier.id)
                session.add(sku)
                ctx.skus_by_code[sp.spec.sku_code] = sku
            sku.supplier_id = sp.supplier.id
            sku.supplier_sku = sp.spec.supplier_sku
            sku.price = sp.spec.price
            sku.supplier_cost = sp.spec.supplier_cost
            sku.currency = sp.spec.currency
            sku.weight_grams = sp.spec.weight_grams
            sku.availability = sp.spec.availability
            sku.status = SkuStatus.ACTIVE.value
            await session.flush()
            if availability_changed:
                session.add(InventorySnapshot(tenant_id=tenant_id, sku_id=sku.id, quantity=None, source=InventorySource.MANUAL.value, observed_at=_now()))
        return product
