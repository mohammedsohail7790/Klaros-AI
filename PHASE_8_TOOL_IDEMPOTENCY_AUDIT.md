# Phase 8 — Tool Idempotency Audit

**Total `Tool` subclasses found (AST-verified, 56 files under `app/tools/builtin/`, including `__init__.py` which contains zero Tool classes): 233.**

Phase 7's log cited "233 tools across 56 files"; this phase's own independent `ast`-based walk (not `grep`, not a re-read of Phase 7's number) over the current tree finds the identical **233 tools across 56 files** — confirmed matching, not assumed. (An earlier draft of this document, since corrected, mis-stated the file count as 55 due to a `grep`-based file count that only counted files containing a matching class definition, silently excluding `app/tools/builtin/__init__.py`, which legitimately has zero Tool subclasses; the `ast`-walk based file count of 56, used throughout this document, is the correct one.)

## Methodology

1. **Enumeration**: `ast.parse` over every `app/tools/builtin/*.py` file, collecting every `ClassDef` whose bases include a name containing `Tool` (excluding the `Tool` base class itself). This is exhaustive and exact — not a `grep` approximation — and found all 233 registered tool classes.
2. **Category** (read-only / internal-write-create / internal-write-mutate / external-side-effect / external-read / mixed): derived from (a) whether the tool's file imports an `app/integrations/*_client` (the only 3 files that do: `stripe_tools.py`, `quickbooks_tools.py`, `google_calendar_tools.py` — verified by direct `grep` over all 56 files, not assumed), and (b) the tool's own `name` verb prefix (`get_`/`list_`/`search_`/... -> read-only; `create_`/`add_`/... -> internal-write-create; `update_`/`delete_`/... -> internal-write-mutate). This is a systematic, disclosed heuristic — not a manual per-tool read for all 233.
3. **Deep, manually-verified evidence for all 10 external-side-effect/external-read tools** (the only tools where a real external provider call happens): each was individually read in full — the `Tool.execute()` method, the service it delegates to, and (where relevant) the integration client method it ultimately calls — to determine whether a genuine provider-documented dedup mechanism, a deterministic identity, and (for concurrency) a `pg_advisory_xact_lock` are all actually present, not merely claimed by a docstring. Full evidence and citations are inline in the table below and in the narrative section after it.
4. **The remaining 223 internal-only tools**: NOT individually read line-by-line with per-tool unique-constraint/upsert verification this phase — this remains explicitly out of budget (the same honest limitation Phase 7 declared for its 227-tool remainder, still not fully closed). The category classification (step 2) is real and automated, but category alone is not sufficient evidence to mark `supports_idempotency = True` per the strict rule this phase enforces (a `get_`/`list_` name is not proof of a read-only implementation without reading it — see rule 3's own caution). **All 223 are therefore left at the safe default `supports_idempotency = False`, unchanged.** This is always the conservative-safe choice (an ambiguous crash mid-call safe-halts rather than guesses), never a false claim.

## Summary

- **external-side-effect**: 8
- **external-read**: 2
- **read-only**: 56
- **internal-write-create**: 73
- **internal-write-mutate**: 47
- **mixed/other**: 47

**`supports_idempotency = True` this phase: 6 of 233** (1 pre-existing from Phase 7 — Stripe checkout — plus 5 newly verified and marked this phase: 3 QuickBooks payment/refund sync tools + 2 Google Calendar read-only tools). **227 tools remain `False`** (222 internal category-classified-only + 5 external tools reviewed and deliberately kept False — see narrative).

## Full inventory

| # | Tool name | Class | File | Category | Provider | supports_idempotency |
|---|---|---|---|---|---|---|
| 1 | `finance.create_credit_note_request` | `CreateCreditNoteRequest` | `adjustment_tools.py` | internal-write-create | — | False |
| 2 | `finance.approve_credit_note` | `ApproveCreditNote` | `adjustment_tools.py` | internal-write-mutate | — | False |
| 3 | `finance.reject_credit_note` | `RejectCreditNote` | `adjustment_tools.py` | internal-write-mutate | — | False |
| 4 | `finance.create_writeoff_request` | `CreateWriteOffRequest` | `adjustment_tools.py` | internal-write-create | — | False |
| 5 | `finance.approve_writeoff` | `ApproveWriteOff` | `adjustment_tools.py` | internal-write-mutate | — | False |
| 6 | `finance.reject_writeoff` | `RejectWriteOff` | `adjustment_tools.py` | internal-write-mutate | — | False |
| 7 | `ai.propose_quote_followup` | `ProposeQuoteFollowup` | `ai_next_action_tools.py` | mixed/other | — | False |
| 8 | `ai.propose_invoice_followup` | `ProposeInvoiceFollowup` | `ai_next_action_tools.py` | mixed/other | — | False |
| 9 | `crm.check_availability` | `CheckAvailability` | `appointment_tools.py` | read-only | — | False |
| 10 | `crm.create_appointment` | `CreateAppointment` | `appointment_tools.py` | internal-write-create | — | False |
| 11 | `crm.cancel_appointment` | `CancelAppointment` | `appointment_tools.py` | internal-write-mutate | — | False |
| 12 | `crm.reschedule_appointment` | `RescheduleAppointment` | `appointment_tools.py` | internal-write-mutate | — | False |
| 13 | `approvals.create_request` | `CreateApprovalRequest` | `approval_tools.py` | internal-write-create | — | False |
| 14 | `approvals.approve` | `ApproveAction` | `approval_tools.py` | mixed/other | — | False |
| 15 | `approvals.reject` | `RejectAction` | `approval_tools.py` | mixed/other | — | False |
| 16 | `approvals.retry_execution` | `RetryApprovalExecution` | `approval_tools.py` | mixed/other | — | False |
| 17 | `approvals.list` | `ListApprovals` | `approval_tools.py` | mixed/other | — | False |
| 18 | `approvals.get_detail` | `GetApprovalDetail` | `approval_tools.py` | read-only | — | False |
| 19 | `finance.get_ar_aging` | `GetARAging` | `ar_tools.py` | read-only | — | False |
| 20 | `finance.get_customer_balance` | `GetCustomerBalance` | `ar_tools.py` | read-only | — | False |
| 21 | `finance.detect_overdue_invoices` | `DetectOverdueInvoices` | `ar_tools.py` | mixed/other | — | False |
| 22 | `finance.execute_due_collection_actions` | `ExecuteDueCollectionActions` | `ar_tools.py` | mixed/other | — | False |
| 23 | `audit.record_action` | `RecordAction` | `audit_tools.py` | internal-write-create | — | False |
| 24 | `audit.list_ai_activity` | `ListAIActivity` | `audit_tools.py` | read-only | — | False |
| 25 | `automation.list_policies` | `ListPolicies` | `automation_policy_tools.py` | read-only | — | False |
| 26 | `automation.get_policy` | `GetPolicy` | `automation_policy_tools.py` | read-only | — | False |
| 27 | `automation.set_policy` | `SetPolicy` | `automation_policy_tools.py` | internal-write-mutate | — | False |
| 28 | `automation.get_autonomy_stats` | `GetAutonomyStats` | `automation_policy_tools.py` | read-only | — | False |
| 29 | `automation.reset_policy` | `ResetPolicy` | `automation_policy_tools.py` | mixed/other | — | False |
| 30 | `finance.generate_cash_forecast` | `GenerateCashForecast` | `cash_tools.py` | internal-write-create | — | False |
| 31 | `operations.generate_completion_packet` | `GenerateCompletionPacket` | `completion_tools.py` | internal-write-create | — | False |
| 32 | `operations.close_job` | `CloseJob` | `completion_tools.py` | internal-write-mutate | — | False |
| 33 | `operations.record_customer_signoff` | `RecordCustomerSignoff` | `completion_tools.py` | internal-write-create | — | False |
| 34 | `compliance.create_license` | `CreateLicense` | `compliance_tools.py` | internal-write-create | — | False |
| 35 | `compliance.list_licenses` | `ListLicenses` | `compliance_tools.py` | read-only | — | False |
| 36 | `compliance.renew_license` | `RenewLicense` | `compliance_tools.py` | mixed/other | — | False |
| 37 | `compliance.detect_expiring` | `DetectExpiring` | `compliance_tools.py` | mixed/other | — | False |
| 38 | `contracts.get_contract` | `GetContract` | `contract_tools.py` | read-only | — | False |
| 39 | `contracts.send_contract` | `SendContract` | `contract_tools.py` | internal-write-create | — | False |
| 40 | `contracts.detect_pending` | `DetectPendingContracts` | `contract_tools.py` | mixed/other | — | False |
| 41 | `crm.create_lead` | `CreateLead` | `crm_tools.py` | internal-write-create | — | False |
| 42 | `crm.bulk_import_leads` | `BulkImportLeads` | `crm_tools.py` | mixed/other | — | False |
| 43 | `crm.get_lead` | `GetLead` | `crm_tools.py` | read-only | — | False |
| 44 | `crm.update_lead` | `UpdateLead` | `crm_tools.py` | internal-write-mutate | — | False |
| 45 | `crm.search_leads` | `SearchLeads` | `crm_tools.py` | read-only | — | False |
| 46 | `crm.qualify_lead` | `QualifyLead` | `crm_tools.py` | mixed/other | — | False |
| 47 | `crm.ai_qualify_lead_advisory` | `AIQualifyLeadAdvisory` | `crm_tools.py` | mixed/other | — | False |
| 48 | `crm.create_customer` | `CreateCustomer` | `crm_tools.py` | internal-write-create | — | False |
| 49 | `crm.bulk_import_customers` | `BulkImportCustomers` | `crm_tools.py` | mixed/other | — | False |
| 50 | `crm.get_customer` | `GetCustomer` | `crm_tools.py` | read-only | — | False |
| 51 | `crm.update_customer` | `UpdateCustomer` | `crm_tools.py` | internal-write-mutate | — | False |
| 52 | `crm.search_customers` | `SearchCustomers` | `crm_tools.py` | read-only | — | False |
| 53 | `crm.get_customer_timeline` | `GetCustomerTimeline` | `crm_tools.py` | read-only | — | False |
| 54 | `crm.create_note` | `CreateNote` | `crm_tools.py` | internal-write-create | — | False |
| 55 | `crm.generate_customer_summary` | `GenerateCustomerSummary` | `crm_tools.py` | internal-write-create | — | False |
| 56 | `operations.add_job_document` | `AddJobDocument` | `document_tools.py` | internal-write-create | — | False |
| 57 | `operations.add_job_photo` | `AddJobPhoto` | `document_tools.py` | internal-write-create | — | False |
| 58 | `operations.add_voice_note` | `AddVoiceNote` | `document_tools.py` | internal-write-create | — | False |
| 59 | `events.publish_event` | `PublishEvent` | `event_tools.py` | internal-write-mutate | — | False |
| 60 | `events.get_event` | `GetEvent` | `event_tools.py` | read-only | — | False |
| 61 | `events.list_events` | `ListEvents` | `event_tools.py` | read-only | — | False |
| 62 | `events.get_event_detail` | `GetEventDetail` | `event_tools.py` | read-only | — | False |
| 63 | `events.list_dead_letters` | `ListDeadLetters` | `event_tools.py` | read-only | — | False |
| 64 | `events.replay_dead_letter` | `ReplayDeadLetter` | `event_tools.py` | mixed/other | — | False |
| 65 | `events.get_worker_metrics` | `GetEventWorkerMetrics` | `event_tools.py` | read-only | — | False |
| 66 | `operations.create_exception` | `CreateException` | `exception_tools.py` | internal-write-create | — | False |
| 67 | `operations.resolve_exception` | `ResolveException` | `exception_tools.py` | internal-write-mutate | — | False |
| 68 | `calendar.list_google_calendars` | `ListGoogleCalendars` | `google_calendar_tools.py` | external-read | Google Calendar | **True** |
| 69 | `calendar.check_google_availability` | `CheckGoogleAvailability` | `google_calendar_tools.py` | external-read | Google Calendar | **True** |
| 70 | `calendar.sync_appointment_to_google` | `SyncAppointmentToGoogle` | `google_calendar_tools.py` | external-side-effect | Google Calendar | False |
| 71 | `calendar.import_from_google` | `ImportFromGoogleCalendar` | `google_calendar_tools.py` | external-side-effect | Google Calendar | False |
| 72 | `insights.get_finance_snapshot` | `GetFinanceSnapshot` | `insight_tools.py` | read-only | — | False |
| 73 | `insights.get_operations_snapshot` | `GetOperationsSnapshot` | `insight_tools.py` | read-only | — | False |
| 74 | `insights.get_sales_snapshot` | `GetSalesSnapshot` | `insight_tools.py` | read-only | — | False |
| 75 | `insights.get_commercial_pipeline_snapshot` | `GetCommercialPipelineSnapshot` | `insight_tools.py` | read-only | — | False |
| 76 | `insights.get_marketing_snapshot` | `GetMarketingSnapshot` | `insight_tools.py` | read-only | — | False |
| 77 | `insights.get_retention_snapshot` | `GetRetentionSnapshot` | `insight_tools.py` | read-only | — | False |
| 78 | `insights.get_voice_snapshot` | `GetVoiceSnapshot` | `insight_tools.py` | read-only | — | False |
| 79 | `insights.get_exception_snapshot` | `GetExceptionSnapshot` | `insight_tools.py` | read-only | — | False |
| 80 | `finance.trigger_invoice_from_job` | `TriggerInvoiceFromJob` | `invoice_tools.py` | internal-write-create | — | False |
| 81 | `finance.create_invoice_draft` | `CreateInvoiceDraft` | `invoice_tools.py` | internal-write-create | — | False |
| 82 | `finance.update_invoice_draft` | `UpdateInvoiceDraft` | `invoice_tools.py` | internal-write-mutate | — | False |
| 83 | `finance.request_invoice_approval` | `RequestInvoiceApproval` | `invoice_tools.py` | internal-write-create | — | False |
| 84 | `finance.approve_invoice` | `ApproveInvoice` | `invoice_tools.py` | internal-write-mutate | — | False |
| 85 | `finance.reject_invoice` | `RejectInvoice` | `invoice_tools.py` | internal-write-mutate | — | False |
| 86 | `finance.send_invoice` | `SendInvoice` | `invoice_tools.py` | internal-write-create | — | False |
| 87 | `finance.void_invoice` | `VoidInvoice` | `invoice_tools.py` | internal-write-mutate | — | False |
| 88 | `finance.bulk_import_invoices` | `BulkImportInvoices` | `invoice_tools.py` | mixed/other | — | False |
| 89 | `finance.get_invoice` | `GetInvoice` | `invoice_tools.py` | read-only | — | False |
| 90 | `finance.record_job_cost` | `RecordJobCost` | `job_cost_tools.py` | internal-write-create | — | False |
| 91 | `finance.sync_material_costs` | `SyncMaterialCosts` | `job_cost_tools.py` | internal-write-create | — | False |
| 92 | `operations.generate_job_summary` | `GenerateJobSummary` | `job_summary_tool.py` | internal-write-create | — | False |
| 93 | `operations.create_job` | `CreateJob` | `job_tools.py` | internal-write-create | — | False |
| 94 | `operations.get_job` | `GetJob` | `job_tools.py` | read-only | — | False |
| 95 | `operations.update_job` | `UpdateJob` | `job_tools.py` | internal-write-mutate | — | False |
| 96 | `operations.search_jobs` | `SearchJobs` | `job_tools.py` | read-only | — | False |
| 97 | `operations.assign_job` | `AssignJob` | `job_tools.py` | internal-write-mutate | — | False |
| 98 | `operations.unassign_job` | `UnassignJob` | `job_tools.py` | mixed/other | — | False |
| 99 | `operations.schedule_job` | `ScheduleJob` | `job_tools.py` | internal-write-create | — | False |
| 100 | `operations.reschedule_job` | `RescheduleJob` | `job_tools.py` | internal-write-mutate | — | False |
| 101 | `operations.dispatch_job` | `DispatchJob` | `job_tools.py` | mixed/other | — | False |
| 102 | `operations.update_job_status` | `UpdateJobStatus` | `job_tools.py` | internal-write-mutate | — | False |
| 103 | `operations.start_job` | `StartJob` | `job_tools.py` | internal-write-create | — | False |
| 104 | `operations.complete_job` | `CompleteJob` | `job_tools.py` | mixed/other | — | False |
| 105 | `operations.cancel_job` | `CancelJob` | `job_tools.py` | internal-write-mutate | — | False |
| 106 | `operations.block_job` | `BlockJob` | `job_tools.py` | mixed/other | — | False |
| 107 | `operations.unblock_job` | `UnblockJob` | `job_tools.py` | mixed/other | — | False |
| 108 | `operations.get_job_timeline` | `GetJobTimeline` | `job_tools.py` | read-only | — | False |
| 109 | `operations.convert_lead_and_book` | `ConvertLeadAndBook` | `job_tools.py` | internal-write-mutate | — | False |
| 110 | `knowledge.list_files` | `ListKnowledgeFiles` | `knowledge_tools.py` | read-only | — | False |
| 111 | `knowledge.get_file` | `GetKnowledgeFile` | `knowledge_tools.py` | read-only | — | False |
| 112 | `knowledge.set_file` | `SetKnowledgeFile` | `knowledge_tools.py` | internal-write-mutate | — | False |
| 113 | `knowledge.delete_file` | `DeleteKnowledgeFile` | `knowledge_tools.py` | internal-write-mutate | — | False |
| 114 | `knowledge.index_file` | `IndexKnowledgeFile` | `knowledge_tools.py` | mixed/other | — | False |
| 115 | `knowledge.search` | `SearchKnowledge` | `knowledge_tools.py` | mixed/other | — | False |
| 116 | `knowledge.ask` | `AskKnowledge` | `knowledge_tools.py` | mixed/other | — | False |
| 117 | `marketing.get_ads_provider_status` | `GetAdsProviderStatus` | `marketing_ads_tools.py` | read-only | — | False |
| 118 | `marketing.attribute_lead` | `AttributeLead` | `marketing_attribution_tools.py` | mixed/other | — | False |
| 119 | `marketing.get_campaign_performance` | `GetCampaignPerformance` | `marketing_attribution_tools.py` | read-only | — | False |
| 120 | `marketing.detect_performance_exceptions` | `DetectPerformanceExceptions` | `marketing_attribution_tools.py` | mixed/other | — | False |
| 121 | `marketing.create_campaign` | `CreateCampaign` | `marketing_campaign_tools.py` | internal-write-create | — | False |
| 122 | `marketing.set_campaign_status` | `SetCampaignStatus` | `marketing_campaign_tools.py` | internal-write-mutate | — | False |
| 123 | `marketing.record_spend` | `RecordSpend` | `marketing_campaign_tools.py` | internal-write-create | — | False |
| 124 | `marketing.get_budget_status` | `GetBudgetStatus` | `marketing_campaign_tools.py` | read-only | — | False |
| 125 | `marketing.create_content_idea` | `CreateContentIdea` | `marketing_content_tools.py` | internal-write-create | — | False |
| 126 | `marketing.generate_content_draft_from_job` | `GenerateDraftFromJob` | `marketing_content_tools.py` | internal-write-create | — | False |
| 127 | `marketing.create_content_from_review` | `CreateContentFromReview` | `marketing_content_tools.py` | internal-write-create | — | False |
| 128 | `marketing.add_content_variant` | `AddContentVariant` | `marketing_content_tools.py` | internal-write-create | — | False |
| 129 | `marketing.request_content_approval` | `RequestContentApproval` | `marketing_content_tools.py` | internal-write-create | — | False |
| 130 | `marketing.approve_content` | `ApproveContent` | `marketing_content_tools.py` | internal-write-mutate | — | False |
| 131 | `marketing.reject_content` | `RejectContent` | `marketing_content_tools.py` | internal-write-mutate | — | False |
| 132 | `marketing.publish_content_variant` | `PublishContentVariant` | `marketing_content_tools.py` | internal-write-mutate | — | False |
| 133 | `marketing.create_nurture_sequence` | `CreateNurtureSequence` | `marketing_nurture_tools.py` | internal-write-create | — | False |
| 134 | `marketing.find_stale_lead_candidates` | `FindStaleLeadCandidates` | `marketing_nurture_tools.py` | read-only | — | False |
| 135 | `marketing.enroll_lead_in_nurture` | `EnrollLeadInNurture` | `marketing_nurture_tools.py` | mixed/other | — | False |
| 136 | `marketing.execute_due_nurture_activities` | `ExecuteDueNurtureActivities` | `marketing_nurture_tools.py` | mixed/other | — | False |
| 137 | `marketing.create_outbound_list` | `CreateOutboundList` | `marketing_outbound_tools.py` | internal-write-create | — | False |
| 138 | `marketing.add_outbound_contact` | `AddOutboundContact` | `marketing_outbound_tools.py` | internal-write-create | — | False |
| 139 | `marketing.create_outbound_sequence` | `CreateOutboundSequence` | `marketing_outbound_tools.py` | internal-write-create | — | False |
| 140 | `marketing.add_outbound_step` | `AddOutboundStep` | `marketing_outbound_tools.py` | internal-write-create | — | False |
| 141 | `marketing.enroll_outbound_contact` | `EnrollOutboundContact` | `marketing_outbound_tools.py` | mixed/other | — | False |
| 142 | `marketing.execute_due_outbound_activities` | `ExecuteDueOutboundActivities` | `marketing_outbound_tools.py` | mixed/other | — | False |
| 143 | `marketing.create_reactivation_campaign` | `CreateReactivationCampaign` | `marketing_reactivation_tools.py` | internal-write-create | — | False |
| 144 | `marketing.identify_inactive_customers` | `IdentifyInactiveCustomers` | `marketing_reactivation_tools.py` | mixed/other | — | False |
| 145 | `marketing.identify_unbooked_qualified_leads` | `IdentifyUnbookedQualifiedLeads` | `marketing_reactivation_tools.py` | mixed/other | — | False |
| 146 | `marketing.generate_seo_page_draft` | `GenerateSEOPageDraft` | `marketing_seo_tools.py` | internal-write-create | — | False |
| 147 | `marketing.publish_seo_page` | `PublishSEOPage` | `marketing_seo_tools.py` | internal-write-mutate | — | False |
| 148 | `marketing.record_seo_keyword` | `RecordSEOKeyword` | `marketing_seo_tools.py` | internal-write-create | — | False |
| 149 | `marketing.create_seo_opportunity` | `CreateSEOOpportunity` | `marketing_seo_tools.py` | internal-write-create | — | False |
| 150 | `marketing.create_local_listing` | `CreateLocalListing` | `marketing_seo_tools.py` | internal-write-create | — | False |
| 151 | `marketing.record_local_review` | `RecordLocalReview` | `marketing_seo_tools.py` | internal-write-create | — | False |
| 152 | `marketing.respond_to_review` | `RespondToReview` | `marketing_seo_tools.py` | mixed/other | — | False |
| 153 | `insights.generate_morning_brief` | `GenerateMorningBrief` | `morning_brief_tools.py` | internal-write-create | — | False |
| 154 | `insights.get_latest_morning_brief` | `GetLatestMorningBrief` | `morning_brief_tools.py` | read-only | — | False |
| 155 | `insights.execute_recommendation` | `ExecuteRecommendation` | `morning_brief_tools.py` | mixed/other | — | False |
| 156 | `insights.dismiss_recommendation` | `DismissRecommendation` | `morning_brief_tools.py` | internal-write-mutate | — | False |
| 157 | `notifications.list_notifications` | `ListNotifications` | `notification_orchestration_tools.py` | read-only | — | False |
| 158 | `notifications.get_unread_count` | `GetUnreadCount` | `notification_orchestration_tools.py` | read-only | — | False |
| 159 | `notifications.mark_read` | `MarkRead` | `notification_orchestration_tools.py` | internal-write-mutate | — | False |
| 160 | `notifications.mark_all_read` | `MarkAllRead` | `notification_orchestration_tools.py` | internal-write-mutate | — | False |
| 161 | `notifications.dismiss` | `Dismiss` | `notification_orchestration_tools.py` | mixed/other | — | False |
| 162 | `notifications.get_preferences` | `GetPreferences` | `notification_orchestration_tools.py` | read-only | — | False |
| 163 | `notifications.set_preference` | `SetPreference` | `notification_orchestration_tools.py` | internal-write-mutate | — | False |
| 164 | `notifications.create_notification` | `CreateNotification` | `notification_tools.py` | internal-write-create | — | False |
| 165 | `organization.get_kill_switch_status` | `GetKillSwitchStatus` | `organization_tools.py` | read-only | — | False |
| 166 | `organization.set_kill_switch` | `SetKillSwitch` | `organization_tools.py` | internal-write-mutate | — | False |
| 167 | `finance.record_test_payment` | `RecordTestPayment` | `payment_tools.py` | internal-write-create | — | False |
| 168 | `finance.create_refund_request` | `CreateRefundRequest` | `payment_tools.py` | internal-write-create | — | False |
| 169 | `finance.approve_refund` | `ApproveRefund` | `payment_tools.py` | internal-write-mutate | — | False |
| 170 | `finance.reject_refund` | `RejectRefund` | `payment_tools.py` | internal-write-mutate | — | False |
| 171 | `operations.start_qa` | `StartQA` | `qa_tools.py` | internal-write-create | — | False |
| 172 | `operations.complete_qa` | `CompleteQA` | `qa_tools.py` | mixed/other | — | False |
| 173 | `operations.fail_qa` | `FailQA` | `qa_tools.py` | mixed/other | — | False |
| 174 | `finance.sync_invoice_to_quickbooks` | `SyncInvoiceToQuickBooks` | `quickbooks_tools.py` | external-side-effect | QuickBooks | False |
| 175 | `finance.sync_deposit_payment_to_quickbooks` | `SyncDepositPaymentToQuickBooks` | `quickbooks_tools.py` | external-side-effect | QuickBooks | **True** |
| 176 | `finance.sync_invoice_payment_to_quickbooks` | `SyncInvoicePaymentToQuickBooks` | `quickbooks_tools.py` | external-side-effect | QuickBooks | **True** |
| 177 | `finance.sync_refund_to_quickbooks` | `SyncRefundToQuickBooks` | `quickbooks_tools.py` | external-side-effect | QuickBooks | **True** |
| 178 | `finance.import_from_quickbooks` | `ImportFromQuickBooks` | `quickbooks_tools.py` | external-side-effect | QuickBooks | False |
| 179 | `finance.get_quote_deposit_status` | `GetQuoteDepositStatus` | `quote_deposit_tools.py` | read-only | — | False |
| 180 | `finance.create_quote_deposit_checkout_session` | `CreateQuoteDepositCheckoutSession` | `quote_deposit_tools.py` | internal-write-create | — | False |
| 181 | `quotes.create_quote_draft` | `CreateQuoteDraft` | `quote_tools.py` | internal-write-create | — | False |
| 182 | `quotes.update_quote_draft` | `UpdateQuoteDraft` | `quote_tools.py` | internal-write-mutate | — | False |
| 183 | `quotes.send_quote` | `SendQuote` | `quote_tools.py` | internal-write-create | — | False |
| 184 | `quotes.get_quote` | `GetQuote` | `quote_tools.py` | read-only | — | False |
| 185 | `quotes.detect_expired_quotes` | `DetectExpiredQuotes` | `quote_tools.py` | mixed/other | — | False |
| 186 | `retention.create_campaign` | `CreateRetentionCampaign` | `retention_campaign_tools.py` | internal-write-create | — | False |
| 187 | `retention.set_campaign_status` | `SetRetentionCampaignStatus` | `retention_campaign_tools.py` | internal-write-mutate | — | False |
| 188 | `retention.enroll_customer_in_campaign` | `EnrollCustomerInRetentionCampaign` | `retention_campaign_tools.py` | mixed/other | — | False |
| 189 | `retention.execute_due_activities` | `ExecuteDueRetentionActivities` | `retention_campaign_tools.py` | mixed/other | — | False |
| 190 | `retention.get_customer_health` | `GetCustomerHealth` | `retention_customer_tools.py` | read-only | — | False |
| 191 | `retention.record_feedback` | `RecordFeedback` | `retention_feedback_tools.py` | internal-write-create | — | False |
| 192 | `retention.record_review_consent` | `RecordReviewConsent` | `retention_feedback_tools.py` | internal-write-create | — | False |
| 193 | `retention.detect_at_risk_and_inactive` | `DetectAtRiskAndInactive` | `retention_lifecycle_tools.py` | mixed/other | — | False |
| 194 | `retention.detect_payment_issue_risk` | `DetectPaymentIssueRisk` | `retention_lifecycle_tools.py` | mixed/other | — | False |
| 195 | `retention.identify_advocate_candidates` | `IdentifyAdvocateCandidates` | `retention_lifecycle_tools.py` | mixed/other | — | False |
| 196 | `retention.update_opportunity_status` | `UpdateOpportunityStatus` | `retention_opportunity_tools.py` | internal-write-mutate | — | False |
| 197 | `retention.create_referral_program` | `CreateReferralProgram` | `retention_referral_tools.py` | internal-write-create | — | False |
| 198 | `retention.get_or_create_referral_code` | `GetOrCreateReferralCode` | `retention_referral_tools.py` | read-only | — | False |
| 199 | `retention.create_referral` | `CreateReferral` | `retention_referral_tools.py` | internal-write-create | — | False |
| 200 | `retention.convert_referral_to_lead` | `ConvertReferralToLead` | `retention_referral_tools.py` | internal-write-mutate | — | False |
| 201 | `retention.request_referral_reward` | `RequestReferralReward` | `retention_referral_tools.py` | internal-write-create | — | False |
| 202 | `retention.approve_referral_reward` | `ApproveReferralReward` | `retention_referral_tools.py` | internal-write-mutate | — | False |
| 203 | `retention.reject_referral_reward` | `RejectReferralReward` | `retention_referral_tools.py` | internal-write-mutate | — | False |
| 204 | `retention.issue_referral_reward` | `IssueReferralReward` | `retention_referral_tools.py` | internal-write-create | — | False |
| 205 | `retention.mark_due_reminders` | `MarkDueReminders` | `retention_reminder_tools.py` | internal-write-mutate | — | False |
| 206 | `retention.update_reminder_status` | `UpdateReminderStatus` | `retention_reminder_tools.py` | internal-write-mutate | — | False |
| 207 | `retention.send_review_request` | `SendReviewRequest` | `retention_review_tools.py` | internal-write-create | — | False |
| 208 | `operations.create_scope_change` | `CreateScopeChange` | `scope_change_tools.py` | internal-write-create | — | False |
| 209 | `operations.request_scope_change_approval` | `RequestScopeChangeApproval` | `scope_change_tools.py` | internal-write-create | — | False |
| 210 | `finance.create_stripe_checkout_session` | `CreateStripeCheckoutSession` | `stripe_tools.py` | external-side-effect | Stripe | **True** |
| 211 | `system.get_tenant_context` | `GetTenantContext` | `system_tools.py` | read-only | — | False |
| 212 | `system.get_current_time` | `GetCurrentTime` | `system_tools.py` | read-only | — | False |
| 213 | `operations.create_task` | `CreateTask` | `task_material_tools.py` | internal-write-create | — | False |
| 214 | `operations.complete_task` | `CompleteTask` | `task_material_tools.py` | mixed/other | — | False |
| 215 | `operations.add_material` | `AddMaterial` | `task_material_tools.py` | internal-write-create | — | False |
| 216 | `operations.create_purchase_order_draft` | `CreatePurchaseOrderDraft` | `task_material_tools.py` | internal-write-create | — | False |
| 217 | `team.list_members` | `ListMembers` | `team_tools.py` | read-only | — | False |
| 218 | `team.update_member` | `UpdateMember` | `team_tools.py` | internal-write-mutate | — | False |
| 219 | `team.create_invite` | `CreateInvite` | `team_tools.py` | internal-write-create | — | False |
| 220 | `team.list_invites` | `ListInvites` | `team_tools.py` | read-only | — | False |
| 221 | `team.revoke_invite` | `RevokeInvite` | `team_tools.py` | internal-write-mutate | — | False |
| 222 | `finance.create_vendor` | `CreateVendor` | `vendor_tools.py` | internal-write-create | — | False |
| 223 | `finance.list_vendors` | `ListVendors` | `vendor_tools.py` | read-only | — | False |
| 224 | `finance.list_vendor_bills` | `ListVendorBills` | `vendor_tools.py` | read-only | — | False |
| 225 | `finance.record_vendor_bill` | `RecordVendorBill` | `vendor_tools.py` | internal-write-create | — | False |
| 226 | `finance.record_payout` | `RecordPayout` | `vendor_tools.py` | internal-write-create | — | False |
| 227 | `retention.create_warranty` | `CreateWarranty` | `warranty_tools.py` | internal-write-create | — | False |
| 228 | `retention.list_warranties` | `ListWarranties` | `warranty_tools.py` | read-only | — | False |
| 229 | `retention.check_in_warranty` | `CheckInWarranty` | `warranty_tools.py` | read-only | — | False |
| 230 | `retention.detect_expiring_warranties` | `DetectExpiringWarranties` | `warranty_tools.py` | mixed/other | — | False |
| 231 | `operations.create_worker` | `CreateWorker` | `worker_tools.py` | internal-write-create | — | False |
| 232 | `operations.list_workers` | `ListWorkers` | `worker_tools.py` | read-only | — | False |
| 233 | `operations.update_worker_status` | `UpdateWorkerStatus` | `worker_tools.py` | internal-write-mutate | — | False |

## Detailed evidence — the 10 external-touching tools (manually verified)

| Tool | Native provider idempotency | DB uniqueness protection | Naturally idempotent | Safe retry | Action | Evidence | Reason |
|---|---|---|---|---|---|---|---|
| `CreateStripeCheckoutSession` | Yes | No | No | Yes | keep true | app/tools/builtin/stripe_tools.py:80 `supports_idempotency = True` (pre-existing, Phase 7-verified). StripeClient.create_checkout_session passes a real `Idempotency-Key` header (app/integrations/stripe_client.py), keyed on a business-derived `klaros-checkout-{invoice_id}-{amount_due}` string — deliberately stronger than the agent-step identity (also dedupes a human's separate manual retry for the same invoice). | Provider-native Idempotency-Key header, verified in client code and tests/test_stripe_client.py. Unchanged this phase. |
| `SyncDepositPaymentToQuickBooks` | Yes | No | No | Yes | mark true (Phase 8) | app/services/quickbooks_payment_sync_service.py:231 passes deterministic `request_id=f"klaros-deposit-payment-{payment.id}"` into QuickBooksClient.create_payment, which forwards it as Intuit's documented `?requestid=` write-dedup query param (app/integrations/quickbooks_client.py:259-317). Also holds a `pg_advisory_xact_lock` across the whole sync (line ~150-156) protecting concurrent callers. | Real, provider-documented dedup mechanism + deterministic business-derived key + advisory lock for concurrency. Not verified against a live QuickBooks account (no credentials in this environment) — documented honestly in the client's own docstring. |
| `SyncInvoicePaymentToQuickBooks` | Yes | No | No | Yes | mark true (Phase 8) | app/services/quickbooks_payment_sync_service.py:335 passes deterministic `request_id=f"klaros-invoice-payment-{payment.id}"` into the same create_payment `?requestid=` mechanism as SyncDepositPaymentToQuickBooks, plus its own pg_advisory_xact_lock (line ~252-260). | Same verified mechanism as the deposit-payment sync tool. |
| `SyncRefundToQuickBooks` | Yes | No | No | Yes | mark true (Phase 8) | app/services/quickbooks_refund_sync_service.py:182 passes deterministic `request_id=f"klaros-refund-{refund.id}"` into QuickBooksClient.create_refund_receipt's `?requestid=` param (app/integrations/quickbooks_client.py:332-367), plus pg_advisory_xact_lock (line ~104). | Same class of verified mechanism as the two payment-sync tools above. |
| `SyncInvoiceToQuickBooks` | No | No | No | No | keep false | QuickBooksClient.create_invoice (app/integrations/quickbooks_client.py:383-409) takes NO request_id/requestid parameter at all — confirmed by direct inspection. QuickBooksSyncService.sync_invoice's pg_advisory_xact_lock (line 77-80) only serializes concurrent callers; it does not cover a crash between the real create_invoice call succeeding and invoice.external_id being committed. | No provider-side dedup mechanism exists for this specific write. A crash-then-retry would create a second real QuickBooks invoice. Left False — the honest, conservative default. |
| `ImportFromQuickBooks` | Unknown | Unknown | Unknown | No | keep false / manual follow-up | Docstring claims 'idempotent: a repeat call only pulls in what's new' via external_id-linked skip (QuickBooksImportService), but per-record atomicity across a crash mid-batch was not verified this phase. | Unverified docstring claim is not sufficient evidence per the strict no-false-idempotency rule. Manual follow-up: read QuickBooksImportService.import_all's per-record commit boundaries to determine if each import is independently atomic. |
| `ListGoogleCalendars` | N/A (read) | N/A | Yes | Yes | mark true (Phase 8) | app/tools/builtin/google_calendar_tools.py execute() calls GoogleCalendarSyncService.list_calendars, which only performs a GET against Google's calendarList API — no session.add/insert/update/delete anywhere in the call path. | Genuine natural idempotency: pure read, verified by inspecting the full call path, not assumed from the `list_` name alone. |
| `CheckGoogleAvailability` | N/A (read) | N/A | Yes | Yes | mark true (Phase 8) | execute() calls GoogleCalendarSyncService.check_availability, a read-only freebusy.query GET — verified no write in the call path. | Genuine natural idempotency, verified. |
| `SyncAppointmentToGoogle` | No | No | No | No | keep false | Class docstring claims 'never creates a duplicate', but Google Calendar's events.insert has no provider-documented idempotency-key mechanism, and GoogleCalendarSyncService.sync_appointment's pg_advisory_xact_lock (google_calendar_sync_service.py ~line 185) only serializes concurrent callers, not a crash between a successful create-event call and the external_id commit. | The class's own docstring overclaims true (crash-safe) idempotency — only concurrency-safety was actually verified/fixed. Left False per the strict no-false-idempotency rule. |
| `ImportFromGoogleCalendar` | Unknown | Unknown | Unknown | No | keep false / manual follow-up | Same shape as ImportFromQuickBooks — docstring claims steady-state dedup via already-linked events, per-record crash atomicity not verified this phase. | Unverified docstring claim insufficient. Manual follow-up: verify GoogleCalendarSyncService.import_events' per-record commit boundaries. |

## QuickBooks — explicit review (per phase mandate)

Reviewed all 5 QuickBooks tools individually (not upgraded as a block merely because Stripe has a working mechanism). Result: genuinely mixed —
3 of 5 (`SyncDepositPaymentToQuickBooks`, `SyncInvoicePaymentToQuickBooks`, `SyncRefundToQuickBooks`) use QuickBooksClient methods (`create_payment`,
`create_refund_receipt`) that DO accept Intuit's documented `?requestid=` write-dedup query parameter, fed a deterministic business-derived identity by
their respective sync services — a real, provider-documented mechanism, verified in the client code, not assumed. The other 2
(`SyncInvoiceToQuickBooks`, `ImportFromQuickBooks`) do not have an equivalent verified mechanism and remain `False`. Phase 7's blanket "QuickBooks has
no idempotency mechanism, all 5 stay False" is superseded by this more granular, per-tool finding — the underlying `quickbooks_client.py` genuinely
changed since Phase 7 (it now documents and implements the `requestid` parameter on 2 of its write methods; `create_invoice` still has none).

## Google Calendar — additional finding

Not explicitly named in this phase's spec, but discovered during the QuickBooks review's cross-check of the other 2 external-integration files. Same
pattern as QuickBooks: 2 genuinely read-only tools were verified and marked True; `SyncAppointmentToGoogle`'s own docstring overclaims full
idempotency ("never creates a duplicate") when only the concurrency case was actually fixed (a `pg_advisory_xact_lock`) — the crash-then-retry case
remains genuinely ambiguous, so it was deliberately NOT upgraded despite what its own comment says. This is exactly the kind of overclaim the
governing principle exists to catch — the tool's own author-facing docstring is not, by itself, sufficient evidence.

## Internal DB tools — audit approach and limitation

For the 223 internal-only tools, the specific per-tool questions the phase mandate raises (unique constraint vs. execution identity, upsert
semantics, whether a natural/business key exists) were **not** individually traced this phase. Spot-checks during the QuickBooks/Google Calendar
review (reading the sync services those tools call) surfaced the exact failure mode the mandate warns about — "database uniqueness" (e.g.
`Invoice.external_id`) is NOT the same guarantee as "this specific execution's retry is safe," precisely because the write to that unique-ish column
happens in a separate commit from the external call it is supposed to correspond to. Applying that same lesson conservatively to all 223 un-audited
internal tools (rather than assuming any of them are safe) is the reasoning for leaving all of them at the default `False`. This is recorded as a
**known limitation**, not a completed audit — see `PHASE_8_IMPLEMENTATION_LOG.md` §17 (Deferred).
