from sqlalchemy.ext.asyncio import async_sessionmaker

from app.calendar.internal_test_adapter import InternalTestCalendarAdapter
from app.communications.factory import get_communication_provider
from app.events.bus import EventBus
from app.services.attachment_service import AttachmentService
from app.services.attribution_service import AttributionService
from app.services.campaign_service import CampaignService
from app.services.content_service import ContentService
from app.services.local_service import LocalService
from app.services.nurture_service import NurtureService
from app.services.outbound_service import OutboundService
from app.services.reactivation_service import ReactivationService
from app.services.seo_service import SEOService
from app.services.completion_service import CompletionService
from app.services.conversion_service import LeadConversionService
from app.services.enrichment_service import LeadEnrichmentService
from app.services.exception_service import ExceptionService
from app.services.job_service import JobService
from app.services.job_transition_service import JobTransitionService
from app.services.lead_service import LeadService
from app.services.material_service import MaterialService
from app.services.qa_service import QAService
from app.services.qualification_service import LeadQualificationService
from app.services.scope_change_service import ScopeChangeService
from app.services.signoff_service import InternalCustomerSignoffProvider
from app.services.task_service import TaskService
from app.services.adjustments_service import AdjustmentsService
from app.services.ar_service import ARService
from app.services.cash_forecast_service import CashForecastService
from app.services.collection_service import CollectionService
from app.services.invoice_service import InvoiceService
from app.services.google_calendar_sync_service import GoogleCalendarSyncService
from app.services.quote_deposit_service import QuoteDepositService
from app.services.quote_service import QuoteService
from app.services.contract_service import ContractService
from app.services.job_costing_service import JobCostingService
from app.services.payment_service import PaymentService
from app.services.vendor_service import VendorService
from app.services.referral_service import ReferralService
from app.services.retention_campaign_service import RetentionCampaignService
from app.services.retention_service import RetentionService
from app.services.review_service import ReviewService
from app.services.insight_service import InsightService
from app.services.morning_brief_service import MorningBriefService
from app.services.approval_execution_service import ApprovalExecutionService
from app.storage.factory import get_object_storage
from app.tools.builtin.adjustment_tools import (
    ApproveCreditNote,
    ApproveWriteOff,
    CreateCreditNoteRequest,
    CreateWriteOffRequest,
    RejectCreditNote,
    RejectWriteOff,
)
from app.tools.builtin.approval_tools import (
    ApproveAction,
    CreateApprovalRequest,
    GetApprovalDetail,
    ListApprovals,
    RejectAction,
    RetryApprovalExecution,
)
from app.tools.builtin.ar_tools import (
    DetectOverdueInvoices,
    ExecuteDueCollectionActions,
    GetARAging,
    GetCustomerBalance,
)
from app.tools.builtin.cash_tools import GenerateCashForecast
from app.tools.builtin.invoice_tools import (
    ApproveInvoice,
    BulkImportInvoices,
    CreateInvoiceDraft,
    GetInvoice,
    RejectInvoice,
    RequestInvoiceApproval,
    SendInvoice,
    TriggerInvoiceFromJob,
    UpdateInvoiceDraft,
    VoidInvoice,
)
from app.tools.builtin.job_cost_tools import RecordJobCost, SyncMaterialCosts
from app.tools.builtin.google_calendar_tools import (
    CheckGoogleAvailability,
    ListGoogleCalendars,
    SyncAppointmentToGoogle,
)
from app.tools.builtin.quote_tools import (
    CreateQuoteDraft,
    DetectExpiredQuotes,
    GetQuote,
    SendQuote,
    UpdateQuoteDraft,
)
from app.tools.builtin.contract_tools import DetectPendingContracts, GetContract, SendContract
from app.tools.builtin.quote_deposit_tools import (
    CreateQuoteDepositCheckoutSession,
    GetQuoteDepositStatus,
)
from app.tools.builtin.marketing_ads_tools import GetAdsProviderStatus
from app.tools.builtin.marketing_attribution_tools import (
    AttributeLead,
    DetectPerformanceExceptions,
    GetCampaignPerformance,
)
from app.tools.builtin.marketing_campaign_tools import (
    CreateCampaign,
    GetBudgetStatus,
    RecordSpend,
    SetCampaignStatus,
)
from app.tools.builtin.marketing_content_tools import (
    AddContentVariant,
    ApproveContent,
    CreateContentFromReview,
    CreateContentIdea,
    GenerateDraftFromJob,
    PublishContentVariant,
    RejectContent,
    RequestContentApproval,
)
from app.tools.builtin.marketing_nurture_tools import (
    CreateNurtureSequence,
    EnrollLeadInNurture,
    ExecuteDueNurtureActivities,
    FindStaleLeadCandidates,
)
from app.tools.builtin.marketing_outbound_tools import (
    AddOutboundContact,
    AddOutboundStep,
    CreateOutboundList,
    CreateOutboundSequence,
    EnrollOutboundContact,
    ExecuteDueOutboundActivities,
)
from app.tools.builtin.marketing_reactivation_tools import (
    CreateReactivationCampaign,
    IdentifyInactiveCustomers,
    IdentifyUnbookedQualifiedLeads,
)
from app.tools.builtin.marketing_seo_tools import (
    CreateLocalListing,
    CreateSEOOpportunity,
    GenerateSEOPageDraft,
    PublishSEOPage,
    RecordLocalReview,
    RecordSEOKeyword,
    RespondToReview,
)
from app.tools.builtin.payment_tools import (
    ApproveRefund,
    CreateRefundRequest,
    RecordTestPayment,
    RejectRefund,
)
from app.tools.builtin.stripe_tools import CreateStripeCheckoutSession
from app.tools.builtin.vendor_tools import CreateVendor, RecordPayout, RecordVendorBill
from app.tools.builtin.appointment_tools import (
    CancelAppointment,
    CheckAvailability,
    CreateAppointment,
    RescheduleAppointment,
)
from app.tools.builtin.audit_tools import RecordAction
from app.tools.builtin.completion_tools import CloseJob, GenerateCompletionPacket, RecordCustomerSignoff
from app.tools.builtin.crm_tools import (
    AIQualifyLeadAdvisory,
    BulkImportCustomers,
    BulkImportLeads,
    CreateCustomer,
    CreateLead,
    CreateNote,
    GenerateCustomerSummary,
    GetCustomer,
    GetCustomerTimeline,
    GetLead,
    QualifyLead,
    SearchCustomers,
    SearchLeads,
    UpdateCustomer,
    UpdateLead,
)
from app.tools.builtin.document_tools import AddJobDocument, AddJobPhoto, AddVoiceNote
from app.tools.builtin.event_tools import (
    GetEvent,
    GetEventDetail,
    GetEventWorkerMetrics,
    ListDeadLetters,
    ListEvents,
    PublishEvent,
    ReplayDeadLetter,
)
from app.tools.builtin.exception_tools import CreateException, ResolveException
from app.tools.builtin.job_summary_tool import GenerateJobSummary
from app.tools.builtin.job_tools import (
    AssignJob,
    BlockJob,
    CancelJob,
    CompleteJob,
    ConvertLeadAndBook,
    CreateJob,
    DispatchJob,
    GetJob,
    GetJobTimeline,
    RescheduleJob,
    ScheduleJob,
    SearchJobs,
    StartJob,
    UnassignJob,
    UnblockJob,
    UpdateJob,
    UpdateJobStatus,
)
from app.tools.builtin.notification_tools import CreateNotification
from app.tools.builtin.qa_tools import CompleteQA, FailQA, StartQA
from app.tools.builtin.scope_change_tools import CreateScopeChange, RequestScopeChangeApproval
from app.tools.builtin.system_tools import GetCurrentTime, GetTenantContext
from app.tools.builtin.task_material_tools import (
    AddMaterial,
    CompleteTask,
    CreatePurchaseOrderDraft,
    CreateTask,
)
from app.tools.builtin.retention_campaign_tools import (
    CreateRetentionCampaign,
    EnrollCustomerInRetentionCampaign,
    ExecuteDueRetentionActivities,
    SetRetentionCampaignStatus,
)
from app.tools.builtin.retention_customer_tools import GetCustomerHealth
from app.tools.builtin.retention_feedback_tools import RecordFeedback, RecordReviewConsent
from app.tools.builtin.retention_lifecycle_tools import (
    DetectAtRiskAndInactive,
    DetectPaymentIssueRisk,
    IdentifyAdvocateCandidates,
)
from app.tools.builtin.retention_opportunity_tools import UpdateOpportunityStatus
from app.tools.builtin.retention_referral_tools import (
    ApproveReferralReward,
    ConvertReferralToLead,
    CreateReferral,
    CreateReferralProgram,
    GetOrCreateReferralCode,
    IssueReferralReward,
    RejectReferralReward,
    RequestReferralReward,
)
from app.tools.builtin.retention_reminder_tools import MarkDueReminders, UpdateReminderStatus
from app.tools.builtin.retention_review_tools import SendReviewRequest
from app.tools.builtin.worker_tools import CreateWorker, ListWorkers, UpdateWorkerStatus
from app.tools.builtin.insight_tools import (
    GetCommercialPipelineSnapshot,
    GetExceptionSnapshot,
    GetFinanceSnapshot,
    GetMarketingSnapshot,
    GetOperationsSnapshot,
    GetRetentionSnapshot,
    GetSalesSnapshot,
    GetVoiceSnapshot,
)
from app.tools.builtin.morning_brief_tools import (
    DismissRecommendation,
    ExecuteRecommendation,
    GenerateMorningBrief,
    GetLatestMorningBrief,
)
from app.tools.builtin.audit_tools import ListAIActivity
from app.tools.builtin.automation_policy_tools import (
    GetAutonomyStats,
    GetPolicy,
    ListPolicies,
    ResetPolicy,
    SetPolicy,
)
from app.tools.builtin.notification_orchestration_tools import (
    Dismiss,
    GetPreferences,
    GetUnreadCount,
    ListNotifications,
    MarkAllRead,
    MarkRead,
    SetPreference,
)
from app.services.notification_service import NotificationService
from app.services.knowledge_service import KnowledgeService
from app.tools.builtin.knowledge_tools import (
    AskKnowledge,
    DeleteKnowledgeFile,
    GetKnowledgeFile,
    IndexKnowledgeFile,
    ListKnowledgeFiles,
    SearchKnowledge,
    SetKnowledgeFile,
)
from app.ai.execution_service import AIExecutionService
from app.tools.registry import ToolRegistry


def build_tool_registry(session_factory: async_sessionmaker, bus: EventBus) -> ToolRegistry:
    from app.services.policy_service import PolicyService

    policy_service = PolicyService(session_factory)
    registry = ToolRegistry(session_factory, bus, policy_service)

    enrichment = LeadEnrichmentService(session_factory)
    lead_service = LeadService(session_factory, bus)
    qualification_service = LeadQualificationService(session_factory, bus, enrichment)
    calendar = InternalTestCalendarAdapter(session_factory)

    job_service = JobService(session_factory, bus)
    job_transitions = JobTransitionService(session_factory, bus)
    task_service = TaskService(session_factory)
    material_service = MaterialService(session_factory)
    attachment_service = AttachmentService(session_factory, get_object_storage())
    qa_service = QAService(session_factory, bus)
    exception_service = ExceptionService(session_factory, bus)
    scope_change_service = ScopeChangeService(session_factory, bus)
    completion_service = CompletionService(session_factory, bus)
    signoff_provider = InternalCustomerSignoffProvider(session_factory)
    conversion_service = LeadConversionService(session_factory, bus, calendar, job_service)

    invoice_service = InvoiceService(session_factory, bus)
    quote_service = QuoteService(session_factory, bus)
    contract_service = ContractService(session_factory, bus)
    from app.api.tool_deps_integrations import get_integration_connection_service

    payment_service = PaymentService(session_factory, bus, get_integration_connection_service())
    adjustments_service = AdjustmentsService(session_factory, bus)
    job_costing_service = JobCostingService(session_factory, exception_service)
    collection_comms = get_communication_provider(session_factory)
    collection_service = CollectionService(session_factory, collection_comms)
    ar_service = ARService(session_factory, exception_service, collection_service)
    cash_forecast_service = CashForecastService(session_factory)
    vendor_service = VendorService(session_factory, job_costing_service)

    campaign_service = CampaignService(session_factory, bus, exception_service)
    attribution_service = AttributionService(session_factory)
    content_service = ContentService(session_factory, bus)
    seo_service = SEOService(session_factory)
    local_service = LocalService(session_factory)
    marketing_comms = get_communication_provider(session_factory)
    outbound_service = OutboundService(session_factory, marketing_comms)
    nurture_service = NurtureService(session_factory, bus, marketing_comms)
    reactivation_service = ReactivationService(session_factory)

    retention_service = RetentionService(session_factory, bus, exception_service)
    review_service = ReviewService(session_factory, bus, exception_service, retention_service, marketing_comms)
    referral_service = ReferralService(session_factory, bus, campaign_service, attribution_service, lead_service)
    retention_campaign_service = RetentionCampaignService(session_factory, marketing_comms)

    registry.register(GetTenantContext())
    registry.register(GetCurrentTime())
    registry.register(PublishEvent(bus))
    registry.register(GetEvent(session_factory))
    registry.register(ListEvents(session_factory))
    registry.register(GetEventDetail(session_factory))
    registry.register(ListDeadLetters(session_factory))
    registry.register(ReplayDeadLetter(session_factory, bus))
    registry.register(GetEventWorkerMetrics())
    registry.register(CreateApprovalRequest(session_factory))
    registry.register(CreateNotification(session_factory))
    registry.register(RecordAction(session_factory))

    registry.register(CreateLead(lead_service))
    registry.register(BulkImportLeads(lead_service))
    registry.register(GetLead(session_factory))
    registry.register(UpdateLead(session_factory))
    registry.register(SearchLeads(session_factory))
    registry.register(QualifyLead(qualification_service))
    from app.services.ai_provider import get_ai_provider
    from app.services.ai_qualification_service import AIQualificationService

    ai_qualification_service = AIQualificationService(session_factory, get_ai_provider())
    registry.register(AIQualifyLeadAdvisory(ai_qualification_service))
    registry.register(CreateCustomer(session_factory))
    registry.register(BulkImportCustomers(session_factory))
    registry.register(GetCustomer(session_factory))
    registry.register(UpdateCustomer(session_factory))
    registry.register(SearchCustomers(session_factory))
    registry.register(GetCustomerTimeline(session_factory))
    registry.register(CreateNote(session_factory))
    registry.register(GenerateCustomerSummary(session_factory))

    registry.register(CheckAvailability(calendar))
    registry.register(CreateAppointment(calendar, bus))
    registry.register(CancelAppointment(calendar, bus))
    registry.register(RescheduleAppointment(calendar, bus))

    registry.register(CreateJob(job_service))
    registry.register(GetJob(session_factory))
    registry.register(UpdateJob(session_factory))
    registry.register(SearchJobs(session_factory))
    registry.register(AssignJob(job_transitions))
    registry.register(UnassignJob(job_transitions))
    registry.register(ScheduleJob(job_transitions))
    registry.register(RescheduleJob(job_transitions))
    registry.register(DispatchJob(job_transitions))
    registry.register(UpdateJobStatus(job_transitions))
    registry.register(StartJob(job_transitions))
    registry.register(CompleteJob(job_transitions))
    registry.register(CancelJob(job_transitions))
    registry.register(BlockJob(job_transitions))
    registry.register(UnblockJob(job_transitions))
    registry.register(GetJobTimeline(session_factory))
    registry.register(GenerateJobSummary(session_factory))
    registry.register(ConvertLeadAndBook(conversion_service))

    registry.register(CreateWorker(session_factory))
    registry.register(ListWorkers(session_factory))
    registry.register(UpdateWorkerStatus(session_factory))

    registry.register(CreateTask(task_service))
    registry.register(CompleteTask(task_service))
    registry.register(AddMaterial(material_service))
    registry.register(CreatePurchaseOrderDraft(material_service))

    registry.register(AddJobDocument(attachment_service))
    registry.register(AddJobPhoto(attachment_service))
    registry.register(AddVoiceNote(attachment_service))

    registry.register(StartQA(qa_service))
    registry.register(CompleteQA(qa_service))
    registry.register(FailQA(qa_service))

    registry.register(CreateScopeChange(scope_change_service))
    registry.register(RequestScopeChangeApproval())

    registry.register(CreateException(exception_service))
    registry.register(ResolveException(exception_service))

    registry.register(GenerateCompletionPacket(completion_service))
    registry.register(CloseJob(completion_service))
    registry.register(RecordCustomerSignoff(signoff_provider))

    registry.register(TriggerInvoiceFromJob(invoice_service))
    registry.register(CreateInvoiceDraft(invoice_service))
    registry.register(BulkImportInvoices(invoice_service, session_factory))
    registry.register(UpdateInvoiceDraft(invoice_service))
    registry.register(RequestInvoiceApproval(invoice_service))
    registry.register(ApproveInvoice(invoice_service))
    registry.register(RejectInvoice(invoice_service))
    registry.register(SendInvoice(invoice_service, session_factory))
    registry.register(VoidInvoice(invoice_service))
    registry.register(GetInvoice(session_factory))

    registry.register(CreateQuoteDraft(quote_service))
    registry.register(UpdateQuoteDraft(quote_service))
    registry.register(SendQuote(quote_service, session_factory))
    registry.register(GetQuote(session_factory))
    registry.register(DetectExpiredQuotes(quote_service))

    registry.register(GetContract(contract_service))
    registry.register(SendContract(contract_service))
    registry.register(DetectPendingContracts(contract_service))

    registry.register(RecordTestPayment(payment_service))
    registry.register(CreateRefundRequest(payment_service))
    registry.register(ApproveRefund(payment_service))
    registry.register(RejectRefund(payment_service))
    from app.api.tool_deps_integrations import get_integration_connection_service

    registry.register(CreateStripeCheckoutSession(session_factory, get_integration_connection_service()))

    quote_deposit_service = QuoteDepositService(session_factory, get_integration_connection_service())
    registry.register(GetQuoteDepositStatus(quote_deposit_service))
    registry.register(CreateQuoteDepositCheckoutSession(quote_deposit_service))

    from app.services.quickbooks_import_service import QuickBooksImportService
    from app.services.quickbooks_payment_sync_service import QuickBooksPaymentSyncService
    from app.services.quickbooks_refund_sync_service import QuickBooksRefundSyncService
    from app.services.quickbooks_sync_service import QuickBooksSyncService
    from app.tools.builtin.quickbooks_tools import (
        ImportFromQuickBooks,
        SyncDepositPaymentToQuickBooks,
        SyncInvoicePaymentToQuickBooks,
        SyncInvoiceToQuickBooks,
        SyncRefundToQuickBooks,
    )

    registry.register(
        SyncInvoiceToQuickBooks(QuickBooksSyncService(session_factory, get_integration_connection_service()))
    )
    _quickbooks_payment_sync_service = QuickBooksPaymentSyncService(
        session_factory, get_integration_connection_service()
    )
    registry.register(SyncDepositPaymentToQuickBooks(_quickbooks_payment_sync_service))
    registry.register(SyncInvoicePaymentToQuickBooks(_quickbooks_payment_sync_service))
    registry.register(
        SyncRefundToQuickBooks(
            QuickBooksRefundSyncService(session_factory, get_integration_connection_service())
        )
    )
    registry.register(
        ImportFromQuickBooks(
            QuickBooksImportService(session_factory, get_integration_connection_service(), invoice_service)
        )
    )

    google_calendar_sync_service = GoogleCalendarSyncService(session_factory, get_integration_connection_service())
    registry.register(ListGoogleCalendars(google_calendar_sync_service))
    registry.register(CheckGoogleAvailability(google_calendar_sync_service))
    registry.register(SyncAppointmentToGoogle(google_calendar_sync_service))

    registry.register(GetARAging(ar_service))
    registry.register(GetCustomerBalance(ar_service))
    registry.register(DetectOverdueInvoices(ar_service))
    registry.register(ExecuteDueCollectionActions(collection_service))

    registry.register(RecordJobCost(job_costing_service))
    registry.register(SyncMaterialCosts(job_costing_service))

    registry.register(GenerateCashForecast(cash_forecast_service))

    registry.register(CreateVendor(vendor_service))
    registry.register(RecordVendorBill(vendor_service))
    registry.register(RecordPayout(vendor_service))

    registry.register(CreateCreditNoteRequest(adjustments_service))
    registry.register(ApproveCreditNote(adjustments_service))
    registry.register(RejectCreditNote(adjustments_service))
    registry.register(CreateWriteOffRequest(adjustments_service))
    registry.register(ApproveWriteOff(adjustments_service))
    registry.register(RejectWriteOff(adjustments_service))

    registry.register(CreateCampaign(campaign_service))
    registry.register(SetCampaignStatus(campaign_service))
    registry.register(RecordSpend(campaign_service))
    registry.register(GetBudgetStatus(campaign_service))

    registry.register(AttributeLead(attribution_service))
    registry.register(GetCampaignPerformance(attribution_service))
    registry.register(DetectPerformanceExceptions(campaign_service, attribution_service))

    registry.register(CreateContentIdea(content_service))
    registry.register(GenerateDraftFromJob(content_service))
    registry.register(CreateContentFromReview(content_service))
    registry.register(AddContentVariant(content_service))
    registry.register(RequestContentApproval(content_service))
    registry.register(ApproveContent(content_service))
    registry.register(RejectContent(content_service))
    registry.register(PublishContentVariant(content_service))

    registry.register(GenerateSEOPageDraft(seo_service))
    registry.register(PublishSEOPage(seo_service))
    registry.register(RecordSEOKeyword(seo_service))
    registry.register(CreateSEOOpportunity(seo_service))
    registry.register(CreateLocalListing(local_service))
    registry.register(RecordLocalReview(local_service))
    registry.register(RespondToReview(local_service))

    registry.register(GetAdsProviderStatus())

    registry.register(CreateOutboundList(outbound_service))
    registry.register(AddOutboundContact(outbound_service))
    registry.register(CreateOutboundSequence(outbound_service))
    registry.register(AddOutboundStep(outbound_service))
    registry.register(EnrollOutboundContact(outbound_service))
    registry.register(ExecuteDueOutboundActivities(outbound_service))

    registry.register(CreateNurtureSequence(nurture_service))
    registry.register(FindStaleLeadCandidates(nurture_service))
    registry.register(EnrollLeadInNurture(nurture_service))
    registry.register(ExecuteDueNurtureActivities(nurture_service))

    registry.register(CreateReactivationCampaign(reactivation_service))
    registry.register(IdentifyInactiveCustomers(reactivation_service))
    registry.register(IdentifyUnbookedQualifiedLeads(reactivation_service))

    registry.register(GetCustomerHealth(retention_service))
    registry.register(DetectAtRiskAndInactive(retention_service))
    registry.register(DetectPaymentIssueRisk(retention_service))
    registry.register(IdentifyAdvocateCandidates(retention_service))
    registry.register(UpdateOpportunityStatus(retention_service))
    registry.register(MarkDueReminders(retention_service))
    registry.register(UpdateReminderStatus(retention_service))

    registry.register(RecordFeedback(review_service))
    registry.register(RecordReviewConsent(review_service))
    registry.register(SendReviewRequest(review_service))

    registry.register(CreateReferralProgram(referral_service))
    registry.register(GetOrCreateReferralCode(referral_service))
    registry.register(CreateReferral(referral_service))
    registry.register(ConvertReferralToLead(referral_service))
    registry.register(RequestReferralReward(referral_service))
    registry.register(ApproveReferralReward(referral_service))
    registry.register(RejectReferralReward(referral_service))
    registry.register(IssueReferralReward(referral_service))

    registry.register(CreateRetentionCampaign(retention_campaign_service))
    registry.register(SetRetentionCampaignStatus(retention_campaign_service))
    registry.register(EnrollCustomerInRetentionCampaign(retention_campaign_service))
    registry.register(ExecuteDueRetentionActivities(retention_campaign_service))

    # --- Phase 8B: Morning Brief. Read-only insight tools first, then the
    # AI execution boundary + MorningBriefService (which calls those insight
    # tools through it), then the brief tools themselves — registered last
    # since ExecuteRecommendation needs the (by-then fully built) registry
    # itself to dispatch a recommendation's underlying tool call. ---
    insight_service = InsightService(session_factory)
    registry.register(GetFinanceSnapshot(insight_service))
    registry.register(GetOperationsSnapshot(insight_service))
    registry.register(GetSalesSnapshot(insight_service))
    registry.register(GetCommercialPipelineSnapshot(insight_service))
    registry.register(GetMarketingSnapshot(insight_service))
    registry.register(GetRetentionSnapshot(insight_service))
    registry.register(GetExceptionSnapshot(insight_service))
    registry.register(GetVoiceSnapshot(insight_service))

    ai_execution_service = AIExecutionService(registry)
    morning_brief_service = MorningBriefService(session_factory, ai_execution_service, bus)

    registry.register(GenerateMorningBrief(morning_brief_service))
    registry.register(GetLatestMorningBrief(session_factory))
    registry.register(ExecuteRecommendation(session_factory, registry))
    registry.register(DismissRecommendation(session_factory))

    # --- Phase 9A: Approval orchestration. ApprovalExecutionService itself
    # calls registry.execute(..., skip_approval_gate=True) to resume the
    # original action — the registry it's given here is the same, by-then
    # fully-populated instance every other tool call goes through. ---
    approval_execution_service = ApprovalExecutionService(session_factory, registry, bus)
    registry.register(ApproveAction(approval_execution_service))
    registry.register(RejectAction(approval_execution_service))
    registry.register(RetryApprovalExecution(approval_execution_service))
    registry.register(ListApprovals(session_factory))
    registry.register(GetApprovalDetail(session_factory))

    # --- Phase 9B: /ai-activity reads the existing AuditLog through this
    # one filtered tool — never a second audit store. ---
    registry.register(ListAIActivity(session_factory))

    # --- Phase 10A: per-tenant automation policy admin tools, same
    # PolicyService instance the registry itself resolves policy through. ---
    registry.register(ListPolicies(policy_service))
    registry.register(GetPolicy(policy_service))
    registry.register(SetPolicy(policy_service))
    registry.register(ResetPolicy(policy_service))
    registry.register(GetAutonomyStats(session_factory))

    # --- Phase 10B: notification orchestration tools. NotificationService is
    # stateless (all state lives in Postgres via session_factory), so
    # app/events/notification_handlers.py constructs its own instance
    # against the same session_factory rather than sharing this object —
    # both write to the exact same `notifications` table through the exact
    # same class, never two notification code paths. ---
    notification_service = NotificationService(session_factory)
    registry.register(ListNotifications(notification_service))
    registry.register(GetUnreadCount(notification_service))
    registry.register(MarkRead(notification_service))
    registry.register(MarkAllRead(notification_service))
    registry.register(Dismiss(notification_service))
    registry.register(GetPreferences(notification_service))
    registry.register(SetPreference(notification_service))

    # --- Phase 12: the Company-OS Knowledge Layer. ---
    knowledge_service = KnowledgeService(session_factory)
    registry.register(ListKnowledgeFiles(knowledge_service))
    registry.register(GetKnowledgeFile(knowledge_service))
    registry.register(SetKnowledgeFile(knowledge_service))
    registry.register(DeleteKnowledgeFile(knowledge_service))

    # --- Phase 3 (RAG): semantic search/QA over the Knowledge Layer. ---
    from app.services.ai_provider import get_ai_provider
    from app.services.knowledge_qa_service import KnowledgeQAService
    from app.services.knowledge_retrieval_service import KnowledgeRetrievalService

    knowledge_retrieval_service = KnowledgeRetrievalService(session_factory, knowledge_service)
    knowledge_qa_service = KnowledgeQAService(session_factory, knowledge_retrieval_service, get_ai_provider())
    registry.register(IndexKnowledgeFile(knowledge_retrieval_service))
    registry.register(SearchKnowledge(knowledge_retrieval_service))
    registry.register(AskKnowledge(knowledge_qa_service))

    # --- Phase 18: AI Next Action — the first real ToolRequest-producing
    # decision layer. Reuses the `ai_execution_service` built for Morning
    # Brief above (the same, by-then fully-populated registry every other
    # tool call goes through) rather than constructing a second one.
    # Registered last for the same reason ExecuteRecommendation is: it
    # needs the fully-built registry (via AIExecutionService) to dispatch
    # its own proposed tool call. ---
    from app.services.ai_next_action_service import AINextActionService
    from app.tools.builtin.ai_next_action_tools import ProposeInvoiceFollowup, ProposeQuoteFollowup

    ai_next_action_service = AINextActionService(session_factory, ai_execution_service, get_ai_provider())
    registry.register(ProposeQuoteFollowup(ai_next_action_service))
    # Phase 20: the second scenario, same service, same governed boundary.
    registry.register(ProposeInvoiceFollowup(ai_next_action_service))

    return registry
