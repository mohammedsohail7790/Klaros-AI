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
    # Phase 8: read-only event-worker admin tools, AUTO. Replay is a
    # deliberate re-attempt of a previously-failed *domain* action, not a new
    # sensitive action of its own — the domain action it retries kept
    # whatever policy it already had before it was dead-lettered (see
    # DEFAULT_TOOL_POLICIES entries below for those tools). Gated by
    # MANAGE_EVENTS at the permission layer instead, consistent with the
    # referral-reward pattern from Phase 7 (dedicated permission-gated tool,
    # not a second generic-approval dead end).
    "events.list_events": ActionPolicy.AUTO,
    "events.get_event_detail": ActionPolicy.AUTO,
    "events.list_dead_letters": ActionPolicy.AUTO,
    "events.replay_dead_letter": ActionPolicy.AUTO,
    "events.get_worker_metrics": ActionPolicy.AUTO,
    "notifications.create_notification": ActionPolicy.AUTO,
    "audit.record_action": ActionPolicy.AUTO,
    "approvals.create_request": ActionPolicy.AUTO,
    # Phase 9: approval orchestration tools are AUTO at the policy layer —
    # gated by dedicated permissions (APPROVE_ACTIONS/REJECT_ACTIONS/
    # EXECUTE_APPROVED_ACTIONS/READ_APPROVALS) instead, the same pattern
    # used for referral rewards (Phase 7) and recommendation execution
    # (Phase 8) to avoid creating a second, nested approval-required-to-
    # approve dead end.
    "approvals.approve": ActionPolicy.AUTO,
    "approvals.reject": ActionPolicy.AUTO,
    "approvals.retry_execution": ActionPolicy.AUTO,
    "approvals.list": ActionPolicy.AUTO,
    "approvals.get_detail": ActionPolicy.AUTO,
    "audit.list_ai_activity": ActionPolicy.AUTO,
    # Phase 10A: automation-policy management tools, permission-gated
    # instead of policy-gated (MANAGE_AUTOMATION_POLICIES) — same reasoning
    # as approvals.* above.
    "automation.list_policies": ActionPolicy.AUTO,
    "automation.get_policy": ActionPolicy.AUTO,
    "automation.set_policy": ActionPolicy.AUTO,
    "automation.reset_policy": ActionPolicy.AUTO,
    "automation.get_autonomy_stats": ActionPolicy.AUTO,
    # Phase 12: Knowledge Layer tools, permission-gated instead of
    # policy-gated (READ_KNOWLEDGE/MANAGE_KNOWLEDGE).
    "knowledge.list_files": ActionPolicy.AUTO,
    "knowledge.get_file": ActionPolicy.AUTO,
    "knowledge.set_file": ActionPolicy.AUTO,
    "knowledge.delete_file": ActionPolicy.AUTO,
    "knowledge.index_file": ActionPolicy.AUTO,
    "knowledge.search": ActionPolicy.AUTO,
    "knowledge.ask": ActionPolicy.AUTO,
    # Phase 18: the AI Next Action decision layer's own entry point is
    # AUTO — it never mutates anything directly itself (observe + decide +
    # propose only); the actual protection is the SEPARATE ActionPolicy
    # resolution the proposed downstream tool goes through a second time,
    # inside app/services/ai_next_action_service.py's own
    # AIExecutionService.request_tool_execution() call. Leaving this tool
    # at the unlisted APPROVAL_REQUIRED default would just create an
    # approval request to "let the AI think," not a real safety boundary.
    "ai.propose_quote_followup": ActionPolicy.AUTO,
    # Phase 20: same reasoning as ai.propose_quote_followup above — this
    # entry point never mutates anything directly itself either.
    "ai.propose_invoice_followup": ActionPolicy.AUTO,
    # Phase 10B: notification tools, permission-gated instead of
    # policy-gated (READ_NOTIFICATIONS/MANAGE_NOTIFICATION_PREFERENCES).
    "notifications.list_notifications": ActionPolicy.AUTO,
    "notifications.get_unread_count": ActionPolicy.AUTO,
    "notifications.mark_read": ActionPolicy.AUTO,
    "notifications.mark_all_read": ActionPolicy.AUTO,
    "notifications.dismiss": ActionPolicy.AUTO,
    "notifications.get_preferences": ActionPolicy.AUTO,
    "notifications.set_preference": ActionPolicy.AUTO,
    # CRM (Phase 3): lead/customer/appointment actions are low-risk and
    # reversible, so AUTO. Nothing here touches money yet.
    "crm.create_lead": ActionPolicy.AUTO,
    "crm.bulk_import_leads": ActionPolicy.AUTO,
    "crm.get_lead": ActionPolicy.AUTO,
    "crm.update_lead": ActionPolicy.AUTO,
    "crm.search_leads": ActionPolicy.AUTO,
    "crm.qualify_lead": ActionPolicy.AUTO,
    # Phase 12E: read-only advisory recommendation — never persists
    # anything to the Lead record, so AUTO is correct for the same reason
    # as crm.qualify_lead itself (nothing moves/sends/changes a record).
    "crm.ai_qualify_lead_advisory": ActionPolicy.AUTO,
    "crm.create_customer": ActionPolicy.AUTO,
    # Same reasoning as crm.create_customer — a business owner deliberately
    # uploading their own existing customer list is the same trust level
    # as creating one customer at a time, just batched.
    "crm.bulk_import_customers": ActionPolicy.AUTO,
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
    "customer.delete": ActionPolicy.BLOCKED,
    # Finance (Phase 5) — section 33's recommended defaults. Reversible,
    # non-customer-visible reads/writes are AUTO; anything customer-visible,
    # destructive, or money-releasing is APPROVAL_REQUIRED or gated purely
    # by permission (approve/reject tools — see each tool's docstring for
    # why AI can never reach those in practice).
    "finance.trigger_invoice_from_job": ActionPolicy.AUTO,
    "finance.create_invoice_draft": ActionPolicy.AUTO,
    # A business owner deliberately uploading their own existing AR ledger
    # — same trust level as crm.bulk_import_customers/leads, a batched
    # version of the owner's own direct action, not an AI proposal.
    "finance.bulk_import_invoices": ActionPolicy.AUTO,
    "finance.update_invoice_draft": ActionPolicy.AUTO,
    "finance.request_invoice_approval": ActionPolicy.AUTO,  # body decides AUTO-approve vs. pending
    "finance.approve_invoice": ActionPolicy.AUTO,  # gated by APPROVE_INVOICE permission instead
    "finance.reject_invoice": ActionPolicy.AUTO,  # gated by APPROVE_INVOICE permission instead
    # AUTO, not APPROVAL_REQUIRED: sending is already gated by the invoice's
    # own approval state machine (only APPROVED invoices can be sent, and
    # APPROVED is only reached via finance.request_invoice_approval's
    # threshold check or a human finance.approve_invoice). A second,
    # generic ToolRegistry-level approval on top of that would be
    # redundant — and, worse, a dead end: approving a ToolRegistry
    # APPROVAL_REQUIRED request only flips that request's own status, it
    # does not resume the original send (see app/tools/registry.py and the
    # Phase 2 note on this accepted limitation). finance.void_invoice below
    # has the same limitation and is left APPROVAL_REQUIRED since voiding
    # isn't gated by any other state machine — see KNOWN ISSUES.
    "finance.send_invoice": ActionPolicy.AUTO,
    "finance.void_invoice": ActionPolicy.APPROVAL_REQUIRED,  # destructive to the financial record
    "finance.get_invoice": ActionPolicy.AUTO,
    # Phase 14: quotes commit no money and no work — creating/editing/
    # sending a quote is a proposal, not a financial or operational
    # commitment (unlike finance.void_invoice above); the real commitment
    # point is the CUSTOMER's own accept decision, made through the
    # public view, which is not a ToolRegistry call at all (no
    # ExecutionContext exists for an unauthenticated customer).
    "quotes.create_quote_draft": ActionPolicy.AUTO,
    "quotes.update_quote_draft": ActionPolicy.AUTO,
    "quotes.send_quote": ActionPolicy.AUTO,
    "quotes.get_quote": ActionPolicy.AUTO,
    "quotes.detect_expired_quotes": ActionPolicy.AUTO,
    "contracts.get_contract": ActionPolicy.AUTO,
    "contracts.send_contract": ActionPolicy.AUTO,
    # Phase 24: same reasoning as quotes.detect_expired_quotes/
    # finance.detect_overdue_invoices — a read/sweep-shaped, no-argument-
    # beyond-tenant-context domain action.
    "contracts.detect_pending": ActionPolicy.AUTO,
    # Phase 15: a read-only status check, and a Stripe-hosted deposit
    # payment LINK generator — same "no money moves until the signed
    # webhook confirms it" reasoning as finance.create_stripe_checkout_session
    # just below.
    "finance.get_quote_deposit_status": ActionPolicy.AUTO,
    "finance.create_quote_deposit_checkout_session": ActionPolicy.AUTO,
    # Phase 17: pushes an already-successful, already-recorded deposit
    # Payment to QuickBooks — moves no new money (the real payment already
    # happened via Stripe), idempotent by construction, same reasoning as
    # finance.sync_invoice_to_quickbooks directly below.
    "finance.sync_deposit_payment_to_quickbooks": ActionPolicy.AUTO,
    # Phase 19: the ordinary-invoice-payment counterpart — identical
    # reasoning (idempotent, moves no new money) as the entry above.
    "finance.sync_invoice_payment_to_quickbooks": ActionPolicy.AUTO,
    # Phase 18: pushes an already-completed refund (real money already
    # moved back via Stripe) to QuickBooks — moves no new money, idempotent
    # by construction, same reasoning as the two entries directly above.
    "finance.sync_refund_to_quickbooks": ActionPolicy.AUTO,
    # The pull direction — a tenant's own deliberate import of their own
    # existing QuickBooks data, same trust level as
    # crm.bulk_import_customers/finance.bulk_import_invoices.
    "finance.import_from_quickbooks": ActionPolicy.AUTO,
    # Team management — gated by Permission.MANAGE_USERS (OWNER/ADMIN
    # only) at the RBAC layer, which is the real security boundary here;
    # AUTO matches every other deliberate-owner-action tool in this file.
    "team.list_members": ActionPolicy.AUTO,
    "team.update_member": ActionPolicy.AUTO,
    "team.create_invite": ActionPolicy.AUTO,
    "team.list_invites": ActionPolicy.AUTO,
    "team.revoke_invite": ActionPolicy.AUTO,
    "organization.get_kill_switch_status": ActionPolicy.AUTO,
    "organization.set_kill_switch": ActionPolicy.AUTO,
    # Phase 14: Google Calendar sync moves no money; pushing/updating a
    # calendar event is reversible and idempotent by construction (see
    # GoogleCalendarSyncService.sync_appointment), same reasoning as
    # finance.sync_invoice_to_quickbooks above.
    "calendar.list_google_calendars": ActionPolicy.AUTO,
    "calendar.check_google_availability": ActionPolicy.AUTO,
    "calendar.sync_appointment_to_google": ActionPolicy.AUTO,
    # The pull direction — a tenant's own deliberate import of their own
    # existing Google Calendar events, same trust level as
    # finance.import_from_quickbooks.
    "calendar.import_from_google": ActionPolicy.AUTO,
    "finance.record_test_payment": ActionPolicy.AUTO,  # internal test provider only, no real money
    # Phase 12C: generates a Stripe-hosted payment LINK only — no money
    # moves until Stripe's signed webhook confirms payment_intent.succeeded
    # (app/api/v1/webhooks.py). Same reasoning as finance.send_invoice.
    "finance.create_stripe_checkout_session": ActionPolicy.AUTO,
    # Phase 13: pushes an already-approved invoice to a tenant's own
    # connected QuickBooks company — moves no money, and idempotent by
    # construction (an already-synced invoice is a safe no-op, see
    # QuickBooksSyncService), same reasoning as the two entries above.
    "finance.sync_invoice_to_quickbooks": ActionPolicy.AUTO,
    "finance.create_refund_request": ActionPolicy.AUTO,  # always lands as a pending request, never issues
    "finance.approve_refund": ActionPolicy.AUTO,  # gated by APPROVE_REFUND permission instead
    "finance.reject_refund": ActionPolicy.AUTO,  # gated by APPROVE_REFUND permission instead
    "finance.get_ar_aging": ActionPolicy.AUTO,
    "finance.get_customer_balance": ActionPolicy.AUTO,
    "finance.detect_overdue_invoices": ActionPolicy.AUTO,
    "finance.execute_due_collection_actions": ActionPolicy.AUTO,
    "finance.record_job_cost": ActionPolicy.AUTO,
    "finance.sync_material_costs": ActionPolicy.AUTO,
    "finance.generate_cash_forecast": ActionPolicy.AUTO,
    "finance.create_vendor": ActionPolicy.AUTO,
    "finance.record_vendor_bill": ActionPolicy.AUTO,
    "finance.record_payout": ActionPolicy.APPROVAL_REQUIRED,  # releases money, even if internal-test
    "finance.create_credit_note_request": ActionPolicy.AUTO,  # always lands as a pending request
    "finance.approve_credit_note": ActionPolicy.AUTO,  # gated by APPROVE_CREDIT_NOTE permission instead
    "finance.reject_credit_note": ActionPolicy.AUTO,  # gated by APPROVE_CREDIT_NOTE permission instead
    "finance.create_writeoff_request": ActionPolicy.AUTO,  # always lands as a pending request
    "finance.approve_writeoff": ActionPolicy.AUTO,  # gated by APPROVE_WRITEOFF permission instead
    "finance.reject_writeoff": ActionPolicy.AUTO,  # gated by APPROVE_WRITEOFF permission instead
    "finance.delete_invoice": ActionPolicy.BLOCKED,  # no tool implements this name — reserved; no destructive delete
    # Marketing (Phase 6). Internal record-keeping (campaigns, spend, SEO/
    # local drafts, outbound/nurture list-building) is AUTO; anything that
    # reaches a real customer/public audience is APPROVAL_REQUIRED;
    # approve_*/reject_* tools are gated by permission instead (AI's
    # default role never holds APPROVE_MARKETING_CONTENT).
    "marketing.create_campaign": ActionPolicy.AUTO,
    "marketing.set_campaign_status": ActionPolicy.AUTO,
    "marketing.record_spend": ActionPolicy.AUTO,
    "marketing.get_budget_status": ActionPolicy.AUTO,
    "marketing.attribute_lead": ActionPolicy.AUTO,
    "marketing.get_campaign_performance": ActionPolicy.AUTO,
    "marketing.detect_performance_exceptions": ActionPolicy.AUTO,
    "marketing.create_content_idea": ActionPolicy.AUTO,
    "marketing.generate_content_draft_from_job": ActionPolicy.AUTO,
    "marketing.create_content_from_review": ActionPolicy.APPROVAL_REQUIRED,  # real customer testimonial data — owner approval gates content generation itself, on top of the existing separate publish approval
    "marketing.add_content_variant": ActionPolicy.AUTO,
    "marketing.request_content_approval": ActionPolicy.AUTO,
    "marketing.approve_content": ActionPolicy.AUTO,  # gated by APPROVE_MARKETING_CONTENT instead
    "marketing.reject_content": ActionPolicy.AUTO,  # gated by APPROVE_MARKETING_CONTENT instead
    "marketing.publish_content_variant": ActionPolicy.APPROVAL_REQUIRED,  # public-visible
    "marketing.generate_seo_page_draft": ActionPolicy.AUTO,
    "marketing.publish_seo_page": ActionPolicy.APPROVAL_REQUIRED,  # public-visible
    "marketing.record_seo_keyword": ActionPolicy.AUTO,
    "marketing.create_seo_opportunity": ActionPolicy.AUTO,
    "marketing.create_local_listing": ActionPolicy.AUTO,
    "marketing.record_local_review": ActionPolicy.AUTO,
    "marketing.respond_to_review": ActionPolicy.APPROVAL_REQUIRED,  # public-visible
    "marketing.get_ads_provider_status": ActionPolicy.AUTO,
    "marketing.create_outbound_list": ActionPolicy.AUTO,
    "marketing.add_outbound_contact": ActionPolicy.AUTO,
    "marketing.create_outbound_sequence": ActionPolicy.AUTO,
    "marketing.add_outbound_step": ActionPolicy.AUTO,
    # AUTO, not APPROVAL_REQUIRED: enrollment only schedules future
    # OutboundActivity rows, and execution only ever calls the *internal
    # test* CommunicationProvider (no real Gmail/Twilio/SendGrid is
    # connected — see NOT_CONNECTED status). A generic ToolRegistry-level
    # APPROVAL_REQUIRED here would hit the same dead end fixed for
    # finance.send_invoice in Phase 5 (an approved ApprovalRequest doesn't
    # resume the original call). Once a real external provider is
    # connected, gating the *send* step is the right place to add
    # APPROVAL_REQUIRED back — not before.
    "marketing.enroll_outbound_contact": ActionPolicy.AUTO,
    "marketing.execute_due_outbound_activities": ActionPolicy.AUTO,
    "marketing.create_nurture_sequence": ActionPolicy.AUTO,
    "marketing.find_stale_lead_candidates": ActionPolicy.AUTO,
    "marketing.enroll_lead_in_nurture": ActionPolicy.AUTO,
    "marketing.execute_due_nurture_activities": ActionPolicy.AUTO,
    "marketing.create_reactivation_campaign": ActionPolicy.AUTO,
    "marketing.identify_inactive_customers": ActionPolicy.AUTO,  # read-only candidate selection, no contact made
    "marketing.identify_unbooked_qualified_leads": ActionPolicy.AUTO,
    # Retention & Referral (Phase 7). Internal record-keeping (lifecycle,
    # opportunities, risk detection, feedback recording, referral creation)
    # is AUTO; sending a real review request is APPROVAL_REQUIRED (it's a
    # single-shot, non-precondition-gated customer-facing send — no state
    # machine already gates it, unlike invoice send in Phase 5); referral
    # reward issuance is gated by permission on dedicated tools instead of
    # a second, dead-end ToolRegistry approval — see
    # finance.send_invoice's docstring in this file for why. Retention
    # campaign enrollment/execution stay AUTO for the same reason Phase
    # 6's outbound/nurture enrollment does: only the internal test
    # CommunicationProvider is ever actually reachable right now.
    "retention.get_customer_health": ActionPolicy.AUTO,
    "retention.detect_at_risk_and_inactive": ActionPolicy.AUTO,
    "retention.detect_payment_issue_risk": ActionPolicy.AUTO,
    "retention.identify_advocate_candidates": ActionPolicy.AUTO,
    "retention.update_opportunity_status": ActionPolicy.AUTO,
    "retention.mark_due_reminders": ActionPolicy.AUTO,
    "retention.update_reminder_status": ActionPolicy.AUTO,
    "retention.record_feedback": ActionPolicy.AUTO,
    "retention.record_review_consent": ActionPolicy.AUTO,  # gated by the explicit ActorType.AI guard instead, matching finance.approve_invoice's pattern
    "retention.send_review_request": ActionPolicy.APPROVAL_REQUIRED,  # reaches a real customer channel
    "retention.create_referral_program": ActionPolicy.AUTO,
    "retention.get_or_create_referral_code": ActionPolicy.AUTO,
    "retention.create_referral": ActionPolicy.AUTO,
    "retention.convert_referral_to_lead": ActionPolicy.AUTO,
    "retention.request_referral_reward": ActionPolicy.AUTO,  # always lands as a pending request, never issues
    "retention.approve_referral_reward": ActionPolicy.AUTO,  # gated by APPROVE_REFERRAL_REWARD instead
    "retention.reject_referral_reward": ActionPolicy.AUTO,  # gated by APPROVE_REFERRAL_REWARD instead
    "retention.issue_referral_reward": ActionPolicy.AUTO,  # gated by APPROVE_REFERRAL_REWARD instead; internal record only
    "retention.create_campaign": ActionPolicy.AUTO,
    "retention.set_campaign_status": ActionPolicy.AUTO,
    "retention.enroll_customer_in_campaign": ActionPolicy.AUTO,
    "retention.execute_due_activities": ActionPolicy.AUTO,

    # Phase 8B: Morning Brief — every insight tool is read-only (AUTO by
    # nature); generating a brief just persists a summary of data the caller
    # could already see, so AUTO. Executing a *recommendation* is NOT
    # separately gated here — it dispatches to the recommendation's own
    # `executable_tool`, which keeps whatever policy that tool already has
    # (e.g. `retention.approve_referral_reward` stays permission-gated, not
    # policy-gated — see above), so there is no second approval layer to
    # duplicate or create a dead end for.
    "insights.get_finance_snapshot": ActionPolicy.AUTO,
    "insights.get_operations_snapshot": ActionPolicy.AUTO,
    "insights.get_sales_snapshot": ActionPolicy.AUTO,
    "insights.get_commercial_pipeline_snapshot": ActionPolicy.AUTO,
    "insights.get_marketing_snapshot": ActionPolicy.AUTO,
    "insights.get_retention_snapshot": ActionPolicy.AUTO,
    "insights.get_exception_snapshot": ActionPolicy.AUTO,
    "insights.get_voice_snapshot": ActionPolicy.AUTO,
    "insights.generate_morning_brief": ActionPolicy.AUTO,
    "insights.get_latest_morning_brief": ActionPolicy.AUTO,
    "insights.execute_recommendation": ActionPolicy.AUTO,
    "insights.dismiss_recommendation": ActionPolicy.AUTO,
}


def policy_for(tool_name: str) -> ActionPolicy:
    return DEFAULT_TOOL_POLICIES.get(tool_name, ActionPolicy.APPROVAL_REQUIRED)


# Phase 10A: tools no tenant can ever override away from BLOCKED, regardless
# of what a `TenantToolPolicy` row says — the platform's own safety floor.
# Every entry here must already be ActionPolicy.BLOCKED above; a tenant
# override attempt for one of these is rejected at the PolicyService layer
# (app/services/policy_service.py), not silently ignored.
SYSTEM_BLOCKED_TOOLS: frozenset[str] = frozenset(
    {
        "operations.delete_job",
        "customer.delete",
        "finance.delete_invoice",
    }
)
