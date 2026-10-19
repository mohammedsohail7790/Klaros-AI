"""Xero contact import, exposed through the governed ToolRegistry pipeline (permission -> tenant -> approval policy -> execution -> audit). Read-from-Xero, create-in-Klaros:
nothing is ever written to Xero. APPROVAL_REQUIRED because it creates customer records in bulk."""

from pydantic import BaseModel, Field

from app.integrations.xero_client import XeroAPIError
from app.models.rbac import Permission
from app.services.xero_import_service import XeroImportService, XeroNotConnectedError
from app.tools.base import ExecutionContext, Tool
from app.tools.errors import ToolError


class ImportXeroContactsInput(BaseModel):
    max_records: int = Field(default=300, le=1000, gt=0)


class ImportXeroContactsOutput(BaseModel):
    contacts_seen: int
    created: int
    matched: int
    already_linked: int
    skipped: int


class ImportXeroContacts(Tool):
    name = "finance.import_xero_contacts"
    description = "Import this tenant's existing Xero contacts as Klaros customers (matches existing customers, never duplicates; read-only on the Xero side)."
    input_schema = ImportXeroContactsInput
    output_schema = ImportXeroContactsOutput
    required_permission = Permission.MANAGE_INTEGRATIONS
    # supports_idempotency stays False (conservative default): per-record idempotency is by external_id linkage, which is not an atomic batch guarantee.

    def __init__(self, import_service: XeroImportService) -> None:
        self._import_service = import_service

    async def execute(self, input: ImportXeroContactsInput, context: ExecutionContext) -> ImportXeroContactsOutput:
        try:
            result = await self._import_service.import_contacts(context.tenant_id, max_records=input.max_records)
        except XeroNotConnectedError as exc:
            raise ToolError(str(exc)) from exc
        except XeroAPIError as exc:
            raise ToolError(f"Xero import failed: {exc}") from exc
        return ImportXeroContactsOutput(
            contacts_seen=result.contacts_seen, created=result.created, matched=result.matched, already_linked=result.already_linked, skipped=result.skipped
        )
