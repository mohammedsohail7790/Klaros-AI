class ToolError(Exception):
    """Base for every failure the tool registry can produce. Always caught at
    the registry boundary and turned into an audit record — never a silent
    failure."""


class ToolNotFoundError(ToolError):
    pass


class ToolPermissionError(ToolError):
    pass


class ToolTenantMismatchError(ToolError):
    pass


class ToolValidationError(ToolError):
    pass


class ToolBlockedError(ToolError):
    pass


class ToolBillingLimitError(ToolError):
    """Raised when a tenant's plan/trial/subscription state disallows this
    call — a lapsed trial, a canceled/past_due subscription, or a Solo-plan
    AI usage cap already hit this month. See
    app/services/billing_service.py::check_ai_usage_allowed. Distinct from
    ToolBlockedError (an owner's own policy choice) — this is Klaros's own
    billing boundary, mapped to HTTP 402 rather than 403."""

    pass


class ToolApprovalRequiredError(ToolError):
    """Not a failure — the caller should treat this as "pending", not "error".
    Raised so callers can't accidentally ignore the approval boundary."""

    def __init__(self, approval_request_id, message: str = "Action requires approval") -> None:
        super().__init__(message)
        self.approval_request_id = approval_request_id
