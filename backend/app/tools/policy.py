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
    # CRM (Phase 3): lead/customer/appointment actions are low-risk and
    # reversible, so AUTO. Nothing here touches money yet.
    "crm.create_lead": ActionPolicy.AUTO,
    "crm.get_lead": ActionPolicy.AUTO,
    "crm.update_lead": ActionPolicy.AUTO,
    "crm.search_leads": ActionPolicy.AUTO,
    "crm.qualify_lead": ActionPolicy.AUTO,
    "crm.create_customer": ActionPolicy.AUTO,
    "crm.get_customer": ActionPolicy.AUTO,
    "crm.update_customer": ActionPolicy.AUTO,
    "crm.search_customers": ActionPolicy.AUTO,
    "crm.get_customer_timeline": ActionPolicy.AUTO,
    "crm.create_note": ActionPolicy.AUTO,
    "crm.generate_customer_summary": ActionPolicy.AUTO,
    "crm.check_availability": ActionPolicy.AUTO,
    "crm.create_appointment": ActionPolicy.AUTO,
    "crm.cancel_appointment": ActionPolicy.AUTO,
    "crm.reschedule_appointment": ActionPolicy.AUTO,
    # Operations (Phase 4) — section 31's recommended defaults.
    "operations.create_job": ActionPolicy.AUTO,
    "operations.get_job": ActionPolicy.AUTO,
    "operations.update_job": ActionPolicy.AUTO,
    "operations.search_jobs": ActionPolicy.AUTO,
    "operations.assign_job": ActionPolicy.AUTO,
    "operations.unassign_job": ActionPolicy.AUTO,
    "operations.schedule_job": ActionPolicy.AUTO,
    "operations.reschedule_job": ActionPolicy.APPROVAL_REQUIRED,  # customer-impacting
    "operations.dispatch_job": ActionPolicy.AUTO,
    "operations.update_job_status": ActionPolicy.AUTO,
    "operations.start_job": ActionPolicy.AUTO,
    "operations.complete_job": ActionPolicy.AUTO,
    "operations.cancel_job": ActionPolicy.AUTO,
    "operations.block_job": ActionPolicy.AUTO,
    "operations.unblock_job": ActionPolicy.AUTO,
    "operations.get_job_timeline": ActionPolicy.AUTO,
    "operations.generate_job_summary": ActionPolicy.AUTO,
    "operations.convert_lead_and_book": ActionPolicy.AUTO,
    "operations.create_worker": ActionPolicy.AUTO,
    "operations.list_workers": ActionPolicy.AUTO,
    "operations.update_worker_status": ActionPolicy.AUTO,
    "operations.create_task": ActionPolicy.AUTO,
    "operations.complete_task": ActionPolicy.AUTO,
    "operations.add_material": ActionPolicy.AUTO,
    "operations.create_purchase_order_draft": ActionPolicy.AUTO,
    "operations.add_job_document": ActionPolicy.AUTO,
    "operations.add_job_photo": ActionPolicy.AUTO,
    "operations.add_voice_note": ActionPolicy.AUTO,
    "operations.start_qa": ActionPolicy.AUTO,
    "operations.complete_qa": ActionPolicy.AUTO,
    "operations.fail_qa": ActionPolicy.AUTO,
    "operations.create_scope_change": ActionPolicy.AUTO,
    "operations.request_scope_change_approval": ActionPolicy.APPROVAL_REQUIRED,  # cost/revenue impact
    "operations.create_exception": ActionPolicy.AUTO,
    "operations.resolve_exception": ActionPolicy.AUTO,
    "operations.generate_completion_packet": ActionPolicy.AUTO,
    "operations.close_job": ActionPolicy.AUTO,  # gated by its own required-checks, not policy
    "operations.record_customer_signoff": ActionPolicy.AUTO,
    "operations.delete_job": ActionPolicy.BLOCKED,  # no tool implements this name — reserved
    # Example of each category from the spec, for future finance/marketing
    # tools to register against:
    "finance.refund_over_threshold": ActionPolicy.APPROVAL_REQUIRED,
    "customer.delete": ActionPolicy.BLOCKED,
}


def policy_for(tool_name: str) -> ActionPolicy:
    return DEFAULT_TOOL_POLICIES.get(tool_name, ActionPolicy.APPROVAL_REQUIRED)
