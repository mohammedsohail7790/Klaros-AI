import uuid

from pydantic import BaseModel

from app.services.ai_next_action_service import AINextActionService
from app.tools.base import ExecutionContext, Tool


class NextActionProposalOutput(BaseModel):
    """Shared output shape for every AI Next Action entry-point tool
    (Phase 20: factored out once a second scenario proved it was
    identical, not introduced speculatively) — mirrors
    `NextActionDecision` field-for-field. Never a fabricated success:
    every outcome is the real result of the governed decision pipeline in
    app/services/ai_next_action_service.py."""

    outcome: str
    proposed_tool_name: str | None = None
    reason: str | None = None
    approval_request_id: str | None = None


class ProposeQuoteFollowupInput(BaseModel):
    quote_id: uuid.UUID


class ProposeQuoteFollowup(Tool):
    """Phase 18: the AI Next Action decision layer's entry point, itself a
    normal governed Tool — called by the Automation Engine's own EVENT
    trigger (see AutomationService.ACTION_ALLOWLIST) exactly like every
    other automation step, through AIExecutionService."""

    name = "ai.propose_quote_followup"
    description = (
        "Observe one stale/expired quote, consult Company Memory, and let the AI propose at most one "
        "governed follow-up action — validated deterministically and executed only through the same "
        "ActionPolicy/ApprovalRequest boundary every other tool call uses. Never executes anything itself."
    )
    input_schema = ProposeQuoteFollowupInput
    output_schema = NextActionProposalOutput
    required_permission = None

    def __init__(self, service: AINextActionService) -> None:
        self._service = service

    async def execute(self, input: ProposeQuoteFollowupInput, context: ExecutionContext) -> NextActionProposalOutput:
        decision = await self._service.decide_quote_followup(
            context.tenant_id, input.quote_id, correlation_id=context.correlation_id
        )
        return NextActionProposalOutput(
            outcome=decision.outcome,
            proposed_tool_name=decision.proposed_tool_name,
            reason=decision.reason,
            approval_request_id=str(decision.approval_request_id) if decision.approval_request_id else None,
        )


class ProposeInvoiceFollowupInput(BaseModel):
    invoice_id: uuid.UUID


class ProposeInvoiceFollowup(Tool):
    """Phase 20: the second real AI Next Action scenario — same governed
    entry-point shape as `ProposeQuoteFollowup`, proving the decision
    pipeline generalizes across independent business domains (quotes vs
    invoices) rather than being quote-specific."""

    name = "ai.propose_invoice_followup"
    description = (
        "Observe one overdue invoice, consult Company Memory, and let the AI propose at most one "
        "governed follow-up action — validated deterministically and executed only through the same "
        "ActionPolicy/ApprovalRequest boundary every other tool call uses. Never executes anything itself."
    )
    input_schema = ProposeInvoiceFollowupInput
    output_schema = NextActionProposalOutput
    required_permission = None

    def __init__(self, service: AINextActionService) -> None:
        self._service = service

    async def execute(self, input: ProposeInvoiceFollowupInput, context: ExecutionContext) -> NextActionProposalOutput:
        decision = await self._service.decide_invoice_followup(
            context.tenant_id, input.invoice_id, correlation_id=context.correlation_id
        )
        return NextActionProposalOutput(
            outcome=decision.outcome,
            proposed_tool_name=decision.proposed_tool_name,
            reason=decision.reason,
            approval_request_id=str(decision.approval_request_id) if decision.approval_request_id else None,
        )
