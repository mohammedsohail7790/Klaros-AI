"""Staff-facing contract tools — the internal half of the contract domain
(list/get/send), mirroring `app/tools/builtin/quote_tools.py`'s split
exactly: the customer's own view/sign/decline happens through the public
endpoint (`app/api/v1/public_contracts.py`), never through the
ToolRegistry, for the same "no authenticated ExecutionContext for an
unauthenticated customer" reason.

Reuses `Permission.SEND_QUOTE` rather than adding a new permission —
whoever can send a quote to a customer can equally send that quote's
resulting contract; this avoids RBAC proliferation for a closely related
action."""

import uuid
from typing import Any

from pydantic import BaseModel

from app.core.config import get_settings
from app.core.security import create_contract_view_token
from app.models.contract import Contract
from app.models.rbac import Permission
from app.services.contract_service import ContractNotFoundError, ContractService, InvalidContractTransitionError
from app.tools.base import ExecutionContext, Tool


def _contract_to_dict(c: Contract) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "contract_number": c.contract_number,
        "quote_id": str(c.quote_id),
        "customer_id": str(c.customer_id),
        "status": c.status,
        "sent_at": c.sent_at.isoformat() if c.sent_at else None,
        "viewed_at": c.viewed_at.isoformat() if c.viewed_at else None,
        "decided_at": c.decided_at.isoformat() if c.decided_at else None,
        "signer_name": c.signer_name,
        "signer_email": c.signer_email,
        "content_hash": c.content_hash,
    }


class GetContractInput(BaseModel):
    contract_id: uuid.UUID


class ContractOutput(BaseModel):
    contract: dict[str, Any]


class GetContract(Tool):
    name = "contracts.get_contract"
    description = "Fetch a contract by id."
    input_schema = GetContractInput
    output_schema = ContractOutput
    required_permission = Permission.SEND_QUOTE

    def __init__(self, contract_service: ContractService) -> None:
        self._contract_service = contract_service

    async def execute(self, input: GetContractInput, context: ExecutionContext) -> ContractOutput:
        try:
            contract = await self._contract_service.get(context.tenant_id, input.contract_id)
        except ContractNotFoundError as e:
            raise ValueError(str(e)) from e
        return ContractOutput(contract=_contract_to_dict(contract))


class SendContractInput(BaseModel):
    contract_id: uuid.UUID


class SendContractOutput(BaseModel):
    contract: dict[str, Any]
    view_url_path: str


class SendContract(Tool):
    """Marks the contract SENT and generates its real signed public view
    link — actual delivery (email/SMS) is left to the caller/staff member
    for now, matching the current scope; the link itself is real and
    functional. `AUTO` policy — see app/tools/policy.py."""

    name = "contracts.send_contract"
    description = "Mark a DRAFT contract SENT and generate its signed public view/sign link."
    input_schema = SendContractInput
    output_schema = SendContractOutput
    required_permission = Permission.SEND_QUOTE

    def __init__(self, contract_service: ContractService) -> None:
        self._contract_service = contract_service

    async def execute(self, input: SendContractInput, context: ExecutionContext) -> SendContractOutput:
        settings = get_settings()
        try:
            contract = await self._contract_service.send(context.tenant_id, input.contract_id)
        except ContractNotFoundError as e:
            raise ValueError(str(e)) from e
        except InvalidContractTransitionError as e:
            raise ValueError(str(e)) from e
        token = create_contract_view_token(contract.id, context.tenant_id)
        view_url_path = f"/contracts/view/{contract.id}?token={token}"
        return SendContractOutput(contract=_contract_to_dict(contract), view_url_path=view_url_path)


class EmptyInput(BaseModel):
    pass


class DetectPendingOutput(BaseModel):
    expired_contract_ids: list[str]


class DetectPendingContracts(Tool):
    """Phase 24: the deterministic contract-pending-follow-up sweep — same
    real pattern as `quotes.detect_expired_quotes`/
    `finance.detect_overdue_invoices` (see `ContractService.
    detect_pending`'s own docstring). Publishes the real, previously-dead
    `EventType.CONTRACT_EXPIRED` for each contract it transitions, which a
    companion EVENT-triggered automation reacts to."""

    name = "contracts.detect_pending"
    description = "Find SENT/VIEWED contracts pending too long and mark them EXPIRED."
    input_schema = EmptyInput
    output_schema = DetectPendingOutput
    required_permission = Permission.SEND_QUOTE

    def __init__(self, contract_service: ContractService) -> None:
        self._contract_service = contract_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> DetectPendingOutput:
        ids = await self._contract_service.detect_pending(context.tenant_id)
        return DetectPendingOutput(expired_contract_ids=[str(i) for i in ids])
