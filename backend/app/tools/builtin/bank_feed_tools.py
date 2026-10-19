from typing import Any

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.bank_feed_service import BankFeedService
from app.tools.base import ExecutionContext, Tool


class SyncBankFeedInput(BaseModel):
    pass


class SyncBankFeedOutput(BaseModel):
    status: str  # not_connected | ok | product_not_ready | login_required | error
    added: int = 0
    modified: int = 0
    removed: int = 0
    message: str = ""
    reconcile: dict[str, Any] | None = None


class SyncBankFeed(Tool):
    name = "finance.sync_bank_feed"
    description = (
        "Pull the latest transactions from the business's connected bank (read-only) and suggest which deposits pay which open invoices. "
        "Writes only the bank feed's own records; never creates or changes a payment or invoice. Reports not_connected when no bank is connected."
    )
    input_schema = SyncBankFeedInput
    output_schema = SyncBankFeedOutput
    required_permission = Permission.MANAGE_COLLECTIONS
    supports_idempotency = True  # transactions are upserted by their bank id and the cursor only moves with the saved rows

    def __init__(self, service: BankFeedService) -> None:
        self._service = service

    async def execute(self, input: SyncBankFeedInput, context: ExecutionContext) -> SyncBankFeedOutput:
        return SyncBankFeedOutput(**await self._service.sync_and_reconcile(context.tenant_id))
