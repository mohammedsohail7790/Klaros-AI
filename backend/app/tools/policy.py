"""section 8: the approval boundary.

Every registered tool resolves to exactly one policy. The registry consults
this before execute() ever runs — an APPROVAL_REQUIRED tool call never
touches the tool body; it creates an ApprovalRequest and returns instead.
"""

from enum import StrEnum


class ActionPolicy(StrEnum):
    AUTO = "AUTO"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    BLOCKED = "BLOCKED"


# Default policy per tool name. Tenant-specific overrides (section 44, rule
# engine) are out of scope for Phase 2 — this static table is the seed for
# that later `approval_policies` table.
DEFAULT_TOOL_POLICIES: dict[str, ActionPolicy] = {
    "system.get_tenant_context": ActionPolicy.AUTO,
    "system.get_current_time": ActionPolicy.AUTO,
    "events.publish_event": ActionPolicy.AUTO,
    "events.get_event": ActionPolicy.AUTO,
    "notifications.create_notification": ActionPolicy.AUTO,
    "audit.record_action": ActionPolicy.AUTO,
    "approvals.create_request": ActionPolicy.AUTO,
    # Example of each category from the spec, for future finance/ops tools to
    # register against:
    "finance.refund_over_threshold": ActionPolicy.APPROVAL_REQUIRED,
    "customer.delete": ActionPolicy.BLOCKED,
}


def policy_for(tool_name: str) -> ActionPolicy:
    return DEFAULT_TOOL_POLICIES.get(tool_name, ActionPolicy.APPROVAL_REQUIRED)
