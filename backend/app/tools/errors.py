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


class ToolApprovalRequiredError(ToolError):
    """Not a failure — the caller should treat this as "pending", not "error".
    Raised so callers can't accidentally ignore the approval boundary."""

    def __init__(self, approval_request_id, message: str = "Action requires approval") -> None:
        super().__init__(message)
        self.approval_request_id = approval_request_id
