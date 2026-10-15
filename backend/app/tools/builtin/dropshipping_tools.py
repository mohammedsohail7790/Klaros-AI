"""Dropshipping store, branch 2: ToolRegistry tools for the catalog.

Six tools, each a thin wrapper over `DropshippingCatalogService` (domain rules live there, never here). Every call, by a person or an AI agent, goes through the
registry's permission, tenant, schema, policy and audit checks.

Policy (see app/tools/policy.py):
  - read and the internal catalog writes are AUTO, the same tier as `medical_tourism.create_provider` and `crm.create_lead`: they move no money and contact no one,
    and each is idempotent by a database rule (SKU code, product slug, supplier name).
  - `publish_product` and `import_catalog` are APPROVAL_REQUIRED: a product going on sale, or a whole catalog arriving in one call, is something a person
    approves when an AI agent asks. The registry applies that policy to every actor, so the STAFF API does NOT call these two tools: it calls the service
    directly behind MANAGE_DROPSHIPPING (like medical tourism's credential routes) and writes its own audit row.

Idempotency (Phase 8 discipline): only `search_products` is `supports_idempotency = True`, because it issues only SELECTs. The writes keep the conservative default
(False), exactly as `medical_tourism.create_provider` does, even though each has a database uniqueness rule behind it.
"""

import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.models.rbac import Permission
from app.services.dropshipping_catalog_import import CatalogValidationError
from app.services.dropshipping_catalog_service import (
    CatalogConflictError,
    CatalogNotFoundError,
    DropshippingCatalogService,
    ImportRejectedError,
    ImportTokenError,
    supplier_to_dict,
)
from app.tools.base import ExecutionContext, Tool
from app.tools.errors import ToolError, ToolNotFoundError, ToolValidationError


def _as_tool_error(exc: Exception) -> ToolError:
    """Business-rule failures become the tool errors the shared HTTP mapping already understands: validation -> 422, not found -> 404, anything else -> 400."""
    if isinstance(exc, CatalogValidationError):
        return ToolValidationError("; ".join(exc.problems))
    if isinstance(exc, ImportRejectedError):
        reasons = "; ".join(f"row {r.row}: {', '.join(r.problems)}" for r in exc.report.rows if r.problems)[:1500]
        return ToolValidationError("The import was rejected and nothing was written: " + (reasons or "see the preview report."))
    if isinstance(exc, ImportTokenError):
        return ToolValidationError(str(exc))
    if isinstance(exc, CatalogNotFoundError):
        return ToolNotFoundError(str(exc))
    return ToolError(str(exc))


# --- search_products ---------------------------------------------------------------------------------------------------------------------------------


class SearchProductsInput(BaseModel):
    q: str | None = None
    status: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class SearchProductsOutput(BaseModel):
    products: list[dict[str, Any]]


class SearchProducts(Tool):
    name = "dropshipping.search_products"
    description = "Search the store's catalog by name, slug or category, optionally by status (DRAFT, ACTIVE, ARCHIVED). Read-only."
    input_schema = SearchProductsInput
    output_schema = SearchProductsOutput
    required_permission = Permission.READ_DROPSHIPPING
    supports_idempotency = True  # SELECT only, verified by reading DropshippingCatalogService.search_products

    def __init__(self, service: DropshippingCatalogService) -> None:
        self._service = service

    async def execute(self, input: SearchProductsInput, context: ExecutionContext) -> SearchProductsOutput:
        return SearchProductsOutput(products=await self._service.search_products(context.tenant_id, q=input.q, status=input.status, limit=input.limit, offset=input.offset))


# --- create_supplier -----------------------------------------------------------------------------------------------------------------------------------


class CreateSupplierInput(BaseModel):
    name: str
    integration_type: str = "MANUAL"
    contact_email: str | None = None
    contact_phone: str | None = None
    notes: str | None = None


class CreateSupplierOutput(BaseModel):
    supplier: dict[str, Any]
    deduplicated: bool = False


class CreateSupplier(Tool):
    name = "dropshipping.create_supplier"
    description = "Create a supplier (who fulfils orders). A supplier with the same name already in this business is returned instead (deduplicated)."
    input_schema = CreateSupplierInput
    output_schema = CreateSupplierOutput
    required_permission = Permission.MANAGE_DROPSHIPPING

    def __init__(self, service: DropshippingCatalogService) -> None:
        self._service = service

    async def execute(self, input: CreateSupplierInput, context: ExecutionContext) -> CreateSupplierOutput:
        try:
            supplier, deduplicated = await self._service.create_supplier(
                context.tenant_id, input.name, integration_type=input.integration_type, contact_email=input.contact_email,
                contact_phone=input.contact_phone, notes=input.notes,
            )
        except CatalogValidationError as exc:
            raise _as_tool_error(exc) from exc
        return CreateSupplierOutput(supplier=supplier_to_dict(supplier), deduplicated=deduplicated)


# --- upsert_product -------------------------------------------------------------------------------------------------------------------------------------


class SkuToolInput(BaseModel):
    sku_code: str
    price: Decimal
    supplier: str  # a supplier id or the supplier's exact name
    supplier_sku: str | None = None
    supplier_cost: Decimal | None = None
    currency: str = "USD"
    weight_grams: int | None = Field(default=None, ge=0)
    availability: str = "UNKNOWN"


class UpsertProductInput(BaseModel):
    name: str
    skus: list[SkuToolInput] = Field(min_length=1, max_length=100)
    slug: str | None = None
    description: str | None = None
    category: str | None = None
    image_url: str | None = None
    is_non_perishable: bool = False


class UpsertProductOutput(BaseModel):
    product: dict[str, Any]


class UpsertProduct(Tool):
    name = "dropshipping.upsert_product"
    description = "Create or update one product and its SKUs (matched by slug and SKU code). The product stays a DRAFT; this never puts anything on sale."
    input_schema = UpsertProductInput
    output_schema = UpsertProductOutput
    required_permission = Permission.MANAGE_DROPSHIPPING

    def __init__(self, service: DropshippingCatalogService) -> None:
        self._service = service

    async def execute(self, input: UpsertProductInput, context: ExecutionContext) -> UpsertProductOutput:
        raw = input.model_dump(mode="json")
        try:
            product = await self._service.upsert_product(context.tenant_id, raw, actor_id=context.actor_id)
        except (CatalogValidationError, CatalogConflictError) as exc:
            raise _as_tool_error(exc) from exc
        return UpsertProductOutput(product=product)


# --- set_availability -----------------------------------------------------------------------------------------------------------------------------------


class SetAvailabilityInput(BaseModel):
    sku_id: uuid.UUID
    availability: str
    quantity: int | None = Field(default=None, ge=0)


class SetAvailabilityOutput(BaseModel):
    sku: dict[str, Any]


class SetAvailability(Tool):
    name = "dropshipping.set_availability"
    description = "Record a SKU's stock availability (AVAILABLE, OUT_OF_STOCK, UNKNOWN), optionally with a quantity. Appends a manual inventory snapshot."
    input_schema = SetAvailabilityInput
    output_schema = SetAvailabilityOutput
    required_permission = Permission.MANAGE_DROPSHIPPING

    def __init__(self, service: DropshippingCatalogService) -> None:
        self._service = service

    async def execute(self, input: SetAvailabilityInput, context: ExecutionContext) -> SetAvailabilityOutput:
        try:
            sku = await self._service.set_availability(context.tenant_id, input.sku_id, input.availability, quantity=input.quantity, actor_id=context.actor_id)
        except (CatalogValidationError, CatalogNotFoundError) as exc:
            raise _as_tool_error(exc) from exc
        return SetAvailabilityOutput(sku=sku)


# --- publish_product ------------------------------------------------------------------------------------------------------------------------------------


class PublishProductInput(BaseModel):
    product_id: uuid.UUID


class PublishProductOutput(BaseModel):
    product: dict[str, Any]


class PublishProduct(Tool):
    name = "dropshipping.publish_product"
    description = "Put a DRAFT product on sale. Refused, with reasons, unless it has an active priced SKU with a supplier and is marked non-perishable."
    input_schema = PublishProductInput
    output_schema = PublishProductOutput
    required_permission = Permission.MANAGE_DROPSHIPPING

    def __init__(self, service: DropshippingCatalogService) -> None:
        self._service = service

    async def execute(self, input: PublishProductInput, context: ExecutionContext) -> PublishProductOutput:
        try:
            product = await self._service.publish_product(context.tenant_id, input.product_id, actor_id=context.actor_id)
        except (CatalogValidationError, CatalogNotFoundError) as exc:
            raise _as_tool_error(exc) from exc
        return PublishProductOutput(product=product)


# --- import_catalog -------------------------------------------------------------------------------------------------------------------------------------


class ImportCatalogInput(BaseModel):
    csv_text: str
    token: str  # from a preview of this exact file; the audit log redacts it


class ImportCatalogOutput(BaseModel):
    report: dict[str, Any]


class ImportCatalog(Tool):
    name = "dropshipping.import_catalog"
    description = "Commit a catalog CSV that was previewed first (the preview token proves it is the same file). All or nothing; products land as DRAFT."
    input_schema = ImportCatalogInput
    output_schema = ImportCatalogOutput
    required_permission = Permission.MANAGE_DROPSHIPPING

    def __init__(self, service: DropshippingCatalogService) -> None:
        self._service = service

    async def execute(self, input: ImportCatalogInput, context: ExecutionContext) -> ImportCatalogOutput:
        try:
            report = await self._service.commit_import(context.tenant_id, input.csv_text, input.token, actor_id=context.actor_id)
        except (CatalogValidationError, CatalogConflictError, ImportTokenError, ImportRejectedError) as exc:
            raise _as_tool_error(exc) from exc
        return ImportCatalogOutput(report=report.to_dict())
